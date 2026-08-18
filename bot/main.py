import asyncio
import hmac
import logging
import os
import sys
from datetime import datetime
from zoneinfo import ZoneInfo
from dotenv import load_dotenv

load_dotenv()

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from aiogram.utils.chat_action import ChatActionMiddleware
from aiohttp import web

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from bot.handlers.user_handlers import user_router

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler(timezone="Asia/Almaty")

BOT_TOKEN    = os.getenv("BOT_TOKEN")
WEBHOOK_HOST = os.getenv("WEBHOOK_HOST", "").rstrip("/")  # https://your-service.run.app
WEBHOOK_PATH = "/webhook"
WEBHOOK_URL  = f"{WEBHOOK_HOST}{WEBHOOK_PATH}"
PORT         = int(os.getenv("PORT", 8080))


def _news_thread_id():
    value = int(os.getenv("NEWS_TARGET_THREAD_ID", "0"))
    return value if value > 0 else None


async def weekly_digest_endpoint(request, bot, now=None):
    """Authenticate a Cloud Scheduler request before publishing a digest."""
    expected_secret = os.getenv("INTERNAL_TICK_SECRET", "")
    provided_secret = request.headers.get("X-Internal-Secret", "")
    if not expected_secret or not provided_secret or not hmac.compare_digest(
        provided_secret, expected_secret
    ):
        return web.json_response({"ok": False}, status=401)

    from bot.services.news_digest_service import publish_digest

    timezone_name = os.getenv("NEWS_TIMEZONE", "Asia/Almaty")
    current_time = now or datetime.now(ZoneInfo(timezone_name))
    publication_key = current_time.astimezone(ZoneInfo(timezone_name)).date().isoformat()
    try:
        await publish_digest(
            bot,
            chat_id=int(os.getenv("NEWS_TARGET_CHAT_ID", "-1002318310296")),
            thread_id=_news_thread_id(),
            publication_key=publication_key,
            test_mode=False,
        )
    except Exception:
        logger.exception("[weekly_digest] Cloud Scheduler publication failed")
        return web.json_response({"ok": False}, status=500)

    return web.json_response({"ok": True})


def create_app(bot: Bot, dispatcher: Dispatcher):
    """Build the aiohttp application and register external/internal routes."""
    app = web.Application()
    SimpleRequestHandler(dispatcher=dispatcher, bot=bot).register(
        app, path=WEBHOOK_PATH
    )

    async def _weekly_digest_handler(request):
        return await weekly_digest_endpoint(request, bot)

    app.router.add_post("/internal/weekly-digest", _weekly_digest_handler)
    setup_application(app, dispatcher, bot=bot)
    return app


def register_weekly_digest_job(bot: Bot, target_scheduler=scheduler):
    """Регистрирует понедельничную публикацию деловых новостей."""
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from bot.services.news_digest_service import digest_schedule, publish_digest

    async def _weekly_digest_job():
        timezone_name = os.getenv("NEWS_TIMEZONE", "Asia/Almaty")
        publication_key = datetime.now(ZoneInfo(timezone_name)).date().isoformat()
        test_mode = os.getenv("NEWS_ADMIN_TEST_MODE", "false").lower() in {
            "1", "true", "yes", "on"
        }
        try:
            if test_mode:
                admin_id = os.getenv("ADMIN_ID")
                if not admin_id:
                    raise RuntimeError("ADMIN_ID обязателен в тестовом режиме")
                chat_id = int(admin_id)
                thread_id = None
            else:
                chat_id = int(
                    os.getenv("NEWS_TARGET_CHAT_ID", "-1002318310296")
                )
                thread_id = _news_thread_id()

            await publish_digest(
                bot,
                chat_id=chat_id,
                thread_id=thread_id,
                publication_key=publication_key,
                test_mode=test_mode,
            )
        except Exception as exc:
            logger.exception("[weekly_digest] Ошибка публикации: %s", exc)
            admin_id = os.getenv("ADMIN_ID")
            if admin_id:
                await bot.send_message(
                    int(admin_id),
                    "⚠️ Не удалось опубликовать понедельничную подборку. "
                    "Проверьте логи.",
                )

    target_scheduler.add_job(
        _weekly_digest_job,
        "cron",
        id="weekly_business_digest",
        replace_existing=True,
        **digest_schedule(),
    )


async def on_startup(bot: Bot):
    from bot.rag.firebase_db import init_firebase, search_similar_articles
    if init_firebase():
        logger.info("Firebase успешно подключена.")
        # Прогреваем векторный кэш фоном — первый запрос пользователя не будет ждать
        async def _warm_cache():
            try:
                # gemini-embedding-2 даёт 3072-мерные векторы
                await search_similar_articles([0.0] * 3072, top_k=1)
                logger.info("Векторный кэш прогрет.")
            except Exception as e:
                logger.warning(f"Прогрев кэша не удался: {e}")
        asyncio.create_task(_warm_cache())
    else:
        logger.warning("Firebase недоступна — RAG будет отключён.")
    # Автообновление новостей каждые 6 часов
    async def _news_job():
        try:
            from bot.rag.news_parser import run_news_update
            count = await run_news_update()
            logger.info(f"[scheduler] Автообновление новостей: +{count} материалов")
        except Exception as e:
            logger.error(f"[scheduler] Ошибка обновления новостей: {e}")

    scheduler.add_job(_news_job, "interval", hours=6, id="news_update", replace_existing=True)
    scheduler.start()
    logger.info("Планировщик запущен: сбор новостей каждые 6 часов.")

    await bot.set_webhook(WEBHOOK_URL)
    logger.info(f"Webhook установлен: {WEBHOOK_URL}")


async def on_shutdown(bot: Bot):
    scheduler.shutdown(wait=False)
    logger.info("Бот остановлен.")
    # Webhook НЕ удаляем — новый контейнер уже установил его,
    # удаление здесь сломало бы входящие сообщения.


def main():
    if not BOT_TOKEN:
        logger.error("BOT_TOKEN не найден в переменных окружения!")
        return
    if not WEBHOOK_HOST:
        logger.error("WEBHOOK_HOST не задан! Добавьте его в .env или переменные Cloud Run.")
        return

    bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp  = Dispatcher()

    dp.message.middleware(ChatActionMiddleware())
    dp.include_router(user_router)

    dp.startup.register(on_startup)
    dp.shutdown.register(on_shutdown)

    app = create_app(bot, dp)

    logger.info(f"Запуск веб-сервера на порту {PORT}...")
    web.run_app(app, host="0.0.0.0", port=PORT)


if __name__ == "__main__":
    main()
