import os
import re
import uuid
import time
import asyncio
import logging
from collections import OrderedDict
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
    "НДС":             0.16,    # 16%
    "КПН":             0.20,    # 20%
    # Базовая ставка НБРК (для расчёта пеней)
    "БАЗОВАЯ_СТАВКА":  0.1525,  # 15.25% — базовая ставка НБРК на 2026
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
• КАТЕГОРИЧЕСКИ ЗАПРЕЩЕНО использовать ставки из своей памяти или обучающих данных — используй ТОЛЬКО ставки, указанные ниже в этом промпте.

════════════════════════════════════
АКТУАЛЬНЫЕ СТАВКИ 2026 (новый НК РК с 01.01.2026) — ЕДИНСТВЕННЫЙ ДОСТОВЕРНЫЙ ИСТОЧНИК:
МЗП = 85 000 тг | МРП = 4 325 тг
ОПВ = 10% (лимит 7 МЗП = 595 000 тг) | ВОСМС = 2% (лимит 20 МЗП)
ИПН: 10% — до 8 500 МРП/год | 15% — свыше 8 500 МРП/год
Базовый налоговый вычет по ИПН = 30 МРП/мес = 129 750 тг (по заявлению)
СО = 5% | ОСМС = 3% (лимит 40 МЗП) | ОПВр = 3.5% | СН = 6%
НДС = 16% | КПН = 20%

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

# ── Кэш системного промпта (обновляется раз в сутки) ──────────────────────────
_cached_system: tuple[str, str] = ("", "")

def _get_system_prompt() -> str:
    today = datetime.now().strftime("%d.%m.%Y")
    if _cached_system[0] == today:
        return _cached_system[1]
    text = _SYSTEM_INSTRUCTION.format(today=today)
    globals()['_cached_system'] = (today, text)
    return text

# ── TTL-кэш ответов (200 вопросов, TTL 1 час) ─────────────────────────────────
class _TTLCache:
    def __init__(self, maxsize: int = 200, ttl: int = 3600):
        self._cache: OrderedDict = OrderedDict()
        self._maxsize = maxsize
        self._ttl = ttl

    def get(self, key: str):
        if key in self._cache:
            value, ts = self._cache[key]
            if time.time() - ts < self._ttl:
                self._cache.move_to_end(key)
                return value
            del self._cache[key]
        return None

    def set(self, key: str, value):
        self._cache[key] = (value, time.time())
        self._cache.move_to_end(key)
        if len(self._cache) > self._maxsize:
            self._cache.popitem(last=False)

_response_cache = _TTLCache(maxsize=200, ttl=3600)

# ── Определение: нужен ли Google Search ───────────────────────────────────────
_LOCAL_KEYWORDS = {
    "мзп", "мрп", "ндс", "ипн", "опв", "кпн", "со ", "осмс", "восмс",
    "ставка", "ставки", "вычет", "амортизац", "зарплат", " зп ", "оклад",
    "что такое", "как рассчит", "формул", "коэффициент", "расчёт", "расчет",
}

def _needs_search(text: str) -> bool:
    """Возвращает False для вопросов, которые решаются без интернета."""
    t = text.lower()
    if len(text) < 200 and any(k in t for k in _LOCAL_KEYWORDS):
        return False
    return True

# ── Сборка RAG-контекста ──────────────────────────────────────────────────────
def _build_context(rag_articles, similar_dialogs, news) -> str:
    context = ""
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
    return context

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
            f"├ НДС (16%) = <code>{nds:,.2f} тг</code>\n"
            f"└ Сумма с НДС = <code>{amount:,.2f} тг</code>\n\n"
            f"<i>ст. 422 НК РК</i>"
        )
    else:
        nds   = round(amount * rate, 2)
        total = round(amount + nds, 2)
        return (
            f"<b>📊 Расчёт НДС на {amount:,.2f} тг</b>\n\n"
            f"├ Сумма без НДС = <code>{amount:,.2f} тг</code>\n"
            f"├ НДС (16%) = <code>{nds:,.2f} тг</code>\n"
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


def _calc_penalty(debt: float, days: int) -> str:
    """Пеня за просрочку налогов (НК РК 2026)."""
    r = RATES_2026
    # Пеня = долг × (базовая ставка НБРК × 1.25) × дней / 365
    daily_rate = r["БАЗОВАЯ_СТАВКА"] * 1.25 / 365
    penalty    = round(debt * daily_rate * days, 2)
    annual_pct = round(r["БАЗОВАЯ_СТАВКА"] * 1.25 * 100, 4)
    return (
        f"<b>📊 Расчёт пени за просрочку</b>\n\n"
        f"├ Сумма долга = <code>{debt:,.2f} тг</code>\n"
        f"├ Дней просрочки = <code>{days}</code>\n"
        f"├ Ставка = базовая НБРК ({r['БАЗОВАЯ_СТАВКА']*100:.2f}%) × 1.25 = <code>{annual_pct}%</code>\n"
        f"└ <b>Пеня = <code>{penalty:,.2f} тг</code></b>\n\n"
        f"<i>ст. 117 НК РК | Базовая ставка НБРК = {r['БАЗОВАЯ_СТАВКА']*100:.2f}%</i>"
    )


def _calc_vacation(monthly_salary: float, vacation_days: int,
                   worked_months: int = 12) -> str:
    """Отпускные (ТК РК): среднедневная ЗП × кол-во дней отпуска."""
    # Среднедневная = средняя ЗП / 29.3 (среднее кол-во дней в месяце по ТК РК)
    avg_daily = round(monthly_salary / 29.3, 2)
    total     = round(avg_daily * vacation_days, 2)
    return (
        f"<b>📊 Расчёт отпускных</b>\n\n"
        f"├ Среднемесячная ЗП = <code>{monthly_salary:,.2f} тг</code>\n"
        f"├ Среднедневная ЗП = <code>{avg_daily:,.2f} тг</code>\n"
        f"├ Дней отпуска = <code>{vacation_days}</code>\n"
        f"└ <b>Отпускные = <code>{total:,.2f} тг</code></b>\n\n"
        f"<i>ст. 92 ТК РК | Коэффициент 29.3 (среднее дней/мес)</i>"
    )


def _calc_sick_leave(monthly_salary: float, sick_days: int) -> str:
    """Больничные (ВОСМС РК): 80% среднедневной ЗП × дней."""
    avg_daily = round(monthly_salary / 29.3, 2)
    pct       = 0.80
    daily_pay = round(avg_daily * pct, 2)
    total     = round(daily_pay * sick_days, 2)
    return (
        f"<b>📊 Расчёт больничных</b>\n\n"
        f"├ Среднемесячная ЗП = <code>{monthly_salary:,.2f} тг</code>\n"
        f"├ Среднедневная ЗП = <code>{avg_daily:,.2f} тг</code>\n"
        f"├ Размер выплаты = <code>80%</code>\n"
        f"├ Дней болезни = <code>{sick_days}</code>\n"
        f"└ <b>Больничные = <code>{total:,.2f} тг</code></b>\n\n"
        f"<i>Закон об ОСМС РК | Выплачивает ФСМС с 1-го дня</i>"
    )


def _parse_and_calculate(text: str) -> str | None:
    """Распознаёт тип расчёта и возвращает результат или None."""
    t = text.lower()
    nums = [float(n.replace(',', '.')) for n in re.findall(r'\d+(?:[.,]\d+)?', text)]

    # ЗП
    if any(k in t for k in ["зп ", "зарплат", "оклад", "расчёт зп", "расчет зп"]):
        if nums:
            return _calc_salary(nums[0])

    # НДС
    if "ндс" in t or "nds" in t:
        if nums:
            with_nds = any(p in t for p in ["с ндс", "включая ндс", "в т.ч", "выдели"])
            return _calc_nds(nums[0], with_nds=with_nds)

    # Пеня
    if any(k in t for k in ["пен", "штраф за просроч", "просрочк"]):
        if len(nums) >= 2:
            return _calc_penalty(nums[0], int(nums[1]))

    # Отпускные
    if any(k in t for k in ["отпускн", "отпуск"]):
        if len(nums) >= 2:
            return _calc_vacation(nums[0], int(nums[1]))
        if len(nums) == 1:
            return _calc_vacation(nums[0], 24)  # 24 дня — стандартный отпуск по ТК РК

    # Больничные
    if any(k in t for k in ["больничн", "нетрудоспособ", "болезн"]):
        if len(nums) >= 2:
            return _calc_sick_leave(nums[0], int(nums[1]))

    # Амортизация
    if any(k in t for k in ["амортизац", "спи "]):
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
    for attempt in range(3):
        try:
            result = await asyncio.to_thread(
                _client.models.embed_content,
                model="gemini-embedding-2",
                contents=text,
            )
            return result.embeddings[0].values
        except Exception as e:
            logger.warning(f"[embed] Ошибка на попытке {attempt+1}: {e}")
            if attempt == 2:
                return []
            await asyncio.sleep(1)
    return []

# ══════════════════════════════════════════════════════════════════════════════
# СТРИМИНГ ОТВЕТА
# ══════════════════════════════════════════════════════════════════════════════

async def stream_ai_response(
    user_text: str,
    thread_id: int = None,
    user_id: int = None,
):
    """
    Async-генератор. Отдаёт куски текста по мере генерации Gemini.
    Последний элемент — tuple ("done", full_answer, doc_id).
    Если вопрос — калькулятор, отдаёт сразу tuple ("calc", result).
    """
    # Калькулятор — мгновенно, без AI
    calc = _parse_and_calculate(user_text)
    if calc:
        yield ("calc", calc)
        return

    # Кэш — мгновенно для повторных вопросов
    cache_key = user_text.lower().strip()
    cached = _response_cache.get(cache_key)
    if cached:
        yield ("cached", cached[0], cached[1])
        return

    try:
        # RAG: эмбеддинг + Firebase параллельно
        query_embedding = await embed_text(user_text)
        if query_embedding:
            rag_articles, similar_dialogs, news = await asyncio.gather(
                search_similar_articles(query_embedding, 2),
                asyncio.to_thread(get_similar_dialogs, query_embedding, 1),
                asyncio.to_thread(get_recent_news, 2),
                return_exceptions=True,
            )
        else:
            rag_articles, similar_dialogs, news = [], [], []

        context = _build_context(rag_articles, similar_dialogs, news)
        full_prompt = f"Контекст:\n{context}\n\nВОПРОС: {user_text}" if context else user_text

        model = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
        if model in ["gemini-1.5-flash", "gemini-1.5-pro", "gemini-2.0-flash", "text-embedding-004"]:
            model = "gemini-2.5-flash"

        tools = [genai_types.Tool(google_search=genai_types.GoogleSearch())] if _needs_search(user_text) else []
        config = genai_types.GenerateContentConfig(
            system_instruction=_get_system_prompt(),
            temperature=0.1,
            max_output_tokens=900,
            tools=tools,
        )

        history = thread_histories.get(thread_id, []) if thread_id is not None else []
        messages = history + [
            genai_types.Content(role="user", parts=[genai_types.Part(text=full_prompt)])
        ]

        full_answer = ""
        try:
            async for chunk in _client.aio.models.generate_content_stream(
                model=model, contents=messages, config=config
            ):
                try:
                    text = chunk.text or ""
                except Exception:
                    text = ""
                if text:
                    full_answer += text
                    yield text
        except AttributeError:
            # Fallback: синхронный вызов если aio недоступен
            response = await asyncio.to_thread(
                _client.models.generate_content,
                model=model, contents=messages, config=config,
            )
            try:
                full_answer = response.text or ""
            except Exception:
                parts = response.candidates[0].content.parts
                full_answer = "".join(p.text for p in parts if hasattr(p, "text") and p.text)
            yield full_answer

        if not full_answer.strip():
            yield ("done", "⚠️ Не удалось получить ответ. Переформулируйте вопрос.", None)
            return

        # Markdown → HTML
        full_answer = re.sub(r'\*\*(.*?)\*\*', r'<b>\1</b>', full_answer)

        # Обновляем историю темы
        if thread_id is not None:
            new_history = messages + [
                genai_types.Content(role="model", parts=[genai_types.Part(text=full_answer)])
            ]
            thread_histories[thread_id] = new_history[-MAX_HISTORY:]

        # Сохраняем диалог в фоне с заранее известным doc_id
        doc_id = f"dlg_{uuid.uuid4().hex[:12]}"

        async def _save():
            await asyncio.to_thread(
                save_dialog, user_id or 0, user_text, full_answer,
                thread_id, query_embedding or None, doc_id,
            )
        asyncio.create_task(_save())

        # Кэшируем ответ
        _response_cache.set(cache_key, (full_answer, doc_id))

        yield ("done", full_answer, doc_id)

    except Exception as e:
        logger.error(f"[stream] Ошибка: {e}", exc_info=True)
        yield ("done", "⚠️ Произошла ошибка при обработке запроса.", None)


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
        model = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
        if model in ["gemini-1.5-flash", "gemini-1.5-pro", "gemini-2.0-flash", "text-embedding-004"]:
            model = "gemini-2.5-flash"

        config = genai_types.GenerateContentConfig(
            system_instruction=_get_system_prompt(),
            temperature=0.1,
            max_output_tokens=900,
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

        response = None
        for attempt in range(3):
            try:
                response = await asyncio.to_thread(
                    _client.models.generate_content,
                    model=model,
                    contents=messages,
                    config=config,
                )
                break
            except Exception as e:
                logger.warning(f"[ai] Попытка {attempt+1} не удалась: {e}")
                if attempt == 2:
                    raise e
                await asyncio.sleep(0.5)

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

        # ── 5. Сохраняем диалог в Firebase фоном (не блокируем ответ) ──────
        doc_id = f"dlg_{hash(user_text + str(user_id)):x}"  # временный ID для кнопок

        async def _save_bg():
            real_id = await asyncio.to_thread(
                save_dialog,
                user_id or 0,
                user_text,
                answer,
                thread_id,
                query_embedding or None,
            )
            logger.info(f"[dialog] Сохранён фоном: {real_id}")

        asyncio.create_task(_save_bg())

        return answer, doc_id

    except Exception as e:
        logger.error(f"[ai_service] Ошибка: {e}", exc_info=True)
        return "⚠️ Произошла ошибка при обработке запроса. Попробуйте ещё раз."
