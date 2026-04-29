import logging
import os

from aiogram import Router, F
from aiogram.filters import CommandStart, Command
from aiogram.types import (
    Message, CallbackQuery,
    InlineKeyboardMarkup, InlineKeyboardButton
)

from bot.services.ai_service import (
    get_ai_response,
    _calc_salary, _calc_nds, _calc_depreciation,
)

logger = logging.getLogger(__name__)
user_router = Router()


def _is_allowed_thread(message: Message) -> bool:
    """Возвращает True, если сообщение пришло из разрешённой темы."""
    allowed_thread_id = os.getenv("ALLOWED_THREAD_ID")
    if not allowed_thread_id:
        # Если переменная не задана — отвечаем везде
        return True
    return str(message.message_thread_id) == str(allowed_thread_id)


# ── Вспомогательная функция: клавиатура оценки ───────────────────────────────
def _rating_keyboard(doc_id: str) -> InlineKeyboardMarkup:
    """Кнопки 👍/👎 для оценки ответа ИИ."""
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(
            text="👍 Полезно",
            callback_data=f"rate:good:{doc_id}"
        ),
        InlineKeyboardButton(
            text="👎 Неточно",
            callback_data=f"rate:bad:{doc_id}"
        ),
    ]])


# ── /start ────────────────────────────────────────────────────────────────────
@user_router.message(CommandStart())
async def cmd_start(message: Message):
    if not _is_allowed_thread(message): return
    welcome_text = (
        "Здравствуйте! Я — <b>Главбух ИИ</b> 🇰🇿\n\n"
        "Эксперт по налогам и бухгалтерии Казахстана.\n"
        "Использую официальные источники и актуальные данные <b>2026 года</b>.\n\n"
        "<b>Что я умею:</b>\n"
        "🔹 Консультации по НК РК, ТК РК\n"
        "🔹 Расчёт ЗП, ИПН, ОПВ, ВОСМС, СО\n"
        "🔹 Расчёт НДС (12%) — начисление и выделение\n"
        "🔹 Расчёт амортизации ОС\n"
        "🔹 Поиск по официальным источникам РК (Google Search)\n"
        "🔹 Обучение на ваших оценках 👍/👎\n\n"
        "<b>Команды:</b>\n"
        "/calc — калькулятор для бухгалтера\n"
        "/rates — актуальные ставки 2026\n"
        "/news — обновить базу новостей\n\n"
        "Просто напишите ваш вопрос или сумму для расчёта!"
    )
    await message.answer(welcome_text, parse_mode="HTML")


# ── /rates — актуальные ставки 2026 ──────────────────────────────────────────
@user_router.message(Command("rates"))
async def cmd_rates(message: Message):
    if not _is_allowed_thread(message): return
    rates_text = (
        "<b>📊 Актуальные ставки 2026 года (РК)</b>\n\n"
        "<b>Базовые показатели:</b>\n"
        "├ МЗП = <code>85 000 тг</code>\n"
        "└ МРП = <code>4 200 тг</code>\n\n"
        "<b>Взносы и налоги с работника:</b>\n"
        "├ ОПВ = <code>10%</code>\n"
        "├ ВОСМС = <code>2%</code>\n"
        "└ ИПН = <code>10%</code> (вычет: 14 МРП = 58 800 тг)\n\n"
        "<b>Взносы работодателя (сверх ЗП):</b>\n"
        "├ СО = <code>3.5%</code>\n"
        "├ ОСМС = <code>3%</code>\n"
        "├ ОПВр = <code>1.5%</code>\n"
        "└ Соц. налог = <code>9.5%</code> − СО\n\n"
        "<b>Прочие налоги:</b>\n"
        "├ НДС = <code>12%</code>\n"
        "├ КПН = <code>20%</code>\n"
        "└ ИПН (нерезиденты) = <code>20%</code>\n\n"
        "<i>Источники: НК РК, Закон об ОСМС, Закон об ЕНПФ</i>"
    )
    await message.answer(rates_text, parse_mode="HTML")


# ── /calc — бухгалтерский калькулятор ────────────────────────────────────────
@user_router.message(Command("calc"))
async def cmd_calc(message: Message):
    if not _is_allowed_thread(message): return
    args = message.text.replace("/calc", "", 1).strip()

    if not args:
        help_text = (
            "<b>🧮 Калькулятор бухгалтера</b>\n\n"
            "<b>Расчёт заработной платы:</b>\n"
            "<code>/calc зп 250000</code>\n\n"
            "<b>Расчёт НДС (начислить):</b>\n"
            "<code>/calc ндс 500000</code>\n\n"
            "<b>Выделить НДС из суммы с НДС:</b>\n"
            "<code>/calc ндс 560000 с ндс</code>\n\n"
            "<b>Расчёт амортизации:</b>\n"
            "<code>/calc амортизация 1200000 0 5</code>\n"
            "<i>(стоимость, остаточная стоимость, лет)</i>"
        )
        await message.answer(help_text, parse_mode="HTML")
        return

    await message.bot.send_chat_action(chat_id=message.chat.id, action="typing")

    import re
    t = args.lower()
    nums = [float(n.replace(',', '.')) for n in re.findall(r'\d+(?:[.,]\d+)?', args)]

    result = None

    if any(k in t for k in ["зп", "зарплат", "оклад"]):
        if nums:
            result = _calc_salary(nums[0])
    elif "ндс" in t or "nds" in t:
        if nums:
            with_nds = any(p in t for p in ["с ндс", "включая", "выдели", "в т.ч"])
            result = _calc_nds(nums[0], with_nds=with_nds)
    elif any(k in t for k in ["амортизац", "спи"]):
        if len(nums) >= 3:
            result = _calc_depreciation(nums[0], nums[1], int(nums[2]))
        elif len(nums) == 2:
            result = _calc_depreciation(nums[0], 0, int(nums[1]))

    if result:
        await message.answer(result, parse_mode="HTML")
    else:
        await message.answer(
            "⚠️ Не удалось распознать расчёт.\n\n"
            "Введите <code>/calc</code> без параметров для примеров.",
            parse_mode="HTML"
        )


# ── /news — обновление базы новостей ─────────────────────────────────────────
@user_router.message(Command("news"))
async def cmd_news(message: Message):
    if not _is_allowed_thread(message): return
    await message.answer("📰 Запускаю сбор свежих новостей…")
    await message.bot.send_chat_action(chat_id=message.chat.id, action="typing")
    try:
        from bot.rag.news_parser import run_news_update
        new_count = await run_news_update()
        if new_count > 0:
            await message.answer(
                f"✅ Готово! Добавлено <b>{new_count}</b> новых материалов в базу знаний.",
                parse_mode="HTML"
            )
        else:
            await message.answer("ℹ️ Новых материалов не найдено — база актуальна.")
    except Exception as e:
        logger.error(f"[/news] Ошибка: {e}")
        await message.answer("⚠️ Ошибка при сборе новостей. Попробуйте позже.")


# ── /learn — обучение бота (только для админа) ────────────────────────────────
@user_router.message(Command("learn"))
async def cmd_learn(message: Message):
    if not _is_allowed_thread(message): return
    admin_id = os.getenv("ADMIN_ID", "6493072610")
    if not admin_id or str(message.from_user.id) != admin_id:
        await message.answer("⛔ У вас нет прав для использования этой команды.")
        return

    text_to_learn = message.text.replace("/learn", "", 1).strip()
    if not text_to_learn:
        await message.answer(
            "ℹ️ Напишите текст для обучения после команды.\n"
            "Пример: <code>/learn С 2026 года МРП = 4 200 тг</code>",
            parse_mode="HTML"
        )
        return

    await message.bot.send_chat_action(chat_id=message.chat.id, action="typing")
    try:
        from bot.services.ai_service import embed_text
        embedding = await embed_text(text_to_learn)
        if not embedding:
            await message.answer("⚠️ Не удалось векторизовать текст.")
            return
        from bot.rag.firebase_db import add_learned_text
        success = await add_learned_text(text_to_learn, embedding)
        if success:
            await message.answer(
                "🧠 <b>Отлично! Я запомнил эту информацию.</b>\n"
                "Теперь буду использовать её при ответах.",
                parse_mode="HTML"
            )
        else:
            await message.answer("⚠️ Ошибка при сохранении в базу данных.")
    except Exception as e:
        logger.error(f"[/learn] Ошибка: {e}")
        await message.answer("⚠️ Произошла ошибка при обучении.")


# ── /review — просмотр плохих ответов (только для админа) ────────────────────
@user_router.message(Command("review"))
async def cmd_review(message: Message):
    if not _is_allowed_thread(message): return
    admin_id = os.getenv("ADMIN_ID", "6493072610")
    if not admin_id or str(message.from_user.id) != admin_id:
        await message.answer("⛔ У вас нет прав для использования этой команды.")
        return

    await message.bot.send_chat_action(chat_id=message.chat.id, action="typing")
    try:
        from bot.rag.firebase_db import get_pending_reviews
        import asyncio
        reviews = await asyncio.to_thread(get_pending_reviews, 5)

        if not reviews:
            await message.answer("✅ Проблемных диалогов нет — всё в порядке!")
            return

        await message.answer(
            f"<b>🔍 Диалоги на проверке: {len(reviews)}</b>\n\n"
            "Используйте <code>/learn [правильный ответ]</code> для исправления.",
            parse_mode="HTML"
        )

        for r in reviews:
            q = r.get("question", "")[:200]
            a = r.get("bad_answer", "")[:300]
            doc_id = r.get("id", "")
            await message.answer(
                f"<b>ID:</b> <code>{doc_id}</code>\n"
                f"<b>❓ Вопрос:</b> {q}\n\n"
                f"<b>❌ Плохой ответ:</b>\n{a}",
                parse_mode="HTML"
            )
    except Exception as e:
        logger.error(f"[/review] Ошибка: {e}")
        await message.answer("⚠️ Ошибка при получении списка диалогов.")


# ── Callback: обработка оценок 👍/👎 ─────────────────────────────────────────
@user_router.callback_query(F.data.startswith("rate:"))
async def handle_rating(callback: CallbackQuery):
    """Сохраняет оценку ответа в Firebase."""
    try:
        parts = callback.data.split(":")
        # формат: rate:<good|bad>:<doc_id>
        if len(parts) < 3:
            await callback.answer("⚠️ Ошибка формата.", show_alert=False)
            return

        rating  = parts[1]           # good / bad
        doc_id  = ":".join(parts[2:])  # doc_id (может содержать двоеточие)

        from bot.rag.firebase_db import update_dialog_rating
        import asyncio
        success = await asyncio.to_thread(update_dialog_rating, doc_id, rating)

        if success:
            if rating == "good":
                await callback.answer("✅ Спасибо! Этот ответ войдёт в базу знаний.", show_alert=False)
                # Редактируем клавиатуру — убираем кнопки
                await callback.message.edit_reply_markup(reply_markup=None)
                await callback.message.reply("👍 <b>Ответ отмечен как полезный.</b>", parse_mode="HTML")
            else:
                await callback.answer("📝 Понял! Передам на проверку.", show_alert=False)
                await callback.message.edit_reply_markup(reply_markup=None)
                await callback.message.reply(
                    "👎 <b>Ответ отмечен как неточный.</b>\n"
                    "Администратор проверит и исправит через <code>/review</code>.",
                    parse_mode="HTML"
                )
        else:
            await callback.answer("⚠️ Не удалось сохранить оценку.", show_alert=True)

    except Exception as e:
        logger.error(f"[rating] Ошибка: {e}")
        await callback.answer("⚠️ Ошибка при сохранении оценки.", show_alert=True)


# ── Любое текстовое сообщение → AI ───────────────────────────────────────────
@user_router.message(F.text)
async def handle_user_message(message: Message):
    thread_id = message.message_thread_id

    allowed_thread_id = os.getenv("ALLOWED_THREAD_ID")
    if allowed_thread_id and str(thread_id) != str(allowed_thread_id):
        return

    user_query = message.text
    user_id    = message.from_user.id
    logger.info(f"Запрос от {user_id} в теме {thread_id}: {user_query[:80]}")

    if not allowed_thread_id:
        logger.info(f"Hint: добавьте в .env ALLOWED_THREAD_ID={thread_id}")

    await message.bot.send_chat_action(chat_id=message.chat.id, action="typing")

    result = await get_ai_response(user_query, thread_id=thread_id, user_id=user_id)

    # get_ai_response возвращает (answer, doc_id) или просто строку (калькулятор)
    if isinstance(result, tuple):
        answer, doc_id = result
    else:
        answer  = result
        doc_id  = None

    # Кнопки оценки (только если ответ от ИИ, не калькулятор)
    reply_markup = _rating_keyboard(doc_id) if doc_id else None

    await message.answer(
        answer,
        parse_mode="HTML",
        message_thread_id=thread_id,
        disable_web_page_preview=True,
        reply_markup=reply_markup,
    )
