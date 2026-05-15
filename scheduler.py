"""
Background scheduler tasks.
"""
from __future__ import annotations

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler

import database as db
import keyboards as kb
import texts
from services.xui import xui

logger = logging.getLogger(__name__)


def setup_scheduler(bot) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone=ZoneInfo("Europe/Moscow"))

    @scheduler.scheduled_job("interval", hours=6, id="check_expiring")
    async def notify_expiring() -> None:
        logger.info("Scheduler: checking expiring subscriptions")
        for threshold in (3, 1):
            expiring = await db.get_expiring_subscriptions(days=threshold)
            for record in expiring:
                try:
                    expires_at = datetime.fromisoformat(record["expires_at"])
                    days_left = max(0, (expires_at - datetime.utcnow()).days)
                    if days_left != threshold:
                        continue

                    await bot.send_message(
                        record["tg_id"],
                        texts.EXPIRING_SOON_NOTIFICATION.format(
                            days=texts._days_label(days_left),
                            date=expires_at.strftime("%d.%m.%Y"),
                        ),
                        reply_markup=kb.main_menu_kb(
                            has_active_sub=True,
                            days_left=days_left,
                        ),
                    )
                except Exception:
                    logger.exception("Scheduler: failed to notify user %s", record["tg_id"])

    @scheduler.scheduled_job("interval", hours=1, id="cleanup_expired")
    async def cleanup_expired() -> None:
        logger.info("Scheduler: cleaning up expired subscriptions")
        expired = await db.get_expired_subscriptions()
        if not expired:
            return

        for record in expired:
            try:
                if record.get("xui_client_id"):
                    await xui.del_client(record["inbound_id"], record["xui_client_id"])

                for client in await db.get_active_vpn_clients_for_subscription(record["id"]):
                    await xui.delete_client(client["xui_client_id"])
                    await db.deactivate_vpn_client(client["xui_client_id"])

                await db.deactivate_subscription(record["id"])

                try:
                    await bot.send_message(
                        record["tg_id"],
                        texts.EXPIRED_NOTIFICATION,
                        reply_markup=kb.main_menu_kb(),
                    )
                except Exception:
                    logger.exception("Scheduler: failed to send expiration notice to %s", record["tg_id"])
            except Exception:
                logger.exception("Scheduler: failed to cleanup subscription %s", record["id"])

    @scheduler.scheduled_job("cron", hour=23, minute=59, id="db_backup_msk")
    async def daily_db_backup() -> None:
        try:
            backup_path = await db.backup_db()
            logger.info("Scheduler: daily DB backup created (MSK): %s", backup_path)
        except Exception:
            logger.exception("Scheduler: failed to create daily DB backup (MSK)")

    return scheduler
