"""capture_workbench.py — Render the workbench page to a PNG for design review.

Runs fully headless (QT_QPA_PLATFORM=offscreen).  Builds a throwaway temp
project with seeded specimens, an active task, grouping, and a handful of
incoming JPG / results TIFF placeholder files so every panel has content,
then grabs the workbench view at 1440x900.

Usage:
    QT_QPA_PLATFORM=offscreen python3 docs/shots/capture_workbench.py
Output:
    docs/shots/workbench.png
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
    """Create incoming/results files + seed the project DB with content."""
    incoming = project_dir / "incoming-jpg"
    results = project_dir / "results"
    incoming.mkdir(parents=True, exist_ok=True)
    results.mkdir(parents=True, exist_ok=True)

    jpgs = [_touch(incoming / f"DSC0{n}.jpg") for n in range(120, 126)]
    tiff = _touch(results / "FJ-YGLZ-B2-DLC001-1-RD75E-20260506-0508.tif",
                  blob=b"II*\x00demo")

    db = open_project_db(str(project_dir))
    pdir = str(Path(project_dir).resolve())

    specimens = [
        ("FJ-YGLZ-B2-DLC001-RD75E-20260506-0508", "DLC001", "FJ", "YGLZ", "B2",
         "RD75E", "Conus textile", "织锦芋螺", True),
        ("FJ-YGLZ-B2-DLC002-T95E-20260506-0508", "DLC002", "FJ", "YGLZ", "B2",
         "T95E", "Cypraea tigris", "虎斑宝贝", False),
        ("FJ-YGLZ-A1-NSC014-D75E-20260505-0507", "NSC014", "FJ", "YGLZ", "A1",
         "D75E", "Nassarius siquijorensis", "织纹螺", False),
        ("FJ-YGLZ-A1-PTL003-T75E-20260505-0507", "PTL003", "FJ", "YGLZ", "A1",
         "T75E", "Patella vulgata", "帽贝", False),
    ]
    for (uid, sid, prov, site, stn, storage, sci, sci_cn, active) in specimens:
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
             "Mollusca", "Neogastropoda", "Conidae", "Conus",
             "王博士", "李工", "陈研究员",
             "福建· 渔港礁石", 119.6543, 25.1234,
             "潮间带礁石区采集，活体固定。", "正面 / 背面 / 腹面三角度。",
             pdir, json.dumps({
                 "uid": uid, "speciesCn": sci_cn,
                 "province": prov, "site": site, "station": stn,
                 "id": sid, "storage": storage,
                 "collectionDate": "20260506", "photoDate": "20260508",
             })),
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
    qss = build_theme_qss_file()
    app.setStyleSheet(qss.read_text(encoding="utf-8"))

    tmp = Path(tempfile.mkdtemp(prefix="wb-shot-"))
    project_dir = tmp / "FJ-YGLZ-2026"
    project_dir.mkdir(parents=True, exist_ok=True)
    _seed_project(project_dir)

    ctx = AppContext()
    ctx.current_project_dir = str(project_dir)

    win = MainWindow(ctx)
    for view_cls in ALL_VIEWS:
        win.register_view(view_cls)
    win.navigate_to("workbench")
    win.resize(1440, 900)
    win.show()

    # Process events so the offscreen surface paints fully before grabbing.
    for _ in range(8):
        app.processEvents()

    out = Path(__file__).resolve().parent / "workbench_v2.png"
    pix = win.grab()
    pix.save(str(out))
    size = out.stat().st_size if out.exists() else 0
    print(f"saved: {out}  ({pix.width()}x{pix.height()}, {size} bytes)")
    return 0 if size > 5000 else 1


if __name__ == "__main__":
    sys.exit(main())
