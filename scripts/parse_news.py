import sys
import os
import re
import html
import hashlib
import feedparser
from datetime import datetime

# Добавляем корневую папку проекта в sys.path, чтобы импорты работали
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import bot.rag.firebase_db as firebase_db
import io

# Настройка кодировки для Windows
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')


def clean_html(raw_text: str) -> str:
    """Очищает HTML-разметку и декодирует entities в чистый текст."""
    # 1. Декодируем HTML-entities: &amp; -> &, &lt; -> <, &nbsp; -> пробел
    text = html.unescape(raw_text)
    # Иногда бывает двойное экранирование, поэтому можно сделать еще раз
    text = html.unescape(text)
    # 2. Убираем все HTML-теги (теперь они выглядят как <...>)
    text = re.sub(r'<[^>]+>', ' ', text)
    # 3. Убираем неразрывные пробелы
    text = text.replace('\xa0', ' ')
    # 4. Схлопываем множественные пробелы и переносы
    text = re.sub(r'\s+', ' ', text).strip()
    return text


def parse_and_save_news():
    # Инициализируем Firebase
    if not firebase_db.init_firebase():
        print("Не удалось инициализировать Firebase.")
        return

    db = firebase_db.db
    if db is None:
        print("База данных не была инициализирована, проверьте учетные данные.")
        return

    # RSS-ленты официальных порталов и бух. сайтов РК.
    RSS_FEEDS = {
        "Uchet.kz": "https://uchet.kz/news/rss/",
        "Tengrinews.kz": "https://tengrinews.kz/news.rss",
    }

    collection_ref = db.collection('news_feed')
    news_count = 0

    for source_name, url in RSS_FEEDS.items():
        print(f"Парсинг: {source_name} ({url})")
        feed = feedparser.parse(url)

        if feed.bozo:
            print(f"Предупреждение (XML): {feed.bozo_exception}")

        if not feed.entries:
            print("Нет новостей для парсинга.")
            continue

        for entry in feed.entries[:10]:
            title = html.unescape(entry.title)
            link = entry.link

            # Фильтрация по ключевым словам для отсева нерелевантных новостей
            ACCOUNTING_KEYWORDS = [
                "закон", "кодекс", "налог", "бухгалтер", "учет", "изменени", 
                "мрп", "мзп", "деклараци", "ндс", "ипн", "штраф", "отчетност"
            ]
            title_lower = title.lower()
            if not any(kw in title_lower for kw in ACCOUNTING_KEYWORDS):
                continue

            # Выбираем самый полный источник текста
            raw_text = ""
            if 'yandex_full-text' in entry:
                raw_text = entry['yandex_full-text']
            elif 'content' in entry and entry.content:
                raw_text = entry.content[0].value
            elif 'summary' in entry:
                raw_text = entry.summary
            elif 'description' in entry:
                raw_text = entry.description

            clean_text = clean_html(raw_text)

            # Дата публикации
            try:
                from dateutil import parser as dateparser
                pub_date = dateparser.parse(entry.published).strftime("%d.%m.%Y")
            except Exception:
                pub_date = datetime.now().strftime("%d.%m.%Y")

            # ID = хэш ссылки, чтобы не было дублей
            doc_id = hashlib.md5(link.encode('utf-8')).hexdigest()
            doc_ref = collection_ref.document(doc_id)

            if not doc_ref.get().exists:
                doc_ref.set({
                    "title": title,
                    "text": clean_text,
                    "url": link,
                    "source": source_name,
                    "date": pub_date,
                    "saved_at": firestore.SERVER_TIMESTAMP
                })
                print(f"[+] Добавлено: {title}")
                news_count += 1
            else:
                print(f"[~] Уже есть в базе: {title}")

    print(f"Готово! Добавлено новых новостей: {news_count}")


if __name__ == "__main__":
    from firebase_admin import firestore
    from dotenv import load_dotenv
    load_dotenv()

    parse_and_save_news()
