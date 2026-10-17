from pathlib import Path
import subprocess,sys,tempfile,unittest
from unittest.mock import patch
from types import SimpleNamespace
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from utils.web_settings import defaults
from utils.runtime_tools import find_tool
from utils.mtn_screenshots import generate
class MtnWarningTests(unittest.TestCase):
 def test_only_usable_warning_images_are_accepted(self):
  for status,valid,accepted in [(1,True,True),(2,True,False),(1,False,False)]:
   with self.subTest(status=status,valid=valid),tempfile.TemporaryDirectory() as temp:
    root=Path(temp);video=root/'video.mp4';video.write_bytes(b'fixture');target=root/'screens';target.mkdir()
    config=defaults();config.set('MediaTools','SCREENSHOT_SINGLES','0')
    def fake_run(command,**kwargs):
     suffix=command[command.index('-o')+1]
     (target/('video'+suffix)).write_bytes(b'\xff\xd8fixture\xff\xd9' if valid else b'not a JPEG')
     return SimpleNamespace(returncode=status,stdout='',stderr='')
    with patch('utils.mtn_screenshots.find_tool',return_value='/mock/mtn'),patch('utils.mtn_screenshots.subprocess.run',side_effect=fake_run):
     if accepted:generate(video,target,24,config)
     else:
      with self.assertRaises(RuntimeError):generate(video,target,24,config)

class MtnTests(unittest.TestCase):
 def test_real_contact_sheet_and_five_frames(self):
  config=defaults()
  if not find_tool('mtn',config):self.skipTest('Bundled Linux MTN required')
  with tempfile.TemporaryDirectory(dir=ROOT/'tmp') as temp:
   root=Path(temp);video=root/'video.mp4';target=root/'screens';target.mkdir()
   subprocess.run(['ffmpeg','-loglevel','error','-f','lavfi','-i','testsrc2=size=640x360:rate=5:duration=24','-c:v','mpeg4',str(video)],check=True)
   generate(video,target,24,config)
   files=list(target.glob('*.jpg'));self.assertEqual(len(files),6)
   self.assertTrue((target/'video-sheet.jpg').is_file())
   self.assertTrue(all(f.read_bytes().startswith(b'\xff\xd8') for f in files))
