#!/usr/bin/env bash
set -euo pipefail
prefix="${1:?Choose an output directory}"
mkdir -p "$prefix/lib" "$prefix/source"
work=$(mktemp -d "${TMPDIR:-/tmp}/digitalcore-mtn.XXXXXXXX")
trap 'rm -rf -- "$work"' EXIT
url='https://gitlab.com/api/v4/projects/movie_thumbnailer%2Fmtn/repository/archive.tar.gz?sha=v3.5.0'
curl --fail --location --retry 3 "$url" -o "$work/mtn-3.5.0.tar.gz"
printf '%s  %s\n' c648bc132060cfd0a7c6279861b123942f29251fa9e4c00d89bebb438510da6a "$work/mtn-3.5.0.tar.gz" | sha256sum --check --strict
mkdir "$work/src"
tar -xzf "$work/mtn-3.5.0.tar.gz" -C "$work/src" --strip-components=1
# The uploader writes JPEG. Ubuntu 22.04's GD supports WebP but not AVIF.
make -C "$work/src/src" -j "${BUILD_JOBS:-2}" ENABLE_AVIF=0
install -m 755 "$work/src/bin/mtn" "$prefix/mtn.bin"
# Ship the matching media libraries; use the host's glibc, not a private loader.
# Ubuntu 22.04 is the build baseline (glibc 2.35).
ldd "$prefix/mtn.bin" | awk '/=> \// {print $3}' | while IFS= read -r library; do
  case "$(basename "$library")" in
    libc.so.*|libm.so.*|libpthread.so.*|libdl.so.*|librt.so.*|libresolv.so.*|libnss_*) continue ;;
  esac
  cp -L "$library" "$prefix/lib/$(basename "$library")"
done
cat > "$prefix/mtn" <<'WRAPPER'
#!/bin/sh
set -eu
directory=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
export LD_LIBRARY_PATH="$directory/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
exec "$directory/mtn.bin" "$@"
WRAPPER
chmod 755 "$prefix/mtn"
cp "$work/mtn-3.5.0.tar.gz" "$prefix/source/"
cp "$0" "$prefix/source/build-mtn.sh"
find "$work/src" -maxdepth 1 -iname '*copying*' -exec cp {} "$prefix/source/" \;
dpkg-query -W > "$prefix/source/build-packages.txt"
"$prefix/mtn" -h 2>&1 | head -4 || true
