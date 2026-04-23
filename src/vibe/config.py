"""Загрузка и валидация настроек приложения."""

from __future__ import annotations

from functools import lru_cache

from dotenv import load_dotenv
from pydantic import Field, ValidationError, computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict

# Поддерживаем .env и .env.local для локальной разработки и Docker.
load_dotenv(".env")
load_dotenv(".env.local", override=True)


class AppConfig(BaseSettings):
    """Конфигурация приложения из окружения и .env файлов."""

    model_config = SettingsConfigDict(
        env_file=(".env", ".env.local"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    telegram_bot_token: str = Field(min_length=1)
    gemini_api_key: str = Field(min_length=1)
    admin_id: int
    data_dir: str = "/app/data"
    database_url: str | None = None
    log_level: str = "INFO"
    log_path: str | None = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def resolved_database_url(self) -> str:
        """Возвращает итоговый DATABASE_URL с безопасным дефолтом для Docker."""
        if self.database_url and self.database_url.strip():
            return self.database_url.strip()
        return f"sqlite+aiosqlite:////{self.data_dir.strip('/')}/bot.db"

    @computed_field  # type: ignore[prop-decorator]
    @property
    def resolved_log_path(self) -> str:
        """Возвращает путь до файла логов в каталоге данных."""
        if self.log_path and self.log_path.strip():
            return self.log_path.strip()
        return f"{self.data_dir.rstrip('/')}/bot.log"


@lru_cache(maxsize=1)
def get_config() -> AppConfig:
    """Читает и валидирует конфигурацию из окружения."""
    try:
        return AppConfig()
    except ValidationError as error:
        raise RuntimeError(f"Ошибка конфигурации окружения: {error}") from error
