import logging
from apscheduler.schedulers.asyncio import AsyncIOScheduler

logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler()


def setup_scheduler():
    """
    Настраивает все фоновые задачи:
      • Ежедневно в 07:00 — сбор свежих новостей (uchet.kz, adilet.zan.kz)
      • Каждую пятницу в 03:00 — полное обновление базы Налогового кодекса
    """
    from bot.rag.news_parser import run_news_update
    from bot.rag.parser import check_for_updates

    # ── 1. Ежедневный парсинг новостей ──────────────────────────────────────
    scheduler.add_job(
        run_news_update,
        trigger="cron",
        hour=7,
        minute=0,
        id="daily_news_update",
        replace_existing=True,
    )

    # ── 2. Еженедельное обновление Налогового кодекса ────────────────────────
    scheduler.add_job(
        check_for_updates,
        trigger="cron",
        day_of_week="fri",
        hour=3,
        minute=0,
        id="weekly_tax_code_update",
        replace_existing=True,
    )

    scheduler.start()
    logger.info(
        "APScheduler запущен: "
        "📰 ежедневные новости в 07:00, "
        "📚 обновление НК каждую пятницу в 03:00."
    )
