"""Тесты /buy и /status."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from tests.helpers.telegram import last_answer_text, make_message
from vibe.payments.handlers import cmd_buy, cmd_status


@pytest.mark.yookassa
@pytest.mark.asyncio
async def test_cmd_buy_returns_payment_button(db, app_config, captured_answers):
    await db.register_user(5001, "buyer")
    msg = make_message("/buy", user_id=5001)
    await cmd_buy(msg)
    text = last_answer_text(captured_answers)
    assert "199" in text
    markup = captured_answers[-1].get("reply_markup")
    assert markup is not None
    url = markup.inline_keyboard[0][0].url
    assert url.startswith("https://mock.yookassa.test/pay/")


@pytest.mark.yookassa
@pytest.mark.asyncio
async def test_cmd_status_inactive(db, captured_answers):
    await db.register_user(5002, "free")
    msg = make_message("/status", user_id=5002)
    await cmd_status(msg)
    text = last_answer_text(captured_answers)
    assert "не активна" in text.lower()


@pytest.mark.yookassa
@pytest.mark.asyncio
async def test_cmd_status_active_subscription(db, captured_answers):
    await db.register_user(5003, "sub")
    await db.set_user_subscription(5003, active=True)
    end = datetime.now(timezone.utc) + timedelta(days=5)
    await db.upsert_subscription(
        telegram_id=5003,
        subscription_end_date=end,
        is_active=True,
    )
    msg = make_message("/status", user_id=5003)
    await cmd_status(msg)
    text = last_answer_text(captured_answers)
    assert "активна" in text.lower()
