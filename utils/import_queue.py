"""Durable torrent import queue. No source trackers are copied to the new torrent."""
import base64
import io
import json
from pathlib import Path, PurePosixPath
import re
import threading
import time
from urllib.parse import parse_qs, urlparse
import uuid
from utils.torrent_metadata import TorrentMetadata as Torrent
import requests
import uploader


def parse_source(data):
    magnet = str(data.get('magnet') or '').strip()
    encoded = data.get('torrent') or ''
    if bool(magnet) == bool(encoded):
        raise ValueError('Choose one .torrent file OR one magnet link.')
    if magnet:
        if len(magnet) > 20000 or urlparse(magnet).scheme != 'magnet':
            raise ValueError('Enter a valid magnet link.')
        query = parse_qs(urlparse(magnet).query)
        hashes = [x[9:] for x in query.get('xt', []) if x.lower().startswith('urn:btih:')]
        if len(hashes) != 1:
            raise ValueError('Use a magnet with a BitTorrent v1 info hash.')
        value = hashes[0]
        if re.fullmatch(r'[A-Z2-7a-z]{32}', value):
            value = base64.b32decode(value.upper()).hex()
        if not re.fullmatch(r'[a-fA-F0-9]{40}', value):
            raise ValueError('This magnet has an invalid info hash.')
        return value.lower(), (query.get('dn') or ['Imported release'])[0][:200], None, magnet
    try:
        raw = base64.b64decode(encoded, validate=True)
        if not 0 < len(raw) <= 4 * 1024 * 1024:
            raise ValueError()
        torrent = Torrent.read_stream(io.BytesIO(raw))
        return torrent.infohash.lower(), str(torrent.name)[:200], raw, None
    except Exception as exc:
        raise ValueError('Choose a valid v1/hybrid .torrent file, up to 4 MB.') from exc


class ImportQueue:
    def __init__(self, workspace):
        self.workspace = workspace
        self.history = workspace.history
        self.lock = threading.RLock()
        self.stop = threading.Event()
        with self.history.connect() as db:
            db.executescript('''CREATE TABLE IF NOT EXISTS torrent_imports (
                id TEXT PRIMARY KEY, client_id TEXT NOT NULL, source_hash TEXT NOT NULL,
                name TEXT NOT NULL, status TEXT NOT NULL, progress REAL NOT NULL DEFAULT 0,
                remote_path TEXT NOT NULL, local_path TEXT NOT NULL, details TEXT NOT NULL,
                created REAL NOT NULL, updated REAL NOT NULL, error TEXT, job_id TEXT);
                CREATE INDEX IF NOT EXISTS idx_import_status ON torrent_imports(status);
            ''')
            if 'seed_status' not in {r[1] for r in db.execute('PRAGMA table_info(torrent_imports)')}:
                db.execute("ALTER TABLE torrent_imports ADD COLUMN seed_status TEXT NOT NULL DEFAULT 'pending'")
            if 'folder_layout' not in {r[1] for r in db.execute('PRAGMA table_info(torrent_imports)')}:
                db.execute("ALTER TABLE torrent_imports ADD COLUMN folder_layout TEXT NOT NULL DEFAULT 'legacy'")
            db.execute("UPDATE torrent_imports SET status='needs_review', error='Interrupted while adding the torrent. Check the client before adding it again.' WHERE status='adding'")
            for row in db.execute("SELECT id FROM torrent_imports WHERE status='publishing'").fetchall():
                job = db.execute('SELECT id FROM upload_jobs WHERE id=?', (row['id'],)).fetchone()
                db.execute('UPDATE torrent_imports SET status=?, job_id=?, error=? WHERE id=?',
                    ('uploaded' if job else 'needs_review', row['id'] if job else None,
                     None if job else 'Interrupted before upload confirmation. Check DigitalCore before retrying.', row['id']))

    def rows(self):
        return self.page(limit=100)['imports']

    def page(self, limit=30, offset=0, status='', search='', sort='created', direction='desc'):
        limit, offset = max(1,min(100,int(limit))), max(0,int(offset))
        groups = {'active':('adding','downloading','waiting','publishing','uploaded','running'),
                  'attention':('needs_review','interrupted'), 'completed':('completed',),
                  'cancelled':('cancelled',), 'duplicate':('duplicate',)}
        if status and status not in groups:
            raise ValueError('Unknown import status filter.')
        effective = "COALESCE(j.status,i.status)"
        joined = ' FROM torrent_imports i LEFT JOIN upload_jobs j ON j.id=i.job_id'
        filters, values = [], []
        if status:
            filters.append(effective+' IN ('+','.join('?' for _ in groups[status])+')'); values.extend(groups[status])
        if search.strip():
            filters.append('(instr(lower(i.name),lower(?))>0 OR instr(lower(i.local_path),lower(?))>0)')
            values.extend([search.strip()[:500]]*2)
        where = ' WHERE '+' AND '.join(filters) if filters else ''
        columns={'created':'i.created','name':'i.name COLLATE NOCASE','status':effective+' COLLATE NOCASE','progress':'i.progress'}
        order=columns.get(sort,'i.created')+(' ASC' if direction=='asc' else ' DESC')
        with self.history.connect() as db:
            rows=[dict(row) for row in db.execute('SELECT i.*,j.status AS upload_status'+joined+where+' ORDER BY '+order+',i.id DESC LIMIT ? OFFSET ?',(*values,limit,offset))]
            total=db.execute('SELECT COUNT(*)'+joined+where,values).fetchone()[0]
            raw=dict(db.execute('SELECT '+effective+',COUNT(*)'+joined+' GROUP BY '+effective))
        counts={key:sum(raw.get(s,0) for s in statuses) for key,statuses in groups.items()}
        for row in rows:
            row.pop('details')
            if row['upload_status'] is None:row.pop('upload_status')
        return {'imports':rows,'total':total,'all_total':sum(raw.values()),'counts':counts,'offset':offset,'limit':limit}

    def update(self, identity, **values):
        with self.history.connect() as db:
            db.execute('UPDATE torrent_imports SET ' + ','.join(key+'=?' for key in values) + ',updated=? WHERE id=?',
                       (*values.values(), time.time(), identity))

    def _original_torrent(self, row, raw=None):
        folder=uploader.ROOT/'data/import-torrents';folder.mkdir(parents=True,exist_ok=True)
        if __import__('os').name!='nt':folder.chmod(0o700)
        target=folder/(row['id']+'.torrent')
        if raw is None:
            raw=target.read_bytes() if target.is_file() else self.workspace.clients.export_torrent(row['client_id'],row['source_hash'])
        if Torrent.read_stream(io.BytesIO(raw)).infohash.lower()!=row['source_hash'].lower():raise ValueError('The original torrent no longer matches this import.')
        if not target.is_file():
            with target.open('xb') as out:out.write(raw)
            if __import__('os').name!='nt':target.chmod(0o600)
        return target

    def add(self, data):
        with self.lock:
            config = uploader.load_config()
            errors = uploader.check_config(config)
            if errors:
                raise ValueError('Finish DigitalCore setup before scheduling automatic uploads: ' + '; '.join(errors))
            uploader.check_writable(config)
            client = self.workspace.clients._client(str(data.get('client_id', '')))
            if not client['enabled']:
                raise ValueError('Enable and test your qBittorrent connection in Clients first.')
            if data.get('category') not in {c['id'] for c in uploader.categories()}:
                raise ValueError('Choose a DigitalCore category.')
            value, name, raw, magnet = parse_source(data)
            remote = str(data.get('remote_root') or '').strip().rstrip('/')
            if not remote.startswith('/') or '..' in PurePosixPath(remote).parts or '\\' in remote or any(c in remote for c in '\r\n\x00'):
                raise ValueError('Enter the absolute download folder as seen by qBittorrent.')
            from web import content_path
            local = content_path(str(data.get('local_root') or ''), config)
            identity = uuid.uuid4().hex
            label = re.sub(r'[^\w. -]', '_', name).strip('. ')[:120] or 'Imported release'
            relative = PurePosixPath(label)
            remote_path = str(PurePosixPath(remote) / relative)
            local_path = str(local.joinpath(*relative.parts))
            details = {key: data.get(key, default) for key, default in
                       [('category', None), ('description', ''), ('images', []), ('metadata', {})]}
            if not isinstance(details['metadata'], dict) or not isinstance(details['images'], list):
                raise ValueError('Invalid release details.')
            if len(str(details['description'])) > 30000 or len(details['images']) > 100:
                raise ValueError('Release details are too large.')
            with self.history.connect() as db:
                if db.execute("SELECT 1 FROM torrent_imports WHERE client_id=? AND source_hash=? AND status!='cancelled'", (client['id'], value)).fetchone():
                    raise ValueError('This torrent is already in the import queue.')
                if Path(local_path).exists() or Path(local_path).is_symlink():
                    raise ValueError(f'The release folder already exists: {local_path}. Nothing was added; choose another folder or use the existing release in New upload.')
                if db.execute("SELECT 1 FROM torrent_imports WHERE (local_path=? OR (client_id=? AND remote_path=?)) AND status!='cancelled'", (local_path,client['id'],remote_path)).fetchone():
                    raise ValueError(f'An import already uses this release folder: {local_path}. Resume the existing import; nothing was added or overwritten.')
                db.execute('INSERT INTO torrent_imports(id,client_id,source_hash,name,status,remote_path,local_path,details,created,updated) VALUES(?,?,?,?,?,?,?,?,?,?)',
                    (identity, client['id'], value, name, 'adding', remote_path, local_path, json.dumps(details), time.time(), time.time()))
                db.execute("UPDATE torrent_imports SET folder_layout='direct' WHERE id=?",(identity,))
            try:
                if raw:self._original_torrent({'id':identity,'client_id':client['id'],'source_hash':value},raw)
                self.workspace.clients.add_torrent(client['id'], value, remote_path, raw, magnet, content_layout='NoSubfolder')
                self.update(identity, status='downloading', error=None)
            except (ValueError, requests.RequestException) as exc:
                self.update(identity, status='needs_review', error='Could not confirm torrent addition. Check the client before adding again.')
                raise ValueError('Could not confirm torrent addition. Check the import queue and your client.') from exc
            return {'id': identity}

    def reconcile(self, identity):
        with self.lock:
            with self.history.connect() as db:
                row = db.execute("SELECT * FROM torrent_imports WHERE id=? AND status='needs_review' AND job_id IS NULL", (identity,)).fetchone()
            if not row or self.history.get(identity):
                raise ValueError('Only an unconfirmed import without an upload attempt can be resumed this way. Check upload history first.')
            torrent, _ = self.workspace.clients.import_status(row['client_id'], row['source_hash'])
            if not torrent:
                raise ValueError('The exact source torrent was not found in its scheduled download folder. Check the client.')
            actual = str(torrent.get('save_path', '')).rstrip('/')
            if actual != row['remote_path']:
                # Cancelling scheduling leaves the download in qBittorrent. Reuse only
                # a known, cancelled import's isolated folder with the same mapping.
                with self.history.connect() as db:
                    previous = db.execute("SELECT * FROM torrent_imports WHERE client_id=? AND source_hash=? AND status='cancelled' AND job_id IS NULL AND remote_path=? ORDER BY created DESC LIMIT 1",
                                          (row['client_id'], row['source_hash'], actual)).fetchone()
                if not previous or self.history.get(previous['id']):
                    raise ValueError('This torrent already exists in another client folder. Its files were left unchanged. Use its existing release folder for a manual upload.')
                levels = {'direct': 0, 'flat': 1, 'legacy': 2}[row['folder_layout']]
                remote_root = PurePosixPath(row['remote_path']).parents[levels]
                local_root = Path(row['local_path']).parents[levels]
                try:relative=PurePosixPath(actual).relative_to(remote_root)
                except ValueError:raise ValueError('The existing torrent uses a different download root.')
                expected_parts={'direct': 1, 'flat': 2, 'legacy': 3}[previous['folder_layout']]
                if (len(relative.parts)!=expected_parts or (previous['folder_layout']!='direct' and relative.parts[0]!='DigitalCore-imports') or '..' in relative.parts or
                        (previous['folder_layout']=='legacy' and relative.parts[1]!=previous['id']) or
                        local_root.joinpath(*relative.parts).resolve() != Path(previous['local_path']).resolve()):
                    raise ValueError('The existing torrent uses a different folder mapping. Check the client and download folders.')
                self.update(identity, remote_path=actual, local_path=previous['local_path'], folder_layout=previous['folder_layout'])
                local_path = previous['local_path']
            else:
                local_path = row['local_path']
            # A user may have published the downloaded folder manually while this
            # import needed attention. Link that successful job; never publish twice.
            with self.history.connect() as db:
                completed = db.execute("SELECT id,source FROM upload_jobs WHERE status='completed' ORDER BY started DESC").fetchall()
            root = Path(local_path).resolve()
            existing = next((job for job in completed if Path(job['source']).resolve().is_relative_to(root)), None)
            if existing:
                self.update(identity, status='uploaded', progress=1, job_id=existing['id'], error=None)
                return {'ok': True, 'existing_upload': True}
            self.update(identity, status='downloading', progress=float(torrent.get('progress', 0)), error=None)
        return {'ok': True}

    def cancel(self, identity):
        with self.lock, self.history.connect() as db:
            changed = db.execute("UPDATE torrent_imports SET status='cancelled', updated=? WHERE id=? AND status IN ('downloading','waiting','needs_review')", (time.time(), identity)).rowcount
            if not changed:
                raise ValueError('This import cannot be cancelled now. Check its upload history.')
        return {'ok': True}

    def tick(self):
        with self.lock:
            self.seed_completed()
            with self.history.connect() as db:
                rows = [dict(r) for r in db.execute("SELECT * FROM torrent_imports WHERE status IN ('downloading','waiting') ORDER BY created")]
            for row in rows:
                try:
                    torrent, files = self.workspace.clients.import_status(row['client_id'], row['source_hash'])
                    if not torrent:
                        self.update(row['id'], error='Torrent not visible on the client yet. Check that it was added.')
                        continue
                    progress = float(torrent.get('progress', 0))
                    self.update(row['id'], progress=progress)
                    # Selected-file progress can be 100% with skipped files: require every file.
                    complete = progress >= 1 and files and all(float(f.get('progress', 0)) >= 1 for f in files)
                    if not complete or torrent.get('state', '').startswith(('checking', 'meta')) or torrent.get('state') in ('error','missingFiles'):
                        message = 'Client reports missing files or an error.' if torrent.get('state') in ('error','missingFiles') else 'Enable all torrent files: some files are skipped or incomplete.' if progress >= 1 and files and not complete else None
                        self.update(row['id'], status='downloading', error=message)
                        continue
                    if str(torrent.get('save_path', '')).rstrip('/') != row['remote_path']:
                        raise ValueError('Client save path changed. Restore the scheduled import path before continuing.')
                    from web import content_path
                    config = uploader.load_config()
                    path = content_path(row['local_path'], config)
                    # Verify the mapping against qBittorrent file names and sizes, not merely folder existence.
                    for item in files:
                        rel = PurePosixPath(str(item['name']))
                        if rel.is_absolute() or '..' in rel.parts or '\\' in str(rel):
                            raise ValueError('Client returned an unsafe file path.')
                        file = path.joinpath(*rel.parts).resolve(strict=True)
                        if not file.is_relative_to(path) or not file.is_file() or file.stat().st_size != int(item['size']):
                            raise ValueError('Download mapping does not match the completed client files.')
                    # Exclude torrent-client sidecar files and preserve the real release folder name.
                    children = [p for p in path.iterdir() if not p.name.startswith('.')]
                    source = children[0] if row['folder_layout']=='legacy' and len(children) == 1 and children[0].is_dir() else path
                    with self.workspace.lock:
                        if self.workspace.active:
                            self.update(row['id'], status='waiting', error='Download complete. Waiting for the current upload.')
                            continue
                        details = json.loads(row['details'])
                        original=self._original_torrent(row)
                        review = self.workspace.review(dict(details, source=str(source)))
                        self.workspace.drafts[review['review_id']]['job']['source_torrent']=str(original)
                        if not review['ready']:
                            raise ValueError('; '.join(review['errors']))
                        self.update(row['id'], status='publishing', error=None)
                        try:
                            result = self.workspace.upload(review['review_id'], identity=row['id'])
                        except Exception:
                            self.update(row['id'], status='needs_review', error='Upload could not be confirmed. Check DigitalCore and history before retrying.')
                            continue
                        self.update(row['id'], status='uploaded', job_id=result['id'], error=None)
                except (ValueError, OSError, KeyError, TypeError, requests.RequestException) as exc:
                    self.update(row['id'], status='waiting', error=str(exc) if isinstance(exc,ValueError) and not isinstance(exc,requests.RequestException) else 'Waiting for complete files, a working client and a correct folder mapping. Check setup and client status.')

    def seed_completed(self):
        with self.history.connect() as db:
            rows = [dict(r) for r in db.execute("SELECT * FROM torrent_imports WHERE status='uploaded' AND seed_status='pending'")]
        for row in rows:
            job = self.history.get(row['job_id'])
            if not job or job['status'] != 'completed' or not job.get('torrent_hash'):
                continue
            try:
                current, _ = self.workspace.clients.import_status(row['client_id'], job['torrent_hash'])
                if not current:
                    path = Path(job['watch_torrent'])
                    watch = Path(uploader.load_config().get('Paths', 'WATCHFOLDER')).resolve()
                    if not path.resolve().is_relative_to(watch):
                        raise ValueError('Confirmed torrent is outside the watch folder.')
                    raw = path.read_bytes()
                    torrent = Torrent.read_stream(io.BytesIO(raw))
                    if torrent.infohash.lower() != job['torrent_hash']:
                        raise ValueError('Confirmed torrent hash changed.')
                    relative = Path(job['source']).relative_to(Path(row['local_path']))
                    remote_source = PurePosixPath(row['remote_path']).joinpath(*relative.parts)
                    # New torrents contain the release root, so seed from its parent.
                    self.workspace.clients.add_torrent(row['client_id'], job['torrent_hash'], str(remote_source.parent), raw=raw)
                self.update(row['id'], seed_status='added', error=None)
                self.workspace.clients.sync(row['client_id'])
            except (OSError, ValueError, KeyError, requests.RequestException):
                self.update(row['id'], error='Upload completed. Waiting to add the confirmed DigitalCore torrent for seeding; check client connection and watch folder.')

    def start(self):
        def loop():
            while not self.stop.is_set():
                try:
                    self.tick()
                except Exception:
                    pass  # Never abandon the queue because a client temporarily failed.
                self.stop.wait(30)
        threading.Thread(target=loop, daemon=True).start()
