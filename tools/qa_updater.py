"""Run the real Windows installer in isolated folders, using disposable test EXEs."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
QA = ROOT / 'qa-output' / ('updater-' + str(time.time_ns()))
QA.mkdir(parents=True)
CSC = Path('C:/Windows/Microsoft.NET/Framework64/v4.0.30319/csc.exe')


def stub(path, label, fail=False):
    code = '''using System; using System.IO;
    class App { static int Main(string[] args) {
      if (Array.IndexOf(args, "--update-healthcheck") >= 0) return FAIL;
      File.WriteAllText(Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "started.txt"), "LABEL");
      return 0;
    }}'''.replace('FAIL', '1' if fail else '0').replace('LABEL', label)
    source = path.with_suffix('.cs')
    source.write_text(code)
    subprocess.run([str(CSC), '/nologo', '/target:winexe', '/out:' + str(path), str(source)], check=True, capture_output=True)
    source.unlink()


def run_case(name, fail=False, tamper=False):
    folder = QA / name
    target, staging = folder / "app space & ' quote", folder / 'stage'
    target.mkdir(parents=True)
    staging.mkdir()
    stub(target / 'app.exe', 'old')
    stub(staging / 'app.exe', 'new', fail)
    (target / 'reports').mkdir()
    (target / 'reports' / 'keep.txt').write_text('user report')
    (target / '_internal').mkdir()
    (target / '_internal' / 'old.txt').write_text('old dependency')
    (staging / '_internal').mkdir()
    (staging / '_internal' / 'new.txt').write_text('new dependency')
    original = hashlib.sha256((target / 'app.exe').read_bytes()).hexdigest()
    manifest = [{'path': p.relative_to(staging).as_posix(), 'sha256': hashlib.sha256(p.read_bytes()).hexdigest()} for p in staging.rglob('*') if p.is_file()]
    config = {'pid': 2147483000, 'target': str(target), 'staging': str(staging),
              'backup': str(target / 'OST-update-backup-test'), 'executable': 'app.exe', 'manifest': manifest, 'version': '9.0.0'}
    if tamper:
        (staging / '_internal' / 'new.txt').write_text('tampered')
    path = folder / 'install.json'
    path.write_text(json.dumps(config), encoding='utf-8')
    done = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File', str(ROOT / 'update_helper.ps1'), '-ConfigPath', str(path)],
                          timeout=70, capture_output=True, text=True, creationflags=subprocess.CREATE_NO_WINDOW)
    if done.returncode != (1 if fail or tamper else 0):
        raise AssertionError((done.returncode, done.stdout, done.stderr, (folder / 'install.log').read_text(encoding='utf-8-sig')))
    if not tamper:
        deadline = time.monotonic() + 5
        while not (target / 'started.txt').exists() and time.monotonic() < deadline:
            time.sleep(.05)
        assert (target / 'started.txt').read_text() == ('old' if fail else 'new')
    assert (target / 'reports' / 'keep.txt').read_text() == 'user report'
    if fail or tamper:
        assert hashlib.sha256((target / 'app.exe').read_bytes()).hexdigest() == original
        assert (target / '_internal' / 'old.txt').exists()
    else:
        assert (target / '_internal' / 'new.txt').exists()
        assert not (target / '_internal' / 'old.txt').exists()
    return {'case': name, 'exit_code': done.returncode, 'passed': True}


results = [run_case('success'), run_case('rollback', fail=True), run_case('tampered', tamper=True)]
(QA / 'verification.json').write_text(json.dumps(results, indent=2))
print(json.dumps({'output': str(QA), 'results': results}, indent=2))
