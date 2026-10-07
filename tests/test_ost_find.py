import json
from pathlib import Path

from ost_find import Finding, detect_query_type, normalize_domain, save_reports


def test_detect_query_types():
    assert detect_query_type("alice@example.com") == "email"
    assert detect_query_type("https://example.com/path") == "domain"
    assert detect_query_type("203.0.113.1") == "ip"
    assert detect_query_type("@alice_dev") == "username"
    assert detect_query_type("Alice Example") == "text"


def test_normalize_domain():
    assert normalize_domain("HTTPS://Example.COM/path") == "example.com"


def test_save_reports(tmp_path: Path):
    findings = [Finding("Test", "Example", "https://example.com", "summary")]
    json_path, md_path = save_reports("example", "username", findings, [], tmp_path)
    data = json.loads(json_path.read_text(encoding="utf-8"))
    assert data["query"] == "example"
    assert data["findings"][0]["title"] == "Example"
    assert "https://example.com" in md_path.read_text(encoding="utf-8")
