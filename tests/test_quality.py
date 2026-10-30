import json
import time
from pathlib import Path
import pytest

from ost_find import Finding, run_search, save_reports
import search_quality as quality
from search_quality import ArticleParser, analyse, parse_query


def test_namesakes_context_and_operators():
    plan = parse_query('Иван Иванов | инженер Москва')
    assert plan.target == 'Иван Иванов'
    assert plan.context == 'инженер Москва'
    assert plan.warnings and 'site:github.com' in plan.operators[1]
    assert 'Иван Иванов' in plan.entities
    with pytest.raises(ValueError):
        parse_query('| Moscow')


def test_cleaning_excludes_ads_navigation_and_scripts():
    parser = ArticleParser()
    parser.feed('<meta name="author" content="Writer"><meta property="article:published_time" content="2026-01-01">'
                '<nav>Menu secret text long enough to count</nav><div class="advertisement">Spam text long enough to count</div>'
                '<article><p>Public fact about Python released in 2026.</p><p>Public fact about Python released in 2026.</p></article>'
                '<script>bad script with a lot of characters</script>')
    text, meta = parser.result()
    assert 'Menu' not in text and 'Spam' not in text and 'script' not in text
    assert text.count('Public fact') == 1
    assert meta['author'] == 'Writer' and meta['published'] == '2026-01-01'


def test_same_url_and_search_engines_are_not_independent_sources():
    findings = [Finding('Bing', 'Python', 'https://example.com/a', 'Python first released in 1991 with publicly documented source code.'),
                Finding('DDG', 'Python source', 'https://example.com/a#part', 'Python first released in 1991 with publicly documented source code.'),
                Finding('Shortcut', 'Search', 'https://search.example/', category='shortcut')]
    report = analyse('Python', findings)
    assert len(report['results']) == 1 and report['duplicates_merged'] == 1
    assert report['claims'][0]['independent_domains'] == 1
    assert len(report['shortcuts']) == 1
    assert report['results'][0]['identity'] == 'не установлена'


def test_numbers_conflicts_and_namesake_are_cautious():
    report = analyse('Ivan Ivanov | engineer', [
        Finding('A', 'Ivan Ivanov', 'https://a.example/x', 'Ivan Ivanov published his detailed research paper in 2020.'),
        Finding('B', 'Ivan Ivanov', 'https://b.example/x', 'Ivan Ivanov published his detailed research paper in 2021.')])
    assert report['possible_conflicts']
    assert all('Контекст' in r['warnings'][0] for r in report['results'])
    assert all(r['evidence_score'] < 100 for r in report['results'])


def test_extraction_failure_does_not_claim_verified(monkeypatch):
    monkeypatch.setattr(quality, 'fetch_article', lambda *a: (_ for _ in ()).throw(ValueError('blocked')))
    report = analyse('Python', [Finding('Test', 'Python docs', 'https://example.com')], extract=True)
    assert report['results'][0]['metadata'] == {}
    assert report['results'][0]['warnings']


def test_private_network_pages_rejected(monkeypatch):
    monkeypatch.setattr(quality.socket, 'getaddrinfo', lambda *a, **kw: [(2, 1, 6, '', ('127.0.0.1', 80))])
    with pytest.raises(ValueError):
        quality.public_url('https://example.com/private')
    with pytest.raises(ValueError):
        quality.public_url('https://example.com:9000/')


def test_llm_one_call_only_existing_ids(monkeypatch):
    calls = []
    class Response:
        def raise_for_status(self): pass
        def json(self): return {'message': {'content': json.dumps({'relevant_ids': ['S1', 'S999'], 'ambiguous_ids': ['S1'], 'made_up_fact': 'ignored'})}}
    monkeypatch.setattr(quality.requests, 'post', lambda *a, **kw: calls.append(kw) or Response())
    report = analyse('Python', [Finding('Test', 'Python', 'https://example.com')])
    result = quality.llm_review(report, 'ollama', 'test-model')
    assert len(calls) == 1
    assert result == {'relevant_ids': ['S1'], 'ambiguous_ids': ['S1']}


def test_search_deadline_retains_partial_results(monkeypatch):
    import ost_find
    def slow(*a, **kw):
        time.sleep(.3)
        return []
    for name in ('search_bing', 'search_duckduckgo', 'search_github', 'search_gitlab', 'search_hacker_news', 'search_wikipedia', 'search_stackoverflow'):
        monkeypatch.setattr(ost_find, name, slow)
    before = time.monotonic()
    kind, findings, errors = run_search('python', 2, time_budget=.05)
    assert time.monotonic() - before < .25
    assert errors and findings  # explicit shortcuts survive but are not evidence


def test_report_contains_cited_quality(tmp_path):
    path, md = save_reports('Python', 'text', [Finding('Test', 'Python', 'https://example.com')], [], tmp_path)
    data = json.loads(path.read_text(encoding='utf-8'))
    assert data['quality']['results'][0]['id'] == 'S1'
    assert '[S1]' in md.read_text(encoding='utf-8')


def test_spam_and_zero_match_are_filtered_but_retained():
    report = analyse('Python', [Finding('A', 'Free money casino', 'https://a.example'),
                              Finding('B', 'Python guide', 'https://b.example')])
    assert len(report['results']) == 2
    assert next(r for r in report['results'] if r['title'].startswith('Free'))['filtered']
    assert not next(r for r in report['results'] if r['title'].startswith('Python'))['filtered']


def test_rich_report_escapes_untrusted_page_markup():
    report = analyse('Python', [Finding('Test', 'Python <img src="file:///private">', 'https://example.com', '<script>bad</script>')])
    rendered = quality.html_report(report)
    assert '<img' not in rendered and '<script' not in rendered
    assert '&lt;img' in rendered


def test_username_prefix_and_sentence_punctuation_do_not_hide_matches():
    report = analyse('@alice_dev', [Finding('GitHub', 'Alice', 'https://github.com/alice_dev')])
    assert report['results'][0]['relevance'] == 100
    assert not report['results'][0]['filtered']
    assert quality.tokens('Python. @alice.dev') == {'python', 'alice.dev'}
