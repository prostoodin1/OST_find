# OST_find

`OST_find` is a terminal application for fast research across public internet sources. It accepts a username, domain, email address, IP address, or ordinary search phrase and collects useful links into JSON and Markdown reports.

## Version 0.2.0

- Windows interface that stays open for repeated searches;
- gray example text and built-in input help;
- clickable, normalized result links without search-engine redirect URLs;
- animated progress and source status;
- automatic query-type detection;
- DuckDuckGo and Bing web results;
- GitHub users and repositories;
- Hacker News discussions;
- Wikipedia references and Stack Overflow discussions;
- DNS, RDAP and Certificate Transparency data for domains;
- public Gravatar profile lookup for email addresses;
- Google, Bing and Reddit shortcuts;
- JSON and Markdown reports;
- no API keys required.

## Windows

Download `OST_find_0.2.0_x64-portable.zip` from the latest Ideas preview release, extract the whole archive and double-click `OST_find.exe`.

The app keeps the window open after each search. Enter another query and press **Найти**; use **Помощь** for examples of supported input.

The source version also has a terminal mode:

```powershell
python ost_find.py --cli "example.com"
```

Reports are written to the `reports` folder next to the current working directory.

The portable folder build deliberately avoids the self-extracting `onefile` format, which can trigger false-positive antivirus detections in unsigned PyInstaller applications.

No application can literally index the entire internet. OST Finder combines general public-web search engines with focused public sources. Closed pages, private accounts and content blocked by source providers are not available.

## From source

```powershell
python -m pip install -r requirements.txt
python ost_find.py
```

## Responsible use

The application queries only public sources and does not bypass authentication, solve CAPTCHAs, access leaked databases, or attempt to identify private accounts. Use it only for lawful research and respect privacy, source terms, and local regulations.

## License

MIT
