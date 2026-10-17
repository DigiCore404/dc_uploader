"""Validate user-facing download mappings without changing torrent client settings."""
from pathlib import PurePosixPath


def remote_root(value):
    value = str(value or '').strip()
    if not value:
        raise ValueError('Enter the download root as seen by qBittorrent first.')
    value = value.rstrip('/') or '/'
    if not value.startswith('/') or '..' in PurePosixPath(value).parts or '\\' in value or any(c in value for c in '\r\n\x00'):
        raise ValueError('Enter an absolute folder as seen by qBittorrent, such as /downloads.')
    return value


def check_mapping(clients, history, client_id, remote, local):
    remote = PurePosixPath(remote_root(remote))
    client = clients._client(client_id)
    if not client['enabled']:
        raise ValueError('Enable this client in Clients before testing its folders.')
    result = clients.sync(client_id)
    if not result['ok']:
        raise ValueError(result.get('error') or 'Could not connect to this client.')
    with history.connect() as db:
        rows = db.execute('SELECT hash,save_path FROM qbit_torrents WHERE client_id=? AND progress>=1 ORDER BY hash', (client_id,)).fetchall()
    candidates = [row for row in rows if PurePosixPath(row['save_path']).is_relative_to(remote)][:5]
    tried = False
    for row in candidates:
        torrent, files = clients.import_status(client_id, row['hash'])
        if not torrent or not files or float(torrent.get('progress', 0)) < 1:
            continue
        save = PurePosixPath(str(torrent.get('save_path', '')))
        if not save.is_relative_to(remote):
            continue
        tried = True
        base = local.joinpath(*save.relative_to(remote).parts)
        matches = 0
        for item in files:
            rel = PurePosixPath(str(item.get('name', '')))
            if rel.is_absolute() or '..' in rel.parts or '\\' in str(rel):
                break
            file = base.joinpath(*rel.parts).resolve()
            if not file.is_relative_to(local) or not file.is_file() or file.stat().st_size != int(item['size']):
                break
            matches += 1
        else:
            return {'status': 'verified', 'message': f'Connected to {client["name"]}. Found {matches} matching files from an existing completed torrent. Download folder mapping verified for this sample. Each import is checked again before publishing.', 'checked_files': matches}
    if tried:
        return {'status': 'mismatch', 'message': 'Client connected, but completed torrent files were not found at the matching uploader path with the expected sizes. Check both container mounts. Do not start automatic imports until the mapping is correct.'}
    return {'status': 'unverified', 'message': 'Client connected. No completed torrent under this download root is available to verify the shared files. Matching path names alone do not prove that both containers see the same storage.'}
