#!/usr/bin/env python3
"""Remove only idle, complete DigitalCore seeds with healthy replacement seeds.

Dry-run by default. Never delete payload files. Credentials remain in the existing
private autoremove configuration; only the explicitly selected tasks are read.
"""
import argparse
import collections
import fcntl
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path, PurePosixPath
import time
from urllib.parse import urlsplit

import requests
import yaml

DEFAULT_POLICY = {
    'ratio': 2.5, 'age_seconds': 259200, 'minimum_seed_seconds': 3600,
    'idle_seconds': 3600, 'minimum_reported_seeds': 3,
    'grace_seconds': 600, 'maximum_removals': 5,
}


def eligibility(torrent, now, policy):
    """Fail closed for unknown fields, selective downloads and active peers."""
    try:
        if torrent['state'] not in ('uploading', 'stalledUP'):
            return 'not_seeding'
        total = int(torrent['total_size'])
        if (total <= 0 or float(torrent['progress']) != 1 or int(torrent['amount_left']) != 0
                or int(torrent['completed']) < total or int(torrent['size']) != total):
            return 'incomplete'
        seeds, leechers = int(torrent['num_complete']), int(torrent['num_incomplete'])
        if seeds < int(policy['minimum_reported_seeds']):
            return 'not_enough_other_seeds'
        if leechers != 0 or int(torrent['num_leechs']) != 0 or int(torrent['upspeed']) != 0:
            return 'downloaders_active_or_unknown'
        if int(torrent['seeding_time']) < int(policy['minimum_seed_seconds']):
            return 'minimum_seed_time'
        activity = int(torrent['last_activity'])
        if activity <= 0 or now - activity < int(policy['idle_seconds']):
            return 'recent_activity_or_unknown'
        if torrent.get('category') == 'upper-zip-seed':
            return 'protected_category'
        age = now - int(torrent['added_on'])
        if float(torrent['ratio']) < float(policy['ratio']) and age < int(policy['age_seconds']):
            return 'ratio_and_age_not_reached'
    except (KeyError, TypeError, ValueError, OverflowError):
        return 'unknown_metadata'
    return None


def healthy_tracker(trackers):
    for tracker in trackers:
        host = urlsplit(tracker.get('url', '')).hostname or ''
        if (host == 'digitalcore.club' or host.endswith('.digitalcore.club')) and tracker.get('status') == 2:
            return True
    return False


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


class Client:
    def __init__(self, settings):
        self.base = settings['host'].rstrip('/')
        parsed = urlsplit(self.base)
        if parsed.hostname not in ('127.0.0.1', 'localhost', '::1') or parsed.port not in (5011, 5012):
            raise ValueError('Only the two local DigitalCore clients are allowed')
        self.session = requests.Session()
        result = self.session.post(self.base + '/api/v2/auth/login', data={
            'username': settings['username'], 'password': settings['password'],
        }, timeout=15)
        result.raise_for_status()
        if result.text.strip() != 'Ok.':
            raise RuntimeError('qBittorrent authentication failed')

    def get(self, route, **params):
        response = self.session.get(self.base + '/api/v2/' + route, params=params, timeout=20)
        response.raise_for_status()
        return response.json()

    def remove_torrent_only(self, infohash):
        response = self.session.post(self.base + '/api/v2/torrents/delete', data={
            'hashes': infohash, 'deleteFiles': 'false',
        }, timeout=20)
        response.raise_for_status()


def inspect_candidate(client, torrent):
    if not healthy_tracker(client.get('torrents/trackers', hash=torrent['hash'])):
        return 'tracker_not_healthy'
    if not complete_files(torrent, client.get('torrents/files', hash=torrent['hash'])):
        return 'payload_missing_incomplete_or_unselected'
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
        reason = inspect_candidate(client, torrent)
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
        reason = eligibility(fresh, time.time(), policy) or inspect_candidate(client, fresh)
        if reason:
            counts['changed_before_removal:' + reason] += 1
            pending.pop(key, None)
            continue
        action = 'remove_torrent_only' if apply else 'would_remove_torrent_only'
        logger.info(json.dumps({'action': action, 'client': client_name, 'name': fresh['name'],
            'hash': fresh['hash'], 'reason': 'complete_idle_redundant_seed_ratio_or_age',
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
                pending.update(clean_client(Client(credentials[name]), name, policy, previous,
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
