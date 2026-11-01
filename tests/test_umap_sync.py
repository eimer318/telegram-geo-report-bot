import tempfile
import unittest
from pathlib import Path

from telegram_geo_report_bot.umap.client import LayerSnapshot
from telegram_geo_report_bot.umap.sync import UMapSyncError, synchronize

from umap_helpers import layer, make_umap_services, report


class FakeClient:
    def __init__(self, current):
        self.current = current
        self.uploads = []
        self.verified = 0

    def verify_login(self):
        self.verified += 1
        return True

    def download_layer(self):
        return LayerSnapshot(self.current, "version-1")

    def upload_layer(self, value, reference):
        self.uploads.append((value, reference))
        self.current = value
        return {"ok": True}


class UMapSyncTests(unittest.TestCase):
    def test_dry_run_never_uploads(self):
        with tempfile.TemporaryDirectory() as temporary:
            services = make_umap_services(Path(temporary))
            services.entries.append(report())
            client = FakeClient(layer())

            result = synchronize(services, client)

            self.assertFalse(result.applied)
            self.assertEqual(1, result.summary.added)
            self.assertEqual([], client.uploads)

    def test_apply_requires_environment_gate(self):
        with tempfile.TemporaryDirectory() as temporary:
            services = make_umap_services(Path(temporary), allow_write=False)
            services.entries.append(report())
            with self.assertRaises(UMapSyncError):
                synchronize(services, FakeClient(layer()), apply=True)

    def test_apply_creates_encrypted_backup_and_verifies_server(self):
        with tempfile.TemporaryDirectory() as temporary:
            services = make_umap_services(Path(temporary), allow_write=True)
            services.entries.append(report())
            client = FakeClient(layer())

            result = synchronize(services, client, apply=True)

            self.assertTrue(result.applied)
            self.assertTrue(result.verified)
            self.assertEqual(1, len(client.uploads))
            self.assertTrue(result.backup_file.is_file())
            backup = result.backup_file.read_bytes()
            self.assertNotIn(b"FeatureCollection", backup)
            self.assertIn(b"FeatureCollection", services.settings.cipher.decrypt(backup))

    def test_no_changes_skips_upload_and_backup(self):
        with tempfile.TemporaryDirectory() as temporary:
            services = make_umap_services(Path(temporary), allow_write=True)
            services.entries.append(report())
            client = FakeClient(layer())
            first = synchronize(services, client, apply=True)

            second = synchronize(services, client, apply=True)

            self.assertTrue(first.applied)
            self.assertFalse(second.applied)
            self.assertTrue(second.verified)
            self.assertEqual(1, len(client.uploads))


if __name__ == "__main__":
    unittest.main()
