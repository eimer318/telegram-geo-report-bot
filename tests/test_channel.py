import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from telegram_geo_report_bot.channel_format import format_digest, partition_media
from telegram_geo_report_bot.channel_publish import (
    ChannelStateError,
    TelegramChannelError,
    initialize_state,
    load_state,
    preview,
    publish,
)

from helpers import entry, make_services


class FakeChannelClient:
    def __init__(self, *, fail_text_once=False):
        self.messages = []
        self.photos = []
        self.albums = []
        self._message_id = 100
        self.fail_text_once = fail_text_once

    def _id(self):
        self._message_id += 1
        return self._message_id

    def check_channel(self):
        return None

    def public_bot_url(self):
        return "https://t.me/example_bot"

    def send_text(self, text):
        if self.fail_text_once:
            self.fail_text_once = False
            raise TelegramChannelError("temporary", retryable=True)
        self.messages.append(text)
        return self._id()

    def send_photo(self, content):
        self.photos.append(content)
        return self._id()

    def send_media_group(self, photos):
        self.albums.append(list(photos))
        return [self._id() for _ in photos]


class ChannelTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.services = make_services(self.root)
        self.state = self.services.settings.paths.channel_state_file
        initialize_state(
            self.services.entries,
            self.state,
            self.services.settings.cipher,
            self.services.settings.channel_id,
        )

    def tearDown(self):
        self.temporary.cleanup()

    def _publish(self, client, *, source="admin", daily_limit=True):
        return publish(
            self.services.entries,
            self.services.photos,
            self.state,
            self.services.settings.paths.channel_lock_path,
            self.services.settings.cipher,
            self.services.settings.config,
            self.services.settings.channel_id,
            client,
            source=source,
            write_authorized=True,
            daily_limit=daily_limit,
            now=datetime(2026, 8, 20, 19, tzinfo=timezone.utc),
        )

    def test_formatter_uses_configured_labels(self):
        chunks = format_digest(
            [entry("one")],
            self.services.settings.config,
            now=datetime(2026, 8, 20, tzinfo=timezone.utc),
        )
        self.assertIn("Objekte:", chunks[0])
        self.assertIn("Kleine Objekte:", chunks[0])
        self.assertNotIn("reporter", chunks[0])

    def test_media_partition_avoids_single_remainder(self):
        chunks = partition_media([str(value) for value in range(11)])
        self.assertEqual([9, 2], [len(chunk) for chunk in chunks])
        self.assertEqual([10, 5], [len(chunk) for chunk in partition_media([str(value) for value in range(15)])])

    def test_manual_empty_does_not_complete_day(self):
        result = self._publish(FakeChannelClient(), source="admin")
        self.assertTrue(result.empty)
        self.assertFalse(result.completed)

    def test_scheduled_empty_completes_day_without_message(self):
        client = FakeChannelClient()
        result = self._publish(client, source="scheduled")
        self.assertTrue(result.empty)
        self.assertTrue(result.completed)
        self.assertEqual([], client.messages)

    def test_text_and_photo_entries_publish_once(self):
        self.services.photos.save("photo.jpg", b"jpeg")
        self.services.entries.save([entry("text"), entry("photo", photo="photo.jpg")])
        client = FakeChannelClient()
        result = self._publish(client)
        self.assertEqual(2, result.sent)
        self.assertEqual(1, result.photos)
        self.assertEqual(2, len(client.messages))  # summary plus footer
        self.assertEqual(1, len(client.photos))
        again = self._publish(client)
        self.assertTrue(again.already_completed)

    def test_more_than_ten_photos_use_safe_groups(self):
        entries = []
        for index in range(11):
            name = f"photo-{index}.jpg"
            self.services.photos.save(name, f"photo-{index}".encode())
            entries.append(entry(f"id-{index}", photo=name))
        self.services.entries.save(entries)
        client = FakeChannelClient()
        result = self._publish(client)
        self.assertEqual(11, result.photos)
        self.assertEqual([9, 2], [len(album) for album in client.albums])

    def test_retry_resumes_existing_batch(self):
        self.services.entries.save([entry("one")])
        with self.assertRaises(TelegramChannelError):
            self._publish(FakeChannelClient(fail_text_once=True))
        state = load_state(
            self.state, self.services.settings.cipher, self.services.settings.channel_id
        )
        self.assertEqual(1, len(state["batches"]))
        retry = FakeChannelClient()
        result = self._publish(retry)
        self.assertTrue(result.resumed)
        self.assertEqual(1, result.sent)

    def test_state_is_bound_to_channel(self):
        with self.assertRaises(ChannelStateError):
            load_state(self.state, self.services.settings.cipher, "-100999")

    def test_preview_excludes_pending_and_blocked(self):
        self.services.entries.save(
            [
                entry("approved"),
                entry("pending", status="pending"),
                entry("blocked", security={"publication_blocked": True}),
            ]
        )
        result = preview(
            self.services.entries,
            self.state,
            self.services.settings.cipher,
            self.services.settings.channel_id,
        )
        self.assertEqual(1, result.eligible)


if __name__ == "__main__":
    unittest.main()
