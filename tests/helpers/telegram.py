"""Фабрики объектов aiogram для тестов."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from aiogram.types import (
    CallbackQuery,
    Chat,
    Message,
    PhotoSize,
    Update,
    User as TgUser,
)


def make_tg_user(user_id: int = 1001, username: str = "tester") -> TgUser:
    return TgUser(
        id=user_id,
        is_bot=False,
        first_name="Test",
        username=username,
    )


def make_message(
    text: str | None = None,
    *,
    user_id: int = 1001,
    with_photo: bool = False,
) -> Message:
    user = make_tg_user(user_id)
    chat = Chat(id=user_id, type="private")
    photo = (
        [PhotoSize(file_id="fid", file_unique_id="uniq", width=100, height=100)]
        if with_photo
        else None
    )
    return Message(
        message_id=1,
        date=datetime.now(timezone.utc),
        chat=chat,
        from_user=user,
        text=text,
        photo=photo,
    )


def make_callback(
    data: str,
    *,
    user_id: int = 1001,
) -> CallbackQuery:
    user = make_tg_user(user_id)
    chat = Chat(id=user_id, type="private")
    message = Message(
        message_id=2,
        date=datetime.now(timezone.utc),
        chat=chat,
        from_user=user,
        text="",
    )
    return CallbackQuery(
        id="cb1",
        from_user=user,
        chat_instance="ci",
        message=message,
        data=data,
    )


def make_update_message(message: Message) -> Update:
    return Update(update_id=1, message=message)


def make_update_callback(callback: CallbackQuery) -> Update:
    return Update(update_id=2, callback_query=callback)


def last_answer_text(captured: list[dict[str, Any]]) -> str:
    assert captured, "expected at least one answer()"
    return str(captured[-1]["text"])
