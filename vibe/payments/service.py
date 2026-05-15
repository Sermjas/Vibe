"""Сервисный слой платежей/подписок.

MVP-логика: создаём платёж, затем фоново опрашиваем YooKassa до статуса `succeeded`
и выдаём подписку на 30 дней.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from loguru import logger

from vibe.config import AppConfig
from vibe.database import Database
from vibe.payments.yookassa_gateway import credentials_configured, get_payment_gateway


@dataclass(frozen=True)
class CreatePaymentResult:
    payment_url: str
    payment_id: str


async def create_subscription_payment(
    db: Database,
    cfg: AppConfig,
    *,
    telegram_id: int,
    amount_rub: Decimal,
) -> CreatePaymentResult:
    if not credentials_configured(cfg):
        raise RuntimeError("YooKassa не настроена: задайте YOOKASSA_SHOP_ID и YOOKASSA_SECRET_KEY")

    gateway = get_payment_gateway(cfg)
    payment_url, payment_id = await gateway.create_payment(
        amount_rub,
        telegram_id,
        return_url="https://t.me",
        description="Подписка Vibe на 30 дней",
    )
    await db.create_payment(
        payment_id=payment_id,
        telegram_id=telegram_id,
        amount=amount_rub,
        status="pending",
        processed=False,
        raw=None,
    )
    return CreatePaymentResult(payment_url=payment_url, payment_id=payment_id)


def _is_success_status(payload: dict[str, Any]) -> bool:
    return str(payload.get("status") or "").lower() == "succeeded"


def _extract_status(payload: dict[str, Any]) -> str:
    status = str(payload.get("status") or "").strip()
    return status or "unknown"


async def process_payment_if_succeeded(
    db: Database,
    cfg: AppConfig,
    *,
    payment_id: str,
) -> bool:
    """Проверяет платёж через API; при succeeded активирует подписку (идемпотентно)."""
    if not credentials_configured(cfg):
        raise RuntimeError("YooKassa не настроена: задайте YOOKASSA_SHOP_ID и YOOKASSA_SECRET_KEY")

    record = await db.get_payment(payment_id)
    if record is None:
        return False
    if record.processed:
        return True

    gateway = get_payment_gateway(cfg)
    payload = await gateway.check_payment(payment_id)
    status = _extract_status(payload)
    await db.update_payment(
        payment_id=payment_id,
        status=status,
        raw=payload,
    )

    if not _is_success_status(payload):
        return False

    end_date = datetime.now(timezone.utc) + timedelta(days=30)
    await db.upsert_subscription(
        telegram_id=record.telegram_id,
        subscription_end_date=end_date,
        is_active=True,
    )
    await db.set_user_subscription(record.telegram_id, active=True)
    await db.mark_payment_processed(payment_id)
    logger.info(f"Подписка активирована: telegram_id={record.telegram_id} until={end_date.isoformat()}")
    return True


async def run_payment_poller(
    db: Database,
    cfg: AppConfig,
    *,
    interval_s: float = 10.0,
) -> None:
    """Фоновый poller: периодически проверяет незавершённые платежи."""
    logger.info("Payment poller запущен.")
    while True:
        try:
            if not credentials_configured(cfg):
                await asyncio.sleep(interval_s)
                continue
            pending = await db.get_unprocessed_payments(limit=50)
            if pending:
                logger.debug(f"Payment poller: pending={len(pending)}")
            for p in pending:
                try:
                    await process_payment_if_succeeded(db, cfg, payment_id=p.payment_id)
                except Exception:
                    logger.exception(f"Ошибка обработки платежа {p.payment_id}")
            await asyncio.sleep(interval_s)
        except asyncio.CancelledError:
            logger.info("Payment poller остановлен.")
            raise
        except Exception:
            logger.exception("Payment poller: ошибка цикла, повтор через интервал")
            await asyncio.sleep(interval_s)
