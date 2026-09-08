import pytest
from PIL import Image

from app.editor import DEFAULT_SETTINGS, normalize_settings, _tone_image


def test_editor_settings_defaults_and_validation():
    assert normalize_settings({}) == DEFAULT_SETTINGS
    settings = normalize_settings({'exposure': '1.5', 'temperature': 7200, 'tint': -10,
                                   'black': 5, 'white': 245, 'denoise': 40})
    assert settings['exposure'] == 1.5
    assert settings['temperature'] == 7200
    assert settings['denoise'] == 40
    with pytest.raises(ValueError):
        normalize_settings({'exposure': 5})
    with pytest.raises(ValueError):
        normalize_settings({'black': 80, 'white': 90})


def test_editor_tone_pipeline_keeps_rgb_image():
    image = Image.new('RGB', (8, 8), (100, 120, 140))
    result = _tone_image(image, normalize_settings({'exposure': 1, 'temperature': 8000,
                                                    'tint': 20, 'black': 3, 'white': 250}))
    assert result.mode == 'RGB'
    assert result.size == image.size
    assert result.getpixel((0, 0)) != image.getpixel((0, 0))
    result.close()
    image.close()
