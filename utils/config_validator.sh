#!/bin/bash

check() {
    for required_key in "${required_keys[@]}"; do
        if [[ "$1" == "$required_key" ]]; then
            if [ -z "$2" ]; then
                    # Empty
                case "$1" in
                    "APIKEY")
                        echo -e "${ylw}Warning${ncl}: TMDB API key not set, will not be able to get IMDB information"
                        ;;
                    "CLIENT_ID" | "CLIENT_SECRET")
                        echo -e "${ylw}Warning${ncl}: IGDB $1 not set, will not be able to get video game information"
                        ;;
                    *)
                        echo -e "${red}Fatal${ncl}: $1 must not be empty"
                        fatal_error=true
                esac
            else
                # Store arg2 for printing, since we might need to redact it during printing
                value="$2"
                if [ "$1" == "PASSWORD" ] || \
                [ "$1" == "CAPTCHA_PASSKEY" ] || \
                [ "$1" == "APIKEY" ] || \
                [ "$1" == "CLIENT_SECRET" ] || \
                [ "$1" == "ANNOUNCEURL" ] || \
                [ "$1" == "password" ]; then
                    value="[REDACTED]"
                fi
                case "$2" in
                    */)
                        # Trailing forward slash. Ignore if a password or URL, since those can contain trailing /
                        if [ "$1" != "password" ] && [ "$1" != "PASSWORD" ] && [ "$1" != "ANNOUNCEURL" ]; then
                            echo -e "${ylw}Warning in $1${ncl}: \"$value\" should not have a trailing forward slash" \
                            "'/', might introduce bugs."
                        fi
                        ;;
                    */[[:space:]]*)
                        # Trailing forward slash with trailing white spaces
                        if [ "$1" != "password" ] && [ "$1" != "PASSWORD" ] && [ "$1" != "ANNOUNCEURL" ]; then
                            echo -e "${ylw}Warning in $1${ncl}: \"$value\" should not have a trailing forward slash" \
                            "'/' with trailing whitespaces, might introduce bugs."
                        elif [[ "$2" == *[[:space:]] ]]; then
                            echo -e "${ylw}Warning in $1${ncl}: \"$value\" should not have a trailing whitespaces," \
                             "might introduce bugs."
                        fi
                        ;;
                    *[[:space:]])
                        # Trailing white spaces
                        echo -e "${ylw}Warning in $1${ncl}: \"$value\" should not have a trailing whitespaces, might" \
                        "introduce bugs."
                esac
            fi
        fi
    done
}

set -e

# Pretty colors
red='\033[0;31m'
grn='\033[1;32m'
ylw='\033[1;33m'
ncl='\033[0m'

full_script_name="$(readlink -f "${BASH_SOURCE[0]}")"
root_dir="${full_script_name%/*}/.."
# Keys that require user to set them
# Depends on who called it
if [ "$1" == "upload.sh" ]; then
    # upload.sh called, no need to validate [AUTH]
    required_keys=("DATADIR" "USERNAME" "PASSWORD" "LOGINTXT" "CAPTCHA_PASSKEY" "APIKEY" \
    "CLIENT_ID" "CLIENT_SECRET" "HASHER" "SOURCEFOLDER" "WATCHFOLDER" "SITEURL" "ANNOUNCEURL")
else
    # Anyone else called, or non-specified, validate all required
    required_keys=("user" "password" "port" "DATADIR" "USERNAME" "PASSWORD" "LOGINTXT" "CAPTCHA_PASSKEY" "APIKEY" \
    "CLIENT_ID" "CLIENT_SECRET" "HASHER" "SOURCEFOLDER" "WATCHFOLDER" "SITEURL" "ANNOUNCEURL")
fi
fatal_error=false
while IFS= read -r line; do
    # Skip lines without an '=' sign
    [[ "$line" != *=* ]] && continue

    # Split into key and value
    key="${line%%=*}"
    value="${line#*=}"

    # Trim whitespace on both sides
    key="$(echo "$key" | sed -E 's/^[[:space:]]+|[[:space:]]+$//g')"

    # Remove leading spaces, and any \r in case config.ini was edited on Windows
    value="$(echo "$value" \
    | sed -E 's/^[[:space:]]+//' \
    | tr -d '\r')"

    # Run the check
    check "$key" "$value"
done < "$root_dir/config.ini"

if $fatal_error; then
    exit 1
else
    echo -e "${grn}config.ini passed validation, no fatal errors${ncl}"
    exit 0
fi