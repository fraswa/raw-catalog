# RAW Catalog 2.0.0

RAW Catalog is a self-hosted web application for indexing, searching, previewing, analyzing, and non-destructively editing large RAW photo archives.

The source photo tree is mounted **read-only** inside the containers. RAW Catalog stores metadata, generated thumbnails/previews, favorites, gear overrides, statistics, and exported JPEG edits separately from the originals.

## Highlights in 2.0.0

- High-throughput recursive RAW indexing with persistent ExifTool sessions
- Pipelined metadata prefetch + parallel RAW rendering
- Parallel processing options: 1 / 2 / 4 / 6 / 8 workers
- Incremental scans, force re-index, skip already imported, and Fast SMB mode
- Automatic exclusion and cleanup of macOS metadata garbage such as `._*`, `.DS_Store`, `.AppleDouble`, and `__MACOSX`
- Search/filter by camera, lens, filename/path, capture date, favorites, and combinations
- Thumbnail grid, fullscreen previews, preview downloads, and original RAW downloads
- On-demand preview generation
- Favorites stored in MariaDB
- Non-destructive RAW editor with live cached previews
- RAW editor controls for exposure, contrast, highlights, shadows, temperature, tint, saturation, black/white levels, and denoise
- Automatic RAW adjustment and full-resolution JPEG export
- Saved Edits gallery with download/delete support
- Gear database for camera and lens metadata overrides
- Camera fields: maker, mount, sensor size, sensor type/format, resolution, notes
- Lens fields: maker, mount, lens type, focal range, maximum aperture, notes
- Statistics for cameras, lenses, focal lengths, apertures, makers, megapixels, sensor sizes, sensor types, lens mounts, years, and trends
- Lens-mount filtering and per-mount breakdowns in Statistics
- Cached statistics with fast unchanged-catalog validation
- Configurable thumbnail, preview, and edited-JPEG storage
- MariaDB-backed catalog
- Docker Compose deployment
- Automatic schema upgrades for supported upgrades

Supported RAW extensions include:

`.cr2`, `.cr3`, `.crw`, `.nef`, `.nrw`, `.arw`, `.srf`, `.sr2`, `.dng`, `.raf`, `.orf`, `.rw2`, `.rwl`, `.pef`, `.ptx`, `.srw`, `.3fr`, `.fff`, `.iiq`, `.kdc`, `.dcr`, `.mos`, `.mrw`, `.raw`, `.x3f`.

## Architecture

RAW Catalog runs three Docker services:

| Service | Purpose |
| --- | --- |
| `web` | Flask/Gunicorn web application and API |
| `worker` | Background indexer and generated-media worker |
| `db` | MariaDB 11.4 |

Container paths:

| Path | Purpose |
| --- | --- |
| `/photos` | RAW originals, mounted read-only |
| `/data/cache` | Docker-managed cache and editor working previews |
| `/storage` | Host folder or mounted share for persistent generated data |

The web and worker containers run as UID/GID **10001**.

---

# Installation

## Requirements

- Linux server; Ubuntu 24.04 or newer recommended
- Docker Engine
- Docker Compose v2
- Git
- Python 3 for `setup.py`
- A local or mounted directory containing the RAW archive
- A writable location for generated media

For large archives, keep MariaDB and preferably thumbnails on SSD/NVMe. RAW originals can remain on SMB/NAS storage.

## 1. Install Docker on Ubuntu

```bash
sudo apt update
sudo apt install -y ca-certificates curl git python3

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

## 2. Clone RAW Catalog

```bash
cd /opt
sudo git clone https://github.com/fraswa/raw-catalog.git
sudo chown -R "$USER":"$USER" /opt/raw-catalog
cd /opt/raw-catalog
```

To install the 2.0.0 release specifically:

```bash
git checkout v2.0.0
```

## 3. Prepare storage

Example local paths:

```bash
sudo mkdir -p /srv/photos
sudo mkdir -p /srv/raw-catalog-storage
sudo chown -R 10001:10001 /srv/raw-catalog-storage
```

The photo path only needs to be readable by UID 10001. Generated-media storage must be writable by UID/GID 10001.

Do **not** make the RAW archive writable just for RAW Catalog.

## 4. Generate `.env`

```bash
cd /opt/raw-catalog
python3 setup.py \
  --photos /srv/photos \
  --storage /srv/raw-catalog-storage
```

`setup.py` asks you to choose and confirm an initial admin password (12–1024 characters), then creates a private `.env` and generates application/database secrets. There is no default password. Initial passwords cannot include single quotes or newlines because of the `.env` format. Keep this file private and out of Git.

## 5. Start

```bash
cd /opt/raw-catalog
docker compose up -d --build
```

Default URL:

```text
http://SERVER-IP:8080
```

Initial login: username **admin**, with the password you chose during setup.

Open **Settings → Username and password** to change either value. Enter the current password to save changes; leave the new-password fields blank to change only the username. New passwords must contain 12–1024 characters. Usernames can contain up to 64 letters, numbers, dots, underscores, or hyphens.

The account is stored in MariaDB with a salted password hash. Changes persist across container restarts. Changing the account signs out other sessions while keeping the current browser signed in.

`INITIAL_ADMIN_PASSWORD` is used only when the account is first created. After the first successful startup, you can remove that entry from `.env`; later starts use the saved account. Changing the environment value does not reset an existing password.

Useful checks:

```bash
docker compose ps
docker compose logs -f web worker db
```

---

# SMB / NAS example

Mount SMB on the Linux host first, then expose the Linux mount path to RAW Catalog.

```bash
sudo apt install -y cifs-utils
sudo mkdir -p /mnt/photos /mnt/raw-catalog-storage
```

Example `/etc/fstab`:

```fstab
//NAS/photos /mnt/photos cifs credentials=/root/.smb-photos,vers=3.1.1,ro,uid=10001,gid=10001,file_mode=0440,dir_mode=0550,_netdev,nofail,x-systemd.automount 0 0
//NAS/raw-catalog-storage /mnt/raw-catalog-storage cifs credentials=/root/.smb-photos,vers=3.1.1,rw,uid=10001,gid=10001,file_mode=0660,dir_mode=0770,_netdev,nofail,x-systemd.automount 0 0
```

Then:

```bash
sudo systemctl daemon-reload
sudo mount -a

python3 setup.py \
  --photos /mnt/photos \
  --storage /mnt/raw-catalog-storage
```

For large archives, a good layout is:

- RAW originals: NAS/SMB
- MariaDB: local SSD/NVMe
- thumbnails: local SSD/NVMe
- full previews / exported edits: local disk or NAS depending on capacity

---

# Main configuration

Typical `.env` values:

```dotenv
PHOTO_PATH='/mnt/photos'
STORAGE_PATH='/mnt/raw-catalog-storage'
WEB_PORT='8080'
BIND_IP='0.0.0.0'
COOKIE_SECURE='false'
PREVIEW_EDGE='2560'
```

Secrets generated by `setup.py` are also stored in `.env`:

```dotenv
DB_PASSWORD='...'
DB_ROOT_PASSWORD='...'
SECRET_KEY='...'
```

Never commit `.env`.

When using HTTPS through a reverse proxy:

```dotenv
COOKIE_SECURE='true'
```

---

# Library

The Library provides:

- camera filter
- lens filter
- filename/folder search
- capture-date range
- favorites-only filter
- capture/indexed sorting
- thumbnail browsing
- fullscreen preview
- RAW and JPEG download
- RAW Editor launch

Photo identity is path-based. Moving/copying the same RAW to a different path creates a new catalog row.

Favorites survive normal same-path re-indexing.

---

# Indexer

The Indexer recursively scans the selected directory below `/photos`.

Available options include:

- **Parallel processing:** 1 / 2 / 4 / 6 / 8 workers
- **Skip preview generation:** index metadata + thumbnails; generate full previews on demand
- **Skip already imported:** skip exact paths already in the database
- **Force re-index:** refresh metadata and generated media
- **Fast SMB mode:** skip the second source `stat()` verification to reduce network metadata round trips

The worker pipelines work approximately like this:

```text
metadata batch N+1  ----->
RAW render batch N ----->
DB writes          ----->
```

Only one metadata-prefetch stream is used, while RAW rendering can run in parallel.

macOS-generated filesystem metadata such as `._*`, `.DS_Store`, `.AppleDouble`, `__MACOSX`, `.Spotlight-V100`, `.Trashes`, and `.fseventsd` is ignored. Previously indexed garbage rows are removed during a normal scan.

---

# RAW Editor

The RAW Editor is non-destructive: it always reads the RAW and writes a separate JPEG.

Controls in 2.0.0:

- Exposure
- Contrast
- Highlights
- Shadows
- Temperature
- Tint
- Saturation
- Black level
- White level
- Simple denoise

Preview rendering uses a cached decoded working image. Normal tone/color changes reuse that cached base; changing denoise can require a new RAW decode.

**Auto** calculates conservative exposure and levels and can apply ISO-based denoise.

**Save full-resolution JPEG** performs a full RAW decode and writes the result to the configured edits folder.

The default editor working cache is:

```text
/data/cache/editor-work
```

For persistent saved edits, configure the edited-JPEG folder under `/storage`, for example:

```text
/storage/edits
```

The **Edits** page provides a gallery of saved JPEGs with download and delete actions.

## Export cosplay photos for ChatGPT

In the RAW Editor, use **Export for ChatGPT** to prepare an sRGB JPEG with the current editor adjustments:

1. Choose **12, 15, or 18 MP** (default: 15 MP) and click **Prepare JPEG for ChatGPT**.
2. Click **Download ChatGPT JPEG**. The copy is saved in the configured edits folder and also appears in **Edits**.
3. Review the reusable cosplay editing prompt and click **Copy prompt**. You can customize it; your browser remembers the prompt. **Reset prompt** restores the standard version.
4. Click **Open ChatGPT**, attach the downloaded JPEG, paste the prompt, and send.

Exports retain the aspect ratio and never upscale smaller photos. They are limited to the selected megapixel budget and 19 MB, with JPEG quality reduced only when needed. Source EXIF, GPS, and other source metadata are omitted; an sRGB color profile is embedded. The original RAW remains untouched.

The standard prompt requests a photorealistic cosplay retouch that preserves identity, expression, pose, costume, makeup, and props, and asks ChatGPT to generate a finished image and downloadable JPEG. ChatGPT controls its native output resolution and available file format; a 15 MP input does not guarantee a 15 MP edited output. If JPEG conversion is available in ChatGPT, the prompt asks for it; otherwise it asks for the generated image in its available format.

This is a manual upload workflow using your ChatGPT account. RAW Catalog makes no OpenAI API requests and does not automatically upload photos or import edited results.

---

# Gear database

Open **Gear database** from Statistics or Settings.

Camera overrides:

- maker
- lens mount
- sensor size
- sensor type / format
- resolution
- notes

Lens overrides:

- maker
- lens mount
- lens type
- focal range
- maximum aperture
- notes

Recommended lens formatting:

```text
Focal range:       24-70 mm
Maximum aperture:  f/2.8
```

Variable-aperture example:

```text
Focal range:       35-350 mm
Maximum aperture:  f/3.5-5.6
```

Overrides affect catalog interpretation/statistics only. Original RAW EXIF is never modified.

---

# Statistics

Statistics are cached in MariaDB and rebuilt only when the catalog or Gear database changes, or when a manual refresh is requested.

Available views include:

- total photographs / dated photographs
- cameras
- lenses
- focal lengths
- apertures
- camera makers
- megapixels
- sensor sizes
- sensor types / formats
- lens mounts
- photographs by year
- camera usage trends
- per-camera breakdowns
- per-lens breakdowns
- per-mount breakdowns

The lens-mount selector can scope camera, lens, focal-length, aperture, maker, sensor, resolution, year, and trend statistics to a specific mount.

Lens-specific mount data takes precedence over camera-body mount fallback, which improves statistics for adapted lenses.

---

# Generated-media storage

Settings allow independent configuration of:

- thumbnail folder
- preview folder
- preview maximum edge
- preview JPEG quality
- edited-JPEG folder
- purge/rebuild operations

Generated files may live under:

```text
/data/cache
```

or under the persistent host-backed path:

```text
/storage
```

Changing a configured path does not automatically move existing files.

---

# Updating

Before upgrading from the previous catalog-password login, add `INITIAL_ADMIN_PASSWORD` to your private `.env` with a password you choose (12–1024 characters, enclosed in single quotes, without single quotes or newlines inside the password). Startup requires this value when no account exists and creates the `admin` account once. The old `CATALOG_PASSWORD` entry is ignored and can be removed. Existing catalog data is preserved; previous password-only sessions must sign in again. Later starts preserve the account saved in Settings and no longer need the initial password entry.

If no index/rebuild job is active:

```bash
cd /opt/raw-catalog
git pull
docker compose up -d --build
```

Avoid recreating the worker during an active scan because the current job will be interrupted. Already committed catalog rows remain and the next incremental scan can continue.

Do not use:

```bash
docker compose down -v
```

unless you intentionally want to remove MariaDB data and Docker-managed cache volumes.

---

# Backup

Back up at least:

- MariaDB `database` volume
- `.env`
- any persistent `/storage` content, especially saved edits
- generated caches if you do not want to regenerate them

RAW originals are outside RAW Catalog and must be backed up separately.

---

# Troubleshooting

Container status:

```bash
docker compose ps
```

Web logs:

```bash
docker compose logs --tail=100 web
```

Indexer logs:

```bash
docker compose logs --tail=200 worker
```

Database logs:

```bash
docker compose logs --tail=200 db
```

Validate Compose configuration:

```bash
docker compose config
```

---

# Security model

RAW Catalog protects originals by design:

- `PHOTO_PATH` is mounted at `/photos` read-only
- source paths outside the configured photo root are rejected
- symbolic links are not followed during recursive indexing
- web/worker run as unprivileged UID/GID 10001
- containers use restricted capabilities / no-new-privileges
- edits and metadata overrides are stored outside the source RAW tree

For Internet access, use HTTPS and an appropriately restricted reverse proxy or VPN.

---

# Development

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install pytest
python -m pytest -q
```

JavaScript syntax checks can be run with Node.js:

```bash
node --check app/static/app.js
node --check app/static/editor.js
node --check app/static/statistics.js
node --check app/static/catalog.js
```

---

# Version

Current release: **2.0.0**

The repository also contains a `VERSION` file and release tags use the `vMAJOR.MINOR.PATCH` format.
