#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
umask 077
die() { printf '%s\n' "$*" >&2; exit 1; }
help() {
  echo 'Usage: bash setup.sh --media /srv/media [--state /srv/uploader] [--uid 1000 --gid 1000]'
  echo 'Options: --bind 127.0.0.1 --port 6080 --image IMAGE --no-start --unrar'
  echo 'Existing .env, login, config and history are preserved. See README.md.'
}
# Parse our simple dotenv format as data; never source or evaluate it.
read_env() {
  local key value
  while IFS='=' read -r key value || [[ -n "$key" ]]; do
    [[ "$key" =~ ^(IMAGE|MEDIA_DIR|STATE_DIR|PUID|PGID|BIND_IP|PORT|DIGITALCORE_WEB_PASSWORD|COMPOSE_FILE)$ ]] || continue
    value=${value%$'\r'}
    if [[ "$value" == \"*\" || "$value" == \'*\' ]]; then value=${value:1:${#value}-2}; fi
    printf -v "$key" '%s' "$value"
  done < .env
}
IMAGE=ghcr.io/digicore404/dc_uploader:edge
MEDIA_DIR= STATE_DIR="$PWD/workspace" PUID="$(id -u)" PGID="$(id -g)"
BIND_IP=127.0.0.1 PORT=6080 DIGITALCORE_WEB_PASSWORD= COMPOSE_FILE=compose.yaml
START=1 UNRAR=0 NEW=0
[[ ! -f .env ]] || read_env
while [[ $# -gt 0 ]]; do
  case "$1" in
    --help|-h) help; exit 0 ;;
    --no-start) START=0; shift ;;
    --unrar) UNRAR=1; shift ;;
    --media|--state|--uid|--gid|--bind|--port|--image)
      [[ $# -ge 2 ]] || die "$1 needs a value."
      case "$1" in
        --media) MEDIA_DIR=$2 ;; --state) STATE_DIR=$2 ;; --uid) PUID=$2 ;; --gid) PGID=$2 ;;
        --bind) BIND_IP=$2 ;; --port) PORT=$2 ;; --image) IMAGE=$2 ;;
      esac
      shift 2 ;;
    *) die "Unknown option: $1. Use --help." ;;
  esac
done
[[ "$(uname -s)" == Linux ]] || die 'Use this installer on your Linux server or Synology.'
[[ "$(uname -m)" == x86_64 ]] || die 'This complete image supports x86_64 / amd64 (including MTN).'
command -v docker >/dev/null || { [[ ! -x /usr/local/bin/docker ]] || export PATH="/usr/local/bin:$PATH"; }
command -v docker >/dev/null || die 'Install Docker with Compose v2 (Synology: Container Manager).'
docker compose version >/dev/null 2>&1 || die 'Docker Compose v2 is required.'
docker info >/dev/null 2>&1 || die 'Cannot access Docker. Use an account with Docker access.'
[[ "$PUID" =~ ^[0-9]+$ && "$PGID" =~ ^[0-9]+$ && "$PUID" != 0 ]] || die 'Choose an ordinary user UID/GID with --uid and --gid.'
[[ "$PORT" =~ ^[0-9]+$ ]] && (( 10#$PORT >= 1 && 10#$PORT <= 65535 )) || die 'Port must be 1–65535.'
[[ "$BIND_IP" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]] || die 'Use an IPv4 bind address, e.g. 127.0.0.1 or 0.0.0.0.'
[[ -d "$MEDIA_DIR" ]] || die 'Choose an existing media directory: --media /srv/media'
MEDIA_DIR=$(cd -- "$MEDIA_DIR" && pwd -P)
if [[ ! -d "$STATE_DIR" ]]; then
  mkdir -p -- "$STATE_DIR"
  if [[ $EUID -eq 0 ]]; then chown "$PUID:$PGID" "$STATE_DIR"; fi
fi
STATE_DIR=$(cd -- "$STATE_DIR" && pwd -P)
for value in "$MEDIA_DIR" "$STATE_DIR" "$IMAGE" "$DIGITALCORE_WEB_PASSWORD"; do
  [[ "$value" != *\'* && "$value" != *$'\n'* && "$value" != *$'\r'* ]] || die 'Values cannot contain single quotes or line breaks.'
done
for part in config data tmp logs watch mounts; do
  if [[ ! -d "$STATE_DIR/$part" ]]; then
    mkdir -- "$STATE_DIR/$part"
    chmod 700 "$STATE_DIR/$part"
    if [[ $EUID -eq 0 ]]; then chown "$PUID:$PGID" "$STATE_DIR/$part"; fi
  fi
done
if [[ $UNRAR -eq 1 || "$COMPOSE_FILE" == *compose.unrar.yaml* ]]; then
  [[ -c /dev/fuse ]] || die 'UNRAR mounts need host /dev/fuse. See README.md.'
  command -v findmnt >/dev/null || die 'Install findmnt to check FUSE mount sharing.'
  [[ "$(findmnt -n -o PROPAGATION --target "$STATE_DIR/mounts")" == *shared* ]] || die 'The mounts directory must be a shared host mount. See README.md before enabling UNRAR.'
  COMPOSE_FILE=compose.yaml:compose.unrar.yaml
  # fusermount needs a real passwd entry, including with an arbitrary Synology UID.
  # Mount only synthetic container identities, never the host passwd/group files.
  if [[ ! -d "$STATE_DIR/identity" ]]; then
    mkdir "$STATE_DIR/identity"
    if [[ $EUID -eq 0 ]]; then chown "$PUID:$PGID" "$STATE_DIR/identity"; fi
  fi
  identity_tmp=$(mktemp "$STATE_DIR/identity/passwd.XXXXXXXX")
  printf 'root:x:0:0:root:/root:/usr/sbin/nologin\nnobody:x:65534:65534:nobody:/nonexistent:/usr/sbin/nologin\n' > "$identity_tmp"
  if [[ "$PUID" != 65534 ]]; then printf 'digitalcore:x:%s:%s:DigitalCore:/app:/usr/sbin/nologin\n' "$PUID" "$PGID" >> "$identity_tmp"; fi
  chmod 644 "$identity_tmp"; mv "$identity_tmp" "$STATE_DIR/identity/passwd"
  identity_tmp=$(mktemp "$STATE_DIR/identity/group.XXXXXXXX")
  printf 'root:x:0:\nnogroup:x:65534:\n' > "$identity_tmp"
  if [[ "$PGID" != 0 && "$PGID" != 65534 ]]; then printf 'digitalcore:x:%s:\n' "$PGID" >> "$identity_tmp"; fi
  chmod 644 "$identity_tmp"; mv "$identity_tmp" "$STATE_DIR/identity/group"
fi
if [[ -z "$DIGITALCORE_WEB_PASSWORD" ]]; then
  DIGITALCORE_WEB_PASSWORD=$(od -An -N24 -tx1 /dev/urandom | tr -d ' \n')
  NEW=1
fi
[[ ${#DIGITALCORE_WEB_PASSWORD} -ge 12 ]] || die 'Choose a workspace password with at least 12 characters.'
env_tmp=$(mktemp .env.setup.XXXXXXXX)
trap 'rm -f -- "$env_tmp"' EXIT
for key in IMAGE MEDIA_DIR STATE_DIR PUID PGID BIND_IP PORT DIGITALCORE_WEB_PASSWORD COMPOSE_FILE; do
  printf "%s='%s'\n" "$key" "${!key}" >> "$env_tmp"
done
chmod 600 "$env_tmp"
mv -- "$env_tmp" .env
unset COMPOSE_FILE
docker compose --env-file .env config --quiet
if [[ $START -eq 1 ]]; then
  docker compose --env-file .env up -d --wait --wait-timeout 120
  address=$BIND_IP; [[ "$address" != 0.0.0.0 ]] || address='<server-ip>'
  printf 'Open http://%s:%s — username: admin\n' "$address" "$PORT"
else
  echo 'Installation prepared and Compose validated. Start with: docker compose up -d'
fi
if [[ $NEW -eq 1 ]]; then printf 'Initial password: %s\nStore it privately; change it in Workspace access.\n' "$DIGITALCORE_WEB_PASSWORD"; fi
echo 'The saved login and settings take precedence after first use. No uploads start during setup.'
