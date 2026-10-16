import json
from pathlib import Path

import base64

import ost_find
from ost_find import Console, Finding, detect_query_type, interactive_loop, normalize_domain, normalize_result_url, save_reports


def test_detect_query_types():
    assert detect_query_type("alice@example.com") == "email"
    assert detect_query_type("https://example.com/path") == "domain"
    assert detect_query_type("203.0.113.1") == "ip"
    assert detect_query_type("@alice_dev") == "username"
    assert detect_query_type("bitcoin") == "text"
    assert detect_query_type("Alice Example") == "text"


def test_normalize_domain():
    assert normalize_domain("HTTPS://Example.COM/path") == "example.com"


def test_normalize_duckduckgo_result_url():
    redirect = "https://duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fpage%3Futm_source%3Dtest%26id%3D7"
    assert normalize_result_url(redirect) == "https://example.com/page?id=7"


def test_normalize_bing_result_url():
    destination = "https://example.com/useful-page"
    encoded = base64.urlsafe_b64encode(destination.encode()).decode().rstrip("=")
    redirect = f"https://www.bing.com/ck/a?u=a1{encoded}&ntb=1"
    assert normalize_result_url(redirect) == destination


def test_reject_non_web_result_url():
    assert normalize_result_url("javascript:alert(1)") == ""


def test_urlscan_uses_human_result_link_and_final_page(monkeypatch):
    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "results": [
                    {
                        "task": {"uuid": "scan-123", "url": "https://redirect.invalid/"},
                        "page": {
                            "domain": "example.com",
                            "url": "https://example.com/",
                            "title": "Example Domain",
                            "ip": "192.0.2.1",
                        },
                        "result": "https://urlscan.io/api/v1/result/scan-123/",
                    }
                ]
            }

    class Session:
        def get(self, *args, **kwargs):
            del args, kwargs
            return Response()

    monkeypatch.setattr(ost_find, "_session", lambda: Session())
    finding = ost_find.search_urlscan("example.com", "domain", 1)[0]
    assert finding.url == "https://urlscan.io/result/scan-123/"
    assert "page: https://example.com/" in finding.summary
    assert "submitted: https://redirect.invalid/" in finding.summary


def test_save_reports(tmp_path: Path):
    findings = [Finding("Test", "Example", "https://example.com", "summary")]
    json_path, md_path = save_reports("example", "username", findings, [], tmp_path)
    data = json.loads(json_path.read_text(encoding="utf-8"))
    assert data["query"] == "example"
    assert data["findings"][0]["title"] == "Example"
    assert "https://example.com" in md_path.read_text(encoding="utf-8")


def test_interactive_loop_accepts_repeated_searches(monkeypatch, tmp_path: Path):
    queries = iter(["first query", "second query", "exit"])
    seen: list[str] = []

    class TestConsole(Console):
        def input(self, prompt: str) -> str:
            del prompt
            return next(queries)

        def print(self, *values: object) -> None:
            del values

        def dim(self, value: str) -> None:
            del value

    monkeypatch.setattr(ost_find, "execute_query", lambda query, limit, output, console: seen.append(query))

    assert interactive_loop(3, tmp_path, TestConsole()) == 0
    assert seen == ["first query", "second query"]
