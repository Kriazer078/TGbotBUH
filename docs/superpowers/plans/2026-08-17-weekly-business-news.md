# Weekly Business News Digest Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Automatically publish a verified, readable five-item economy and business digest every Monday at 09:30 Asia/Almaty in Telegram chat `-1002318310296`, topic `1`.

**Architecture:** Add one focused digest service that normalizes candidates from the existing parser, enforces the 24-hour window and deduplication, asks Gemini to rank and summarize only verified candidates, formats safe Telegram HTML, and publishes idempotently. Keep scheduling in `bot/main.py`, persistence in `bot/rag/firebase_db.py`, and the admin entry point in `bot/handlers/user_handlers.py`.

**Tech Stack:** Python 3.11, aiogram 3, APScheduler, google-genai, Firebase Firestore, python-dateutil, unittest.

---

## File map

- Create `bot/services/news_digest_service.py`: candidate validation, Gemini selection, fallback search, formatting, splitting, and publish orchestration.
- Create `tests/test_news_digest_service.py`: pure-function and async orchestration tests without real network calls.
- Modify `bot/rag/firebase_db.py`: idempotency record for successful scheduled publications.
- Modify `bot/main.py`: weekly cron job and configuration.
- Modify `bot/handlers/user_handlers.py`: admin-only `/send_news` preview/live command.
- Modify `requirements.txt`: add `python-dateutil` for reliable source-date parsing.
- Modify `README.md`: document the webhook launch and digest environment variables.

### Task 1: Model, validate, filter, and deduplicate candidates

**Files:**
- Create: `bot/services/news_digest_service.py`
- Create: `tests/test_news_digest_service.py`
- Modify: `requirements.txt`

- [ ] **Step 1: Write failing tests for the 24-hour boundary and duplicates**

```python
# tests/test_news_digest_service.py
import unittest
from datetime import datetime, timezone

from bot.services.news_digest_service import normalize_candidates


class NormalizeCandidatesTests(unittest.TestCase):
    def test_keeps_only_verified_items_from_last_24_hours(self):
        now = datetime(2026, 8, 17, 3, 30, tzinfo=timezone.utc)
        raw = [
            {"article_id": "new", "title": "Новая мера для бизнеса", "text": "Подробное описание события", "url": "https://nationalbank.kz/news/1", "source": "НБК", "date": "2026-08-16T04:00:00Z"},
            {"article_id": "edge", "title": "Граничная новость", "text": "Подробное описание события", "url": "https://stat.gov.kz/news/2", "source": "БНС", "date": "2026-08-16T03:30:00Z"},
            {"article_id": "old", "title": "Старая новость", "text": "Подробное описание события", "url": "https://kgd.gov.kz/news/3", "source": "КГД", "date": "2026-08-16T03:29:59Z"},
            {"article_id": "bad", "title": "Без ссылки", "text": "Описание", "url": "", "source": "КГД", "date": "2026-08-17T01:00:00Z"},
        ]

        result = normalize_candidates(raw, now=now)

        self.assertEqual([item.article_id for item in result], ["new", "edge"])

    def test_deduplicates_normalized_urls_and_similar_titles(self):
        now = datetime(2026, 8, 17, 3, 30, tzinfo=timezone.utc)
        raw = [
            {"article_id": "a", "title": "НБК сохранил базовую ставку", "text": "Первое описание", "url": "https://nationalbank.kz/news/rate?utm_source=x", "source": "НБК", "date": "2026-08-17T01:00:00Z"},
            {"article_id": "b", "title": "НБК сохранил базовую ставку!", "text": "Более длинное описание решения Национального банка", "url": "https://nationalbank.kz/news/rate", "source": "НБК", "date": "2026-08-17T01:05:00Z"},
        ]

        result = normalize_candidates(raw, now=now)

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].article_id, "b")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the tests and confirm the module is missing**

Run: `python -m unittest tests.test_news_digest_service.NormalizeCandidatesTests -v`

Expected: `ModuleNotFoundError: No module named 'bot.services.news_digest_service'`.

- [ ] **Step 3: Add the dependency and minimal normalization implementation**

Append to `requirements.txt`:

```text
python-dateutil>=2.9.0,<3
```

Create `bot/services/news_digest_service.py`:

```python
import html
import json
import logging
import os
import random
import re
import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from zoneinfo import ZoneInfo

import requests
from dateutil import parser as date_parser

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class NewsCandidate:
    article_id: str
    title: str
    text: str
    url: str
    source: str
    published_at: datetime
    is_world: bool = False


@dataclass(frozen=True)
class DigestItem:
    candidate: NewsCandidate
    summary: str
    importance: str


def _parse_date(value: str) -> datetime | None:
    if not value:
        return None
    try:
        parsed = date_parser.parse(value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except (TypeError, ValueError, OverflowError):
        return None


def _canonical_url(value: str) -> str:
    parts = urlsplit(value.strip())
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        return ""
    query = [(k, v) for k, v in parse_qsl(parts.query) if not k.lower().startswith("utm_")]
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path.rstrip("/"), urlencode(query), ""))


def _title_key(value: str) -> str:
    return re.sub(r"[^a-zа-я0-9]+", " ", value.lower()).strip()


def normalize_candidates(raw_items: list[dict], now: datetime | None = None) -> list[NewsCandidate]:
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    cutoff = current - timedelta(hours=24)
    accepted: list[NewsCandidate] = []
    for raw in raw_items:
        published_at = _parse_date(str(raw.get("date", "")))
        url = _canonical_url(str(raw.get("url", "")))
        title = str(raw.get("title", "")).strip()
        text = str(raw.get("text", "")).strip()
        if not published_at or not (cutoff <= published_at <= current) or not url or not title or not text:
            continue
        candidate = NewsCandidate(
            article_id=str(raw.get("article_id") or url),
            title=title,
            text=text,
            url=url,
            source=str(raw.get("source", "Источник")).strip() or "Источник",
            published_at=published_at,
            is_world=bool(raw.get("is_world", False)),
        )
        duplicate_index = next((
            index for index, item in enumerate(accepted)
            if item.url == candidate.url
            or SequenceMatcher(None, _title_key(item.title), _title_key(candidate.title)).ratio() >= 0.92
        ), None)
        if duplicate_index is None:
            accepted.append(candidate)
        elif len(candidate.text) > len(accepted[duplicate_index].text):
            accepted[duplicate_index] = candidate
    return sorted(accepted, key=lambda item: item.published_at, reverse=True)
```

- [ ] **Step 4: Install the dependency and run the tests**

Run: `python -m pip install -r requirements.txt`

Expected: installation completes successfully.

Run: `python -m unittest tests.test_news_digest_service.NormalizeCandidatesTests -v`

Expected: 2 tests pass.

- [ ] **Step 5: Commit the normalization unit**

```bash
git add requirements.txt bot/services/news_digest_service.py tests/test_news_digest_service.py
git commit -m "feat: normalize recent business news candidates"
```

### Task 2: Rank, summarize, and format verified news

**Files:**
- Modify: `bot/services/news_digest_service.py`
- Modify: `tests/test_news_digest_service.py`

- [ ] **Step 1: Write failing tests for selection limits and final HTML**

Add to `tests/test_news_digest_service.py`:

```python
from bot.services.news_digest_service import DigestItem, NewsCandidate, format_digest, parse_ranked_items


class DigestFormattingTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 8, 17, 3, 30, tzinfo=timezone.utc)
        self.candidates = [
            NewsCandidate(str(i), f"Новость {i}", "Исходный текст", f"https://example.com/{i}", "Источник", self.now, i >= 4)
            for i in range(1, 7)
        ]

    def test_parser_rejects_unknown_urls_and_limits_world_news(self):
        payload = json.dumps({"items": [
            {"url": "https://example.com/1", "summary": "Факт 1.", "importance": "Важно 1."},
            {"url": "https://example.com/4", "summary": "Факт 4.", "importance": "Важно 4."},
            {"url": "https://example.com/5", "summary": "Факт 5.", "importance": "Важно 5."},
            {"url": "https://example.com/6", "summary": "Факт 6.", "importance": "Важно 6."},
            {"url": "https://unknown.example/news", "summary": "Выдумка.", "importance": "Нет."},
        ], "overview": "Главный вывод дня."}, ensure_ascii=False)

        items, overview = parse_ranked_items(payload, self.candidates)

        self.assertEqual([item.candidate.article_id for item in items], ["1", "4", "5"])
        self.assertEqual(overview, "Главный вывод дня.")

    def test_format_contains_context_importance_source_and_time(self):
        item = DigestItem(self.candidates[0], "Произошло важное изменение.", "Оно влияет на стоимость финансирования.")

        text = format_digest([item], "Рынок ждёт новых решений.", greeting="Доброе утро! ☀️")

        self.assertIn("🇰🇿 <b>1. Новость 1</b>", text)
        self.assertIn("<b>Почему важно:</b>", text)
        self.assertIn('<a href="https://example.com/1">Источник</a>', text)
        self.assertIn("<b>Коротко о главном:</b>", text)
```

Also add `import json` at the top of the test file.

- [ ] **Step 2: Run the new tests and confirm missing functions**

Run: `python -m unittest tests.test_news_digest_service.DigestFormattingTests -v`

Expected: import failure for `format_digest` or `parse_ranked_items`.

- [ ] **Step 3: Implement strict Gemini-output parsing and formatting**

Add to `bot/services/news_digest_service.py`:

```python
GREETINGS = (
    "Доброе утро! ☀️ Пусть новая неделя начнётся спокойно, продуктивно и с хороших новостей.",
    "Доброе утро! 🌤 Желаем лёгкого старта недели и уверенных решений.",
    "С добрым утром! ☕ Начинаем неделю с главных событий экономики и бизнеса.",
    "Доброе утро! 📈 Пусть эта неделя принесёт полезные идеи и хорошие результаты.",
    "С понедельником! ☀️ Коротко и понятно рассказываем, что произошло за последние сутки.",
)


def parse_ranked_items(payload: str, candidates: list[NewsCandidate]) -> tuple[list[DigestItem], str]:
    cleaned = re.sub(r"^```(?:json)?|```$", "", payload.strip(), flags=re.MULTILINE).strip()
    data = json.loads(cleaned)
    by_url = {item.url: item for item in candidates}
    selected: list[DigestItem] = []
    world_count = 0
    for raw in data.get("items", []):
        candidate = by_url.get(_canonical_url(str(raw.get("url", ""))))
        if not candidate or any(item.candidate.article_id == candidate.article_id for item in selected):
            continue
        if candidate.is_world and world_count >= 2:
            continue
        summary = str(raw.get("summary", "")).strip()
        importance = str(raw.get("importance", "")).strip()
        if not summary or not importance:
            continue
        selected.append(DigestItem(candidate, summary, importance))
        world_count += int(candidate.is_world)
        if len(selected) == 5:
            break
    return selected, str(data.get("overview", "")).strip()


def format_digest(items: list[DigestItem], overview: str, greeting: str | None = None) -> str:
    parts = [
        html.escape(greeting or random.choice(GREETINGS)),
        "<b>Главное в экономике и бизнесе за последние 24 часа:</b>",
    ]
    for index, item in enumerate(items, 1):
        flag = "🌍" if item.candidate.is_world else "🇰🇿"
        local_time = item.candidate.published_at.astimezone(
            ZoneInfo(os.getenv("NEWS_TIMEZONE", "Asia/Almaty"))
        ).strftime("%H:%M")
        parts.append(
            f'{flag} <b>{index}. {html.escape(item.candidate.title)}</b>\n\n'
            f'{html.escape(item.summary)}\n\n'
            f'<b>Почему важно:</b> {html.escape(item.importance)}\n\n'
            f'🔗 <a href="{html.escape(item.candidate.url, quote=True)}">{html.escape(item.candidate.source)}</a> · {local_time}'
        )
    if overview:
        parts.append(f"<b>Коротко о главном:</b> {html.escape(overview)}")
    parts.append("Хорошей и успешной недели! 🚀")
    return "\n\n".join(parts)
```

- [ ] **Step 4: Add Gemini selection with a strict candidate allow-list**

Add to `bot/services/news_digest_service.py`:

```python
async def rank_and_summarize(candidates: list[NewsCandidate]) -> tuple[list[DigestItem], str]:
    from google import genai
    from google.genai import types as genai_types

    client = genai.Client(api_key=os.getenv("GOOGLE_API_KEY"))
    candidate_data = [{
        "title": item.title,
        "text": item.text[:2500],
        "url": item.url,
        "source": item.source,
        "published_at": item.published_at.isoformat(),
        "scope": "world" if item.is_world else "kazakhstan",
    } for item in candidates[:30]]
    prompt = (
        "Выбери до 5 важнейших новостей экономики и бизнеса. Казахстан имеет приоритет; "
        "мировых новостей максимум две. Используй только переданные URL. Для каждой дай "
        "summary из 2–3 коротких предложений и importance — одно практическое предложение. "
        "Верни только JSON: {\"items\":[{\"url\":...,\"summary\":...,\"importance\":...}],"
        "\"overview\":\"одна строка\"}. Данные:\n" + json.dumps(candidate_data, ensure_ascii=False)
    )
    for attempt in range(2):
        try:
            response = await client.aio.models.generate_content(
                model=os.getenv("GEMINI_MODEL", "gemini-2.5-flash"),
                contents=prompt,
                config=genai_types.GenerateContentConfig(temperature=0.1, max_output_tokens=1800),
            )
            return parse_ranked_items(response.text or "{}", candidates)
        except (json.JSONDecodeError, ValueError, TypeError) as exc:
            logger.warning("[weekly_digest] ranking attempt %s failed: %s", attempt + 1, exc)
            if attempt == 0:
                await asyncio.sleep(1)
    return [], ""
```

- [ ] **Step 5: Run the focused and full tests**

Run: `python -m unittest tests.test_news_digest_service -v`

Expected: all digest tests pass without network access.

- [ ] **Step 6: Commit ranking and formatting**

```bash
git add bot/services/news_digest_service.py tests/test_news_digest_service.py
git commit -m "feat: rank and format verified news digest"
```

### Task 3: Add fallback discovery and idempotent publication

**Files:**
- Modify: `bot/services/news_digest_service.py`
- Modify: `bot/rag/firebase_db.py`
- Modify: `tests/test_news_digest_service.py`

- [ ] **Step 1: Write failing async orchestration tests**

Add to `tests/test_news_digest_service.py`:

```python
from unittest.mock import AsyncMock, patch
from bot.services.news_digest_service import build_digest, publish_digest


class DigestOrchestrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_build_uses_fallback_only_when_primary_has_fewer_than_five(self):
        primary = [{"article_id": "1", "title": "Новость", "text": "Текст", "url": "https://example.com/1", "source": "Источник", "date": "2026-08-17T01:00:00Z"}]
        fallback = [{"article_id": str(i), "title": f"Новость {i}", "text": "Текст", "url": f"https://example.com/{i}", "source": "Источник", "date": "2026-08-17T01:00:00Z"} for i in range(2, 7)]
        now = datetime(2026, 8, 17, 3, 30, tzinfo=timezone.utc)
        ranked = [DigestItem(NewsCandidate("1", "Новость", "Текст", "https://example.com/1", "Источник", now), "Сводка.", "Важно.")]
        with patch("bot.rag.news_parser.fetch_all_news", AsyncMock(return_value=primary)), patch("bot.services.news_digest_service.search_additional_news", AsyncMock(return_value=fallback)) as fallback_mock, patch("bot.services.news_digest_service.rank_and_summarize", AsyncMock(return_value=(ranked, "Вывод."))):
            text, article_ids = await build_digest(now=now)
        fallback_mock.assert_awaited_once()
        self.assertIn("Новость", text)
        self.assertEqual(article_ids, ["1"])

    async def test_publish_sends_to_topic_and_marks_success(self):
        bot = AsyncMock()
        with patch("bot.services.news_digest_service.build_digest", AsyncMock(return_value=("digest", ["1"]))), patch("bot.rag.firebase_db.was_digest_published", return_value=False), patch("bot.rag.firebase_db.mark_digest_published") as mark:
            sent = await publish_digest(bot, chat_id=-1002318310296, thread_id=1, publication_key="2026-08-17")
        self.assertTrue(sent)
        bot.send_message.assert_awaited_once_with(chat_id=-1002318310296, message_thread_id=1, text="digest", parse_mode="HTML", disable_web_page_preview=True)
        mark.assert_called_once_with("2026-08-17", ["1"])

    async def test_publish_skips_an_existing_publication(self):
        bot = AsyncMock()
        with patch("bot.rag.firebase_db.was_digest_published", return_value=True):
            sent = await publish_digest(bot, -1002318310296, 1, "2026-08-17")
        self.assertFalse(sent)
        bot.send_message.assert_not_awaited()

    def test_split_digest_keeps_every_chunk_below_limit(self):
        from bot.services.news_digest_service import split_digest
        chunks = split_digest("Блок один\n\nБлок два\n\nБлок три", limit=20)
        self.assertEqual("\n\n".join(chunks), "Блок один\n\nБлок два\n\nБлок три")
        self.assertTrue(all(len(chunk) <= 20 for chunk in chunks))
```

- [ ] **Step 2: Run the orchestration tests and verify failure**

Run: `python -m unittest tests.test_news_digest_service.DigestOrchestrationTests -v`

Expected: import errors for `build_digest` and `publish_digest`.

- [ ] **Step 3: Add Firebase idempotency helpers**

Append to `bot/rag/firebase_db.py`:

```python
def was_digest_published(publication_key: str) -> bool:
    if not db:
        raise RuntimeError("Firebase is unavailable; publication cannot be checked safely")
    return db.collection("news_digest_publications").document(publication_key).get().exists


def mark_digest_published(publication_key: str, article_ids: list[str]) -> None:
    if not db:
        raise RuntimeError("Firebase is unavailable; publication cannot be recorded safely")
    db.collection("news_digest_publications").document(publication_key).set({
        "article_ids": article_ids,
        "published_at": firestore.SERVER_TIMESTAMP,
    })
```

- [ ] **Step 4: Add fallback search, build, splitting, retries, and publication**

Add the following public interfaces to `bot/services/news_digest_service.py` and keep network clients injectable in tests:

```python
async def search_additional_news(now: datetime) -> list[dict]:
    from google import genai
    from google.genai import types as genai_types

    domains = tuple(filter(None, os.getenv(
        "NEWS_ALLOWED_DOMAINS",
        "nationalbank.kz,kgd.gov.kz,gov.kz,stat.gov.kz,uchet.kz",
    ).split(",")))
    cutoff = now.astimezone(timezone.utc) - timedelta(hours=24)
    prompt = (
        "Найди новости экономики и бизнеса Казахстана и 1–2 важные мировые новости, "
        f"опубликованные строго между {cutoff.isoformat()} и {now.astimezone(timezone.utc).isoformat()}. "
        f"Источники для Казахстана: {', '.join(domains)}. Верни только JSON-массив объектов "
        "с полями title, text, url, source, date, is_world. Не включай материал без точной даты и URL."
    )
    client = genai.Client(api_key=os.getenv("GOOGLE_API_KEY"))
    for attempt in range(2):
        try:
            response = await client.aio.models.generate_content(
                model=os.getenv("GEMINI_MODEL", "gemini-2.5-flash"),
                contents=prompt,
                config=genai_types.GenerateContentConfig(
                    temperature=0.0,
                    max_output_tokens=3000,
                    tools=[genai_types.Tool(google_search=genai_types.GoogleSearch())],
                ),
            )
            cleaned = re.sub(r"^```(?:json)?|```$", "", (response.text or "[]").strip(), flags=re.MULTILINE).strip()
            raw_items = json.loads(cleaned)
            if not isinstance(raw_items, list):
                return []

            async def reachable(item: dict) -> dict | None:
                url = _canonical_url(str(item.get("url", "")))
                if not url:
                    return None
                host = urlsplit(url).netloc.lower()
                if not item.get("is_world") and not any(
                    host == domain or host.endswith(f".{domain}") for domain in domains
                ):
                    return None

                def check() -> bool:
                    try:
                        response = requests.get(
                            url,
                            headers={"User-Agent": "Mozilla/5.0"},
                            timeout=8,
                            stream=True,
                            allow_redirects=True,
                        )
                        response.close()
                        return response.status_code < 400
                    except requests.RequestException:
                        return False

                if not await asyncio.to_thread(check):
                    return None
                verified = dict(item)
                verified["url"] = url
                verified["article_id"] = str(item.get("article_id") or url)
                return verified

            checked = await asyncio.gather(*(reachable(item) for item in raw_items[:20]))
            return [item for item in checked if item]
        except (json.JSONDecodeError, ValueError, TypeError) as exc:
            logger.warning("[weekly_digest] fallback attempt %s failed: %s", attempt + 1, exc)
            if attempt == 0:
                await asyncio.sleep(1)
    return []


def split_digest(text: str, limit: int = 3900) -> list[str]:
    blocks = text.split("\n\n")
    chunks: list[str] = []
    current = ""
    for block in blocks:
        candidate = f"{current}\n\n{block}" if current else block
        if len(candidate) <= limit:
            current = candidate
        else:
            if current:
                chunks.append(current)
            current = block
    if current:
        chunks.append(current)
    return chunks


async def build_digest(now: datetime | None = None) -> tuple[str, list[str]]:
    from bot.rag.news_parser import fetch_all_news
    current = now or datetime.now(timezone.utc)
    primary = await fetch_all_news()
    candidates = normalize_candidates(primary, current)
    if len(candidates) < 5:
        candidates = normalize_candidates(primary + await search_additional_news(current), current)
    items, overview = await rank_and_summarize(candidates)
    if not items:
        raise RuntimeError("No verified news available for the digest")
    return format_digest(items, overview), [item.candidate.article_id for item in items]


async def publish_digest(bot, chat_id: int, thread_id: int, publication_key: str, test_mode: bool = False) -> bool:
    from bot.rag.firebase_db import mark_digest_published, was_digest_published
    if not test_mode and was_digest_published(publication_key):
        return False
    text, article_ids = await build_digest()
    for chunk in split_digest(text):
        await bot.send_message(
            chat_id=chat_id,
            message_thread_id=thread_id,
            text=chunk,
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
    if not test_mode:
        mark_digest_published(publication_key, article_ids)
    return True
```

Resolve the test’s initial string-only mock by returning `(text, article_ids)` consistently. Add two-attempt retry around candidate discovery and Gemini ranking, but never retry Telegram sending after an ambiguous timeout because that can duplicate a post.

- [ ] **Step 5: Run all digest tests**

Run: `python -m unittest tests.test_news_digest_service -v`

Expected: normalization, formatting, fallback, splitting, and publishing tests all pass.

- [ ] **Step 6: Commit orchestration and persistence**

```bash
git add bot/services/news_digest_service.py bot/rag/firebase_db.py tests/test_news_digest_service.py
git commit -m "feat: publish weekly digest idempotently"
```

### Task 4: Wire the weekly scheduler and admin preview command

**Files:**
- Modify: `bot/main.py:24-59`
- Modify: `bot/handlers/user_handlers.py:316-420`
- Modify: `tests/test_news_digest_service.py`

- [ ] **Step 1: Write a failing scheduler-configuration test**

Add to `tests/test_news_digest_service.py`:

```python
from bot.services.news_digest_service import digest_schedule


class DigestScheduleTests(unittest.TestCase):
    def test_default_schedule_is_monday_0930_almaty(self):
        schedule = digest_schedule({})
        self.assertEqual(schedule, {
            "day_of_week": "mon",
            "hour": 9,
            "minute": 30,
            "timezone": "Asia/Almaty",
        })
```

- [ ] **Step 2: Run the test and verify failure**

Run: `python -m unittest tests.test_news_digest_service.DigestScheduleTests -v`

Expected: import error for `digest_schedule`.

- [ ] **Step 3: Implement environment parsing**

Add to `bot/services/news_digest_service.py`:

```python
def digest_schedule(env: dict[str, str] | None = None) -> dict:
    values = os.environ if env is None else env
    raw_time = values.get("NEWS_SCHEDULE_TIME", "09:30")
    hour_text, minute_text = raw_time.split(":", 1)
    hour, minute = int(hour_text), int(minute_text)
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError("NEWS_SCHEDULE_TIME must be HH:MM")
    return {
        "day_of_week": values.get("NEWS_SCHEDULE_DAY", "mon"),
        "hour": hour,
        "minute": minute,
        "timezone": values.get("NEWS_TIMEZONE", "Asia/Almaty"),
    }
```

- [ ] **Step 4: Register the cron job in `bot/main.py`**

Inside `on_startup`, after the existing six-hour parser job, add:

```python
    from datetime import datetime
    from zoneinfo import ZoneInfo
    from bot.services.news_digest_service import digest_schedule, publish_digest

    async def _weekly_digest_job():
        timezone_name = os.getenv("NEWS_TIMEZONE", "Asia/Almaty")
        publication_key = datetime.now(ZoneInfo(timezone_name)).date().isoformat()
        try:
            await publish_digest(
                bot,
                chat_id=int(os.getenv("NEWS_TARGET_CHAT_ID", "-1002318310296")),
                thread_id=int(os.getenv("NEWS_TARGET_THREAD_ID", "1")),
                publication_key=publication_key,
            )
        except Exception as exc:
            logger.exception("[weekly_digest] Publication failed: %s", exc)
            admin_id = os.getenv("ADMIN_ID")
            if admin_id:
                await bot.send_message(int(admin_id), "⚠️ Не удалось опубликовать понедельничную подборку. Проверьте логи.")

    scheduler.add_job(
        _weekly_digest_job,
        "cron",
        id="weekly_business_digest",
        replace_existing=True,
        **digest_schedule(),
    )
```

- [ ] **Step 5: Add the admin-only `/send_news` command**

Add near the existing admin commands in `bot/handlers/user_handlers.py`:

```python
@user_router.message(Command("send_news"))
async def cmd_send_news(message: Message):
    admin_id = os.getenv("ADMIN_ID", "")
    if not admin_id or str(message.from_user.id) != admin_id:
        await message.answer("⛔ У вас нет прав для использования этой команды.")
        return
    live = "live" in message.text.lower().split()[1:]
    await message.answer("🔍 Формирую проверенную подборку…")
    try:
        from datetime import datetime
        from zoneinfo import ZoneInfo
        from bot.services.news_digest_service import build_digest, publish_digest
        if live:
            timezone_name = os.getenv("NEWS_TIMEZONE", "Asia/Almaty")
            await publish_digest(
                message.bot,
                int(os.getenv("NEWS_TARGET_CHAT_ID", "-1002318310296")),
                int(os.getenv("NEWS_TARGET_THREAD_ID", "1")),
                datetime.now(ZoneInfo(timezone_name)).date().isoformat(),
            )
            await message.answer("✅ Подборка опубликована в целевой теме.")
        else:
            preview, _ = await build_digest()
            await message.answer(preview, parse_mode="HTML", disable_web_page_preview=True)
    except Exception as exc:
        logger.exception("[/send_news] Ошибка: %s", exc)
        await message.answer("⚠️ Не удалось сформировать подборку. Проверьте источники и повторите позже.")
```

Document command behavior in the handler comment: `/send_news` previews only to the admin; `/send_news live` publishes to the target topic. Do not require `_is_allowed_thread` for this admin command, so it can be tested safely in the administrator’s private chat.

- [ ] **Step 6: Run the focused tests and syntax check**

Run: `python -m unittest tests.test_news_digest_service -v`

Expected: all tests pass.

Run: `python -m compileall -q bot tests`

Expected: exit code 0 with no syntax errors.

- [ ] **Step 7: Commit scheduler and admin command**

```bash
git add bot/main.py bot/handlers/user_handlers.py bot/services/news_digest_service.py tests/test_news_digest_service.py
git commit -m "feat: schedule Monday business digest"
```

### Task 5: Document configuration and perform safe end-to-end verification

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Replace the minimal README with exact setup instructions**

Add these environment variables and commands to `README.md`:

```markdown
## Понедельничная подборка

```env
NEWS_TARGET_CHAT_ID=-1002318310296
NEWS_TARGET_THREAD_ID=1
NEWS_SCHEDULE_DAY=mon
NEWS_SCHEDULE_TIME=09:30
NEWS_TIMEZONE=Asia/Almaty
```

Бот должен иметь право отправлять сообщения в указанную тему.

- `/send_news` — сформировать тестовую подборку и отправить только администратору.
- `/send_news live` — опубликовать подборку в целевой теме.
```

Also correct the launch description: current production mode is webhook and requires `WEBHOOK_HOST`; it is not long polling.

- [ ] **Step 2: Run all local tests**

Run: `python -m unittest discover -s tests -v`

Expected: all tests pass and no network calls occur.

- [ ] **Step 3: Verify imports against the installed environment**

Run: `python -c "from bot.services.news_digest_service import digest_schedule; print(digest_schedule({}))"`

Expected: `{'day_of_week': 'mon', 'hour': 9, 'minute': 30, 'timezone': 'Asia/Almaty'}`.

- [ ] **Step 4: Run an administrator-only preview**

Deploy with the five `NEWS_*` variables, then send `/send_news` in the administrator’s private chat.

Expected: the administrator receives a formatted digest with 1–5 verified items, 2–3 sentence summaries, `Почему важно`, source links, publication times, and no post appears in the target topic.

- [ ] **Step 5: Run one controlled live publication**

Send `/send_news live` once.

Expected: the digest appears in chat `-1002318310296`, topic `1`; a `news_digest_publications` document is created; a second live run for the same date reports no duplicate publication.

- [ ] **Step 6: Commit documentation**

```bash
git add README.md
git commit -m "docs: document weekly news digest"
```

- [ ] **Step 7: Final verification**

Run: `git status --short`

Expected: only the user’s pre-existing unrelated changes remain; no untracked test artifacts or `__pycache__` files are present.
