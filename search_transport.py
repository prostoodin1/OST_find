"""Per-query deadlines and per-host cooldown, without bypassing rate limits."""
import os
import threading
import time
import math
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit
import requests

_local = threading.local()
_lock = threading.Lock()
_cooldowns = {}


def with_deadline(job, deadline):
    _local.deadline = deadline
    try:
        return job()
    finally:
        _local.deadline = None


class SearchSession(requests.Session):
    def request(self, method, url, **kwargs):
        host = urlsplit(url).hostname
        with _lock:
            cooldown = _cooldowns.get(host, 0) - time.monotonic()
        if cooldown > 0:
            raise requests.RequestException('Source cooldown active; retry later')
        left = (getattr(_local, 'deadline', None) or (time.monotonic() + 12)) - time.monotonic()
        if left <= 0:
            raise requests.Timeout('Search time budget exceeded')
        kwargs['timeout'] = min(float(kwargs.get('timeout', 12)), left)
        proxy = os.environ.get('OST_SEARCH_PROXY')
        if proxy:
            kwargs['proxies'] = {'http': proxy, 'https': proxy}
        response = super().request(method, url, **kwargs)
        if response.status_code in {429, 503}:
            try:
                delay = float(response.headers.get('Retry-After', 60))
            except ValueError:
                try:
                    delay = (parsedate_to_datetime(response.headers.get('Retry-After', '')) - datetime.now(timezone.utc)).total_seconds()
                except (TypeError, ValueError, OverflowError):
                    delay = 60
            if not math.isfinite(delay):
                delay = 60
            with _lock:
                _cooldowns[host] = time.monotonic() + max(1, delay)
        return response
