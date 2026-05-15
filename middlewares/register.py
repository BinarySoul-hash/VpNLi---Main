"""
Middleware that auto-registers every incoming user
and blocks banned users.
"""
import logging
from typing import Callable, Dict, Any, Awaitable

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, Message, CallbackQuery

import database as db

logger = logging.getLogger(__name__)


class UserMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        # Extract user from message or callback
        tg_user = None
        ref_code = None

        if isinstance(event, Message):
            tg_user = event.from_user
            logger.debug(f"Message from user {tg_user.id}: {event.text}")
            # Extract ref code from /start ref_XXX (и /start@bot ref_XXX)
            text = (event.text or "").strip()
            if text.startswith("/start"):
                parts = text.split(maxsplit=1)
                payload = parts[1].strip() if len(parts) > 1 else ""
                if payload.startswith("ref_"):
                    ref_code = payload.split("ref_", 1)[1].strip()
        elif isinstance(event, CallbackQuery):
            tg_user = event.from_user

        if tg_user:
            logger.debug(f"Processing user {tg_user.id}")
            user = await db.get_user(tg_user.id)
            if not user:
                logger.debug(f"User {tg_user.id} not found, creating...")
                referred_by_id = None
                if ref_code:
                    referrer = await db.get_user_by_ref_code(ref_code)
                    if referrer and referrer["tg_id"] != tg_user.id:
                        referred_by_id = referrer["id"]

                user = await db.create_user(
                    tg_id=tg_user.id,
                    username=tg_user.username or "",
                    full_name=tg_user.full_name or str(tg_user.id),
                    referred_by_id=referred_by_id,
                )
                logger.debug(f"User {tg_user.id} created: {user}")
            else:
                # Разрешаем привязать реферала уже существующему пользователю,
                # если ранее связь не была установлена и ещё не было оплат.
                if ref_code and not user.get("referred_by"):
                    referrer = await db.get_user_by_ref_code(ref_code)
                    if (
                        referrer
                        and referrer["id"] != user["id"]
                        and not await db.has_paid_before(user["id"])
                    ):
                        changed = await db.set_user_referred_by(user["id"], referrer["id"])
                        if changed:
                            user = await db.get_user(tg_user.id)

            if user and user.get("is_banned"):
                logger.debug(f"User {tg_user.id} is banned")
                if isinstance(event, Message):
                    await event.answer("🚫 Вы заблокированы.")
                elif isinstance(event, CallbackQuery):
                    await event.answer("🚫 Вы заблокированы.", show_alert=True)
                return

            data["db_user"] = user
            logger.debug(f"db_user set for user {tg_user.id}: {user}")
        else:
            logger.warning("No tg_user found in event")

        return await handler(event, data)
