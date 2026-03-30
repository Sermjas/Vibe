"""Точка входа Telegram-бота (aiogram 3.x). Только интерфейс и маршрутизация."""

from __future__ import annotations

import asyncio
import csv
import io
import logging
import re
import sys
from datetime import datetime, timezone
from decimal import Decimal

from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

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

_CB_OCR_CONFIRM = "ocr:confirm"
_CB_OCR_EDIT_AMOUNT = "ocr:edit_amount"
_CB_OCR_CATEGORY_MENU = "ocr:category_menu"
_CB_OCR_CATEGORY_PREFIX = "ocr:category:"

_CATEGORIES: list[str] = [
    "Продукты",
    "Рестораны",
    "Транспорт",
    "Одежда",
    "Здоровье",
    "Развлечения",
    "Другое",
]


class ReceiptStates(StatesGroup):
    """FSM для подтверждения OCR и ручных правок."""

    waiting_confirmation = State()
    waiting_manual_amount = State()
    waiting_category = State()


def _receipt_confirm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Верно", callback_data=_CB_OCR_CONFIRM),
                InlineKeyboardButton(text="✏️ Изменить сумму", callback_data=_CB_OCR_EDIT_AMOUNT),
            ],
            [InlineKeyboardButton(text="📁 Категория", callback_data=_CB_OCR_CATEGORY_MENU)],
        ]
    )


def _category_keyboard() -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for i in range(0, len(_CATEGORIES), 2):
        row = [
            InlineKeyboardButton(
                text=_CATEGORIES[i], callback_data=f"{_CB_OCR_CATEGORY_PREFIX}{_CATEGORIES[i]}"
            )
        ]
        if i + 1 < len(_CATEGORIES):
            row.append(
                InlineKeyboardButton(
                    text=_CATEGORIES[i + 1],
                    callback_data=f"{_CB_OCR_CATEGORY_PREFIX}{_CATEGORIES[i + 1]}",
                )
            )
        rows.append(row)
    return InlineKeyboardMarkup(inline_keyboard=rows)


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


@router.message(Command("export"))
async def cmd_export(message: Message) -> None:
    """Экспорт транзакций пользователя в CSV."""
    if message.from_user is None:
        await message.answer("Не удалось определить пользователя Telegram.")
        return
    db = _get_db()
    user_result = await db.get_or_create_user(
        telegram_id=message.from_user.id,
        username=message.from_user.username,
    )
    txs = await db.get_user_transactions(user_result.user.id)
    if not txs:
        await message.answer("Пока нет транзакций для экспорта.")
        return

    output = io.StringIO()
    writer = csv.writer(output, delimiter=",", lineterminator="\n")
    writer.writerow(["created_at_utc", "amount", "category", "telegram_file_id"])
    for tx in txs:
        created = tx.created_at.astimezone(timezone.utc).isoformat()
        writer.writerow(
            [
                created,
                _format_amount(tx.amount),
                tx.category or "",
                tx.telegram_file_id or "",
            ]
        )
    data = output.getvalue().encode("utf-8-sig")
    filename = f"transactions_{message.from_user.id}.csv"
    await message.answer_document(BufferedInputFile(data, filename=filename))


@router.message(Command("admin"))
async def cmd_admin(message: Message) -> None:
    """Админ-панель: доступ только для user.is_admin == True."""
    if message.from_user is None:
        await message.answer("Не удалось определить пользователя Telegram.")
        return
    db = _get_db()
    user_result = await db.get_or_create_user(
        telegram_id=message.from_user.id,
        username=message.from_user.username,
    )
    if not user_result.user.is_admin:
        await message.answer("Доступ запрещён.")
        return

    users_count = await db.get_users_count()
    today_sum = await db.get_today_total_sum()
    await message.answer(
        "Админ-панель:\n"
        f"- Пользователей: {users_count}\n"
        f"- Сумма транзакций за сегодня (UTC): {_format_amount(today_sum)} руб."
    )


@router.message(F.photo)
async def on_photo(message: Message, bot: Bot, state: FSMContext) -> None:
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

    # Новый поток: сначала подтверждение пользователем, запись в БД только по кнопке "Верно".
    ocr_amount = amount.get("amount") if isinstance(amount, dict) else None
    ocr_category = amount.get("category") if isinstance(amount, dict) else None
    decimal_amount: Decimal | None = None
    if ocr_amount is not None:
        decimal_amount = Decimal(str(ocr_amount)).quantize(Decimal("0.01"))

    await state.set_state(ReceiptStates.waiting_confirmation)
    await state.update_data(
        user_id=user_result.user.id,
        telegram_file_id=photo.file_id,
        amount=str(decimal_amount) if decimal_amount is not None else None,
        category=ocr_category or "Другое",
        raw_data=amount if isinstance(amount, dict) else None,
    )

    logger.info(
        "OCR выполнен (ожидание подтверждения): user_id=%s amount=%s category=%s file_id=%s",
        user_result.user.id,
        decimal_amount,
        ocr_category,
        photo.file_id,
    )
    amount_text = (
        _format_amount(decimal_amount) if decimal_amount is not None else "не найдена"
    )
    category_text = (ocr_category or "Другое") if isinstance(ocr_category, str) else "Другое"
    await message.answer(
        "Я распознал:\n"
        f"- Сумма: {amount_text}\n"
        f"- Категория: {category_text}\n"
        "\n"
        "Подтвердите или исправьте данные.",
        reply_markup=_receipt_confirm_keyboard(),
    )


@router.callback_query(F.data == _CB_OCR_EDIT_AMOUNT)
async def on_ocr_edit_amount(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.message is None:
        await callback.answer()
        return
    await state.set_state(ReceiptStates.waiting_manual_amount)
    await callback.message.answer("Введите сумму вручную (например: 123.45).")
    await callback.answer()


@router.callback_query(F.data == _CB_OCR_CATEGORY_MENU)
async def on_ocr_category_menu(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.message is None:
        await callback.answer()
        return
    await state.set_state(ReceiptStates.waiting_category)
    await callback.message.answer("Выберите категорию:", reply_markup=_category_keyboard())
    await callback.answer()


@router.callback_query(F.data.startswith(_CB_OCR_CATEGORY_PREFIX))
async def on_ocr_category_pick(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.message is None:
        await callback.answer()
        return
    category = callback.data[len(_CB_OCR_CATEGORY_PREFIX) :] if callback.data else ""
    if category not in _CATEGORIES:
        await callback.answer("Неизвестная категория.", show_alert=True)
        return
    await state.update_data(category=category)
    await state.set_state(ReceiptStates.waiting_confirmation)
    await callback.message.answer(f"Категория обновлена: {category}")
    await callback.answer()


@router.callback_query(F.data == _CB_OCR_CONFIRM)
async def on_ocr_confirm(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.message is None:
        await callback.answer()
        return
    data = await state.get_data()
    user_id = data.get("user_id")
    amount_raw = data.get("amount")
    telegram_file_id = data.get("telegram_file_id")
    category = data.get("category")
    raw_data = data.get("raw_data")

    if not isinstance(user_id, int):
        await callback.answer("Нет данных для подтверждения.", show_alert=True)
        return
    if amount_raw is None:
        await callback.answer("Сумма не указана. Нажмите «Изменить сумму».", show_alert=True)
        return

    try:
        decimal_amount = Decimal(str(amount_raw)).quantize(Decimal("0.01"))
    except Exception:
        await callback.answer("Некорректная сумма. Нажмите «Изменить сумму».", show_alert=True)
        return

    db = _get_db()
    await db.add_transaction(
        user_id=user_id,
        amount=decimal_amount,
        telegram_file_id=str(telegram_file_id) if telegram_file_id is not None else None,
        category=str(category) if category is not None else None,
        raw_data=raw_data if isinstance(raw_data, dict) else None,
    )
    logger.info(
        "Транзакция подтверждена: user_id=%s amount=%s category=%s file_id=%s",
        user_id,
        decimal_amount,
        category,
        telegram_file_id,
    )
    await state.clear()
    await callback.message.answer(
        f"Расход записан: {_format_amount(decimal_amount)} руб."
        + (f" (категория: {category})" if category else "")
    )
    await callback.answer("Сохранено")


@router.message(F.text)
async def on_text(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip()
    if not text or text.startswith("/"):
        return

    # FSM: ручной ввод суммы для OCR-подтверждения
    current_state = await state.get_state()
    if current_state == ReceiptStates.waiting_manual_amount.state:
        amount = _parse_amount_from_text(text)
        if amount is None:
            await message.answer("Не смог распознать сумму. Попробуйте ещё раз (например: 123.45).")
            return
        await state.update_data(amount=str(amount))
        await state.set_state(ReceiptStates.waiting_confirmation)
        data = await state.get_data()
        category = data.get("category") or "Другое"
        await message.answer(
            "Сумма обновлена.\n"
            f"- Сумма: {_format_amount(amount)}\n"
            f"- Категория: {category}\n"
            "\n"
            "Подтвердите сохранение.",
            reply_markup=_receipt_confirm_keyboard(),
        )
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
            category=None,
            raw_data=None,
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
    # Хранилище FSM в памяти (достаточно для текущего проекта).
    dp = Dispatcher(storage=MemoryStorage())
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
