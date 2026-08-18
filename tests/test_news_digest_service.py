import json
import unittest
from datetime import datetime, timezone

from bot.services.news_digest_service import (
    DigestItem,
    NewsCandidate,
    build_digest,
    digest_schedule,
    format_digest,
    normalize_candidates,
    fetch_telegram_news,
    parse_telegram_feed,
    parse_ranked_items,
    publish_digest,
    rank_and_summarize,
    search_additional_news,
    split_digest,
)


class TelegramFeedTests(unittest.IsolatedAsyncioTestCase):
    HTML = """
    <div class="tgme_widget_message" data-post="prg_jur/101">
      <div class="tgme_widget_message_text">НБК изменил правила для банков<br>Изменения влияют на бизнес.</div>
      <time datetime="2026-08-18T03:15:00+00:00"></time>
    </div>
    <div class="tgme_widget_message" data-post="prg_jur/102">
      <div class="tgme_widget_message_text">Сообщение без времени</div>
    </div>
    """

    def test_parses_exact_timestamp_text_and_canonical_post_url(self):
        items = parse_telegram_feed(self.HTML, "prg_jur", "ZANGER | PRG")

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["article_id"], "telegram:prg_jur/101")
        self.assertEqual(items[0]["url"], "https://t.me/prg_jur/101")
        self.assertEqual(items[0]["date"], "2026-08-18T03:15:00+00:00")
        self.assertEqual(items[0]["source"], "ZANGER | PRG")
        self.assertIn("Изменения влияют", items[0]["text"])

    async def test_fetches_both_channels_independently(self):
        requested = []

        class Response:
            def __init__(self, text):
                self.text = text

            def raise_for_status(self):
                return None

        def getter(url, **kwargs):
            requested.append(url)
            channel = url.rsplit("/", 1)[-1]
            return Response(TelegramFeedTests.HTML.replace("prg_jur", channel))

        items = await fetch_telegram_news(http_get=getter)

        self.assertEqual(
            requested,
            ["https://t.me/s/prg_jur", "https://t.me/s/commentariuskz"],
        )
        self.assertEqual(len(items), 2)


class NormalizeCandidatesTests(unittest.TestCase):
    def test_keeps_only_verified_items_from_last_24_hours(self):
        now = datetime(2026, 8, 17, 3, 30, tzinfo=timezone.utc)
        raw = [
            {
                "article_id": "new",
                "title": "Новая мера для бизнеса",
                "text": "Подробное описание события",
                "url": "https://nationalbank.kz/news/1",
                "source": "НБК",
                "date": "2026-08-16T04:00:00Z",
            },
            {
                "article_id": "edge",
                "title": "Граничная новость",
                "text": "Подробное описание события",
                "url": "https://stat.gov.kz/news/2",
                "source": "БНС",
                "date": "2026-08-16T03:30:00Z",
            },
            {
                "article_id": "old",
                "title": "Старая новость",
                "text": "Подробное описание события",
                "url": "https://kgd.gov.kz/news/3",
                "source": "КГД",
                "date": "2026-08-16T03:29:59Z",
            },
            {
                "article_id": "bad",
                "title": "Без ссылки",
                "text": "Описание",
                "url": "",
                "source": "КГД",
                "date": "2026-08-17T01:00:00Z",
            },
        ]

        result = normalize_candidates(raw, now=now)

        self.assertEqual([item.article_id for item in result], ["new", "edge"])

    def test_deduplicates_normalized_urls_and_similar_titles(self):
        now = datetime(2026, 8, 17, 3, 30, tzinfo=timezone.utc)
        raw = [
            {
                "article_id": "a",
                "title": "НБК сохранил базовую ставку",
                "text": "Первое описание",
                "url": "https://nationalbank.kz/news/rate?utm_source=x",
                "source": "НБК",
                "date": "2026-08-17T01:00:00Z",
            },
            {
                "article_id": "b",
                "title": "НБК сохранил базовую ставку!",
                "text": "Более длинное описание решения Национального банка",
                "url": "https://nationalbank.kz/news/rate",
                "source": "НБК",
                "date": "2026-08-17T01:05:00Z",
            },
        ]

        result = normalize_candidates(raw, now=now)

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].article_id, "b")

    def test_treats_source_date_without_timezone_as_almaty_time(self):
        now = datetime(2026, 8, 17, 3, 30, tzinfo=timezone.utc)
        raw = [
            {
                "article_id": "edge",
                "title": "Новость на границе",
                "text": "Описание",
                "url": "https://stat.gov.kz/news/edge",
                "source": "БНС",
                "date": "2026-08-16 08:30",
            },
            {
                "article_id": "old",
                "title": "Новость старше суток",
                "text": "Описание",
                "url": "https://stat.gov.kz/news/old",
                "source": "БНС",
                "date": "2026-08-16 08:29",
            },
        ]

        result = normalize_candidates(raw, now=now)

        self.assertEqual([item.article_id for item in result], ["edge"])

    def test_rejects_date_without_publication_time(self):
        now = datetime(2026, 8, 17, 3, 30, tzinfo=timezone.utc)
        raw = [{
            "article_id": "date-only",
            "title": "Новость без точного времени",
            "text": "Описание",
            "url": "https://stat.gov.kz/news/date-only",
            "source": "БНС",
            "date": "2026-08-17",
        }]

        result = normalize_candidates(raw, now=now)

        self.assertEqual(result, [])

    def test_rejects_publication_time_without_calendar_date(self):
        now = datetime(2026, 8, 17, 3, 30, tzinfo=timezone.utc)
        raw = [{
            "article_id": "time-only",
            "title": "Новость без даты",
            "text": "Описание",
            "url": "https://stat.gov.kz/news/time-only",
            "source": "БНС",
            "date": "08:00",
        }]

        result = normalize_candidates(raw, now=now)

        self.assertEqual(result, [])


class DigestFormattingTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 8, 17, 3, 30, tzinfo=timezone.utc)
        self.candidates = [
            NewsCandidate(
                str(index),
                f"Новость {index}",
                "Исходный текст",
                f"https://example.com/{index}",
                "Источник",
                self.now,
                index >= 4,
            )
            for index in range(1, 7)
        ]

    def test_parser_rejects_unknown_urls_and_limits_world_news(self):
        payload = json.dumps(
            {
                "items": [
                    {
                        "url": "https://example.com/1",
                        "summary": "Факт 1.",
                        "importance": "Важно 1.",
                    },
                    {
                        "url": "https://example.com/4",
                        "summary": "Факт 4.",
                        "importance": "Важно 4.",
                    },
                    {
                        "url": "https://example.com/5",
                        "summary": "Факт 5.",
                        "importance": "Важно 5.",
                    },
                    {
                        "url": "https://example.com/6",
                        "summary": "Факт 6.",
                        "importance": "Важно 6.",
                    },
                    {
                        "url": "https://unknown.example/news",
                        "summary": "Выдумка.",
                        "importance": "Нет.",
                    },
                ],
                "overview": "Главный вывод дня.",
            },
            ensure_ascii=False,
        )

        items, overview = parse_ranked_items(payload, self.candidates)

        self.assertEqual(
            [item.candidate.article_id for item in items],
            ["1", "4", "5"],
        )
        self.assertEqual(overview, "Главный вывод дня.")

    def test_format_contains_context_importance_source_and_time(self):
        item = DigestItem(
            self.candidates[0],
            "Произошло важное изменение.",
            "Оно влияет на стоимость финансирования.",
        )

        text = format_digest(
            [item],
            "Рынок ждёт новых решений.",
            greeting="Доброе утро! ☀️",
        )

        self.assertIn("🇰🇿 <b>1. Новость 1</b>", text)
        self.assertIn("<b>Почему важно:</b>", text)
        self.assertIn('<a href="https://example.com/1">Источник</a>', text)
        self.assertIn("· 08:30", text)
        self.assertIn("<b>Коротко о главном:</b>", text)


class RankAndSummarizeTests(unittest.IsolatedAsyncioTestCase):
    async def test_uses_candidate_allow_list_and_parses_response(self):
        now = datetime(2026, 8, 17, 3, 30, tzinfo=timezone.utc)
        candidate = NewsCandidate(
            "1",
            "Решение НБК",
            "Национальный банк опубликовал решение.",
            "https://nationalbank.kz/news/1",
            "НБК",
            now,
        )

        class FakeModels:
            def __init__(self):
                self.contents = ""

            async def generate_content(self, **kwargs):
                self.contents = kwargs["contents"]
                return type(
                    "Response",
                    (),
                    {
                        "text": json.dumps(
                            {
                                "items": [
                                    {
                                        "url": candidate.url,
                                        "summary": "НБК сообщил о решении.",
                                        "importance": "Решение влияет на стоимость денег.",
                                    }
                                ],
                                "overview": "Финансовые условия остаются в центре внимания.",
                            },
                            ensure_ascii=False,
                        )
                    },
                )()

        models = FakeModels()
        fake_client = type("Client", (), {"aio": type("Aio", (), {"models": models})()})()

        items, overview = await rank_and_summarize([candidate], client=fake_client)

        self.assertIn(candidate.url, models.contents)
        self.assertEqual(items[0].candidate.article_id, "1")
        self.assertIn("Финансовые условия", overview)

    async def test_retries_when_gemini_returns_wrong_json_shape(self):
        now = datetime(2026, 8, 17, 3, 30, tzinfo=timezone.utc)
        candidate = NewsCandidate(
            "1",
            "Решение НБК",
            "Подробности",
            "https://nationalbank.kz/news/1",
            "НБК",
            now,
        )

        class FakeModels:
            def __init__(self):
                self.calls = 0

            async def generate_content(self, **kwargs):
                self.calls += 1
                payload = [] if self.calls == 1 else {
                    "items": [
                        {
                            "url": candidate.url,
                            "summary": "Сводка.",
                            "importance": "Важно.",
                        }
                    ],
                    "overview": "Вывод.",
                }
                return type(
                    "Response",
                    (),
                    {"text": json.dumps(payload, ensure_ascii=False)},
                )()

        models = FakeModels()
        fake_client = type(
            "Client",
            (),
            {"aio": type("Aio", (), {"models": models})()},
        )()

        items, _ = await rank_and_summarize([candidate], client=fake_client)

        self.assertEqual(models.calls, 2)
        self.assertEqual(items[0].candidate.article_id, "1")

    async def test_retries_after_gemini_transport_error(self):
        now = datetime(2026, 8, 17, 3, 30, tzinfo=timezone.utc)
        candidate = NewsCandidate(
            "1",
            "Решение НБК",
            "Подробности",
            "https://nationalbank.kz/news/1",
            "НБК",
            now,
        )

        class FakeModels:
            def __init__(self):
                self.calls = 0

            async def generate_content(self, **kwargs):
                self.calls += 1
                if self.calls == 1:
                    raise RuntimeError("temporary network failure")
                return type(
                    "Response",
                    (),
                    {
                        "text": json.dumps({
                            "items": [{
                                "url": candidate.url,
                                "summary": "Сводка.",
                                "importance": "Важно.",
                            }],
                            "overview": "Вывод.",
                        }, ensure_ascii=False)
                    },
                )()

        models = FakeModels()
        fake_client = type(
            "Client",
            (),
            {"aio": type("Aio", (), {"models": models})()},
        )()

        items, _ = await rank_and_summarize([candidate], client=fake_client)

        self.assertEqual(models.calls, 2)
        self.assertEqual(items[0].candidate.article_id, "1")


class DigestOrchestrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_build_uses_fallback_when_primary_has_fewer_than_five(self):
        now = datetime(2026, 8, 17, 3, 30, tzinfo=timezone.utc)
        primary = [
            {
                "article_id": "1",
                "title": "Новость 1",
                "text": "Текст новости 1",
                "url": "https://example.com/1",
                "source": "Источник",
                "date": "2026-08-17T01:00:00Z",
            }
        ]
        fallback = [
            {
                "article_id": str(index),
                "title": f"Новость {index}",
                "text": f"Текст новости {index}",
                "url": f"https://example.com/{index}",
                "source": "Источник",
                "date": "2026-08-17T01:00:00Z",
            }
            for index in range(2, 7)
        ]
        seen_candidates = []

        async def fetcher():
            return primary

        async def fallback_search(_now):
            return fallback

        async def ranker(candidates):
            seen_candidates.extend(candidates)
            return [DigestItem(candidates[0], "Сводка.", "Важно.")], "Вывод."

        text, article_ids = await build_digest(
            now=now,
            fetcher=fetcher,
            fallback_search=fallback_search,
            ranker=ranker,
        )

        self.assertEqual(len(seen_candidates), 6)
        self.assertEqual(article_ids, ["1"])
        self.assertIn("Новость 1", text)

    async def test_build_supplements_local_only_primary_candidates(self):
        now = datetime(2026, 8, 17, 3, 30, tzinfo=timezone.utc)
        titles = [
            "НБК сообщил о базовой ставке",
            "Экспорт Казахстана вырос за месяц",
            "Опубликованы данные по инфляции",
            "Банки изменили условия кредитования",
            "Предприниматели открыли новые производства",
        ]
        primary = [
            {
                "article_id": str(index),
                "title": titles[index - 1],
                "text": "Подробности",
                "url": f"https://example.kz/{index}",
                "source": "Источник",
                "date": "2026-08-17T01:00:00Z",
                "is_world": False,
            }
            for index in range(1, 6)
        ]
        fallback_calls = []

        async def fallback_search(_now):
            fallback_calls.append(True)
            return [
                {
                    "article_id": "world",
                    "title": "Мировая новость",
                    "text": "Подробности",
                    "url": "https://reuters.com/world/1",
                    "source": "Reuters",
                    "date": "2026-08-17T01:00:00Z",
                    "is_world": True,
                }
            ]

        async def fetcher():
            return primary

        async def ranker(candidates):
            return [DigestItem(candidates[0], "Сводка.", "Важно.")], "Вывод."

        await build_digest(
            now=now,
            fetcher=fetcher,
            fallback_search=fallback_search,
            ranker=ranker,
        )

        self.assertEqual(fallback_calls, [True])

    async def test_publish_sends_to_topic_and_records_success(self):
        sent_messages = []
        recorded = []

        class FakeBot:
            async def send_message(self, **kwargs):
                sent_messages.append(kwargs)

        async def builder():
            return "digest", ["1"]

        result = await publish_digest(
            FakeBot(),
            chat_id=-1002318310296,
            thread_id=1,
            publication_key="2026-08-17",
            builder=builder,
            claimer=lambda _key: True,
            recorder=lambda key, ids: recorded.append((key, ids)),
        )

        self.assertTrue(result)
        self.assertEqual(sent_messages[0]["chat_id"], -1002318310296)
        self.assertEqual(sent_messages[0]["message_thread_id"], 1)
        self.assertEqual(recorded, [("2026-08-17", ["1"])])

    async def test_publish_skips_existing_publication(self):
        class FakeBot:
            async def send_message(self, **kwargs):
                raise AssertionError("Telegram must not be called")

        result = await publish_digest(
            FakeBot(),
            chat_id=-1002318310296,
            thread_id=1,
            publication_key="2026-08-17",
            claimer=lambda _key: False,
        )

        self.assertFalse(result)

    async def test_publish_releases_claim_when_build_fails(self):
        released = []

        async def failing_builder():
            raise RuntimeError("build failed")

        with self.assertRaisesRegex(RuntimeError, "build failed"):
            await publish_digest(
                object(),
                chat_id=-1002318310296,
                thread_id=1,
                publication_key="2026-08-17",
                builder=failing_builder,
                claimer=lambda _key: True,
                releaser=lambda key: released.append(key),
            )

        self.assertEqual(released, ["2026-08-17"])

    async def test_publish_keeps_claim_after_partial_send_failure(self):
        released = []
        failures = []

        class FailingBot:
            def __init__(self):
                self.calls = 0

            async def send_message(self, **kwargs):
                self.calls += 1
                if self.calls == 2:
                    raise RuntimeError("telegram failed")

        async def builder():
            return "Первая часть\n\n──────────\n\nВторая часть", ["1"]

        with self.assertRaisesRegex(RuntimeError, "telegram failed"):
            await publish_digest(
                FailingBot(),
                chat_id=-1002318310296,
                thread_id=1,
                publication_key="2026-08-17",
                builder=builder,
                claimer=lambda _key: True,
                releaser=lambda key: released.append(key),
                failure_recorder=lambda key, count, ambiguous: failures.append(
                    (key, count, ambiguous)
                ),
                chunk_limit=20,
            )

        self.assertEqual(released, [])
        self.assertEqual(failures, [("2026-08-17", 1, True)])

    async def test_publish_keeps_claim_when_first_send_has_ambiguous_error(self):
        released = []
        failures = []

        class AmbiguousBot:
            async def send_message(self, **kwargs):
                raise TimeoutError("Telegram response timed out")

        async def builder():
            return "Одна часть", ["1"]

        with self.assertRaises(TimeoutError):
            await publish_digest(
                AmbiguousBot(),
                chat_id=-1002318310296,
                thread_id=1,
                publication_key="2026-08-17",
                builder=builder,
                claimer=lambda _key: True,
                releaser=lambda key: released.append(key),
                failure_recorder=lambda key, count, ambiguous: failures.append(
                    (key, count, ambiguous)
                ),
            )

        self.assertEqual(released, [])
        self.assertEqual(failures, [("2026-08-17", 0, True)])

    async def test_fallback_accepts_allowed_local_and_world_sources(self):
        now = datetime(2026, 8, 17, 3, 30, tzinfo=timezone.utc)
        payload = [
            {
                "title": "Новость НБК",
                "text": "Подробности",
                "url": "https://nationalbank.kz/news/1",
                "source": "НБК",
                "date": "2026-08-17T01:00:00Z",
                "is_world": False,
            },
            {
                "title": "Непроверенный сайт",
                "text": "Подробности",
                "url": "https://unknown.kz/news/2",
                "source": "Unknown",
                "date": "2026-08-17T01:00:00Z",
                "is_world": False,
            },
            {
                "title": "Мировая новость",
                "text": "Подробности",
                "url": "https://reuters.com/world/3",
                "source": "Reuters",
                "date": "2026-08-17T01:00:00Z",
                "is_world": True,
            },
        ]

        class FakeModels:
            async def generate_content(self, **kwargs):
                return type(
                    "Response",
                    (),
                    {"text": json.dumps(payload, ensure_ascii=False)},
                )()

        fake_client = type(
            "Client",
            (),
            {"aio": type("Aio", (), {"models": FakeModels()})()},
        )()

        verified_pages = {
            "https://nationalbank.kz/news/1": {
                "final_url": "https://nationalbank.kz/news/1",
                "title": "Подтверждённая новость НБК",
                "text": "На странице опубликовано подробное описание решения.",
                "date": "2026-08-17T01:05:00Z",
            },
            "https://reuters.com/world/3": {
                "final_url": "https://reuters.com/world/3",
                "title": "Подтверждённая мировая новость",
                "text": "На странице опубликовано подробное описание события.",
                "date": "2026-08-17T01:10:00Z",
            },
        }

        result = await search_additional_news(
            now,
            client=fake_client,
            url_checker=lambda url: verified_pages.get(url),
        )

        self.assertEqual(
            [item["title"] for item in result],
            ["Подтверждённая новость НБК", "Подтверждённая мировая новость"],
        )
        self.assertEqual(result[0]["date"], "2026-08-17T01:05:00Z")

    async def test_fallback_rejects_redirect_outside_allowed_domains(self):
        now = datetime(2026, 8, 17, 3, 30, tzinfo=timezone.utc)
        payload = [{
            "title": "Подозрительная новость",
            "text": "Подробности",
            "url": "https://nationalbank.kz/redirect",
            "source": "НБК",
            "date": "2026-08-17T01:00:00Z",
            "is_world": False,
        }]

        class FakeModels:
            async def generate_content(self, **kwargs):
                return type("Response", (), {"text": json.dumps(payload)})()

        fake_client = type(
            "Client",
            (),
            {"aio": type("Aio", (), {"models": FakeModels()})()},
        )()

        result = await search_additional_news(
            now,
            client=fake_client,
            url_checker=lambda _url: {
                "final_url": "https://evil.example/fake",
                "title": "Чужая страница",
                "text": "Чужой текст",
                "date": "2026-08-17T01:00:00Z",
            },
        )

        self.assertEqual(result, [])

    async def test_fallback_retries_transport_error(self):
        now = datetime(2026, 8, 17, 3, 30, tzinfo=timezone.utc)
        payload = [{
            "title": "Новость НБК",
            "text": "Подробности",
            "url": "https://nationalbank.kz/news/retry",
            "source": "НБК",
            "date": "2026-08-17T01:00:00Z",
            "is_world": False,
        }]

        class FakeModels:
            def __init__(self):
                self.calls = 0

            async def generate_content(self, **kwargs):
                self.calls += 1
                if self.calls == 1:
                    raise RuntimeError("temporary search failure")
                return type("Response", (), {"text": json.dumps(payload)})()

        models = FakeModels()
        fake_client = type(
            "Client",
            (),
            {"aio": type("Aio", (), {"models": models})()},
        )()

        result = await search_additional_news(
            now,
            client=fake_client,
            url_checker=lambda url: {
                "final_url": url,
                "title": "Подтверждённая новость",
                "text": "Подтверждённый подробный текст публикации.",
                "date": "2026-08-17T01:00:00Z",
            },
        )

        self.assertEqual(models.calls, 2)
        self.assertEqual(result[0]["title"], "Подтверждённая новость")

    async def test_fallback_retries_wrong_json_shape(self):
        now = datetime(2026, 8, 17, 3, 30, tzinfo=timezone.utc)
        valid_payload = [{
            "title": "Новость НБК",
            "text": "Подробности",
            "url": "https://nationalbank.kz/news/shape",
            "source": "НБК",
            "date": "2026-08-17T01:00:00Z",
            "is_world": False,
        }]

        class FakeModels:
            def __init__(self):
                self.calls = 0

            async def generate_content(self, **kwargs):
                self.calls += 1
                payload = {} if self.calls == 1 else valid_payload
                return type("Response", (), {"text": json.dumps(payload)})()

        models = FakeModels()
        fake_client = type(
            "Client",
            (),
            {"aio": type("Aio", (), {"models": models})()},
        )()

        result = await search_additional_news(
            now,
            client=fake_client,
            url_checker=lambda url: {
                "final_url": url,
                "title": "Подтверждённая новость",
                "text": "Подтверждённый подробный текст публикации.",
                "date": "2026-08-17T01:00:00Z",
            },
        )

        self.assertEqual(models.calls, 2)
        self.assertEqual(len(result), 1)

    def test_split_digest_keeps_chunks_below_limit(self):
        text = "Блок один\n\nБлок два\n\nБлок три"

        chunks = split_digest(text, limit=20)

        self.assertEqual("\n\n".join(chunks), text)
        self.assertTrue(all(len(chunk) <= 20 for chunk in chunks))

    def test_format_limits_unexpectedly_long_ai_text(self):
        now = datetime(2026, 8, 17, 3, 30, tzinfo=timezone.utc)
        candidate = NewsCandidate(
            "long",
            "Очень длинный заголовок " * 100,
            "Исходный текст",
            "https://example.com/long",
            "Источник",
            now,
        )
        item = DigestItem(candidate, "Сводка " * 1000, "Важно " * 1000)

        text = format_digest([item], "Вывод " * 1000, greeting="Доброе утро")
        chunks = split_digest(text)

        self.assertTrue(all(len(chunk) <= 3900 for chunk in chunks))

    def test_split_digest_keeps_each_news_item_with_its_source(self):
        now = datetime(2026, 8, 17, 3, 30, tzinfo=timezone.utc)
        items = [
            DigestItem(
                NewsCandidate(
                    str(index),
                    f"Уникальный заголовок {index}",
                    "Исходный текст",
                    f"https://example.com/{index}",
                    f"Источник {index}",
                    now,
                ),
                f"Описание {index} " * 25,
                f"Значение {index} " * 10,
            )
            for index in (1, 2)
        ]

        chunks = split_digest(
            format_digest(items, "Общий вывод", greeting="Доброе утро"),
            limit=700,
        )

        for index in (1, 2):
            item_chunk = next(
                chunk for chunk in chunks if f"Уникальный заголовок {index}" in chunk
            )
            self.assertIn(f"Источник {index}", item_chunk)


class DigestPersistenceTests(unittest.TestCase):
    def setUp(self):
        import bot.rag.firebase_db as firebase_db

        self.firebase_db = firebase_db
        self.original_db = firebase_db.db

    def tearDown(self):
        self.firebase_db.db = self.original_db

    def test_requires_firebase_before_checking_publication(self):
        from bot.rag.firebase_db import was_digest_published

        self.firebase_db.db = None

        with self.assertRaises(RuntimeError):
            was_digest_published("2026-08-17")

    def test_records_article_ids_after_successful_publication(self):
        from bot.rag.firebase_db import mark_digest_published

        saved = {}

        class FakeDocument:
            def set(self, data):
                saved.update(data)

        class FakeCollection:
            def document(self, key):
                self.key = key
                return FakeDocument()

        class FakeDb:
            def collection(self, name):
                self.name = name
                return FakeCollection()

        fake_db = FakeDb()
        self.firebase_db.db = fake_db

        mark_digest_published("2026-08-17", ["a", "b"])

        self.assertEqual(fake_db.name, "news_digest_publications")
        self.assertEqual(saved["article_ids"], ["a", "b"])
        self.assertIn("published_at", saved)
        self.assertEqual(saved["status"], "published")

    def test_claim_uses_atomic_document_create(self):
        from bot.rag.firebase_db import claim_digest_publication

        created = []

        class FakeDocument:
            def create(self, data):
                created.append(data)

        class FakeCollection:
            def document(self, _key):
                return FakeDocument()

        class FakeDb:
            def collection(self, _name):
                return FakeCollection()

        self.firebase_db.db = FakeDb()

        claimed = claim_digest_publication("2026-08-17")

        self.assertTrue(claimed)
        self.assertEqual(created[0]["status"], "publishing")

    def test_claim_returns_false_when_document_already_exists(self):
        from google.api_core.exceptions import AlreadyExists
        from bot.rag.firebase_db import claim_digest_publication

        class FakeDocument:
            def create(self, _data):
                raise AlreadyExists("already claimed")

            def get(self):
                return type(
                    "Snapshot",
                    (),
                    {"to_dict": lambda _self: {"status": "published"}},
                )()

        class FakeCollection:
            def document(self, _key):
                return FakeDocument()

        class FakeDb:
            def collection(self, _name):
                return FakeCollection()

        self.firebase_db.db = FakeDb()

        self.assertFalse(claim_digest_publication("2026-08-17"))

    def test_release_deletes_unused_claim(self):
        from bot.rag.firebase_db import release_digest_claim

        deleted = []

        class FakeDocument:
            def delete(self):
                deleted.append(True)

        class FakeCollection:
            def document(self, _key):
                return FakeDocument()

        class FakeDb:
            def collection(self, _name):
                return FakeCollection()

        self.firebase_db.db = FakeDb()

        release_digest_claim("2026-08-17")

        self.assertEqual(deleted, [True])

    def test_stale_claim_can_be_reclaimed_atomically(self):
        from datetime import timedelta

        from bot.rag.firebase_db import _try_reclaim_digest_claim

        now = datetime(2026, 8, 17, 3, 30, tzinfo=timezone.utc)
        updates = []

        class FakeSnapshot:
            def to_dict(self):
                return {
                    "status": "publishing",
                    "lease_until": now - timedelta(minutes=1),
                }

        class FakeDocument:
            def get(self, transaction=None):
                return FakeSnapshot()

        class FakeTransaction:
            def update(self, _doc, data):
                updates.append(data)

        reclaimed = _try_reclaim_digest_claim(
            FakeTransaction(),
            FakeDocument(),
            now,
            now + timedelta(minutes=30),
        )

        self.assertTrue(reclaimed)
        self.assertEqual(updates[0]["status"], "publishing")

    def test_active_claim_is_not_reclaimed(self):
        from datetime import timedelta

        from bot.rag.firebase_db import _try_reclaim_digest_claim

        now = datetime(2026, 8, 17, 3, 30, tzinfo=timezone.utc)

        class FakeSnapshot:
            def to_dict(self):
                return {
                    "status": "publishing",
                    "lease_until": now + timedelta(minutes=1),
                }

        class FakeDocument:
            def get(self, transaction=None):
                return FakeSnapshot()

        class FakeTransaction:
            def update(self, _doc, _data):
                raise AssertionError("Active claim must not be updated")

        reclaimed = _try_reclaim_digest_claim(
            FakeTransaction(),
            FakeDocument(),
            now,
            now + timedelta(minutes=30),
        )

        self.assertFalse(reclaimed)


class DigestScheduleTests(unittest.TestCase):
    def test_default_schedule_is_monday_0930_almaty(self):
        schedule = digest_schedule({})

        self.assertEqual(
            schedule,
            {
                "day_of_week": "mon",
                "hour": 9,
                "minute": 30,
                "timezone": "Asia/Almaty",
            },
        )

    def test_rejects_invalid_time(self):
        with self.assertRaisesRegex(ValueError, "HH:MM"):
            digest_schedule({"NEWS_SCHEDULE_TIME": "25:70"})


if __name__ == "__main__":
    unittest.main()
