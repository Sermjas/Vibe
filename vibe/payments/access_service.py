"""Централизованные проверки доступа пользователя (регистрация, trial, подписка)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from aiogram.types import CallbackQuery, Message, TelegramObject

from vibe.database import Database, User
from vibe.payments.access import (
    CB_REGISTER,
    NOT_REGISTERED_TEXT,
    PAID_FEATURE_TEXTS,
    PAYWALL_TEXT,
    TRIAL_ALLOWED_TEXTS,
    has_full_access,
    is_trial_exhausted,
)

_CB_MOD_PREFIXES = ("mod:approve:", "mod:block:")


@dataclass(frozen=True)
class AccessVerdict:
    """Результат проверки доступа."""

    allowed: bool
    deny_text: str | None = None
    deny_alert: str | None = None


class AccessService:
    """Проверки регистрации, пробного скана и подписки."""

    def __init__(self, db: Database) -> None:
        self._db = db

    async def get_user(self, telegram_id: int) -> User | None:
        return await self._db.get_user_by_telegram_id(telegram_id)

    async def has_active_yookassa_subscription(self, telegram_id: int) -> bool:
        sub = await self._db.get_subscription(telegram_id)
        if sub is None or not sub.is_active or sub.subscription_end_date is None:
            return False
        end = sub.subscription_end_date
        if end.tzinfo is None:
            end = end.replace(tzinfo=timezone.utc)
        return end > datetime.now(timezone.utc)

    async def sync_yookassa_subscription(self, telegram_id: int) -> bool:
        """Синхронизирует users.subscription с активной записью YooKassa."""
        if not await self.has_active_yookassa_subscription(telegram_id):
            return False
        await self._db.set_user_subscription(telegram_id, active=True)
        return True

    async def resolve_user_access(self, telegram_id: int) -> User | None:
        """Возвращает пользователя; при активной подписке YooKassa обновляет subscription."""
        user = await self.get_user(telegram_id)
        if user is None:
            return None
        if has_full_access(user):
            return user
        if await self.sync_yookassa_subscription(telegram_id):
            return await self.get_user(telegram_id)
        return user

    def check_ocr_allowed(self, user: User) -> AccessVerdict:
        if is_trial_exhausted(user):
            return AccessVerdict(
                allowed=False,
                deny_text=PAYWALL_TEXT,
                deny_alert="Пробный скан уже использован",
            )
        return AccessVerdict(allowed=True)

    def check_trial_scan_button(self, user: User | None) -> AccessVerdict:
        if user is None or has_full_access(user):
            return AccessVerdict(allowed=False)
        if is_trial_exhausted(user):
            return AccessVerdict(allowed=False, deny_text=PAYWALL_TEXT)
        return AccessVerdict(allowed=True)

    @staticmethod
    def is_always_allowed_event(event: TelegramObject) -> bool:
        if isinstance(event, Message):
            text = (event.text or "").strip()
            if text in {"/start", "/buy", "/status"} or text.startswith(
                ("/start@", "/buy@", "/status@")
            ):
                return True
            if text in {"ℹ️ Инфо", "🆘 Поддержка"}:
                return True
            return False
        if isinstance(event, CallbackQuery):
            data = (event.data or "").strip()
            if data == CB_REGISTER:
                return True
            if data.startswith(_CB_MOD_PREFIXES):
                return True
        return False

    @staticmethod
    def is_paid_feature_event(event: TelegramObject) -> bool:
        if isinstance(event, Message):
            if event.photo:
                return True
            text = (event.text or "").strip()
            if text in PAID_FEATURE_TEXTS:
                return True
            if text.startswith("/export") or text.startswith("/admin"):
                return True
            return False
        if isinstance(event, CallbackQuery):
            data = (event.data or "").strip()
            return data.startswith("ocr:") or data.startswith("stats:")
        return False

    @staticmethod
    def is_trial_allowed_event(event: TelegramObject) -> bool:
        if isinstance(event, Message):
            if event.photo:
                return True
            return (event.text or "").strip() in TRIAL_ALLOWED_TEXTS
        if isinstance(event, CallbackQuery):
            return (event.data or "").strip().startswith("ocr:")
        return False

    async def evaluate_middleware(self, event: TelegramObject) -> AccessVerdict | None:
        """None — пропустить без проверки (публичное или не платное)."""
        if self.is_always_allowed_event(event):
            return None
        if not self.is_paid_feature_event(event):
            return None

        from_user = getattr(event, "from_user", None)
        if from_user is None:
            return None

        user = await self.get_user(from_user.id)
        if user is None:
            return AccessVerdict(
                allowed=False,
                deny_text=NOT_REGISTERED_TEXT,
                deny_alert="Требуется регистрация",
            )

        if has_full_access(user):
            return AccessVerdict(allowed=True)

        if await self.sync_yookassa_subscription(from_user.id):
            return AccessVerdict(allowed=True)

        if not user.trial_attempt and self.is_trial_allowed_event(event):
            return AccessVerdict(allowed=True)

        return AccessVerdict(
            allowed=False,
            deny_text=PAYWALL_TEXT,
            deny_alert="Нужна подписка",
        )
