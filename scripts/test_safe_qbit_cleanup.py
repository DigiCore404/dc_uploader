import importlib.util, logging, time, unittest
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
if __name__ == '__main__':
    unittest.main()
