"""Produce a credential-free native Linux distribution with portable permissions."""
from pathlib import Path
import hashlib
import gzip
import io
import tarfile
import zipfile
try:
    from scripts.publication import check
except ModuleNotFoundError:
    from publication import check

ROOT=Path(__file__).resolve().parents[1]
NAME='dc_uploader-seedbox'
VERSION='3.0.0'


def entries():
    names=['defaults.ini','requirements.txt','requirements.lock.txt','uploader.py',
           'web.py','backend.py','install.sh','start.sh','start-web.sh',
           'scripts/service.py','scripts/upload.sh','scripts/build-archive-tools.sh','scripts/install-mkbrr.py']
    names += [p.relative_to(ROOT).as_posix() for p in sorted((ROOT/'utils').glob('*.py'))]
    names += [p.relative_to(ROOT).as_posix() for p in sorted((ROOT/'webui').iterdir()) if p.is_file()]
    names += ['files/template.txt','files/filters.json','files/digitalcore-categories.json']
    names += [p.relative_to(ROOT).as_posix() for p in sorted((ROOT/'bin').rglob('*')) if p.is_file()]
    result={name:(ROOT/name).read_bytes() for name in names}
    result['README.md']=(ROOT/'SEEDBOX-INSTALL.md').read_bytes()
    result['config.example.ini']=(ROOT/'config.seedbox.ini').read_bytes()
    result['config.ini']=result['config.example.ini']
    # Bash scripts must work even when the source checkout uses Windows line endings.
    result={name:raw if name.startswith('bin/') else raw.replace(b'\r\n',b'\n') for name,raw in result.items()}
    check(result)
    return result


def mode(name):
    return 0o600 if name=='config.ini' else 0o755 if name.endswith('.sh') or name.endswith('/mkbrr') or name in ('bin/mtn/mtn','bin/mtn/mtn.bin') else 0o644


def build():
    output=ROOT/'dist';output.mkdir(exist_ok=True)
    files=entries();stem=f'{NAME}-{VERSION}'
    paths=[output/(stem+'.tar.gz'),output/(stem+'.zip')]
    with paths[0].open('wb') as output_file, gzip.GzipFile(filename='',mode='wb',fileobj=output_file,mtime=0) as compressed, tarfile.open(fileobj=compressed,mode='w') as archive:
        for name,raw in sorted(files.items()):
            info=tarfile.TarInfo(NAME+'/'+name);info.size=len(raw);info.mode=mode(name)
            info.uid=info.gid=0;info.uname=info.gname='';info.mtime=0
            archive.addfile(info,io.BytesIO(raw))
    with zipfile.ZipFile(paths[1],'w',compression=zipfile.ZIP_DEFLATED) as archive:
        for name,raw in sorted(files.items()):
            info=zipfile.ZipInfo(NAME+'/'+name);info.create_system=3
            info.external_attr=(0o100000|mode(name))<<16;info.compress_type=zipfile.ZIP_DEFLATED
            archive.writestr(info,raw)
    checksums=''.join(f'{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.name}\n' for p in paths)
    checksum_path=output/(stem+'.sha256');checksum_path.write_text(checksums,encoding='ascii')
    for path in [*paths,checksum_path]:print(path)
    return paths


if __name__=='__main__':build()
