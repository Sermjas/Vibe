"""Совместимый экспорт OCR-сервиса.

Основной код расположен в `src/vibe/`.
"""

from __future__ import annotations

import sys
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parent / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from vibe.ocr_service import OCR_RATE_LIMIT_ERROR, get_amount_from_checkpoint

__all__ = ["OCR_RATE_LIMIT_ERROR", "get_amount_from_checkpoint"]

