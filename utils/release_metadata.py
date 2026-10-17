"""Conservative release year, language, duration and NFO IMDb extraction."""
import re
import subprocess
from pathlib import Path

DATE_RE = re.compile(r'(?:^|[._ -])(\d{2})[._ -](\d{2})[._ -](\d{2})(?:[._ -]|$)')
YEAR_RE = re.compile(r'(?:^|[._ -])((?:19|20)\d{2})(?:[._ -]|$)')
LANGUAGE_TOKENS = {
    'french': 'French', 'german': 'German', 'spanish': 'Spanish',
    'italian': 'Italian', 'swedish': 'Swedish', 'dutch': 'Dutch',
    'flemish': 'Dutch', 'japanese': 'Japanese', 'russian': 'Russian',
    'czech': 'Czech', 'polish': 'Polish', 'portuguese': 'Portuguese',
    'brazilian': 'Portuguese', 'hungarian': 'Hungarian', 'danish': 'Danish',
    'norwegian': 'Norwegian', 'finnish': 'Finnish', 'korean': 'Korean',
    'chinese': 'Chinese', 'thai': 'Thai', 'hindi': 'Hindi', 'turkish': 'Turkish',
}

MEDIA_EXTS = {'.mp4', '.mkv', '.avi', '.mov', '.flv', '.wmv', '.mpg', '.m2ts', '.vob', '.m4v'}


def _strip_group(name):
    """Remove the trailing -GROUP tag."""
    if '-' in name:
        head, _, tail = name.rpartition('-')
        if tail and '.' not in tail and ' ' not in tail:
            return head
    return name


def _tokens(name):
    return [t for t in re.split(r'[._ ]+', name) if t]


def extract_release_year(release_name):
    name = _strip_group(release_name)
    date_match = DATE_RE.search(name)
    if date_match:
        return str(2000 + int(date_match.group(1)))
    year_match = YEAR_RE.search(name)
    if year_match:
        return year_match.group(1)
    return ''


def extract_language(release_name):
    for token in _tokens(_strip_group(release_name)):
        lang = LANGUAGE_TOKENS.get(token.lower())
        if lang:
            return lang
    return ''


def runtime_minutes(directory):
    """Runtime in whole minutes from the largest media file, via mediainfo."""
    try:
        media = [p for p in Path(directory).rglob('*')
                 if p.is_file() and p.suffix.lower() in MEDIA_EXTS
                 and 'sample' not in str(p).lower()]
        if not media:
            return ''
        biggest = max(media, key=lambda p: p.stat().st_size)
        result = subprocess.run(
            ['mediainfo', '--Inform=General;%Duration%', str(biggest)],
            capture_output=True, text=True, timeout=60,
        )
        minutes = round(float(result.stdout.strip()) / 60000)
        if 1 <= minutes <= 65535:
            return str(minutes)
    except (OSError, subprocess.TimeoutExpired, ValueError):
        pass
    return ''


def extract_metadata(release_name, directory=None):
    metadata = {'release_year': extract_release_year(release_name), 'language': extract_language(release_name)}
    if directory is not None:
        metadata['runtime_minutes'] = runtime_minutes(directory)
        for nfo in sorted(Path(directory).glob('*.nfo'))[:10]:
            if nfo.stat().st_size > 2 * 1024 * 1024:
                continue
            match = re.search(r'imdb\.com/title/(tt[0-9]{7,10})', nfo.read_text(encoding='utf-8', errors='replace'), re.I)
            if match:
                metadata['imdbId'] = match.group(1)
                break
    return {key: value for key, value in metadata.items() if value}
