import logging
import requests
from bs4 import BeautifulSoup
from datetime import datetime
from bot.rag.firebase_db import save_articles_to_firebase

logger = logging.getLogger(__name__)

TAX_CODE_URL = "https://adilet.zan.kz/rus/docs/K1700000120"

async def fetch_tax_code_html():
    """
    Скачивает актуальный HTML Налогового Кодекса с adilet.zan.kz.
    """
    try:
        logger.info(f"Скачивание Налогового кодекса РК: {TAX_CODE_URL}")
        response = requests.get(TAX_CODE_URL, timeout=30)
        response.raise_for_status()
        return response.text
    except Exception as e:
        logger.error(f"Ошибка при скачивании Налогового кодекса: {e}")
        return None

def parse_tax_code(html_content: str):
    """
    Парсит HTML, извлекая статьи, их заголовки и текст.
    """
    soup = BeautifulSoup(html_content, "html.parser")
    
    # В adilet.zan.kz текст обычно находится в <div id="doc_text">
    doc_text = soup.find("div", id="doc_text")
    if not doc_text:
        logger.error("Не найден div id='doc_text' на странице.")
        return []

    articles = []
    # Логика разбивки на чанки будет здесь.
    # В Adilet заголовки статей часто пишутся как "Статья 1. ..." или имеют классы.
    # Это базовая реализация, которую можно расширять (semantic chunking).
    
    # Пример простой нарезки по параграфам:
    paragraphs = doc_text.find_all("p")
    current_article_title = "Общие положения"
    current_text = []

    for p in paragraphs:
        text = p.get_text(strip=True)
        if not text:
            continue
            
        if text.startswith("Статья "):
            # Сохраняем предыдущую статью
            if current_text:
                articles.append({
                    "title": current_article_title,
                    "text": "\n".join(current_text),
                    "url": TAX_CODE_URL
                })
            current_article_title = text
            current_text = [text]
        else:
            current_text.append(text)
            
    # Сохраняем последнюю статью
    if current_text:
        articles.append({
            "title": current_article_title,
            "text": "\n".join(current_text),
            "url": TAX_CODE_URL
        })

    logger.info(f"Налоговый кодекс распарсен. Найдено {len(articles)} статей/разделов.")
    return articles

async def check_for_updates():
    """
    Проверяет, были ли изменения в законе, парсит и сохраняет в Firebase.
    """
    logger.info("Начало проверки обновлений законодательства...")
    html = await fetch_tax_code_html()
    if html:
        articles = parse_tax_code(html)
        if articles:
            logger.info(f"Успешно получены актуальные данные ({len(articles)} частей). Готов к векторизации.")
            
            # В реальном проекте здесь нужно генерировать эмбеддинги
            # Для простоты и избежания блокировок API, мы загрузим первые 5 статей для теста
            # или используем клиент из ai_service.py
            try:
                from bot.services.ai_service import embed_text
                articles_with_embeddings = []
                
                # Тестовая загрузка первых 10 статей, чтобы не превысить лимиты API за раз
                for article in articles[:10]:
                    try:
                        embedding = await embed_text(article["text"][:8000])
                        if embedding:
                            article["embedding"] = embedding
                            articles_with_embeddings.append(article)
                    except Exception as e:
                        logger.error(f"Ошибка получения эмбеддинга: {e}")
                
                if articles_with_embeddings:
                    await save_articles_to_firebase(articles_with_embeddings)
                    logger.info("База данных успешно обновлена новыми статьями.")
                    return True
            except Exception as e:
                logger.error(f"Ошибка при сохранении обновлений в базу: {e}")
                
    return False
