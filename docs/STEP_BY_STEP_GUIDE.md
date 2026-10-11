# Step-by-Step Setup and Testing Guide

This guide walks through a clean Windows and VS Code setup using a Telegram test
bot. It starts with a fresh SSH clone and keeps all runtime data isolated from
the repository.

## Requirements

- Windows PowerShell and Git;
- Python 3.11 or newer;
- Visual Studio Code (recommended);
- SSH access to the GitHub repository;
- a Telegram test-bot token;
- a contact address or project URL for the Nominatim user agent.

Never use a production token or production data for this walkthrough. Only one
long-polling process may use a Telegram token at a time. Stop every other local
or deployed instance before starting the test bot.

## 1. Clone the repository

```powershell
cd C:\Code
git clone git@github.com:eimer318/telegram-geo-report-bot.git
cd telegram-geo-report-bot
code .
```

All following commands run in a new VS Code PowerShell terminal opened in the
repository root.

## 2. Create the Python environment

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python -m pip install --upgrade pip
.\.venv\Scripts\python -m pip install -e ".[dev]"
```

Dependencies are declared in `pyproject.toml`; no separate requirements file is
needed. The editable installation makes local source changes available without
reinstalling the package.

If Windows blocks the local helper script, allow scripts only for the current
PowerShell process:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
```

This setting ends when the terminal is closed.

## 3. Create local configuration

```powershell
Copy-Item .env.example .env.local
Copy-Item config\bot.example.json config\bot.local.json
.\.venv\Scripts\python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Open `.env.local` and set the test token, the newly generated Fernet key, and a
descriptive Nominatim user agent:

```dotenv
BOT_TOKEN=YOUR_TEST_BOT_TOKEN
ENCRYPTION_KEY=YOUR_NEW_FERNET_KEY
BOT_CONFIG_FILE=config/bot.local.json
BOT_DATA_DIR=.local/data
BOT_PHOTO_DIR=.local/photos
NOMINATIM_USER_AGENT=TelegramGeoReportBot-Test/0.1 (your-contact@example.org)
NOMINATIM_SEARCH_URL=https://nominatim.openstreetmap.org/search

TELEGRAM_CHANNEL_ID=
TELEGRAM_CHANNEL_ALLOW_WRITE=false
CHANNEL_DAILY_LIMIT=true
```

The user agent is not a password or API key. It identifies the application to
the public Nominatim service. Replace the example contact with an address or URL
monitored by the operator. `NOMINATIM_SEARCH_URL` may point to another compatible
Nominatim provider or a self-hosted instance without changing the application.
Before deployment, review the selected provider's usage policy. The public OSMF
service has strict capacity, identification, caching, and attribution rules:
https://operations.osmfoundation.org/policies/nominatim/

The files `.env.local`, `config/bot.local.json`, and the `.local/` directory are
ignored by Git. Confirm that they remain untracked before every commit.

## 4. Configure text, categories, and geographic scope

Edit `config/bot.local.json`, not generated export files. The most relevant
settings are:

- `welcome_text`, `help_text`, and `contact_text`;
- `public_result_url` and optional channel text;
- `amount_options`;
- `categories` and their `subcategories`;
- `geocoding` search restrictions.

Keep category and subcategory IDs stable, lowercase, and preferably ASCII with
underscores. Visible labels may contain spaces, umlauts, and `ß`.

For example, replace the complete `categories` array with categories suitable
for the intended reporting project:

```json
"categories": [
  {
    "id": "street_furniture",
    "label": "Straßenmobiliar",
    "subcategories": [
      {"id": "bench", "label": "Sitzbank"},
      {"id": "bicycle_rack", "label": "Fahrradständer"},
      {"id": "street_light", "label": "Straßenbeleuchtung"}
    ]
  },
  {
    "id": "accessibility",
    "label": "Barrierefreiheit",
    "subcategories": [
      {"id": "blocked_path", "label": "Blockierter Weg"},
      {"id": "missing_ramp", "label": "Fehlende Rampe"},
      {"id": "damaged_tactile_paving", "label": "Defektes Blindenleitsystem"}
    ]
  },
  {
    "id": "cleanliness",
    "label": "Sauberkeit",
    "subcategories": [
      {"id": "litter", "label": "Müllablagerung"},
      {"id": "full_bin", "label": "Überfüllter Mülleimer"}
    ]
  },
  {
    "id": "other",
    "label": "Sonstiges",
    "subcategories": [
      {"id": "other", "label": "Sonstiges"}
    ]
  }
]
```

The IDs are stored in encrypted reports and exports, so do not rename them once
real data exists. Labels are display text and may be adjusted later. Every
category needs at least one subcategory; use an `other` fallback when no finer
classification is required. The values in `amount_options`, for example
`["1", "5+", "10+"]`, apply to every configured category.

The example configuration searches throughout Germany:

```json
{
  "country_codes": ["de"],
  "viewbox": null,
  "bounded": false,
  "result_limit": 8,
  "allowed_states": [],
  "allowed_counties": [],
  "allowed_municipalities": [],
  "postcode_prefixes": []
}
```

To restrict results to one federal state, set its OpenStreetMap/Nominatim name:

```json
"allowed_states": ["Bayern"]
```

Non-empty filter groups are combined. Verify the exact state, county, and
municipality names returned by Nominatim before deployment. A bounded `viewbox`
can provide an additional hard geographic boundary.

## 5. Start the bot and create the first administrator

Start the isolated local bot:

```powershell
.\scripts\run_local.ps1
```

In Telegram, send `/start` and then `/me`. Copy the displayed numeric Telegram
ID. Stop the bot with `Ctrl+C`, add that ID as the first administrator, and start
the bot again:

```powershell
.\scripts\run_local.ps1 -Command admin -CommandArguments @("add", "YOUR_TELEGRAM_ID")
.\scripts\run_local.ps1
```

Administrator identifiers are hashed and stored in the encrypted
`.local/data/admins.enc` file. They do not belong in configuration or Git.

If selections unexpectedly expire or the bot jumps to the main menu, first
check that no second process, terminal, computer, or server is polling with the
same token. Restart the single remaining instance and begin again with `/start`.

## 6. Complete the manual acceptance test

Run these scenarios in order:

1. **Navigation:** Open `/start`, Help, Data Processing, and every back route.
2. **Report without a photo:** Search and select an address, choose category,
   subcategory and amount, enter a description, and skip the photo.
3. **German characters:** Use `ä`, `ö`, `ü`, `Ä`, `Ö`, `Ü`, and `ß` in a test
   description and confirm that the review displays them unchanged.
4. **Edit before submission:** Change address, category, amount, and description;
   every edit must return to the same review.
5. **Report with a photo:** Add a photo, replace it, and remove it in another test
   report. Processed description and photo messages should be removed from the
   chat while their values remain in the review.
6. **Cancellation:** Cancel on multiple steps and confirm that the main menu is
   usable afterward.
7. **Admin review:** Create three pending reports. Verify stable `1/3`, `2/3`,
   `3/3` progress and test Approve, Reject, and Skip.
8. **All reports:** Test previous/next navigation and later status changes among
   `pending`, `approved`, and `rejected`.
9. **Administrator management:** Add and remove a second test administrator. The
   final administrator must not be removable through Telegram.
10. **Restart:** Stop and restart the bot. Reports, statuses, and administrator
    access must remain available.
11. **Free text:** Send text outside an active workflow. The bot should return a
    clear main menu.

## 7. Generate and inspect exports

After approving at least one report, stop the bot and run:

```powershell
.\scripts\run_local.ps1 -Command export
Get-Content -Raw .local\data\entries.json
Get-Content -Raw .local\data\entries.geojson
```

Confirm that:

- only approved, non-blocked reports are exported;
- umlauts and `ß` remain readable;
- GeoJSON coordinates use `[longitude, latitude]` order;
- Telegram IDs, reporter hashes, review and security metadata, and internal
  photo file names are absent;
- `has_photo` is only a Boolean indicator.

Do not edit either export manually. The encrypted `.local/data/entries.enc` file
is authoritative, and the export command regenerates both derived files.

## 8. Run automated checks

```powershell
.\.venv\Scripts\python -m unittest discover -s tests -v
.\.venv\Scripts\python -m ruff check --no-cache src tests scripts
.\.venv\Scripts\python -m pip check
.\.venv\Scripts\python scripts\security_check.py
git status --short
```

Expected result: all tests and checks pass, dependencies are consistent, and no
secret, local configuration, generated runtime data, or photo appears in Git
status.

## 9. Update an existing clone

Stop the bot before updating:

```powershell
git pull
.\.venv\Scripts\python -m pip install -e ".[dev]"
.\scripts\run_local.ps1
```

Reinstalling is important after dependency or package metadata changes. Existing
`.env.local`, `config/bot.local.json`, and `.local/` data remain local and are
not replaced by `git pull`.
