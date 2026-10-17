"""Install a rootless, headless systemd user service for this checkout."""
import argparse
import ipaddress
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]

def quote(value):
    return '"' + str(value).replace('\\', '\\\\').replace('"', '\\"').replace('%', '%%') + '"'

def unit(root, python, host, port):
    ipaddress.ip_address(host)
    if not 1024 <= port <= 65535:
        raise ValueError('Choose an unprivileged port between 1024 and 65535.')
    return f'''[Unit]
Description=DigitalCore Uploader (headless browser service)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory={quote(root)}
ExecStart={quote(python)} {quote(root / 'uploader.py')} web --host {host} --port {port} --no-browser
Restart=on-failure
RestartSec=5
UMask=0077
NoNewPrivileges=true
PrivateTmp=true
KillMode=control-group
TimeoutStopSec=120

[Install]
WantedBy=default.target
'''

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['install','start','stop','status'])
    parser.add_argument('--host',default='127.0.0.1',help='Bind address; use the server LAN IP for remote browser access')
    parser.add_argument('--port',default=6080,type=int)
    args=parser.parse_args()
    if sys.platform != 'linux' or not shutil.which('systemctl'):
        parser.error('This installer needs Linux with systemd. On Synology use the supplied Docker service.')
    if os.geteuid()==0:
        parser.error('Run this as the regular user who owns the uploader, without sudo.')
    name='dc_uploader.service'
    if args.action=='install':
        python=ROOT/'.venv/bin/python'
        if not python.is_file():parser.error('Run bash install.sh first.')
        directory=Path(os.environ.get('XDG_CONFIG_HOME') or Path.home()/'.config')/'systemd/user'
        directory.mkdir(parents=True,exist_ok=True)
        path=directory/name
        path.write_text(unit(ROOT,python,args.host,args.port),encoding='utf-8');path.chmod(0o600)
        subprocess.run(['systemctl','--user','daemon-reload'],check=True)
        subprocess.run(['systemctl','--user','enable','--now',name],check=True)
        print('Headless service installed. Read first-run login with: journalctl --user -u '+name+' -n 30')
        print('For startup before login / after logout, enable user lingering if your host permits it: loginctl enable-linger '+os.environ.get('USER','YOUR_USER'))
    else:
        subprocess.run(['systemctl','--user',args.action,name],check=True)

if __name__=='__main__':main()
