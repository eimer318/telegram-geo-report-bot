"""Shared Telegram UI helpers."""

from __future__ import annotations

from datetime import datetime
from io import BytesIO
from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import BadRequest

from ..services import Services


def get_services(context) -> Services:
    services = context.application.bot_data.get("services")
    if not isinstance(services, Services):
        raise RuntimeError("Anwendungsdienste wurden nicht initialisiert")
    return services


async def edit_or_send(update, text: str, keyboard=None) -> Any:
    markup = InlineKeyboardMarkup(keyboard) if keyboard else None
    query = update.callback_query
    if query is not None:
        try:
            return await query.edit_message_text(text, reply_markup=markup)
        except BadRequest:
            pass
    return await update.effective_message.reply_text(text, reply_markup=markup)


def main_keyboard(is_admin: bool, *, has_result_link: bool) -> list[list[InlineKeyboardButton]]:
    keyboard = [[InlineKeyboardButton("➕ Neuen Eintrag hinzufügen", callback_data="main:add")]]
    if is_admin:
        keyboard.append([InlineKeyboardButton("🔐 Adminbereich", callback_data="main:admin")])
    if has_result_link:
        keyboard.append([InlineKeyboardButton("🗺️ Veröffentlichte Meldungen", callback_data="main:results")])
    keyboard.extend(
        [
            [InlineKeyboardButton("🔐 Datenverarbeitung", callback_data="main:privacy")],
            [InlineKeyboardButton("ℹ️ Hilfe & Kontakt", callback_data="main:help")],
        ]
    )
    return keyboard


async def clear_admin_card(update, context) -> None:
    message_id = context.user_data.pop("admin_card_message_id", None)
    if not message_id:
        return
    try:
        await context.bot.delete_message(
            chat_id=update.effective_chat.id, message_id=message_id
        )
    except BadRequest:
        pass


async def replace_admin_card(update, context, text: str, keyboard=None, *, photo: bytes | None = None) -> Any:
    """Send one complete admin card, then remove the previous card."""
    markup = InlineKeyboardMarkup(keyboard) if keyboard else None
    chat_id = update.effective_chat.id
    previous_ids = set()
    tracked = context.user_data.get("admin_card_message_id")
    if tracked:
        previous_ids.add(int(tracked))
    query = update.callback_query
    if query is not None and getattr(query, "message", None) is not None:
        previous_ids.add(int(query.message.message_id))
    if photo is not None:
        message = await context.bot.send_photo(
            chat_id=chat_id, photo=BytesIO(photo), caption=text, reply_markup=markup
        )
    else:
        message = await context.bot.send_message(
            chat_id=chat_id, text=text, reply_markup=markup
        )
    context.user_data["admin_card_message_id"] = message.message_id
    for message_id in previous_ids:
        if message_id == message.message_id:
            continue
        try:
            await context.bot.delete_message(chat_id=chat_id, message_id=message_id)
        except BadRequest:
            pass
    return message


async def show_main_menu(update, context) -> None:
    services = get_services(context)
    user = update.effective_user
    is_admin = bool(user and services.admins.contains(user.id))
    text = services.settings.config.welcome_text
    keyboard = main_keyboard(
        is_admin,
        has_result_link=bool(services.settings.config.public_result_url),
    )
    if context.user_data.get("admin_card_message_id"):
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=text,
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
        await clear_admin_card(update, context)
    else:
        await edit_or_send(update, text, keyboard)
    context.user_data.clear()


def _display_value(value: Any, *, limit: int = 250) -> str:
    text = " ".join(str(value or "–").split())
    return text if len(text) <= limit else f"{text[: limit - 1]}…"


def _address_text(entry: dict[str, Any]) -> str:
    address = entry.get("address") if isinstance(entry.get("address"), dict) else {}
    locality = " ".join(
        part for part in (
            str(address.get("postcode") or "").strip(),
            str(address.get("city") or "").strip(),
        ) if part
    )
    value = str(address.get("street") or "–")
    if locality:
        value += f", {locality}"
    return _display_value(value)


def _labels(entry: dict[str, Any], services: Services) -> tuple[str, str]:
    category = services.settings.config.category_labels.get(
        str(entry.get("category")), str(entry.get("category") or "–")
    )
    subcategory = services.settings.config.subcategory_labels.get(
        str(entry.get("subcategory")), str(entry.get("subcategory") or "–")
    )
    return category, subcategory


def format_user_entry(entry: dict[str, Any], services: Services, *, title: str = "Meldung") -> str:
    category, subcategory = _labels(entry, services)
    photo_text = "Foto vorhanden" if entry.get("photo") else "Kein Foto"
    return (
        f"{title}\n\n"
        f"📍 Adresse: {_address_text(entry)}\n"
        f"🏷️ Kategorie: {category}\n"
        f"🔹 Unterkategorie: {subcategory}\n"
        f"🔢 Menge: {entry.get('amount') if entry.get('amount') not in (None, '', 'Null') else '–'}\n"
        f"📝 Beschreibung: {_display_value(entry.get('description') or 'Keine Zusatzinfos', limit=120)}\n"
        f"📷 Foto: {photo_text}"
    )


def _display_timestamp(value: Any) -> str:
    if not value:
        return "–"
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return _display_value(value, limit=40)
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone()
    return parsed.strftime("%d-%m-%Y %H:%M:%S")


def _validation_text(entry: dict[str, Any]) -> str:
    validation = entry.get("validation")
    if not isinstance(validation, dict):
        return "–"
    label = str(validation.get("type") or "–")
    decision = validation.get("decision")
    if decision:
        label += f" ({decision})"
    reference = validation.get("reference_entry_id") or validation.get("reference")
    if reference:
        label += f", Referenz {_display_value(reference, limit=40)}"
    distance = validation.get("distance_m")
    if distance is not None:
        label += f", {distance} m"
    return _display_value(label, limit=160)


def format_admin_entry(
    entry: dict[str, Any], services: Services, *, title: str = "Meldung", notice: str = ""
) -> str:
    category, subcategory = _labels(entry, services)
    amount = entry.get("amount")
    lines = [
        title, "",
        f"ID: {_display_value(entry.get('id'), limit=64)}",
        f"Adresse: {_address_text(entry)}",
        f"Koordinaten: {entry.get('lat', '–')}, {entry.get('lon', '–')}",
        f"Kategorie: {category}",
        f"Unterkategorie: {subcategory}",
        f"Menge: {amount if amount not in (None, '', 'Null') else '–'}",
        f"Beschreibung: {_display_value(entry.get('description') or 'Keine Zusatzinfos', limit=120)}",
        f"Zeitstempel: {_display_timestamp(entry.get('timestamp') or entry.get('created_at'))}",
        f"Status: {entry.get('status', 'pending')}",
        f"Validierung: {_validation_text(entry)}",
    ]
    if notice:
        lines.extend(("", _display_value(notice, limit=180)))
    return "\n".join(lines)
