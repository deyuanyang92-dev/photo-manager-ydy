"""capture_taxonomy_func.py — 1920x1080 screenshot of TaxonomyView for functional review.

Usage:
    QT_QPA_PLATFORM=offscreen python3 docs/shots/capture_taxonomy_func.py
Output:
    docs/shots/taxonomy_func.png
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_ROOT))

from PyQt6.QtWidgets import QApplication, QMainWindow  # noqa: E402

from app.app_context import AppContext  # noqa: E402
from app.config.theme import build_theme_qss_file, load_fonts  # noqa: E402
from app.views.taxonomy_view import TaxonomyView  # noqa: E402
from app.services.taxonomy_service import TaxonomyService  # noqa: E402


_SEED_DATA = [
    {
        "class": "Polychaeta",   "order": "Phyllodocida",
        "family": "Polynoidae",  "species": "Halosydna brevisetosa",
        "classCn": "多毛纲",     "orderCn": "叶须虫目",
        "familyCn": "多鳞虫科",  "genus": "Halosydna",
        "genusCn": "海鳞虫属",   "speciesCn": "短毛海鳞虫",
    },
    {
        "class": "Polychaeta",   "order": "Phyllodocida",
        "family": "Aphroditidae","species": "Aphrodita aculeata",
        "classCn": "多毛纲",     "orderCn": "叶须虫目",
        "familyCn": "鳞沙蚕科",  "genus": "Aphrodita",
        "genusCn": "鳞沙蚕属",   "speciesCn": "棘鳞沙蚕",
    },
    {
        "class": "Malacostraca", "order": "Decapoda",
        "family": "Portunidae",  "species": "Portunus trituberculatus",
        "classCn": "软甲纲",     "orderCn": "十足目",
        "familyCn": "梭子蟹科",  "genus": "Portunus",
        "genusCn": "梭子蟹属",   "speciesCn": "三疣梭子蟹",
    },
    {
        "class": "Gastropoda",   "order": "Neogastropoda",
        "family": "Conidae",     "species": "Conus textile",
        "classCn": "腹足纲",     "orderCn": "新腹足目",
        "familyCn": "芋螺科",    "genus": "Conus",
        "genusCn": "芋螺属",     "speciesCn": "织锦芋螺",
    },
    {
        "class": "Bivalvia",     "order": "Mytilida",
        "family": "Mytilidae",   "species": "Mytilus edulis",
        "classCn": "双壳纲",     "orderCn": "贻贝目",
        "familyCn": "贻贝科",    "genus": "Mytilus",
        "genusCn": "贻贝属",     "speciesCn": "紫贻贝",
    },
    {
        "class": "Echinoidea",   "order": "Diadematoida",
        "family": "Diadematidae","species": "Diadema setosum",
        "classCn": "海胆纲",     "orderCn": "冠海胆目",
        "familyCn": "冠海胆科",  "genus": "Diadema",
        "genusCn": "冠海胆属",   "speciesCn": "刺冠海胆",
    },
]


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    load_fonts(app)
    qss_file = build_theme_qss_file()
    app.setStyleSheet(qss_file.read_text(encoding="utf-8"))

    tmp = Path(tempfile.mkdtemp(prefix="taxon-func-shot-"))
    seed_p = tmp / "taxonomy_seed.json"
    user_p = tmp / "user_taxonomy.json"
    seed_p.write_text(json.dumps(_SEED_DATA), encoding="utf-8")
    user_p.write_text("[]", encoding="utf-8")

    ctx = MagicMock(spec=AppContext)
    ctx.current_project_dir = None
    ctx.settings = MagicMock()
    ctx.settings.last_nav_index = 0

    view = TaxonomyView(ctx)
    svc = TaxonomyService(seed_p, user_p)
    view._svc = svc

    # Add user records with history (to show history button is wired)
    svc.learn({
        "class": "Polychaeta",  "order": "Phyllodocida",
        "family": "Polynoidae", "species": "Harmothoe imbricata",
        "classCn": "多毛纲",    "orderCn": "叶须虫目",
        "familyCn": "多鳞虫科", "genus": "Harmothoe",
        "genusCn": "叶须虫属",  "speciesCn": "覆瓦叶须虫",
    })
    # Update to create a history entry
    recs, _ = svc.all_records(source_filter="user")
    if recs:
        svc.update(recs[0]["recordId"], {"orderCn": "叶须虫目（已验证）"})

    svc.learn({
        "class": "Cephalopoda", "order": "Octopoda",
        "family": "Octopodidae","species": "Octopus vulgaris",
        "classCn": "头足纲",    "orderCn": "八腕目",
        "familyCn": "章鱼科",   "genus": "Octopus",
        "genusCn": "章鱼属",    "speciesCn": "普通章鱼",
    })

    win = QMainWindow()
    win.setWindowTitle("内置分类库 — 功能截图 1920×1080")
    win.setCentralWidget(view)
    win.resize(1920, 1080)
    win.show()

    view.on_activate()

    for _ in range(15):
        app.processEvents()

    out = Path(__file__).resolve().parent / "taxonomy_func.png"
    pix = win.grab()
    pix.save(str(out))
    size = out.stat().st_size if out.exists() else 0
    print(f"saved: {out}  ({pix.width()}x{pix.height()}, {size} bytes)")
    return 0 if size > 5000 else 1


if __name__ == "__main__":
    sys.exit(main())
