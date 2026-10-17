from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from urllib.parse import parse_qs

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from utils.job_history import JobHistory
from utils.qbit_clients import QbitClients, state_group
import web

HASH = 'a' * 40


class QbitTests(unittest.TestCase):
    def setUp(self):
        (ROOT / 'tmp').mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / 'tmp')
        self.history = JobHistory(Path(self.temp.name) / 'uploads.db')
        self.clients = QbitClients(self.history)
        self.fake = {'version': 'v5.1.2', 'state': 'stalledUP', 'actions': [], 'bad_list': False}
        fake = self.fake
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def output(self, value, code=200):
                self.send_response(code)
                self.end_headers()
                self.wfile.write(value.encode())
            def do_POST(self):
                body = self.rfile.read(int(self.headers.get('Content-Length', '0')))
                data = parse_qs(body.decode(errors='replace'))
                if self.path.endswith('/auth/login'):
                    if data.get('username') == ['admin'] and data.get('password') == ['private-test-password']:
                        self.send_response(fake.get('login_status', 200))
                        self.send_header('Set-Cookie', 'SID=test-session; Path=/')
                        self.end_headers()
                        if fake.get('login_status', 200) != 204:
                            self.wfile.write(b'Ok.')
                    else:
                        self.output('Fails.')
                elif self.path.endswith('/torrents/add') and self.headers.get('Cookie') == 'SID=test-session':
                    fake['add_body'] = body
                    fake['add_content_type'] = self.headers.get('Content-Type')
                    self.output('' if fake.get('add_status') == 204 else 'Ok.', fake.get('add_status', 200))
                elif self.headers.get('Cookie') == 'SID=test-session':
                    fake['actions'].append((self.path, data))
                    fake['state'] = 'stoppedUP' if self.path.endswith('/stop') else 'pausedUP' if self.path.endswith('/pause') else 'uploading'
                    self.output('')
                else:
                    self.output('Forbidden', 403)
            def do_GET(self):
                if self.headers.get('Cookie') != 'SID=test-session':
                    return self.output('Forbidden', 403)
                if self.path.endswith('/app/version'):
                    self.output(fake['version'])
                elif self.path.split('?')[0].endswith('/torrents/files'):
                    self.output(json.dumps([{'name':'file.mp4','size':999,'progress':1}]))
                elif self.path.split('?')[0].endswith('/torrents/info') and '?' in self.path and HASH not in self.path:
                    self.output('[]')
                elif self.path.split('?')[0].endswith('/torrents/info'):
                    self.output('invalid JSON' if fake['bad_list'] else json.dumps([{
                        'hash': HASH, 'name': 'Same.Release', 'state': fake['state'], 'progress': 1,
                        'ratio': 2.5, 'uploaded': 123456, 'upspeed': 1000, 'size': 999,
                        'added_on': 1760000000, 'seeding_time': 3600, 'save_path': '/downloads', 'category': 'digitalcore'}]))
                else:
                    self.output('Not found', 404)
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.identity = self.clients.save({'name': 'Test client', 'url': f'http://127.0.0.1:{self.server.server_port}',
                                          'username': 'admin', 'password': 'private-test-password'})

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.temp.cleanup()

    def test_login_snapshot_and_password_redaction(self):
        self.assertTrue(self.clients.sync(self.identity)['ok'])
        public = self.clients.all()
        self.assertNotIn('private-test-password', json.dumps(public))
        self.assertTrue(public[0]['online'])
        torrents = self.clients.torrents()['torrents']
        self.assertEqual(torrents[0]['status'], 'seeding')
        self.assertEqual(torrents[0]['ratio'], 2.5)
        self.assertEqual(torrents[0]['added_on'], 1760000000)

    def test_existing_snapshot_migrates_without_losing_torrents(self):
        self.clients.sync(self.identity)
        with self.history.connect() as db:
            db.execute('ALTER TABLE qbit_torrents DROP COLUMN added_on')
        upgraded = QbitClients(self.history)
        torrent = upgraded.torrents()['torrents'][0]
        self.assertEqual(torrent['hash'], HASH)
        self.assertEqual(torrent['added_on'], 0)
        self.assertTrue(upgraded.sync(self.identity)['ok'])
        self.assertEqual(upgraded.torrents()['torrents'][0]['added_on'], 1760000000)

    def test_all_column_sorts_both_directions_and_pagination(self):
        self.clients.sync(self.identity)
        with self.history.connect() as db:
            original=dict(db.execute('SELECT * FROM qbit_torrents LIMIT 1').fetchone())
            db.execute('DELETE FROM qbit_torrents')
            for index,name in enumerate(['Zulu','alpha','Bravo']):
                row=dict(original,hash=str(index+1)*40,name=name,size=[100,300,200][index],
                         progress=[.4,.8,1][index],ratio=index,upspeed=3-index,dlspeed=index+1,
                         status=['seeding','downloading','paused'][index],added_on=[300,100,200][index])
                db.execute('INSERT INTO qbit_torrents('+','.join(row)+') VALUES('+','.join('?' for _ in row)+')',tuple(row.values()))
        for key in ['name','client','size','progress','status','upspeed','dlspeed','ratio','added_on']:
            for direction in ['asc','desc']:
                rows=self.clients.torrents(sort=key,direction=direction)['torrents']
                field='client_name' if key=='client' else key
                values=[r[field].lower() if isinstance(r[field],str) else r[field] for r in rows]
                self.assertEqual(values,sorted(values,reverse=direction=='desc'),(key,direction))
                page=self.clients.torrents(sort=key,direction=direction,offset=1,limit=1)
                self.assertEqual(page['torrents'][0]['hash'],rows[1]['hash'])
                self.assertEqual(page['total'],3)
        self.assertEqual(self.clients.torrents(sort='size; DROP TABLE qbit_clients',direction='invalid')['total'],3)

    def test_history_matches_exact_hash_and_never_name(self):
        self.clients.sync(self.identity)
        workspace = object.__new__(web.Workspace)
        workspace.clients = self.clients
        jobs = [{'name': 'Same.Release', 'torrent_hash': HASH},
                {'name': 'Same.Release', 'torrent_hash': 'b' * 40},
                {'name': 'Same.Release'}]
        workspace.enrich(jobs)
        self.assertEqual(jobs[0]['seeding_status'], 'seeding')
        self.assertEqual(jobs[1]['seeding_status'], 'not_found')
        self.assertEqual(jobs[2]['seeding_status'], 'unlinked')

    def test_qbit52_successful_login_uses_http204(self):
        self.fake['login_status'] = 204
        self.fake['version'] = 'v5.2.4'
        self.assertTrue(self.clients.sync(self.identity)['ok'])
        self.assertEqual(self.clients.all()[0]['version'], 'v5.2.4')

    def test_import_magnet_file_and_completion_http_contract(self):
        self.fake['login_status'] = 204
        self.fake['add_status'] = 204
        magnet = 'magnet:?xt=urn:btih:' + 'b' * 40
        self.clients.add_torrent(self.identity, 'b' * 40, '/downloads/import', magnet=magnet)
        data = parse_qs(self.fake['add_body'].decode())
        self.assertEqual(data['savepath'], ['/downloads/import'])
        self.assertEqual(data['urls'], [magnet])
        self.assertEqual(data['autoTMM'], ['false'])
        self.assertEqual(data['contentLayout'], ['Original'])
        self.clients.add_torrent(self.identity, 'b' * 40, '/downloads/Sample.Release', magnet=magnet, content_layout='NoSubfolder')
        data = parse_qs(self.fake['add_body'].decode())
        self.assertEqual(data['savepath'], ['/downloads/Sample.Release'])
        self.assertEqual(data['contentLayout'], ['NoSubfolder'])
        self.clients.add_torrent(self.identity, 'b' * 40, '/downloads/import', raw=b'test-torrent-content')
        self.assertIn('multipart/form-data', self.fake['add_content_type'])
        self.assertIn(b'test-torrent-content', self.fake['add_body'])
        row, files = self.clients.import_status(self.identity, HASH)
        self.assertEqual(row['hash'], HASH)
        self.assertEqual(files[0]['progress'], 1)
        with self.assertRaisesRegex(ValueError, 'already on the client'):
            self.clients.add_torrent(self.identity, HASH, '/downloads/import', magnet=magnet)

    def test_multiple_clients_and_filters(self):
        other = self.clients.save({'name': 'Second client', 'url': f'http://127.0.0.1:{self.server.server_port}',
                                   'username': 'admin', 'password': 'private-test-password'})
        self.clients.sync_all()
        self.assertEqual(len(self.clients.matches(HASH)), 2)
        self.assertEqual(self.clients.torrents(client_id=other)['total'], 1)
        self.assertEqual(self.clients.torrents(status='seeding')['total'], 2)
        self.assertEqual(self.clients.torrents(query='does not exist')['total'], 0)

    def test_pause_resume_qbit5(self):
        self.clients.sync(self.identity)
        self.clients.action(self.identity, HASH, 'pause')
        self.assertEqual(self.fake['actions'][-1][0], '/api/v2/torrents/stop')
        self.assertEqual(self.clients.matches(HASH)[0]['status'], 'paused')
        self.clients.action(self.identity, HASH, 'resume')
        self.assertEqual(self.fake['actions'][-1][0], '/api/v2/torrents/start')

    def test_pause_resume_qbit4(self):
        self.fake['version'] = 'v4.6.7'
        result = self.clients.sync(self.identity)
        self.assertTrue(result['ok'], result)
        self.clients.action(self.identity, HASH, 'pause')
        self.assertEqual(self.fake['actions'][-1][0], '/api/v2/torrents/pause')
        self.clients.action(self.identity, HASH, 'resume')
        self.assertEqual(self.fake['actions'][-1][0], '/api/v2/torrents/resume')

    def test_failed_sync_keeps_snapshot_but_marks_it_stale(self):
        self.clients.sync(self.identity)
        self.fake['bad_list'] = True
        self.assertFalse(self.clients.sync(self.identity)['ok'])
        self.assertFalse(self.clients.all()[0]['online'])
        self.assertTrue(self.clients.torrents()['torrents'][0]['stale'])
        workspace = object.__new__(web.Workspace)
        workspace.clients = self.clients
        self.assertEqual(workspace.enrich([{'torrent_hash': HASH}])[0]['seeding_status'], 'unknown')

    def test_bulk_and_unknown_hash_actions_are_rejected(self):
        with self.assertRaises(ValueError):
            self.clients.action(self.identity, 'all', 'pause')
        with self.assertRaises(ValueError):
            self.clients.action(self.identity, HASH, 'delete')
        with self.assertRaises(ValueError):
            self.clients.action(self.identity, 'b' * 40, 'pause')
        self.assertEqual(self.fake['actions'], [])

    def test_failed_login_and_disconnect(self):
        self.clients.save({'id': self.identity, 'name': 'Client', 'url': f'http://127.0.0.1:{self.server.server_port}',
                           'username': 'admin', 'password': 'wrong'})
        result = self.clients.sync(self.identity)
        self.assertFalse(result['ok'])
        self.assertNotIn('wrong', result['error'])
        self.assertFalse(self.clients.all()[0]['enabled'])
        self.clients.remove(self.identity)
        self.assertEqual(self.clients.all(), [])
        self.assertEqual(self.fake['actions'], [])


if __name__ == '__main__':
    unittest.main()
