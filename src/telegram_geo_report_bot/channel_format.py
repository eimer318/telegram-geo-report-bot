"""Neutral, configuration-driven Telegram channel formatting."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Iterable

from .config import BotConfig


TEXT_LIMIT = 4096
GERMAN_MONTHS = (
    "", "Januar", "Februar", "März", "April", "Mai", "Juni",
    "Juli", "August", "September", "Oktober", "November", "Dezember",
)


def localized_date(now: datetime, language: str) -> str:
    if language.casefold() == "de":
        return f"{now.day}. {GERMAN_MONTHS[now.month]} {now.year}"
    return now.date().isoformat()


def _address(entry: dict[str, Any]) -> str:
    address = entry.get("address") if isinstance(entry.get("address"), dict) else {}
    street = " ".join(str(address.get("street") or "").split())
    postcode = " ".join(str(address.get("postcode") or "").split())
    city = " ".join(str(address.get("city") or "").split())
    locality = " ".join(part for part in (postcode, city) if part)
    return ", ".join(part for part in (street, locality) if part) or "–"


def _entry_line(entry: dict[str, Any]) -> str:
    line = _address(entry)
    amount = entry.get("amount")
    if amount not in (None, "", "Null"):
        line += f" ({amount})"
    return line


def _grouped(entries: Iterable[dict[str, Any]], config: BotConfig):
    categories: dict[str, dict[str, list[str]]] = {}
    category_order = [item.id for item in config.categories]
    subcategory_order = [
        choice.id for category in config.categories for choice in category.subcategories
    ]
    for entry in entries:
        category = str(entry.get("category") or "other")
        subcategory = str(entry.get("subcategory") or "other")
        categories.setdefault(category, {}).setdefault(subcategory, []).append(_entry_line(entry))

    def ordered(values, preferred):
        return [value for value in preferred if value in values] + sorted(
            value for value in values if value not in preferred
        )

    for category in ordered(categories, category_order):
        yield config.category_labels.get(category, category.replace("_", " ").title()), [
            (
                config.subcategory_labels.get(
                    subcategory, subcategory.replace("_", " ").title()
                ),
                categories[category][subcategory],
            )
            for subcategory in ordered(categories[category], subcategory_order)
        ]


def format_digest(
    entries: Iterable[dict[str, Any]], config: BotConfig, *, now: datetime, limit: int = TEXT_LIMIT
) -> list[str]:
    if limit <= 0:
        raise ValueError("Nachrichtenlimit muss positiv sein")
    chunks: list[str] = []
    current = f"Zusammenfassung vom {localized_date(now, config.language)}"

    def append_block(block: str) -> None:
        nonlocal current
        candidate = f"{current}\n\n{block}" if current else block
        if len(candidate) <= limit:
            current = candidate
            return
        if current:
            chunks.append(current)
        if len(block) > limit:
            raise ValueError("Eine einzelne Kanalzeile überschreitet das Telegram-Limit")
        current = block

    for category_label, subgroups in _grouped(entries, config):
        append_block(f"{category_label}:")
        for subcategory_label, lines in subgroups:
            append_block(f"{subcategory_label}:\n" + "\n".join(lines))
    if current:
        chunks.append(current)
    return chunks


def format_footer(config: BotConfig, bot_url: str = "") -> str:
    blocks = []
    if config.public_result_url:
        label = config.channel_footer_text or "Veröffentlichte Meldungen:"
        blocks.append(f"{label} {config.public_result_url}")
    if bot_url:
        blocks.append(f"Neue Meldung einreichen: {bot_url}")
    return "\n\n".join(blocks)


def partition_media(entry_ids: Iterable[str], maximum: int = 10) -> list[list[str]]:
    remaining = list(entry_ids)
    chunks: list[list[str]] = []
    while len(remaining) > maximum:
        size = maximum - 1 if len(remaining) == maximum + 1 else maximum
        chunks.append(remaining[:size])
        remaining = remaining[size:]
    if remaining:
        chunks.append(remaining)
    return chunks
