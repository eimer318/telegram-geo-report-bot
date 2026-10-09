import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from telegram_geo_report_bot.handlers.admin import (
    _show_all,
    _show_review,
    _start_review,
    handle_admin_callback,
    handle_admin_text,
    show_dashboard,
)
from telegram_geo_report_bot.handlers.common import (
    format_admin_entry,
    replace_admin_card,
)

from helpers import entry, make_services


class AdminCardTests(unittest.IsolatedAsyncioTestCase):
    async def test_photo_text_and_buttons_are_sent_as_one_replacement_card(self):
        sent = SimpleNamespace(message_id=30)
        bot = SimpleNamespace(
            send_photo=AsyncMock(return_value=sent),
            send_message=AsyncMock(),
            delete_message=AsyncMock(),
        )
        context = SimpleNamespace(
            user_data={"admin_card_message_id": 10},
            bot=bot,
        )
        update = SimpleNamespace(
            effective_chat=SimpleNamespace(id=7),
            callback_query=SimpleNamespace(message=SimpleNamespace(message_id=11)),
        )

        result = await replace_admin_card(
            update, context, "Eintrag", [], photo=b"jpeg"
        )

        self.assertIs(result, sent)
        bot.send_photo.assert_awaited_once()
        bot.send_message.assert_not_awaited()
        deleted = {
            call.kwargs["message_id"] for call in bot.delete_message.await_args_list
        }
        self.assertEqual({10, 11}, deleted)
        self.assertEqual(30, context.user_data["admin_card_message_id"])

    async def test_failed_new_card_keeps_the_previous_card(self):
        bot = SimpleNamespace(
            send_photo=AsyncMock(side_effect=RuntimeError("send failed")),
            send_message=AsyncMock(),
            delete_message=AsyncMock(),
        )
        context = SimpleNamespace(
            user_data={"admin_card_message_id": 10}, bot=bot
        )
        update = SimpleNamespace(
            effective_chat=SimpleNamespace(id=7), callback_query=None
        )

        with self.assertRaises(RuntimeError):
            await replace_admin_card(update, context, "Eintrag", [], photo=b"jpeg")

        bot.delete_message.assert_not_awaited()
        self.assertEqual(10, context.user_data["admin_card_message_id"])


class AdminFlowTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.services = make_services(Path(self.temporary.name), channel_id="")
        self.context = SimpleNamespace(
            user_data={},
            application=SimpleNamespace(bot_data={"services": self.services}),
        )
        self.update = SimpleNamespace(
            effective_user=SimpleNamespace(id=7),
            effective_chat=SimpleNamespace(id=7),
            callback_query=None,
        )

    def test_admin_formatter_never_displays_security_details(self):
        value = entry(
            "blocked",
            status="pending",
            security={
                "publication_blocked": True,
                "reason_codes": ["shell_command_substitution"],
            },
        )
        text = format_admin_entry(value, self.services)
        self.assertNotIn("Sicherheit", text)
        self.assertNotIn("shell_command_substitution", text)
        self.assertNotIn("gesperrt", text.lower())

    def test_admin_photo_caption_stays_within_telegram_limit(self):
        value = entry(
            "x" * 200,
            address={
                "street": "L" * 500,
                "postcode": "12345",
                "city": "M" * 200,
            },
            description="D" * 500,
            validation={
                "type": "automatic",
                "reference_entry_id": "r" * 300,
                "distance_m": 9.5,
            },
        )
        text = format_admin_entry(value, self.services, notice="N" * 500)
        self.assertLessEqual(len(text), 1024)

    async def test_dashboard_contains_no_security_summary(self):
        self.services.admins.add(7)
        with patch(
            "telegram_geo_report_bot.handlers.admin._show_card", new=AsyncMock()
        ) as show_card:
            await show_dashboard(self.update, self.context)

        text = show_card.await_args.args[2]
        self.assertNotIn("Sicherheit", text)
        self.assertIn("Offen:", text)

    async def test_unauthorized_user_cannot_open_admin_area(self):
        query = SimpleNamespace(
            data="main:admin",
            answer=AsyncMock(),
            message=SimpleNamespace(message_id=1),
        )
        update = SimpleNamespace(
            effective_user=SimpleNamespace(id=999),
            effective_chat=SimpleNamespace(id=7),
            callback_query=query,
        )
        with patch(
            "telegram_geo_report_bot.handlers.admin.replace_admin_card",
            new=AsyncMock(),
        ) as replace_card:
            await handle_admin_callback(update, self.context)

        self.assertIn("Kein Admin-Zugriff", replace_card.await_args.args[2])

    async def test_admin_management_validates_and_applies_ids(self):
        self.services.admins.add(7)
        self.context.user_data["admin_action"] = "add"
        update = SimpleNamespace(
            effective_user=SimpleNamespace(id=7),
            effective_chat=SimpleNamespace(id=7),
            effective_message=SimpleNamespace(text="ungueltig"),
            callback_query=None,
        )
        with patch(
            "telegram_geo_report_bot.handlers.admin._show_card", new=AsyncMock()
        ) as show_card:
            consumed = await handle_admin_text(update, self.context)
        self.assertTrue(consumed)
        self.assertIn("positive numerische Telegram-ID", show_card.await_args.args[2])

        update.effective_message.text = "8"
        with patch(
            "telegram_geo_report_bot.handlers.admin._show_admin_management",
            new=AsyncMock(),
        ):
            await handle_admin_text(update, self.context)
        self.assertTrue(self.services.admins.contains(8))

        self.context.user_data["admin_action"] = "remove"
        with patch(
            "telegram_geo_report_bot.handlers.admin._show_admin_management",
            new=AsyncMock(),
        ):
            await handle_admin_text(update, self.context)
        self.assertFalse(self.services.admins.contains(8))
        self.assertTrue(self.services.admins.contains(7))

    async def test_all_entries_navigation_and_status_change_keep_focus(self):
        self.services.admins.add(7)
        for identifier in ("one", "two", "three"):
            self.services.entries.append(entry(identifier))
        with patch(
            "telegram_geo_report_bot.handlers.admin._show_card", new=AsyncMock()
        ) as show_card:
            await _show_all(self.update, self.context, refresh=True)
            first_callbacks = [
                button.callback_data
                for row in show_card.await_args.args[3]
                for button in row
            ]
            self.assertIn("all:next", first_callbacks)
            self.assertNotIn("all:prev", first_callbacks)

            self.context.user_data["all_index"] = 1
            await _show_all(self.update, self.context)
            middle_callbacks = [
                button.callback_data
                for row in show_card.await_args.args[3]
                for button in row
            ]
            self.assertIn("all:next", middle_callbacks)
            self.assertIn("all:prev", middle_callbacks)

            focused_id = self.context.user_data["all_entries"][1]["id"]
            query = SimpleNamespace(
                data=f"status:rejected:{focused_id}",
                answer=AsyncMock(),
                message=SimpleNamespace(message_id=2),
            )
            update = SimpleNamespace(
                effective_user=SimpleNamespace(id=7),
                effective_chat=SimpleNamespace(id=7),
                callback_query=query,
            )
            await handle_admin_callback(update, self.context)

        focused = self.context.user_data["all_entries"][
            self.context.user_data["all_index"]
        ]
        self.assertEqual(focused_id, focused["id"])
        self.assertEqual("rejected", focused["status"])

    async def test_review_progress_is_stable_and_blocked_entry_is_neutral(self):
        entries = [
            entry("one", status="pending"),
            entry(
                "two",
                status="pending",
                security={
                    "publication_blocked": True,
                    "reason_codes": ["template_expression"],
                },
            ),
            entry("three", status="pending"),
        ]
        for value in entries:
            self.services.entries.append(value)
        _start_review(self.context, self.services)

        with patch(
            "telegram_geo_report_bot.handlers.admin._show_card", new=AsyncMock()
        ) as show_card:
            await _show_review(self.update, self.context)
            self.assertIn("Review (1/3)", show_card.await_args.args[2])

            self.context.user_data["review_entries"] = entries[1:]
            self.context.user_data["review_processed"] = 1
            await _show_review(self.update, self.context)
            text = show_card.await_args.args[2]
            keyboard = show_card.await_args.args[3]
            self.assertIn("Review (2/3)", text)
            self.assertNotIn("template_expression", text)
            callbacks = [button.callback_data for row in keyboard for button in row]
            self.assertNotIn("review:approve:two", callbacks)
            self.assertIn("admin:description:two", callbacks)
            self.assertIn("review:skip:two", callbacks)

            self.context.user_data["review_entries"] = entries[2:]
            self.context.user_data["review_processed"] = 2
            await _show_review(self.update, self.context)
            self.assertIn("Review (3/3)", show_card.await_args.args[2])


if __name__ == "__main__":
    unittest.main()
