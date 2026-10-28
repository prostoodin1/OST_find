from __future__ import annotations

import argparse
import builtins
import hashlib
import html
import ipaddress
import json
import re
import socket
import sys
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable
from urllib.parse import parse_qs, quote_plus, unquote, urlparse

import requests


APP_NAME = "OST_find"
APP_VERSION = "0.1.1"
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
    if re.fullmatch(r"@?[A-Za-z0-9_.-]{2,39}", value):
        return "username"
    return "text"


def normalize_domain(query: str) -> str:
    return query.strip().lower().removeprefix("https://").removeprefix("http://").split("/")[0]


def _session() -> requests.Session:
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT, "Accept": "application/json, text/html;q=0.9"})
    return session


def _clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", value))).strip()


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
        url = html.unescape(raw_url)
        if url.startswith("//"):
            url = "https:" + url
        parsed = urlparse(url)
        if "duckduckgo.com" in parsed.netloc and parsed.path.startswith("/l/"):
            encoded = parse_qs(parsed.query).get("uddg", [""])[0]
            if encoded:
                url = unquote(encoded)
        title = _clean_text(raw_title)
        if title and url.startswith(("http://", "https://")):
            findings.append(Finding("DuckDuckGo", title, url, category="search"))
        if len(findings) >= limit:
            break
    return findings


def search_github(query: str, limit: int) -> list[Finding]:
    session = _session()
    findings: list[Finding] = []
    user_query = query.lstrip("@").strip()
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
    if query_type == "username":
        username = quote_plus(query.lstrip("@"))
        links.extend(
            [
                Finding("Profile shortcut", "GitHub profile", f"https://github.com/{username}", category="shortcut"),
                Finding("Profile shortcut", "Reddit profile", f"https://www.reddit.com/user/{username}", category="shortcut"),
            ]
        )
    return links


def run_search(query: str, limit: int, console: Console) -> tuple[str, list[Finding], list[str]]:
    query_type = detect_query_type(query)
    jobs: dict[str, Callable[[], list[Finding]]] = {
        "DuckDuckGo": lambda: search_duckduckgo(query, limit),
        "GitHub": lambda: search_github(query, limit),
        "Hacker News": lambda: search_hacker_news(query, limit),
    }
    if query_type == "domain":
        jobs["Domain intelligence"] = lambda: search_domain_intel(query, limit)
    if query_type == "email":
        jobs["Public email profile"] = lambda: search_email_profile(query, limit)

    findings = search_links(query, query_type)
    errors: list[str] = []
    with ThreadPoolExecutor(max_workers=min(5, len(jobs))) as pool:
        pending = {pool.submit(job): name for name, job in jobs.items()}
        completed = 0
        spinner = "|/-\\"
        frame = 0
        while pending:
            done, _ = wait(pending, timeout=0.12, return_when=FIRST_COMPLETED)
            if not done:
                builtins.print(
                    f"\r{spinner[frame % len(spinner)]} Searching public sources [{completed}/{len(jobs)}]",
                    end="",
                    flush=True,
                )
                frame += 1
                continue
            for future in done:
                name = pending.pop(future)
                try:
                    findings.extend(future.result())
                except Exception as exc:  # network failures should not abort the report
                    errors.append(f"{name}: {type(exc).__name__}: {exc}")
                completed += 1
                builtins.print(f"\r✓ Checked {name} [{completed}/{len(jobs)}]".ljust(72))
            time.sleep(0.03)

    unique: dict[tuple[str, str], Finding] = {}
    for item in findings:
        if item.url:
            unique[(item.url.casefold(), item.title.casefold())] = item
    return query_type, list(unique.values()), errors


def save_reports(query: str, query_type: str, findings: list[Finding], errors: list[str], output_dir: Path) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
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
        lines.extend([f"### {item.title}", "", f"- Source: {item.source}", f"- URL: {item.url}"])
        if item.summary:
            lines.append(f"- Summary: {item.summary}")
        lines.append("")
    if errors:
        lines.extend(["## Source errors", ""] + [f"- {error}" for error in errors] + [""])
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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Public-source research from the terminal.")
    parser.add_argument("query", nargs="?", help="Username, domain, email or search phrase")
    parser.add_argument("--limit", type=int, default=8, help="Maximum results per source (default: 8)")
    parser.add_argument("--output", type=Path, default=Path("reports"), help="Report directory")
    parser.add_argument("--no-pause", action="store_true", help="Do not wait before closing an interactive Windows session")
    parser.add_argument("--version", action="version", version=f"{APP_NAME} {APP_VERSION}")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    console = Console()
    console.print("+" + "-" * 62 + "+")
    console.print(f"| {APP_NAME} v{APP_VERSION}".ljust(63) + "|")
    console.print("| Searches public sources. Use legally and respect privacy.".ljust(63) + "|")
    console.print("+" + "-" * 62 + "+")
    query = args.query or console.input("[bold]What should I search for?[/bold] ").strip()
    if not query:
        console.print("[red]A search query is required.[/red]")
        return 2
    limit = max(1, min(args.limit, 25))
    query_type, findings, errors = run_search(query, limit, console)
    display_findings(findings, console)
    json_path, md_path = save_reports(query, query_type, findings, errors, args.output)
    console.print(f"\n[green]Saved:[/green] {json_path.resolve()}")
    console.print(f"[green]Saved:[/green] {md_path.resolve()}")
    if errors:
        console.print(f"[yellow]{len(errors)} source(s) were unavailable; details are in the report.[/yellow]")
    if argv is None and not args.no_pause and getattr(sys, "frozen", False):
        console.input("\nPress Enter to close...")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
