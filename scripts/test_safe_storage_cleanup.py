import importlib.util
import logging
from pathlib import Path
import tempfile
import time
import os
import unittest
from unittest.mock import patch

spec=importlib.util.spec_from_file_location('storage',Path(__file__).with_name('safe_storage_cleanup.py'))
storage=importlib.util.module_from_spec(spec);spec.loader.exec_module(storage)


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.release=self.root/'Release';self.release.mkdir()
        self.link=self.release/'missing.rar';self.link.symlink_to(self.root/'missing-source')
        self.payload=self.release/'payload.rar';self.payload.write_bytes(b'keep')
        self.now=time.time();self.previous={str(self.link):dict(first_missing=self.now-86401,last_seen=self.now-60,inode=self.link.lstat().st_ino)}
        self.patches=[patch.object(storage,'COMPLETE',self.root),patch.object(storage,'old_enough',return_value=True)]
        for p in self.patches:p.start();self.addCleanup(p.stop)
    def run_cleanup(self,protected=(),previous=None,apply=True):
        return storage.clean_links(set(protected),self.previous if previous is None else previous,self.now,apply,logging.getLogger('test'))
    def test_registered_release_is_never_touched(self):
        self.run_cleanup(['Release']);self.assertTrue(self.link.is_symlink());self.assertEqual(self.payload.read_bytes(),b'keep')
    def test_first_missing_observation_does_not_delete(self):
        self.run_cleanup(previous={});self.assertTrue(self.link.is_symlink())
    def test_dry_run_never_deletes(self):
        self.run_cleanup(apply=False);self.assertTrue(self.link.is_symlink())
    def test_confirmed_old_dead_link_only_is_removed(self):
        self.run_cleanup();self.assertFalse(self.link.is_symlink());self.assertEqual(self.payload.read_bytes(),b'keep')
    def test_new_inode_resets_missing_observation(self):
        self.previous[str(self.link)]['inode']-=1;self.run_cleanup();self.assertTrue(self.link.is_symlink())
    def test_io_error_is_not_treated_as_missing(self):
        class FakePath:
            def is_symlink(self):return True
            def stat(self):raise OSError('temporary mount failure')
        with self.assertRaises(OSError):storage.broken_link(FakePath())
    def test_root_and_outside_paths_do_not_protect_wrong_release(self):
        self.assertEqual(storage.top_release(self.root/'Release'/'file',self.root),'Release')
        self.assertIsNone(storage.top_release(self.root,self.root))
        self.assertIsNone(storage.top_release('/other/path',self.root))
    def test_three_day_event_cleanup_keeps_fresh_files_links_and_directories(self):
        events=self.root/'events';events.mkdir()
        old=events/'old';old.write_text('event');os.utime(old,(self.now-3*86400-1,)*2)
        fresh=events/'fresh';fresh.write_text('event')
        link=events/'symlink';link.symlink_to(old)
        (events/'directory').mkdir()
        with patch.object(storage,'EVENTS',events):
            self.assertEqual(storage.clean_events(self.now,False),1);self.assertTrue(old.exists())
            self.assertEqual(storage.clean_events(self.now,True),1)
        self.assertFalse(old.exists());self.assertTrue(fresh.exists());self.assertTrue(link.is_symlink());self.assertTrue((events/'directory').is_dir())


if __name__=='__main__':unittest.main()
