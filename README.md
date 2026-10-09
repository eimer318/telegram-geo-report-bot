# Telegram Geo Report Bot

A configurable Telegram bot for collecting geographic reports, reviewing them, and exporting approved records as JSON and GeoJSON.

## Features

- guided address, category, description, and photo collection;
- encrypted line-based storage using Fernet;
- administrator review and later status changes;
- automatic validation of nearby reports from different pseudonymous reporters;
- JSON and GeoJSON exports containing approved reports only;
- optional, resumable Telegram channel digests;
- rate limiting and suspicious-input quarantine.

## Installation

Python 3.11 or newer is required.

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# Linux/macOS: source .venv/bin/activate
python -m pip install -e .
```

Create the local configuration. Neither file may be committed:

```bash
cp .env.example .env
cp config/bot.example.json config/bot.json
```

Generate a Fernet key:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Add the first administrator using their numeric Telegram ID and start the bot:

```bash
python -m telegram_geo_report_bot admin add 123456789
python -m telegram_geo_report_bot run
```

Use separate `.env` files and `BOT_DATA_DIR` values when testing with another token. This prevents test activity from touching operational data.

## Configuration

Secrets and runtime paths are configured through `.env`. User-facing text, categories, amount options, the result link, and Nominatim search constraints live in `config/bot.json`.

Category and subcategory IDs must be stable lowercase identifiers. Labels may be changed without rewriting encrypted entries. An unknown legacy ID is displayed as a readable fallback.

The address search uses the public Nominatim service. Configure a descriptive `NOMINATIM_USER_AGENT`, keep automated tests mocked, and do not bulk-query the public endpoint.

### Geographic scope

By default, `country_codes: ["de"]` searches throughout Germany. Empty regional lists mean “no additional restriction”. To restrict results to Berlin, use:

```json
{
  "geocoding": {
    "country_codes": ["de"],
    "allowed_states": ["Berlin"],
    "allowed_counties": [],
    "allowed_municipalities": [],
    "postcode_prefixes": []
  }
}
```

Non-empty filter groups are combined with AND; values inside one list are alternatives. For example, `["Berlin", "Brandenburg"]` permits either state, while `postcode_prefixes: ["10", "12"]` adds a postal-code restriction. A bounded `viewbox` can provide an additional hard geographic boundary. Regional filters use Nominatim address fields, so verify the exact OpenStreetMap state, county, and municipality names for the intended area before deployment.

## Local test workflow

Only one long-polling process may use a Telegram token. Stop any deployed instance before reusing its test token locally.

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -e ".[dev]"
Copy-Item .env.example .env.local
Copy-Item config\bot.example.json config\bot.local.json
```

Set `BOT_TOKEN` and a newly generated `ENCRYPTION_KEY` in `.env.local`, then adjust `config/bot.local.json`. Both files and all `.local/` runtime data are ignored by Git.

Customize the neutral workflow texts in `config/bot.local.json`. `help_text` explains the reporting steps, while `contact_text` should contain the operator's actual support channel.

User throttling is disabled in the example configuration so local evaluation never introduces a waiting period. Operators may set `daily_user_limit` above zero and enable `rate_limits` with suitable minute/hour values for a public deployment.

There is no default administrator and no administrator ID in the JSON configuration. Send `/me` to the bot, stop the local process, and add the returned numeric Telegram ID with the command below. The bot stores only a SHA-256 representation inside the Fernet-encrypted `.local/data/admins.enc`; neither the ID nor the hash belongs in Git. After this bootstrap, administrators can add or remove further administrators through the Telegram admin menu. The last administrator cannot be removed there; the CLI remains available for recovery.

```powershell
.\scripts\run_local.ps1
```

In Telegram, send `/start` and complete one report. Send `/me` to obtain your numeric ID. Stop the process with `Ctrl+C`, add yourself as an administrator, and restart:

```powershell
.\scripts\run_local.ps1 -Command admin -CommandArguments add,123456789
.\scripts\run_local.ps1
```

Review the report through `/admin`, then regenerate and inspect the isolated exports:

```powershell
.\scripts\run_local.ps1 -Command export
Get-Content .local\data\entries.json
Get-Content .local\data\entries.geojson
```

## Commands

```text
python -m telegram_geo_report_bot run
python -m telegram_geo_report_bot export
python -m telegram_geo_report_bot admin add|remove|list [TELEGRAM_ID]
python -m telegram_geo_report_bot channel --check
python -m telegram_geo_report_bot channel --initialize
python -m telegram_geo_report_bot channel --dry-run
python -m telegram_geo_report_bot channel --publish --source scheduled
```

Channel writes require `TELEGRAM_CHANNEL_ALLOW_WRITE=true`. Scheduling is deliberately external; invoke the last command from the scheduler provided by the deployment environment.

## Export formats

`data/entries.enc` is authoritative. `data/entries.json` and `data/entries.geojson` are derived atomically and can be regenerated with the `export` command.

Both exports contain only approved, non-blocked entries. Internal status, reporter, review, security, and encrypted-photo references are omitted. GeoJSON features use point coordinates in `[longitude, latitude]` order.

```json
{
  "type": "FeatureCollection",
  "features": [
    {
      "type": "Feature",
      "id": "b4a6c2f4a0e94784af907f21a7eac262",
      "geometry": {"type": "Point", "coordinates": [13.4, 52.5]},
      "properties": {
        "category": "category_one",
        "subcategory": "type_one",
        "has_photo": false
      }
    }
  ]
}
```

## Development

```bash
python -m pip install -e ".[dev]"
python -m unittest discover -s tests -v
ruff check --no-cache src tests scripts
python -m pip check
python scripts/security_check.py
```

Unit tests use temporary encrypted data and mocked clients. They must never send requests to Telegram or Nominatim.

## License

Licensed under the GNU Affero General Public License v3.0 only. See `LICENSE`.
