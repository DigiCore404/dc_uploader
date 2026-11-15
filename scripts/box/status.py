#!/usr/bin/env python3
import subprocess

print('Automatic services:', flush=True)
print('One-shot cleanup services are normally inactive between timer runs.', flush=True)
subprocess.run(['systemctl', '--no-pager', '--plain', 'list-units', '--all',
                'digitalcore-qbit-cleanup.service', 'uploader-storage-cleanup.service',
                'uploader-link-watcher.service', 'uploader-aux-maintenance.service'])
print('\nCleanup timers:', flush=True)
subprocess.run(['systemctl', '--no-pager', 'list-timers', 'digitalcore-qbit-cleanup.timer', 'uploader-storage-cleanup.timer'])
print('\nExisting screens:', flush=True)
subprocess.run(['screen', '-ls'])
