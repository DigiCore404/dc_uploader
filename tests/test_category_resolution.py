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

    def test_reported_webflac_music_releases(self):
        releases = [
            'Richy_Hendrix-10_33_(Special_Edition)-16BIT-WEBFLAC-2026-1033',
            'Boss_Hogg_x_Dubee-Thizzlamic-16BIT-WEBFLAC-2026-ESGFLAC',
            'Babyceo_Trell-Kidd_Dat_Did_It-16BIT-WEBFLAC-2026-ESGFLAC',
            'Melly_Mike-TIME4WAR-16BIT-WEBFLAC-2026-ESGFLAC',
            'Malibu_Mitch-Sailor_Goon-16BIT-WEBFLAC-2026-ESGFLAC',
            'The_Game-Unreleased_Lost_Files-16BIT-WEBFLAC-2026-ESGFLAC',
            'Xzibit_x_B-Real_x_Demrick-Serial_Killers_The_Murder_Show_(Remastered_2026)-16BIT-WEBFLAC-2026-ESGFLAC',
            'Nino_Breeze-NON-FICTION-16BIT-WEBFLAC-2026-ESGFLAC',
            'Juice_Webster-Kind_of_Fantasy-24BIT-WEBFLAC-2026-PreMaXXing',
            'Miguel_Atwood-Ferguson-Ananta_Spirit_Of_Coltrane-24BIT-WEBFLAC-2026-PreMaXXing',
            'Stephen_Steinbrink-In_a_Spirit_Sistered_World-24BIT-WEBFLAC-2026-PreMaXXing',
            'Radio_Free_Alice-Whats_In_Between-16BIT-WEBFLAC-2026-PreMaXXing',
            'Moses_Boyd-Songs_For_Sinners-16BIT-WEBFLAC-2026-PreMaXXing',
            'Sua_Galera-Sua_Galera-24BIT-WEBFLAC-2026-PreMaXXing',
            'Coco_Elise-The_longest_bridge_in_the_world-24BIT-WEBFLAC-2026-PreMaXXing',
            'Or_x_John_Talabot-Music_For_Empty_Stadiums-24BIT-WEBFLAC-2026-PreMaXXing',
            'ionnalee-ionnalee_MOUTH_OF_A_RIVER_pt_3-24BIT-WEBFLAC-2026-PreMaXXing',
        ]
        for name in releases:
            for variant in [name, name.lower(), name.upper()]:
                with self.subTest(name=variant):
                    self.assertEqual(self.category(variant), ('Music/FLAC', '23'))

    def test_flac_tags_do_not_capture_movie_titles_or_video_audio_codecs(self):
        for name in ['Artist-Album-FLAC-2026-GROUP', 'Artist-Album-WEB-FLAC-2026-GROUP', 'Artist.Album.WEBFLAC.2026.GROUP', 'FLAC-Artist-Album-2026']:
            with self.subTest(name=name):
                self.assertEqual(self.category(name), ('Music/FLAC', '23'))
        self.assertEqual(self.category('Flack.2026.1080p.WEB.H264-GROUP')[1], '6')
        self.assertNotEqual(self.category('Example.Movie.2026.1080p.BluRay.x264.FLAC-GROUP')[1], '23')

    def test_reported_2160p_bluray_encodes(self):
        for name in ['Duplicity.2009.2160p.UHD.BluRay.H265-MALUS', 'Duplicity.2009.2160p.UHD.BluRay.x265-B0MBARDiERS']:
            for variant in [name, name.lower(), name.upper()]:
                with self.subTest(name=variant):
                    self.assertEqual(self.category(variant), ('Movies/2160p', '4'))

    def test_full_disc_categories_are_preserved(self):
        for name, identity in [('Example.Movie.2009.COMPLETE.UHD.BLURAY-GROUP', '38'), ('Example.Movie.2009.COMPLETE.BLURAY-GROUP', '3'), ('Example.Movie.2009.1080p.COMPLETE.BLURAY-GROUP','3'), ('Example.Movie.2009.2160p.COMPLETE.BLURAY-GROUP','38')]:
            with self.subTest(name=name):
                self.assertEqual(self.category(name)[1], identity)
        for marker in ['COMPLETE']:
            with self.subTest(marker=marker):
                self.assertEqual(self.category(f'Example.Movie.2009.2160p.UHD.BluRay.{marker}.H265-GROUP')[1], '38')

    def test_only_complete_releases_enter_disc_categories(self):
        for marker in ['', 'BDMV', 'ISO', 'BD50', 'BD66', 'BD100', 'HEVC', 'AVC', 'x265', 'H265', 'INCOMPLETE']:
            name=f'Example.Movie.2009.2160p.UHD.BluRay.{marker}-GROUP'
            with self.subTest(name=name):
                self.assertEqual(self.category(name)[1], '4')
        self.assertEqual(self.category('Ringu.1998.2160p.GBR.Blu-ray.UHD.HEVC.DTS-HD.MA.5.1')[1], '4')
        self.assertEqual(self.category('Example.Movie.2009.UHD.BluRay.HEVC-GROUP')[1], '4')
        self.assertEqual(self.category('COMPLETE.Example.Movie.2009.UHD.BluRay-GROUP')[1], '38')
        self.assertEqual(self.category('Example.Movie.2009.2160p.COMPLETE.UHD.BluRay.REMUX-GROUP')[1], '4')

    def test_related_resolution_categories_are_preserved(self):
        for name, identity in [('Example.Movie.2009.2160p.UHD.Blu-ray.H.265-GROUP', '4'), ('Example.Movie.2009.2160p.UHD.BluRay.REMUX.HEVC-GROUP', '4'), ('Example.Show.S01E01.2160p.UHD.BluRay.x265-GROUP', '13'), ('Example.Show.S01.2160p.UHD.BluRay.x265-GROUP', '12'), ('Example.Movie.2009.1080p.BluRay.x264-GROUP', '6')]:
            with self.subTest(name=name):
                self.assertEqual(self.category(name)[1], identity)

    def test_explicit_manual_category_remains_an_override(self):
        self.assertEqual(self.category('Duplicity.2009.2160p.UHD.BluRay.H265-MALUS', manual_id=38), ('Manual Override', '38'))


if __name__ == '__main__':
    unittest.main()
