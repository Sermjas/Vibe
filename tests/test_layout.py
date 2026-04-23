from pathlib import Path


def test_src_layout_exists() -> None:
    assert Path("src/vibe").exists()

