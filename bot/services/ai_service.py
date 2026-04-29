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

# ── Актуальные ставки 2026 (НК РК) ────────────────────────────────────────────
RATES_2026 = {
    "МЗП":       85_000,
    "МРП":        4_200,
    "ОПВ":         0.10,
    "ВОСМС":       0.02,
    "ИПН":         0.10,
    "СО":          0.035,
    "ОСМС":        0.03,
    "ОПВр":        0.015,
    "НДС":         0.12,
    "КПН":         0.20,
    "СН":          0.095,
    "ВЫЧЕТ_МРП":   14,
}

# ── Системная инструкция ───────────────────────────────────────────────────────
_SYSTEM_INSTRUCTION = """Ты — «Главбух ИИ», эксперт по налогам и бухгалтерскому учёту Казахстана.
Работаешь СТРОГО по действующему законодательству РК. Сегодня: {today}.
Используешь только ОФИЦИАЛЬНЫЕ и АКТУАЛЬНЫЕ данные 2026 года.

════════════════════════════════════
ПРАВИЛА ОТВЕТА НА ПРОФЕССИОНАЛЬНЫЕ ВОПРОСЫ (налоги, законы, расчёты):
1. Краткая суть (1–2 предложения).
2. Обоснование: ссылка на статью закона («ст. 341 НК РК»).
3. Примечание: риски, сроки, ограничения — если есть.
4. ОБЯЗАТЕЛЬНО в конце — блок «Источники» со ссылками.

ОБЩИЕ ВОПРОСЫ (кто ты, приветствия, разговоры):
Отвечай естественно и вежливо. НЕ используй блоки "Обоснование", "Примечание", "Источники".

Стиль: деловой; термины РК (ЭСФ, ФНО, КГД, МЗП, МРП, ИПН, БИН, ИИН).
Форматирование: ТОЛЬКО HTML-теги <b>, <i>, <code>. ЗАПРЕЩЁН Markdown (символы * или **). Для списков используй символ •.

════════════════════════════════════
СТАВКИ 2026 (НК РК):
МЗП = 85 000 тг | МРП = 4 200 тг
ОПВ = 10% | ВОСМС = 2% | ИПН = 10% | вычет = 14 МРП
СО = 3.5% | ОСМС = 3% | ОПВр = 1.5% | СН = 9.5%
НДС = 12% | КПН = 20%

════════════════════════════════════
АЛГОРИТМ РАСЧЁТА ЗП (X = начисленная):
1. ОПВ = X × 10%
2. ВОСМС = X × 2% (макс 10 МЗП × 2%)
3. База ИПН = X − ОПВ − ВОСМС − 58 800 (если <0 → ИПН=0)
4. ИПН = База × 10%
5. К выдаче = X − ОПВ − ВОСМС − ИПН
Работодатель: СО=3.5%, ОСМС=3%, ОПВр=1.5%, СН=9.5%−СО

════════════════════════════════════
ИНТЕРНЕТ-ПОИСК (Google Search):
Используй ТОЛЬКО официальные ресурсы РК:
adilet.zan.kz | kgd.gov.kz | minfin.gov.kz
egov.kz | enbek.gov.kz | stat.gov.kz | uchet.kz
Данные только 2025–2026 года. Устаревшие ставки ЗАПРЕЩЕНЫ.

════════════════════════════════════
ИСТОЧНИКИ (блок в конце каждого ответа):
<b>Источники:</b>
• <a href="https://adilet.zan.kz/rus/docs/K2200000120">Налоговый кодекс РК</a>
• <a href="https://kgd.gov.kz">КГД МФ РК — kgd.gov.kz</a>
• <a href="https://minfin.gov.kz">Министерство финансов РК</a>
• <a href="https://www.egov.kz">eGov.kz</a>
• <a href="https://uchet.kz">Uchet.kz</a>
Если однозначного ответа нет: «Рекомендую обратиться в КГД через <a href="https://cabinet.salyk.kz">e-Otinish</a>.»"""

# ── История по темам ───────────────────────────────────────────────────────────
thread_histories: dict[int, list] = {}
MAX_HISTORY = 20

# ══════════════════════════════════════════════════════════════════════════════
# ВСТРОЕННЫЙ КАЛЬКУЛЯТОР
# ══════════════════════════════════════════════════════════════════════════════

def _calc_salary(gross: float) -> str:
    r = RATES_2026
    opv    = round(gross * r["ОПВ"])
    vosms  = round(min(gross, 10 * r["МЗП"]) * r["ВОСМС"])
    vychet = r["ВЫЧЕТ_МРП"] * r["МРП"]
    base   = max(0.0, gross - opv - vosms - vychet)
    ipn    = round(base * r["ИПН"])
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
        # ── 0. Встроенный калькулятор ─────────────────────────────────────────
        calc_result = _parse_and_calculate(user_text)
        if calc_result:
            logger.info(f"[CALC] Прямой расчёт: {user_text[:60]}")
            return calc_result

        # ── 1. RAG-контекст из Firebase ───────────────────────────────────────
        context         = ""
        query_embedding = []

        query_embedding = await embed_text(user_text)

        if query_embedding:
            # 1a. Статьи из базы знаний
            rag_articles = await search_similar_articles(query_embedding, top_k=2)
            if rag_articles:
                context += "\n[БАЗА ЗНАНИЙ]:\n"
                for art in rag_articles:
                    context += f"- {art['title']}: {art['text'][:800]}\n"

            # 1b. Похожие прошлые диалоги с оценкой 👍 (обучение на опыте)
            similar = await asyncio.to_thread(get_similar_dialogs, query_embedding, 2)
            if similar:
                context += "\n[ПРОШЛЫЙ ОПЫТ — одобренные ответы]:\n"
                for dlg in similar:
                    context += (
                        f"Q: {dlg['question']}\n"
                        f"A: {dlg['answer'][:600]}\n"
                    )

        # 1c. Последние новости
        news = await asyncio.to_thread(get_recent_news, 3)
        if news:
            context += "\n[НОВОСТИ]:\n"
            for n in news:
                context += f"- {n['title']} ({n['source']}): {n['text'][:400]}\n"

        # ── 2. Финальный промпт ───────────────────────────────────────────────
        search_hint = (
            "\n\n[ВАЖНО]: Используй Google Search только по официальным казахстанским "
            "источникам (adilet.zan.kz, kgd.gov.kz, minfin.gov.kz, egov.kz, enbek.gov.kz). "
            "Только данные 2025–2026 года."
        )

        if context:
            full_prompt = (
                f"Контекст из базы данных:\n{context}\n\n"
                f"ВОПРОС: {user_text}"
                f"{search_hint}"
            )
        else:
            full_prompt = user_text + search_hint

        # ── 3. Запрос к Gemini с Google Search ───────────────────────────────
        today  = datetime.now().strftime("%d.%m.%Y")
        model  = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
        if model in ["gemini-1.5-flash", "gemini-1.5-pro", "text-embedding-004"]:
            model = "gemini-2.5-flash"
        system = _SYSTEM_INSTRUCTION.format(today=today)

        config = genai_types.GenerateContentConfig(
            system_instruction=system,
            temperature=0.1,
            max_output_tokens=2000,
            tools=[genai_types.Tool(google_search=genai_types.GoogleSearch())],
        )

        # Строим историю (для сессии по thread_id)
        if thread_id is not None:
            history = thread_histories.get(thread_id, [])
        else:
            history = []

        # Формируем список сообщений: история + новый вопрос
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

        # Извлекаем текст — response.text может быть None при Google Search
        answer = response.text
        if not answer:
            try:
                parts = response.candidates[0].content.parts
                answer = "".join(p.text for p in parts if hasattr(p, 'text') and p.text)
            except Exception:
                answer = None
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
