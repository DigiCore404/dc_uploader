import os
import re
import subprocess
from pathlib import Path

from utils.bcolors import bcolors


def generate_mediainfo(directory, tmp_dir):
    """Generate media info for movie files in the given directory using the mediainfo executable, and save to a file in tmp_dir."""
    media_extensions = ['*.mp4', '*.mkv', '*.avi', '*.mov', '*.flv', '*.wmv', '*.mpg', '*.mpeg', '*.webm', '*.m4v']
    
    mediainfo_output = ""
    print(f"{bcolors.YELLOW}Creating mediainfo for directory: {directory}\n{bcolors.ENDC}")

    # Collect all movie files matching the extensions recursively
    media_files = []
    for ext in media_extensions:
        found_files = list(Path(directory).rglob(ext))
        media_files.extend(found_files)
    
    if not media_files:
        print(f"{bcolors.FAIL}No media files found.{bcolors.ENDC}")  # Red text for no files found
        return
    else:
        print(f"Found {len(media_files)} media files, attempting to get mediainfo from one candidate.")
        # Sort found media files alphabetically
        media_files = sorted(media_files)

    for media_file in media_files:
        print(f"Processing file: {media_file}\n")
        try:
            # Run mediainfo command and capture output
            result = subprocess.run(
                ['mediainfo', str(media_file)],
                text=True,
                capture_output=True,
                check=True
            )
        except subprocess.CalledProcessError as e:
            # Handle errors in running mediainfo
            print(f"{bcolors.FAIL}Error getting media info for {media_file}: {e}{bcolors.ENDC}")  # Red text for errors
            mediainfo_output = '' # reset for next run
        else:
            mediainfo_output = result.stdout
            # Modify lines containing "Complete name:" to just be the basename
            new_lines = []
            for line in mediainfo_output.splitlines():
                m = re.match(r'(\s*Complete name\s*:\s*)(.+)', line, flags=re.IGNORECASE)
                if m:
                    prefix, fullpath = m.groups()
                    # basename will drop all directories, leaving just the final filename
                    name = os.path.basename(fullpath)
                    new_lines.append(f"{prefix}{name}")
                else:
                    new_lines.append(line)

            mediainfo_output = "\n".join(new_lines)

            # Quick check
            if 'General' in mediainfo_output and ('Video' in mediainfo_output or 'Audio' in mediainfo_output):
                print(
                    f"{bcolors.OKGREEN}MediaInfo successfully generated for {media_file}{bcolors.ENDC}")
                break
            else:
                mediainfo_output = ''

    # Save mediainfo output to a file in the tmp_dir
    mediainfo_file_path = None
    if mediainfo_output:
        mediainfo_output = mediainfo_output.strip()
        mediainfo_file_path = Path(tmp_dir) / 'mediainfo_output.txt'
        with open(mediainfo_file_path, 'w') as file:
            file.write(mediainfo_output)
        
        print(f"{bcolors.OKGREEN}MediaInfo created and saved to: {mediainfo_file_path}\n{bcolors.ENDC}")
    
    return mediainfo_file_path
