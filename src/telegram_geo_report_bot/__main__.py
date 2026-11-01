"""Unified command line interface."""

from __future__ import annotations

import argparse
import getpass
import logging
import sys
import time
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
from .operation_lock import OperationLockedError
from .services import build_services
from .umap.client import UMapClient, UMapError, UMapNetworkError
from .umap.session_store import UMapCredentials, UMapSessionError, save_session
from .umap.sync import UMapSyncError, check_connection, synchronize


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

    umap = subcommands.add_parser("umap", help="uMap-Anbindung prüfen und synchronisieren")
    umap_commands = umap.add_subparsers(dest="umap_command", required=True)
    umap_commands.add_parser("check", help="Sitzung und Ziel-Layer lesend prüfen")
    umap_commands.add_parser(
        "session", help="uMap-Sitzung interaktiv prüfen und verschlüsselt speichern"
    )
    umap_sync = umap_commands.add_parser(
        "sync", help="uMap-Synchronisierung; ohne --apply immer Dry-Run"
    )
    umap_sync.add_argument("--apply", action="store_true", help="Änderungen hochladen")
    umap_sync.add_argument(
        "--network-retries",
        type=int,
        default=0,
        choices=range(0, 4),
        metavar="0..3",
        help="Temporäre Lesefehler bis zu dreimal erneut versuchen",
    )
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


def _umap_summary(result) -> str:
    summary = result.summary
    mode = "angewendet und verifiziert" if result.applied else "Dry-Run"
    return (
        f"{mode}: neu={summary.added}, geändert={summary.updated}, "
        f"entfernt={summary.removed}, unverändert={summary.unchanged}, "
        f"verwaist={summary.orphaned}, fotos={summary.photos}"
    )


def _configure_umap_session(services) -> int:
    config = services.settings.config.umap
    if config is None:
        raise UMapSyncError("Die uMap-Anbindung ist nicht konfiguriert oder deaktiviert")
    session_id = getpass.getpass("uMap-Cookie sessionid: ").strip()
    csrf_token = getpass.getpass("uMap-Cookie csrftoken: ").strip()
    user_agent = input(
        f"Browser-User-Agent [{services.settings.umap_user_agent}]: "
    ).strip() or services.settings.umap_user_agent
    credentials = UMapCredentials(session_id, csrf_token, user_agent)
    client = UMapClient(
        config,
        credentials,
        user_agent=services.settings.umap_user_agent,
    )
    client.verify_login()
    save_session(
        services.settings.paths.umap_session_file,
        services.settings.cipher,
        credentials,
    )
    print("uMap-Sitzung erfolgreich geprüft und verschlüsselt gespeichert.")
    return 0


def _umap_command(args, services) -> int:
    if args.umap_command == "session":
        return _configure_umap_session(services)
    if args.umap_command == "check":
        check_connection(services)
        print("uMap-Sitzung und Ziel-Layer sind lesend erreichbar.")
        return 0
    for attempt in range(args.network_retries + 1):
        try:
            result = synchronize(services, apply=args.apply)
            break
        except UMapNetworkError:
            if attempt >= args.network_retries:
                raise
            delay = 5 * (2**attempt)
            logging.warning(
                "Temporärer uMap-Netzwerkfehler; neuer Versuch in %s Sekunden.",
                delay,
            )
            time.sleep(delay)
    print(_umap_summary(result))
    if result.backup_file:
        print(f"Verschlüsseltes Layer-Backup: {result.backup_file}")
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
        if args.command == "umap":
            return _umap_command(args, services)
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
    except (
        ConfigurationError,
        ChannelError,
        UMapError,
        UMapSessionError,
        UMapSyncError,
        OperationLockedError,
        OSError,
        ValueError,
    ) as exc:
        print(f"Abgebrochen: {exc}", file=sys.stderr)
        return 1
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
