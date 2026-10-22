# Changelog

## Unreleased · 2026-10-22

- Match explicit console and Mac game platforms before sports and video rules.
- Recognize Formula/Formule 1, Formula E, additional sports formats, ROM collections, tutorials, audio libraries and Mac application tags with underscores.
- Match release-group suffixes exactly and require separate XXX markers, preserving film titles such as MaXXXine.
- Respect explicit video resolutions for BDRip/DVDRip releases and recognize TV DVD season discs.
- Keep explicitly named audiobooks in their configured category when their folders contain MP3 files.
- Add 48 category regression checks. Rules use JSON order because this legacy matcher does not implement the priority fields. Filename-only matching cannot resolve ambiguous titles or contradictory historical category assignments.
