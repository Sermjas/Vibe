from __future__ import annotations

import asyncio

from aiogram import Bot
from aiogram.exceptions import TelegramNetworkError, TelegramRetryAfter
from loguru import logger


async def send_admin_message(*, bot_token: str, admin_id: int, text: str) -> None:
    if not bot_token or not bot_token.strip():
        raise ValueError("Пустой TELEGRAM_BOT_TOKEN")
    if not admin_id:
        raise ValueError("Пустой ADMIN_ID")

    bot = Bot(token=bot_token.strip())
    try:
        for attempt in range(1, 4):
            try:
                await bot.send_message(chat_id=admin_id, text=text)
                return
            except TelegramRetryAfter as e:
                delay = max(int(getattr(e, "retry_after", 1)), 1)
                logger.warning(f"Telegram rate limit, ждём {delay} сек (попытка {attempt}/3)")
                await asyncio.sleep(delay)
            except TelegramNetworkError as e:
                logger.warning(f"Сетевая ошибка Telegram: {e} (попытка {attempt}/3)")
                await asyncio.sleep(attempt)
        raise RuntimeError("Не удалось отправить уведомление администратору в Telegram (3 попытки)")
    finally:
        await bot.session.close()

