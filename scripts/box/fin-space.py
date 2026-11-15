#!/usr/bin/env python3
"""Launch the unchanged archive manager with its original working directory."""
import os
import sys

os.chdir('/glftpd/fin-space')
os.execv('/usr/bin/python3', ['python3', '/glftpd/fin-space/fin-space.py', *sys.argv[1:]])
