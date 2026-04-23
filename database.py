"""Совместимый экспорт слоя БД.

Основной код расположен в `src/vibe/`.
"""

from __future__ import annotations

import sys
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parent / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from vibe.database import Database, Transaction, User, UserUpsertResult

__all__ = ["Database", "Transaction", "User", "UserUpsertResult"]

