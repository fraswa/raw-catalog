"""Read-only metadata and preview extraction. Never writes to originals."""
import atexit
import io
import json
import os
import select
import subprocess
import threading
import time
from pathlib import Path
from PIL import Image, ImageOps, ImageCms

EXTENSIONS = {'.cr2', '.cr3', '.crw', '.nef', '.nrw', '.arw', '.srf', '.sr2', '.dng',
              '.raf', '.orf', '.rw2', '.rwl', '.pef', '.ptx', '.srw', '.3fr', '.fff',
              '.iiq', '.kdc', '.dcr', '.mos', '.mrw', '.raw', '.x3f'}
TAGS = ['Make', 'Model', 'LensMake', 'LensModel', 'LensID', 'Lens', 'LensType', 'LensMount',
        'DateTimeOriginal', 'CreateDate', 'ISO', 'FNumber', 'ExposureTime', 'FocalLength',
        'ImageWidth', 'ImageHeight', 'ExifImageWidth', 'ExifImageHeight', 'SensorSize', 'SensorType',
        'SensorWidth', 'SensorHeight', 'FocalPlaneXResolution', 'FocalPlaneYResolution',
        'FocalPlaneResolutionUnit', 'MaxApertureValue', 'Orientation', 'SerialNumber', 'LensSerialNumber', 'FileType']

_sessions = set()
_sessions_lock = threading.Lock()
_metadata_session = None
_metadata_lock = threading.Lock()
_preview_local = threading.local()


class ExifToolSession:
    """Persistent ExifTool -stay_open process. One instance is safe to share serially."""
    def __init__(self):
        self._process = None
        self._counter = 0
        self._lock = threading.Lock()
        with _sessions_lock:
            _sessions.add(self)

    def _start(self):
        self._process = subprocess.Popen(
            ['exiftool', '-stay_open', 'True', '-@', '-'],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            bufsize=0)

    def close(self):
        process = self._process
        self._process = None
        if not process:
            return
        try:
            if process.poll() is None and process.stdin:
                process.stdin.write(b'-stay_open\nFalse\n')
                process.stdin.flush()
                process.wait(timeout=2)
        except Exception:
            try:
                process.terminate()
                process.wait(timeout=1)
            except Exception:
                try:
                    process.kill()
                except Exception:
                    pass

    def _oneshot(self, args, timeout):
        result = subprocess.run(['exiftool', *args], capture_output=True,
                                timeout=timeout, check=False)
        return result.stdout

    def _execute_once(self, args, timeout):
        # ExifTool argfiles are line based, so preserve support for rare newline-containing paths.
        if any('\n' in str(arg) or '\r' in str(arg) for arg in args):
            return self._oneshot(args, timeout)
        if self._process is None or self._process.poll() is not None:
            self.close()
            self._start()
        self._counter += 1
        token = str(self._counter)
        marker = ('{ready' + token + '}\n').encode('ascii')
        payload = ''.join(str(arg) + '\n' for arg in args) + '-execute' + token + '\n'
        try:
            self._process.stdin.write(payload.encode('utf-8', errors='surrogateescape'))
            self._process.stdin.flush()
        except (AttributeError, BrokenPipeError, OSError) as exc:
            raise RuntimeError('ExifTool persistent session write failed') from exc

        deadline = time.monotonic() + timeout
        output = bytearray()
        fd = self._process.stdout.fileno()
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError('ExifTool persistent session timed out')
            readable, _, _ = select.select([fd], [], [], remaining)
            if not readable:
                raise TimeoutError('ExifTool persistent session timed out')
            chunk = os.read(fd, 65536)
            if not chunk:
                raise RuntimeError('ExifTool persistent session ended unexpectedly')
            output.extend(chunk)
            position = output.find(marker)
            if position >= 0:
                return bytes(output[:position])

    def execute(self, args, timeout=90):
        with self._lock:
            for attempt in range(2):
                try:
                    return self._execute_once(args, timeout)
                except (TimeoutError, RuntimeError, OSError):
                    self.close()
                    if attempt:
                        raise
            raise RuntimeError('ExifTool persistent session failed')


def close_exiftool_sessions():
    global _metadata_session
    with _sessions_lock:
        sessions = list(_sessions)
        _sessions.clear()
    for session in sessions:
        session.close()
    with _metadata_lock:
        _metadata_session = None
    try:
        delattr(_preview_local, 'session')
    except AttributeError:
        pass


atexit.register(close_exiftool_sessions)


def _metadata_exiftool():
    global _metadata_session
    with _metadata_lock:
        if _metadata_session is None:
            _metadata_session = ExifToolSession()
        return _metadata_session


def _preview_exiftool():
    session = getattr(_preview_local, 'session', None)
    if session is None:
        session = ExifToolSession()
        _preview_local.session = session
    return session


def metadata_batch(paths):
    output = _metadata_exiftool().execute(
        ['-j', '-charset', 'filename=UTF8', '-Orientation#',
         *['-' + tag for tag in TAGS if tag != 'Orientation'], *map(str, paths)], timeout=180)
    try:
        records = json.loads(output)
    except (ValueError, UnicodeError) as exc:
        raise RuntimeError('ExifTool did not return valid metadata') from exc
    return {r['SourceFile']: r for r in records if 'SourceFile' in r}


def orient(image, orientation):
    embedded = image.getexif().get(274)
    if embedded:
        return ImageOps.exif_transpose(image)
    transforms = {2: Image.Transpose.FLIP_LEFT_RIGHT, 3: Image.Transpose.ROTATE_180,
                  4: Image.Transpose.FLIP_TOP_BOTTOM, 5: Image.Transpose.TRANSPOSE,
                  6: Image.Transpose.ROTATE_270, 7: Image.Transpose.TRANSVERSE,
                  8: Image.Transpose.ROTATE_90}
    return image.transpose(transforms[orientation]) if orientation in transforms else image


def open_preview(path, metadata):
    orientation = metadata.get('Orientation', 1)
    exiftool = _preview_exiftool()
    for tag in ('JpgFromRaw', 'PreviewImage'):
        data = exiftool.execute(['-b', '-' + tag, str(path)], timeout=90)
        if data:
            try:
                image = Image.open(io.BytesIO(data))
                image.load()
                return orient(image, orientation)
            except (OSError, ValueError):
                pass
    import rawpy
    with rawpy.imread(str(path)) as raw:
        try:
            thumb = raw.extract_thumb()
            image = (Image.open(io.BytesIO(thumb.data)) if thumb.format == rawpy.ThumbFormat.JPEG
                     else Image.fromarray(thumb.data))
            image.load()
            return orient(image, orientation)
        except (rawpy.LibRawNoThumbnailError, rawpy.LibRawUnsupportedThumbnailError, OSError):
            return Image.fromarray(raw.postprocess(use_camera_wb=True, half_size=True,
                                                   output_color=rawpy.ColorSpace.sRGB))


def cache_file(cache, key, kind):
    return Path(cache) / key[:2] / (key + '-' + kind + '.jpg')


def _srgb(image):
    profile = image.info.get('icc_profile')
    if profile:
        try:
            return ImageCms.profileToProfile(image, ImageCms.ImageCmsProfile(io.BytesIO(profile)),
                                             ImageCms.createProfile('sRGB'), outputMode='RGB')
        except (OSError, ValueError, ImageCms.PyCMSError):
            return image.convert('RGB')
    return image.convert('RGB')


def _save_scaled(image, output, edge, quality, optimize=True):
    output.parent.mkdir(parents=True, exist_ok=True)
    scaled = image.copy()
    try:
        scaled.thumbnail((edge, edge), Image.Resampling.LANCZOS)
        temp = output.with_suffix('.tmp')
        scaled.save(temp, 'JPEG', quality=quality, optimize=optimize)
        temp.replace(output)
    finally:
        scaled.close()


def make_thumbnail(path, metadata, thumbnail_cache, key):
    image = open_preview(path, metadata)
    try:
        converted = _srgb(image)
        try:
            _save_scaled(converted, cache_file(thumbnail_cache, key, 'thumb'), 480, 80,
                         optimize=False)
        finally:
            if converted is not image:
                converted.close()
    finally:
        image.close()


def make_preview(path, metadata, preview_cache, key, edge=None, quality=88):
    edge = int(edge or os.environ.get('PREVIEW_EDGE', '2560'))
    quality = int(quality)
    image = open_preview(path, metadata)
    try:
        converted = _srgb(image)
        try:
            _save_scaled(converted, cache_file(preview_cache, key, 'preview'), edge, quality,
                         optimize=True)
        finally:
            if converted is not image:
                converted.close()
    finally:
        image.close()


def make_previews(path, metadata, preview_cache, key, thumbnail_cache=None, preview_edge=None,
                  preview_quality=88):
    thumbnail_cache = thumbnail_cache or preview_cache
    edge = int(preview_edge or os.environ.get('PREVIEW_EDGE', '2560'))
    quality = int(preview_quality)
    image = open_preview(path, metadata)
    try:
        converted = _srgb(image)
        try:
            _save_scaled(converted, cache_file(preview_cache, key, 'preview'), edge, quality,
                         optimize=True)
            _save_scaled(converted, cache_file(thumbnail_cache, key, 'thumb'), 480, 80,
                         optimize=False)
        finally:
            if converted is not image:
                converted.close()
    finally:
        image.close()
