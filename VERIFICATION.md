# Verification — 2026-10-28

OST Finder 0.4.0 Spider, Windows x64, Python 3.13, PySide6 6.10.3, PyInstaller 6.22.3.

- 13 automated tests passed: core URL normalization, report persistence, repeated terminal searches, private browser profile, live DOM extraction against a controlled local HTTP fixture, page metadata, invalid payloads and repeated GUI reports.
- Two real-network searches in one source GUI session returned 25 and 26 findings, including 4 and 5 browser findings. Both saved reports; `isOffTheRecord()` was true and neither reported a source error.
- The packaged portable GUI ran a real search and saved 25 findings, 4 from the browser, with no source errors.
- The final standalone GUI EXE ran a real search and saved 25 findings, 4 from the browser, with no source errors. The floating mini-browser and animated spider were inspected through the Windows UI.
- The terminal EXE accepted `help` and `exit`, completed an IP search and wrote JSON/Markdown reports.
- Microsoft Defender custom scans of the GUI EXE, portable folder and terminal EXE found no matching detections.
- Every one of the 13 original release assets copied from Ideas (versions 0.1.0–0.3.0) matched its original size and GitHub SHA-256 digest.

Packaging fix: unrelated Poppler ICU DLLs inherited from the build host's PATH were excluded in the maintained spec. Qt uses Windows' system ICU; the Qt VC runtime versions are kept consistent in both build variants.

These are preview checks on this Windows computer, not a validation of every public website or every OS. Sources can change markup or availability. Historical release files were copied, not rebuilt or retested as new binaries.
