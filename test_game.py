import argparse
import os
import re
from datetime import datetime, timezone
from pathlib import Path

import requests

from utils.config_loader import ConfigLoader
from utils.logging_utils import log_to_file

# Load configuration
config = ConfigLoader().get_config()
igdb_client_id = config.get('IGDB', 'CLIENT_ID')
igdb_client_secret = config.get('IGDB', 'CLIENT_SECRET')

# Constants
IGDB_API_URL = "https://api.igdb.com/v4"

# Authentication function for IGDB API
def get_igdb_token():
    auth_url = "https://id.twitch.tv/oauth2/token"
    params = {
        'client_id': igdb_client_id,
        'client_secret': igdb_client_secret,
        'grant_type': 'client_credentials'
    }
    response = requests.post(auth_url, params=params)
    if response.status_code == 200:
        return response.json()['access_token']
    else:
        raise Exception(f"Failed to authenticate with IGDB: {response.text}")

def extract_game_name(release_name):
    """
    Extract the game name by removing everything after stop words (like Update, Repack, etc.)
    or the dash, and clean up underscores and dots in the release name.
    
    Args:
        release_name (str): The release name string.
    
    Returns:
        str: The cleaned-up game name suitable for querying.
    """
    # Define stop words including the dash ('-') for removing unnecessary parts
    stop_words = r'(UNRATED|Update|DLC|NSW|Unlocker|Trainer|MacOS|Linux|Repack|JPN|USA|EUR|-)'
    
    # Remove everything after the first occurrence of any stop word (including the dash)
    release_name = re.split(stop_words, release_name, 1)[0]

    # Replace underscores and dots with spaces
    game_name = release_name.replace('_', ' ').replace('.', ' ').strip()

    # Remove any extra spaces
    game_name = re.sub(r'\s+', ' ', game_name).strip()

    return game_name


def fetch_game_info(game_name, releasedir):
    """
    Fetch game information from IGDB API.
    
    Args:
        game_name (str): The name of the game to search for.
        releasedir (str): The release directory for logging and image processing.
        
    Returns:
        dict: A dictionary containing game details and images.
    """
    access_token = get_igdb_token()
    headers = {
        'Client-ID': igdb_client_id,
        'Authorization': f'Bearer {access_token}'
    }

    search_url = f"{IGDB_API_URL}/games"
    query = f'search "{game_name}"; fields name, summary, genres.name, cover.url, screenshots.url, first_release_date;'
    
    # Define temporary directory for this process
    tmp_dir = Path(config.get('Paths', 'TMP_DIR')) / str(os.getpid())
    tmp_dir.mkdir(parents=True, exist_ok=True)

    # Define the image subdirectory where images will be saved
    image_dir = tmp_dir / 'images'
    image_dir.mkdir(parents=True, exist_ok=True)

    log_file_path = tmp_dir / 'game_info.log'

    try:
        response = requests.post(search_url, headers=headers, data=query)
        if response.status_code == 200:
            game_data = response.json()
            if not game_data:
                log_to_file(log_file_path, f"Game not found for {game_name}")
                return None

            # Process the first result (most relevant)
            game = game_data[0]

            # Convert the release date from Unix time to a readable format
            release_date_unix = game.get('first_release_date', '')
            release_date = (
                datetime.fromtimestamp(release_date_unix, timezone.utc)
                .strftime('%d %B %Y')
                if release_date_unix
                else 'Unknown'
            )

            # Function to replace the default image size in the URL with `t_720p` and add https if missing
            def fix_url(url):
                url = re.sub(r't_thumb', 't_720p', url)  # Replace t_thumb with t_720p
                return url if url.startswith('http') else 'https:' + url  # Add https if missing

            # Game details
            game_info = {
                'game_name': game.get('name', ''),
                'summary': game.get('summary', ''),
                'release_date': release_date,  # Use the formatted release date
                'genres': [genre['name'] for genre in game.get('genres', [])],
                'cover_image': fix_url(game.get('cover', {}).get('url', '')),
                'screenshots': [fix_url(screenshot['url']) for screenshot in game.get('screenshots', [])],
                'images': []  # Placeholder for downloaded image paths
            }

            log_to_file(log_file_path, f"Game info fetched for {game_name}: {game_info}")

            # Download cover image and screenshots
            counter = 1

            if game_info['cover_image']:
                cover_image_path = download_image(game_info['cover_image'], f"{counter}-cover.jpg", image_dir, game_name)
                if cover_image_path:
                    game_info['images'].append(cover_image_path)
                counter += 1

            for screenshot_url in game_info['screenshots']:
                if counter > 4:  # Limit to 4 images (cover + screenshots)
                    break
                screenshot_path = download_image(screenshot_url, f"{counter}-screenshot.jpg", image_dir, game_name)
                if screenshot_path:
                    game_info['images'].append(screenshot_path)
                counter += 1

            return game_info
        else:
            log_to_file(log_file_path, f"Failed to fetch game info for {game_name}: {response.text}")
            return None
    except Exception as e:
        log_to_file(log_file_path, f"Error fetching game info for {game_name}: {str(e)}")
        return None

def download_image(image_url, filename, image_dir, game_name):
    """
    Download an image from the given URL and save it to the specified directory.
    
    Args:
        image_url (str): The URL of the image to download.
        filename (str): The name to save the image as.
        image_dir (Path): The directory to save the image.
        game_name (str): The name of the game for logging purposes.
        
    Returns:
        Path: The path to the saved image, or None if the download failed.
    """
    log_file_path = Path(config.get('Paths', 'TMP_DIR')) / str(os.getpid()) / 'game_info.log'
    try:
        response = requests.get(image_url)
        if response.status_code == 200:
            image_path = image_dir / filename
            with open(image_path, 'wb') as image_file:
                image_file.write(response.content)
            log_to_file(log_file_path, f"Downloaded {filename} for {game_name}")
            return image_path
        else:
            log_to_file(log_file_path, f"Failed to download image {filename} for {game_name}: {response.text}")
            return None
    except Exception as e:
        log_to_file(log_file_path, f"Error downloading image {filename} for {game_name}: {str(e)}")
        return None

# Main entry point
if __name__ == "__main__":
    # Parse the game release name as an argument
    parser = argparse.ArgumentParser(description="Process a game release name and fetch its information.")
    parser.add_argument("release_name", help="The game release name string")
    
    args = parser.parse_args()

    # Extract game name
    release_name = args.release_name
    game_name = extract_game_name(release_name)
    print(f"Extracted game name: {game_name}")

    # Fetch game info
    #game_info = fetch_game_info(game_name, None)
    #if game_info:
    #    print(f"Fetched game info: {game_info}")
    #else:
    #    print(f"Failed to fetch info for {game_name}")
