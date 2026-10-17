import time
from pathlib import Path

DEFAULT_MAX_LOG_BYTES = 512 * 1024
DEFAULT_KEEP_LOG_BYTES = 128 * 1024


def log_timestamp():
    return time.strftime('%Y-%m-%d %H:%M:%S %Z')


def timestamp_log_message(message):
    """Prefix each log line with a timestamp so standalone error logs are useful later."""
    lines = str(message).splitlines() or ['']
    return "\n".join(f"[{log_timestamp()}] {line}" for line in lines)


def prune_text_log(log_file_path, max_bytes=DEFAULT_MAX_LOG_BYTES, keep_bytes=DEFAULT_KEEP_LOG_BYTES):
    """Trim a text log file to its newest bytes once it grows past the limit."""
    log_file_path = Path(log_file_path)
    if not log_file_path.exists():
        return

    file_size = log_file_path.stat().st_size
    if file_size <= max_bytes:
        return

    keep_bytes = min(keep_bytes, file_size)
    with open(log_file_path, 'rb') as log_file:
        log_file.seek(-keep_bytes, 2)
        tail = log_file.read().decode('utf-8', errors='ignore')

    first_newline = tail.find('\n')
    if first_newline != -1:
        tail = tail[first_newline + 1:]

    with open(log_file_path, 'w', encoding='utf-8') as log_file:
        log_file.write(timestamp_log_message("... log truncated ...") + "\n")
        log_file.write(tail)


def log_to_file(log_file_path, message):
    """Append a log message to the specified log file.
    Args:
        log_file_path (Path): Path to the log file (as a Path object).
        message (str): Message to be logged
    """
    log_file_path = Path(log_file_path)
    log_file_path.parent.mkdir(parents=True, exist_ok=True)
    prune_text_log(log_file_path)
    with open(log_file_path, 'a', encoding='utf-8') as log_file:
        log_file.write(f"{timestamp_log_message(message)}\n")

def log_upload_details(upload_details, log_file_path: Path, duplicate_found=False):
    """Log detailed upload information to the upload.log file.
    
    Args:
        upload_details (dict): Dictionary containing upload details.
        log_file_path (Path): Path to the log file (as a Path object).
        duplicate_found (bool): Flag indicating if a duplicate was found.
    """
    prune_text_log(log_file_path, max_bytes=2 * 1024 * 1024, keep_bytes=512 * 1024)
    with open(log_file_path, 'a', encoding='utf-8') as log_file:
        def write_line(line=""):
            if line:
                log_file.write(f"{timestamp_log_message(line)}\n")
            else:
                log_file.write("\n")

        write_line()
        write_line("########################################################")
        write_line(f"### auto upload started at: {time.strftime('%a %b %d %H:%M:%S %Z %Y')}")
        
        if duplicate_found:
            write_line("### Dupe: Torrent already uploaded.")
            # Log reduced details for duplicate
            write_line(f"### name: {upload_details.get('name', 'N/A')}")
            write_line(f"### path: {upload_details.get('path', 'N/A')}")
            write_line(f"### size: {upload_details.get('size', 'N/A')}")
            write_line(f"### nfo: {upload_details.get('nfo', 'N/A')}")
            if upload_details.get('unrar_upload_status'):
                write_line(f"### unrar upload status: {upload_details.get('unrar_upload_status')}")
            if upload_details.get('unrar_upload_reason'):
                write_line(f"### unrar upload reason: {upload_details.get('unrar_upload_reason')}")
            if upload_details.get('unrar_mount_path'):
                write_line(f"### unrar mount path: {upload_details.get('unrar_mount_path')}")
            if upload_details.get('unrar_torrent_name'):
                write_line(f"### unrar torrent name: {upload_details.get('unrar_torrent_name')}")
        else:
            # Log all details for a full upload
            write_line(f"### name: {upload_details.get('name', 'N/A')}")
            write_line(f"### path: {upload_details.get('path', 'N/A')}")
            write_line(f"### size: {upload_details.get('size', 'N/A')}")
            write_line(f"### upload status: {upload_details.get('upload_status', 'N/A')}")
            if upload_details.get('site_upload_status'):
                write_line(f"### site upload status: {upload_details.get('site_upload_status')}")
            if upload_details.get('seed_setup_status'):
                write_line(f"### seed setup status: {upload_details.get('seed_setup_status')}")
            if upload_details.get('upload_error'):
                write_line(f"### upload error: {upload_details.get('upload_error')}")
            if upload_details.get('torrent_id'):
                write_line(f"### site torrent id: {upload_details.get('torrent_id')}")
            if upload_details.get('torrent_name'):
                write_line(f"### site torrent name: {upload_details.get('torrent_name')}")
            if upload_details.get('torrent_download_url'):
                write_line(f"### site torrent download url: {upload_details.get('torrent_download_url')}")
            if upload_details.get('watch_torrent'):
                write_line(f"### watch torrent: {upload_details.get('watch_torrent')}")
            if upload_details.get('unrar_upload_status'):
                write_line(f"### unrar upload status: {upload_details.get('unrar_upload_status')}")
            if upload_details.get('unrar_upload_reason'):
                write_line(f"### unrar upload reason: {upload_details.get('unrar_upload_reason')}")
            if upload_details.get('unrar_mount_path'):
                write_line(f"### unrar mount path: {upload_details.get('unrar_mount_path')}")
            if upload_details.get('unrar_torrent_name'):
                write_line(f"### unrar torrent name: {upload_details.get('unrar_torrent_name')}")
            write_line(f"### category: {upload_details.get('category', 'N/A')}")
            write_line(f"### piece size: {upload_details.get('piece_size', 'N/A')}")
            write_line(f"### etor started: {upload_details.get('etor_started', 'N/A')}")
            write_line(f"### Create torrent file.. {upload_details.get('torrent_file', 'N/A')}")
            write_line(f"### nfo: {upload_details.get('nfo', 'N/A')}")
            write_line(f"### etor completed: {upload_details.get('etor_completed', 'N/A')}")
            write_line(f"### auto upload completed at: {time.strftime('%a %b %d %H:%M:%S %Z %Y')}")
            write_line()
