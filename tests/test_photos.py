import tempfile
import unittest
from io import BytesIO
from pathlib import Path

from cryptography.fernet import Fernet
from PIL import Image

from telegram_geo_report_bot.photos import PhotoError, PhotoStore, sanitize_jpeg


class PhotoTests(unittest.TestCase):
    def test_sanitized_photo_is_jpeg_with_limited_dimensions(self):
        source = BytesIO()
        Image.new("RGB", (2400, 1200), "red").save(
            source, format="JPEG", exif=Image.Exif()
        )
        result = sanitize_jpeg(source.getvalue(), max_bytes=10_000_000, max_dimension=800)
        with Image.open(BytesIO(result)) as image:
            self.assertEqual("JPEG", image.format)
            self.assertLessEqual(max(image.size), 800)
            self.assertFalse(image.getexif())

    def test_store_encrypts_photo_and_rejects_path_traversal(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            store = PhotoStore(root, Fernet(Fernet.generate_key()))
            store.save("photo_test.jpg", b"jpeg")
            self.assertNotEqual(b"jpeg", (root / "photo_test.jpg.enc").read_bytes())
            self.assertEqual(b"jpeg", store.load("photo_test.jpg"))
            with self.assertRaises(PhotoError):
                store.save("../escape.jpg", b"x")


if __name__ == "__main__":
    unittest.main()
