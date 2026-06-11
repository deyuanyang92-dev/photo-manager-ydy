"""screenshot_service.py — Qt-free path helpers for the screenshot feature.

The screenshot capture/overlay logic lives in
``app/widgets/screenshot_overlay.py`` (needs Qt); this module only owns the
"where does a saved screenshot go" decision so it can be unit-tested without
a display.

Screenshots auto-save into ``<project>/results/screenshots/`` (``results`` is
already a reserved workspace dir — see ``project_tree_service.RESERVED_DIR_NAMES``).
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

_SUBDIR = "screenshots"


def screenshot_dir(project_dir: str | Path) -> Path:
    """Return the directory screenshots are saved under for *project_dir*.

    Does not create the directory — callers create it on save.
    """
    return Path(project_dir) / "results" / _SUBDIR


def default_screenshot_name(dt: datetime) -> str:
    """Timestamped PNG filename, e.g. ``截图-20260609-143005.png``."""
    return f"截图-{dt:%Y%m%d-%H%M%S}.png"


def default_screenshot_path(project_dir: str | Path, dt: datetime) -> Path:
    """Full target path for a screenshot auto-saved into *project_dir*."""
    return screenshot_dir(project_dir) / default_screenshot_name(dt)


def recent_screenshots(project_dir: str | Path, limit: int = 24) -> list[Path]:
    """Return recent PNG screenshots for *project_dir*, newest first."""
    if limit <= 0:
        return []
    directory = screenshot_dir(project_dir)
    if not directory.exists():
        return []
    shots = [p for p in directory.iterdir() if p.is_file() and p.suffix.lower() == ".png"]
    shots.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return shots[:limit]
