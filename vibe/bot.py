"""Точка входа Telegram-бота (aiogram 3.x). Только интерфейс и маршрутизация."""

from __future__ import annotations

import asyncio
import csv
import io
import re
import sys
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, cast

import pandas as pd
from pandas._typing import WriteExcelBuffer
from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramUnauthorizedError
from aiogram.filters import BaseFilter, Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
)
from loguru import logger

from vibe.config import get_config
from vibe.database import Database, Transaction, User
from vibe.ocr_service import OCR_RATE_LIMIT_ERROR, get_amount_from_checkpoint
from vibe.payments.access import (
    BTN_PAY_SUB,
    BTN_SCAN_RECEIPT,
    BTN_TRIAL_SCAN,
    CB_REGISTER,
    NOT_REGISTERED_TEXT,
    PAYWALL_TEXT,
    has_full_access,
)
from vibe.payments.access_service import AccessService
from vibe.payments.service import run_payment_poller

router = Router()

_db: Database | None = None
_admin_id: int | None = None

_CB_STATS_MENU = "stats:menu"
_CB_STATS_ALL = "stats:all"
_CB_STATS_MONTH = "stats:month"

_CB_OCR_CONFIRM = "ocr:confirm"
_CB_OCR_EDIT = "ocr:edit"
_CB_OCR_CANCEL = "ocr:cancel"
_CB_OCR_EDIT_AMOUNT = "ocr:edit_amount"
_CB_OCR_CATEGORY_MENU = "ocr:category_menu"
_CB_OCR_CATEGORY_PREFIX = "ocr:category:"
_CB_MOD_APPROVE = "mod:approve:"
_CB_MOD_BLOCK = "mod:block:"
_CB_STATS_CSV = "stats:csv"
_CB_STATS_EXCEL = "stats:excel"

_CATEGORIES: list[str] = [
    "Продукты",
    "Рестораны",
    "Транспорт",
    "Одежда",
    "Здоровье",
    "Развлечения",
    "Другое",
]


class IsAdmin(BaseFilter):
    """Доступ только для ADMIN_ID из конфигурации."""

    async def __call__(self, message: Message) -> bool:
        return message.from_user is not None and message.from_user.id == _get_admin_id()


class OCRState(StatesGroup):
    """FSM: подтверждение результата OCR и ручные правки."""

    confirming = State()
    waiting_manual_amount = State()
    waiting_category = State()


def _fsm_state_key(state: State) -> str:
    key = state.state
    if key is None:
        raise RuntimeError(f"FSM state key is not set for {state!r}")
    return key


_OCR_ACTIVE_STATES = frozenset(
    {
        _fsm_state_key(OCRState.confirming),
        _fsm_state_key(OCRState.waiting_manual_amount),
        _fsm_state_key(OCRState.waiting_category),
    }
)


def _register_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Зарегистрироваться", callback_data=CB_REGISTER)],
        ]
    )


def _limited_menu_keyboard() -> ReplyKeyboardMarkup:
    """Меню после регистрации без активной подписки."""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_TRIAL_SCAN), KeyboardButton(text=BTN_PAY_SUB)],
            [KeyboardButton(text="ℹ️ Инфо"), KeyboardButton(text="🆘 Поддержка")],
        ],
        resize_keyboard=True,
        is_persistent=True,
        input_field_placeholder="Выберите действие",
    )


def _main_reply_keyboard(is_admin_user: bool) -> ReplyKeyboardMarkup:
    """Главное меню: постоянная клавиатура; третий ряд — только для администратора."""
    row1 = [
        KeyboardButton(text=BTN_SCAN_RECEIPT),
        KeyboardButton(text="📊 Моя статистика"),
    ]
    row2 = [
        KeyboardButton(text="ℹ️ Инфо"),
        KeyboardButton(text="🆘 Поддержка"),
    ]
    rows: list[list[KeyboardButton]] = [row1, row2]
    if is_admin_user:
        rows.append(
            [
                KeyboardButton(text="🛡 Админ-панель"),
                KeyboardButton(text="📥 Экспорт всей БД"),
            ]
        )
    return ReplyKeyboardMarkup(
        keyboard=rows,
        resize_keyboard=True,
        is_persistent=True,
        input_field_placeholder="Пришлите фото чека для распознавания",
    )


def _receipt_confirm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Верно", callback_data=_CB_OCR_CONFIRM),
                InlineKeyboardButton(text="📝 Изменить", callback_data=_CB_OCR_EDIT),
                InlineKeyboardButton(text="❌ Отмена", callback_data=_CB_OCR_CANCEL),
            ],
        ]
    )


def _receipt_edit_keyboard() -> InlineKeyboardMarkup:
    """Подменю после «Изменить»: сумма и категория."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✏️ Изменить сумму", callback_data=_CB_OCR_EDIT_AMOUNT),
                InlineKeyboardButton(text="📁 Категория", callback_data=_CB_OCR_CATEGORY_MENU),
            ],
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


def _moderation_keyboard(target_telegram_id: int) -> InlineKeyboardMarkup:
    """Кнопки модерации для админов (Approve / Block)."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Approve",
                    callback_data=f"{_CB_MOD_APPROVE}{target_telegram_id}",
                ),
                InlineKeyboardButton(
                    text="Block",
                    callback_data=f"{_CB_MOD_BLOCK}{target_telegram_id}",
                ),
            ]
        ]
    )


def _blocked_access_text() -> str:
    """Сообщение для заблокированных пользователей."""
    return "Ваш аккаунт заблокирован. Обратитесь в поддержку."


def _access() -> AccessService:
    return AccessService(_get_db())


async def _get_registered_user(message: Message) -> User | None:
    if message.from_user is None:
        await message.answer("Не удалось определить пользователя Telegram.")
        return None
    db = _get_db()
    user = await db.get_user_by_telegram_id(message.from_user.id)
    if user is None:
        await message.answer(NOT_REGISTERED_TEXT, reply_markup=_register_keyboard())
        return None
    if not user.is_active and not user.is_admin:
        await message.answer(_blocked_access_text())
        return None
    return user


async def _require_ocr_session(
    callback: CallbackQuery,
    state: FSMContext,
    *,
    allowed_states: frozenset[str] | None = None,
) -> dict[str, Any] | None:
    """Проверяет владельца FSM-сессии OCR и допустимое состояние."""
    if callback.from_user is None or callback.message is None:
        await callback.answer()
        return None

    current_state = await state.get_state()
    if current_state is None or current_state not in _OCR_ACTIVE_STATES:
        await callback.answer("Нет активного распознавания.", show_alert=True)
        return None
    if allowed_states is not None and current_state not in allowed_states:
        await callback.answer("Действие недоступно в текущем шаге.", show_alert=True)
        return None

    data = await state.get_data()
    user_id = data.get("user_id")
    owner_telegram_id = data.get("owner_telegram_id")
    if not isinstance(user_id, int) or owner_telegram_id != callback.from_user.id:
        await state.clear()
        await callback.answer("Нет данных для подтверждения.", show_alert=True)
        return None

    db = _get_db()
    user = await db.get_user_by_id(user_id)
    if user is None or user.telegram_id != callback.from_user.id:
        await state.clear()
        await callback.answer("Доступ запрещён", show_alert=True)
        return None
    if not user.is_active and not user.is_admin:
        await state.clear()
        await callback.message.answer(_blocked_access_text())
        await callback.answer("Аккаунт заблокирован", show_alert=True)
        return None

    return data


async def _get_registered_user_from_callback(callback: CallbackQuery) -> User | None:
    if callback.from_user is None:
        await callback.answer()
        return None
    db = _get_db()
    user = await db.get_user_by_telegram_id(callback.from_user.id)
    if user is None:
        if callback.message is not None:
            await callback.message.answer(NOT_REGISTERED_TEXT, reply_markup=_register_keyboard())
        await callback.answer("Требуется регистрация", show_alert=True)
        return None
    if not user.is_active and not user.is_admin:
        if callback.message is not None:
            await callback.message.answer(_blocked_access_text())
        await callback.answer("Аккаунт заблокирован", show_alert=True)
        return None
    return user


def _get_db() -> Database:
    if _db is None:
        raise RuntimeError("База данных не инициализирована.")
    return _db


def _get_admin_id() -> int:
    if _admin_id is None:
        raise RuntimeError("ADMIN_ID не инициализирован.")
    return _admin_id


async def _answer_admin_panel(message: Message) -> None:
    """Текст админ-панели (общий для /admin и кнопки меню)."""
    db = _get_db()
    users_count = await db.get_users_count()
    today_sum = await db.get_today_total_sum()
    await message.answer(
        "Админ-панель:\n"
        f"- Пользователей: {users_count}\n"
        f"- Сумма транзакций за сегодня (UTC): {_format_amount(today_sum)} руб."
    )


def _stats_csv_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📥 Скачать CSV", callback_data=_CB_STATS_CSV)],
            [InlineKeyboardButton(text="📥 Export to Excel", callback_data=_CB_STATS_EXCEL)],
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


def _user_csv_bytes(txs: list[Transaction]) -> tuple[bytes, str]:
    """Генерация CSV пользователя в памяти (csv + StringIO), без pandas."""
    output = io.StringIO()
    writer = csv.writer(output, delimiter=",", lineterminator="\n")
    writer.writerow(["created_at_utc", "amount", "category", "telegram_file_id"])
    for tx in txs:
        created = tx.created_at.astimezone(timezone.utc).isoformat()
        writer.writerow([created, _format_amount(tx.amount), tx.category or "", tx.telegram_file_id or ""])
    data = output.getvalue().encode("utf-8-sig")
    return data, "my_transactions.csv"


def _user_excel_bytes(txs: list[Transaction]) -> tuple[bytes, str]:
    """Генерация Excel-файла в памяти через pandas и openpyxl."""
    table_rows = [
        {
            "created_at_utc": tx.created_at.astimezone(timezone.utc).isoformat(),
            "amount": float(tx.amount),
            "category": tx.category or "",
            "telegram_file_id": tx.telegram_file_id or "",
        }
        for tx in txs
    ]
    frame = pd.DataFrame.from_records(
        table_rows,
        columns=["created_at_utc", "amount", "category", "telegram_file_id"],
    )

    output = io.BytesIO()
    with pd.ExcelWriter(cast(WriteExcelBuffer, output), engine="openpyxl") as writer:
        frame.to_excel(writer, sheet_name="Отчёт", index=False)
    output.seek(0)

    report_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    filename = f"report_{report_date}.xlsx"
    return output.getvalue(), filename


@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    if message.from_user is None:
        await message.answer("Не удалось определить пользователя Telegram.")
        return

    db = _get_db()
    telegram_id = message.from_user.id
    username = message.from_user.username
    user = await db.get_user_by_telegram_id(telegram_id)

    if user is None:
        await message.answer(
            "Добро пожаловать!\n"
            "Для начала работы нажмите «Зарегистрироваться».",
            reply_markup=_register_keyboard(),
        )
        return

    await db.update_user_username(telegram_id, username)

    if not user.is_active and not user.is_admin:
        await message.answer(_blocked_access_text())
        return

    is_adm = telegram_id == _get_admin_id()
    if has_full_access(user):
        await message.answer(
            "С возвращением! Пришлите фото чека — я распознаю сумму и категорию.",
            reply_markup=_main_reply_keyboard(is_adm),
        )
        return

    await message.answer(
        "С возвращением!\n"
        "Доступен один пробный скан чека или оформление подписки.",
        reply_markup=_limited_menu_keyboard(),
    )


@router.callback_query(F.data == CB_REGISTER)
async def on_register(callback: CallbackQuery) -> None:
    if callback.from_user is None:
        await callback.answer()
        return

    db = _get_db()
    telegram_id = callback.from_user.id
    existing = await db.get_user_by_telegram_id(telegram_id)
    if existing is not None:
        keyboard = (
            _main_reply_keyboard(telegram_id == _get_admin_id())
            if has_full_access(existing)
            else _limited_menu_keyboard()
        )
        if callback.message is not None:
            await callback.message.answer("Вы уже зарегистрированы.", reply_markup=keyboard)
        await callback.answer("Уже зарегистрированы")
        return

    user = await db.register_user(telegram_id, callback.from_user.username)
    logger.info(f"Регистрация: telegram_id={telegram_id} username={callback.from_user.username}")

    if callback.message is None:
        await callback.answer("Регистрация завершена")
        return

    if has_full_access(user):
        await callback.message.answer(
            "Регистрация завершена. Добро пожаловать!",
            reply_markup=_main_reply_keyboard(telegram_id == _get_admin_id()),
        )
    else:
        await callback.message.answer(
            "Регистрация завершена.\n"
            "Выберите «Пробный скан чека» или «Оплатить подписку».",
            reply_markup=_limited_menu_keyboard(),
        )
    await callback.answer("Готово")


@router.message(F.text == BTN_TRIAL_SCAN)
async def on_trial_scan_button(message: Message) -> None:
    user = await _get_registered_user(message)
    if user is None:
        return
    fresh = await _access().resolve_user_access(user.telegram_id)
    verdict = _access().check_trial_scan_button(fresh)
    if not verdict.allowed:
        if verdict.deny_text:
            await message.answer(verdict.deny_text)
        return
    await message.answer("Отправьте фото чека для пробного сканирования.")


@router.message(F.text == BTN_PAY_SUB)
async def on_pay_subscription_button(message: Message) -> None:
    if message.from_user is None:
        return
    user = await _get_registered_user(message)
    if user is None:
        return
    await message.answer("Для оплаты подписки отправьте команду /buy")


@router.callback_query(F.data.startswith(_CB_MOD_APPROVE))
async def on_moderation_approve(callback: CallbackQuery, bot: Bot) -> None:
    """Админ подтверждает доступ пользователю."""
    if callback.from_user is None:
        await callback.answer()
        return

    db = _get_db()
    admin_user = await db.get_or_create_user(
        telegram_id=callback.from_user.id,
        username=callback.from_user.username,
    )
    if not admin_user.user.is_admin:
        await callback.answer("Доступ запрещён", show_alert=True)
        return

    if callback.data is None:
        await callback.answer()
        return

    telegram_id_str = callback.data[len(_CB_MOD_APPROVE) :]
    try:
        target_telegram_id = int(telegram_id_str)
    except ValueError:
        await callback.answer("Неверный ID пользователя", show_alert=True)
        return

    await db.set_user_active_by_telegram_id(target_telegram_id, True)
    await bot.send_message(chat_id=target_telegram_id, text="Доступ предоставлен.")
    await callback.answer("Разрешено")


@router.callback_query(F.data.startswith(_CB_MOD_BLOCK))
async def on_moderation_block(callback: CallbackQuery, bot: Bot) -> None:
    """Админ блокирует доступ пользователю."""
    if callback.from_user is None:
        await callback.answer()
        return

    db = _get_db()
    admin_user = await db.get_or_create_user(
        telegram_id=callback.from_user.id,
        username=callback.from_user.username,
    )
    if not admin_user.user.is_admin:
        await callback.answer("Доступ запрещён", show_alert=True)
        return

    if callback.data is None:
        await callback.answer()
        return

    telegram_id_str = callback.data[len(_CB_MOD_BLOCK) :]
    try:
        target_telegram_id = int(telegram_id_str)
    except ValueError:
        await callback.answer("Неверный ID пользователя", show_alert=True)
        return

    await db.set_user_active_by_telegram_id(target_telegram_id, False)
    await bot.send_message(chat_id=target_telegram_id, text="Ваш аккаунт заблокирован.")
    await callback.answer("Заблокировано", show_alert=True)


@router.message(Command("export"))
async def cmd_export(message: Message) -> None:
    """Экспорт транзакций пользователя в CSV (как в «Моя статистика»)."""
    user = await _get_registered_user(message)
    if user is None:
        return
    db = _get_db()
    txs = await db.get_user_transactions(user.id)
    if not txs:
        await message.answer("Пока нет транзакций для экспорта.")
        return

    data, filename = _user_csv_bytes(txs)
    await message.answer_document(BufferedInputFile(data, filename=filename))


@router.message(Command("admin"))
async def cmd_admin(message: Message) -> None:
    """Админ-панель: только ADMIN_ID из .env."""
    if message.from_user is None:
        await message.answer("Не удалось определить пользователя Telegram.")
        return
    if message.from_user.id != _get_admin_id():
        await message.answer("Доступ запрещён.")
        return

    await _answer_admin_panel(message)


@router.message(F.photo)
async def on_photo(message: Message, bot: Bot, state: FSMContext) -> None:
    user = await _get_registered_user(message)
    if user is None:
        await state.clear()
        return

    db = _get_db()
    fresh_user = await db.get_user_by_telegram_id(user.telegram_id)
    if fresh_user is None:
        await state.clear()
        await message.answer(NOT_REGISTERED_TEXT, reply_markup=_register_keyboard())
        return

    ocr_verdict = _access().check_ocr_allowed(fresh_user)
    if not ocr_verdict.allowed:
        await state.clear()
        if ocr_verdict.deny_text:
            await message.answer(ocr_verdict.deny_text)
        return
    if not has_full_access(fresh_user):
        if await state.get_state() is not None:
            await message.answer("Сначала завершите или отмените текущее распознавание чека.")
            return

    photos = message.photo
    if not photos:
        await message.answer("Не удалось получить фото. Попробуйте отправить его ещё раз.")
        return
    photo = photos[-1]
    image_buffer = io.BytesIO()

    try:
        await bot.download(file=photo, destination=image_buffer)
        image_bytes = image_buffer.getvalue()
        logger.info(
            f"Получено фото для OCR: chat_id={message.chat.id}, "
            f"file_id={photo.file_id}, size={len(image_bytes)} байт"
        )
    except Exception:
        logger.exception("Не удалось скачать фото в память.")
        await message.answer("Не удалось обработать фото. Попробуйте отправить его ещё раз.")
        return

    amount = await get_amount_from_checkpoint(image_bytes)
    if amount == OCR_RATE_LIMIT_ERROR:
        await message.answer(
            "⚠️ Сервис распознавания временно недоступен: достигнут лимит запросов к Gemini (429). "
            "Повторите попытку примерно через 10 минут."
        )
        return
    if isinstance(amount, str):
        await message.answer(amount)
        return
    if amount is None:
        await message.answer("Не удалось распознать сумму. Сделайте более чёткое фото чека и отправьте снова.")
        return

    ocr_amount = amount.get("amount") if isinstance(amount, dict) else None
    ocr_category = amount.get("category") if isinstance(amount, dict) else None
    decimal_amount: Decimal | None = None
    if ocr_amount is not None:
        decimal_amount = Decimal(str(ocr_amount)).quantize(Decimal("0.01"))

    await state.set_state(OCRState.confirming)
    await state.update_data(
        user_id=fresh_user.id,
        owner_telegram_id=fresh_user.telegram_id,
        telegram_file_id=photo.file_id,
        amount=str(decimal_amount) if decimal_amount is not None else None,
        category=ocr_category or "Другое",
        raw_data=amount if isinstance(amount, dict) else None,
    )

    logger.info(
        "OCR выполнен (ожидание подтверждения): "
        f"user_id={fresh_user.id} amount={decimal_amount} "
        f"category={ocr_category} file_id={photo.file_id}"
    )
    amount_text = _format_amount(decimal_amount) if decimal_amount is not None else "не найдена"
    category_text = (ocr_category or "Другое") if isinstance(ocr_category, str) else "Другое"
    await message.answer(
        "Я распознал:\n"
        f"- Сумма: {amount_text}\n"
        f"- Категория: {category_text}\n"
        "\n"
        "Подтвердите или исправьте данные.",
        reply_markup=_receipt_confirm_keyboard(),
    )


@router.callback_query(F.data == _CB_OCR_EDIT)
async def on_ocr_edit(callback: CallbackQuery, state: FSMContext) -> None:
    if await _require_ocr_session(callback, state, allowed_states=frozenset({_fsm_state_key(OCRState.confirming)})) is None:
        return
    if callback.message is None:
        await callback.answer()
        return
    await callback.message.answer("Что изменить?", reply_markup=_receipt_edit_keyboard())
    await callback.answer()


@router.callback_query(F.data == _CB_OCR_EDIT_AMOUNT)
async def on_ocr_edit_amount(callback: CallbackQuery, state: FSMContext) -> None:
    if await _require_ocr_session(callback, state, allowed_states=frozenset({_fsm_state_key(OCRState.confirming)})) is None:
        return
    if callback.message is None:
        await callback.answer()
        return
    await state.set_state(OCRState.waiting_manual_amount)
    await callback.message.answer("Введите сумму вручную (например: 123.45).")
    await callback.answer()


@router.callback_query(F.data == _CB_OCR_CATEGORY_MENU)
async def on_ocr_category_menu(callback: CallbackQuery, state: FSMContext) -> None:
    if await _require_ocr_session(callback, state, allowed_states=frozenset({_fsm_state_key(OCRState.confirming)})) is None:
        return
    if callback.message is None:
        await callback.answer()
        return
    await state.set_state(OCRState.waiting_category)
    await callback.message.answer("Выберите категорию:", reply_markup=_category_keyboard())
    await callback.answer()


@router.callback_query(F.data.startswith(_CB_OCR_CATEGORY_PREFIX))
async def on_ocr_category_pick(callback: CallbackQuery, state: FSMContext) -> None:
    if await _require_ocr_session(callback, state, allowed_states=frozenset({_fsm_state_key(OCRState.waiting_category)})) is None:
        return
    if callback.message is None:
        await callback.answer()
        return
    category = callback.data[len(_CB_OCR_CATEGORY_PREFIX) :] if callback.data else ""
    if category not in _CATEGORIES:
        await callback.answer("Неизвестная категория.", show_alert=True)
        return
    await state.update_data(category=category)
    await state.set_state(OCRState.confirming)
    await callback.message.answer(f"Категория обновлена: {category}")
    await callback.answer()


@router.callback_query(F.data == _CB_OCR_CANCEL)
async def on_ocr_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    if await _require_ocr_session(callback, state) is None:
        return
    await state.clear()
    if callback.message is not None:
        await callback.message.answer("Распознавание отменено.")
    await callback.answer()


@router.callback_query(F.data == _CB_OCR_CONFIRM)
async def on_ocr_confirm(callback: CallbackQuery, state: FSMContext) -> None:
    data = await _require_ocr_session(callback, state, allowed_states=frozenset({_fsm_state_key(OCRState.confirming)}))
    if data is None or callback.message is None or callback.from_user is None:
        return

    user_id = data.get("user_id")
    amount_raw = data.get("amount")
    telegram_file_id = data.get("telegram_file_id")
    category = data.get("category")
    raw_data = data.get("raw_data")

    if not isinstance(user_id, int):
        await state.clear()
        await callback.answer("Нет данных для подтверждения.", show_alert=True)
        return

    db = _get_db()
    fresh_user = await db.get_user_by_telegram_id(callback.from_user.id)
    if fresh_user is None:
        await state.clear()
        await callback.answer("Требуется регистрация", show_alert=True)
        return
    ocr_verdict = _access().check_ocr_allowed(fresh_user)
    if not ocr_verdict.allowed:
        await state.clear()
        if ocr_verdict.deny_text:
            await callback.message.answer(ocr_verdict.deny_text)
        await callback.answer(ocr_verdict.deny_alert or "Доступ ограничен", show_alert=True)
        return

    if amount_raw is None:
        await callback.answer("Сумма не указана. Нажмите «Изменить».", show_alert=True)
        return

    try:
        decimal_amount = Decimal(str(amount_raw)).quantize(Decimal("0.01"))
    except Exception:
        await callback.answer("Некорректная сумма. Нажмите «Изменить».", show_alert=True)
        return

    await db.add_transaction(
        user_id=user_id,
        amount=decimal_amount,
        telegram_file_id=str(telegram_file_id) if telegram_file_id is not None else None,
        category=str(category) if category is not None else None,
        raw_data=raw_data if isinstance(raw_data, dict) else None,
    )
    if not has_full_access(fresh_user):
        await db.mark_trial_used(callback.from_user.id)

    logger.info(
        "Транзакция подтверждена: "
        f"user_id={user_id} amount={decimal_amount} "
        f"category={category} file_id={telegram_file_id}"
    )
    await state.clear()
    await callback.message.answer(
        f"Расход записан: {_format_amount(decimal_amount)} руб."
        + (f" (категория: {category})" if category else "")
    )
    if fresh_user and not has_full_access(fresh_user):
        await callback.message.answer(
            "Пробный скан использован. Для продолжения оформите подписку: /buy",
            reply_markup=_limited_menu_keyboard(),
        )
    await callback.answer("Сохранено")


@router.callback_query(F.data == _CB_STATS_CSV)
async def on_stats_csv(callback: CallbackQuery) -> None:
    if callback.message is None:
        await callback.answer()
        return
    if callback.from_user is None:
        await callback.answer("Пользователь не определён.", show_alert=True)
        return
    user = await _get_registered_user_from_callback(callback)
    if user is None:
        return
    db = _get_db()
    txs = await db.get_user_transactions(user.id)
    if not txs:
        await callback.answer("Нет транзакций для выгрузки.", show_alert=True)
        return
    data, filename = _user_csv_bytes(txs)
    await callback.message.answer_document(BufferedInputFile(data, filename=filename))
    await callback.answer()


@router.callback_query(F.data == _CB_STATS_EXCEL)
async def on_stats_excel(callback: CallbackQuery, bot: Bot) -> None:
    """Экспорт транзакций пользователя в Excel-файл."""
    if callback.message is None:
        await callback.answer()
        return
    if callback.from_user is None:
        await callback.answer("Пользователь не определён.", show_alert=True)
        return

    user = await _get_registered_user_from_callback(callback)
    if user is None:
        return
    db = _get_db()
    txs = await db.get_user_transactions(user.id)
    if not txs:
        await callback.answer("Нет транзакций для выгрузки.", show_alert=True)
        return

    data, filename = _user_excel_bytes(txs)
    await bot.send_document(
        chat_id=callback.message.chat.id,
        document=BufferedInputFile(data, filename=filename),
    )
    await callback.answer("Excel-отчёт отправлен.")


@router.message(F.text == "📊 Моя статистика")
async def on_my_stats(message: Message) -> None:
    user = await _get_registered_user(message)
    if user is None:
        return
    db = _get_db()
    total = await db.get_total_spent(user.id)
    now = datetime.now(timezone.utc)
    month_start = datetime(now.year, now.month, 1, tzinfo=timezone.utc)
    month_total = await db.get_month_spent(user.id, month_start)
    await message.answer(
        "📊 Ваша статистика\n\n"
        f"За всё время: {_format_amount(total)} руб.\n"
        f"За этот месяц: {_format_amount(month_total)} руб.",
        reply_markup=_stats_csv_keyboard(),
    )


@router.message(F.text == BTN_SCAN_RECEIPT)
async def on_scan_hint(message: Message) -> None:
    await message.answer("Сфотографируйте чек и отправьте изображение в этот чат — я распознаю сумму и категорию.")


@router.message(F.text == "ℹ️ Инфо")
async def on_info(message: Message) -> None:
    await message.answer(
        "Бот учитывает расходы по фото чеков (Gemini OCR). "
        "Команда /start — главное меню. Поддержка: через кнопку «Поддержка»."
    )


@router.message(F.text == "🆘 Поддержка")
async def on_support(message: Message) -> None:
    await message.answer(
        "Если что-то не работает: пришлите чёткое фото чека целиком и проверьте лимиты API. "
        "При ошибке лимита подождите около 10 минут и повторите."
    )


@router.message(IsAdmin(), F.text == "🛡 Админ-панель")
async def on_admin_panel(message: Message) -> None:
    await _answer_admin_panel(message)


@router.message(IsAdmin(), F.text == "📥 Экспорт всей БД")
async def on_admin_full_export(message: Message) -> None:
    """Полный экспорт транзакций всех пользователей (только админ)."""
    db = _get_db()
    rows = await db.get_all_transactions_with_telegram_ids()
    if not rows:
        await message.answer("В базе пока нет транзакций.")
        return
    output = io.StringIO()
    writer = csv.writer(output, delimiter=",", lineterminator="\n")
    writer.writerow(["created_at_utc", "telegram_id", "amount", "category", "telegram_file_id"])
    for tx, tg_id in rows:
        created = tx.created_at.astimezone(timezone.utc).isoformat()
        writer.writerow([created, tg_id, _format_amount(tx.amount), tx.category or "", tx.telegram_file_id or ""])
    data = output.getvalue().encode("utf-8-sig")
    await message.answer_document(BufferedInputFile(data, filename="full_database_export.csv"))


@router.message(F.text)
async def on_text(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip()
    if not text or text.startswith("/"):
        return

    if message.from_user is None:
        await message.answer("Не удалось определить пользователя Telegram.")
        return

    user = await _get_registered_user(message)
    if user is None:
        await state.clear()
        return

    db = _get_db()
    fresh_user = await db.get_user_by_telegram_id(message.from_user.id)
    if fresh_user is None:
        await state.clear()
        return

    ocr_verdict = _access().check_ocr_allowed(fresh_user)
    if not ocr_verdict.allowed:
        await state.clear()
        if ocr_verdict.deny_text:
            await message.answer(ocr_verdict.deny_text)
        return

    current_state = await state.get_state()
    if current_state == OCRState.waiting_manual_amount.state:
        amount = _parse_amount_from_text(text)
        if amount is None:
            await message.answer("Не смог распознать сумму. Попробуйте ещё раз (например: 123.45).")
            return
        await state.update_data(amount=str(amount))
        await state.set_state(OCRState.confirming)
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

    if not has_full_access(fresh_user):
        await message.answer("Я не понял это сообщение. Используйте кнопки меню или пришлите фото чека.")
        return

    amount = _parse_amount_from_text(text)
    if amount is not None:
        await state.set_state(OCRState.confirming)
        await state.update_data(
            user_id=fresh_user.id,
            owner_telegram_id=message.from_user.id,
            telegram_file_id=None,
            amount=str(amount),
            category="Другое",
            raw_data={"source": "manual_text", "input": text},
        )
        logger.info(
            "Ручной ввод суммы (ожидание подтверждения): "
            f"user_id={fresh_user.id} amount={amount}"
        )
        await message.answer(
            "Вы ввели сумму вручную:\n"
            f"- Сумма: {_format_amount(amount)}\n"
            "- Категория: Другое\n"
            "\n"
            "Подтвердите сохранение или измените данные.",
            reply_markup=_receipt_confirm_keyboard(),
        )
        return

    await message.answer("Я не понял это сообщение. Используйте кнопки меню или пришлите фото чека.")


async def main() -> None:
    global _db
    global _admin_id

    cfg = get_config()
    # Конфигурируем loguru: stdout + файл в каталоге данных.
    logger.remove()
    logger.add(
        sys.stdout,
        level=cfg.log_level.upper(),
        format="{time:YYYY-MM-DD HH:mm:ss} | {level} | {name}:{function}:{line} - {message}",
        enqueue=True,
    )
    logger.add(
        cfg.resolved_log_path,
        level=cfg.log_level.upper(),
        rotation="10 MB",
        retention="14 days",
        encoding="utf-8",
        enqueue=True,
    )

    _admin_id = cfg.admin_id
    _db = Database(cfg.resolved_database_url, admin_telegram_id=cfg.admin_id)
    await _db.init_models()
    logger.info("База данных инициализирована.")
    from vibe.dispatcher_factory import create_dispatcher

    bot = Bot(token=cfg.telegram_bot_token)
    dp = create_dispatcher(_db)
    logger.info("Бот запущен, ожидание апдейтов…")
    poller_task = asyncio.create_task(run_payment_poller(_db, cfg))
    try:
        await dp.start_polling(bot)
    except TelegramUnauthorizedError:
        logger.error(
            "Telegram вернул Unauthorized. Проверьте TELEGRAM_BOT_TOKEN в .env: "
            "токен должен быть действительным бот-токеном от @BotFather, без лишних пробелов."
        )
        raise
    finally:
        poller_task.cancel()
        try:
            await poller_task
        except asyncio.CancelledError:
            pass


def run() -> None:
    """Синхронный запуск приложения."""
    asyncio.run(main())


if __name__ == "__main__":
    run()
