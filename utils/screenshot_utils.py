import os
import subprocess
from pathlib import Path

from utils.bcolors import bcolors
from utils.config_loader import ConfigLoader

# Load configuration settings
config = ConfigLoader().get_config()

def generate_screenshots(directory, category_id):
    """Generate screenshots for movie files in the given directory using the MTN tool."""
    mtn_width = config.get('MediaTools', 'MTNWIDTH')
    mtn_postby = config.get('MediaTools', 'MTNPOSTBY')
    mtn_setting = config.get('MediaTools', 'MTNSETTING')
    mtn_fontfile = config.get('MediaTools', 'MTNFONTFILE')
    tmp_dir = Path(config.get('Paths', 'TMP_DIR'))

    # Define paths for temporary files
    tmp_dir = tmp_dir / str(os.getpid())
    screenshots_dir = tmp_dir / 'screens'
    screenshots_dir.mkdir(parents=True, exist_ok=True)
    mounts_dir = tmp_dir / 'mounts'
    mounts_dir.mkdir(parents=True, exist_ok=True)

    # Prepare the command options
    command_opts = f"{mtn_width} {mtn_postby} {mtn_setting} -f {mtn_fontfile} -h 150 -q"

    # Check if screenshots are enabled and category is in the configured list
    screenshots_enabled = config.getboolean('Settings', 'SCREENSHOTS')
    rar2fs_screenshots_enabled = config.getboolean('Settings', 'RAR2FS_SCREENSHOTS')
    screenshot_categories = set(map(int, config.get('Settings', 'SCREENSHOT_CATEGORIES').split(',')))
    rar2fs_categories = set(map(int, config.get('Settings', 'RAR2FS_CATEGORIES').split(',')))

    if not screenshots_enabled or category_id not in screenshot_categories:
        print("Screenshots are disabled or category ID is not in the screenshot categories.")
        return

    # Check if RAR2FS should be used
    if rar2fs_screenshots_enabled and category_id in rar2fs_categories:
        print(f"{bcolors.YELLOW}RAR2FS Enabled, mounting RAR files\n{bcolors.ENDC}")
        rar_files = list(Path(directory).rglob('*.rar'))
        if rar_files:
            for rar_file in rar_files:
                mount_point = mounts_dir / str(os.getpid())
                mount_point.mkdir(parents=True, exist_ok=True)
                try:
                    # Mount RAR file using rar2fs
                    subprocess.run(['rar2fs', '-o', 'allow_other', '--seek-length=1', str(rar_file), str(mount_point)], check=True)
                    # Process movie files
                    process_media_files(mount_point, command_opts, screenshots_dir)
                finally:
                    # Clean up mount point
                    subprocess.run(['fusermount', '-u', str(mount_point)], check=True)
                    mount_point.rmdir()
        else:
            # Process movie files directly if no RAR files found
            process_media_files(directory, command_opts, screenshots_dir)
    else:
        # Process movie files directly if RAR2FS is not enabled
        process_media_files(directory, command_opts, screenshots_dir)
    

def process_media_files(directory, command_opts, screenshots_dir, is_rar2fs=False, skip_sample_filter=None):
    """Process movie files to generate screenshots using MTN."""
    if skip_sample_filter is None:
        skip_sample_filter = config.getboolean('Settings', 'SKIP_SAMPLE_FILTER', fallback=False)
    
    valid_media_ext = ['*.mp4', '*.mkv', '*.avi', '*.mov', '*.flv', '*.wmv', '*.mpg', '*.m2ts', '*.vob']
    media_files = []
    screenshots_generated = False
    
    print(f"{bcolors.YELLOW}Creating screenshots\n{bcolors.ENDC}")

    # Collect all movie files recursively
    for ext in valid_media_ext:
        media_files.extend(Path(directory).rglob(ext))
    
    if not media_files:
        print(f"{bcolors.FAIL}No media files found.{bcolors.ENDC}")  # Red text for no files found
        return
    else:
        # Sort it so it's alphabetical
        media_files = sorted(media_files)
        # Log the number of media files found
        print(f"Found {len(media_files)} media files, attempting to get screenshots from one candidate.")

    not_sample_media = []

    for item in media_files:
        name = str(item)
        # Create a list of actual media vs sample folders via folder name. Searches for 'sample', case-insensitive
        if not skip_sample_filter and not is_rar2fs and 'sample' in name.lower():
            print(f"{bcolors.YELLOW}Skipping sample file: {item}{bcolors.ENDC}")
        else:
            # Add it to the list of actual media
            not_sample_media.append(item)

    for media_file in not_sample_media:
        print(f"Processing {media_file}")
        # Run MTN to generate screenshots
        screenshots_generated = mtn_exec(command_opts, media_file, [config.get('MediaTools', 'MTNBIN')],
                                         screenshots_dir)
        if not screenshots_generated:
            print("Trying again with fallback mtn binary")
            screenshots_generated = mtn_exec(command_opts, media_file, ['bin/mtn/mtn'], screenshots_dir)
            if not screenshots_generated:
                # Still can't generate screenshots
                print(f"No screenshots generated for {media_file} with fallback mtn binary. Possibly broken media file")
            else:
                # We successfully got screenshots with fallback, return
                return
        else:
            # We successfully got screenshots with fallback, return
            return

    # If no screenshots were generated, and it's not a RAR2FS mount, check sample directories, if any
    screenshot_sample_fallback = config.getboolean('Settings', 'SCREENSHOT_SAMPLE_FALLBACK', fallback=True)
    if not screenshots_generated and not is_rar2fs and screenshot_sample_fallback:
        print(f"{bcolors.YELLOW}No screenshots generated. Checking sample directories...{bcolors.ENDC}")
        # Process movie files in sample directories if no screenshots were generated
        sample_dirs = [
            d for d in Path(directory).rglob('*')
            if d.is_dir() and 'sample' in d.name.lower()
        ]
        if sample_dirs:
            print(f"Found sample directories: {sample_dirs}")
            for sample_dir in sample_dirs:
                process_media_files(sample_dir, command_opts, screenshots_dir, is_rar2fs=False, skip_sample_filter=True)


def mtn_exec(command_opts, media_file, mtn_path, screenshots_dir):
    command = mtn_path + command_opts.split() + [str(media_file), '-o', '.jpg', '-O', str(screenshots_dir)]
    print(f"Running command: {' '.join(command)}")
    try:
        result = subprocess.run(command, capture_output=True, text=True)
    except subprocess.CalledProcessError as e:
        print(f"{bcolors.FAIL}Error creating screenshots for {media_file}: {e}{bcolors.ENDC}")  # Red text for errors
    except Exception as e:
        print(f"{bcolors.FAIL}Unexpected error for {media_file}: {e}{bcolors.ENDC}")  # Red text for unexpected errors
    else:
        # Debug
        # print(f"Command output: {result.stdout}")
        # print(f"Command error: {result.stderr}")
        if result.returncode != 0:
            print(f"Failed to generate screenshots for {media_file}.")
            return False

        # Check if any screenshot files are created
        screenshot_files = list(screenshots_dir.glob(f"{media_file.stem}*.jpg"))
        if screenshot_files:
            print(f"Successfully generated screenshots")
            print(f"Generated screenshots: {screenshot_files}")
            return True
        else:
            print(f"No screenshots generated for {media_file}.")
            return False
