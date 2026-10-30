"""OST Finder Spider: native desktop UI and an off-the-record Chromium browser."""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from urllib.parse import quote_plus, urlparse

from PySide6.QtCore import Qt, QThread, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QFont
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDockWidget, QFileDialog, QFrame, QHBoxLayout, QHeaderView,
    QLabel, QLineEdit, QMainWindow, QMessageBox, QPlainTextEdit, QProgressBar,
    QPushButton, QSpinBox, QStackedLayout, QTabWidget, QTextBrowser, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile, QWebEngineSettings
from PySide6.QtWebEngineWidgets import QWebEngineView

from ost_find import APP_VERSION, Finding, default_output_dir, normalize_result_url, run_search, save_reports
from spider_overlay import SpiderOverlay as WireframeSpider
from search_quality import analyse, llm_review, html_report, parse_query
from updater import auto_updates_enabled, set_auto_updates, check_release, stage_update, schedule_install


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
QTextBrowser { background:#0d1724; color:#c7d9e8; border:none; padding:12px; font-size:14px; }
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


class BrowserView(QWebEngineView):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.spider = WireframeSpider(self)
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
        self.tour_timer = QTimer(self)
        self.tour_timer.setInterval(250)
        self.tour_timer.timeout.connect(self.poll_targets)
        self.search_deadline = QTimer(self)
        self.search_deadline.setSingleShot(True)
        self.search_deadline.timeout.connect(self.end_budget)
        self.tour_kind = 'search'
        self.poll_pending = False
        self.tour_count = 0
        self.empty_polls = 0
        self.tour_started = 0
        self.tour_callback = None
        resource_root = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent))
        self.targets_script = (resource_root / 'browser_targets.js').read_text(encoding='utf-8')
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
        dark_mode = getattr(QWebEngineSettings.WebAttribute, 'ForceDarkMode', None)
        if dark_mode is not None:
            page.settings().setAttribute(dark_mode, True)
        self.view.setZoomFactor(.8)
        self.view.setMinimumSize(360, 310)
        self.view.urlChanged.connect(lambda url: self.address.setText(url.toString() if url.scheme() in {'http', 'https'} else ''))
        self.view.loadFinished.connect(self.loaded)
        self.view.spider.collected.connect(self.capture_target)
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
        self.tour_timer.stop()
        self.view.spider.reset_page()
        self.generation += 1
        generation = self.generation
        self.stage = stage
        self.waiting_load = True
        self.view.load(QUrl(url))
        QTimer.singleShot(18000, lambda: self.expired(generation))

    def start(self, query, limit):
        self.search_deadline.start(20000)
        self.busy = True
        self.errors = []
        self.urls = []
        self.limit = limit
        self.view.spider.reset_session()
        self.view.setEnabled(False)
        self.view.spider.set_active(True)
        self.status.emit('Браузер: открываю поисковую страницу')
        self.navigate('https://duckduckgo.com/?q=' + quote_plus(query) + '&ia=web', 'search')

    def end_budget(self):
        if self.busy:
            self.errors.append('Browser: time budget exceeded (20s), partial results retained')
            self.urls.clear()
            self.waiting_load = False
            self.view.stop()
            self.next_page()

    def expired(self, generation):
        if self.busy and generation == self.generation and self.stage != 'tour':
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
            self.urls = [finding.url for finding in valid[:2]]
            self.status.emit(f'Браузер: вижу {len(valid)} ссылок, паук собирает элементы')
            self.start_tour('search', self.next_page)
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
        self.start_tour('page', self.next_page)

    def start_tour(self, kind, callback):
        self.stage = 'tour'
        self.tour_kind = kind
        self.tour_count = 0
        self.empty_polls = 0
        self.poll_pending = False
        self.tour_started = time.monotonic()
        self.tour_callback = callback
        self.tour_timer.start()
        self.poll_targets()

    def poll_targets(self):
        if not self.busy or self.stage != 'tour':
            return
        if time.monotonic() - self.tour_started > 12:
            self.finish_tour()
            return
        if self.poll_pending:
            return
        self.poll_pending = True
        generation = self.generation
        script = self.targets_script.replace('__ZOOM__', str(self.view.zoomFactor())).replace('__SEARCH__', 'true' if self.tour_kind == 'search' else 'false')
        self.view.page().runJavaScript(script, lambda data: self.received_targets(generation, data))

    def received_targets(self, generation, payload):
        if not self.busy or generation != self.generation or self.stage != 'tour':
            return
        self.poll_pending = False
        try:
            data = json.loads(payload) if isinstance(payload, str) else {}
        except ValueError:
            data = {}
        rows = data.get('targets', []) if isinstance(data, dict) else []
        self.view.spider.set_targets(rows)
        if not self.view.spider.targets or all(target.key in self.view.spider.seen for target in self.view.spider.targets):
            self.empty_polls += 1
            if self.empty_polls >= 8:
                self.finish_tour()
        else:
            self.empty_polls = 0

    def capture_target(self, target):
        if not self.busy or self.stage != 'tour':
            return
        goal = min(self.limit, 4) if self.tour_kind == 'search' else 3
        if self.tour_count >= goal:
            return
        url = safe_web_url(str(target.get('url') or ''))
        text = str(target.get('text') or '').strip()
        if not url or not text:
            return
        source = 'Browser / DOM link' if target.get('kind') == 'link' else 'Browser / Text'
        self.findings.emit([Finding(source, text[:150], url, text[:350], 'browser')])
        self.tour_count += 1
        self.status.emit(f'Паук захватил: {text[:75]}')
        if self.tour_count >= goal:
            self.tour_timer.stop()
            self.view.spider.set_targets([])
            generation = self.generation
            QTimer.singleShot(1500, lambda: self.finish_tour() if generation == self.generation else None)
        elif self.tour_count % 2 == 0 and self.tour_kind != 'search':
            self.view.page().runJavaScript('window.scrollBy({top:innerHeight*.35,behavior:"smooth"})')

    def finish_tour(self):
        if not self.busy or self.stage != 'tour':
            return
        self.tour_timer.stop()
        self.stage = 'between-pages'
        callback, self.tour_callback = self.tour_callback, None
        self.view.spider.reset_page()
        if callback:
            callback()

    def next_page(self):
        if not self.busy:
            return
        if self.urls:
            url = self.urls.pop(0)
            self.status.emit('Браузер: изучаю ' + (urlparse(url).hostname or url))
            self.navigate(url, 'page')
        else:
            self.search_deadline.stop()
            self.tour_timer.stop()
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


class QualityThread(QThread):
    result = Signal(dict)

    def __init__(self, query, findings, provider='', model='', parent=None):
        super().__init__(parent)
        self.query, self.findings, self.provider, self.model = query, findings, provider, model

    def run(self):
        try:
            quality = analyse(self.query, self.findings, extract=True)
            if self.provider:
                try:
                    quality['llm'] = llm_review(quality, self.provider, self.model)
                except Exception as error:
                    quality['llm_error'] = type(error).__name__
        except Exception as error:
            quality = analyse(self.query, self.findings)
            quality['analysis_error'] = type(error).__name__
        self.result.emit(quality)


class UpdateThread(QThread):
    result = Signal(str, object)

    def run(self):
        try:
            release = check_release(APP_VERSION, 'gui')
            if not release:
                self.result.emit('Установлена последняя версия.', None)
            elif not getattr(sys, 'frozen', False):
                self.result.emit('Доступна ' + release['version'] + '. Для исходников: git pull; автоустановка работает в EXE.', None)
            else:
                self.result.emit('Обновление ' + release['version'] + ' загружается и проверяется…', None)
                self.result.emit('Обновление проверено. Приложение перезапустится после завершения поиска.', stage_update(release))
        except Exception as error:
            self.result.emit('Обновление недоступно: ' + type(error).__name__ + '. Текущая версия сохранена.', None)


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
        self.quality_thread = None
        self.update_thread = None
        self.pending_update = None
        self.quality = None
        self.quality_started = False
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
        tag = QLabel('WIREFRAME / ' + APP_VERSION)
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
        self.context = QLineEdit()
        self.context.setPlaceholderText('Контекст для отсечения тёзок: компания, город, профессия (необязательно)')
        layout.addWidget(self.context)
        options = QHBoxLayout()
        self.save_enabled = QCheckBox('Сохранять отчёты на этом компьютере')
        self.save_enabled.setChecked(True)
        self.save_enabled.setToolTip('Снимите флажок, чтобы запрос и результаты оставались только в памяти приложения.')
        options.addWidget(self.save_enabled)
        self.llm_provider = QComboBox()
        self.llm_provider.addItems(['Без LLM · 0 запросов', 'Ollama · локально', 'OpenRouter · отправка выдержек'])
        options.addWidget(self.llm_provider)
        self.llm_model = QLineEdit()
        self.llm_model.setPlaceholderText('Модель для LLM (1 запрос на поиск)')
        options.addWidget(self.llm_model)
        layout.addLayout(options)
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
        self.quality_view = QTextBrowser()
        self.quality_view.setReadOnly(True)
        self.quality_view.setOpenLinks(False)
        self.quality_view.anchorClicked.connect(lambda url: self.browser.preview(url.toString()) if not self.busy else None)
        self.tabs.addTab(self.quality_view, 'Качество и сводка')
        layout.addWidget(self.tabs, 1)
        self.status = QLabel('Введите запрос. Двойной щелчок по результату откроет его в приватном браузере.')
        self.status.setObjectName('muted')
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        actions = QHBoxLayout()
        for text, callback in [('Отчёты', self.open_reports), ('Куда сохранять…', self.select_output), ('Мини-браузер', self.toggle_browser), ('Как искать', self.show_help), ('Обновить', self.check_updates)]:
            button = QPushButton(text)
            button.clicked.connect(callback)
            actions.addWidget(button)
        actions.addStretch()
        hint = QLabel('JSON + MD  /  PRIVATE BROWSER')
        hint.setObjectName('eyebrow')
        actions.addWidget(hint)
        layout.addLayout(actions)
        self.auto_update = QCheckBox('Автоматически проверять, устанавливать обновления и перезапускать приложение после поиска')
        self.auto_update.setChecked(auto_updates_enabled())
        self.auto_update.toggled.connect(self.change_auto_updates)
        layout.addWidget(self.auto_update)
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

    def change_auto_updates(self, enabled):
        try:
            set_auto_updates(enabled)
        except OSError:
            self.log_line('Не удалось сохранить настройку обновлений.')

    def check_updates(self):
        if self.update_thread and self.update_thread.isRunning():
            return
        self.update_thread = UpdateThread(self)
        self.update_thread.result.connect(self.update_result)
        self.update_thread.finished.connect(self.apply_pending_update)
        self.update_thread.start()
        self.log_line('Проверяю публичный релиз OST Finder…')

    def update_result(self, message, staged):
        self.log_line(message)
        self.status.setText(message)
        if staged:
            self.pending_update = staged

    def apply_pending_update(self):
        if not self.pending_update or self.busy or (self.update_thread and self.update_thread.isRunning()):
            return
        try:
            schedule_install(self.pending_update)
            self.pending_update = None
            self.close()
        except OSError as error:
            self.log_line('Не удалось запустить установку: ' + type(error).__name__)

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
        if self.context.text().strip():
            query += ' | ' + self.context.text().strip()
        try:
            plan = parse_query(query)
        except ValueError as error:
            self.status.setText(str(error))
            return
        self.current_query = query
        self.quality = None
        self.quality_started = False
        self.quality_view.clear()
        self.search_provider = ['', 'ollama', 'openrouter'][self.llm_provider.currentIndex()]
        self.search_model = self.llm_model.text().strip()
        self.should_save = self.save_enabled.isChecked()
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
        self.context.setEnabled(False)
        self.llm_provider.setEnabled(False)
        self.llm_model.setEnabled(False)
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
        self.browser.start(plan.target + (' ' + plan.context if plan.context else ''), self.limit.value())

    def add_findings(self, findings):
        for finding in findings:
            url = safe_web_url(finding.url)
            key = (url, finding.title.casefold())
            if not url:
                continue
            if key in self.findings:
                if finding.summary and not self.findings[key].summary:
                    self.findings[key].summary = finding.summary
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
        if not self.quality_started:
            self.quality_started = True
            self.status.setText('Проверяю совпадения, очищаю страницы и составляю сводку…')
            self.quality_thread = QualityThread(self.current_query, list(self.findings.values()),
                getattr(self, 'search_provider', ''), getattr(self, 'search_model', ''), self)
            self.quality_thread.result.connect(self.quality_done)
            self.quality_thread.finished.connect(self.maybe_finish)
            self.quality_thread.start()
            return
        if self.quality is None or (self.quality_thread and self.quality_thread.isRunning()):
            return
        self.busy = False
        self.browser.view.spider.set_active(False)
        self.elapsed.stop()
        self.tick()
        self.query.setEnabled(True)
        self.context.setEnabled(True)
        self.llm_provider.setEnabled(True)
        self.llm_model.setEnabled(True)
        self.limit.setEnabled(True)
        self.search_button.setEnabled(True)
        self.tree.setSortingEnabled(False)
        self.badge.setText('●  ПОИСК ЗАВЕРШЁН')
        for error in self.errors:
            self.log_line('Недоступен источник: ' + error)
        try:
            if getattr(self, 'should_save', self.save_enabled.isChecked()):
                json_path, md_path = save_reports(self.current_query, self.query_type, list(self.findings.values()), self.errors, self.output, self.quality)
                self.log_line('JSON: ' + str(json_path.resolve()))
                self.log_line('Markdown: ' + str(md_path.resolve()))
                storage = 'Отчёты сохранены локально.'
            else:
                storage = 'Результаты только в памяти; отчёты не сохранены.'
            self.status.setText(f'Найдено {len(self.quality["results"])} уникальных страниц. {storage}')
        except OSError as error:
            self.status.setText('Не удалось сохранить отчёт. Выберите другую папку кнопкой «Куда сохранять…».')
            self.log_line('Ошибка сохранения: ' + str(error))
        self.query.setFocus()
        QTimer.singleShot(0, self.apply_pending_update)

    def quality_done(self, quality):
        self.quality = quality
        self.quality_view.setHtml(html_report(quality))
        self.tree.setSortingEnabled(False)
        self.tree.clear()
        for row in quality['results']:
            if row['filtered']:
                continue
            item = QTreeWidgetItem([', '.join(row['sources']), f"[{row['evidence_score']}/100] {row['title']}", row['url']])
            item.setData(0, Qt.ItemDataRole.UserRole, row['url'])
            item.setToolTip(1, row['excerpt'] + '\n' + '\n'.join(row['warnings']))
            item.setForeground(2, QColor('#65d6e8'))
            self.tree.addTopLevelItem(item)
        for row in quality['shortcuts']:
            item = QTreeWidgetItem(['Search shortcut', row['title'], row['url']])
            item.setData(0, Qt.ItemDataRole.UserRole, row['url'])
            self.tree.addTopLevelItem(item)
        self.log_line(f'Качество: {len(quality["results"])} страниц, объединено повторов: {quality["duplicates_merged"]}.')
        if quality.get('llm_error'):
            self.log_line('LLM-анализ недоступен: ' + quality['llm_error'] + '; локальная сводка готова.')

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
            'ФИО | компания город — поиск с контекстом против тёзок.\n'
            'Контекст также можно ввести в отдельное поле под запросом.\n'
            'Вкладка «Качество и сводка» — совпадения, выдержки, даты и расхождения.\n'
            'Балл свидетельств не доказывает истинность или личность.\n'
            'Без LLM ключи не нужны. Ollama/OpenRouter: один запрос, до 12 выдержек.\n'
            'OpenRouter получает запрос и выдержки; ключ — OST_OPENROUTER_KEY.\n'
            'Снимите «Сохранять отчёты», чтобы не записывать запрос и результаты.\n\n'
            'Двойной щелчок по результату — просмотр в приватном браузере.\n'
            'Двойной щелчок по заголовку браузера — отдельное маленькое окно.\n\n'
            'Паук собирает ссылки и сведения с двух найденных страниц.\n'
            'CAPTCHA и недоступные сайты отмечаются в журнале.\n'
            'Инкогнито не скрывает IP. Cookies не сохраняются, отчёты — сохраняются.')

    def closeEvent(self, event):
        if self.busy or (self.update_thread and self.update_thread.isRunning()):
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
    parser.add_argument('--no-auto-update', action='store_true')
    parser.add_argument('--update-healthcheck', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.update_healthcheck:
        from updater import healthcheck_gui
        return healthcheck_gui()
    QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setApplicationName('OST Finder Spider')
    app.setOrganizationName('OST Finder')
    app.setFont(QFont('Segoe UI', 10))
    window = SpiderWindow(args.output, max(1, min(args.limit, 25)))
    window.show()
    if getattr(sys, 'frozen', False) and not args.no_auto_update and auto_updates_enabled():
        QTimer.singleShot(1500, window.check_updates)
    return app.exec()


if __name__ == '__main__':
    raise SystemExit(main())
