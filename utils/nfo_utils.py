import os
import subprocess
from pathlib import Path
from tempfile import NamedTemporaryFile

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

def read_nfo_content(nfo_file: Path, tmp_dir: Path, from_encoding: str = 'cp437', to_encoding: str = 'utf-8') -> str:
    """
    Read the content of the .nfo file, handling potential encoding issues and converting to UTF-8.

    Args:
        nfo_file (Path): The path to the .nfo file.
        tmp_dir (Path): The temporary directory to use for intermediate files.
        from_encoding (str): The source encoding (default is 'cp437').
        to_encoding (str): The target encoding (default is 'utf-8').

    Returns:
        str: The content of the .nfo file.
    """
    try:
        # Ensure tmp_dir exists
        tmp_dir.mkdir(parents=True, exist_ok=True)
        
        # Create a temporary file in the specified tmp_dir
        with NamedTemporaryFile(delete=False, mode='w+', encoding=to_encoding, dir=tmp_dir) as temp_file:
            temp_file_path = temp_file.name
            
            # Convert the file encoding using iconv
            result = subprocess.run(
                ['iconv', f'--from-code={from_encoding}', f'--to-code={to_encoding}', str(nfo_file)],
                stdout=temp_file,
                stderr=subprocess.PIPE,
                check=True
            )
        
        # Read the content from the temporary file
        with open(temp_file_path, 'r', encoding=to_encoding) as file:
            content = file.read()
        
        return content
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"Error converting file encoding: {e}")
    finally:
        # Clean up the temporary file
        if Path(temp_file_path).exists():
            Path(temp_file_path).unlink()

def process_nfo(directory, replacements, log_file_path):
    """
    Find a .nfo file in the specified directory, read its content, and update the replacements dictionary.

    Args:
        directory (Path): The directory to search for .nfo files.
        replacements (dict): The dictionary to update with the .nfo content.
        log_file_path (Path): The path to the log file for logging errors.
    """
    try:
        nfo_file = find_nfo_file(directory)
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
