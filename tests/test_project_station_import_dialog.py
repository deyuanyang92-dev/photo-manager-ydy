"""test_project_station_import_dialog.py — 项目站位总表导入对话框冒烟.

Run:
    QT_QPA_PLATFORM=offscreen python3 -m pytest tests/test_project_station_import_dialog.py -v
"""
from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from app.db.db_manager import open_project_db
from app.services import collection_record_service as crs

_APP = QApplication.instance() or QApplication([])


def _csv(tmp_path: Path) -> Path:
    p = tmp_path / "total.csv"
    p.write_text(
        "断面,站位,经度,纬度\n"
        "T01,B2,121.76,29.11\n"
        "T01,B3,121.80,29.20\n"
        "T99,Z1,118.0,24.0\n",  # T99 has no folder → unmatched
        encoding="utf-8",
    )
    return p


def _dlg(root_dir: str):
    from app.widgets.project_station_import_dialog import ProjectStationImportDialog
    return ProjectStationImportDialog(root_dir=root_dir)


class TestProjectStationImportDialog:
    def test_instantiates(self, tmp_path):
        d = _dlg(str(tmp_path))
        assert d is not None
        assert "transect" in d._field_combos

    def test_load_file_auto_guesses_transect(self, tmp_path):
        (tmp_path / "T01").mkdir()
        d = _dlg(str(tmp_path))
        d.load_file(str(_csv(tmp_path)))
        assert d._headers == ["断面", "站位", "经度", "纬度"]
        mapping = d.current_mapping()
        # Auto-guess should map 断面→transect, 站位→station, 经度→lon, 纬度→lat.
        assert mapping.get("transect") == "断面"
        assert mapping.get("station") == "站位"
        assert mapping.get("lon") == "经度"
        assert mapping.get("lat") == "纬度"

    def test_preview_matches_and_unmatches(self, tmp_path):
        (tmp_path / "T01").mkdir()  # T01 exists, T99 doesn't
        d = _dlg(str(tmp_path))
        d.load_file(str(_csv(tmp_path)))
        d.set_mapping({"transect": "断面", "station": "站位",
                       "lon": "经度", "lat": "纬度"})
        plan = d.preview()
        assert "T01" in plan["matched"]
        assert len(plan["matched"]["T01"]["rows"]) == 2
        assert "T99" in plan["unmatched"]

    def test_preview_then_distribute_writes_records(self, tmp_path):
        (tmp_path / "T01").mkdir()
        d = _dlg(str(tmp_path))
        d.load_file(str(_csv(tmp_path)))
        d.set_mapping({"transect": "断面", "station": "站位",
                       "lon": "经度", "lat": "纬度"})
        # Drive the preview handler, then distribute.
        d._on_preview()
        assert d._plan is not None
        assert d._btn_distribute.isEnabled()
        result = d.distribute()
        assert result["written"] == 2
        assert result["skipped_unmatched_rows"] == 1

        # Verify T01's collection_records actually got the rows.
        db = open_project_db(str(tmp_path / "T01"), create=False)
        rec = crs.lookup_record(db, "", "", "B2", "")
        assert rec is not None
        assert abs(rec["lon"] - 121.76) < 1e-6

    def test_preview_without_transect_warns(self, tmp_path, monkeypatch):
        d = _dlg(str(tmp_path))
        d.load_file(str(_csv(tmp_path)))
        # Clear transect mapping so the guard fires.
        d._field_combos["transect"].setCurrentText("—无—")
        warned = {}
        monkeypatch.setattr(
            "app.widgets.project_station_import_dialog.ui.warn",
            lambda *a, **k: warned.setdefault("called", True),
        )
        d._on_preview()
        assert warned.get("called") is True
        assert not d._btn_distribute.isEnabled()
