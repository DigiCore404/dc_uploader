"""CLI, callbacks and browser workers share one durable Activity history."""
import configparser
from contextlib import contextmanager
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import uploader
from utils.job_events import PREFIX
from utils.job_history import JobHistory

class CliHistoryTests(unittest.TestCase):
    def setUp(self):
        (uploader.ROOT / 'tmp').mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=uploader.ROOT / 'tmp')
        self.root = Path(self.temp.name)
        self.source = self.root / 'media/Release'
        self.source.mkdir(parents=True)
        (self.root / 'tmp').mkdir()
        self.config = configparser.ConfigParser(interpolation=None)
        self.config.read_dict({'Paths': {'DATADIR': str(self.root / 'media'), 'TMP_DIR': str(self.root / 'tmp')},
                              'Website': {'API_KEY': 'private-key'}, 'IGDB': {'CLIENT_SECRET': 'private-igdb'}})
        self.job = {'source': str(self.source), 'category': 6}
        self.root_patch = patch.object(uploader, 'ROOT', self.root)
        self.root_patch.start()
    def tearDown(self):
        self.root_patch.stop()
        self.temp.cleanup()
    def history(self):
        return JobHistory(self.root / 'data/uploads.db')
    @contextmanager
    def backend(self, code=0):
        process = SimpleNamespace(stdout=['log private-key private-igdb\n', PREFIX + json.dumps({'site_status':'accepted', 'torrent_id':123, 'torrent_hash':'a'*40}) + '\n'], wait=lambda:code)
        with patch.object(uploader, 'check_writable'), patch('utils.database_utils.initialize_upload_database'), patch.object(uploader.subprocess, 'Popen') as popen:
            popen.return_value.__enter__.return_value = process
            yield
    def test_cli_upload_records_result_events_logs_and_hides_credentials(self):
        with self.backend():
            self.assertEqual(uploader.run_job(self.job, self.config, lambda line:None), 0)
        row = self.history().page()['jobs'][0]
        detail = self.history().get(row['id'])
        self.assertEqual(detail['status'], 'completed')
        self.assertEqual(detail['torrent_id'], 123)
        self.assertEqual(detail['torrent_hash'], 'a'*40)
        self.assertTrue(detail['events'])
        self.assertNotIn('private-key', json.dumps(detail))
        self.assertNotIn('private-igdb', json.dumps(detail))
    def test_duplicate_and_failure_are_visible(self):
        for code, status in [(10,'duplicate'), (1,'needs_review')]:
            with self.backend(code):
                self.assertEqual(uploader.run_job(self.job, self.config, lambda line:None), code)
            self.assertEqual(self.history().page()['jobs'][0]['status'], status)
    def test_callback_has_one_history_entry_and_repeat_does_not_repost(self):
        with self.backend():
            self.assertEqual(uploader.completed(self.job, self.config, 'b'*40), 0)
            self.assertEqual(uploader.completed(self.job, self.config, 'b'*40), 0)
        self.assertEqual(self.history().page()['total'], 1)
    def test_browser_managed_worker_does_not_create_duplicate(self):
        self.history().create('browser', self.job)
        def emit(line):pass
        emit.history_managed = True
        with self.backend():
            uploader.run_job(self.job, self.config, emit)
        self.assertEqual(self.history().page()['total'], 1)
    def test_exception_is_recorded_and_re_raised(self):
        with patch.object(uploader, 'check_writable', side_effect=ValueError('broken private-key')):
            with self.assertRaises(ValueError):uploader.run_job(self.job, self.config, lambda line:None)
        detail = self.history().page()['jobs'][0]
        self.assertEqual(detail['status'], 'needs_review')
        self.assertEqual(detail['error'], 'broken [redacted]')
    def test_interrupt_is_recorded_without_hiding_interrupt(self):
        with patch.object(uploader, 'check_writable', side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):uploader.run_job(self.job, self.config, lambda line:None)
        self.assertEqual(self.history().page()['jobs'][0]['status'], 'interrupted')
    def test_backend_auto_category_is_preserved(self):
        command = uploader.backend_command({'source': str(self.source), 'category':None}, self.root/'job.json')
        self.assertNotIn('--cat', command)
    def test_legacy_backend_command_uses_history_enabled_runner(self):
        with patch.object(uploader, 'ensure_config'), patch.object(uploader, 'load_config', return_value=self.config), patch.object(uploader, 'check_config', return_value=[]), patch.object(uploader, 'run_job', return_value=0) as run:
            import os
            original = Path.cwd()
            try:self.assertEqual(uploader.run_backend_cli([str(self.source)]), 0)
            finally:os.chdir(original)
            self.assertEqual(run.call_args.args[0]['source'], str(self.source))
            self.assertIsNone(run.call_args.args[0]['category'])
