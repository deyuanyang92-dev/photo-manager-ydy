"""capture_coords.py — Render CoordsView to PNG for design review.

Runs fully headless (QT_QPA_PLATFORM=offscreen).

Usage:
    QT_QPA_PLATFORM=offscreen python3 docs/shots/capture_coords.py

Output:
    docs/shots/page_coords.png
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_ROOT))

from PyQt6.QtWidgets import QApplication  # noqa: E402
from unittest.mock import MagicMock       # noqa: E402

from app.config.theme import build_theme_qss_file, load_fonts  # noqa: E402
from app.app_context import AppContext                           # noqa: E402
from app.views.coords_view import CoordsView                    # noqa: E402


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    load_fonts(app)
    qss = build_theme_qss_file()
    app.setStyleSheet(qss.read_text(encoding="utf-8"))

    ctx = MagicMock(spec=AppContext)
    ctx.has_project = False
    ctx.current_project_dir = None
    ctx.settings = MagicMock()

    view = CoordsView(ctx)
    view.resize(1100, 840)
    view.show()

    # Seed the view with a parsed DMS coordinate so all panels show content
    view._input_edit.setText("29°06'53.7\"N 121°45'51.2\"E")
    app.processEvents()

    # Open batch section with example data
    view._on_batch_toggle()
    view._batch_textarea.setPlainText(
        "29.11492, 121.76421\n"
        "24.48921N 118.18432E\n"
        "29°06'53.7\"N 121°45'51.2\"E\n"
        "北纬 24.48921  东经 118.18432\n"
        "not-a-coord"
    )
    view._on_batch_parse()
    app.processEvents()

    # Process a few more times to let the layout settle
    for _ in range(6):
        app.processEvents()

    out = Path(__file__).resolve().parent / "page_coords.png"
    pix = view.grab()
    pix.save(str(out))
    size = out.stat().st_size if out.exists() else 0
    print(f"saved: {out}  ({pix.width()}x{pix.height()}, {size} bytes)")
    return 0 if size > 5000 else 1


if __name__ == "__main__":
    sys.exit(main())
