"""Application service container."""

from __future__ import annotations

from dataclasses import dataclass

from .admins import AdminStore
from .config import Settings
from .entries import EntryRepository
from .geocoding import NominatimClient
from .photos import PhotoStore


@dataclass(frozen=True)
class Services:
    settings: Settings
    entries: EntryRepository
    photos: PhotoStore
    admins: AdminStore
    geocoder: NominatimClient


def build_services(settings: Settings) -> Services:
    return Services(
        settings=settings,
        entries=EntryRepository(
            settings.paths.entries_file,
            settings.paths.json_export_file,
            settings.paths.geojson_export_file,
            settings.cipher,
        ),
        photos=PhotoStore(settings.paths.photo_dir, settings.cipher),
        admins=AdminStore(settings.paths.admins_file, settings.cipher),
        geocoder=NominatimClient(settings.config.geocoding, settings.nominatim_user_agent),
    )
