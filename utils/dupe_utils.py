import json
import os
from urllib.parse import quote

import requests

from utils.bcolors import bcolors
from utils.config_loader import ConfigLoader
from utils.logging_utils import log_to_file
from utils.torrent_utils import download_torrent

# Load configuration
config = ConfigLoader().get_config()
TMP_DIR = os.path.join(config.get('Paths', 'TMP_DIR'), str(os.getpid()))
REQUEST_TIMEOUT = config.getint('Network', 'Timeout', fallback=60)
VERIFY_SSL = config.getboolean('Network', 'VERIFY_SSL', fallback=False)

# Ensure TMP_DIR exists
os.makedirs(TMP_DIR, exist_ok=True)


def similar(uploading, existing):
    """
    Calculate the Jaccard similarity between two dictionaries.

    For dictionaries, similarity is based on key-value pairs that match.
    J(A,B) = |A ∩ B| / |A ∪ B| where intersection and union operate on key-value pairs.

    Args:
        uploading (dict): First dictionary
        existing (dict): Second dictionary

    Returns:
        float: Similarity score between 0 and 1
    """
    ### NOTE: UNUSED FUNCTION FOR NOW
    # Get all keys
    keys1 = set(uploading.keys())
    keys2 = set(existing.keys())

    # Find common keys
    common_keys = keys1.intersection(keys2)

    # Count matching key-value pairs
    matches = sum(1 for k in common_keys if uploading[k] == existing[k])

    # Total unique keys (the union)
    total_keys = len(keys1.union(keys2))

    # Prevent division by zero
    if total_keys == 0:
        return 0
    return matches / total_keys


def check_and_download_dupe(release_name, auth_headers):
    """Check for duplicate torrent and download it if found, based on configuration."""
    dupe_check_flag = config.getboolean('Settings', 'DUPECHECK')
    dupe_dl_flag = config.getboolean('Settings', 'DUPEDL')

    if not dupe_check_flag:
        print(f"{bcolors.WARNING}Duplicate check is disabled in the configuration.{bcolors.ENDC}")
        return False

    search_url = f"{config.get('Website', 'SITEURL')}/api/v1/torrents_exact_search?searchText={quote(release_name)}"

    try:
        print(f"{bcolors.YELLOW}Checking for dupe: {release_name}\n{bcolors.ENDC}")
        # Use auth_headers instead of cookies
        response = requests.get(search_url, headers=auth_headers, timeout=REQUEST_TIMEOUT, verify=VERIFY_SSL)
        response.raise_for_status()

        # Log the response
        log_to_file(os.path.join(TMP_DIR, 'dupe_check_response.log'), response.text)

        # Check if the response is empty
        if not response.text.strip():
            print(f"{bcolors.FAIL}Did not found a dupe for: {release_name}\n{bcolors.ENDC}")
            log_to_file(os.path.join(TMP_DIR, 'dupe_empty_response.log'),
                        f"Empty response received for: {release_name}")
            raise ValueError('Duplicate check returned an empty response. Upload stopped; retry when the API responds.')

        # Parse the JSON response
        try:
            torrents = json.loads(response.text)
        except json.JSONDecodeError as e:
            print(f"{bcolors.FAIL}Failed to decode JSON response: {str(e)}{bcolors.ENDC}")
            log_to_file(os.path.join(TMP_DIR, 'dupe_json_decode_error.log'),
                        f"Failed to decode JSON response: {str(e)}")
            raise ValueError('Duplicate check returned invalid JSON. Upload stopped.') from e

        if not isinstance(torrents, list):
            print(f"{bcolors.FAIL}Unexpected response format for: {release_name}{bcolors.ENDC}")
            log_to_file(os.path.join(TMP_DIR, 'dupe_unexpected_format.log'),
                        f"Unexpected response format for: {release_name}")
            raise ValueError('Duplicate check returned an unexpected format. Upload stopped.')

        for torrent in torrents:
            if torrent['name'] == release_name:
                torrent_id = torrent['id']
                dupe_torrent_url = f"{config.get('Website', 'SITEURL')}/api/v1/torrents/download/{torrent_id}"

                # Log and print the duplicate detection
                log_to_file(os.path.join(TMP_DIR, 'dupe_detected.log'),
                            f"Duplicate found: {release_name} (ID: {torrent_id})")
                print(f"{bcolors.OKGREEN}Duplicate found: {release_name}.{bcolors.ENDC}")

                # If DUPECHECK is true and DUPEDL is false, exit the script after checking for duplicates
                if not dupe_dl_flag:
                    print(
                        f"{bcolors.WARNING}Duplicate download is disabled in the configuration. Exiting.{bcolors.ENDC}")
                    return True  # Indicate that a duplicate was found but not downloaded

                # Download the duplicate torrent; download_torrent places it in the watch folder itself
                torrent_file_path = download_torrent(dupe_torrent_url, auth_headers, release_name,
                                                     is_dupe=True, dupe_id=torrent_id)

                if torrent_file_path is None:
                    print(f"{bcolors.FAIL}Failed to download the duplicate torrent.{bcolors.ENDC}")
                    log_to_file(os.path.join(TMP_DIR, 'dupe_download_error.log'),
                                f"Failed to download duplicate torrent for: {release_name}")
                    raise RuntimeError(f"Duplicate found, but failed to download torrent for seeding: {release_name}")

                return True  # Indicate that a duplicate was found and handled

        # If no duplicate was found
        print(f"{bcolors.FAIL}No duplicate found for: {release_name}{bcolors.ENDC}")
        log_to_file(os.path.join(TMP_DIR, 'dupe_not_found.log'), f"No duplicate found for: {release_name}")
        return False

    except requests.RequestException as e:
        # Log any request exceptions
        log_to_file(os.path.join(TMP_DIR, 'dupe_check_error.log'), f"Failed to check for duplicate: {str(e)}")
        print(f"{bcolors.FAIL}Failed to check for duplicate: {str(e)}{bcolors.ENDC}")
        raise RuntimeError('Duplicate check failed. Upload stopped; check your API key and connection.') from e
