# Third-party components

OST Finder's own source is MIT licensed. This does not replace the licenses of bundled components.

- Python: PSF license; https://docs.python.org/3/license.html
- requests: Apache-2.0; https://github.com/psf/requests
- PySide6 / Shiboken6 6.10.3 and Qt: LGPL-3.0 / GPL alternatives as applicable. Source: https://code.qt.io/cgit/pyside/pyside-setup.git/ and https://code.qt.io/cgit/qt/ ; https://doc.qt.io/qtforpython-6/licenses.html . Qt is used as dynamically loaded libraries, not statically linked.
- Qt WebEngine embeds Chromium and its third-party components: https://doc.qt.io/qt-6/qtwebengine-licensing.html . The browser's `chrome://credits/` page contains the embedded component notices.
- PyInstaller bootloader: GPL with the PyInstaller exception; https://pyinstaller.org/en/stable/license.html . This exception permits distribution of applications under their own license.

LGPL-3.0 and GPL-3.0 text is supplied in `licenses/`. The portable build exposes the Qt DLLs under `_internal/PySide6/`; recipients can replace compatible libraries or rebuild using the provided Python source and build scripts. Reverse engineering for debugging modifications to LGPL components is not prohibited by this project. Upstream source corresponding to the bundled Qt/PySide version is available above (tag `v6.10.3`).

The standalone EXE extracts its runtime to a temporary directory at launch. Use the portable build or provided source to replace/rebuild components. The terminal-only executable does not bundle Qt or Chromium.
