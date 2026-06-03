"""标本照片工作台 — 桌面版入口。

Usage:
    python main.py                 # normal launch (requires display)
    QT_QPA_PLATFORM=offscreen python main.py   # headless CI check
"""
import sys
from pathlib import Path

from PyQt6.QtWidgets import QApplication
from PyQt6.QtGui import QFontDatabase

from app.app_context import AppContext
from app.config.theme import build_theme_qss_file
from app.main_window import MainWindow
from app.views.registry import ALL_VIEWS


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("标本照片工作台")
    app.setOrganizationName("SpecimenPhotoWorkbench")

    # ── Theme ─────────────────────────────────────────────────────────
    qss_path = build_theme_qss_file()
    app.setStyleSheet(qss_path.read_text(encoding="utf-8"))

    # ── Font loading hook ─────────────────────────────────────────────
    # Add custom font files here when available, e.g.:
    #   QFontDatabase.addApplicationFont("resources/fonts/NotoSansSC-Regular.ttf")
    # (font files are not bundled in the skeleton; system fonts are used)

    # ── App context (shared state + DI container) ─────────────────────
    ctx = AppContext()

    # ── Main window ───────────────────────────────────────────────────
    win = MainWindow(ctx)

    # Register all 14 module views
    for view_cls in ALL_VIEWS:
        win.register_view(view_cls)

    # Restore last window state + nav selection
    win.restore_state()
    win.show()

    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
