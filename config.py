"""Загрузка настроек из переменных окружения (.env)."""

from dataclasses import dataclass
import os

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class AppConfig:
    """Конфигурация приложения с удобным доступом к полям."""

    telegram_bot_token: str
    gemini_api_key: str
    database_url: str
    # Строго целочисленный Telegram user id администратора (из .env).
    admin_id: int


def get_config() -> AppConfig:
    """Читает и валидирует конфигурацию из окружения."""
    token = (os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
    api_key = (os.getenv("GEMINI_API_KEY") or "").strip()
    db_url = (os.getenv("DATABASE_URL") or "sqlite+aiosqlite:///bot.db").strip()
    admin_raw = (os.getenv("ADMIN_ID") or "").strip()
    if not token:
        raise RuntimeError("В .env или окружении не задан TELEGRAM_BOT_TOKEN.")
    if not api_key:
        raise RuntimeError("В .env или окружении не задан GEMINI_API_KEY.")
    if not db_url:
        raise RuntimeError("В .env или окружении не задан DATABASE_URL.")
    if not admin_raw:
        raise RuntimeError("В .env или окружении не задан ADMIN_ID (целое число Telegram user id).")
    try:
        admin_id = int(admin_raw)
    except ValueError as e:
        raise RuntimeError(
            "ADMIN_ID должен быть целым числом (Telegram user id), без пробелов и лишних символов."
        ) from e
    return AppConfig(
        telegram_bot_token=token,
        gemini_api_key=api_key,
        database_url=db_url,
        admin_id=admin_id,
    )
