from pathlib import Path
import os,sys,tempfile,unittest,subprocess
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from torrent_fixtures import Torrent,encode
from utils.torrent_metadata import TorrentMetadata,decode
from utils.web_settings import defaults
class ImportedTorrentTests(unittest.TestCase):
 def test_mkbrr_edits_multifile_and_singlefile_without_reading_media(self):
  import utils.torrent_utils as tu
  from utils.runtime_tools import find_tool
  config=defaults();config.set('Torrent','ANNOUNCEURL','https://example.invalid/announce')
  if not find_tool('mkbrr',config):self.skipTest('Bundled Linux mkbrr required')
  for single in (False,True):
   with self.subTest(single=single),tempfile.TemporaryDirectory(dir=ROOT/'tmp') as temp:
    root=Path(temp);old=root/'Original.Release';old.mkdir();media=old/'video.mp4';media.write_bytes(b'media payload'*10000)
    torrent=Torrent(path=media if single else old);torrent.generate();raw=torrent.dump()
    meta,_=decode(raw);meta[b'announce']=b'https://source.invalid/private-passkey';meta[b'url-list']=[b'https://source.invalid/webseed']
    original=root/'source.torrent';original.write_bytes(encode(meta));before=TorrentMetadata.read(original)
    source=root/'Imported.Release';old.rename(source);media=source/'video.mp4';media.chmod(0)
    try:
     real_popen=subprocess.Popen
     with patch.object(tu,'config',config),patch.object(tu.subprocess,'Popen',wraps=real_popen) as process,patch.object(tu,'job_event') as event:
      output,_=tu.create_torrent(source,root/'out',False,'mkbrr',source_torrent=original)
      self.assertEqual(process.call_args.args[0][1],'modify')
      self.assertTrue(any(c.kwargs.get('hash_mode')=='editing' for c in event.call_args_list))
     after=TorrentMetadata.read(output)
     self.assertEqual(after.metainfo['info']['pieces'],before.metainfo['info']['pieces'])
     self.assertEqual(after.name,'Imported.Release');self.assertTrue(after.private)
     self.assertEqual(after.trackers,[['https://example.invalid/announce']]);self.assertEqual(after.source,'DigitalCore')
     self.assertNotIn(b'private-passkey',Path(output).read_bytes());self.assertNotIn(b'webseed',Path(output).read_bytes())
     self.assertTrue(after.verify_filesize(source))
    finally:media.chmod(0o600)
 def test_missing_source_never_falls_back_to_hashing(self):
  from utils.source_torrent import prepare
  with tempfile.TemporaryDirectory(dir=ROOT/'tmp') as temp:
   root=Path(temp)
   with self.assertRaises(OSError):prepare(root/'missing.torrent',root,root/'out.torrent')
