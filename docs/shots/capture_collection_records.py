"""capture_collection_records.py — Render the 采集记录 view per zone to PNGs.

Headless (QT_QPA_PLATFORM=offscreen). Seeds a throwaway project with a few
潮间带 (intertidal, H.39) + 潮下带 (subtidal, H.30) collection records, then
grabs the CollectionRecordsView under each top-bar zone segment so the zone
split (columns / editor fields) can be eyeballed for UI issues.

Usage:
    QT_QPA_PLATFORM=offscreen python3 docs/shots/capture_collection_records.py
Output:
    docs/shots/collection_<zone>.png
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))  # for capture_workbench

from PyQt6.QtWidgets import QApplication  # noqa: E402

from app.app_context import AppContext  # noqa: E402
from app.config.theme import build_theme_qss_file, load_fonts  # noqa: E402
from app.db.db_manager import open_project_db  # noqa: E402
from app.main_window import MainWindow  # noqa: E402
from app.services import collection_record_service as crs  # noqa: E402
from app.views.registry import ALL_VIEWS  # noqa: E402

from capture_workbench import _seed_project  # noqa: E402


_INTERTIDAL = [
    {"province": "浙江", "site": "三门湾", "station": "B2-Q1", "collection_date": "2026-06-15",
     "zone": "intertidal", "collector": "李四", "photographer": "王五", "recorder": "赵六",
     "verifier": "钱七", "lon": 121.632, "lat": 28.705, "habitat": "泥沙滩",
     "tidal_zone": "中潮区", "quadrate_no": "B2-Q1", "air_temp": "26.5",
     "quant_bottles": 3, "qual_bottles": 2, "replicates": 3, "weather": "晴"},
    {"province": "浙江", "site": "三门湾", "station": "B2-Q3", "collection_date": "2026-06-15",
     "zone": "intertidal", "collector": "李四", "photographer": "王五",
     "habitat": "岩石岸", "tidal_zone": "低潮区", "quadrate_no": "B2-Q3",
     "quant_bottles": 4, "replicates": 3},
]
_SUBTIDAL = [
    {"province": "浙江", "site": "三门湾", "station": "S05", "collection_date": "2026-06-16",
     "zone": "subtidal", "collector": "李四", "photographer": "王五", "recorder": "赵六",
     "verifier": "钱七", "lon": 121.658, "lat": 28.692, "habitat": "粉砂质粘土",
     "depth": "18.5", "cruise": "三门湾2026航次", "vessel": "浙渔科3号",
     "sampler_model": "抓斗式采泥器", "sampler_area": 0.1, "wire_out": "23",
     "net_type": "阿氏网", "net_width": 2.0, "trawl_distance": "500",
     "trawl_start": "121.658/28.692", "trawl_end": "121.663/28.695",
     "grab_sample_total": 2, "trawl_sample_total": 1, "replicates": 2},
    {"province": "浙江", "site": "三门湾", "station": "S08", "collection_date": "2026-06-16",
     "zone": "subtidal", "collector": "李四", "photographer": "王五",
     "habitat": "细砂", "depth": "25.0", "cruise": "三门湾2026航次",
     "vessel": "浙渔科3号", "sampler_model": "箱式采泥器", "sampler_area": 0.05},
]


def _seed_records(project_dir: Path) -> None:
    db = open_project_db(str(project_dir), create=True)
    for rec in _INTERTIDAL + _SUBTIDAL:
        crs.upsert_record(db, rec)
    db.close()


def main() -> int:
    w, h = 1600, 1000
    app = QApplication.instance() or QApplication(sys.argv)
    load_fonts(app)
    app.setStyleSheet(build_theme_qss_file().read_text(encoding="utf-8"))

    tmp = Path(tempfile.mkdtemp(prefix="cr-shot-"))
    project_dir = tmp / "FJ-YGLZ-2026"
    project_dir.mkdir(parents=True, exist_ok=True)
    # _seed_project uses a strict open (create=False), so materialise the
    # workspace DB first; ensure_schema is idempotent so re-opening is safe.
    open_project_db(str(project_dir), create=True).close()
    _seed_project(project_dir)
    _seed_records(project_dir)

    ctx = AppContext()
    ctx.current_project_dir = str(project_dir)

    win = MainWindow(ctx)
    for view_cls in ALL_VIEWS:
        win.register_view(view_cls)
    win.resize(w, h)
    win.show()
    for _ in range(12):
        app.processEvents()

    win.navigate_to("collection_records")
    for _ in range(12):
        app.processEvents()

    view = win.findChild(__import__("PyQt6.QtWidgets", fromlist=["QWidget"]).QWidget,
                         "collection_records")
    out_dir = Path(__file__).resolve().parent

    # Grab each zone segment by toggling the view's zone filter then refreshing.
    zones = [("all", "全部"), ("intertidal", "潮间带"), ("subtidal", "潮下带")]
    for zone_key, _label in zones:
        view._on_zone_filter(zone_key)
        if hasattr(view, "_reload"):
            view._reload()
        for _ in range(8):
            app.processEvents()
        out = out_dir / f"collection_{zone_key}.png"
        win.grab().save(str(out))
        print(f"saved: {out.name}  ({out.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
