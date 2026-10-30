import hashlib
import json
from pathlib import Path
import zipfile
import pytest
import updater


def release(portable=False):
    name = 'OST_Finder_Spider_0.7.0_x64' + ('-portable.zip' if portable else '.exe')
    return {'tag_name': 'v0.7.0', 'assets': [{'name': name, 'size': 100,
        'digest': 'sha256:' + 'a' * 64,
        'browser_download_url': f'https://github.com/prostoodin1/OST_find/releases/download/v0.7.0/{name}'}]}


def test_version_selection_and_no_downgrades():
    assert updater.select_release(release(), '0.6.0', 'gui')['version'] == '0.7.0'
    assert updater.select_release(release(), '0.7.0', 'gui') is None
    assert updater.select_release({**release(), 'prerelease': True}, '0.6.0', 'gui') is None
    assert updater.select_release(release(True), '0.6.0', 'gui', True)['portable']


def test_untrusted_update_origin_or_missing_digest_rejected():
    data = release()
    data['assets'][0]['browser_download_url'] = 'https://evil.example/update.exe'
    with pytest.raises(ValueError): updater.select_release(data, '0.6.0', 'gui')
    data = release()
    del data['assets'][0]['digest']
    with pytest.raises(ValueError): updater.select_release(data, '0.6.0', 'gui')
    with pytest.raises(ValueError): updater.select_release(release(), '0.6.0', 'cli')


@pytest.mark.parametrize('name', ['../escape.exe', '/absolute.exe', 'C:/escape.exe', 'safe\\..\\escape.exe', 'CON.txt', 'safe./file'])
def test_zip_traversal_and_windows_special_paths_rejected(tmp_path, name):
    archive = tmp_path / 'bad.zip'
    with zipfile.ZipFile(archive, 'w') as package: package.writestr(name, b'bad')
    with pytest.raises(ValueError): updater.extract_package(archive, tmp_path / 'output')
    assert not (tmp_path / 'escape.exe').exists()


def test_zip_case_collisions_rejected(tmp_path):
    archive = tmp_path / 'bad.zip'
    with zipfile.ZipFile(archive, 'w') as package:
        package.writestr('A.dll', b'a')
        package.writestr('a.dll', b'b')
    with pytest.raises(ValueError): updater.extract_package(archive, tmp_path / 'output')


def test_settings_can_disable_updates_without_query_data(tmp_path, monkeypatch):
    monkeypatch.setattr(updater, 'state_path', lambda: tmp_path / 'settings.json')
    updater.set_auto_updates(False)
    assert not updater.auto_updates_enabled()
    assert json.loads((tmp_path / 'settings.json').read_text()) == {'enabled': False}


@pytest.mark.parametrize('corrupt', [False, True])
def test_download_is_verified_before_any_installed_file_changes(tmp_path, monkeypatch, corrupt):
    installed = tmp_path / 'installed.exe'
    installed.write_bytes(b'old app')
    payload = b'new app package'
    class Response:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def raise_for_status(self): pass
        def iter_content(self, size): yield payload
    monkeypatch.setattr(updater.sys, 'frozen', True, raising=False)
    monkeypatch.setattr(updater.sys, 'executable', str(installed))
    monkeypatch.setattr(updater.sys, '_MEIPASS', str(Path(updater.__file__).parent), raising=False)
    monkeypatch.setattr(updater.requests, 'get', lambda *a, **kw: Response())
    info = {'name': 'new.exe', 'url': 'https://github.com/test', 'size': len(payload), 'version': '0.7.0',
            'sha256': ('0' * 64 if corrupt else hashlib.sha256(payload).hexdigest()), 'portable': False, 'variant': 'cli'}
    if corrupt:
        with pytest.raises(ValueError, match='mismatch'): updater.stage_update(info)
    else:
        root = updater.stage_update(info)
        assert (root / 'contents' / installed.name).read_bytes() == payload
        assert (root / 'install.ps1').exists()
        assert json.loads((root / 'install.json').read_text())['manifest'][0]['sha256'] == info['sha256']
    assert installed.read_bytes() == b'old app'
