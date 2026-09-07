# RAW Catalog

RAW Catalog is a self-hosted web application for indexing and browsing large RAW photo archives without modifying the original files.

It recursively scans a photo tree, extracts EXIF metadata, stores searchable information in MariaDB, creates cached thumbnails/previews, and provides a browser-based Library, Indexer, Settings, and Statistics interface.

The original photo tree is mounted **read-only** inside the containers.

## Features

- Recursive RAW photo indexing
- Search/filter by camera, lens, filename/path, capture date, and combinations of filters
- Thumbnail grid and full-size preview viewer
- On-demand preview generation
- Parallel RAW processing: 1 / 2 / 4 / 6 / 8 workers
- Persistent ExifTool sessions for high-throughput indexing
- Optional **Fast SMB mode** to reduce SMB metadata round trips
- Incremental scans and optional "skip already imported" mode
- Configurable thumbnail and preview storage locations
- Configurable preview size and JPEG quality
- Camera/lens/focal-length/aperture statistics
- Camera maker, megapixel, sensor-size, and lens-mount statistics
- Camera and lens reference cards in the Statistics view
- MariaDB-backed metadata database
- Docker Compose deployment
- Read-only protection for originals

Supported RAW extensions currently include:

`.cr2`, `.cr3`, `.crw`, `.nef`, `.nrw`, `.arw`, `.srf`, `.sr2`, `.dng`, `.raf`, `.orf`, `.rw2`, `.rwl`, `.pef`, `.ptx`, `.srw`, `.3fr`, `.fff`, `.iiq`, `.kdc`, `.dcr`, `.mos`, `.mrw`, `.raw`, `.x3f`.

## Architecture

RAW Catalog runs three Docker services:

| Service | Purpose |
| --- | --- |
| `web` | Flask/Gunicorn web application and API |
| `worker` | Background indexing and generated-media worker |
| `db` | MariaDB 11.4 |

Container paths:

| Container path | Purpose |
| --- | --- |
| `/photos` | Original photo archive, mounted read-only |
| `/data/cache` | Docker-managed generated-media cache |
| `/storage` | Host folder or mounted share for generated media |

The application runs as UID/GID **10001** inside the `web` and `worker` containers.

---

# Installation

## Requirements

Recommended server:

- Linux server, Ubuntu 24.04 or newer recommended
- Docker Engine
- Docker Compose v2 (`docker compose`)
- Git
- Python 3 for the configuration helper
- A mounted/local directory containing the RAW archive
- A writable location for thumbnails/previews

For large archives, use local SSD/NVMe for MariaDB and preferably for thumbnails. RAW originals can remain on NAS/SMB storage.

## 1. Install Docker on Ubuntu

Install prerequisites:

```bash
sudo apt update
sudo apt install -y ca-certificates curl git python3
```

Add Docker's official repository:

```bash
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
  -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc

. /etc/os-release

echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu ${UBUNTU_CODENAME:-$VERSION_CODENAME} stable" \
  | sudo tee /etc/apt/sources.list.d/docker.list >/dev/null

sudo apt update
sudo apt install -y docker-ce docker-ce-cli containerd.io \
  docker-buildx-plugin docker-compose-plugin
```

Verify:

```bash
docker --version
docker compose version
```

Optional: allow your normal user to run Docker without `sudo`:

```bash
sudo usermod -aG docker "$USER"
```

Log out and back in after changing group membership.

## 2. Clone RAW Catalog

Example installation under `/opt`:

```bash
cd /opt
sudo git clone https://github.com/fraswa/raw-catalog.git
sudo chown -R "$USER":"$USER" /opt/raw-catalog
cd /opt/raw-catalog
```

## 3. Prepare the photo and cache folders

The original photo folder must already exist before running setup.

Example using local directories:

```bash
sudo mkdir -p /srv/photos
sudo mkdir -p /srv/raw-catalog-storage
sudo chown -R 10001:10001 /srv/raw-catalog-storage
```

The photo path only needs to be readable by UID 10001. The storage path must be writable by UID/GID 10001.

Do **not** make the original photo archive writable just for RAW Catalog.

## 4. Generate `.env`

Run the included configuration helper:

```bash
cd /opt/raw-catalog
python3 setup.py \
  --photos /srv/photos \
  --storage /srv/raw-catalog-storage
```

You will be asked to choose the RAW Catalog web password. It must be at least 12 characters.

`setup.py` creates a `.env` file with mode `0600` and generates random MariaDB/application secrets automatically.

If `.env` already exists, `setup.py` intentionally refuses to overwrite it. Edit the existing file manually instead.

## 5. Start the application

```bash
cd /opt/raw-catalog
docker compose up -d --build
```

Check container status:

```bash
docker compose ps
```

Follow logs:

```bash
docker compose logs -f web worker db
```

Default URL:

```text
http://SERVER-IP:8080
```

Log in using the catalog password entered during `setup.py`.

---

# Using an SMB/NAS photo archive

Mount the SMB share on the Linux host first, then give RAW Catalog the Linux mount path. Do not configure an SMB URL directly in the application.

Install CIFS support:

```bash
sudo apt install -y cifs-utils
```

Create mount points:

```bash
sudo mkdir -p /mnt/photos
sudo mkdir -p /mnt/raw-catalog-storage
```

Create a protected credentials file:

```bash
sudo nano /root/.smb-photos
```

Example:

```text
username=YOUR_SMB_USER
password=YOUR_SMB_PASSWORD
```

Protect it:

```bash
sudo chmod 600 /root/.smb-photos
```

Example `/etc/fstab` entries:

```fstab
//NAS/photos /mnt/photos cifs credentials=/root/.smb-photos,vers=3.1.1,ro,uid=10001,gid=10001,file_mode=0440,dir_mode=0550,_netdev,nofail,x-systemd.automount 0 0
//NAS/raw-catalog-storage /mnt/raw-catalog-storage cifs credentials=/root/.smb-photos,vers=3.1.1,rw,uid=10001,gid=10001,file_mode=0660,dir_mode=0770,_netdev,nofail,x-systemd.automount 0 0
```

Reload and test:

```bash
sudo systemctl daemon-reload
sudo mount -a
ls -la /mnt/photos
sudo -u '#10001' test -r /mnt/photos && echo "photo share readable"
sudo -u '#10001' test -w /mnt/raw-catalog-storage && echo "cache share writable"
```

Then configure:

```bash
python3 setup.py \
  --photos /mnt/photos \
  --storage /mnt/raw-catalog-storage
```

For best performance, keep thumbnails on local SSD/NVMe when possible. Large previews are more suitable for NAS-backed storage than hundreds of thousands of small thumbnail files.

---

# Configuration

The main deployment configuration is stored in `.env`.

Example:

```dotenv
PHOTO_PATH='/mnt/photos'
STORAGE_PATH='/mnt/raw-catalog-storage'
WEB_PORT='8080'
BIND_IP='0.0.0.0'
COOKIE_SECURE='false'
PREVIEW_EDGE='2560'
```

Secrets generated by `setup.py` are also stored there:

```dotenv
DB_PASSWORD='...'
DB_ROOT_PASSWORD='...'
SECRET_KEY='...'
CATALOG_PASSWORD='...'
```

Do not commit `.env` to Git.

## Environment variables

| Variable | Default | Description |
| --- | --- | --- |
| `PHOTO_PATH` | required | Host path containing RAW originals |
| `STORAGE_PATH` | `./storage` | Writable host path exposed as `/storage` |
| `WEB_PORT` | `8080` | TCP port exposed by the web application |
| `BIND_IP` | `0.0.0.0` | Host address to bind the web service to |
| `COOKIE_SECURE` | `false` | Set to `true` when the site is served only through HTTPS |
| `PREVIEW_EDGE` | `2560` | Default maximum preview dimension |
| `CATALOG_PASSWORD` | required | Web login password |
| `DB_PASSWORD` | required | MariaDB application password |
| `DB_ROOT_PASSWORD` | required | MariaDB root password |
| `SECRET_KEY` | required | Flask session secret |

After editing `.env`, recreate the affected containers:

```bash
docker compose up -d
```

If application code or the Docker image changed, rebuild:

```bash
docker compose up -d --build
```

## Reverse proxy / HTTPS

RAW Catalog can be placed behind Nginx, Caddy, Traefik, Nginx Proxy Manager, or another HTTPS reverse proxy.

When the application is available only over HTTPS, set:

```dotenv
COOKIE_SECURE='true'
```

Then recreate the web container:

```bash
docker compose up -d web
```

You can bind RAW Catalog only to localhost when using a reverse proxy on the same host:

```dotenv
BIND_IP='127.0.0.1'
WEB_PORT='8080'
```

---

# First-time configuration in the web UI

## Settings

The Settings page controls generated media independently from the original photo archive.

You can configure:

- thumbnail folder
- preview folder
- preview maximum edge
- preview JPEG quality
- cache statistics
- thumbnail/preview purge
- thumbnail/preview rebuild

Generated media can be placed under either:

- `/data/cache` — Docker-managed cache volume
- `/storage` — the host path configured by `STORAGE_PATH`

Changing a cache path does not move old files automatically.

## Indexer

Open **Indexer** and select the source folder to scan. The selected path must remain below `/photos`.

Indexer options:

### Parallel processing

Available values:

```text
1 / 2 / 4 / 6 / 8 workers
```

Start with **4 workers** on a typical 6-core server and benchmark your own storage/CPU combination.

### Skip preview generation

Creates metadata and thumbnails during indexing. A full preview is generated automatically when the image is opened for the first time.

This is recommended for very large archives.

### Skip already imported photos

Skips a file when the exact source path already exists in the catalog.

Photo identity is currently path-based: moving or copying the same RAW to a new path creates another catalog entry.

### Force re-index existing photos

Re-reads metadata and regenerates requested media even if the file appears unchanged.

This is mutually exclusive with **Skip already imported photos**.

### Fast SMB mode

Skips the second `stat()` check after processing a source file.

The initial filesystem check, metadata read, RAW read, and read-only mount protection remain in place. This reduces SMB round trips, but if a RAW changes while it is being indexed, that change will be detected on the next scan instead of immediately.

---

# Statistics

The Statistics page aggregates the indexed archive and caches the result in MariaDB.

Current statistics include:

- photographs and dated/undated counts
- camera usage
- lens usage
- focal-length usage
- aperture usage
- camera usage over time
- photographs by year
- camera maker usage
- megapixel usage
- sensor-size usage
- lens-mount usage
- metadata coverage percentages
- per-camera breakdowns
- per-lens breakdowns

Hover or focus the information control beside supported camera/lens names for a reference card showing technical and archive-specific information.

Some newer technical fields such as sensor size, lens maker, and explicit lens mount require metadata collected by recent versions of the indexer. Re-index older files if those fields have low coverage.

---

# Performance recommendations

For large archives:

1. Keep MariaDB on local SSD/NVMe.
2. Prefer local SSD/NVMe for the thumbnail cache.
3. RAW originals may remain on SMB/NAS storage.
4. Use **Skip preview generation** for the initial import when full previews are not needed immediately.
5. Enable **Fast SMB mode** when the source archive is stable/read-only.
6. Benchmark 4 and 6 workers rather than assuming more threads are always faster.
7. Use SMB 3.x and a low-latency network when scanning network storage.

The worker uses persistent ExifTool `-stay_open` processes to avoid repeatedly starting ExifTool for every RAW file.

Useful monitoring commands:

```bash
docker stats
```

```bash
iostat -xz 1
```

Install `iostat` on Ubuntu with:

```bash
sudo apt install -y sysstat
```

---

# Updating

If no indexing/rebuild job is running:

```bash
cd /opt/raw-catalog
git pull
docker compose up -d --build
```

Avoid restarting the worker during an active indexing job. A worker restart marks the active job as interrupted; already committed catalog entries remain and the next incremental scan can continue the work.

Check logs after updating:

```bash
docker compose logs --tail=100 web worker
```

---

# Backup

Important persistent data:

- MariaDB Docker volume: `database`
- generated-media Docker volume: `previews`
- external generated-media path configured by `STORAGE_PATH`
- `.env`

The RAW originals are not stored inside RAW Catalog and should be backed up separately using your normal photo-backup strategy.

Do **not** use the following command unless you intentionally want to destroy the database and Docker-managed cache:

```bash
docker compose down -v
```

Normal stop/start operations should use:

```bash
docker compose stop
docker compose start
```

or:

```bash
docker compose up -d
```

---

# Troubleshooting

## Containers do not start

```bash
docker compose ps
docker compose logs --tail=200
```

Validate the Compose file and `.env`:

```bash
docker compose config
```

## Photo folder is unavailable

Confirm the host mount exists:

```bash
findmnt /mnt/photos
ls -la /mnt/photos
```

Confirm UID 10001 can read it:

```bash
sudo -u '#10001' find /mnt/photos -maxdepth 1 -type f -print -quit
```

## Generated media cannot be written

Check the configured storage path:

```bash
sudo -u '#10001' touch /mnt/raw-catalog-storage/.raw-catalog-write-test
sudo rm /mnt/raw-catalog-storage/.raw-catalog-write-test
```

For SMB mounts, verify `uid=10001,gid=10001` and suitable `file_mode` / `dir_mode` mount options.

## Web UI returns an error

```bash
docker compose logs --tail=100 web
```

## Indexer errors

```bash
docker compose logs --tail=200 worker
```

## MariaDB errors

```bash
docker compose logs --tail=200 db
```

---

# Security model

RAW Catalog is designed so that the photo source remains read-only:

- Docker mounts `PHOTO_PATH` at `/photos` with `read_only: true`.
- The application rejects source paths outside the configured photo root.
- Symbolic links are not followed during recursive indexing.
- Web and worker containers run as unprivileged UID/GID 10001.
- Containers use `no-new-privileges` and drop Linux capabilities.

Generated thumbnails/previews and MariaDB data are writable, but originals are not.

For access from the public Internet, place the application behind HTTPS and an authenticated/restricted reverse proxy or VPN.

---

# Development / tests

Create a Python environment and install dependencies:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install pytest
```

Run tests:

```bash
python -m pytest -q
```

JavaScript syntax checks can be run with Node.js:

```bash
node --check app/static/indexer.js
node --check app/static/statistics.js
```

---

## License

No license has been declared in this repository yet.
