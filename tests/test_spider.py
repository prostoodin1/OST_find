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
        assert any(f.source == 'Browser / DuckDuckGo' and f.title == 'Public evidence' for f in captured)
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


def test_window_keeps_two_searches_and_reports_independent(app, tmp_path):
    window = SpiderWindow(output=tmp_path)
    for index in range(2):
        window.current_query = f'query-{index}'
        window.query_type = 'text'
        window.started = time.monotonic()
        window.findings.clear()
        window.tree.clear()
        window.busy = True
        window.network_finished = True
        window.browser_finished = True
        window.add_findings([Finding('Test', f'Result {index}', f'https://example.com/{index}')])
        window.maybe_finish()
        assert not window.busy
        assert window.search_button.isEnabled()
    reports = list(tmp_path.glob('*.json'))
    assert len(reports) == 2
    assert {json.loads(p.read_text(encoding='utf-8'))['query'] for p in reports} == {'query-0', 'query-1'}
    window.close()
