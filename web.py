"""Small self-hosted HTML UI for the existing DigitalCore upload worker."""
import configparser
import hashlib
import hmac
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import secrets
import threading
import time
from urllib.parse import parse_qs, urlparse
import uuid
import sqlite3
import ssl
from utils.job_history import JobHistory
from utils.qbit_clients import QbitClients
from utils.import_queue import ImportQueue
from utils import tmdb, igdb, tvmaze, image_host
from utils.metadata_settings import ignore_words, clean_words
from utils.public_download import catalogue_page, download as public_download

from utils.web_security import Security
from utils.delivery import remote_root, check_mapping
import uploader

ASSETS = Path(__file__).resolve().parent / 'webui'
OPTIONS = ('IMAGE_UPLOAD', 'SCREENSHOTS', 'MEDIAINFO', 'DUPECHECK')


def config_digest(config):
    return hashlib.sha256(repr({s: dict(config[s]) for s in config.sections()}).encode()).hexdigest()


def content_path(value, config):
    root = Path(config.get('Paths', 'DATADIR')).resolve()
    path = Path(value or root).expanduser()
    path = (path if path.is_absolute() else root / path).resolve(strict=True)
    if not path.is_relative_to(root) or not path.is_dir():
        raise ValueError('Choose a folder inside the configured content folder.')
    return path


def browse(value, config):
    path = content_path(value, config)
    root = Path(config.get('Paths', 'DATADIR')).resolve()
    folders, files = [], []
    for child in sorted(path.iterdir(), key=lambda p: p.name.casefold()):
        if child.name.startswith('.'):
            continue
        try:
            if not child.resolve().is_relative_to(root):
                continue
            if child.is_dir():
                folders.append({'name': child.name, 'path': str(child)})
            elif child.is_file():
                files.append({'name': child.name, 'size': child.stat().st_size})
        except OSError:
            continue
        if len(folders) + len(files) >= 1000:
            break
    return {'path': str(path), 'root': str(root), 'parent': str(path.parent) if path != root else None,
            'folders': folders, 'files': files}


def folder_summary(path):
    count = size = images = videos = archives = 0
    limited = False
    for root, dirs, names in os.walk(path, followlinks=False):
        dirs[:] = [d for d in dirs if not d.startswith('.')]
        for name in names:
            if name.startswith('.'):
                continue
            item = Path(root) / name
            try:
                size += item.stat().st_size
            except OSError:
                continue
            count += 1
            suffix = item.suffix.lower()
            images += suffix in {'.jpg', '.jpeg', '.png', '.webp', '.gif'}
            videos += suffix in {'.mp4', '.mkv', '.avi', '.mov', '.wmv', '.webm', '.m4v'}
            archives += bool(re.search(r'\.(rar|r\d{2,3}|zip|7z)$', name, re.IGNORECASE))
            if count >= 20000:
                limited = True
                break
        if limited:
            break
    return {'files': count, 'bytes': size, 'images': images, 'videos': videos, 'archives': archives, 'limited': limited}



class Workspace:
    def __init__(self, password, history_path=None):
        self.sessions = {}
        self.attempts = {}
        self.drafts = {}
        self.lock = threading.RLock()
        self.active = None
        database_path = Path(history_path or uploader.ROOT / 'data/uploads.db')
        self.history = JobHistory(database_path, legacy_path=database_path.with_name('web-history.json'))
        config = uploader.load_config()
        self.security = Security(self.history, password, os.environ.get('DIGITALCORE_WEB_USERNAME') or config.get('Web', 'USERNAME', fallback='admin'), config.get('Web', 'ALLOWED_IPS', fallback=''))
        self.history.recover_interrupted()
        self.clients = QbitClients(self.history)
        self.imports = ImportQueue(self)

    def enrich(self, jobs):
        clients = self.clients.all()
        for job in jobs:
            matches = self.clients.matches(job.get('torrent_hash'))
            job['clients'] = matches
            fresh = [row for row in matches if not row['stale']]
            if not job.get('torrent_hash'):
                job['seeding_status'] = 'unlinked'
            elif not clients:
                job['seeding_status'] = 'no_clients'
            elif any(row['status'] == 'seeding' for row in fresh):
                job['seeding_status'] = 'seeding'
            elif fresh:
                job['seeding_status'] = fresh[0]['status']
            else:
                job['seeding_status'] = 'not_found' if all(c['online'] for c in clients if c['enabled']) and any(c['enabled'] for c in clients) else 'unknown'
        return jobs

    def state(self):
        config = uploader.load_config()
        errors = uploader.check_config(config)
        try:
            uploader.check_writable(config)
        except (OSError, ValueError) as exc:
            errors.append(str(exc))
        history = self.history.page()
        self.enrich(history['jobs'])
        return {'categories': uploader.categories(), 'settings': {
            'site': config.get('Website', 'SITEURL'),
            'has_api_key': bool(config.get('Website', 'API_KEY').strip()),
            'has_announce': bool(config.get('Torrent', 'ANNOUNCEURL').strip()),
            'has_tmdb_token': bool(config.get('TMDB', 'TOKEN', fallback='').strip()),
            'has_igdb_credentials': bool(config.get('IGDB','CLIENT_ID',fallback='').strip() and config.get('IGDB','CLIENT_SECRET',fallback='').strip()),
            'metadata_ignore_words': ignore_words(config),
            'config_file': str(uploader.config_path()),
            'config_schema': __import__('utils.web_settings',fromlist=['schema']).schema(config),
            'screenshot_engine': config.get('MediaTools','SCREENSHOT_ENGINE',fallback='ffmpeg'),
            'image_host': image_host.provider(config),
            'has_image_host_key': bool(config.get('ImageHost', 'API_KEY', fallback='').strip()),
            'import_remote_root': config.get('Imports', 'REMOTE_ROOT', fallback=''),
            'import_local_root': config.get('Imports', 'LOCAL_ROOT', fallback='').strip() or config.get('Paths', 'DATADIR'),
            'import_client': config.get('Imports', 'CLIENT_ID', fallback=''),
            'content': config.get('Paths', 'DATADIR'), 'watch': config.get('Paths', 'WATCHFOLDER'),
            'paths_locked': os.environ.get('DIGITALCORE_CONTAINER') == '1',
            'options': {key: config.getboolean('Settings', key) for key in OPTIONS}},
            'ready': not errors, 'errors': errors, 'active': self.active,
            'jobs': history['jobs'],
            'history': {k: v for k, v in history.items() if k != 'jobs'}}

    def settings(self, data):
        with self.lock:
            if self.active:
                raise ValueError('Wait for the current upload to finish before changing settings.')
            config = configparser.ConfigParser(interpolation=None)
            config.read(uploader.config_path(), encoding='utf-8')
            config.remove_section('PussyTorrents')
            for section in ('Website', 'Torrent', 'Paths', 'Settings', 'TMDB', 'IGDB', 'ImageHost', 'Imports', 'MediaTools'):
                if not config.has_section(section):
                    config.add_section(section)
            for key, section, option in (('api_key', 'Website', 'API_KEY'), ('announce', 'Torrent', 'ANNOUNCEURL'), ('tmdb_token', 'TMDB', 'TOKEN'), ('igdb_client_id','IGDB','CLIENT_ID'), ('igdb_client_secret','IGDB','CLIENT_SECRET'), ('image_host_key', 'ImageHost', 'API_KEY')):
                value = str(data.get(key, '')).strip()
                if value:
                    if '\n' in value or '\r' in value:
                        raise ValueError('Credentials must be a single line.')
                    if key == 'announce' and urlparse(value).scheme not in ('https', 'http'):
                        raise ValueError('Enter a complete HTTP(S) announce URL.')
                    config.set(section, option, value)
            if 'metadata_ignore_words' in data:
                if not config.has_section('Metadata'):config.add_section('Metadata')
                config.set('Metadata', 'SEARCH_IGNORE_WORDS', '\n'.join(clean_words(data['metadata_ignore_words'])))
            if 'image_host' in data:
                selected = str(data['image_host'])
                if selected not in ('digitalcore', 'imghost', 'custom'):
                    raise ValueError('Choose a supported image host.')
                config.set('ImageHost', 'PROVIDER', selected)
                if selected != 'custom':
                    config.set('ImageHost', 'UPLOADIMGURL', 'https://imghost.dev/api/1/upload' if selected=='imghost' else 'https://img.digitalcore.club/api/upload')
            config.set('Torrent','HASHER','mkbrr')
            if 'screenshot_engine' in data:
                engine=str(data['screenshot_engine'])
                if engine not in ('mtn','ffmpeg'):raise ValueError('Choose MTN or FFmpeg for screenshots.')
                config.set('MediaTools','SCREENSHOT_ENGINE',engine)
            if os.environ.get('DIGITALCORE_CONTAINER') != '1':
                for key, option in (('content', 'DATADIR'), ('watch', 'WATCHFOLDER')):
                    value = str(data.get(key, '')).strip()
                    if value:
                        config.set('Paths', option, value)
            if 'import_client' in data:
                identity = str(data['import_client']).strip()
                if identity and not self.clients._client(identity)['enabled']:
                    raise ValueError('Choose an enabled qBittorrent connection.')
                config.set('Imports', 'CLIENT_ID', identity)
            if 'import_remote_root' in data:
                value = str(data['import_remote_root']).strip()
                config.set('Imports', 'REMOTE_ROOT', remote_root(value) if value else '')
            if 'import_local_root' in data:
                value = str(data['import_local_root']).strip()
                effective = uploader.load_config() if os.environ.get('DIGITALCORE_CONTAINER') == '1' else config
                config.set('Imports', 'LOCAL_ROOT', str(content_path(value, effective)) if value else effective.get('Paths', 'DATADIR'))
            for key, value in data.get('options', {}).items():
                if key in OPTIONS and isinstance(value, bool):
                    config.set('Settings', key, str(value).lower())
            if 'config_fields' in data:
                from utils.web_settings import apply
                apply(config,data['config_fields'])
            if config.getboolean('Settings','SCREENSHOTS',fallback=True):
                engine=config.get('MediaTools','SCREENSHOT_ENGINE',fallback='mtn')
                count=config.getint('MediaTools','SCREENSHOT_SINGLES',fallback=5)
                if count==0 and (engine=='ffmpeg' or not config.getboolean('MediaTools','SCREENSHOT_SHEET',fallback=True)):
                    raise ValueError('Enable a thumbnail sheet or at least one separate screenshot.')
            path = uploader.config_path()
            temp = path.with_suffix('.ini.new')
            with temp.open('w', encoding='utf-8') as output:
                config.write(output)
            if os.name != 'nt':
                temp.chmod(0o600)
            temp.replace(path)
            self.drafts.clear()
        return self.state()

    def review(self, data):
        config = uploader.load_config()
        path = content_path(str(data.get('source', '')), config)
        if path == Path(config.get('Paths', 'DATADIR')).resolve():
            raise ValueError('Choose a release subfolder, not the entire content library.')
        job = uploader.validate_job(path, data.get('category'), str(data.get('description', '')),
                                    data.get('images', []), data.get('metadata', {}), config)
        # Inspect symlink targets too: the web UI cannot upload arbitrary host paths.
        root = Path(config.get('Paths', 'DATADIR')).resolve()
        for directory, dirs, files in os.walk(path, followlinks=False):
            for name in dirs + files:
                child = Path(directory) / name
                if child.is_symlink() and not child.resolve().is_relative_to(root):
                    raise ValueError('The release contains a link outside the content folder.')
        token = secrets.token_urlsafe(24)
        with self.lock:
            self.drafts = {k: v for k, v in self.drafts.items() if v['expires'] > time.time()}
            if len(self.drafts) >= 50:
                self.drafts.pop(next(iter(self.drafts)))
            self.drafts[token] = {'job': job, 'config': config_digest(config), 'expires': time.time() + 1800}
        summary = folder_summary(path)
        errors = uploader.check_config(config)
        if summary['archives'] and not config.getboolean('UnrarUpload', 'ENABLED', fallback=False):
            errors.append('Archive files found. Choose an extracted release folder before uploading.')
        return {'review_id': token, 'job': job, 'summary': summary,
                'category_name': next(c['name'] for c in uploader.categories() if c['id'] == job['category']),
                'ready': not errors, 'errors': errors,
                'watch': config.get('Paths', 'WATCHFOLDER')}

    def upload(self, token, identity=None):
        with self.lock:
            if self.active:
                raise ValueError('An upload is already running. Follow it in Activity.')
            draft = self.drafts.get(token)
            if not draft or draft['expires'] < time.time():
                raise ValueError('Review this release again before uploading.')
            config = uploader.load_config()
            if config_digest(config) != draft['config']:
                raise ValueError('Settings changed. Review the upload again.')
            errors = uploader.check_config(config)
            if folder_summary(draft['job']['source'])['archives'] and not config.getboolean('UnrarUpload', 'ENABLED', fallback=False):
                errors.append('Archive files found. Choose an extracted release folder before uploading.')
            if errors:
                raise ValueError('\n'.join(errors))
            uploader.check_writable(config)
            job = draft['job']
            content_path(job['source'], config)
            identity = identity or uuid.uuid4().hex
            self.history.create(identity, job)
            self.active = identity
            del self.drafts[token]

        def worker():
            secrets_to_hide = [config.get('Website', 'API_KEY'), config.get('Torrent', 'ANNOUNCEURL'), config.get('ImageHost', 'API_KEY', fallback=''), config.get('ImageHost', 'AUTHCODE', fallback=''), config.get('TMDB', 'TOKEN', fallback='')]
            def redact(line):
                line = re.sub(r'\x1b\[[0-9;]*[A-Za-z]', '', str(line))
                for secret in secrets_to_hide:
                    if secret:
                        line = line.replace(secret, '[redacted]')
                return line
            def emit(line):
                self.history.append_log(identity, redact(line))
            def event(fields):
                clean = {key: redact(value) if isinstance(value, str) else value for key, value in fields.items()}
                self.history.update(identity, clean)
            emit.event = event
            code = None
            error = None
            status = 'needs_review'
            try:
                code = uploader.run_job(job, config, emit)
                status = 'completed' if code == 0 else 'duplicate' if code == 10 else 'needs_review'
                if status == 'needs_review':
                    current = self.history.get(identity)
                    error = current['error'] or 'The upload worker failed. Read the log and check DigitalCore before retrying.'
            except Exception as exc:
                error = redact(exc)
                emit(error)
            finally:
                with self.lock:
                    self.history.finish(identity, status, code, error)
                    self.active = None
        try:
            threading.Thread(target=worker, daemon=True).start()
        except RuntimeError as exc:
            self.history.finish(identity, 'needs_review', error=str(exc))
            self.active = None
            raise ValueError('The upload worker could not start.') from exc
        return {'id': identity}


class Handler(BaseHTTPRequestHandler):
    server_version = 'DigitalCore'

    def log_message(self, format, *args):
        # Request bodies and query strings can contain private data; do not log them.
        pass

    def send(self, data, status=200, mime='application/json', cookie=None):
        content = json.dumps(data, ensure_ascii=False).encode() if mime == 'application/json' else data
        self.send_response(status)
        self.send_header('Content-Type', mime + ('; charset=utf-8' if mime.startswith(('text/', 'application/json')) else ''))
        self.send_header('Content-Length', str(len(content)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('X-Frame-Options', 'DENY')
        self.send_header('Referrer-Policy', 'same-origin')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'")
        if cookie:
            self.send_header('Set-Cookie', cookie + ('; Secure' if isinstance(self.connection, ssl.SSLSocket) else ''))
        self.end_headers()
        self.wfile.write(content)

    def session(self):
        try:
            cookie = SimpleCookie(self.headers.get('Cookie', ''))
            token = cookie['dc_uploader_session'].value
            session = self.server.workspace.sessions.get(token)
            return session if session and session['expires'] > time.time() and session.get('ip') == self.client_address[0] else None
        except (KeyError, ValueError):
            return None

    def access_allowed(self):
        if self.server.workspace.security.allowed(self.client_address[0]):
            return True
        self.send({'error': 'Access from this IP address is not allowed.'}, 403)
        return False

    def do_GET(self):
        if not self.access_allowed():
            return
        path = urlparse(self.path).path
        if path == '/vnc.html':
            self.send_response(303)
            self.send_header('Location', '/')
            self.send_header('Content-Length', '0')
            self.end_headers()
            return
        if path == '/health':
            return self.send({'ok': True})
        if path in ('/', '/app.css', '/app.js', '/logo.svg', '/logo-light.svg', '/catalogue.js', '/search-name.js', '/config-settings.js', '/favicon.svg', '/favicon.ico', '/apple-touch-icon.png'):
            name, mime = {'/': ('index.html', 'text/html'), '/app.css': ('app.css', 'text/css'),
                          '/app.js': ('app.js', 'text/javascript'),
                          '/config-settings.js': ('config-settings.js', 'text/javascript'),
                          '/catalogue.js': ('catalogue.js', 'text/javascript'),
                          '/search-name.js': ('search-name.js', 'text/javascript'),
                          '/logo.svg': ('logo.svg', 'image/svg+xml'),
                          '/favicon.svg': ('favicon.svg', 'image/svg+xml'),
                          '/favicon.ico': ('favicon.ico', 'image/x-icon'),
                          '/apple-touch-icon.png': ('apple-touch-icon.png', 'image/png'),
                          '/logo-light.svg': ('logo-light.svg', 'image/svg+xml')}[path]
            return self.send((ASSETS / name).read_bytes(), mime=mime)
        session = self.session()
        if not session:
            return self.send({'error': 'Please sign in.'}, 401)
        try:
            workspace = self.server.workspace
            if path == '/api/security':
                return self.send(dict(workspace.security.state(), current_ip=self.client_address[0]))
            if path == '/api/image-host/public-config':
                from utils.digitalcore_images import ENDPOINT, authentication
                return self.send({'endpoint': ENDPOINT, 'public_key': authentication(uploader.load_config())})
            if path == '/api/imports':
                query = parse_qs(urlparse(self.path).query)
                return self.send(workspace.imports.page(query.get('limit',[30])[0], query.get('offset',[0])[0],
                    query.get('status',[''])[0], query.get('search',[''])[0], query.get('sort',['created'])[0], query.get('direction',['desc'])[0]))
            if path == '/api/state':
                with workspace.lock:
                    state = workspace.state()
                    state['csrf'] = session['csrf']
                return self.send(state)
            if path == '/api/folders':
                query = parse_qs(urlparse(self.path).query)
                return self.send(browse(query.get('path', [''])[0], uploader.load_config()))
            if path == '/api/history':
                query = parse_qs(urlparse(self.path).query)
                result = workspace.history.page(query.get('limit', [30])[0],
                    query.get('offset', [0])[0], query.get('status', [''])[0], query.get('search',[''])[0],
                    query.get('sort',['started'])[0], query.get('direction',['desc'])[0])
                workspace.enrich(result['jobs'])
                return self.send(result)
            if path == '/api/clients':
                return self.send({'clients': workspace.clients.all()})
            if path == '/api/client-torrents':
                query = parse_qs(urlparse(self.path).query)
                return self.send(workspace.clients.torrents(query.get('client', [''])[0], query.get('search', [''])[0],
                    query.get('status', [''])[0], query.get('offset', [0])[0], sort=query.get('sort', ['name'])[0], direction=query.get('direction', [''])[0]))
            if path.startswith('/api/jobs/'):
                with workspace.lock:
                    job = workspace.history.get(path.rsplit('/', 1)[1])
                    if job:
                        workspace.enrich([job])
                    return self.send(job if job else {'error': 'Upload not found.'}, 200 if job else 404)
            return self.send({'error': 'Not found.'}, 404)
        except (OSError, ValueError, configparser.Error, sqlite3.Error) as exc:
            return self.send({'error': str(exc)}, 400)

    def do_POST(self):
        if not self.access_allowed():
            return
        try:
            origin = self.headers.get('Origin')
            expected = {'http://' + self.headers.get('Host', ''), 'https://' + self.headers.get('Host', '')}
            if origin and origin not in expected:
                return self.send({'error': 'Invalid request origin.'}, 403)
            if self.headers.get('Content-Type', '').split(';')[0] != 'application/json':
                return self.send({'error': 'JSON request required.'}, 415)
            size = int(self.headers.get('Content-Length', '0'))
            if not 0 < size <= (12 * 1024 * 1024 if urlparse(self.path).path == '/api/images/upload' else 6 * 1024 * 1024 if urlparse(self.path).path == '/api/imports/add' else 100000):
                return self.send({'error': 'Request is too large or empty.'}, 413)
            data = json.loads(self.rfile.read(size))
            if not isinstance(data, dict):
                raise ValueError('Expected an object.')
            path = urlparse(self.path).path
            workspace = self.server.workspace
            if path == '/api/login':
                with workspace.lock:
                    now = time.time()
                    ip = self.client_address[0]
                    attempts = [v for v in workspace.attempts.get(ip, []) if v > now - 300]
                    workspace.attempts[ip] = attempts
                    if len(attempts) >= 8:
                        return self.send({'error': 'Too many attempts. Try again in five minutes.'}, 429)
                    if not workspace.security.verify(data.get('username', ''), data.get('password', '')):
                        attempts.append(now)
                        return self.send({'error': 'That username or password is not correct.'}, 401)
                    workspace.attempts.pop(ip, None)
                    token = secrets.token_urlsafe(32)
                    workspace.sessions = {k: v for k, v in workspace.sessions.items() if v['expires'] > now}
                    workspace.sessions[token] = {'csrf': secrets.token_urlsafe(24), 'expires': now + 43200, 'ip': ip}
                return self.send({'ok': True}, cookie=f'dc_uploader_session={token}; HttpOnly; SameSite=Strict; Path=/; Max-Age=43200')
            session = self.session()
            if not session:
                return self.send({'error': 'Please sign in.'}, 401)
            if not hmac.compare_digest(self.headers.get('X-CSRF-Token', ''), session['csrf']):
                return self.send({'error': 'Session check failed. Refresh and try again.'}, 403)
            if path == '/api/catalogue/fetch':
                raise ValueError('Use TMDB Movies or TV search for DigitalCore metadata.')
            if path == '/api/images/preview':
                import base64
                raw, _, _ = public_download(str(data.get('url', '')), limit=image_host.MAX_IMAGE)
                _, mime = image_host.identify(raw)
                return self.send({'image': 'data:' + mime + ';base64,' + base64.b64encode(raw).decode('ascii')})
            if path in ('/api/images/upload', '/api/images/rehost'):
                import base64
                if path.endswith('/rehost'):
                    raw, mime, final = public_download(str(data.get('url', '')), limit=image_host.MAX_IMAGE)
                    filename = urlparse(final).path.rsplit('/', 1)[-1] or 'cover'
                else:
                    try:
                        raw = base64.b64decode(str(data.get('image', '')), validate=True)
                    except ValueError:
                        raise ValueError('Choose a valid image file.')
                    filename = str(data.get('filename', 'image'))
                return self.send(image_host.upload(uploader.load_config(), filename, raw))
            if path == '/api/security':
                with workspace.lock:
                    result = workspace.security.update(data, self.client_address[0])
                    workspace.sessions.clear()
                return self.send(dict(result, sign_in_required=True), cookie='dc_uploader_session=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0')
            if path == '/api/imports/add':
                return self.send(workspace.imports.add(data), 202)
            if path == '/api/imports/reconcile':
                return self.send(workspace.imports.reconcile(str(data.get('id', ''))))
            if path == '/api/imports/cancel':
                return self.send(workspace.imports.cancel(str(data.get('id', ''))))
            if path in ('/api/tvmaze/search','/api/tvmaze/detail'):
                if path.endswith('/search'):
                    return self.send({'results':tvmaze.search(str(data.get('query','')))})
                return self.send(tvmaze.detail(str(data.get('id',''))))
            if path in ('/api/igdb/search','/api/igdb/detail'):
                cfg=uploader.load_config()
                client_id=cfg.get('IGDB','CLIENT_ID',fallback='').strip()
                secret=cfg.get('IGDB','CLIENT_SECRET',fallback='').strip()
                if path.endswith('/search'):
                    return self.send({'results':igdb.search(client_id,secret,str(data.get('query','')))})
                return self.send(igdb.detail(client_id,secret,str(data.get('id',''))))
            if path in ('/api/tmdb/search', '/api/tmdb/detail'):
                token = uploader.load_config().get('TMDB', 'TOKEN', fallback='').strip()
                kind = str(data.get('kind', 'movie'))
                if path.endswith('/search'):
                    return self.send({'results': tmdb.search(token, str(data.get('query', '')), kind)})
                return self.send(tmdb.detail(token, str(data.get('id', '')), kind))
            if path == '/api/settings':
                return self.send(workspace.settings(data))
            if path == '/api/delivery/check':
                local = content_path(str(data.get('local_root', '')), uploader.load_config())
                return self.send(check_mapping(workspace.clients, workspace.history, str(data.get('client_id', '')), data.get('remote_root', ''), local))
            if path == '/api/clients/save':
                identity = workspace.clients.save(data)
                result = workspace.clients.sync(identity)
                return self.send({'id': identity, 'test': result, 'clients': workspace.clients.all()})
            if path == '/api/clients/test':
                return self.send(workspace.clients.sync(str(data.get('id', ''))))
            if path == '/api/clients/sync':
                threading.Thread(target=workspace.clients.sync_all, daemon=True).start()
                return self.send({'ok': True})
            if path == '/api/clients/remove':
                workspace.clients.remove(str(data.get('id', '')))
                return self.send({'ok': True})
            if path == '/api/clients/action':
                return self.send(workspace.clients.action(str(data.get('id', '')), str(data.get('hash', '')), str(data.get('action', ''))))
            if path == '/api/review':
                return self.send(workspace.review(data))
            if path == '/api/upload':
                return self.send(workspace.upload(str(data.get('review_id', ''))), 202)
            if path == '/api/logout':
                cookie = SimpleCookie(self.headers.get('Cookie', ''))
                workspace.sessions.pop(cookie['dc_uploader_session'].value, None)
                return self.send({'ok': True}, cookie='dc_uploader_session=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0')
            return self.send({'error': 'Not found.'}, 404)
        except (OSError, ValueError, TypeError, KeyError, configparser.Error, sqlite3.Error) as exc:
            return self.send({'error': str(exc)}, 400)


def create_server(host='127.0.0.1', port=6080, password=None, history_path=None):
    uploader.ensure_config()
    if password is None:
        password = os.environ.get('DIGITALCORE_WEB_PASSWORD') or uploader.load_config().get('Web', 'PASSWORD', fallback='')
    generated_password = not password
    if not password:
        password = secrets.token_urlsafe(16)
    server = ThreadingHTTPServer((host, port), Handler)
    server.daemon_threads = True
    server.workspace = Workspace(password, history_path)
    if generated_password and server.workspace.security.created:
        print(f'Initial workspace login: {server.workspace.security.state()["username"]} / {password}', flush=True)
    return server


def serve(host='127.0.0.1', port=6080, open_browser=False, tls_cert=None, tls_key=None):
    os.chdir(uploader.ROOT)
    server = create_server(host, port)
    tls_cert = tls_cert or os.environ.get('DIGITALCORE_TLS_CERT')
    tls_key = tls_key or os.environ.get('DIGITALCORE_TLS_KEY')
    if bool(tls_cert) != bool(tls_key):
        server.server_close()
        raise ValueError('Provide both a TLS certificate and private key.')
    if tls_cert:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.load_cert_chain(tls_cert, tls_key)
        server.socket = context.wrap_socket(server.socket, server_side=True)
    scheme = 'https' if tls_cert else 'http'
    server.workspace.clients.start()
    server.workspace.imports.start()
    print(f'DigitalCore Uploader: {scheme}://{host}:{server.server_port}', flush=True)
    if open_browser and host in ('127.0.0.1', 'localhost'):
        import webbrowser
        webbrowser.open(f'{scheme}://{host}:{server.server_port}')
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.workspace.clients.stop.set()
        server.workspace.imports.stop.set()
        server.server_close()


if __name__ == '__main__':
    serve(os.environ.get('HOST', '0.0.0.0'), int(os.environ.get('PORT', '6080')))
