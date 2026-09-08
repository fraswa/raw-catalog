"""Small non-destructive RAW editor used by the web UI.

All operations read the original and write a new JPEG to the configured edits folder.
Original RAW files are never modified.
"""
import fcntl
import hashlib
import io
import math
import os
from pathlib import Path

from PIL import Image, ImageEnhance, ImageStat
import rawpy

DEFAULT_SETTINGS = {
    'exposure': 0.0,
    'contrast': 0.0,
    'highlights': 0.0,
    'shadows': 0.0,
    'temperature': 6500,
    'tint': 0.0,
    'saturation': 0.0,
    'black': 0,
    'white': 255,
    'denoise': 0,
}


def _number(data, name, cast, minimum, maximum):
    value = data.get(name, DEFAULT_SETTINGS[name])
    try:
        value = cast(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f'Invalid {name}') from exc
    if value < minimum or value > maximum:
        raise ValueError(f'{name.capitalize()} must be between {minimum} and {maximum}')
    return value


def normalize_settings(data):
    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise ValueError('Editor settings must be an object')
    result = {
        'exposure': _number(data, 'exposure', float, -4.0, 4.0),
        'contrast': _number(data, 'contrast', float, -100.0, 100.0),
        'highlights': _number(data, 'highlights', float, -100.0, 100.0),
        'shadows': _number(data, 'shadows', float, -100.0, 100.0),
        'temperature': _number(data, 'temperature', int, 2000, 12000),
        'tint': _number(data, 'tint', float, -100.0, 100.0),
        'saturation': _number(data, 'saturation', float, -100.0, 100.0),
        'black': _number(data, 'black', int, 0, 80),
        'white': _number(data, 'white', int, 128, 255),
        'denoise': _number(data, 'denoise', int, 0, 100),
    }
    if result['white'] - result['black'] < 16:
        raise ValueError('White point must be at least 16 levels above black point')
    return result


def _temperature_rgb(kelvin):
    """Approximate black-body RGB colour, normalized to 0..1."""
    temperature = max(1000, min(40000, kelvin)) / 100.0
    if temperature <= 66:
        red = 255.0
        green = 99.4708025861 * math.log(temperature) - 161.1195681661
        if temperature <= 19:
            blue = 0.0
        else:
            blue = 138.5177312231 * math.log(temperature - 10) - 305.0447927307
    else:
        red = 329.698727446 * ((temperature - 60) ** -0.1332047592)
        green = 288.1221695283 * ((temperature - 60) ** -0.0755148492)
        blue = 255.0
    return tuple(max(0.0, min(255.0, channel)) / 255.0 for channel in (red, green, blue))


def _channel_gains(settings):
    current = _temperature_rgb(settings['temperature'])
    neutral = _temperature_rgb(6500)
    gains = [current[index] / max(neutral[index], 0.01) for index in range(3)]
    tint = settings['tint'] / 100.0
    # Positive tint moves toward magenta, negative toward green.
    gains[0] *= 1.0 + 0.12 * tint
    gains[1] *= 1.0 - 0.24 * tint
    gains[2] *= 1.0 + 0.12 * tint
    exposure = 2.0 ** settings['exposure']
    return [max(0.20, min(5.0, gain * exposure)) for gain in gains]


def _tone_image(image, settings):
    gains = _channel_gains(settings)
    black = settings['black']
    white = settings['white']
    span = max(1, white - black)
    table = []
    for gain in gains:
        channel = []
        for value in range(256):
            adjusted = value * gain
            leveled = (adjusted - black) * 255.0 / span
            channel.append(max(0, min(255, int(round(leveled)))))
        table.extend(channel)
    return image.point(table)


def _shadow_highlight_lut(shadows, highlights):
    """Build a smooth RGB tone curve with anchored black/white endpoints.

    Shadows peak in the lower midtones; highlights peak in the upper midtones.
    Using the same LUT for every RGB channel keeps neutral colours neutral.
    """
    shadow_amount = float(shadows) / 100.0
    highlight_amount = float(highlights) / 100.0
    lut = []
    for value in range(256):
        x = value / 255.0
        shadow_weight = 4.0 * x * (1.0 - x) ** 2
        highlight_weight = 4.0 * x * x * (1.0 - x)
        y = x + 0.22 * (shadow_amount * shadow_weight + highlight_amount * highlight_weight)
        lut.append(max(0, min(255, int(round(y * 255.0)))))
    return lut


def _creative_image(image, settings):
    """Apply selective tone, contrast and colour controls after RAW levels/WB."""
    if settings['shadows'] or settings['highlights']:
        lut = _shadow_highlight_lut(settings['shadows'], settings['highlights'])
        result = image.point(lut * 3)
    else:
        result = image.copy()

    contrast = float(settings['contrast'])
    if contrast:
        adjusted = ImageEnhance.Contrast(result).enhance(max(0.0, 1.0 + contrast / 100.0))
        result.close()
        result = adjusted

    saturation = float(settings['saturation'])
    if saturation:
        adjusted = ImageEnhance.Color(result).enhance(max(0.0, 1.0 + saturation / 100.0))
        result.close()
        result = adjusted
    return result


def _denoise_passes(denoise):
    return min(3, max(0, int(round(float(denoise) / 34.0))))


def _decode_raw(path, denoise=0, half_size=False):
    passes = _denoise_passes(denoise)
    with rawpy.imread(str(path)) as raw:
        pixels = raw.postprocess(
            use_camera_wb=True,
            half_size=half_size,
            output_color=rawpy.ColorSpace.sRGB,
            output_bps=8,
            median_filter_passes=passes,
        )
    return Image.fromarray(pixels, 'RGB')


def _editor_work_root():
    root = Path(os.environ.get('CACHE_DIR', '/data/cache')) / 'editor-work'
    root.mkdir(parents=True, exist_ok=True)
    return root


def _preview_cache_file(path, denoise, max_edge):
    source = Path(path)
    stat = source.stat()
    signature = f'{source}:{stat.st_size}:{stat.st_mtime_ns}:{_denoise_passes(denoise)}:{int(max_edge)}'
    key = hashlib.sha256(signature.encode('utf-8', errors='surrogateescape')).hexdigest()
    return _editor_work_root() / key[:2] / f'{key}.jpg'


def _open_cached_rgb(path):
    with Image.open(path) as image:
        return image.convert('RGB')


def _load_cached_preview_base(path, denoise=0, max_edge=1600):
    """Decode the RAW once per source/denoise bucket and reuse a local working JPEG."""
    target = _preview_cache_file(path, denoise, max_edge)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.is_file():
        return _open_cached_rgb(target)

    lock_path = target.with_suffix('.lock')
    with open(lock_path, 'a+b') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if not target.is_file():
            image = _decode_raw(path, denoise, half_size=True)
            temp = target.with_suffix(f'.{os.getpid()}.tmp')
            try:
                image.thumbnail((max_edge, max_edge), Image.Resampling.LANCZOS)
                image.save(temp, 'JPEG', quality=92, optimize=False)
                os.replace(temp, target)
            finally:
                image.close()
                try:
                    temp.unlink()
                except FileNotFoundError:
                    pass
    return _open_cached_rgb(target)


def render(path, settings, max_edge=None):
    settings = normalize_settings(settings)
    image = (_load_cached_preview_base(path, settings['denoise'], max_edge)
             if max_edge else _decode_raw(path, settings['denoise'], half_size=False))
    try:
        toned = _tone_image(image, settings)
    finally:
        image.close()
    try:
        return _creative_image(toned, settings)
    finally:
        toned.close()


def render_preview(path, settings, max_edge=1600, quality=88):
    image = render(path, settings, max_edge=max_edge)
    try:
        output = io.BytesIO()
        image.save(output, 'JPEG', quality=quality, optimize=False)
        output.seek(0)
        return output
    finally:
        image.close()


def save_jpeg(path, settings, output_path, quality=92):
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    image = render(path, settings)
    temp = output_path.with_suffix('.tmp')
    try:
        image.save(temp, 'JPEG', quality=quality, optimize=True, subsampling=0)
        temp.replace(output_path)
    finally:
        image.close()
        try:
            temp.unlink()
        except FileNotFoundError:
            pass
    return output_path


def _percentile(histogram, fraction):
    total = sum(histogram)
    target = total * fraction
    seen = 0
    for index, count in enumerate(histogram):
        seen += count
        if seen >= target:
            return index
    return 255


def auto_settings(path, metadata=None):
    """Return conservative automatic exposure/levels settings from a quick RAW decode."""
    base = dict(DEFAULT_SETTINGS)
    image = _load_cached_preview_base(path, denoise=0, max_edge=1600)
    try:
        image.thumbnail((800, 800), Image.Resampling.BILINEAR)
        luminance = image.convert('L')
        try:
            histogram = luminance.histogram()
            mean = ImageStat.Stat(luminance).mean[0]
        finally:
            luminance.close()
    finally:
        image.close()

    low = _percentile(histogram, 0.01)
    high = _percentile(histogram, 0.995)
    base['exposure'] = round(max(-2.5, min(2.5, math.log2(112.0 / max(mean, 1.0)))), 2)
    base['black'] = max(0, min(40, low))
    base['white'] = max(base['black'] + 32, min(255, max(180, high)))

    iso = None
    if metadata:
        try:
            iso = float(metadata.get('ISO'))
        except (TypeError, ValueError):
            pass
    if iso is not None:
        if iso >= 6400:
            base['denoise'] = 60
        elif iso >= 3200:
            base['denoise'] = 40
        elif iso >= 1600:
            base['denoise'] = 20
    return base
