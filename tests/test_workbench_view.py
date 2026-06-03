"""test_workbench_view.py — Smoke tests for WorkbenchView and its widgets.

These tests run headless (QT_QPA_PLATFORM=offscreen) and verify:
  - All six files can be imported without error.
  - Each widget can be constructed without crashing.
  - WorkbenchView.on_activate() does not crash when no project is set.
  - WorkbenchView.on_activate() does not crash when a valid project is set.
  - NamingPanel live-preview produces the correct UID / result-ID.
  - SpecimenSidebar.refresh() does not crash on an empty DB.
  - GroupingPanel.clear() is idempotent.
  - MetadataPanel.clear() is idempotent.
  - MonitorPanel.clear() is idempotent.
"""
from __future__ import annotations

import os
import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# ── Qt setup (offscreen) ──────────────────────────────────────────────────────
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

# One shared QApplication instance for all tests in this module
_APP = None


@pytest.fixture(scope="module", autouse=True)
def qt_app():
    global _APP
    if _APP is None:
        _APP = QApplication.instance() or QApplication([])
    return _APP


# ── Minimal AppContext mock ───────────────────────────────────────────────────

def _make_ctx(project_dir: str | None = None, db: sqlite3.Connection | None = None):
    """Return a lightweight mock AppContext."""
    ctx = MagicMock()
    ctx.has_project = project_dir is not None
    ctx.current_project_dir = project_dir
    ctx.get_db.return_value = db
    ctx.settings = MagicMock()
    return ctx


def _make_db(path: str) -> sqlite3.Connection:
    """Open an in-memory (or tmp-file) SQLite DB with the minimum schema."""
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS specimens (
            uid TEXT PRIMARY KEY,
            id TEXT, province TEXT, site TEXT, station TEXT,
            storage TEXT, collection_date TEXT, photo_date TEXT,
            scientific_name TEXT, scientific_name_cn TEXT,
            taxon_group TEXT, taxon_group_cn TEXT,
            order_name TEXT, order_cn TEXT,
            family TEXT, family_cn TEXT, genus TEXT, genus_cn TEXT,
            lon REAL, lat REAL, geo_area TEXT,
            collector TEXT, photographer TEXT, identifier TEXT,
            notes TEXT, photo_notes TEXT, angle TEXT,
            metadata INTEGER DEFAULT 0, pinned INTEGER DEFAULT 0,
            owner_project_dir TEXT, raw_json TEXT
        );
        CREATE TABLE IF NOT EXISTS tasks (
            uid TEXT PRIMARY KEY,
            is_active INTEGER DEFAULT 0,
            activated_at TEXT,
            last_organized_at TEXT,
            next_result_sequence_hint INTEGER,
            raw_json TEXT
        );
        CREATE TABLE IF NOT EXISTS grouping (
            uid TEXT, group_index INTEGER,
            angle_label TEXT, jpg_paths TEXT, composed_tiff_path TEXT,
            status TEXT, source TEXT, created_at TEXT, updated_at TEXT,
            result_sequence INTEGER, archive_zip TEXT,
            retired_tiff_paths TEXT, raw_json TEXT,
            PRIMARY KEY (uid, group_index)
        );
        CREATE TABLE IF NOT EXISTS explicit_unassigns (
            path TEXT PRIMARY KEY,
            created_at TEXT
        );
        CREATE TABLE IF NOT EXISTS seen_files (
            name TEXT PRIMARY KEY,
            first_seen_at TEXT
        );
    """)
    conn.commit()
    return conn


# ── Import smoke tests ────────────────────────────────────────────────────────

class TestImports:
    def test_import_specimen_sidebar(self):
        from app.widgets.specimen_sidebar import SpecimenSidebar
        assert SpecimenSidebar is not None

    def test_import_monitor_panel(self):
        from app.widgets.monitor_panel import MonitorPanel
        assert MonitorPanel is not None

    def test_import_grouping_panel(self):
        from app.widgets.grouping_panel import GroupingPanel
        assert GroupingPanel is not None

    def test_import_naming_panel(self):
        from app.widgets.naming_panel import NamingPanel
        assert NamingPanel is not None

    def test_import_metadata_panel(self):
        from app.widgets.metadata_panel import MetadataPanel
        assert MetadataPanel is not None

    def test_import_workbench_view(self):
        from app.views.workbench_view import WorkbenchView
        assert WorkbenchView is not None


# ── Construction smoke tests ──────────────────────────────────────────────────

class TestConstruction:
    def test_specimen_sidebar_constructs(self):
        from app.widgets.specimen_sidebar import SpecimenSidebar
        ctx = _make_ctx()
        w = SpecimenSidebar(ctx)
        assert w is not None

    def test_monitor_panel_constructs(self):
        from app.widgets.monitor_panel import MonitorPanel
        ctx = _make_ctx()
        w = MonitorPanel(ctx)
        assert w is not None

    def test_grouping_panel_constructs(self):
        from app.widgets.grouping_panel import GroupingPanel
        ctx = _make_ctx()
        w = GroupingPanel(ctx)
        assert w is not None

    def test_naming_panel_constructs(self):
        from app.widgets.naming_panel import NamingPanel
        ctx = _make_ctx()
        w = NamingPanel(ctx)
        assert w is not None

    def test_metadata_panel_constructs(self):
        from app.widgets.metadata_panel import MetadataPanel
        ctx = _make_ctx()
        w = MetadataPanel(ctx)
        assert w is not None

    def test_workbench_view_constructs(self):
        from app.views.workbench_view import WorkbenchView
        ctx = _make_ctx()
        w = WorkbenchView(ctx)
        assert w is not None
        assert w.view_id == "workbench"
        assert w.nav_title == "工作台"
        assert w.nav_icon == "🔬"


# ── on_activate smoke tests ───────────────────────────────────────────────────

class TestOnActivate:
    def test_on_activate_no_project(self):
        """on_activate must not crash when no project is loaded."""
        from app.views.workbench_view import WorkbenchView
        ctx = _make_ctx(project_dir=None)
        w = WorkbenchView(ctx)
        w.on_activate()  # must not raise

    def test_on_activate_with_project(self, tmp_path):
        """on_activate must not crash with a valid (but empty) project."""
        from app.views.workbench_view import WorkbenchView
        project_dir = str(tmp_path / "proj")
        Path(project_dir).mkdir(parents=True)
        (Path(project_dir) / "incoming-jpg").mkdir()
        (Path(project_dir) / "results").mkdir()
        (Path(project_dir) / "_data").mkdir()

        db_path = str(tmp_path / "proj" / "_data" / "project.db")
        db = _make_db(db_path)

        ctx = _make_ctx(project_dir=project_dir, db=db)
        w = WorkbenchView(ctx)
        w.on_activate()  # must not raise
        db.close()


# ── NamingPanel live-preview ──────────────────────────────────────────────────

class TestNamingPanel:
    def test_live_preview_uid(self):
        from app.widgets.naming_panel import NamingPanel
        ctx = _make_ctx()
        w = NamingPanel(ctx)
        w._province.setText("FJ")
        w._site.setText("XM")
        w._station.setText("B2")
        w._species_id.setText("DLC001")
        w._storage.setText("T95E")
        w._collection_date.setText("20260601")
        w._update_preview()
        uid = w.current_uid()
        assert uid.startswith("FJ-XM-B2-DLC001")
        assert "T95E" in uid

    def test_live_preview_result_id_has_seq(self):
        from app.widgets.naming_panel import NamingPanel
        ctx = _make_ctx()
        w = NamingPanel(ctx)
        w._province.setText("FJ")
        w._site.setText("XM")
        w._station.setText("B2")
        w._species_id.setText("DLC001")
        w._storage.setText("T95E")
        w._collection_date.setText("20260601")
        w._seq.setValue(3)
        w._update_preview()
        rid = w.current_result_id()
        # Must include the sequence number
        assert "-3-" in rid

    def test_rna_warning_shown_for_r_prefix(self):
        """R-prefix storage: warning label must NOT be explicitly hidden."""
        from app.widgets.naming_panel import NamingPanel
        ctx = _make_ctx()
        w = NamingPanel(ctx)
        w._storage.setText("RD75E")
        w._update_preview()
        # In offscreen mode the widget is not shown(), so isVisible() is always
        # False.  We verify instead that the label is not explicitly hidden
        # (i.e. show() was called — the label's own hide/show state).
        assert not w._rna_warning.isHidden()

    def test_rna_warning_hidden_for_non_r_prefix(self):
        from app.widgets.naming_panel import NamingPanel
        ctx = _make_ctx()
        w = NamingPanel(ctx)
        w._storage.setText("T95E")
        w._update_preview()
        assert w._rna_warning.isHidden()


# ── SpecimenSidebar ───────────────────────────────────────────────────────────

class TestSpecimenSidebar:
    def test_refresh_no_project(self):
        from app.widgets.specimen_sidebar import SpecimenSidebar
        ctx = _make_ctx(project_dir=None, db=None)
        w = SpecimenSidebar(ctx)
        w.refresh()  # must not crash; list should be empty
        assert w._list.count() == 0

    def test_refresh_empty_db(self, tmp_path):
        from app.widgets.specimen_sidebar import SpecimenSidebar
        db_path = str(tmp_path / "project.db")
        db = _make_db(db_path)
        ctx = _make_ctx(project_dir=str(tmp_path), db=db)
        w = SpecimenSidebar(ctx)
        w.refresh()
        assert w._list.count() == 0
        db.close()

    def test_refresh_with_specimens(self, tmp_path):
        from app.widgets.specimen_sidebar import SpecimenSidebar
        project_dir = str(tmp_path)
        db_path = str(tmp_path / "project.db")
        db = _make_db(db_path)
        db.execute(
            "INSERT INTO specimens (uid, owner_project_dir) VALUES (?, ?)",
            ("FJ-XM-B2-DLC001-T95E-20260601", project_dir),
        )
        db.commit()
        ctx = _make_ctx(project_dir=project_dir, db=db)
        w = SpecimenSidebar(ctx)
        w.refresh()
        assert w._list.count() == 1
        db.close()


# ── GroupingPanel ─────────────────────────────────────────────────────────────

class TestGroupingPanel:
    def test_clear_is_idempotent(self):
        from app.widgets.grouping_panel import GroupingPanel
        ctx = _make_ctx()
        w = GroupingPanel(ctx)
        w.clear()
        w.clear()

    def test_load_grouping_with_draft(self):
        from app.widgets.grouping_panel import GroupingPanel
        from app.services.grouping_service import Group, SpecimenGrouping
        ctx = _make_ctx()
        w = GroupingPanel(ctx)
        sg = SpecimenGrouping(
            uid="FJ-XM-B2-DLC001-T95E-20260601",
            groups=[Group(group_index=0, angle_label="正面", jpg_paths=[])],
        )
        w.load_grouping("FJ-XM-B2-DLC001-T95E-20260601", sg)

    def test_load_grouping_with_composed(self):
        from app.widgets.grouping_panel import GroupingPanel
        from app.services.grouping_service import Group, SpecimenGrouping
        ctx = _make_ctx()
        w = GroupingPanel(ctx)
        sg = SpecimenGrouping(
            uid="FJ-XM-B2-DLC001-T95E-20260601",
            groups=[
                Group(
                    group_index=0,
                    angle_label="正面",
                    jpg_paths=["/fake/a.jpg", "/fake/b.jpg"],
                    composed_tiff_path="/fake/result.tif",
                )
            ],
        )
        w.load_grouping("FJ-XM-B2-DLC001-T95E-20260601", sg)


# ── MetadataPanel ─────────────────────────────────────────────────────────────

class TestMetadataPanel:
    def test_clear_is_idempotent(self):
        from app.widgets.metadata_panel import MetadataPanel
        ctx = _make_ctx()
        w = MetadataPanel(ctx)
        w.clear()
        w.clear()

    def test_load_specimen(self):
        from app.widgets.metadata_panel import MetadataPanel
        from app.models.specimen import Specimen
        ctx = _make_ctx()
        w = MetadataPanel(ctx)
        sp = Specimen(
            uid="FJ-XM-B2-DLC001-T95E-20260601",
            collector="张三",
            scientific_name="Conus textile",
            storage="T95E",
        )
        w.load_specimen(sp)
        assert w._collector.text() == "张三"
        assert w._scientific_name.text() == "Conus textile"


# ── MonitorPanel ──────────────────────────────────────────────────────────────

class TestMonitorPanel:
    def test_clear_is_idempotent(self):
        from app.widgets.monitor_panel import MonitorPanel
        ctx = _make_ctx()
        w = MonitorPanel(ctx)
        w.clear()
        w.clear()

    def test_load_scan_empty(self):
        from app.widgets.monitor_panel import MonitorPanel
        from app.services.monitor_service import ScanResult
        ctx = _make_ctx()
        w = MonitorPanel(ctx)
        result = ScanResult(project_dir="/fake")
        w.load_scan(result)

    def test_load_scan_with_files(self):
        from app.widgets.monitor_panel import MonitorPanel
        from app.services.monitor_service import FileEntry, ScanResult
        ctx = _make_ctx()
        w = MonitorPanel(ctx)
        entries = [
            FileEntry(
                name="IMG_001.jpg",
                path="/fake/IMG_001.jpg",
                kind="jpg",
                size=1000,
                mtime="2026-06-01T00:00:00+00:00",
                attributed_specimen_id="FJ-XM-B2-DLC001-T95E-20260601",
            ),
            FileEntry(
                name="FJ-XM-B2-DLC001-1-T95E-20260601.tif",
                path="/fake/result.tif",
                kind="tiff",
                size=5000000,
                mtime="2026-06-01T01:00:00+00:00",
            ),
        ]
        result = ScanResult(
            project_dir="/fake",
            jpg_files=[entries[0]],
            tiff_files=[entries[1]],
        )
        w.load_scan(result)
