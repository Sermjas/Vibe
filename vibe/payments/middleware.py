"""Middleware контроля доступа: регистрация, пробный скан, подписка."""

from __future__ import annotations

from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject

from vibe.database import Database
from vibe.payments.access_service import AccessService


class AccessMiddleware(BaseMiddleware):
    """Проверяет регистрацию, пробный скан и подписку перед платными действиями."""

    def __init__(self, db: Database) -> None:
        self._access = AccessService(db)

    async def _deny(
        self,
        event: TelegramObject,
        *,
        text: str,
        alert: str | None = None,
    ) -> None:
        if isinstance(event, Message):
            await event.answer(text)
            return
        if isinstance(event, CallbackQuery):
            if event.message is not None:
                await event.message.answer(text)
            await event.answer(alert or "Доступ ограничен", show_alert=True)

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        verdict = await self._access.evaluate_middleware(event)
        if verdict is None:
            return await handler(event, data)
        if verdict.allowed:
            return await handler(event, data)
        await self._deny(event, text=verdict.deny_text or "", alert=verdict.deny_alert)
        return None
