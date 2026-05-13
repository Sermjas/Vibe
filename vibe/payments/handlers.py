"""Хендлеры оплаты/подписки."""

from __future__ import annotations

from decimal import Decimal

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message
from loguru import logger

from vibe.config import get_config
from vibe.database import Database
from vibe.payments.service import create_subscription_payment
from vibe.payments.yookassa_api import YooKassaApiError

router = Router()

_SUBSCRIPTION_PRICE_RUB = Decimal("199.00")

_db: Database | None = None


def bind_db(db: Database) -> None:
    global _db
    _db = db


def _get_db() -> Database:
    if _db is None:
        raise RuntimeError("База данных не инициализирована (payments).")
    return _db


def _pay_button(url: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="Оплатить", url=url)]]
    )


@router.message(Command("buy"))
async def cmd_buy(message: Message) -> None:
    if message.from_user is None:
        await message.answer("Не удалось определить пользователя Telegram.")
        return

    cfg = get_config()
    db = _get_db()
    telegram_id = message.from_user.id

    try:
        result = await create_subscription_payment(
            db,
            cfg,
            telegram_id=telegram_id,
            amount_rub=_SUBSCRIPTION_PRICE_RUB,
        )
    except YooKassaApiError:
        logger.exception("Ошибка API YooKassa при создании платежа")
        await message.answer(
            "Не удалось создать платёж: ошибка сервиса оплаты. "
            "Попробуйте позже или проверьте настройки YooKassa."
        )
        return
    except Exception:
        logger.exception("Не удалось создать платёж")
        await message.answer(
            "Не удалось создать платёж. Попробуйте позже или обратитесь в поддержку."
        )
        return

    await message.answer(
        "Оплата подписки на 30 дней.\n"
        f"Сумма: {_SUBSCRIPTION_PRICE_RUB} руб.\n"
        "Нажмите кнопку ниже, чтобы перейти к оплате.",
        reply_markup=_pay_button(result.payment_url),
    )


@router.message(Command("status"))
async def cmd_status(message: Message) -> None:
    if message.from_user is None:
        await message.answer("Не удалось определить пользователя Telegram.")
        return

    db = _get_db()
    telegram_id = message.from_user.id

    sub = await db.get_subscription(telegram_id)
    if sub is None or (not sub.is_active):
        await message.answer("Подписка не активна. Чтобы оплатить: /buy")
        return

    end_date = sub.subscription_end_date
    if end_date is None:
        await message.answer("Подписка не активна. Чтобы оплатить: /buy")
        return

    await message.answer(
        "Подписка активна.\n"
        f"Действует до (UTC): {end_date.isoformat()}",
    )

