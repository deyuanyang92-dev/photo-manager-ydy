"""capture_project_dialog.py — Render ProjectDialog (new mode) to PNG.

Runs headless (QT_QPA_PLATFORM=offscreen).

Usage:
    QT_QPA_PLATFORM=offscreen python3 docs/shots/capture_project_dialog.py
Output:
    docs/shots/new_project_dialog.png
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = Path(__file__).resolve().parent.parent.parent   # photo-platform-ydy-v3/
sys.path.insert(0, str(_ROOT))

from PyQt6.QtWidgets import QApplication  # noqa: E402

from app.config.theme import build_theme_qss_file, load_fonts  # noqa: E402
from app.views.project_dialog import ProjectDialog  # noqa: E402


def main() -> None:
    app = QApplication.instance() or QApplication(sys.argv)
    load_fonts(app)
    qss = build_theme_qss_file()
    app.setStyleSheet(qss.read_text(encoding="utf-8"))

    dlg = ProjectDialog(mode="new", existing_projects=[])
    dlg.resize(560, 560)
    dlg.show()
    app.processEvents()

    out = Path(__file__).parent / "new_project_dialog.png"
    pixmap = dlg.grab()
    pixmap.save(str(out), "PNG")
    print(f"Saved → {out}")


if __name__ == "__main__":
    main()
