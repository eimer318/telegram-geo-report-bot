import tempfile
import unittest
from pathlib import Path

from cryptography.fernet import Fernet

from telegram_geo_report_bot.admins import AdminStore, AdminStoreError, admin_hash


class AdminStoreTests(unittest.TestCase):
    def test_admin_identifiers_are_hashed_and_encrypted(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "admins.enc"
            store = AdminStore(path, Fernet(Fernet.generate_key()))

            self.assertTrue(store.add(123456789))
            self.assertFalse(store.add(123456789))
            self.assertTrue(store.contains(123456789))
            self.assertNotIn(b"123456789", path.read_bytes())
            self.assertEqual({admin_hash(123456789)}, store.load())

            self.assertTrue(store.remove(123456789))
            self.assertFalse(store.remove(123456789))
            self.assertFalse(store.contains(123456789))

    def test_ui_removal_cannot_remove_the_last_administrator(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = AdminStore(
                Path(temporary) / "admins.enc", Fernet(Fernet.generate_key())
            )
            store.add(1)
            with self.assertRaises(AdminStoreError):
                store.remove_preserving_one(1)

            store.add(2)
            self.assertTrue(store.remove_preserving_one(1))
            self.assertTrue(store.contains(2))


if __name__ == "__main__":
    unittest.main()
