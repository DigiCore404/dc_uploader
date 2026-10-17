import configparser
import hashlib
import importlib.util
from pathlib import Path
import tarfile
import unittest
import zipfile

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('seedbox_package',ROOT/'scripts/package_seedbox.py')
package=importlib.util.module_from_spec(spec);spec.loader.exec_module(package)


class SeedboxPackageTests(unittest.TestCase):
    def test_clean_allowlist_configs_and_shell_line_endings(self):
        files=package.entries()
        for excluded in ['Dockerfile','compose.yaml','install.cmd','install.ps1','.env','data/uploads.db','README-docker.md']:
            self.assertNotIn(excluded,files)
        self.assertFalse(any(name.startswith(('.venv/','data/','logs/','tmp/','tests/')) for name in files))
        self.assertEqual(files['config.ini'],files['config.example.ini'])
        config=configparser.ConfigParser(interpolation=None);config.read_string(files['config.ini'].decode())
        self.assertFalse(config.get('Website','API_KEY'))
        self.assertFalse(config.get('Torrent','ANNOUNCEURL'))
        self.assertFalse(config.get('Web','PASSWORD'))
        self.assertEqual(config.get('Watch','INCOMING_DIR'),'incoming')
        self.assertTrue({'start-web.sh','install.sh','webui/index.html','utils/import_queue.py','scripts/service.py'}<=files.keys())
        for name,raw in files.items():

            if not name.startswith('bin/'):self.assertNotIn(b'\r\n',raw,name)
    def test_linux_and_zip_distributions_match_with_safe_modes(self):
        paths=package.build()
        with tarfile.open(paths[0]) as tar,zipfile.ZipFile(paths[1]) as zip:
            members=tar.getmembers()
            self.assertEqual({m.name for m in members},set(zip.namelist()))
            for member in members:
                self.assertTrue(member.isfile())
                self.assertNotIn('..',Path(member.name).parts)
                relative=member.name.split('/',1)[1]
                self.assertEqual(member.mode,package.mode(relative))
                self.assertEqual((zip.getinfo(member.name).external_attr>>16)&0o777,member.mode)
                self.assertEqual(tar.extractfile(member).read(),zip.read(member.name))
        checksums=(ROOT/'dist/dc_uploader-seedbox-3.0.0.sha256').read_text()
        for path in paths:self.assertIn(hashlib.sha256(path.read_bytes()).hexdigest()+'  '+path.name,checksums)
