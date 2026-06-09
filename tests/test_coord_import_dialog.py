"""test_coord_import_dialog.py — 站位批量导入向导冒烟.

Run:
    QT_QPA_PLATFORM=offscreen python3 -m pytest tests/test_coord_import_dialog.py -v
"""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from app.db.db_manager import ensure_schema
from app.services import collection_record_service as crs

_APP = QApplication.instance() or QApplication([])


def _db():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    ensure_schema(c)
    return c


def _csv(tmp_path: Path) -> Path:
    p = tmp_path / "plan.csv"
    p.write_text("地区,断面,站位,经度,纬度\nZJ,SMW,B2,121.76,29.11\nFJ,XM,A1,118.0,24.0\n",
                 encoding="utf-8")
    return p


def _dlg(db):
    from app.widgets.coord_import_dialog import CoordImportDialog
    return CoordImportDialog(db)


class TestImportDialog:
    def test_instantiates(self):
        assert _dlg(_db()) is not None

    def test_load_file_populates_headers(self, tmp_path):
        d = _dlg(_db())
        d.load_file(str(_csv(tmp_path)))
        assert d._headers == ["地区", "断面", "站位", "经度", "纬度"]
        assert len(d._rows) == 2

    def test_preview_and_import(self, tmp_path):
        db = _db()
        d = _dlg(db)
        d.load_file(str(_csv(tmp_path)))
        d.set_mapping({"province": "地区", "site": "断面", "station": "站位",
                       "lon": "经度", "lat": "纬度"})
        prev = d.preview()
        assert sum(1 for r in prev if r["ok"]) == 2
        n_ok, n_err = d.do_import()
        assert n_ok == 2 and n_err == 0
        rec = crs.lookup_record(db, "FJ", "XM", "A1", "")
        assert rec is not None
        assert abs(rec["lon"] - 118.0) < 1e-6
