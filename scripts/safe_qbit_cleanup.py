#!/usr/bin/env python3
"""Remove complete torrents at ratio 2, two other seeds or three days; clear proven missing data.

Dry-run by default. Never delete payload files. Credentials remain in the existing
private autoremove configuration; only the explicitly selected tasks are read.
"""
import argparse
import collections
import fcntl
import json
import logging
import math
import re
import stat
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path, PurePosixPath
import time
from urllib.parse import urlsplit

import requests
import yaml

DEFAULT_POLICY = {
    'ratio': 2.0, 'minimum_reported_seeds': 3, 'max_age_seconds': 259200,
    'grace_seconds': 0, 'maximum_removals': 1000,
}


LOCAL_COMPLETE = Path('/uploaders/COMPLETE')
LOCAL_INCOMING = Path('/glftpd/site')
IGNORED_LINK_METADATA = {'.uploaded', '.tvmaze', '.imdb', '.message'}


def expired_complete(torrent, now, policy):
    maximum = int(policy.get('max_age_seconds', 259200))
    try:
        completed = float(torrent.get('completion_on', 0))
        return maximum > 0 and math.isfinite(completed) and completed > 0 and now - completed >= maximum
    except (TypeError, ValueError, OverflowError):
        return False


def local_path_status(path, mounts):
    """Return missing/present/unknown, without following aliases or remote mounts."""
    path = Path(os.path.abspath(path))
    if any(m != Path('/') and (path == m or path.is_relative_to(m) or m.is_relative_to(path)) for m in mounts):
        return 'unknown'
    try:
        for parent in reversed(path.parents):
            if parent.is_symlink():
                return 'unknown'
        path.lstat()
        return 'present'
    except FileNotFoundError:
        return 'missing'
    except OSError:
        return 'unknown'


def local_mounts():
    return [Path(re.sub(r'\\([0-7]{3})', lambda m: chr(int(m[1], 8)), line.split()[4]))
            for line in Path('/proc/self/mountinfo').read_text().splitlines()]


def data_definitely_gone(torrent, mounts=None):
    """Inspect only local COMPLETE links; partial data and uncertain IO stay protected."""
    if torrent.get('uploader_active'):
        return False
    raw = torrent.get('content_path')
    if not raw:
        return False
    root = Path(os.path.abspath(raw))
    if root.parent != LOCAL_COMPLETE:
        return False
    try:
        if mounts is None:
            mounts = local_mounts()
        root_status = local_path_status(root, mounts)
        if root_status == 'unknown':
            return False
        was_complete = (float(torrent.get('progress', 0)) == 1 and int(torrent.get('amount_left', -1)) == 0
                        and int(torrent.get('completed', 0)) >= int(torrent.get('total_size', 1)) > 0)
        old_enough = time.time() - float(torrent.get('added_on', time.time())) >= 60
        if root_status == 'missing':
            return was_complete and old_enough
        stack = [root]
        broken_source_link = False
        while stack:
            path = stack.pop()
            if path.name in IGNORED_LINK_METADATA:
                continue
            st = path.lstat()
            if stat.S_ISLNK(st.st_mode):
                target = Path(os.readlink(path))
                if not target.is_absolute():
                    target = path.parent / target
                target = Path(os.path.abspath(target))
                if not target.is_relative_to(LOCAL_INCOMING):
                    return False
                # Archive aliases and unavailable mounts are not proof of deletion.
                if target.relative_to(LOCAL_INCOMING).parts[0].upper() in {'ARCHIVE', 'ARCHIVES'}:
                    return False
                status = local_path_status(target, mounts)
                if status != 'missing':
                    return False
                broken_source_link = True
            elif stat.S_ISDIR(st.st_mode):
                if local_path_status(path, mounts) != 'present':
                    return False
                stack.extend(path.iterdir())
            elif stat.S_ISREG(st.st_mode):
                # Keep even one byte of partially downloaded payload.
                if st.st_size > 0:
                    return False
            else:
                return False
        return broken_source_link or (was_complete and old_enough)
    except (OSError, ValueError, KeyError, IndexError, TypeError):
        return False


def eligibility(torrent, now, policy):
    """Remove proven missing data or complete torrents meeting ratio, seeds or age."""
    try:
        if torrent.get('uploader_active'):
            return 'active_publisher'
        if torrent.get('data_missing') is True:
            return None
        if torrent['state'] not in ('uploading', 'stalledUP', 'pausedUP', 'stoppedUP', 'missingFiles', 'error'):
            return 'not_seeding'
        total = int(torrent['total_size'])
        if (total <= 0 or float(torrent['progress']) != 1 or int(torrent['amount_left']) != 0
                or int(torrent['completed']) < total or int(torrent['size']) != total):
            return 'incomplete'
        if expired_complete(torrent, now, policy):
            return None
        # Scrape totals include this seed. Three reported seeds guarantees at
        # least two others; ratio alone is an independent removal condition.
        ratio = float(torrent['ratio'])
        if not math.isfinite(ratio) or ratio < 0:
            return 'unknown_metadata'
        if (ratio < float(policy['ratio'])
                and int(torrent['num_complete']) < int(policy['minimum_reported_seeds'])):
            return 'ratio_seeds_or_three_day_age_not_reached'
    except (KeyError, TypeError, ValueError, OverflowError):
        return 'unknown_metadata'
    return None


def healthy_tracker(trackers, allowed_hosts=('digitalcore.club',), minimum_reported_seeds=3):
    groups = {}
    for tracker in trackers:
        host = urlsplit(tracker.get('url', '')).hostname
        if not host:
            continue
        site = next((root for root in allowed_hosts if host == root or host.endswith('.' + root)), None)
        if not site:
            return False
        groups.setdefault(site, []).append(tracker)
    if not groups:
        return False
    for endpoints in groups.values():
        healthy = [entry for entry in endpoints if entry.get('status') == 2]
        # Proxy URLs for the same site report the same swarm at different
        # announce times. One healthy confirmed result is sufficient; a stale
        # mirror must not veto it. Separate sites still need their own proof.
        counts = []
        for entry in healthy:
            try:
                counts.append(int(entry.get('num_seeds', -1)))
            except (TypeError, ValueError):
                continue
        if not counts or max(counts) < minimum_reported_seeds:
            return False
    return True


def complete_files(torrent, files):
    if not files:
        return False
    total = 0
    for item in files:
        try:
            relative = PurePosixPath(item['name'])
            if relative.is_absolute() or '..' in relative.parts or not relative.parts:
                return False
            if int(item['priority']) <= 0 or float(item['progress']) != 1:
                return False
            target = Path(torrent['save_path']).joinpath(*relative.parts)
            expected = int(item['size'])
            if not target.is_file() or target.stat().st_size != expected:
                return False
            total += expected
        except (KeyError, TypeError, ValueError, OSError):
            return False
    return total == int(torrent['total_size'])


def active_upload_names():
    """Keep publication jobs from racing torrent removal, without private config."""
    names = set()
    for process in Path('/proc').iterdir():
        if not process.name.isdigit():
            continue
        try:
            if not (process / 'comm').read_text().startswith(('python', 'bash', 'sh')):
                continue
            argv = [os.fsdecode(arg) for arg in (process / 'cmdline').read_bytes().split(b'\0') if arg]
            for index, arg in enumerate(argv[:-1]):
                if Path(arg).name in ('backend.py', 'recovery_backend.py', 'upload.sh') and not argv[index + 1].startswith('-'):
                    names.add(argv[index + 1])
        except (FileNotFoundError, ProcessLookupError):
            continue
    return names


class Client:
    def __init__(self, settings):
        self.base = settings['host'].rstrip('/')
        parsed = urlsplit(self.base)
        if parsed.hostname not in ('127.0.0.1', 'localhost', '::1') or parsed.port not in (5011, 5012, 5015):
            raise ValueError('Only the three local uploader clients are allowed')
        self.allowed_hosts = settings.get('allowed_tracker_hosts', ['digitalcore.club'])
        self.session = requests.Session()
        result = self.session.post(self.base + '/api/v2/auth/login', data={
            'username': settings['username'], 'password': settings['password'],
        }, timeout=15)
        result.raise_for_status()
        if result.text.strip() != 'Ok.':
            raise RuntimeError('qBittorrent authentication failed')

    def get(self, route, **params):
        # The sync endpoint supplies cached metadata without walking broken FUSE views.
        if route == 'torrents/info':
            # Incremental sync rechecks the latest state before each removal,
            # without copying thousands of unchanged torrent records each time.
            rid = getattr(self, '_rid', 0) if params.get('hashes') else 0
            response = self.session.get(self.base + '/api/v2/sync/maindata', params={'rid': rid}, timeout=20)
            response.raise_for_status()
            data = response.json()
            changes = data.get('torrents', {})
            if not isinstance(changes, dict) or not isinstance(data.get('rid'), int):
                raise ValueError('Incomplete torrent metadata inventory')
            if data.get('full_update'):
                self._inventory = {key: dict(value) for key, value in changes.items()}
            elif not rid or not hasattr(self, '_inventory'):
                raise ValueError('Missing initial torrent metadata inventory')
            else:
                for key, value in changes.items():
                    self._inventory.setdefault(key, {}).update(value)
                for key in data.get('torrents_removed', []):
                    self._inventory.pop(key, None)
            self._rid = data['rid']
            selected = set(params['hashes'].split('|')) if params.get('hashes') else None
            active = active_upload_names()
            try:
                mounts = local_mounts()
            except OSError:
                mounts = None
            result = []
            for infohash, item in self._inventory.items():
                if selected is not None and infohash not in selected:
                    continue
                item = {**item, 'hash': infohash, 'uploader_active': item.get('name') in active}
                item['data_missing'] = data_definitely_gone(item, mounts) if mounts is not None else False
                result.append(item)
            return result
        response = self.session.get(self.base + '/api/v2/' + route, params=params, timeout=20)
        response.raise_for_status()
        return response.json()

    def remove_torrent_only(self, infohash):
        response = self.session.post(self.base + '/api/v2/torrents/delete', data={
            'hashes': infohash, 'deleteFiles': 'false',
        }, timeout=20)
        response.raise_for_status()


def inspect_candidate(client, torrent, policy=None):
    policy = policy or DEFAULT_POLICY
    if torrent.get('data_missing') is True or expired_complete(torrent, time.time(), policy):
        return None
    if float(torrent['ratio']) >= float(policy['ratio']):
        return None
    if not healthy_tracker(client.get('torrents/trackers', hash=torrent['hash']), client.allowed_hosts,
                           int(policy['minimum_reported_seeds'])):
        return 'two_other_tracker_seeds_not_confirmed'
    return None


def clean_client(client, client_name, policy, previous, now, apply, logger):
    pending, counts, removed = {}, collections.Counter(), 0
    rows = client.get('torrents/info')
    if not isinstance(rows, list):
        raise ValueError('Unexpected torrent list')
    for torrent in rows:
        reason = eligibility(torrent, now, policy)
        if reason:
            counts[reason] += 1
            continue
        key = client_name + ':' + torrent['hash']
        reason = inspect_candidate(client, torrent, policy)
        if reason:
            counts[reason] += 1
            continue
        first_seen = previous.get(key, {}).get('first_eligible', now)
        last_seen = previous.get(key, {}).get('last_seen', now)
        if now - last_seen > max(900, policy['grace_seconds'] * 2):
            first_seen = now
        pending[key] = {'first_eligible': first_seen, 'last_seen': now, 'name': torrent['name']}
        if now - first_seen < policy['grace_seconds']:
            counts['observation_grace_period'] += 1
            continue
        if removed >= policy['maximum_removals']:
            counts['per_run_limit'] += 1
            continue
        # Re-read immediately before removal: peers/progress may have changed.
        fresh = client.get('torrents/info', hashes=torrent['hash'])
        if not isinstance(fresh, list) or len(fresh) != 1:
            counts['fresh_lookup_uncertain'] += 1
            pending.pop(key, None)
            continue
        fresh = fresh[0]
        reason = eligibility(fresh, time.time(), policy) or inspect_candidate(client, fresh, policy)
        if reason:
            counts['changed_before_removal:' + reason] += 1
            pending.pop(key, None)
            continue
        action = 'remove_torrent_only' if apply else 'would_remove_torrent_only'
        logger.info(json.dumps({'action': action, 'client': client_name, 'name': fresh['name'],
            'hash': fresh['hash'], 'reason': ('data_definitely_gone' if fresh.get('data_missing') else 'complete_three_days' if expired_complete(fresh, time.time(), policy) else 'complete_ratio_or_two_other_seeds'),
            'ratio': fresh['ratio'], 'reported_seeds': fresh['num_complete'],
            'leechers': fresh['num_incomplete'], 'delete_files': False}))
        if apply:
            client.remove_torrent_only(fresh['hash'])
            pending.pop(key, None)
        removed += 1
    logger.info(json.dumps({'client': client_name, 'total': len(rows), 'removed': removed if apply else 0,
                           'dry_run': not apply, 'protected': dict(counts)}))
    return pending


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True)
    parser.add_argument('--apply', action='store_true', help='Enable removal; default is read-only')
    args = parser.parse_args()
    config = json.loads(Path(args.config).read_text())
    policy = {**DEFAULT_POLICY, **config.get('policy', {})}
    state_file = Path(config['state_file'])
    state_file.parent.mkdir(parents=True, exist_ok=True)
    with state_file.with_suffix('.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        logger = logging.getLogger('safe-qbit-cleanup')
        logger.setLevel(logging.INFO)
        log = Path(config['log_file']); log.parent.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(log, maxBytes=2 * 1024 * 1024, backupCount=3)
        handler.setFormatter(logging.Formatter('%(asctime)s %(message)s'))
        logger.addHandler(handler)
        credentials = yaml.safe_load(Path(config['credentials_file']).read_text())
        previous = json.loads(state_file.read_text()) if state_file.exists() else {}
        pending = {}
        failed = False
        for name in config['tasks']:
            try:
                settings = {**credentials[name], 'allowed_tracker_hosts': config.get('tracker_domains', {}).get(name, ['digitalcore.club'])}
                client_policy = {**policy, **config.get('task_policies', {}).get(name, {})}
                pending.update(clean_client(Client(settings), name, client_policy, previous,
                                            time.time(), args.apply, logger))
            except Exception as error:
                # Do not expose private connection configuration in logs.
                logger.error(json.dumps({'client': name, 'action': 'skip_client',
                                         'error_type': type(error).__name__}))
                failed = True
        if args.apply:
            temporary = state_file.with_suffix('.tmp')
            temporary.write_text(json.dumps(pending))
            os.chmod(temporary, 0o600)
            temporary.replace(state_file)
        print(json.dumps({'dry_run': not args.apply, 'pending_observations': len(pending),
                          'client_errors': failed, 'log_file': str(log)}))
        return 1 if failed else 0


if __name__ == '__main__':
    raise SystemExit(main())
