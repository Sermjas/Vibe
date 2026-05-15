"""Сборка Dispatcher для бота (main и тесты)."""

from __future__ import annotations

from aiogram import Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage

from vibe.database import Database
from vibe.payments.handlers import bind_db as bind_payments_db
from vibe.payments.handlers import router as payments_router
from vibe.payments.middleware import AccessMiddleware


def create_dispatcher(db: Database) -> Dispatcher:
    from vibe.bot import router as bot_router

    bind_payments_db(db)
    dp = Dispatcher(storage=MemoryStorage())
    access = AccessMiddleware(db)
    dp.message.middleware(access)
    dp.callback_query.middleware(access)
    dp.include_router(payments_router)
    dp.include_router(bot_router)
    return dp
