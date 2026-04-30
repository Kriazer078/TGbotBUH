"""
news_parser.py — Парсер актуальных новостей для бухгалтеров РК.

Источники:
  1. uchet.kz      — практические статьи, разъяснения, изменения
  2. adilet.zan.kz — последние изменения в законодательстве
"""

import logging
import hashlib
import asyncio
from datetime import datetime
from typing import Optional

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

# ─── Заголовки браузера, чтобы не получить блокировку ─────────────────────────
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ru-RU,ru;q=0.9",
}
REQUEST_TIMEOUT = 20  # секунд


# ══════════════════════════════════════════════════════════════════════════════
# Вспомогательные функции
# ══════════════════════════════════════════════════════════════════════════════

def _safe_get(url: str) -> Optional[str]:
    """Безопасный GET-запрос. Возвращает HTML или None при ошибке."""
    try:
        resp = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        resp.encoding = resp.apparent_encoding  # авто-определение кодировки
        return resp.text
    except requests.RequestException as e:
        logger.warning(f"[news_parser] Ошибка запроса к {url}: {e}")
        return None


def _make_id(url: str, title: str) -> str:
    """Создаёт стабильный ID статьи из URL + заголовка."""
    raw = f"{url}:{title}"
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


# ══════════════════════════════════════════════════════════════════════════════
# ИСТОЧНИК 1: uchet.kz
# ══════════════════════════════════════════════════════════════════════════════

UCHET_SECTIONS = [
    "https://uchet.kz/articles/",       # Статьи
    "https://uchet.kz/news/",           # Новости (будут фильтроваться)
]

# Ключевые слова для фильтрации новостей, чтобы брать только законы, налоги и бухгалтерию
ACCOUNTING_KEYWORDS = [
    "закон", "кодекс", "налог", "бухгалтер", "учет", "изменени", 
    "мрп", "мзп", "деклараци", "ндс", "ипн", "штраф", "отчетност"
]

def parse_uchet_kz(html: str, section_url: str) -> list[dict]:
    """
    Парсит список статей/новостей с uchet.kz.
    Фильтрует по ключевым словам (законы, налоги, бухгалтерия).
    Возвращает список словарей с полями: title, text, url, source, date, article_id.
    """
    soup = BeautifulSoup(html, "html.parser")
    articles = []

    # Карточки статей на uchet.kz имеют класс .article-card или .news-item
    cards = (
        soup.select(".article-card")
        or soup.select(".news-item")
        or soup.select("article")
        or soup.select(".entry-card")
    )

    for card in cards[:30]:  # берём больше карточек для фильтрации
        # ── Заголовок ──────────────────────────────────────────────────────
        title_tag = card.select_one("h2, h3, .article-title, .entry-title")
        title = title_tag.get_text(strip=True) if title_tag else ""
        if not title:
            continue
            
        # Фильтрация по ключевым словам
        title_lower = title.lower()
        if not any(kw in title_lower for kw in ACCOUNTING_KEYWORDS):
            continue

        # ── Ссылка ─────────────────────────────────────────────────────────
        link_tag = card.select_one("a[href]")
        href = link_tag["href"] if link_tag else ""
        if href and not href.startswith("http"):
            href = "https://uchet.kz" + href

        # ── Краткий текст (анонс) ──────────────────────────────────────────
        excerpt_tag = card.select_one(".excerpt, .entry-excerpt, p")
        excerpt = excerpt_tag.get_text(strip=True) if excerpt_tag else ""

        # ── Дата ───────────────────────────────────────────────────────────
        date_tag = card.select_one("time, .date, .post-date")
        date_str = date_tag.get("datetime", date_tag.get_text(strip=True)) if date_tag else ""

        articles.append({
            "article_id": _make_id(href or section_url, title),
            "title": title,
            "text": f"{title}\n\n{excerpt}",
            "url": href,
            "source": "uchet.kz",
            "date": date_str,
        })

    logger.info(f"[uchet.kz] {section_url} → найдено {len(articles)} статей (после фильтрации)")
    return articles


async def fetch_uchet_news() -> list[dict]:
    """Асинхронно загружает новости со всех разделов uchet.kz."""
    all_articles: list[dict] = []
    for section_url in UCHET_SECTIONS:
        html = await asyncio.to_thread(_safe_get, section_url)
        if html:
            all_articles.extend(parse_uchet_kz(html, section_url))
    return all_articles


# ══════════════════════════════════════════════════════════════════════════════
# ИСТОЧНИК 2: adilet.zan.kz — последние правовые акты
# ══════════════════════════════════════════════════════════════════════════════

# Поисковый фид по теме «налоговый» — последние документы
ADILET_SEARCH_URLS = [
    "https://adilet.zan.kz/rus/search/docs/?phrase=%D0%BD%D0%B0%D0%BB%D0%BE%D0%B3%D0%BE%D0%B2%D1%8B%D0%B9&sort=date",
    "https://adilet.zan.kz/rus/search/docs/?phrase=%D0%B1%D1%83%D1%85%D0%B3%D0%B0%D0%BB%D1%82%D0%B5%D1%80%D1%81%D0%BA%D0%B8%D0%B9&sort=date",
]
ADILET_BASE = "https://adilet.zan.kz"


def parse_adilet_search(html: str, search_url: str) -> list[dict]:
    """
    Парсит страницу поиска adilet.zan.kz и возвращает последние документы.
    """
    soup = BeautifulSoup(html, "html.parser")
    articles = []

    # Строки результатов поиска
    rows = soup.select(".search-result-item, .result-item, tr.doc-row")

    # Если стандартных классов нет — пробуем универсальный подход
    if not rows:
        rows = soup.select("ul.search-results li, .search-results .item")

    for row in rows[:15]:
        title_tag = row.select_one("a.doc-link, a.result-title, a[href*='/docs/']")
        if not title_tag:
            title_tag = row.select_one("a[href]")
        if not title_tag:
            continue

        title = title_tag.get_text(strip=True)
        href = title_tag.get("href", "")
        if href and not href.startswith("http"):
            href = ADILET_BASE + href

        date_tag = row.select_one(".doc-date, .date, time")
        date_str = date_tag.get_text(strip=True) if date_tag else ""

        desc_tag = row.select_one(".doc-desc, .description, p")
        desc = desc_tag.get_text(strip=True) if desc_tag else ""

        if not title:
            continue

        articles.append({
            "article_id": _make_id(href or search_url, title),
            "title": title,
            "text": f"{title}\n\n{desc}",
            "url": href,
            "source": "adilet.zan.kz",
            "date": date_str,
        })

    logger.info(f"[adilet.zan.kz] {search_url} → найдено {len(articles)} документов")
    return articles


async def fetch_adilet_news() -> list[dict]:
    """Асинхронно загружает последние акты с adilet.zan.kz."""
    all_articles: list[dict] = []
    for search_url in ADILET_SEARCH_URLS:
        html = await asyncio.to_thread(_safe_get, search_url)
        if html:
            all_articles.extend(parse_adilet_search(html, search_url))
    return all_articles


# ══════════════════════════════════════════════════════════════════════════════
# Главная функция: сбор всех новостей
# ══════════════════════════════════════════════════════════════════════════════

async def fetch_all_news() -> list[dict]:
    """
    Запускает параллельный сбор новостей со всех источников.
    Возвращает дедублированный список статей.
    """
    logger.info("[news_parser] Начало сбора новостей со всех источников...")

    uchet_task = asyncio.create_task(fetch_uchet_news())
    adilet_task = asyncio.create_task(fetch_adilet_news())

    uchet_news, adilet_news = await asyncio.gather(uchet_task, adilet_task)

    all_news = uchet_news + adilet_news

    # Дедупликация по article_id
    seen_ids: set[str] = set()
    unique_news: list[dict] = []
    for item in all_news:
        if item["article_id"] not in seen_ids:
            seen_ids.add(item["article_id"])
            unique_news.append(item)

    logger.info(
        f"[news_parser] Итого собрано {len(unique_news)} уникальных материалов "
        f"(uchet.kz: {len(uchet_news)}, adilet.zan.kz: {len(adilet_news)})"
    )
    return unique_news


# ══════════════════════════════════════════════════════════════════════════════
# Сохранение новостей в Firebase (без эмбеддингов — быстрый путь)
# ══════════════════════════════════════════════════════════════════════════════

async def save_news_to_firebase(articles: list[dict]) -> int:
    """
    Сохраняет новости в коллекцию 'news_feed' в Firestore.
    Использует article_id как ключ документа — повторная запись не дублирует данные.
    Возвращает количество новых (ранее не существовавших) документов.
    """
    from bot.rag.firebase_db import db

    if not db:
        logger.error("[news_parser] Firebase не инициализирован.")
        return 0

    new_count = 0
    collection_ref = db.collection("news_feed")
    now_str = datetime.utcnow().isoformat()

    for item in articles:
        doc_ref = collection_ref.document(item["article_id"])
        snapshot = doc_ref.get()

        if not snapshot.exists:
            doc_ref.set({
                "title": item["title"],
                "text": item["text"],
                "url": item["url"],
                "source": item["source"],
                "date": item.get("date", ""),
                "saved_at": now_str,
            })
            new_count += 1

    logger.info(f"[news_parser] Сохранено {new_count} новых статей в Firebase (news_feed).")
    return new_count


# ══════════════════════════════════════════════════════════════════════════════
# Точка входа для ручного запуска / отладки
# ══════════════════════════════════════════════════════════════════════════════

async def run_news_update() -> int:
    """
    Полный цикл: сбор новостей → сохранение в Firebase.
    Возвращает количество новых статей.
    """
    articles = await fetch_all_news()
    if not articles:
        logger.warning("[news_parser] Новостей не получено.")
        return 0
    return await save_news_to_firebase(articles)
