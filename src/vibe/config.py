"""Загрузка и валидация настроек приложения."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path, PurePosixPath

from dotenv import load_dotenv
from pydantic import Field, ValidationError, computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict

# Поддерживаем .env и .env.local для локальной разработки и Docker.
load_dotenv(".env")
load_dotenv(".env.local", override=True)


def _sqlite_aiosqlite_url(path: str) -> str:
    """Строит корректный sqlite+aiosqlite URL из пути к файлу.

    Поддерживает Windows-пути (например, D:\\data\\bot.db) и POSIX-пути.
    """
    raw = (path or "/app/data/bot.db").strip()
    if not raw:
        raw = "/app/data/bot.db"

    # Если уже передали полноценный SQLAlchemy URL, не трогаем.
    if "://" in raw:
        return raw

    # Приводим к POSIX-виду (D:\\x\\y.db -> D:/x/y.db).
    url_path = Path(raw).as_posix()
    if not url_path.startswith("/"):
        url_path = f"/{url_path}"

    # SQLAlchemy ожидает абсолютный URL формата sqlite+aiosqlite:////abs/path.db
    # Для Windows это выглядит как: sqlite+aiosqlite:////D:/path/to/file.db
    return f"sqlite+aiosqlite:////{url_path.lstrip('/')}"


def _default_log_path(database_path: str) -> str:
    db_raw = (database_path or "/app/data/bot.db").strip() or "/app/data/bot.db"
    if "://" in db_raw:
        return "/app/data/bot.log"

    parent = Path(db_raw).parent
    if str(parent) in {".", ""}:
        return "/app/data/bot.log"

    return str(PurePosixPath(parent.as_posix()) / "bot.log")


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
    # Путь к SQLite файлу в volume Docker. По умолчанию: /app/data/bot.db
    database_path: str = Field(default="/app/data/bot.db", validation_alias="DATABASE_PATH")
    database_url: str | None = None  # оставляем для обратной совместимости
    log_level: str = "INFO"
    log_path: str | None = None

    # --- Инфраструктура / мониторинг диска Docker-хоста ---
    disk_monitor_path: str = Field(default="/", validation_alias="DISK_MONITOR_PATH")
    disk_warn_percent: float = Field(default=20.0, validation_alias="DISK_WARN_PERCENT")
    disk_critical_percent: float = Field(default=10.0, validation_alias="DISK_CRITICAL_PERCENT")
    disk_monitor_enable_prune: bool = Field(default=True, validation_alias="DISK_MONITOR_ENABLE_PRUNE")
    disk_monitor_prune_volumes: bool = Field(default=True, validation_alias="DISK_MONITOR_PRUNE_VOLUMES")
    disk_monitor_log_path: str = Field(default="/app/data/disk_monitor.log", validation_alias="DISK_MONITOR_LOG_PATH")
    disk_monitor_state_file: str = Field(
        default="/app/data/disk_monitor_state.json",
        validation_alias="DISK_MONITOR_STATE_FILE",
    )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def resolved_database_url(self) -> str:
        """Возвращает итоговый SQLAlchemy URL.

        Приоритет:
        1) DATABASE_URL (если задан) — для внешних БД
        2) DATABASE_PATH (SQLite файл) — основной путь для Docker volume
        """
        if self.database_url and self.database_url.strip():
            return self.database_url.strip()
        return _sqlite_aiosqlite_url(self.database_path)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def resolved_log_path(self) -> str:
        """Возвращает путь до файла логов в каталоге данных."""
        if self.log_path and self.log_path.strip():
            return self.log_path.strip()
        # Логи по умолчанию рядом с базой (в каталоге volume).
        return _default_log_path(self.database_path)


@lru_cache(maxsize=1)
def get_config() -> AppConfig:
    """Читает и валидирует конфигурацию из окружения."""
    try:
        return AppConfig()
    except ValidationError as error:
        raise RuntimeError(f"Ошибка конфигурации окружения: {error}") from error
