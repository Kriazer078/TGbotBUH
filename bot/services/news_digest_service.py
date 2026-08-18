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
from bs4 import BeautifulSoup
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
    "Доброе утро, коллеги! ☀️ Коротко о важном в экономике и бизнесе.",
    "Коллеги, держим руку на пульсе: главные новости за последние сутки. 📈",
    "Всем продуктивного дня! ☕ Собрали только то, что может быть важно для бизнеса.",
    "Доброе утро! 🌤 Ниже — проверенные новости без лишнего шума.",
    "Коллеги, начинаем день с краткой деловой повестки. 🚀",
)
DIGEST_SECTION_SEPARATOR = "\n\n──────────\n\n"
TELEGRAM_NEWS_CHANNELS = (
    ("prg_jur", "ZANGER | PRG"),
    ("commentariuskz", "Комментарий"),
)
RANKING_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "maxItems": 5,
            "items": {
                "type": "object",
                "properties": {
                    "url": {"type": "string"},
                    "summary": {"type": "string"},
                    "importance": {"type": "string"},
                },
                "required": ["url", "summary", "importance"],
            },
        },
        "overview": {"type": "string"},
    },
    "required": ["items", "overview"],
}


def digest_schedule(env: dict[str, str] | None = None) -> dict:
    values = os.environ if env is None else env
    raw_time = values.get("NEWS_SCHEDULE_TIME", "09:30")
    try:
        hour_text, minute_text = raw_time.split(":", 1)
        hour, minute = int(hour_text), int(minute_text)
    except (AttributeError, TypeError, ValueError) as exc:
        raise ValueError("NEWS_SCHEDULE_TIME must use HH:MM format") from exc
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError("NEWS_SCHEDULE_TIME must use HH:MM format")
    return {
        "day_of_week": values.get("NEWS_SCHEDULE_DAY", "mon"),
        "hour": hour,
        "minute": minute,
        "timezone": values.get("NEWS_TIMEZONE", "Asia/Almaty"),
    }


def _parse_date(value: str) -> datetime | None:
    has_time = re.search(r"\d{1,2}:\d{2}", value or "")
    has_date = re.search(
        r"(?:\d{4}-\d{1,2}-\d{1,2}|"
        r"\d{1,2}[./]\d{1,2}[./]\d{2,4}|"
        r"\d{1,2}\s+[A-Za-z]{3,9}\s+\d{4})",
        value or "",
    )
    if not value or not has_time or not has_date:
        return None
    try:
        parsed = date_parser.parse(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(
            tzinfo=ZoneInfo(os.getenv("NEWS_TIMEZONE", "Asia/Almaty"))
        )
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


def parse_telegram_feed(payload: str, channel: str, source: str) -> list[dict]:
    """Parse stable, timestamped text posts from a public Telegram page."""
    soup = BeautifulSoup(payload, "html.parser")
    items = []
    for message in soup.select(".tgme_widget_message[data-post]"):
        data_post = str(message.get("data-post", "")).strip()
        if not data_post.startswith(f"{channel}/"):
            continue
        message_id = data_post.split("/", 1)[1]
        if not message_id.isdigit():
            continue
        time_node = message.select_one("time[datetime]")
        text_node = message.select_one(".tgme_widget_message_text")
        published = str(time_node.get("datetime", "")).strip() if time_node else ""
        text = text_node.get_text(" ", strip=True) if text_node else ""
        if not published or len(text) < 20:
            continue
        kazakhstan_signals = (
            "казахстан", "казах", "тенге", "нбк", "нацбанк", "kase",
            "алматы", "астана", " рк ",
        )
        searchable = f" {text.lower()} "
        is_world = channel == "commentariuskz" and not any(
            signal in searchable for signal in kazakhstan_signals
        )
        items.append(
            {
                "article_id": f"telegram:{data_post}",
                "title": _clip_text(text, 180),
                "text": text,
                "url": f"https://t.me/{data_post}",
                "source": source,
                "date": published,
                "is_world": is_world,
            }
        )
    return items


async def fetch_telegram_news(http_get=None) -> list[dict]:
    """Fetch configured public channels without requiring Telegram credentials."""
    getter = http_get or requests.get

    async def fetch(channel: str, source: str) -> list[dict]:
        try:
            response = await asyncio.to_thread(
                getter,
                f"https://t.me/s/{channel}",
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=10,
            )
            response.raise_for_status()
            return parse_telegram_feed(response.text, channel, source)
        except requests.RequestException as exc:
            logger.warning("[weekly_digest] Telegram %s unavailable: %s", channel, exc)
            return []

    batches = await asyncio.gather(
        *(fetch(channel, source) for channel, source in TELEGRAM_NEWS_CHANNELS)
    )
    return [item for batch in batches for item in batch]


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
    data = json.loads(_clean_json_payload(payload), strict=False)
    if not isinstance(data, dict):
        raise ValueError("Gemini response must be a JSON object")
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


def _clip_text(value: str, limit: int) -> str:
    cleaned = " ".join(value.split())
    if len(cleaned) <= limit:
        return cleaned
    shortened = cleaned[: limit - 1].rsplit(" ", 1)[0].rstrip(".,;:—- ")
    return f"{shortened}…"


def format_digest(
    items: list[DigestItem],
    overview: str,
    greeting: str | None = None,
) -> str:
    sections = [
        f"{html.escape(greeting or random.choice(GREETINGS))}\n\n"
        "<b>Главное в экономике и бизнесе за последние 24 часа:</b>"
    ]
    digest_timezone = ZoneInfo(os.getenv("NEWS_TIMEZONE", "Asia/Almaty"))
    for index, item in enumerate(items, 1):
        flag = "🌍" if item.candidate.is_world else "🇰🇿"
        local_time = item.candidate.published_at.astimezone(digest_timezone).strftime(
            "%H:%M"
        )
        sections.append(
            f"{flag} <b>{index}. {html.escape(_clip_text(item.candidate.title, 240))}</b>\n\n"
            f"{html.escape(_clip_text(item.summary, 1200))}\n\n"
            f"<b>Почему важно:</b> {html.escape(_clip_text(item.importance, 600))}\n\n"
            f'🔗 <a href="{html.escape(item.candidate.url, quote=True)}">'
            f"{html.escape(_clip_text(item.candidate.source, 120))}</a> · {local_time}"
        )
    closing = []
    if overview:
        closing.append(
            f"<b>Коротко о главном:</b> "
            f"{html.escape(_clip_text(overview, 600))}"
        )
    closing.append("Хорошей и успешной недели! 🚀")
    sections.append("\n\n".join(closing))
    return DIGEST_SECTION_SEPARATOR.join(sections)


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
        "Игнорируй рекламу, развлечения и общую политику. Юридические новости "
        "выбирай только если они практически влияют на бизнес. "
        "Используй только переданные URL. Для каждой дай summary из 2–3 "
        "коротких предложений и importance — одно практическое предложение. "
        'Верни только JSON: {"items":[{"url":"...","summary":"...",'
        '"importance":"..."}],"overview":"одна строка"}. Данные:\n'
        + json.dumps(candidate_data, ensure_ascii=False)
    )

    for attempt in range(2):
        try:
            response = await asyncio.wait_for(
                gemini_client.aio.models.generate_content(
                    model=os.getenv("GEMINI_MODEL", "gemini-2.5-flash"),
                    contents=prompt,
                    config={
                        "temperature": 0.1,
                        "max_output_tokens": 8000,
                        "thinking_config": {"thinking_budget": 0},
                        "response_mime_type": "application/json",
                        "response_schema": RANKING_RESPONSE_SCHEMA,
                    },
                ),
                timeout=float(os.getenv("NEWS_AI_TIMEOUT_SECONDS", "30")),
            )
            return parse_ranked_items(response.text or "{}", candidates)
        except Exception as exc:
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


def _inspect_source_page(url: str) -> dict | None:
    try:
        response = requests.get(
            url,
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=8,
            stream=True,
            allow_redirects=True,
        )
        response.raise_for_status()
    except requests.RequestException:
        return None

    final_url = _canonical_url(response.url)
    soup = BeautifulSoup(response.text, "html.parser")
    title_node = (
        soup.select_one('meta[property="og:title"][content]')
        or soup.select_one("h1")
        or soup.select_one("title")
    )
    if title_node is None:
        return None
    title = (
        title_node.get("content", "")
        if title_node.name == "meta"
        else title_node.get_text(" ", strip=True)
    )
    date_node = (
        soup.select_one('meta[property="article:published_time"][content]')
        or soup.select_one('meta[name="date"][content]')
        or soup.select_one('meta[itemprop="datePublished"][content]')
        or soup.select_one("time[datetime]")
        or soup.select_one("time")
    )
    if date_node is None:
        return None
    published = date_node.get("content") or date_node.get("datetime") or date_node.get_text(
        " ", strip=True
    )
    content = soup.select_one("article") or soup.select_one("main") or soup.body
    text = content.get_text(" ", strip=True) if content else ""
    if not final_url or not title.strip() or not published.strip() or len(text) < 40:
        return None
    return {
        "final_url": final_url,
        "title": title.strip(),
        "text": text,
        "date": published.strip(),
    }


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
    checker = url_checker or _inspect_source_page

    for attempt in range(2):
        try:
            response = await asyncio.wait_for(
                client.aio.models.generate_content(
                    model=os.getenv("GEMINI_MODEL", "gemini-2.5-flash"),
                    contents=prompt,
                    config={
                        "temperature": 0.0,
                        "max_output_tokens": 3000,
                        "tools": [{"google_search": {}}],
                    },
                ),
                timeout=float(os.getenv("NEWS_AI_TIMEOUT_SECONDS", "30")),
            )
            raw_items = json.loads(
                _clean_json_payload(response.text or "[]"), strict=False
            )
            if not isinstance(raw_items, list) or any(
                not isinstance(item, dict) for item in raw_items
            ):
                raise ValueError("Gemini search response must be a JSON array of objects")

            async def verify(raw: dict) -> dict | None:
                url = _canonical_url(str(raw.get("url", "")))
                is_world = bool(raw.get("is_world", False))
                allowed = _host_allowed(
                    url,
                    world_domains if is_world else local_domains,
                )
                if not url or not allowed:
                    return None
                page = await asyncio.to_thread(checker, url)
                if not isinstance(page, dict):
                    return None
                final_url = _canonical_url(str(page.get("final_url", "")))
                if not final_url or not _host_allowed(
                    final_url,
                    world_domains if is_world else local_domains,
                ):
                    return None
                verified_title = str(page.get("title", "")).strip()
                verified_text = str(page.get("text", "")).strip()
                verified_date = str(page.get("date", "")).strip()
                if not verified_title or not verified_text or not _parse_date(verified_date):
                    return None
                verified = dict(raw)
                verified["url"] = final_url
                verified["title"] = verified_title
                verified["text"] = verified_text
                verified["date"] = verified_date
                verified["article_id"] = str(raw.get("article_id") or final_url)
                verified["is_world"] = is_world
                return verified

            checked = await asyncio.gather(
                *(verify(item) for item in raw_items[:20])
            )
            return [item for item in checked if item is not None]
        except Exception as exc:
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
    separator = (
        DIGEST_SECTION_SEPARATOR
        if DIGEST_SECTION_SEPARATOR in text
        else "\n\n"
    )
    blocks = text.split(separator)
    chunks: list[str] = []
    current = ""
    for block in blocks:
        if len(block) > limit:
            raise ValueError("A digest block exceeds the Telegram message limit")
        candidate = f"{current}{separator}{block}" if current else block
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
    telegram_fetcher=None,
    fallback_search=None,
    ranker=None,
) -> tuple[str, list[str]]:
    if fetcher is None:
        from bot.rag.news_parser import fetch_all_news

        fetcher = fetch_all_news
    telegram = telegram_fetcher or fetch_telegram_news
    fallback = fallback_search or search_additional_news
    select = ranker or rank_and_summarize
    current = now or datetime.now(timezone.utc)

    primary, telegram_items = await asyncio.gather(fetcher(), telegram())
    candidates = normalize_candidates(telegram_items + primary, current)
    if len(candidates) < 5 or not any(item.is_world for item in candidates):
        additional = await fallback(current)
        candidates = normalize_candidates(telegram_items + primary + additional, current)
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
    claimer=None,
    recorder=None,
    releaser=None,
    failure_recorder=None,
    chunk_limit: int = 3900,
) -> bool:
    claimed = False
    sent_chunks = 0
    attempted_chunks = 0
    if not test_mode:
        if claimer is None:
            from bot.rag.firebase_db import claim_digest_publication

            claimer = claim_digest_publication
        if not claimer(publication_key):
            return False
        claimed = True

    try:
        build = builder or build_digest
        text, article_ids = await build()
        for chunk in split_digest(text, limit=chunk_limit):
            attempted_chunks += 1
            await bot.send_message(
                chat_id=chat_id,
                message_thread_id=thread_id,
                text=chunk,
                parse_mode="HTML",
                disable_web_page_preview=True,
            )
            sent_chunks += 1
        if claimed:
            if recorder is None:
                from bot.rag.firebase_db import mark_digest_published

                recorder = mark_digest_published
            recorder(publication_key, article_ids)
        return True
    except Exception:
        if claimed and attempted_chunks == 0:
            if releaser is None:
                from bot.rag.firebase_db import release_digest_claim

                releaser = release_digest_claim
            releaser(publication_key)
        elif claimed and attempted_chunks > 0:
            if failure_recorder is None:
                from bot.rag.firebase_db import mark_digest_partial_failure

                failure_recorder = mark_digest_partial_failure
            failure_recorder(
                publication_key,
                sent_chunks,
                attempted_chunks > sent_chunks,
            )
        raise
