from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from utils.job_history import JobHistory


class HistoryTests(unittest.TestCase):
    def setUp(self):
        (ROOT / 'tmp').mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / 'tmp')
        self.root = Path(self.temp.name)
        self.path = self.root / 'uploads.db'
        self.history = JobHistory(self.path)

    def tearDown(self):
        self.temp.cleanup()

    def job(self, identity='job'):
        self.history.create(identity, {'source': '/content/Same.Release', 'category': 9,
                                      'metadata': {'studio': 'Studio'}, 'description': 'Description'})

    def test_log_and_accepted_result_survive_restart(self):
        self.job()
        self.history.append_log('job', 'Live log committed before completion')
        self.history.update('job', {'stage': 'downloading_torrent', 'site_status': 'accepted', 'torrent_id': 456})
        reloaded = JobHistory(self.path)
        self.assertEqual(reloaded.get('job')['logs'], ['Live log committed before completion'])
        self.assertEqual(reloaded.recover_interrupted(), 1)
        record = reloaded.get('job')
        self.assertEqual(record['status'], 'interrupted')
        self.assertEqual(record['torrent_id'], 456)
        self.assertEqual(record['site_status'], 'accepted')
        self.assertEqual(record['events'][-1]['stage'], 'interrupted')
        self.assertIsNotNone(record['finished'])

    def test_attempts_are_unique_even_for_the_same_release(self):
        for i in range(65):
            self.job(str(i))
            self.history.finish(str(i), 'completed', 0)
        first = self.history.page()
        second = self.history.page(offset=30)
        self.assertEqual(first['total'], 65)
        self.assertEqual(len(first['jobs']), 30)
        self.assertEqual(len(second['jobs']), 30)
        self.assertFalse({j['id'] for j in first['jobs']} & {j['id'] for j in second['jobs']})
        self.assertEqual(self.history.page(status='completed')['total'], 65)

    def test_json_migration_is_atomic_and_idempotent(self):
        old = self.root / 'web-history.json'
        old.write_text(json.dumps([{'id': 'old', 'name': 'Old release', 'status': 'completed',
                                    'started': 123, 'finished': 456, 'logs': ['old log']}]))
        JobHistory(self.path, old)
        JobHistory(self.path, old)
        self.assertTrue(old.exists())
        self.assertEqual(self.history.page()['total'], 1)
        self.assertEqual(self.history.get('old')['logs'], ['old log'])
        self.assertEqual(self.history.get('old')['started'], 123)

    def test_invalid_migration_does_not_partially_import(self):
        old = self.root / 'web-history.json'
        old.write_text(json.dumps([{'id': 'valid', 'name': 'Valid'}, {'bad': True}]))
        with self.assertRaises(ValueError):
            JobHistory(self.path, old)
        self.assertEqual(self.history.page()['total'], 0)
        self.assertTrue(old.exists())

    def test_concurrent_log_writes_and_reads(self):
        self.job()
        def write(i):
            self.history.append_log('job', f'line {i}')
            self.history.page()
        with ThreadPoolExecutor(max_workers=5) as pool:
            list(pool.map(write, range(75)))
        record = self.history.get('job')
        self.assertEqual(record['log_count'], 75)
        self.assertEqual(len(set(record['logs'])), 75)

    def test_site_acceptance_and_local_failure_are_distinct(self):
        self.job()
        self.history.update('job', {'stage': 'downloading_torrent', 'site_status': 'accepted', 'torrent_id': 99})
        self.history.finish('job', 'needs_review', 1, 'Torrent download failed')
        result = JobHistory(self.path).get('job')
        self.assertEqual(result['error'], 'Torrent download failed')
        self.assertEqual(result['site_status'], 'accepted')
        self.assertEqual(result['torrent_id'], 99)
        self.assertEqual(result['exit_code'], 1)
        with self.history.connect() as db:
            self.assertEqual(db.execute('PRAGMA integrity_check').fetchone()[0], 'ok')

    def test_existing_backend_history_is_preserved(self):
        with self.history.connect() as db:
            db.execute('CREATE TABLE uploads (id INTEGER PRIMARY KEY, name TEXT, status TEXT)')
            db.execute("INSERT INTO uploads VALUES (1, 'Existing release', 'uploaded')")
        self.job()
        JobHistory(self.path)
        with self.history.connect() as db:
            self.assertEqual(db.execute('SELECT name, status FROM uploads').fetchone()[0], 'Existing release')
            self.assertEqual(db.execute('SELECT COUNT(*) FROM upload_jobs').fetchone()[0], 1)


if __name__ == '__main__':
    unittest.main()
