from __future__ import annotations

import argparse
import base64
import builtins
import hashlib
import html
import ipaddress
import json
import os
import re
import socket
import sys
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable
from urllib.parse import parse_qs, quote, quote_plus, unquote, urlencode, urlparse, urlunparse

import requests
from search_transport import SearchSession, with_deadline
from search_quality import analyse, parse_query, markdown as quality_markdown


APP_NAME = "OST_find"
APP_VERSION = "0.6.0"
USER_AGENT = f"{APP_NAME}/{APP_VERSION} public-source research tool"
DEFAULT_TIMEOUT = 12


class Console:
    """Tiny ANSI-friendly console wrapper that keeps the packaged app dependency-light."""

    _markup = re.compile(r"\[/?(?:bold|dim|cyan|green|yellow|red|magenta)(?: [^\]]+)?\]")

    @classmethod
    def _plain(cls, value: object) -> str:
        return cls._markup.sub("", str(value))

    def print(self, *values: object) -> None:
        builtins.print(*(self._plain(value) for value in values))

    def input(self, prompt: str) -> str:
        return builtins.input(self._plain(prompt))

    def dim(self, value: str) -> None:
        if getattr(sys.stdout, "isatty", lambda: False)():
            builtins.print(f"\x1b[2;90m{value}\x1b[0m")
        else:
            builtins.print(value)


@dataclass(slots=True)
class Finding:
    source: str
    title: str
    url: str
    summary: str = ""
    category: str = "web"


def detect_query_type(query: str) -> str:
    value = query.strip()
    if re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", value):
        return "email"
    candidate = value.lower().removeprefix("https://").removeprefix("http://").split("/")[0]
    if re.fullmatch(r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}", candidate):
        return "domain"
    try:
        ipaddress.ip_address(value)
        return "ip"
    except ValueError:
        pass
    if re.fullmatch(r"@[A-Za-z0-9_.-]{2,39}", value) or (
        re.fullmatch(r"[A-Za-z0-9_.-]{2,39}", value) and any(character in value for character in "_.-")
    ):
        return "username"
    return "text"


def normalize_domain(query: str) -> str:
    return query.strip().lower().removeprefix("https://").removeprefix("http://").split("/")[0]


def _session() -> requests.Session:
    session = SearchSession()
    session.headers.update({"User-Agent": USER_AGENT, "Accept": "application/json, text/html;q=0.9"})
    return session


def _clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", value))).strip()


def normalize_result_url(raw_url: str) -> str:
    """Resolve known search-engine redirects and remove common tracking parameters."""

    url = html.unescape(raw_url).strip()
    if url.startswith("//"):
        url = "https:" + url
    parsed = urlparse(url)
    params = parse_qs(parsed.query)
    if "duckduckgo.com" in parsed.netloc and parsed.path.startswith("/l/"):
        url = unquote(params.get("uddg", [url])[0])
        parsed = urlparse(url)
    elif parsed.netloc.endswith("bing.com") and parsed.path.startswith("/ck/a"):
        encoded = params.get("u", [""])[0]
        if encoded.startswith("a1"):
            try:
                payload = encoded[2:]
                url = base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)).decode("utf-8")
                parsed = urlparse(url)
            except (ValueError, UnicodeDecodeError):
                pass
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return ""
    tracking = {"utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "gclid", "fbclid"}
    clean_query = [(key, value) for key, values in parse_qs(parsed.query, keep_blank_values=True).items() for value in values if key.casefold() not in tracking]
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path, parsed.params, urlencode(clean_query, doseq=True), ""))


def search_duckduckgo(query: str, limit: int) -> list[Finding]:
    response = _session().get(
        "https://html.duckduckgo.com/html/",
        params={"q": query},
        timeout=DEFAULT_TIMEOUT,
    )
    response.raise_for_status()
    pattern = re.compile(
        r'<a[^>]+class="[^"]*result__a[^"]*"[^>]+href="([^"]+)"[^>]*>(.*?)</a>',
        re.IGNORECASE | re.DOTALL,
    )
    findings: list[Finding] = []
    for raw_url, raw_title in pattern.findall(response.text):
        url = normalize_result_url(raw_url)
        title = _clean_text(raw_title)
        if title and url:
            findings.append(Finding("DuckDuckGo", title, url, category="search"))
        if len(findings) >= limit:
            break
    return findings


def search_bing(query: str, limit: int) -> list[Finding]:
    response = _session().get(
        "https://www.bing.com/search",
        params={"q": query, "count": min(limit, 20), "setlang": "en-US"},
        headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) OST_find/0.2"},
        timeout=DEFAULT_TIMEOUT,
    )
    response.raise_for_status()
    block_pattern = re.compile(r'<li[^>]+class="[^"]*b_algo[^"]*"[^>]*>(.*?)</li>', re.IGNORECASE | re.DOTALL)
    link_pattern = re.compile(r'<h2[^>]*>\s*<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', re.IGNORECASE | re.DOTALL)
    summary_pattern = re.compile(r'<p[^>]*>(.*?)</p>', re.IGNORECASE | re.DOTALL)
    findings: list[Finding] = []
    for block in block_pattern.findall(response.text):
        match = link_pattern.search(block)
        if not match:
            continue
        url = normalize_result_url(match.group(1))
        title = _clean_text(match.group(2))
        summary_match = summary_pattern.search(block)
        summary = _clean_text(summary_match.group(1))[:400] if summary_match else ""
        if title and url:
            findings.append(Finding("Bing", title, url, summary, "search"))
        if len(findings) >= limit:
            break
    return findings


def search_wikipedia(query: str, limit: int) -> list[Finding]:
    language = "ru" if re.search(r"[А-Яа-яЁё]", query) else "en"
    response = _session().get(
        f"https://{language}.wikipedia.org/w/api.php",
        params={"action": "query", "list": "search", "srsearch": query, "srlimit": min(limit, 20), "format": "json", "utf8": 1},
        timeout=DEFAULT_TIMEOUT,
    )
    response.raise_for_status()
    findings: list[Finding] = []
    for item in response.json().get("query", {}).get("search", [])[:limit]:
        title = str(item.get("title") or "Wikipedia")
        findings.append(
            Finding(
                f"Wikipedia ({language})",
                title,
                f"https://{language}.wikipedia.org/wiki/{quote(title.replace(' ', '_'))}",
                _clean_text(str(item.get("snippet") or ""))[:400],
                "reference",
            )
        )
    return findings


def search_stackoverflow(query: str, limit: int) -> list[Finding]:
    response = _session().get(
        "https://api.stackexchange.com/2.3/search/advanced",
        params={"q": query, "site": "stackoverflow", "sort": "relevance", "order": "desc", "pagesize": min(limit, 20)},
        timeout=DEFAULT_TIMEOUT,
    )
    response.raise_for_status()
    findings: list[Finding] = []
    for item in response.json().get("items", [])[:limit]:
        url = normalize_result_url(str(item.get("link") or ""))
        if url:
            findings.append(
                Finding(
                    "Stack Overflow",
                    _clean_text(str(item.get("title") or "Stack Overflow question")),
                    url,
                    f"score: {item.get('score', 0)}; answers: {item.get('answer_count', 0)}",
                    "discussion",
                )
            )
    return findings


def search_gitlab(query: str, limit: int) -> list[Finding]:
    response = _session().get(
        "https://gitlab.com/api/v4/projects",
        params={"search": query.lstrip("@"), "simple": "true", "per_page": min(limit, 20), "order_by": "last_activity_at"},
        timeout=DEFAULT_TIMEOUT,
    )
    response.raise_for_status()
    findings: list[Finding] = []
    for item in response.json()[:limit]:
        url = normalize_result_url(str(item.get("web_url") or ""))
        if url:
            findings.append(
                Finding(
                    "GitLab projects",
                    str(item.get("path_with_namespace") or item.get("name") or "GitLab project"),
                    url,
                    _clean_text(str(item.get("description") or ""))[:240],
                    "code",
                )
            )
    return findings


def search_github(query: str, limit: int, include_users: bool = True) -> list[Finding]:
    session = _session()
    findings: list[Finding] = []
    user_query = query.lstrip("@").strip()
    if include_users:
        user_response = session.get(
            "https://api.github.com/search/users",
            params={"q": user_query, "per_page": min(limit, 10)},
            timeout=DEFAULT_TIMEOUT,
        )
        user_response.raise_for_status()
        for item in user_response.json().get("items", [])[:limit]:
            findings.append(
                Finding(
                    "GitHub users",
                    item.get("login", "GitHub user"),
                    item.get("html_url", ""),
                    f"type: {item.get('type', 'User')}",
                    "profile",
                )
            )
    repo_response = session.get(
        "https://api.github.com/search/repositories",
        params={"q": query, "per_page": min(limit, 10), "sort": "updated"},
        timeout=DEFAULT_TIMEOUT,
    )
    repo_response.raise_for_status()
    for item in repo_response.json().get("items", [])[:limit]:
        summary = item.get("description") or ""
        findings.append(
            Finding(
                "GitHub repositories",
                item.get("full_name", "GitHub repository"),
                item.get("html_url", ""),
                summary[:240],
                "code",
            )
        )
    return findings


def search_hacker_news(query: str, limit: int) -> list[Finding]:
    response = _session().get(
        "https://hn.algolia.com/api/v1/search",
        params={"query": query, "hitsPerPage": min(limit, 20)},
        timeout=DEFAULT_TIMEOUT,
    )
    response.raise_for_status()
    findings: list[Finding] = []
    for item in response.json().get("hits", [])[:limit]:
        object_id = item.get("objectID", "")
        url = item.get("url") or f"https://news.ycombinator.com/item?id={object_id}"
        title = item.get("title") or item.get("story_title") or "Hacker News item"
        findings.append(
            Finding(
                "Hacker News",
                _clean_text(title),
                url,
                f"points: {item.get('points') or 0}; author: {item.get('author') or 'unknown'}",
                "discussion",
            )
        )
    return findings


def search_domain_intel(query: str, limit: int) -> list[Finding]:
    domain = normalize_domain(query)
    findings: list[Finding] = []
    try:
        addresses = sorted({item[4][0] for item in socket.getaddrinfo(domain, None)})
        for address in addresses[:limit]:
            findings.append(
                Finding("DNS", f"{domain} resolves to {address}", f"https://rdap.org/ip/{address}", category="infrastructure")
            )
    except socket.gaierror:
        pass
    try:
        response = _session().get(f"https://rdap.org/domain/{quote_plus(domain)}", timeout=DEFAULT_TIMEOUT)
        if response.ok:
            data = response.json()
            summary_parts = [f"status: {', '.join(data.get('status', [])) or 'unknown'}"]
            if data.get("port43"):
                summary_parts.append(f"WHOIS: {data['port43']}")
            findings.append(
                Finding("RDAP", data.get("ldhName", domain), f"https://rdap.org/domain/{domain}", "; ".join(summary_parts), "domain")
            )
    except requests.RequestException:
        pass
    try:
        cert_response = _session().get(
            "https://crt.sh/",
            params={"q": f"%.{domain}", "output": "json"},
            timeout=DEFAULT_TIMEOUT,
        )
        if cert_response.ok:
            seen: set[str] = set()
            for item in cert_response.json():
                for name in str(item.get("name_value", "")).splitlines():
                    name = name.strip().lower().removeprefix("*.")
                    if name and name not in seen:
                        seen.add(name)
                        findings.append(
                            Finding("Certificate Transparency", name, f"https://crt.sh/?q={quote_plus(name)}", category="subdomain")
                        )
                    if len([f for f in findings if f.source == "Certificate Transparency"]) >= limit:
                        return findings
    except (requests.RequestException, ValueError):
        pass
    dns_types = ("A", "AAAA", "MX", "NS", "TXT")
    for record_type in dns_types:
        record_count = 0
        try:
            dns_response = _session().get(
                "https://dns.google/resolve",
                params={"name": domain, "type": record_type},
                timeout=DEFAULT_TIMEOUT,
            )
            if not dns_response.ok:
                continue
            for answer in dns_response.json().get("Answer", []):
                data = str(answer.get("data") or "").strip()
                if not data:
                    continue
                findings.append(
                    Finding(
                        "DNS",
                        f"{record_type} record: {data}",
                        f"https://dns.google/query?name={quote_plus(domain)}&type={record_type}",
                        f"TTL: {answer.get('TTL', 'unknown')}",
                        "infrastructure",
                    )
                )
                record_count += 1
                if record_count >= min(limit, 2):
                    break
        except (requests.RequestException, ValueError):
            continue
    try:
        security_url = f"https://{domain}/.well-known/security.txt"
        security_response = _session().get(security_url, timeout=DEFAULT_TIMEOUT, allow_redirects=True)
        if security_response.ok and "contact:" in security_response.text.casefold():
            contact = next(
                (line.strip() for line in security_response.text.splitlines() if line.casefold().startswith("contact:")),
                "Published security contact",
            )
            findings.append(Finding("security.txt", f"Security contact for {domain}", security_url, contact[:400], "security"))
    except requests.RequestException:
        pass
    return findings


def search_urlscan(query: str, query_type: str, limit: int) -> list[Finding]:
    if query_type not in {"domain", "ip"}:
        return []
    value = normalize_domain(query) if query_type == "domain" else query.strip()
    response = _session().get(
        "https://urlscan.io/api/v1/search/",
        params={"q": f"{query_type}:{value}", "size": min(limit * 5, 100)},
        timeout=DEFAULT_TIMEOUT,
    )
    response.raise_for_status()
    findings: list[Finding] = []
    for item in response.json().get("results", []):
        task = item.get("task") or {}
        page = item.get("page") or {}
        if query_type == "domain":
            page_domain = str(page.get("domain") or "").casefold().rstrip(".")
            page_url_domain = urlparse(str(page.get("url") or "")).hostname or ""
            if not any(
                candidate == value or candidate.endswith("." + value)
                for candidate in (page_domain, page_url_domain.casefold())
            ):
                continue
        elif str(page.get("ip") or "") != value:
            continue
        scan_id = str(task.get("uuid") or "").strip()
        result_url = f"https://urlscan.io/result/{scan_id}/" if scan_id else normalize_result_url(str(item.get("result") or ""))
        if result_url:
            page_url = str(page.get("url") or task.get("url") or "unknown")
            submitted_url = str(task.get("url") or "")
            summary = f"page: {page_url}; IP: {page.get('ip') or 'unknown'}"
            if submitted_url and submitted_url != page_url:
                summary += f"; submitted: {submitted_url}"
            findings.append(
                Finding(
                    "urlscan.io",
                    str(page.get("title") or page.get("domain") or task.get("url") or "Public URL scan"),
                    result_url,
                    summary,
                    "security",
                )
            )
            if len(findings) >= limit:
                break
    return findings


def search_ip_intel(query: str, limit: int) -> list[Finding]:
    del limit
    address = query.strip()
    findings: list[Finding] = []
    response = _session().get(f"https://rdap.org/ip/{quote_plus(address)}", timeout=DEFAULT_TIMEOUT)
    response.raise_for_status()
    data = response.json()
    summary = "; ".join(
        part
        for part in (
            f"name: {data.get('name')}" if data.get("name") else "",
            f"country: {data.get('country')}" if data.get("country") else "",
            f"type: {data.get('type')}" if data.get("type") else "",
            f"range: {data.get('startAddress')} - {data.get('endAddress')}" if data.get("startAddress") else "",
        )
        if part
    )
    findings.append(Finding("RDAP IP", address, f"https://rdap.org/ip/{address}", summary, "infrastructure"))
    try:
        hostname, aliases, _ = socket.gethostbyaddr(address)
        findings.append(
            Finding(
                "Reverse DNS",
                hostname,
                f"https://dns.google/query?name={quote_plus(address)}&type=PTR",
                f"aliases: {', '.join(aliases) if aliases else 'none'}",
                "infrastructure",
            )
        )
    except (socket.herror, socket.gaierror):
        pass
    return findings


def search_email_profile(query: str, limit: int) -> list[Finding]:
    del limit
    email_value = query.strip().lower()
    digest = hashlib.md5(email_value.encode("utf-8"), usedforsecurity=False).hexdigest()
    profile_url = f"https://www.gravatar.com/{digest}.json"
    response = _session().get(profile_url, timeout=DEFAULT_TIMEOUT)
    if not response.ok:
        return []
    entry = (response.json().get("entry") or [{}])[0]
    return [
        Finding(
            "Gravatar",
            entry.get("displayName") or "Public Gravatar profile",
            entry.get("profileUrl") or f"https://gravatar.com/{digest}",
            entry.get("aboutMe") or "Public profile linked to this email hash",
            "profile",
        )
    ]


def search_links(query: str, query_type: str) -> list[Finding]:
    encoded = quote_plus(query)
    links = [
        Finding("Search shortcut", "Bing", f"https://www.bing.com/search?q={encoded}", category="shortcut"),
        Finding("Search shortcut", "Google", f"https://www.google.com/search?q={encoded}", category="shortcut"),
        Finding("Search shortcut", "Reddit", f"https://www.reddit.com/search/?q={encoded}", category="shortcut"),
    ]
    if query_type == "domain":
        domain = normalize_domain(query)
        links.append(
            Finding(
                "Archive shortcut",
                "Wayback Machine history",
                f"https://web.archive.org/web/*/{quote(domain, safe='.')}",
                category="archive",
            )
        )
    if query_type == "username":
        username = quote_plus(query.lstrip("@"))
        links.extend(
            [
                Finding("Profile shortcut", "GitHub profile", f"https://github.com/{username}", category="shortcut"),
                Finding("Profile shortcut", "Reddit profile", f"https://www.reddit.com/user/{username}", category="shortcut"),
            ]
        )
    return links


EXTRA_SOURCES: dict[str, Callable[[str, int], list[Finding]]] = {}


def register_source(name: str, fetcher: Callable[[str, int], list[Finding]]) -> None:
    """Extensions implement fetcher(query, limit), returning Finding objects."""
    EXTRA_SOURCES[name] = fetcher


def search_brave(query: str, limit: int) -> list[Finding]:
    response = _session().get('https://api.search.brave.com/res/v1/web/search',
        params={'q': query, 'count': min(limit, 20)},
        headers={'X-Subscription-Token': os.environ['OST_BRAVE_KEY']}, timeout=DEFAULT_TIMEOUT)
    response.raise_for_status()
    return [Finding('Brave API', _clean_text(r.get('title', '')), normalize_result_url(r.get('url', '')),
                    _clean_text(r.get('description', ''))[:800], 'search')
            for r in response.json().get('web', {}).get('results', [])[:limit]]


def search_wayback(query: str, limit: int) -> list[Finding]:
    domain = normalize_domain(query)
    response = _session().get('https://archive.org/wayback/available', params={'url': domain}, timeout=DEFAULT_TIMEOUT)
    response.raise_for_status()
    item = response.json().get('archived_snapshots', {}).get('closest', {})
    if item.get('available') and item.get('url'):
        return [Finding('Wayback Machine', 'Archived snapshot: ' + domain, item['url'],
                        'Snapshot timestamp: ' + str(item.get('timestamp', '')), 'archive')]
    return []


def run_search(
    query: str,
    limit: int,
    console: Console | None = None,
    progress_callback: Callable[[str, int, int], None] | None = None,
    findings_callback: Callable[[list[Finding]], None] | None = None,
    time_budget: float = 20,
) -> tuple[str, list[Finding], list[str]]:
    plan = parse_query(query)
    query = plan.target + (' ' + plan.context if plan.context else '')
    web_query = plan.operators[0] if re.fullmatch(r'[A-ZА-ЯЁ][a-zа-яё]+(?:\s+[A-ZА-ЯЁ][a-zа-яё]+){1,2}', plan.target) else query
    deadline = time.monotonic() + time_budget
    query_type = detect_query_type(plan.target)
    jobs: dict[str, Callable[[], list[Finding]]] = {
        "DuckDuckGo": lambda: search_duckduckgo(web_query, limit),
        "Bing": lambda: search_bing(web_query, limit),
    }
    if query_type == "text":
        jobs.update(
            {
                "GitHub": lambda: search_github(query, limit, include_users=False),
                "GitLab": lambda: search_gitlab(query, limit),
                "Hacker News": lambda: search_hacker_news(query, limit),
                "Wikipedia": lambda: search_wikipedia(query, limit),
                "Stack Overflow": lambda: search_stackoverflow(query, limit),
            }
        )
    elif query_type == "username":
        jobs["GitHub"] = lambda: search_github(query, limit, include_users=True)
        jobs["GitLab"] = lambda: search_gitlab(query, limit)
    elif query_type == "domain":
        jobs["GitHub"] = lambda: search_github(query, limit, include_users=False)
        jobs["Hacker News"] = lambda: search_hacker_news(query, limit)
        jobs["Wikipedia"] = lambda: search_wikipedia(query, limit)
    if query_type == "domain":
        jobs["Domain intelligence"] = lambda: search_domain_intel(plan.target, limit)
        jobs["urlscan.io"] = lambda: search_urlscan(plan.target, query_type, limit)
    if query_type == "ip":
        jobs["IP intelligence"] = lambda: search_ip_intel(plan.target, limit)
        jobs["urlscan.io"] = lambda: search_urlscan(plan.target, query_type, limit)
    if query_type == "email":
        jobs["Public email profile"] = lambda: search_email_profile(plan.target, limit)
    if query_type == 'domain':
        jobs['Wayback Machine'] = lambda: search_wayback(plan.target, limit)
    if os.environ.get('OST_BRAVE_KEY'):
        jobs['Brave API'] = lambda: search_brave(web_query, limit)
    for name, fetcher in EXTRA_SOURCES.items():
        jobs[name] = lambda fetcher=fetcher: fetcher(query, limit)

    findings = search_links(query, query_type)
    findings.extend(Finding('Search shortcut', 'Уточнённый запрос: ' + operator,
        'https://www.google.com/search?q=' + quote_plus(operator), category='shortcut') for operator in plan.operators)
    findings.append(Finding('Search shortcut', 'Yandex: точный запрос',
        'https://yandex.com/search/?text=' + quote_plus(plan.operators[0]), category='shortcut'))
    errors: list[str] = []
    pool = ThreadPoolExecutor(max_workers=min(10, len(jobs)))
    try:
        pending = {pool.submit(with_deadline, job, deadline): name for name, job in jobs.items()}
        completed = 0
        spinner = "|/-\\"
        frame = 0
        while pending:
            if time.monotonic() >= deadline:
                for future, name in pending.items():
                    future.cancel()
                    errors.append(name + ': time budget exceeded')
                break
            done, _ = wait(pending, timeout=0.12, return_when=FIRST_COMPLETED)
            if not done:
                if progress_callback:
                    progress_callback("Searching public web", completed, len(jobs))
                elif console:
                    builtins.print(
                        f"\r{spinner[frame % len(spinner)]} Searching public web [{completed}/{len(jobs)}]",
                        end="",
                        flush=True,
                    )
                frame += 1
                continue
            for future in done:
                name = pending.pop(future)
                try:
                    source_findings = future.result()
                    findings.extend(source_findings)
                    if findings_callback:
                        findings_callback(source_findings)
                except Exception as exc:  # network failures should not abort the report
                    # Exception messages can contain query strings or proxy credentials.
                    errors.append(f"{name}: {type(exc).__name__}")
                completed += 1
                if progress_callback:
                    progress_callback(f"Checked {name}", completed, len(jobs))
                elif console:
                    builtins.print(f"\r✓ Checked {name} [{completed}/{len(jobs)}]".ljust(72))
            time.sleep(0.03)
    finally:
        pool.shutdown(wait=False, cancel_futures=True)

    unique: dict[tuple[str, str], Finding] = {}
    for item in findings:
        if item.url:
            unique[(item.url, item.title.casefold())] = item
    return query_type, list(unique.values()), errors


def save_reports(query: str, query_type: str, findings: list[Finding], errors: list[str], output_dir: Path, quality=None) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    json_path = output_dir / f"ost_find_{stamp}.json"
    md_path = output_dir / f"ost_find_{stamp}.md"
    payload = {
        "tool": APP_NAME,
        "version": APP_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "query": query,
        "query_type": query_type,
        "findings": [asdict(item) for item in findings],
        "errors": errors,
        "quality": quality if quality is not None else analyse(query, findings),
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [
        f"# {APP_NAME} report",
        "",
        f"- Query: `{query}`",
        f"- Detected type: `{query_type}`",
        f"- Generated: `{payload['generated_at']}`",
        f"- Findings: `{len(findings)}`",
        "",
        "## Results",
        "",
    ]
    for item in findings:
        lines.extend([f"### {item.title}", "", f"- Source: {item.source}", f"- URL: <{item.url}>"])
        if item.summary:
            lines.append(f"- Summary: {item.summary}")
        lines.append("")
    if errors:
        lines.extend(["## Source errors", ""] + [f"- {error}" for error in errors] + [""])
    lines.append(quality_markdown(payload['quality']))
    md_path.write_text("\n".join(lines), encoding="utf-8")
    return json_path, md_path


def display_findings(findings: list[Finding], console: Console) -> None:
    console.print(f"\n{APP_NAME} results ({len(findings)})")
    console.print("=" * 72)
    for index, item in enumerate(findings, start=1):
        console.print(f"{index:02}. [{item.source}] {item.title}")
        console.print(f"    {item.url}")
        if item.summary:
            console.print(f"    {item.summary}")


def default_output_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent / "reports"
    return Path("reports")


def print_help(console: Console) -> None:
    console.print("\nHow to search:")
    console.print("  @username             public profiles and code accounts")
    console.print("  example.com           web, DNS, RDAP, certificates, urlscan and archives")
    console.print("  name@example.com      public web mentions and Gravatar")
    console.print("  8.8.8.8               RDAP, reverse DNS, urlscan and web mentions")
    console.print("  open source player    an ordinary topic across public web sources")
    console.print('  Alice Smith | company city    context to distinguish namesakes')
    console.print('  --no-save             keep results in memory; no local report files')
    console.print('  --update              verify, install latest Windows build and restart')
    console.print("\nCommands: help, clear, exit")
    console.print("Only public sources are queried. Closed pages, leaked databases and authentication bypass are not used.\n")


def print_banner(console: Console) -> None:
    console.print("+" + "-" * 70 + "+")
    console.print(f"| {APP_NAME} v{APP_VERSION} · public OSINT terminal".ljust(71) + "|")
    console.print("| Repeated search · real destination URLs · reports after every query".ljust(71) + "|")
    console.print("+" + "-" * 70 + "+")


def execute_query(query: str, limit: int, output_dir: Path, console: Console, *, save=True, extract=True, llm='', model='') -> None:
    query_type, findings, errors = run_search(query, limit, console)
    display_findings(findings, console)
    quality = analyse(query, findings, extract=extract)
    if llm:
        from search_quality import llm_review
        try:
            quality['llm'] = llm_review(quality, llm, model)
        except Exception as error:
            errors.append('LLM: ' + type(error).__name__)
    console.print(quality_markdown(quality))
    if save:
        json_path, md_path = save_reports(query, query_type, findings, errors, output_dir, quality)
        console.print(f"\nSaved: {json_path.resolve()}")
        console.print(f"Saved: {md_path.resolve()}")
    if errors:
        console.print(f"{len(errors)} source(s) were unavailable; details are in the report.")


def interactive_loop(limit: int, output_dir: Path, console: Console, options=None) -> int:
    print_banner(console)
    console.dim("Hint: enter @username, example.com, email@example.com, an IP address or any topic. Type help for examples.")
    while True:
        try:
            query = console.input("\nOST_find> ").strip()
        except (EOFError, KeyboardInterrupt):
            console.print("\nClosing OST Finder.")
            return 0
        command = query.casefold()
        if command in {"exit", "quit", "q", "выход"}:
            console.print("Closing OST Finder.")
            return 0
        if command in {"help", "?", "помощь"}:
            print_help(console)
            continue
        if command in {"clear", "cls", "очистить"}:
            os.system("cls" if os.name == "nt" else "clear")
            print_banner(console)
            continue
        if not query:
            console.dim("Enter a search value or type help. The program stays open after every search.")
            continue
        execute_query(query, limit, output_dir, console, **(options or {}))


def launch_gui(limit: int, output_dir: Path) -> int:
    """Legacy GUI code retained only as historical source text; terminal mode is the product interface.

    import tkinter as tk
    from tkinter import messagebox, ttk

    root = tk.Tk()
    root.title(f"{APP_NAME} {APP_VERSION}")
    root.geometry("980x720")
    root.minsize(760, 560)
    root.configure(bg="#101521")

    colors = {"bg": "#101521", "panel": "#171e2d", "text": "#edf2ff", "muted": "#7f8da8", "accent": "#66d9ef", "green": "#7ee787"}
    style = ttk.Style(root)
    style.theme_use("clam")
    style.configure("TFrame", background=colors["bg"])
    style.configure("Panel.TFrame", background=colors["panel"])
    style.configure("TLabel", background=colors["bg"], foreground=colors["text"], font=("Segoe UI", 10))
    style.configure("Title.TLabel", background=colors["bg"], foreground=colors["text"], font=("Segoe UI Semibold", 22))
    style.configure("Muted.TLabel", background=colors["bg"], foreground=colors["muted"], font=("Segoe UI", 9))
    style.configure("Accent.TButton", font=("Segoe UI Semibold", 10), padding=(16, 9))

    main_frame = ttk.Frame(root, padding=22)
    main_frame.pack(fill="both", expand=True)
    ttk.Label(main_frame, text="OST Finder", style="Title.TLabel").pack(anchor="w")
    ttk.Label(main_frame, text="Поиск по открытому вебу и публичным источникам", style="Muted.TLabel").pack(anchor="w", pady=(0, 16))

    search_row = ttk.Frame(main_frame)
    search_row.pack(fill="x")
    placeholder = "Например: username, example.com, email@example.com или тема поиска"
    query_var = tk.StringVar(value=placeholder)
    entry = tk.Entry(
        search_row,
        textvariable=query_var,
        font=("Segoe UI", 11),
        bg=colors["panel"],
        fg=colors["muted"],
        insertbackground=colors["text"],
        relief="flat",
        highlightthickness=1,
        highlightbackground="#2b354b",
        highlightcolor=colors["accent"],
    )
    entry.pack(side="left", fill="x", expand=True, ipady=10)

    def clear_placeholder(_event: object | None = None) -> None:
        if query_var.get() == placeholder:
            query_var.set("")
            entry.configure(fg=colors["text"])

    def restore_placeholder(_event: object | None = None) -> None:
        if not query_var.get().strip():
            query_var.set(placeholder)
            entry.configure(fg=colors["muted"])

    entry.bind("<FocusIn>", clear_placeholder)
    entry.bind("<FocusOut>", restore_placeholder)

    search_button = ttk.Button(search_row, text="Найти", style="Accent.TButton")
    search_button.pack(side="left", padx=(10, 0))

    def show_help() -> None:
        messagebox.showinfo(
            "Как пользоваться OST Finder",
            "Что можно вводить:\n\n"
            "• ник: @username или username\n"
            "• сайт: example.com или https://example.com\n"
            "• email: name@example.com\n"
            "• IP-адрес: 8.8.8.8\n"
            "• обычный вопрос или тему: open source music player\n\n"
            "Нажмите на синюю ссылку, чтобы открыть результат в браузере. "
            "После поиска окно остаётся открытым — просто введите следующий запрос.\n\n"
            "Программа ищет по общедоступному вебу и публичным API. Она не видит закрытые страницы и не может буквально проиндексировать весь интернет.",
        )

    ttk.Button(search_row, text="Помощь", command=show_help).pack(side="left", padx=(8, 0))

    progress = ttk.Progressbar(main_frame, mode="indeterminate")
    progress.pack(fill="x", pady=(14, 5))
    status_var = tk.StringVar(value="Введите запрос и нажмите «Найти»")
    ttk.Label(main_frame, textvariable=status_var, style="Muted.TLabel").pack(anchor="w", pady=(0, 10))

    result_frame = ttk.Frame(main_frame, style="Panel.TFrame")
    result_frame.pack(fill="both", expand=True)
    results = tk.Text(
        result_frame,
        wrap="word",
        bg=colors["panel"],
        fg=colors["text"],
        insertbackground=colors["text"],
        relief="flat",
        padx=16,
        pady=14,
        font=("Segoe UI", 10),
        state="disabled",
    )
    scrollbar = ttk.Scrollbar(result_frame, orient="vertical", command=results.yview)
    results.configure(yscrollcommand=scrollbar.set)
    scrollbar.pack(side="right", fill="y")
    results.pack(fill="both", expand=True)
    results.tag_configure("title", font=("Segoe UI Semibold", 11), foreground=colors["text"])
    results.tag_configure("source", foreground=colors["green"])
    results.tag_configure("summary", foreground="#aeb9cc")
    results.tag_configure("empty", foreground=colors["muted"], justify="center")

    def render(findings: list[Finding], query_type: str, errors: list[str], report_paths: tuple[Path, Path]) -> None:
        results.configure(state="normal")
        results.delete("1.0", "end")
        if not findings:
            results.insert("end", "Ничего не найдено. Попробуйте более точный или более общий запрос.", "empty")
        for index, item in enumerate(findings, 1):
            results.insert("end", f"{index:02}. {item.title}\n", "title")
            results.insert("end", f"{item.source}\n", "source")
            tag = f"url_{index}"
            results.insert("end", item.url + "\n", tag)
            results.tag_configure(tag, foreground=colors["accent"], underline=True)
            results.tag_bind(tag, "<Enter>", lambda _event: results.configure(cursor="hand2"))
            results.tag_bind(tag, "<Leave>", lambda _event: results.configure(cursor=""))
            results.tag_bind(tag, "<Button-1>", lambda _event, url=item.url: webbrowser.open(url))
            if item.summary:
                results.insert("end", item.summary + "\n", "summary")
            results.insert("end", "\n")
        results.configure(state="disabled")
        progress.stop()
        search_button.configure(state="normal")
        status = f"Готово: {len(findings)} результатов · тип: {query_type} · отчёт: {report_paths[1].name}"
        if errors:
            status += f" · недоступно источников: {len(errors)}"
        status_var.set(status)
        entry.focus_set()
        entry.selection_range(0, "end")

    def fail(message: str) -> None:
        progress.stop()
        search_button.configure(state="normal")
        status_var.set("Ошибка поиска")
        messagebox.showerror("OST Finder", message)

    def begin_search(_event: object | None = None) -> None:
        query = query_var.get().strip()
        if not query or query == placeholder:
            messagebox.showwarning("OST Finder", "Введите ник, домен, email, IP-адрес или обычную тему поиска.")
            entry.focus_set()
            return
        search_button.configure(state="disabled")
        progress.start(12)
        status_var.set("Ищу по открытому интернету…")

        def on_progress(name: str, completed: int, total: int) -> None:
            root.after(0, status_var.set, f"{name}: {completed}/{total}")

        def worker() -> None:
            try:
                query_type, findings, errors = run_search(query, limit, progress_callback=on_progress)
                report_paths = save_reports(query, query_type, findings, errors, output_dir)
                root.after(0, render, findings, query_type, errors, report_paths)
            except Exception as exc:
                root.after(0, fail, f"{type(exc).__name__}: {exc}")

        threading.Thread(target=worker, daemon=True).start()

    search_button.configure(command=begin_search)
    entry.bind("<Return>", begin_search)
    root.after(150, entry.focus_set)
    root.mainloop()
    return 0
    """


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Public-source research from the terminal.")
    parser.add_argument("query", nargs="?", help="Username, domain, email or search phrase")
    parser.add_argument("--limit", type=int, default=8, help="Maximum results per source (default: 8)")
    parser.add_argument("--output", type=Path, default=None, help="Report directory")
    parser.add_argument("--cli", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--no-pause", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--version", action="version", version=f"{APP_NAME} {APP_VERSION}")
    parser.add_argument('--no-save', action='store_true', help='Keep query and results in memory only')
    parser.add_argument('--no-extract', action='store_true', help='Skip fetching up to three public pages')
    parser.add_argument('--llm', choices=['ollama', 'openrouter'], default='', help='Optional one-request evidence selection; sends excerpts to chosen provider')
    parser.add_argument('--model', default='', help='Explicit model for optional LLM')
    parser.add_argument('--update', action='store_true', help='Download, verify and install latest release, then restart')
    parser.add_argument('--check-update', action='store_true', help='Check latest release without installing')
    parser.add_argument('--no-auto-update', action='store_true', help='Disable startup updates for this run')
    parser.add_argument('--update-healthcheck', action='store_true', help=argparse.SUPPRESS)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.update_healthcheck:
        return 0
    from updater import check_release, stage_update, schedule_install, auto_updates_enabled
    automatic = getattr(sys, 'frozen', False) and not args.no_auto_update and auto_updates_enabled()
    if args.update or args.check_update or (automatic and not args.query):
        try:
            release = check_release(APP_VERSION, 'cli')
            if release:
                print('Available update: ' + release['version'])
                if not args.check_update:
                    staged = stage_update(release)
                    schedule_install(staged)
                    return 0
            elif args.check_update or args.update:
                print('Already up to date.')
            if args.check_update or args.update:
                return 0
        except Exception as error:
            print('Update unavailable: ' + type(error).__name__)
            if args.update or args.check_update:
                return 1
    output_dir = args.output or default_output_dir()
    limit = max(1, min(args.limit, 25))
    console = Console()
    options = dict(save=not args.no_save, extract=not args.no_extract, llm=args.llm, model=args.model)
    if not args.query:
        return interactive_loop(limit, output_dir, console, options)
    print_banner(console)
    execute_query(args.query, limit, output_dir, console, **options)
    if automatic:
        # Never discard a one-shot query just because a newer version exists.
        try:
            release = check_release(APP_VERSION, 'cli')
            if release:
                console.print('Installing update after completed search: ' + release['version'])
                schedule_install(stage_update(release))
        except Exception as error:
            console.print('Update unavailable: ' + type(error).__name__)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
