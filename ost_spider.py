"""OST Finder Spider: native desktop UI and an off-the-record Chromium browser."""
from __future__ import annotations

import argparse
import json
import math
import sys
import time
from pathlib import Path
from urllib.parse import quote_plus, urlparse

from PySide6.QtCore import QPointF, Qt, QThread, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QFont, QPainter, QPen
from PySide6.QtWidgets import (
    QApplication, QDockWidget, QFileDialog, QFrame, QHBoxLayout, QHeaderView,
    QLabel, QLineEdit, QMainWindow, QMessageBox, QPlainTextEdit, QProgressBar,
    QPushButton, QSpinBox, QStackedLayout, QTabWidget, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile, QWebEngineSettings
from PySide6.QtWebEngineWidgets import QWebEngineView

from ost_find import APP_VERSION, Finding, default_output_dir, normalize_result_url, run_search, save_reports


STYLE = """
QWidget { background:#0b111b; color:#d6e2f1; font-family:'Segoe UI'; font-size:13px; }
QMainWindow { background:#0b111b; }
QFrame#header { border-bottom:1px solid #263547; background:#101a28; }
QLabel#brand { font-family:'Consolas'; font-size:25px; font-weight:700; letter-spacing:2px; color:#e5f4ff; }
QLabel#eyebrow { color:#54d2ee; font-family:'Consolas'; font-size:11px; letter-spacing:2px; }
QLabel#muted { color:#8b9cb4; }
QLabel#badge { color:#7ee7bf; background:#14332f; border:1px solid #255246; border-radius:10px; padding:6px 12px; }
QLabel#title { font-size:21px; font-weight:600; }
QLabel#metric { font-family:'Consolas'; font-size:26px; color:#65dbef; }
QFrame#card { background:#101b2a; border:1px solid #243448; border-radius:9px; }
QFrame#card QLabel { background:transparent; }
QLineEdit, QSpinBox { background:#121f30; border:1px solid #314960; border-radius:6px; padding:10px; color:#e1f4ff; }
QLineEdit:focus { border:1px solid #55d2eb; }
QPushButton { background:#19283a; border:1px solid #31445a; border-radius:6px; padding:9px 13px; }
QPushButton:hover { background:#243d54; border-color:#56bacf; }
QPushButton:disabled { color:#668099; background:#152130; }
QPushButton#primary { background:#47c9e3; color:#07131d; font-weight:700; border:none; padding:12px 22px; }
QPushButton#primary:hover { background:#77e1f3; }
QPushButton#primary:disabled { background:#24576b; color:#93b4c1; }
QTabWidget::pane { border:1px solid #26384d; border-radius:6px; }
QTabBar::tab { background:#132133; padding:10px 18px; color:#8dabc0; }
QTabBar::tab:selected { color:#79dff3; border-bottom:2px solid #54d1e7; }
QTreeWidget { background:#0d1724; border:none; alternate-background-color:#111e2e; }
QTreeWidget::item { padding:9px 4px; border-bottom:1px solid #192b3c; }
QTreeWidget::item:selected { background:#1c4456; }
QHeaderView::section { background:#152538; padding:10px; color:#91b4cc; border:none; }
QPlainTextEdit { background:#0a121d; color:#92d4c1; font-family:'Consolas'; border:none; padding:8px; }
QProgressBar { background:#1a293a; border:none; border-radius:3px; max-height:6px; }
QProgressBar::chunk { background:#52d5c9; border-radius:3px; }
QDockWidget { color:#8ecfe0; font-family:'Consolas'; }
QDockWidget::title { background:#152538; padding:9px; }
QToolTip { background:#192b3f; color:#d6e8f4; border:1px solid #41677b; }
"""


def safe_web_url(value: str) -> str:
    try:
        value = normalize_result_url(value)
        parsed = urlparse(value)
        return value if parsed.hostname and not parsed.username and not parsed.password else ""
    except (ValueError, UnicodeError):
        return ''


class PublicPage(QWebEnginePage):
    def acceptNavigationRequest(self, url, navigation_type, is_main_frame):
        return url.scheme() in {"http", "https", "about", "data"} or url.toString() == 'chrome://credits/'

    def javaScriptConsoleMessage(self, level, message, line, source):
        # Page content is untrusted; it must not write to the app's operator log.
        pass


class SpiderOverlay(QWidget):
    def __init__(self, parent):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.active = False
        self.started = time.monotonic()
        self.timer = QTimer(self)
        self.timer.setInterval(33)
        self.timer.timeout.connect(self.animate)

    def animate(self):
        self.raise_()
        self.update()

    def set_active(self, active):
        self.active = active
        self.started = time.monotonic()
        self.setVisible(active)
        if active:
            self.timer.start()
        else:
            self.timer.stop()

    def paintEvent(self, event):
        if not self.active:
            return
        elapsed = time.monotonic() - self.started
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        width, height = self.width(), self.height()
        x = width / 2 + math.sin(elapsed * .52) * max(0, width / 2 - 65)
        y = height / 2 + math.sin(elapsed * .85) * max(0, height / 2 - 60)
        painter.setPen(QPen(QColor(78, 226, 206, 100), 1))
        painter.drawLine(QPointF(width / 2, 0), QPointF(x, y))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(6, 20, 30, 210))
        painter.drawEllipse(QPointF(x, y), 38, 38)
        painter.setPen(QPen(QColor('#5ff0cb'), 2.5))
        for side in (-1, 1):
            for leg in range(4):
                lift = math.sin(elapsed * 9 + leg * 1.6 + side) * 5
                shoulder = QPointF(x + side * 7, y - 8 + leg * 5)
                joint = QPointF(x + side * (20 + leg * 2), y - 26 + leg * 17 + lift)
                foot = QPointF(x + side * (32 - leg), y - 18 + leg * 13 - lift)
                painter.drawLine(shoulder, joint)
                painter.drawLine(joint, foot)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor('#52d9bc'))
        painter.drawEllipse(QPointF(x, y + 5), 9, 13)
        painter.drawEllipse(QPointF(x, y - 9), 7, 7)
        painter.setBrush(QColor('#081922'))
        painter.drawEllipse(QPointF(x - 3, y - 11), 1.5, 1.5)
        painter.drawEllipse(QPointF(x + 3, y - 11), 1.5, 1.5)


class BrowserView(QWebEngineView):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.spider = SpiderOverlay(self)
        self.spider.hide()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.spider.setGeometry(self.rect())
        self.spider.raise_()


class PrivateBrowser(QWidget):
    findings = Signal(list)
    completed = Signal(list)
    status = Signal(str)

    SEARCH_SCRIPT = """JSON.stringify(Array.from(document.querySelectorAll(
        'a[data-testid="result-title-a"], a.result__a:not(.related-searches__link), article h2 a, li.b_algo h2 a'
    )).slice(0,25).map(a => ({title:(a.innerText || a.textContent || '').trim(),url:a.href})))"""
    PAGE_SCRIPT = """JSON.stringify({title:document.title, url:location.href,
        summary:(document.querySelector('meta[name="description"]') || {}).content || ''})"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.busy = False
        self.generation = 0
        self.urls = []
        self.errors = []
        self.limit = 5
        self.stage = "idle"
        self.profile = QWebEngineProfile(self)  # Unnamed = off-the-record.
        self.profile.setPersistentCookiesPolicy(QWebEngineProfile.PersistentCookiesPolicy.NoPersistentCookies)
        self.profile.setHttpCacheType(QWebEngineProfile.HttpCacheType.MemoryHttpCache)
        self.profile.downloadRequested.connect(lambda download: download.cancel())
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        self.address = QLineEdit()
        self.address.setReadOnly(True)
        self.address.setPlaceholderText("Временный профиль · история не сохраняется")
        self.address.setAccessibleName("Адрес приватного браузера")
        layout.addWidget(self.address)
        self.view = BrowserView()
        page = PublicPage(self.profile, self.view)
        self.view.setPage(page)
        page.permissionRequested.connect(lambda permission: permission.deny())
        page.settings().setAttribute(QWebEngineSettings.WebAttribute.FullScreenSupportEnabled, False)
        self.view.setZoomFactor(.8)
        self.view.setMinimumSize(360, 310)
        self.view.urlChanged.connect(lambda url: self.address.setText(url.toString() if url.scheme() in {'http', 'https'} else ''))
        self.view.loadFinished.connect(self.loaded)
        # Keep the overlay above Chromium's replaceable rendering widget,
        # including when the dock becomes a separate native window.
        canvas = QWidget()
        stack = QStackedLayout(canvas)
        stack.setContentsMargins(0, 0, 0, 0)
        stack.setStackingMode(QStackedLayout.StackingMode.StackAll)
        stack.addWidget(self.view)
        self.view.spider.setParent(canvas)
        stack.addWidget(self.view.spider)
        stack.setCurrentWidget(self.view.spider)
        self.view.spider.hide()
        layout.addWidget(canvas, 1)
        self.note = QLabel("INCOGNITO · cookies и история хранятся в памяти")
        self.note.setObjectName('muted')
        self.note.setWordWrap(True)
        layout.addWidget(self.note)
        self.view.setHtml("""<html><body style='background:#0d1929;color:#8fb5cd;font:15px Segoe UI;padding:30px'>
            <p style='color:#5bd4e7;letter-spacing:4px'>PRIVATE WEB / SPIDER</p>
            <h2 style='color:#d8ecf8'>Окно наблюдения</h2>
            <p>Начните поиск — здесь откроется браузер.<br>Паук пройдёт по найденным страницам и соберёт ссылки.</p>
            <p style='font-size:12px;color:#69879e'>Публичные страницы · временная сессия · живой поиск</p>
            </body></html>""")

    def navigate(self, url, stage):
        self.generation += 1
        generation = self.generation
        self.stage = stage
        self.waiting_load = True
        self.view.load(QUrl(url))
        QTimer.singleShot(18000, lambda: self.expired(generation))

    def start(self, query, limit):
        self.busy = True
        self.errors = []
        self.urls = []
        self.limit = limit
        self.view.setEnabled(False)
        self.view.spider.set_active(True)
        self.status.emit('Браузер: открываю поисковую страницу')
        self.navigate('https://duckduckgo.com/?q=' + quote_plus(query) + '&ia=web', 'search')

    def expired(self, generation):
        if self.busy and generation == self.generation:
            self.waiting_load = False
            self.generation += 1
            self.view.stop()
            self.errors.append('Browser: страница не ответила за 18 секунд')
            self.next_page()

    def loaded(self, ok):
        if not self.busy or not self.waiting_load:
            return
        self.waiting_load = False
        generation = self.generation
        if not ok:
            self.errors.append('Browser: страница недоступна: ' + self.view.url().toString())
            self.next_page()
        elif self.stage == 'search':
            QTimer.singleShot(1600, lambda: self.extract_search(generation, 0))
        else:
            self.view.page().runJavaScript(self.PAGE_SCRIPT, lambda data: self.page_result(generation, data))

    def extract_search(self, generation, attempt):
        if not self.busy or generation != self.generation:
            return
        self.view.page().runJavaScript(self.SEARCH_SCRIPT, lambda rows: self.search_result(generation, attempt, rows))

    def search_result(self, generation, attempt, rows):
        if not self.busy or generation != self.generation:
            return
        try:
            rows = json.loads(rows) if isinstance(rows, str) else []
        except ValueError:
            rows = []
        valid = []
        seen = set()
        for row in rows if isinstance(rows, list) else []:
            if not isinstance(row, dict):
                continue
            url = safe_web_url(str(row.get('url', '')))
            title = str(row.get('title', '')).strip()[:300]
            hostname = (urlparse(url).hostname or '').casefold()
            is_search_navigation = hostname == 'duckduckgo.com' or hostname.endswith('.duckduckgo.com')
            if url and title and url not in seen and not is_search_navigation:
                valid.append(Finding('Browser / DuckDuckGo', title, url, category='browser'))
                seen.add(url)
            if len(valid) >= self.limit:
                break
        if not valid and attempt < 4:
            QTimer.singleShot(1000, lambda: self.extract_search(generation, attempt + 1))
            return
        if valid:
            self.findings.emit(valid)
            self.urls = [finding.url for finding in valid[:2]]
            self.status.emit(f'Браузер: собрано ссылок — {len(valid)}, проверяю страницы')
        else:
            self.errors.append('Browser: выдача не предоставила ссылок; возможна CAPTCHA или смена разметки')
        self.next_page()

    def page_result(self, generation, data):
        if not self.busy or generation != self.generation:
            return
        try:
            data = json.loads(data) if isinstance(data, str) else {}
        except ValueError:
            data = {}
        if isinstance(data, dict):
            url = safe_web_url(str(data.get('url', '')))
            if url:
                finding = Finding('Browser / Page', str(data.get('title') or url)[:300], url,
                                  str(data.get('summary') or '')[:500], 'browser')
                self.findings.emit([finding])
        QTimer.singleShot(900, self.next_page)

    def next_page(self):
        if not self.busy:
            return
        if self.urls:
            url = self.urls.pop(0)
            self.status.emit('Браузер: изучаю ' + (urlparse(url).hostname or url))
            self.navigate(url, 'page')
        else:
            self.generation += 1
            self.busy = False
            self.stage = 'idle'
            self.view.setEnabled(True)
            self.view.spider.set_active(False)
            self.completed.emit(self.errors)

    def preview(self, url):
        if not self.busy and (url := safe_web_url(url)):
            self.view.load(QUrl(url))


class SearchThread(QThread):
    progress = Signal(str, int, int)
    findings = Signal(list)
    result = Signal(str, list, list)
    failed = Signal(str)

    def __init__(self, query, limit, parent=None):
        super().__init__(parent)
        self.query, self.limit = query, limit

    def run(self):
        try:
            kind, findings, errors = run_search(self.query, self.limit,
                progress_callback=self.progress.emit, findings_callback=self.findings.emit)
            self.result.emit(kind, findings, errors)
        except Exception as error:
            self.failed.emit(str(error))


class SpiderWindow(QMainWindow):
    def __init__(self, output=None, limit=5):
        super().__init__()
        self.setWindowTitle(f'OST Finder {APP_VERSION} · Spider')
        self.setMinimumSize(1040, 720)
        self.resize(1400, 900)
        self.output = output or default_output_dir()
        self.findings = {}
        self.errors = []
        self.busy = False
        self.thread = None
        self.elapsed = QTimer(self)
        self.elapsed.timeout.connect(self.tick)
        self.elapsed.setInterval(250)
        self.setStyleSheet(STYLE)
        root = QWidget()
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(18)
        header = QHBoxLayout()
        brand = QLabel('OST FINDER')
        brand.setObjectName('brand')
        header.addWidget(brand)
        tag = QLabel('SPIDER / 0.4')
        tag.setObjectName('eyebrow')
        header.addWidget(tag)
        header.addStretch()
        self.badge = QLabel('●  ГОТОВ К ПОИСКУ')
        self.badge.setObjectName('badge')
        header.addWidget(self.badge)
        layout.addLayout(header)
        title = QLabel('Разведка открытых источников')
        title.setObjectName('title')
        layout.addWidget(title)
        description = QLabel('Один запрос. Публичные источники, живые страницы и собранные ссылки.')
        description.setObjectName('muted')
        layout.addWidget(description)
        search = QHBoxLayout()
        self.query = QLineEdit()
        self.query.setPlaceholderText('@username, example.com, email, IP или тема поиска…')
        self.query.setAccessibleName('Поисковый запрос')
        self.query.returnPressed.connect(self.start_search)
        search.addWidget(self.query, 1)
        self.limit = QSpinBox()
        self.limit.setRange(1, 25)
        self.limit.setValue(limit)
        self.limit.setPrefix('Лимит: ')
        self.limit.setToolTip('Максимальное число результатов на источник')
        search.addWidget(self.limit)
        self.search_button = QPushButton('Начать поиск  →')
        self.search_button.setObjectName('primary')
        self.search_button.clicked.connect(self.start_search)
        search.addWidget(self.search_button)
        layout.addLayout(search)
        metrics = QHBoxLayout()
        self.found_label = self.metric(metrics, 'СОБРАНО ССЫЛОК', '00')
        self.sources_label = self.metric(metrics, 'ИСТОЧНИКОВ ПРОВЕРЕНО', '0 / 0')
        self.time_label = self.metric(metrics, 'ВРЕМЯ ПОИСКА', '00:00')
        layout.addLayout(metrics)
        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.setValue(0)
        layout.addWidget(self.progress)
        self.tabs = QTabWidget()
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(['Источник', 'Название / найденный след', 'Ссылка'])
        self.tree.setRootIsDecorated(False)
        self.tree.setAlternatingRowColors(True)
        self.tree.setColumnWidth(0, 150)
        self.tree.setColumnWidth(1, 290)
        self.tree.header().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.tree.itemDoubleClicked.connect(self.preview_item)
        self.tabs.addTab(self.tree, 'Результаты · 0')
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(1000)
        self.tabs.addTab(self.log, 'Журнал источников')
        layout.addWidget(self.tabs, 1)
        self.status = QLabel('Введите запрос. Двойной щелчок по результату откроет его в приватном браузере.')
        self.status.setObjectName('muted')
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        actions = QHBoxLayout()
        for text, callback in [('Отчёты', self.open_reports), ('Куда сохранять…', self.select_output), ('Мини-браузер', self.toggle_browser), ('Как искать', self.show_help)]:
            button = QPushButton(text)
            button.clicked.connect(callback)
            actions.addWidget(button)
        actions.addStretch()
        hint = QLabel('JSON + MD  /  PRIVATE BROWSER')
        hint.setObjectName('eyebrow')
        actions.addWidget(hint)
        layout.addLayout(actions)
        self.browser = PrivateBrowser()
        self.dock = QDockWidget('ПРИВАТНЫЙ БРАУЗЕР  /  SPIDER', self)
        self.dock.setObjectName('privateBrowserDock')
        self.dock.setFeatures(QDockWidget.DockWidgetFeature.DockWidgetFloatable | QDockWidget.DockWidgetFeature.DockWidgetMovable)
        self.dock.setWidget(self.browser)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.dock)
        self.resizeDocks([self.dock], [465], Qt.Orientation.Horizontal)
        self.browser.findings.connect(self.add_findings)
        self.browser.status.connect(self.browser_status)
        self.browser.completed.connect(self.browser_done)
        self.log_line('Система готова. Cookies и история браузера — только в памяти; отчёты сохраняются на диск.')

    @staticmethod
    def metric(layout, caption, value):
        card = QFrame()
        card.setObjectName('card')
        box = QVBoxLayout(card)
        box.setContentsMargins(14, 12, 14, 12)
        label = QLabel(caption)
        label.setObjectName('eyebrow')
        box.addWidget(label)
        metric = QLabel(value)
        metric.setObjectName('metric')
        box.addWidget(metric)
        layout.addWidget(card, 1)
        return metric

    def log_line(self, value):
        self.log.appendPlainText(time.strftime('%H:%M:%S') + '  ' + value)

    def start_search(self):
        if self.busy:
            return
        query = self.query.text().strip()
        if not query:
            self.query.setFocus()
            self.status.setText('Введите @ник, домен, email, IP или обычный поисковый запрос.')
            return
        self.current_query = query
        self.query_type = 'text'
        self.findings = {}
        self.errors = []
        self.tree.clear()
        self.log.clear()
        self.started = time.monotonic()
        self.busy = True
        self.network_finished = False
        self.browser_finished = False
        self.search_button.setEnabled(False)
        self.query.setEnabled(False)
        self.limit.setEnabled(False)
        self.tree.setSortingEnabled(False)
        self.badge.setText('●  ПАУК ИЩЕТ')
        self.found_label.setText('00')
        self.tabs.setTabText(0, 'Результаты · 0')
        self.sources_label.setText('0 / …')
        self.progress.setValue(0)
        self.log_line('Запрос: ' + query)
        self.elapsed.start()
        self.show_mini_browser()
        self.thread = SearchThread(query, self.limit.value(), self)
        self.thread.progress.connect(self.source_progress)
        self.thread.findings.connect(self.add_findings)
        self.thread.result.connect(self.network_done)
        self.thread.failed.connect(self.network_error)
        self.thread.finished.connect(self.maybe_finish)
        self.thread.start()
        self.browser.start(query, self.limit.value())

    def add_findings(self, findings):
        for finding in findings:
            url = safe_web_url(finding.url)
            key = (url.casefold(), finding.title.casefold())
            if not url or key in self.findings:
                continue
            finding.url = url
            self.findings[key] = finding
            item = QTreeWidgetItem([finding.source, finding.title, url])
            item.setData(0, Qt.ItemDataRole.UserRole, url)
            item.setToolTip(1, finding.summary or finding.title)
            item.setToolTip(2, url)
            item.setForeground(2, QColor('#65d6e8'))
            self.tree.addTopLevelItem(item)
        count = len(self.findings)
        self.found_label.setText(f'{count:02}')
        self.tabs.setTabText(0, f'Результаты · {count}')

    def source_progress(self, message, done, total):
        self.sources_label.setText(f'{done} / {total}')
        self.progress.setValue(round(done / total * 100) if total else 0)
        if message.startswith('Checked '):
            self.log_line('Проверен источник: ' + message.removeprefix('Checked '))

    def browser_status(self, message):
        self.status.setText(message)
        self.log_line(message)

    def network_done(self, kind, findings, errors):
        self.query_type = kind
        self.add_findings(findings)
        self.errors.extend(errors)
        self.network_finished = True
        self.maybe_finish()

    def network_error(self, error):
        self.errors.append('Search: ' + error)
        self.network_finished = True
        self.maybe_finish()

    def browser_done(self, errors):
        self.errors.extend(errors)
        self.browser_finished = True
        self.maybe_finish()
        if self.busy:
            self.browser.view.spider.set_active(True)
            self.status.setText('Браузер проверен. Ожидаю ответы остальных источников…')

    def maybe_finish(self):
        if not self.busy or not self.network_finished or not self.browser_finished or (self.thread and self.thread.isRunning()):
            return
        self.busy = False
        self.browser.view.spider.set_active(False)
        self.elapsed.stop()
        self.tick()
        self.query.setEnabled(True)
        self.limit.setEnabled(True)
        self.search_button.setEnabled(True)
        self.tree.setSortingEnabled(True)
        self.badge.setText('●  ПОИСК ЗАВЕРШЁН')
        for error in self.errors:
            self.log_line('Недоступен источник: ' + error)
        try:
            json_path, md_path = save_reports(self.current_query, self.query_type, list(self.findings.values()), self.errors, self.output)
            self.log_line('JSON: ' + str(json_path.resolve()))
            self.log_line('Markdown: ' + str(md_path.resolve()))
            self.status.setText(f'Найдено {len(self.findings)} ссылок. Недоступных источников: {len(self.errors)}. Отчёты сохранены.')
        except OSError as error:
            self.status.setText('Не удалось сохранить отчёт. Выберите другую папку кнопкой «Куда сохранять…».')
            self.log_line('Ошибка сохранения: ' + str(error))
        self.query.setFocus()

    def tick(self):
        elapsed = int(time.monotonic() - self.started)
        self.time_label.setText(f'{elapsed // 60:02}:{elapsed % 60:02}')

    def preview_item(self, item, column):
        del column
        if not self.busy:
            self.browser.preview(item.data(0, Qt.ItemDataRole.UserRole))

    def open_reports(self):
        try:
            self.output.mkdir(parents=True, exist_ok=True)
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.output.resolve())))
        except OSError as error:
            self.status.setText('Не удалось открыть папку: ' + str(error))

    def show_mini_browser(self):
        self.dock.setFloating(True)
        self.dock.resize(560, 460)
        position = self.frameGeometry().topRight()
        self.dock.move(max(0, position.x() - 570), max(0, position.y() + 125))
        self.dock.show()

    def toggle_browser(self):
        if self.dock.isFloating():
            self.dock.setFloating(False)
        else:
            self.show_mini_browser()

    def select_output(self):
        folder = QFileDialog.getExistingDirectory(self, 'Папка для JSON и Markdown отчётов', str(self.output.resolve()))
        if folder:
            self.output = Path(folder)
            self.log_line('Папка отчётов: ' + folder)

    def show_help(self):
        QMessageBox.information(self, 'Как искать',
            '@username — публичные профили и репозитории\n'
            'example.com — домен, DNS, RDAP, сертификаты и веб\n'
            'name@example.com — публичные упоминания\n'
            '8.8.8.8 — IP, RDAP, reverse DNS и urlscan\n'
            'Любая тема — поиск по открытым источникам\n\n'
            'Двойной щелчок по результату — просмотр в приватном браузере.\n'
            'Двойной щелчок по заголовку браузера — отдельное маленькое окно.\n\n'
            'Паук собирает ссылки и сведения с двух найденных страниц.\n'
            'CAPTCHA и недоступные сайты отмечаются в журнале.\n'
            'Инкогнито не скрывает IP. Cookies не сохраняются, отчёты — сохраняются.')

    def closeEvent(self, event):
        if self.busy:
            self.status.setText('Поиск ещё выполняется. После завершения окно можно закрыть.')
            event.ignore()
            return
        self.browser.view.stop()
        super().closeEvent(event)


def main(argv=None):
    parser = argparse.ArgumentParser(description='OST Finder Spider — desktop OSINT')
    parser.add_argument('--output', type=Path)
    parser.add_argument('--limit', type=int, default=5)
    parser.add_argument('--version', action='version', version='OST Finder Spider ' + APP_VERSION)
    args = parser.parse_args(argv)
    QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setApplicationName('OST Finder Spider')
    app.setOrganizationName('OST Finder')
    app.setFont(QFont('Segoe UI', 10))
    window = SpiderWindow(args.output, max(1, min(args.limit, 25)))
    window.show()
    return app.exec()


if __name__ == '__main__':
    raise SystemExit(main())
