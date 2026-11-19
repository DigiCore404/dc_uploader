BOX2 MAINTENANCE SCRIPTS
=======================
Central directory: /uploaders/scripts
Check current services and screens: ./status.py
One-shot cleanup services are normally inactive between timer runs.

AUTOMATIC SYSTEMD JOBS - DO NOT ALSO START THESE IN A SCREEN
link.py
  Receives complete-release events, maintains durable links and starts uploaders.
  Service: uploader-link-watcher.service
  Archive copies are rebound per file. A changed or missing NFO, sample or
  other file cannot prevent matching payload files from being rebound.
  Files completed after the initial snapshot are compared byte for byte.
  Unmatched originals are retained; archive maintenance does not block new
  completion records. Missing archives may have been intentionally removed
  for space and are not recreated by this service.
safe_qbit_cleanup.py
  Completed uploader torrents are checked every minute.
  Remove at ratio >= 2 OR at least two other tracker seeders, without an
  additional age, idle-time or seeding-time delay. Three reported seeders
  include this box and guarantee at least two others.
  Incomplete downloads and active publication jobs remain protected.
  Removes torrent registrations only; never requests payload deletion.
  Timer: digitalcore-qbit-cleanup.timer
  Configuration: /etc/digitalcore-qbit-cleanup.json
  Log: /var/log/digitalcore-qbit-cleanup/cleanup.log (bounded rotation)
  The matching systemd drop-ins are under box/systemd in the source repository.
  Install them under /etc/systemd/system, then reload systemd and restart the timer.
safe_storage_cleanup.py
  Protected inactive-link and old-event cleanup every ten minutes.
  Timer: uploader-storage-cleanup.timer
  Registered torrents, running uploads and mounts are protected.
  Regular payload and archive files are never deleted.
  Broken payload links must be seven days old and observed missing for a full day.
  Event marker files in /uploaders/LINKS are removed after three days.
uploader_aux_maintenance.py
  Existing DirtyBytes retention and the four UNRAR mount-cleanup helpers.
  Service: uploader-aux-maintenance.service

EXISTING ARCHIVE MANAGER - UNCHANGED
fin-space.py
  The original archive manager remains in its existing fin-space screen.
  If stopped, use ./fin-space.py to preserve its original working directory.

MANUAL HELPER
cleanup-stale-archive-mounts.py
  Existing standalone stale archive-mount cleanup; not newly scheduled.

WEAVEFTPD HOOK
on_complete_links.py
  Invoked by WeaveFTPd, not manually. Writes /uploaders/LINKS events for link.py.
  /uploaders/on_complete_links.py remains a compatibility link because the FTP
  configuration already calls that path.

Uploader application code and per-uploader helper modules remain in their own
application directories; they are not duplicated here. The archive manager and
its configuration are unchanged. Removed legacy launchers are backed up under
/var/backups/dc-qbit-cleanup-20261115.
The durable_game_links.json and symlink_* logs under /uploaders are active
runtime state/logs, not obsolete scripts.
