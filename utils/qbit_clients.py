"""Multiple qBittorrent Web API connections and exact-hash seeding status."""
from concurrent.futures import ThreadPoolExecutor
import re
import threading
import time
from urllib.parse import urlparse
import uuid
import requests


def added_confirmed(response, hash_value):
    if response.status_code == 204:
        return True
    if response.status_code == 200 and response.content.strip() == b'Ok.':
        return True
    if response.status_code in (200, 202):
        try:
            result = response.json()
            return isinstance(result, dict) and (hash_value in result.get('added_torrent_ids', []) or int(result.get('pending_count', 0)) > 0)
        except (ValueError, TypeError):
            pass
    return False


def state_group(state):
    if state in ('uploading', 'stalledUP', 'forcedUP'):
        return 'seeding'
    if state.startswith(('paused', 'stopped')):
        return 'paused'
    if state.startswith('checking'):
        return 'checking'
    if state in ('error', 'missingFiles'):
        return 'error'
    if state.startswith('queued'):
        return 'queued'
    if state in ('downloading', 'forcedDL', 'stalledDL', 'metaDL', 'forcedMetaDL', 'allocating'):
        return 'downloading'
    return 'unknown'


class QbitClients:
    def __init__(self, history):
        self.history = history
        self.sync_lock = threading.Lock()
        self.stop = threading.Event()
        with history.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS qbit_clients (
                    id TEXT PRIMARY KEY, name TEXT NOT NULL, url TEXT NOT NULL,
                    username TEXT NOT NULL DEFAULT '', password TEXT NOT NULL DEFAULT '',
                    verify_ssl INTEGER NOT NULL DEFAULT 1, enabled INTEGER NOT NULL DEFAULT 1,
                    last_sync REAL, error TEXT, version TEXT
                );
                CREATE TABLE IF NOT EXISTS qbit_torrents (
                    client_id TEXT NOT NULL REFERENCES qbit_clients(id) ON DELETE CASCADE,
                    hash TEXT NOT NULL, name TEXT NOT NULL, state TEXT NOT NULL, status TEXT NOT NULL,
                    progress REAL NOT NULL, ratio REAL NOT NULL, uploaded INTEGER NOT NULL,
                    upspeed INTEGER NOT NULL, dlspeed INTEGER NOT NULL, size INTEGER NOT NULL,
                    seeding_time INTEGER NOT NULL, save_path TEXT NOT NULL, category TEXT NOT NULL,
                    added_on INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY(client_id, hash)
                );
                CREATE INDEX IF NOT EXISTS idx_qbit_torrent_hash ON qbit_torrents(hash);
            ''')
            columns = {row[1] for row in db.execute('PRAGMA table_info(qbit_torrents)')}
            if 'added_on' not in columns:
                db.execute('ALTER TABLE qbit_torrents ADD COLUMN added_on INTEGER NOT NULL DEFAULT 0')

    def all(self, private=False):
        with self.history.connect() as db:
            rows = db.execute('SELECT * FROM qbit_clients ORDER BY name, id').fetchall()
            result = []
            for row in rows:
                client = dict(row)
                if not private:
                    client['has_password'] = bool(client.pop('password'))
                counts = dict(db.execute('SELECT status, COUNT(*) FROM qbit_torrents WHERE client_id=? GROUP BY status', (row['id'],)))
                client['counts'] = counts
                client['torrent_count'] = sum(counts.values())
                client['online'] = bool(row['enabled'] and not row['error'] and row['last_sync'] and time.time() - row['last_sync'] < 90)
                result.append(client)
        return result

    def save(self, data):
        identity = str(data.get('id') or uuid.uuid4().hex)
        name = str(data.get('name', '')).strip()[:100]
        url = str(data.get('url', '')).strip().rstrip('/')
        parsed = urlparse(url)
        if not name or parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError('Enter a client name and a valid WebUI URL without embedded credentials.')
        with self.history.connect() as db:
            old = db.execute('SELECT * FROM qbit_clients WHERE id=?', (identity,)).fetchone()
            if old and old['url'] != url and old['password'] and not data.get('password'):
                raise ValueError('Re-enter the password when changing a client URL.')
            password = str(data.get('password') or (old['password'] if old else ''))
            username = str(data.get('username', '')).strip()
            db.execute('''INSERT INTO qbit_clients(id,name,url,username,password,verify_ssl,enabled)
                VALUES (?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name,url=excluded.url,
                username=excluded.username,password=excluded.password,verify_ssl=excluded.verify_ssl,
                enabled=excluded.enabled,error=NULL,last_sync=NULL''',
                (identity, name, url, username, password, int(bool(data.get('verify_ssl', True))), int(bool(data.get('enabled', True)))))
        return identity

    def remove(self, identity):
        with self.history.connect() as db:
            db.execute('DELETE FROM qbit_clients WHERE id=?', (identity,))

    def _client(self, identity):
        with self.history.connect() as db:
            row = db.execute('SELECT * FROM qbit_clients WHERE id=?', (identity,)).fetchone()
        if row is None:
            raise ValueError('Client not found.')
        return dict(row)

    def _session(self, client):
        session = requests.Session()
        session.trust_env = False
        session.verify = bool(client['verify_ssl'])
        parsed = urlparse(client['url'])
        session.headers.update({'Referer': client['url'] + '/', 'Origin': parsed.scheme + '://' + parsed.netloc})
        try:
            if client['username'] or client['password']:
                response = session.post(client['url'] + '/api/v2/auth/login',
                    data={'username': client['username'], 'password': client['password']}, timeout=8, allow_redirects=False)
                if response.status_code not in (200, 204) or (response.status_code == 200 and response.content.strip() != b'Ok.'):
                    raise ValueError('qBittorrent login failed. Check the WebUI username and password.')
            response = session.get(client['url'] + '/api/v2/app/version', timeout=8, allow_redirects=False)
            version = response.content.decode('utf-8', errors='replace').strip()
            if response.status_code != 200 or not re.match(r'^v?\d+\.\d+', version):
                raise ValueError('Could not access qBittorrent. Check the WebUI URL and credentials.')
            return session, version
        except Exception:
            session.close()
            raise

    def sync(self, identity):
        client = self._client(identity)
        if not client['enabled']:
            return {'ok': False, 'error': 'This connection is disabled.'}
        try:
            session, version = self._session(client)
            with session:
                response = session.get(client['url'] + '/api/v2/torrents/info', timeout=15, allow_redirects=False)
                response.raise_for_status()
                torrents = response.json()
                if not isinstance(torrents, list):
                    raise ValueError('The qBittorrent torrent list was not valid.')
            rows = []
            for torrent in torrents:
                value = str(torrent.get('hash', '')).lower()
                if not re.fullmatch(r'[a-f0-9]{40}|[a-f0-9]{64}', value):
                    continue
                state = str(torrent.get('state', 'unknown'))
                rows.append((identity, value, str(torrent.get('name', '')), state, state_group(state),
                    float(torrent.get('progress', 0)), float(torrent.get('ratio', 0)), int(torrent.get('uploaded', 0)),
                    int(torrent.get('upspeed', 0)), int(torrent.get('dlspeed', 0)), int(torrent.get('size', 0)),
                    int(torrent.get('seeding_time', 0)), str(torrent.get('save_path', '')), str(torrent.get('category', '')),
                    max(0, int(torrent.get('added_on', 0) or 0))))
            with self.history.connect() as db:
                current = db.execute('SELECT url,username,password,enabled FROM qbit_clients WHERE id=?', (identity,)).fetchone()
                if not current or not current['enabled'] or any(current[key] != client[key] for key in ('url', 'username', 'password')):
                    return {'ok': False, 'error': 'The connection changed during refresh. Refresh again.'}
                db.execute('DELETE FROM qbit_torrents WHERE client_id=?', (identity,))
                db.executemany('INSERT INTO qbit_torrents (client_id,hash,name,state,status,progress,ratio,uploaded,upspeed,dlspeed,size,seeding_time,save_path,category,added_on) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)', rows)
                db.execute('UPDATE qbit_clients SET last_sync=?, error=NULL, version=? WHERE id=?', (time.time(), version, identity))
            return {'ok': True, 'version': version, 'torrents': len(rows)}
        except (requests.RequestException, ValueError, TypeError, KeyError) as exc:
            # Network exceptions can contain URL credentials; store only a generic diagnostic.
            error = str(exc) if isinstance(exc, ValueError) and not isinstance(exc, requests.RequestException) else 'Connection failed. Check URL, network access and HTTPS certificate.'
            with self.history.connect() as db:
                db.execute('UPDATE qbit_clients SET error=? WHERE id=?', (error, identity))
                if error.startswith('qBittorrent login failed'):
                    db.execute('UPDATE qbit_clients SET enabled=0 WHERE id=?', (identity,))
            return {'ok': False, 'error': error}

    def sync_all(self):
        if not self.sync_lock.acquire(blocking=False):
            return
        try:
            clients = [c for c in self.all() if c['enabled']]
            def refresh(client):
                try:
                    return self.sync(client['id'])
                except ValueError:
                    return None
            with ThreadPoolExecutor(max_workers=4) as pool:
                list(pool.map(refresh, clients))
        finally:
            self.sync_lock.release()

    def start(self):
        def loop():
            while not self.stop.is_set():
                self.sync_all()
                self.stop.wait(30)
        threading.Thread(target=loop, daemon=True).start()

    def torrents(self, client_id='', query='', status='', offset=0, limit=50, sort='name', direction=''):
        offset, limit = max(0, int(offset)), max(1, min(100, int(limit)))
        columns = {'name': 't.name COLLATE NOCASE', 'client': 'c.name COLLATE NOCASE', 'status': 't.status COLLATE NOCASE',
                   'size': 't.size', 'progress': 't.progress', 'upspeed': 't.upspeed', 'dlspeed': 't.dlspeed', 'ratio': 't.ratio', 'added_on': 't.added_on'}
        sort = sort if sort in columns else 'name'
        direction = direction if direction in ('asc', 'desc') else ('asc' if sort in ('name', 'client', 'status') else 'desc')
        order = columns[sort] + (' ASC' if direction == 'asc' else ' DESC')
        filters, values = [], []
        if client_id:
            filters.append('t.client_id=?'); values.append(client_id)
        if query:
            filters.append('t.name LIKE ?'); values.append('%' + query + '%')
        if status:
            filters.append('t.status=?'); values.append(status)
        where = ' WHERE ' + ' AND '.join(filters) if filters else ''
        with self.history.connect() as db:
            count = db.execute('SELECT COUNT(*) FROM qbit_torrents t' + where, values).fetchone()[0]
            rows = db.execute('''SELECT t.*, c.name AS client_name, c.last_sync, c.error AS client_error,
                c.enabled AS client_enabled FROM qbit_torrents t JOIN qbit_clients c ON t.client_id=c.id''' + where +
                ' ORDER BY ' + order + ', t.name COLLATE NOCASE, t.client_id, t.hash LIMIT ? OFFSET ?', (*values, limit, offset)).fetchall()
        torrents = [dict(row) for row in rows]
        for torrent in torrents:
            torrent['stale'] = bool(not torrent['client_enabled'] or torrent['client_error'] or not torrent['last_sync'] or time.time() - torrent['last_sync'] > 90)
        return {'torrents': torrents, 'total': count}

    def matches(self, hash_value):
        if not hash_value:
            return []
        with self.history.connect() as db:
            rows = db.execute('''SELECT t.*, c.name AS client_name, c.last_sync, c.error AS client_error,
                c.enabled AS client_enabled FROM qbit_torrents t JOIN qbit_clients c ON t.client_id=c.id WHERE t.hash=?''', (hash_value.lower(),)).fetchall()
        result = [dict(row) for row in rows]
        for row in result:
            row['stale'] = bool(not row['client_enabled'] or row['client_error'] or not row['last_sync'] or time.time() - row['last_sync'] > 90)
        return result

    def action(self, identity, hash_value, action):
        if action not in ('pause', 'resume') or not re.fullmatch(r'[a-fA-F0-9]{40}|[a-fA-F0-9]{64}', hash_value):
            raise ValueError('Choose one valid torrent and a pause/resume action.')
        client = self._client(identity)
        if not client['enabled']:
            raise ValueError('This client is disabled.')
        with self.history.connect() as db:
            if not db.execute('SELECT 1 FROM qbit_torrents WHERE client_id=? AND hash=?', (identity, hash_value.lower())).fetchone():
                raise ValueError('Torrent not found on this client. Refresh the client first.')
        try:
            session, version = self._session(client)
            major = int(version.lstrip('v').split('.')[0])
            method = ('stop' if action == 'pause' else 'start') if major >= 5 else action
            with session:
                response = session.post(client['url'] + '/api/v2/torrents/' + method, data={'hashes': hash_value.lower()}, timeout=10, allow_redirects=False)
                response.raise_for_status()
        except requests.RequestException as exc:
            raise ValueError('The client action failed. Refresh the client to check its current state.') from exc
        return self.sync(identity)

    def export_torrent(self, identity, hash_value):
        if not re.fullmatch(r'[a-fA-F0-9]{40}',hash_value):raise ValueError('A v1 source torrent hash is required.')
        client=self._client(identity)
        session,_=self._session(client)
        with session:
            response=session.get(client['url']+'/api/v2/torrents/export',params={'hash':hash_value},timeout=20,allow_redirects=False,stream=True)
            if response.status_code!=200:raise ValueError('Cannot export the source torrent from qBittorrent. No new hashing was started.')
            raw=bytearray()
            for chunk in response.iter_content(65536):
                raw.extend(chunk)
                if len(raw)>64*1024*1024:raise ValueError('Source torrent metadata is too large.')
        from utils.torrent_metadata import TorrentMetadata
        if TorrentMetadata(bytes(raw)).infohash.lower()!=hash_value.lower():raise ValueError('Exported source torrent hash does not match the import.')
        return bytes(raw)

    def add_torrent(self, identity, hash_value, save_path, raw=None, magnet=None, content_layout='Original'):
        if content_layout not in ('Original', 'NoSubfolder'):
            raise ValueError('Unsupported torrent folder layout.')
        client = self._client(identity)
        if not client['enabled']:
            raise ValueError('This client is disabled.')
        try:
            session, _ = self._session(client)
            with session:
                existing = session.get(client['url'] + '/api/v2/torrents/info', params={'hashes': hash_value}, timeout=15, allow_redirects=False)
                existing.raise_for_status()
                if existing.json():
                    raise ValueError('This torrent is already on the client. Use its existing folder in New upload.')
                data = {'savepath': save_path, 'autoTMM': 'false', 'contentLayout': content_layout, 'paused': 'false', 'stopped': 'false'}
                if magnet:
                    data['urls'] = magnet
                response = session.post(client['url'] + '/api/v2/torrents/add', data=data,
                    files={'torrents': ('import.torrent', raw, 'application/x-bittorrent')} if raw else None,
                    timeout=30, allow_redirects=False)
                if not added_confirmed(response, hash_value):
                    raise ValueError('qBittorrent did not confirm torrent addition.')
        except requests.RequestException as exc:
            raise ValueError('Could not confirm torrent addition. Check the client.') from exc

    def import_status(self, identity, hash_value):
        client = self._client(identity)
        if not client['enabled']:
            raise ValueError('This client is disabled.')
        try:
            session, _ = self._session(client)
            with session:
                response = session.get(client['url'] + '/api/v2/torrents/info', params={'hashes': hash_value}, timeout=15, allow_redirects=False)
                response.raise_for_status()
                rows = response.json()
                row = next((t for t in rows if str(t.get('hash', '')).lower() == hash_value), None)
                if not row or float(row.get('progress', 0)) < 1:
                    return row, []
                response = session.get(client['url'] + '/api/v2/torrents/files', params={'hash': hash_value}, timeout=15, allow_redirects=False)
                response.raise_for_status()
                files = response.json()
                if not isinstance(files, list):
                    raise ValueError('Client returned an invalid file list.')
                return row, files
        except requests.RequestException as exc:
            raise ValueError('Could not read import progress from the client.') from exc
