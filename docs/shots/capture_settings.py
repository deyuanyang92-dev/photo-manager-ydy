"""capture_settings.py — Render SettingsView to PNG for design review.

Runs fully headless (QT_QPA_PLATFORM=offscreen), themed deep-teal, 1920x1080.

Usage:
    QT_QPA_PLATFORM=offscreen python3 docs/shots/capture_settings.py

Output:
    docs/shots/page_settings.png
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_ROOT))

from PyQt6.QtWidgets import QApplication  # noqa: E402

from app.config.theme import build_theme_qss_file, load_fonts  # noqa: E402
from app.app_context import AppContext                           # noqa: E402
from app.views.settings_view import SettingsView                # noqa: E402


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    load_fonts(app)
    qss = build_theme_qss_file()
    app.setStyleSheet(qss.read_text(encoding="utf-8"))

    ctx = MagicMock(spec=AppContext)
    ctx.has_project = False
    ctx.current_project_dir = "/mnt/n/projects/FJ-XM-polychaeta-2026"
    ctx.settings = MagicMock()
    # Provide a real QSettings backed by a temp ini so _load_all() works
    from PyQt6.QtCore import QSettings
    qs = QSettings()  # in-memory / default scope
    ctx.settings._qs = qs
    ctx.settings.sync = lambda: qs.sync()

    view = SettingsView(ctx)
    view.resize(1920, 1080)
    view.show()
    view.on_activate()

    for _ in range(6):
        app.processEvents()

    # Switch to Helicon tab (index 1) for the screenshot
    view._tabs.setCurrentIndex(1)
    for _ in range(4):
        app.processEvents()

    out = Path(__file__).resolve().parent / "page_settings.png"
    pix = view.grab()
    pix.save(str(out))
    size = out.stat().st_size if out.exists() else 0
    print(f"saved: {out}  ({pix.width()}x{pix.height()}, {size} bytes)")
    return 0 if size > 5000 else 1


if __name__ == "__main__":
    sys.exit(main())
