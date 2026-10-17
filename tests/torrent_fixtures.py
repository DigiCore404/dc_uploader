"""Tiny deterministic fixture builder; never used for production torrent creation."""
from pathlib import Path
import hashlib
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from utils.torrent_metadata import TorrentMetadata


def encode(value):
    if isinstance(value,str):value=value.encode()
    if isinstance(value,bytes):return str(len(value)).encode()+b':'+value
    if isinstance(value,int):return b'i'+str(value).encode()+b'e'
    if isinstance(value,list):return b'l'+b''.join(encode(x) for x in value)+b'e'
    if isinstance(value,dict):return b'd'+b''.join(encode(k)+encode(v) for k,v in sorted(value.items()))+b'e'
    raise TypeError(value)


class Torrent:
    read=TorrentMetadata.read
    def __init__(self,path):self.path=Path(path)
    def generate(self):
        files=sorted(p for p in self.path.rglob('*') if p.is_file()) if self.path.is_dir() else [self.path]
        data=b''.join(p.read_bytes() for p in files);piece=16384
        info={'name':self.path.name,'piece length':piece,'pieces':b''.join(hashlib.sha1(data[x:x+piece]).digest() for x in range(0,len(data),piece))}
        if self.path.is_dir():info['files']=[{'length':p.stat().st_size,'path':list(p.relative_to(self.path).parts)} for p in files]
        else:info['length']=self.path.stat().st_size
        self.raw=encode({'info':info});self.infohash=TorrentMetadata(self.raw).infohash
    def dump(self):return self.raw
