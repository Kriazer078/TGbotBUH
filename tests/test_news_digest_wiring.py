import os
import unittest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

from aiogram import Dispatcher

from bot.handlers.user_handlers import cmd_send_news
from bot.main import create_app, register_weekly_digest_job, weekly_digest_endpoint
from bot.services.news_digest_service import DigestItem, NewsCandidate, format_digest


class FakeScheduler:
    def __init__(self):
        self.calls = []

    def add_job(self, *args, **kwargs):
        self.calls.append((args, kwargs))


class FakeRequest:
    def __init__(self, secret=None):
        self.headers = {}
        if secret is not None:
            self.headers["X-Internal-Secret"] = secret


class CloudSchedulerEndpointTests(unittest.IsolatedAsyncioTestCase):
    async def test_rejects_when_server_secret_is_missing(self):
        with patch.dict(os.environ, {}, clear=True), patch(
            "bot.services.news_digest_service.publish_digest", AsyncMock()
        ) as publish:
            response = await weekly_digest_endpoint(FakeRequest("provided"), object())

        self.assertEqual(response.status, 401)
        publish.assert_not_awaited()

    async def test_valid_request_publishes_to_configured_topic(self):
        fixed_now = datetime(2026, 8, 17, 4, 30, tzinfo=timezone.utc)
        environment = {
            "INTERNAL_TICK_SECRET": "expected",
            "NEWS_TARGET_CHAT_ID": "-1002318310296",
            "NEWS_TARGET_THREAD_ID": "1",
            "NEWS_TIMEZONE": "Asia/Almaty",
        }
        with patch.dict(os.environ, environment, clear=True), patch(
            "bot.services.news_digest_service.publish_digest", AsyncMock(return_value=True)
        ) as publish:
            response = await weekly_digest_endpoint(
                FakeRequest("expected"), object(), now=fixed_now
            )

        self.assertEqual(response.status, 200)
        publish.assert_awaited_once_with(
            unittest.mock.ANY,
            chat_id=-1002318310296,
            thread_id=1,
            publication_key="2026-08-17",
            test_mode=False,
        )

    async def test_publication_failure_returns_500(self):
        with patch.dict(os.environ, {"INTERNAL_TICK_SECRET": "expected"}), patch(
            "bot.services.news_digest_service.publish_digest",
            AsyncMock(side_effect=RuntimeError("failure")),
        ):
            response = await weekly_digest_endpoint(FakeRequest("expected"), object())

        self.assertEqual(response.status, 500)

    async def test_app_registers_internal_post_route(self):
        app = create_app(object(), Dispatcher())

        routes = {(route.method, route.resource.canonical) for route in app.router.routes()}
        self.assertIn(("POST", "/internal/weekly-digest"), routes)

    async def test_rejects_missing_request_secret(self):
        with patch.dict(os.environ, {"INTERNAL_TICK_SECRET": "expected"}), patch(
            "bot.services.news_digest_service.publish_digest", AsyncMock()
        ) as publish:
            response = await weekly_digest_endpoint(FakeRequest(), object())

        self.assertEqual(response.status, 401)
        publish.assert_not_awaited()

    async def test_rejects_incorrect_request_secret(self):
        with patch.dict(os.environ, {"INTERNAL_TICK_SECRET": "expected"}), patch(
            "bot.services.news_digest_service.publish_digest", AsyncMock()
        ) as publish:
            response = await weekly_digest_endpoint(FakeRequest("incorrect"), object())

        self.assertEqual(response.status, 401)
        publish.assert_not_awaited()


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
