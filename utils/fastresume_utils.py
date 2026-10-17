import os
from pathlib import Path


from utils.console import print_section
from utils.bcolors import bcolors
from utils.config_loader import ConfigLoader
from utils.logging_utils import log_to_file

config = ConfigLoader().get_config()
ROOT_DIR = Path(__file__).resolve().parents[1]


def resolve_config_path(path_value):
    path = Path(path_value)
    return path if path.is_absolute() else ROOT_DIR / path


def persistent_log_path(file_name):
    upload_log = Path(config.get('Paths', 'UPLOADLOG', fallback='files/upload.log'))
    log_dir = upload_log.parent if upload_log.parent != Path('') else Path('files')
    return resolve_config_path(log_dir) / file_name


def log_fastresume_event(output_file, message, file_name='fastresume.log'):
    log_path = os.path.join(os.path.dirname(output_file) or '.', file_name)
    log_to_file(log_path, message)
    log_to_file(persistent_log_path(file_name), message)


def log_fastresume_error(output_file, message):
    log_fastresume_event(output_file, message)
    log_fastresume_event(output_file, message, file_name='fastresume_error.log')


def add_fastresume(torrent_file, download_dir, output_file):
    """Add fast resume to the torrent using the rfr Python module."""
    #print(f"{bcolors.OKBLUE}Adding fast resume information...{bcolors.ENDC}")
    try:
        import rfr
    except ImportError:
        print('Optional fastresume module is unavailable; use the configured plain torrent fallback.')
        return False
    #print(f"Torrent file: {torrent_file}")
    #print(f"Download directory: {download_dir}")
    #print(f"Output file: {output_file}")
    print_section("Fastresume")
    log_fastresume_event(
        output_file,
        f"Fastresume start: torrent_file={torrent_file}, download_dir={download_dir}, output_file={output_file}"
    )

    try:
        # Check if the torrent file exists
        if not os.path.isfile(torrent_file):
            raise FileNotFoundError(f"Torrent file not found: {torrent_file}")

        # Initialize FastTorrent object
        tor = rfr.FastTorrent(torrent_file, download_dir)
        
        # Generate fast resume data
        tor.do_resume()
        
        # Save the resulting torrent to a file
        tor.save_to_file(output_file)
        
        if os.path.exists(output_file):
            log_fastresume_event(output_file, f"Fastresume added successfully: {output_file}")
            print(f"{bcolors.OKGREEN}Fast resume added successfully: {output_file}\n{bcolors.ENDC}")
            return True
        else:
            raise FileNotFoundError(f"Fast resume output file not found: {output_file}")
    except FileNotFoundError as e:
        message = f"File not found: {str(e)}"
        log_fastresume_error(output_file, message)
        print(f"{bcolors.FAIL}{message}{bcolors.ENDC}")
        return False
    except Exception as e:
        message = f"Failed to add fast resume: {str(e)}"
        log_fastresume_error(output_file, message)
        print(f"{bcolors.FAIL}{message}{bcolors.ENDC}")
        return False
