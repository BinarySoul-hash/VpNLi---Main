"""
Global error handlers for update processing.
"""
from __future__ import annotations

import logging

from aiogram import Router
from aiogram.exceptions import RestartingTelegram, TelegramNetworkError, TelegramServerError
from aiogram.filters.exception import ExceptionTypeFilter
from aiogram.types import ErrorEvent

logger = logging.getLogger(__name__)
router = Router()


@router.errors(ExceptionTypeFilter(TelegramNetworkError, TelegramServerError, RestartingTelegram))
async def handle_transient_telegram_error(event: ErrorEvent) -> bool:
    logger.warning(
        "Transient Telegram API error while processing update %s: %s",
        getattr(event.update, "update_id", "n/a"),
        event.exception,
    )
    return True
