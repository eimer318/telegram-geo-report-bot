"""Rate-limited Nominatim address search."""

from __future__ import annotations

import threading
import time
from typing import Any

import requests

from .config import GeocodingConfig


class GeocodingError(RuntimeError):
    pass


def _fold(value: Any) -> str:
    return " ".join(str(value or "").casefold().split())


def _matches_any(values: tuple[Any, ...], allowed: tuple[str, ...]) -> bool:
    if not allowed:
        return True
    actual = {_fold(value) for value in values if _fold(value)}
    expected = {_fold(value) for value in allowed}
    return bool(actual & expected)


def _address_allowed(address: dict[str, Any], config: GeocodingConfig) -> bool:
    if not _matches_any((address.get("state"), address.get("region")), config.allowed_states):
        return False
    if not _matches_any(
        (address.get("county"), address.get("state_district")),
        config.allowed_counties,
    ):
        return False
    if not _matches_any(
        (
            address.get("city"),
            address.get("town"),
            address.get("village"),
            address.get("municipality"),
        ),
        config.allowed_municipalities,
    ):
        return False
    if config.postcode_prefixes:
        postcode = _fold(address.get("postcode")).replace(" ", "")
        prefixes = tuple(_fold(value).replace(" ", "") for value in config.postcode_prefixes)
        if not postcode or not postcode.startswith(prefixes):
            return False
    return True


def _filters_active(config: GeocodingConfig) -> bool:
    return any(
        (
            config.allowed_states,
            config.allowed_counties,
            config.allowed_municipalities,
            config.postcode_prefixes,
        )
    )


class NominatimClient:
    def __init__(
        self,
        config: GeocodingConfig,
        user_agent: str,
        search_url: str = "https://nominatim.openstreetmap.org/search",
    ):
        if not user_agent:
            raise ValueError("NOMINATIM_USER_AGENT fehlt")
        self.config = config
        self.user_agent = user_agent
        self.search_url = search_url
        self._lock = threading.Lock()
        self._last_request = 0.0
        self._cache: dict[str, list[dict[str, Any]]] = {}

    def search(self, query: str) -> list[dict[str, Any]]:
        cache_key = " ".join(query.casefold().split())
        if cache_key in self._cache:
            return self._cache[cache_key]
        params: dict[str, Any] = {
            "q": query,
            "format": "jsonv2",
            "addressdetails": 1,
            "limit": 40 if _filters_active(self.config) else self.config.result_limit,
        }
        if self.config.country_codes:
            params["countrycodes"] = ",".join(self.config.country_codes)
        if self.config.viewbox:
            params["viewbox"] = self.config.viewbox
            params["bounded"] = int(self.config.bounded)
        try:
            with self._lock:
                delay = 1.0 - (time.monotonic() - self._last_request)
                if delay > 0:
                    time.sleep(delay)
                response = requests.get(
                    self.search_url,
                    params=params,
                    headers={"User-Agent": self.user_agent},
                    timeout=10,
                )
                self._last_request = time.monotonic()
            response.raise_for_status()
            raw_results = response.json()
        except (requests.RequestException, ValueError) as exc:
            raise GeocodingError("Adresssuche ist vorübergehend nicht verfügbar") from exc
        if not isinstance(raw_results, list):
            raise GeocodingError("Adresssuche lieferte ein ungültiges Ergebnis")
        results = []
        seen = set()
        for raw in raw_results:
            if not isinstance(raw, dict):
                continue
            address = raw.get("address") if isinstance(raw.get("address"), dict) else {}
            if not _address_allowed(address, self.config):
                continue
            road = str(
                address.get("road")
                or address.get("pedestrian")
                or address.get("path")
                or ""
            ).strip()
            house_number = str(address.get("house_number") or "").strip()
            postcode = str(address.get("postcode") or "").strip()
            city = str(
                address.get("city")
                or address.get("town")
                or address.get("village")
                or address.get("municipality")
                or ""
            ).strip()
            state = str(address.get("state") or address.get("region") or "").strip()
            county = str(address.get("county") or address.get("state_district") or "").strip()
            street = " ".join(part for part in (road, house_number) if part)
            location = " ".join(part for part in (postcode, city) if part)
            label_parts = [part for part in (street, location) if part]
            if state and _fold(state) != _fold(city):
                label_parts.append(state)
            label = ", ".join(label_parts)
            normalized = " ".join(label.casefold().split())
            if not normalized or normalized in seen:
                continue
            try:
                lat = float(raw["lat"])
                lon = float(raw["lon"])
            except (KeyError, TypeError, ValueError):
                continue
            seen.add(normalized)
            results.append(
                {
                    "label": label,
                    "lat": lat,
                    "lon": lon,
                    "address": {
                        "street": street,
                        "road": road,
                        "house_number": house_number,
                        "postcode": postcode,
                        "city": city,
                        "state": state,
                        "county": county,
                        "country_code": str(address.get("country_code") or "").strip().lower(),
                    },
                }
            )
        results = results[: self.config.result_limit]
        if len(self._cache) >= 128:
            self._cache.pop(next(iter(self._cache)))
        self._cache[cache_key] = results
        return results
