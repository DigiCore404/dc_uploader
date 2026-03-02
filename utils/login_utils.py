import os
import warnings
from pathlib import Path
import requests

from utils.bcolors import bcolors
from utils.config_loader import ConfigLoader
from utils.logging_utils import log_to_file

# Suppress InsecureRequestWarning for self-signed certificates
warnings.simplefilter('ignore', requests.packages.urllib3.exceptions.InsecureRequestWarning)

# Load the configuration
config = ConfigLoader().get_config()

# Setup paths for logging
process_id = os.getpid()
TMP_DIR = Path(config.get('Paths', 'TMP_DIR')) / str(process_id)
TMP_DIR.mkdir(parents=True, exist_ok=True)

# API Credentials from config
SITEURL = config.get('Website', 'SITEURL')
API_KEY = config.get('Website', 'API_KEY')

def get_api_headers():
    """
    Constructs the authentication headers required by the API.
    Based on the documentation image provided by the user.
    """
    if not API_KEY:
        print(f"{bcolors.FAIL}[!] ERROR: API_KEY is missing in config.ini{bcolors.ENDC}")
        return None
    
    return {
        'X-API-KEY': API_KEY,
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) dc_uploader/1.0',
        'Accept': 'application/json'
    }

def login():
    """
    In the API model, 'login' validates the API key by attempting to reach
    the /api/v1/torrents endpoint.
    Returns: headers dict if successful, None if failed.
    """
    print(f"{bcolors.OKBLUE}[*] Validating API Key for {SITEURL}...{bcolors.ENDC}")
    
    headers = get_api_headers()
    if not headers:
        return None

    # Using the 'List Torrents' route from your screenshot to verify access
    test_url = f"{SITEURL}/api/v1/torrents"
    
    try:
        response = requests.get(test_url, headers=headers, verify=False, timeout=15)
        
        # Log details for debugging
        log_to_file(TMP_DIR / 'api_check.log', f"URL: {test_url}\nStatus: {response.status_code}\nResponse: {response.text[:500]}")

        if response.status_code == 200:
            print(f"{bcolors.OKGREEN}[+] API Authentication Successful!{bcolors.ENDC}")
            # Return the headers so other scripts can use them in their requests
            return headers
        elif response.status_code == 401 or response.status_code == 403:
            print(f"{bcolors.FAIL}[!] Login Failed: Invalid API Key (401/403).{bcolors.ENDC}")
        else:
            print(f"{bcolors.FAIL}[!] Login Failed: Server returned status {response.status_code}{bcolors.ENDC}")
            
        return None

    except requests.RequestException as e:
        log_to_file(TMP_DIR / 'api_error.log', f"Request failed: {str(e)}")
        print(f"{bcolors.FAIL}[!] Connection Error: {str(e)}{bcolors.ENDC}")
        return None
