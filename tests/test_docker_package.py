import importlib.util
import sys
from pathlib import Path
import tarfile
import unittest
import zipfile

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
spec=importlib.util.spec_from_file_location('docker_package',ROOT/'scripts/package_docker.py')
package=importlib.util.module_from_spec(spec);spec.loader.exec_module(package)


class DockerPackageTests(unittest.TestCase):
    def test_only_installation_files_directly_at_root(self):
        files=package.entries()
        self.assertEqual(set(files),{'compose.yaml','compose.unrar.yaml','.env.example','setup.sh','README.md'})
        self.assertNotIn('DIGITALCORE_WEB_PASSWORD=example',files['.env.example'].decode())
        self.assertNotIn('build:',files['compose.yaml'].decode())
        self.assertNotIn('SYS_ADMIN',files['compose.yaml'].decode())
        self.assertIn('SYS_ADMIN',files['compose.unrar.yaml'].decode())
        self.assertIn('/identity/passwd',files['compose.unrar.yaml'].decode())

    def test_archive_parity_and_no_wrapper_folder_or_real_env(self):
        paths=package.build()
        with tarfile.open(paths[0]) as tar,zipfile.ZipFile(paths[1]) as zip:
            self.assertEqual(set(tar.getnames()),set(zip.namelist()))
            self.assertEqual(set(tar.getnames()),set(package.entries()))
            for member in tar:
                self.assertTrue(member.isfile())
                self.assertEqual(tar.extractfile(member).read(),zip.read(member.name))
                self.assertNotIn('..',Path(member.name).parts)
