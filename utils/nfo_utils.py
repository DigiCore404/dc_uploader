import os
from pathlib import Path

from utils.bcolors import bcolors
from utils.config_loader import ConfigLoader
from utils.logging_utils import log_to_file

# Load configuration
config = ConfigLoader().get_config()
TMP_DIR = Path(config.get('Paths', 'TMP_DIR')) / str(os.getpid())
TEMPLATE_PATH = Path(config.get('Paths', 'TEMPLATE_PATH'))

# Ensure TMP_DIR exists
TMP_DIR.mkdir(parents=True, exist_ok=True)

def find_nfo_file(directory):
    """
    Find the first .nfo file in the specified directory.

    Args:
        directory (Path): The directory to search for .nfo files.

    Returns:
        Path: The path to the found .nfo file, or None if no .nfo file is found.
    """
    for file in directory.iterdir():
        if file.suffix.lower() == '.nfo':
            return file
    return None

def read_nfo_content(nfo_file: Path, tmp_dir: Path = None, from_encoding: str = 'cp437', to_encoding: str = 'utf-8') -> str:
    """
    Read the content of the .nfo file, converting classic scene cp437 encoding to UTF-8.

    Done in pure Python (no iconv subprocess or temp files needed). Falls back to
    UTF-8 with replacement characters if the primary decode fails.

    Args:
        nfo_file (Path): The path to the .nfo file.
        tmp_dir (Path): Unused, kept for backwards compatibility.
        from_encoding (str): The source encoding (default is 'cp437').
        to_encoding (str): Unused, output is always a Python string.

    Returns:
        str: The content of the .nfo file.
    """
    raw = Path(nfo_file).read_bytes()

    # Many modern NFOs are actually UTF-8; prefer that when it decodes cleanly
    try:
        return raw.decode('utf-8')
    except UnicodeDecodeError:
        pass

    try:
        return raw.decode(from_encoding)
    except (UnicodeDecodeError, LookupError):
        return raw.decode('utf-8', errors='replace')

def process_nfo(directory, replacements, log_file_path, nfo_file=None):
    """
    Find a .nfo file in the specified directory, read its content, and update the replacements dictionary.

    Args:
        directory (Path): The directory to search for .nfo files.
        replacements (dict): The dictionary to update with the .nfo content.
        log_file_path (Path): The path to the log file for logging errors.
    """
    try:
        nfo_file = Path(nfo_file) if nfo_file else find_nfo_file(directory)
        if nfo_file:
            # Read the NFO content while preserving formatting
            nfo_content = read_nfo_content(nfo_file, TMP_DIR)
            
            # Update replacements with formatted NFO content
            replacements['!nfo!'] = f"[nfo]\n{nfo_content}\n[/nfo]"
            
            print(f"{bcolors.YELLOW}Found NFO data...\n{bcolors.ENDC}")
            log_to_file(log_file_path, f"NFO file processed successfully: {nfo_file.name}")
        else:
            replacements['!nfo!'] = ''
            log_to_file(log_file_path, "No .nfo file found in the directory.")
            print(f"{bcolors.RED}Did not find NFO data...\n{bcolors.ENDC}")
    except Exception as e:
        log_to_file(log_file_path, f"Error processing .nfo file: {str(e)}")
        replacements['!nfo!'] = ''
