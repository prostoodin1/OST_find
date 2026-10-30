# OST Finder 0.6.0 Search Quality + Updates

- Query entities, exact-name search, optional context, Google/Yandex advanced-query shortcuts and namesake warnings.
- Evidence-ranked results, canonical URL deduplication, copied-text warnings, low-match/spam filtering with retained evidence.
- Bounded public HTML extraction, robots.txt, publisher metadata, cited summaries, chronology and possible numeric conflicts.
- Optional Brave API and real Wayback snapshot lookup, source extension registry, 20s collection budgets and rate-limit cooldowns.
- Local analysis by default: zero LLM requests. Optional Ollama/OpenRouter evidence selection uses at most one call, bounded input/output, existing citation IDs only.
- Rich linked quality tab and no-save mode in GUI/CLI. Reports remain local and opt-out.
- Full Windows EXE/portable self-update: release check, SHA-256, staged download, deferred install, backup, health check, rollback and restart. Controls in GUI and CLI.
- 0.5.0 requires a one-time manual upgrade. Source checkouts update via git. SHA-256 is not a developer signature; evidence scores are not truth probabilities.

# OST Finder 0.5.0 Wireframe

- Procedural wireframe spider with eight articulated legs, based on the supplied video reference.
- Live DOM rectangles guide the feet and the crawler route.
- Each collected link/text fragment produces an actual collection event after contact.
- Colored target boxes, lifted text packets and a CAPTURED counter replace the decorative icon.
- Bounded page tours, scrolling, private profile, reports and a separate terminal build.

# OST Finder 0.4.0 Spider

- Kali-inspired dark native desktop interface with live results and source log.
- Separate small off-the-record Chromium browser window opens during search.
- Animated spider crawls over the page; real DOM links and page metadata are collected.
- Browser visits up to two discovered public pages, with timeouts and errors in reports.
- Repeated searches, JSON/Markdown export and independent terminal EXE remain available.
- Versions 0.1.0, 0.1.1, 0.2.0 and 0.3.0 migrated to the personal private OST_find repository with verified SHA-256.

# OST_find 0.3.0

Persistent terminal and stronger public OSINT coverage.

- Search repeatedly from the same `OST_find>` terminal session.
- Use dim input examples plus `help`, `clear` and `exit` commands.
- See normalized destination URLs instead of Bing or DuckDuckGo redirect links.
- Search DuckDuckGo, Bing, GitHub, GitLab, Hacker News, Wikipedia and Stack Overflow.
- Inspect public urlscan.io data, IP RDAP, reverse DNS, expanded DNS records, `security.txt`, certificates and Wayback history.
- Keep JSON and Markdown reports for every query.
- Choose either a direct standalone EXE or the lower-risk portable ZIP.

The application searches public sources. It cannot access private pages or literally index every page on the internet.

No API keys are required. The tool does not bypass authentication or access private databases.
