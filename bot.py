"""
VPNLi Telegram bot entrypoint.
"""
from __future__ import annotations

import asyncio
import logging
import sys

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage

from config import BOT_TOKEN
from handlers import admin, errors, payment, profile, start, subscription
from middlewares.antispam import AntiSpamMiddleware
from middlewares.register import UserMiddleware
from scheduler import setup_scheduler
from services.enforcer import ip_limit_enforcer_loop
from services.telegram_api import TelegramRetryMiddleware
from services.xui import xui

import database as db

logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)
TELEGRAM_REQUEST_TIMEOUT = 120  # Increased from 90 to handle transient DNS/network issues


async def main() -> None:
    if not BOT_TOKEN:
        logger.critical("BOT_TOKEN is not set")
        sys.exit(1)

    await db.init_db()
    logger.info("Database initialized")

    logger.info("3X-UI auth is lazy: login will be attempted on demand after panel errors")

    session = AiohttpSession(timeout=TELEGRAM_REQUEST_TIMEOUT)
    session.middleware(TelegramRetryMiddleware())

    bot = Bot(
        token=BOT_TOKEN,
        session=session,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher(storage=MemoryStorage())

    dp.message.middleware(AntiSpamMiddleware())
    dp.callback_query.middleware(AntiSpamMiddleware())
    dp.message.middleware(UserMiddleware())
    dp.callback_query.middleware(UserMiddleware())

    dp.include_router(errors.router)
    dp.include_router(start.router)
    dp.include_router(subscription.router)
    dp.include_router(payment.router)
    dp.include_router(profile.router)
    dp.include_router(admin.router)

    scheduler = setup_scheduler(bot)
    scheduler.start()
    logger.info("Scheduler started")

    # Enforcer runs every 2 minutes to minimize server load
    enforcer_task = asyncio.create_task(ip_limit_enforcer_loop(bot, interval=120))
    logger.info("IP limit enforcer started (interval: 120s)")

    # Wait for network before starting polling
    for attempt in range(1, 11):
        try:
            await bot.get_me()
            logger.info("Telegram API connection OK")
            break
        except Exception as exc:
            if attempt >= 10:
                logger.critical("Cannot reach Telegram API after %d attempts, giving up", attempt)
                raise
            delay = min(2 ** attempt, 30.0)
            logger.warning(
                "Telegram API unreachable on startup, retrying in %.0fs (%d/10): %s",
                delay, attempt, exc,
            )
            await asyncio.sleep(delay)

    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        enforcer_task.cancel()
        scheduler.shutdown(wait=False)
        try:
            backup_path = await db.backup_db()
            logger.info("Database backup created: %s", backup_path)
        except Exception:
            logger.exception("Failed to create database backup on shutdown")
        await xui.close()
        await bot.session.close()
        logger.info("Bot stopped")


if __name__ == "__main__":
    asyncio.run(main())
