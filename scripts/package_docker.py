"""Build the small end-user Docker installer without source or private data."""
from pathlib import Path
import gzip
import hashlib
import io
import tarfile
import zipfile
try:
    from scripts.publication import ROOT, check
except ModuleNotFoundError:
    from publication import ROOT, check

VERSION = '3.0.0'


def entries():
    names = ['compose.yaml', 'compose.unrar.yaml', '.env.example', 'setup.sh']
    files = {name: (ROOT/name).read_bytes().replace(b'\r\n', b'\n') for name in names}
    files['README.md'] = (ROOT/'README-docker.md').read_bytes().replace(b'\r\n', b'\n')
    check(files)
    return files


def build():
    files = entries()
    output = ROOT/'dist'; output.mkdir(exist_ok=True)
    stem = 'dc_uploader-docker-'+VERSION
    paths = [output/(stem+'.tar.gz'), output/(stem+'.zip')]
    with paths[0].open('wb') as stream, gzip.GzipFile(filename='', fileobj=stream, mode='wb', mtime=0) as gz, tarfile.open(fileobj=gz, mode='w') as tar:
        for name, raw in sorted(files.items()):
            item = tarfile.TarInfo(name); item.size=len(raw); item.mode=0o755 if name.endswith('.sh') else 0o644
            tar.addfile(item, io.BytesIO(raw))
    with zipfile.ZipFile(paths[1], 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for name, raw in sorted(files.items()):
            info=zipfile.ZipInfo(name); info.create_system=3
            info.external_attr=(0o100755 if name.endswith('.sh') else 0o100644)<<16
            info.compress_type=zipfile.ZIP_DEFLATED; archive.writestr(info, raw)
    checksum=output/(stem+'.sha256')
    checksum.write_text(''.join(hashlib.sha256(p.read_bytes()).hexdigest()+'  '+p.name+'\n' for p in paths), encoding='ascii')
    for p in [*paths, checksum]: print(p)
    return paths


if __name__ == '__main__': build()
