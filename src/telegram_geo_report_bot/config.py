"""Validated application configuration without import-time side effects."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID

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
class UMapCategoryStyle:
    marker_color: str = ""
    marker_label: str = ""


@dataclass(frozen=True)
class UMapConfig:
    base_url: str
    map_url: str
    map_id: str
    layer_id: str
    language: str
    source_id: str
    publish_photos: bool
    category_styles: dict[str, UMapCategoryStyle]

    @property
    def layer_url(self) -> str:
        return (
            f"{self.base_url}/{self.language}/datalayer/"
            f"{self.map_id}/{self.layer_id}/"
        )

    @property
    def update_url(self) -> str:
        return (
            f"{self.base_url}/{self.language}/map/{self.map_id}/"
            f"datalayer/update/{self.layer_id}/"
        )



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
    umap: UMapConfig | None

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
    umap_session_file: Path
    umap_backup_dir: Path
    umap_lock_path: Path


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
    umap_write_enabled: bool
    umap_user_agent: str


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


def _https_origin(value: Any, name: str) -> str:
    cleaned = _http_url(value, name).rstrip("/")
    parsed = urlsplit(cleaned)
    if parsed.scheme != "https" or parsed.path not in {"", "/"}:
        raise ConfigurationError(f"{name} muss eine HTTPS-Origin ohne Pfad sein")
    if parsed.query or parsed.fragment:
        raise ConfigurationError(f"{name} darf keine Query oder Fragment enthalten")
    return cleaned


def _umap_source_id(value: Any) -> str:
    cleaned = str(value or "").strip()
    if not re.fullmatch(r"[a-z][a-z0-9_-]{2,63}", cleaned):
        raise ConfigurationError(
            "umap.source_id muss mit einem Kleinbuchstaben beginnen und darf "
            "nur Kleinbuchstaben, Zahlen, Bindestriche und Unterstriche enthalten"
        )
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

    umap_config = None
    umap_raw = raw.get("umap") or {}
    if not isinstance(umap_raw, dict):
        raise ConfigurationError("umap muss ein Objekt sein")
    if _boolean(umap_raw.get("enabled"), False):
        base_url = _https_origin(umap_raw.get("base_url"), "umap.base_url")
        map_url = _http_url(umap_raw.get("map_url"), "umap.map_url")
        base = urlsplit(base_url)
        target = urlsplit(map_url)
        if (target.scheme, target.netloc) != (base.scheme, base.netloc):
            raise ConfigurationError("umap.map_url gehört nicht zur uMap-Origin")
        map_id = str(umap_raw.get("map_id") or "").strip()
        if not map_id.isdigit():
            raise ConfigurationError("umap.map_id muss numerisch sein")
        if map_id not in target.path:
            raise ConfigurationError("umap.map_url enthält nicht die konfigurierte map_id")
        layer_id = str(umap_raw.get("layer_id") or "").strip()
        try:
            UUID(layer_id)
        except (ValueError, AttributeError) as exc:
            raise ConfigurationError("umap.layer_id muss eine UUID sein") from exc
        language = str(umap_raw.get("language") or "de").strip().lower()
        if not re.fullmatch(r"[a-z]{2}(?:-[a-z]{2})?", language):
            raise ConfigurationError("umap.language ist ungültig")

        styles_raw = umap_raw.get("category_styles") or {}
        if not isinstance(styles_raw, dict):
            raise ConfigurationError("umap.category_styles muss ein Objekt sein")
        known_categories = {category.id for category in categories}
        category_styles: dict[str, UMapCategoryStyle] = {}
        for category_id, style_raw in styles_raw.items():
            if category_id not in known_categories:
                raise ConfigurationError(
                    f"umap.category_styles enthält unbekannte Kategorie: {category_id}"
                )
            if not isinstance(style_raw, dict):
                raise ConfigurationError(
                    f"umap.category_styles.{category_id} muss ein Objekt sein"
                )
            category_styles[category_id] = UMapCategoryStyle(
                marker_color=_clean_text(
                    style_raw.get("marker_color"),
                    f"umap.category_styles.{category_id}.marker_color",
                    required=False,
                    limit=50,
                ),
                marker_label=_clean_text(
                    style_raw.get("marker_label"),
                    f"umap.category_styles.{category_id}.marker_label",
                    required=False,
                    limit=20,
                ),
            )
        umap_config = UMapConfig(
            base_url=base_url,
            map_url=map_url,
            map_id=map_id,
            layer_id=layer_id,
            language=language,
            source_id=_umap_source_id(umap_raw.get("source_id")),
            publish_photos=_boolean(umap_raw.get("publish_photos"), False),
            category_styles=category_styles,
        )

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
        umap=umap_config,
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
    umap_session_file = Path(values.get("UMAP_SESSION_FILE", "data/umap_session.enc"))
    umap_backup_dir = Path(values.get("UMAP_BACKUP_DIR", "data/umap_backups"))
    if not umap_session_file.is_absolute():
        umap_session_file = base_dir / umap_session_file
    if not umap_backup_dir.is_absolute():
        umap_backup_dir = base_dir / umap_backup_dir

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
        umap_session_file=umap_session_file.resolve(),
        umap_backup_dir=umap_backup_dir.resolve(),
        umap_lock_path=data_dir / "umap_sync.lock",
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
        umap_write_enabled=_boolean(values.get("UMAP_ALLOW_WRITE"), False),
        umap_user_agent=(
            values.get("UMAP_USER_AGENT", "TelegramGeoReportBot-uMap/1.0").strip()
            or "TelegramGeoReportBot-uMap/1.0"
        ),
    )
