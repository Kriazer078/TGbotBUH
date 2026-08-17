import os
import unittest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

from bot.handlers.user_handlers import cmd_send_news
from bot.main import register_weekly_digest_job
from bot.services.news_digest_service import DigestItem, NewsCandidate, format_digest


class FakeScheduler:
    def __init__(self):
        self.calls = []

    def add_job(self, *args, **kwargs):
        self.calls.append((args, kwargs))


class SchedulerWiringTests(unittest.TestCase):
    def test_registers_monday_job_at_0930_almaty(self):
        scheduler = FakeScheduler()

        register_weekly_digest_job(object(), scheduler)

        args, kwargs = scheduler.calls[0]
        self.assertEqual(args[1], "cron")
        self.assertEqual(kwargs["id"], "weekly_business_digest")
        self.assertEqual(kwargs["day_of_week"], "mon")
        self.assertEqual(kwargs["hour"], 9)
        self.assertEqual(kwargs["minute"], 30)
        self.assertEqual(kwargs["timezone"], "Asia/Almaty")


class FakeUser:
    def __init__(self, user_id):
        self.id = user_id


class FakeMessage:
    def __init__(self, user_id, text="/send_news"):
        self.from_user = FakeUser(user_id)
        self.text = text
        self.answers = []
        self.bot = object()

    async def answer(self, text, **kwargs):
        self.answers.append((text, kwargs))


class SendNewsCommandTests(unittest.IsolatedAsyncioTestCase):
    async def test_rejects_non_admin(self):
        message = FakeMessage(10)

        with patch.dict(os.environ, {"ADMIN_ID": "20"}):
            await cmd_send_news(message)

        self.assertIn("нет прав", message.answers[0][0])

    async def test_default_mode_sends_preview_only_to_admin(self):
        message = FakeMessage(20)
        preview = "<b>Проверенная подборка</b>"

        with patch.dict(os.environ, {"ADMIN_ID": "20"}), patch(
            "bot.services.news_digest_service.build_digest",
            AsyncMock(return_value=(preview, ["1"])),
        ):
            await cmd_send_news(message)

        self.assertEqual(message.answers[-1][0], preview)
        self.assertEqual(message.answers[-1][1]["parse_mode"], "HTML")

    async def test_preview_splits_long_digest_for_telegram(self):
        message = FakeMessage(20)
        now = datetime(2026, 8, 17, 3, 30, tzinfo=timezone.utc)
        items = [
            DigestItem(
                NewsCandidate(
                    str(index),
                    f"Новость {index}",
                    "Исходный текст",
                    f"https://example.com/{index}",
                    "Источник",
                    now,
                ),
                "Подробная сводка " * 100,
                "Практическое значение " * 30,
            )
            for index in range(5)
        ]
        preview = format_digest(items, "Общий вывод", greeting="Доброе утро")

        with patch.dict(os.environ, {"ADMIN_ID": "20"}), patch(
            "bot.services.news_digest_service.build_digest",
            AsyncMock(return_value=(preview, [str(index) for index in range(5)])),
        ):
            await cmd_send_news(message)

        preview_messages = message.answers[1:]
        self.assertGreater(len(preview_messages), 1)
        self.assertTrue(all(len(text) <= 3900 for text, _ in preview_messages))
