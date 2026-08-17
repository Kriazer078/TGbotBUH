import json
import unittest
from datetime import datetime, timezone

from bot.services.news_digest_service import (
    DigestItem,
    NewsCandidate,
    build_digest,
    format_digest,
    normalize_candidates,
    parse_ranked_items,
    publish_digest,
    rank_and_summarize,
    search_additional_news,
    split_digest,
)


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
            already_published=lambda _key: False,
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
            already_published=lambda _key: True,
        )

        self.assertFalse(result)

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

        result = await search_additional_news(
            now,
            client=fake_client,
            url_checker=lambda _url: True,
        )

        self.assertEqual(
            [item["title"] for item in result],
            ["Новость НБК", "Мировая новость"],
        )

    def test_split_digest_keeps_chunks_below_limit(self):
        text = "Блок один\n\nБлок два\n\nБлок три"

        chunks = split_digest(text, limit=20)

        self.assertEqual("\n\n".join(chunks), text)
        self.assertTrue(all(len(chunk) <= 20 for chunk in chunks))


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


if __name__ == "__main__":
    unittest.main()
