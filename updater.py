"""Public GitHub release updater for standalone and directory Windows builds."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import tempfile
import time
from urllib.parse import urlsplit
import zipfile

import requests

REPO = 'prostoodin1/OST_find'
API = f'https://api.github.com/repos/{REPO}/releases/latest'
MAX_DOWNLOAD = 1024 * 1024 * 1024


def state_path():
    return Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) / 'OST Finder' / 'updates.json'


def read_settings():
    try:
        value = json.loads(state_path().read_text(encoding='utf-8'))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def set_auto_updates(enabled):
    state = read_settings()
    state['enabled'] = bool(enabled)
    path = state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(state), encoding='utf-8')
    temporary.replace(path)


def auto_updates_enabled():
    return read_settings().get('enabled', True) is True


def version_tuple(value):
    match = re.fullmatch(r'v?(\d+)\.(\d+)\.(\d+)', str(value))
    if not match:
        raise ValueError('Unsupported release version')
    return tuple(map(int, match.groups()))


def portable_build():
    return getattr(sys, 'frozen', False) and Path(getattr(sys, '_MEIPASS', '')).resolve() == Path(sys.executable).resolve().parent / '_internal'


def select_release(data, current_version, variant, portable=False):
    if data.get('draft') or data.get('prerelease'):
        return None
    tag = data.get('tag_name', '')
    version = version_tuple(tag)
    if version <= version_tuple(current_version):
        return None
    number = '.'.join(map(str, version))
    prefix = 'OST_Finder_Spider' if variant == 'gui' else 'OST_find'
    name = f'{prefix}_{number}_x64' + ('-portable.zip' if portable else '.exe')
    assets = [a for a in data.get('assets', []) if a.get('name') == name and a.get('state', 'uploaded') == 'uploaded']
    if len(assets) != 1:
        raise ValueError('Release is missing the matching Windows package')
    asset = assets[0]
    digest = asset.get('digest', '')
    if not re.fullmatch(r'sha256:[0-9a-fA-F]{64}', digest):
        raise ValueError('Release asset has no GitHub SHA-256 digest')
    expected_url = f'https://github.com/{REPO}/releases/download/{tag}/{name}'
    if asset.get('browser_download_url') != expected_url:
        raise ValueError('Unexpected update download origin')
    size = asset.get('size', 0)
    if not isinstance(size, int) or not 0 < size <= MAX_DOWNLOAD:
        raise ValueError('Unexpected update size')
    return {'version': number, 'name': name, 'url': expected_url, 'sha256': digest[7:].lower(),
            'size': size, 'portable': portable, 'variant': variant}


def check_release(current_version, variant, portable=None):
    # No credentials, no user queries, HTTPS certificate validation stays enabled.
    with requests.get(API, headers={'Accept': 'application/vnd.github+json', 'User-Agent': 'OST-Finder-Updater'}, timeout=5) as response:
        response.raise_for_status()
        if len(response.content) > 2000000:
            raise ValueError('Oversize release metadata')
        return select_release(response.json(), current_version, variant, portable_build() if portable is None else portable)


def extract_package(archive, destination):
    with zipfile.ZipFile(archive) as package:
        seen, total = set(), 0
        for entry in package.infolist():
            name = entry.filename
            path = PurePosixPath(name)
            if '\\' in name or ':' in name or path.is_absolute() or any(p in {'..', '.'} or p.endswith((' ', '.')) for p in path.parts):
                raise ValueError('Unsafe ZIP path')
            if not path.parts or any(p.split('.')[0].upper() in {'CON', 'PRN', 'AUX', 'NUL', *(f'COM{i}' for i in range(1, 10)), *(f'LPT{i}' for i in range(1, 10))} for p in path.parts):
                raise ValueError('Reserved ZIP path')
            if (entry.external_attr >> 16) & 0o170000 == 0o120000:
                raise ValueError('ZIP links are not supported')
            folded = str(path).casefold()
            if folded in seen:
                raise ValueError('Duplicate ZIP path')
            seen.add(folded)
            total += entry.file_size
            if total > 3 * MAX_DOWNLOAD or len(seen) > 15000:
                raise ValueError('Expanded package too large')
        package.extractall(destination)


def stage_update(release):
    if not getattr(sys, 'frozen', False) or os.name != 'nt':
        raise ValueError('Автоустановка доступна в Windows EXE; исходники обновляются через git pull')
    root = Path(tempfile.mkdtemp(prefix='OST-update-'))
    try:
        downloaded = root / release['name']
        digest, count = hashlib.sha256(), 0
        deadline = time.monotonic() + 600
        with requests.get(release['url'], stream=True, timeout=(10, 30), headers={'User-Agent': 'OST-Finder-Updater'}) as response:
            response.raise_for_status()
            with downloaded.open('wb') as output:
                for chunk in response.iter_content(1024 * 1024):
                    count += len(chunk)
                    if count > release['size'] or time.monotonic() > deadline:
                        raise ValueError('Download size/time limit exceeded')
                    digest.update(chunk)
                    output.write(chunk)
        if count != release['size'] or digest.hexdigest() != release['sha256']:
            raise ValueError('Update SHA-256/size mismatch; current version was not modified')
        contents = root / 'contents'
        contents.mkdir()
        executable = Path(sys.executable).resolve()
        if release['portable']:
            extract_package(downloaded, contents)
            package_exe = 'OST_Finder_Spider.exe' if release['variant'] == 'gui' else 'OST_find.exe'
            if not (contents / package_exe).is_file() or not (contents / '_internal').is_dir():
                raise ValueError('Incomplete portable package')
            # Only the known distributable entries are swapped. Reports and user files survive.
            allowed = {package_exe, '_internal', 'licenses', 'README.md', 'THIRD_PARTY.md', 'VERSIONS.md', 'LICENSE'}
            if any(p.name not in allowed for p in contents.iterdir()):
                raise ValueError('Unexpected portable package entry')
            if executable.name != package_exe:
                raise ValueError('Portable executable was renamed; use the original name')
        else:
            shutil.copy2(downloaded, contents / executable.name)
        # Ensure the target directory is writable before closing the app.
        with tempfile.TemporaryFile(dir=executable.parent):
            pass
        manifest = []
        for path in contents.rglob('*'):
            if path.is_file():
                digest = hashlib.sha256()
                with path.open('rb') as source:
                    for chunk in iter(lambda: source.read(1024 * 1024), b''):
                        digest.update(chunk)
                manifest.append({'path': path.relative_to(contents).as_posix(), 'sha256': digest.hexdigest()})
        config = {'pid': os.getpid(), 'target': str(executable.parent), 'executable': executable.name,
                  'staging': str(contents), 'backup': str(executable.parent / ('OST-update-backup-' + str(time.time_ns()))),
                  'manifest': manifest, 'version': release['version']}
        (root / 'install.json').write_text(json.dumps(config), encoding='utf-8')
        helper = Path(getattr(sys, '_MEIPASS', Path(__file__).parent)) / 'update_helper.ps1'
        shutil.copy2(helper, root / 'install.ps1')
        return root
    except Exception:
        shutil.rmtree(root, ignore_errors=True)
        raise


def schedule_install(root):
    root = Path(root).resolve()
    subprocess.Popen(['powershell.exe', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
                      '-File', str(root / 'install.ps1'), '-ConfigPath', str(root / 'install.json')],
                     creationflags=subprocess.CREATE_NO_WINDOW, close_fds=True)


def healthcheck_gui():
    """Run Qt and its WebEngine import from the new package before accepting update."""
    from PySide6.QtWidgets import QApplication
    from PySide6.QtWebEngineWidgets import QWebEngineView
    from PySide6.QtWebEngineCore import QWebEngineProfile
    app = QApplication.instance() or QApplication([])
    profile = QWebEngineProfile()
    assert profile.isOffTheRecord()
    assert QWebEngineView is not None
    return 0
