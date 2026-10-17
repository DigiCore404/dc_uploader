# DigitalCore Uploader

DigitalCore's self-hosted browser and CLI uploader. This modernizes the original
DC Uploader while retaining its repository history and license. The shared upload
engine and workspace workflow are adapted from the DirtyBytes uploader.

## Docker / Synology

Requires Linux x86_64 and Docker Compose v2 (Synology Container Manager).

```bash
git clone https://github.com/DigiCore404/dc_uploader.git
cd dc_uploader
docker build --target release -t dc-uploader:local .
bash setup.sh --media /srv/media --image dc-uploader:local --bind 0.0.0.0
```

When running as root, pass your ordinary account's `--uid` and `--gid`.
The installer generates a private workspace password and preserves existing login
and settings on subsequent starts. Open `http://<server-ip>:6080`.
For access from outside your LAN use an HTTPS reverse proxy or SSH tunnel.
See [Docker storage and installation](README-docker.md).

## Features

* DigitalCore Nexus dark blue theme and the existing DigitalCore wordmark.
* Release-folder browsing, review before upload, full activity history and logs.
* All 42 DigitalCore categories; a manual selection always takes precedence.
  Ambiguous automatic classifications stop for review.
* TMDB movie/TV, IGDB game and key-free TVmaze series search, preview and cover selection, IMDb from NFO or
  manual input, and Steam/GOG/IGDB game-page links. IGDB uses server-side Twitch application credentials.
* mkbrr creation and source-torrent editing, preserving piece hashes on imports.
* MTN/FFmpeg screenshots, MediaInfo, NFO, and configurable image hosting.
* qBittorrent connections, paths check, import queue and seeding status.
* Persistent private SQLite state, authentication, CSRF and workspace access rules.
* CLI uploads, completed-download callbacks and optional incoming-folder watcher.
* Optional archive mounting requires the separate FUSE Compose override.

Native binaries and media libraries are installed into the image, not committed
to this repository. mkbrr is downloaded from its official pinned release and
verified against the binary SHA-256 manifest; MTN/rar2fs are built from pinned sources.

## First run

1. Open Settings and add your DigitalCore API key and full personal announce URL.
2. Use DigitalCore’s own image host (default, no separate key) and optionally configure TMDB/IGDB credentials. TVmaze needs no key. Secrets stay on
   your server; blank secret fields preserve their existing values.
3. Mount your media read-only at `/content` and configure the qBittorrent path
   mapping in Clients. The media must exist on shared storage, not just remotely
   in a client's API.
4. Choose a release, category and details, then review before publishing.
5. Seed the confirmed torrent downloaded from DigitalCore. New uploads deliver
   to `/watch`; scheduled imports use their selected qBittorrent client.

An API key is never needed to test the UI, health check or isolated regression
suite. Do not put production credentials in this repository.

```bash
# Full test image with disposable runtime folders
docker build --target test -t dc-uploader:test .
docker run --rm --tmpfs /app/data --tmpfs /app/tmp --entrypoint python dc-uploader:test -m unittest discover -s tests -v
```

The `main` workflow tests the source before publishing the `edge` image to
`ghcr.io/digicore404/dc_uploader:edge`. Stable tags produce versioned releases.
