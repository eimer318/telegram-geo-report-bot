"""Idempotent merge of repository reports into an existing uMap layer."""

from __future__ import annotations

import hashlib
import math
import re
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID

from ..exports import public_record


FEATURE_ID_RE = re.compile(r"^[A-Za-z0-9]{5}$")
BASE62 = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
MANAGED_PROPERTIES = {
    "source",
    "source_entry_id",
    "source_schema",
    "name",
    "description",
    "category",
    "category_key",
    "subcategory",
    "subcategory_key",
    "amount",
    "address",
    "created_at",
    "photo_included",
    "marker_label",
    "marker_color",
}
MANAGED_UMAP_OPTIONS = {"color", "iconClass", "iconUrl"}


class UMapLayerError(ValueError):
    pass


@dataclass(frozen=True)
class MergeSummary:
    added: int = 0
    updated: int = 0
    removed: int = 0
    unchanged: int = 0
    orphaned: int = 0
    photos: int = 0

    @property
    def changed(self) -> bool:
        return bool(self.added or self.updated or self.removed)


@dataclass(frozen=True)
class MergeResult:
    layer: dict
    summary: MergeSummary


def validate_layer(layer: dict, expected_layer_id: str) -> dict:
    if not isinstance(layer, dict) or layer.get("type") != "FeatureCollection":
        raise UMapLayerError("Der Ziel-Layer ist keine GeoJSON FeatureCollection")
    if str(layer.get("id")) != str(expected_layer_id):
        raise UMapLayerError("Die geladene Layer-ID stimmt nicht mit dem Ziel überein")
    if not isinstance(layer.get("features"), list):
        raise UMapLayerError("Der Ziel-Layer besitzt keine gültige Feature-Liste")
    if not isinstance(layer.get("properties"), dict):
        raise UMapLayerError("Die Eigenschaften des Ziel-Layers fehlen")
    for feature in layer["features"]:
        if not isinstance(feature, dict) or feature.get("type") != "Feature":
            raise UMapLayerError("Der Ziel-Layer enthält ein ungültiges Feature")
        if not isinstance(feature.get("properties"), dict):
            raise UMapLayerError("Ein Feature besitzt keine gültigen Eigenschaften")
    return layer


def _safe_text(value) -> str:
    replacements = {
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        "{": "&#123;",
        "}": "&#125;",
        "[": "&#91;",
        "]": "&#93;",
    }
    return "".join(replacements.get(char, char) for char in str(value or ""))


def _display_timestamp(value) -> str:
    if not value:
        return ""
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return str(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def _validate_uuid(value) -> str:
    try:
        return str(UUID(str(value)))
    except (TypeError, ValueError, AttributeError) as exc:
        raise UMapLayerError(f"Ungültige Eintrags-UUID: {value}") from exc


def _feature_id(source_id: str, entry_id: str, reserved: set[str]) -> str:
    for attempt in range(1000):
        digest = hashlib.sha256(
            f"{source_id}\0{entry_id}\0{attempt}".encode("utf-8")
        ).digest()
        number = int.from_bytes(digest[:8], "big")
        value = ""
        for _ in range(5):
            number, index = divmod(number, len(BASE62))
            value += BASE62[index]
        if value not in reserved:
            reserved.add(value)
            return value
    raise UMapLayerError("Keine kollisionsfreie uMap-Feature-ID gefunden")


def _coordinates(record: dict) -> tuple[float, float]:
    try:
        lat = float(record["lat"])
        lon = float(record["lon"])
    except (KeyError, TypeError, ValueError) as exc:
        raise UMapLayerError("Eine freigegebene Meldung hat ungültige Koordinaten") from exc
    if not math.isfinite(lat) or not math.isfinite(lon):
        raise UMapLayerError("Koordinaten müssen endlich sein")
    if not -90 <= lat <= 90 or not -180 <= lon <= 180:
        raise UMapLayerError("Koordinaten liegen außerhalb des gültigen Bereichs")
    return lat, lon


def _address_text(address: dict) -> str:
    street = str(address.get("street") or "").strip()
    place = " ".join(
        part
        for part in (
            str(address.get("postcode") or "").strip(),
            str(address.get("city") or "").strip(),
        )
        if part
    )
    return ", ".join(part for part in (street, place) if part)


def _managed_properties(record, bot_config, umap_config, image_uri=None) -> dict:
    category_key = record["category"]
    subcategory_key = record["subcategory"]
    category_label = bot_config.category_labels.get(category_key, category_key)
    subcategory_label = bot_config.subcategory_labels.get(
        subcategory_key, subcategory_key
    )
    address = _address_text(record["address"])
    created_at = _display_timestamp(record["created_at"])
    lines = []
    if subcategory_label:
        lines.append(f"Unterkategorie: {_safe_text(subcategory_label)}")
    if record.get("amount"):
        lines.append(f"Menge: {_safe_text(record['amount'])}")
    if address:
        lines.append(f"Adresse: {_safe_text(address)}")
    if record.get("description"):
        lines.extend(("", _safe_text(record["description"])))
    if created_at:
        lines.extend(("", f"Erfasst: {_safe_text(created_at)}"))
    if image_uri:
        lines.extend(("", f"{{{{{image_uri}}}}}"))
    style = umap_config.category_styles.get(category_key)
    return {
        "source": umap_config.source_id,
        "source_entry_id": record["id"],
        "source_schema": 1,
        "name": category_label,
        "description": "\n".join(lines),
        "category": category_label,
        "category_key": category_key,
        "subcategory": subcategory_label,
        "subcategory_key": subcategory_key,
        "amount": record.get("amount"),
        "address": address,
        "created_at": created_at,
        "photo_included": bool(image_uri),
        "marker_label": style.marker_label if style else "",
        "marker_color": style.marker_color if style else "",
    }


def _desired_feature(
    entry,
    record,
    existing,
    reserved,
    bot_config,
    umap_config,
    photo_loader,
):
    lat, lon = _coordinates(record)
    image_uri = None
    if umap_config.publish_photos and record["has_photo"]:
        photo_name = entry.get("photo")
        if not photo_name or photo_name == "Null":
            raise UMapLayerError("Eine Meldung verweist auf ein fehlendes Foto")
        image_uri = photo_loader(photo_name)
    feature = deepcopy(existing) if existing else {"type": "Feature", "properties": {}}
    if not FEATURE_ID_RE.fullmatch(str(feature.get("id") or "")):
        feature["id"] = _feature_id(umap_config.source_id, record["id"], reserved)
    feature["geometry"] = {"type": "Point", "coordinates": [lon, lat]}
    properties = feature.setdefault("properties", {})
    for key in MANAGED_PROPERTIES:
        properties.pop(key, None)
    properties.update(_managed_properties(record, bot_config, umap_config, image_uri))

    options = properties.get("_umap_options")
    options = deepcopy(options) if isinstance(options, dict) else {}
    for key in MANAGED_UMAP_OPTIONS:
        options.pop(key, None)
    style = umap_config.category_styles.get(record["category"])
    if style:
        if style.marker_color:
            options["color"] = style.marker_color
        if style.marker_label:
            options.update({"iconClass": "Default", "iconUrl": style.marker_label})
    if options:
        properties["_umap_options"] = options
    else:
        properties.pop("_umap_options", None)
    return feature, bool(image_uri)


def merge_entries(layer, entries, bot_config, umap_config, photo_loader) -> MergeResult:
    validate_layer(layer, umap_config.layer_id)
    if not isinstance(entries, list):
        raise UMapLayerError("Die Eintragsquelle muss eine Liste sein")
    local = {}
    for entry in entries:
        if not isinstance(entry, dict):
            raise UMapLayerError("Die Eintragsquelle enthält einen Nicht-Objektwert")
        entry_id = _validate_uuid(entry.get("id"))
        if entry_id in local:
            raise UMapLayerError(f"Doppelte Eintrags-UUID: {entry_id}")
        local[entry_id] = entry

    reserved: set[str] = set()
    remote_ids: set[str] = set()
    for feature in layer["features"]:
        if FEATURE_ID_RE.fullmatch(str(feature.get("id") or "")):
            reserved.add(str(feature["id"]))
        properties = feature["properties"]
        if properties.get("source") != umap_config.source_id:
            continue
        entry_id = str(properties.get("source_entry_id") or "")
        if not entry_id:
            raise UMapLayerError("Verwaltetes uMap-Feature ohne source_entry_id")
        if entry_id in remote_ids:
            raise UMapLayerError(f"Doppeltes verwaltetes uMap-Feature: {entry_id}")
        remote_ids.add(entry_id)

    output = deepcopy(layer)
    output["features"] = []
    handled: set[str] = set()
    added = updated = removed = unchanged = orphaned = photos = 0
    for feature in layer["features"]:
        properties = feature["properties"]
        if properties.get("source") != umap_config.source_id:
            output["features"].append(deepcopy(feature))
            continue
        entry_id = str(properties.get("source_entry_id"))
        entry = local.get(entry_id)
        if entry is None:
            output["features"].append(deepcopy(feature))
            orphaned += 1
            continue
        handled.add(entry_id)
        record = public_record(entry)
        if record is None:
            removed += 1
            continue
        desired, has_photo = _desired_feature(
            entry,
            record,
            feature,
            reserved,
            bot_config,
            umap_config,
            photo_loader,
        )
        photos += int(has_photo)
        output["features"].append(desired)
        if desired == feature:
            unchanged += 1
        else:
            updated += 1

    for entry_id, entry in local.items():
        if entry_id in handled:
            continue
        record = public_record(entry)
        if record is None:
            continue
        desired, has_photo = _desired_feature(
            entry,
            record,
            None,
            reserved,
            bot_config,
            umap_config,
            photo_loader,
        )
        photos += int(has_photo)
        output["features"].append(desired)
        added += 1

    validate_layer(output, umap_config.layer_id)
    return MergeResult(
        output,
        MergeSummary(
            added=added,
            updated=updated,
            removed=removed,
            unchanged=unchanged,
            orphaned=orphaned,
            photos=photos,
        ),
    )
