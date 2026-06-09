"""Tests for screenshot_service path helpers (no Qt / no display needed)."""
from datetime import datetime
from pathlib import Path

from app.services.screenshot_service import (
    default_screenshot_name,
    default_screenshot_path,
    screenshot_dir,
)


def test_screenshot_dir_under_results():
    d = screenshot_dir("/proj/福建样地")
    assert d == Path("/proj/福建样地/results/screenshots")


def test_default_name_is_timestamped_png():
    dt = datetime(2026, 6, 9, 14, 30, 5)
    assert default_screenshot_name(dt) == "截图-20260609-143005.png"


def test_default_path_joins_dir_and_name():
    dt = datetime(2026, 6, 9, 14, 30, 5)
    p = default_screenshot_path("/proj/x", dt)
    assert p == Path("/proj/x/results/screenshots/截图-20260609-143005.png")
