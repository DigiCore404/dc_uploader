"""Reported UHD encodes must use the resolution category, not the disc category."""
import configparser
import importlib
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


class MovieResolutionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = configparser.ConfigParser()
        cls.config.read_dict({'Paths': {'DATADIR': str(ROOT / 'content'), 'FILTERS': str(ROOT / 'files/filters.json')}})
        with patch('utils.config_loader.ConfigLoader') as loader:
            loader.return_value.get_config.return_value = cls.config
            cls.matcher = importlib.import_module('utils.category_utils')
        cls.filters = json.loads((ROOT / 'files/filters.json').read_text(encoding='utf-8'))

    def category(self, name, manual_id=None):
        with patch.object(self.matcher, 'load_config', return_value=self.config), patch.object(self.matcher, 'load_filters', return_value=self.filters), patch.object(self.matcher, 'check_for_mp3_files', return_value=False), patch('builtins.print'):
            return self.matcher.determine_category(name, manual_id=manual_id)

    def test_reported_2160p_bluray_encodes(self):
        for name in ['Duplicity.2009.2160p.UHD.BluRay.H265-MALUS', 'Duplicity.2009.2160p.UHD.BluRay.x265-B0MBARDiERS']:
            for variant in [name, name.lower(), name.upper()]:
                with self.subTest(name=variant):
                    self.assertEqual(self.category(variant), ('Movies/2160p', '4'))

    def test_full_disc_categories_are_preserved(self):
        for name, identity in [('Example.Movie.2009.COMPLETE.UHD.BLURAY-GROUP', '38'), ('Example.Movie.2009.COMPLETE.BLURAY-GROUP', '3'), ('Ringu.1998.2160p.GBR.Blu-ray.UHD.HEVC.DTS-HD.MA.5.1', '38')]:
            with self.subTest(name=name):
                self.assertEqual(self.category(name)[1], identity)
        for marker in ['COMPLETE', 'BDMV', 'ISO', 'BD50', 'BD66', 'BD100']:
            with self.subTest(marker=marker):
                self.assertEqual(self.category(f'Example.Movie.2009.2160p.UHD.BluRay.{marker}.H265-GROUP')[1], '38')

    def test_related_resolution_categories_are_preserved(self):
        for name, identity in [('Example.Movie.2009.2160p.UHD.Blu-ray.H.265-GROUP', '4'), ('Example.Movie.2009.2160p.UHD.BluRay.REMUX.HEVC-GROUP', '4'), ('Example.Show.S01E01.2160p.UHD.BluRay.x265-GROUP', '13'), ('Example.Show.S01.2160p.UHD.BluRay.x265-GROUP', '12'), ('Example.Movie.2009.1080p.BluRay.x264-GROUP', '6')]:
            with self.subTest(name=name):
                self.assertEqual(self.category(name)[1], identity)

    def test_explicit_manual_category_remains_an_override(self):
        self.assertEqual(self.category('Duplicity.2009.2160p.UHD.BluRay.H265-MALUS', manual_id=38), ('Manual Override', '38'))


if __name__ == '__main__':
    unittest.main()
