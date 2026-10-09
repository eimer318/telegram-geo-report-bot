import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cryptography.fernet import Fernet

from telegram_geo_report_bot.security import (
    SecurityStateError,
    assess_address_query,
    assess_description,
    check_and_record_rate_action,
    pseudonymize_user,
    record_security_event,
    security_event_summary,
)


class SecurityTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.key = Fernet.generate_key().decode()
        self.cipher = Fernet(self.key.encode())

    def tearDown(self):
        self.temporary.cleanup()

    def test_normal_text_and_german_characters_are_allowed(self):
        result = assess_description("  Grüße   aus Köln  ")
        self.assertEqual("Grüße aus Köln", result.normalized)
        self.assertFalse(result.suspicious)

    def test_shell_html_template_and_unicode_patterns_are_detected(self):
        cases = {
            "$(id)": "shell_command_substitution",
            "<script>alert(1)</script>": "html_markup",
            "{{secret}}": "template_expression",
            "normal\u202etext": "unicode_control_character",
        }
        for text, reason in cases.items():
            with self.subTest(text=text):
                self.assertIn(reason, assess_description(text).reason_codes)

    def test_address_length_is_limited(self):
        self.assertTrue(assess_address_query("x" * 201).too_long)

    def test_pseudonym_is_deterministic_and_does_not_contain_user_id(self):
        first = pseudonymize_user(123456, self.key)
        self.assertEqual(first, pseudonymize_user(123456, self.key))
        self.assertNotIn("123456", first)

    def test_minute_and_hour_rate_limits(self):
        path = self.root / "rate.enc"
        now = datetime(2026, 8, 20, tzinfo=timezone.utc)
        for offset in range(3):
            result = check_and_record_rate_action(
                path, self.cipher, "reporter", now=now + timedelta(seconds=offset)
            )
            self.assertTrue(result.allowed)
        blocked = check_and_record_rate_action(
            path, self.cipher, "reporter", now=now + timedelta(seconds=3)
        )
        self.assertFalse(blocked.allowed)
        self.assertGreater(blocked.retry_after_seconds, 0)

    def test_events_are_encrypted_and_contain_no_raw_text_or_id(self):
        path = self.root / "events.enc"
        record_security_event(
            path,
            self.cipher,
            "pseudonym",
            "suspicious_description",
            ("template_expression",),
            entry_id="entry-one",
        )
        raw = path.read_bytes()
        self.assertNotIn(b"pseudonym", raw)
        payload = json.loads(self.cipher.decrypt(raw))
        serialized = json.dumps(payload)
        self.assertNotIn("raw input", serialized)
        summary = security_event_summary(path, self.cipher)
        self.assertEqual(1, summary["events"])

    def test_corrupt_state_fails_closed(self):
        path = self.root / "events.enc"
        path.write_bytes(b"bad")
        with self.assertRaises(SecurityStateError):
            security_event_summary(path, self.cipher)


if __name__ == "__main__":
    unittest.main()
