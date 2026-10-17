import hashlib,importlib.util,io,json,tarfile,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('install_mkbrr',ROOT/'scripts/install-mkbrr.py')
installer=importlib.util.module_from_spec(spec);spec.loader.exec_module(installer)
class NativeInstallerTests(unittest.TestCase):
 def prepare(self,root,expected):
  (root/'bin').mkdir();(root/'bin/versions.json').write_text(json.dumps({'mkbrr':{'version':'1.27.0','binaries':{'bin/mkbrr/linux/x86_64/mkbrr':hashlib.sha256(expected).hexdigest()}}}))
 def archive(self,body):
  stream=io.BytesIO()
  with tarfile.open(fileobj=stream,mode='w:gz') as tar:
   row=tarfile.TarInfo('../../mkbrr');row.size=len(body);tar.addfile(row,io.BytesIO(body))
  return io.BytesIO(stream.getvalue())
 def test_checksum_and_fixed_output_path(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);self.prepare(root,b'verified')
   with patch.object(installer.platform,'system',return_value='Linux'),patch.object(installer.platform,'machine',return_value='x86_64'),patch.object(installer.urllib.request,'urlopen',return_value=self.archive(b'verified')) as request:
    output=installer.install(root)
   self.assertEqual(output,root/'bin/mkbrr/linux/x86_64/mkbrr');self.assertEqual(output.read_bytes(),b'verified');self.assertIn('https://github.com/autobrr/mkbrr/releases/download/v1.27.0/',request.call_args.args[0])
 def test_mismatch_cannot_replace_existing_binary(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);self.prepare(root,b'verified');target=root/'bin/mkbrr/linux/x86_64/mkbrr';target.parent.mkdir(parents=True);target.write_bytes(b'previous')
   with patch.object(installer.platform,'system',return_value='Linux'),patch.object(installer.platform,'machine',return_value='x86_64'),patch.object(installer.urllib.request,'urlopen',return_value=self.archive(b'corrupt')):
    with self.assertRaisesRegex(RuntimeError,'checksum'):installer.install(root)
   self.assertEqual(target.read_bytes(),b'previous')
if __name__=='__main__':unittest.main()
