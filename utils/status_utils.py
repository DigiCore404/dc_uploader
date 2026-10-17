import os

# The data directory can be a read-only mount (e.g. rar2fs or a bind mount of
# the site), where creating/removing the hidden status folders is impossible.
# Every operation here is therefore best-effort: on a read-only/permission
# error we warn once and carry on instead of crashing the upload.

_warned_readonly = False


def _warn_readonly(directory_path, exc):
    global _warned_readonly
    if not _warned_readonly:
        print(
            f"WARNING: cannot write status markers in {directory_path} ({exc}). "
            f"Filesystem is read-only or not permitted - continuing without status markers."
        )
        _warned_readonly = True


def get_folder_timestamps(folder_path):
    """Retrieve the folder's access and modification times."""
    folder_stats = os.stat(folder_path)
    return folder_stats.st_atime, folder_stats.st_mtime


def set_folder_timestamps(folder_path, access_time, modification_time):
    """Set the folder's access and modification times (best-effort)."""
    try:
        os.utime(folder_path, (access_time, modification_time))
    except OSError:
        pass


def create_status_folder(directory_path, status):
    """Create a status folder in the given directory without changing the parent folder's timestamps."""
    status_folder_path = os.path.join(directory_path, f'.{status}')

    # Get the folder's original timestamps
    access_time, modification_time = get_folder_timestamps(directory_path)

    try:
        os.makedirs(status_folder_path, exist_ok=True)
    except OSError as exc:
        _warn_readonly(directory_path, exc)
        return

    # Restore the original timestamps of the directory
    set_folder_timestamps(directory_path, access_time, modification_time)


def remove_status_folder(directory_path, status):
    """Remove the specified status folder from the given directory without changing the parent folder's timestamps."""
    status_folder_path = os.path.join(directory_path, f'.{status}')

    if os.path.isdir(status_folder_path):
        # Get the folder's original timestamps
        access_time, modification_time = get_folder_timestamps(directory_path)

        try:
            os.rmdir(status_folder_path)
        except OSError as exc:
            _warn_readonly(directory_path, exc)
            return

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
    for status in ['uploading', 'uploaded', 'dupe', 'failed']:
        remove_status_folder(directory_path, status)

    # Create the new status folder
    create_status_folder(directory_path, new_status)

    # Restore the original timestamps of the directory
    set_folder_timestamps(directory_path, access_time, modification_time)