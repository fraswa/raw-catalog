import io

import pytest
from PIL import Image

import app.chatgpt_export as export


@pytest.mark.parametrize('megapixels', [12, 15, 18])
@pytest.mark.parametrize('dimensions', [(6000, 4000), (4000, 6000), (8000, 3000), (3000, 3000), (1, 1)])
def test_export_preserves_aspect_ratio_budget_and_smaller_sources(megapixels, dimensions):
    width, height = export.export_dimensions(*dimensions, megapixels)
    assert width * height <= megapixels * 1_000_000
    assert width <= dimensions[0] and height <= dimensions[1]
    assert abs(width - height * dimensions[0] / dimensions[1]) <= 3
    if dimensions[0] * dimensions[1] <= megapixels * 1_000_000:
        assert (width, height) == dimensions


@pytest.mark.parametrize('value', [True, None, 11, 19, 12.5, '15', [], {}])
def test_export_rejects_invalid_size(value):
    with pytest.raises(ValueError):
        export.normalize_megapixels(value)


def test_export_writes_real_jpeg_with_srgb_and_no_source_metadata(tmp_path):
    output = tmp_path / 'exports' / 'portrait.jpg'
    with Image.new('RGB', (6000, 4000), (90, 120, 150)) as image:
        exif = Image.Exif()
        exif[315] = 'Private photographer'
        exif[37510] = b'Private comment'
        image.info['exif'] = exif.tobytes()
        image.info['comment'] = b'Private comment'
        original_size = image.size
        details = export.save_export(image, output, 12)
        assert image.size == original_size
        assert image.info['exif'] == exif.tobytes()
    with Image.open(output) as saved:
        assert saved.format == 'JPEG' and saved.mode == 'RGB'
        assert saved.width * saved.height <= 12_000_000
        assert saved.size == (details['width'], details['height'])
        assert saved.getexif() == {}
        assert 'comment' not in saved.info
        assert saved.info['icc_profile'] == export.SRGB_PROFILE
    assert details['bytes'] == output.stat().st_size <= export.MAX_EXPORT_BYTES
    assert details['quality'] == 95
    assert not list(output.parent.glob('*.tmp'))


def test_export_reduces_quality_when_needed_without_changing_dimensions(tmp_path, monkeypatch):
    with Image.effect_noise((256, 256), 100).convert('RGB') as image:
        sizes = {}
        for quality in (95, 80):
            buffer = io.BytesIO()
            image.save(buffer, 'JPEG', quality=quality, optimize=True, subsampling=0,
                       icc_profile=export.SRGB_PROFILE, exif=b'')
            sizes[quality] = buffer.tell()
        limit = (sizes[80] + sizes[95]) // 2
        monkeypatch.setattr(export, 'MAX_EXPORT_BYTES', limit)
        output = tmp_path / 'noise.jpg'
        details = export.save_export(image, output, 15)
    assert details['quality'] < 95
    assert details['bytes'] <= limit
    with Image.open(output) as saved:
        assert saved.size == (256, 256)


def test_failed_export_preserves_existing_file_and_cleans_temporary_files(tmp_path, monkeypatch):
    output = tmp_path / 'existing.jpg'
    output.write_bytes(b'previous-jpeg')
    monkeypatch.setattr(export, 'MAX_EXPORT_BYTES', 1)
    with Image.new('RGB', (32, 32)) as image, pytest.raises(ValueError, match='smaller'):
        export.save_export(image, output, 12)
    assert output.read_bytes() == b'previous-jpeg'
    assert list(tmp_path.iterdir()) == [output]
