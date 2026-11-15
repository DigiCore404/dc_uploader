#!/usr/bin/env python3
"""Bounded cleanup of inactive upload links and processed events; dry-run default.

Regular payload files and archive directories are never deleted. Registered
torrents, running uploads and mounted releases are protected. A broken link must
be old AND observed missing for a day before it can be removed.
"""
import argparse
import configparser
import fcntl
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import re
import sqlite3
import stat
import time
from urllib.parse import urlsplit
import requests
import yaml

COMPLETE = Path('/uploaders/COMPLETE')
EVENTS = Path('/uploaders/LINKS')
MIN_AGE = 7 * 86400
MISSING_GRACE = 86400
EVENT_AGE = 3 * 86400


def top_release(path, root=COMPLETE):
    try:
        parts = Path(path).relative_to(root).parts
        return parts[0] if parts and parts[0] not in ('.', '..') else None
    except ValueError:
        return None


def old_enough(st, now):
    return now - max(st.st_mtime, st.st_ctime) >= MIN_AGE


def broken_link(path):
    """Only confirmed ENOENT qualifies; I/O/mount/permission failures do not."""
    if not path.is_symlink(): return False
    try:
        path.stat()
        return False
    except FileNotFoundError:
        return True


def collect_protection(config):
    tasks = yaml.safe_load(Path(config['credentials_file']).read_text())
    connections = [tasks[name] for name in ('my_task_cl1', 'my_task_cl2', 'my_task_cl3')]
    extra = Path('/uploaders/dc_uploader/config.ini')
    if extra.exists():
        cfg = configparser.ConfigParser(); cfg.read(extra)
        connections.append(dict(host='http://127.0.0.1:5013',
                                username=cfg.get('Qbit', 'USERNAME'), password=cfg.get('Qbit', 'PASSWORD')))
    protected, processed, seen = set(), set(), set()
    for connection in connections:
        host = connection['host'].rstrip('/'); parsed = urlsplit(host)
        if parsed.hostname not in ('127.0.0.1', 'localhost', '::1') or parsed.port not in (5011, 5012, 5013, 5015):
            raise ValueError('Unexpected client for storage protection')
        if host in seen: continue
        seen.add(host); session = requests.Session()
        r = session.post(host + '/api/v2/auth/login', data={
            'username': connection['username'], 'password': connection['password']}, timeout=15)
        r.raise_for_status()
        if r.text.strip() != 'Ok.': raise RuntimeError('Client protection lookup failed')
        r = session.get(host + '/api/v2/torrents/info', timeout=25); r.raise_for_status(); rows = r.json()
        if not isinstance(rows, list): raise ValueError('Unknown torrent list')
        for row in rows:
            for path in [row.get('content_path', ''), str(Path(row['save_path']) / row['name'])]:
                release = top_release(path)
                if release: protected.add(release)
    for n in ('dc_uploader1', 'dc_uploader2', 'upper1', 'upper'):
        database = Path('/uploaders') / n / 'data/uploads.db'
        if not database.exists(): continue
        with sqlite3.connect('file:' + str(database) + '?mode=ro', uri=True, timeout=5) as db:
            for name, status in db.execute('SELECT name,status FROM uploads'):
                if status in ('pending', 'uploading'): protected.add(name)
                if status in ('uploaded', 'dupe'): processed.add(name)
    # Never walk mounted rar2fs views or delete their mount points.
    for line in Path('/proc/self/mountinfo').read_text().splitlines():
        path = re.sub(r'\\([0-7]{3})', lambda m: chr(int(m[1], 8)), line.split()[4])
        release = top_release(path)
        if release: protected.add(release)
    return protected, processed


def clean_links(protected, previous, now, apply, logger, cursor=0):
    pending = {k: v for k, v in previous.items() if now - v['last_seen'] <= 2 * MISSING_GRACE}
    counts = dict(scanned=0, protected=0, removed_links=0, removed_empty_dirs=0)
    # Read parent directory types without entering mounted rar2fs views.
    roots = sorted((Path(e.path) for e in os.scandir(COMPLETE) if e.is_dir(follow_symlinks=False)), key=lambda p: p.name)
    if not roots: return pending, counts, 0
    start = cursor % len(roots); selected = (roots[start:] + roots[:start])[:100]
    for root in selected:
        counts['scanned'] += 1
        if root.name in protected or not old_enough(root.stat(), now):
            counts['protected'] += 1; continue
        for directory, dirs, files in os.walk(root, followlinks=False):
            dirs[:] = [d for d in dirs if not d.startswith('.') and not (Path(directory) / d).is_symlink()]
            for name in files:
                path = Path(directory) / name
                if not path.is_symlink(): continue  # Never unlink regular payload.
                try:
                    st = path.lstat(); key = str(path)
                    if not old_enough(st, now) or not broken_link(path):
                        pending.pop(key, None); continue
                    first = pending.setdefault(key, dict(first_missing=now, last_seen=now, inode=st.st_ino))
                    if first['inode'] != st.st_ino or now - first['last_seen'] > 2 * MISSING_GRACE:
                        pending[key] = dict(first_missing=now, last_seen=now, inode=st.st_ino); continue
                    first['last_seen'] = now
                    if now - first['first_missing'] < MISSING_GRACE: continue
                    if broken_link(path) and path.lstat().st_ino == st.st_ino:
                        if apply: path.unlink(); pending.pop(key, None)
                        counts['removed_links'] += 1
                        logger.info(json.dumps(dict(action='removed_dead_link' if apply else 'would_remove_dead_link', path=str(path))))
                except OSError:
                    counts['protected'] += 1
            if counts['removed_links'] >= 100: break
        # rmdir is atomic and refuses non-empty directories; markers are kept.
        if apply and old_enough(root.stat(), now):
            try: root.rmdir(); counts['removed_empty_dirs'] += 1
            except OSError: pass
        if counts['removed_links'] >= 100: break
    return pending, counts, (start + counts['scanned']) % len(roots)


def clean_events(now, apply, limit=5000):
    removed = 0
    for item in os.scandir(EVENTS):
        if removed >= limit: break
        if not item.is_file(follow_symlinks=False): continue
        path = Path(item.path)
        before = item.stat(follow_symlinks=False)
        if now - before.st_mtime < EVENT_AGE: continue
        current = path.stat(follow_symlinks=False)
        if current.st_ino != before.st_ino or current.st_mtime_ns != before.st_mtime_ns: continue
        if apply: path.unlink()
        removed += 1
    return removed


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True); parser.add_argument('--apply', action='store_true')
    parser.add_argument('--events-only', action='store_true', help='Only prune old LINKS events; never inspect or touch payload')
    parser.add_argument('--event-limit', type=int, default=5000)
    args = parser.parse_args(); config = json.loads(Path(args.config).read_text())
    state_file = Path('/var/lib/digitalcore-qbit-cleanup/storage-state.json'); state_file.parent.mkdir(parents=True, exist_ok=True)
    log = logging.getLogger('safe-storage-cleanup'); log.setLevel(logging.INFO)
    handler = RotatingFileHandler('/var/log/digitalcore-qbit-cleanup/storage.log', maxBytes=2*1024*1024, backupCount=3)
    handler.setFormatter(logging.Formatter('%(asctime)s %(message)s')); log.addHandler(handler)
    with state_file.with_suffix('.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try: protected, processed = collect_protection(config) if not args.events_only else (set(), set())
        except Exception as error:
            log.error(json.dumps(dict(action='skip_all_cleanup', error_type=type(error).__name__)))
            print('Storage cleanup skipped: protection inventory unavailable'); return 1
        state = json.loads(state_file.read_text()) if state_file.exists() else {}; now = time.time()
        pending, counts, cursor = (state.get('missing', {}), dict(scanned=0), state.get('cursor', 0)) if args.events_only else clean_links(protected, state.get('missing', {}), now, args.apply, log, state.get('cursor', 0))
        if args.event_limit < 1 or args.event_limit > 100000: raise ValueError('Event limit must be 1..100000')
        counts['removed_old_events'] = clean_events(now, args.apply, args.event_limit)
        if args.apply:
            temporary = state_file.with_suffix('.tmp'); temporary.write_text(json.dumps(dict(missing=pending, cursor=cursor))); temporary.replace(state_file)
        log.info(json.dumps(dict(action='summary', dry_run=not args.apply, protected_releases=len(protected), **counts)))
        print(json.dumps(dict(dry_run=not args.apply, protected_releases=len(protected), **counts)))
        return 0


if __name__ == '__main__': raise SystemExit(main())
