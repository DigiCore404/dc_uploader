"""Prepare an imported torrent for mkbrr modify without reading/hashing media."""
from pathlib import Path
from utils.torrent_metadata import TorrentMetadata, decode

def encode(value):
    if isinstance(value,bytes):return str(len(value)).encode()+b':'+value
    if type(value) is int:return b'i'+str(value).encode()+b'e'
    if isinstance(value,list):return b'l'+b''.join(encode(x) for x in value)+b'e'
    if isinstance(value,dict):return b'd'+b''.join(encode(k)+encode(v) for k,v in sorted(value.items()))+b'e'
    raise ValueError('Unsupported torrent metadata.')

def prepare(original,directory,target):
    metadata=TorrentMetadata.read(original)
    if not metadata.verify_filesize(directory):raise ValueError('The imported torrent does not match the completed files. No new hashing was started.')
    meta,_=decode(metadata.raw);info=meta[b'info']
    # A single-file download is kept in a release directory by the import UI.
    # Wrap that same file without changing its piece hashes or byte order.
    if b'files' not in info:
        info[b'files']=[{b'length':info.pop(b'length'),b'path':[metadata.name.encode('utf-8')]}]
    info[b'name']=Path(directory).name.encode('utf-8');info.pop(b'name.utf-8',None)
    clean={b'info':info}
    if b'piece layers' in meta:clean[b'piece layers']=meta[b'piece layers']
    Path(target).write_bytes(encode(clean))
    return metadata
