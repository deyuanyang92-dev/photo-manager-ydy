"""capture_all.py — Render every nav view to a PNG for design iteration.

Runs fully headless (QT_QPA_PLATFORM=offscreen).  Reuses the seeded throwaway
project from capture_workbench.py so the workbench (and overview/summary) have
real content, then walks every view in ALL_VIEWS, navigating + grabbing each at
a fixed window size.

Usage:
    QT_QPA_PLATFORM=offscreen python3 docs/shots/capture_all.py
    QT_QPA_PLATFORM=offscreen python3 docs/shots/capture_all.py 1600 1000
Output:
    docs/shots/all_<idx>_<view_id>.png   (one per view)
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_ROOT))

from PyQt6.QtWidgets import QApplication  # noqa: E402

from app.app_context import AppContext  # noqa: E402
from app.config.theme import build_theme_qss_file, load_fonts  # noqa: E402
from app.main_window import MainWindow  # noqa: E402
from app.views.registry import ALL_VIEWS  # noqa: E402

# Reuse the workbench seeding so panels have content.
from capture_workbench import _seed_project  # noqa: E402


def main() -> int:
    w = int(sys.argv[1]) if len(sys.argv) > 1 else 1600
    h = int(sys.argv[2]) if len(sys.argv) > 2 else 1000

    app = QApplication.instance() or QApplication(sys.argv)
    load_fonts(app)
    app.setStyleSheet(build_theme_qss_file().read_text(encoding="utf-8"))

    tmp = Path(tempfile.mkdtemp(prefix="all-shot-"))
    project_dir = tmp / "FJ-YGLZ-2026"
    project_dir.mkdir(parents=True, exist_ok=True)
    _seed_project(project_dir)

    ctx = AppContext()
    ctx.current_project_dir = str(project_dir)

    win = MainWindow(ctx)
    for view_cls in ALL_VIEWS:
        win.register_view(view_cls)
    win.resize(w, h)
    win.show()
    for _ in range(12):
        app.processEvents()

    out_dir = Path(__file__).resolve().parent
    rc = 0
    for idx, view_cls in enumerate(ALL_VIEWS):
        vid = getattr(view_cls, "view_id", str(idx))
        try:
            win.navigate_to(vid)
        except Exception:
            win._activate_index(idx)
        win.resize(w, h)
        for _ in range(10):
            app.processEvents()
        out = out_dir / f"all_{idx}_{vid}.png"
        pix = win.grab()
        pix.save(str(out))
        size = out.stat().st_size if out.exists() else 0
        flag = "" if size > 5000 else "  <-- SUSPECT (tiny)"
        print(f"saved: {out.name}  ({pix.width()}x{pix.height()}, {size} bytes){flag}")
        if size <= 5000:
            rc = 1
    return rc


if __name__ == "__main__":
    sys.exit(main())
