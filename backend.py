import json
import os
import re
import shutil
import time
import argparse
import sys
from pathlib import Path


from utils.console import print_section
from utils.bcolors import bcolors
from utils.category_utils import determine_category
from utils.config_loader import ConfigLoader
from utils.job_events import publish as job_event
from utils.database_utils import insert_upload, update_upload_status
from utils.dupe_utils import check_and_download_dupe
from utils.image_utils import upload_images
from utils.logging_utils import log_to_file, log_upload_details
from utils.login_utils import login
from utils.mediainfo_utils import generate_mediainfo
from utils.nfo_utils import process_nfo
from utils.release_metadata import extract_metadata
from utils.screenshot_utils import generate_screenshots
from utils.status_utils import update_status
from utils.template_utils import prepare_template
from utils.torrent_utils import UploadResponseError, TorrentWatchFolderError, create_torrent, upload_torrent
from utils.unrar_upload_utils import run_unrar_upload, register_unrar_mount
from utils.unpacked_source import prepare_source


def log(message, file_path):
    """Utility function to log messages to a file and print to the console."""
    #print(message)
    log_to_file(file_path, message)

def scan_directory(directory):
    """Single walk over the directory: total size in bytes (following symlinks) and first .nfo file."""
    total_size = 0
    nfo_file = None
    for dirpath, dirnames, filenames in os.walk(directory):
        for f in sorted(filenames):
            fp = os.path.join(dirpath, f)
            try:
                total_size += os.path.getsize(os.path.realpath(fp) if os.path.islink(fp) else fp)
            except OSError:
                pass
            if nfo_file is None and f.lower().endswith('.nfo'):
                nfo_file = fp
    return total_size, nfo_file


def parse_category_ids(raw_value):
    """Parse comma-separated category ids into a set for fast membership checks."""
    return {int(cat.strip()) for cat in raw_value.split(',') if cat.strip()}


def cleanup_tmp_dir(directory, cleanup_enabled):
    """Clean up temporary directory if cleanup is enabled."""
    if cleanup_enabled:
        try:
            if directory.exists() and directory.is_dir():
                shutil.rmtree(directory)
                print(f"Cleaned up temporary directory: {directory}{bcolors.ENDC}")
        except Exception as e:
            print(f"Error during cleanup: {str(e)}{bcolors.ENDC}")

def fail_exit(directory, cleanup_enabled, release_dir=None):
    """Clean up tmp and exit non-zero. If release_dir is given, mark it .failed
    so its status doesn't stay stuck on 'uploading'."""
    if release_dir is not None:
        try:
            update_status(release_dir, 'failed')
        except Exception as e:
            print(f"Could not set failed status on {release_dir}: {e}")
    cleanup_tmp_dir(directory, cleanup_enabled)
    sys.exit(1)


def main():
    # --- COMMAND LINE ARGUMENT SETUP ---
    parser = argparse.ArgumentParser(description="DigitalCore upload worker")
    parser.add_argument('directory', help="The name of the release directory to upload")
    parser.add_argument('-cat', '--cat', help="Manual Category ID override", type=int, default=None)
    parser.add_argument('--job', help='Local JSON file containing reviewed upload details')
    args = parser.parse_args()
    # ------------------------------------

    """Main function to run the script."""
    job = json.loads(Path(args.job).read_text(encoding='utf-8')) if args.job else {}

    # Load configuration
    config = ConfigLoader().get_config()

    # Join tmp and running process's PID to generate a unique directory
    tmp_dir = Path(config.get('Paths', 'TMP_DIR')) / str(os.getpid())
    cleanup_enabled = config.getboolean('Settings', 'CLEANUP')

    program_version = config.get('Header', 'VERSION')

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
        directory_name = Path(args.directory).name
        manual_cat_id = args.cat

        print_section("Header", program_version)

        print(f"\n{bcolors.OKBLUE}Starting upload script...{bcolors.ENDC}")

        base_dir = config.get('Paths', 'DATADIR')

        if not base_dir:
            log("DATADIR not found in config.", log_file_path)
            print(f"{bcolors.ENDC}{bcolors.FAIL}DATADIR not found in config.\n{bcolors.ENDC}")
            fail_exit(tmp_dir, cleanup_enabled)
        else:
            # It exists, path it
            base_dir = Path(base_dir)

        directory = Path(args.directory)
        if not directory.is_absolute():
            directory = base_dir / directory
        directory = directory.resolve()

        if not directory.exists():
            log(f"The provided directory does not exist: {directory}", log_file_path)
            print(f"{bcolors.ENDC}{bcolors.FAIL}Directory does not exist: {directory}\n{bcolors.ENDC}")
            fail_exit(tmp_dir, cleanup_enabled)

        if hasher != 'mkbrr':
            log(f"Unknown hasher: {hasher}", log_file_path)
            print(f"{bcolors.ENDC}{bcolors.FAIL}Unknown hasher: {hasher}\n{bcolors.ENDC}")
            fail_exit(tmp_dir, cleanup_enabled)

        original_release_name = directory_name
        try:
            directory, rar_source = prepare_source(directory, log_file_path)
            directory_name = directory.name
        except Exception as exc:
            print(f'Cannot prepare unpacked source: {exc}', flush=True)
            fail_exit(tmp_dir, cleanup_enabled)

        update_status(directory, 'uploading')
        job_event(stage='preparing')

        # One single walk gets us both the total size and the .nfo file
        total_size_bytes, nfo_file_path = scan_directory(directory)

        # Initialize upload details dictionary
        upload_details = {"name": directory_name, "path": str(directory), "category": None,
                          "piece_size": None, "piece_size_bytes": None, "etor_started": None, "torrent_file": None,
                          "etor_completed": None, 'size': f"{round(total_size_bytes / (1024 * 1024), 2)} MB",
                          'nfo': nfo_file_path or "NFO file not found"}

        # Check if settings are enabled
        screenshots_enabled = config.getboolean('Settings', 'SCREENSHOTS')
        mediainfo_enabled = config.getboolean('Settings', 'MEDIAINFO')
        dupecheck_enabled = config.getboolean('Settings', 'DUPECHECK')
        dupedl_enabled = config.getboolean('Settings', 'DUPEDL')
        fast_resume = config.getboolean('Settings', 'ADDFASTRESUME')
        image_upload_enabled = config.getboolean('Settings', 'IMAGE_UPLOAD')
        filters_path = config.get('Paths', 'FILTERS')
        upload_log = config.get('Paths', 'UPLOADLOG')

        screenshot_categories = parse_category_ids(config.get('Settings', 'SCREENSHOT_CATEGORIES'))
        mediainfo_categories = parse_category_ids(config.get('Settings', 'MEDIAINFO_CATEGORIES'))

        # Print settings status
        print()
        print(f"{bcolors.RED}################ Settings ################\n{bcolors.ENDC}")
        print(f"{bcolors.GREEN}Screenshots enabled: {screenshots_enabled}{bcolors.ENDC}")
        print(f"{bcolors.GREEN}Mediainfo enabled: {mediainfo_enabled}{bcolors.ENDC}")
        print(f"{bcolors.GREEN}Dupe Check enabled: {dupecheck_enabled}{bcolors.ENDC}")
        print(f"{bcolors.GREEN}Dupe Download enabled: {dupedl_enabled}{bcolors.ENDC}")
        print(f"{bcolors.GREEN}Fastresume enabled: {fast_resume}{bcolors.ENDC}")
        print(f"{bcolors.GREEN}Image Upload enabled: {image_upload_enabled}{bcolors.ENDC}")
        print(f"{bcolors.GREEN}Cleanup enabled: {cleanup_enabled}{bcolors.ENDC}")
        print(f"{bcolors.GREEN}Loading filters: {filters_path}{bcolors.ENDC}")
        print(f"{bcolors.GREEN}Loading uploadlog: {upload_log}\n{bcolors.ENDC}")
        print(f"{bcolors.RED}################ Settings ################{bcolors.ENDC}")
        print()

        print_section("Login")
        print(f"{bcolors.ENDC}{bcolors.YELLOW}Logging in via API Key...\n{bcolors.ENDC}")
        # Login now returns headers (including API Key) instead of cookies
        try:
            auth_headers = login()  # Updated login returns auth headers
            if not auth_headers:
                log_to_file(log_file_path, "Login failed. Cannot proceed with the script.")
                print(f"{bcolors.RED}Login failed. Cannot proceed with the script.\n{bcolors.ENDC}")
                fail_exit(tmp_dir, cleanup_enabled, release_dir=directory)
            else:
                print(f"{bcolors.OKGREEN}Login successful. Proceeding...\n{bcolors.ENDC}")
        except Exception as e:
            log_to_file(log_file_path, f"Error during login: {str(e)}")
            print(f"{bcolors.FAIL}Error during login: {str(e)}\n{bcolors.ENDC}")
            fail_exit(tmp_dir, cleanup_enabled, release_dir=directory)

        insert_upload(name=directory_name)

        # Create process-specific directory in tmp_dir
        temp_dir = tmp_dir

        # Check for duplicates
        try:
            # Checking is independent of whether existing torrents should be downloaded.
            if dupecheck_enabled:
                job_event(stage='duplicate_check')
                print_section("Dupe checking")
                # Passing auth_headers instead of cookies
                duplicate_found = check_and_download_dupe(directory_name, auth_headers)
                if duplicate_found:
                    job_event(stage='duplicate', site_status='duplicate')
                    log("Duplicate found. Skipping further operations.", log_file_path)
                    category_name, category_id_str = determine_category(directory_name, manual_id=manual_cat_id)
                    category_id = int(category_id_str)
                    upload_details['category'] = f"{category_name} ({category_id})"

                    dupe_replacements = {'!version!': program_version}
                    try:
                        process_nfo(directory, dupe_replacements, log_file_path, nfo_file=nfo_file_path)
                    except Exception as e:
                        log(f"Error processing .nfo file for duplicate UNRAR upload: {str(e)}", log_file_path)

                    try:
                        unrar_result = run_unrar_upload(
                            release_name=directory_name,
                            release_dir=directory,
                            category_name=category_name,
                            category_id=category_id,
                            auth_headers=auth_headers,
                            mediainfo_content='',
                            base_replacements=dupe_replacements,
                            template_path=template_path,
                            tmp_dir=tmp_dir,
                            upload_log_path=upload_log_path,
                            hasher=hasher,
                            dupedl_enabled=dupedl_enabled,
                        )
                    except Exception as e:
                        log(f"Optional UNRAR upload failed after duplicate handling: {str(e)}", log_file_path)
                        unrar_result = {'status': 'failed', 'reason': str(e)}
                    upload_details['unrar_upload_status'] = unrar_result.get('status')
                    if unrar_result.get('reason'):
                        upload_details['unrar_upload_reason'] = unrar_result.get('reason')
                    if unrar_result.get('mount_path'):
                        upload_details['unrar_mount_path'] = unrar_result.get('mount_path')
                    if unrar_result.get('torrent_name'):
                        upload_details['unrar_torrent_name'] = unrar_result.get('torrent_name')

                    update_status(directory, 'dupe')
                    update_upload_status(name=directory_name, new_status='dupe')
                    log_upload_details(upload_details, upload_log_path, duplicate_found=True)
                    cleanup_tmp_dir(tmp_dir, cleanup_enabled)  # Clean up tmp_dir
                    sys.exit(10)
            else:
                log("Dupe check or download is disabled in the config.", log_file_path)

        except Exception as e:
            log(f"Error checking for duplicates: {str(e)}", log_file_path)
            update_upload_status(name=directory_name, new_status='failed')
            fail_exit(tmp_dir, cleanup_enabled, release_dir=directory)

        print_section("Category")

        # --- Updated call to pass manual_category_id ---
        category_name, category_id_str = determine_category(directory_name, manual_id=manual_cat_id)
        category_id = int(category_id_str)  # Convert category_id to integer
        # -----------------------------------------------

        upload_details['category'] = f"{category_name} ({category_id})"
        update_upload_status(name=directory_name, new_status='uploading', size=f'{upload_details["size"]}', category=f'{category_name}')

        # Initialize replacements dictionary with version info.
        replacements = {'!version!': program_version, '!description!': job.get('description', ''),
                        '!imageupload!': '', '!screenshots!': '', '!nfo!': ''}
        manual_images = job.get('images', [])
        if manual_images:
            replacements['!imageupload!'] = '\n'.join(f'[c][img]{url}[/img][/c]' for url in manual_images)
        
        ### Screenshots processing section
        if screenshots_enabled:
            job_event(stage='screenshots')
            print_section("Screenshots")
            if category_id in screenshot_categories:
                try:
                    generate_screenshots(directory, category_id)
                except Exception as e:
                    log(f"Error generating screenshots: {str(e)}", log_file_path)
                    print(f"Error generating screenshots: {e}", flush=True)
                    fail_exit(tmp_dir, cleanup_enabled, release_dir=directory)
            else:
                log(f"Category ID {category_id} is not in the screenshot categories: {screenshot_categories}", log_file_path)
        else:
            log("Screenshots are disabled.", log_file_path)

        # Mediainfo processing
        mediainfo_content = ''
        if mediainfo_enabled:
            job_event(stage='mediainfo')
            print_section("Mediainfo")
            if category_id in mediainfo_categories:
                try:
                    mediainfo_file_path = generate_mediainfo(directory, temp_dir)
                except Exception as e:
                    log(f"Error generating mediainfo: {str(e)}", log_file_path)
                else:
                    if mediainfo_file_path and mediainfo_file_path.exists():
                        with open(mediainfo_file_path, 'r') as file:
                            mediainfo_content = file.read()

        ### Torrent creation section
        print_section("Create Torrent")
        job_event(stage='hashing')
        # Create a torrent file and store it in the process-specific directory
        try:
            upload_details['etor_started'] = time.strftime('%a %b %d %H:%M:%S %Z %Y')

            # Capture both torrent_file and piece_size from create_torrent
            torrent_file, piece_size = create_torrent(directory, temp_dir,
                                                      config.getboolean('Torrent', 'EDIT_TORRENT'), hasher,
                                                      total_size_bytes=total_size_bytes, source_torrent=None if rar_source else job.get('source_torrent'))

            if torrent_file is None:
                raise RuntimeError("Failed to create torrent file.")

            upload_details['torrent_file'] = torrent_file
            upload_details['piece_size'] = piece_size
            upload_details['etor_completed'] = time.strftime('%a %b %d %H:%M:%S %Z %Y')

        except Exception as e:
            log(f"Error creating torrent: {str(e)}", log_file_path)
            update_upload_status(name=directory_name, new_status='failed')
            fail_exit(tmp_dir, cleanup_enabled, release_dir=directory)

        # Image upload processing
        print_section("UploadImages")
        job_event(stage='images')
        if image_upload_enabled:
            try:
                if not manual_images:
                    source_image_urls = upload_images(directory)
                    if not source_image_urls and any(p.suffix.lower() in ('.jpg', '.jpeg', '.png', '.gif', '.webp')
                                                     for p in directory.rglob('*') if p.is_file()):
                        raise RuntimeError('Images were found in the folder but none could be uploaded.')
                    replacements['!imageupload!'] = '\n'.join(source_image_urls)
                    if source_image_urls:
                        update_upload_status(name=directory_name, image_url='\n'.join(source_image_urls))
                screenshots_dir = tmp_dir / 'screens'
                if screenshots_enabled and screenshots_dir.exists():
                    generated_count = len(list(screenshots_dir.glob('*.jpg')))
                    screenshot_urls = upload_images(screenshots_dir, is_screenshots=True)
                    replacements['!screenshots!'] = '\n'.join(screenshot_urls)
                    if screenshot_urls:
                        update_upload_status(name=directory_name, screenshot_url='\n'.join(screenshot_urls))
                    elif generated_count and config.getboolean('Settings', 'REQUIRE_SCREENSHOTS', fallback=True):
                        raise RuntimeError('Screenshots were generated but could not be uploaded. Check the image host.')
            except Exception as e:
                print(f'Image upload failed: {e}', flush=True)
                fail_exit(tmp_dir, cleanup_enabled, release_dir=directory)

        # Process .nfo file
        print_section("NFO")
        job_event(stage='description')
        print(f"{bcolors.YELLOW}\nFinding NFO data...\n{bcolors.ENDC}")
        try:
            if config.getboolean('Settings','INCLUDE_NFO',fallback=True):
                process_nfo(directory, replacements, log_file_path, nfo_file=nfo_file_path)
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
            template_content = template_content.strip()

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
            fail_exit(tmp_dir, cleanup_enabled, release_dir=directory)
        except Exception as e:
            log(f"Error preparing template: {str(e)}", log_file_path)
            update_upload_status(name=directory_name, new_status='failed')
            fail_exit(tmp_dir, cleanup_enabled, release_dir=directory)

        # Upload the torrent
        print_section("Uploading")
        job_event(stage='uploading', site_status='pending')

        # Print and log variables before upload
        print(f"{bcolors.YELLOW}Uploading torrent...\n{bcolors.ENDC}")

        # Log variables
        log(f"Torrent file: {torrent_file}", log_file_path)
        log(f"Template content path: {template_content}", log_file_path)
        redacted_auth_headers = {
            key: ('***' if 'KEY' in key.upper() or 'TOKEN' in key.upper() else value)
            for key, value in auth_headers.items()
        }
        log(f"Auth Headers: {redacted_auth_headers}", log_file_path)
        log(f"Category ID: {category_id}", log_file_path)
        log(f"Mediainfo content length: {len(mediainfo_content) if mediainfo_content else '0'}", log_file_path)

        # Studio / performers / year / language from the release name,
        # runtime from mediainfo on the biggest file
        release_metadata = {}
        if config.getboolean('Settings', 'POST_METADATA', fallback=True):
            try:
                release_metadata = extract_metadata(directory_name, directory=directory)
                log(f"Release metadata: {release_metadata}", log_file_path)
            except Exception as e:
                log(f"Metadata extraction failed: {e}", log_file_path)

        release_metadata.update(job.get('metadata', {}))
        if manual_images:
            release_metadata['firstpic'] = manual_images[0]

        try:
            # We pass auth_headers here instead of cookies
            upload_result = upload_torrent(
                torrent_file,
                template_content,
                auth_headers,
                category_id,
                mediainfo_content,
                dupedl_enabled,
                metadata=release_metadata,
            )
            if rar_source:
                register_unrar_mount(original_release_name, directory, upload_result,
                    config.getfloat('UnrarUpload','RATIO_TARGET',fallback=2.5), archive_path=rar_source)
            upload_details['upload_status'] = 'uploaded'
            upload_details['site_upload_status'] = 'accepted'
            upload_details['seed_setup_status'] = 'watch_torrent_created'
            upload_details['torrent_id'] = upload_result.get('torrent_id')
            upload_details['torrent_name'] = upload_result.get('torrent_name')
            upload_details['watch_torrent'] = upload_result.get('watch_torrent')
            job_event(stage='watch_folder_ready', site_status='accepted',
                      torrent_id=upload_result.get('torrent_id'), torrent_name=upload_result.get('torrent_name'),
                      watch_torrent=upload_result.get('watch_torrent'))

            try:
                unrar_result = run_unrar_upload(
                    release_name=directory_name,
                    release_dir=directory,
                    category_name=category_name,
                    category_id=category_id,
                    auth_headers=auth_headers,
                    mediainfo_content=mediainfo_content,
                    base_replacements=replacements,
                    template_path=template_path,
                    tmp_dir=tmp_dir,
                    upload_log_path=upload_log_path,
                    hasher=hasher,
                    dupedl_enabled=dupedl_enabled,
                )
            except Exception as e:
                log(f"Optional UNRAR upload failed after main upload: {str(e)}", log_file_path)
                unrar_result = {'status': 'failed', 'reason': str(e)}
            upload_details['unrar_upload_status'] = unrar_result.get('status')
            if unrar_result.get('reason'):
                upload_details['unrar_upload_reason'] = unrar_result.get('reason')
            if unrar_result.get('mount_path'):
                upload_details['unrar_mount_path'] = unrar_result.get('mount_path')
            if unrar_result.get('torrent_name'):
                upload_details['unrar_torrent_name'] = unrar_result.get('torrent_name')

            log_upload_details(upload_details, upload_log_path, duplicate_found=False)
            update_status(directory, 'uploaded')
            update_upload_status(name=directory_name, new_status='uploaded')
            print(f"Torrent uploaded successfully. Details logged at: {upload_log_path}")
        except Exception as e:
            log(f"Error uploading torrent: {str(e)}", log_file_path)
            job_event(error=str(e))
            print(f"Failed to upload torrent. Error: {str(e)}")
            if isinstance(e, UploadResponseError):
                job_event(site_status='unknown')
                upload_details['upload_status'] = 'confirmation_failed'
                upload_details['site_upload_status'] = 'unknown_check_site'
                upload_details['seed_setup_status'] = 'not_started'
            elif isinstance(e, TorrentWatchFolderError):
                upload_details['upload_status'] = 'failed_after_site_upload'
                upload_details['site_upload_status'] = 'accepted'
                upload_details['torrent_id'] = e.torrent_id
                upload_details['torrent_name'] = e.torrent_name
                upload_details['torrent_download_url'] = e.torrent_url
                upload_details['seed_setup_status'] = 'failed'
            else:
                upload_details['upload_status'] = 'failed'
            upload_details['upload_error'] = str(e)
            log_upload_details(upload_details, upload_log_path, duplicate_found=False)
            update_upload_status(name=directory_name, new_status='failed')
            fail_exit(tmp_dir, cleanup_enabled, release_dir=directory)
    except KeyboardInterrupt:
        # Cleanup on keyboard interrupt (directory may not exist yet when interrupted early)
        print(f"\nKeyboard interrupt detected. Cleaning up and exiting...")
        release_dir = locals().get('directory')
        if release_dir is not None:
            update_upload_status(name=release_dir.name, new_status='failed')
        fail_exit(tmp_dir, cleanup_enabled, release_dir=release_dir)
    finally:
        cleanup_tmp_dir(tmp_dir, cleanup_enabled)

if __name__ == "__main__":
    main()
