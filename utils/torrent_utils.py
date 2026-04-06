import configparser
import os
import platform
import re
import shlex
import shutil
import subprocess
import time
from pathlib import Path

import cli_ui
import requests
from torf import Torrent, ReadError, BdecodeError, MetainfoError, VerifyIsDirectoryError, VerifyFileSizeError, \
    WriteError

from utils.bcolors import bcolors
from utils.config_loader import ConfigLoader
from utils.fastresume_utils import add_fastresume
from utils.logging_utils import log_to_file

# Load configuration
config = ConfigLoader().get_config()

TMP_DIR = os.path.join(config.get('Paths', 'TMP_DIR'), str(os.getpid()))
WATCHFOLDER = config.get('Paths', 'WATCHFOLDER')
PREPENDNAME = config.get('Settings', 'PREPENDNAME')

# Ensure TMP_DIR exists
os.makedirs(TMP_DIR, exist_ok=True)
torf_start_time = time.time()

def get_root_dir():
    """Use config.ini to get the root directory."""
    return os.path.dirname(os.path.abspath('config.ini'))

def torf_cb(torrent, filepath, pieces_done, pieces_total):
    global torf_start_time

    if pieces_done == 0:
        torf_start_time = time.time()  # Reset start time when hashing starts

    elapsed_time = time.time() - torf_start_time

    # Calculate percentage done
    if pieces_total > 0:
        percentage_done = (pieces_done / pieces_total) * 100
    else:
        percentage_done = 0

    # Estimate ETA (if at least one piece is done)
    if pieces_done > 0:
        estimated_total_time = elapsed_time / (pieces_done / pieces_total)
        eta_seconds = max(0, estimated_total_time - elapsed_time)
        eta = time.strftime("%M:%S", time.gmtime(eta_seconds))
    else:
        eta = "--:--"

    # Calculate hashing speed (MB/s)
    if elapsed_time > 0 and pieces_done > 0:
        piece_size = torrent.piece_size / (1024 * 1024)
        speed = (pieces_done * piece_size) / elapsed_time
        speed_str = f"{speed:.2f} MB/s"
    else:
        speed_str = "-- MB/s"

    # Display progress with percentage, speed, and ETA
    cli_ui.info_progress(f"Torf hashing... {speed_str} | ETA: {eta}", int(percentage_done), 100)

def create_torrent(directory, temp_dir, edit, hasher):
    """Create a torrent file from the given directory using torf-cli.
        Args:
            directory (Path): Path of the directory to generate a torrent for
            temp_dir (Path): Path to the temp directory to store output
            edit (bool): If true, edit the torrent file
            hasher (str): Which hasher to use
    """
    try:
        ecomment = config.get('Torrent', 'ECOMMENT').strip()
        esource = config.get('Torrent', 'ESOURCE').strip()
        creator = config.get('Torrent', 'CREATOR').strip()
        announceurl = config.get('Torrent', 'ANNOUNCEURL').strip()
        etorrentpath = config.get('Torrent', 'SOURCEFOLDER').strip().rstrip('/')
    except (configparser.NoOptionError, configparser.NoSectionError) as e:
        print(f"Config error: {e}")
        return None

    directory_path = Path(directory)
    temp_dir_path = Path(temp_dir)
    output_torrent = str(temp_dir_path) + f"/{directory_path.name}.torrent"

    # Proceed only if SOURCEFOLDER is set
    if etorrentpath:
        etorrent_file_path = Path(etorrentpath) / f"{directory_path.name}.torrent"
    else:
        print("SOURCEFOLDER is not configured; not reusing existing torrent. New torrent will be generated.")
        etorrent_file_path = None

    if edit and etorrent_file_path:
        if not etorrent_file_path.exists():
            print("No existing .torrent found. New torrent will be generated.")
            # Call itself, but set edit to false
            return create_torrent(directory, temp_dir, False, hasher)

        # Existing torrent *file* successfully found, try to use it
        try:
            reused_torrent = Torrent.read(f"{shlex.quote(str(etorrent_file_path))}")
        except MetainfoError as e:
            print(f"Invalid existing torrent. {e}\n"
                  f"New torrent will be generated.")
            # Call itself, but set edit to false
            return create_torrent(directory, temp_dir, False, hasher)
        except Exception as e:
            # Catch the rest, the only error that is not treated as fatal is MetainfoError
            log_to_file(temp_dir_path / 'create_torrent_error.log', str(e))
            print(f"Error reusing torrent: {e}")
            return None, None
        else:
            print(f"### Found existing torrent. {output_torrent}. Saving an edited copy")
            # Now edit the torrent as an object
            reused_torrent.trackers = [announceurl]
            reused_torrent.comment = ecomment
            reused_torrent.created_by = creator
            piece_size = reused_torrent.piece_size
            
            info_dict = reused_torrent.metainfo['info']
            valid_keys = ['name', 'piece length', 'pieces', 'private', 'source']

            # Add the correct key based on single vs multi file torrent
            if 'files' in info_dict:
                valid_keys.append('files')
            elif 'length' in info_dict:
                valid_keys.append('length')

            # Remove everything not in the whitelist
            for each in list(info_dict):
                if each not in valid_keys:
                    info_dict.pop(each, None)
            for each in list(reused_torrent.metainfo):
                if each not in ('announce', 'comment', 'creation date', 'created by', 'encoding', 'info'):
                    reused_torrent.metainfo.pop(each, None)

            reused_torrent.source = f"{shlex.quote(esource)}"
            reused_torrent.private = True
            try:
                reused_torrent = Torrent.copy(reused_torrent)
            except Exception as e:
                log_to_file(temp_dir_path / 'create_torrent_error.log', str(e))
                print(f"Error when trying to copy torrent: {e}")
                return None, None
            try:
                reused_torrent.write(output_torrent, overwrite=True)
            except WriteError as e:
                log_to_file(temp_dir_path / 'create_torrent_error.log', str(e))
                print(f"Error, could not write torrent to {output_torrent}: {e}")
                return None, None
            except MetainfoError as e:
                log_to_file(temp_dir_path / 'create_torrent_error.log', str(e))
                print(f"Error with newly copied torrent {directory_path.name}.torrent metainfo: {e}")
                return None, None
    else:
        piece_size = calculate_piece_size(directory)
        max_piece_size_bytes = piece_size * 1024 * 1024
        if hasher == 'torf':
            try:
                new_torrent = Torrent(path=str(directory_path),
                                      name=directory_path.name,
                                      trackers=[announceurl],
                                      source=esource,
                                      created_by=creator,
                                      comment=ecomment,
                                      randomize_infohash=True,
                                      private=True,
                                      piece_size_max=max_piece_size_bytes)

            except Exception as e:
                log_to_file(temp_dir_path / 'create_torrent_error.log', str(e))
                print(f"{bcolors.FAIL}Error when writing torrent metainfo to Torrent object: {e}")
                return None, None

            try:
                if new_torrent.generate(callback=torf_cb, interval=5):
                    new_torrent.write(output_torrent, overwrite=True)
                    log_to_file(temp_dir_path / 'create_torrent_output.log',
                                "New torrent successfully generated. Validating now")
                    print("New torrent successfully generated. Validating now")
                    try:
                        Torrent.read(output_torrent).validate()
                    except (ReadError, BdecodeError, MetainfoError) as e:
                        log_to_file(temp_dir_path / 'create_torrent_error.log', str(e))
                        print(f"Could not read output torrent: {e}")
                        return None, None
                    try:
                        validated = new_torrent.verify_filesize(directory_path)
                    except (ReadError, MetainfoError, VerifyIsDirectoryError, VerifyFileSizeError) as e:
                        log_to_file(temp_dir_path / 'create_torrent_error.log', str(e))
                        print(f"Could not verify output torrent: {e}")
                    else:
                        if validated:
                            log_to_file(temp_dir_path / 'create_torrent_output.log',
                                        "Torrent file successfully validated")
                            print("Torrent file successfully validated")
                        else:
                            log_to_file(temp_dir_path / 'create_torrent_error.log',
                                        "Failed to hash all pieces during torrent generation")
                            print("Failed to hash all pieces during torrent generation")
                            return None, None
                else:
                    # Failed for whatever reason that wasn't raised as an exception
                    log_to_file(temp_dir_path / 'create_torrent_error.log', "Failed to hash all pieces during torrent generation")
                    print("Failed to hash all pieces during torrent generation")
                    return None, None
            except Exception as e:
                log_to_file(temp_dir_path / 'create_torrent_error.log', str(e))
                print(f"{bcolors.FAIL}Error generating torrent: {e}{bcolors.ENDC}")
                return None, None

        elif hasher == 'mkbrr':
            try:
                mkbrr_path = get_mkbrr_bin()
            except Exception as e:
                log_to_file(temp_dir_path / 'create_torrent_error.log', str(e))
                print(f"{bcolors.FAIL}Error getting mkbrr binary: {e}{bcolors.ENDC}")
                return None, None

            # Ensure mkbrr is executable for both owner and group
            os.chmod(mkbrr_path, 0o775)

            if piece_size < 16:
                # Mkbrr only supports a minimum piece length of 16. If it's lower than 16, set to 16.
                max_piece_size_bytes = 16 * 1024 * 1024

            # Largest power of 2 that's less than or equal to max piece size in bytes
            import math
            power = min(27, max(16, math.floor(math.log2(max_piece_size_bytes))))
            print(
                f"{bcolors.YELLOW}Setting mkbrr piece length to {(2 ** power) / (1024 * 1024):.2f} MiB{bcolors.ENDC}")
            cmd = [mkbrr_path,
                   "create",
                   str(directory_path),
                   '-t', f'{announceurl}',
                   '-e',
                   '-l', str(power),
                   '-o', get_root_dir() + f'/{output_torrent}',
                   '-s', f'{esource}',
                   '-c', f'{ecomment}']

            try:
                process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
                print(f"mkbrr PID: {process.pid}")
            except OSError as e:
                log_to_file(temp_dir_path / 'create_torrent_error.log', str(e))
                print(f"{bcolors.FAIL}Error starting mkbrr process: {e}")
                return None, None

            total_pieces = 100  # Default to 100% for scaling progress
            pieces_done = 0
            mkbrr_start_time = time.time()
            torrent_written = False

            error = "Unknown error" # Initialize error to "Unknown error"
            try:
                for line in process.stdout:
                    line = line.strip()
                    if 'error' in line.lower():
                        error = line
                        break
                    # Detect hashing progress, speed, and percentage
                    match = re.search(r"Hashing pieces.*?\[(\d+(?:\.\d+)? [GM](?:B|iB)/s)]\s+(\d+)%", line)
                    if match:
                        speed = match.group(1)  # Extract speed (e.g., "12734.21 MB/s")
                        pieces_done = int(match.group(2))  # Extract percentage (e.g., "60")

                        # Try to extract the ETA directly if it's in the format [elapsed:remaining]
                        eta_match = re.search(r'\[(\d+)s:(\d+)s]', line)
                        if eta_match:
                            eta_seconds = int(eta_match.group(2))
                            eta = time.strftime("%M:%S", time.gmtime(eta_seconds))
                        else:
                            # Fallback to calculating ETA if not directly available
                            elapsed_time = time.time() - mkbrr_start_time
                            if pieces_done > 0:
                                estimated_total_time = elapsed_time / (pieces_done / 100)
                                eta_seconds = max(0, estimated_total_time - elapsed_time)
                                eta = time.strftime("%M:%S", time.gmtime(eta_seconds))
                            else:
                                eta = "--:--"  # Placeholder if we can't estimate yet

                        cli_ui.info_progress(f"mkbrr hashing... {speed} | ETA: {eta}", pieces_done, total_pieces)

                    # Detect final output line
                    if "Wrote" in line and ".torrent" in line:
                        # Print the final torrent file creation message
                        print(f"{bcolors.BCyan}{line}")
                        torrent_written = True

                # Wait for the process to finish
                result = process.wait()

                # Verify the torrent was actually created
                if result != 0:
                    raise RuntimeError(f"mkbrr exited with following error: {error}")

                if not torrent_written or not os.path.exists(output_torrent):
                    raise FileNotFoundError(f"Expected torrent file {output_torrent} was not created")
            except Exception as e:
                log_to_file(temp_dir_path / 'create_torrent_error.log', str(e))
                print(f"{bcolors.FAIL}Error creating torrent: {e}{bcolors.ENDC}")

                # Ensure process is termianted to prevent orphaned mkbrr processes
                process.terminate()
                return None, None

            # Validate the torrent file by trying to read it
            try:
                test_torrent = Torrent.read(output_torrent)
                if not test_torrent.metainfo.get('info', {}).get('pieces'):
                    raise ValueError("Generated torrent is missing pieces")
            except Exception as e:
                log_to_file(temp_dir_path / 'create_torrent_error.log', str(e))
                print(f"{bcolors.FAIL}Error creating torrent: {e}{bcolors.ENDC}")
                return None, None

    print(f"{bcolors.OKGREEN}Torrent to be uploaded has been created: {output_torrent}\n{bcolors.ENDC}")
    return output_torrent, piece_size  # Return the path to the torrent file as a string

def get_mkbrr_bin():
    """Get the path to the mkbrr binary based on the platform."""
    platform_type = platform.machine().lower()
    system_platform = platform.system().lower()
    mkbrr_path = 'bin/mkbrr/'
    if system_platform == 'linux':
        mkbrr_path += 'linux/'
        if 'amd64' in platform_type:
            mkbrr_path += 'amd64/mkbrr'
        elif 'x86_64' in platform_type:
            mkbrr_path += 'x86_64/mkbrr'
        elif 'aarch64' in platform_type or 'arm64' in platform_type or 'armv8' in platform_type:
            mkbrr_path += 'arm64/mkbrr'
        elif "armv6" in platform_type:
            mkbrr_path += 'armv6/mkbrr'
        elif "armv7" in platform_type:
            mkbrr_path += 'arm/mkbrr'
        else:
            raise FileNotFoundError(f"Unsupported Linux architecture: {platform_type}")
    return mkbrr_path

def calculate_size(directory):
    """Calculate the directory size."""
    total_size = sum(
        os.path.getsize(os.path.join(root, file))
        for root, _, files in os.walk(directory)
        for file in files
    )

    return total_size

def calculate_piece_size(directory):
    """Calculate the appropriate piece size for the torrent based on directory size."""
    total_size = sum(
        os.path.getsize(os.path.join(root, file))
        for root, _, files in os.walk(directory)
        for file in files
    )

    mb = total_size // (1024 * 1024)

    if mb > 484352:
        pieces = 24
    elif mb > 194560:
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


def download_torrent(url, auth_headers, release_name, is_dupe=False, dupe_id=None):
    """Download a torrent file, distinguishing between duplicate and regular torrents."""
    try:
        # Determine the temporary file path with appropriate naming
        temp_torrent_path = os.path.join(TMP_DIR, f'{dupe_id}_{release_name}.torrent') if is_dupe and dupe_id else os.path.join(TMP_DIR, f'{release_name}.torrent')

        # Download the torrent content
        response = requests.get(url, headers=auth_headers)
        response.raise_for_status()  # Raise an error for bad responses
        log_to_file(os.path.join(TMP_DIR, 'response_debug.log'), f"Response status: {response.status_code}\nResponse content: {response.text}")

        # Write the torrent content to the temporary file
        with open(temp_torrent_path, 'wb') as f:
            f.write(response.content)

        # Choose the appropriate log file
        log_file = 'dupe_download.log' if is_dupe else 'torrent_download.log'
        
        # Log the successful download
        log_to_file(os.path.join(TMP_DIR, log_file), f"Torrent downloaded successfully: {temp_torrent_path}")
        print(f"{bcolors.OKGREEN}Torrent downloaded successfully: {temp_torrent_path}\n{bcolors.ENDC}")
        
        # Check if fast resume should be added
        add_fastresume_flag = config.getboolean('Settings', 'ADDFASTRESUME')
        if add_fastresume_flag:
            # Get the data directory from configuration
            download_dir = config.get('Paths', 'DATADIR')  # Ensure this path is correct
            
            # Prepare the fast resume output file path
            fastresume_output_path = os.path.join(TMP_DIR, f'dc.{release_name}.torrent')
            
            # Add fast resume data
            add_fastresume(temp_torrent_path, download_dir, fastresume_output_path)
            
            # Check if the fast resume file exists and move it
            if os.path.exists(fastresume_output_path):
                final_torrent_path = os.path.join(WATCHFOLDER, f'{PREPENDNAME}{release_name}.torrent')
                
                # Use copyfile and remove instead of rename
                shutil.copyfile(fastresume_output_path, final_torrent_path)
                os.remove(fastresume_output_path)
                
                print(f"{bcolors.OKGREEN}Torrent copied to watch folder: {final_torrent_path}\n{bcolors.ENDC}")
            else:
                print(f"{bcolors.FAIL}Fast resume output file not found: {fastresume_output_path}{bcolors.ENDC}")
        else:
            final_torrent_path = os.path.join(WATCHFOLDER, f'{PREPENDNAME}{release_name}.torrent')
            
            # Use copyfile and remove instead of rename
            shutil.copyfile(temp_torrent_path, final_torrent_path)
            os.remove(temp_torrent_path)
            
            print(f"{bcolors.OKGREEN}Torrent copied to watch folder: {final_torrent_path}\n{bcolors.ENDC}")

    except requests.RequestException as e:
        # Log any request exceptions
        log_to_file(os.path.join(TMP_DIR, 'dupe_download_error.log' if is_dupe else 'torrent_download_error.log'), f"Failed to download torrent: {str(e)}")
        print(f"{bcolors.FAIL}Failed to download torrent: {str(e)}{bcolors.ENDC}")

    except Exception as e:
        # General exception logging
        log_to_file(os.path.join(TMP_DIR, 'dupe_general_error.log' if is_dupe else 'torrent_general_error.log'), f"An error occurred: {str(e)}")
        print(f"{bcolors.FAIL}An error occurred: {str(e)}{bcolors.ENDC}")

def upload_torrent(torrent_file, template_file, auth_headers, category_id, imdb_id, mediainfo_text, dupedl_enabled):
    """
    Uploads a torrent file to the specified site with the required details.

    Args:
        torrent_file (str): Path to the .torrent file.
        template_file (str): Path to the NFO template file.
        auth_headers (dict): Authentication headers (X-API-KEY, User-Agent, Accept).
        category_id (int): The ID of the category of the torrent.
        imdb_id (str): The IMDB ID associated with the torrent.
        mediainfo_text (str): The mediainfo text to be included in the upload.

    Returns:
        None
    """
    # Log torrent file path to ensure it's correct
    #print(f"Torrent file path: {torrent_file}")
    
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
            'imdbId': imdb_id,
            'reqid': 0,
            'section': 'new',
            'frileech': int(f"{config.get('UploadForm', 'FREELEECH')}"),
            'anonymousUpload': int(f"{config.get('UploadForm', 'ANONYMOUS')}"),
            'p2p': 0,
            'nfo': nfo_content
        }

        if len(mediainfo_text) > 0:
            # Add in mediainfo if there is any
            data['mediainfo'] = mediainfo_text
        
        # Log request details for debugging
        log_to_file(os.path.join(TMP_DIR, 'upload_request.log'), f"Uploading to URL: {upload_url}")
        log_to_file(os.path.join(TMP_DIR, 'upload_request.log'), f"Headers: {auth_headers}")
        log_to_file(os.path.join(TMP_DIR, 'upload_request.log'), f"Files: {files}")
        log_to_file(os.path.join(TMP_DIR, 'upload_request.log'), f"Data: {data}")

        try:
            response = requests.post(
                upload_url,
                headers=auth_headers,
                files=files,
                data=data,
                verify=False
            )
        except requests.RequestException as e:
            log_to_file(os.path.join(TMP_DIR, 'upload_response.log'), f"Request exception: {e}")
            raise requests.RequestException(e)

        response_code = response.status_code
        # Log the upload response
        log_to_file(os.path.join(TMP_DIR, 'upload_response.log'), f"Upload response status: {response_code}\n{response.text}")

        if response_code == 200 or (response_code == 409 and dupedl_enabled):
            response_json = response.json()
            torrent_id = response_json.get('id')
            torrent_name = response_json.get('name')

            if response_code == 200:
                print(f"{bcolors.OKGREEN}Torrent uploaded successfully: {torrent_name}\n{bcolors.ENDC}")
            elif response_code == 409:
                print(f"{bcolors.WARNING}Torrent already exists. Downloading and seeding found duplicate torrent id: "
                      f"{torrent_id}.\n{bcolors.ENDC}")
            
            # Download the torrent file using the ID from the response
            torrent_url = f"{config.get('Website', 'SITEURL')}/api/v1/torrents/download/{torrent_id}"
            download_torrent(torrent_url, auth_headers, torrent_name, is_dupe=False)
            return response_code
        else:
            raise requests.RequestException(f"Status code: {response_code}\nResponse: {response.text}")
