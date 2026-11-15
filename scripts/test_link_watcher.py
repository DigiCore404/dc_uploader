import importlib.util
import tempfile
import shutil
import unittest
from pathlib import Path
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('link_watcher', Path(__file__).with_name('link_watcher.py'))
watcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(watcher)


class DurableLinksTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        watcher.DEBUG_LOGFILE = self.root / 'debug.log'
        watcher.INCOMING_ROOT = self.root / 'incoming'
        watcher.ARCHIVE_ROOT = self.root / 'archive'
        watcher.PENDING_LINKS = self.root / 'pending.json'

    def test_links_survive_removing_virtual_release_alias(self):
        physical = self.root / 'DISK1' / 'Example.Game-RUNE'
        physical.mkdir(parents=True)
        (physical / 'game.rar').write_bytes(b'payload')
        virtual = self.root / 'site' / 'Example.Game-RUNE'
        virtual.parent.mkdir()
        virtual.symlink_to(physical, target_is_directory=True)
        dest = self.root / 'complete' / virtual.name
        watcher.sync_links(virtual, dest)
        virtual.unlink()
        self.assertEqual((dest / 'game.rar').read_bytes(), b'payload')
        self.assertEqual((dest / 'game.rar').readlink(), physical / 'game.rar')

    def test_existing_volatile_links_are_repaired(self):
        physical = self.root / 'disk' / 'release'
        physical.mkdir(parents=True)
        (physical / 'game.rar').write_bytes(b'payload')
        alias = self.root / 'alias'
        alias.symlink_to(physical, target_is_directory=True)
        dest = self.root / 'complete'
        dest.mkdir()
        (dest / 'game.rar').symlink_to(alias / 'game.rar')
        watcher.sync_links(alias, dest)
        alias.unlink()
        self.assertTrue((dest / 'game.rar').is_file())

    def test_empty_source_is_never_verified(self):
        source = self.root / 'empty'
        source.mkdir()
        self.assertFalse(watcher.verify_links(source, self.root / 'dest')[0])

    def test_real_incoming_move_preserves_data_then_releases_hardlinks(self):
        source = watcher.INCOMING_ROOT / 'NSW' / 'Game_NSW-GROUP'
        source.mkdir(parents=True)
        (source / 'game.rar').write_bytes(b'payload')
        dest = self.root / 'complete' / source.name
        watcher.sync_links(source, dest)
        self.assertFalse((dest / 'game.rar').is_symlink())
        self.assertTrue(watcher.verify_links(source, dest)[0])
        watcher.record_pending_archive(source, dest)
        archive = watcher.ARCHIVE_ROOT / 'DISK1' / 'NSW' / source.name
        shutil.copytree(source, archive)
        shutil.rmtree(source)
        self.assertEqual((dest / 'game.rar').read_bytes(), b'payload')
        watcher.reconcile_archived_releases()
        self.assertTrue((dest / 'game.rar').is_symlink())
        self.assertEqual((dest / 'game.rar').readlink(), archive / 'game.rar')
        self.assertEqual((dest / 'game.rar').read_bytes(), b'payload')

    def test_foreign_tv_links_follow_flattened_archive_layout(self):
        source = watcher.INCOMING_ROOT / 'FOREIGN' / 'TV-PL' / 'Episode-GROUP'
        source.mkdir(parents=True)
        (source / 'episode.rar').write_bytes(b'payload')
        dest = self.root / 'complete' / source.name
        watcher.sync_links(source, dest)
        watcher.record_pending_archive(source, dest)
        archive = watcher.ARCHIVE_ROOT / 'DISK1' / 'TV-PL' / source.name
        shutil.copytree(source, archive)
        shutil.rmtree(source)
        watcher.reconcile_archived_releases()
        self.assertTrue((dest / 'episode.rar').is_symlink())
        self.assertEqual((dest / 'episode.rar').read_bytes(), b'payload')

    def test_incomplete_archive_keeps_original_hardlinks(self):
        source = watcher.INCOMING_ROOT / 'GAMES' / 'Game-GROUP'
        source.mkdir(parents=True)
        (source / 'game.rar').write_bytes(b'payload')
        dest = self.root / 'complete' / source.name
        watcher.sync_links(source, dest)
        watcher.record_pending_archive(source, dest)
        shutil.rmtree(source)
        archive = watcher.ARCHIVE_ROOT / 'DISK1' / 'GAMES' / source.name
        archive.mkdir(parents=True)
        (archive / 'game.rar').write_bytes(b'part')
        watcher.reconcile_archived_releases()
        self.assertFalse((dest / 'game.rar').is_symlink())
        self.assertEqual((dest / 'game.rar').read_bytes(), b'payload')

    def test_old_volatile_incoming_symlink_becomes_hardlink(self):
        source = watcher.INCOMING_ROOT / 'NSW' / 'Game-GROUP'
        source.mkdir(parents=True)
        (source / 'game.rar').write_bytes(b'payload')
        dest = self.root / 'complete'
        dest.mkdir()
        (dest / 'game.rar').symlink_to(source / 'game.rar')
        watcher.sync_links(source, dest)
        self.assertFalse((dest / 'game.rar').is_symlink())
        self.assertTrue(watcher.verify_links(source, dest)[0])

    def test_failed_verification_does_not_launch_uploaders(self):
        watcher.SOURCE = self.root / 'events'
        watcher.SOURCE.mkdir()
        (watcher.SOURCE / 'Missing.Game-RUNE').write_text('Missing.Game-RUNE GAMES /missing')
        watcher.COMPLETE = self.root / 'complete'
        watcher.LOGFILE = self.root / 'errors.log'
        with patch.object(watcher, 'launch_uploaders') as launch:
            watcher.process_release('Missing.Game-RUNE', watcher.UploaderPicker(watcher.UPLOADERS))
        launch.assert_not_called()


if __name__ == '__main__':
    unittest.main()
