#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
exec bash start.sh web --host 127.0.0.1 --port 6080 --no-browser "$@"
