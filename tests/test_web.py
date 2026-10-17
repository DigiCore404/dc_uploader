import configparser
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import uploader
import web


class WebTests(unittest.TestCase):
    def test_web_start_does_not_interrupt_active_cli_upload(self):
        self.server.workspace.history.create('external-cli', {'source': str(self.root / 'Release'), 'category': 6})
        with uploader.upload_lock():
            another = web.Workspace('test-access', self.root / 'uploads.db')
        self.assertEqual(another.history.get('external-cli')['status'], 'running')

    def test_public_image_host_config_uses_official_public_key(self):
        path = self.base + '/api/image-host/public-config'
        self.assertEqual(self.client.get(path).status_code, 401)
        self.login()
        with patch('utils.digitalcore_images.authentication', return_value='public-upload-key'):
            response = self.client.get(path)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'endpoint': 'https://img.digitalcore.club/api/upload', 'public_key': 'public-upload-key'})

    def setUp(self):
        (ROOT / 'tmp').mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / 'tmp')
        self.root = Path(self.temp.name)
        self.content = self.root / 'content'
        self.release = self.content / 'Sample.Release'
        self.release.mkdir(parents=True)
        (self.release / 'video.mp4').write_bytes(b'offline test content')
        (self.release / 'cover.jpg').write_bytes(b'offline image')
        self.config = self.root / 'config.ini'
        self.config.write_text(f'''[Website]
SITEURL = https://example.invalid
API_KEY = fake-key-with-%-characters
[Torrent]
ANNOUNCEURL = https://example.invalid/announce/private-key
[Paths]
DATADIR = {self.content}
WATCHFOLDER = {self.root / 'watch'}
TMP_DIR = {self.root / 'scratch'}
UPLOADLOG = {self.root / 'logs/upload.log'}
[Settings]
IMAGE_UPLOAD = false
SCREENSHOTS = false
MEDIAINFO = false
''', encoding='utf-8')
        self.environment = patch.dict(os.environ, DIGITALCORE_CONFIG=str(self.config), DIGITALCORE_CONTAINER='0')
        self.environment.start()
        self.server = web.create_server('127.0.0.1', 0, password='test-access', history_path=self.root / 'uploads.db')
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f'http://127.0.0.1:{self.server.server_port}'
        self.client = requests.Session()
        self.client.trust_env = False
        self.csrf = ''

    def tearDown(self):
        self.client.close()
        self.server.shutdown()
        self.server.server_close()
        self.environment.stop()
        from utils.config_loader import ConfigLoader
        ConfigLoader._cache.clear()
        self.temp.cleanup()

    def post(self, path, data, **kwargs):
        return self.client.post(self.base + path, json=data,
                                headers={'X-CSRF-Token': self.csrf, 'Origin': self.base}, timeout=5, **kwargs)

    def login(self):
        response = self.post('/api/login', {'username': 'admin', 'password': 'test-access'})
        self.assertEqual(response.status_code, 200)
        self.assertIn('HttpOnly', response.headers['Set-Cookie'])
        self.assertIn('SameSite=Strict', response.headers['Set-Cookie'])
        state = self.client.get(self.base + '/api/state').json()
        self.csrf = state['csrf']
        return state

    def review(self):
        return self.post('/api/review', {'source': str(self.release), 'category': 3,
                         'description': 'An offline preview', 'metadata': {'studio': 'Test Studio'}, 'images': []})

    def test_image_preview_requires_session_csrf_and_only_returns_images(self):
        endpoint='/api/images/preview'
        with patch.object(web,'public_download') as download:
            self.assertEqual(self.post(endpoint,{'url':'https://example.invalid/cover.jpg'}).status_code,401)
            download.assert_not_called()
        self.login()
        csrf=self.csrf;self.csrf='invalid'
        with patch.object(web,'public_download') as download:
            self.assertEqual(self.post(endpoint,{'url':'https://example.invalid/cover.jpg'}).status_code,403)
            download.assert_not_called()
        self.csrf=csrf
        with patch.object(web,'public_download',return_value=(b'\xff\xd8\xffimage','image/jpeg','https://example.invalid/cover.jpg')) as download, patch.object(web.image_host,'upload') as upload:
            response=self.post(endpoint,{'url':'https://example.invalid/cover.jpg'})
            self.assertEqual(response.status_code,200)
            self.assertTrue(response.json()['image'].startswith('data:image/jpeg;base64,'))
            download.assert_called_once_with('https://example.invalid/cover.jpg',limit=web.image_host.MAX_IMAGE)
            upload.assert_not_called()
        with patch.object(web,'public_download',return_value=(b'<script>bad</script>','text/html','https://example.invalid/cover.jpg')):
            self.assertEqual(self.post(endpoint,{'url':'https://example.invalid/cover.jpg'}).status_code,400)
        self.assertEqual(self.post(endpoint,{'url':'https://127.0.0.1/cover.jpg'}).status_code,400)

    def test_metadata_ignore_words_persist_and_allow_empty_list(self):
        state=self.login()
        self.assertIn('XXX',state['settings']['metadata_ignore_words'])
        self.assertIn('WEB-DL',state['settings']['metadata_ignore_words'])
        result=self.post('/api/settings',{'metadata_ignore_words':'XXX, WEB-DL\nGROUP\nxxx'})
        self.assertEqual(result.status_code,200)
        self.assertEqual(result.json()['settings']['metadata_ignore_words'],['XXX','WEB-DL','GROUP'])
        self.assertEqual(self.client.get(self.base+'/api/state').json()['settings']['metadata_ignore_words'],['XXX','WEB-DL','GROUP'])
        self.assertEqual(self.post('/api/settings',{'metadata_ignore_words':'bad'*3000}).status_code,400)
        result=self.post('/api/settings',{'metadata_ignore_words':''})
        self.assertEqual(result.status_code,200)
        self.assertEqual(result.json()['settings']['metadata_ignore_words'],[])

    def test_html_assets_and_health_are_served_without_desktop(self):
        for path in ('/', '/app.js', '/app.css', '/health'):
            response = self.client.get(self.base + path)
            self.assertEqual(response.status_code, 200)
        self.assertIn('Share something good', self.client.get(self.base).text)
        self.assertIn("frame-ancestors 'none'", response.headers['Content-Security-Policy'])

    def test_tmdb_token_is_saved_but_never_returned_and_blank_keeps_it(self):
        self.login()
        response = self.post('/api/settings', {'tmdb_token': 'private-tmdb-token'})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['settings']['has_tmdb_token'])
        self.assertNotIn('private-tmdb-token', response.text)
        self.post('/api/settings', {'tmdb_token': ''})
        self.assertEqual(uploader.load_config().get('TMDB', 'TOKEN'), 'private-tmdb-token')
        with patch.object(web.tmdb, 'search', return_value=[]) as search:
            self.assertEqual(self.post('/api/tmdb/search', {'query': 'Example'}).status_code, 200)
            search.assert_called_once_with('private-tmdb-token', 'Example', 'movie')

    def test_igdb_credentials_are_private_preserved_and_used_only_server_side(self):
        self.login()
        response=self.post('/api/settings',{'igdb_client_id':'example-client','igdb_client_secret':'example-secret'})
        self.assertEqual(response.status_code,200,response.text)
        self.assertTrue(response.json()['settings']['has_igdb_credentials'])
        self.assertNotIn('example-secret',response.text)
        self.assertNotIn('example-client',response.text)
        self.post('/api/settings',{'igdb_client_id':'','igdb_client_secret':''})
        with patch.object(web.igdb,'search',return_value=[]) as search:
            self.assertEqual(self.post('/api/igdb/search',{'query':'Example'}).status_code,200)
            search.assert_called_once_with('example-client','example-secret','Example')

    def test_tvmaze_requires_auth_and_csrf_but_no_api_key(self):
        self.assertEqual(self.post('/api/tvmaze/search',{'query':'Example'}).status_code,401)
        self.login()
        with patch.object(web.tvmaze,'search',return_value=[]) as search:
            self.assertEqual(self.post('/api/tvmaze/search',{'query':'Example'}).status_code,200)
            search.assert_called_once_with('Example')
        self.csrf='wrong'
        with patch.object(web.tvmaze,'search') as search:
            self.assertEqual(self.post('/api/tvmaze/search',{'query':'Example'}).status_code,403)
            search.assert_not_called()

    def test_import_defaults_are_saved_and_mapping_check_is_authenticated(self):
        self.assertEqual(self.post('/api/delivery/check',{}).status_code,401)
        self.login()
        identity=self.server.workspace.clients.save({'name':'Seedbox','url':'http://example.invalid'})
        response=self.post('/api/settings',{'import_client':identity,'import_remote_root':'/downloads/',
                                           'import_local_root':str(self.content)})
        self.assertEqual(response.status_code,200,response.text)
        settings=response.json()['settings']
        self.assertEqual(settings['import_remote_root'],'/downloads')
        self.assertEqual(settings['import_local_root'],str(self.content))
        self.assertEqual(settings['import_client'],identity)
        self.assertEqual(self.post('/api/settings',{'import_remote_root':'/downloads/../etc'}).status_code,400)
        self.assertEqual(self.post('/api/settings',{'import_local_root':str(self.root)}).status_code,400)
        self.assertEqual(uploader.load_config().get('Imports','REMOTE_ROOT'),'/downloads')
        with patch('web.check_mapping',return_value={'status':'verified','message':'sample checked'}) as check:
            response=self.post('/api/delivery/check',{'client_id':identity,'remote_root':'/downloads','local_root':str(self.content)})
            self.assertEqual(response.json()['status'],'verified')
            check.assert_called_once()

    def test_docker_import_defaults_validate_against_container_mount_not_raw_host_path(self):
        self.login()
        with patch.dict(os.environ,DIGITALCORE_CONTAINER='1'), \
             patch('web.content_path',return_value=Path('/content')) as validate:
            response=self.post('/api/settings',{'import_local_root':'/content'})
            self.assertEqual(response.status_code,200,response.text)
            self.assertEqual(validate.call_args[0][1].get('Paths','DATADIR'),'/content')

    def test_import_endpoints_require_auth_and_csrf(self):
        self.assertEqual(self.client.get(self.base + '/api/imports').status_code, 401)
        self.login()
        self.assertEqual(self.client.get(self.base + '/api/imports').json()['imports'], [])
        self.csrf = 'wrong'
        with patch.object(self.server.workspace.imports, 'add') as add:
            self.assertEqual(self.post('/api/imports/add', {}).status_code, 403)
            add.assert_not_called()

    def test_access_settings_require_current_password_and_revoke_sessions(self):
        self.login()
        self.assertEqual(self.post('/api/security', {'username': 'owner', 'current_password': 'wrong'}).status_code, 400)
        response = self.post('/api/security', {'username': 'owner', 'current_password': 'test-access', 'password': 'long-new-password', 'allowed_ips': ['127.0.0.1']})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.client.get(self.base + '/api/state').status_code, 401)
        self.assertEqual(self.post('/api/login', {'username': 'admin', 'password': 'test-access'}).status_code, 401)
        self.assertEqual(self.post('/api/login', {'username': 'owner', 'password': 'long-new-password'}).status_code, 200)

    def test_ip_block_applies_to_login_assets_and_ignores_forwarded_headers(self):
        self.login()
        with self.server.workspace.history.connect() as db:
            db.execute('UPDATE workspace_security SET allowed_ips=? WHERE id=1', ('["10.0.0.0/8"]',))
        for path in ('/', '/api/state', '/health'):
            self.assertEqual(self.client.get(self.base + path, headers={'X-Forwarded-For': '10.0.0.1'}).status_code, 403)
        self.assertEqual(self.post('/api/login', {'username': 'admin', 'password': 'test-access'}).status_code, 403)

    def test_image_host_settings_preserve_and_redact_key(self):
        self.login()
        response=self.post('/api/settings',{'image_host':'imghost','image_host_key':'private-image-key'})
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.json()['settings']['image_host'],'imghost')
        self.assertNotIn('private-image-key',response.text)
        self.post('/api/settings',{'image_host':'imghost','image_host_key':''})
        self.assertEqual(uploader.load_config().get('ImageHost','API_KEY'),'private-image-key')

    def test_manual_image_upload_uses_server_host_and_returns_only_link(self):
        import base64
        self.login()
        with patch.object(web.image_host,'upload',return_value={'url':'https://example.com/cover.gif','provider':'imghost'}) as upload:
            response=self.post('/api/images/upload',{'filename':'cover.gif','image':base64.b64encode(b'GIF89a').decode()})
            self.assertEqual(response.status_code,200,response.text)
            self.assertEqual(upload.call_args.args[1:],('cover.gif',b'GIF89a'))
            self.assertEqual(response.json()['url'],'https://example.com/cover.gif')
        self.csrf='wrong'
        with patch.object(web.image_host,'upload') as upload:
            self.assertEqual(self.post('/api/images/upload',{'image':'invalid'}).status_code,403)
            upload.assert_not_called()

    def test_catalogue_fetch_and_rehost_need_authenticated_csrf(self):
        self.assertEqual(self.post('/api/catalogue/fetch',{'url':'https://example.com/'}).status_code,401)
        self.login()
        self.assertEqual(self.post('/api/catalogue/fetch',{'url':'https://example.com/'}).status_code,400)
        with patch.object(web,'public_download',return_value=(b'GIF89a','image/gif','https://example.com/cover.gif')),patch.object(web.image_host,'upload',return_value={'url':'https://imghost.dev/cover.gif','provider':'imghost'}):
            self.assertEqual(self.post('/api/images/rehost',{'url':'https://example.com/cover.gif'}).status_code,200)

    def test_api_requires_login(self):
        self.assertEqual(self.client.get(self.base + '/api/state').status_code, 401)
        self.assertEqual(self.client.get(self.base + '/api/folders').status_code, 401)

    def test_login_and_rate_limit(self):
        for _ in range(8):
            self.assertEqual(self.post('/api/login', {'username': 'admin', 'password': 'wrong'}).status_code, 401)
        self.assertEqual(self.post('/api/login', {'username': 'admin', 'password': 'test-access'}).status_code, 429)

    def test_state_does_not_return_credentials(self):
        state = self.login()
        self.assertTrue(state['ready'])
        encoded = json.dumps(state)
        self.assertNotIn('fake-key-with', encoded)
        self.assertNotIn('announce/private-key', encoded)

    def test_csrf_is_required_for_changes(self):
        self.login()
        self.csrf = 'incorrect'
        self.assertEqual(self.post('/api/settings', {}).status_code, 403)

    def test_cross_origin_request_is_rejected(self):
        response = self.client.post(self.base + '/api/login', json={'username': 'admin', 'password': 'test-access'},
                                    headers={'Origin': 'https://other.invalid'})
        self.assertEqual(response.status_code, 403)

    def test_folder_browser_is_confined_to_content(self):
        self.login()
        self.assertEqual(self.client.get(self.base + '/api/folders', params={'path': str(self.root)}).status_code, 400)
        result = self.client.get(self.base + '/api/folders').json()
        self.assertEqual(result['folders'][0]['name'], 'Sample.Release')
        self.assertIsNone(result['parent'])

    def test_review_validates_without_uploading(self):
        self.login()
        with patch.object(uploader, 'run_job') as run:
            response = self.review()
            self.assertEqual(response.status_code, 200, response.text)
            result = response.json()
            self.assertEqual(result['summary']['files'], 2)
            self.assertEqual(result['summary']['images'], 1)
            self.assertEqual(result['job']['metadata']['studio'], 'Test Studio')
            run.assert_not_called()

    def test_entire_library_cannot_be_uploaded_accidentally(self):
        self.login()
        response = self.post('/api/review', {'source': str(self.content), 'category': 3})
        self.assertEqual(response.status_code, 400)
        self.assertIn('release subfolder', response.json()['error'])

    def test_archives_are_flagged_before_publication(self):
        self.login()
        (self.release / 'release.rar').write_bytes(b'archive')
        response = self.review()
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()['ready'])
        self.assertIn('extracted', ' '.join(response.json()['errors']))
        self.assertEqual(self.post('/api/upload', {'review_id': response.json()['review_id']}).status_code, 400)

    def test_upload_uses_one_time_review_and_redacts_logs(self):
        self.login()
        token = self.review().json()['review_id']
        def run(job, config, emit):
            emit.event({'stage': 'downloading_torrent', 'site_status': 'accepted', 'torrent_id': 123})
            emit('log: ' + config.get('Website', 'API_KEY'))
            return 0
        with patch.object(uploader, 'run_job', side_effect=run) as worker:
            response = self.post('/api/upload', {'review_id': token})
            self.assertEqual(response.status_code, 202, response.text)
            identity = response.json()['id']
            for _ in range(50):
                result = self.client.get(self.base + '/api/jobs/' + identity).json()
                if result['status'] != 'running':
                    break
                time.sleep(.02)
            self.assertEqual(result['status'], 'completed')
            self.assertEqual(result['logs'], ['log: [redacted]'])
            self.assertEqual(result['torrent_id'], 123)
            self.assertEqual(result['exit_code'], 0)
            self.assertEqual(result['site_status'], 'accepted')
            self.assertTrue(any(e['stage'] == 'downloading_torrent' for e in result['events']))
            from utils.job_history import JobHistory
            self.assertEqual(JobHistory(self.root / 'uploads.db').get(identity)['status'], 'completed')
            self.assertEqual(self.post('/api/upload', {'review_id': token}).status_code, 400)
            worker.assert_called_once()

    def test_settings_save_keeps_secrets_and_invalidates_review(self):
        self.login()
        token = self.review().json()['review_id']
        response = self.post('/api/settings', {'api_key': '', 'announce': '', 'options': {'DUPECHECK': False}})
        self.assertEqual(response.status_code, 200, response.text)
        config = uploader.load_config()
        self.assertEqual(config.get('Website', 'API_KEY'), 'fake-key-with-%-characters')
        self.assertFalse(config.getboolean('Settings', 'DUPECHECK'))
        self.assertEqual(self.post('/api/upload', {'review_id': token}).status_code, 400)

    def test_interrupted_jobs_do_not_restart(self):
        path = self.root / 'history.json'
        path.write_text(json.dumps([{'id': 'test', 'status': 'running', 'name': 'Test', 'logs': []}]))
        with patch.object(uploader, 'ROOT', self.root):
            (self.root / 'data').mkdir()
            path.replace(self.root / 'data/web-history.json')
            with patch.object(uploader, 'run_job') as run:
                workspace = web.Workspace('test')
                self.assertEqual(workspace.history.get('test')['status'], 'interrupted')
                self.assertIsNone(workspace.active)
                run.assert_not_called()

    def test_history_pagination_and_status_filter(self):
        self.login()
        history = self.server.workspace.history
        for i in range(34):
            history.create(str(i), {'source': str(self.release), 'category': 3})
            history.finish(str(i), 'completed' if i % 2 == 0 else 'needs_review', i % 2)
        first = self.client.get(self.base + '/api/history?limit=20').json()
        second = self.client.get(self.base + '/api/history?limit=20&offset=20').json()
        failed = self.client.get(self.base + '/api/history?status=needs_review').json()
        self.assertEqual(first['total'], 34)
        self.assertEqual(len(first['jobs']), 20)
        self.assertEqual(len(second['jobs']), 14)
        self.assertEqual(failed['total'], 17)
        self.assertTrue(all(job['status'] == 'needs_review' for job in failed['jobs']))


    def test_full_config_settings_persist_and_redact_secrets(self):
        self.login()
        fields={'MediaTools':{'screenshot_sheet':'true','screenshot_singles':'3','mtnsetting':'-c 2 -r 8 -D 8'},'Settings':{'include_nfo':'true'},'ImageHost':{'authcode':'private-image-secret'}}
        response=self.post('/api/settings',{'config_fields':fields})
        self.assertEqual(response.status_code,200,response.text)
        self.assertNotIn('private-image-secret',response.text)
        self.assertEqual(uploader.load_config().getint('MediaTools','screenshot_singles'),3)
        self.assertTrue(uploader.load_config().getboolean('MediaTools','screenshot_sheet'))
        self.assertEqual(uploader.load_config().get('ImageHost','authcode'),'private-image-secret')
        invalid=self.post('/api/settings',{'config_fields':{'MediaTools':{'screenshot_singles':'99'}}})
        self.assertEqual(invalid.status_code,400)
        self.assertEqual(uploader.load_config().getint('MediaTools','screenshot_singles'),3)

    def test_list_api_search_sort_counts_and_pagination(self):
        self.login()
        history=self.server.workspace.history
        for i in range(6):
            history.create(str(i),{'source':'/content/Release.'+str(i),'category':3})
            history.finish(str(i),'completed',0)
        result=self.client.get(self.base+'/api/history?search=Release.3&sort=name&direction=asc').json()
        self.assertEqual(result['total'],1)
        self.assertEqual(result['jobs'][0]['name'],'Release.3')
        self.assertEqual(result['all_total'],6)
        with history.connect() as db:
            db.execute("INSERT INTO torrent_imports(id,client_id,source_hash,name,status,remote_path,local_path,details,created,updated,job_id) VALUES('test','client','hash','Needle','uploaded','/downloads/Needle','/content/Needle','{}',1,1,'3')")
        result=self.client.get(self.base+'/api/imports?search=Needle&status=completed&sort=name&direction=asc&limit=1').json()
        self.assertEqual(result['total'],1)
        self.assertEqual(result['counts']['completed'],1)
        self.assertEqual(result['imports'][0]['name'],'Needle')
        self.assertNotIn('details',result['imports'][0])


if __name__ == '__main__':
    unittest.main()
