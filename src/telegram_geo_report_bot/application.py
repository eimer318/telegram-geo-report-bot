"""Telegram application construction and lifecycle."""

from __future__ import annotations

import logging

from telegram import Update
from telegram.error import Conflict, InvalidToken, NetworkError
from telegram.ext import (
    Application,
    ApplicationHandlerStop,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    TypeHandler,
    filters,
)

from .handlers.admin import admin_command, handle_admin_callback, handle_admin_text
from .handlers.common import get_services, show_main_menu
from .handlers.user import (
    handle_flow_callback,
    handle_main_callback,
    handle_photo,
    handle_text,
    me,
    start,
)
from .security import SecurityStateError, pseudonymize_user, record_security_event
from .services import Services


async def ignore_bot_sender(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if user is None or user.is_bot is not True:
        return
    services = get_services(context)
    try:
        reporter = pseudonymize_user(user.id, services.settings.encryption_key)
        record_security_event(
            services.settings.paths.security_events_file,
            services.settings.cipher,
            reporter,
            "bot_sender_ignored",
            ("bot_sender",),
        )
    except SecurityStateError:
        logging.warning("Bot-Absender ignoriert; Sicherheitszustand ist nicht verfügbar")
    raise ApplicationHandlerStop


async def text_dispatch(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await handle_admin_text(update, context):
        return
    if not await handle_text(update, context):
        await show_main_menu(update, context)


async def error_handler(update, context) -> None:
    error = context.error
    if isinstance(error, Conflict):
        logging.error("Eine weitere Bot-Instanz verwendet denselben Token.")
    elif isinstance(error, NetworkError):
        logging.warning("Telegram-Netzwerkfehler.")
    else:
        logging.exception("Unbehandelter Bot-Fehler", exc_info=error)


def build_application(services: Services) -> Application:
    application = (
        Application.builder()
        .token(services.settings.token)
        .connect_timeout(15.0)
        .read_timeout(30.0)
        .write_timeout(30.0)
        .pool_timeout(15.0)
        .get_updates_connect_timeout(15.0)
        .get_updates_read_timeout(30.0)
        .get_updates_write_timeout(30.0)
        .get_updates_pool_timeout(15.0)
        .build()
    )
    application.bot_data["services"] = services
    application.add_handler(TypeHandler(Update, ignore_bot_sender), group=-1)
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("me", me))
    application.add_handler(CommandHandler("admin", admin_command))
    application.add_handler(
        MessageHandler(filters.ChatType.PRIVATE & filters.TEXT & ~filters.COMMAND, text_dispatch)
    )
    application.add_handler(
        MessageHandler(filters.ChatType.PRIVATE & filters.PHOTO, handle_photo)
    )
    application.add_handler(
        CallbackQueryHandler(
            handle_admin_callback,
            pattern=r"^(main:admin|admin:|review:|all:|status:)",
        )
    )
    application.add_handler(
        CallbackQueryHandler(
            handle_main_callback,
            pattern=r"^main:(add|help|privacy|results|back)$",
        )
    )
    application.add_handler(
        CallbackQueryHandler(
            handle_flow_callback,
            pattern=r"^(addr:|cat:|sub:|amount:|description:none$|photo:|entry:)",
        )
    )
    application.add_error_handler(error_handler)
    return application


def run_bot(services: Services) -> int:
    services.settings.paths.data_dir.mkdir(parents=True, exist_ok=True)
    services.settings.paths.photo_dir.mkdir(parents=True, exist_ok=True)
    services.entries.refresh_exports()
    application = build_application(services)
    try:
        application.run_polling(
            drop_pending_updates=False,
            allowed_updates=("message", "callback_query"),
        )
    except InvalidToken:
        logging.error("Telegram hat den Bot-Token abgelehnt.")
        return 1
    return 0
