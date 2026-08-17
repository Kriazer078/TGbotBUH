import json
import unittest
from datetime import datetime, timezone

from bot.services.news_digest_service import (
    DigestItem,
    NewsCandidate,
    format_digest,
    normalize_candidates,
    parse_ranked_items,
    rank_and_summarize,
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


if __name__ == "__main__":
    unittest.main()
