import json
import os
import re
import shlex
import shutil
import subprocess
import time
from pathlib import Path

from utils.bcolors import bcolors
from utils.config_loader import ConfigLoader
from utils.logging_utils import log_to_file, log_upload_details
from utils.mediainfo_utils import is_sample_path

config = ConfigLoader().get_config()
PERSISTENT_LOG_PATH = Path(config.get('UnrarUpload', 'LOG', fallback='files/unrar_upload.log'))
ARCHIVE_SUFFIX_RE = re.compile(
    r'(?i)\.(?:rar|r\d{2,3}|s\d{2,3}|part\d+\.rar|zip|7z|[0-9]{3}|rev|sfv|srr)$'
)


def unrar_log(log_file_path, message):
    """Write UNRAR diagnostics to both the per-run tmp log and a persistent log."""
    log_to_file(log_file_path, message)
    log_to_file(PERSISTENT_LOG_PATH, message)


def is_archive_path(path):
    return bool(ARCHIVE_SUFFIX_RE.search(str(path)))


def should_skip_unrar_payload(path):
    return is_sample_path(path) or is_archive_path(path)


def unrar_config_enabled():
    return config.getboolean('UnrarUpload', 'ENABLED', fallback=False)


def title_tag():
    return config.get(
        'UnrarUpload',
        'TITLE_SUFFIX',
        fallback=config.get('UnrarUpload', 'TITLE_PREFIX', fallback='[UNRAR]')
    ).strip()


def title_separator():
    return config.get('UnrarUpload', 'TITLE_SEPARATOR', fallback=' ') or ' '


def unrar_release_name(release_name):
    tag = title_tag()
    if release_name.endswith(tag) or release_name.startswith(tag):
        return release_name
    return f"{release_name}{title_separator()}{tag}"


def parse_category_ids(raw_value):
    return {int(item.strip()) for item in raw_value.split(',') if item.strip()}


def configured_categories():
    raw_value = config.get('UnrarUpload', 'CATEGORIES', fallback='').strip()
    return parse_category_ids(raw_value) if raw_value else set()


def find_rar_files(release_dir, limit=20):
    """Find RAR files while following symlinked release folders/directories."""
    matches = []
    seen_dirs = set()
    for dirpath, dirnames, filenames in os.walk(release_dir, followlinks=True):
        try:
            stat_result = os.stat(dirpath)
        except OSError:
            dirnames[:] = []
            continue

        dir_key = (stat_result.st_dev, stat_result.st_ino)
        if dir_key in seen_dirs:
            dirnames[:] = []
            continue
        seen_dirs.add(dir_key)

        dirnames[:] = [
            dirname for dirname in dirnames
            if not dirname.endswith(title_tag()) and not dirname.endswith(f"{title_separator()}{title_tag()}")
        ]
        for filename in filenames:
            if filename.lower().endswith('.rar'):
                matches.append(str(Path(dirpath) / filename))
                if len(matches) >= limit:
                    return matches
    return matches


def archive_sort_key(path):
    path = Path(path)
    path_text = str(path).lower()
    try:
        size_score = -path.stat().st_size
    except OSError:
        size_score = 0

    # Prefer the main archive over sample/proof archives, then largest archive.
    return (is_sample_path(path), size_score, path_text)


def choose_mount_archive(rar_files):
    candidates = sorted((Path(path) for path in rar_files), key=archive_sort_key)
    for candidate in candidates:
        if not is_sample_path(candidate):
            return candidate
    return candidates[0] if candidates else None


def should_upload_unrar(release_name, release_dir, category_id):
    if config.get('UnrarUpload','MODE',fallback='') == 'unpacked_only':
        return False, 'handled_before_upload', []
    if not unrar_config_enabled():
        return False, 'disabled', []
    tag = title_tag()
    if release_name.endswith(tag) or release_name.startswith(tag):
        return False, 'already_unrar_release', []

    categories = configured_categories()
    if categories and category_id not in categories:
        return False, f'category_not_enabled:{category_id}', []

    rar_files = find_rar_files(release_dir)
    if not rar_files:
        return False, 'no_rar_files', []

    return True, 'enabled', rar_files


def safe_mount_path(release_name):
    mount_base = Path(config.get('UnrarUpload', 'MOUNT_BASE', fallback=config.get('Paths', 'DATADIR'))).resolve()
    mount_name = unrar_release_name(release_name).replace('/', '_').replace('\\', '_').replace('\0', '')
    mount_path = (mount_base / mount_name).resolve()

    if mount_path.parent != mount_base:
        raise ValueError(f"Unsafe UNRAR mount path: {mount_path}")

    return mount_path


def run_mount_command(archive_path, mount_path, log_file_path):
    mount_binary = config.get('UnrarUpload', 'RAR2FS_BIN', fallback='rar2fs').strip()
    if mount_binary == 'rar2fs':
        local_binary = Path(__file__).resolve().parents[1] / 'tools/archive/bin/rar2fs'
        if local_binary.is_file():
            mount_binary = str(local_binary)
    mount_options = config.get('UnrarUpload', 'MOUNT_OPTIONS', fallback='-o allow_other --seek-length=1').strip()
    command = [mount_binary] + shlex.split(mount_options) + [str(archive_path), str(mount_path)]

    unrar_log(log_file_path, f"UNRAR mount command: {' '.join(command)}")
    result = subprocess.run(command, text=True, capture_output=True)
    if result.returncode != 0:
        raise RuntimeError(
            f"rar2fs mount failed with exit code {result.returncode}: "
            f"{(result.stderr or result.stdout or '').strip()}"
        )


def ensure_unrar_mount(release_name, archive_path, log_file_path):
    archive_path = Path(archive_path)
    mount_path = safe_mount_path(release_name)

    if mount_path.exists() and os.path.ismount(mount_path):
        if count_excluded_unrar_paths(mount_path, limit=1):
            unrar_log(
                log_file_path,
                f"UNRAR mount already active but contains archive/sample files; remounting correctly: {mount_path}"
            )
            if not unmount_path(mount_path, log_file_path):
                raise RuntimeError(f"Could not unmount existing incorrect UNRAR mount: {mount_path}")
        else:
            unrar_log(log_file_path, f"UNRAR mount already active: {mount_path}")
            return mount_path

    if mount_path.exists() and any(mount_path.iterdir()):
        raise RuntimeError(f"UNRAR mount path exists and is not empty: {mount_path}")

    mount_path.mkdir(parents=True, exist_ok=True)
    run_mount_command(archive_path, mount_path, log_file_path)

    timeout = config.getint('UnrarUpload', 'MOUNT_READY_TIMEOUT', fallback=15)
    deadline = time.time() + timeout
    while time.time() < deadline:
        if os.path.ismount(mount_path) or any(mount_path.iterdir()):
            unrar_log(log_file_path, f"UNRAR mount ready: {mount_path}")
            return mount_path
        time.sleep(0.5)

    raise RuntimeError(f"UNRAR mount did not become ready within {timeout}s: {mount_path}")


def unmount_path(mount_path, log_file_path):
    mount_path = Path(mount_path)
    commands = [
        ['fusermount', '-u', str(mount_path)],
        ['fusermount3', '-u', str(mount_path)],
        ['umount', str(mount_path)],
    ]

    last_error = ''
    for command in commands:
        if shutil.which(command[0]) is None:
            continue
        result = subprocess.run(command, text=True, capture_output=True)
        if result.returncode == 0:
            unrar_log(log_file_path, f"Unmounted UNRAR mount: {mount_path}")
            return True
        last_error = (result.stderr or result.stdout or '').strip()

    if not os.path.ismount(mount_path):
        return True

    unrar_log(log_file_path, f"Failed to unmount UNRAR mount {mount_path}: {last_error}")
    return False


def scan_directory(directory):
    total_size = 0
    nfo_file = None
    for dirpath, dirnames, filenames in os.walk(directory):
        dirnames[:] = [dirname for dirname in dirnames if not should_skip_unrar_payload(Path(dirpath) / dirname)]
        for filename in sorted(filenames):
            file_path = os.path.join(dirpath, filename)
            if should_skip_unrar_payload(file_path):
                continue
            try:
                total_size += os.path.getsize(os.path.realpath(file_path) if os.path.islink(file_path) else file_path)
            except OSError:
                pass
            if nfo_file is None and filename.lower().endswith('.nfo'):
                nfo_file = file_path
    return total_size, nfo_file


def unrar_exclude_regexs():
    return (
        r'(?i)(^|[/\\])(sample|samples|proof|trailer|extras|extra)([/\\]|$)',
        r'(?i)(^|[/\\])[^/\\]*sample[^/\\]*$',
        r'(?i)(^|[/\\])proof\.[^/\\]*$',
        r'(?i)(^|[/\\])[^/\\]*\.(rar|r\d{2,3}|s\d{2,3}|part\d+\.rar|zip|7z|rev|sfv|srr)$',
        r'(?i)(^|[/\\])[^/\\]*\.(00[1-9]|0[1-9]\d|[1-9]\d{2})$',
    )


def count_excluded_unrar_paths(directory, limit=20):
    excluded = []
    for dirpath, dirnames, filenames in os.walk(directory):
        for dirname in dirnames:
            path = Path(dirpath) / dirname
            if should_skip_unrar_payload(path):
                excluded.append(str(path))
                if len(excluded) >= limit:
                    return excluded
        for filename in filenames:
            path = Path(dirpath) / filename
            if should_skip_unrar_payload(path):
                excluded.append(str(path))
                if len(excluded) >= limit:
                    return excluded
    return excluded


def validate_unrar_torrent_payload(torrent_file, log_file_path):
    from utils.torrent_metadata import TorrentMetadata as Torrent

    torrent = Torrent.read(str(torrent_file))
    bad_files = [str(path) for path in torrent.files if should_skip_unrar_payload(path)]
    if bad_files:
        unrar_log(log_file_path, f"UNRAR torrent validation failed; excluded files still present: {bad_files[:20]}")
        raise RuntimeError(f"UNRAR torrent still contains archive/sample files: {bad_files[:5]}")

    unrar_log(log_file_path, f"UNRAR torrent validation passed: {len(torrent.files)} payload files")
    return True


def mounted_payload_path(mount_path, torrent_name, torrent_file_path):
    normalized_path = str(torrent_file_path).replace('\\', '/')
    parts = [part for part in normalized_path.split('/') if part]
    if parts and parts[0] == torrent_name:
        parts = parts[1:]
    return Path(mount_path).joinpath(*parts)


def validate_unrar_seed_source(mount_path, torrent_file, log_file_path):
    """Make sure qBittorrent can see every torrent payload file before upload."""
    from utils.torrent_metadata import TorrentMetadata as Torrent

    mount_path = Path(mount_path)
    torrent = Torrent.read(str(torrent_file))
    missing_files = []
    zero_byte_files = []

    if not os.path.ismount(mount_path):
        raise RuntimeError(f"UNRAR mount is not active before seed setup: {mount_path}")

    for relative_path in torrent.files:
        payload_path = mounted_payload_path(mount_path, torrent.name, relative_path)
        try:
            stat_result = payload_path.stat()
        except OSError:
            missing_files.append(str(payload_path))
            if len(missing_files) >= 10:
                break
            continue

        if stat_result.st_size <= 0:
            zero_byte_files.append(str(payload_path))

    if missing_files:
        unrar_log(log_file_path, f"UNRAR seed source validation failed; missing files: {missing_files}")
        raise RuntimeError(f"UNRAR mount is missing torrent payload files: {missing_files[:5]}")

    if zero_byte_files:
        unrar_log(log_file_path, f"UNRAR seed source validation warning; zero-byte files: {zero_byte_files[:10]}")

    unrar_log(log_file_path, f"UNRAR seed source validation passed for qBittorrent: {mount_path}")
    return True


def prepare_clean_template(template_path, output_template_path, replacements):
    from utils.template_utils import prepare_template

    prepare_template(str(template_path), output_template_path, replacements)
    with open(output_template_path, 'r', encoding='utf-8') as file:
        template_content = file.read()

    template_content = "\n".join(line for line in template_content.splitlines() if line.strip())
    template_content = re.sub(r'\[([a-zA-Z0-9]+)]\s*\[/\1]', '', template_content)

    with open(output_template_path, 'w', encoding='utf-8') as file:
        file.write(template_content)

    return output_template_path


def torrent_infohash(torrent_path):
    from utils.torrent_metadata import TorrentMetadata as Torrent

    try:
        return Torrent.read(str(torrent_path)).infohash
    except Exception:
        return None


def registry_path():
    return Path(config.get('UnrarUpload', 'REGISTRY', fallback='data/unrar_mounts.json'))


def load_registry():
    path = registry_path()
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError):
        return {}


def save_registry(registry):
    path = registry_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(registry, indent=2, sort_keys=True), encoding='utf-8')


def register_unrar_mount(original_name, mount_path, upload_result, ratio_target, archive_path=None):
    registry = load_registry()
    watch_torrent = upload_result.get('watch_torrent')
    registry[original_name] = {
        'status': 'seeding',
        'created_at': time.strftime('%Y-%m-%d %H:%M:%S %Z'),
        'created_at_epoch': time.time(),
        'source_path': str(Path(config.get('Paths', 'DATADIR')) / original_name),
        'archive_path': str(archive_path) if archive_path else None,
        'mount_path': str(mount_path),
        'torrent_name': upload_result.get('torrent_name') or unrar_release_name(original_name),
        'torrent_id': upload_result.get('torrent_id'),
        'torrent_hash': torrent_infohash(watch_torrent) if watch_torrent else None,
        'watch_torrent': watch_torrent,
        'ratio_target': ratio_target,
    }
    save_registry(registry)


def maybe_cleanup_failed_mount(mount_path, log_file_path):
    if config.getboolean('UnrarUpload', 'KEEP_FAILED_MOUNTS', fallback=False):
        return
    if not mount_path:
        return

    if unmount_path(mount_path, log_file_path):
        try:
            Path(mount_path).rmdir()
            unrar_log(log_file_path, f"Removed failed UNRAR mount directory: {mount_path}")
        except OSError as exc:
            unrar_log(log_file_path, f"Could not remove failed UNRAR mount directory {mount_path}: {exc}")


def run_unrar_upload(
    *,
    release_name,
    release_dir,
    category_name,
    category_id,
    auth_headers,
    mediainfo_content,
    base_replacements,
    template_path,
    tmp_dir,
    upload_log_path,
    hasher,
    dupedl_enabled,
):
    log_file_path = Path(tmp_dir) / 'unrar_upload.log'
    try:
        allowed, reason, rar_files = should_upload_unrar(release_name, release_dir, category_id)
        unrar_log(
            log_file_path,
            (
                f"UNRAR decision for {release_name}: allowed={allowed}, reason={reason}, "
                f"enabled={unrar_config_enabled()}, category={category_id}, "
                f"rar_count={len(rar_files)}, source={release_dir}, suffix={title_tag()!r}"
            )
        )
        if rar_files:
            unrar_log(log_file_path, f"UNRAR rar files for {release_name}: {rar_files[:5]}")
    except Exception as exc:
        message = f"UNRAR decision failed for {release_name}: {exc}"
        unrar_log(log_file_path, message)
        print(f"{bcolors.FAIL}{message}{bcolors.ENDC}")
        return {'status': 'failed', 'reason': message}

    if not allowed:
        message = f"UNRAR upload skipped for {release_name}: {reason}"
        unrar_log(log_file_path, message)
        print(f"{bcolors.YELLOW}{message}{bcolors.ENDC}")
        return {'status': 'skipped', 'reason': reason}

    mount_path = None
    unrar_name = release_name
    unrar_details = {}

    try:
        from utils.mediainfo_utils import generate_mediainfo
        from utils.torrent_utils import create_torrent, upload_torrent

        unrar_name = unrar_release_name(release_name)
        print(f"{bcolors.YELLOW}Creating optional UNRAR upload for {release_name}{bcolors.ENDC}")
        unrar_details = {
            'name': unrar_name,
            'path': 'N/A',
            'category': f"{category_name} ({category_id})",
            'piece_size': None,
            'etor_started': None,
            'torrent_file': None,
            'etor_completed': None,
            'size': 'N/A',
            'nfo': 'NFO file not found',
        }

        archive_path = choose_mount_archive(rar_files)
        if not archive_path:
            raise RuntimeError("No mountable RAR archive found.")

        unrar_log(log_file_path, f"UNRAR upload starting for {release_name} -> {unrar_name}")
        unrar_log(log_file_path, f"UNRAR selected archive: {archive_path}")
        mount_path = ensure_unrar_mount(release_name, archive_path, log_file_path)
        total_size_bytes, nfo_file_path = scan_directory(mount_path)
        if total_size_bytes <= 0:
            raise RuntimeError("UNRAR mount has no payload files after excluding archive/sample files.")

        excluded_paths = count_excluded_unrar_paths(mount_path)
        if excluded_paths:
            unrar_log(log_file_path, f"UNRAR excluding archive/sample/extras paths from torrent: {excluded_paths}")
        unrar_log(
            log_file_path,
            f"UNRAR mounted scan for {unrar_name}: size_bytes={total_size_bytes}, nfo={nfo_file_path or 'none'}"
        )
        unrar_details['path'] = str(mount_path)
        unrar_details['size'] = f"{round(total_size_bytes / (1024 * 1024), 2)} MB"
        unrar_details['nfo'] = nfo_file_path or "NFO file not found"

        unrar_mediainfo = mediainfo_content
        if config.getboolean('UnrarUpload', 'REGENERATE_MEDIAINFO', fallback=True):
            try:
                generated_mediainfo = generate_mediainfo(mount_path, tmp_dir, allow_sample_fallback=False)
            except Exception as exc:
                unrar_log(log_file_path, f"Could not regenerate UNRAR mediainfo, continuing without it: {exc}")
            else:
                if generated_mediainfo and generated_mediainfo.exists():
                    unrar_mediainfo = generated_mediainfo.read_text(encoding='utf-8', errors='ignore')
                else:
                    unrar_log(log_file_path, "UNRAR mediainfo was not regenerated; continuing with existing/empty mediainfo.")

        replacements = dict(base_replacements)
        for placeholder in ('!imageupload!', '!gameimage!', '!gameinfo!', '!screenshots!', '!nfo!'):
            replacements.setdefault(placeholder, '')
        replacements['!releasename!'] = unrar_name

        output_template_path = Path(tmp_dir) / 'unrar_output_template.txt'
        prepared_template = prepare_clean_template(template_path, output_template_path, replacements)

        unrar_details['etor_started'] = time.strftime('%a %b %d %H:%M:%S %Z %Y')
        torrent_file, piece_size = create_torrent(
            mount_path,
            tmp_dir,
            False,
            hasher,
            total_size_bytes=total_size_bytes,
            exclude_regexs=unrar_exclude_regexs(),
        )
        if torrent_file is None:
            raise RuntimeError("Failed to create UNRAR torrent file.")
        validate_unrar_torrent_payload(torrent_file, log_file_path)
        validate_unrar_seed_source(mount_path, torrent_file, log_file_path)
        unrar_log(log_file_path, f"UNRAR torrent created: {torrent_file}, piece_size={piece_size}")

        unrar_details['torrent_file'] = torrent_file
        unrar_details['piece_size'] = piece_size
        unrar_details['etor_completed'] = time.strftime('%a %b %d %H:%M:%S %Z %Y')

        upload_result = upload_torrent(
            torrent_file,
            prepared_template,
            auth_headers,
            category_id,
                    unrar_mediainfo,
            dupedl_enabled,
            allow_plain_fallback=False,
            allow_local_upload_fallback=True,
        )

        unrar_details['upload_status'] = 'uploaded'
        unrar_details['site_upload_status'] = 'accepted'
        unrar_details['seed_setup_status'] = 'watch_torrent_created'
        unrar_details['torrent_id'] = upload_result.get('torrent_id')
        unrar_details['torrent_name'] = upload_result.get('torrent_name')
        unrar_details['watch_torrent'] = upload_result.get('watch_torrent')
        log_upload_details(unrar_details, upload_log_path, duplicate_found=False)

        ratio_target = config.getfloat('UnrarUpload', 'RATIO_TARGET', fallback=2.5)
        register_unrar_mount(release_name, mount_path, upload_result, ratio_target, archive_path=archive_path)
        unrar_log(
            log_file_path,
            (
                f"UNRAR upload completed for {release_name}: id={upload_result.get('torrent_id')}, "
                f"name={upload_result.get('torrent_name')}, watch={upload_result.get('watch_torrent')}, "
                f"registry={registry_path()}"
            )
        )
        print(f"{bcolors.OKGREEN}UNRAR upload completed and mount registered: {mount_path}{bcolors.ENDC}")
        return {
            'status': 'uploaded',
            'mount_path': str(mount_path),
            'torrent_id': upload_result.get('torrent_id'),
            'torrent_name': upload_result.get('torrent_name'),
            'watch_torrent': upload_result.get('watch_torrent'),
        }
    except Exception as exc:
        if not unrar_details:
            unrar_details = {
                'name': unrar_name,
                'path': str(mount_path) if mount_path else 'N/A',
                'category': f"{category_name} ({category_id})",
                'piece_size': None,
                'etor_started': None,
                'torrent_file': None,
                'etor_completed': None,
                'size': 'N/A',
                'nfo': 'NFO file not found',
            }
        if exc.__class__.__name__ == 'TorrentWatchFolderError':
            unrar_details['upload_status'] = 'failed_after_site_upload'
            unrar_details['site_upload_status'] = 'accepted'
            unrar_details['torrent_id'] = exc.torrent_id
            unrar_details['torrent_name'] = exc.torrent_name
            unrar_details['torrent_download_url'] = exc.torrent_url
            unrar_details['seed_setup_status'] = 'failed'
        else:
            unrar_details['upload_status'] = 'failed'

        unrar_details['upload_error'] = str(exc)
        log_upload_details(unrar_details, upload_log_path, duplicate_found=False)
        unrar_log(log_file_path, f"UNRAR upload failed for {release_name}: {exc}")
        print(f"{bcolors.FAIL}UNRAR upload failed: {exc}{bcolors.ENDC}")
        maybe_cleanup_failed_mount(mount_path, log_file_path)
        return {'status': 'failed', 'reason': str(exc), 'mount_path': str(mount_path) if mount_path else None}
