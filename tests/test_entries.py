import json
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cryptography.fernet import Fernet

from telegram_geo_report_bot.entries import EntryLoadError, EntryRepository, EntrySecurityError

from helpers import entry


class EntryRepositoryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.cipher = Fernet(Fernet.generate_key())
        self.repository = EntryRepository(
            self.root / "entries.enc",
            self.root / "entries.json",
            self.root / "entries.geojson",
            self.cipher,
        )

    def tearDown(self):
        self.temporary.cleanup()

    def test_existing_line_format_remains_readable(self):
        value = entry("legacy")
        self.repository.entries_file.write_bytes(
            self.cipher.encrypt(json.dumps(value).encode("utf-8")) + b"\n"
        )
        self.assertEqual("legacy", self.repository.load()[0]["id"])

    def test_corrupted_source_fails_closed(self):
        self.repository.entries_file.write_bytes(b"not-fernet\n")
        with self.assertRaises(EntryLoadError):
            self.repository.load()

    def test_concurrent_appends_do_not_lose_entries(self):
        threads = [
            threading.Thread(target=self.repository.append, args=(entry(f"id-{index}"),))
            for index in range(20)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(20, len(self.repository.load()))

    def test_security_block_prevents_approval(self):
        self.repository.save(
            [entry("blocked", status="pending", security={"publication_blocked": True})]
        )
        with self.assertRaises(EntrySecurityError):
            self.repository.set_status("blocked", "approved", "admin")

    def test_admin_replacement_uses_configured_description_limit(self):
        self.repository.save(
            [
                entry(
                    "blocked",
                    status="pending",
                    security={
                        "publication_blocked": True,
                        "automatic_approval_blocked": True,
                    },
                )
            ]
        )
        replacement = "S" * 80

        self.repository.replace_blocked_description(
            "blocked", replacement, "admin", max_length=100
        )

        saved = self.repository.load()[0]
        self.assertEqual(replacement, saved["description"])
        self.assertFalse(saved["security"]["publication_blocked"])
        self.assertTrue(saved["security"]["automatic_approval_blocked"])

    def test_admin_status_change_rebuilds_both_exports(self):
        self.repository.save([entry("pending", status="pending")])
        self.repository.set_status("pending", "approved", "admin")
        self.assertIn("pending", self.repository.json_export_file.read_text("utf-8"))
        self.assertIn("FeatureCollection", self.repository.geojson_export_file.read_text("utf-8"))

    def test_auto_validation_only_uses_last_168_hours(self):
        now = datetime(2026, 8, 20, 12, tzinfo=timezone.utc)
        old = entry(
            "old",
            status="pending",
            reporter="old-user",
            created_at=(now - timedelta(hours=169)).isoformat(),
            timestamp=(now - timedelta(hours=169)).isoformat(),
        )
        recent_a = entry(
            "a",
            status="pending",
            reporter="a-user",
            created_at=(now - timedelta(hours=1)).isoformat(),
            timestamp=(now - timedelta(hours=1)).isoformat(),
        )
        recent_b = entry(
            "b",
            status="pending",
            reporter="b-user",
            lat=52.5001,
            created_at=now.isoformat(),
            timestamp=now.isoformat(),
        )
        self.repository.save([old, recent_a, recent_b])
        changed = self.repository.auto_validate(now=now)
        self.assertEqual({"a", "b"}, set(changed))
        statuses = {value["id"]: value["status"] for value in self.repository.load()}
        self.assertEqual("pending", statuses["old"])

    def test_paging_returns_newest_entries(self):
        self.repository.save([entry(f"id-{index}") for index in range(75)])
        first, more = self.repository.page(limit=50)
        second, final_more = self.repository.page(offset=50, limit=50)
        self.assertEqual("id-74", first[0]["id"])
        self.assertEqual(50, len(first))
        self.assertTrue(more)
        self.assertEqual(25, len(second))
        self.assertFalse(final_more)


if __name__ == "__main__":
    unittest.main()
