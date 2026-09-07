#!/usr/bin/env python3
"""Generate local secrets and photo/storage-folder settings without third-party dependencies."""
import argparse
import getpass
import os
from pathlib import Path
import secrets

parser = argparse.ArgumentParser(description='Configure RAW Catalog')
parser.add_argument('--photos', required=True, help='Absolute Linux folder containing RAW photos')
parser.add_argument('--storage', help='Host folder or mounted share for generated thumbnails/previews (default: ./storage)')
args = parser.parse_args()
photo_path = Path(args.photos).expanduser().resolve()
if not photo_path.is_dir():
    parser.error('Photo folder must already exist on this server')
storage_path = Path(args.storage).expanduser().resolve() if args.storage else (Path.cwd() / 'storage').resolve()
try:
    storage_path.mkdir(parents=True, exist_ok=True)
except OSError as exc:
    parser.error(f'Could not create/access storage folder: {exc}')
if not storage_path.is_dir():
    parser.error('Storage path must be a folder')
for path in (photo_path, storage_path):
    if any(c in str(path) for c in "\r\n'"):
        parser.error('Use mount paths without quotes or newlines')
if Path('.env').exists():
    parser.error('.env already exists; edit it directly to preserve your database passwords')
password = getpass.getpass('Choose catalog password (12+ characters): ')
if len(password) < 12 or any(c in password for c in "\r\n'"):
    parser.error('Password must have 12+ characters and no single quotes or newlines')
if password != getpass.getpass('Confirm password: '):
    parser.error('Passwords do not match')
values = {'PHOTO_PATH':str(photo_path), 'STORAGE_PATH':str(storage_path),
          'DB_PASSWORD':secrets.token_hex(24), 'DB_ROOT_PASSWORD':secrets.token_hex(24),
          'SECRET_KEY':secrets.token_hex(32), 'CATALOG_PASSWORD':password, 'WEB_PORT':'8080',
          'BIND_IP':'0.0.0.0', 'COOKIE_SECURE':'false', 'PREVIEW_EDGE':'2560'}
fd = os.open('.env', os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
with os.fdopen(fd,'w') as f:
    for key,value in values.items():
        f.write(f"{key}='{value}'\n")
print('Configuration saved. Run: docker compose up -d --build')
