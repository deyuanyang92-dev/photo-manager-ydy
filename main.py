"""标本照片工作台 — 桌面版入口。

Usage:
    python main.py                 # normal launch (requires display)
    QT_QPA_PLATFORM=offscreen python main.py   # headless CI check
"""
import sys
from pathlib import Path

from PyQt6.QtWidgets import QApplication

from app.app_context import AppContext
from app.config.theme import build_theme_qss_file, load_fonts
from app.main_window import MainWindow
from app.views.registry import ALL_VIEWS


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("标本照片工作台")
    app.setOrganizationName("SpecimenPhotoWorkbench")

    # ── Fonts (bundled Noto Sans/Serif SC + JetBrains Mono if present;
    #    web-parity system fallback otherwise) ──────────────────────────
    load_fonts(app)

    # ── Theme ─────────────────────────────────────────────────────────
    qss_path = build_theme_qss_file()
    app.setStyleSheet(qss_path.read_text(encoding="utf-8"))

    # ── App context (shared state + DI container) ─────────────────────
    ctx = AppContext()

    # ── Main window ───────────────────────────────────────────────────
    win = MainWindow(ctx)

    # Register all 14 module views
    for view_cls in ALL_VIEWS:
        win.register_view(view_cls)

    # Restore last window state + nav selection
    win.restore_state()
    win.showMaximized()   # open full-screen so the columns get room to breathe

    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
