import importlib.util, logging, time, unittest
import tempfile
from pathlib import Path
from unittest.mock import patch
spec = importlib.util.spec_from_file_location('cleaner', Path(__file__).with_name('safe_qbit_cleanup.py'))
c = importlib.util.module_from_spec(spec)
spec.loader.exec_module(c)

def row(**kw):
    return dict(dict(name='Test-GROUP', hash='a' * 40, state='uploading', progress=1, amount_left=0, completed=7, size=7, total_size=7, ratio=0, num_complete=1, num_incomplete=3, num_leechs=3, upspeed=5, added_on=time.time(), seeding_time=0, last_activity=time.time(), category='upper-zip-seed'), **kw)

class Client:
    allowed_hosts = ['digitalcore.club']

    def __init__(self, t, fresh=None, seeds=3):
        self.t = t
        self.fresh = fresh or t
        self.seeds = seeds
        self.deleted = []

    def get(self, route, **kw):
        if route == 'torrents/info':
            return [self.fresh if kw else self.t]
        if route == 'torrents/trackers':
            return [dict(url='https://tracker.digitalcore.club/announce', status=2, num_seeds=self.seeds)]
        raise AssertionError(route)

    def remove_torrent_only(self, h):
        self.deleted.append(h)

class Tests(unittest.TestCase):

    def eligible(self, t):
        return c.eligibility(t, time.time(), c.DEFAULT_POLICY)

    def run_cleanup(self, client, apply=True):
        c.clean_client(client, 'test', c.DEFAULT_POLICY, {}, time.time(), apply, logging.getLogger('test'))
        return client.deleted

    def test_ratio_alone_immediate(self):
        client = Client(row(ratio=2), seeds=0)
        self.assertIsNone(self.eligible(client.t))
        self.assertEqual(self.run_cleanup(client), ['a' * 40])

    def test_two_other_seeders_alone_immediate(self):
        self.assertIsNone(self.eligible(row(num_complete=3)))
        self.assertEqual(self.run_cleanup(Client(row(num_complete=3))), ['a' * 40])

    def test_own_seed_and_old_age_are_not_enough(self):
        self.assertIsNotNone(self.eligible(row(ratio=1.99, num_complete=2, added_on=1)))
        self.assertEqual(self.run_cleanup(Client(row(num_complete=3), seeds=2)), [])

    def test_incomplete_and_active_publication_are_protected(self):
        for kw in [dict(progress=0.99), dict(progress=float('nan')), dict(amount_left=1), dict(completed=6), dict(size=6), dict(total_size=0), dict(uploader_active=True), dict(ratio=float('nan')), dict(ratio=-1)]:
            self.assertIsNotNone(self.eligible(row(**{'ratio': 5, **kw})))
        for state in ['downloading', 'stalledDL', 'checkingUP', 'checkingDL', 'pausedDL', 'queuedUP', 'unknown']:
            self.assertIsNotNone(self.eligible(row(ratio=5, state=state)))

    def test_recheck_and_dry_run(self):
        self.assertEqual(self.run_cleanup(Client(row(ratio=2), fresh=row(ratio=2, progress=0.5, amount_left=3))), [])
        self.assertEqual(self.run_cleanup(Client(row(ratio=2)), False), [])

    def test_tracker_seed_proof(self):
        t = dict(url='https://tracker.digitalcore.club/announce', status=2, num_seeds=3)
        self.assertTrue(c.healthy_tracker([t]))
        self.assertFalse(c.healthy_tracker([{**t, 'num_seeds': 2}]))
        self.assertFalse(c.healthy_tracker([{**t, 'status': 4}]))
        self.assertFalse(c.healthy_tracker([{**t, 'url': 'https://other.invalid'}]))

    def test_stale_mirror_does_not_override_confirmed_same_site_seeds(self):
        good = dict(url='https://tracker.digitalcore.club/announce', status=2, num_seeds=11)
        stale = dict(url='https://trackerprxy.digitalcore.club/announce', status=2, num_seeds=1)
        self.assertTrue(c.healthy_tracker([good, stale]))
        self.assertTrue(c.healthy_tracker([good, {**stale, 'num_seeds': None}]))
        other_site = dict(url='https://dirtybytes.net/announce', status=2, num_seeds=1)
        self.assertFalse(c.healthy_tracker([good, other_site], ['digitalcore.club', 'dirtybytes.net']))

    def test_three_days_since_completion_is_an_independent_condition(self):
        old = row(completion_on=time.time() - 259201, ratio=0, num_complete=1)
        self.assertIsNone(self.eligible(old))
        self.assertEqual(self.run_cleanup(Client(old, seeds=0)), ['a' * 40])

    def test_recent_completion_and_incomplete_old_downloads_are_preserved(self):
        self.assertIsNotNone(self.eligible(row(added_on=1, completion_on=time.time() - 10)))
        self.assertIsNotNone(self.eligible(row(completion_on=1, progress=.5, amount_left=3)))
        self.assertFalse(c.expired_complete(row(completion_on=0), time.time(), c.DEFAULT_POLICY))
        self.assertFalse(c.expired_complete(row(completion_on=float('nan')), time.time(), c.DEFAULT_POLICY))
        self.assertFalse(c.expired_complete(row(completion_on=1), time.time(), {'max_age_seconds': 0}))

    def test_age_boundary_is_exact(self):
        self.assertFalse(c.expired_complete(row(completion_on=100), 259299, c.DEFAULT_POLICY))
        self.assertTrue(c.expired_complete(row(completion_on=100), 259300, c.DEFAULT_POLICY))

    def test_proven_missing_data_does_not_require_ratio_or_seeds(self):
        missing = row(state='error', progress=.5, amount_left=3, data_missing=True)
        self.assertIsNone(self.eligible(missing))
        self.assertEqual(self.run_cleanup(Client(missing, seeds=0)), ['a' * 40])
        self.assertIsNotNone(self.eligible({**missing, 'uploader_active': True}))

    def test_delete_never_removes_payload(self):

        class Response:

            def raise_for_status(self):
                pass

        class Session:

            def post(self, url, **kw):
                self.call = kw
                return Response()
        client = object.__new__(c.Client)
        client.base = 'http://127.0.0.1:5011'
        client.session = Session()
        client.remove_torrent_only('a' * 40)
        self.assertEqual(client.session.call['data']['deleteFiles'], 'false')

    def test_incremental_recheck_updates_and_removals(self):

        class Response:

            def __init__(self, d):
                self.d = d

            def raise_for_status(self):
                pass

            def json(self):
                return self.d

        class Session:

            def __init__(self):
                self.rids = []

            def get(self, url, **kw):
                self.rids.append(kw['params']['rid'])
                return Response([{'rid': 1, 'full_update': True, 'torrents': {'a': row()}}, {'rid': 2, 'torrents': {'a': {'progress': 0.5, 'amount_left': 3}}}, {'rid': 3, 'torrents_removed': ['a']}][len(self.rids) - 1])
        client = object.__new__(c.Client)
        client.base = 'http://127.0.0.1:5011'
        client.session = Session()
        with patch.object(c, 'active_upload_names', return_value=set()):
            self.assertEqual(client.get('torrents/info')[0]['progress'], 1)
            fresh = client.get('torrents/info', hashes='a')[0]
            self.assertEqual(fresh['progress'], 0.5)
            self.assertEqual(fresh['total_size'], 7)
            self.assertEqual(client.get('torrents/info', hashes='a'), [])
        self.assertEqual(client.session.rids, [0, 1, 2])
class MissingDataTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.complete = self.base / 'COMPLETE'
        self.incoming = self.base / 'incoming'
        self.root = self.complete / 'Test-GROUP'
        self.root.mkdir(parents=True)
        self.incoming.mkdir()
        self.patcher = patch.multiple(c, LOCAL_COMPLETE=self.complete, LOCAL_INCOMING=self.incoming)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)
        self.torrent = row(content_path=str(self.root), added_on=time.time() - 3600)

    def test_broken_local_source_links_are_proof_even_for_incomplete_record(self):
        (self.root / 'payload.rar').symlink_to(self.incoming / 'TV' / 'Test-GROUP' / 'payload.rar')
        incomplete = {**self.torrent, 'progress': .5, 'amount_left': 3, 'completed': 3}
        self.assertTrue(c.data_definitely_gone(incomplete, mounts=[]))

    def test_remaining_partial_payload_is_never_missing_data(self):
        (self.root / 'payload.rar').write_bytes(b'partial data')
        self.assertFalse(c.data_definitely_gone(self.torrent, mounts=[]))

    def test_missing_empty_new_download_is_not_removed(self):
        self.root.rmdir()
        incomplete = {**self.torrent, 'progress': 0, 'amount_left': 7, 'completed': 0}
        self.assertFalse(c.data_definitely_gone(incomplete, mounts=[]))
        self.assertTrue(c.data_definitely_gone(self.torrent, mounts=[]))

    def test_mount_failure_and_archive_alias_are_not_deletion_proof(self):
        target = self.incoming / 'TV' / 'Test-GROUP' / 'payload.rar'
        link = self.root / 'payload.rar'
        link.symlink_to(target)
        self.assertFalse(c.data_definitely_gone(self.torrent, mounts=[self.incoming / 'TV']))
        link.unlink()
        link.symlink_to(self.incoming / 'ARCHiVE' / 'Test-GROUP' / 'payload.rar')
        self.assertFalse(c.data_definitely_gone(self.torrent, mounts=[]))
        with patch.object(c, 'local_path_status', return_value='unknown'):
            self.assertFalse(c.data_definitely_gone(self.torrent, mounts=[]))

    def test_alias_parent_untrusted_root_and_active_publisher_stay_protected(self):
        (self.incoming / 'TV').symlink_to(self.base / 'offline', target_is_directory=True)
        (self.root / 'payload.rar').symlink_to(self.incoming / 'TV' / 'Test-GROUP' / 'payload.rar')
        self.assertFalse(c.data_definitely_gone(self.torrent, mounts=[]))
        self.assertFalse(c.data_definitely_gone({**self.torrent, 'content_path': str(self.base)}, mounts=[]))
        self.assertFalse(c.data_definitely_gone({**self.torrent, 'uploader_active': True}, mounts=[]))


if __name__ == '__main__':
    unittest.main()
