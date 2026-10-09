"""Privacy-preserving JSON and GeoJSON exports."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Iterable


def publication_blocked(entry: dict[str, Any]) -> bool:
    security = entry.get("security")
    return isinstance(security, dict) and security.get("publication_blocked") is True


def _coordinates(entry: dict[str, Any]) -> tuple[float, float] | None:
    try:
        lat = float(entry.get("lat"))
        lon = float(entry.get("lon"))
    except (TypeError, ValueError):
        return None
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return None
    return lat, lon


def public_record(entry: dict[str, Any]) -> dict[str, Any] | None:
    """Return the stable public JSON representation or ``None`` if private."""
    if entry.get("status") != "approved" or publication_blocked(entry):
        return None
    coordinates = _coordinates(entry)
    if coordinates is None:
        return None
    lat, lon = coordinates
    address = entry.get("address") if isinstance(entry.get("address"), dict) else {}
    return {
        "id": str(entry.get("id", "")),
        "created_at": str(entry.get("created_at") or entry.get("timestamp") or ""),
        "lat": lat,
        "lon": lon,
        "address": {
            "street": str(address.get("street") or ""),
            "road": str(address.get("road") or ""),
            "house_number": str(address.get("house_number") or ""),
            "postcode": str(address.get("postcode") or ""),
            "city": str(address.get("city") or ""),
        },
        "category": str(entry.get("category") or ""),
        "subcategory": str(entry.get("subcategory") or ""),
        "amount": None if entry.get("amount") in (None, "", "Null") else str(entry["amount"]),
        "description": str(entry.get("description") or ""),
        "has_photo": entry.get("photo") not in (None, "", "Null"),
    }


def json_payload(entries: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    return [record for entry in entries if (record := public_record(entry)) is not None]


def geojson_payload(entries: Iterable[dict[str, Any]]) -> dict[str, Any]:
    features = []
    for record in json_payload(entries):
        properties = {
            "created_at": record["created_at"],
            "street": record["address"]["street"],
            "road": record["address"]["road"],
            "house_number": record["address"]["house_number"],
            "postcode": record["address"]["postcode"],
            "city": record["address"]["city"],
            "category": record["category"],
            "subcategory": record["subcategory"],
            "amount": record["amount"],
            "description": record["description"],
            "has_photo": record["has_photo"],
        }
        features.append(
            {
                "type": "Feature",
                "id": record["id"],
                "geometry": {
                    "type": "Point",
                    "coordinates": [record["lon"], record["lat"]],
                },
                "properties": properties,
            }
        )
    return {"type": "FeatureCollection", "features": features}


def _atomic_json(path: Path, payload: Any) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        try:
            path.chmod(0o600)
        except OSError:
            pass
    finally:
        temporary.unlink(missing_ok=True)


def write_exports(
    entries: Iterable[dict[str, Any]], json_path: Path, geojson_path: Path
) -> int:
    entries = list(entries)
    records = json_payload(entries)
    _atomic_json(Path(json_path), records)
    _atomic_json(Path(geojson_path), geojson_payload(entries))
    return len(records)
