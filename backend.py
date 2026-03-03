import io
import os
import platform
import re
import requests
import shutil
import sqlite3
import sys
import time
import argparse  # Added for CLI argument handling
from pathlib import Path
from requests import HTTPError

from utils.art_utils import ascii_art_header
from utils.bcolors import bcolors
from utils.category_utils import determine_category
from utils.config_loader import ConfigLoader
from utils.database_utils import insert_upload, update_upload_status
from utils.dupe_utils import check_and_download_dupe
from utils.gameinfo_utils import fetch_game_info, extract_game_name
from utils.image_utils import upload_images
from utils.imdb_utils import extract_imdb_link_from_nfo, get_imdb_info
from utils.logging_utils import log_to_file, log_upload_details
from utils.login_utils import login
from utils.mediainfo_utils import generate_mediainfo
from utils.nfo_utils import process_nfo
from utils.screenshot_utils import generate_screenshots
from utils.status_utils import update_status
from utils.template_utils import prepare_template
from utils.torrent_utils import create_torrent, upload_torrent


class CustomOutput(io.TextIOBase):
    def __init__(self, original_stdout, db_path="data/terminal_output.db"):
        self.original_stdout = original_stdout
        self.db_path = db_path
        self.ensure_db_initialized()

    def ensure_db_initialized(self):
        """Initialize the SQLite database and the logs table if they don't exist."""
        # Ensure the database directory exists
        db_dir = os.path.dirname(self.db_path)
        if not os.path.exists(db_dir):
            os.makedirs(db_dir)

        # Connect to the database and ensure the table exists
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        c.execute('''
            CREATE TABLE IF NOT EXISTS terminal_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT DEFAULT CURRENT_TIMESTAMP,
                source TEXT,  -- Optional, can be used to differentiate uploaders
                log_line TEXT
            )
        ''')
        conn.commit()
        conn.close()

    def write(self, message):
        """Write to terminal and store message in the database."""
        # Write to terminal as normal
        self.original_stdout.write(message)

        # Log the message to the database
        self.log_to_db(message)

    def log_to_db(self, message):
        """Insert the log message into the SQLite database."""
        # Strip all ANSI color codes from the message
        message = re.sub(r'\033\[[0-9;]*m', '', message)
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()

        # Insert the message into the logs table with the current timestamp
        c.execute('''
            INSERT INTO terminal_logs (log_line)
            VALUES (?)
        ''', (message,))

        conn.commit()
        conn.close()

    def flush(self):
        """Flush the original stdout."""
        self.original_stdout.flush()

    def isatty(self):
        """Return whether the original stdout is a TTY."""
        return self.original_stdout.isatty()

    def fileno(self):
        return self.original_stdout.fileno()

# Replace sys.stdout with CustomOutput
sys.stdout = CustomOutput(sys.stdout)

def log(message, file_path):
    """Utility function to log messages to a file and print to the console."""
    #print(message)
    log_to_file(file_path, message)

def calculate_directory_size(directory):
    """Calculate the total size of files in a directory, including handling soft links."""
    total_size = 0
    for dirpath, dirnames, filenames in os.walk(directory):
        for f in filenames:
            fp = os.path.join(dirpath, f)
            if os.path.islink(fp):
                # If it's a symlink, follow the link and add its real size
                real_path = os.path.realpath(fp)
                total_size += os.path.getsize(real_path)
            else:
                # If it's a real file, just add its size
                total_size += os.path.getsize(fp)
    return round(total_size / (1024 * 1024), 2)  # Size in MB

def find_nfo_file(directory):
    """Find the .nfo file in the directory."""
    for root, dirs, files in os.walk(directory):
        for file in files:
            if file.endswith('.nfo'):
                return os.path.join(root, file)
    return None

def cleanup_tmp_dir(directory, cleanup_enabled):
    """Clean up temporary directory if cleanup is enabled."""
    if cleanup_enabled:
        try:
            if directory.exists() and directory.is_dir():
                shutil.rmtree(directory)
                print(f"Cleaned up temporary directory: {directory}{bcolors.ENDC}")
        except Exception as e:
            print(f"Error during cleanup: {str(e)}{bcolors.ENDC}")

def fail_exit(directory, cleanup_enabled):
    cleanup_tmp_dir(directory, cleanup_enabled)
    exit(1)

def version_check(program_version):
    # Get latest version number from GitHub
    response = requests.get("https://api.github.com/repos/DigiCore404/dc_uploader/releases/latest")
    try:
        response.raise_for_status()
    except HTTPError as e:
        print(f"{bcolors.WARNING}Received following HTTP code when trying to check version:\n"
              f"{e}\n"
              f"Unable to check for latest version, continuing without version check{bcolors.ENDC}")
    else:
        try:
            new_version = response.json()["name"]
            # Strip 'v' prefix if present
            if new_version.startswith('v'):
                new_version = new_version[1:]
            if program_version.startswith('v'):
                program_version = program_version[1:]

            # Parse versions into components
            new_parts = [int(x) for x in new_version.split('.')]
            current_parts = [int(x) for x in program_version.split('.')]

            # Pad shorter version with zeros for comparison
            while len(new_parts) < len(current_parts):
                new_parts.append(0)
            while len(current_parts) < len(new_parts):
                current_parts.append(0)

            # Compare versions
            if new_parts > current_parts:
                print(
                    f"{bcolors.WARNING}A newer version available: v{new_version} (you are on v{program_version})"
                    f"{bcolors.ENDC}")
            elif new_parts < current_parts:
                print(
                    f"{bcolors.WARNING}You're using a development version: v{program_version} "
                    f"(latest stable is v{new_version}){bcolors.ENDC}")
            else:
                print(f"{bcolors.OKGREEN}You're using the latest version: v{program_version}{bcolors.ENDC}")
        except KeyError:
            print(f"{bcolors.WARNING}GitHub API response did not contain a version number. "
                  f"Continuing without version check{bcolors.ENDC}")
        except Exception as e:
            print(f"{bcolors.WARNING}Error comparing versions: {str(e)}. "
                  f"Continuing without version check{bcolors.ENDC}")

def main():
    # --- COMMAND LINE ARGUMENT SETUP ---
    parser = argparse.ArgumentParser(description="dc_uploader Backend")
    parser.add_argument('directory', help="The name of the release directory to upload")
    parser.add_argument('-cat', '--cat', help="Manual Category ID override", type=int, default=None)
    args = parser.parse_args()
    # ------------------------------------

    """Main function to run the script."""
    system_platform = platform.system()
    if system_platform.lower() != 'linux':
        print(f"This tool is designed only for Linux. You are on {system_platform}")
        exit(1)

    # Load configuration
    config = ConfigLoader().get_config()
    # We know utils will always be in config.ini. If a user moves it, it's on them for any bugs

    # Join tmp and running process's PID to generate a unique directory
    tmp_dir = Path(config.get('Paths', 'TMP_DIR')) / str(os.getpid())
    cleanup_enabled = config.getboolean('Settings', 'CLEANUP')

    program_version = "1.2.1"

    try:
        hasher = config.get('Torrent', 'HASHER').strip()
        template_path = Path(config.get('Paths', 'TEMPLATE_PATH'))
        upload_log_path = Path(config.get('Paths', 'UPLOADLOG'))
        # Ensure tmp_dir exists
        tmp_dir.mkdir(parents=True, exist_ok=True)

        log_file_path = tmp_dir / 'main.log'

        # Ensure upload.log is created
        try:
            upload_log_path.touch(exist_ok=True)  # Create the file if it does not exist
            #print(f"Upload log created at: {upload_log_path}")
        except Exception as e:
            print(f"Error creating upload log: {str(e)}")
            fail_exit(tmp_dir, cleanup_enabled)

        # Set variables from CLI arguments
        directory_name = args.directory
        manual_cat_id = args.cat

        ascii_art_header("Header", program_version)
        version_check(program_version)
        time.sleep(3) # Sleep 3 seconds so users can see our pretty header :)

        print(f"\n{bcolors.OKBLUE}Starting upload script...{bcolors.ENDC}")

        base_dir = config.get('Paths', 'DATADIR')

        if not base_dir:
            log("DATADIR not found in config.", log_file_path)
            print(f"{bcolors.ENDC}{bcolors.FAIL}DATADIR not found in config.\n{bcolors.ENDC}")
            fail_exit(tmp_dir, cleanup_enabled)
        else:
            # It exists, path it
            base_dir = Path(base_dir)

        directory = base_dir / directory_name

        if not directory.exists():
            log(f"The provided directory does not exist: {directory}", log_file_path)
            print(f"{bcolors.ENDC}{bcolors.FAIL}Directory does not exist: {directory}\n{bcolors.ENDC}")
            fail_exit(tmp_dir, cleanup_enabled)

        if hasher != 'torf' and hasher != 'mkbrr':
            log(f"Unknown hasher: {hasher}", log_file_path)
            print(f"{bcolors.ENDC}{bcolors.FAIL}Unknown hasher: {hasher}\n{bcolors.ENDC}")
            fail_exit(tmp_dir, cleanup_enabled)

        update_status(directory, 'uploading')

        # Initialize upload details dictionary
        upload_details = {"name": directory_name, "path": str(directory), "category": None,
                          "piece_size": None, "piece_size_bytes": None, "etor_started": None, "torrent_file": None,
                          "etor_completed": None, 'size': f"{calculate_directory_size(directory)} MB",
                          'nfo': find_nfo_file(directory) or "NFO file not found"}

        # Check if settings are enabled
        screenshots_enabled = config.getboolean('Settings', 'SCREENSHOTS')
        rar2fs_screenshots_enabled = config.getboolean('Settings', 'RAR2FS_SCREENSHOTS')
        mediainfo_enabled = config.getboolean('Settings', 'MEDIAINFO')
        dupecheck_enabled = config.getboolean('Settings', 'DUPECHECK')
        dupedl_enabled = config.getboolean('Settings', 'DUPEDL')
        fast_resume = config.getboolean('Settings', 'ADDFASTRESUME')
        imdb_enabled = config.getboolean('Settings', 'IMDB')
        image_upload_enabled = config.getboolean('Settings', 'IMAGE_UPLOAD')
        gameinfo_enabled = config.getboolean('Settings', 'GAME_INFO')
        filters_path = config.get('Paths', 'FILTERS')
        upload_log = config.get('Paths', 'UPLOADLOG')

        screenshot_categories = [int(cat.strip()) for cat in config.get('Settings', 'SCREENSHOT_CATEGORIES').split(',')]
        rar2fs_categories = [int(cat.strip()) for cat in config.get('Settings', 'RAR2FS_CATEGORIES').split(',')]
        mediainfo_categories = [int(cat.strip()) for cat in config.get('Settings', 'MEDIAINFO_CATEGORIES').split(',')]
        imdb_movie_categories = [int(cat.strip()) for cat in config.get('Settings', 'IMDB_MOVIE_CATEGORIES').split(',')]
        imdb_tv_categories = [int(cat.strip()) for cat in config.get('Settings', 'IMDB_TV_CATEGORIES').split(',')]
        game_categories = [int(cat.strip()) for cat in config.get('Settings', 'GAME_CATEGORIES').split(',')]

        # Print settings status
        print()
        print(f"{bcolors.RED}################ Settings ################\n{bcolors.ENDC}")
        print(f"{bcolors.GREEN}Screenshots enabled: {screenshots_enabled}{bcolors.ENDC}")
        print(f"{bcolors.GREEN}RAR2FS enabled: {rar2fs_screenshots_enabled}{bcolors.ENDC}")
        print(f"{bcolors.GREEN}Mediainfo enabled: {mediainfo_enabled}{bcolors.ENDC}")
        print(f"{bcolors.GREEN}Dupe Check enabled: {dupecheck_enabled}{bcolors.ENDC}")
        print(f"{bcolors.GREEN}Dupe Download enabled: {dupedl_enabled}{bcolors.ENDC}")
        print(f"{bcolors.GREEN}Fastresume enabled: {fast_resume}{bcolors.ENDC}")
        print(f"{bcolors.GREEN}IMDB enabled: {imdb_enabled}{bcolors.ENDC}")
        print(f"{bcolors.GREEN}Gameinfo enabled: {gameinfo_enabled}{bcolors.ENDC}")
        print(f"{bcolors.GREEN}Image Upload enabled: {image_upload_enabled}{bcolors.ENDC}")
        print(f"{bcolors.GREEN}Cleanup enabled: {cleanup_enabled}{bcolors.ENDC}")
        print(f"{bcolors.GREEN}Loading filters: {filters_path}{bcolors.ENDC}")
        print(f"{bcolors.GREEN}Loading uploadlog: {upload_log}\n{bcolors.ENDC}")
        print(f"{bcolors.RED}################ Settings ################{bcolors.ENDC}")
        print()

        ascii_art_header("Login")
        print(f"{bcolors.ENDC}{bcolors.YELLOW}Logging in via API Key...\n{bcolors.ENDC}")
        # Login now returns headers (including API Key) instead of cookies
        try:
            auth_headers = login()  # Updated login returns auth headers
            if not auth_headers:
                log_to_file(log_file_path, "Login failed. Cannot proceed with the script.")
                print(f"{bcolors.RED}Login failed. Cannot proceed with the script.\n{bcolors.ENDC}")
                fail_exit(tmp_dir, cleanup_enabled)
            else:
                print(f"{bcolors.OKGREEN}Login successful. Proceeding...\n{bcolors.ENDC}")
        except Exception as e:
            log_to_file(log_file_path, f"Error during login: {str(e)}")
            print(f"{bcolors.FAIL}Error during login: {str(e)}\n{bcolors.ENDC}")
            fail_exit(tmp_dir, cleanup_enabled)

        insert_upload(name=directory_name)

        # Create process-specific directory in tmp_dir
        temp_dir = tmp_dir

        # Check for duplicates
        try:
            # Only run dupe check if both DUPECHECK and DUPEDL are enabled
            if dupecheck_enabled and dupedl_enabled:
                ascii_art_header("Dupe checking")
                # Passing auth_headers instead of cookies
                duplicate_found = check_and_download_dupe(directory_name, auth_headers)
                if duplicate_found:
                    log("Duplicate found. Skipping further operations.", log_file_path)
                    update_status(directory, 'dupe')
                    update_upload_status(name=directory_name, new_status='dupe')
                    log_upload_details(upload_details, upload_log_path, duplicate_found=True)
                    cleanup_tmp_dir(tmp_dir, cleanup_enabled)  # Clean up tmp_dir
                    exit(0)
            else:
                log("Dupe check or download is disabled in the config.", log_file_path)

        except Exception as e:
            log(f"Error checking for duplicates: {str(e)}", log_file_path)
            cleanup_tmp_dir(tmp_dir, cleanup_enabled)  # Clean up tmp_dir
            exit(1)

        ascii_art_header("Category")

        # --- Updated call to pass manual_category_id ---
        category_name, category_id_str = determine_category(directory_name, manual_id=manual_cat_id)
        category_id = int(category_id_str)  # Convert category_id to integer
        # -----------------------------------------------

        upload_details['category'] = f"{category_name} ({category_id})"
        update_upload_status(name=directory_name, new_status='uploading', size=f'{upload_details["size"]}', category=f'{category_name}')

        # Initialize replacements dictionary with version info.
        replacements = {'!version!': program_version}
        
        ### Screenshots processing section
        if screenshots_enabled:
            ascii_art_header("Screenshots")
            if category_id in screenshot_categories:
                try:
                    generate_screenshots(directory, category_id)
                except Exception as e:
                    log(f"Error generating screenshots: {str(e)}", log_file_path)
                    fail_exit(tmp_dir, cleanup_enabled)
            else:
                log(f"Category ID {category_id} is not in the screenshot categories: {screenshot_categories}", log_file_path)
        else:
            log("Screenshots are disabled.", log_file_path)

        # Mediainfo processing
        mediainfo_content = ''
        if mediainfo_enabled:
            ascii_art_header("Mediainfo")
            if category_id in mediainfo_categories:
                try:
                    mediainfo_file_path = generate_mediainfo(directory, temp_dir)
                except Exception as e:
                    log(f"Error generating mediainfo: {str(e)}", log_file_path)
                else:
                    if mediainfo_file_path.exists():
                        with open(mediainfo_file_path, 'r') as file:
                            mediainfo_content = file.read()

        # IMDb processing
        imdb_link = ''
        if imdb_enabled:
            ascii_art_header("IMDB")

            print(f"{bcolors.YELLOW}Searching for IMDB data\n{bcolors.ENDC}")

            if category_id in imdb_movie_categories or category_id in imdb_tv_categories:
                # Attempt to extract IMDb link from .nfo file
                imdb_link = extract_imdb_link_from_nfo(directory)

                if imdb_link:
                    print(f"{bcolors.OKGREEN}IMDb link found in NFO: {imdb_link}\n{bcolors.ENDC}")

                    update_upload_status(name=directory_name, imdb_url=imdb_link)  # Update IMDb URL in DB
                elif category_id in imdb_movie_categories or category_id in imdb_tv_categories:
                    if category_id in imdb_movie_categories:
                        media_type = 'movie'
                    else:
                        media_type = 'tv'
                    print(f"{bcolors.YELLOW}No IMDb link found in NFO or no NFO file present. "
                          f"Attempting to extract details from directory name.\n{bcolors.ENDC}")
                    # If found, will contain a dict with 'id', 'title', and 'year'
                    imdb_info = get_imdb_info(directory_name, media_type)
                    if imdb_info:
                        imdb_link = f"https://www.imdb.com/title/{imdb_info['id']}/"

                        print(f"{bcolors.OKGREEN}IMDb link found: {imdb_link}\n{bcolors.ENDC}")
                        update_upload_status(name=directory_name, imdb_url=imdb_link)  # Update IMDb URL in DB
            else:
                print(f"{bcolors.YELLOW}Category ID {category_id} is not in the IMDb categories: "
                      f"{imdb_movie_categories} or {imdb_tv_categories}{bcolors.ENDC}")

        imdb_id = re.search(r'tt\d+', imdb_link).group() if imdb_link else ''

        # Game information processing
        if gameinfo_enabled and category_id in game_categories:
            ascii_art_header("Gameinfo")

            print(f"{bcolors.YELLOW}Fetching game information...\n{bcolors.ENDC}")

            try:
                # Use the correct function for extracting the game name from the release name or directory
                game_name = extract_game_name(directory_name)  # Ensure this function exists and works

                if game_name:
                    print(f"{bcolors.YELLOW}Extracted Game Name: {game_name}{bcolors.ENDC}")

                    # Fetch game information from IGDB
                    game_info = fetch_game_info(game_name, directory_name)

                    if game_info:
                        # Extract relevant game info
                        game_summary = game_info['summary']
                        game_genres = ', '.join(game_info['genres'])
                        game_release_date = game_info['release_date']

                        # Prepare the game info content for the template with BBCode formatting
                        gameinfo_content = (
                            f"[b]Game:[/b] [color=purple]{game_info['game_name']}[/color]\n"
                            f"[b]Summary:[/b] [i]{game_summary}[/i]\n"
                            f"[b]Genres:[/b] [color=green]{game_genres}[/color]\n"
                            f"[b]Release Date:[/b] [color=cyan]{game_release_date}[/color]\n"
                        )

                        # Log and display fetched game info
                        print(f"{bcolors.GREEN}Fetched Game Info:\n{gameinfo_content}{bcolors.ENDC}")

                        # Insert gameinfo content into replacements
                        replacements['!gameinfo!'] = gameinfo_content
                        print(f"{bcolors.GREEN}Game information successfully fetched and added to template!{bcolors.ENDC}")
                    else:
                        # Handle case when no game info is found
                        replacements['!gameinfo!'] = ''
                        print(f"{bcolors.RED}No game information found for {game_name}.\n{bcolors.ENDC}")
                else:
                    print(f"{bcolors.RED}Game name could not be extracted from the NFO or directory.\n{bcolors.ENDC}")
                    replacements['!gameinfo!'] = ''

            except Exception as e:
                # Handle exceptions and log errors
                replacements['!gameinfo!'] = ''
                log(f"Error fetching game information: {str(e)}", log_file_path)
                print(f"{bcolors.RED}Error fetching game information: {str(e)}{bcolors.ENDC}")
        else:
            # If gameinfo is disabled or category is not in game categories
            replacements['!gameinfo!'] = ''

        ### Torrent creation section
        ascii_art_header("Create Torrent")
        # Create a torrent file and store it in the process-specific directory
        try:
            upload_details['etor_started'] = time.strftime('%a %b %d %H:%M:%S %Z %Y')

            # Capture both torrent_file and piece_size from create_torrent
            torrent_file, piece_size = create_torrent(directory, temp_dir,
                                                      config.getboolean('Torrent', 'EDIT_TORRENT'), hasher)

            if torrent_file is None:
                raise RuntimeError("Failed to create torrent file.")

            upload_details['torrent_file'] = torrent_file
            upload_details['piece_size'] = piece_size
            upload_details['etor_completed'] = time.strftime('%a %b %d %H:%M:%S %Z %Y')

        except Exception as e:
            log(f"Error creating torrent: {str(e)}", log_file_path)
            update_upload_status(name=directory_name, new_status='failed')
            fail_exit(tmp_dir, cleanup_enabled)

        # Image upload processing
        ascii_art_header("UploadImages")
        if image_upload_enabled:
            print(f"{bcolors.YELLOW}Uploading images...\n{bcolors.ENDC}")
            try:
                # Collect all image URLs from the source directory
                source_image_urls = upload_images(directory)

                if source_image_urls:
                    image_urls_str = '\n'.join(source_image_urls)
                    update_upload_status(name=directory_name, image_url=image_urls_str)
                    replacements['!imageupload!'] = '\n'.join(source_image_urls)
                    print(f"{bcolors.GREEN}Image upload successful!\n{bcolors.ENDC}")  # Print success message
                else:
                    replacements['!imageupload!'] = ''
                    print(f"No images found in the source directory.\n")  # Print no images found message

                # Upload the screenshots and get URLs
                if screenshots_enabled:
                    print(f"{bcolors.YELLOW}Uploading screenshots...\n{bcolors.ENDC}")
                    screenshots_dir = tmp_dir / 'screens'
                    if screenshots_dir.exists():
                        screenshot_urls = upload_images(screenshots_dir, is_screenshots=True)

                        if screenshot_urls:
                            screenshot_urls_str = '\n'.join(screenshot_urls)
                            update_upload_status(name=directory_name, screenshot_url=screenshot_urls_str)
                            replacements['!screenshots!'] = '\n'.join(screenshot_urls)
                            print(f"{bcolors.GREEN}Screenshot upload successful!{bcolors.ENDC}")  # Print success message
                        else:
                            replacements['!screenshots!'] = ''
                            print(f"No screenshots found.\n")  # Print no screenshots found message
                    else:
                        replacements['!screenshots!'] = ''
                        print(f"Screenshots directory not found or no screenshots token.\n")

                # Upload the game images if game info is available and game images exist
                game_image_dir = tmp_dir / 'images'
                if game_image_dir.exists() and any(game_image_dir.iterdir()):
                    print(f"{bcolors.YELLOW}Uploading game images...\n{bcolors.ENDC}")

                    # Sort files based on the number prefix (1-cover, 2-screenshot, etc.)
                    sorted_images = sorted(game_image_dir.iterdir(), key=lambda x: int(x.name.split('-')[0]))

                    # Ensure the first image is cover image if it exists
                    cover_image = [img for img in sorted_images if '1-cover' in img.name]
                    remaining_images = [img for img in sorted_images if '1-cover' not in img.name]

                    # Combine the cover and remaining images, ensuring the cover is first
                    sorted_images = cover_image + remaining_images

                    # Pass the directory (sorted) to the upload_images function
                    game_image_urls = upload_images(game_image_dir, is_screenshots=False)

                    if game_image_urls:
                        game_image_urls_str = '\n'.join(game_image_urls)
                        update_upload_status(name=directory_name, image_url=game_image_urls_str)
                        replacements['!gameimage!'] = '\n'.join(game_image_urls)
                        print(f"{bcolors.GREEN}Game image upload successful!\n{bcolors.ENDC}")
                    else:
                        replacements['!gameimage!'] = ''
                        print(f"No game images found.\n")
                else:
                    replacements['!gameimage!'] = ''
                    print(f"No game images directory found.")

            except Exception as e:
                log(f"Error uploading images: {str(e)}", log_file_path)
                print(f"{bcolors.RED}Error uploading images: {str(e)}{bcolors.ENDC}")  # Print error message

        # Process .nfo file
        ascii_art_header("NFO")
        print(f"{bcolors.YELLOW}\nFinding NFO data...\n{bcolors.ENDC}")
        try:
            process_nfo(directory, replacements, log_file_path)
        except Exception as e:
            log(f"Error processing .nfo file: {str(e)}", log_file_path)

        print(f"{bcolors.GREEN}Add directory name to template\n{bcolors.ENDC}")
        replacements['!releasename!'] = directory_name

       # Prepare and save the final template with all content
        try:
            output_template_path = os.path.join(tmp_dir, 'output_template.txt')
            prepare_template(str(template_path), output_template_path, replacements)

            # Read the template content after replacements
            with open(output_template_path, 'r', encoding='utf-8') as file:
                template_content = file.read()

            # Remove empty lines
            template_content = "\n".join([line for line in template_content.splitlines() if line.strip()])

            # Remove empty BBCode tags (e.g., [tag][/tag], [tag][tag2][/tag2][/tag])
            template_content = re.sub(r'\[([a-zA-Z0-9]+)]\s*\[/\1]', '', template_content)

            # Save the cleaned content back to the file
            with open(output_template_path, 'w', encoding='utf-8') as file:
                file.write(template_content)

            # Correctly use the path to the file, not the content itself
            template_content = output_template_path  # This ensures the path is used later

        except FileNotFoundError as e:
            log(f"File not found: {str(e)}", log_file_path)
            update_upload_status(name=directory_name, new_status='failed')
            fail_exit(tmp_dir, cleanup_enabled)
        except Exception as e:
            log(f"Error preparing template: {str(e)}", log_file_path)
            update_upload_status(name=directory_name, new_status='failed')
            fail_exit(tmp_dir, cleanup_enabled)

        # Upload the torrent
        ascii_art_header("Uploading")

        # Print and log variables before upload
        print(f"{bcolors.YELLOW}Uploading torrent via API Key...\n{bcolors.ENDC}")

        # Log variables
        log(f"Torrent file: {torrent_file}", log_file_path)
        log(f"Template content path: {template_content}", log_file_path)
        log(f"Auth Headers: {auth_headers}", log_file_path) # Now logging headers instead of cookies
        log(f"Category ID: {category_id}", log_file_path)
        log(f"IMDB ID: {imdb_id}", log_file_path)
        log(f"Mediainfo content length: {len(mediainfo_content) if mediainfo_content else '0'}", log_file_path)

        try:
            # We pass auth_headers here instead of cookies
            upload_torrent(torrent_file, template_content, auth_headers, category_id, imdb_id, mediainfo_content, dupedl_enabled)
            log_upload_details(upload_details, upload_log_path, duplicate_found=False)
            update_status(directory, 'uploaded')
            update_upload_status(name=directory_name, new_status='uploaded')
            print(f"Torrent uploaded successfully. Details logged at: {upload_log_path}")
        except Exception as e:
            log(f"Error uploading torrent: {str(e)}", log_file_path)
            print(f"Failed to upload torrent. Error: {str(e)}")
            update_upload_status(name=directory_name, new_status='failed')
            fail_exit(tmp_dir, cleanup_enabled)
    except KeyboardInterrupt:
        # Cleanup on keyboard interrupt
        print(f"\nKeyboard interrupt detected. Cleaning up and exiting...")
        update_upload_status(name=directory_name, new_status='failed')
        fail_exit(tmp_dir, cleanup_enabled)
    finally:
        cleanup_tmp_dir(tmp_dir, cleanup_enabled)

if __name__ == "__main__":
    main()
