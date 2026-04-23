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
- **Settings**: `pydantic-settings` (+ `python-dotenv` для `.env`)
- **Logging**: `loguru`

## 🏗 Архитектура проекта
```text
/project
  ├── src/vibe/                 # Основной пакет приложения
  │   ├── bot.py                # aiogram 3.x: интерфейс/хэндлеры, entrypoint
  │   ├── config.py             # pydantic-settings конфигурация
  │   ├── database.py           # SQLAlchemy async слой БД (SQLite WAL)
  │   └── ocr_service.py        # Gemini OCR + предобработка
  ├── bot.py                    # совместимая обёртка (реэкспорт из src/vibe)
  ├── config.py                 # совместимая обёртка
  ├── database.py               # совместимая обёртка
  ├── ocr_service.py            # совместимая обёртка
  ├── docker-compose.yml         # запуск в Docker (volume /app/data)
  ├── Dockerfile                # multi-stage, non-root, PYTHONPATH=/app/src
  ├── pyproject.toml            # зависимости + ruff
  ├── requirements.txt          # зависимости для Docker build
  ├── tests/                    # тесты (каркас)
  └── docs/context.md           # контекст и решения проекта
```

## ⚙️ Переменные окружения
См. `.env.example` (также есть `.env.local.example` и `.env.docker.example`).

Обязательные:
- `TELEGRAM_BOT_TOKEN`
- `GEMINI_API_KEY`
- `ADMIN_ID`

База данных:
- `DATABASE_PATH` — путь к файлу SQLite (по умолчанию: `/app/data/bot.db`)

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

## 🚀 Деплой на VPS (GitHub Actions)
Workflow: `.github/workflows/deploy.yml` (триггер на push в `main`).

Secrets, которые нужны в GitHub:
- `VPS_HOST`, `VPS_USER`, `VPS_SSH_KEY`
- `VPS_PORT` (опционально)
- `VPS_PROJECT_PATH`

На VPS должны быть установлены Docker и docker-compose, а репозиторий уже должен быть клонирован в `VPS_PROJECT_PATH`.
