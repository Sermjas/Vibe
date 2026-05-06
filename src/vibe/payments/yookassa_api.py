"""Минимальная интеграция с API YooKassa.

Документация: https://yookassa.ru/developers/api
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from decimal import Decimal
from typing import Any
from uuid import uuid4

import aiohttp


@dataclass(frozen=True)
class YooKassaCredentials:
    shop_id: str
    secret_key: str


def _basic_auth_header(shop_id: str, secret_key: str) -> str:
    token = base64.b64encode(f"{shop_id}:{secret_key}".encode("utf-8")).decode("ascii")
    return f"Basic {token}"


def _amount_value(amount: Decimal | int | float | str) -> str:
    value = Decimal(str(amount)).quantize(Decimal("0.01"))
    return format(value, "f")


class YooKassaApiError(RuntimeError):
    pass


async def create_payment(
    amount: Decimal | int | float | str,
    user_id: int,
    *,
    credentials: YooKassaCredentials,
    return_url: str = "https://t.me",
    description: str | None = None,
    timeout_s: float = 20.0,
) -> tuple[str, str]:
    """Создаёт платёж в YooKassa.

    Возвращает (payment_url, payment_id).
    """
    url = "https://api.yookassa.ru/v3/payments"
    payload: dict[str, Any] = {
        "amount": {"value": _amount_value(amount), "currency": "RUB"},
        "confirmation": {"type": "redirect", "return_url": return_url},
        "capture": True,
        "description": description or f"Подписка на 30 дней (telegram_id={user_id})",
        "metadata": {"telegram_id": str(user_id)},
    }

    headers = {
        "Authorization": _basic_auth_header(credentials.shop_id, credentials.secret_key),
        "Idempotence-Key": str(uuid4()),
        "Content-Type": "application/json",
    }

    timeout = aiohttp.ClientTimeout(total=timeout_s)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(url, headers=headers, json=payload) as resp:
            text = await resp.text()
            if resp.status >= 400:
                raise YooKassaApiError(f"YooKassa create_payment failed ({resp.status}): {text}")
            try:
                data = json.loads(text)
            except Exception as exc:  # noqa: BLE001
                raise YooKassaApiError(f"YooKassa create_payment: invalid JSON: {text}") from exc

    payment_id = str(data.get("id") or "")
    confirmation = data.get("confirmation") if isinstance(data, dict) else None
    payment_url = ""
    if isinstance(confirmation, dict):
        payment_url = str(confirmation.get("confirmation_url") or "")

    if not payment_id or not payment_url:
        raise YooKassaApiError(f"YooKassa create_payment: missing id/url in response: {data!r}")

    return payment_url, payment_id


async def check_payment(
    payment_id: str,
    *,
    credentials: YooKassaCredentials,
    timeout_s: float = 20.0,
) -> dict[str, Any]:
    """Проверяет платёж и возвращает сырой ответ YooKassa (JSON dict)."""
    pid = (payment_id or "").strip()
    if not pid:
        raise ValueError("payment_id is required")

    url = f"https://api.yookassa.ru/v3/payments/{pid}"
    headers = {
        "Authorization": _basic_auth_header(credentials.shop_id, credentials.secret_key),
        "Content-Type": "application/json",
    }

    timeout = aiohttp.ClientTimeout(total=timeout_s)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(url, headers=headers) as resp:
            text = await resp.text()
            if resp.status >= 400:
                raise YooKassaApiError(f"YooKassa check_payment failed ({resp.status}): {text}")
            try:
                return json.loads(text)
            except Exception as exc:  # noqa: BLE001
                raise YooKassaApiError(f"YooKassa check_payment: invalid JSON: {text}") from exc

