#!/bin/bash

set -e

script_path="$(readlink -f "${BASH_SOURCE[0]}")"
script_dir="${script_path%/*}"
script="${script_path##*/}"
root_dir="$script_dir/.."

cd "$root_dir" || exit 1

log_file="$root_dir/files/webapp.log"
red='\033[0;31m'
ncl='\033[0m' # No color
ylw='\033[0;33m'

if "$root_dir/utils/config_validator.sh" "start.sh"; then
    # Only start if config validator returns on fatal errors
    echo "Starting web app with detached screen named \"dc-uploader\""
    if [ -f "$log_file" ]; then
        # Check if the log file size is 2MB or greater
        max_size=$((2 * 1024 * 1024)) # 2MB in bytes
        current_size=$(stat -c%s "$log_file")
        if [ "$current_size" -ge "$max_size" ]; then
            # Rotate the log file
            mv "$log_file" "$log_file.old"
        fi
    fi
    if screen -list | grep -q "dc-uploader"; then
        # If the screen session already exists, echo a warning
        echo -e "${ylw}Warning: Screen session named dc-uploader already exists. Please kill it before starting a" \
        "new one.${ncl}" >&2
    else
        screen -dm -L -Logfile "$log_file" -S dc-uploader "/venv/dc_uploader/bin/python3" app.py
        # Check if the screen session was created successfully
        if screen -list | grep -q "dc-uploader"; then
            echo "Web app started successfully in detached screen session named dc-uploader."
            exit 0
        else
            echo -e "${red}Error: Failed to start web app, please check the logs${ncl}" >&2
            exit 1
        fi
    fi
fi