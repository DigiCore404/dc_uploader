#!/usr/bin/env bash
set -euo pipefail
prefix="${1:-$PWD/tools/archive}"
[[ "$prefix" == /* ]] || prefix="$PWD/$prefix"
for tool in curl sha256sum tar make c++ autoreconf; do
 command -v "$tool" >/dev/null || { echo "Missing archive build dependency: $tool (see README-docker.md)." >&2; exit 1; }
done
mkdir -p "$prefix"
work=$(mktemp -d "${TMPDIR:-/tmp}/digitalcore-archive-build.XXXXXXXX")
trap 'rm -rf -- "$work"' EXIT
rar2fs_url=https://codeload.github.com/hasse69/rar2fs/tar.gz/799ca3be1a2d9dcee416a650d737fb333fb4be5b
unrar_url=https://www.rarlab.com/rar/unrarsrc-7.3.1.tar.gz
curl --fail --location --retry 3 "$rar2fs_url" -o "$work/rar2fs.tar.gz"
curl --fail --location --retry 3 "$unrar_url" -o "$work/unrar.tar.gz"
printf '%s  %s\n' f5d82eb5d15210a0e751a99f3c8ebb7ca00c24df513bed4ae4c8735187f61c32 "$work/rar2fs.tar.gz" 634900842a3737d9cc15bbcc71d4c74cc713437e0bca296a573424fe5f2660ab "$work/unrar.tar.gz" | sha256sum --check --strict
mkdir "$work/rar2fs" "$work/unrar" "$work/unrar-cli"
tar -xzf "$work/rar2fs.tar.gz" -C "$work/rar2fs" --strip-components=1
tar -xzf "$work/unrar.tar.gz" -C "$work/unrar" --strip-components=1
tar -xzf "$work/unrar.tar.gz" -C "$work/unrar-cli" --strip-components=1
make -C "$work/unrar" -j "${BUILD_JOBS:-2}" lib
make -C "$work/unrar-cli" -j "${BUILD_JOBS:-2}" unrar
(cd "$work/rar2fs"; autoreconf -fi; ./configure --prefix="$prefix" --with-unrar="$work/unrar" --enable-static-unrar; make -j "${BUILD_JOBS:-2}"; make install)
install -m 755 "$work/unrar-cli/unrar" "$prefix/bin/unrar"
mkdir -p "$prefix/share/licenses/rar2fs" "$prefix/share/licenses/unrar" "$prefix/share/source"
cp "$work/rar2fs/COPYING" "$prefix/share/licenses/rar2fs/COPYING"
cp "$work/unrar/license.txt" "$prefix/share/licenses/unrar/license.txt"
cp "$work/rar2fs.tar.gz" "$work/unrar.tar.gz" "$prefix/share/source/"
cp "$0" "$prefix/share/source/build-archive-tools.sh"
"$prefix/bin/rar2fs" --version
printf '%s\n' 'rar2fs and UnRAR installed; host FUSE/device support is required to mount archives.'
