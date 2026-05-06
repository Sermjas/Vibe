"""Middleware проверки подписки для платных фич."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject

from vibe.database import Database


class SubscriptionMiddleware(BaseMiddleware):
    def __init__(self, db: Database) -> None:
        self._db = db

    @staticmethod
    def _is_command_message(message: Message, command: str) -> bool:
        text = (message.text or "").strip()
        return text == f"/{command}" or text.startswith(f"/{command}@")

    def _is_allowed_without_subscription(self, event: TelegramObject) -> bool:
        if isinstance(event, Message):
            if self._is_command_message(event, "start"):
                return True
            if self._is_command_message(event, "buy"):
                return True
            if self._is_command_message(event, "status"):
                return True
            # Системные/информационные пункты меню оставляем бесплатными.
            if (event.text or "").strip() in {"ℹ️ Инфо", "🆘 Поддержка"}:
                return True
        return False

    @staticmethod
    def _is_paid_feature(event: TelegramObject) -> bool:
        # Платные фичи в текущем проекте: OCR, статистика и экспорт.
        if isinstance(event, Message):
            if event.photo:
                return True
            text = (event.text or "").strip()
            if text in {"📸 Сканировать чек", "📊 Моя статистика"}:
                return True
            if text.startswith("/export"):
                return True
            # Ручной ввод суммы — тоже часть платного функционала.
            if text and not text.startswith("/"):
                return True
            return False

        if isinstance(event, CallbackQuery):
            data = (event.data or "").strip()
            if data.startswith("ocr:"):
                return True
            if data.startswith("stats:"):
                return True
            if data.startswith("mod:"):
                # Модерация — не платная, это админский флоу.
                return False
            return False

        return False

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if self._is_allowed_without_subscription(event):
            return await handler(event, data)

        if not self._is_paid_feature(event):
            return await handler(event, data)

        from_user = getattr(event, "from_user", None)
        if from_user is None:
            return await handler(event, data)

        # Админ не ограничивается подпиской.
        user_res = await self._db.get_or_create_user(telegram_id=from_user.id, username=from_user.username)
        if user_res.user.is_admin:
            return await handler(event, data)

        sub = await self._db.get_subscription(from_user.id)
        now = datetime.now(timezone.utc)
        is_active = (
            sub is not None
            and sub.is_active
            and sub.subscription_end_date is not None
            and sub.subscription_end_date > now
        )
        if is_active:
            return await handler(event, data)

        text = "Доступ ограничен, пожалуйста оплатите подписку: /buy"
        if isinstance(event, Message):
            await event.answer(text)
            return None
        if isinstance(event, CallbackQuery):
            if event.message is not None:
                await event.message.answer(text)
            await event.answer("Доступ ограничен", show_alert=True)
            return None

        return None

