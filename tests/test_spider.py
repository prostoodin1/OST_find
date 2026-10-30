import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import sys
import time

import pytest

pytest.importorskip('PySide6.QtWebEngineWidgets')
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from PySide6.QtWebEngineCore import QWebEngineProfile

from ost_spider import PrivateBrowser, SpiderWindow, safe_web_url
from ost_find import Finding
from spider_overlay import SpiderOverlay, parse_targets


@pytest.fixture(scope='module')
def app():
    QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
    return QApplication.instance() or QApplication(['spider-tests'])


def wait_until(app, condition, timeout=15):
    deadline = time.monotonic() + timeout
    while not condition() and time.monotonic() < deadline:
        app.processEvents()
        QTest.qWait(25)
    assert condition(), 'Qt operation timed out'


def test_web_url_rejects_local_files_and_embedded_credentials():
    assert safe_web_url('file:///C:/private.txt') == ''
    assert safe_web_url('javascript:alert(1)') == ''
    assert safe_web_url('http://[') == ''
    assert safe_web_url('https://user:password@example.com/') == ''
    assert safe_web_url('https://example.com/page?utm_source=test') == 'https://example.com/page'


def test_private_browser_uses_no_persistent_profile(app):
    browser = PrivateBrowser()
    assert browser.profile.isOffTheRecord()
    assert browser.profile.persistentCookiesPolicy() == QWebEngineProfile.PersistentCookiesPolicy.NoPersistentCookies
    assert browser.profile.httpCacheType() == QWebEngineProfile.HttpCacheType.MemoryHttpCache
    assert not browser.profile.storageName()
    browser.close()


def test_browser_collects_actual_dom_links_and_page_metadata(app):
    class Pages(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            if self.path == '/search':
                markup = '<a data-testid="result-title-a" href="/evidence">Public evidence</a>'
            else:
                markup = '<title>Evidence page</title><meta name="description" content="Verified browser extraction">'
            body = markup.encode()
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(('127.0.0.1', 0), Pages)
    serving = threading.Thread(target=server.serve_forever, daemon=True)
    serving.start()
    browser = PrivateBrowser()
    captured = []
    finished = []
    browser.resize(640, 480)
    browser.show()
    browser.findings.connect(captured.extend)
    browser.completed.connect(finished.append)
    try:
        # Let the initial about page finish before navigating to the controlled fixture.
        QTest.qWait(400)
        browser.busy = True
        browser.view.spider.set_active(True)
        browser.navigate(f'http://127.0.0.1:{server.server_port}/search', 'search')
        wait_until(app, lambda: bool(finished))
        assert finished == [[]]
        assert any(f.source == 'Browser / DOM link' and f.title == 'Public evidence' for f in captured)
        assert any(f.source == 'Browser / Page' and f.summary == 'Verified browser extraction' for f in captured)
        assert not browser.view.spider.timer.isActive()
    finally:
        browser.close()
        server.shutdown()
        server.server_close()
        serving.join()


def test_invalid_dom_payload_and_stale_generation_are_ignored(app):
    browser = PrivateBrowser()
    captured = []
    browser.findings.connect(captured.extend)
    browser.busy = True
    browser.generation = 3
    browser.search_result(2, 0, json.dumps([{'title': 'stale', 'url': 'https://example.com'}]))
    assert captured == []
    browser.search_result(3, 4, 'null')
    assert not browser.busy
    assert browser.errors
    browser.close()


def test_window_keeps_two_searches_and_reports_independent(app, tmp_path, monkeypatch):
    import ost_spider
    from search_quality import analyse
    monkeypatch.setattr(ost_spider, 'analyse', lambda query, findings, **kwargs: analyse(query, findings))
    window = SpiderWindow(output=tmp_path)
    for index in range(2):
        window.current_query = f'query-{index}'
        window.query_type = 'text'
        window.started = time.monotonic()
        window.findings.clear()
        window.tree.clear()
        window.busy = True
        window.quality = None
        window.quality_started = False
        window.network_finished = True
        window.browser_finished = True
        window.add_findings([Finding('Test', f'Result {index}', f'https://example.com/{index}')])
        window.maybe_finish()
        wait_until(app, lambda: not window.busy)
        assert not window.busy
        assert window.search_button.isEnabled()
    reports = list(tmp_path.glob('*.json'))
    assert len(reports) == 2
    assert {json.loads(p.read_text(encoding='utf-8'))['query'] for p in reports} == {'query-0', 'query-1'}
    window.close()


def test_no_save_mode_keeps_reports_off_disk(app, tmp_path, monkeypatch):
    import ost_spider
    from search_quality import analyse
    monkeypatch.setattr(ost_spider, 'analyse', lambda query, findings, **kwargs: analyse(query, findings))
    window = SpiderWindow(output=tmp_path)
    window.current_query = 'privacy test'
    window.query_type = 'text'
    window.started = time.monotonic()
    window.busy = window.network_finished = window.browser_finished = True
    window.save_enabled.setChecked(False)
    window.add_findings([Finding('Test', 'privacy test', 'https://example.com/privacy')])
    window.maybe_finish()
    wait_until(app, lambda: not window.busy)
    assert not list(tmp_path.iterdir())
    assert window.quality_view.toPlainText()
    window.close()


def test_update_waits_until_search_finishes(app, tmp_path, monkeypatch):
    import ost_spider
    calls = []
    monkeypatch.setattr(ost_spider, 'schedule_install', calls.append)
    window = SpiderWindow(output=tmp_path)
    window.pending_update = tmp_path
    window.busy = True
    window.apply_pending_update()
    assert calls == []
    window.busy = False
    window.apply_pending_update()
    assert calls == [tmp_path] and window.pending_update is None


def test_overlay_collects_only_after_contact_and_only_once(app):
    spider = SpiderOverlay()
    spider.resize(600, 450)
    captures = []
    spider.collected.connect(captures.append)
    spider.set_targets([{'text': 'Real DOM link', 'url': 'https://example.com/evidence',
                         'kind': 'link', 'rect': [60, 55, 130, 22]}])
    spider.advance(.05)
    assert captures == []
    for _ in range(160):
        spider.advance(.05)
    assert len(captures) == 1
    assert captures[0]['url'] == 'https://example.com/evidence'
    assert len(spider.feet) == 8
    assert spider.count == 1


def test_overlay_never_invents_data_when_no_dom_targets(app):
    spider = SpiderOverlay()
    spider.resize(600, 450)
    captures = []
    spider.collected.connect(captures.append)
    for _ in range(100):
        spider.advance(.05)
    assert captures == []
    assert spider.count == 0


def test_dom_geometry_validation_and_clipping():
    valid = {'text': 'Visible evidence', 'url': 'https://example.com', 'kind': 'link', 'rect': [-10, 20, 120, 30]}
    assert parse_targets([valid], 600, 450)[0].rect == (0, 20, 110, 30)
    assert parse_targets([{**valid, 'rect': [0, float('nan'), 80, 20]}], 600, 450) == []
    assert parse_targets([{**valid, 'rect': [0, 700, 80, 20]}], 600, 450) == []
    assert parse_targets([{**valid, 'url': 'file:///private.txt'}], 600, 450) == []
