"""Общие правила доступа: подписка, пробный скан, paywall."""

from __future__ import annotations

from vibe.database import User

NOT_REGISTERED_TEXT = (
    "Сначала зарегистрируйтесь: нажмите /start и кнопку «Зарегистрироваться»."
)

PAYWALL_TEXT = (
    "Пробный скан уже использован.\n"
    "Оформите подписку, чтобы продолжить работу с ботом."
)

BTN_TRIAL_SCAN = "🧪 Пробный скан чека"
BTN_PAY_SUB = "💳 Оплатить подписку"
BTN_SCAN_RECEIPT = "📸 Сканировать чек"

PAID_FEATURE_TEXTS: frozenset[str] = frozenset(
    {
        BTN_SCAN_RECEIPT,
        BTN_TRIAL_SCAN,
        BTN_PAY_SUB,
        "📊 Моя статистика",
        "🛡 Админ-панель",
        "📥 Экспорт всей БД",
    }
)

TRIAL_ALLOWED_TEXTS: frozenset[str] = frozenset({BTN_SCAN_RECEIPT, BTN_TRIAL_SCAN})

CB_REGISTER = "reg:register"


def has_full_access(user: User) -> bool:
    return user.is_admin or user.subscription


def is_trial_exhausted(user: User) -> bool:
    return not has_full_access(user) and user.trial_attempt
