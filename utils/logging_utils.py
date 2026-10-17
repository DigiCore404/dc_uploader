import time
from pathlib import Path

def log_to_file(log_file_path, message):
    """Append a log message to the specified log file.
    Args:
        log_file_path (Path): Path to the log file (as a Path object).
        message (str): Message to be logged
    """
    with open(log_file_path, 'a', encoding='utf-8') as log_file:
        log_file.write(f"{message}\n")

def log_upload_details(upload_details, log_file_path: Path, duplicate_found=False):
    """Log detailed upload information to the upload.log file.
    
    Args:
        upload_details (dict): Dictionary containing upload details.
        log_file_path (Path): Path to the log file (as a Path object).
        duplicate_found (bool): Flag indicating if a duplicate was found.
    """
    with open(log_file_path, 'a', encoding='utf-8') as log_file:
        log_file.write("\n########################################################\n")
        log_file.write(f"### auto upload started at: {time.strftime('%a %b %d %H:%M:%S %Z %Y')}\n")
        
        if duplicate_found:
            log_file.write(f"### Dupe: Torrent already uploaded.\n")
            # Log reduced details for duplicate
            log_file.write(f"### name: {upload_details.get('name', 'N/A')}\n")
            log_file.write(f"### path: {upload_details.get('path', 'N/A')}\n")
            log_file.write(f"### size: {upload_details.get('size', 'N/A')}\n")
            log_file.write(f"### nfo: {upload_details.get('nfo', 'N/A')}\n")
        else:
            # Log all details for a full upload
            log_file.write(f"### name: {upload_details.get('name', 'N/A')}\n")
            log_file.write(f"### path: {upload_details.get('path', 'N/A')}\n")
            log_file.write(f"### size: {upload_details.get('size', 'N/A')}\n")
            log_file.write(f"### category: {upload_details.get('category', 'N/A')}\n")
            log_file.write(f"### piece size: {upload_details.get('piece_size', 'N/A')}\n")
            log_file.write(f"### etor started: {upload_details.get('etor_started', 'N/A')}\n")
            log_file.write(f"### Create torrent file.. {upload_details.get('torrent_file', 'N/A')}\n")
            log_file.write(f"### nfo: {upload_details.get('nfo', 'N/A')}\n")
            log_file.write(f"### etor completed: {upload_details.get('etor_completed', 'N/A')}\n")        
            log_file.write(f"### auto upload completed at: {time.strftime('%a %b %d %H:%M:%S %Z %Y')}\n")
            log_file.write("\n")
