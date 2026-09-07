#!/usr/bin/env python3
"""Generate local secrets and photo-folder settings without third-party dependencies."""
import argparse
import getpass
import os
from pathlib import Path
import secrets

parser = argparse.ArgumentParser(description='Configure RAW Catalog')
parser.add_argument('--photos', required=True, help='Absolute Linux folder containing RAW photos')
args = parser.parse_args()
path = Path(args.photos).expanduser().resolve()
if not path.is_dir():
    parser.error('Photo folder must already exist on this server')
if any(c in str(path) for c in "\r\n'"):
    parser.error('Use a photo mount path without quotes or newlines')
if Path('.env').exists():
    parser.error('.env already exists; edit it directly to preserve your database passwords')
password = getpass.getpass('Choose catalog password (12+ characters): ')
if len(password) < 12 or any(c in password for c in "\r\n'"):
    parser.error('Password must have 12+ characters and no single quotes or newlines')
if password != getpass.getpass('Confirm password: '):
    parser.error('Passwords do not match')
values = {'PHOTO_PATH':str(path), 'DB_PASSWORD':secrets.token_hex(24), 'DB_ROOT_PASSWORD':secrets.token_hex(24),
          'SECRET_KEY':secrets.token_hex(32), 'CATALOG_PASSWORD':password, 'WEB_PORT':'8080',
          'BIND_IP':'0.0.0.0', 'COOKIE_SECURE':'false', 'PREVIEW_EDGE':'2560'}
fd = os.open('.env', os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
with os.fdopen(fd,'w') as f:
    for key,value in values.items():
        f.write(f"{key}='{value}'\n")
print('Configuration saved. Run: docker compose up -d --build')
