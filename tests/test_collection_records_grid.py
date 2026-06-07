"""tests/test_collection_records_grid.py — 采集记录「批量表格」(步骤 4)."""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QTableWidgetItem
from PyQt6.QtCore import Qt

from app.app_context import AppContext
from app.db import db_manager
from app.services import collection_record_service as crs
from app.services import project_settings_service as pss
from app.views.collection_records_view import CollectionRecordsView, _GRID_COLS


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


@pytest.fixture()
def ctx(tmp_path):
    db_manager.close_all()
    c = AppContext()
    c.current_project_dir = str(tmp_path)
    yield c
    db_manager.close_all()


def _col(key: str) -> int:
    return [k for k, _ in _GRID_COLS].index(key)


def _set(view, row, key, text):
    view._grid.setItem(row, _col(key), QTableWidgetItem(text))


def test_grid_loads_records_plus_blank_row(qapp, ctx):
    db = ctx.get_db()
    crs.upsert_record(db, {"province": "FJ", "site": "XM", "station": "B2",
                           "collection_date": "20260518", "habitat": "泥滩"})
    view = CollectionRecordsView(ctx)
    view.on_activate()
    # one record + one trailing blank row
    assert view._grid.rowCount() == 2
    assert view._grid.item(0, _col("station")).text() == "B2"
    assert view._grid.item(0, _col("habitat")).text() == "泥滩"


def test_grid_save_uses_inherited_province_site(qapp, ctx):
    # set project-level 地区/样地 once; grid rows must inherit them
    db = ctx.get_db()
    pss.save_setting(db, "code_labels",
                     {"province": "GD", "site": "雷州", "stations": {}, "species": {}})
    view = CollectionRecordsView(ctx)
    view.on_activate()
    # fill the trailing blank row (row 0, since no records yet)
    _set(view, 0, "station", "S01")
    _set(view, 0, "collection_date", "20260601")
    _set(view, 0, "habitat", "岩相")
    _set(view, 0, "collector", "张三")
    view._grid_save()

    rec = crs.lookup_record(db, "GD", "雷州", "S01", "20260601")
    assert rec is not None
    assert rec["habitat"] == "岩相"
    assert rec["collector"] == "张三"


def test_grid_skips_rows_without_station_or_date(qapp, ctx):
    db = ctx.get_db()
    pss.save_setting(db, "code_labels",
                     {"province": "GD", "site": "雷州", "stations": {}, "species": {}})
    view = CollectionRecordsView(ctx)
    view.on_activate()
    _set(view, 0, "habitat", "泥滩")  # no station/date → must be skipped
    view._grid_save()
    assert crs.list_records(db) == []


def test_grid_fill_down(qapp, ctx):
    view = CollectionRecordsView(ctx)
    view.on_activate()
    view._grid_add_row(inherit=False)
    view._grid_add_row(inherit=False)  # now ≥3 rows
    _set(view, 0, "collection_date", "20260601")
    view._grid.setCurrentCell(0, _col("collection_date"))
    view._grid_fill_down()
    assert view._grid.item(1, _col("collection_date")).text() == "20260601"
    assert view._grid.item(2, _col("collection_date")).text() == "20260601"


def test_grid_add_row_inherits_date_and_collector(qapp, ctx):
    view = CollectionRecordsView(ctx)
    view.on_activate()
    _set(view, 0, "collection_date", "20260601")
    _set(view, 0, "collector", "李四")
    view._grid_add_row(inherit=True)
    last = view._grid.rowCount() - 1
    assert view._grid.item(last, _col("collection_date")).text() == "20260601"
    assert view._grid.item(last, _col("collector")).text() == "李四"
    assert view._grid.item(last, _col("station")).text() == ""  # station NOT carried


def test_grid_edit_existing_record_updates_in_place(qapp, ctx):
    db = ctx.get_db()
    crs.upsert_record(db, {"province": "FJ", "site": "XM", "station": "B2",
                           "collection_date": "20260518", "habitat": "泥滩"})
    view = CollectionRecordsView(ctx)
    view.on_activate()
    # edit habitat of the existing row (row 0)
    view._grid.item(0, _col("habitat")).setText("岩相")
    view._grid_save()
    rec = crs.lookup_record(db, "FJ", "XM", "B2", "20260518")
    assert rec["habitat"] == "岩相"
    assert len(crs.list_records(db)) == 1  # updated in place, not duplicated
