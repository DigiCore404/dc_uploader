# Install with Docker

The complete Linux amd64 image contains Python, FFmpeg, MediaInfo, mkbrr, MTN, UnRAR and rar2fs.

Download and extract the Docker installer from this repository's Releases page.
Run on your server, choosing your existing media library:

```bash
bash setup.sh --media /srv/media --bind 0.0.0.0
```

The installer creates a private `.env`, generates a unique initial password,
prepares writable application folders and starts the service. Open
`http://<server-ip>:6080`, username `admin`, and use the printed password.
Change your login in **Settings → Workspace access**. Without `--bind`, access
is restricted to the server itself (`127.0.0.1`). Use an SSH tunnel or your HTTPS
reverse proxy for access over the internet.

Run as a normal Docker-capable user. When installing as root, specify the user
that should own the application files, e.g. `--uid 1000 --gid 1000`. Use your own
account's numeric IDs (`id`). The installer never changes media ownership.
Synology requires Container Manager and an x86_64 NAS; run the script over SSH,
or import these same Compose files and the generated `.env` as a project.

## Which settings go where?

| Location | Purpose |
| --- | --- |
| `.env` | Image, host media/state folders, UID/GID, listening IP/port and initial login |
| Web Settings | DigitalCore key/announce, screenshots/MTN, images, metadata, upload options |
| Web Clients | qBittorrent connections and paths visible to those clients |
| Web Workspace access | Active username/password and allowed IPs; stored in private SQLite |
| `workspace/config/config.ini` | Same uploader settings as the browser and CLI; advanced/manual editing |

An existing login is preserved on restart. Editing the initial password in `.env`
does not replace a login already saved in SQLite. Rerunning setup preserves it.
`config.ini`, `.env`, databases, browser sessions and logs are private: do not share
them or commit them. Back up the state directory privately.

## Storage and qBittorrent

The uploader sees `MEDIA_DIR` at `/content`, read-only. Your download client needs
write access to that same physical storage through its own mount. For example:

| Component | Path |
| --- | --- |
| Host media directory | `/srv/media` |
| Uploader | `/content` |
| qBittorrent | `/downloads` |

Configure import roots `/content` and `/downloads` in Settings. A new import is
placed directly at `/content/My.Release` (qBittorrent: `/downloads/My.Release`).
Existing target folders generate an error. No extra import or hash folder is made.
Use the built-in path check before publishing. A remote client requires shared
storage; an API connection alone does not make its files readable on this server.

All runtime files live below `STATE_DIR`: `config`, `data` (SQLite and exported
source torrents), `tmp`, `logs`, `watch` and `mounts`. Keep these directories private.
Completed torrents appear in `/watch`; alternatively use the configured client
delivery. Manuals in the app explain qBittorrent's completed-download command.

## UNRAR / rar2fs (optional)

The image includes both tools, but archive mounting additionally requires the
host's FUSE support. This feature is off by default. Extracted media can always be
uploaded normally. To mount RAR files without extracting them:

1. Ensure the host supplies `/dev/fuse` and FUSE user mounts. Your provider may need
   to enable this. Some NAS configurations do not support mount propagation.
2. An administrator must prepare `STATE_DIR/mounts` as a **shared host mount**.
   For a dedicated application directory, the Linux commands are
   `mount --bind /srv/uploader/mounts /srv/uploader/mounts` then
   `mount --make-rshared /srv/uploader/mounts`. Replace the path with your chosen
   state directory. Configure persistence with your host's mount manager.
3. Run `bash setup.sh --unrar`. This adds only the optional FUSE device,
   `SYS_ADMIN`, AppArmor allowance and shared propagation from `compose.unrar.yaml`.
   The script checks prerequisites; it never changes host mount propagation itself.
   It also creates synthetic container passwd/group files for your numeric UID/GID;
   your host's user database is never mounted into the container.
4. Enable **Settings → Archive handling (UNRAR / rar2fs)**. `/mounts` is the
   container's archive mount root; it is separate from read-only `/content`.
5. A qBittorrent container seeding the unpacked view must also mount the host's
   `STATE_DIR/mounts`, e.g. at `/unpacked`, with `rslave` propagation. Configure
   its save path to that unpacked release, or watch folder delivery accordingly.
   Keep the uploader/FUSE mounts running for as long as that data is being seeded.

Ordinary media imports edit the original torrent using mkbrr and keep its piece
hashes. Unpacked RAR contents differ from the original archive payload, so they
require a new torrent and hashing. Archive-to-unpacked seeding needs the additional
path mapping above; the ordinary import-root mapping is not sufficient.

## Update, logs and CLI

```bash
bash setup.sh --image ghcr.io/digicore404/dc_uploader:edge --no-start
docker compose pull
docker compose up -d --wait
docker compose logs --tail 100 uploader
docker compose run --rm uploader check
docker compose run --rm uploader upload /content/My.Release --cat 6 --dry-run
```

Set `IMAGE` in `.env` to a specific release tag for repeatable installs. Updates
preserve mounted configuration and history. `latest` tracks stable releases;
`edge` tracks main. Build locally with
`docker build --target release -t dc_uploader:local .`, then select that
image with `setup.sh --image dc_uploader:local --media /srv/media`.

Third-party tool licences are retained in `bin`. The image includes the rar2fs
and UnRAR licences, corresponding source archives and build script under
`/usr/local/share/digitalcore-archive`. Source versions and SHA-256 checks are
pinned in `scripts/build-archive-tools.sh`.
