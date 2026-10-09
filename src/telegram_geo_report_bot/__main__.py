"""Unified command line interface."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from .application import run_bot
from .channel_publish import (
    ChannelError,
    TelegramChannelClient,
    initialize_state,
    preview,
    publish,
)
from .config import ConfigurationError, load_settings
from .services import build_services


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="telegram-geo-report-bot")
    parser.add_argument("--env-file", type=Path, help="Alternative .env-Datei")
    subcommands = parser.add_subparsers(dest="command", required=True)
    subcommands.add_parser("run", help="Telegram-Bot starten")
    subcommands.add_parser("export", help="JSON- und GeoJSON-Export neu erzeugen")

    channel = subcommands.add_parser("channel", help="Telegram-Kanal verwalten")
    channel_mode = channel.add_mutually_exclusive_group()
    channel_mode.add_argument("--check", action="store_true")
    channel_mode.add_argument("--initialize", action="store_true")
    channel_mode.add_argument("--dry-run", action="store_true")
    channel_mode.add_argument("--publish", action="store_true")
    channel.add_argument("--source", choices=("admin", "scheduled"), default="scheduled")

    admin = subcommands.add_parser("admin", help="Administratoren verwalten")
    admin.add_argument("action", choices=("add", "remove", "list"))
    admin.add_argument("user_id", nargs="?", type=int)
    return parser


def _channel_command(args, services) -> int:
    settings = services.settings
    if not settings.channel_id:
        raise ChannelError("TELEGRAM_CHANNEL_ID fehlt")
    client = TelegramChannelClient(settings.token, settings.channel_id)
    if args.check:
        client.check_channel()
        print("Kanalzugriff und Post-Berechtigung sind gültig.")
        return 0
    if args.initialize:
        count = initialize_state(
            services.entries,
            settings.paths.channel_state_file,
            settings.cipher,
            settings.channel_id,
        )
        print(f"Kanalzustand initialisiert; {count} bestehende Freigaben übersprungen.")
        return 0
    if not args.publish:
        result = preview(
            services.entries,
            settings.paths.channel_state_file,
            settings.cipher,
            settings.channel_id,
        )
        print(
            f"Dry-run: initialisiert={result.initialized}, "
            f"bereit={result.eligible}, fotos={result.photos}, "
            f"heute_abgeschlossen={result.completed}"
        )
        return 0
    result = publish(
        services.entries,
        services.photos,
        settings.paths.channel_state_file,
        settings.paths.channel_lock_path,
        settings.cipher,
        settings.config,
        settings.channel_id,
        client,
        source=args.source,
        write_authorized=settings.channel_write_enabled,
        daily_limit=settings.channel_daily_limit,
    )
    print(
        f"Kanalversand: gesendet={result.sent}, fotos={result.photos}, "
        f"fallbacks={result.photo_fallbacks}, abgeschlossen={result.completed}"
    )
    return 0


def main(argv=None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
        stream=sys.stdout,
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    parser = build_parser()
    args = parser.parse_args(argv)
    base_dir = Path(__file__).resolve().parents[2]
    require_token = args.command in {"run", "channel"}
    try:
        settings = load_settings(
            base_dir, env_file=args.env_file, require_token=require_token
        )
        services = build_services(settings)
        if args.command == "run":
            return run_bot(services)
        if args.command == "export":
            count = services.entries.refresh_exports()
            print(f"Export erstellt: {count} freigegebene Meldungen.")
            return 0
        if args.command == "channel":
            return _channel_command(args, services)
        if args.command == "admin":
            if args.action == "list":
                print(f"Administratoren: {len(services.admins.load())}")
                return 0
            if args.user_id is None or args.user_id <= 0:
                parser.error("admin add/remove benötigt eine positive Telegram-ID")
            changed = (
                services.admins.add(args.user_id)
                if args.action == "add"
                else services.admins.remove(args.user_id)
            )
            print("Administrator gespeichert." if changed else "Keine Änderung.")
            return 0
    except (ConfigurationError, ChannelError, OSError, ValueError) as exc:
        print(f"Abgebrochen: {exc}", file=sys.stderr)
        return 1
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
