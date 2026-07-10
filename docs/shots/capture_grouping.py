"""Capture 分组工具 popup(4 空组, 复刻用户截图状态)。

Usage: QT_QPA_PLATFORM=offscreen python capture_grouping.py [out.png]
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

ROOT = Path("/mnt/n/claude/photo-platform-ydy-v3")
sys.path.insert(0, str(ROOT))

from PyQt6.QtWidgets import QApplication, QDialog, QVBoxLayout

from app.config.settings import AppSettings
from app.config.theme import (
    apply_default_font,
    apply_theme,
    load_fonts,
    set_typography,
)


def main() -> int:
    out = sys.argv[1] if len(sys.argv) > 1 else "grouping_before.png"
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

    from app.services.grouping_service import Group, SpecimenGrouping
    from app.widgets.grouping_panel import GroupingPanel

    panel = GroupingPanel(ctx)
    grouping = SpecimenGrouping(
        uid="DEMO-UID",
        groups=[Group(group_index=i, angle_label=f"结果{i+1}") for i in range(4)],
    )
    panel.load_grouping("DEMO-UID", grouping)

    dlg = QDialog()
    dlg.setObjectName("GroupingDialog")
    dlg.setWindowTitle("分组工具")
    lay = QVBoxLayout(dlg)
    lay.setContentsMargins(14, 14, 14, 14)
    header = getattr(panel, "_group_header_widget", None)
    if header is not None:
        header.setVisible(False)
    divider = getattr(panel, "_header_divider", None)
    if divider is not None:
        divider.setVisible(False)
    lay.addWidget(panel)
    dlg.resize(1100, 480)
    dlg.show()
    app.processEvents()
    pm = dlg.grab()
    pm.save(out)
    print(f"saved {out} {pm.width()}x{pm.height()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
