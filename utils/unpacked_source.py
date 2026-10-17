"""Resolve RAR input to a persistent unpacked view before any upload step."""
import os,re
from pathlib import Path
from utils.config_loader import ConfigLoader
from utils.unrar_upload_utils import find_rar_files,ensure_unrar_mount,is_archive_path

def prepare_source(directory,log_path):
    directory=Path(directory)
    archives=find_rar_files(directory,limit=10000)
    if not archives:return directory,None
    config=ConfigLoader().get_config()
    if not config.getboolean('UnrarUpload','ENABLED',fallback=False):
        raise RuntimeError('RAR source detected but unpacking is disabled. Packed uploads are forbidden.')
    first=[]
    for value in archives:
        p=Path(value);part=re.search(r'(?i)\.part(\d+)\.rar$',p.name)
        if part and int(part.group(1))!=1:continue
        if 'sample' in [x.lower() for x in p.parts]:continue
        first.append(p)
    if len(first)!=1:raise RuntimeError('Expected one main RAR set. Choose the release folder for one archive set.')
    source=first[0].resolve()
    target=ensure_unrar_mount(directory.name,source,log_path)
    payload=[Path(root)/name for root,_,names in os.walk(target) for name in names]
    if not payload or any(is_archive_path(p) for p in payload):
        raise RuntimeError('Unpacked view is empty or still contains archive files. Upload stopped.')
    return target,source

def assert_unpacked_torrent(torrent):
    bad=[str(p) for p in torrent.files if is_archive_path(p)]
    if bad:raise RuntimeError('Packed torrent blocked: unpack the RAR source first.')
