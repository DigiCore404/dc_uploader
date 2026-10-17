# DigitalCore Uploader

Upload to DigitalCore through your browser or the command line. Includes metadata
lookup with TMDB, TVmaze and IGDB, automatic screenshots, MediaInfo, DigitalCore's
image host and qBittorrent integration.

## Install

For Docker or Synology, you need Linux x86_64 and Docker Compose (Container Manager
on Synology).

```bash
git clone https://github.com/DigiCore404/dc_uploader.git
cd dc_uploader
docker build --target release -t dc-uploader:local .
bash setup.sh --media /srv/media --image dc-uploader:local --bind 0.0.0.0
```

Replace `/srv/media` with your media folder. When running as root, also pass your
normal account's `--uid` and `--gid`.

Open `http://<server-ip>:6080` and sign in with the login shown during installation.

[Docker and Synology setup](README-docker.md) · [Linux seedbox setup](SEEDBOX-INSTALL.md)

## Upload

1. Add your DigitalCore API key and personal announce URL in **Settings**.
2. Connect qBittorrent in **Clients** if you want to import torrents.
3. Choose **New upload** or **Import torrent**, fill in the release details and review before uploading.

DigitalCore's image host is ready to use. TVmaze works without a key; TMDB and
IGDB can be enabled in Settings with your own credentials.

Keep seeding after uploading. Folder and seeding instructions are available in
**Manuals** inside the uploader.

## Command line

The CLI, download-completion callback and folder watcher remain available.
For a native Linux installation, upload a release with:

```bash
bash start.sh upload /srv/media/My.Release --cat 6
```

Browser and CLI uploads appear together in **Activity**, with their progress,
logs and results. Use the same installation and persistent data folder.
