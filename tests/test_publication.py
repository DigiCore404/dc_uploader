import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('publication',ROOT/'scripts/publication.py')
publication=importlib.util.module_from_spec(spec);spec.loader.exec_module(publication)


class PublicationTests(unittest.TestCase):
    def test_vendor_fixture_exception_requires_exact_bytes_and_path(self):
        raw=b'-'*5+b'BEGIN '+b'PRIVATE KEY'+b'-'*5+b'\n'+b'A'*80+b'\n'
        name='bin/mtn/lib/libgnutls.so.30'
        import hashlib
        with patch.object(publication, 'VENDOR_CRYPTO_FIXTURES', {name:hashlib.sha256(raw).hexdigest()}):
            publication.check({name:raw})
            for path,data in [(name,raw+b'changed'),('other.so',raw)]:
                with self.assertRaises(ValueError):publication.check({path:data})

    def test_crypto_library_format_marker_is_not_a_private_key(self):
        marker=b'-'*5+b'BEGIN '+b'PRIVATE KEY'+b'-'*5
        publication.check({'bin/mtn/lib/format.so':marker+b'\x00format string'})
        with self.assertRaises(ValueError):
            publication.check({'key.txt':marker+b'\n'+b'A'*80+b'\n'})

    def test_empty_examples_are_accepted(self):
        publication.check({'config.example.ini':b'[Website]\nAPI_KEY=\n[Web]\nPASSWORD=\n', '.env.example':b'DIGITALCORE_WEB_PASSWORD=\n'})

    def test_private_files_credentials_and_paths_block_without_echoing_secret(self):
        cases=[('data/history.db',b'private'),('.env',b'private'),('config.example.ini',b'[Web]\nPASSWORD=unique-private-value\n'),
               ('README.md',b'C:'+b'/Users/'+b'ExamplePerson'+b'/private'),('some.key',b'private'),('../escape.txt',b'private')]
        for name,raw in cases:
            with self.subTest(name=name):
                with self.assertRaises(ValueError) as raised:publication.check({name:raw})
                self.assertNotIn('unique-private-value',str(raised.exception))

    def test_native_package_checks_are_enforced(self):
        source=(ROOT/'scripts/package_seedbox.py').read_text()
        self.assertIn('check(result)',source)
        self.assertNotIn("(ROOT/'config.ini').read_bytes()",source)
