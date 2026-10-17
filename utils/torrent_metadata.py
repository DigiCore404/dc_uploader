"""Read/validate v1 or hybrid torrent metadata; media hashing is exclusively mkbrr."""
import hashlib
import io
from pathlib import Path

LIMIT=64*1024*1024


def decode(raw):
    if not isinstance(raw,bytes) or not 0<len(raw)<=LIMIT:raise ValueError('Invalid torrent size.')
    position=0;raw_info=None
    def value(depth=0):
        nonlocal position,raw_info
        if depth>64 or position>=len(raw):raise ValueError('Invalid bencode nesting or truncated torrent.')
        token=raw[position:position+1]
        if token==b'i':
            end=raw.find(b'e',position+1)
            if end<0:raise ValueError('Truncated integer.')
            text=raw[position+1:end]
            if not text or text==b'-0' or text.startswith(b'0') and len(text)>1 or text.startswith(b'-0') or any(c not in b'0123456789' for c in text.lstrip(b'-')) or text.count(b'-')>1 or b'-' in text[1:]:raise ValueError('Invalid integer.')
            position=end+1;return int(text)
        if token in (b'l',b'd'):
            dictionary=token==b'd';position+=1;result={} if dictionary else []
            while position<len(raw) and raw[position:position+1]!=b'e':
                item=value(depth+1)
                if dictionary:
                    if not isinstance(item,bytes) or item in result:raise ValueError('Invalid or duplicate dictionary key.')
                    start=position;result[item]=value(depth+1)
                    if depth==0 and item==b'info':raw_info=raw[start:position]
                else:result.append(item)
            if position>=len(raw):raise ValueError('Truncated collection.')
            position+=1;return result
        if not token.isdigit():raise ValueError('Invalid bencode token.')
        end=raw.find(b':',position)
        if end<0:raise ValueError('Truncated string length.')
        length=raw[position:end]
        if not length.isdigit() or len(length)>9 or len(length)>1 and length.startswith(b'0'):raise ValueError('Invalid string length.')
        start=end+1;position=start+int(length)
        if position>len(raw):raise ValueError('Truncated string.')
        return raw[start:position]
    result=value()
    if position!=len(raw) or not isinstance(result,dict) or raw_info is None:raise ValueError('Invalid torrent dictionary.')
    return result,raw_info


def text(value):
    if not isinstance(value,bytes):raise ValueError('Expected torrent string.')
    return value.decode('utf-8',errors='replace')


def safe_component(value):
    result=text(value)
    if result in ('','.','..') or any(c in result for c in '/\\\x00\r\n'):raise ValueError('Unsafe torrent file path.')
    return result


class TorrentMetadata:
    def __init__(self,raw):
        meta,info_raw=decode(raw);self.raw=raw;info=meta.get(b'info')
        if not isinstance(info,dict):raise ValueError('Missing torrent info.')
        self.name=safe_component(info.get(b'name.utf-8',info.get(b'name')))
        self.piece_size=info.get(b'piece length')
        pieces=info.get(b'pieces')
        if type(self.piece_size) is not int or self.piece_size<1 or not isinstance(pieces,bytes) or len(pieces)%20:raise ValueError('A valid v1/hybrid torrent is required.')
        entries=info.get(b'files')
        if entries is None:entries=[{b'length':info.get(b'length'),b'path':[]}]
        elif not isinstance(entries,list) or not entries:raise ValueError('Invalid torrent files.')
        self.files=[];self.file_sizes=[]
        for entry in entries:
            if not isinstance(entry,dict) or type(entry.get(b'length')) is not int or entry[b'length']<0:raise ValueError('Invalid torrent file length.')
            path=entry.get(b'path.utf-8',entry.get(b'path'))
            if not isinstance(path,list) or b'files' in info and not path:raise ValueError('Invalid torrent file path.')
            self.files.append(Path(self.name,*[safe_component(part) for part in path]));self.file_sizes.append(entry[b'length'])
        if len(set(self.files))!=len(self.files):raise ValueError('Duplicate torrent file paths.')
        total=sum(self.file_sizes)
        if len(pieces)!=((total+self.piece_size-1)//self.piece_size)*20:raise ValueError('Torrent piece count does not match its files.')
        self.infohash=hashlib.sha1(info_raw).hexdigest()
        self.private=info.get(b'private')==1
        self.source=text(info[b'source']) if b'source' in info else ''
        self.comment=text(meta[b'comment']) if b'comment' in meta else ''
        self.trackers=[[text(url) for url in tier] for tier in meta.get(b'announce-list',[]) if isinstance(tier,list)]
        if not self.trackers and isinstance(meta.get(b'announce'),bytes):self.trackers=[[text(meta[b'announce'])]]
        self.metainfo={'info':{'name':self.name,'piece length':self.piece_size,'pieces':pieces}}
    @classmethod
    def read(cls,path):
        with Path(path).open('rb') as file:return cls.read_stream(file)
    @classmethod
    def read_stream(cls,file):return cls(file.read(LIMIT+1))
    def validate(self):return True
    def verify_filesize(self,source):
        source=Path(source).resolve()
        for relative,size in zip(self.files,self.file_sizes):
            path=source if source.is_file() else source.joinpath(*relative.parts[1:]) if len(relative.parts)>1 else source/relative.name
            resolved=path.resolve(strict=True)
            if not resolved.is_relative_to(source if source.is_dir() else source.parent) or not resolved.is_file() or resolved.stat().st_size!=size:return False
        return True
