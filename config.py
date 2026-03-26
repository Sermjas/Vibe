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


def get_config() -> AppConfig:
    """Читает и валидирует конфигурацию из окружения."""
    token = (os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
    api_key = (os.getenv("GEMINI_API_KEY") or "").strip()
    if not token:
        raise RuntimeError("В .env или окружении не задан TELEGRAM_BOT_TOKEN.")
    if not api_key:
        raise RuntimeError("В .env или окружении не задан GEMINI_API_KEY.")
    return AppConfig(telegram_bot_token=token, gemini_api_key=api_key)
