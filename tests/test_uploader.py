import configparser
from email.parser import BytesParser
from email.policy import default
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import uploader


class UploaderTests(unittest.TestCase):
    def setUp(self):
        (ROOT / 'tmp').mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix='digitalcore-test-', dir=ROOT / 'tmp')
        self.root = Path(self.temp.name)
        self.release = self.root / 'content' / 'Test.Release'
        self.release.mkdir(parents=True)
        (self.release / 'payload.txt').write_text('test payload ' * 1000)
        self.cfg = self.root / 'config.ini'
        self.cfg.write_text(f'''[Website]
API_KEY = test-key-with-%-and-#-characters
SITEURL = https://example.invalid
[Torrent]
ANNOUNCEURL = https://example.invalid/announce/test
[Paths]
DATADIR = {self.root / 'content'}
WATCHFOLDER = {self.root / 'watch'}
TMP_DIR = {self.root / 'tmp'}
UPLOADLOG = {self.root / 'logs' / 'upload.log'}
[Settings]
SCREENSHOTS = false
MEDIAINFO = false
IMAGE_UPLOAD = false
DUPECHECK = false
POST_METADATA = false
[Network]
TorrentDownloadRetries = 1
TorrentDownloadRetryDelay = 0
''', encoding='utf-8')
        self.env = patch.dict(os.environ, DIGITALCORE_CONFIG=str(self.cfg))
        self.env.start()
        self.old_cwd = Path.cwd()
        os.chdir(ROOT)
        self.config = uploader.load_config()

    def tearDown(self):
        os.chdir(self.old_cwd)
        self.env.stop()
        from utils.config_loader import ConfigLoader
        ConfigLoader._cache.clear()
        self.temp.cleanup()

    def job(self, **kwargs):
        return uploader.validate_job(self.release, 25, config=self.config, **kwargs)

    def test_percent_api_key_loads_without_interpolation(self):
        self.assertIn('%', self.config.get('Website', 'API_KEY'))
        self.assertEqual(uploader.check_config(self.config), [])

    def test_all_categories_are_unique(self):
        self.assertEqual({row['id'] for row in uploader.categories()}, set(range(1, 45)) - {16, 19})

    def test_validate_review_metadata_and_images(self):
        job = self.job(description='Test description', images=['https://example.invalid/cover.jpg'],
                       metadata={'studio': 'Studio', 'performers': 'A, B', 'content_tags': 'Test', 'password': 'omit'})
        self.assertEqual(job['metadata']['studio'], 'Studio')
        self.assertNotIn('password', job['metadata'])
        self.assertIn('Test description', uploader.review_text(job, self.config))
        self.assertNotIn(self.config.get('Website', 'API_KEY'), uploader.review_text(job, self.config))

    def test_invalid_category(self):
        with self.assertRaisesRegex(ValueError, 'category'):
            uploader.validate_job(self.release, 999, config=self.config)

    def test_digitalcore_metadata_validation(self):
        with self.assertRaisesRegex(ValueError, 'IMDb'):
            self.job(metadata={'imdbId': '12345'})
        with self.assertRaisesRegex(ValueError, 'HTTPS'):
            self.job(metadata={'gameUrl': 'https://evil.invalid/game'})
        self.assertEqual(self.job(metadata={'imdbId': 'tt1234567'})['metadata']['imdbId'], 'tt1234567')

    def test_empty_folder(self):
        empty = self.root / 'empty'
        empty.mkdir()
        with self.assertRaisesRegex(ValueError, 'empty'):
            uploader.validate_job(empty, 25, config=self.config)

    def test_invalid_images(self):
        for url in ('file:///etc/passwd', 'https://example.invalid/a[/img]', 'not-a-url'):
            with self.subTest(url=url), self.assertRaises(ValueError):
                self.job(images=[url])

    def test_invalid_year(self):
        with self.assertRaises(ValueError):
            self.job(metadata={'release_year': 'tomorrow'})

    def test_write_checks_clean_their_files(self):
        uploader.check_writable(self.config)
        self.assertTrue((self.root / 'watch').is_dir())
        self.assertEqual(list(self.root.rglob('.write-check-*')), [])

    def test_dry_run_does_not_upload_or_hash(self):
        result = subprocess.run([sys.executable, str(ROOT / 'uploader.py'), 'upload', str(self.release),
                                 '--cat', '25', '--studio', 'Test Studio', '--dry-run'],
                                capture_output=True, text=True, env=os.environ)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('Test Studio', result.stdout)
        self.assertFalse((self.root / 'watch').exists())
        self.assertFalse((self.root / 'tmp').exists())

    def test_watch_fingerprint_changes_after_file_update(self):
        first = uploader.fingerprint(self.release)
        (self.release / 'payload.txt').write_text('changed')
        self.assertNotEqual(first, uploader.fingerprint(self.release))

    def test_container_paths_are_mapped(self):
        with patch.dict(os.environ, DIGITALCORE_CONTAINER='1'):
            config = uploader.load_config()
            self.assertEqual(config.get('Paths', 'DATADIR'), '/content')
            self.assertEqual(config.get('Paths', 'WATCHFOLDER'), '/watch')

    def test_docker_uses_config_paths_without_a_shell(self):
        spec = importlib.util.spec_from_file_location('docker_run', ROOT / 'docker-run.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        cmd = module.command(['upload', str(self.release), '--cat', '25'], self.config)
        self.assertIn('/content/Test.Release', cmd)
        self.assertTrue(any(str(self.root / 'content') in value for value in cmd))
        self.assertNotIn('-p', cmd)

    def test_upload_lock_blocks_parallel_upload(self):
        with uploader.upload_lock():
            with self.assertRaisesRegex(ValueError, 'Another upload'):
                with uploader.upload_lock():
                    pass

    def serve(self, mode='success'):
        state = {'posts': [], 'torrent': b'', 'mode': mode, 'images': 0}
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def do_POST(self):
                body = self.rfile.read(int(self.headers['Content-Length']))
                message = BytesParser(policy=default).parsebytes(
                    b'Content-Type: ' + self.headers['Content-Type'].encode() + b'\r\n\r\n' + body)
                if '/images/upload' in self.path:
                    state['images'] += 1
                    self.send_response(200)
                    self.end_headers()
                    self.wfile.write(b'{"data":{"link":"https://example.invalid/uploaded.jpg"}}')
                    return
                fields = {}
                for part in message.iter_parts():
                    name = part.get_param('name', header='content-disposition')
                    value = part.get_payload(decode=True)
                    if name == 'file':
                        state['torrent'] = value
                    else:
                        fields[name] = value.decode('utf-8')
                state['posts'].append(fields)
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(b'{"id":123,"name":"Test.Release"}' if mode != 'bad-confirmation' else b'not JSON')
            def do_GET(self):
                if 'exact_search' in self.path:
                    data = b'not JSON' if mode == 'bad-dupe-check' else b'[{"id":123,"name":"Test.Release"}]'
                else:
                    data = state['torrent'] if mode != 'bad-download' else b'invalid torrent'
                self.send_response(200)
                self.end_headers()
                self.wfile.write(data)
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.cfg.write_text(self.cfg.read_text().replace('https://example.invalid\n',
                            f'http://127.0.0.1:{server.server_port}\n'))
        if mode in ('duplicate', 'bad-dupe-check'):
            self.cfg.write_text(self.cfg.read_text().replace('DUPECHECK = false', 'DUPECHECK = true'))
        with self.cfg.open('a') as config_file:
            config_file.write(f'\n[ImageHost]\nPROVIDER = custom\nUPLOADIMGURL = http://127.0.0.1:{server.server_port}/api/v1/images/upload\n')
        self.config = uploader.load_config()
        return state

    def test_full_upload_hash_metadata_download_and_watch_folder(self):
        state = self.serve()
        job = self.job(description='Custom BBCode description',
                       images=['https://example.invalid/cover.jpg'],
                       metadata={'studio': 'Test Studio', 'performers': 'A, B', 'content_tags': 'Test Genre', 'imdbId': 'tt1234567', 'gameUrl': 'https://store.steampowered.com/app/12345/'})
        output = []
        events = []
        def emit(line):
            output.append(line)
        emit.event = events.append
        result = uploader.run_job(job, self.config, emit)
        self.assertEqual(result, 0, '\n'.join(output))
        self.assertTrue(any(event.get('stage') == 'hashing' for event in events))
        self.assertTrue(any(event.get('torrent_id') == 123 and event.get('site_status') == 'accepted' for event in events))
        self.assertTrue(any(event.get('watch_torrent') for event in events))
        self.assertEqual(len(state['posts']), 1)
        fields = state['posts'][0]
        self.assertEqual(fields['studio'], 'Test Studio')
        self.assertEqual(fields['content_tags'], 'Test Genre')
        self.assertEqual(fields['category'], '25')
        self.assertEqual(fields['imdbId'], 'tt1234567')
        self.assertEqual(fields['gameUrl'], 'https://store.steampowered.com/app/12345/')
        self.assertEqual(fields['othergenre'], 'Test Genre')
        self.assertIn('Production: Test Studio', fields['nfo'])
        self.assertIn('Custom BBCode description', fields['nfo'])
        self.assertIn('https://example.invalid/cover.jpg', fields['nfo'])
        from torrent_fixtures import Torrent
        torrents = list((self.root / 'watch').glob('*.torrent'))
        self.assertEqual(len(torrents), 1)
        torrent = Torrent.read(str(torrents[0]))
        torrent.validate()
        self.assertTrue(torrent.private)
        self.assertEqual(torrent.source, 'DigitalCore')
        self.assertEqual((self.release / 'payload.txt').read_text(), 'test payload ' * 1000)

    def test_invalid_download_is_not_published_to_watch_folder(self):
        self.serve('bad-download')
        output = []
        self.assertNotEqual(uploader.run_job(self.job(), self.config, output.append), 0)
        self.assertEqual(list((self.root / 'watch').glob('*.torrent')), [])
        self.assertIn('accepted', (self.root / 'logs/upload.log').read_text())

    def test_unknown_confirmation_stops_without_download(self):
        self.serve('bad-confirmation')
        output = []
        self.assertNotEqual(uploader.run_job(self.job(), self.config, output.append), 0)
        self.assertIn('Check the site before retrying', '\n'.join(output))
        self.assertEqual(list((self.root / 'watch').glob('*.torrent')), [])

    def test_duplicate_skips_post(self):
        state = self.serve('duplicate')
        output = []
        self.assertEqual(uploader.run_job(self.job(), self.config, output.append), 10, '\n'.join(output))
        self.assertEqual(state['posts'], [])

    def test_failed_duplicate_check_never_posts(self):
        state = self.serve('bad-dupe-check')
        self.assertNotEqual(uploader.run_job(self.job(), self.config, lambda _: None), 0)
        self.assertEqual(state['posts'], [])

    def test_images_are_uploaded_from_the_folder(self):
        state = self.serve()
        import base64
        (self.release / 'cover.gif').write_bytes(base64.b64decode('R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7'))
        self.cfg.write_text(self.cfg.read_text().replace('IMAGE_UPLOAD = false', 'IMAGE_UPLOAD = true'))
        self.config = uploader.load_config()
        output = []
        self.assertEqual(uploader.run_job(self.job(), self.config, output.append), 0, '\n'.join(output))
        self.assertEqual(state['images'], 1)
        self.assertIn('https://example.invalid/uploaded.jpg', state['posts'][0]['nfo'])

    def test_watcher_records_attempt_and_never_retries_automatically(self):
        # Isolate watcher state; do not change the application paths used by backend subprocesses.
        (self.root / 'files').mkdir()
        shutil.copyfile(ROOT / 'files/digitalcore-categories.json', self.root / 'files/digitalcore-categories.json')
        with patch.object(uploader, 'ROOT', self.root), \
             patch.object(uploader.time, 'monotonic', side_effect=range(0, 1000, 10)), \
             patch.object(uploader.time, 'sleep', side_effect=[None, None, KeyboardInterrupt]), \
             patch.object(uploader, 'run_job', return_value=1) as run:
            uploader.watch(self.config, 25, interval=5, stable=5)
            self.assertEqual(run.call_count, 1)
        with patch.object(uploader, 'ROOT', self.root), \
             patch.object(uploader.time, 'sleep', side_effect=KeyboardInterrupt), \
             patch.object(uploader, 'run_job') as run:
            uploader.watch(self.config, 25, interval=5, stable=5)
            run.assert_not_called()

    def test_watcher_limits_uploads_to_configured_or_explicit_incoming_folder(self):
        (self.root/'files').mkdir()
        shutil.copyfile(ROOT/'files/digitalcore-categories.json',self.root/'files/digitalcore-categories.json')
        library=self.root/'content'
        for name in ['incoming-a','incoming-b']:
            release=library/name/'Ready.Release';release.mkdir(parents=True)
            (release/'payload.txt').write_text('completed content')
        self.config.add_section('Watch')
        self.config.set('Watch','INCOMING_DIR','incoming-a')
        for override,expected in [(None,'incoming-a'),('incoming-b','incoming-b')]:
            with patch.object(uploader,'ROOT',self.root), \
                 patch.object(uploader.time,'monotonic',side_effect=range(0,1000,10)), \
                 patch.object(uploader.time,'sleep',side_effect=[None,None,KeyboardInterrupt]), \
                 patch.object(uploader,'run_job',return_value=0) as run:
                uploader.watch(self.config,25,interval=5,stable=5,folder=override)
                run.assert_called_once()
                self.assertEqual(Path(run.call_args[0][0]['source']),library/expected/'Ready.Release')
        with self.assertRaisesRegex(ValueError,'inside the configured'):
            uploader.watch(self.config,25,folder=str(self.root))

    @unittest.skipUnless(shutil.which('mediainfo') and shutil.which('ffmpeg'), 'Media tools are not installed')
    def test_real_mediainfo_from_generated_video(self):
        subprocess.run(['ffmpeg', '-loglevel', 'error', '-f', 'lavfi', '-i',
                        'color=c=blue:s=320x240:d=1', '-c:v', 'mpeg4', str(self.release / 'video.mp4')], check=True)
        from utils.mediainfo_utils import generate_mediainfo
        output = self.root / 'mediainfo'
        output.mkdir()
        result = generate_mediainfo(self.release, output)
        self.assertIn('Video', result.read_text())

    @unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'FFmpeg is not installed')
    def test_screenshots_are_real_images(self):
        video = self.release / 'video.mp4'
        subprocess.run(['ffmpeg', '-loglevel', 'error', '-f', 'lavfi', '-i',
                        'color=c=blue:s=320x240:d=3', '-c:v', 'mpeg4', str(video)], check=True)
        from utils.screenshot_utils import generate_screenshots
        self.config.set('MediaTools','SCREENSHOT_ENGINE','ffmpeg')
        generate_screenshots(self.release, 3)
        screens = list((self.root / 'tmp' / str(os.getpid()) / 'screens').glob('*.jpg'))
        self.assertEqual(len(screens), 5)
        self.assertTrue(all(p.read_bytes().startswith(b'\xff\xd8') for p in screens))



if __name__ == '__main__':
    unittest.main()
