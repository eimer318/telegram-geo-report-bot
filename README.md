# Telegram Geo Report Bot

A configurable Telegram bot for collecting location-based reports, reviewing
submissions, and exporting approved records as JSON and GeoJSON.

> New here? Follow the complete [Step-by-Step Setup and Testing Guide](docs/STEP_BY_STEP_GUIDE.md).

## Features

- guided address, category, description, and photo collection;
- Fernet-encrypted entries, administrator data, and photos;
- administrator review and later status changes;
- automatic validation of nearby reports from different pseudonymous reporters;
- privacy-conscious JSON and GeoJSON exports of approved reports;
- optional, resumable Telegram channel digests;
- configurable categories, text, limits, and geographic search scope.

## Quick start

Python 3.11 or newer, Git, a Telegram test-bot token, and a descriptive
Nominatim user agent are required.

```powershell
git clone git@github.com:eimer318/telegram-geo-report-bot.git
cd telegram-geo-report-bot
py -3 -m venv .venv
.\.venv\Scripts\python -m pip install -e ".[dev]"
```

Do not start the bot before creating its local environment, configuration,
encryption key, and first administrator. The
[Step-by-Step Guide](docs/STEP_BY_STEP_GUIDE.md) covers the complete Windows and
VS Code workflow. Only one long-polling process may use a Telegram token at a
time.

## Exports

The encrypted `entries.enc` file is authoritative. The `export` command creates
derived `entries.json` and `entries.geojson` files atomically. Both contain only
approved, non-blocked reports and exclude Telegram identifiers, reporter hashes,
review metadata, security metadata, and internal photo references. GeoJSON point
coordinates use `[longitude, latitude]` order.

## Development checks

```powershell
.\.venv\Scripts\python -m unittest discover -s tests -v
.\.venv\Scripts\python -m ruff check --no-cache src tests scripts
.\.venv\Scripts\python -m pip check
.\.venv\Scripts\python scripts\security_check.py
```

Tests use temporary encrypted data and mocked clients. They must not send
requests to Telegram or Nominatim.

## Documentation

- [Step-by-Step Setup and Testing Guide](docs/STEP_BY_STEP_GUIDE.md)
- [Security Policy](SECURITY.md)

## License

Licensed under the GNU Affero General Public License v3.0 only. See [LICENSE](LICENSE).
