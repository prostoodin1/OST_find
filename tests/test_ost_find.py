import json
from pathlib import Path

import base64

from ost_find import Finding, detect_query_type, normalize_domain, normalize_result_url, save_reports


def test_detect_query_types():
    assert detect_query_type("alice@example.com") == "email"
    assert detect_query_type("https://example.com/path") == "domain"
    assert detect_query_type("203.0.113.1") == "ip"
    assert detect_query_type("@alice_dev") == "username"
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


def test_save_reports(tmp_path: Path):
    findings = [Finding("Test", "Example", "https://example.com", "summary")]
    json_path, md_path = save_reports("example", "username", findings, [], tmp_path)
    data = json.loads(json_path.read_text(encoding="utf-8"))
    assert data["query"] == "example"
    assert data["findings"][0]["title"] == "Example"
    assert "https://example.com" in md_path.read_text(encoding="utf-8")
