from __future__ import annotations

import json
from pathlib import Path

from cryptography.fernet import Fernet

from telegram_geo_report_bot.config import load_settings
from telegram_geo_report_bot.services import build_services


def config_payload():
    return {
        "name": "Test Bot",
        "language": "de",
        "welcome_text": "Willkommen",
        "help_text": "Hilfe",
        "contact_text": "Kontakt",
        "public_result_url": "https://example.invalid/results",
        "channel_footer_text": "Alle Meldungen:",
        "daily_user_limit": 20,
        "description_max_length": 60,
        "photo_max_bytes": 1048576,
        "geocoding": {
            "country_codes": ["de"],
            "viewbox": None,
            "bounded": False,
            "result_limit": 5,
            "allowed_states": [],
            "allowed_counties": [],
            "allowed_municipalities": [],
            "postcode_prefixes": [],
        },
        "amount_options": ["1", "10+", "20+"],
        "categories": [
            {
                "id": "objects",
                "label": "Objekte",
                "subcategories": [
                    {"id": "small", "label": "Kleine Objekte"},
                    {"id": "large", "label": "Große Objekte"},
                ],
            },
            {
                "id": "other",
                "label": "Sonstiges",
                "subcategories": [{"id": "other", "label": "Sonstiges"}],
            },
        ],
    }


def make_services(root: Path, *, channel_id="-1001234567890"):
    config = root / "config.json"
    config.write_text(json.dumps(config_payload()), encoding="utf-8")
    key = Fernet.generate_key().decode("ascii")
    settings = load_settings(
        root,
        environ={
            "BOT_TOKEN": "123456:TEST_TOKEN_VALUE_ABCDEFGHIJKLMNOPQRSTUVWXYZ",
            "ENCRYPTION_KEY": key,
            "BOT_CONFIG_FILE": str(config),
            "BOT_DATA_DIR": str(root / "runtime"),
            "BOT_PHOTO_DIR": str(root / "photos"),
            "NOMINATIM_USER_AGENT": "TestBot/1.0 test@example.invalid",
            "TELEGRAM_CHANNEL_ID": channel_id,
            "TELEGRAM_CHANNEL_ALLOW_WRITE": "true",
        },
    )
    return build_services(settings)


def entry(identifier: str, *, status="approved", photo=None, reporter="reporter-a", **updates):
    value = {
        "id": identifier,
        "status": status,
        "created_at": "2026-08-16T12:00:00+00:00",
        "timestamp": "2026-08-16T12:00:00+00:00",
        "lat": 52.5,
        "lon": 13.4,
        "address": {
            "street": "Teststraße 1",
            "road": "Teststraße",
            "house_number": "1",
            "postcode": "10115",
            "city": "Teststadt",
        },
        "category": "objects",
        "subcategory": "small",
        "amount": "1",
        "description": "Hinweis",
        "photo": photo,
        "reporter_hash": reporter,
        "review": {"reviewed": status != "pending"},
        "validation": None,
    }
    value.update(updates)
    return value
