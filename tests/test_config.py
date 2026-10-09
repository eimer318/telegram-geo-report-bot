import json
import tempfile
import unittest
from pathlib import Path

from cryptography.fernet import Fernet

from telegram_geo_report_bot.config import ConfigurationError, load_bot_config, load_settings

from helpers import config_payload


class ConfigurationTests(unittest.TestCase):
    def test_configuration_is_dynamic_and_has_no_directory_side_effects(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = root / "bot.json"
            config.write_text(json.dumps(config_payload()), encoding="utf-8")
            settings = load_settings(
                root,
                environ={
                    "BOT_TOKEN": "token",
                    "ENCRYPTION_KEY": Fernet.generate_key().decode(),
                    "BOT_CONFIG_FILE": str(config),
                    "BOT_DATA_DIR": str(root / "not-created"),
                    "BOT_PHOTO_DIR": str(root / "also-not-created"),
                },
            )
            self.assertEqual("Objekte", settings.config.category_labels["objects"])
            self.assertFalse(settings.paths.data_dir.exists())
            self.assertFalse(settings.paths.photo_dir.exists())

    def test_multiline_help_and_disabled_limits_are_preserved(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "bot.json"
            payload = config_payload()
            payload["help_text"] = "Hilfe\n\n1. Adresse\n2. Kategorie"
            payload["daily_user_limit"] = 0
            payload["rate_limits"] = {
                "enabled": False,
                "per_minute": 3,
                "per_hour": 10,
            }
            path.write_text(json.dumps(payload), encoding="utf-8")

            config = load_bot_config(path)

            self.assertEqual("Hilfe\n\n1. Adresse\n2. Kategorie", config.help_text)
            self.assertEqual(0, config.daily_user_limit)
            self.assertFalse(config.rate_limits.enabled)

    def test_duplicate_category_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "bot.json"
            payload = config_payload()
            payload["categories"].append(payload["categories"][0])
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaises(ConfigurationError):
                load_bot_config(path)

    def test_offline_commands_may_omit_token(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = root / "bot.json"
            config.write_text(json.dumps(config_payload()), encoding="utf-8")
            settings = load_settings(
                root,
                environ={
                    "ENCRYPTION_KEY": Fernet.generate_key().decode(),
                    "BOT_CONFIG_FILE": str(config),
                },
                require_token=False,
            )
            self.assertEqual("", settings.token)

    def test_token_is_required_for_runtime(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = root / "bot.json"
            config.write_text(json.dumps(config_payload()), encoding="utf-8")
            with self.assertRaisesRegex(ConfigurationError, "BOT_TOKEN"):
                load_settings(
                    root,
                    environ={
                        "ENCRYPTION_KEY": Fernet.generate_key().decode(),
                        "BOT_CONFIG_FILE": str(config),
                    },
                )
    def test_region_filters_must_be_lists(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "bot.json"
            payload = config_payload()
            payload["geocoding"]["allowed_states"] = "Berlin"
            path.write_text(json.dumps(payload), encoding="utf-8")

            with self.assertRaisesRegex(ConfigurationError, "allowed_states"):
                load_bot_config(path)




if __name__ == "__main__":
    unittest.main()
