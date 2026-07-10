"""Capture the collaboration view with the same CJK font setup as main.py.

Usage:
    QT_QPA_PLATFORM=offscreen python docs/shots/capture_collab.py
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from PyQt6.QtWidgets import QApplication

from app.config.settings import AppSettings
from app.config.theme import (
    apply_default_font,
    apply_theme,
    load_fonts,
    set_typography,
)
from app.services.collab_service import CollabService
from app.views.collab_view import CollabView


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)

    load_fonts(app)
    settings = AppSettings()
    set_typography(scale=settings.ui_font_scale, family=settings.ui_font_family)
    apply_default_font(app)
    app.setStyleSheet(apply_theme(settings.current_theme))

    ctx = MagicMock()
    ctx.settings = settings
    ctx.current_project_dir = ""
    ctx.get_db.return_value = None

    svc = CollabService()
    svc._running = True
    svc.set_group_code("TEAM-39H-QNN")
    ctx.collab_service = svc

    view = CollabView(ctx)
    view.resize(2048, 1000)
    view.on_activate()
    view.show()
    app.processEvents()

    out = Path(__file__).with_name("collab_view_check.png")
    view.grab().save(str(out))
    svc.stop()
    print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
