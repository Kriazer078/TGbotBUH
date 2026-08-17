import unittest
from datetime import datetime, timezone

from bot.services.news_digest_service import normalize_candidates


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


if __name__ == "__main__":
    unittest.main()
