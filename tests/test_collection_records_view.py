"""test_collection_records_view.py — offscreen smoke tests for 采集记录 view.

Checks the view instantiates, is registered as a nav page, loads records from
the project DB on activate, and that its form↔service round-trip works.

Run:
    QT_QPA_PLATFORM=offscreen python3 -m pytest tests/test_collection_records_view.py -v
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from app.app_context import AppContext
from app.db import db_manager
from app.services import collection_record_service as crs
from app.views.collection_records_view import CollectionRecordsView
from app.views.registry import ALL_VIEWS


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture()
def ctx(tmp_path):
    db_manager.close_all()
    c = AppContext()
    c.current_project_dir = str(tmp_path)
    # New strict-open semantics: establishing the workspace db is explicit.
    db_manager.open_project_db(str(tmp_path), create=True)
    yield c
    db_manager.close_all()


def test_registered_in_nav():
    assert CollectionRecordsView in ALL_VIEWS


def test_class_attrs():
    assert CollectionRecordsView.view_id == "collection_records"
    assert CollectionRecordsView.nav_title


def test_instantiates_and_loads(qapp, ctx):
    db = ctx.get_db()
    crs.upsert_record(db, {
        "province": "ZJ", "site": "SMW", "station": "B2",
        "collection_date": "20260518", "collector": "杨德援", "habitat": "泥滩",
    })
    view = CollectionRecordsView(ctx)
    view.on_activate()
    assert view._table.rowCount() == 1


def test_save_from_form_persists(qapp, ctx):
    db = ctx.get_db()
    view = CollectionRecordsView(ctx)
    view.on_activate()
    view._new_record()
    view._fields["province"].setText("FJ")
    view._fields["site"].setText("XM")
    view._fields["station"].setText("H1")
    view._fields["collection_date"].setText("20260601")
    view._fields["habitat"].setText("岩相")
    view._save_record()

    rec = crs.lookup_record(db, "FJ", "XM", "H1", "20260601")
    assert rec is not None
    assert rec["habitat"] == "岩相"
    assert view._table.rowCount() == 1


def test_no_project_is_safe(qapp):
    db_manager.close_all()
    c = AppContext()  # no project dir
    view = CollectionRecordsView(c)
    view.on_activate()  # must not raise
    assert view._table.rowCount() == 0


def test_pending_record_filter_selects_matching_row(qapp, ctx):
    """采集地图点击点 → 设 ctx.pending_record_filter → 本页 on_activate 选中对应行并清句柄。"""
    db = ctx.get_db()
    crs.upsert_record(db, {"province": "ZJ", "site": "SMW", "station": "B2",
                           "collection_date": "20260518", "lon": "121.0", "lat": "29.0"})
    crs.upsert_record(db, {"province": "FJ", "site": "XM", "station": "A1",
                           "collection_date": "20260601", "lon": "118.0", "lat": "24.0"})
    view = CollectionRecordsView(ctx)
    ctx.pending_record_filter = {"province": "FJ", "site": "XM", "station": "A1"}
    view.on_activate()
    # 选中行应为 A1
    sel = view._table.selectedItems()
    assert sel, "no row selected"
    row = sel[0].row()
    # station 是表格第 0 列（见 _TABLE_COLS）
    assert view._table.item(row, 0).text() == "A1"
    # 句柄被消费
    assert getattr(ctx, "pending_record_filter", None) is None
