"""Совместимый entrypoint.

Основной код расположен в `src/vibe/`.
"""

from __future__ import annotations

import sys
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parent / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from vibe.bot import main, run

__all__ = ["main", "run"]


if __name__ == "__main__":
    run()

