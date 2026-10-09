import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from telegram_geo_report_bot.handlers.common import format_user_entry, main_keyboard
from telegram_geo_report_bot.handlers.user import (
    _back_or_cancel_button,
    _edit_keyboard,
    _flow_message,
    _progress,
    _rate_allowed,
    handle_flow_callback,
    handle_text,
)


class UserUiTests(unittest.IsolatedAsyncioTestCase):
    async def test_flow_message_reuses_the_active_bot_prompt(self):
        edited = SimpleNamespace(message_id=42)
        bot = SimpleNamespace(edit_message_text=AsyncMock(return_value=edited))
        reply_text = AsyncMock()
        update = SimpleNamespace(
            callback_query=None,
            effective_chat=SimpleNamespace(id=7),
            effective_message=SimpleNamespace(reply_text=reply_text),
        )
        context = SimpleNamespace(user_data={"flow_message_id": 41}, bot=bot)

        message = await _flow_message(update, context, "Nächster Schritt")

        self.assertIs(message, edited)
        bot.edit_message_text.assert_awaited_once_with(
            chat_id=7,
            message_id=41,
            text="Nächster Schritt",
            reply_markup=None,
        )
        reply_text.assert_not_awaited()
        self.assertEqual(42, context.user_data["flow_message_id"])

    async def test_address_input_is_removed_after_it_was_processed(self):
        delete = AsyncMock()
        update = SimpleNamespace(
            callback_query=None,
            effective_user=SimpleNamespace(id=123),
            effective_message=SimpleNamespace(
                text="Musterstraße 12, 12345 Musterstadt",
                delete=delete,
            ),
        )
        context = SimpleNamespace(user_data={"flow_step": "address"})
        services = SimpleNamespace(
            settings=SimpleNamespace(
                config=SimpleNamespace(
                    rate_limits=SimpleNamespace(
                        enabled=True, per_minute=3, per_hour=10
                    )
                )
            ),
            admins=SimpleNamespace(contains=lambda _user_id: True),
            geocoder=SimpleNamespace(
                search=lambda _query: [
                    {
                        "label": "Musterstraße 12, Musterstadt",
                        "lat": "52.0",
                        "lon": "13.0",
                        "address": {"street": "Musterstraße 12"},
                    }
                ]
            ),
        )
        with (
            patch(
                "telegram_geo_report_bot.handlers.user.get_services",
                return_value=services,
            ),
            patch(
                "telegram_geo_report_bot.handlers.user._flow_message",
                new=AsyncMock(),
            ) as flow_message,
        ):
            consumed = await handle_text(update, context)

        self.assertTrue(consumed)
        delete.assert_awaited_once_with()
        flow_message.assert_awaited_once()
        self.assertIn("address_results", context.user_data)

    async def test_failed_address_cleanup_does_not_interrupt_the_flow(self):
        delete = AsyncMock(side_effect=RuntimeError("not permitted"))
        update = SimpleNamespace(
            callback_query=None,
            effective_user=SimpleNamespace(id=123),
            effective_message=SimpleNamespace(
                text="Musterstraße 12, 12345 Musterstadt",
                delete=delete,
            ),
        )
        context = SimpleNamespace(user_data={"flow_step": "address"})
        services = SimpleNamespace(
            settings=SimpleNamespace(
                config=SimpleNamespace(
                    rate_limits=SimpleNamespace(
                        enabled=True, per_minute=3, per_hour=10
                    )
                )
            ),
            admins=SimpleNamespace(contains=lambda _user_id: True),
            geocoder=SimpleNamespace(search=lambda _query: []),
        )
        with (
            patch(
                "telegram_geo_report_bot.handlers.user.get_services",
                return_value=services,
            ),
            patch(
                "telegram_geo_report_bot.handlers.user._flow_message",
                new=AsyncMock(),
            ) as flow_message,
        ):
            consumed = await handle_text(update, context)

        self.assertTrue(consumed)
        delete.assert_awaited_once_with()
        flow_message.assert_awaited_once()

    def test_main_menu_and_edit_menu_use_clear_emoji_labels(self):
        main_labels = [row[0].text for row in main_keyboard(True, has_result_link=True)]
        self.assertIn("🔐 Adminbereich", main_labels)
        self.assertIn("🗺️ Veröffentlichte Meldungen", main_labels)
        self.assertIn("🔐 Datenverarbeitung", main_labels)
        self.assertIn("ℹ️ Hilfe & Kontakt", main_labels)

        callbacks = [
            row[0].callback_data for row in _edit_keyboard({"photo": "photo.jpg"})
        ]
        self.assertEqual(
            [
                "entry:edit:address",
                "entry:edit:category",
                "entry:edit:amount",
                "entry:edit:description",
                "entry:edit:photo",
                "entry:review",
            ],
            callbacks,
        )
        self.assertIn("ersetzen/entfernen", _edit_keyboard({"photo": "x"})[4][0].text)
        self.assertIn("hinzufügen", _edit_keyboard({"photo": None})[4][0].text)

    def test_edit_steps_go_back_to_review_instead_of_cancelling_report(self):
        edit_context = SimpleNamespace(user_data={"editing_field": "amount"})
        normal_context = SimpleNamespace(user_data={})
        self.assertEqual(
            "entry:review", _back_or_cancel_button(edit_context).callback_data
        )
        self.assertEqual(
            "entry:cancel", _back_or_cancel_button(normal_context).callback_data
        )
        self.assertEqual(
            "entry:back:category",
            _back_or_cancel_button(normal_context, "category").callback_data,
        )

    def test_progress_uses_the_production_style_indicator(self):
        self.assertEqual("🟩🟩⬜⬜⬜ 2/5", _progress(2))

    async def test_disabled_rate_limits_do_not_record_or_block_actions(self):
        limits = SimpleNamespace(enabled=False, per_minute=3, per_hour=10)
        config = SimpleNamespace(rate_limits=limits)
        services = SimpleNamespace(
            settings=SimpleNamespace(config=config),
            admins=SimpleNamespace(contains=lambda _user_id: False),
        )
        update = SimpleNamespace(effective_user=SimpleNamespace(id=123))
        with patch(
            "telegram_geo_report_bot.handlers.user.check_and_record_rate_action"
        ) as check:
            allowed = await _rate_allowed(update, services)

        self.assertTrue(allowed)
        check.assert_not_called()

    async def test_amount_edit_updates_draft_and_returns_to_review(self):
        query = SimpleNamespace(data="amount:1", answer=AsyncMock())
        update = SimpleNamespace(callback_query=query)
        context = SimpleNamespace(
            user_data={
                "entry": {"amount": "1"},
                "editing_field": "amount",
                "flow_step": "amount",
            }
        )
        config = SimpleNamespace(amount_options=("1", "10+", "20+"))
        services = SimpleNamespace(settings=SimpleNamespace(config=config))
        with (
            patch(
                "telegram_geo_report_bot.handlers.user.get_services",
                return_value=services,
            ),
            patch(
                "telegram_geo_report_bot.handlers.user._show_review",
                new=AsyncMock(),
            ) as show_review,
        ):
            await handle_flow_callback(update, context)

        self.assertEqual("10+", context.user_data["entry"]["amount"])
        self.assertEqual("review", context.user_data["flow_step"])
        self.assertNotIn("editing_field", context.user_data)
        show_review.assert_awaited_once_with(update, context)

    async def test_each_initial_back_button_returns_to_the_previous_step(self):
        services = SimpleNamespace()
        routes = {
            "address": "_show_address_question",
            "category": "_show_category_question",
            "subcategory": "_show_subcategory_question",
            "amount": "_show_amount_question",
            "description": "_show_description_question",
        }
        for target, helper_name in routes.items():
            with self.subTest(target=target):
                query = SimpleNamespace(
                    data=f"entry:back:{target}", answer=AsyncMock()
                )
                update = SimpleNamespace(callback_query=query)
                context = SimpleNamespace(user_data={})
                with (
                    patch(
                        "telegram_geo_report_bot.handlers.user.get_services",
                        return_value=services,
                    ),
                    patch(
                        f"telegram_geo_report_bot.handlers.user.{helper_name}",
                        new=AsyncMock(),
                    ) as helper,
                ):
                    await handle_flow_callback(update, context)
                helper.assert_awaited_once_with(update, context)

    def test_user_review_hides_internal_fields(self):
        config = SimpleNamespace(
            category_labels={"objects": "Objekte"},
            subcategory_labels={"small": "Kleine Objekte"},
        )
        services = SimpleNamespace(settings=SimpleNamespace(config=config))
        value = {
            "id": "internal-id",
            "status": "pending",
            "address": {
                "street": "Teststraße 1",
                "postcode": "12345",
                "city": "Teststadt",
            },
            "category": "objects",
            "subcategory": "small",
            "amount": "1",
            "description": "Hinweis",
            "security": {"publication_blocked": True},
            "validation": {"type": "manual"},
            "photo": None,
        }

        text = format_user_entry(value, services)

        self.assertNotIn("internal-id", text)
        self.assertNotIn("Status", text)
        self.assertNotIn("Validierung", text)
        self.assertNotIn("Sicherheit", text)

    async def test_address_edit_preserves_entry_and_updates_location(self):
        result = {
            "lat": "52.51",
            "lon": "13.42",
            "address": {"street": "Neue Straße 1", "city": "Berlin"},
        }
        original = {"id": "draft", "address": {"street": "Alt 1"}}
        query = SimpleNamespace(data="addr:0", answer=AsyncMock())
        update = SimpleNamespace(callback_query=query)
        context = SimpleNamespace(
            user_data={
                "entry": original,
                "editing_field": "address",
                "flow_step": "address",
                "address_results": [result],
            }
        )
        services = SimpleNamespace()
        with (
            patch(
                "telegram_geo_report_bot.handlers.user.get_services",
                return_value=services,
            ),
            patch(
                "telegram_geo_report_bot.handlers.user._show_review",
                new=AsyncMock(),
            ) as show_review,
        ):
            await handle_flow_callback(update, context)

        self.assertIs(original, context.user_data["entry"])
        self.assertEqual(52.51, original["lat"])
        self.assertEqual(13.42, original["lon"])
        self.assertEqual("Neue Straße 1", original["address"]["street"])
        self.assertNotIn("address_results", context.user_data)
        show_review.assert_awaited_once_with(update, context)


if __name__ == "__main__":
    unittest.main()
