import os
import unittest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

from aiogram import Dispatcher

from bot.handlers.user_handlers import cmd_send_news
from bot.main import (
    create_app,
    primary_documents_endpoint,
    register_weekly_digest_job,
    weekly_digest_endpoint,
)
from bot.services.news_digest_service import DigestItem, NewsCandidate, format_digest
from bot.services.primary_documents_broadcast import (
    parse_broadcast_targets,
    publish_primary_documents,
)


class PrimaryDocumentsTargetTests(unittest.TestCase):
    def test_parses_internal_chat_ids_with_general_topic(self):
        targets = parse_broadcast_targets("3918833089/1 3031822455/1")

        self.assertEqual(targets, [(-1003918833089, None), (-1003031822455, None)])

    def test_rejects_duplicate_or_malformed_targets(self):
        with self.assertRaises(ValueError):
            parse_broadcast_targets("3918833089/1 3918833089/1")

        with self.assertRaises(ValueError):
            parse_broadcast_targets("3918833089/not-a-topic")


class PrimaryDocumentsPublishingTests(unittest.IsolatedAsyncioTestCase):
    async def test_sends_one_photo_to_each_allowlisted_general_topic(self):
        bot = AsyncMock()
        environment = {
            "PRIMARY_DOCUMENT_TARGETS": "3918833089/1 3031822455/1",
            "PRIMARY_DOCUMENT_IMAGE_PATH": "C:/safe/primary-documents.png",
        }

        with patch.dict(os.environ, environment, clear=True), patch(
            "bot.services.primary_documents_broadcast.FSInputFile",
            side_effect=lambda path: f"file:{path}",
        ):
            result = await publish_primary_documents(bot)

        self.assertEqual(result, {"sent": 2, "failed": 0})
        bot.send_photo.assert_has_awaits([
            unittest.mock.call(
                chat_id=-1003918833089,
                message_thread_id=None,
                photo="file:C:/safe/primary-documents.png",
            ),
            unittest.mock.call(
                chat_id=-1003031822455,
                message_thread_id=None,
                photo="file:C:/safe/primary-documents.png",
            ),
        ])


class FakeScheduler:
    def __init__(self):
        self.calls = []

    def add_job(self, *args, **kwargs):
        self.calls.append((args, kwargs))

    def start(self):
        return None


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

    async def test_zero_thread_id_publishes_to_general_chat(self):
        environment = {
            "INTERNAL_TICK_SECRET": "expected",
            "NEWS_TARGET_CHAT_ID": "-1002318310296",
            "NEWS_TARGET_THREAD_ID": "0",
        }
        with patch.dict(os.environ, environment, clear=True), patch(
            "bot.services.news_digest_service.publish_digest", AsyncMock(return_value=True)
        ) as publish:
            response = await weekly_digest_endpoint(FakeRequest("expected"), object())

        self.assertEqual(response.status, 200)
        self.assertIsNone(publish.await_args.kwargs["thread_id"])

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
        self.assertIn(("POST", "/internal/primary-documents"), routes)

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


class PrimaryDocumentsEndpointTests(unittest.IsolatedAsyncioTestCase):
    async def test_authorized_request_starts_allowlisted_photo_delivery(self):
        with patch.dict(os.environ, {"INTERNAL_TICK_SECRET": "expected"}, clear=True), patch(
            "bot.services.primary_documents_broadcast.publish_primary_documents",
            AsyncMock(return_value={"sent": 44, "failed": 0}),
        ) as publish:
            response = await primary_documents_endpoint(FakeRequest("expected"), object())

        self.assertEqual(response.status, 200)
        self.assertTrue(publish.awaited)

    async def test_rejects_request_without_internal_secret(self):
        with patch.dict(os.environ, {"INTERNAL_TICK_SECRET": "expected"}, clear=True), patch(
            "bot.services.primary_documents_broadcast.publish_primary_documents",
            AsyncMock(),
        ) as publish:
            response = await primary_documents_endpoint(FakeRequest(), object())

        self.assertEqual(response.status, 401)
        publish.assert_not_awaited()

    async def test_test_endpoint_uses_only_separate_test_chat(self):
        environment = {
            "INTERNAL_TICK_SECRET": "expected",
            "PRIMARY_DOCUMENT_TEST_TARGET": "2318310296/1",
        }
        with patch.dict(os.environ, environment, clear=True), patch(
            "bot.services.primary_documents_broadcast.publish_primary_documents",
            AsyncMock(return_value={"sent": 1, "failed": 0}),
        ) as publish:
            from bot.main import primary_documents_test_endpoint

            bot = object()
            response = await primary_documents_test_endpoint(FakeRequest("expected"), bot)

        self.assertEqual(response.status, 200)
        publish.assert_awaited_once_with(bot, raw_targets="2318310296/1")


class SchedulerWiringTests(unittest.IsolatedAsyncioTestCase):
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

    async def test_startup_does_not_register_weekly_in_process_job(self):
        scheduler = FakeScheduler()
        bot = AsyncMock()

        with patch("bot.main.scheduler", scheduler), patch(
            "bot.rag.firebase_db.init_firebase", return_value=False
        ), patch("bot.main.register_weekly_digest_job") as register_weekly:
            from bot.main import on_startup

            await on_startup(bot)

        register_weekly.assert_not_called()
        registered_ids = [kwargs["id"] for _, kwargs in scheduler.calls]
        self.assertEqual(registered_ids, ["news_update"])


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
