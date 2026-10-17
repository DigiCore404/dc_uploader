import os

import rfr

from utils.art_utils import ascii_art_header
from utils.bcolors import bcolors


def add_fastresume(torrent_file, download_dir, output_file):
    """Add fast resume to the torrent using the rfr Python module."""
    #print(f"{bcolors.OKBLUE}Adding fast resume information...{bcolors.ENDC}")
    #print(f"Torrent file: {torrent_file}")
    #print(f"Download directory: {download_dir}")
    #print(f"Output file: {output_file}")
    print(ascii_art_header("Fastresume"))

    try:
        # Check if the torrent file exists
        if not os.path.isfile(torrent_file):
            raise FileNotFoundError(f"Torrent file not found: {torrent_file}")

        # Initialize FastTorrent object
        tor = rfr.FastTorrent(torrent_file, download_dir)
        
        # Generate fast resume data
        tor.do_resume()
        
        # Save the resulting torrent to a file
        tor.save_to_file(output_file)
        
        if os.path.exists(output_file):
            print(f"{bcolors.OKGREEN}Fast resume added successfully: {output_file}\n{bcolors.ENDC}")
        else:
            raise FileNotFoundError(f"Fast resume output file not found: {output_file}")
    except FileNotFoundError as e:
        print(f"{bcolors.FAIL}File not found: {str(e)}{bcolors.ENDC}")
    except Exception as e:
        print(f"{bcolors.FAIL}Failed to add fast resume: {str(e)}{bcolors.ENDC}")
