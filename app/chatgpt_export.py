"""Local JPEG preparation and a reusable cosplay photo-editing prompt.

This module makes no network requests. Uploads happen manually in ChatGPT.
"""
import math
import os
from pathlib import Path
import tempfile

from PIL import Image, ImageCms

MEGAPIXEL_OPTIONS = (12, 15, 18)
DEFAULT_MEGAPIXELS = 15
MAX_EXPORT_BYTES = 19_000_000
SRGB_PROFILE = ImageCms.ImageCmsProfile(ImageCms.createProfile('sRGB')).tobytes()

COSPLAY_PROMPT = """Edit the attached cosplay photograph using image editing and generate the finished image. Use this photograph as the source, rather than inventing a new scene. Do the edit now; do not return only advice, an editing recipe, or a description.

Create a polished, photorealistic cosplay portrait with natural professional retouching and a subtle cinematic finish. Improve exposure, white balance, contrast, highlight recovery, shadow detail, and color balance. Keep skin tones believable and preserve intentional colored lighting. Enhance costume, wig, fabric, armor, and prop details without oversharpening or oversaturating them.

Preserve the person's identity, facial features, expression, age, body proportions, pose, hands, and skin texture. Preserve cosplay makeup, contact lenses, tattoos, scars, costume colors and design, accessories, props, and character-specific details. Retouch only small temporary blemishes and distracting stray hairs conservatively; avoid plastic skin, face reshaping, beauty-filter effects, and changes to the outfit or body.

Keep the original framing, orientation, aspect ratio, and location. Reduce minor background distractions only where this can be done naturally. Do not replace the background, remove other people, add characters or objects, invent costume details, or add text, borders, or watermarks. Keep the result looking like a real photograph, rather than an illustration or anime rendering.

Return one finished edited image and a downloadable high-quality JPEG in sRGB. Use the highest native output resolution available while preserving the composition; do not claim the output retains the source megapixel count if it does not. If the image tool produces PNG or WebP, convert the finished image to JPEG when file tools are available. If JPEG conversion is unavailable, return the edited image in its available format and state that limitation briefly."""


def normalize_megapixels(value=DEFAULT_MEGAPIXELS):
    if isinstance(value, bool) or not isinstance(value, int) or value not in MEGAPIXEL_OPTIONS:
        raise ValueError('JPEG size must be 12, 15 or 18 megapixels')
    return value


def export_dimensions(width, height, megapixels):
    """Fit within a pixel budget, preserving proportions and never upscaling."""
    budget = normalize_megapixels(megapixels) * 1_000_000
    if width * height <= budget:
        return width, height
    scale = math.sqrt(budget / (width * height))
    return max(1, math.floor(width * scale)), max(1, math.floor(height * scale))


def save_export(image, output_path, megapixels=DEFAULT_MEGAPIXELS):
    """Save rendered sRGB pixels atomically, without source EXIF or GPS data."""
    megapixels = normalize_megapixels(megapixels)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    dimensions = export_dimensions(*image.size, megapixels)
    rgb = image.convert('RGB')
    temp = None
    try:
        if rgb.size != dimensions:
            resized = rgb.resize(dimensions, Image.Resampling.LANCZOS)
            rgb.close()
            rgb = resized
        rgb.info.clear()
        with tempfile.NamedTemporaryFile(dir=output_path.parent, prefix='.chatgpt-', suffix='.tmp', delete=False) as file:
            temp = Path(file.name)
        for quality in (95, 92, 90, 85, 80):
            rgb.save(temp, 'JPEG', quality=quality, optimize=True, subsampling=0,
                     icc_profile=SRGB_PROFILE, exif=b'')
            size = temp.stat().st_size
            if size <= MAX_EXPORT_BYTES:
                break
        else:
            raise ValueError('JPEG exceeds 19 MB; choose a smaller megapixel size')
        os.replace(temp, output_path)
        width, height = dimensions
        return dict(width=width, height=height, megapixels=round(width * height / 1_000_000, 2),
                    target_megapixels=megapixels, bytes=size, quality=quality)
    finally:
        rgb.close()
        if temp is not None:
            try:
                temp.unlink()
            except FileNotFoundError:
                pass
