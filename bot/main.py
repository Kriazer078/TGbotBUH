import asyncio
import logging
import os
import sys
from dotenv import load_dotenv

load_dotenv()

# Добавляем корневую директорию в PYTHONPATH для корректных импортов
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

# Импорт роутера из обработчиков
from bot.handlers.user_handlers import user_router

# Логирование
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Загрузка токена напрямую или через dotenv
BOT_TOKEN = os.getenv("BOT_TOKEN")

async def main():
    if not BOT_TOKEN:
        logger.error("BOT_TOKEN не найден в переменных окружения! Убедитесь, что вы создали .env файл.")
        return

    logger.info("Запуск бота Бухгалтер-Ассистент РК...")
    from bot.rag.firebase_db import init_firebase
    if init_firebase():
        logger.info("База данных Firebase успешно подключена.")
    else:
        logger.warning("База знаний RAG будет недоступна (отсутствует подключение).")

    # Инициализация бота
    bot = Bot(
        token=BOT_TOKEN, 
        default=DefaultBotProperties(parse_mode=ParseMode.HTML)
    )
    from aiogram.utils.chat_action import ChatActionMiddleware
    
    dp = Dispatcher()
    
    # Регистрация middleware для chat action (индикатор "печатает")
    dp.message.middleware(ChatActionMiddleware())

    # Регистрация роутеров
    dp.include_router(user_router)

    # Запуск polling
    try:
        await bot.delete_webhook(drop_pending_updates=True)
        await dp.start_polling(bot)
    finally:
        await bot.session.close()

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Бот остановлен.")
