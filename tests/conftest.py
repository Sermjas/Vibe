"""Общие фикстуры pytest."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest
from aiogram.types import CallbackQuery, Message

import vibe.bot as bot_module
from vibe.config import clear_config_cache, get_config
from vibe.database import Database
from vibe.dispatcher_factory import create_dispatcher
from vibe.payments.handlers import bind_db as bind_payments_db
from vibe.payments.yookassa_gateway import reset_mock_gateway


@pytest.fixture
def env_config(monkeypatch, tmp_path):
    db_path = tmp_path / "test.db"
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "1234567890:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw")
    monkeypatch.setenv("GEMINI_API_KEY", "test-gemini-key")
    monkeypatch.setenv("ADMIN_ID", "999")
    monkeypatch.setenv("DATABASE_PATH", str(db_path))
    monkeypatch.setenv("YOOKASSA_MOCK_MODE", "true")
    monkeypatch.setenv("YOOKASSA_SHOP_ID", "mock-shop")
    monkeypatch.setenv("YOOKASSA_SECRET_KEY", "mock-secret")
    clear_config_cache()
    reset_mock_gateway()
    yield tmp_path
    clear_config_cache()
    reset_mock_gateway()


@pytest.fixture
async def db(env_config):
    cfg = get_config()
    database = Database(cfg.resolved_database_url, admin_telegram_id=cfg.admin_id)
    await database.init_models()
    bind_payments_db(database)
    bot_module._db = database
    bot_module._admin_id = cfg.admin_id
    yield database
    bot_module._db = None
    bot_module._admin_id = None


@pytest.fixture
def app_config(env_config):
    return get_config()


@pytest.fixture
def dispatcher(db):
    return create_dispatcher(db)


@pytest.fixture
def captured_answers(monkeypatch) -> list[dict[str, Any]]:
    """Перехватывает Message.answer / CallbackQuery.answer (frozen models)."""
    captured: list[dict[str, Any]] = []

    async def message_answer(self, text: str, **kwargs: Any) -> Any:
        captured.append({"kind": "message", "text": text, **kwargs})
        return AsyncMock()

    async def callback_answer(self, text: str | None = None, **kwargs: Any) -> Any:
        captured.append({"kind": "callback", "text": text, **kwargs})
        return True

    monkeypatch.setattr(Message, "answer", message_answer)
    monkeypatch.setattr(CallbackQuery, "answer", callback_answer)
    return captured
