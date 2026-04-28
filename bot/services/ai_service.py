import os
import logging
from datetime import datetime
import asyncio
import google.generativeai as genai
from dotenv import load_dotenv

from bot.rag.firebase_db import search_similar_articles, get_recent_news

load_dotenv()

logger = logging.getLogger(__name__)

# ── Gemini Конфигурация ───────────────────────────────────────────────────────
api_key = os.getenv("GOOGLE_API_KEY")
if api_key:
    genai.configure(api_key=api_key)
else:
    logger.error("GOOGLE_API_KEY не найден в .env!")

# ── Системная инструкция (Token Economy: сжата и перенесена в параметры модели) ──
_SYSTEM_INSTRUCTION = """Ты — «Главбух ИИ», эксперт по налогам и учёту в Казахстане 🇰🇿. 
Работаешь СТРОГО по законодательству РК (НК РК, ТК РК). Сегодня: {today}.

ПРАВИЛА ОТВЕТА:
1. Краткая суть (1-2 предл).
2. Обоснование: ссылка на статью (напр. «ст. 412 НК РК»).
3. ⚠️ Примечание: риски или сроки.
Стиль: деловой, сленг РК (ЭСФ, ФНО, КГД). HTML-теги: <b>, <i>, <code>.

РАСЧЁТ ЗП (очерёдность): ОПВ(10%) -> ВОСМС(2%) -> ИПН(база = доход-ОПВ-ВОСМС-1МЗП). 
СО(3.5%), ОСМС(3%), ОПВр.

Если нет ответа в базе: «В законе нет однозначной трактовки. Рекомендую запрос в КГД через e-Otinish.»
📌 Справочно. adilet.zan.kz"""

def _get_model():
    """Инициализирует модель с системной инструкцией."""
    today = datetime.now().strftime("%d.%m.%Y")
    return genai.GenerativeModel(
        model_name=os.getenv("GEMINI_MODEL", "gemini-1.5-flash"),
        system_instruction=_SYSTEM_INSTRUCTION.format(today=today),
        generation_config={
            "temperature": 0.2,
            "max_output_tokens": 1024, # Экономия на длине ответа
        }
    )

async def embed_text(text: str) -> list:
    """
    Генерирует эмбеддинг для текста с использованием Google Native SDK.
    """
    try:
        emb_result = await asyncio.to_thread(
            genai.embed_content,
            model="models/gemini-embedding-2",
            content=text,
            task_type="retrieval_document" # Для документов используем этот тип
        )
        return emb_result['embedding']
    except Exception as e:
        logger.error(f"Ошибка эмбеддинга: {e}")
        return []

async def get_ai_response(user_text: str) -> str:
    """
    Основная функция получения ответа от Gemini.
    """
    try:
        # 1. Эмбеддинг (Google Native)
        query_embedding = await embed_text(user_text)
        if not query_embedding:
            raise ValueError("Не удалось получить эмбеддинг")

        # 2. RAG: поиск в базе знаний (НК РК)
        # Token Economy: берем 2 самые релевантные статьи вместо 3
        rag_articles = await search_similar_articles(query_embedding, top_k=2)

        # 3. Свежие новости
        news = await asyncio.to_thread(get_recent_news, 3) # Берем 3 новости вместо 5

        # 4. Формируем контекст (сжато)
        context = ""
        if rag_articles:
            context += "\n[БАЗА ЗНАНИЙ]:\n"
            for art in rag_articles:
                context += f"- {art['title']}: {art['text'][:800]}\n" # Сжато до 800 симв.
        
        if news:
            context += "\n[НОВОСТИ]:\n"
            for n in news:
                context += f"- {n['title']} ({n['source']}): {n['text'][:400]}\n"

        # 5. Запрос к Gemini
        model = _get_model()
        full_prompt = f"КОНТЕКСТ РК:\n{context}\n\nВОПРОС: {user_text}"
        
        response = await model.generate_content_async(full_prompt)
        return response.text

    except Exception as e:
        logger.error(f"[ai_service] Ошибка Gemini: {e}")
        return (
            "⚠️ Техническая заминка. Пожалуйста, повторите вопрос позже."
        )
