import json
import tempfile
import unittest
from pathlib import Path

from telegram_geo_report_bot.exports import geojson_payload, json_payload, write_exports

from helpers import entry


class ExportTests(unittest.TestCase):
    def test_only_approved_unblocked_entries_are_exported(self):
        approved = entry("approved")
        pending = entry("pending", status="pending")
        rejected = entry("rejected", status="rejected")
        blocked = entry(
            "blocked",
            security={"publication_blocked": True},
        )
        result = json_payload([approved, pending, rejected, blocked])
        self.assertEqual(["approved"], [value["id"] for value in result])

    def test_private_fields_and_photo_name_are_absent(self):
        value = entry(
            "one",
            photo="secret-name.jpg",
            user_id=123,
            reporter_hash="private",
            security={"reason_codes": ["x"]},
        )
        exported = json_payload([value])[0]
        self.assertTrue(exported["has_photo"])
        serialized = json.dumps(exported)
        for forbidden in ("secret-name", "reporter_hash", "user_id", "security", "review"):
            self.assertNotIn(forbidden, serialized)

    def test_geojson_uses_longitude_latitude_order(self):
        value = entry("one", lat=50.1, lon=8.6)
        result = geojson_payload([value])
        self.assertEqual("FeatureCollection", result["type"])
        self.assertEqual([8.6, 50.1], result["features"][0]["geometry"]["coordinates"])

    def test_invalid_coordinates_are_not_exported(self):
        self.assertEqual([], json_payload([entry("bad", lat=999)]))

    def test_files_are_written_atomically_as_valid_json(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            count = write_exports([entry("one")], root / "entries.json", root / "entries.geojson")
            self.assertEqual(1, count)
            self.assertEqual("one", json.loads((root / "entries.json").read_text("utf-8"))[0]["id"])
            self.assertEqual(
                "FeatureCollection",
                json.loads((root / "entries.geojson").read_text("utf-8"))["type"],
            )

    def test_json_and_geojson_preserve_umlauts_and_sharp_s(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            value = entry("unicode")
            value["address"]["street"] = "Grüne Straße 12"
            value["description"] = "Grüße aus Köln"

            write_exports(
                [value], root / "entries.json", root / "entries.geojson"
            )

            json_text = (root / "entries.json").read_text("utf-8")
            geojson_text = (root / "entries.geojson").read_text("utf-8")
            self.assertIn("Grüne Straße 12", json_text)
            self.assertIn("Grüße aus Köln", json_text)
            self.assertIn("Grüne Straße 12", geojson_text)
            self.assertNotIn("\\u00df", json_text)


if __name__ == "__main__":
    unittest.main()
