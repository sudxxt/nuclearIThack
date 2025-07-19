import asyncio
import logging

from aiogram import Bot, Dispatcher
try:
    from aiogram.client.default import DefaultBotProperties
except ImportError:
    # Older aiogram (<3) or incorrect installation; define stub for graceful fallback
    DefaultBotProperties = None  # type: ignore
from aiogram.fsm.storage.memory import MemoryStorage

from donor_bot.config import settings
from donor_bot.db import init_db, SessionLocal
from donor_bot.handlers import (
    common_router,
    donor_menu_router,
    admin_menu_router,
    tickets_admin_router,
    tickets_user_router,
    voice,
)
from donor_bot.middleware.db import DbSessionMiddleware
from donor_bot.services.scheduler import schedule_jobs

# (Отключено) Запуск мини-сервера WebApp больше не требуется


async def main():
    import sys
    from pathlib import Path

    # ---------- logging configuration ----------
    # Create ./logs directory next to this file (if it doesn’t exist)
    logs_dir = Path(__file__).resolve().parent / "logs"
    logs_dir.mkdir(exist_ok=True)
    log_file = logs_dir / "bot.log"

    # Configure root logger to write both to console *and* to rotating file
    logging.basicConfig(
        level=logging.DEBUG,  # show everything; adjust to INFO if too verbose
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),  # console
            logging.FileHandler(log_file, encoding="utf-8"),  # file
        ],
        force=True,
    )

    # Показываем логи сторонних библиотек тоже (если нужно, поменяйте на INFO)
    logging.getLogger("aiogram").setLevel(logging.DEBUG)
    logging.getLogger("apscheduler").setLevel(logging.DEBUG)

    logging.info("Bot starting…")

    # Init DB (создаём таблицы и миграции)
    await init_db()

    # Bot and Dispatcher
    if DefaultBotProperties is not None:
        bot = Bot(token=settings.BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
    else:
        # Fallback for environments where aiogram.client.default is unavailable
        bot = Bot(token=settings.BOT_TOKEN, parse_mode="HTML")

    # (Описание бота убрано по требованию)
    dp = Dispatcher(storage=MemoryStorage())

    # Middleware
    dp.update.middleware(DbSessionMiddleware(session_pool=SessionLocal))

    # Routers
    dp.include_router(common_router)
    dp.include_router(admin_menu_router)
    dp.include_router(tickets_admin_router)
    dp.include_router(tickets_user_router)
    dp.include_router(donor_menu_router)
    dp.include_router(voice.router)

    # Scheduler
    schedule_jobs(bot)

    # Start polling
    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())




