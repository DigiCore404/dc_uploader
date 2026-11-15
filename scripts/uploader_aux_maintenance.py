#!/usr/bin/env python3
"""Keep the existing DirtyBytes and UNRAR maintenance separate from DigitalCore."""
import logging
from pathlib import Path
import subprocess
import time


def main():
    Path('/var/log/dirtybytes-autoremove').mkdir(parents=True, exist_ok=True)
    while True:
        commands = [['/usr/local/bin/autoremove-torrents', '--conf', '/root/config.yml',
                     '--task', 'my_task_cl3', '--log', '/var/log/dirtybytes-autoremove']]
        for uploader in ('dc_uploader1', 'dc_uploader2', 'upper1', 'upper'):
            script = Path('/uploaders') / uploader / 'scripts/cleanup_unrar_mounts.py'
            if script.is_file(): commands.append(['/usr/bin/python3', str(script)])
        for command in commands:
            try:
                result = subprocess.run(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                if result.returncode: logging.error('Maintenance failed: %s', command[1])
            except OSError: logging.exception('Could not start maintenance')
        time.sleep(10)


if __name__ == '__main__': main()
