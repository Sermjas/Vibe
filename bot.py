"""Точка входа Telegram-бота (aiogram 3.x). Только интерфейс и маршрутизация."""

from __future__ import annotations

import asyncio
import io
import logging
import re
import sys
from datetime import datetime, timezone
from decimal import Decimal

from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import CommandStart
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from config import get_config
from database import Database
from ocr_service import get_amount_from_checkpoint

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("bot.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger(__name__)
router = Router()

_db: Database | None = None

_CB_STATS_MENU = "stats:menu"
_CB_STATS_ALL = "stats:all"
_CB_STATS_MONTH = "stats:month"


def _get_db() -> Database:
    if _db is None:
        raise RuntimeError("База данных не инициализирована.")
    return _db


def _stats_main_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📊 Моя статистика", callback_data=_CB_STATS_MENU)]
        ]
    )


def _stats_period_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="За всё время", callback_data=_CB_STATS_ALL)],
            [InlineKeyboardButton(text="За этот месяц", callback_data=_CB_STATS_MONTH)],
        ]
    )


def _format_amount(amount: Decimal | float) -> str:
    value = Decimal(str(amount))
    text = format(value.quantize(Decimal("0.01")), "f")
    if text.endswith(".00"):
        return text[:-3]
    if text.endswith("0"):
        return text[:-1]
    return text


def _parse_amount_from_text(text: str) -> Decimal | None:
    raw = (text or "").strip()
    if not raw or raw.lower() in {"none", "null", "н/д", "n/a"}:
        return None

    compact = re.sub(r"(?i)(руб\.?|рублей|рубля|р\.?|kop\.?|коп\.?|копеек|копейки)", "", raw)
    compact = re.sub(r"[₽$€£¥₸₴]", "", compact)
    compact = re.sub(r"[\s\u00a0\u202f\u2009]+", "", compact)
    compact = re.sub(r"[^\d,.\-+]", "", compact)
    if not compact:
        return None

    match = re.search(r"[-+]?\d[\d.,]*", compact)
    if not match:
        return None
    candidate = match.group(0)

    sign = ""
    if candidate and candidate[0] in "+-":
        sign = candidate[0]
        candidate = candidate[1:]

    last_dot = candidate.rfind(".")
    last_comma = candidate.rfind(",")

    if last_dot == -1 and last_comma == -1:
        normalized = candidate
    else:
        dec_sep = "." if last_dot > last_comma else ","
        if dec_sep == ",":
            left = candidate[:last_comma].replace(".", "").replace(",", "")
            right = re.sub(r"\D", "", candidate[last_comma + 1 :])
            normalized = f"{left}.{right}" if right else left
        else:
            left = candidate[:last_dot].replace(",", "").replace(".", "")
            right = re.sub(r"\D", "", candidate[last_dot + 1 :])
            normalized = f"{left}.{right}" if right else left

    normalized = normalized.strip(".")
    if not normalized or not re.search(r"\d", normalized):
        return None

    try:
        return Decimal(f"{sign}{normalized}").quantize(Decimal("0.01"))
    except Exception:
        return None


@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    if message.from_user is None:
        await message.answer("Не удалось определить пользователя Telegram.")
        return

    db = _get_db()
    result = await db.get_or_create_user(
        telegram_id=message.from_user.id,
        username=message.from_user.username,
    )
    if result.created:
        logger.info(
            "Новый пользователь: telegram_id=%s username=%s",
            message.from_user.id,
            message.from_user.username,
        )

    await message.answer(
        "Вы зарегистрированы. Пришлите фото чека — я попробую определить сумму покупки.",
        reply_markup=_stats_main_keyboard(),
    )


@router.message(F.photo)
async def on_photo(message: Message, bot: Bot) -> None:
    if message.from_user is None:
        await message.answer("Не удалось определить пользователя Telegram.")
        return

    db = _get_db()
    user_result = await db.get_or_create_user(
        telegram_id=message.from_user.id,
        username=message.from_user.username,
    )
    photo = message.photo[-1]
    image_buffer = io.BytesIO()

    try:
        await bot.download(file=photo, destination=image_buffer)
        image_bytes = image_buffer.getvalue()
        logger.info(
            "Получено фото для OCR: chat_id=%s, file_id=%s, size=%s байт",
            message.chat.id,
            photo.file_id,
            len(image_bytes),
        )
    except Exception:
        logger.exception("Не удалось скачать фото в память.")
        await message.answer("Не удалось обработать фото. Попробуйте отправить его ещё раз.")
        return

    amount = await get_amount_from_checkpoint(image_bytes)
    if isinstance(amount, str):
        await message.answer(amount)
        return
    if amount is None:
        await message.answer(
            "Не удалось распознать сумму. Сделайте более чёткое фото чека и отправьте снова."
        )
        return

    decimal_amount = Decimal(str(amount)).quantize(Decimal("0.01"))
    await db.add_transaction(
        user_id=user_result.user.id,
        amount=decimal_amount,
        telegram_file_id=photo.file_id,
    )
    logger.info(
        "Успешный OCR: user_id=%s amount=%s file_id=%s",
        user_result.user.id,
        decimal_amount,
        photo.file_id,
    )
    await message.answer(f"Расход записан: {_format_amount(decimal_amount)} руб.")


@router.message(F.text)
async def on_text(message: Message) -> None:
    text = (message.text or "").strip()
    if not text or text.startswith("/"):
        return

    amount = _parse_amount_from_text(text)
    if amount is not None:
        if message.from_user is None:
            await message.answer("Не удалось определить пользователя Telegram.")
            return
        db = _get_db()
        user_result = await db.get_or_create_user(
            telegram_id=message.from_user.id,
            username=message.from_user.username,
        )
        await db.add_transaction(
            user_id=user_result.user.id,
            amount=amount,
            telegram_file_id=None,
        )
        logger.info(
            "Ручной ввод суммы: user_id=%s amount=%s",
            user_result.user.id,
            amount,
        )
        await message.answer(f"Расход записан: {_format_amount(amount)} руб.")
        return

    await message.answer("Я не понял это сообщение")


@router.callback_query(F.data == _CB_STATS_MENU)
async def on_stats_menu(callback: CallbackQuery) -> None:
    if callback.message is None:
        await callback.answer()
        return
    await callback.message.answer("Выберите период статистики:", reply_markup=_stats_period_keyboard())
    await callback.answer()


@router.callback_query(F.data == _CB_STATS_ALL)
async def on_stats_all(callback: CallbackQuery) -> None:
    if callback.message is None:
        await callback.answer()
        return
    if callback.from_user is None:
        await callback.answer("Пользователь не определён.", show_alert=True)
        return
    db = _get_db()
    user_result = await db.get_or_create_user(
        telegram_id=callback.from_user.id,
        username=callback.from_user.username,
    )
    total = await db.get_total_spent(user_result.user.id)
    await callback.message.answer(f"За всё время: {_format_amount(total)} руб.")
    await callback.answer()


@router.callback_query(F.data == _CB_STATS_MONTH)
async def on_stats_month(callback: CallbackQuery) -> None:
    if callback.message is None:
        await callback.answer()
        return
    if callback.from_user is None:
        await callback.answer("Пользователь не определён.", show_alert=True)
        return
    db = _get_db()
    user_result = await db.get_or_create_user(
        telegram_id=callback.from_user.id,
        username=callback.from_user.username,
    )
    now = datetime.now(timezone.utc)
    month_start = datetime(now.year, now.month, 1, tzinfo=timezone.utc)
    total = await db.get_month_spent(user_result.user.id, month_start)
    await callback.message.answer(f"За этот месяц: {_format_amount(total)} руб.")
    await callback.answer()


async def main() -> None:
    global _db

    cfg = get_config()
    _db = Database(cfg.database_url)
    await _db.init_models()
    logger.info("База данных инициализирована.")

    bot = Bot(token=cfg.telegram_bot_token)
    dp = Dispatcher()
    dp.include_router(router)
    logger.info("Бот запущен, ожидание апдейтов…")
    await dp.start_polling(bot)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Остановка по Ctrl+C.")
        sys.exit(0)
    except RuntimeError as e:
        logger.error("%s", e)
        sys.exit(1)
