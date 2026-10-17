import sqlite3
import tempfile
from pathlib import Path
from contextlib import contextmanager
from types import SimpleNamespace
import unittest
import sys
from unittest.mock import Mock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from utils.delivery import check_mapping, remote_root


class DeliveryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name).resolve()
        (self.root/'Release').mkdir()
        (self.root/'Release/video.mp4').write_bytes(b'video')
        self.db=sqlite3.connect(':memory:');self.db.row_factory=sqlite3.Row
        self.db.execute('CREATE TABLE qbit_torrents(client_id,hash,save_path,progress)')
        self.db.execute("INSERT INTO qbit_torrents VALUES('client','hash','/downloads',1)")
        @contextmanager
        def connect():yield self.db
        self.history=SimpleNamespace(connect=connect)
        self.clients=Mock()
        self.clients._client.return_value={'enabled':True,'name':'Test'}
        self.clients.sync.return_value={'ok':True}
        self.clients.import_status.return_value=({'save_path':'/downloads','progress':1},[{'name':'Release/video.mp4','size':5}])
    def tearDown(self):
        self.db.close();self.temp.cleanup()
    def check(self,remote='/downloads'):
        return check_mapping(self.clients,self.history,'client',remote,self.root)
    def test_real_files_and_sizes_verify_sample_without_client_mutations(self):
        self.assertEqual(self.check()['status'],'verified')
        self.clients.add_torrent.assert_not_called();self.clients.action.assert_not_called()
    def test_wrong_mount_and_same_size_requirement(self):
        self.clients.import_status.return_value[1][0]['size']=99
        self.assertEqual(self.check()['status'],'mismatch')
        (self.root/'Release/video.mp4').unlink()
        self.assertEqual(self.check()['status'],'mismatch')
    def test_empty_client_or_unrelated_path_is_unverified(self):
        self.assertEqual(self.check('/other')['status'],'unverified')
        self.clients.import_status.assert_not_called()
    def test_traversal_and_symlink_escape_are_not_verified(self):
        self.clients.import_status.return_value[1][0]['name']='../secret'
        self.assertEqual(self.check()['status'],'mismatch')
        for value in ['', 'relative', '/a/../b', '/a\\b']:
            with self.assertRaises(ValueError):remote_root(value)
    def test_offline_or_disabled_client_does_not_claim_mapping(self):
        self.clients.sync.return_value={'ok':False,'error':'Offline'}
        with self.assertRaisesRegex(ValueError,'Offline'):self.check()
        self.clients._client.return_value={'enabled':False}
        with self.assertRaisesRegex(ValueError,'Enable'):self.check()
