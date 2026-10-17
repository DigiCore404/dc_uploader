import os
import re
import subprocess
from pathlib import Path

from utils.bcolors import bcolors

MEDIA_EXTENSIONS = {'.mp4', '.mkv', '.avi', '.mov', '.flv', '.wmv', '.mpg', '.mpeg', '.webm', '.m4v', '.m2ts', '.ts'}
SAMPLE_WORDS = {'sample', 'samples', 'proof', 'trailer', 'extras', 'extra'}


def is_sample_path(path):
    """Return True for obvious sample/extras paths."""
    path = Path(path)
    parts = {part.lower() for part in path.parts}
    name = path.name.lower()
    stem = path.stem.lower()
    return bool(parts & SAMPLE_WORDS) or 'sample' in stem or name.startswith('proof.')


def iter_media_files(directory):
    """Walk media files using os.walk; this behaves better on rar2fs/FUSE than Path.rglob."""
    seen_dirs = set()
    for dirpath, dirnames, filenames in os.walk(directory, followlinks=True):
        try:
            stat_result = os.stat(dirpath)
        except OSError:
            dirnames[:] = []
            continue

        dir_key = (stat_result.st_dev, stat_result.st_ino)
        if dir_key in seen_dirs:
            dirnames[:] = []
            continue
        seen_dirs.add(dir_key)

        for filename in filenames:
            path = Path(dirpath) / filename
            if path.suffix.lower() in MEDIA_EXTENSIONS:
                yield path


def media_priority(path):
    try:
        size_score = -path.stat().st_size
    except OSError:
        size_score = 0
    return (is_sample_path(path), size_score, str(path).lower())


def generate_mediainfo(directory, tmp_dir, allow_sample_fallback=True):
    """Generate media info for movie files in the given directory using the mediainfo executable, and save to a file in tmp_dir."""
    mediainfo_output = ""
    print(f"{bcolors.YELLOW}Creating mediainfo for directory: {directory}\n{bcolors.ENDC}")

    # os.walk with followlinks is more reliable for symlinks and rar2fs/FUSE mounts.
    media_files = sorted(iter_media_files(directory), key=media_priority)

    if not media_files:
        print(f"{bcolors.FAIL}No media files found.{bcolors.ENDC}")  # Red text for no files found
        return

    print(f"Found {len(media_files)} media files, attempting to get mediainfo from one candidate.")

    main_media_files = [path for path in media_files if not is_sample_path(path)]
    if main_media_files:
        media_files = main_media_files
    elif not allow_sample_fallback:
        print(f"{bcolors.WARNING}Only sample/extras media files found; skipping mediainfo.{bcolors.ENDC}")
        return
    else:
        print(f"{bcolors.WARNING}Only sample/extras media files found; using sample fallback.{bcolors.ENDC}")

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
