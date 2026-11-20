import importlib.util
import sqlite3
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('watcher', Path(__file__).with_name('link_watcher.py'))
watcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(watcher)


class SampleReadinessTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        watcher.INCOMING_ROOT = self.root / 'incoming'
        watcher.RACE_DB = self.root / 'race.db'
        watcher.DEBUG_LOGFILE = self.root / 'debug.log'
        watcher._SAMPLE_STATES.clear()
        self.source = watcher.INCOMING_ROOT / 'TV-1080P' / 'Example.S01E01.1080p.WEB.H264-GROUP'
        self.source.mkdir(parents=True)
        (self.source / 'release.rar').write_bytes(b'complete payload')

    def ready(self, now, category='TV-1080P'):
        return watcher.sample_source_ready(self.source, category, now=now)[0]

    def test_sample_folder_arriving_after_completion_is_included(self):
        self.assertFalse(self.ready(0))
        folder = self.source / 'Sample'
        folder.mkdir()
        (folder / 'sample.mkv').write_bytes(b'complete sample')
        self.assertFalse(self.ready(4))
        self.assertFalse(self.ready(5))
        self.assertTrue(self.ready(6))

    def test_absent_sample_is_not_mandatory(self):
        self.assertFalse(self.ready(0))
        self.assertTrue(self.ready(5))
        self.assertFalse((self.source / 'Sample').exists())

    def test_empty_sample_folder_never_passes_on_timeout(self):
        (self.source / 'Sample').mkdir()
        self.assertFalse(self.ready(0))
        self.assertFalse(self.ready(300))

    def test_growing_and_zero_byte_samples_are_not_ready(self):
        folder = self.source / 'sAmPlE'
        folder.mkdir()
        sample = folder / 'sample.mkv'
        sample.touch()
        self.assertFalse(self.ready(0))
        self.assertFalse(self.ready(5))
        sample.write_bytes(b'part')
        self.assertFalse(self.ready(6))
        sample.write_bytes(b'complete sample')
        self.assertFalse(self.ready(7))
        self.assertTrue(self.ready(9))

    def test_ftp_receipt_is_required_even_when_size_stops_changing(self):
        folder = self.source / 'Sample'
        folder.mkdir()
        (folder / 'sample.mkv').write_bytes(b'data')
        with sqlite3.connect(watcher.RACE_DB) as db:
            db.executescript('CREATE TABLE releases(id INTEGER, path TEXT); CREATE TABLE release_files(release_id INTEGER, filename TEXT, size_bytes INTEGER, is_present INTEGER);')
            db.execute('INSERT INTO releases VALUES(1,?)', ('/' + folder.relative_to(watcher.INCOMING_ROOT).as_posix(),))
            db.execute('INSERT INTO release_files VALUES(1,?,?,0)', ('sample.mkv', 4))
        self.assertFalse(self.ready(0))
        self.assertFalse(self.ready(60))
        with sqlite3.connect(watcher.RACE_DB) as db:
            db.execute('UPDATE release_files SET is_present=1')
        self.assertTrue(self.ready(62))

    def test_non_video_without_sample_does_not_wait(self):
        self.assertTrue(self.ready(0, category='FLAC'))

    def test_duplicate_events_schedule_only_one_retry(self):
        marker = self.root / 'marker'
        marker.write_text('unchanged completion event')
        with patch.object(watcher.threading, 'Timer') as timer:
            watcher.schedule_sample_retry(marker)
            watcher.schedule_sample_retry(marker)
            self.assertEqual(timer.call_count, 1)
            watcher.cancel_sample_retry(marker)
            timer.return_value.cancel.assert_called_once()
        self.assertEqual(marker.read_text(), 'unchanged completion event')

    def test_waiting_does_not_link_launch_or_consume_uploader_selection(self):
        watcher.SOURCE = self.root / 'events'
        watcher.SOURCE.mkdir()
        watcher.COMPLETE = self.root / 'complete'
        marker = watcher.SOURCE / self.source.name
        marker.write_text(f'{self.source.name} TV-1080P {self.source}')
        picker = unittest.mock.Mock()
        with patch.object(watcher, 'schedule_sample_retry') as retry, \
                patch.object(watcher, 'sync_links') as link, \
                patch.object(watcher, 'launch_uploaders') as launch:
            watcher.process_release(self.source.name, picker)
            retry.assert_called_once()
            link.assert_not_called()
            launch.assert_not_called()
            picker.next.assert_not_called()

    def test_delayed_sample_is_linked_before_uploader_launch(self):
        watcher.SOURCE = self.root / 'events'
        watcher.SOURCE.mkdir()
        watcher.COMPLETE = self.root / 'complete'
        watcher.PENDING_LINKS = self.root / 'pending.json'
        marker = watcher.SOURCE / self.source.name
        marker.write_text(f'{self.source.name} TV-1080P {self.source}')
        picker = unittest.mock.Mock()
        picker.next.return_value = watcher.UPLOADERS[0]
        with patch.object(watcher, 'schedule_sample_retry'), \
                patch.object(watcher, 'launch_uploaders') as launch:
            with patch.object(watcher.time, 'monotonic', return_value=0):
                watcher.process_release(self.source.name, picker)
            sample = self.source / 'Sample' / 'sample.mkv'
            sample.parent.mkdir()
            sample.write_bytes(b'completed sample')
            with patch.object(watcher.time, 'monotonic', return_value=4):
                watcher.process_release(self.source.name, picker)
            launch.assert_not_called()
            with patch.object(watcher.time, 'monotonic', return_value=6):
                watcher.process_release(self.source.name, picker)
            launch.assert_called_once()
            picker.next.assert_called_once()
        linked = watcher.COMPLETE / self.source.name / 'Sample' / 'sample.mkv'
        self.assertEqual(linked.read_bytes(), b'completed sample')

    @unittest.skipUnless(shutil.which('inotifywait'), 'Linux inotifywait required')
    def test_retry_requeues_a_real_event_without_changing_contents(self):
        marker = self.root / 'marker'
        marker.write_bytes(b'original completion event')
        process = subprocess.Popen(['inotifywait', '-e', 'close_write', '-t', '3', str(marker)],
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.addCleanup(lambda: process.poll() is None and process.kill())
        self.assertIn(b'Setting up watches', process.stderr.readline())
        self.assertIn(b'Watches established', process.stderr.readline())
        with patch.object(watcher, 'SAMPLE_RETRY_SECONDS', .01):
            watcher.schedule_sample_retry(marker)
            output, _ = process.communicate(timeout=4)
        self.assertEqual(process.returncode, 0)
        self.assertIn(b'CLOSE_WRITE', output)
        self.assertEqual(marker.read_bytes(), b'original completion event')


if __name__ == '__main__':
    unittest.main()
