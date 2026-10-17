import subprocess
import configparser
import json
import os
import re
import math
import shutil
import time
from pathlib import Path

import requests
from utils.torrent_metadata import TorrentMetadata as Torrent

from utils.bcolors import bcolors
from utils.config_loader import ConfigLoader
from utils.job_events import publish as job_event
from utils.fastresume_utils import add_fastresume
from utils.logging_utils import log_to_file

# Load configuration
config = ConfigLoader().get_config()
ROOT_DIR = Path(__file__).resolve().parents[1]
REQUEST_TIMEOUT = config.getint('Network', 'Timeout', fallback=60)
VERIFY_SSL = config.getboolean('Network', 'VERIFY_SSL', fallback=False)
TORRENT_DOWNLOAD_RETRIES = config.getint('Network', 'TorrentDownloadRetries', fallback=12)
TORRENT_DOWNLOAD_RETRY_DELAY = config.getfloat('Network', 'TorrentDownloadRetryDelay', fallback=5.0)

class UploadResponseError(RuntimeError):
    """The upload outcome cannot be determined from the server confirmation."""


def parse_upload_response(response):
    try:
        payload = response.json()
    except ValueError as exc:
        raise UploadResponseError(
            f"Server returned HTTP {response.status_code} without valid JSON. "
            "The torrent may already have been uploaded. Check the site before retrying; "
            "the server response is recorded in files/upload_response.log."
        ) from exc
    if (not isinstance(payload, dict) or isinstance(payload.get('id'), bool)
            or not str(payload.get('id', '')).isdigit() or int(payload['id']) <= 0
            or not isinstance(payload.get('name'), str) or not payload['name'].strip()):
        raise UploadResponseError(
            "Server confirmation is missing a valid torrent ID or name. "
            "Check the site before retrying; see files/upload_response.log."
        )
    return payload


def resolve_config_path(path_value):
    path = Path(path_value)
    return path if path.is_absolute() else ROOT_DIR / path


TMP_DIR = str(resolve_config_path(config.get('Paths', 'TMP_DIR')) / str(os.getpid()))
WATCHFOLDER = str(resolve_config_path(config.get('Paths', 'WATCHFOLDER')))
PREPENDNAME = config.get('Settings', 'PREPENDNAME')

# Ensure TMP_DIR exists
os.makedirs(TMP_DIR, exist_ok=True)



def persistent_log_path(file_name):
    upload_log = Path(config.get('Paths', 'UPLOADLOG', fallback='files/upload.log'))
    log_dir = upload_log.parent if upload_log.parent != Path('') else Path('files')
    return resolve_config_path(log_dir) / file_name


def log_download_event(file_name, message):
    """Keep per-run tmp logs and persistent files/ logs for post-cleanup debugging."""
    log_to_file(os.path.join(TMP_DIR, file_name), message)
    log_to_file(persistent_log_path(file_name), message)


def copy_torrent_to_watchfolder(source_path, final_torrent_path, remove_source=False):
    """Copy via a temporary file so qBittorrent never sees a partial torrent."""
    temp_final_path = f"{final_torrent_path}.tmp.{os.getpid()}"
    shutil.copyfile(source_path, temp_final_path)
    os.replace(temp_final_path, final_torrent_path)
    if remove_source:
        os.remove(source_path)


def fetch_torrent_with_retries(url, auth_headers, release_name, log_file):
    """Download torrent bytes, retrying while the site finishes publishing the torrent."""
    retry_statuses = {404, 409, 429, 500, 502, 503, 504}
    attempts = max(1, TORRENT_DOWNLOAD_RETRIES)

    for attempt in range(1, attempts + 1):
        try:
            response = requests.get(url, headers=auth_headers, timeout=REQUEST_TIMEOUT, verify=VERIFY_SSL)
        except requests.RequestException as exc:
            if attempt >= attempts:
                raise
            message = (
                f"Torrent download request failed for {release_name}; "
                f"attempt={attempt}/{attempts}, retry_in={TORRENT_DOWNLOAD_RETRY_DELAY}s, error={exc}"
            )
            log_download_event(log_file, message)
            log_download_event('torrent_download_error.log', message)
            time.sleep(TORRENT_DOWNLOAD_RETRY_DELAY)
            continue

        if response.ok:
            if attempt > 1:
                log_download_event(log_file, f"Torrent download succeeded after retry: attempt={attempt}/{attempts}")
            return response

        if response.status_code in retry_statuses and attempt < attempts:
            message = (
                f"Torrent download not ready for {release_name}; "
                f"status={response.status_code}, attempt={attempt}/{attempts}, "
                f"retry_in={TORRENT_DOWNLOAD_RETRY_DELAY}s, url={url}, body={response.text[:300]}"
            )
            log_download_event(log_file, message)
            log_download_event('torrent_download_error.log', message)
            time.sleep(TORRENT_DOWNLOAD_RETRY_DELAY)
            continue

        response.raise_for_status()

    raise RuntimeError(f"Torrent download failed after {attempts} attempts for {release_name}: {url}")


def validate_downloaded_torrent(torrent_path, log_file):
    try:
        torrent = Torrent.read(str(torrent_path))
        torrent.validate()
        job_event(torrent_hash=torrent.infohash)
    except Exception as exc:
        message = f"Downloaded torrent is invalid and will not be copied to watch folder: {torrent_path}; error={exc}"
        log_download_event(log_file, message)
        log_download_event('torrent_download_error.log', message)
        raise RuntimeError(message)


def place_torrent_in_watchfolder(
    source_torrent_path,
    release_name,
    log_file,
    remove_source=False,
    source_label='torrent',
    allow_plain_fallback=None,
):
    """Add fastresume when possible, then place a torrent in the watch folder."""
    validate_downloaded_torrent(source_torrent_path, log_file)

    final_torrent_path = os.path.join(WATCHFOLDER, f'{PREPENDNAME}{release_name}.torrent')
    os.makedirs(WATCHFOLDER, exist_ok=True)
    log_download_event(log_file, f"Watch folder target: {final_torrent_path}; source={source_label}")

    plain_fallback_enabled = (
        config.getboolean('Settings', 'WATCHFOLDER_FALLBACK_PLAIN', fallback=True)
        if allow_plain_fallback is None
        else bool(allow_plain_fallback)
    )

    if config.getboolean('Settings', 'ADDFASTRESUME'):
        download_dir = config.get('Paths', 'DATADIR')
        fastresume_output_path = os.path.join(TMP_DIR, f'{PREPENDNAME}{release_name}.fastresume.torrent')
        log_download_event(
            log_file,
            f"Adding fastresume: download_dir={download_dir}, output={fastresume_output_path}, source={source_label}"
        )
        fastresume_ok = add_fastresume(source_torrent_path, download_dir, fastresume_output_path)

        if fastresume_ok and os.path.exists(fastresume_output_path):
            copy_torrent_to_watchfolder(fastresume_output_path, final_torrent_path, remove_source=True)
            if remove_source:
                os.remove(source_torrent_path)
            log_download_event(log_file, f"Fastresume torrent copied to watch folder: {final_torrent_path}")
            print(f"{bcolors.OKGREEN}Torrent copied to watch folder: {final_torrent_path}\n{bcolors.ENDC}")
            return final_torrent_path

        error_message = (
            "Fast resume failed or output was not found. "
            f"download_dir={download_dir}, output={fastresume_output_path}, source={source_label}"
        )
        log_download_event('torrent_download_error.log', error_message)
        print(f"{bcolors.WARNING}{error_message}{bcolors.ENDC}")

        if plain_fallback_enabled:
            copy_torrent_to_watchfolder(source_torrent_path, final_torrent_path, remove_source=remove_source)
            fallback_message = (
                "Fastresume failed, but WATCHFOLDER_FALLBACK_PLAIN is enabled. "
                f"Plain {source_label} copied to watch folder: {final_torrent_path}. "
                "If qBittorrent's watched-folder save path points at DATADIR, this should still seed."
            )
            log_download_event(log_file, fallback_message)
            log_download_event('torrent_download_error.log', fallback_message)
            print(f"{bcolors.WARNING}{fallback_message}\n{bcolors.ENDC}")
            return final_torrent_path

        error_message = (
            f"{error_message}. Plain watchfolder fallback is disabled for this upload, "
            "so no torrent was copied that could start downloading instead of seeding."
        )
        log_download_event('torrent_download_error.log', error_message)
        print(f"{bcolors.FAIL}{error_message}{bcolors.ENDC}")
        return None

    if allow_plain_fallback is False:
        error_message = (
            "ADDFASTRESUME is disabled, but this upload requires fastresume. "
            f"Refusing to copy plain {source_label} to watch folder because it may download instead of seed: "
            f"{final_torrent_path}"
        )
        log_download_event(log_file, error_message)
        log_download_event('torrent_download_error.log', error_message)
        print(f"{bcolors.FAIL}{error_message}{bcolors.ENDC}")
        return None

    copy_torrent_to_watchfolder(source_torrent_path, final_torrent_path, remove_source=remove_source)
    log_download_event(log_file, f"Plain {source_label} copied to watch folder: {final_torrent_path}")
    print(f"{bcolors.OKGREEN}Torrent copied to watch folder: {final_torrent_path}\n{bcolors.ENDC}")
    return final_torrent_path


class TorrentWatchFolderError(RuntimeError):
    """Raised when the site accepted a torrent but local seeding setup failed."""

    def __init__(self, message, *, torrent_id=None, torrent_name=None, torrent_url=None):
        super().__init__(message)
        self.torrent_id = torrent_id
        self.torrent_name = torrent_name
        self.torrent_url = torrent_url


def get_root_dir():
    """Use config.ini to get the root directory."""
    return os.path.dirname(os.path.abspath('config.ini'))

def create_torrent(directory,temp_dir,edit,hasher,total_size_bytes=None,exclude_globs=(),exclude_regexs=(),source_torrent=None):
    from utils.runtime_tools import find_tool
    directory=Path(directory).resolve();temp_dir=Path(temp_dir);temp_dir.mkdir(parents=True,exist_ok=True)
    binary=find_tool('mkbrr',config)
    if not binary:raise RuntimeError('mkbrr is missing. Configure Torrent.MKBRR_BIN or reinstall the package.')
    if hasher!='mkbrr':raise ValueError('Set Torrent.HASHER = mkbrr; the torf engine has been removed.')
    output=temp_dir/(directory.name+'.torrent')
    source_folder=config.get('Torrent','SOURCEFOLDER',fallback='').strip()
    original=Path(source_folder)/(directory.name+'.torrent') if source_folder else None
    common=['--tracker',config.get('Torrent','ANNOUNCEURL'),'--private','--source',config.get('Torrent','ESOURCE'),'--comment',config.get('Torrent','ECOMMENT'),'--skip-prefix']
    editing=False
    imported=None
    if source_torrent:
        from utils.source_torrent import prepare
        imported=prepare(source_torrent,directory,temp_dir/'imported-source.torrent')
        original=temp_dir/'imported-source.torrent'
        if exclude_globs or exclude_regexs:raise ValueError('Import torrent editing cannot exclude payload files. Extracted/changed payloads need a separate new upload.')
    if (source_torrent or edit) and original and original.is_file() and not exclude_globs and not exclude_regexs:
        metadata=Torrent.read(original)
        if metadata.name!=directory.name or not metadata.verify_filesize(directory):raise ValueError('Existing torrent does not match the selected release files.')
        command=[binary,'modify',str(original),'--output',output.stem,'--output-dir',str(temp_dir),*common]
        editing=True
    else:
        command=[binary,'create',str(directory),'--output',str(output),*common]
        for pattern in exclude_globs:command+=['--exclude',str(pattern)]
        if exclude_regexs:
            import glob
            patterns=[re.compile(pattern) for pattern in exclude_regexs]
            for root,_,files in os.walk(directory):
                for name in files:
                    relative=Path(root,name).relative_to(directory).as_posix()
                    if any(pattern.search(relative) for pattern in patterns):command+=['--exclude',glob.escape(relative)]
    print('mkbrr: '+('editing existing torrent without rehashing' if editing else 'hashing release files'),flush=True)
    job_event(stage='hashing',hash_progress=None if editing else 0.0,hash_mode='editing' if editing else 'hashing',hash_total=int(total_size_bytes or 0))
    last_percent=-1
    with subprocess.Popen(command,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,encoding='utf-8',errors='replace') as process:
        for line in process.stdout:
            plain=re.sub(r'\x1b\[[0-9;?]*[ -/]*[@-~]','',line).strip()
            progress=re.search(r'Hashing pieces.*?(\d+(?:\.\d+)?)%',plain,re.I)
            if progress:
                percent=min(100,max(0,int(float(progress[1]))))
                if percent==last_percent:continue
                last_percent=percent
                job_event(stage='hashing',hash_progress=percent/100,hash_mode='hashing',hash_total=int(total_size_bytes or 0))
            if plain:print(plain,flush=True)
        if process.wait():raise RuntimeError('mkbrr failed to create or edit the torrent.')
    creator=config.get('Torrent','CREATOR',fallback='').strip()
    if creator:
        from utils.torrent_metadata import decode
        from utils.source_torrent import encode
        metainfo,_=decode(output.read_bytes());metainfo[b'created by']=creator.encode('utf-8')
        output.write_bytes(encode(metainfo))
    metadata=Torrent.read(output)
    if imported and metadata.metainfo['info']['pieces']!=imported.metainfo['info']['pieces']:
        raise RuntimeError('Edited torrent changed its piece hashes.')
    if metadata.trackers != [[config.get('Torrent','ANNOUNCEURL')]]:
        raise RuntimeError('Torrent tracker replacement failed; no upload was started.')
    if not metadata.private or metadata.source!=config.get('Torrent','ESOURCE') or not metadata.verify_filesize(directory):raise RuntimeError('mkbrr output does not match the private release payload.')
    job_event(stage='hashing',hash_progress=1.0,hash_mode='editing' if editing else 'hashing',hash_total=int(total_size_bytes or 0))
    return str(output),metadata.piece_size


def calculate_size(directory):
    """Calculate the directory size in bytes (follows symlinks)."""
    total_size = 0
    for root, _, files in os.walk(directory):
        for file in files:
            try:
                total_size += os.path.getsize(os.path.join(root, file))
            except OSError:
                pass
    return total_size

def calculate_piece_size(directory, total_size=None):
    """Calculate the appropriate piece size exponent based on directory size.

    Args:
        directory: Directory to measure (only used when total_size is not given).
        total_size (int, optional): Pre-computed size in bytes, avoids re-walking the directory.
    """
    if total_size is None:
        total_size = calculate_size(directory)

    mb = total_size // (1024 * 1024)

    if mb > 194560:
        pieces = 24
    elif mb > 73728:
        pieces = 23
    elif mb > 16384:
        pieces = 22
    elif mb > 8192:
        pieces = 21
    elif mb > 4096:
        pieces = 20
    elif mb > 2048:
        pieces = 19
    elif mb > 1024:
        pieces = 18
    elif mb > 512:
        pieces = 17
    elif mb > 256:
        pieces = 16
    else:
        pieces = 15

    return pieces


def download_torrent(url, auth_headers, release_name, is_dupe=False, dupe_id=None, allow_plain_fallback=None):
    """Download a torrent file and place it in the watch folder.

    Returns the final path of the .torrent in the watch folder, or None on failure.
    """
    try:
        log_file = 'dupe_download.log' if is_dupe else 'torrent_download.log'

        # Determine the temporary file path with appropriate naming
        temp_torrent_path = os.path.join(TMP_DIR, f'{dupe_id}_{release_name}.download.torrent') if is_dupe and dupe_id else os.path.join(TMP_DIR, f'{release_name}.download.torrent')

        # Download the torrent content
        response = fetch_torrent_with_retries(url, auth_headers, release_name, log_file)

        # Write the torrent content to the temporary file
        with open(temp_torrent_path, 'wb') as f:
            f.write(response.content)

        # Log the successful download
        log_download_event(
            log_file,
            (
                f"Torrent downloaded successfully: {temp_torrent_path}; "
                f"status={response.status_code}; content_type={response.headers.get('content-type')}; "
                f"bytes={len(response.content)}; url={url}"
            )
        )
        print(f"{bcolors.OKGREEN}Torrent downloaded successfully: {temp_torrent_path}\n{bcolors.ENDC}")
        return place_torrent_in_watchfolder(
            temp_torrent_path,
            release_name,
            log_file,
            remove_source=True,
            source_label='site torrent',
            allow_plain_fallback=allow_plain_fallback,
        )

    except requests.RequestException as e:
        # Log any request exceptions
        log_download_event(
            'dupe_download_error.log' if is_dupe else 'torrent_download_error.log',
            f"Failed to download torrent for {release_name} from {url}: {str(e)}"
        )
        print(f"{bcolors.FAIL}Failed to download torrent: {str(e)}{bcolors.ENDC}")
        return None

    except Exception as e:
        # General exception logging
        log_download_event(
            'dupe_general_error.log' if is_dupe else 'torrent_general_error.log',
            f"An error occurred while placing torrent for {release_name} in watch folder: {str(e)}"
        )
        print(f"{bcolors.FAIL}An error occurred: {str(e)}{bcolors.ENDC}")
        return None

def upload_torrent(
    torrent_file,
    template_file,
    auth_headers,
    category_id,
    mediainfo_text,
    dupedl_enabled,
    allow_plain_fallback=None,
    allow_local_upload_fallback=None,
    metadata=None,
):
    """
    Uploads a torrent file to the specified site with the required details.

    Args:
        torrent_file (str): Path to the .torrent file.
        template_file (str): Path to the NFO template file.
        auth_headers (dict): Authentication headers (X-API-KEY, User-Agent, Accept).
        category_id (int): The ID of the category of the torrent.
        mediainfo_text (str): The mediainfo text to be included in the upload.

    Returns:
        dict: Upload response details, including the final watch-folder torrent path.
    """
    # Log torrent file path to ensure it's correct
    #print(f"Torrent file path: {torrent_file}")
    
    from utils.unpacked_source import assert_unpacked_torrent
    assert_unpacked_torrent(Torrent.read(str(torrent_file)))
    upload_url = f"{config.get('Website', 'SITEURL')}/api/v1/torrents/upload"
    user_agent = config.get('Network', 'UserAgent', fallback='Mozilla/5.0')
    
    # Read the content of the template file
    with open(template_file, 'r', encoding='utf-8') as file:
        nfo_content = file.read()
    
    # Open the torrent file in binary mode
    with open(torrent_file, 'rb') as torrent_file_obj:
        files = {
            'file': (os.path.basename(torrent_file), torrent_file_obj, 'application/x-bittorrent')
        }
        
        data = {
            'category': category_id,
            'reqid': 0,
            'frileech': int(f"{config.get('UploadForm', 'FREELEECH')}"),
            'anonymousUpload': int(f"{config.get('UploadForm', 'ANONYMOUS')}"),
            'nfo': nfo_content
        }


        # Name-derived metadata (studio, performers, release_year,
        # runtime_minutes, language) - only fields that have a value
        if metadata:
            allowed = {'studio', 'performers', 'release_year', 'runtime_minutes',
                       'language', 'content_categories', 'content_tags',
                       'firstpic', 'backdrop_url', 'othergenre', 'imdbId', 'gameUrl'}
            for key, value in metadata.items():
                if key in allowed and value not in (None, ''):
                    data[key] = value
            print(f"{bcolors.OKBLUE}Posting metadata: "
                  + ", ".join(f"{k}={v}" for k, v in data.items() if k in allowed)
                  + f"{bcolors.ENDC}")

        if metadata:
            if metadata.get('content_tags'):
                data['othergenre'] = metadata['content_tags']
            # DigitalCore uses IMDb/game IDs for its own metadata. Keep optional
            # production/cast information in the description rather than losing it.
            extras = []
            for key, label in (('studio', 'Production'), ('performers', 'Cast')):
                if metadata.get(key):
                    extras.append(label + ': ' + str(metadata[key]))
            if extras:
                data['nfo'] += '\n\n' + '\n'.join(extras)

        if len(mediainfo_text) > 0:
            # Add in mediainfo if there is any
            data['mediainfo'] = mediainfo_text

        request_log_payload = {
            'url': upload_url,
            'headers': {key: ('***' if 'KEY' in key.upper() else value) for key, value in auth_headers.items()},
            'file_name': os.path.basename(torrent_file),
            'category': category_id,
            'mediainfo_length': len(mediainfo_text or ''),
        }
        log_download_event('upload_request.log', json.dumps(request_log_payload, indent=2))

        try:
            response = requests.post(
                upload_url,
                headers=auth_headers,
                files=files,
                data=data,
                verify=VERIFY_SSL,
                timeout=REQUEST_TIMEOUT,
            )
        except requests.RequestException as e:
            log_download_event('upload_response.log', f"Request exception: {e}")
            raise requests.RequestException(e)

        response_code = response.status_code
        # Log the upload response
        log_download_event(
            'upload_response.log',
            f"Upload response status: {response_code}\n{response.text[:4000]}"
        )

        if response_code == 200 or (response_code == 409 and dupedl_enabled):
            response_json = parse_upload_response(response)
            torrent_id = response_json.get('id')
            torrent_name = response_json.get('name')
            job_event(stage='downloading_torrent', site_status='accepted',
                      torrent_id=int(torrent_id), torrent_name=torrent_name)

            if response_code == 200:
                print(f"{bcolors.OKGREEN}Torrent uploaded successfully: {torrent_name}\n{bcolors.ENDC}")
            elif response_code == 409:
                print(f"{bcolors.WARNING}Torrent already exists. Downloading and seeding found duplicate torrent id: "
                      f"{torrent_id}.\n{bcolors.ENDC}")
            
            # Download the torrent file using the ID from the response
            torrent_url = f"{config.get('Website', 'SITEURL')}/api/v1/torrents/download/{torrent_id}"
            log_download_event(
                'upload_response.log',
                f"Site upload accepted: status={response_code}, id={torrent_id}, name={torrent_name}, download_url={torrent_url}"
            )
            watch_torrent_path = download_torrent(
                torrent_url,
                auth_headers,
                torrent_name,
                is_dupe=False,
                allow_plain_fallback=allow_plain_fallback,
            )
            if not watch_torrent_path:
                local_upload_fallback_enabled = (
                    config.getboolean('Settings', 'WATCHFOLDER_FALLBACK_UPLOADED_TORRENT', fallback=True)
                    if allow_local_upload_fallback is None
                    else bool(allow_local_upload_fallback)
                )
                if local_upload_fallback_enabled:
                    fallback_message = (
                        "Site torrent download failed after retries. Falling back to the locally generated "
                        f"uploaded torrent because the upload was accepted. This is usually safe when the "
                        f"configured ANNOUNCEURL already contains the user's passkey. id={torrent_id}, "
                        f"name={torrent_name}, download_url={torrent_url}"
                    )
                    log_download_event('torrent_download_error.log', fallback_message)
                    print(f"{bcolors.WARNING}{fallback_message}{bcolors.ENDC}")
                    watch_torrent_path = place_torrent_in_watchfolder(
                        torrent_file,
                        torrent_name,
                        'torrent_download.log',
                        remove_source=False,
                        source_label='locally generated uploaded torrent',
                        allow_plain_fallback=allow_plain_fallback,
                    )

                if watch_torrent_path:
                    return {
                        'status_code': response_code,
                        'torrent_id': torrent_id,
                        'torrent_name': torrent_name,
                        'watch_torrent': watch_torrent_path,
                        'watch_torrent_source': 'local_upload_fallback',
                    }

                raise TorrentWatchFolderError(
                    "Site upload accepted, but local seeding setup failed: the downloaded torrent was not placed "
                    "in the watch folder. Check files/torrent_download_error.log and files/fastresume_error.log.",
                    torrent_id=torrent_id,
                    torrent_name=torrent_name,
                    torrent_url=torrent_url,
                )

            return {
                'status_code': response_code,
                'torrent_id': torrent_id,
                'torrent_name': torrent_name,
                'watch_torrent': watch_torrent_path,
            }
        else:
            raise requests.RequestException(f"Status code: {response_code}\nResponse: {response.text}")
