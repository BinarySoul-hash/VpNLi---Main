"""
Hard IP/device limit enforcement loop.
"""
from __future__ import annotations

import asyncio
import logging
import time

from aiogram import Bot

import database as db
import keyboards as kb
import texts
from config import HARD_IP_LIMIT_COOLDOWN_SECONDS, HARD_IP_LIMIT_ENFORCEMENT
from services.xui import xui

logger = logging.getLogger(__name__)


async def ip_limit_enforcer_loop(bot: Bot, interval: int = 120) -> None:
    logger.info("IP limit enforcer loop started (interval: %d seconds)", interval)
    cooldown_until: dict[str, float] = {}
    notified_until: dict[str, float] = {}
    next_onlines_warn_at: float = 0.0
    while True:
        try:
            online_counts = await xui.get_all_onlines()
            if online_counts is None:
                now_ts = time.time()
                if now_ts >= next_onlines_warn_at:
                    logger.warning(
                        "IP enforcer: /onlines unavailable, using per-client fallback this cycle"
                    )
                    next_onlines_warn_at = now_ts + max(interval, 120)
                online_counts = {}

            active_subs = await db.get_active_subscriptions_map()

            for email, sub in active_subs.items():
                limit = max(1, int(sub.get("devices") or 1))
                now_ts = time.time()

                if HARD_IP_LIMIT_ENFORCEMENT:
                    until = cooldown_until.get(email)
                    if until and now_ts >= until:
                        re_enabled = False
                        for target_id in xui.get_all_inbound_ids():
                            if await xui.enable_client(target_id, email):
                                re_enabled = True
                        if re_enabled:
                            cooldown_until.pop(email, None)
                            logger.info("IP enforcer: re-enabled %s after cooldown", email)

                count = online_counts.get(email, 0)
                if email not in online_counts:
                    count = await xui.get_online_ips_count(email)

                try:
                    await xui.ensure_client_limit_ip(sub["inbound_id"], email, limit)
                except Exception:
                    logger.exception("IP enforcer: ensure_client_limit_ip failed for %s", email)

                if count <= limit:
                    continue

                logger.warning(
                    "IP enforcer: limit exceeded for %s, count=%s limit=%s",
                    email,
                    count,
                    limit,
                )
                await db.log_violation(sub["id"], sub["user_id"], email, count, limit)

                if HARD_IP_LIMIT_ENFORCEMENT and email not in cooldown_until:
                    disabled = False
                    for target_id in xui.get_all_inbound_ids():
                        if await xui.disable_client(target_id, email):
                            disabled = True
                    if disabled:
                        try:
                            await xui.clear_client_ips(email)
                        except Exception:
                            logger.exception("IP enforcer: clear_client_ips failed for %s", email)
                        cooldown_until[email] = now_ts + max(10, int(HARD_IP_LIMIT_COOLDOWN_SECONDS))

                notify_ttl = max(60, int(HARD_IP_LIMIT_COOLDOWN_SECONDS))
                if notified_until.get(email, 0) > now_ts:
                    continue

                user = await db.get_user_by_id(sub["user_id"])
                if not user:
                    continue

                try:
                    await bot.send_message(
                        user["tg_id"],
                        texts.device_limit_exceeded(sub, count, HARD_IP_LIMIT_COOLDOWN_SECONDS),
                        reply_markup=kb.limit_violation_kb(sub["id"]),
                    )
                    notified_until[email] = now_ts + notify_ttl
                except Exception:
                    logger.exception("IP enforcer: failed to notify user %s", user["tg_id"])

        except Exception:
            logger.exception("IP enforcer loop failed")

        await asyncio.sleep(interval)
