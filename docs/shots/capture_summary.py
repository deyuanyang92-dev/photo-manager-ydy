"""capture_summary.py — Offscreen screenshot of SummaryView → page_summary.png.

Run:
    【WSL】 QT_QPA_PLATFORM=offscreen python3 docs/shots/capture_summary.py
"""
from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path
from unittest.mock import MagicMock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, str(Path(__file__).parents[2]))  # project root

from PyQt6.QtWidgets import QApplication
from PyQt6.QtGui import QPixmap

app = QApplication.instance() or QApplication(sys.argv)

# ── Minimal in-memory DB with sample data ────────────────────────────────────

conn = sqlite3.connect(":memory:")
conn.row_factory = sqlite3.Row
conn.executescript("""
CREATE TABLE IF NOT EXISTS specimens (
    uid TEXT PRIMARY KEY, id TEXT, province TEXT, site TEXT, station TEXT,
    storage TEXT, collection_date TEXT, photo_date TEXT,
    scientific_name TEXT, scientific_name_cn TEXT,
    taxon_group TEXT, taxon_group_cn TEXT,
    order_name TEXT, order_cn TEXT, family TEXT, family_cn TEXT,
    genus TEXT, genus_cn TEXT, lon REAL, lat REAL, geo_area TEXT,
    collector TEXT, photographer TEXT, identifier TEXT,
    notes TEXT, photo_notes TEXT, angle TEXT,
    metadata INTEGER DEFAULT 0, pinned INTEGER DEFAULT 0,
    owner_project_dir TEXT, raw_json TEXT
);
CREATE TABLE IF NOT EXISTS grouping (
    uid TEXT, group_index INTEGER,
    status TEXT, source TEXT, created_at TEXT, updated_at TEXT,
    result_sequence INTEGER, archive_zip TEXT,
    retired_tiff_paths TEXT, raw_json TEXT,
    PRIMARY KEY (uid, group_index)
);
INSERT INTO specimens VALUES
  ('FJ-YGLZ-B2-DLC001-RD75E-20260506-0508','DLC001','FJ','YGLZ','B2','RD75E','2026-05-06','2026-05-08',
   'Aplysia californica','加州海兔','Gastropoda','腹足纲','Aplysiida','无楯目','Aplysiidae','海兔科',
   'Aplysia','海兔属',119.123,26.456,'福建·丫鼓礁','李明','王芳','陈磊',NULL,NULL,NULL,0,0,'/tmp/proj_A',NULL),
  ('FJ-YGLZ-B2-DLC002-RD75E-20260506-0508','DLC002','FJ','YGLZ','B3','D75E','2026-05-06','2026-05-08',
   'Octopus vulgaris','普通章鱼','Cephalopoda','头足纲','Octopoda','章鱼目','Octopodidae','章鱼科',
   'Octopus','章鱼属',119.124,26.457,'福建·丫鼓礁','李明','王芳',NULL,NULL,NULL,NULL,0,0,'/tmp/proj_A',NULL),
  ('GD-NKLS-C1-OCT003-T95E-20260510-0512','OCT003','GD','NKLS','C1','T95E','2026-05-10','2026-05-12',
   'Haliotis discus hannai','皱纹盘鲍','Gastropoda','腹足纲','Lepetellida','帽贝目','Haliotidae','鲍科',
   'Haliotis','鲍属',113.5,22.3,'广东·南坑岭','张伟','刘洋','陈磊',NULL,NULL,NULL,0,0,'/tmp/proj_B',NULL);
INSERT INTO grouping VALUES
  ('FJ-YGLZ-B2-DLC001-RD75E-20260506-0508', 0, 'composed', 'helicon', '2026-05-09','2026-05-09',1,NULL,NULL,NULL),
  ('GD-NKLS-C1-OCT003-T95E-20260510-0512',  0, 'pending',  'helicon', '2026-05-11','2026-05-11',1,NULL,NULL,NULL);
""")

ctx = MagicMock()
ctx.has_project = True
ctx.current_project_dir = "/tmp/proj_A"
ctx.get_db.return_value = conn
ctx.settings = MagicMock()

from app.views.summary_view import SummaryView

view = SummaryView(ctx)
view.resize(1280, 720)
view.on_activate()

out = Path(__file__).parent / "page_summary.png"
pix = view.grab()
pix.save(str(out))
print(f"Screenshot saved → {out}")
