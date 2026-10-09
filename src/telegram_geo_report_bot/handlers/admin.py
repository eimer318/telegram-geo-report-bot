"""Administrator review, status management, and manual channel publishing."""

from __future__ import annotations

import asyncio
from pathlib import Path

from telegram import InlineKeyboardButton

from ..admins import AdminStoreError
from ..channel_publish import ChannelError, TelegramChannelClient, preview, publish
from ..entries import EntrySecurityError
from ..exports import publication_blocked
from .common import format_admin_entry, get_services, replace_admin_card


PAGE_SIZE = 50


def _authorized(update, services) -> bool:
    user = update.effective_user
    return bool(user and services.admins.contains(user.id))


def _signature(path: Path):
    try:
        stat = path.stat()
        return stat.st_size, stat.st_mtime_ns
    except OSError:
        return None


def _clear_admin_flow(context) -> None:
    for key in tuple(context.user_data):
        if key != "admin_card_message_id":
            context.user_data.pop(key, None)


async def _show_card(update, context, text: str, keyboard, *, entry=None) -> None:
    services = get_services(context)
    photo = None
    if isinstance(entry, dict) and entry.get("photo"):
        try:
            photo = await asyncio.to_thread(services.photos.load, entry.get("photo"))
        except Exception:
            photo = None
        if photo is None:
            text = f"{text}\n\n📷 Foto nicht verfügbar."
    await replace_admin_card(update, context, text, keyboard, photo=photo)


async def admin_command(update, context) -> None:
    services = get_services(context)
    if not _authorized(update, services):
        await update.effective_message.reply_text("❌ Kein Admin-Zugriff.")
        return
    await show_dashboard(update, context)


async def show_dashboard(update, context) -> None:
    services = get_services(context)
    if not _authorized(update, services):
        await replace_admin_card(update, context, "❌ Kein Admin-Zugriff.")
        return
    _clear_admin_flow(context)
    entries = await asyncio.to_thread(services.entries.load)
    counts = {
        status: sum(
            entry.get("status", "pending") == status for entry in entries
        )
        for status in ("pending", "approved", "rejected")
    }
    lines = [
        "🔐 Adminbereich",
        "",
        f"Offen: {counts['pending']}",
        f"Freigegeben: {counts['approved']}",
        f"Abgelehnt: {counts['rejected']}",
    ]
    channel_text = None
    if services.settings.channel_id:
        try:
            digest = preview(
                services.entries,
                services.settings.paths.channel_state_file,
                services.settings.cipher,
                services.settings.channel_id,
            )
            channel_text = (
                f"Kanal: {digest.eligible} bereit; "
                f"heute abgeschlossen={'Ja' if digest.completed else 'Nein'}"
            )
        except ChannelError:
            channel_text = "Kanal: Status nicht verfügbar"
    if channel_text:
        lines.append(channel_text)

    keyboard = [
        [InlineKeyboardButton("✅ Neue Meldungen prüfen", callback_data="admin:review")],
        [InlineKeyboardButton("🗑️ Abgelehnte Meldungen löschen", callback_data="admin:delete")],
    ]
    if services.settings.channel_id:
        keyboard.append(
            [InlineKeyboardButton("📣 Kanal-Post veröffentlichen", callback_data="admin:channel")]
        )
    keyboard.append(
        [
            InlineKeyboardButton("Alle Meldungen", callback_data="admin:all"),
            InlineKeyboardButton("Admins verwalten", callback_data="admin:manage"),
        ]
    )
    keyboard.append(
        [InlineKeyboardButton("⬅️ Hauptmenü", callback_data="main:back")]
    )
    await _show_card(update, context, "\n".join(lines), keyboard)


def _start_review(context, services):
    entries = services.entries.pending()
    context.user_data["review_entries"] = entries
    context.user_data["review_skipped"] = []
    context.user_data["review_processed"] = 0
    context.user_data["review_total"] = len(entries)
    return entries


def _remaining_review_entries(context):
    entries = context.user_data.get("review_entries")
    if not isinstance(entries, list):
        return []
    skipped = set(context.user_data.get("review_skipped") or [])
    return [entry for entry in entries if entry.get("id") not in skipped]


def _remove_review_entry(context, entry_id: str) -> None:
    entries = context.user_data.get("review_entries")
    if isinstance(entries, list):
        context.user_data["review_entries"] = [
            entry for entry in entries if str(entry.get("id")) != entry_id
        ]


def _update_cached_entry(context, updated: dict) -> None:
    entry_id = str(updated.get("id"))
    for key in ("review_entries", "all_entries"):
        entries = context.user_data.get(key)
        if not isinstance(entries, list):
            continue
        for index, entry in enumerate(entries):
            if str(entry.get("id")) == entry_id:
                entries[index] = updated


async def _show_review(update, context, *, notice: str = "") -> None:
    services = get_services(context)
    entries = _remaining_review_entries(context)
    if not entries:
        await _show_card(
            update,
            context,
            "✅ Keine weiteren offenen Meldungen.",
            [[
                InlineKeyboardButton("🗂 Alle Meldungen", callback_data="admin:all"),
                InlineKeyboardButton("⬅️ Adminbereich", callback_data="admin:dashboard"),
            ]],
        )
        return

    entry = entries[0]
    total = max(int(context.user_data.get("review_total", len(entries))), 1)
    position = min(int(context.user_data.get("review_processed", 0)) + 1, total)
    blocked = publication_blocked(entry)
    if blocked:
        neutral_notice = "Vor der Freigabe muss die Beschreibung bearbeitet oder entfernt werden."
        notice = f"{notice}\n{neutral_notice}".strip()
        keyboard = [
            [
                InlineKeyboardButton(
                    "✏️ Beschreibung bearbeiten",
                    callback_data=f"admin:description:{entry['id']}",
                ),
                InlineKeyboardButton(
                    "🗑 Beschreibung entfernen",
                    callback_data=f"admin:description-clear:{entry['id']}",
                ),
            ],
            [
                InlineKeyboardButton("❌ Ablehnen", callback_data=f"review:reject:{entry['id']}"),
                InlineKeyboardButton("⏭ Überspringen", callback_data=f"review:skip:{entry['id']}"),
            ],
            [InlineKeyboardButton("⬅️ Adminbereich", callback_data="admin:dashboard")],
        ]
    else:
        keyboard = [
            [
                InlineKeyboardButton("✅ Freigeben", callback_data=f"review:approve:{entry['id']}"),
                InlineKeyboardButton("❌ Ablehnen", callback_data=f"review:reject:{entry['id']}"),
            ],
            [
                InlineKeyboardButton("⏭ Überspringen", callback_data=f"review:skip:{entry['id']}"),
                InlineKeyboardButton("⬅️ Adminbereich", callback_data="admin:dashboard"),
            ],
        ]
    text = format_admin_entry(
        entry,
        services,
        title=f"📋 Review ({position}/{total})",
        notice=notice,
    )
    await _show_card(update, context, text, keyboard, entry=entry)


def _load_all_cache(context, services, *, refresh=False, preserve_count=None):
    signature = _signature(services.settings.paths.entries_file)
    if refresh or context.user_data.get("all_signature") != signature:
        limit = max(int(preserve_count or PAGE_SIZE), PAGE_SIZE)
        entries, has_more = services.entries.page(limit=limit)
        context.user_data.update(
            {
                "all_entries": entries,
                "all_has_more": has_more,
                "all_signature": signature,
                "all_index": 0,
            }
        )
    return context.user_data.get("all_entries", [])


async def _show_all(
    update,
    context,
    *,
    refresh=False,
    focus_entry_id: str | None = None,
    notice: str = "",
) -> None:
    services = get_services(context)
    cached = context.user_data.get("all_entries")
    preserve_count = len(cached) if isinstance(cached, list) else None
    entries = _load_all_cache(
        context, services, refresh=refresh, preserve_count=preserve_count
    )
    if not entries:
        await _show_card(
            update,
            context,
            "Keine Meldungen vorhanden.",
            [[InlineKeyboardButton("⬅️ Adminbereich", callback_data="admin:dashboard")]],
        )
        return
    if focus_entry_id:
        focused = next(
            (
                index
                for index, entry in enumerate(entries)
                if str(entry.get("id")) == focus_entry_id
            ),
            None,
        )
        index = focused if focused is not None else 0
    else:
        index = min(
            max(int(context.user_data.get("all_index", 0)), 0), len(entries) - 1
        )
    context.user_data["all_index"] = index
    context.user_data["admin_view_mode"] = "all"
    entry = entries[index]
    blocked = publication_blocked(entry)
    keyboard = []
    if blocked:
        keyboard.append(
            [
                InlineKeyboardButton(
                    "✏️ Beschreibung bearbeiten",
                    callback_data=f"admin:description:{entry['id']}",
                ),
                InlineKeyboardButton(
                    "🗑 Beschreibung entfernen",
                    callback_data=f"admin:description-clear:{entry['id']}",
                ),
            ]
        )
        keyboard.append(
            [
                InlineKeyboardButton("⏳ Pending", callback_data=f"status:pending:{entry['id']}"),
                InlineKeyboardButton("❌ Ablehnen", callback_data=f"status:rejected:{entry['id']}"),
            ]
        )
        notice = (
            f"{notice}\nVor der Freigabe muss die Beschreibung bearbeitet oder entfernt werden."
        ).strip()
    else:
        keyboard.append(
            [
                InlineKeyboardButton("⏳ Pending", callback_data=f"status:pending:{entry['id']}"),
                InlineKeyboardButton("✅ Freigeben", callback_data=f"status:approved:{entry['id']}"),
                InlineKeyboardButton("❌ Ablehnen", callback_data=f"status:rejected:{entry['id']}"),
            ]
        )
    navigation = []
    if index > 0:
        navigation.append(InlineKeyboardButton("⬅️ Vorherige", callback_data="all:prev"))
    if index + 1 < len(entries):
        navigation.append(InlineKeyboardButton("Nächste ➡️", callback_data="all:next"))
    if navigation:
        keyboard.append(navigation)
    if context.user_data.get("all_has_more") and index == len(entries) - 1:
        keyboard.append(
            [InlineKeyboardButton("📚 50 ältere Meldungen laden", callback_data="all:more")]
        )
    keyboard.append(
        [InlineKeyboardButton("⬅️ Adminbereich", callback_data="admin:dashboard")]
    )
    total = f"{len(entries)}+" if context.user_data.get("all_has_more") else len(entries)
    text = format_admin_entry(
        entry,
        services,
        title=f"🗂 Alle Meldungen ({index + 1}/{total})",
        notice=notice,
    )
    await _show_card(update, context, text, keyboard, entry=entry)


async def _show_admin_management(update, context, *, notice: str = "") -> None:
    services = get_services(context)
    count = len(await asyncio.to_thread(services.admins.load))
    text = f"👥 Admins verwalten\n\nRegistrierte Admins: {count}"
    if notice:
        text += f"\n\n{notice}"
    await _show_card(
        update,
        context,
        text,
        [
            [InlineKeyboardButton("➕ Admin hinzufügen", callback_data="admin:add")],
            [InlineKeyboardButton("➖ Admin entfernen", callback_data="admin:remove")],
            [InlineKeyboardButton("⬅️ Adminbereich", callback_data="admin:dashboard")],
        ],
    )


async def _return_after_description(update, context, entry_id: str, *, notice: str = ""):
    services = get_services(context)
    updated = next(
        (
            entry
            for entry in await asyncio.to_thread(services.entries.load)
            if str(entry.get("id")) == entry_id
        ),
        None,
    )
    if updated is not None:
        _update_cached_entry(context, updated)
    mode = context.user_data.pop("admin_description_return", "review")
    context.user_data.pop("admin_description_entry_id", None)
    if mode == "all":
        await _show_all(update, context, focus_entry_id=entry_id, notice=notice)
    else:
        await _show_review(update, context, notice=notice)


async def handle_admin_text(update, context) -> bool:
    services = get_services(context)
    if not _authorized(update, services):
        return False
    entry_id = context.user_data.get("admin_description_entry_id")
    if entry_id:
        try:
            changed = await asyncio.to_thread(
                services.entries.replace_blocked_description,
                entry_id,
                update.effective_message.text,
                str(update.effective_user.id),
                max_length=services.settings.config.description_max_length,
            )
        except EntrySecurityError:
            await _show_card(
                update,
                context,
                "Die Beschreibung konnte nicht gespeichert werden. Bitte verwende eine andere Formulierung.",
                [[InlineKeyboardButton("⬅️ Zurück", callback_data="admin:description-cancel")]],
            )
            return True
        notice = (
            "Beschreibung gespeichert. Die Meldung kann jetzt geprüft werden."
            if changed
            else "Die Meldung wurde nicht gefunden."
        )
        await _return_after_description(update, context, entry_id, notice=notice)
        return True

    action = context.user_data.get("admin_action")
    if action not in {"add", "remove"}:
        return False
    raw = str(update.effective_message.text or "").strip()
    if not raw.isdigit() or int(raw) <= 0:
        await _show_card(
            update,
            context,
            "Bitte sende eine positive numerische Telegram-ID. Die eigene ID zeigt /me.",
            [[InlineKeyboardButton("⬅️ Abbrechen", callback_data="admin:manage")]],
        )
        return True
    user_id = int(raw)
    try:
        if action == "add":
            changed = await asyncio.to_thread(services.admins.add, user_id)
            notice = "Admin hinzugefügt." if changed else "Diese ID ist bereits als Admin registriert."
        else:
            changed = await asyncio.to_thread(services.admins.remove_preserving_one, user_id)
            notice = "Admin entfernt." if changed else "Diese ID ist nicht als Admin registriert."
    except AdminStoreError as exc:
        notice = str(exc)
    context.user_data.pop("admin_action", None)
    await _show_admin_management(update, context, notice=notice)
    return True


async def handle_admin_callback(update, context) -> None:
    query = update.callback_query
    await query.answer()
    services = get_services(context)
    if not _authorized(update, services):
        await replace_admin_card(update, context, "❌ Kein Admin-Zugriff.")
        return
    data = query.data
    if data in {"main:admin", "admin:dashboard"}:
        await show_dashboard(update, context)
    elif data == "admin:review":
        _start_review(context, services)
        context.user_data["admin_view_mode"] = "review"
        await _show_review(update, context)
    elif data.startswith("review:approve:") or data.startswith("review:reject:"):
        action, entry_id = data.split(":")[1:]
        status = "approved" if action == "approve" else "rejected"
        try:
            changed = await asyncio.to_thread(
                services.entries.set_status,
                entry_id,
                status,
                str(update.effective_user.id),
            )
        except EntrySecurityError:
            await _show_review(
                update,
                context,
                notice="Vor der Freigabe muss die Beschreibung bearbeitet oder entfernt werden.",
            )
            return
        if changed:
            context.user_data["review_processed"] = int(
                context.user_data.get("review_processed", 0)
            ) + 1
            _remove_review_entry(context, entry_id)
        await _show_review(update, context)
    elif data.startswith("review:skip:"):
        entry_id = data.rsplit(":", 1)[1]
        context.user_data.setdefault("review_skipped", []).append(entry_id)
        context.user_data["review_processed"] = int(
            context.user_data.get("review_processed", 0)
        ) + 1
        await _show_review(update, context)
    elif data.startswith("admin:description-clear:"):
        entry_id = data.rsplit(":", 1)[1]
        try:
            changed = await asyncio.to_thread(
                services.entries.replace_blocked_description,
                entry_id,
                "Keine Zusatzinfos",
                str(update.effective_user.id),
                max_length=services.settings.config.description_max_length,
            )
        except EntrySecurityError:
            changed = False
        notice = (
            "Beschreibung entfernt. Die Meldung kann jetzt geprüft werden."
            if changed
            else "Die Beschreibung konnte nicht geändert werden."
        )
        context.user_data["admin_description_return"] = context.user_data.get(
            "admin_view_mode", "review"
        )
        await _return_after_description(update, context, entry_id, notice=notice)
    elif data.startswith("admin:description:"):
        entry_id = data.rsplit(":", 1)[1]
        context.user_data["admin_description_entry_id"] = entry_id
        context.user_data["admin_description_return"] = context.user_data.get(
            "admin_view_mode", "review"
        )
        await _show_card(
            update,
            context,
            "✏️ Beschreibung bearbeiten\n\nSende eine neue Beschreibung oder nutze ‚Beschreibung entfernen‘.",
            [
                [InlineKeyboardButton("🗑 Beschreibung entfernen", callback_data=f"admin:description-clear:{entry_id}")],
                [InlineKeyboardButton("⬅️ Zurück", callback_data="admin:description-cancel")],
            ],
        )
    elif data == "admin:description-cancel":
        entry_id = str(context.user_data.pop("admin_description_entry_id", ""))
        mode = context.user_data.pop("admin_description_return", "review")
        if mode == "all":
            await _show_all(update, context, focus_entry_id=entry_id or None)
        else:
            await _show_review(update, context)
    elif data == "admin:all":
        await _show_all(update, context, refresh=True)
    elif data == "all:prev":
        context.user_data["all_index"] = max(
            0, int(context.user_data.get("all_index", 0)) - 1
        )
        await _show_all(update, context)
    elif data == "all:next":
        entries = _load_all_cache(context, services)
        context.user_data["all_index"] = min(
            len(entries) - 1, int(context.user_data.get("all_index", 0)) + 1
        )
        await _show_all(update, context)
    elif data == "all:more":
        cached = _load_all_cache(context, services)
        older, has_more = await asyncio.to_thread(
            services.entries.page, offset=len(cached), limit=PAGE_SIZE
        )
        context.user_data["all_entries"] = [*cached, *older]
        context.user_data["all_has_more"] = has_more
        context.user_data["all_signature"] = _signature(
            services.settings.paths.entries_file
        )
        context.user_data["all_index"] = len(cached)
        await _show_all(update, context)
    elif data.startswith("status:"):
        _, status, entry_id = data.split(":", 2)
        try:
            await asyncio.to_thread(
                services.entries.set_status,
                entry_id,
                status,
                str(update.effective_user.id),
            )
        except EntrySecurityError:
            await _show_all(
                update,
                context,
                focus_entry_id=entry_id,
                notice="Vor der Freigabe muss die Beschreibung bearbeitet oder entfernt werden.",
            )
            return
        await _show_all(update, context, refresh=True, focus_entry_id=entry_id)
    elif data == "admin:manage":
        context.user_data.pop("admin_action", None)
        await _show_admin_management(update, context)
    elif data in {"admin:add", "admin:remove"}:
        context.user_data["admin_action"] = data.rsplit(":", 1)[1]
        action = "hinzufügen" if data == "admin:add" else "entfernen"
        await _show_card(
            update,
            context,
            f"Sende die numerische Telegram-ID des Admins, den du {action} möchtest.\n\nDie eigene ID zeigt /me.",
            [[InlineKeyboardButton("⬅️ Abbrechen", callback_data="admin:manage")]],
        )
    elif data == "admin:delete":
        rejected = [
            entry
            for entry in await asyncio.to_thread(services.entries.load)
            if entry.get("status") == "rejected"
        ]
        lines = []
        for entry in rejected[:20]:
            address = entry.get("address") if isinstance(entry.get("address"), dict) else {}
            lines.append(f"• {address.get('street') or 'Ohne Adresse'}")
        text = f"{len(rejected)} abgelehnte Meldungen endgültig löschen?"
        if lines:
            text += "\n\n" + "\n".join(lines)
        await _show_card(
            update,
            context,
            text,
            [
                [InlineKeyboardButton("🗑️ Endgültig löschen", callback_data="admin:delete-confirm")],
                [InlineKeyboardButton("⬅️ Abbrechen", callback_data="admin:dashboard")],
            ],
        )
    elif data == "admin:delete-confirm":
        removed = await asyncio.to_thread(services.entries.delete_status, "rejected")
        deleted_photos = 0
        for entry in removed:
            if await asyncio.to_thread(services.photos.delete, entry.get("photo")):
                deleted_photos += 1
        await _show_card(
            update,
            context,
            f"Gelöscht: {len(removed)} Meldungen und {deleted_photos} Fotos.",
            [[InlineKeyboardButton("⬅️ Adminbereich", callback_data="admin:dashboard")]],
        )
    elif data == "admin:channel":
        try:
            digest = await asyncio.to_thread(
                preview,
                services.entries,
                services.settings.paths.channel_state_file,
                services.settings.cipher,
                services.settings.channel_id,
            )
        except ChannelError as exc:
            await _show_card(
                update,
                context,
                f"Kanalstatus nicht verfügbar: {exc}",
                [[InlineKeyboardButton("⬅️ Adminbereich", callback_data="admin:dashboard")]],
            )
            return
        await _show_card(
            update,
            context,
            f"📣 Kanal-Post veröffentlichen?\n\nVersandbereit: {digest.eligible} Meldungen\nDavon mit Foto: {digest.photos}",
            [
                [InlineKeyboardButton("Jetzt veröffentlichen", callback_data="admin:channel-confirm")],
                [InlineKeyboardButton("⬅️ Abbrechen", callback_data="admin:dashboard")],
            ],
        )
    elif data == "admin:channel-confirm":
        lock = context.application.bot_data.setdefault("channel_ui_lock", asyncio.Lock())
        if lock.locked():
            await _show_card(update, context, "Der Kanalversand läuft bereits.", [])
            return
        await _show_card(update, context, "📣 Der Kanal-Post wird veröffentlicht …", [])
        try:
            async with lock:
                client = TelegramChannelClient(
                    services.settings.token, services.settings.channel_id
                )
                result = await asyncio.to_thread(
                    publish,
                    services.entries,
                    services.photos,
                    services.settings.paths.channel_state_file,
                    services.settings.paths.channel_lock_path,
                    services.settings.cipher,
                    services.settings.config,
                    services.settings.channel_id,
                    client,
                    source="admin",
                    write_authorized=services.settings.channel_write_enabled,
                    daily_limit=services.settings.channel_daily_limit,
                )
        except ChannelError as exc:
            await _show_card(
                update,
                context,
                f"Kanalversand fehlgeschlagen: {exc}",
                [[InlineKeyboardButton("⬅️ Adminbereich", callback_data="admin:dashboard")]],
            )
            return
        await _show_card(
            update,
            context,
            f"✅ Kanalversand abgeschlossen.\n\nMeldungen: {result.sent}\nFotos: {result.photos}",
            [[InlineKeyboardButton("⬅️ Adminbereich", callback_data="admin:dashboard")]],
        )
