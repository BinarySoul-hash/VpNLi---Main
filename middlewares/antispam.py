"""
Simple in-memory anti-spam middleware for messages and callback queries.
"""
from __future__ import annotations

import time
from collections import deque
from typing import Any, Awaitable, Callable, Deque, Dict, Tuple

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject


class AntiSpamMiddleware(BaseMiddleware):
    _MAX_CACHE_SIZE = 5000
    _CLEANUP_INTERVAL = 300

    def __init__(
        self,
        *,
        message_min_interval: float = 0.8,
        callback_min_interval: float = 0.35,
        burst_window_seconds: float = 8.0,
        burst_limit: int = 8,
    ) -> None:
        self.message_min_interval = max(0.1, float(message_min_interval))
        self.callback_min_interval = max(0.1, float(callback_min_interval))
        self.burst_window_seconds = max(1.0, float(burst_window_seconds))
        self.burst_limit = max(3, int(burst_limit))
        self._last_event_at: Dict[Tuple[int, str], float] = {}
        self._events: Dict[Tuple[int, str], Deque[float]] = {}
        self._last_warn_at: Dict[int, float] = {}
        self._last_cleanup_at: float = 0.0

    def _cleanup_if_needed(self) -> None:
        now = time.monotonic()
        if now - self._last_cleanup_at < self._CLEANUP_INTERVAL:
            return
        self._last_cleanup_at = now
        if len(self._last_event_at) <= self._MAX_CACHE_SIZE:
            return
        stale_threshold = now - self.burst_window_seconds * 3
        stale_keys = [
            key for key, ts in self._last_event_at.items()
            if ts < stale_threshold
        ]
        for key in stale_keys:
            self._last_event_at.pop(key, None)
            self._events.pop(key, None)
        stale_warn = [
            uid for uid, ts in self._last_warn_at.items()
            if ts < stale_threshold
        ]
        for uid in stale_warn:
            self._last_warn_at.pop(uid, None)

    def _drop_old(self, dq: Deque[float], now: float) -> None:
        threshold = now - self.burst_window_seconds
        while dq and dq[0] < threshold:
            dq.popleft()

    async def _warn(self, event: Message | CallbackQuery, now: float) -> None:
        tg_id = event.from_user.id
        last = self._last_warn_at.get(tg_id, 0.0)
        if now - last < 3.0:
            return
        self._last_warn_at[tg_id] = now
        if isinstance(event, CallbackQuery):
            await event.answer("Слишком часто. Подождите секунду.", show_alert=False)
        else:
            await event.answer("Слишком часто. Подождите секунду.")

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        if not isinstance(event, (Message, CallbackQuery)) or not event.from_user:
            return await handler(event, data)

        now = time.monotonic()
        self._cleanup_if_needed()
        tg_id = event.from_user.id
        event_type = "callback" if isinstance(event, CallbackQuery) else "message"
        min_interval = (
            self.callback_min_interval if event_type == "callback" else self.message_min_interval
        )
        key = (tg_id, event_type)

        last_at = self._last_event_at.get(key, 0.0)
        if now - last_at < min_interval:
            await self._warn(event, now)
            return

        dq = self._events.setdefault(key, deque())
        self._drop_old(dq, now)
        if len(dq) >= self.burst_limit:
            await self._warn(event, now)
            return

        dq.append(now)
        self._last_event_at[key] = now
        return await handler(event, data)
