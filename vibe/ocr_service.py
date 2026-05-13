"""Распознавание суммы чека через Google Gemini (OCR), SDK google-genai."""

from __future__ import annotations

import asyncio
import io
import json
import random
import re
import time

from google import genai
from google.genai import errors as genai_errors
from google.genai import types
from loguru import logger

from vibe.config import get_config

# Промпт: эксперт по чекам, строго JSON (amount + category)
_GEMINI_PROMPT = (
    "Ты эксперт по финансовым чекам. На фото — чек. "
    "Твоя задача: определить итоговую сумму покупки и категорию расхода.\n"
    "\n"
    "Верни СТРОГО валидный JSON и ничего больше, без Markdown, без пояснений, без текста вокруг.\n"
    "Формат ответа:\n"
    '{"amount": 123.45, "category": "Категория"}\n'
    "\n"
    "Правила:\n"
    "- Сумма: ищи итог (ориентируйся на слова «Итого», «Итог», «Total», «К оплате»).\n"
    "- Если сумму определить нельзя, верни amount: null.\n"
    "- category должна быть одной из: Продукты, Рестораны, Транспорт, Одежда, Здоровье, Развлечения, Другое.\n"
    "- Если не уверен в категории, выбери «Другое».\n"
)

# Модель с поддержкой изображения (Gemini API)
_GEMINI_MODEL = "gemini-2.5-flash-lite"

# Таймаут запроса к API (секунды)
_GEMINI_TIMEOUT_SEC: float = 60.0

# Повтор при 429 (ResourceExhausted) / временных сбоях
# Экспоненциальный backoff + jitter (уменьшает синхронные повторы и снижает шанс новых 429)
_GEMINI_RETRY_MAX_ATTEMPTS: int = 3  # до 3 раз
_GEMINI_RETRY_BASE_SEC: float = 1.5
_GEMINI_RETRY_JITTER_SEC_MAX: float = 0.75
_QUOTA_COOLDOWN_SEC: float = 600.0  # 10 минут паузы при постоянной квотной ошибке

# Глобальная пауза для OCR после подтверждённого исчерпания квоты.
_quota_blocked_until_monotonic: float = 0.0

# Сигнал для bot.py: лимит Gemini (HTTP 429 / квота), пауза ~10 минут.
OCR_RATE_LIMIT_ERROR = "OCR_RATE_LIMIT_429"


def _compress_image_for_gemini(image_bytes: bytes) -> bytes:
    """
    Сжимает изображение для уменьшения нагрузки на Gemini (bytes / tokens).
    Возвращает сжатые байты (или исходные при ошибках/отсутствии Pillow).
    """
    try:
        from PIL import Image  # type: ignore
    except Exception:
        logger.warning("Pillow не установлен. Сжатие изображения пропущено.")
        return image_bytes
    max_dim = 1600
    quality = 70

    try:
        with Image.open(io.BytesIO(image_bytes)) as img:
            if img.mode in ("RGBA", "LA"):
                # Накладываем альфу на белый фон, чтобы сохранить читаемость цен.
                bg = Image.new("RGBA", img.size, (255, 255, 255, 255))
                bg.alpha_composite(img)
                img = bg.convert("RGB")
            else:
                img = img.convert("RGB")

            w, h = img.size
            max_side = max(w, h)
            if max_side > max_dim:
                scale = max_dim / float(max_side)
                new_w = max(1, int(w * scale))
                new_h = max(1, int(h * scale))
                img = img.resize((new_w, new_h), Image.Resampling.LANCZOS)

            out_buffer = io.BytesIO()
            img.save(out_buffer, format="JPEG", quality=quality, optimize=True)
            compressed = out_buffer.getvalue()

        logger.info(f"Сжатие изображения: {len(image_bytes)} -> {len(compressed)} байт")
        return compressed
    except Exception:
        logger.exception("Не удалось сжать изображение; используем исходное.")
        return image_bytes


def _is_permanent_quota_error(error_message: str) -> bool:
    """
    Определяет сценарии 429, которые не решаются ретраем:
    - лимит квоты = 0;
    - требуется тариф/биллинг;
    - явное "quota exceeded".
    """
    msg = (error_message or "").lower()
    patterns = (
        "quota exceeded",
        "limit: 0",
        "billing details",
        "check your plan",
        "free_tier_requests",
        "free_tier_input_token_count",
    )
    return any(p in msg for p in patterns)


def _is_daily_quota_exhausted(error_message: str) -> bool:
    """Проверяет квотное истощение с признаком `limit: 0`."""
    msg = (error_message or "").lower()
    return "limit: 0" in msg


def _sync_generate_raw_text(image_bytes: bytes, api_key: str) -> str | None:
    """Синхронный вызов Gemini через Client; выполняется в пуле потоков."""
    global _quota_blocked_until_monotonic

    now = time.monotonic()
    if now < _quota_blocked_until_monotonic:
        wait_left = _quota_blocked_until_monotonic - now
        logger.warning(
            "OCR временно приостановлен из-за исчерпанной квоты Gemini. "
            f"Осталось ждать примерно {wait_left:.0f} с."
        )
        return OCR_RATE_LIMIT_ERROR

    contents = [
        _GEMINI_PROMPT,
        types.Part.from_bytes(data=image_bytes, mime_type="image/jpeg"),
    ]

    for attempt in range(_GEMINI_RETRY_MAX_ATTEMPTS):
        try:
            with genai.Client(api_key=api_key) as client:
                response = client.models.generate_content(
                    model=_GEMINI_MODEL,
                    contents=contents,
                    # Явно отключаем AFC, чтобы не тратить квоту на автодействия.
                    config=types.GenerateContentConfig(
                        automatic_function_calling=types.AutomaticFunctionCallingConfig(
                            disable=True
                        )
                    ),
                )
        except genai_errors.ClientError as e:
            # 404 чаще всего означает "модель/ресурс недоступен" (например, несуществующая модель).
            # Это не решается ретраями, поэтому сразу выходим.
            if e.code == 404:
                logger.error(
                    f"Gemini вернул 404 (модель/ресурс недоступен): {e.message or e}"
                )
                return None

            # 429 Too Many Requests — ResourceExhausted (лимит квоты/частоты)
            if e.code == 429:
                message = str(e.message or e)

                # Дневной лимит: сразу возвращаем сообщение без ретраев.
                if _is_daily_quota_exhausted(message):
                    _quota_blocked_until_monotonic = time.monotonic() + _QUOTA_COOLDOWN_SEC
                    logger.error(
                        f"Истощен дневной лимит Gemini API (HTTP 429): {message}. "
                        "Повторы отключены."
                    )
                    return OCR_RATE_LIMIT_ERROR

                # Постоянная квотная ошибка: отключаем ретраи на время cooldown.
                if _is_permanent_quota_error(message):
                    _quota_blocked_until_monotonic = time.monotonic() + _QUOTA_COOLDOWN_SEC
                    logger.error(
                        "Постоянная квотная ошибка Gemini (HTTP 429): "
                        f"{message}. Повторы отключены, OCR приостановлен "
                        f"на {_QUOTA_COOLDOWN_SEC:.0f} с."
                    )
                    return OCR_RATE_LIMIT_ERROR

                # Временная 429: делаем backoff + jitter.
                if attempt < _GEMINI_RETRY_MAX_ATTEMPTS - 1:
                    wait = _GEMINI_RETRY_BASE_SEC * (2**attempt)
                    jitter = random.uniform(0.0, _GEMINI_RETRY_JITTER_SEC_MAX)
                    wait_total = wait + jitter
                    logger.warning(
                        "Gemini запрос отклонен (HTTP 429 / ResourceExhausted). "
                        f"Пауза перед повтором: {wait_total:.1f} с "
                        f"(попытка {attempt + 2}/{_GEMINI_RETRY_MAX_ATTEMPTS})."
                    )
                    time.sleep(wait_total)
                    continue

                logger.warning(
                    f"Исчерпаны повторы после HTTP 429 (ResourceExhausted), "
                    f"пауза ~{_QUOTA_COOLDOWN_SEC:.0f} с."
                )
                return OCR_RATE_LIMIT_ERROR

            logger.error(f"Ошибка клиента Gemini API: HTTP {e.code}. {e.message or e}")
            return None

        except genai_errors.ServerError as e:
            if e.code in (500, 502, 503) and attempt < _GEMINI_RETRY_MAX_ATTEMPTS - 1:
                wait = _GEMINI_RETRY_BASE_SEC * (2**attempt)
                logger.warning(
                    f"Временная ошибка сервера Gemini (HTTP {e.code}), "
                    f"попытка {attempt + 1}/{_GEMINI_RETRY_MAX_ATTEMPTS}, "
                    f"пауза {wait:.1f} с."
                )
                time.sleep(wait)
                continue
            logger.error(f"Ошибка сервера Gemini API: HTTP {e.code}. {e.message or e}")
            return None

        except Exception:
            logger.exception("Ошибка при обращении к Gemini API.")
            return None

        if not response.candidates:
            logger.warning("Пустой ответ Gemini (нет candidates).")
            return None
        text = (response.text or "").strip()
        if not text:
            logger.warning("Пустой текст ответа Gemini.")
            return None
        return text

    return None


def _clean_amount_response(raw: str) -> str:
    """
    Убирает из ответа модели валюту, пробелы и прочие символы — остаётся строка для числа.
    """
    t = raw.strip()
    if not t:
        return ""
    # Слова «none» и похожий мусор
    if t.lower() in ("none", "null", "н/д", "n/a"):
        return ""
    # Все пробельные символы (включая неразрывный и узкий)
    t = re.sub(r"[\s\u00a0\u202f\u2009]+", "", t)
    # Распространённые обозначения валют (слова и символы)
    t = re.sub(
        r"(?i)(руб\.?|рублей|рубля|р\.?|rub|rubs?|usd|eur|uah|kzt|byn|gbp)",
        "",
        t,
    )
    t = re.sub(r"[₽$€£¥₸₴]", "", t)
    # Оставляем только цифры, знак минуса и разделители дробной части
    t = re.sub(r"[^\d,.\-+]", "", t)
    return t


def _parse_amount(raw: str) -> float | None:
    """
    Извлекает число из ответа модели даже при добавленном тексте/валюте.
    При отсутствии суммы возвращает None.
    """
    t = (raw or "").strip()
    if not t:
        return None

    # Если модель явно ответила None и цифр при этом нет — считаем, что суммы нет.
    if re.search(r"\b(none|null|н/д|n/a)\b", t, flags=re.IGNORECASE) and not re.search(
        r"\d", t
    ):
        return None

    compact = _clean_amount_response(t)
    if not compact:
        return None

    # Извлекаем кандидат на число регуляркой (на входе уже только цифры/разделители).
    match = re.search(r"[-+]?\d[\d.,]*", compact)
    if not match:
        logger.warning(f"Не удалось извлечь число из ответа: {raw!r}")
        return None
    candidate = match.group(0)

    # Нормализуем десятичный разделитель: последняя из '.'/',' считается десятичной.
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
        logger.warning(f"Некорректное число после парсинга: {raw!r}")
        return None


def _extract_json_object(raw: str) -> str | None:
    """Пытается вытащить JSON-объект из ответа модели (на случай лишнего текста)."""
    t = (raw or "").strip()
    if not t:
        return None
    # Убираем популярные обёртки ```json ... ```
    t = re.sub(r"^\s*```(?:json)?\s*", "", t, flags=re.IGNORECASE)
    t = re.sub(r"\s*```\s*$", "", t)

    # Если уже похоже на объект — пробуем как есть.
    if t.startswith("{") and t.endswith("}"):
        return t

    # Иначе ищем первый {...} блок.
    start = t.find("{")
    end = t.rfind("}")
    if start != -1 and end != -1 and end > start:
        return t[start : end + 1]
    return None


def _normalize_category(value: str | None) -> str:
    """Нормализует категорию в одну из допустимых."""
    allowed = {
        "Продукты",
        "Рестораны",
        "Транспорт",
        "Одежда",
        "Здоровье",
        "Развлечения",
        "Другое",
    }
    if not value:
        return "Другое"
    v = value.strip()
    if v in allowed:
        return v
    # Небольшая попытка сопоставления по нижнему регистру.
    lower_map = {a.lower(): a for a in allowed}
    return lower_map.get(v.lower(), "Другое")


def _parse_ocr_json(raw: str) -> dict | None:
    """Парсит JSON-ответ Gemini в формате {amount, category}."""
    obj_text = _extract_json_object(raw)
    if obj_text is None:
        logger.warning(f"Не удалось выделить JSON из ответа Gemini: {raw!r}")
        return None
    try:
        data = json.loads(obj_text)
    except Exception:
        logger.warning(f"Ответ Gemini не является валидным JSON: {raw!r}")
        return None

    if not isinstance(data, dict):
        return None

    amount_raw = data.get("amount", None)
    amount: float | None
    if amount_raw is None:
        amount = None
    elif isinstance(amount_raw, (int, float)):
        amount = float(amount_raw)
    elif isinstance(amount_raw, str):
        # На всякий случай: если модель вернула строку, пробуем вытащить число старым парсером.
        amount = _parse_amount(amount_raw)
    else:
        amount = None

    category = _normalize_category(
        data.get("category") if isinstance(data.get("category"), str) else None
    )

    # raw_data сохраняем максимально близким к ответу модели, но с нормализованной категорией.
    return {"amount": amount, "category": category}


async def get_amount_from_checkpoint(image_bytes: bytes) -> dict | str | None:
    """
    Асинхронно извлекает данные чека (amount + category) с изображения.
    Возвращает:
    - dict: {"amount": float|None, "category": str}
    - str: константа OCR_RATE_LIMIT_ERROR при лимите Gemini (HTTP 429 / квота)
    - None: если распознать не удалось (ошибка/пустой ответ)
    """
    if not image_bytes:
        logger.error("Пустые байты изображения для OCR.")
        return None

    cfg = get_config()
    compressed_bytes = await asyncio.to_thread(_compress_image_for_gemini, image_bytes)
    try:
        raw = await asyncio.wait_for(
            asyncio.to_thread(_sync_generate_raw_text, compressed_bytes, cfg.gemini_api_key),
            timeout=_GEMINI_TIMEOUT_SEC,
        )
    except asyncio.TimeoutError:
        logger.warning(f"Таймаут Gemini ({_GEMINI_TIMEOUT_SEC} с) при обработке фото.")
        return None
    except Exception:
        logger.exception("Неожиданная ошибка при вызове OCR.")
        return None

    if raw is None:
        return None
    if raw == OCR_RATE_LIMIT_ERROR:
        return raw
    parsed = _parse_ocr_json(raw)
    if parsed is not None:
        return parsed

    # Фоллбек: если JSON не распарсился, попробуем хотя бы сумму по старой логике.
    amount = _parse_amount(raw)
    if amount is None:
        return None
    return {"amount": amount, "category": "Другое"}
