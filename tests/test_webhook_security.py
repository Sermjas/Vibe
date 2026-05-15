"""Безопасность webhook: не доверять status из тела уведомления."""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest

from vibe.payments.service import create_subscription_payment
from vibe.payments.webhook import extract_payment_id, process_yookassa_webhook


@pytest.mark.yookassa
@pytest.mark.asyncio
async def test_extract_payment_id_from_notification():
    notification = {"object": {"id": "pay-123", "status": "succeeded"}}
    assert extract_payment_id(notification) == "pay-123"


@pytest.mark.yookassa
@pytest.mark.asyncio
async def test_webhook_does_not_trust_body_status(db, app_config):
    result = await create_subscription_payment(
        db,
        app_config,
        telegram_id=6001,
        amount_rub=Decimal("199.00"),
    )
    await db.register_user(6001, "wh")

    notification = {"object": {"id": result.payment_id, "status": "succeeded"}}
    with patch(
        "vibe.payments.webhook.process_payment_if_succeeded",
        new_callable=AsyncMock,
        return_value=False,
    ) as mocked:
        ok = await process_yookassa_webhook(notification, db=db, cfg=app_config)
        assert ok is False
        mocked.assert_awaited_once_with(db, app_config, payment_id=result.payment_id)


@pytest.mark.yookassa
@pytest.mark.asyncio
async def test_webhook_invalid_payload_returns_false(db, app_config):
    assert await process_yookassa_webhook({}, db=db, cfg=app_config) is False
    assert await process_yookassa_webhook({"object": {}}, db=db, cfg=app_config) is False
