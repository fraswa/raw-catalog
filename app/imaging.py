"""Read-only metadata and preview extraction. Never writes to originals."""
import io
import json
import os
import subprocess
from pathlib import Path
from PIL import Image, ImageOps, ImageCms

EXTENSIONS = {'.cr2', '.cr3', '.crw', '.nef', '.nrw', '.arw', '.srf', '.sr2', '.dng',
              '.raf', '.orf', '.rw2', '.rwl', '.pef', '.ptx', '.srw', '.3fr', '.fff',
              '.iiq', '.kdc', '.dcr', '.mos', '.mrw', '.raw', '.x3f'}
TAGS = ['Make', 'Model', 'LensModel', 'LensID', 'Lens', 'LensType', 'DateTimeOriginal',
        'CreateDate', 'ISO', 'FNumber', 'ExposureTime', 'FocalLength', 'ImageWidth',
        'ImageHeight', 'Orientation', 'SerialNumber', 'LensSerialNumber', 'FileType']


def metadata_batch(paths):
    result = subprocess.run(['exiftool', '-j', '-charset', 'filename=UTF8', '-Orientation#',
                             *['-' + t for t in TAGS if t != 'Orientation'], *map(str, paths)],
                            capture_output=True, timeout=180, check=False)
    try:
        records = json.loads(result.stdout)
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
    for tag in ('JpgFromRaw', 'PreviewImage'):
        result = subprocess.run(['exiftool', '-b', '-' + tag, str(path)],
                                capture_output=True, timeout=90, check=False)
        if result.stdout:
            try:
                image = Image.open(io.BytesIO(result.stdout))
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


def _save_scaled(image, output, edge, quality):
    output.parent.mkdir(parents=True, exist_ok=True)
    scaled = image.copy()
    try:
        scaled.thumbnail((edge, edge), Image.Resampling.LANCZOS)
        temp = output.with_suffix('.tmp')
        scaled.save(temp, 'JPEG', quality=quality, optimize=True)
        temp.replace(output)
    finally:
        scaled.close()


def make_thumbnail(path, metadata, thumbnail_cache, key):
    image = open_preview(path, metadata)
    try:
        converted = _srgb(image)
        try:
            _save_scaled(converted, cache_file(thumbnail_cache, key, 'thumb'), 480, 80)
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
            _save_scaled(converted, cache_file(preview_cache, key, 'preview'), edge, quality)
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
            _save_scaled(converted, cache_file(preview_cache, key, 'preview'), edge, quality)
            _save_scaled(converted, cache_file(thumbnail_cache, key, 'thumb'), 480, 80)
        finally:
            if converted is not image:
                converted.close()
    finally:
        image.close()
