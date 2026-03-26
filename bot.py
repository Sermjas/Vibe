"""Точка входа Telegram-бота (aiogram 3.x). Только обработка апдейтов и вызов сервисов."""

from __future__ import annotations

import asyncio
import logging
import re
import sys
from pathlib import Path

from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import CommandStart
from aiogram.types import Message

from config import get_config
from ocr_service import get_amount_from_checkpoint

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

router = Router()

# Папка для временных фото приёма
TEMP_DIR = Path(__file__).resolve().parent / "temp"


def _ensure_temp_dir() -> None:
    TEMP_DIR.mkdir(parents=True, exist_ok=True)


def _format_amount(amount: float) -> str:
    """Форматирует сумму без лишних нулей после запятой."""
    if amount == int(amount):
        return str(int(amount))
    return str(amount)


def _parse_amount_from_text(text: str) -> float | None:
    """
    Пытается распознать число из пользовательского текста.
    Поддерживает варианты: `636.93`, `636,93` и числа с пробелами/валютой.
    """
    raw = (text or "").strip()
    if not raw:
        return None

    # Игнорируем явные служебные ответы
    if raw.lower() in {"none", "null", "н/д", "n/a"}:
        return None

    # Убираем валюту и лишние символы, оставляя только цифры/разделители/знак.
    compact = re.sub(r"(?i)(руб\.?|рублей|рубля|р\.?|kop\.?|коп\.?|копеек|копейки)", "", raw)
    compact = re.sub(r"[₽$€£¥₸₴]", "", compact)
    compact = re.sub(r"[\s\u00a0\u202f\u2009]+", "", compact)
    compact = re.sub(r"[^\d,.\-+]", "", compact)

    if not compact:
        return None

    # Извлекаем кандидат регуляркой.
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
            left = candidate[:last_comma]
            right = candidate[last_comma + 1 :]
            left = left.replace(".", "").replace(",", "")
            right = re.sub(r"\D", "", right)
            normalized = f"{left}.{right}" if right else left
        else:
            left = candidate[:last_dot]
            right = candidate[last_dot + 1 :]
            left = left.replace(",", "").replace(".", "")
            right = re.sub(r"\D", "", right)
            normalized = f"{left}.{right}" if right else left

    normalized = normalized.strip(".")
    if not normalized or not re.search(r"\d", normalized):
        return None

    try:
        return float(f"{sign}{normalized}")
    except ValueError:
        return None


@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    await message.answer(
        "Пришлите фото чека — я попробую определить сумму покупки."
    )


@router.message(F.photo)
async def on_photo(message: Message, bot: Bot) -> None:
    _ensure_temp_dir()
    photo = message.photo[-1]
    file_path = TEMP_DIR / f"{message.chat.id}_{message.message_id}.jpg"

    try:
        await bot.download(file=photo, destination=file_path)
        logger.info(f"Фото сохранено: {file_path}")
    except Exception:
        logger.exception("Не удалось скачать фото.")
        await message.answer("Не удалось сохранить файл. Попробуйте отправить фото ещё раз.")
        return

    try:
        amount = await get_amount_from_checkpoint(file_path)
    except Exception:
        logger.exception("Ошибка при вызове OCR.")
        amount = None
    finally:
        try:
            if file_path.is_file():
                file_path.unlink()
        except OSError as e:
            logger.warning(f"Не удалось удалить временный файл {file_path}: {e}")

    if isinstance(amount, str):
        # Сообщение не является суммой покупки, это системная ошибка (например, квота API).
        await message.answer(amount)
    elif amount is not None:
        text_sum = _format_amount(amount)
        await message.answer(f"Расход записан: {text_sum} руб.")
    else:
        await message.answer(
            "Не удалось распознать сумму. Сделайте более чёткое фото чека и отправьте снова."
        )


@router.message(F.text)
async def on_text(message: Message) -> None:
    """Распознаёт ручной ввод суммы числами."""
    text = (message.text or "").strip()
    if not text:
        return
    if text.startswith("/"):
        # Команды обрабатываются отдельным хэндлером; игнорируем, чтобы не отвечать дважды.
        return

    amount = _parse_amount_from_text(text)
    if amount is None:
        return

    logger.info(f"Ручной ввод суммы: chat_id={message.chat.id}, amount={amount}")
    await message.answer(f"Расход записан: {_format_amount(amount)} руб.")


@router.message()
async def on_unhandled_message(message: Message) -> None:
    """Финальная заглушка, чтобы не было логов `Update is not handled`."""
    if message.photo:
        return
    if message.text:
        text = message.text.strip()
        if text.startswith("/"):
            return
        if _parse_amount_from_text(text) is not None:
            return
    await message.answer("Я не понял это сообщение")


async def main() -> None:
    cfg = get_config()
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
