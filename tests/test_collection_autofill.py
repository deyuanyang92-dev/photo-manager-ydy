"""test_collection_autofill.py — capture-side auto-fill from a 采集记录.

Covers:
  - MetadataPanel.apply_autofill / current_values (non-destructive).
  - NamingPanel.set_location_keys / current_keys + keys_committed signal.
  - WorkbenchView._apply_collection_autofill end-to-end against a project DB.

Run:
    QT_QPA_PLATFORM=offscreen python3 -m pytest tests/test_collection_autofill.py -v
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from app.app_context import AppContext
from app.db import db_manager
from app.services import collection_record_service as crs
from app.widgets.metadata_panel import MetadataPanel
from app.widgets.naming_panel import NamingPanel


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


# ── MetadataPanel ─────────────────────────────────────────────────────────────

class TestMetadataApplyAutofill:
    def test_fills_empty_only(self, qapp, ctx):
        panel = MetadataPanel(ctx)
        panel._collector.setText("已填采集人")
        panel.apply_autofill({"collector": "记录采集人", "lon": "121.5", "geo_area": "三门湾"})
        assert panel._collector.text() == "已填采集人"   # preserved
        assert panel._lon.text() == "121.5"               # filled
        assert panel._geo_area.text() == "三门湾"          # filled

    def test_current_values_keys(self, qapp, ctx):
        panel = MetadataPanel(ctx)
        vals = panel.current_values()
        assert set(vals) == {"collector", "photographer", "identifier",
                             "lon", "lat", "geo_area"}


# ── NamingPanel ───────────────────────────────────────────────────────────────

class TestNamingKeys:
    def test_set_and_get_keys_emits_signal(self, qapp, ctx):
        panel = NamingPanel(ctx)
        fired = []
        panel.keys_committed.connect(lambda: fired.append(True))
        panel.set_location_keys("ZJ", "SMW", "B2", "20260518")
        assert panel.current_keys() == ("ZJ", "SMW", "B2", "20260518")
        assert fired  # keys_committed emitted


# ── WorkbenchView end-to-end ──────────────────────────────────────────────────

class TestWorkbenchAutofill:
    def test_autofill_from_record(self, qapp, ctx):
        db = ctx.get_db()
        crs.upsert_record(db, {
            "province": "ZJ", "site": "SMW", "station": "B2",
            "collection_date": "20260518",
            "collector": "杨德援", "photographer": "钟珅",
            "lon": "121.764", "lat": "29.114", "geo_area": "三门湾",
            "habitat": "泥滩", "tide": "低潮",
        })
        from app.views.workbench_view import WorkbenchView
        wb = WorkbenchView(ctx)

        # User picks / types the 4 keys → triggers auto-fill.
        wb._naming.set_location_keys("ZJ", "SMW", "B2", "20260518")

        assert wb._metadata._collector.text() == "杨德援"
        assert wb._metadata._photographer.text() == "钟珅"
        assert wb._metadata._lon.text() == "121.764"
        assert wb._metadata._geo_area.text() == "三门湾"

    def test_autofill_does_not_overwrite_user_value(self, qapp, ctx):
        db = ctx.get_db()
        crs.upsert_record(db, {
            "province": "FJ", "site": "XM", "station": "H1",
            "collection_date": "20260601", "collector": "记录人",
        })
        from app.views.workbench_view import WorkbenchView
        wb = WorkbenchView(ctx)
        wb._metadata._collector.setText("我手填的")
        wb._naming.set_location_keys("FJ", "XM", "H1", "20260601")
        assert wb._metadata._collector.text() == "我手填的"

    def test_no_match_is_noop(self, qapp, ctx):
        from app.views.workbench_view import WorkbenchView
        wb = WorkbenchView(ctx)
        wb._naming.set_location_keys("XX", "YY", "ZZ", "20990101")  # no record
        assert wb._metadata._collector.text() == ""
