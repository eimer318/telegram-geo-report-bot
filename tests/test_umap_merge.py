import base64
import tempfile
import unittest
from io import BytesIO
from pathlib import Path

from PIL import Image

from telegram_geo_report_bot.umap.merge import UMapLayerError, merge_entries
from telegram_geo_report_bot.umap.photos import photo_data_uri

from umap_helpers import layer, make_umap_services, report


class UMapMergeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.services = make_umap_services(self.root)
        self.bot_config = self.services.settings.config
        self.umap_config = self.bot_config.umap

    def tearDown(self):
        self.temporary.cleanup()

    def merge(self, source, entries):
        return merge_entries(
            source,
            entries,
            self.bot_config,
            self.umap_config,
            lambda _: "data:image/jpeg;base64,abc",
        )

    def test_foreign_features_are_preserved_and_second_merge_is_idempotent(self):
        foreign = {
            "type": "Feature",
            "id": "Ab123",
            "geometry": {"type": "Point", "coordinates": [1, 2]},
            "properties": {"manual": True},
        }
        first = self.merge(layer([foreign]), [report()])
        self.assertEqual(1, first.summary.added)
        self.assertEqual(foreign, first.layer["features"][0])

        second = self.merge(first.layer, [report()])
        self.assertFalse(second.summary.changed)
        self.assertEqual(1, second.summary.unchanged)
        self.assertEqual(first.layer, second.layer)

    def test_status_change_removes_only_managed_feature(self):
        first = self.merge(layer(), [report()])
        result = self.merge(first.layer, [report(status="rejected")])
        self.assertEqual(1, result.summary.removed)
        self.assertEqual([], result.layer["features"])

    def test_missing_local_feature_is_preserved_as_orphan(self):
        first = self.merge(layer(), [report()])
        result = self.merge(first.layer, [])
        self.assertEqual(1, result.summary.orphaned)
        self.assertEqual(first.layer, result.layer)

    def test_dynamic_labels_styles_and_owner_properties_are_preserved(self):
        first = self.merge(layer(), [report()])
        feature = first.layer["features"][0]
        feature["properties"]["owner_note"] = "keep"
        feature["properties"]["_umap_options"]["showLabel"] = True

        result = self.merge(first.layer, [report(description="Changed")])
        properties = result.layer["features"][0]["properties"]
        self.assertEqual("Objekte", properties["name"])
        self.assertEqual("Kleine Objekte", properties["subcategory"])
        self.assertEqual("keep", properties["owner_note"])
        self.assertTrue(properties["_umap_options"]["showLabel"])
        self.assertEqual("#123456", properties["_umap_options"]["color"])
        self.assertNotIn("reporter_hash", str(properties))

    def test_duplicate_uuid_and_wrong_layer_are_rejected(self):
        with self.assertRaises(UMapLayerError):
            self.merge(layer(), [report(), report()])
        wrong = layer()
        wrong["id"] = "00000000-0000-0000-0000-000000000000"
        with self.assertRaises(UMapLayerError):
            self.merge(wrong, [report()])

    def test_photo_is_only_embedded_when_explicitly_enabled(self):
        result = self.merge(layer(), [report(photo="photo.jpg")])
        self.assertFalse(result.layer["features"][0]["properties"]["photo_included"])
        services = make_umap_services(self.root / "photo", publish_photos=True)
        result = merge_entries(
            layer(),
            [report(photo="photo.jpg")],
            services.settings.config,
            services.settings.config.umap,
            lambda _: "data:image/jpeg;base64,abc",
        )
        self.assertTrue(result.layer["features"][0]["properties"]["photo_included"])

    def test_embedded_photo_is_small_exif_free_jpeg(self):
        raw = BytesIO()
        Image.new("RGB", (1200, 600), "blue").save(raw, format="PNG")
        self.services.photos.save("photo.jpg", raw.getvalue())

        uri = photo_data_uri(self.services.photos, "photo.jpg")

        decoded = base64.b64decode(uri.split(",", 1)[1])
        with Image.open(BytesIO(decoded)) as image:
            self.assertEqual("JPEG", image.format)
            self.assertLessEqual(image.width, 300)
            self.assertLessEqual(image.height, 300)
            self.assertEqual({}, image.getexif())


if __name__ == "__main__":
    unittest.main()
