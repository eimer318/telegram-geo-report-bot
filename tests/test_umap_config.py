import json
import tempfile
import unittest
from pathlib import Path

from telegram_geo_report_bot.config import ConfigurationError, load_bot_config

from umap_helpers import LAYER_ID, umap_payload


class UMapConfigurationTests(unittest.TestCase):
    def test_disabled_configuration_has_no_required_target_values(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "config.json"
            payload = umap_payload()
            payload["umap"] = {"enabled": False}
            path.write_text(json.dumps(payload), "utf-8")

            self.assertIsNone(load_bot_config(path).umap)

    def test_enabled_configuration_builds_safe_urls_and_styles(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "config.json"
            path.write_text(json.dumps(umap_payload()), "utf-8")

            config = load_bot_config(path).umap

            self.assertEqual(
                f"https://umap.example.test/de/datalayer/123/{LAYER_ID}/",
                config.layer_url,
            )
            self.assertEqual("#123456", config.category_styles["objects"].marker_color)
            self.assertFalse(config.publish_photos)

    def test_mismatched_origin_and_unknown_style_are_rejected(self):
        cases = []
        origin = umap_payload()
        origin["umap"]["map_url"] = "https://other.example.test/de/map/example_123/"
        cases.append(origin)
        style = umap_payload()
        style["umap"]["category_styles"] = {"missing": {"marker_color": "red"}}
        cases.append(style)
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "config.json"
            for payload in cases:
                with self.subTest(payload=payload["umap"]):
                    path.write_text(json.dumps(payload), "utf-8")
                    with self.assertRaises(ConfigurationError):
                        load_bot_config(path)


if __name__ == "__main__":
    unittest.main()
