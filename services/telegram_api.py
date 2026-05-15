"""
Helpers for resilient Telegram Bot API requests.
"""
from __future__ import annotations

import asyncio
import logging

from aiogram.client.session.middlewares.base import BaseRequestMiddleware, NextRequestMiddlewareType
from aiogram.exceptions import (
    RestartingTelegram,
    TelegramBadRequest,
    TelegramNetworkError,
    TelegramRetryAfter,
    TelegramServerError,
)
from aiogram.methods.base import TelegramType

logger = logging.getLogger(__name__)


class TelegramRetryMiddleware(BaseRequestMiddleware):
    def __init__(self, attempts: int = 3, base_delay: float = 1.0) -> None:
        self.attempts = max(1, int(attempts))
        self.base_delay = max(0.1, float(base_delay))

    def _is_retryable_method(self, api_method: str) -> bool:
        return (
            api_method.startswith("edit")
            or api_method.startswith("get")
            or api_method in {
                "answerCallbackQuery",
                "deleteMessage",
                "sendChatAction",
            }
        )

    async def __call__(
        self,
        make_request: NextRequestMiddlewareType[TelegramType],
        bot,
        method,
    ) -> TelegramType:
        api_method = method.__api_method__
        if not self._is_retryable_method(api_method):
            return await make_request(bot, method)

        for attempt in range(1, self.attempts + 1):
            try:
                return await make_request(bot, method)
            except TelegramRetryAfter as exc:
                if attempt >= self.attempts:
                    raise
                delay = max(self.base_delay, min(float(exc.retry_after), 5.0))
                logger.warning(
                    "Telegram API asked to retry %s in %.1fs (%d/%d)",
                    api_method,
                    delay,
                    attempt,
                    self.attempts,
                )
                await asyncio.sleep(delay)
            except (TelegramNetworkError, TelegramServerError, RestartingTelegram, asyncio.TimeoutError) as exc:
                if attempt >= self.attempts:
                    raise
                delay = self.base_delay * (2 ** (attempt - 1))  # Exponential backoff
                logger.warning(
                    "Telegram API transient error on %s, retrying in %.1fs (%d/%d): %s",
                    api_method,
                    delay,
                    attempt,
                    self.attempts,
                    exc,
                )
                await asyncio.sleep(delay)
            except TelegramBadRequest as exc:
                # Harmless case: trying to edit message with exactly same text/markup.
                if api_method.startswith("edit") and "message is not modified" in str(exc).lower():
                    logger.debug("Ignoring benign TelegramBadRequest on %s: %s", api_method, exc)
                    return True
                raise

        raise RuntimeError(f"Unreachable retry flow for Telegram method {api_method}")
