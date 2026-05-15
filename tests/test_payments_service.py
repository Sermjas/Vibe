"""Тесты платежей YooKassa (mock-режим)."""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest

from vibe.payments.service import (
    create_subscription_payment,
    process_payment_if_succeeded,
)
from vibe.payments.yookassa_api import YooKassaApiError
from vibe.payments.yookassa_gateway import get_mock_gateway


@pytest.mark.yookassa
@pytest.mark.asyncio
async def test_create_payment_mock_url(db, app_config):
    result = await create_subscription_payment(
        db,
        app_config,
        telegram_id=4001,
        amount_rub=Decimal("199.00"),
    )
    assert result.payment_url.startswith("https://mock.yookassa.test/pay/")
    assert result.payment_id.startswith("mock-")
    assert result.payment_url.endswith(result.payment_id)

    record = await db.get_payment(result.payment_id)
    assert record is not None
    assert record.status == "pending"
    assert record.processed is False


@pytest.mark.yookassa
@pytest.mark.asyncio
async def test_process_success_activates_subscription(db, app_config):
    result = await create_subscription_payment(
        db,
        app_config,
        telegram_id=4002,
        amount_rub=Decimal("199.00"),
    )
    await db.register_user(4002, "payer")

    gateway = get_mock_gateway()
    gateway.set_payment_status(result.payment_id, "succeeded")

    activated = await process_payment_if_succeeded(db, app_config, payment_id=result.payment_id)
    assert activated is True

    user = await db.get_user_by_telegram_id(4002)
    assert user is not None
    assert user.subscription is True

    sub = await db.get_subscription(4002)
    assert sub is not None
    assert sub.is_active is True


@pytest.mark.yookassa
@pytest.mark.asyncio
async def test_process_idempotent_no_double_activation(db, app_config):
    result = await create_subscription_payment(
        db,
        app_config,
        telegram_id=4003,
        amount_rub=Decimal("199.00"),
    )
    await db.register_user(4003, "payer2")
    gateway = get_mock_gateway()
    gateway.set_payment_status(result.payment_id, "succeeded")

    assert await process_payment_if_succeeded(db, app_config, payment_id=result.payment_id)
    first_end = (await db.get_subscription(4003)).subscription_end_date

    assert await process_payment_if_succeeded(db, app_config, payment_id=result.payment_id)
    second_end = (await db.get_subscription(4003)).subscription_end_date
    assert first_end == second_end

    payment = await db.get_payment(result.payment_id)
    assert payment is not None
    assert payment.processed is True


@pytest.mark.yookassa
@pytest.mark.asyncio
async def test_pending_payment_does_not_activate(db, app_config):
    result = await create_subscription_payment(
        db,
        app_config,
        telegram_id=4004,
        amount_rub=Decimal("199.00"),
    )
    await db.register_user(4004, "waiter")

    activated = await process_payment_if_succeeded(db, app_config, payment_id=result.payment_id)
    assert activated is False
    user = await db.get_user_by_telegram_id(4004)
    assert user is not None
    assert user.subscription is False


@pytest.mark.yookassa
@pytest.mark.asyncio
async def test_yookassa_api_error_on_create(db, app_config):
    await db.register_user(4005, "err")
    with patch(
        "vibe.payments.service.get_payment_gateway",
        side_effect=YooKassaApiError("API down"),
    ):
        with pytest.raises(YooKassaApiError):
            await create_subscription_payment(
                db,
                app_config,
                telegram_id=4005,
                amount_rub=Decimal("199.00"),
            )


@pytest.mark.yookassa
@pytest.mark.asyncio
async def test_cmd_buy_handler_api_error(db, app_config, captured_answers):
    from tests.helpers.telegram import last_answer_text, make_message
    from vibe.payments.handlers import cmd_buy

    msg = make_message("/buy", user_id=4006)
    await db.register_user(4006, "buyer")

    with patch(
        "vibe.payments.handlers.create_subscription_payment",
        new_callable=AsyncMock,
        side_effect=YooKassaApiError("fail"),
    ):
        await cmd_buy(msg)

    text = last_answer_text(captured_answers)
    assert "ошибка сервиса оплаты" in text.lower() or "YooKassa" in text
