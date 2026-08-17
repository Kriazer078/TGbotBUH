import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from dateutil import parser as date_parser


@dataclass(frozen=True)
class NewsCandidate:
    article_id: str
    title: str
    text: str
    url: str
    source: str
    published_at: datetime
    is_world: bool = False


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
