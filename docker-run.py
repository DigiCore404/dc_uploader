"""Run Docker using host folder paths from config.ini; no second configuration file."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import uploader


def command(args, config):
    mounts = [(str(uploader.config_path()), '/app/config.ini', True),
              (config.get('Paths', 'DATADIR'), '/content', True),
              (config.get('Paths', 'WATCHFOLDER'), '/watch', False),
              (str(uploader.ROOT / 'data'), '/app/data', False),
              (str(uploader.ROOT / 'tmp'), '/app/tmp', False),
              (str(uploader.ROOT / 'logs'), '/app/logs', False)]
    result = ['docker', 'run', '--rm', '--init']
    if os.name != 'nt' and hasattr(os, 'getuid'):
        result += ['--user', f'{os.getuid()}:{os.getgid()}']
    for host, target, readonly in mounts:
        result += ['--mount', f'type=bind,source={host},target={target}' + (',readonly' if readonly else '')]
    if args and args[0] == 'upload' and len(args) > 1:
        source = Path(args[1]).expanduser()
        if source.is_absolute():
            source = source.resolve().relative_to(Path(config.get('Paths', 'DATADIR')).resolve())
        if '..' in source.parts:
            raise ValueError('Docker uploads must be inside DATADIR.')
        args = ['upload', '/content/' + source.as_posix(), *args[2:]]
    if not args or args[0] in ('gui', 'web'):
        raise ValueError('Use Docker Compose for the browser interface. This helper runs check, upload or watch.')
    return result + ['dc_uploader:local'] + args


def main():
    if not shutil.which('docker'):
        raise ValueError('Install Docker first and start its engine.')
    uploader.ensure_config()
    config = uploader.load_config()
    args = sys.argv[1:] or ['check']
    if args == ['build']:
        return subprocess.call(['docker', 'build', '-t', 'dc_uploader:local', str(uploader.ROOT)])
    if not Path(config.get('Paths', 'DATADIR')).is_dir():
        raise ValueError('Create the DATADIR content folder configured in config.ini first.')
    for path in (Path(config.get('Paths', 'WATCHFOLDER')), uploader.ROOT / 'data',
                 uploader.ROOT / 'tmp', uploader.ROOT / 'logs'):
        path.mkdir(parents=True, exist_ok=True)
    return subprocess.call(command(args, config))


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (ValueError, OSError) as exc:
        print(f'ERROR: {exc}', file=sys.stderr)
        sys.exit(2)
