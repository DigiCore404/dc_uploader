import json
import os
import re
from pathlib import Path

import requests
from guessit import guessit

from utils.bcolors import bcolors
from utils.config_loader import ConfigLoader

# Load configuration
config = ConfigLoader().get_config()

def contact_tmdb_api(url):
    response = requests.get(url)
    response.raise_for_status()  # Raise an exception for HTTP errors
    return response

def extract_media_details(directory_name, media_type):
    """Extract various media details from the directory name using guessit.
    Args:
        directory_name (str): The directory name to extract details from.
        media_type (str): The type of media ('movie' or 'tv').
        """
    if media_type == 'tv':
        # See if it's an episode or not via presence of SxxExx format.
        # Probably will fail to match for anime absolute numbering
        match = re.match(r'[S|s]\d{2}[E|e]\d{2}', directory_name)
        if match:
            tv_type = 'episode'
        else:
            tv_type = 'season'
        results = guessit(directory_name, options={"episode_prefer_number": True, "type": f"{tv_type}"})
    else:
        results = guessit(directory_name, options={"type": "movie"})

    return results

def fetch_imdb_id(tmdb_id, api_key, media_type):
    """Get the IMDb ID for a given movie or TV show ID from TMDb.
    Args:
        tmdb_id (str): The ID of the movie or TV show.
        api_key (str): The TMDb API key.
        media_type (str): The type of media ('movie' or 'tv').
        """
    tmdb_api_url = f"https://api.themoviedb.org/3/{media_type}/{tmdb_id}/external_ids?api_key={api_key}"

    try:
        response = contact_tmdb_api(tmdb_api_url)
    except requests.exceptions.RequestException as e:
        print(f"{bcolors.FAIL}Error fetching IMDb ID: {str(e)}\n{bcolors.ENDC}")
        return None

    data = response.json()

    print(f"Received data for IMDb ID: {json.dumps(data, indent=2)}")

    if 'imdb_id' in data and data['imdb_id']:
        return data['imdb_id']
    else:
        print(f"{bcolors.FAIL}No IMDb ID found for {media_type} ID: {tmdb_id}\n{bcolors.ENDC}")
        return None

def get_imdb_info(directory_name, media_type):
    """Get IMDb information based on directory name.
    Args:
        directory_name (str): Directory to get imdb ID for
        media_type (str): The type of media (movie or tv)
        """

    extracted_info = extract_media_details(directory_name, media_type)
    title = extracted_info.get('title')
    alt_title = extracted_info.get('alternative_title')
    year = extracted_info.get('year')
    season = extracted_info.get('season')
    episode = extracted_info.get('episode')

    
    if not title:
        # Can't continue. Just return none
        print(f"{bcolors.FAIL}Could not extract movie title from directory name.{bcolors.ENDC}")
        return None
    elif alt_title:
        # Title exists, but alt title is also present
        # Append alt title to title with a space so it can be searched with title
        title += f' {alt_title}'

    # Set to 'empty string' if not provided, title is the only one we MUST have
    if not year:
        year = ''
    if not season:
        season = ''
    if not episode:
        episode = ''

    print(f"{bcolors.YELLOW}Trying to extract IMDb info from title: {title}\n{bcolors.ENDC}")

    # Load API key from configuration
    api_key = config.get('TMDB', 'APIKEY')

    tmdb_api_url = "https://api.themoviedb.org/3/search/"

    if media_type == 'tv':
        tmdb_api_url += "tv?"
    else:
        tmdb_api_url += "movie?"

    tmdb_api_url += (f"api_key={api_key}"
                     f"&query={title}")

    if media_type == 'tv':
        tmdb_api_url += f"&first_air_date_year={year}"
    else:
        tmdb_api_url += f"&primary_release_year={year}"
    
    tmdb_api_url += f"&include_adult=true&language=en-US&page=1"

    try:
        response = contact_tmdb_api(tmdb_api_url)
    except requests.exceptions.RequestException as e:
        print(f"{bcolors.FAIL}Error fetching TMDb info: {str(e)}\n{bcolors.ENDC}")
        return None

    data = response.json()

    # Log the received data
    print(f"Received data: {json.dumps(data, indent=2)}")

    # Check if there are any results
    if 'results' in data and data['results']:
        print(f"{bcolors.OKGREEN}TMDb info fetched successfully.\n{bcolors.ENDC}")
        
        # Iterate over results
        for result in data['results']:
            tmdb_id = result['id']
            if media_type == 'tv':
                title = result['name']
                # TV show, has first_air_date
                year = result.get('first_air_date', '').split('-')[0]
            else:
                # Movie, has release_date
                title = result['title']
                year = result.get('release_date', '').split('-')[0]  # Extract only the year part

            print(f"Processing result: {title} ({year})")

            # Fetch IMDb ID for the show
            imdb_id = fetch_imdb_id(tmdb_id, api_key, media_type)
            
            if imdb_id:
                # Successfully got an imdb ID, return with dictionary of imdb ID, title and year
                print(f"{bcolors.OKGREEN}IMDb ID: {imdb_id} found.\n{bcolors.ENDC}")
                return {"id": imdb_id, "title": title, "year": year}

        print(f"{bcolors.FAIL}No matching IMDb results found.\n{bcolors.ENDC}")
    else:
        print(f"{bcolors.FAIL}No IMDb results found.\n{bcolors.ENDC}")
    
    # If we reach here, no results were found
    return None

def extract_imdb_link_from_nfo(directory):
    """Extract IMDb link from a .nfo file in the directory."""
    nfo_files = list(Path(directory).glob('*.nfo'))

    for nfo_file in nfo_files:
        nfo = os.path.basename(nfo_file)
        print(f"{bcolors.YELLOW}Trying to extract IMDb data from: {nfo}\n{bcolors.ENDC}")
        
        with open(nfo_file, 'r', encoding='utf-8', errors='ignore') as file:
            content = file.read()
            # Match various IMDb link formats, including HTTPS and non-www URLs
            match = re.search(r'https?://(?:www\.)?imdb\.com/title/tt\d+', content)
            if match:
                #print(f"{bcolors.OKGREEN}IMDb link found: {match.group(0)}\n{bcolors.ENDC}")
                return match.group(0)
    return None