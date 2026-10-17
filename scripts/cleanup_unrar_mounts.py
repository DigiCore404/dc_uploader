#!/usr/bin/env python3
import argparse
import os
import sys
import time
from pathlib import Path

import requests

ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR))
os.chdir(ROOT_DIR)

from utils.config_loader import ConfigLoader
from utils.logging_utils import log_to_file
from utils.unrar_upload_utils import load_registry, save_registry, title_tag, unmount_path


config = ConfigLoader().get_config()
LOG_PATH = Path(config.get('UnrarUpload', 'CLEANUP_LOG', fallback='files/unrar_cleanup.log'))


def parse_hosts():
    if config.has_option('Qbit', 'HOSTS'):
        raw_hosts = config.get('Qbit', 'HOSTS', fallback='')
    else:
        raw_hosts = config.get('Qbit', 'URL', fallback=config.get('Qbit', 'HOST', fallback=''))
    return [host.strip().rstrip('/') for host in raw_hosts.split(',') if host.strip()]


def qbit_login(session, host):
    username = config.get('Qbit', 'USERNAME', fallback='')
    password = config.get('Qbit', 'PASSWORD', fallback='')
    if not username or not password:
        raise RuntimeError('Qbit username/password not configured')

    response = session.post(
        f'{host}/api/v2/auth/login',
        data={'username': username, 'password': password},
        timeout=config.getint('Network', 'Timeout', fallback=30),
    )
    response.raise_for_status()
    if response.text.strip().lower() != 'ok.':
        raise RuntimeError(f'qBittorrent login failed for {host}: {response.text[:200]}')


def qbit_torrents(host):
    session = requests.Session()
    qbit_login(session, host)
    response = session.get(
        f'{host}/api/v2/torrents/info',
        timeout=config.getint('Network', 'Timeout', fallback=30),
    )
    response.raise_for_status()
    return response.json()


def collect_qbit_seen_torrents(hosts):
    seen_hashes = set()
    seen_names = set()
    checked_hosts = 0
    total_torrents = 0

    for host in hosts:
        try:
            torrents = qbit_torrents(host)
        except Exception as exc:
            log_to_file(LOG_PATH, f'Could not query qBittorrent at {host}: {exc}')
            continue

        checked_hosts += 1
        total_torrents += len(torrents)
        for torrent in torrents:
            torrent_hash = torrent.get('hash')
            torrent_name = torrent.get('name')
            if torrent_hash:
                seen_hashes.add(torrent_hash.lower())
            if torrent_name:
                seen_names.add(torrent_name)

    return seen_hashes, seen_names, checked_hosts, total_torrents


def mount_is_safe(mount_path):
    mount_path = Path(mount_path).resolve()
    mount_base = Path(config.get('UnrarUpload', 'MOUNT_BASE', fallback=config.get('Paths', 'DATADIR'))).resolve()
    tag = title_tag()
    return mount_path.parent == mount_base and (mount_path.name.endswith(tag) or mount_path.name.startswith(tag))


def record_is_in_grace_period(record):
    grace_seconds = config.getint('UnrarUpload', 'CLEANUP_GRACE_SECONDS', fallback=300)
    if grace_seconds <= 0:
        return False

    created_at_epoch = record.get('created_at_epoch')
    if created_at_epoch is None:
        return False

    try:
        age_seconds = time.time() - float(created_at_epoch)
    except (TypeError, ValueError):
        return False

    return age_seconds < grace_seconds


def cleanup_registry(dry_run=False):
    registry = load_registry()
    if not registry:
        print('No UNRAR mounts registered.')
        return 0

    hosts = parse_hosts()
    if not hosts:
        print('No qBittorrent hosts configured.')
        return 1

    seen_hashes, seen_names, checked_hosts, total_torrents = collect_qbit_seen_torrents(hosts)
    if checked_hosts == 0:
        print('Could not query any qBittorrent host; refusing to cleanup mounts.')
        return 1
    if total_torrents == 0:
        print('qBittorrent returned 0 torrents; refusing to cleanup mounts because this may be a bad API/config state.')
        log_to_file(LOG_PATH, 'qBittorrent returned 0 torrents; refusing to cleanup mounts.')
        return 1

    print(f'Checked {checked_hosts}/{len(hosts)} qBittorrent hosts; saw {total_torrents} torrents.')

    cleaned = 0
    would_clean = 0
    skipped_grace = 0
    for original_name, record in list(registry.items()):
        if record.get('status') != 'seeding':
            continue

        if record_is_in_grace_period(record):
            skipped_grace += 1
            continue

        torrent_hash = (record.get('torrent_hash') or '').lower()
        torrent_name = record.get('torrent_name') or ''
        still_in_qbit = (torrent_hash and torrent_hash in seen_hashes) or (torrent_name and torrent_name in seen_names)
        if still_in_qbit:
            continue

        mount_path = Path(record.get('mount_path') or '')
        if not mount_is_safe(mount_path):
            log_to_file(LOG_PATH, f'Refusing unsafe UNRAR cleanup path for {original_name}: {mount_path}')
            record['status'] = 'cleanup_refused_unsafe_path'
            continue

        if dry_run:
            would_clean += 1
            print(f'Would clean UNRAR mount for removed torrent: {original_name} -> {mount_path}')
            log_to_file(LOG_PATH, f'DRY-RUN would clean UNRAR mount for removed torrent: {original_name} -> {mount_path}')
            continue

        print(f'Cleaning UNRAR mount for removed torrent: {original_name} -> {mount_path}')
        log_to_file(LOG_PATH, f'Cleaning UNRAR mount for removed torrent: {original_name} -> {mount_path}')

        if unmount_path(mount_path, LOG_PATH):
            try:
                mount_path.rmdir()
            except OSError as exc:
                log_to_file(LOG_PATH, f'Could not remove UNRAR mount directory {mount_path}: {exc}')
            record['status'] = 'cleaned'
            record['cleaned_at'] = time.strftime('%Y-%m-%d %H:%M:%S %Z')
            record['cleaned_at_epoch'] = time.time()
            cleaned += 1
        else:
            record['status'] = 'cleanup_failed_unmount'

    if not dry_run:
        save_registry(registry)

    if dry_run:
        print(f'UNRAR cleanup dry-run complete. Would clean: {would_clean}. Skipped grace-period: {skipped_grace}')
    else:
        print(f'UNRAR cleanup complete. Cleaned: {cleaned}. Skipped grace-period: {skipped_grace}')
    return 0


def main():
    parser = argparse.ArgumentParser(description='Cleanup rar2fs UNRAR mounts after qBittorrent removes torrents.')
    parser.add_argument('--dry-run', action='store_true', help='Show what would be cleaned without unmounting/removing.')
    args = parser.parse_args()
    raise SystemExit(cleanup_registry(dry_run=args.dry_run))


if __name__ == '__main__':
    main()
