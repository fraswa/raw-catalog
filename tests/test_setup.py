import getpass
from pathlib import Path
import runpy
import sys

import pytest


SETUP = Path(__file__).resolve().parents[1] / 'setup.py'


def run_setup(monkeypatch, tmp_path, answers):
    photos = tmp_path / 'photos'
    photos.mkdir(exist_ok=True)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, 'argv', [str(SETUP), '--photos', str(photos)])
    responses = iter(answers)
    monkeypatch.setattr(getpass, 'getpass', lambda prompt: next(responses))
    runpy.run_path(str(SETUP), run_name='__main__')


def test_setup_saves_chosen_password_privately_without_printing_it(monkeypatch, tmp_path, capsys):
    password = 'test-private-$password'
    run_setup(monkeypatch, tmp_path, [password, password])
    contents = (tmp_path / '.env').read_text()
    assert f"INITIAL_ADMIN_PASSWORD='{password}'" in contents
    assert 'CATALOG_PASSWORD=' not in contents
    assert password not in capsys.readouterr().out


@pytest.mark.parametrize('answers', [
    ['short'], ['x' * 1025], ["test-password-with'quote"], ['test-password\nnewline'],
    ['test-initial-password', 'different-password'],
])
def test_invalid_setup_does_not_write_configuration(monkeypatch, tmp_path, answers):
    with pytest.raises(SystemExit):
        run_setup(monkeypatch, tmp_path, answers)
    assert not (tmp_path / '.env').exists()


def test_setup_preserves_existing_secrets(monkeypatch, tmp_path):
    existing = tmp_path / '.env'
    existing.write_text('existing-private-configuration')
    with pytest.raises(SystemExit):
        run_setup(monkeypatch, tmp_path, [])
    assert existing.read_text() == 'existing-private-configuration'
