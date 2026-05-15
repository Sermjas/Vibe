"""Тесты хендлеров bot.py и интеграции с AccessService."""

from __future__ import annotations

import pytest

from tests.helpers.telegram import last_answer_text, make_callback, make_message
from vibe.bot import cmd_start, on_register, on_trial_scan_button
from vibe.payments.access import BTN_TRIAL_SCAN, CB_REGISTER


@pytest.mark.asyncio
async def test_start_unregistered_user(db, captured_answers):
    msg = make_message("/start", user_id=2001)
    await cmd_start(msg)
    text = last_answer_text(captured_answers)
    assert "Зарегистрироваться" in text
    user = await db.get_user_by_telegram_id(2001)
    assert user is None


@pytest.mark.asyncio
async def test_start_registered_limited_menu(db, captured_answers):
    await db.register_user(2002, "limited")
    msg = make_message("/start", user_id=2002)
    await cmd_start(msg)
    text = last_answer_text(captured_answers)
    assert "пробный скан" in text.lower() or "Пробный" in text


@pytest.mark.asyncio
async def test_register_creates_user_with_trial_false(db, captured_answers):
    cb = make_callback(CB_REGISTER, user_id=2003)
    await on_register(cb)
    user = await db.get_user_by_telegram_id(2003)
    assert user is not None
    assert user.trial_attempt is False
    assert user.subscription is False


@pytest.mark.asyncio
async def test_trial_scan_button_prompts_photo(db, captured_answers):
    await db.register_user(2004, "trialer")
    msg = make_message(BTN_TRIAL_SCAN, user_id=2004)
    await on_trial_scan_button(msg)
    text = last_answer_text(captured_answers)
    assert "фото чека" in text.lower()


@pytest.mark.asyncio
async def test_trial_scan_blocked_after_exhausted(db, captured_answers):
    await db.register_user(2005, "done")
    await db.mark_trial_used(2005)
    msg = make_message(BTN_TRIAL_SCAN, user_id=2005)
    await on_trial_scan_button(msg)
    text = last_answer_text(captured_answers)
    assert "Пробный скан уже использован" in text
