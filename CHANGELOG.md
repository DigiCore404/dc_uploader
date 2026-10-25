# Changelog

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
