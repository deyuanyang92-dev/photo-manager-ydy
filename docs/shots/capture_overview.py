"""capture_overview.py — Render the 项目总览 page to a PNG for design review.

Runs fully headless (QT_QPA_PLATFORM=offscreen).  Seeds three sample projects
(with directories, dates, locations, and collectors) so the table has rows,
then grabs the overview page at 1440×900.

Usage:
    QT_QPA_PLATFORM=offscreen python3 docs/shots/capture_overview.py
Output:
    docs/shots/page_overview.png
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = Path(__file__).resolve().parent.parent.parent   # photo-platform-ydy-v3/
sys.path.insert(0, str(_ROOT))

from PyQt6.QtWidgets import QApplication  # noqa: E402

from app.app_context import AppContext  # noqa: E402
from app.config.theme import build_theme_qss_file, load_fonts  # noqa: E402
from app.main_window import MainWindow  # noqa: E402
from app.views.registry import ALL_VIEWS  # noqa: E402


def _seed_projects_json(tmp_dir: Path) -> Path:
    """Write a user_projects.json with three representative projects."""
    projects = [
        {
            "id": "proj-001",
            "name": "厦门潮间带多毛类调查",
            "directory": str(tmp_dir / "FJ-XM-polychaeta-2026"),
            "year": "2026",
            "dateRange": "2026-05 ~ 2026-06",
            "location": "福建·厦门·渔港礁石",
            "collector": "王博士",
        },
        {
            "id": "proj-002",
            "name": "广州湾海藻分类普查",
            "directory": str(tmp_dir / "GD-GZ-algae-2025"),
            "year": "2025",
            "dateRange": "2025-11",
            "location": "广东·广州湾",
            "collector": "陈研究员",
        },
        {
            "id": "proj-003",
            "name": "三亚珊瑚礁底栖动物监测",
            "directory": str(tmp_dir / "HN-SY-coral-2026"),
            "year": "2026",
            "dateRange": "2026-04 ~ 2026-05",
            "location": "海南·三亚·珊瑚礁",
            "collector": "李工",
        },
    ]
    # Create the project directories so they actually exist
    for p in projects:
        Path(p["directory"]).mkdir(parents=True, exist_ok=True)

    json_path = tmp_dir / "user_projects.json"
    json_path.write_text(
        json.dumps({"version": 1, "projects": projects}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return json_path


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    load_fonts(app)
    qss = build_theme_qss_file()
    app.setStyleSheet(qss.read_text(encoding="utf-8"))

    tmp = Path(tempfile.mkdtemp(prefix="ov-shot-"))
    json_path = _seed_projects_json(tmp)

    ctx = AppContext()
    win = MainWindow(ctx)
    for view_cls in ALL_VIEWS:
        win.register_view(view_cls)

    # Patch the project JSON path so the overview view reads our seed data
    import app.views.overview_view as ov_mod
    original_resolve = ov_mod._resolve_projects_json

    def patched_resolve():
        return json_path

    ov_mod._resolve_projects_json = patched_resolve  # type: ignore[assignment]

    win.resize(1440, 900)
    win.navigate_to("overview")
    win.show()

    # Process events so the offscreen surface paints fully before grabbing
    for _ in range(10):
        app.processEvents()

    out = Path(__file__).resolve().parent / "page_overview.png"
    pix = win.grab()
    pix.save(str(out))

    # Restore
    ov_mod._resolve_projects_json = original_resolve  # type: ignore[assignment]

    size = out.stat().st_size if out.exists() else 0
    print(f"saved: {out}  ({pix.width()}x{pix.height()}, {size} bytes)")
    return 0 if size > 5000 else 1


if __name__ == "__main__":
    sys.exit(main())
