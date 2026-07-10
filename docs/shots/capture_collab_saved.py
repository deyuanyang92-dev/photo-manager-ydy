"""Capture collab view after inline team save (post-save layout check).

Usage:
    QT_QPA_PLATFORM=offscreen python docs/shots/capture_collab_saved.py
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
    svc.set_group_code("TEAM-ASN-31A")
    ctx.collab_service = svc
    ctx.ensure_collab_service = lambda: svc

    view = CollabView(ctx)
    view.resize(1280, 900)
    view.show()
    app.processEvents()

    view._team_code_edit.setText("TEAM-ASN-31A")
    view._team_operator_edit.setText("测试员")
    view._save_team_setup_inline()
    app.processEvents()

    assert not view._team_post_save_frame.isHidden(), "post-save block should show"
    assert view._team_setup_status.isHidden()
    assert "等待队友" in view._connection_result_title.text()
    assert view._method_stack.currentWidget() is view._team_method_detail

    app.processEvents()

    out = Path(__file__).with_name("collab_view_saved_check.png")
    view.grab().save(str(out))
    svc.stop()
    print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
