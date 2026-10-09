import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from telegram.ext import CallbackQueryHandler, TypeHandler

from telegram_geo_report_bot.application import build_application, text_dispatch

from helpers import make_services


class ApplicationTests(unittest.TestCase):
    def test_application_registers_global_guard_and_admin_user_flows(self):
        with tempfile.TemporaryDirectory() as temporary:
            services = make_services(Path(temporary))
            application = build_application(services)
            self.assertIn(-1, application.handlers)
            self.assertTrue(any(isinstance(handler, TypeHandler) for handler in application.handlers[-1]))
            callback_handlers = [
                handler
                for handlers in application.handlers.values()
                for handler in handlers
                if isinstance(handler, CallbackQueryHandler)
            ]
            patterns = " ".join(str(handler.pattern.pattern) for handler in callback_handlers)
            self.assertIn("admin:", patterns)
            self.assertIn("cat:", patterns)
            self.assertIn("main:", patterns)


class ApplicationDispatchTests(unittest.IsolatedAsyncioTestCase):
    async def test_free_text_outside_a_flow_returns_to_main_menu(self):
        update = SimpleNamespace()
        context = SimpleNamespace()
        with (
            patch(
                "telegram_geo_report_bot.application.handle_admin_text",
                new=AsyncMock(return_value=False),
            ),
            patch(
                "telegram_geo_report_bot.application.handle_text",
                new=AsyncMock(return_value=False),
            ),
            patch(
                "telegram_geo_report_bot.application.show_main_menu",
                new=AsyncMock(),
            ) as show_main_menu,
        ):
            await text_dispatch(update, context)

        show_main_menu.assert_awaited_once_with(update, context)


if __name__ == "__main__":
    unittest.main()
