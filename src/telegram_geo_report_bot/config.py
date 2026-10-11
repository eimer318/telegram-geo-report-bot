"""Validated application configuration without import-time side effects."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from cryptography.fernet import Fernet
from dotenv import dotenv_values


_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,31}$")


class ConfigurationError(RuntimeError):
    """Configuration is missing or unsafe."""


@dataclass(frozen=True)
class Choice:
    id: str
    label: str


@dataclass(frozen=True)
class Category:
    id: str
    label: str
    subcategories: tuple[Choice, ...]


@dataclass(frozen=True)
class GeocodingConfig:
    country_codes: tuple[str, ...]
    viewbox: str | None
    bounded: bool
    result_limit: int
    allowed_states: tuple[str, ...]
    allowed_counties: tuple[str, ...]
    allowed_municipalities: tuple[str, ...]
    postcode_prefixes: tuple[str, ...]

@dataclass(frozen=True)
class RateLimitConfig:
    enabled: bool
    per_minute: int
    per_hour: int



@dataclass(frozen=True)
class BotConfig:
    name: str
    language: str
    welcome_text: str
    help_text: str
    contact_text: str
    public_result_url: str
    channel_footer_text: str
    daily_user_limit: int
    rate_limits: RateLimitConfig
    description_max_length: int
    photo_max_bytes: int
    geocoding: GeocodingConfig
    amount_options: tuple[str, ...]
    categories: tuple[Category, ...]

    @property
    def category_labels(self) -> dict[str, str]:
        return {category.id: category.label for category in self.categories}

    @property
    def subcategory_labels(self) -> dict[str, str]:
        return {
            choice.id: choice.label
            for category in self.categories
            for choice in category.subcategories
        }

    def category(self, category_id: str) -> Category | None:
        return next((item for item in self.categories if item.id == category_id), None)


@dataclass(frozen=True)
class Paths:
    base_dir: Path
    data_dir: Path
    photo_dir: Path
    entries_file: Path
    json_export_file: Path
    geojson_export_file: Path
    admins_file: Path
    channel_state_file: Path
    channel_lock_path: Path
    security_events_file: Path
    rate_limit_file: Path


@dataclass(frozen=True)
class Settings:
    token: str
    encryption_key: str
    cipher: Fernet
    config: BotConfig
    paths: Paths
    channel_id: str
    channel_write_enabled: bool
    channel_daily_limit: bool
    nominatim_user_agent: str
    nominatim_search_url: str


def _boolean(value: Any, default: bool = False) -> bool:
    if value in (None, ""):
        return default
    normalized = str(value).strip().casefold()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ConfigurationError(f"Ungültiger boolescher Wert: {value!r}")


def _http_url(value: Any, name: str) -> str:
    cleaned = str(value or "").strip()
    parsed = urlsplit(cleaned)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ConfigurationError(f"{name} muss eine vollständige HTTP(S)-URL sein")
    if parsed.username or parsed.password:
        raise ConfigurationError(f"{name} darf keine Zugangsdaten enthalten")
    return cleaned


def _positive_int(value: Any, name: str, *, minimum: int = 1, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ConfigurationError(f"{name} muss eine Zahl sein") from exc
    if not minimum <= parsed <= maximum:
        raise ConfigurationError(f"{name} muss zwischen {minimum} und {maximum} liegen")
    return parsed


def _clean_text(value: Any, name: str, *, required: bool = True, limit: int = 1000) -> str:
    text = " ".join(str(value or "").split())
    if required and not text:
        raise ConfigurationError(f"{name} fehlt")
    if len(text) > limit:
        raise ConfigurationError(f"{name} ist zu lang")
    return text


def _clean_multiline_text(
    value: Any,
    name: str,
    *,
    required: bool = True,
    limit: int = 4000,
) -> str:
    cleaned: list[str] = []
    previous_blank = False
    raw_text = str(value or "").replace("\r\n", "\n").replace("\r", "\n")
    for raw_line in raw_text.split("\n"):
        line = " ".join(raw_line.split())
        if line:
            cleaned.append(line)
            previous_blank = False
        elif cleaned and not previous_blank:
            cleaned.append("")
            previous_blank = True
    text = "\n".join(cleaned).strip()
    if required and not text:
        raise ConfigurationError(f"{name} fehlt")
    if len(text) > limit:
        raise ConfigurationError(f"{name} ist zu lang")
    return text


def _identifier(value: Any, name: str) -> str:
    identifier = str(value or "").strip()
    if not _ID_PATTERN.fullmatch(identifier):
        raise ConfigurationError(
            f"{name} muss mit einem Kleinbuchstaben beginnen und darf nur "
            "Kleinbuchstaben, Zahlen und Unterstriche enthalten"
        )
    return identifier


def _optional_text_list(value: Any, name: str, *, item_limit: int = 100) -> tuple[str, ...]:
    if value in (None, ""):
        return ()
    if not isinstance(value, list):
        raise ConfigurationError(f"{name} muss eine Liste sein")
    cleaned = tuple(_clean_text(item, name, limit=item_limit) for item in value)
    normalized = [item.casefold() for item in cleaned]
    if len(normalized) != len(set(normalized)):
        raise ConfigurationError(f"{name} enthält doppelte Werte")
    return cleaned


def load_bot_config(path: Path) -> BotConfig:
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigurationError(f"Bot-Konfiguration ist nicht lesbar: {path}") from exc
    if not isinstance(raw, dict):
        raise ConfigurationError("Bot-Konfiguration muss ein JSON-Objekt sein")

    raw_categories = raw.get("categories")
    if not isinstance(raw_categories, list) or not raw_categories:
        raise ConfigurationError("Mindestens eine Kategorie ist erforderlich")
    categories: list[Category] = []
    identifiers: set[str] = set()
    subcategory_identifiers: set[str] = set()
    for category_index, item in enumerate(raw_categories):
        if not isinstance(item, dict):
            raise ConfigurationError("Jede Kategorie muss ein Objekt sein")
        category_id = _identifier(item.get("id"), f"categories[{category_index}].id")
        if category_id in identifiers:
            raise ConfigurationError(f"Doppelte Kategorie-ID: {category_id}")
        identifiers.add(category_id)
        raw_subcategories = item.get("subcategories")
        if not isinstance(raw_subcategories, list) or not raw_subcategories:
            raise ConfigurationError(f"Kategorie {category_id} benötigt Unterkategorien")
        subcategories: list[Choice] = []
        for sub_index, subitem in enumerate(raw_subcategories):
            if not isinstance(subitem, dict):
                raise ConfigurationError("Jede Unterkategorie muss ein Objekt sein")
            sub_id = _identifier(
                subitem.get("id"),
                f"categories[{category_index}].subcategories[{sub_index}].id",
            )
            if sub_id in subcategory_identifiers:
                raise ConfigurationError(f"Doppelte Unterkategorie-ID: {sub_id}")
            subcategory_identifiers.add(sub_id)
            subcategories.append(Choice(sub_id, _clean_text(subitem.get("label"), "label")))
        categories.append(
            Category(
                category_id,
                _clean_text(item.get("label"), f"categories[{category_index}].label"),
                tuple(subcategories),
            )
        )

    amounts = raw.get("amount_options")
    if not isinstance(amounts, list) or not amounts:
        raise ConfigurationError("amount_options darf nicht leer sein")
    amount_options = tuple(_clean_text(value, "amount_option", limit=20) for value in amounts)
    if len(set(amount_options)) != len(amount_options):
        raise ConfigurationError("amount_options enthält doppelte Werte")

    geocoding_raw = raw.get("geocoding") or {}
    if not isinstance(geocoding_raw, dict):
        raise ConfigurationError("geocoding muss ein Objekt sein")
    country_codes_raw = geocoding_raw.get("country_codes") or []
    if not isinstance(country_codes_raw, list):
        raise ConfigurationError("geocoding.country_codes muss eine Liste sein")
    country_codes = tuple(str(value).strip().lower() for value in country_codes_raw if str(value).strip())
    if any(not re.fullmatch(r"[a-z]{2}", value) for value in country_codes):
        raise ConfigurationError("country_codes müssen zweistellige ISO-Codes sein")
    viewbox_value = geocoding_raw.get("viewbox")
    viewbox = _clean_text(viewbox_value, "viewbox", required=False, limit=100) or None
    allowed_states = _optional_text_list(
        geocoding_raw.get("allowed_states"), "geocoding.allowed_states"
    )
    allowed_counties = _optional_text_list(
        geocoding_raw.get("allowed_counties"), "geocoding.allowed_counties"
    )
    allowed_municipalities = _optional_text_list(
        geocoding_raw.get("allowed_municipalities"),
        "geocoding.allowed_municipalities",
    )
    postcode_prefixes = _optional_text_list(
        geocoding_raw.get("postcode_prefixes"),
        "geocoding.postcode_prefixes",
        item_limit=20,
    )

    rate_limits_raw = raw.get("rate_limits") or {}
    if not isinstance(rate_limits_raw, dict):
        raise ConfigurationError("rate_limits muss ein Objekt sein")

    return BotConfig(
        name=_clean_text(raw.get("name"), "name", limit=100),
        language=_clean_text(raw.get("language", "de"), "language", limit=10),
        welcome_text=_clean_multiline_text(raw.get("welcome_text"), "welcome_text"),
        help_text=_clean_multiline_text(raw.get("help_text"), "help_text"),
        contact_text=_clean_multiline_text(
            raw.get("contact_text"), "contact_text", required=False
        ),
        public_result_url=_clean_text(
            raw.get("public_result_url"), "public_result_url", required=False, limit=500
        ),
        channel_footer_text=_clean_text(
            raw.get("channel_footer_text"), "channel_footer_text", required=False, limit=500
        ),
        daily_user_limit=_positive_int(
            raw.get("daily_user_limit", 0),
            "daily_user_limit",
            minimum=0,
            maximum=1000,
        ),
        rate_limits=RateLimitConfig(
            enabled=_boolean(rate_limits_raw.get("enabled"), False),
            per_minute=_positive_int(
                rate_limits_raw.get("per_minute", 3),
                "rate_limits.per_minute",
                maximum=1000,
            ),
            per_hour=_positive_int(
                rate_limits_raw.get("per_hour", 10),
                "rate_limits.per_hour",
                maximum=10_000,
            ),
        ),
        description_max_length=_positive_int(
            raw.get("description_max_length", 60),
            "description_max_length",
            maximum=1000,
        ),
        photo_max_bytes=_positive_int(
            raw.get("photo_max_bytes", 10 * 1024 * 1024),
            "photo_max_bytes",
            maximum=50 * 1024 * 1024,
        ),
        geocoding=GeocodingConfig(
            country_codes=country_codes,
            viewbox=viewbox,
            bounded=_boolean(geocoding_raw.get("bounded"), False),
            result_limit=_positive_int(
                geocoding_raw.get("result_limit", 8), "result_limit", maximum=20
            ),
            allowed_states=allowed_states,
            allowed_counties=allowed_counties,
            allowed_municipalities=allowed_municipalities,
            postcode_prefixes=postcode_prefixes,
        ),
        amount_options=amount_options,
        categories=tuple(categories),
    )


def load_settings(
    base_dir: Path | None = None,
    *,
    env_file: Path | None = None,
    environ: dict[str, str] | None = None,
    require_token: bool = True,
) -> Settings:
    base_dir = Path(base_dir or Path.cwd()).resolve()
    values = dict(os.environ if environ is None else environ)
    selected_env_file = Path(env_file or values.get("BOT_ENV_FILE") or base_dir / ".env")
    if selected_env_file.is_file():
        for key, value in dotenv_values(selected_env_file).items():
            if value is not None:
                values.setdefault(key, str(value))

    token = values.get("BOT_TOKEN", "").strip()
    key = values.get("ENCRYPTION_KEY", "").strip()
    if require_token and not token:
        raise ConfigurationError("BOT_TOKEN fehlt")
    if not key:
        raise ConfigurationError("ENCRYPTION_KEY fehlt")
    try:
        cipher = Fernet(key.encode("ascii"))
    except (ValueError, UnicodeEncodeError) as exc:
        raise ConfigurationError("ENCRYPTION_KEY ist kein gültiger Fernet-Schlüssel") from exc

    config_path = Path(values.get("BOT_CONFIG_FILE", "config/bot.json"))
    if not config_path.is_absolute():
        config_path = base_dir / config_path
    data_dir = Path(values.get("BOT_DATA_DIR", "data"))
    photo_dir = Path(values.get("BOT_PHOTO_DIR", "photos"))
    if not data_dir.is_absolute():
        data_dir = base_dir / data_dir
    if not photo_dir.is_absolute():
        photo_dir = base_dir / photo_dir
    data_dir = data_dir.resolve()
    photo_dir = photo_dir.resolve()

    paths = Paths(
        base_dir=base_dir,
        data_dir=data_dir,
        photo_dir=photo_dir,
        entries_file=data_dir / "entries.enc",
        json_export_file=data_dir / "entries.json",
        geojson_export_file=data_dir / "entries.geojson",
        admins_file=data_dir / "admins.enc",
        channel_state_file=data_dir / "channel_publish_state.enc",
        channel_lock_path=data_dir / "channel_publish.lock",
        security_events_file=data_dir / "security_events.enc",
        rate_limit_file=data_dir / "rate_limit_state.enc",
    )
    return Settings(
        token=token,
        encryption_key=key,
        cipher=cipher,
        config=load_bot_config(config_path),
        paths=paths,
        channel_id=values.get("TELEGRAM_CHANNEL_ID", "").strip(),
        channel_write_enabled=_boolean(values.get("TELEGRAM_CHANNEL_ALLOW_WRITE"), False),
        channel_daily_limit=_boolean(values.get("CHANNEL_DAILY_LIMIT"), True),
        nominatim_user_agent=values.get(
            "NOMINATIM_USER_AGENT", "TelegramGeoReportBot/1.0"
        ).strip(),
        nominatim_search_url=_http_url(
            values.get(
                "NOMINATIM_SEARCH_URL",
                "https://nominatim.openstreetmap.org/search",
            ),
            "NOMINATIM_SEARCH_URL",
        ),
    )
