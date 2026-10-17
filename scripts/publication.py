"""Allowlisted publication files and checks that never print secret values."""
import configparser
import hashlib
import re
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
SECRET_KEY = re.compile(r'(?:password|passwd|passkey|api[_-]?key|token|secret|authcode|cookie|announceurl)$', re.I)
TOKEN = re.compile(rb'(?:gh[pousr]_[A-Za-z0-9_]{30,}|github_pat_[A-Za-z0-9_]{30,}|-----BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY-----\r?\n[A-Za-z0-9+/=\r\n]{64,})')
# Ubuntu's unmodified GnuTLS library embeds public crypto self-test fixtures.
# Trust only this exact package binary; a changed file must be reviewed again.
VENDOR_CRYPTO_FIXTURES = {'bin/mtn/lib/libgnutls.so.30': 'dcadda7f0f82c54ca157b6ca0a5fdc8e1128db4d778109c8e197e2f84c2ac9ba'}
PERSONAL = re.compile(rb'(?:/volume[0-9]+/|[Cc]:[\\/]Users[\\/](?!Public\b|Example\b)[^\s/\\]+|89\.149\.221\.100|192\.168\.1\.109|\bdisksta[t]ion\b)', re.I)


def check(files):
    errors = []
    for name, raw in files.items():
        path = PurePosixPath(name)
        if path.is_absolute() or '..' in path.parts:
            errors.append((name, 'unsafe archive path'))
        if any(p in ('.git', '.venv', '__pycache__', 'data', 'logs', 'tmp', 'content', 'workspace', 'tools') for p in path.parts):
            errors.append((name, 'private/runtime directory'))
        if (path.name.startswith('.env') and path.name != '.env.example') or path.name == 'web.env' or path.suffix.lower() in ('.db', '.sqlite', '.sqlite3', '.log', '.torrent', '.pem', '.key', '.pyc', '.bak', '.backup'):
            errors.append((name, 'private/runtime file'))
        if TOKEN.search(raw) and VENDOR_CRYPTO_FIXTURES.get(name) != hashlib.sha256(raw).hexdigest():
            errors.append((name, 'credential pattern'))
        if PERSONAL.search(raw): errors.append((name, 'personal host or path'))
        if path.suffix == '.ini':
            config = configparser.ConfigParser(interpolation=None)
            config.read_string(raw.decode('utf-8'))
            for section in config.sections():
                for key, value in config.items(section):
                    if SECRET_KEY.search(key) and value.strip():
                        errors.append((name, 'nonempty credential field: '+section+'.'+key))
        if name == '.env.example':
            for line in raw.decode().splitlines():
                if '=' in line and not line.lstrip().startswith('#'):
                    key, value = line.split('=', 1)
                    if SECRET_KEY.search(key) and value.strip(): errors.append((name, 'nonempty credential field'))
    if errors:
        raise ValueError('Publication blocked:\n'+'\n'.join(name+': '+reason for name, reason in errors))
    return len(files)


def source_files():
    names = ['LICENSE', 'Dockerfile', '.dockerignore', '.gitignore', '.env.example', 'compose.yaml', 'compose.unrar.yaml',
             'setup.sh', 'README.md', 'README-docker.md', 'SEEDBOX-INSTALL.md', 'install.sh', 'start.sh',
             'start-web.sh', 'docker-run.py', 'defaults.ini', 'config.example.ini', 'config.seedbox.ini',
             'requirements.txt', 'requirements.lock.txt', 'uploader.py', 'web.py', 'backend.py',
             'files/template.txt', 'files/filters.json', 'files/digitalcore-categories.json']
    for directory in ('utils', 'webui', 'bin', 'scripts', 'tests', '.github'):
        names += [p.relative_to(ROOT).as_posix() for p in sorted((ROOT/directory).rglob('*'))
                  if p.is_file() and '__pycache__' not in p.parts and p.suffix != '.pyc']
    files = {name: (ROOT/name).read_bytes() for name in names}
    files = {name: raw if name.startswith('bin/') else raw.replace(b'\r\n', b'\n') for name, raw in files.items()}
    check(files)
    return files


if __name__ == '__main__':
    import subprocess
    import sys
    files = source_files()
    if '--checkout' in sys.argv:
        tracked = subprocess.check_output(['git','ls-files','-z'], cwd=ROOT).decode().split('\0')
        extras = set(filter(None, tracked))-files.keys()
        if extras: raise ValueError('Files outside publication allowlist: '+', '.join(sorted(extras)))
    print(f'Publication scan passed: {len(files)} allowlisted files; no private runtime files or configured credentials.')
