import os
import time
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests

from utils.config_loader import ConfigLoader
from utils.logging_utils import log_to_file
from utils import image_host


def upload_images(directory, is_screenshots=False):
    """
    Upload images from the specified directory and its subdirectories, returning formatted URLs.

    Rate-limit friendly: uploads are spaced out (IMAGE_UPLOAD_DELAY seconds between
    requests) and a 429 response is retried with backoff, honoring Retry-After.

    Args:
        directory (Path): The directory containing the images to upload.
        is_screenshots (bool): Flag to indicate if the directory is for screenshots.

    Returns:
        list: A list of formatted image URLs.
    """
    # Load configuration
    config = ConfigLoader().get_config()
    upload_workers = max(1, config.getint('Settings', 'IMAGE_UPLOAD_WORKERS', fallback=1))
    upload_delay = config.getfloat('Settings', 'IMAGE_UPLOAD_DELAY', fallback=1.5)
    max_attempts = max(1, config.getint('Settings', 'IMAGE_UPLOAD_RETRIES', fallback=3))
    request_timeout = config.getint('Network', 'Timeout', fallback=120)
    temp_dir = Path(config.get('Paths', 'TMP_DIR')) / str(os.getpid())

    # Ensure TMP_DIR exists
    temp_dir.mkdir(parents=True, exist_ok=True)

    # Prepare to collect image URLs
    uploaded_image_urls = []

    # Recursively find image files in the directory
    image_files = [f for f in directory.rglob('*') if f.suffix.lower() in ('.jpg', '.jpeg', '.png', '.gif', '.webp')]

    # Sort image files by priority (cover/front first) and then alphabetically
    image_files.sort(key=lambda f: (not any(keyword in f.stem.lower() for keyword in ['cover', 'front']), f.name))

    log_file_path = temp_dir / 'image_upload.log'

    if not image_files:
        print(f"No images found {directory} to upload")
        log_to_file(log_file_path, f"No images found {directory} to upload")
        return uploaded_image_urls

    # Global pacing between requests, shared across workers
    pace_lock = threading.Lock()
    last_request = [0.0]

    def pace():
        if upload_delay <= 0:
            return
        with pace_lock:
            wait = last_request[0] + upload_delay - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            last_request[0] = time.monotonic()

    def post_image(image_file, filename_without_extension):
        return image_host.post(config, image_file.name, image_file.read_bytes())

    RETRYABLE_STATUS = {429, 500, 502, 503, 504}

    def upload_one(image_file):
        """Upload a single image, retrying rate limits, server hiccups and
        connection errors with backoff. Returns the formatted URL or None."""
        filename_without_extension = image_file.stem
        log_to_file(log_file_path, f"Attempting to upload image: {image_file.name} ({image_file.stat().st_size} bytes)")

        for attempt in range(1, max_attempts + 1):
            pace()
            response = None
            try:
                response = post_image(image_file, filename_without_extension)
                status = response.status_code
            except ValueError:
                print(f'Image file is invalid or image-host settings are incomplete: {image_file.name}', flush=True)
                return None
            except requests.RequestException as e:
                status = 'connection error'
            log_to_file(log_file_path,
                        f"Image upload result for {image_file.name}: {status} (attempt {attempt})")

            if response is not None and response.status_code in (200,201):
                image_url = image_host.image_url(response)
                if image_url:
                    log_to_file(log_file_path, f"Image uploaded successfully: {image_file.name}")
                    return f"[c][imgw]{image_url}[/imgw][/c]" if is_screenshots else f"[c][img]{image_url}[/img][/c]"
                log_to_file(log_file_path, f"Image URL not found in response for {image_file.name}")
                return None

            retryable = response is None or response.status_code in RETRYABLE_STATUS
            if retryable and attempt < max_attempts:
                wait = 5.0 * (2 ** (attempt - 1))  # 5s, 10s, 20s...
                if response is not None:
                    try:
                        wait = float(response.headers.get('Retry-After', ''))
                    except ValueError:
                        pass
                wait = min(max(wait, 1.0), 60.0)
                print(f"Upload of {image_file.name} failed ({status}), retrying in {wait:.0f}s "
                      f"(attempt {attempt}/{max_attempts})", flush=True)
                time.sleep(wait)
                continue

            if response is not None:
                log_to_file(log_file_path,
                            f"Failed to upload image {image_file.name}. Status code: {response.status_code}")
                # Show the site's actual error in the console/qbit log too
                print(f"Failed to upload image {image_file.name} ({status}). Check image-host settings.", flush=True)
            else:
                print(f"Failed to upload image {image_file.name} ({status})", flush=True)
            return None
        return None

    # Results keep the original (sorted) order. Default is sequential with pacing;
    # IMAGE_UPLOAD_WORKERS > 1 re-enables parallelism (pacing still applies globally).
    with ThreadPoolExecutor(max_workers=min(upload_workers, len(image_files))) as pool:
        results = dict(zip(image_files, pool.map(upload_one, image_files)))

    # Second chance: anything that failed gets one more sequential pass after a
    # cool-down, so a temporary host hiccup can't silently drop screenshots.
    failed = [f for f, url in results.items() if not url]
    if failed and len(failed) < len(image_files):
        print(f"{len(failed)} of {len(image_files)} images failed, retrying them in 30s...", flush=True)
        log_to_file(log_file_path, f"Retrying failed images: {[f.name for f in failed]}")
        time.sleep(30)
        for image_file in failed:
            results[image_file] = upload_one(image_file)

    uploaded_image_urls = [results[f] for f in image_files if results[f]]
    failed_final = [f.name for f in image_files if not results[f]]
    if failed_final:
        print(f"WARNING: {len(failed_final)}/{len(image_files)} images were NOT uploaded: "
              + ", ".join(failed_final), flush=True)
        log_to_file(log_file_path, f"FINAL image upload failures: {failed_final}")
    else:
        print(f"All {len(image_files)} images uploaded.", flush=True)

    return uploaded_image_urls