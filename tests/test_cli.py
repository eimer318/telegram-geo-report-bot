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


if __name__ == "__main__":
    unittest.main()
