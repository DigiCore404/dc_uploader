import configparser
from contextlib import closing, contextmanager
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import uploader

class CompletionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]/"tmp")
        self.root = Path(self.temp.name)
        (self.root/'data').mkdir()
        self.library = self.root/'media'
        self.source = self.library/'Release'
        self.source.mkdir(parents=True)
        self.config = configparser.ConfigParser()
        self.config.read_dict({'Paths': {'DATADIR': str(self.library)}})
        self.job = {'source': str(self.source)}
        self.root_patch = patch.object(uploader, 'ROOT', self.root)
        self.root_patch.start()
    def tearDown(self):
        self.root_patch.stop()
        self.temp.cleanup()
    def test_success_and_repeated_callback_publish_once(self):
        with patch.object(uploader, '_run_job', return_value=0) as run:
            self.assertEqual(uploader.completed(self.job, self.config, 'a'*40), 0)
            self.assertEqual(uploader.completed(self.job, self.config, 'a'*40), 0)
            run.assert_called_once()
        with closing(sqlite3.connect(self.root/'data/completions.db')) as db:
            self.assertEqual(db.execute('SELECT status FROM attempts').fetchone()[0], 'finished')
    def test_uncertain_exception_does_not_repost(self):
        with patch.object(uploader, '_run_job', side_effect=RuntimeError('connection lost')):
            with self.assertRaises(RuntimeError):
                uploader.completed(self.job, self.config, 'b'*40)
        with patch.object(uploader, '_run_job') as run:
            uploader.completed(self.job, self.config, 'b'*40)
            run.assert_not_called()
    def test_failed_publication_does_not_repost(self):
        with patch.object(uploader, '_run_job', return_value=2) as run:
            self.assertEqual(uploader.completed(self.job, self.config, 'c'*40), 2)
            uploader.completed(self.job, self.config, 'c'*40)
            run.assert_called_once()
    def test_rejects_library_root_and_outside_paths(self):
        for source in (self.library, self.root):
            with self.subTest(source=source), self.assertRaises(ValueError):
                uploader.completed({'source': str(source)}, self.config, 'd'*40)
    def test_rejects_invalid_hash(self):
        with self.assertRaises(ValueError):
            uploader.completed(self.job, self.config, '%I')
    def test_busy_uploader_waits_before_claiming_callback(self):
        calls = []
        @contextmanager
        def lock():
            calls.append(1)
            if len(calls) == 1:
                raise ValueError('busy')
            yield
        with patch.object(uploader, 'upload_lock', lock), patch.object(uploader.time, 'sleep') as sleep, patch.object(uploader, '_run_job', return_value=0) as run:
            self.assertEqual(uploader.completed(self.job, self.config, 'f'*40), 0)
            sleep.assert_called_once_with(2)
            run.assert_called_once()
        self.assertEqual(len(calls), 2)
    def test_busy_timeout_does_not_record_attempt(self):
        with patch.object(uploader, 'upload_lock', side_effect=ValueError('busy')):
            with self.assertRaises(ValueError):
                uploader.completed(self.job, self.config, 'e'*40, wait_seconds=0)
        self.assertFalse((self.root/'data/completions.db').exists())

if __name__ == '__main__': unittest.main()
