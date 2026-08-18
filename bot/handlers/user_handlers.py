import asyncio
import logging
import os
import time as _time

from aiogram import Router, F
from aiogram.filters import CommandStart, Command
from aiogram.types import (
    Message, CallbackQuery,
    InlineKeyboardMarkup, InlineKeyboardButton
)

from bot.services.ai_service import (
    get_ai_response,
    stream_ai_response,
    _calc_salary, _calc_nds, _calc_depreciation,
    _calc_penalty, _calc_vacation, _calc_sick_leave,
    transcribe_voice,
)
from bot.services.currency_service import format_rate_message, convert_to_tenge

logger = logging.getLogger(__name__)
user_router = Router()


def _is_allowed_thread(message: Message) -> bool:
    """Возвращает True, если сообщение пришло из разрешённой темы."""
    # Поддерживаем как один ID, так и список через запятую
    allowed_ids_str = os.getenv("ALLOWED_THREAD_ID", "")
    
    if not allowed_ids_str:
        # Если переменная не задана — отвечаем везде
        return True
        
    allowed_ids = [i.strip() for i in allowed_ids_str.split(",") if i.strip()]
    return str(message.message_thread_id) in allowed_ids


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
        "🔹 Расчёт НДС (16%) — начисление и выделение\n"
        "🔹 Расчёт амортизации, пеней, отпускных, больничных\n"
        "🔹 Курс валют НБК — USD, EUR, RUB, CNY\n"
        "🔹 Поиск по официальным источникам РК (Google Search)\n"
        "🔹 Обучение на ваших оценках 👍/👎\n\n"
        "<b>Команды:</b>\n"
        "/calc — калькулятор (ЗП, НДС, пени, отпускные, больничные)\n"
        "/rates — актуальные ставки 2026\n"
        "/usd /eur /rub /cny — курс валют НБК\n"
        "/task [текст] — добавить личное напоминание/задачу\n"
        "/tasks — посмотреть список своих задач\n"
        "/feedback [текст] — отправить отзыв или пожелание\n\n"
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
        "└ МРП = <code>4 325 тг</code>\n\n"
        "<b>Взносы и налоги с работника:</b>\n"
        "├ ОПВ = <code>10%</code>\n"
        "├ ВОСМС = <code>2%</code>\n"
        "└ ИПН = <code>10%</code> (вычет: 30 МРП = 129 750 тг)\n\n"
        "<b>Взносы работодателя (сверх ЗП):</b>\n"
        "├ СО = <code>5%</code>\n"
        "├ ОСМС = <code>3%</code>\n"
        "├ ОПВр = <code>3.5%</code>\n"
        "└ Соц. налог = <code>6%</code> (без вычета СО)\n\n"
        "<b>Прочие налоги:</b>\n"
        "├ НДС = <code>16%</code>\n"
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
            "<i>(стоимость, остаточная стоимость, лет)</i>\n\n"
            "<b>Расчёт пени за просрочку:</b>\n"
            "<code>/calc пеня 500000 30</code>\n"
            "<i>(сумма долга, дней просрочки)</i>\n\n"
            "<b>Расчёт отпускных:</b>\n"
            "<code>/calc отпускные 300000 24</code>\n"
            "<i>(среднемесячная ЗП, дней отпуска)</i>\n\n"
            "<b>Расчёт больничных:</b>\n"
            "<code>/calc больничные 300000 10</code>\n"
            "<i>(среднемесячная ЗП, дней болезни)</i>"
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
    elif any(k in t for k in ["пен", "просрочк"]):
        if len(nums) >= 2:
            result = _calc_penalty(nums[0], int(nums[1]))
    elif any(k in t for k in ["отпускн", "отпуск"]):
        if len(nums) >= 2:
            result = _calc_vacation(nums[0], int(nums[1]))
        elif len(nums) == 1:
            result = _calc_vacation(nums[0], 24)
    elif any(k in t for k in ["больничн", "болезн"]):
        if len(nums) >= 2:
            result = _calc_sick_leave(nums[0], int(nums[1]))

    if result:
        await message.answer(result, parse_mode="HTML")
    else:
        await message.answer(
            "⚠️ Не удалось распознать расчёт.\n\n"
            "Введите <code>/calc</code> без параметров для примеров.",
            parse_mode="HTML"
        )


# ── /feedback — обратная связь от пользователей ──────────────────────────────
@user_router.message(Command("feedback"))
async def cmd_feedback(message: Message):
    if not _is_allowed_thread(message): return
    
    text = message.text.replace("/feedback", "", 1).strip()
    if not text:
        await message.answer(
            "ℹ️ Напишите ваш отзыв или предложение после команды.\n"
            "Пример: <code>/feedback Добавьте расчет отпускных</code>",
            parse_mode="HTML"
        )
        return
        
    await message.bot.send_chat_action(chat_id=message.chat.id, action="typing")
    try:
        from bot.rag.firebase_db import save_feedback
        import asyncio
        success = await asyncio.to_thread(save_feedback, message.from_user.id, text)
        
        if success:
            await message.answer("✅ <b>Спасибо за отзыв!</b> Ваше пожелание сохранено.", parse_mode="HTML")
        else:
            await message.answer("⚠️ Не удалось сохранить отзыв. Попробуйте позже.")
    except Exception as e:
        logger.error(f"[/feedback] Ошибка: {e}")
        await message.answer("⚠️ Произошла ошибка при отправке отзыва.")


# ── /task — добавление задачи ────────────────────────────────────────────────
@user_router.message(Command("task"))
async def cmd_task(message: Message):
    if not _is_allowed_thread(message): return
    
    text = message.text.replace("/task", "", 1).strip()
    if not text:
        await message.answer(
            "ℹ️ Напишите задачу после команды.\n"
            "Пример: <code>/task Сдать ФНО 910 до 15 мая</code>",
            parse_mode="HTML"
        )
        return
        
    await message.bot.send_chat_action(chat_id=message.chat.id, action="typing")
    try:
        from bot.rag.firebase_db import save_user_task
        import asyncio
        success = await asyncio.to_thread(save_user_task, message.from_user.id, text)
        
        if success:
            await message.answer("📝 <b>Задача сохранена!</b>\nПосмотреть список: /tasks", parse_mode="HTML")
        else:
            await message.answer("⚠️ Не удалось сохранить задачу. Попробуйте позже.")
    except Exception as e:
        logger.error(f"[/task] Ошибка: {e}")
        await message.answer("⚠️ Произошла ошибка.")

# ── /tasks — просмотр списка задач ───────────────────────────────────────────
@user_router.message(Command("tasks"))
async def cmd_tasks(message: Message):
    if not _is_allowed_thread(message): return
    
    await message.bot.send_chat_action(chat_id=message.chat.id, action="typing")
    try:
        from bot.rag.firebase_db import get_user_tasks
        import asyncio
        tasks = await asyncio.to_thread(get_user_tasks, message.from_user.id)
        
        if not tasks:
            await message.answer("✅ У вас нет активных задач!")
            return
            
        await message.answer(f"<b>📋 Ваши задачи ({len(tasks)}):</b>", parse_mode="HTML")
        
        for task in tasks:
            # Создаем кнопку для выполнения задачи
            keyboard = InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="✅ Выполнено", callback_data=f"done_task:{task['id']}")
            ]])
            await message.answer(
                f"📌 {task['text']}",
                reply_markup=keyboard,
                parse_mode="HTML"
            )
            
    except Exception as e:
        logger.error(f"[/tasks] Ошибка: {e}")
        await message.answer("⚠️ Произошла ошибка при получении задач.")

# ── Обработчик выполнения задачи ─────────────────────────────────────────────
@user_router.callback_query(F.data.startswith("done_task:"))
async def handle_done_task(callback: CallbackQuery):
    try:
        task_id = callback.data.split(":")[1]
        from bot.rag.firebase_db import delete_user_task
        import asyncio
        success = await asyncio.to_thread(delete_user_task, task_id)
        
        if success:
            await callback.answer("Задача выполнена!", show_alert=False)
            # Обновляем сообщение, зачеркивая текст и убирая кнопку
            old_text = callback.message.text.replace("📌 ", "", 1)
            await callback.message.edit_text(f"<s>📌 {old_text}</s>", parse_mode="HTML", reply_markup=None)
        else:
            await callback.answer("⚠️ Ошибка. Попробуйте еще раз.", show_alert=True)
    except Exception as e:
        logger.error(f"[done_task] Ошибка: {e}")
        await callback.answer("⚠️ Произошла ошибка.", show_alert=True)

# ── /usd /eur /rub /cny — курс валют НБК ────────────────────────────────────
@user_router.message(Command("usd", "eur", "rub", "cny", "gbp"))
async def cmd_currency(message: Message):
    if not _is_allowed_thread(message): return
    await message.bot.send_chat_action(chat_id=message.chat.id, action="typing")

    # Определяем валюту из команды
    cmd = message.text.split()[0].lstrip("/").upper().split("@")[0]

    # Проверяем: может быть конвертация "/usd 1500"
    args = message.text.split()
    if len(args) >= 2:
        try:
            amount = float(args[1].replace(",", "."))
            result = await asyncio.to_thread(convert_to_tenge, amount, cmd)
            if result:
                await message.answer(result, parse_mode="HTML")
                return
        except ValueError:
            pass

    result = await asyncio.to_thread(format_rate_message, cmd)
    await message.answer(result, parse_mode="HTML")


# ── /update_laws — ручной запуск парсинга законодательства ────────────────────
@user_router.message(Command("send_news"))
async def cmd_send_news(message: Message):
    """Показывает подборку админу; аргумент live публикует её в целевой теме."""
    admin_id = os.getenv("ADMIN_ID", "")
    if not admin_id or str(message.from_user.id) != admin_id:
        await message.answer("⛔ У вас нет прав для использования этой команды.")
        return

    args = message.text.lower().split()[1:]
    live = "live" in args
    await message.answer("🔍 Формирую проверенную подборку за последние 24 часа…")

    try:
        from datetime import datetime
        from zoneinfo import ZoneInfo

        from bot.services.news_digest_service import (
            build_digest,
            publish_digest,
            split_digest,
        )

        if live:
            timezone_name = os.getenv("NEWS_TIMEZONE", "Asia/Almaty")
            published = await publish_digest(
                message.bot,
                chat_id=int(
                    os.getenv("NEWS_TARGET_CHAT_ID", "-1002318310296")
                ),
                thread_id=(
                    int(os.getenv("NEWS_TARGET_THREAD_ID", "0"))
                    if int(os.getenv("NEWS_TARGET_THREAD_ID", "0")) > 0
                    else None
                ),
                publication_key=datetime.now(
                    ZoneInfo(timezone_name)
                ).date().isoformat(),
            )
            if published:
                await message.answer("✅ Подборка опубликована в целевой теме.")
            else:
                await message.answer("ℹ️ Сегодняшняя подборка уже была опубликована.")
            return

        preview, _ = await build_digest()
        for chunk in split_digest(preview):
            await message.answer(
                chunk,
                parse_mode="HTML",
                disable_web_page_preview=True,
            )
    except Exception as exc:
        logger.exception("[/send_news] Ошибка: %s", exc)
        await message.answer(
            "⚠️ Не удалось сформировать подборку. "
            "Проверьте источники и повторите позже."
        )


# ── /update_laws — ручной запуск парсинга законодательства ────────────────────
@user_router.message(Command("update_laws"))
async def cmd_update_laws(message: Message):
    if not _is_allowed_thread(message): return
    admin_id = os.getenv("ADMIN_ID", "6493072610")
    if not admin_id or str(message.from_user.id) != admin_id:
        await message.answer("⛔ У вас нет прав для использования этой команды.")
        return

    await message.answer("🔍 Запускаю ручной сбор законов и изменений для бухгалтеров…")
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
        logger.error(f"[/update_laws] Ошибка: {e}")
        await message.answer("⚠️ Ошибка при сборе данных. Попробуйте позже.")


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


# ── Вспомогательная функция стриминга ────────────────────────────────────────
async def _stream_and_send(message: Message, user_query: str, thread_id: int, user_id: int):
    """Отправляет плейсхолдер, стримит ответ Gemini, редактирует по мере поступления."""
    sent = await message.answer("⏳")
    accumulated = ""
    last_edit = _time.time()
    doc_id = None

    try:
        async for chunk in stream_ai_response(user_query, thread_id=thread_id, user_id=user_id):
            # Финальные состояния
            if isinstance(chunk, tuple):
                kind = chunk[0]
                if kind in ("done", "cached"):
                    accumulated, doc_id = chunk[1], chunk[2]
                elif kind == "calc":
                    await sent.edit_text(chunk[1], parse_mode="HTML")
                    return
                break

            # Обычный текстовый кусок
            accumulated += chunk
            now = _time.time()
            if now - last_edit >= 1.0 and accumulated.strip():
                try:
                    await sent.edit_text(
                        accumulated + " ▌",
                        parse_mode="HTML",
                        disable_web_page_preview=True,
                    )
                    last_edit = now
                except Exception:
                    pass

        # Финальное редактирование с кнопками оценки
        reply_markup = _rating_keyboard(doc_id) if doc_id else None
        await sent.edit_text(
            accumulated or "⚠️ Не удалось получить ответ.",
            parse_mode="HTML",
            disable_web_page_preview=True,
            reply_markup=reply_markup,
        )
    except Exception as e:
        logger.error(f"[stream_and_send] {e}")
        try:
            await sent.edit_text("⚠️ Произошла ошибка. Попробуйте ещё раз.")
        except Exception:
            pass


# ── Голосовые сообщения → транскрипция → стриминг AI ─────────────────────────
@user_router.message(F.voice)
async def handle_voice_message(message: Message):
    if not _is_allowed_thread(message):
        return

    await message.bot.send_chat_action(chat_id=message.chat.id, action="typing")

    try:
        voice_file = await message.bot.get_file(message.voice.file_id)
        file_bytes = await message.bot.download_file(voice_file.file_path)
        audio_data = file_bytes.read()
    except Exception as e:
        logger.error(f"[voice] Ошибка загрузки: {e}")
        await message.answer("⚠️ Не удалось загрузить голосовое сообщение.")
        return

    user_query = await transcribe_voice(audio_data)
    if not user_query:
        await message.answer("⚠️ Не удалось распознать речь. Попробуйте написать текстом.")
        return

    logger.info(f"[voice] Транскрипция: {user_query[:80]}")
    await message.answer(f"🎙 <i>{user_query}</i>", parse_mode="HTML")

    await _stream_and_send(message, user_query, message.message_thread_id, message.from_user.id)


# ── Любое текстовое сообщение → стриминг AI ──────────────────────────────────
@user_router.message(F.text)
async def handle_user_message(message: Message):
    if not _is_allowed_thread(message):
        return

    thread_id  = message.message_thread_id
    user_query = message.text
    user_id    = message.from_user.id
    logger.info(f"Запрос от {user_id} в теме {thread_id}: {user_query[:80]}")

    await _stream_and_send(message, user_query, thread_id, user_id)
