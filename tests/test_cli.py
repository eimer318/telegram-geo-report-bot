import logging
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cryptography.fernet import Fernet

from telegram_geo_report_bot.__main__ import build_parser, main

from helpers import config_payload


class CommandLineTests(unittest.TestCase):
    def test_umap_sync_defaults_to_dry_run(self):
        args = build_parser().parse_args(["umap", "sync"])

        self.assertEqual("umap", args.command)
        self.assertEqual("sync", args.umap_command)
        self.assertFalse(args.apply)

    def test_umap_apply_is_explicit(self):
        args = build_parser().parse_args(["umap", "sync", "--apply"])

        self.assertTrue(args.apply)

    def test_channel_dry_run_is_an_explicit_supported_mode(self):
        args = build_parser().parse_args(["channel", "--dry-run"])

        self.assertEqual("channel", args.command)
        self.assertTrue(args.dry_run)
        self.assertFalse(args.publish)

    def test_admin_commands_require_the_documented_shape(self):
        args = build_parser().parse_args(["admin", "add", "123456"])

        self.assertEqual("admin", args.command)
        self.assertEqual("add", args.action)
        self.assertEqual(123456, args.user_id)

    def test_export_command_runs_without_a_telegram_token(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = root / "bot.json"
            config.write_text(json.dumps(config_payload()), encoding="utf-8")
            env_file = root / ".env.local"
            env_file.write_text(
                "\n".join(
                    (
                        f"ENCRYPTION_KEY={Fernet.generate_key().decode('ascii')}",
                        f"BOT_CONFIG_FILE={config}",
                        f"BOT_DATA_DIR={root / 'data'}",
                        f"BOT_PHOTO_DIR={root / 'photos'}",
                    )
                ),
                encoding="utf-8",
            )

            with patch.dict(os.environ, {}, clear=True):
                result = main(["--env-file", str(env_file), "export"])

            self.assertEqual(0, result)
            self.assertEqual([], json.loads((root / "data" / "entries.json").read_text("utf-8")))
            geojson = json.loads((root / "data" / "entries.geojson").read_text("utf-8"))
            self.assertEqual("FeatureCollection", geojson["type"])
            self.assertGreaterEqual(logging.getLogger("httpx").level, logging.WARNING)
            self.assertGreaterEqual(logging.getLogger("httpcore").level, logging.WARNING)

    def test_umap_commands_do_not_require_a_telegram_token(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = root / "bot.json"
            payload = config_payload()
            payload["umap"] = {
                "enabled": True,
                "base_url": "https://umap.example.test",
                "map_url": "https://umap.example.test/de/map/example_123/",
                "map_id": "123",
                "layer_id": "60fa595f-5700-4163-b8ed-4a93fd58f10d",
                "language": "de",
                "source_id": "test-project",
                "publish_photos": False,
                "category_styles": {},
            }
            config.write_text(json.dumps(payload), "utf-8")
            env_file = root / ".env.local"
            env_file.write_text(
                "\n".join((
                    f"ENCRYPTION_KEY={Fernet.generate_key().decode('ascii')}",
                    f"BOT_CONFIG_FILE={config}",
                    f"BOT_DATA_DIR={root / 'data'}",
                )),
                "utf-8",
            )
            with patch("telegram_geo_report_bot.__main__._umap_command", return_value=0):
                result = main(["--env-file", str(env_file), "umap", "check"])
            self.assertEqual(0, result)


if __name__ == "__main__":
    unittest.main()
