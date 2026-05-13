from pathlib import Path


def test_package_layout_exists() -> None:
    assert Path("vibe").exists()
    assert Path("vibe/bot.py").exists()

