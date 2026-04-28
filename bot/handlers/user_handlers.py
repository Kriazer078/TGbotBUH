import logging

from aiogram import Router, F
from aiogram.filters import CommandStart, Command
from aiogram.types import Message

from bot.services.ai_service import get_ai_response

logger = logging.getLogger(__name__)
user_router = Router()


# ── /start ────────────────────────────────────────────────────────────────────
@user_router.message(CommandStart())
async def cmd_start(message: Message):
    welcome_text = (
        "Здравствуйте! Я — <b>Бухгалтер-Ассистент РК</b> 🇰🇿\n\n"
        "Я могу помочь вам с вопросами по:\n"
        "🔹 Налоговому кодексу РК\n"
        "🔹 Трудовому кодексу РК\n"
        "🔹 Бухгалтерскому учёту\n\n"
        "<b>Команды:</b>\n"
        "/news — обновить базу новостей вручную\n\n"
        "Просто напишите ваш вопрос!"
    )
    await message.answer(welcome_text)


# ── /news — ручной запуск парсинга новостей ───────────────────────────────────
@user_router.message(Command("news"))
async def cmd_news(message: Message):
    await message.answer("📰 Запускаю сбор свежих новостей…")
    await message.bot.send_chat_action(chat_id=message.chat.id, action="typing")

    try:
        from bot.rag.news_parser import run_news_update
        new_count = await run_news_update()

        if new_count > 0:
            await message.answer(
                f"✅ Готово! Добавлено <b>{new_count}</b> новых материалов в базу знаний."
            )
        else:
            await message.answer("ℹ️ Новых материалов не найдено — база актуальна.")
    except Exception as e:
        logger.error(f"[/news] Ошибка при парсинге: {e}")
        await message.answer("⚠️ Произошла ошибка при сборе новостей. Попробуйте позже.")


# ── Любое текстовое сообщение → AI ───────────────────────────────────────────
@user_router.message(F.text)
async def handle_user_message(message: Message):
    user_query = message.text
    logger.info(f"Запрос от пользователя {message.from_user.id}: {user_query}")

    await message.bot.send_chat_action(chat_id=message.chat.id, action="typing")

    answer = await get_ai_response(user_query)
    await message.answer(answer)
