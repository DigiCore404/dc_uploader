"""Configurable literal words removed from metadata lookup titles only."""
import re

DEFAULT_IGNORE_WORDS = '''XXX
WEB-DL
WEBRIP
WEBDL
WEB
BluRay
BDRip
BRRip
DVDRip
DVD
HDTV
REMUX
REPACK
PROPER
INTERNAL
UHD
FHD
HD
SD
360p
480p
576p
720p
1080p
1080i
1440p
2160p
4320p
4K
8K
1920x1080
3840x2160
x264
x265
H.264
H.265
H264
H265
HEVC
AVC
AV1
VP9
AAC
AAC2.0
AC3
EAC3
E-AC-3
DTS-HD
DTS
TrueHD
Atmos
DD5.1
DDP5.1
FLAC
MP3
30fps
60fps
MKV
MP4
AVI
WMV
TORRENT
NSW
NSP
NSZ
XCI
PS3
PS4
PS5
PSP
PSV
PSVITA
X360
XBOX360
XBOXONE
XSX
XBLA
NDS
3DS
WII
WIIU
GBA
GBC
NGC
N64
WIN32
WIN64'''


def clean_words(value):
    if not isinstance(value, str) or len(value) > 8000:
        raise ValueError('Use up to 8000 characters for metadata ignore words.')
    words = []
    seen = set()
    for row in re.split(r'[\r\n,]+', value):
        word = row.strip()
        if not word:
            continue
        if len(word) > 80 or any(ord(c) < 32 for c in word):
            raise ValueError('Each metadata ignore word must be at most 80 characters, without control characters.')
        if word.casefold() not in seen:
            seen.add(word.casefold())
            words.append(word)
    if len(words) > 200:
        raise ValueError('Use up to 200 metadata ignore words or phrases.')
    return words


def ignore_words(config):
    return clean_words(config.get('Metadata', 'SEARCH_IGNORE_WORDS', fallback=DEFAULT_IGNORE_WORDS))
