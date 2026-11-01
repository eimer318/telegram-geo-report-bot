"""Minimal authenticated HTTP client for uMap layer synchronization."""

from __future__ import annotations

import json
from dataclasses import dataclass
from urllib.parse import urlsplit

import requests

from .session_store import UMapCredentials


class UMapError(RuntimeError):
    pass


class UMapAuthenticationError(UMapError):
    pass


class UMapNetworkError(UMapError):
    pass


class UMapConflictError(UMapError):
    pass


class UMapAmbiguousUploadError(UMapError):
    pass


@dataclass(frozen=True)
class LayerSnapshot:
    layer: dict
    reference: str


class UMapClient:
    def __init__(self, config, credentials: UMapCredentials, *, user_agent: str, session=None):
        self.config = config
        self.credentials = credentials
        self.session = session or requests.Session()
        hostname = urlsplit(config.base_url).hostname
        self.session.cookies.set("sessionid", credentials.session_id, domain=hostname, path="/")
        self.session.cookies.set("csrftoken", credentials.csrf_token, domain=hostname, path="/")
        self.session.headers.update({
            "User-Agent": credentials.user_agent or user_agent,
            "Accept": "application/json, text/plain, */*",
        })

    @staticmethod
    def _check_common(response) -> None:
        if response.status_code in {301, 302, 303, 307, 308, 401}:
            raise UMapAuthenticationError("Die uMap-Sitzung ist nicht mehr gültig")
        if response.status_code == 403:
            raise UMapAuthenticationError("uMap hat Sitzung oder CSRF-Wert abgelehnt")

    @staticmethod
    def _check_transient(response, operation: str) -> None:
        if response.status_code == 429 or 500 <= response.status_code <= 599:
            raise UMapNetworkError(
                f"Temporärer uMap-Fehler bei {operation}: HTTP {response.status_code}"
            )

    def verify_login(self) -> bool:
        try:
            response = self.session.get(
                f"{self.config.base_url}/{self.config.language}/whoami",
                headers={"X-Requested-With": "XMLHttpRequest"},
                timeout=(10, 30),
                allow_redirects=False,
            )
        except requests.RequestException as exc:
            raise UMapNetworkError("Netzwerkfehler bei der uMap-Anmeldeprüfung") from exc
        self._check_common(response)
        self._check_transient(response, "Anmeldeprüfung")
        if response.status_code != 200:
            raise UMapError(f"uMap-Anmeldeprüfung fehlgeschlagen: HTTP {response.status_code}")
        try:
            payload = response.json()
        except ValueError as exc:
            raise UMapAuthenticationError("uMap lieferte keine gültige Anmeldeantwort") from exc
        if not isinstance(payload, dict) or not payload.get("user"):
            raise UMapAuthenticationError("uMap meldet keine angemeldete Person")
        return True

    def download_layer(self) -> LayerSnapshot:
        try:
            response = self.session.get(
                self.config.layer_url,
                headers={
                    "Cache-Control": "no-cache",
                    "X-Requested-With": "XMLHttpRequest",
                },
                timeout=(10, 60),
                allow_redirects=False,
            )
        except requests.RequestException as exc:
            raise UMapNetworkError("Netzwerkfehler beim uMap-Layer-Download") from exc
        self._check_common(response)
        self._check_transient(response, "Layer-Download")
        if response.status_code != 200:
            raise UMapError(f"uMap-Layer-Download fehlgeschlagen: HTTP {response.status_code}")
        reference = str(response.headers.get("X-Datalayer-Version") or "").strip()
        if not reference:
            raise UMapError("Der uMap-Layer enthält keine Konfliktversion")
        try:
            layer = response.json()
        except ValueError as exc:
            raise UMapError("Der uMap-Layer ist kein gültiges JSON") from exc
        return LayerSnapshot(layer=layer, reference=reference)

    def upload_layer(self, layer: dict, reference: str) -> dict:
        if not str(reference or "").strip():
            raise UMapError("Upload ohne Konfliktversion ist gesperrt")
        properties = layer.get("properties") or {}
        payload = json.dumps(layer, ensure_ascii=False, separators=(",", ":"))
        data = {
            "name": properties.get("name", ""),
            "parent": layer.get("parent", ""),
            "display_on_load": str(bool(properties.get("displayOnLoad", True))).lower(),
            "rank": str(layer.get("rank", 0)),
            "settings": json.dumps(properties, ensure_ascii=False, separators=(",", ":")),
        }
        try:
            response = self.session.post(
                self.config.update_url,
                data=data,
                files={"geojson": ("layer.geojson", payload, "application/geo+json")},
                headers={
                    "X-Requested-With": "XMLHttpRequest",
                    "X-CSRFToken": self.credentials.csrf_token,
                    "X-Datalayer-Reference": str(reference),
                    "Origin": self.config.base_url,
                    "Referer": self.config.map_url,
                },
                timeout=(10, 120),
                allow_redirects=False,
            )
        except requests.RequestException as exc:
            raise UMapAmbiguousUploadError(
                "Netzwerkfehler beim uMap-Upload; der Serverzustand ist unklar"
            ) from exc
        if response.status_code == 412:
            raise UMapConflictError(
                "Der uMap-Layer wurde parallel geändert; Synchronisierung erneut starten"
            )
        self._check_common(response)
        if response.status_code == 429 or 500 <= response.status_code <= 599:
            raise UMapAmbiguousUploadError(
                "uMap bestätigte den Upload nicht eindeutig; der Serverzustand ist unklar"
            )
        if response.status_code != 200:
            raise UMapError(f"uMap-Layer-Upload fehlgeschlagen: HTTP {response.status_code}")
        try:
            result = response.json()
        except ValueError as exc:
            raise UMapError("Der uMap-Upload lieferte keine gültige JSON-Antwort") from exc
        if not isinstance(result, dict):
            raise UMapError("Der uMap-Upload lieferte ein unerwartetes Ergebnis")
        return result
