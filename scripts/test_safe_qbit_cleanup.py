import copy
import importlib.util
import logging
from pathlib import Path
import tempfile
import time
import unittest

spec = importlib.util.spec_from_file_location('cleaner', Path(__file__).with_name('safe_qbit_cleanup.py'))
cleaner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cleaner)


def torrent(now):
    return dict(name='Example.Release-GROUP', hash='a' * 40, state='stalledUP',
                progress=1, amount_left=0, completed=7, size=7, total_size=7,
                ratio=3, seeding_time=7200, added_on=now - 300000,
                last_activity=now - 7200, num_complete=3, num_incomplete=0,
                num_leechs=0, upspeed=0, category='')


class FakeClient:
    def __init__(self, row, files, fresh=None):
        self.row = row; self.files = files; self.fresh = fresh or row; self.deleted = []
    def get(self, route, **params):
        if route == 'torrents/info': return [self.fresh if params else self.row]
        if route == 'torrents/files': return self.files
        if route == 'torrents/trackers': return [dict(url='https://tracker.digitalcore.club/announce', status=2)]
        raise AssertionError(route)
    def remove_torrent_only(self, infohash): self.deleted.append(infohash)


class CleanupTests(unittest.TestCase):
    def setUp(self):
        self.now = time.time(); self.row = torrent(self.now)
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name); (self.root / 'payload').write_bytes(b'payload')
        self.row['save_path'] = str(self.root)
        self.files = [dict(name='payload', size=7, progress=1, priority=1)]
        self.policy = dict(cleaner.DEFAULT_POLICY)
        self.previous = {'client:' + self.row['hash']: dict(first_eligible=self.now - 601, last_seen=self.now - 300)}

    def test_incomplete_states_are_protected_even_with_high_ratio_and_age(self):
        for state in ['downloading', 'stalledDL', 'checkingDL', 'checkingUP', 'queuedUP', 'pausedUP', 'error', 'unknown']:
            with self.subTest(state=state):
                row = {**self.row, 'state': state}; self.assertIsNotNone(cleaner.eligibility(row, self.now, self.policy))

    def test_percentage_and_full_payload_are_required(self):
        for changes in [dict(progress=.99), dict(progress=float('nan')), dict(amount_left=1), dict(completed=6), dict(size=6), dict(total_size=0)]:
            with self.subTest(changes=changes):
                self.assertIsNotNone(cleaner.eligibility({**self.row, **changes}, self.now, self.policy))

    def test_only_seeder_or_any_downloader_protects_torrent(self):
        for changes in [dict(num_complete=1), dict(num_complete=2), dict(num_incomplete=1), dict(num_incomplete=-1), dict(num_leechs=1), dict(upspeed=1)]:
            with self.subTest(changes=changes):
                self.assertIsNotNone(cleaner.eligibility({**self.row, **changes}, self.now, self.policy))

    def test_recent_or_unknown_activity_and_protected_category(self):
        for changes in [dict(last_activity=0), dict(last_activity=self.now), dict(seeding_time=10), dict(category='upper-zip-seed')]:
            self.assertIsNotNone(cleaner.eligibility({**self.row, **changes}, self.now, self.policy))
        row = dict(self.row); del row['num_complete']
        self.assertIsNotNone(cleaner.eligibility(row, self.now, self.policy))

    def test_payload_missing_unselected_or_escaping_is_protected(self):
        for changes in [dict(name='missing'), dict(name='../payload'), dict(name='/payload'), dict(priority=0), dict(progress=.5), dict(size=8)]:
            self.assertFalse(cleaner.complete_files(self.row, [{**self.files[0], **changes}]))
        self.assertTrue(cleaner.complete_files(self.row, self.files))

    def test_observation_period_and_dry_run_never_delete(self):
        client = FakeClient(self.row, self.files)
        cleaner.clean_client(client, 'client', self.policy, {}, self.now, True, logging.getLogger('test'))
        cleaner.clean_client(client, 'client', self.policy, self.previous, self.now, False, logging.getLogger('test'))
        self.assertEqual(client.deleted, [])

    def test_new_downloader_between_reads_cancels_removal(self):
        client = FakeClient(self.row, self.files, fresh={**self.row, 'num_incomplete': 1})
        cleaner.clean_client(client, 'client', self.policy, self.previous, self.now, True, logging.getLogger('test'))
        self.assertEqual(client.deleted, [])

    def test_eligible_removal_keeps_payload_files(self):
        client = FakeClient(self.row, self.files)
        cleaner.clean_client(client, 'client', self.policy, self.previous, self.now, True, logging.getLogger('test'))
        self.assertEqual(client.deleted, [self.row['hash']])
        self.assertEqual((self.root / 'payload').read_bytes(), b'payload')

    def test_only_healthy_digitalcore_trackers_are_accepted(self):
        self.assertFalse(cleaner.healthy_tracker([dict(url='https://other.invalid', status=2)]))
        self.assertFalse(cleaner.healthy_tracker([dict(url='https://tracker.digitalcore.club', status=4)]))
        self.assertTrue(cleaner.healthy_tracker([dict(url='https://trackerprxy.digitalcore.club/announce', status=2)]))

    def test_api_always_requests_torrent_only_deletion(self):
        class Response:
            def raise_for_status(self): pass
        class Session:
            def post(self, url, **kwargs): self.call = (url, kwargs); return Response()
        client = object.__new__(cleaner.Client); client.base = 'http://127.0.0.1:5011'; client.session = Session()
        client.remove_torrent_only('a' * 40)
        self.assertEqual(client.session.call[1]['data']['deleteFiles'], 'false')


if __name__ == '__main__': unittest.main()
