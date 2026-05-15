"""Интеграционные тесты AccessMiddleware."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from tests.helpers.telegram import last_answer_text, make_message
from vibe.payments.access import BTN_SCAN_RECEIPT, PAYWALL_TEXT
from vibe.payments.middleware import AccessMiddleware


@pytest.mark.asyncio
async def test_middleware_blocks_paid_button_without_registration(db, captured_answers):
    mw = AccessMiddleware(db)
    handler = AsyncMock(return_value="ok")
    msg = make_message(BTN_SCAN_RECEIPT, user_id=3001)
    result = await mw(handler, msg, {})
    assert result is None
    handler.assert_not_awaited()
    assert "зарегистрируйтесь" in last_answer_text(captured_answers).lower()


@pytest.mark.asyncio
async def test_middleware_allows_trial_scan_text(db, captured_answers):
    await db.register_user(3002, "u")
    mw = AccessMiddleware(db)
    handler = AsyncMock(return_value="ok")
    msg = make_message(BTN_SCAN_RECEIPT, user_id=3002)
    result = await mw(handler, msg, {})
    assert result == "ok"
    handler.assert_awaited_once()


@pytest.mark.asyncio
async def test_middleware_paywall_after_trial(db, captured_answers):
    await db.register_user(3003, "u")
    await db.mark_trial_used(3003)
    mw = AccessMiddleware(db)
    handler = AsyncMock()
    msg = make_message(BTN_SCAN_RECEIPT, user_id=3003)
    await mw(handler, msg, {})
    handler.assert_not_awaited()
    assert PAYWALL_TEXT in last_answer_text(captured_answers)
