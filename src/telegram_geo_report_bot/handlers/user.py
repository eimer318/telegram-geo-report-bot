"""User-facing report collection flow."""

from __future__ import annotations

import asyncio
import logging
import uuid

from telegram import InlineKeyboardButton
from telegram.error import BadRequest

from ..entries import count_reporter_entries_on_date, utc_now_iso
from ..geocoding import GeocodingError
from ..photos import PhotoError, sanitize_jpeg
from ..security import (
    SecurityStateError,
    assess_address_query,
    assess_description,
    check_and_record_rate_action,
    pseudonymize_user,
    record_security_event,
)
from .common import (
    edit_or_send,
    format_user_entry,
    get_services,
    main_keyboard,
    show_main_menu,
)


def _reporter(user_id: int, services) -> str:
    return pseudonymize_user(user_id, services.settings.encryption_key)


def _new_entry(result: dict, reporter_hash: str) -> dict:
    now = utc_now_iso()
    return {
        "id": uuid.uuid4().hex,
        "status": "pending",
        "created_at": now,
        "timestamp": now,
        "lat": float(result["lat"]),
        "lon": float(result["lon"]),
        "address": dict(result["address"]),
        "category": "",
        "subcategory": "",
        "amount": None,
        "description": "",
        "photo": None,
        "reporter_hash": reporter_hash,
        "validation": None,
        "review": {"reviewed": False, "reviewed_by": None, "reviewed_at": None},
    }


def _is_editing(context, field: str | None = None) -> bool:
    active = context.user_data.get("editing_field")
    return bool(active) if field is None else active == field


def _edit_keyboard(entry: dict) -> list[list[InlineKeyboardButton]]:
    photo_label = "📷 Foto ersetzen/entfernen" if entry.get("photo") else "📷 Foto hinzufügen"
    return [
        [InlineKeyboardButton("📍 Adresse", callback_data="entry:edit:address")],
        [InlineKeyboardButton("🏷️ Kategorie", callback_data="entry:edit:category")],
        [InlineKeyboardButton("🔢 Menge", callback_data="entry:edit:amount")],
        [InlineKeyboardButton("📝 Beschreibung", callback_data="entry:edit:description")],
        [InlineKeyboardButton(photo_label, callback_data="entry:edit:photo")],
        [InlineKeyboardButton("⬅️ Zurück zur Prüfung", callback_data="entry:review")],
    ]


def _progress(step: int, total: int = 5) -> str:
    return f"{'🟩' * step}{'⬜' * (total - step)} {step}/{total}"


def _back_or_cancel_button(
    context, target: str | None = None
) -> InlineKeyboardButton:
    if _is_editing(context):
        return InlineKeyboardButton(
            "⬅️ Zurück zur Prüfung", callback_data="entry:review"
        )
    if target:
        return InlineKeyboardButton(
            "⬅️ Zurück", callback_data=f"entry:back:{target}"
        )
    return InlineKeyboardButton("❌ Abbrechen", callback_data="entry:cancel")


def _draft_address_text(entry: dict) -> str:
    address = entry.get("address") if isinstance(entry.get("address"), dict) else {}
    locality = " ".join(
        value
        for value in (
            str(address.get("postcode") or "").strip(),
            str(address.get("city") or "").strip(),
        )
        if value
    )
    return ", ".join(
        value for value in (str(address.get("street") or "").strip(), locality) if value
    )


async def _flow_message(update, context, text: str, keyboard=None):
    """Keep one active bot prompt while a report is being entered."""
    query = update.callback_query
    if query is not None:
        message = await edit_or_send(update, text, keyboard)
    else:
        message = None
        message_id = context.user_data.get("flow_message_id")
        if message_id:
            try:
                message = await context.bot.edit_message_text(
                    chat_id=update.effective_chat.id,
                    message_id=message_id,
                    text=text,
                    reply_markup=_markup(keyboard) if keyboard else None,
                )
            except BadRequest:
                message = None
        if message is None:
            message = await update.effective_message.reply_text(
                text, reply_markup=_markup(keyboard) if keyboard else None
            )
    message_id = getattr(message, "message_id", None)
    if message_id:
        context.user_data["flow_message_id"] = message_id
    return message


async def _delete_consumed_user_message(update) -> None:
    """Remove a processed text input without ever interrupting the dialog."""
    if update.callback_query is not None:
        return
    message = update.effective_message
    delete = getattr(message, "delete", None)
    if not callable(delete):
        return
    try:
        await delete()
    except Exception:
        logging.debug("Verarbeitete Nutzereingabe konnte nicht entfernt werden.")


async def _rate_allowed(update, services) -> bool:
    user = update.effective_user
    limits = services.settings.config.rate_limits
    if (
        user is None
        or services.admins.contains(user.id)
        or not limits.enabled
    ):
        return True
    reporter = _reporter(user.id, services)
    try:
        result = check_and_record_rate_action(
            services.settings.paths.rate_limit_file,
            services.settings.cipher,
            reporter,
            minute_limit=limits.per_minute,
            hour_limit=limits.per_hour,
        )
    except SecurityStateError:
        await update.effective_message.reply_text(
            "⚠️ Die Sicherheitsprüfung ist vorübergehend nicht verfügbar."
        )
        return False
    if result.allowed:
        return True
    try:
        record_security_event(
            services.settings.paths.security_events_file,
            services.settings.cipher,
            reporter,
            "rate_limit_exceeded",
            ("rolling_rate_limit",),
        )
    except SecurityStateError:
        logging.warning("Ratenlimit aktiv; Sicherheitsereignis nicht speicherbar")
    await update.effective_message.reply_text(
        f"⏳ Zu viele Meldeschritte. Bitte warte etwa "
        f"{result.retry_after_seconds} Sekunden."
    )
    return False


async def start(update, context) -> None:
    await show_main_menu(update, context)


async def me(update, context) -> None:
    services = get_services(context)
    user_id = update.effective_user.id
    role = "Administrator" if services.admins.contains(user_id) else "Nutzer"
    await update.effective_message.reply_text(
        f"🪪 Telegram-ID: {user_id}\nRolle: {role}"
    )


async def handle_main_callback(update, context) -> None:
    query = update.callback_query
    await query.answer()
    services = get_services(context)
    action = query.data
    if action == "main:add":
        if not await _rate_allowed(update, services):
            return
        context.user_data.clear()
        await _show_address_question(update, context)
    elif action == "main:help":
        text = services.settings.config.help_text
        if services.settings.config.contact_text:
            text += f"\n\n{services.settings.config.contact_text}"
        await edit_or_send(
            update,
            text,
            [[InlineKeyboardButton("⬅️ Zurück", callback_data="main:back")]],
        )
    elif action == "main:privacy":
        await edit_or_send(
            update,
            "🔐 Datenverarbeitung\n\n"
            "Meldungen und Fotos werden lokal verschlüsselt gespeichert. "
            "Fotos werden vor dem Speichern von Metadaten bereinigt. "
            "Öffentliche Exporte enthalten nur freigegebene Meldungen und "
            "keine Nutzerkennung.",
            [[InlineKeyboardButton("⬅️ Zurück", callback_data="main:back")]],
        )
    elif action == "main:results":
        url = services.settings.config.public_result_url
        await edit_or_send(
            update,
            url or "ℹ️ Es ist kein öffentlicher Ergebnislink konfiguriert.",
            [[InlineKeyboardButton("⬅️ Zurück", callback_data="main:back")]],
        )
    elif action == "main:back":
        await show_main_menu(update, context)


async def handle_text(update, context) -> bool:
    """Handle user flow text. Return whether the update was consumed."""
    services = get_services(context)
    step = context.user_data.get("flow_step")
    if step == "address":
        try:
            if not await _rate_allowed(update, services):
                return True
            assessment = assess_address_query(update.effective_message.text)
            if assessment.too_long:
                await _flow_message(
                    update,
                    context,
                    "⚠️ Die Adressangabe ist zu lang.\n\n"
                    "📍 Bitte gib eine kürzere Adresse mit Ort ein.",
                    [[_back_or_cancel_button(context)]],
                )
                return True
            if assessment.suspicious:
                reporter = _reporter(update.effective_user.id, services)
                record_security_event(
                    services.settings.paths.security_events_file,
                    services.settings.cipher,
                    reporter,
                    "suspicious_address",
                    assessment.reason_codes,
                )
                await _flow_message(
                    update,
                    context,
                    "⚠️ Die Eingabe enthält nicht unterstützte Steuer- oder "
                    "Programmmuster.\n\n📍 Bitte gib nur die Adresse ein.",
                    [[_back_or_cancel_button(context)]],
                )
                return True
            try:
                results = await asyncio.to_thread(
                    services.geocoder.search, assessment.normalized
                )
            except GeocodingError as exc:
                await _flow_message(
                    update,
                    context,
                    f"⚠️ {exc}\n\n📍 Bitte versuche es noch einmal.",
                    [[_back_or_cancel_button(context)]],
                )
                return True
            if not results:
                await _flow_message(
                    update,
                    context,
                    "🔎 Keine passende Adresse gefunden. Bitte versuche eine "
                    "genauere Schreibweise.",
                    [[_back_or_cancel_button(context)]],
                )
                return True
            context.user_data["address_results"] = results
            keyboard = [
                [
                    InlineKeyboardButton(
                        f"📍 {result['label']}"[:60],
                        callback_data=f"addr:{index}",
                    )
                ]
                for index, result in enumerate(results)
            ]
            keyboard.append([_back_or_cancel_button(context, "address")])
            await _flow_message(
                update,
                context,
                "🔎 Bitte die passende Adresse auswählen:\n\n"
                "Adressdaten © OpenStreetMap-Mitwirkende\n"
                "https://www.openstreetmap.org/copyright",
                keyboard,
            )
            return True
        finally:
            await _delete_consumed_user_message(update)
    if step == "description":
        try:
            return await _handle_description_text(update, context, services)
        finally:
            await _delete_consumed_user_message(update)
    return False


async def _handle_description_text(update, context, services) -> bool:
    entry = context.user_data.get("entry")
    if not isinstance(entry, dict):
        await show_main_menu(update, context)
        return True
    assessment = assess_description(
        update.effective_message.text,
        max_length=services.settings.config.description_max_length,
    )
    if assessment.too_long:
        await _flow_message(
            update,
            context,
            "⚠️ Die Beschreibung darf höchstens "
            f"{services.settings.config.description_max_length} Zeichen haben.\n\n"
            "📝 Bitte sende eine kürzere Beschreibung.",
            [
                [
                    InlineKeyboardButton(
                        "⏭ Keine Zusatzinfos",
                        callback_data="description:none",
                    )
                ],
                [_back_or_cancel_button(context, "amount")],
            ],
        )
        return True
    entry["description"] = assessment.normalized or "Keine Zusatzinfos"
    if assessment.suspicious:
        now = utc_now_iso()
        entry["security"] = {
            "publication_blocked": True,
            "automatic_approval_blocked": True,
            "reason_codes": list(assessment.reason_codes),
            "detected_at": now,
            "cleared_at": None,
            "cleared_by": None,
        }
        reporter = _reporter(update.effective_user.id, services)
        record_security_event(
            services.settings.paths.security_events_file,
            services.settings.cipher,
            reporter,
            "suspicious_description",
            assessment.reason_codes,
            entry_id=entry["id"],
        )
    if _is_editing(context, "description"):
        await _finish_edit(update, context)
    else:
        context.user_data["flow_step"] = "photo_decision"
        await _show_photo_question(update, context)
    return True


def _markup(keyboard):
    from telegram import InlineKeyboardMarkup

    return InlineKeyboardMarkup(keyboard)


async def _show_address_question(update, context) -> None:
    context.user_data["flow_step"] = "address"
    await _flow_message(
        update,
        context,
        f"{_progress(1)}\n\n"
        "📍 Wo befindet sich der Eintrag?\n\n"
        "Gib Straße, Hausnummer, Postleitzahl und Ort ein.\n"
        "Beispiel: Musterstraße 12, 12345 Musterstadt",
        [[_back_or_cancel_button(context)]],
    )


async def _show_category_question(update, context, *, notice: str = "") -> None:
    services = get_services(context)
    keyboard = [
        [
            InlineKeyboardButton(
                f"🏷️ {category.label}",
                callback_data=f"cat:{category.id}",
            )
        ]
        for category in services.settings.config.categories
    ]
    keyboard.append([_back_or_cancel_button(context, "address")])
    prefix = f"{notice}\n\n" if notice else ""
    await _flow_message(
        update,
        context,
        f"{prefix}{_progress(2)}\n\n🏷️ Bitte eine Kategorie auswählen:",
        keyboard,
    )


async def _show_subcategory_question(update, context) -> None:
    services = get_services(context)
    entry = context.user_data.get("entry")
    category = (
        services.settings.config.category(str(entry.get("category")))
        if isinstance(entry, dict)
        else None
    )
    if category is None:
        await _show_category_question(update, context)
        return
    context.user_data["flow_step"] = "subcategory"
    keyboard = [
        [
            InlineKeyboardButton(
                f"🔹 {choice.label}", callback_data=f"sub:{choice.id}"
            )
        ]
        for choice in category.subcategories
    ]
    keyboard.append([_back_or_cancel_button(context, "category")])
    await _flow_message(
        update,
        context,
        f"{_progress(2)}\n\n"
        f"✅ Kategorie: {category.label}\n\n"
        "🔹 Bitte eine Unterkategorie auswählen:",
        keyboard,
    )


async def _show_amount_question(update, context) -> None:
    services = get_services(context)
    keyboard = [
        [InlineKeyboardButton(f"🔢 {value}", callback_data=f"amount:{index}")]
        for index, value in enumerate(services.settings.config.amount_options)
    ]
    keyboard.append([_back_or_cancel_button(context, "subcategory")])
    await _flow_message(
        update,
        context,
        f"{_progress(3)}\n\n🔢 Bitte die passende Menge auswählen:",
        keyboard,
    )


async def _show_description_question(update, context) -> None:
    services = get_services(context)
    context.user_data["flow_step"] = "description"
    await _flow_message(
        update,
        context,
        f"{_progress(4)}\n\n"
        "📝 Ergänze optional eine kurze Beschreibung "
        f"(maximal {services.settings.config.description_max_length} Zeichen):",
        [
            [
                InlineKeyboardButton(
                    "⏭ Keine Zusatzinfos",
                    callback_data="description:none",
                )
            ],
            [_back_or_cancel_button(context, "amount")],
        ],
    )


async def _show_photo_question(update, context) -> None:
    await _flow_message(
        update,
        context,
        f"{_progress(5)}\n\n📷 Möchtest du ein Foto hinzufügen?",
        [
            [
                InlineKeyboardButton("📷 Ja", callback_data="photo:yes"),
                InlineKeyboardButton("➡️ Nein", callback_data="photo:no"),
            ],
            [_back_or_cancel_button(context, "description")],
        ],
    )


async def _finish_edit(update, context) -> None:
    context.user_data.pop("editing_field", None)
    context.user_data["flow_step"] = "review"
    await _show_review(update, context)


async def handle_flow_callback(update, context) -> None:
    query = update.callback_query
    await query.answer()
    services = get_services(context)
    data = query.data
    if data == "entry:cancel":
        entry = context.user_data.get("entry")
        if isinstance(entry, dict) and entry.get("photo"):
            await asyncio.to_thread(services.photos.delete, entry.get("photo"))
        await show_main_menu(update, context)
        return
    if data.startswith("entry:back:"):
        target = data.rsplit(":", 1)[1]
        if target == "address":
            await _show_address_question(update, context)
        elif target == "category":
            context.user_data["flow_step"] = "category"
            await _show_category_question(update, context)
        elif target == "subcategory":
            await _show_subcategory_question(update, context)
        elif target == "amount":
            await _show_amount_question(update, context)
        elif target == "description":
            await _show_description_question(update, context)
        else:
            await show_main_menu(update, context)
        return
    if data == "entry:edit":
        entry = context.user_data.get("entry")
        if not isinstance(entry, dict):
            await show_main_menu(update, context)
            return
        await edit_or_send(
            update,
            "✏️ Meldung bearbeiten\n\nWas möchtest du ändern?",
            _edit_keyboard(entry),
        )
        return
    if data == "entry:edit:address":
        context.user_data["editing_field"] = "address"
        await _show_address_question(update, context)
        return
    if data == "entry:edit:category":
        context.user_data["editing_field"] = "category"
        await _show_category_question(update, context)
        return
    if data == "entry:edit:amount":
        context.user_data["editing_field"] = "amount"
        context.user_data["flow_step"] = "amount"
        await _show_amount_question(update, context)
        return
    if data == "entry:edit:description":
        context.user_data["editing_field"] = "description"
        await _show_description_question(update, context)
        return
    if data == "entry:edit:photo":
        entry = context.user_data.get("entry")
        if not isinstance(entry, dict):
            await show_main_menu(update, context)
            return
        context.user_data["editing_field"] = "photo"
        keyboard = [
            [
                InlineKeyboardButton(
                    "📷 Foto ersetzen" if entry.get("photo") else "📷 Foto hinzufügen",
                    callback_data="photo:yes",
                )
            ]
        ]
        if entry.get("photo"):
            keyboard.append(
                [InlineKeyboardButton("🗑 Foto entfernen", callback_data="photo:remove")]
            )
        keyboard.append(
            [InlineKeyboardButton("⬅️ Zurück zur Prüfung", callback_data="entry:review")]
        )
        await edit_or_send(update, "📷 Foto bearbeiten", keyboard)
        return
    if data == "entry:review":
        context.user_data.pop("editing_field", None)
        context.user_data["flow_step"] = "review"
        await _show_review(update, context)
        return
    if data.startswith("addr:"):
        try:
            result = context.user_data["address_results"][
                int(data.split(":", 1)[1])
            ]
        except (KeyError, IndexError, ValueError, TypeError):
            await edit_or_send(
                update,
                "ℹ️ Die Adressauswahl ist abgelaufen. Bitte beginne erneut.",
            )
            return
        entry = context.user_data.get("entry")
        if _is_editing(context, "address") and isinstance(entry, dict):
            entry["lat"] = float(result["lat"])
            entry["lon"] = float(result["lon"])
            entry["address"] = dict(result["address"])
            context.user_data.pop("address_results", None)
            await _finish_edit(update, context)
            return
        context.user_data["entry"] = _new_entry(
            result, _reporter(update.effective_user.id, services)
        )
        context.user_data.pop("address_results", None)
        context.user_data["flow_step"] = "category"
        address_text = _draft_address_text(context.user_data["entry"])
        await _show_category_question(
            update,
            context,
            notice=f"✅ Adresse übernommen: {address_text}",
        )
        return
    if data.startswith("cat:"):
        entry = context.user_data.get("entry")
        category = services.settings.config.category(data.split(":", 1)[1])
        if not isinstance(entry, dict) or category is None:
            await edit_or_send(update, "ℹ️ Die Auswahl ist abgelaufen.")
            return
        entry["category"] = category.id
        entry["subcategory"] = ""
        await _show_subcategory_question(update, context)
        return
    if data.startswith("sub:"):
        entry = context.user_data.get("entry")
        subcategory = data.split(":", 1)[1]
        category = (
            services.settings.config.category(str(entry.get("category")))
            if isinstance(entry, dict)
            else None
        )
        allowed = (
            {item.id for item in category.subcategories}
            if category is not None
            else set()
        )
        if not isinstance(entry, dict) or subcategory not in allowed:
            await edit_or_send(update, "ℹ️ Die Auswahl ist abgelaufen.")
            return
        entry["subcategory"] = subcategory
        if _is_editing(context, "category"):
            await _finish_edit(update, context)
        else:
            context.user_data["flow_step"] = "amount"
            await _show_amount_question(update, context)
        return
    if data.startswith("amount:"):
        entry = context.user_data.get("entry")
        try:
            amount = services.settings.config.amount_options[
                int(data.split(":", 1)[1])
            ]
        except (IndexError, ValueError):
            await edit_or_send(update, "ℹ️ Die Auswahl ist abgelaufen.")
            return
        if not isinstance(entry, dict):
            await edit_or_send(update, "ℹ️ Die Auswahl ist abgelaufen.")
            return
        entry["amount"] = amount
        if _is_editing(context, "amount"):
            await _finish_edit(update, context)
        else:
            await _show_description_question(update, context)
        return
    if data == "description:none":
        entry = context.user_data.get("entry")
        if not isinstance(entry, dict):
            await show_main_menu(update, context)
            return
        entry["description"] = "Keine Zusatzinfos"
        if _is_editing(context, "description"):
            await _finish_edit(update, context)
        else:
            context.user_data["flow_step"] = "photo_decision"
            await _show_photo_question(update, context)
        return
    if data == "photo:yes":
        entry = context.user_data.get("entry")
        if not isinstance(entry, dict):
            await show_main_menu(update, context)
            return
        context.user_data["flow_step"] = "photo"
        await edit_or_send(
            update,
            f"{_progress(5)}\n\n"
            "📷 Bitte sende jetzt ein Foto. Metadaten werden beim Speichern entfernt.",
            [[_back_or_cancel_button(context, "description")]],
        )
        return
    if data == "photo:no":
        context.user_data["flow_step"] = "review"
        await _show_review(update, context)
        return
    if data == "photo:remove":
        entry = context.user_data.get("entry")
        if not isinstance(entry, dict):
            await show_main_menu(update, context)
            return
        photo_name = entry.get("photo")
        if photo_name:
            await asyncio.to_thread(services.photos.delete, str(photo_name))
        entry["photo"] = None
        await _finish_edit(update, context)
        return
    if data == "entry:save":
        await _save_entry(update, context)


async def handle_photo(update, context) -> None:
    services = get_services(context)
    if context.user_data.get("flow_step") != "photo":
        return
    try:
        await _handle_photo_input(update, context, services)
    finally:
        await _delete_consumed_user_message(update)


async def _handle_photo_input(update, context, services) -> None:
    entry = context.user_data.get("entry")
    if not isinstance(entry, dict):
        await show_main_menu(update, context)
        return
    telegram_file = await update.effective_message.photo[-1].get_file()
    raw = bytes(await telegram_file.download_as_bytearray())
    try:
        clean = await asyncio.to_thread(
            sanitize_jpeg,
            raw,
            max_bytes=services.settings.config.photo_max_bytes,
        )
        name = f"photo_{uuid.uuid4().hex}.jpg"
        await asyncio.to_thread(services.photos.save, name, clean)
    except PhotoError as exc:
        await _flow_message(
            update,
            context,
            f"⚠️ {exc}\n\n📷 Bitte sende ein anderes Foto oder fahre ohne Foto fort.",
            [
                [InlineKeyboardButton("➡️ Ohne Foto", callback_data="photo:no")],
                [_back_or_cancel_button(context, "description")],
            ],
        )
        return
    previous = entry.get("photo")
    entry["photo"] = name
    if previous and previous != name:
        await asyncio.to_thread(services.photos.delete, str(previous))
    context.user_data["flow_step"] = "review"
    context.user_data.pop("editing_field", None)
    await _show_review(update, context)


async def _show_review(update, context) -> None:
    services = get_services(context)
    entry = context.user_data.get("entry")
    if not isinstance(entry, dict):
        await show_main_menu(update, context)
        return
    await _flow_message(
        update,
        context,
        f"{_progress(5)}\n\n"
        + format_user_entry(entry, services, title="✅ Eintrag prüfen"),
        [
            [InlineKeyboardButton("✅ Absenden", callback_data="entry:save")],
            [InlineKeyboardButton("✏️ Meldung bearbeiten", callback_data="entry:edit")],
            [InlineKeyboardButton("❌ Abbrechen", callback_data="entry:cancel")],
        ],
    )


async def _save_entry(update, context) -> None:
    services = get_services(context)
    entry = context.user_data.get("entry")
    user = update.effective_user
    if not isinstance(entry, dict) or user is None:
        await show_main_menu(update, context)
        return
    existing = services.entries.load()
    daily_limit = services.settings.config.daily_user_limit
    if daily_limit > 0 and not services.admins.contains(user.id):
        count = count_reporter_entries_on_date(existing, entry["reporter_hash"])
        if count >= daily_limit:
            photo_name = entry.get("photo")
            if photo_name:
                await asyncio.to_thread(services.photos.delete, str(photo_name))
            context.user_data.clear()
            await edit_or_send(update, "⚠️ Das tägliche Meldelimit ist erreicht.")
            return
    await asyncio.to_thread(services.entries.append, entry)
    approved_ids = await asyncio.to_thread(services.entries.auto_validate)
    text = "✅ Meldung gespeichert."
    if entry["id"] in approved_ids:
        text += " Sie wurde automatisch freigegeben."
    elif entry.get("security", {}).get("publication_blocked"):
        text += " Sie bleibt wegen einer Sicherheitsprüfung zur Admin-Prüfung offen."
    else:
        text += " Sie wartet auf die Admin-Prüfung."
    text += "\n\n👇 Was möchtest du als Nächstes tun?"
    keyboard = main_keyboard(
        services.admins.contains(user.id),
        has_result_link=bool(services.settings.config.public_result_url),
    )
    await _flow_message(update, context, text, keyboard)
    context.user_data.clear()
