import os
import re
import asyncio
import logging
from datetime import datetime

from google import genai
from google.genai import types as genai_types
from dotenv import load_dotenv

from bot.rag.firebase_db import (
    search_similar_articles, get_recent_news,
    save_dialog, get_similar_dialogs,
)

load_dotenv()
logger = logging.getLogger(__name__)

# ── Клиент Gemini (новый SDK google-genai) ─────────────────────────────────────
_api_key = os.getenv("GOOGLE_API_KEY")
if not _api_key:
    logger.error("GOOGLE_API_KEY не найден в .env!")

_client = genai.Client(api_key=_api_key)

# ── Актуальные ставки 2026 (НК РК, новый кодекс с 01.01.2026) ────────────────
# Источники: gov.kz, minfin.gov.kz, mybuh.kz, fexpert.com.kz
RATES_2026 = {
    # Базовые показатели
    "МЗП":           85_000,    # Закон о республиканском бюджете на 2026-2028
    "МРП":            4_325,    # Закон о республиканском бюджете на 2026-2028
    # Удержания с работника
    "ОПВ":             0.10,    # 10% от дохода, лимит 7 МЗП
    "ВОСМС":           0.02,    # 2% от дохода, лимит 20 МЗП
    "ИПН_1":           0.10,    # 10% — до 8500 МРП/год (новый НК РК)
    "ИПН_2":           0.15,    # 15% — свыше 8500 МРП/год (прогрессивная шкала)
    "ИПН_ПОРОГ_МРП":  8_500,    # Порог для перехода на ставку 15% (МРП/год)
    "ВЫЧЕТ_МРП":         30,    # Базовый налоговый вычет = 30 МРП/мес (новый НК РК)
    # Платежи работодателя
    "ОПВр":            0.035,   # 3.5% от дохода работника (новый НК РК)
    "СО":              0.05,    # 5% (новый НК РК, ст. по соц. отчислениям)
    "ОСМС":            0.03,    # 3%, лимит 40 МЗП
    "СН":              0.06,    # 6% (новый НК РК, без вычета СО)
    # Прочие налоги
    "НДС":             0.12,    # 12%
    "КПН":             0.20,    # 20%
}

# ── Системная инструкция ───────────────────────────────────────────────────────
_SYSTEM_INSTRUCTION = """Ты — «Главбух ИИ», персональный ИИ-ассистент бухгалтера Казахстана от компании «Open Consulting».
Сегодня: {today}. Новый НК РК действует с 01.01.2026.

ТВОЯ АУДИТОРИЯ — профессиональные бухгалтеры. Они знают термины. Им нужны быстрые, точные, практические ответы.

════════════════════════════════════
КАК ОТВЕЧАТЬ:

На профессиональный вопрос (расчёт, налог, отчётность, закон):
• Сразу давай ответ / результат расчёта — без вступлений.
• Коротко обоснуй: статья НК РК, ТК РК или иной НПА.
• Укажи сроки, риски, ограничения — если есть и важны.
• Ссылки на источники — только если тема требует официального подтверждения.

На общий вопрос (приветствие, кто ты, не по теме):
• Отвечай естественно и кратко. Без структуры.

ЗАПРЕЩЕНО:
• Писать «Конечно!», «Отличный вопрос!» и другие пустые фразы.
• Отвечать общими словами вместо конкретики.
• Придумывать ставки или ссылки — только достоверные данные.

════════════════════════════════════
АКТУАЛЬНЫЕ СТАВКИ 2026 (новый НК РК с 01.01.2026):
МЗП = 85 000 тг | МРП = 4 325 тг
ОПВ = 10% (лимит 7 МЗП = 595 000 тг) | ВОСМС = 2% (лимит 20 МЗП)
ИПН: 10% — до 8 500 МРП/год | 15% — свыше 8 500 МРП/год
Базовый налоговый вычет по ИПН = 30 МРП/мес = 129 750 тг (по заявлению)
СО = 5% | ОСМС = 3% (лимит 40 МЗП) | ОПВр = 3.5% | СН = 6%
НДС = 12% | КПН = 20%

════════════════════════════════════
РАСЧЁТ ЗП (новый НК РК 2026, X = начисленная):
1. ОПВ = X × 10%
2. ВОСМС = X × 2%
3. Вычет ИПН = 30 МРП = 129 750 тг (если работник подал заявление)
4. База ИПН = X − ОПВ − ВОСМС − 129 750 (если <0 → ИПН = 0)
5. ИПН = База × 10%
6. К выдаче = X − ОПВ − ВОСМС − ИПН
Начисления работодателя: СО = X×5%, ОСМС = X×3%, ОПВр = X×3.5%, СН = X×6%

════════════════════════════════════
ПОИСК (Google Search) — только официальные источники РК:
adilet.zan.kz | kgd.gov.kz | minfin.gov.kz | egov.kz | enbek.gov.kz

ФОРМАТИРОВАНИЕ: только HTML-теги <b>, <i>, <code>. Markdown (* и **) — ЗАПРЕЩЁН. Списки — символ •.
Если однозначного ответа нет — направь в КГД: <a href="https://cabinet.salyk.kz">e-Otinish</a>."""

# ── История по темам ───────────────────────────────────────────────────────────
thread_histories: dict[int, list] = {}
MAX_HISTORY = 20

# ══════════════════════════════════════════════════════════════════════════════
# ТРАНСКРИПЦИЯ ГОЛОСОВЫХ СООБЩЕНИЙ
# ══════════════════════════════════════════════════════════════════════════════

async def transcribe_voice(audio_bytes: bytes) -> str | None:
    """Транскрибирует голосовое сообщение (.ogg) через Gemini."""
    try:
        import base64
        audio_b64 = base64.b64encode(audio_bytes).decode("utf-8")
        response = await asyncio.to_thread(
            _client.models.generate_content,
            model="gemini-2.5-flash",
            contents=[
                {
                    "parts": [
                        {
                            "inline_data": {
                                "mime_type": "audio/ogg",
                                "data": audio_b64,
                            }
                        },
                        {"text": "Транскрибируй это аудио на русском языке. Верни ТОЛЬКО текст, без комментариев."},
                    ]
                }
            ],
        )
        text = (response.text or "").strip()
        return text if text else None
    except Exception as e:
        logger.error(f"[transcribe_voice] Ошибка: {e}")
        return None

# ══════════════════════════════════════════════════════════════════════════════
# ВСТРОЕННЫЙ КАЛЬКУЛЯТОР
# ══════════════════════════════════════════════════════════════════════════════

def _calc_salary(gross: float) -> str:
    r = RATES_2026
    opv    = round(gross * r["ОПВ"])
    vosms  = round(min(gross, 10 * r["МЗП"]) * r["ВОСМС"])
    vychet = r["ВЫЧЕТ_МРП"] * r["МРП"]
    base   = max(0.0, gross - opv - vosms - vychet)
    ipn    = round(base * r["ИПН_1"])  # 10% — базовая ставка до 8500 МРП/год
    netto  = round(gross - opv - vosms - ipn)
    so     = round(gross * r["СО"])
    osms   = round(gross * r["ОСМС"])
    opvr   = round(gross * r["ОПВр"])
    sn     = round(gross * r["СН"] - so)
    total  = round(gross + so + osms + opvr + sn)

    return (
        f"<b>📊 Расчёт ЗП: {gross:,.0f} тг (2026)</b>\n\n"
        f"<b>Удержания из ЗП работника:</b>\n"
        f"├ ОПВ (10%) = <code>{opv:,} тг</code>\n"
        f"├ ВОСМС (2%) = <code>{vosms:,} тг</code>\n"
        f"├ Вычет (14 МРП) = <code>{vychet:,} тг</code>\n"
        f"├ База ИПН = <code>{base:,.0f} тг</code>\n"
        f"├ ИПН (10%) = <code>{ipn:,} тг</code>\n"
        f"└ <b>К выдаче = <code>{netto:,} тг</code></b>\n\n"
        f"<b>Расходы работодателя (сверх ЗП):</b>\n"
        f"├ СО (3.5%) = <code>{so:,} тг</code>\n"
        f"├ ОСМС (3%) = <code>{osms:,} тг</code>\n"
        f"├ ОПВр (1.5%) = <code>{opvr:,} тг</code>\n"
        f"├ Соц. налог = <code>{sn:,} тг</code>\n"
        f"└ <b>Итого расход = <code>{total:,} тг</code></b>\n\n"
        f"<i>МЗП={r['МЗП']:,} тг, МРП={r['МРП']:,} тг | ст. 320, 341 НК РК</i>"
    )


def _calc_nds(amount: float, with_nds: bool = False) -> str:
    rate = RATES_2026["НДС"]
    if with_nds:
        base = round(amount / (1 + rate), 2)
        nds  = round(amount - base, 2)
        return (
            f"<b>📊 Выделение НДС из {amount:,.2f} тг</b>\n\n"
            f"├ Сумма без НДС = <code>{base:,.2f} тг</code>\n"
            f"├ НДС (12%) = <code>{nds:,.2f} тг</code>\n"
            f"└ Сумма с НДС = <code>{amount:,.2f} тг</code>\n\n"
            f"<i>ст. 422 НК РК</i>"
        )
    else:
        nds   = round(amount * rate, 2)
        total = round(amount + nds, 2)
        return (
            f"<b>📊 Расчёт НДС на {amount:,.2f} тг</b>\n\n"
            f"├ Сумма без НДС = <code>{amount:,.2f} тг</code>\n"
            f"├ НДС (12%) = <code>{nds:,.2f} тг</code>\n"
            f"└ Сумма с НДС = <code>{total:,.2f} тг</code>\n\n"
            f"<i>ст. 422 НК РК</i>"
        )


def _calc_depreciation(cost: float, residual: float, years: int) -> str:
    annual  = round((cost - residual) / years, 2)
    monthly = round(annual / 12, 2)
    return (
        f"<b>📊 Амортизация (линейный метод)</b>\n\n"
        f"├ Первоначальная стоимость = <code>{cost:,.2f} тг</code>\n"
        f"├ Остаточная стоимость = <code>{residual:,.2f} тг</code>\n"
        f"├ СПИ = <code>{years} лет</code>\n"
        f"├ Годовая амортизация = <code>{annual:,.2f} тг</code>\n"
        f"└ Ежемесячная = <code>{monthly:,.2f} тг</code>\n\n"
        f"<i>ст. 270–279 НК РК; МСФО IAS 16</i>"
    )


def _parse_and_calculate(text: str) -> str | None:
    """Распознаёт тип расчёта и возвращает результат или None."""
    t = text.lower()

    # ЗП
    if any(k in t for k in ["зп ", "зарплат", "оклад", "расчёт зп", "расчет зп"]):
        nums = re.findall(r'\d+(?:[.,]\d+)?', text)
        if nums:
            return _calc_salary(float(nums[0].replace(',', '.')))

    # НДС
    if "ндс" in t or "nds" in t:
        nums = re.findall(r'\d+(?:[.,]\d+)?', text)
        if nums:
            with_nds = any(p in t for p in ["с ндс", "включая ндс", "в т.ч", "выдели"])
            return _calc_nds(float(nums[0].replace(',', '.')), with_nds=with_nds)

    # Амортизация
    if any(k in t for k in ["амортизац", "спи "]):
        nums = [float(n.replace(',', '.')) for n in re.findall(r'\d+(?:[.,]\d+)?', text)]
        if len(nums) >= 3:
            return _calc_depreciation(nums[0], nums[1], int(nums[2]))
        if len(nums) == 2:
            return _calc_depreciation(nums[0], 0, int(nums[1]))

    return None

# ══════════════════════════════════════════════════════════════════════════════
# ЭМБЕДДИНГ
# ══════════════════════════════════════════════════════════════════════════════

async def embed_text(text: str) -> list:
    """Генерирует вектор текста через новый google-genai SDK."""
    try:
        result = await asyncio.to_thread(
            _client.models.embed_content,
            model="gemini-embedding-2",
            contents=text,
        )
        return result.embeddings[0].values
    except Exception as e:
        logger.error(f"[embed] Ошибка: {e}")
        return []

# ══════════════════════════════════════════════════════════════════════════════
# ГЛАВНАЯ ФУНКЦИЯ
# ══════════════════════════════════════════════════════════════════════════════

async def get_ai_response(
    user_text: str,
    thread_id: int = None,
    user_id: int = None,
) -> tuple | str:
    """
    Возвращает:
      (answer: str, doc_id: str) — ответ ИИ + ID для кнопок 👍/👎
      answer: str                — только текст (прямой расчёт калькулятором)
    """
    try:
        # ── 0. Встроенный калькулятор (мгновенно) ────────────────────────────
        calc_result = _parse_and_calculate(user_text)
        if calc_result:
            logger.info(f"[CALC] Прямой расчёт: {user_text[:60]}")
            return calc_result

        # ── 1. RAG: эмбеддинг + все запросы к Firebase параллельно ──────────
        query_embedding = await embed_text(user_text)

        context = ""
        if query_embedding:
            # Запускаем все три запроса к Firebase одновременно
            rag_articles, similar_dialogs, news = await asyncio.gather(
                search_similar_articles(query_embedding, 2),
                asyncio.to_thread(get_similar_dialogs, query_embedding, 1),
                asyncio.to_thread(get_recent_news, 2),
                return_exceptions=True,
            )
        else:
            rag_articles, similar_dialogs, news = [], [], []

        if isinstance(rag_articles, list) and rag_articles:
            context += "\n[БАЗА ЗНАНИЙ]:\n"
            for art in rag_articles:
                context += f"- {art['title']}: {art['text'][:600]}\n"

        if isinstance(similar_dialogs, list) and similar_dialogs:
            context += "\n[ПРОШЛЫЙ ОПЫТ]:\n"
            for dlg in similar_dialogs:
                context += f"Q: {dlg['question']}\nA: {dlg['answer'][:400]}\n"

        if isinstance(news, list) and news:
            context += "\n[НОВОСТИ]:\n"
            for n in news:
                context += f"- {n['title']}: {n['text'][:300]}\n"

        # ── 2. Финальный промпт ───────────────────────────────────────────────
        if context:
            full_prompt = f"Контекст:\n{context}\n\nВОПРОС: {user_text}"
        else:
            full_prompt = user_text

        # ── 3. Запрос к Gemini ────────────────────────────────────────────────
        today  = datetime.now().strftime("%d.%m.%Y")
        model  = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
        if model in ["gemini-1.5-flash", "gemini-1.5-pro", "gemini-2.0-flash", "text-embedding-004"]:
            model = "gemini-2.5-flash"
        system = _SYSTEM_INSTRUCTION.format(today=today)

        config = genai_types.GenerateContentConfig(
            system_instruction=system,
            temperature=0.1,
            max_output_tokens=1200,
            tools=[genai_types.Tool(google_search=genai_types.GoogleSearch())],
        )

        if thread_id is not None:
            history = thread_histories.get(thread_id, [])
        else:
            history = []

        messages = history + [
            genai_types.Content(
                role="user",
                parts=[genai_types.Part(text=full_prompt)]
            )
        ]

        response = await asyncio.to_thread(
            _client.models.generate_content,
            model=model,
            contents=messages,
            config=config,
        )

        # Извлекаем текст.
        # ВАЖНО: в google-genai SDK response.text может БРОСИТЬ исключение
        # (ValueError/AttributeError), а не вернуть None — особенно при Google Search.
        # Поэтому оборачиваем в try/except и сразу переходим к ручному разбору.
        answer = None
        try:
            answer = response.text
        except Exception as e:
            logger.warning(f"[ai] response.text недоступен: {e}")

        if not answer:
            try:
                parts = response.candidates[0].content.parts
                answer = "".join(p.text for p in parts if hasattr(p, 'text') and p.text)
            except Exception as e:
                logger.warning(f"[ai] Ручной разбор candidates не удался: {e}")
                answer = None
                
        # Конвертируем Markdown-жирный шрифт (**) в HTML (<b>)
        if answer:
            answer = re.sub(r'\*\*(.*?)\*\*', r'<b>\1</b>', answer)
            
        logger.info(f"[ai] Ответ получен, длина: {len(answer) if answer else 0} символов")

        # Обновляем историю
        if thread_id is not None:
            new_history = messages + [
                genai_types.Content(
                    role="model",
                    parts=[genai_types.Part(text=answer or "")]
                )
            ]
            thread_histories[thread_id] = new_history[-MAX_HISTORY:]
            logger.info(f"[thread={thread_id}] История: {len(thread_histories[thread_id])} сообщений")

        # ── 4. Проверка ответа ────────────────────────────────────────────────
        if not answer or not answer.strip():
            return "⚠️ Не удалось получить ответ. Пожалуйста, переформулируйте вопрос."

        # ── 5. Сохраняем диалог в Firebase (для обучения на ошибках) ─────────
        doc_id = await asyncio.to_thread(
            save_dialog,
            user_id or 0,
            user_text,
            answer,
            thread_id,
            query_embedding or None,
        )
        logger.info(f"[dialog] Сохранён: {doc_id}")

        return answer, doc_id

    except Exception as e:
        logger.error(f"[ai_service] Ошибка: {e}", exc_info=True)
        return "⚠️ Произошла ошибка при обработке запроса. Попробуйте ещё раз."
