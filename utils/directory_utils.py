import os
from pathlib import Path
from utils.config_loader import ConfigLoader

def create_process_directory(directory_name):
    """Create a process-specific directory using the process ID."""
    # Load configuration
    config = ConfigLoader().get_config()

    # Get the base temporary directory from the config
    temp_dir_base = Path(config.get('Paths', 'TMP_DIR'))
    
    # Get the process ID
    process_id = str(os.getpid())

    # Create the base tmp directory if it does not exist
    temp_dir_base.mkdir(parents=True, exist_ok=True)
    #print(f"\033[94mBase temporary directory ensured at: {temp_dir_base}\033[0m")
    
    # Create the process-specific directory
    temp_dir = temp_dir_base / process_id
    
    # Create the final directory for the given directory name
    final_dir = temp_dir / directory_name
    final_dir.mkdir(parents=True, exist_ok=True)
    print(f"\033[92mWorking directory created at: {final_dir}\033[0m")
    return final_dir

