"""DigitalCore HTML/CLI entry point. Importing this module never uploads."""
import argparse
from contextlib import contextmanager, closing
import configparser
import importlib.util
import json
import os
import re
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from urllib.parse import urlparse
from utils.job_events import PREFIX as EVENT_PREFIX

ROOT = Path(__file__).resolve().parent
if os.name == 'nt' and (ROOT / 'tools/mediainfo/MediaInfo.exe').is_file():
    os.environ['PATH'] = str(ROOT / 'tools/mediainfo') + os.pathsep + os.environ.get('PATH', '')
META_FIELDS = ('studio', 'performers', 'content_tags', 'release_year', 'runtime_minutes', 'language', 'imdbId', 'gameUrl')


def load_config():
    from utils.config_loader import ConfigLoader
    ConfigLoader._cache.clear()
    return ConfigLoader().get_config()


def categories():
    return json.loads((ROOT / 'files/digitalcore-categories.json').read_text(encoding='utf-8'))


def config_path():
    return Path(os.environ.get('DIGITALCORE_CONFIG', str(ROOT / 'config.ini'))).resolve()


def ensure_config():
    path = config_path()
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('x', encoding='utf-8') as output:
            output.write((ROOT / 'config.example.ini').read_text(encoding='utf-8'))
        if os.name != 'nt':
            path.chmod(0o600)
    return path


def check_config(config, tools=True):
    errors = []
    key = config.get('Website', 'API_KEY', fallback='').strip()
    if not key or key.lower().startswith(('your_', 'replace')):
        errors.append('Add your DigitalCore API_KEY to config.ini [Website].')
    for section, field in (('Website', 'SITEURL'), ('Torrent', 'ANNOUNCEURL')):
        value = config.get(section, field, fallback='').strip()
        parsed = urlparse(value)
        if parsed.scheme not in ('http', 'https') or not parsed.hostname:
            errors.append(f'Enter a valid {field} in config.ini [{section}].')
    if __import__('utils.image_host',fromlist=['provider']).provider(config) == 'imghost' and (config.getboolean('Settings', 'IMAGE_UPLOAD') or config.getboolean('Settings', 'SCREENSHOTS')) and not config.get('ImageHost', 'API_KEY', fallback='').strip():
        errors.append('Add your imghost.dev API key in Settings or configure a custom image host.')
    source = Path(config.get('Paths', 'DATADIR'))
    if not source.is_dir():
        errors.append(f'Content folder does not exist: {source}')
    hasher=config.get('Torrent','HASHER').strip().lower()
    if hasher != 'mkbrr':
        errors.append('Set Torrent.HASHER = mkbrr.')
    engine=config.get('MediaTools','SCREENSHOT_ENGINE',fallback='ffmpeg').strip().lower()
    if engine not in ('ffmpeg','mtn'):errors.append('Choose ffmpeg or mtn as MediaTools.SCREENSHOT_ENGINE.')
    if config.getboolean('UnrarUpload', 'ENABLED', fallback=False) and os.name == 'nt':
        errors.append('RAR mounting requires Linux. Extract archives before uploading on Windows.')
    if tools:
        from utils.runtime_tools import find_tool
        if config.getboolean('UnrarUpload','ENABLED',fallback=False) and sys.platform=='linux':
            archive_tool=config.get('UnrarUpload','RAR2FS_BIN',fallback='rar2fs').strip()
            local_archive_tool=ROOT/'tools/archive/bin/rar2fs'
            if not (shutil.which(archive_tool) or archive_tool=='rar2fs' and local_archive_tool.is_file()):
                errors.append('rar2fs is missing. Use install.sh --rar2fs or the complete Docker image.')
            if not Path('/dev/fuse').exists():
                errors.append('UNRAR mounts need /dev/fuse. In Docker use setup.sh --unrar; native seedboxes need provider FUSE support.')
        if hasher=='mkbrr' and not find_tool('mkbrr',config):errors.append('mkbrr is selected but missing. Configure Torrent.MKBRR_BIN or reinstall the native tool.')
        if config.getboolean('Settings','SCREENSHOTS') and engine=='mtn' and not find_tool('mtn',config):errors.append('MTN is selected but missing for this host. Configure MediaTools.MTNBIN or use ffmpeg.')
        needed = []
        if config.getboolean('Settings', 'SCREENSHOTS'):
            needed += ['ffmpeg', 'ffprobe']
        if config.getboolean('Settings', 'MEDIAINFO'):
            needed += ['mediainfo']
        for name in needed:
            if not shutil.which(name):
                errors.append(f'{name} is missing. Run the installer or add it to PATH.')
        if importlib.util.find_spec('requests') is None:
            errors.append('Missing Python dependency: requests. Run the installer.')
    for field in ('TEMPLATE_PATH', 'FILTERS'):
        if not Path(config.get('Paths', field)).is_file():
            errors.append(f'{field} does not point to a file.')
    return errors


def check_writable(config):
    """Actually create/write/read/remove a file before making network requests."""
    for path in (Path(config.get('Paths', 'WATCHFOLDER')), Path(config.get('Paths', 'TMP_DIR')),
                 Path(config.get('Paths', 'UPLOADLOG')).parent, ROOT / 'data'):
        path.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=path, prefix='.write-check-', delete=False) as f:
            target = Path(f.name)
            f.write(b'DigitalCore')
        try:
            if target.read_bytes() != b'DigitalCore':
                raise ValueError(f'Write verification failed: {path}')
        finally:
            target.unlink(missing_ok=True)


def validate_job(source, cat, description='', images=(), metadata=None, config=None):
    config = config or load_config()
    path = Path(source).expanduser()
    if not path.is_absolute():
        path = Path(config.get('Paths', 'DATADIR')) / path
    path = path.resolve(strict=True)
    if not path.is_dir():
        raise ValueError('Choose a release folder containing your media, images and optional NFO.')
    if not any(p.is_file() for p in path.rglob('*')):
        raise ValueError('The selected release folder is empty.')
    if cat not in {row['id'] for row in categories()}:
        raise ValueError('Choose a DigitalCore category.')
    urls = [url.strip() for url in images if url.strip()]
    for url in urls:
        parsed = urlparse(url)
        if parsed.scheme not in ('http', 'https') or not parsed.hostname or any(c in url for c in '[]\r\n'):
            raise ValueError('Image URLs must be one complete HTTP(S) URL per line.')
    values = {key: str(value).strip() for key, value in (metadata or {}).items()
              if key in META_FIELDS and str(value).strip()}
    if values.get('imdbId') and not re.fullmatch(r'tt[0-9]{7,10}', values['imdbId']):
        raise ValueError('IMDb ID must look like tt1234567.')
    if values.get('gameUrl'):
        parsed_game = urlparse(values['gameUrl'])
        if parsed_game.scheme != 'https' or parsed_game.username or parsed_game.password or parsed_game.hostname not in ('store.steampowered.com', 'www.gog.com', 'gog.com', 'www.igdb.com', 'igdb.com'):
            raise ValueError('Use an HTTPS Steam, GOG or IGDB game URL.')
    if values.get('release_year') and (not values['release_year'].isdigit() or
                                      not 1900 <= int(values['release_year']) <= 2100):
        raise ValueError('Release year must be between 1900 and 2100.')
    if values.get('runtime_minutes') and (not values['runtime_minutes'].isdigit() or not 1 <= int(values['runtime_minutes']) <= 2000):
        raise ValueError('Runtime must be between 1 and 2000 minutes.')
    return {'source': str(path), 'category': cat, 'description': description.strip(),
            'images': urls, 'metadata': values}


def review_text(job, config):
    category = next(row['name'] for row in categories() if row['id'] == job['category'])
    lines = [f"Folder: {job['source']}", f'Category: {category}',
             f"Torrent watch folder: {config.get('Paths', 'WATCHFOLDER')}",
             'Images: ' + (f"{len(job['images'])} supplied URL(s)" if job['images'] else 'from release folder'),
             'Screenshots: ' + ('enabled' if config.getboolean('Settings', 'SCREENSHOTS') else 'disabled')]
    lines += [f'{k.replace("_", " ").title()}: {v}' for k, v in job['metadata'].items()]
    lines += ['', 'Description:', job['description'] or '(Use the NFO in the folder, if present.)']
    return '\n'.join(lines)


def backend_command(job, job_path):
    return [sys.executable, '-u', str(ROOT / 'backend.py'), job['source'],
            '--cat', str(job['category']), '--job', str(job_path)]


@contextmanager
def upload_lock():
    """OS lock releases on process exit, including an interrupted upload."""
    lock_path = ROOT / 'data/upload.lock'
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open('a+b') as handle:
        if os.fstat(handle.fileno()).st_size == 0:
            handle.write(b'0')
            handle.flush()
        handle.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise ValueError('Another upload is running. Wait for it to finish.') from exc
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == 'nt':
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def run_job(job, config, emit=print):
    with upload_lock():
        return _run_job(job, config, emit)


def _run_job(job, config, emit):
    check_writable(config)
    from utils.database_utils import initialize_upload_database
    initialize_upload_database()
    temp_dir = Path(config.get('Paths', 'TMP_DIR'))
    with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', suffix='.json',
                                     dir=temp_dir, delete=False) as f:
        json.dump(job, f)
        job_path = Path(f.name)
    try:
        env = dict(os.environ, DIGITALCORE_CONFIG=str(config_path()),
                   PYTHONIOENCODING='utf-8')
        with subprocess.Popen(backend_command(job, job_path), cwd=ROOT, env=env,
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              encoding='utf-8', errors='replace', bufsize=1) as proc:
            for line in proc.stdout:
                if line.startswith(EVENT_PREFIX):
                    event = json.loads(line[len(EVENT_PREFIX):])
                    handler = getattr(emit, 'event', None)
                    if handler:
                        handler(event)
                else:
                    emit(line.rstrip())
            return proc.wait()
    finally:
        job_path.unlink(missing_ok=True)


def fingerprint(path):
    """Include file identity/size/mtime so replacing a file resets the settling timer."""
    rows = []
    for file in sorted(path.rglob('*')):
        if file.is_file():
            st = file.stat()
            rows.append((str(file.relative_to(path)), st.st_size, st.st_mtime_ns))
    return rows


def completed(job, config, torrent_hash, wait_seconds=1800):
    """One-shot qBittorrent callback; persist attempts before external publication."""
    torrent_hash = torrent_hash.strip().lower()
    if len(torrent_hash) not in (40, 64) or any(c not in '0123456789abcdef' for c in torrent_hash):
        raise ValueError('Pass the completed source torrent hash using qBittorrent %I.')
    library = Path(config.get('Paths', 'DATADIR')).resolve(strict=True)
    source = Path(job['source']).resolve(strict=True)
    if source == library or not source.is_relative_to(library):
        raise ValueError('The completed release must be a subfolder inside DATADIR, never the whole library.')
    # Concurrent completion callbacks wait for the normal web/CLI upload lock.
    deadline = time.monotonic() + wait_seconds
    while True:
        lock = upload_lock()
        try:
            lock.__enter__()
            break
        except ValueError:
            if time.monotonic() >= deadline:
                raise ValueError('Uploader stayed busy; no upload attempted. Run this callback again later.')
            time.sleep(2)
    try:
        with closing(sqlite3.connect(ROOT / 'data/completions.db')) as db:
            db.execute('CREATE TABLE IF NOT EXISTS attempts '
                       '(source_hash TEXT PRIMARY KEY, path TEXT NOT NULL, status TEXT NOT NULL)')
            previous = db.execute('SELECT status FROM attempts WHERE source_hash=?', (torrent_hash,)).fetchone()
            if previous:
                print(f'Completion already recorded ({previous[0]}). No automatic repost.')
                return 0
            db.execute('INSERT INTO attempts VALUES (?, ?, ?)', (torrent_hash, str(source), 'started'))
            db.commit()
            # A crash or uncertain result leaves an attempt on record for manual review.
            result = _run_job(job, config, print)
            db.execute('UPDATE attempts SET status=? WHERE source_hash=?',
                       ('finished' if result == 0 else 'duplicate' if result == 10 else 'needs_review', torrent_hash))
            db.commit()
            return result
    finally:
        lock.__exit__(None, None, None)


def watch(config, category, interval=None, stable=None, folder=None):
    check_writable(config)
    library = Path(config.get('Paths', 'DATADIR')).resolve()
    requested = Path(folder or config.get('Watch', 'INCOMING_DIR', fallback='').strip() or library)
    incoming = (requested if requested.is_absolute() else library / requested).resolve(strict=True)
    if not incoming.is_dir() or not incoming.is_relative_to(library):
        raise ValueError('The incoming watch folder must be inside the configured content library.')
    interval = max(5, interval or config.getint('Watch', 'INTERVAL_SECONDS', fallback=30))
    stable = max(interval, stable or config.getint('Watch', 'STABLE_SECONDS', fallback=120))
    db = sqlite3.connect(ROOT / 'data/watch.db')
    db.execute('CREATE TABLE IF NOT EXISTS jobs (path TEXT PRIMARY KEY, status TEXT NOT NULL)')
    snapshots = {}
    print(f'Watching {incoming}; settle time {stable}s. Ctrl+C stops.')
    try:
        while True:
            for path in sorted(incoming.iterdir()):
                if not path.is_dir() or path.name.startswith('.') or path.is_symlink():
                    continue
                key = str(path.resolve())
                if db.execute('SELECT 1 FROM jobs WHERE path=?', (key,)).fetchone():
                    continue
                try:
                    stamp = fingerprint(path)
                    if not stamp:
                        continue
                    previous, since = snapshots.get(key, (None, time.monotonic()))
                    if stamp != previous:
                        snapshots[key] = (stamp, time.monotonic())
                        continue
                    if time.monotonic() - since < stable:
                        continue
                    job = validate_job(path, category, config=config)
                    # Commit before upload: an interruption must never trigger an automatic repost.
                    db.execute('INSERT INTO jobs VALUES (?, ?)', (key, 'started'))
                    db.commit()
                    result = run_job(job, config)
                    db.execute('UPDATE jobs SET status=? WHERE path=?',
                               ('finished' if result == 0 else 'duplicate' if result == 10 else 'needs_review', key))
                    db.commit()
                except (OSError, ValueError) as exc:
                    print(f'Skipped {path.name}: {exc}', flush=True)
            time.sleep(interval)
    except KeyboardInterrupt:
        print('Watcher stopped. Check DigitalCore before retrying an interrupted upload.')
    finally:
        db.close()


def main():
    parser = argparse.ArgumentParser(description='DigitalCore Uploader - browser, CLI and folder watch')
    sub = parser.add_subparsers(dest='command')
    sub.add_parser('gui', help='Open the HTML upload interface')
    web_parser = sub.add_parser('web', help='Run the browser upload interface')
    web_parser.add_argument('--host', default='127.0.0.1')
    web_parser.add_argument('--port', type=int, default=6080)
    web_parser.add_argument('--no-browser', action='store_true')
    web_parser.add_argument('--tls-cert', help='PEM TLS certificate for HTTPS')
    web_parser.add_argument('--tls-key', help='PEM TLS private key for HTTPS')
    sub.add_parser('check', help='Check settings, dependencies and folder write access; no network calls')
    upload = sub.add_parser('upload', help='Upload one release folder')
    upload.add_argument('folder')
    upload.add_argument('--cat', '-cat', type=int, required=True)
    for field in ('studio', 'performers', 'genre', 'year', 'language', 'description'):
        upload.add_argument('--' + field, default='')
    upload.add_argument('--image', action='append', default=[])
    upload.add_argument('--dry-run', action='store_true', help='Review only, without uploading or hashing')
    completion = sub.add_parser('completed', parents=[upload], add_help=False,
                                help='One-shot qBittorrent completion callback for a release folder')
    completion.add_argument('--hash', required=True, help='Completed source torrent hash: qBittorrent %%I')
    watcher = sub.add_parser('watch', help='Upload settled release folders, recording each attempt')
    watcher.add_argument('--cat', type=int, required=True)
    watcher.add_argument('--interval', type=int)
    watcher.add_argument('--stable', type=int)
    watcher.add_argument('--folder', help='Dedicated incoming folder inside DATADIR; overrides Watch.INCOMING_DIR')
    args = parser.parse_args()
    os.chdir(ROOT)
    ensure_config()
    if args.command in (None, 'gui', 'web'):
        from web import serve
        serve(getattr(args, 'host', '127.0.0.1'), getattr(args, 'port', 6080),
              open_browser=not getattr(args, 'no_browser', False),
              tls_cert=getattr(args, 'tls_cert', None), tls_key=getattr(args, 'tls_key', None))
        return 0
    config = load_config()
    errors = check_config(config, tools=not getattr(args, 'dry_run', False))
    if errors:
        print('\n'.join('ERROR: ' + e for e in errors), file=sys.stderr)
        return 2
    if args.command == 'check':
        check_writable(config)
        print('Configuration, dependencies and write access OK. No upload made.')
        return 0
    if args.command == 'watch':
        if args.cat not in {row['id'] for row in categories()}:
            raise ValueError('Choose a valid category ID from files/digitalcore-categories.json.')
        watch(config, args.cat, args.interval, args.stable, args.folder)
        return 0
    job = validate_job(args.folder, args.cat, args.description, args.image,
                       dict(studio=args.studio, performers=args.performers, content_tags=args.genre,
                            release_year=args.year, language=args.language), config)
    print(review_text(job, config))
    if args.dry_run:
        print('\nDry run complete. No network calls, torrent creation or upload.')
        return 0
    if args.command == 'completed':
        return completed(job, config, args.hash)
    return run_job(job, config)


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (OSError, ValueError, configparser.Error) as exc:
        print(f'ERROR: {exc}', file=sys.stderr)
        sys.exit(2)
