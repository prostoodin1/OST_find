# Build both GUI variants from a single dependency analysis.
import os
from pathlib import Path
import PySide6
from PyInstaller.utils.hooks import copy_metadata

root = Path(SPECPATH).parent
os.chdir(root)
datas = [(str(root / 'licenses'), 'licenses'), (str(root / 'README.md'), '.'), (str(root / 'THIRD_PARTY.md'), '.')]
datas += copy_metadata('requests')
datas += copy_metadata('PySide6')
a = Analysis([str(root / 'ost_spider.py')], pathex=[], binaries=[], datas=datas,
    hiddenimports=[], hookspath=[], hooksconfig={}, runtime_hooks=[],
    excludes=[], noarchive=False, optimize=0)

# Native Poppler tools on the build host supply an ICU with versioned symbols.
# Qt for Windows uses the OS ICU with unversioned symbols. Never ship the
# unrelated Poppler DLLs just because they happen to occur on the build PATH.
a.binaries = [entry for entry in a.binaries if 'poppler' not in entry[1].casefold()]
qt_dir = Path(PySide6.__file__).parent
for name in ('vcruntime140.dll', 'vcruntime140_1.dll'):
    bundled = qt_dir / name
    if bundled.exists():
        a.binaries = [entry for entry in a.binaries if entry[0].casefold() != name]
        a.binaries.append((name, str(bundled), 'BINARY'))

pyz = PYZ(a.pure)
options = dict(debug=False, bootloader_ignore_signals=False, strip=False,
    upx=False, console=False, disable_windowed_traceback=False,
    argv_emulation=False, target_arch=None, codesign_identity=None,
    entitlements_file=None, version=str(root / 'version_info.txt'))
portable = EXE(pyz, a.scripts, [], exclude_binaries=True,
    name='OST_Finder_Spider', **options)
folder = COLLECT(portable, a.binaries, a.datas, strip=False, upx=False,
    upx_exclude=[], name='OST_Finder_Spider')
standalone = EXE(pyz, a.scripts, a.binaries, a.datas, [],
    name='OST_Finder_Spider_0.4.0_x64', **options)
