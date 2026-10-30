"""Run repeated real-network searches through the actual desktop application."""
import argparse
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import QApplication
from ost_spider import SpiderWindow

parser = argparse.ArgumentParser()
parser.add_argument('--output', type=Path, default=Path('qa-output/live'))
parser.add_argument('--queries', nargs='+', default=['python requests library', 'github public api'])
args = parser.parse_args()
QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
app = QApplication([])
window = SpiderWindow(args.output, limit=3)
window.show()
remaining = iter(args.queries)
summary = []
started = 0


def start_next():
    global started
    query = next(remaining, None)
    if query is None:
        args.output.mkdir(parents=True, exist_ok=True)
        (args.output / 'verification.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps(summary, ensure_ascii=True, indent=2), flush=True)
        window.close()
        app.exit(0 if all(s['browser_findings'] and s['reports_saved'] for s in summary) else 1)
        return
    window.query.setText(query)
    window.start_search()
    started = time.monotonic()
    QTimer.singleShot(500, watch)


def watch():
    if window.busy:
        if time.monotonic() - started > 160:
            print('QA TIMEOUT: ' + window.status.text(), flush=True)
            import os
            os._exit(2)
        QTimer.singleShot(250, watch)
        return
    summary.append({
        'query': window.current_query, 'findings': len(window.findings),
        'elapsed_seconds': round(time.monotonic() - started, 2),
        'quality_pages': len(window.quality['results']),
        'extracted_pages': sum(bool(r['metadata']) for r in window.quality['results']),
        'filtered_pages': sum(r.get('filtered', False) for r in window.quality['results']),
        'browser_findings': sum(f.category == 'browser' for f in window.findings.values()),
        'off_the_record': window.browser.profile.isOffTheRecord(),
        'reports_saved': len(list(args.output.glob('ost_find_*.json'))) >= len(summary) + 1,
        'errors': window.errors,
    })
    window.tabs.setCurrentIndex(2)
    args.output.mkdir(parents=True, exist_ok=True)
    window.grab().save(str(args.output / f'quality-{len(summary)}.png'))
    QTimer.singleShot(500, start_next)


QTimer.singleShot(700, start_next)
raise SystemExit(app.exec())
