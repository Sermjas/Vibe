"""Безопасная обработка webhook YooKassa (если будет подключён).

Важно: статус из тела уведомления не используется для выдачи подписки —
только повторная проверка через API (как в poller).
"""

from __future__ import annotations

from typing import Any

from vibe.config import AppConfig
from vibe.database import Database
from vibe.payments.service import process_payment_if_succeeded


def extract_payment_id(notification: dict[str, Any]) -> str | None:
    """Извлекает payment_id из тела webhook без доверия к status."""
    if not isinstance(notification, dict):
        return None
    obj = notification.get("object")
    if not isinstance(obj, dict):
        return None
    payment_id = str(obj.get("id") or "").strip()
    return payment_id or None


async def process_yookassa_webhook(
    notification: dict[str, Any],
    *,
    db: Database,
    cfg: AppConfig,
) -> bool:
    """Обрабатывает webhook: активация только после verify через YooKassa API."""
    payment_id = extract_payment_id(notification)
    if payment_id is None:
        return False
    return await process_payment_if_succeeded(db, cfg, payment_id=payment_id)
