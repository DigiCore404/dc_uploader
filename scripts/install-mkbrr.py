"""Install pinned mkbrr from its official release, verifying the extracted binary."""
import hashlib
import io
import json
import os
from pathlib import Path
import platform
import tarfile
import urllib.request

ROOT = Path(__file__).resolve().parents[1]

def install(root=ROOT):
    if platform.system() != 'Linux':
        raise RuntimeError('Install mkbrr using your platform package manager, then configure MKBRR_BIN.')
    arch={'x86_64':'x86_64','amd64':'x86_64','aarch64':'arm64','arm64':'arm64','armv7l':'arm','armv6l':'armv6'}.get(platform.machine().lower())
    if not arch:
        raise RuntimeError('Unsupported mkbrr architecture.')
    spec=json.loads((root/'bin/versions.json').read_text())['mkbrr']
    relative='bin/mkbrr/linux/'+arch+'/mkbrr'
    target=root/relative
    expected=spec['binaries'][relative]
    if target.is_file() and hashlib.sha256(target.read_bytes()).hexdigest()==expected:
        target.chmod(0o755)
        return target
    url='https://github.com/autobrr/mkbrr/releases/download/v'+spec['version']+'/mkbrr_'+spec['version']+'_linux_'+arch+'.tar.gz'
    with urllib.request.urlopen(url,timeout=60) as response:
        archive=response.read(64*1024*1024+1)
    if len(archive)>64*1024*1024:
        raise RuntimeError('mkbrr archive exceeds the download limit.')
    with tarfile.open(fileobj=io.BytesIO(archive),mode='r:gz') as tar:
        matches=[entry for entry in tar if entry.isfile() and Path(entry.name).name=='mkbrr']
        if len(matches)!=1 or matches[0].size>64*1024*1024:
            raise RuntimeError('Unexpected mkbrr release archive.')
        binary=tar.extractfile(matches[0]).read()
    if hashlib.sha256(binary).hexdigest()!=expected:
        raise RuntimeError('mkbrr checksum mismatch; installation refused.')
    target.parent.mkdir(parents=True,exist_ok=True)
    temporary=target.with_suffix('.new');temporary.write_bytes(binary);temporary.chmod(0o755);os.replace(temporary,target)
    return target

if __name__=='__main__':
    print('Verified mkbrr:',install())
