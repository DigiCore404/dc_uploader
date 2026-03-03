#!/bin/bash

print_help() {
    echo "Script usage: $script \"folder/to/be/uploaded\" [OPTION]"
    echo "Directory name or full absolute directory path should be in double quotes, as shown above, to avoid" \
    "unexpected behavior."
    echo
    echo "Optional arguments:"
    echo "    -l, --ln: Hardlinks provided directory to DATADIR. If hardlink fails, fallback to symlink."
    echo
    echo "    -c, --cp: Copies provided directory to DATADIR."
    echo
    echo "    -cat, --cat: Use a category number here to override the filters.json."
    echo
    echo "    -m, --mv: Moves provided directory to DATADIR. May break other torrents that rely on the same data," \
    "use with caution."
    echo
    echo "    -h, --help: Show this help page."
}

script_path="$(readlink -f "${BASH_SOURCE[0]}")"
script_dir="${script_path%/*}"
script="${script_path##*/}"
root_dir="$script_dir/.."

# Use script path to get the parent directory - AKA dc_uploader
cd "$root_dir" || exit 1

# Pretty colors
red='\033[0;31m'
ylw='\033[1;33m'
ncl='\033[0m'

data_dir="$(awk -F '=' '/^DATADIR[[:space:]]*=/ {gsub(/^[[:space:]]+|[[:space:]]+$/, "", $2); print $2}' "$root_dir/config.ini")"
# In case user put in a trailing forward slash to DATADIR
data_dir=$(realpath -s "$data_dir")
watch_dir="$(awk -F '=' '/^WATCHFOLDER[[:space:]]*=/ {gsub(/^[[:space:]]+|[[:space:]]+$/, "", $2); print $2}' "$root_dir/config.ini")"
# In case user put in a trailing forward slash to DATADIR
watch_dir=$(realpath -s "$watch_dir")
data_path="$1"

# Initialize variables
LN=false
CP=false
MV=false
CAT_ID=""

# Initial argument check
if [ $# -eq 0 ] || [[ "$data_path" == "--help" ]] || [[ "$data_path" == "-h" ]]; then
    # No arguments provided or first argument was help, just print help
    print_help
    exit 0
fi

if [ $# -gt 2 ]; then
    echo -e "${red}ERROR: Too many arguments provided${ncl}" >&2
    exit 1
fi

if [ $# -gt 1 ]; then
    # Only bother parsing args if an arg beside path is specified
    if [ $# -gt 2 ]; then
        echo -e "${red}ERROR: Too many args.${ncl}" >&2
        exit 1
    fi

    valid_args=("-h" "--help" "-l" "--ln" "-c" "--cp" "-m" "--mv" "-cat" "--cat")
    found=false

    for item in "${valid_args[@]}"; do
        if [[ "$item" == "$2" ]]; then
            found=true
            break
        fi
    done
    if ! $found; then
        echo -e "${red}Error: Unrecognized argument: $2${ncl}" >&2
        exit 1
    fi

    if ! opts=$(getopt -o 'hlcm' -l 'help,ln,cp,mv,cat:' -n "$script" -- "$@"); then
        echo -e "${red}ERROR: Failed to parse options. See --help.${ncl}" >&2
        exit 1
    fi
    # Reset the positional parameters to the parsed options
    eval set -- "$opts"

    # Process arguments
    while true; do
        case "$1" in
            -l | --ln)
                LN=true
                shift
                ;;
            -c | --cp)
                CP=true
                shift
                ;;
            -m | --mv)
                MV=true
                shift
                ;;
            -cat | --cat)
                CAT_ID="$2"
                shift 2
                ;;
            -h | --help)
                print_help
                exit 0
                ;;
            --)
                shift
                break
                ;;
            *)
                echo -e "${red}ERROR: Unrecognized argument${ncl}" >&2
                echo "This might have been caused by the provided path containing special characters. See --help."
                exit 1
                ;;
        esac
    done
fi

# Only continue if config validator returns on fatal errors
if ! "$root_dir/utils/config_validator.sh" "upload.sh"; then
    exit 1
fi

# RW permissions check
if ! touch "$data_dir/perm.test"; then
    echo -e "${red}ERROR: User running script does not have write permissions to $data_dir${ncl}" >&2
    exit 1
else
    # Cleanup
    rm "$data_dir/perm.test"
fi

if ! touch "$watch_dir/perm.test"; then
    echo -e "${red}ERROR: User running script does not have write permissions to $watch_dir${ncl}" >&2
    exit 1
else
    # Cleanup
    rm "$watch_dir/perm.test"
fi

# Validate path input
if [[ "$data_path" != *"/"* ]]; then
    # A directory name was provided, assume rest of path is in DATADIR
    data_path="$data_dir/$data_path"
elif [[ "$data_path" == /* ]]; then
    : # Don't do anything, already a full path. This is just to gate the else.
else
    echo -e "${red}ERROR: only absolute paths or directory names are allowed: $data_path${ncl}" >&2
    exit 1
fi

data_path=$(realpath -s "$data_path")
if [ -f "$data_path" ]; then
    # It's a file. Error.
    echo -e "${red}ERROR: $data_path is a file${ncl}" >&2
    exit 1
elif ! [ -d "$data_path" ]; then
    # There's no directory in DATADIR actually called what user provided
    # No need to check for file not existing, since if it was a file, above if catches
    # If file doesn't exist, -d will fail anyhow and thus be caught here.
    echo -e "${red}ERROR: $data_path does not exist${ncl}" >&2
    exit 1
fi

uploaded_directory="$(basename "$data_path")"
if ! [ -d "$data_dir/$uploaded_directory" ]; then
    # If this path doesn't already exit in DATADIR, it would have been already caught.
    # So this check functions as check for when users provide a path to data that is OUTSIDE of the DATADIR that doesn't
    # exist in DATADIR. If the data is already in DATADIR, no need to do anything

    # Data not already in DATADIR, clone/move it with the option provided
    echo "$uploaded_directory not already in $data_dir, creating it with specified option"
    if $LN; then
        # Hardlink, fallback to symlink otherwise
        if ! cp -alv --strip-trailing-slashes -t "$data_dir" "$data_path"; then
            echo -e "${ylw}Could not hardlink, falling back to symlink${ncl}"
            ln -sv -t "$data_dir" "$data_path" || exit 1
        fi
    elif $CP; then
        # Copy
        cp -av --strip-trailing-slashes -t "$data_dir" "$data_path" || exit 1
    elif $MV; then
        mv -v --strip-trailing-slashes -t "$data_dir" "$data_path" || exit 1
    else
        echo -e "${red}ERROR: Cannot create data to upload. When providing a path that is outside of DATADIR," \
        "move, copy, or link MUST be specified.${ncl}" >&2
    fi
fi

# Run using venv
source "/venv/dc_uploader/bin/activate"

# Build python command with optional category override
python_args=("$root_dir/backend.py" "$uploaded_directory")
if [ -n "$CAT_ID" ]; then
    python_args+=("-cat" "$CAT_ID")
fi

if python3 "${python_args[@]}"; then
    exit 0;
else
    exit 1;
fi
