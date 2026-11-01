from __future__ import annotations

import json
from pathlib import Path

from cryptography.fernet import Fernet

from helpers import config_payload, entry
from telegram_geo_report_bot.config import load_settings
from telegram_geo_report_bot.services import build_services


LAYER_ID = "60fa595f-5700-4163-b8ed-4a93fd58f10d"
ENTRY_ID = "12345678-1234-5678-1234-567812345678"


def umap_payload(*, publish_photos=False):
    payload = config_payload()
    payload["umap"] = {
        "enabled": True,
        "base_url": "https://umap.example.test",
        "map_url": "https://umap.example.test/de/map/example_123/",
        "map_id": "123",
        "layer_id": LAYER_ID,
        "language": "de",
        "source_id": "test-project",
        "publish_photos": publish_photos,
        "category_styles": {
            "objects": {"marker_color": "#123456", "marker_label": "Ob"}
        },
    }
    return payload


def make_umap_services(root: Path, *, allow_write=False, publish_photos=False):
    root.mkdir(parents=True, exist_ok=True)
    config = root / "config.json"
    config.write_text(json.dumps(umap_payload(publish_photos=publish_photos)), "utf-8")
    key = Fernet.generate_key().decode("ascii")
    settings = load_settings(
        root,
        environ={
            "ENCRYPTION_KEY": key,
            "BOT_CONFIG_FILE": str(config),
            "BOT_DATA_DIR": str(root / "data"),
            "BOT_PHOTO_DIR": str(root / "photos"),
            "NOMINATIM_USER_AGENT": "Test/1.0 test@example.invalid",
            "UMAP_ALLOW_WRITE": str(allow_write).lower(),
        },
        require_token=False,
    )
    return build_services(settings)


def report(**updates):
    return entry(ENTRY_ID, **updates)


def layer(features=None):
    return {
        "type": "FeatureCollection",
        "id": LAYER_ID,
        "rank": 0,
        "properties": {"name": "Target", "displayOnLoad": True},
        "features": list(features or []),
    }
