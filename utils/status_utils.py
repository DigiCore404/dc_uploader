import os


def get_folder_timestamps(folder_path):
    """Retrieve the folder's access and modification times."""
    folder_stats = os.stat(folder_path)
    return folder_stats.st_atime, folder_stats.st_mtime

def set_folder_timestamps(folder_path, access_time, modification_time):
    """Set the folder's access and modification times."""
    os.utime(folder_path, (access_time, modification_time))

def create_status_folder(directory_path, status):
    """Create a status folder in the given directory without changing the parent folder's timestamps."""
    status_folder_path = os.path.join(directory_path, f'.{status}')

    # Get the folder's original timestamps
    access_time, modification_time = get_folder_timestamps(directory_path)

    # Create the status folder
    os.makedirs(status_folder_path, exist_ok=True)

    # Restore the original timestamps of the directory
    set_folder_timestamps(directory_path, access_time, modification_time)

def remove_status_folder(directory_path, status):
    """Remove the specified status folder from the given directory without changing the parent folder's timestamps."""
    status_folder_path = os.path.join(directory_path, f'.{status}')

    if os.path.isdir(status_folder_path):
        # Get the folder's original timestamps
        access_time, modification_time = get_folder_timestamps(directory_path)

        # Remove the status folder
        os.rmdir(status_folder_path)

        # Restore the original timestamps of the directory
        set_folder_timestamps(directory_path, access_time, modification_time)

def has_status(directory_path, status):
    """Check if a status folder exists in the given directory."""
    return os.path.isdir(os.path.join(directory_path, f'.{status}'))

def update_status(directory_path, new_status):
    """Update the status folders in the directory while preserving the parent folder's timestamps."""
    # Get the folder's original timestamps
    access_time, modification_time = get_folder_timestamps(directory_path)

    # Remove previous status folders
    for status in ['uploading', 'uploaded', 'dupe']:
        remove_status_folder(directory_path, status)
    
    # Create the new status folder
    create_status_folder(directory_path, new_status)

    # Restore the original timestamps of the directory
    set_folder_timestamps(directory_path, access_time, modification_time)
