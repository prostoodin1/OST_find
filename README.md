# OST_find

`OST_find` is a terminal application for fast research across public internet sources. It accepts a username, domain, email address, IP address, or ordinary search phrase and collects useful links into JSON and Markdown reports.

## Version 0.1.1

- animated terminal progress;
- automatic query-type detection;
- DuckDuckGo web results;
- GitHub users and repositories;
- Hacker News discussions;
- DNS, RDAP and Certificate Transparency data for domains;
- public Gravatar profile lookup for email addresses;
- Google, Bing and Reddit shortcuts;
- JSON and Markdown reports;
- no API keys required.

## Windows

Download `OST_find_0.1.1_x64-portable.zip` from the latest Ideas preview release, extract the whole archive and double-click `OST_find.exe`, or run:

```powershell
.\OST_find.exe "example.com"
```

Reports are written to the `reports` folder next to the current working directory.

The portable folder build deliberately avoids the self-extracting `onefile` format, which can trigger false-positive antivirus detections in unsigned PyInstaller applications.

## From source

```powershell
python -m pip install -r requirements.txt
python ost_find.py "open source intelligence"
```

## Responsible use

The application queries only public sources and does not bypass authentication, solve CAPTCHAs, access leaked databases, or attempt to identify private accounts. Use it only for lawful research and respect privacy, source terms, and local regulations.

## License

MIT
