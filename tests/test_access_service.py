"""Тесты AccessService: регистрация, trial, подписка."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from tests.helpers.telegram import make_callback, make_message
from vibe.payments.access import PAYWALL_TEXT, has_full_access
from vibe.payments.access_service import AccessService


@pytest.mark.asyncio
async def test_registration_defaults(db):
    user = await db.register_user(1001, "alice")
    assert user.trial_attempt is False
    assert user.subscription is False
    assert has_full_access(user) is False


@pytest.mark.asyncio
async def test_trial_exhausted_after_mark(db):
    await db.register_user(1002, "bob")
    await db.mark_trial_used(1002)
    user = await db.get_user_by_telegram_id(1002)
    assert user is not None
    assert user.trial_attempt is True

    access = AccessService(db)
    verdict = access.check_ocr_allowed(user)
    assert verdict.allowed is False
    assert verdict.deny_text == PAYWALL_TEXT


@pytest.mark.asyncio
async def test_sync_yookassa_subscription_sets_flag(db):
    await db.register_user(1003, "carol")
    end = datetime.now(timezone.utc) + timedelta(days=10)
    await db.upsert_subscription(
        telegram_id=1003,
        subscription_end_date=end,
        is_active=True,
    )

    access = AccessService(db)
    synced = await access.sync_yookassa_subscription(1003)
    assert synced is True
    user = await db.get_user_by_telegram_id(1003)
    assert user is not None
    assert user.subscription is True


@pytest.mark.asyncio
async def test_middleware_allows_trial_photo(db):
    await db.register_user(1004, "dave")
    access = AccessService(db)
    msg = make_message(with_photo=True, user_id=1004)
    verdict = await access.evaluate_middleware(msg)
    assert verdict is not None
    assert verdict.allowed is True


@pytest.mark.asyncio
async def test_middleware_blocks_exhausted_trial(db):
    await db.register_user(1005, "erin")
    await db.mark_trial_used(1005)
    access = AccessService(db)
    msg = make_message(with_photo=True, user_id=1005)
    verdict = await access.evaluate_middleware(msg)
    assert verdict is not None
    assert verdict.allowed is False
    assert verdict.deny_text == PAYWALL_TEXT


@pytest.mark.asyncio
async def test_middleware_allows_register_callback(db):
    access = AccessService(db)
    cb = make_callback("reg:register", user_id=9999)
    assert access.is_always_allowed_event(cb) is True
    verdict = await access.evaluate_middleware(cb)
    assert verdict is None
