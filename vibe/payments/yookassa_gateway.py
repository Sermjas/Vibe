"""Абстракция YooKassa: боевой API и mock-режим для тестов."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Protocol
from uuid import uuid4

from vibe.config import AppConfig
from vibe.payments.yookassa_api import (
    YooKassaApiError,
    YooKassaCredentials,
    check_payment as api_check_payment,
    create_payment as api_create_payment,
)


class PaymentGateway(Protocol):
    async def create_payment(
        self,
        amount: Decimal,
        telegram_id: int,
        *,
        return_url: str,
        description: str,
    ) -> tuple[str, str]:
        """Возвращает (payment_url, payment_id)."""
        ...

    async def check_payment(self, payment_id: str) -> dict[str, Any]:
        ...


@dataclass
class RealPaymentGateway:
    credentials: YooKassaCredentials

    async def create_payment(
        self,
        amount: Decimal,
        telegram_id: int,
        *,
        return_url: str,
        description: str,
    ) -> tuple[str, str]:
        return await api_create_payment(
            amount,
            telegram_id,
            credentials=self.credentials,
            return_url=return_url,
            description=description,
        )

    async def check_payment(self, payment_id: str) -> dict[str, Any]:
        return await api_check_payment(payment_id, credentials=self.credentials)


@dataclass
class MockPaymentGateway:
    """In-memory mock без HTTP; для тестов и YOOKASSA_MOCK_MODE=true."""

    _store: dict[str, dict[str, Any]] = field(default_factory=dict)

    async def create_payment(
        self,
        amount: Decimal,
        telegram_id: int,
        *,
        return_url: str,
        description: str,
    ) -> tuple[str, str]:
        payment_id = f"mock-{uuid4().hex}"
        payment_url = f"https://mock.yookassa.test/pay/{payment_id}"
        self._store[payment_id] = {
            "id": payment_id,
            "status": "pending",
            "amount": {"value": str(amount), "currency": "RUB"},
            "metadata": {"telegram_id": str(telegram_id)},
            "confirmation": {"type": "redirect", "confirmation_url": payment_url},
            "description": description,
            "return_url": return_url,
        }
        return payment_url, payment_id

    async def check_payment(self, payment_id: str) -> dict[str, Any]:
        record = self._store.get(payment_id)
        if record is None:
            raise YooKassaApiError(f"Mock payment not found: {payment_id}")
        return dict(record)

    def set_payment_status(self, payment_id: str, status: str) -> None:
        if payment_id not in self._store:
            raise KeyError(payment_id)
        self._store[payment_id]["status"] = status

    def clear(self) -> None:
        self._store.clear()


_mock_gateway: MockPaymentGateway | None = None


def get_mock_gateway() -> MockPaymentGateway:
    global _mock_gateway
    if _mock_gateway is None:
        _mock_gateway = MockPaymentGateway()
    return _mock_gateway


def reset_mock_gateway() -> None:
    global _mock_gateway
    if _mock_gateway is not None:
        _mock_gateway.clear()
    _mock_gateway = None


def _credentials_from_config(cfg: AppConfig) -> YooKassaCredentials | None:
    shop_id = (cfg.yookassa_shop_id or "").strip()
    secret = (cfg.yookassa_secret_key or "").strip()
    if not shop_id or not secret:
        return None
    return YooKassaCredentials(shop_id=shop_id, secret_key=secret)


def get_payment_gateway(cfg: AppConfig) -> PaymentGateway:
    if cfg.yookassa_mock_mode:
        return get_mock_gateway()
    creds = _credentials_from_config(cfg)
    if creds is None:
        raise RuntimeError("YooKassa не настроена: задайте YOOKASSA_SHOP_ID и YOOKASSA_SECRET_KEY")
    return RealPaymentGateway(credentials=creds)


def credentials_configured(cfg: AppConfig) -> bool:
    return cfg.yookassa_mock_mode or _credentials_from_config(cfg) is not None
