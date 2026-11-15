# Changelog

## Protected DigitalCore cleanup - 2026-11-15

- Centralize active entry points under /uploaders/scripts with English documentation and Python-only administration. Keep the FTP hook path working and the archive manager unchanged.
- Replace the filesystem deletion loop with registered-torrent/mount protection and confirmed-missing link retention; prune LINKS events after three days.

- Add a dry-run-first qBittorrent cleaner and systemd timer for the two DigitalCore clients. Require a complete, readable payload, healthy tracker, at least three reported seeds, no downloaders, one hour of seeding and inactivity, and repeated eligibility before ratio/age cleanup. Payload deletion is never requested.
- Keep compact rotating logs with removal reasons and aggregate protection counts. Read credentials from the existing private configuration.
- Extend durable incoming upload links to TV and other release categories, including flattened FOREIGN archive layouts, without changing the archive mover.
- Add cleanup policy, last-moment peer changes, payload safety and TV archive regression coverage.

## Durable game upload links - 2026-11-12

- Maintain the box link watcher in scripts/link_watcher.py. Incoming game files use temporary hardlinks while fin-space moves the source, then switch to verified physical archive paths. Existing archive links resolve virtual aliases before linking.
- Refuse empty or unverifiable sources and retain launcher output for early failures.
- Cover archive moves, incomplete archives, old incoming links, alias removal and failed verification with regression tests.

## WEBFLAC category fix - 2026-10-29

- Recognize WEBFLAC as an explicit lossless music tag alongside FLAC. WEBFLAC albums now use Music/FLAC (23) instead of falling back to Movies/SD (2).
- Add regression cases for the 17 reported albums, mixed-case names and FLAC tag variants; retain existing movie, TV and full-disc classification.

## Category correction - 2026-10-25

- Keep 2160p UHD BluRay H265/x265 encodes in Movies/2160p (ID 4). The early UHD-disc rule no longer captures these encodes, including the reported Duplicity MALUS and B0MBARDiERS releases.
- Preserve explicit COMPLETE/BDMV/ISO/BD50/BD66/BD100 disc releases, HEVC-only disc recognition, remuxes, TV categories and manual overrides. Filename-only matching still depends on the supplied release tags.

## Unreleased · 2026-10-23

- Require separate FLAC and EPUB tags so titles containing Flack or Republic are not sent to Unknown.
- Recognize hyphenated Blu-ray UHD disc names, Dolby Vision Toolset releases and movie remuxes without a resolution tag.
- Cover the reported Unknown releases with regression checks; preserve real FLAC/EPUB categories and existing TV, film, software and console rules.

## Previous changes Â· 2026-10-22

- Match explicit console and Mac game platforms before sports and video rules.
- Recognize Formula/Formule 1, Formula E, additional sports formats, ROM collections, tutorials, audio libraries and Mac application tags with underscores.
- Match release-group suffixes exactly and require separate XXX markers, preserving film titles such as MaXXXine.
- Respect explicit video resolutions for BDRip/DVDRip releases and recognize TV DVD season discs.
- Keep explicitly named audiobooks in their configured category when their folders contain MP3 files.
- Add 48 category regression checks. Rules use JSON order because this legacy matcher does not implement the priority fields. Filename-only matching cannot resolve ambiguous titles or contradictory historical category assignments.
