import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class UMapSystemdContractTests(unittest.TestCase):
    def test_service_is_oneshot_hardened_and_doubly_write_guarded(self):
        text = (ROOT / "deploy/systemd/telegram-geo-report-umap.service").read_text("utf-8")
        self.assertIn("Type=oneshot", text)
        self.assertIn("User=telegram-geo-report", text)
        self.assertIn("NoNewPrivileges=true", text)
        self.assertIn("UMask=0077", text)
        self.assertIn("umap sync --apply", text)
        self.assertNotIn("UMAP_ALLOW_WRITE=true", text)

    def test_timer_is_persistent_and_not_linked_to_install_automatically(self):
        text = (ROOT / "deploy/systemd/telegram-geo-report-umap.timer").read_text("utf-8")
        self.assertIn("Persistent=true", text)
        self.assertIn("OnCalendar=", text)
        self.assertIn("WantedBy=timers.target", text)


if __name__ == "__main__":
    unittest.main()
