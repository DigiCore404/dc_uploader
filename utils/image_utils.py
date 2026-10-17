import os
from pathlib import Path

import requests

from utils.config_loader import ConfigLoader
from utils.logging_utils import log_to_file


def upload_images(directory, is_screenshots=False):
    """
    Upload images from the specified directory and its subdirectories, returning formatted URLs.

    Args:
        directory (Path): The directory containing the images to upload.
        is_screenshots (bool): Flag to indicate if the directory is for screenshots.

    Returns:
        list: A list of formatted image URLs.
    """
    # Load configuration
    config = ConfigLoader().get_config()
    upload_url = config.get('ImageHost', 'UPLOADIMGURL')
    auth_code = config.get('ImageHost', 'AUTHCODE')
    temp_dir = Path(config.get('Paths', 'TMP_DIR')) / str(os.getpid())

    # Ensure TMP_DIR exists
    temp_dir.mkdir(parents=True, exist_ok=True)

    # Prepare to collect image URLs
    uploaded_image_urls = []

    # Recursively find image files in the directory
    image_files = [f for f in directory.rglob('*') if f.suffix.lower() in ('.jpg', '.jpeg', '.png', '.gif')]

    # Sort image files by priority (cover/front first) and then alphabetically
    image_files.sort(key=lambda f: (not any(keyword in f.stem.lower() for keyword in ['cover', 'front']), f.name))

    log_file_path = temp_dir / 'image_upload.log'

    if not image_files:
        print(f"No images found {directory} to upload")
        log_to_file(log_file_path, f"No images found {directory} to upload")
        return uploaded_image_urls

    for image_file in image_files:
        try:
            # Extract filename without extension
            filename_without_extension = image_file.stem

            # Log image file details
            log_to_file(log_file_path, f"Attempting to upload image: {image_file.name}")
            log_to_file(log_file_path, f"File path: {image_file}")
            log_to_file(log_file_path, f"File size: {image_file.stat().st_size} bytes")

            with image_file.open('rb') as image:
                response = requests.post(
                    upload_url,
                    headers={'Authorization': auth_code},  # Directly use the provided Authorization header
                    files={'file': (image_file.name, image, 'multipart/form-data')},
                    data={'title': filename_without_extension}  # Use the filename without extension
                )

                # Log response details
                log_to_file(
                    log_file_path,
                    f"Image upload response status: {response.status_code}"
                )
                log_to_file(
                    log_file_path,
                    f"Response content: {response.text}"
                )

                if response.status_code == 200:
                    response_json = response.json()
                    image_url = response_json.get('data', {}).get('link', '')
                    if image_url:
                        formatted_url = f"[c][img]{image_url}[/img][/c]" if not is_screenshots else f"[c][imgw]{image_url}[/imgw][/c]"
                        uploaded_image_urls.append(formatted_url)
                        log_to_file(log_file_path, f"Image uploaded successfully: {image_file.name}")
                    else:
                        log_to_file(log_file_path, f"Image URL not found in response for {image_file.name}")
                else:
                    log_to_file(
                        log_file_path,
                        f"Failed to upload image {image_file.name}. Status code: {response.status_code}\n{response.text}"
                    )
                    print(f"Failed to upload image {image_file.name}. Status code: {response.status_code}")

        except Exception as e:
            log_to_file(
                log_file_path,
                f"Error uploading image {image_file.name}: {str(e)}"
            )
            print(f"Error uploading image {image_file.name}: {str(e)}")

    return uploaded_image_urls