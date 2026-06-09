"""capture_workbench_complete.py — Render the complete workbench at 1920x1080.

Seeds specimen data, phase pills, grouping, score ring and all new controls.

Usage:
    QT_QPA_PLATFORM=offscreen python3 docs/shots/capture_workbench_complete.py
Output:
    docs/shots/workbench_complete.png
"""
from __future__ import annotations

import json
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
from app.db.db_manager import open_project_db  # noqa: E402
from app.main_window import MainWindow  # noqa: E402
from app.views.registry import ALL_VIEWS  # noqa: E402


def _touch(path: Path, blob: bytes = b"\xff\xd8\xff\xe0demo") -> str:
    path.write_bytes(blob)
    return str(path.resolve())


def _seed_project(project_dir: Path) -> None:
    incoming = project_dir / "incoming-jpg"
    results  = project_dir / "results"
    incoming.mkdir(parents=True, exist_ok=True)
    results.mkdir(parents=True, exist_ok=True)

    jpgs = [_touch(incoming / f"DSC0{n}.jpg") for n in range(120, 128)]
    tiff = _touch(results / "FJ-YGLZ-B2-DLC001-1-RD75E-20260506-0508.tif",
                  blob=b"II*\x00demo")

    db = open_project_db(str(project_dir))
    pdir = str(project_dir.resolve())

    specimens = [
        ("FJ-YGLZ-B2-DLC001-RD75E-20260506-0508", "DLC001", "FJ", "YGLZ", "B2",
         "RD75E", "Conus textile", "织锦芋螺", True,
         "Conidae", 119.6543, 25.1234),
        ("FJ-YGLZ-B2-DLC002-T95E-20260506-0508", "DLC002", "FJ", "YGLZ", "B2",
         "T95E", "Cypraea tigris", "虎斑宝贝", False,
         "Cypraeidae", None, None),
        ("FJ-YGLZ-A1-NSC014-D75E-20260505-0507", "NSC014", "FJ", "YGLZ", "A1",
         "D75E", "Nassarius siquijorensis", "织纹螺", False,
         "Nassariidae", None, None),
        ("FJ-YGLZ-A1-PTL003-T75E-20260505-0507", "PTL003", "FJ", "YGLZ", "A1",
         "T75E", "Patella vulgata", "帽贝", False,
         "Patellidae", None, None),
    ]
    for (uid, sid, prov, site, stn, storage, sci, sci_cn, active, family, lon, lat) in specimens:
        db.execute(
            """INSERT OR REPLACE INTO specimens
               (uid, id, province, site, station, storage,
                collection_date, photo_date,
                scientific_name, scientific_name_cn,
                taxon_group, order_name, family, genus,
                collector, photographer, identifier,
                geo_area, lon, lat, notes, photo_notes,
                owner_project_dir, raw_json)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (uid, sid, prov, site, stn, storage,
             "20260506", "20260508",
             sci, sci_cn,
             "Mollusca", "Neogastropoda", family, "Conus",
             "王博士", "李工", "陈研究员",
             "福建· 渔港礁石", lon, lat,
             "潮间带礁石区采集，活体固定。", "正面 / 背面 / 腹面三角度。",
             pdir, "{}"),
        )

    active_uid = specimens[0][0]
    db.execute(
        """INSERT OR REPLACE INTO tasks
           (uid, is_active, activated_at, next_result_sequence_hint)
           VALUES (?, 1, ?, 1)""",
        (active_uid, "2026-05-08T09:42:11"),
    )

    # Grouping: one draft group (3 JPG) + one composed group (3 JPG → TIFF)
    db.execute(
        """INSERT OR REPLACE INTO grouping
           (uid, group_index, angle_label, jpg_paths, composed_tiff_path,
            status, created_at, updated_at)
           VALUES (?,?,?,?,?,?,?,?)""",
        (active_uid, 0, "正面", json.dumps(jpgs[0:3]), None,
         "pending", "2026-05-08T09:40:00", "2026-05-08T09:40:00"),
    )
    db.execute(
        """INSERT OR REPLACE INTO grouping
           (uid, group_index, angle_label, jpg_paths, composed_tiff_path,
            status, created_at, updated_at)
           VALUES (?,?,?,?,?,?,?,?)""",
        (active_uid, 1, "背面", json.dumps(jpgs[3:6]), tiff,
         "composed", "2026-05-08T09:41:00", "2026-05-08T09:45:00"),
    )
    db.commit()


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    load_fonts(app)
    theme_name = os.environ.get("APP_THEME", "classic_light")
    qss = build_theme_qss_file(theme_name)
    app.setStyleSheet(qss.read_text(encoding="utf-8"))

    tmp = Path(tempfile.mkdtemp(prefix="wb-complete-"))
    project_dir = tmp / "FJ-YGLZ-2026"
    project_dir.mkdir(parents=True, exist_ok=True)
    _seed_project(project_dir)

    ctx = AppContext()
    ctx.current_project_dir = str(project_dir)

    win = MainWindow(ctx)
    for view_cls in ALL_VIEWS:
        win.register_view(view_cls)
    win.navigate_to("workbench")
    win.resize(1920, 1080)
    win.show()

    for _ in range(12):
        app.processEvents()

    suffix = "" if theme_name == "classic_light" else f"_{theme_name}"
    out = Path(__file__).resolve().parent / f"workbench_complete{suffix}.png"
    pix = win.grab()
    pix.save(str(out))
    size = out.stat().st_size if out.exists() else 0
    print(f"saved: {out}  ({pix.width()}x{pix.height()}, {size} bytes)")
    return 0 if size > 5000 else 1


if __name__ == "__main__":
    sys.exit(main())
