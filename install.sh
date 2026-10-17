#!/bin/bash

print_help() {
    echo "Script usage: $script [OPTION]"
    echo
    echo "Optional arguments:"
    echo "    -d, --domain: Fully qualified domain name (e.g. hostname.domain.tld) you wish to use for the web app."
    echo
    echo "    -h, --help: Show this help page"
}

command_exists() {
    command -v "$1" &> /dev/null
}

set -e

# Pretty colors
red='\033[0;31m'
ncl='\033[0m'

# Set default values if not provided
HOSTNAME="$(hostname -f)"

script_path="$(readlink -f "${BASH_SOURCE[0]}")"
root_dir="${script_path%/*}"
script="${script_path##*/}"
script_dir="$root_dir/scripts"

cd "$root_dir" || exit 1

if [ $# -ne 0 ]; then
    if [ $# -gt 1 ]; then
        if [ "$1" != "-d" ] && [ "$1" != "--domain" ]; then
            echo -e "${red}ERROR: Too many args. The only argument this script takes is -d/--domain. See --help.${ncl}" >&2
            exit 1
        fi
    fi

    valid_args=("-h" "--help" "-d" "--domain")
    found=false

    for item in "${valid_args[@]}"; do
        if [[ "$item" == "$1" ]]; then
            found=true
            break
        fi
    done
    if ! $found; then
        echo -e "${red}Error: Unrecognized argument: $1${ncl}" >&2
        exit 1
    fi

    if ! opts=$(getopt -o 'hd:' -l 'help,domain:' -n "$script" -- "$@"); then
        echo -e "${red}ERROR: Failed to parse options. See --help.${ncl}" >&2
        exit 1
    fi
    # Reset the positional parameters to the parsed options
    eval set -- "$opts"
    # Process arguments
    while true; do
        case "$1" in
            -d | --domain)
                server_name="$2"
                shift 2
                ;;
            -h | --help)
                print_help
                exit 0
                ;;
            --)
                shift
                # No domain provided, use a default
                server_name=${server_name:-"$HOSTNAME"}
                break
                ;;
            *)
                echo -e "${red}Error: Unrecognized argument${ncl}" >&2
                print_help
                exit 1
                ;;
        esac
    done
fi

if [ "$EUID" -ne 0 ]; then
    echo -e "${red}Please run as root or with sudo.${ncl}" >&2
    exit 1
else
    stored_user="$(whoami)";
    if [ -z  "$stored_user" ]; then
        echo -e "${red}Couldn't determine current user, contact the devs.${ncl}" >&2
        exit 1
    fi
fi

. /etc/os-release

if [ "$ID" != "ubuntu" ] && [ "$ID" != "debian" ]; then
    echo -e "${red}ERROR: This program was only built for ubuntu/debian, aborting install.${ncl}" >&2
    exit 1
fi

if [ -z "$server_name" ]; then
    # Initiate server name to hostname in case user selects N. Needed for handle_reply.
    read -p \
    "Enter the fully qualified domain name for the self-signed certificate. Leave blank for default [default: $HOSTNAME] : " -r
    server_name=${REPLY:-"$HOSTNAME"}
fi

# Domain name validation if an actual domain is being used
if [ "$server_name" != "$HOSTNAME" ]; then
    if ! echo "$server_name" | grep -qE '^([a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z]{2,}$'; then
        echo -e "${red}Error: Invalid domain name provided${ncl}" >&2
        exit 1
    fi
fi

if ! command_exists mediainfo; then
    # If mediainfo isn't already installed, add the repo
    wget https://mediaarea.net/repo/deb/repo-mediaarea_1.0-25_all.deb
    dpkg -i repo-mediaarea_1.0-25_all.deb
    rm repo-mediaarea_1.0-25_all.deb
fi
# If mtn isn't already installed, add it.
if ! command_exists mtn; then
if [ "$ID" == "ubuntu" ]; then
    apt-get install -y software-properties-common
    
    # 1. Add the repo but tell it NOT to update yet (so it doesn't crash)
    add-apt-repository -y -n ppa:wahibre/mtn
    
    # 2. Force the update and tell apt "I don't care if the label changed, just do it"
    apt-get update --allow-releaseinfo-change
    
    # 3. Now install
    apt-get install -y mtn

    elif [ "$ID" == "debian" ]; then
        if [ "$VERSION_ID" -ge 9 ]; then
            if [ "$VERSION_ID" -eq 9 ]; then
                VERSION_ID="9.0"
            fi
            apt-get install -y gpg
            echo "deb http://download.opensuse.org/repositories/home:/movie_thumbnailer/Debian_$VERSION_ID/ /" | \
                sudo tee /etc/apt/sources.list.d/home:movie_thumbnailer.list
            curl -fsSL "https://download.opensuse.org/repositories/home:movie_thumbnailer/Debian_$VERSION_ID/Release.key" | \
                gpg --dearmor | sudo tee /etc/apt/trusted.gpg.d/home_movie_thumbnailer.gpg > /dev/null
        else
            echo -e "${red}$ID $VERSION_ID is not supported. Aborting.${ncl}" >&2
            exit 1
        fi
    fi
fi

apt-get update --allow-releaseinfo-change

# Required packages
echo "Installing required tools and their dependencies..."
apt-get install -y build-essential mtn mediainfo fuse3 libfuse-dev screen autoconf python3 python3-venv

# Install rar2fs
if [[ ! -f /usr/local/bin/rar2fs ]]; then
    echo "Installing rar2fs..."
    unrar_ver="7.1.6"
    rar2fs_ver="1.29.7"
    workdir="/tmp/rar2fs_installation"

    # Download rar2fs
    mkdir -p $workdir
    cd $workdir
    wget https://github.com/hasse69/rar2fs/archive/refs/tags/v$rar2fs_ver.tar.gz
    tar zxvf v$rar2fs_ver.tar.gz
    cd rar2fs-$rar2fs_ver

    # Download unrar inside rar2fs directory
    wget https://www.rarlab.com/rar/unrarsrc-$unrar_ver.tar.gz
    tar zxvf unrarsrc-$unrar_ver.tar.gz
    cd unrar

    # Install unrar libraries, that's all rar2fs needs
    echo "Compiling unrar..."
    make --silent lib && echo "Unrar library compiled, installing..."
    make install-lib && echo "Unrar library installed successfully"

    # Back to rar2fs root directory
    cd ..

    # Install rar2fs
    echo "Compiling rar2fs..."
    autoreconf -f -i
    ./configure && make --silent && echo "rar2fs compiled successfully, installing..."
    make install && echo "rar2fs installed successfully"

    sed -i 's/#user_allow_other/user_allow_other/g' /etc/fuse.conf
    cd "$root_dir" || exit 1
    rm -rf $workdir
fi

# Create venv
mkdir -p /venv
python3 -m venv /venv/dc_uploader

# Install Python packages
echo "Installing Python packages in virtual environment..."
"/venv/dc_uploader/bin/pip3" install --upgrade pip wheel
"/venv/dc_uploader/bin/pip3" install --upgrade -r "$root_dir/requirements.txt"

# Ensure scripts are executable
chmod +x "$script_dir/start.sh"
chmod +x "$script_dir/shutdown.sh"
chmod +x "$script_dir/upload.sh"
chmod +x "$script_dir/queue_upload.sh"
chmod +x "$root_dir/utils/config_validator.sh"

echo "Initiating polar bear attack (do you guys actually read these messages?)"

# Call the Python script with the function name as an argument
echo "Initializing databases..."
# Does not need virtual environment since it is touching stuff outside of virtual environment

if /venv/dc_uploader/bin/python3 "$root_dir/utils/database_utils.py" initialize_all_databases; then
    echo "Databases created successfully."
else
    echo -e "${red}Error: Couldn't initialize databases${ncl}" >&2
    exit 1
fi

# SSL setup
# Generate a self-signed certificate
echo "Generating self-signed certificate..."
mkdir -p "$root_dir/certificates"
openssl req -x509 -newkey ec -pkeyopt ec_paramgen_curve:secp384r1 -keyout "$root_dir/certificates/key.pem" \
-out "$root_dir/certificates/cert.pem" -days 3650 -nodes -subj "/CN=$server_name"

chown -R "$stored_user":"$stored_user" certificates

echo "Self-signed SSL certificate generation complete."
echo "Your Flask app will now run with HTTPS!"

# Update the config.ini file with user, password, and port
echo "Updating config.ini hostname..."
sed -i "s/^hostname = .*/hostname = $server_name/" "$root_dir/config.ini"

echo "Setup complete. Start web server by executing start.sh, and make your first upload with upload.sh!"
echo "Web app can be shutdown with shutdown.sh"
echo -e "${red}If you are exposing the web app to the wider Internet, update config.ini to a more secure" \
"username/password${ncl}"
echo "Note: web app does not need to be running to upload, its usage is entirely optional"
