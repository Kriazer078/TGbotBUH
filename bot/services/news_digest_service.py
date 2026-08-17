import asyncio
import html
import json
import logging
import os
import random
import re
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


GREETINGS = (
    "Доброе утро! ☀️ Пусть новая неделя начнётся спокойно, продуктивно и с хороших новостей.",
    "Доброе утро! 🌤 Желаем лёгкого старта недели и уверенных решений.",
    "С добрым утром! ☕ Начинаем неделю с главных событий экономики и бизнеса.",
    "Доброе утро! 📈 Пусть эта неделя принесёт полезные идеи и хорошие результаты.",
    "С понедельником! ☀️ Коротко и понятно рассказываем, что произошло за последние сутки.",
)


def _parse_date(value: str) -> datetime | None:
    if not value:
        return None
    try:
        parsed = date_parser.parse(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _canonical_url(value: str) -> str:
    parts = urlsplit(value.strip())
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        return ""
    query = [
        (key, item)
        for key, item in parse_qsl(parts.query)
        if not key.lower().startswith("utm_")
    ]
    return urlunsplit(
        (
            parts.scheme.lower(),
            parts.netloc.lower(),
            parts.path.rstrip("/"),
            urlencode(query),
            "",
        )
    )


def _title_key(value: str) -> str:
    return re.sub(r"[^a-zа-яё0-9]+", " ", value.lower()).strip()


def normalize_candidates(
    raw_items: list[dict],
    now: datetime | None = None,
) -> list[NewsCandidate]:
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    cutoff = current - timedelta(hours=24)
    accepted: list[NewsCandidate] = []

    for raw in raw_items:
        published_at = _parse_date(str(raw.get("date", "")))
        url = _canonical_url(str(raw.get("url", "")))
        title = str(raw.get("title", "")).strip()
        text = str(raw.get("text", "")).strip()
        if (
            not published_at
            or not (cutoff <= published_at <= current)
            or not url
            or not title
            or not text
        ):
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
        duplicate_index = next(
            (
                index
                for index, item in enumerate(accepted)
                if item.url == candidate.url
                or SequenceMatcher(
                    None,
                    _title_key(item.title),
                    _title_key(candidate.title),
                ).ratio()
                >= 0.92
            ),
            None,
        )
        if duplicate_index is None:
            accepted.append(candidate)
        elif len(candidate.text) > len(accepted[duplicate_index].text):
            accepted[duplicate_index] = candidate

    return sorted(accepted, key=lambda item: item.published_at, reverse=True)


def _clean_json_payload(payload: str) -> str:
    return re.sub(
        r"^```(?:json)?|```$",
        "",
        payload.strip(),
        flags=re.MULTILINE,
    ).strip()


def parse_ranked_items(
    payload: str,
    candidates: list[NewsCandidate],
) -> tuple[list[DigestItem], str]:
    data = json.loads(_clean_json_payload(payload))
    by_url = {item.url: item for item in candidates}
    selected: list[DigestItem] = []
    world_count = 0

    for raw in data.get("items", []):
        candidate = by_url.get(_canonical_url(str(raw.get("url", ""))))
        if candidate is None or any(
            item.candidate.article_id == candidate.article_id for item in selected
        ):
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


def format_digest(
    items: list[DigestItem],
    overview: str,
    greeting: str | None = None,
) -> str:
    parts = [
        html.escape(greeting or random.choice(GREETINGS)),
        "<b>Главное в экономике и бизнесе за последние 24 часа:</b>",
    ]
    digest_timezone = ZoneInfo(os.getenv("NEWS_TIMEZONE", "Asia/Almaty"))
    for index, item in enumerate(items, 1):
        flag = "🌍" if item.candidate.is_world else "🇰🇿"
        local_time = item.candidate.published_at.astimezone(digest_timezone).strftime(
            "%H:%M"
        )
        parts.append(
            f"{flag} <b>{index}. {html.escape(item.candidate.title)}</b>\n\n"
            f"{html.escape(item.summary)}\n\n"
            f"<b>Почему важно:</b> {html.escape(item.importance)}\n\n"
            f'🔗 <a href="{html.escape(item.candidate.url, quote=True)}">'
            f"{html.escape(item.candidate.source)}</a> · {local_time}"
        )
    if overview:
        parts.append(f"<b>Коротко о главном:</b> {html.escape(overview)}")
    parts.append("Хорошей и успешной недели! 🚀")
    return "\n\n".join(parts)


async def rank_and_summarize(
    candidates: list[NewsCandidate],
    client=None,
) -> tuple[list[DigestItem], str]:
    if client is None:
        from google import genai

        client = genai.Client(api_key=os.getenv("GOOGLE_API_KEY"))
    gemini_client = client
    candidate_data = [
        {
            "title": item.title,
            "text": item.text[:2500],
            "url": item.url,
            "source": item.source,
            "published_at": item.published_at.isoformat(),
            "scope": "world" if item.is_world else "kazakhstan",
        }
        for item in candidates[:30]
    ]
    prompt = (
        "Выбери до 5 важнейших новостей экономики и бизнеса. "
        "Казахстан имеет приоритет; мировых новостей максимум две. "
        "Используй только переданные URL. Для каждой дай summary из 2–3 "
        "коротких предложений и importance — одно практическое предложение. "
        'Верни только JSON: {"items":[{"url":"...","summary":"...",'
        '"importance":"..."}],"overview":"одна строка"}. Данные:\n'
        + json.dumps(candidate_data, ensure_ascii=False)
    )

    for attempt in range(2):
        try:
            response = await gemini_client.aio.models.generate_content(
                model=os.getenv("GEMINI_MODEL", "gemini-2.5-flash"),
                contents=prompt,
                config={"temperature": 0.1, "max_output_tokens": 1800},
            )
            return parse_ranked_items(response.text or "{}", candidates)
        except (json.JSONDecodeError, ValueError, TypeError) as exc:
            logger.warning(
                "[weekly_digest] Ошибка ранжирования, попытка %s: %s",
                attempt + 1,
                exc,
            )
            if attempt == 0:
                await asyncio.sleep(1)
    return [], ""


def _host_allowed(url: str, domains: tuple[str, ...]) -> bool:
    host = urlsplit(url).netloc.lower()
    return any(host == domain or host.endswith(f".{domain}") for domain in domains)


def _url_reachable(url: str) -> bool:
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


async def search_additional_news(
    now: datetime,
    client=None,
    url_checker=None,
) -> list[dict]:
    if client is None:
        from google import genai

        client = genai.Client(api_key=os.getenv("GOOGLE_API_KEY"))

    local_domains = tuple(
        domain.strip().lower()
        for domain in os.getenv(
            "NEWS_ALLOWED_DOMAINS",
            "nationalbank.kz,kgd.gov.kz,gov.kz,stat.gov.kz,uchet.kz,"
            "forbes.kz,kursiv.media,kapital.kz",
        ).split(",")
        if domain.strip()
    )
    world_domains = tuple(
        domain.strip().lower()
        for domain in os.getenv(
            "NEWS_WORLD_DOMAINS",
            "reuters.com,bloomberg.com,ft.com,worldbank.org,imf.org",
        ).split(",")
        if domain.strip()
    )
    current = now.astimezone(timezone.utc)
    cutoff = current - timedelta(hours=24)
    prompt = (
        "Найди важные новости экономики и бизнеса Казахстана и не более двух "
        "мировых новостей, влияющих на Казахстан. "
        f"Период публикации: от {cutoff.isoformat()} до {current.isoformat()}. "
        f"Источники Казахстана: {', '.join(local_domains)}. "
        f"Мировые источники: {', '.join(world_domains)}. "
        "Верни только JSON-массив объектов с полями title, text, url, source, "
        "date и is_world. Не включай материал без точной даты и прямого URL."
    )
    checker = url_checker or _url_reachable

    for attempt in range(2):
        try:
            response = await client.aio.models.generate_content(
                model=os.getenv("GEMINI_MODEL", "gemini-2.5-flash"),
                contents=prompt,
                config={
                    "temperature": 0.0,
                    "max_output_tokens": 3000,
                    "tools": [{"google_search": {}}],
                },
            )
            raw_items = json.loads(_clean_json_payload(response.text or "[]"))
            if not isinstance(raw_items, list):
                return []

            async def verify(raw: dict) -> dict | None:
                url = _canonical_url(str(raw.get("url", "")))
                is_world = bool(raw.get("is_world", False))
                allowed = _host_allowed(
                    url,
                    world_domains if is_world else local_domains,
                )
                if not url or not allowed:
                    return None
                if not await asyncio.to_thread(checker, url):
                    return None
                verified = dict(raw)
                verified["url"] = url
                verified["article_id"] = str(raw.get("article_id") or url)
                verified["is_world"] = is_world
                return verified

            checked = await asyncio.gather(
                *(verify(item) for item in raw_items[:20] if isinstance(item, dict))
            )
            return [item for item in checked if item is not None]
        except (json.JSONDecodeError, ValueError, TypeError) as exc:
            logger.warning(
                "[weekly_digest] Ошибка резервного поиска, попытка %s: %s",
                attempt + 1,
                exc,
            )
            if attempt == 0:
                await asyncio.sleep(1)
    return []


def split_digest(text: str, limit: int = 3900) -> list[str]:
    if limit <= 0:
        raise ValueError("limit must be positive")
    blocks = text.split("\n\n")
    chunks: list[str] = []
    current = ""
    for block in blocks:
        if len(block) > limit:
            raise ValueError("A digest block exceeds the Telegram message limit")
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


async def build_digest(
    now: datetime | None = None,
    fetcher=None,
    fallback_search=None,
    ranker=None,
) -> tuple[str, list[str]]:
    if fetcher is None:
        from bot.rag.news_parser import fetch_all_news

        fetcher = fetch_all_news
    fallback = fallback_search or search_additional_news
    select = ranker or rank_and_summarize
    current = now or datetime.now(timezone.utc)

    primary = await fetcher()
    candidates = normalize_candidates(primary, current)
    if len(candidates) < 5:
        additional = await fallback(current)
        candidates = normalize_candidates(primary + additional, current)
    if not candidates:
        raise RuntimeError("Не найдено проверенных новостей за последние 24 часа")

    items, overview = await select(candidates)
    if not items:
        raise RuntimeError("Gemini не выбрал ни одной проверенной новости")
    return format_digest(items, overview), [
        item.candidate.article_id for item in items
    ]


async def publish_digest(
    bot,
    chat_id: int,
    thread_id: int,
    publication_key: str,
    test_mode: bool = False,
    builder=None,
    already_published=None,
    recorder=None,
) -> bool:
    if already_published is None:
        from bot.rag.firebase_db import was_digest_published

        already_published = was_digest_published

    if not test_mode and already_published(publication_key):
        return False

    build = builder or build_digest
    text, article_ids = await build()
    for chunk in split_digest(text):
        await bot.send_message(
            chat_id=chat_id,
            message_thread_id=thread_id,
            text=chunk,
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
    if not test_mode:
        if recorder is None:
            from bot.rag.firebase_db import mark_digest_published

            recorder = mark_digest_published
        recorder(publication_key, article_ids)
    return True
