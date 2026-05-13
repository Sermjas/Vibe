# 🧾 Receipt OCR Vibe-Bot

> **AI-ассистент для учёта финансов**. Telegram-бот распознаёт сумму и категорию по фото чеков через Google Gemini, сохраняет транзакции в SQLite и умеет выгружать отчёты (CSV/Excel).

---

## ✨ Основные возможности
- **Gemini OCR**: распознавание суммы и категории по фото.
- **Умная обработка лимитов**: `cooldown` ~10 минут при квотных 429 + ретраи с экспоненциальной задержкой.
- **Smart Compression**: сжатие изображений через `Pillow` в памяти.
- **Подтверждение перед записью**: транзакция сохраняется только после подтверждения пользователем.
- **Экспорт отчётов**: выгрузка транзакций пользователя в **CSV** и **Excel** (в памяти, без временных файлов).

## 🛠 Технологический стек
- **Core**: Python 3.10+
- **Framework**: `aiogram 3.x` (async)
- **AI**: `google-genai` (Gemini API)
- **DB**: `SQLAlchemy 2.0` + `aiosqlite` (SQLite, WAL)
- **Reports**: `pandas` + `openpyxl`
- **Settings**: `pydantic-settings` (чтение `.env` / `.env.local` через `env_file`)
- **Logging**: `loguru`

## 🏗 Архитектура проекта
```text
/project
  ├── vibe/                     # пакет приложения в корне репозитория
  │   ├── bot.py                # aiogram 3.x: интерфейс/хэндлеры, entrypoint
  │   ├── config.py             # pydantic-settings конфигурация
  │   ├── database.py           # SQLAlchemy async слой БД (SQLite WAL)
  │   ├── ocr_service.py        # Gemini OCR + предобработка
  │   ├── infra/                # disk_monitor, уведомления
  │   └── payments/             # YooKassa, подписка
  ├── docker-compose.yml        # запуск в Docker (volume /app/data)
  ├── Dockerfile                # multi-stage, non-root, PYTHONPATH=/app
  ├── pyproject.toml            # зависимости, entry points: vibe-bot, vibe-disk-monitor
  ├── requirements.txt          # зависимости для Docker build
  ├── tests/
  └── docs/context.md
```

## 💻 Локальный запуск (без Docker)

1. Создайте виртуальное окружение и установите проект в editable-режиме (пакет `vibe` в каталоге `vibe/`):

```bash
python -m venv .venv
# Windows: .\.venv\Scripts\activate
# Linux/macOS: source .venv/bin/activate
pip install -e .
```

2. Скопируйте `.env.example` в `.env` (и при необходимости `.env.local`) и задайте секреты.

3. Запуск бота — любой из вариантов:

```bash
vibe-bot
```

или

```bash
python -m vibe.bot
```

Команда должна выполняться **из корня репозитория** (чтобы находились `.env` / `.env.local`).

**Без `pip install -e .`:** задайте `PYTHONPATH=.` (текущий каталог — корень репозитория) и затем `python -m vibe.bot` (на Windows PowerShell: `$env:PYTHONPATH = "."`).

## ⚙️ Переменные окружения
См. `.env.example` (также есть `.env.local.example` и `.env.docker.example`).

Обязательные:
- `TELEGRAM_BOT_TOKEN`
- `GEMINI_API_KEY`
- `ADMIN_ID`

База данных:
- `DATABASE_URL` — полный SQLAlchemy URL (если задан, имеет приоритет над путём к файлу)
- `DATABASE_PATH` — путь к файлу SQLite (по умолчанию: `/app/data/bot.db`), если `DATABASE_URL` не задан

Логи:
- `LOG_LEVEL` (по умолчанию: `INFO`)
- `LOG_PATH` (по умолчанию: `/app/data/bot.log`)

## 🐳 Запуск в Docker
1) Создайте `.env.docker` на основе `.env.docker.example`
2) Запуск:

```bash
docker-compose up -d --build
```

Данные и база `bot.db` хранятся в volume, смонтированном в `/app/data`.

Сервис `disk_monitor` по умолчанию работает в **режиме демона** (проверка раз в `DISK_MONITOR_INTERVAL_SEC`, минимум 60 с). Для однократного запуска из cron: `command: ["python", "-m", "vibe.infra.disk_monitor", "--once"]`.

## 🚀 Деплой на VPS (GitHub Actions)
Workflow: `.github/workflows/deploy.yml` (триггер на push в `main`).

Secrets, которые нужны в GitHub:
- `VPS_HOST`, `VPS_USER`, `VPS_SSH_KEY`
- `VPS_PORT` (опционально)
- `VPS_PROJECT_PATH`

На VPS должны быть установлены Docker и docker-compose, а репозиторий уже должен быть клонирован в `VPS_PROJECT_PATH`.
