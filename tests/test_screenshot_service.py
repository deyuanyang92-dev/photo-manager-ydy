"""Tests for screenshot_service path helpers (no Qt / no display needed)."""
from datetime import datetime
import os
from pathlib import Path

from app.services.screenshot_service import (
    default_screenshot_name,
    default_screenshot_path,
    recent_screenshots,
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


def test_recent_screenshots_returns_png_newest_first(tmp_path):
    shots = tmp_path / "results" / "screenshots"
    shots.mkdir(parents=True)
    old = shots / "截图-20260609-100000.png"
    new = shots / "截图-20260609-110000.png"
    ignored = shots / "notes.txt"
    old.write_bytes(b"old")
    new.write_bytes(b"new")
    ignored.write_text("x")
    os.utime(old, (100, 100))
    os.utime(new, (200, 200))

    result = recent_screenshots(tmp_path, limit=10)
    assert result == [new, old]


def test_recent_screenshots_missing_dir_is_empty(tmp_path):
    assert recent_screenshots(tmp_path) == []


def test_recent_screenshots_honors_limit(tmp_path):
    shots = tmp_path / "results" / "screenshots"
    shots.mkdir(parents=True)
    for i in range(3):
        p = shots / f"截图-20260609-10000{i}.png"
        p.write_bytes(b"x")
        os.utime(p, (100 + i, 100 + i))

    assert len(recent_screenshots(tmp_path, limit=2)) == 2
