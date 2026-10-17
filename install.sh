#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
umask 077
SYSTEM_DEPS=0
SERVICE=0
ARCHIVE=0
PYTHON=python3
while [[ $# -gt 0 ]]; do
  case "$1" in
    --help|-h)
      echo 'Usage: bash install.sh [--python /path/to/python3] [--system-deps] [--service] [--rar2fs]'
      echo 'Default: install into .venv as your ordinary seedbox user; preserve config.ini.'
      echo '--system-deps: explicitly install Debian/Ubuntu Python and media tools using sudo/root.'
      echo '--service: optionally install/start a systemd user service on 127.0.0.1:6080.'
      echo '--rar2fs: compile pinned rar2fs and UnRAR sources into tools/archive (requires host FUSE).'
      exit 0 ;;
    --system-deps) SYSTEM_DEPS=1; shift ;;
    --service) SERVICE=1; shift ;;
    --rar2fs) ARCHIVE=1; shift ;;
    --python) [[ $# -ge 2 ]] || { echo '--python needs an executable path.' >&2; exit 2; }; PYTHON="$2"; shift 2 ;;
    *) echo "Unknown option: $1. Use --help." >&2; exit 2 ;;
  esac
done
if [[ $SERVICE -eq 1 ]]; then
  [[ $EUID -ne 0 ]] || { echo 'Install a user service as your seedbox user, without sudo.' >&2; exit 1; }
  command -v systemctl >/dev/null || { echo 'This host has no systemd. Omit --service and use your provider process manager.' >&2; exit 1; }
fi
if [[ $SYSTEM_DEPS -eq 1 ]]; then
  command -v apt-get >/dev/null || { echo 'Install Python 3.10+, venv, FFmpeg and MediaInfo with your package manager.'; exit 1; }
  SUDO=()
  if [[ $EUID -ne 0 ]]; then SUDO=(sudo); fi
  "${SUDO[@]}" apt-get update
  "${SUDO[@]}" apt-get install -y python3 python3-venv ffmpeg mediainfo
  if [[ $ARCHIVE -eq 1 ]]; then "${SUDO[@]}" apt-get install -y build-essential autoconf automake libtool pkg-config libfuse-dev fuse curl ca-certificates; fi
fi
command -v "$PYTHON" >/dev/null || { echo 'Python is missing. Use --python /path/to/python3 or ask your provider for Python 3.10+.' >&2; exit 1; }
"$PYTHON" -c 'import sys; assert sys.version_info >= (3,10), "Python 3.10+ is required"'
if [[ ! -e .venv ]]; then
  "$PYTHON" -m venv .venv || { echo 'Cannot create .venv. Ask your provider for python3-venv, or use --system-deps on your own Debian/Ubuntu server.' >&2; exit 1; }
fi
[[ -x .venv/bin/python ]] || { echo 'The existing .venv is not a working Linux environment. Move it aside and rerun this installer.' >&2; exit 1; }
.venv/bin/python -m pip install --disable-pip-version-check -r requirements.lock.txt
.venv/bin/python -m pip check
.venv/bin/python scripts/install-mkbrr.py
if [[ $ARCHIVE -eq 1 ]]; then bash scripts/build-archive-tools.sh "$PWD/tools/archive"; fi
if [[ ! -e config.ini ]]; then
  (umask 077; cp config.example.ini config.ini)
fi
chmod 600 config.ini
mkdir -p content/incoming watch tmp data logs
chmod +x start.sh scripts/upload.sh
if [[ -f start-web.sh ]]; then chmod +x start-web.sh; fi
if [[ -d bin ]]; then find bin -type f \( -name mkbrr -o -name mtn -o -name mtn.bin \) -exec chmod u+x {} +; fi
echo 'Python environment installed. Existing config.ini and application data were preserved.'
MISSING=0
for tool in ffmpeg ffprobe mediainfo; do
  if ! command -v "$tool" >/dev/null; then echo "Missing: $tool. Ask your provider to install it, or use --system-deps on your own server."; MISSING=1; fi
done
if [[ $MISSING -ne 0 ]]; then
  echo 'Installation needs media tools before the default upload configuration is ready. Rerun after installing them.' >&2
  exit 1
fi
echo 'Next: edit config.ini (DigitalCore API_KEY, ANNOUNCEURL and your media/watch paths).'
echo 'Check: bash start.sh check'
echo 'Browser service: bash start.sh web --host 127.0.0.1 --port 6080 --no-browser'
echo 'PC access: ssh -L 6080:127.0.0.1:6080 your-user@seedbox.example.com'
echo 'Then open http://127.0.0.1:6080 on your PC. No uploads start during installation.'
if [[ $SERVICE -eq 1 ]]; then .venv/bin/python scripts/service.py install --host 127.0.0.1 --port 6080; fi
