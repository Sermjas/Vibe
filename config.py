"""Совместимый экспорт конфигурации.

Основной код расположен в `src/vibe/`.
"""

from __future__ import annotations

import sys
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parent / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from vibe.config import AppConfig, get_config

__all__ = ["AppConfig", "get_config"]

