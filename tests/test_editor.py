import pytest
from PIL import Image

import app.editor as editor
from app.editor import DEFAULT_SETTINGS, normalize_settings, _tone_image, _creative_image


def test_editor_settings_defaults_and_validation():
    assert normalize_settings({}) == DEFAULT_SETTINGS
    settings = normalize_settings({'exposure': '1.5', 'contrast': 20, 'highlights': -35, 'shadows': 40,
                                   'temperature': 7200, 'tint': -10, 'saturation': 25,
                                   'black': 5, 'white': 245, 'denoise': 40})
    assert settings['exposure'] == 1.5
    assert settings['contrast'] == 20
    assert settings['highlights'] == -35
    assert settings['shadows'] == 40
    assert settings['temperature'] == 7200
    assert settings['saturation'] == 25
    assert settings['denoise'] == 40
    with pytest.raises(ValueError):
        normalize_settings({'exposure': 5})
    with pytest.raises(ValueError):
        normalize_settings({'contrast': 101})
    with pytest.raises(ValueError):
        normalize_settings({'saturation': -101})
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


def test_editor_creative_controls_change_target_tones_and_color():
    image = Image.new('RGB', (3, 1))
    image.putdata([(32, 32, 32), (128, 96, 64), (224, 224, 224)])
    settings = normalize_settings({'shadows': 60, 'highlights': -60, 'contrast': 20, 'saturation': 40})
    result = _creative_image(image, settings)
    assert result.mode == 'RGB' and result.size == image.size
    # Positive shadows lift the dark sample; negative highlights pull down the bright sample.
    assert sum(result.getpixel((0, 0))) > sum(image.getpixel((0, 0)))
    assert sum(result.getpixel((2, 0))) < sum(image.getpixel((2, 0)))
    # Saturation/contrast also alter the coloured midtone.
    assert result.getpixel((1, 0)) != image.getpixel((1, 0))
    result.close()
    image.close()


def test_editor_working_preview_cache_reuses_raw_decode(tmp_path, monkeypatch):
    source = tmp_path / 'test.raw'
    source.write_bytes(b'raw')
    monkeypatch.setenv('CACHE_DIR', str(tmp_path / 'cache'))
    calls = []
    def fake_decode(path, denoise=0, half_size=False):
        calls.append((denoise, half_size))
        return Image.new('RGB', (120, 80), (100, 110, 120))
    monkeypatch.setattr(editor, '_decode_raw', fake_decode)
    first = editor.render(source, normalize_settings({'exposure': 0}), max_edge=1600)
    first.close()
    second = editor.render(source, normalize_settings({'exposure': 1, 'contrast': 25, 'saturation': 10}), max_edge=1600)
    second.close()
    # Tone/color controls must reuse the same decoded RAW working preview.
    assert len(calls) == 1
    third = editor.render(source, normalize_settings({'denoise': 70}), max_edge=1600)
    third.close()
    assert len(calls) == 2
