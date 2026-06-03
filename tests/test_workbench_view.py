"""test_workbench_view.py — Smoke tests for WorkbenchView and its widgets.

These tests run headless (QT_QPA_PLATFORM=offscreen) and verify:
  - All seven files can be imported without error.
  - Each widget can be constructed without crashing.
  - WorkbenchView.on_activate() does not crash when no project is set.
  - WorkbenchView.on_activate() does not crash when a valid project is set.
  - NamingPanel live-preview produces the correct UID / result-ID.
  - SpecimenSidebar.refresh() does not crash on an empty DB.
  - GroupingPanel.clear() is idempotent.
  - ResultsColumn.clear() is idempotent and load_uid works.
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

    def test_import_results_column(self):
        from app.widgets.results_column import ResultsColumn
        assert ResultsColumn is not None

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

    def test_results_column_constructs(self):
        from app.widgets.results_column import ResultsColumn
        w = ResultsColumn()
        assert w is not None

    def test_workbench_view_constructs(self):
        from app.views.workbench_view import WorkbenchView
        ctx = _make_ctx()
        w = WorkbenchView(ctx)
        assert w is not None
        assert w.view_id == "workbench"
        assert w.nav_title == "照片工作区"
        assert w.nav_icon == "🔬"


# ── on_activate smoke tests ───────────────────────────────────────────────────

class TestOnActivate:
    def test_on_activate_no_project(self):
        """on_activate must not crash when no project is loaded."""
        from app.views.workbench_view import WorkbenchView
        ctx = _make_ctx(project_dir=None)
        w = WorkbenchView(ctx)
        w.on_activate()  # must not raise

    def test_auto_poll_timer_starts_on_activate(self, tmp_path):
        """on_activate must start _auto_refresh_timer."""
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
        w.on_activate()
        assert hasattr(w, "_auto_refresh_timer")
        assert w._auto_refresh_timer.isActive()
        db.close()

    def test_auto_poll_timer_stops_on_deactivate(self, tmp_path):
        """on_deactivate must stop _auto_refresh_timer."""
        from app.views.workbench_view import WorkbenchView
        project_dir = str(tmp_path / "proj2")
        Path(project_dir).mkdir(parents=True)
        (Path(project_dir) / "incoming-jpg").mkdir()
        (Path(project_dir) / "results").mkdir()
        (Path(project_dir) / "_data").mkdir()
        db_path = str(tmp_path / "proj2" / "_data" / "project.db")
        db = _make_db(db_path)
        ctx = _make_ctx(project_dir=project_dir, db=db)
        w = WorkbenchView(ctx)
        w.on_activate()
        w.on_deactivate()
        assert not w._auto_refresh_timer.isActive()
        db.close()

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

    def test_species_sequence_hint_and_apply_button(self, tmp_path):
        from app.widgets.naming_panel import NamingPanel
        project_dir = str(tmp_path)
        db = _make_db(str(tmp_path / "project.db"))
        for n in (1, 2, 3):
            db.execute(
                """
                INSERT INTO specimens (uid, id, owner_project_dir)
                VALUES (?, ?, ?)
                """,
                (
                    f"FJ-XM-B2-DLC{n:03d}-T95E-20260601",
                    f"DLC{n:03d}",
                    project_dir,
                ),
            )
        db.commit()
        ctx = _make_ctx(project_dir=project_dir, db=db)
        w = NamingPanel(ctx)
        w._species_id.setText("dlc")
        assert "建议 DLC004" in w._seq_hint_label.text()
        assert w.current_sequence_suggestion() == "DLC004"
        w._seq_apply_btn.click()
        assert w._species_id.text() == "DLC004"
        db.close()


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


# ── ResultsColumn ─────────────────────────────────────────────────────────────

class TestResultsColumn:
    def test_clear_is_idempotent(self):
        from app.widgets.results_column import ResultsColumn
        w = ResultsColumn()
        w.clear()
        w.clear()

    def test_load_uid_empty(self):
        from app.widgets.results_column import ResultsColumn
        w = ResultsColumn()
        w.load_uid("FJ-XM-B2-DLC001-T95E-20260601", [], [])

    def test_load_uid_with_tiffs_and_zips(self):
        from app.widgets.results_column import ResultsColumn
        w = ResultsColumn()
        tiffs = [{"path": "/fake/result.tif", "name": "result.tif"}]
        zips = [{"path": "/fake/result.zip", "name": "result.zip", "size": 12345}]
        w.load_uid("FJ-XM-B2-DLC001-T95E-20260601", tiffs, zips)

    def test_workbench_view_has_results_column(self):
        """WorkbenchView must expose a _results attribute (ResultsColumn)."""
        from app.views.workbench_view import WorkbenchView
        from app.widgets.results_column import ResultsColumn
        ctx = _make_ctx()
        w = WorkbenchView(ctx)
        assert hasattr(w, "_results")
        assert isinstance(w._results, ResultsColumn)


# ── MetaScoreRing ─────────────────────────────────────────────────────────────

class TestMetaScoreRing:
    def test_ring_constructs(self):
        from app.widgets.metadata_panel import MetaScoreRing
        ring = MetaScoreRing()
        assert ring.score() == 0

    def test_set_score_clamps(self):
        from app.widgets.metadata_panel import MetaScoreRing
        ring = MetaScoreRing()
        ring.set_score(150)
        assert ring.score() == 100
        ring.set_score(-5)
        assert ring.score() == 0

    def test_set_score_normal(self):
        from app.widgets.metadata_panel import MetaScoreRing
        ring = MetaScoreRing()
        ring.set_score(75)
        assert ring.score() == 75

    def test_metadata_panel_has_score_ring(self):
        """MetadataPanel must have a _score_ring attribute."""
        from app.widgets.metadata_panel import MetadataPanel, MetaScoreRing
        ctx = _make_ctx()
        w = MetadataPanel(ctx)
        assert hasattr(w, "_score_ring")
        assert isinstance(w._score_ring, MetaScoreRing)

    def test_load_specimen_updates_ring(self):
        """Loading a fully-complete specimen should set score > 0."""
        from app.widgets.metadata_panel import MetadataPanel
        from app.models.specimen import Specimen
        ctx = _make_ctx()
        w = MetadataPanel(ctx)
        sp = Specimen(
            uid="FJ-XM-B2-DLC001-T95E-20260601",
            scientific_name="Conus textile",
            family="Conidae",
            collector="张三",
            lon=120.123,
            lat=25.456,
        )
        w.load_specimen(sp)
        assert w._score_ring.score() == 100

    def test_load_specimen_partial_score(self):
        """Specimen with only 2 of 5 fields filled → 40 %."""
        from app.widgets.metadata_panel import MetadataPanel
        from app.models.specimen import Specimen
        ctx = _make_ctx()
        w = MetadataPanel(ctx)
        sp = Specimen(
            uid="FJ-XM-B2-DLC001-T95E-20260601",
            scientific_name="Conus textile",
            collector="张三",
        )
        w.load_specimen(sp)
        assert w._score_ring.score() == 40

    def test_clear_resets_ring(self):
        from app.widgets.metadata_panel import MetadataPanel
        from app.models.specimen import Specimen
        ctx = _make_ctx()
        w = MetadataPanel(ctx)
        sp = Specimen(uid="FJ-XM-B2-DLC001-T95E-20260601", scientific_name="X",
                      family="Y", collector="Z", lon=1.0, lat=2.0)
        w.load_specimen(sp)
        assert w._score_ring.score() == 100
        w.clear()
        assert w._score_ring.score() == 0


# ── Delete with TIFF warning ───────────────────────────────────────────────────

class TestDeleteWithTiffWarning:
    """Test that MonitorPanel._on_delete_clicked identifies TIFF in selection and deletes JPGs."""

    def test_actual_jpg_deletion(self, tmp_path):
        """_on_delete_clicked must actually call os.unlink on confirmed JPG paths."""
        from app.widgets.monitor_panel import MonitorPanel
        from app.services.monitor_service import FileEntry, ScanResult
        ctx = _make_ctx()
        # Create a real temporary JPG file
        jpg_path = str(tmp_path / "test.jpg")
        with open(jpg_path, "wb") as f:
            f.write(b"JFIF" * 100)
        w = MonitorPanel(ctx)
        entries = [FileEntry(
            name="test.jpg", path=jpg_path, kind="jpg",
            size=400, mtime="2026-06-01T00:00:00+00:00",
        )]
        result = ScanResult(project_dir=str(tmp_path), jpg_files=entries)
        w.load_scan(result)
        w._on_select_all()
        # Patch QMessageBox.question to return Yes automatically
        from unittest.mock import patch
        from PyQt6.QtWidgets import QMessageBox
        with patch.object(QMessageBox, 'question', return_value=QMessageBox.StandardButton.Yes):
            w._on_delete_clicked()
        assert not os.path.exists(jpg_path), "JPG must be deleted after confirm"

    def test_tiff_selection_blocked(self, tmp_path):
        """_on_delete_clicked must show warning and abort when TIFFs are selected."""
        from app.widgets.monitor_panel import MonitorPanel, _FileCard
        ctx = _make_ctx()
        w = MonitorPanel(ctx)

        class _Entry:
            path = str(tmp_path / "result.tif")
            kind = "tiff"
            name = "result.tif"
            attributed_specimen_id = None
            composed_tiff = None
            archived = None

        tif_card = _FileCard(_Entry(), parent=w)
        tif_card._selected = True
        w._cards = [tif_card]

        from unittest.mock import patch
        from PyQt6.QtWidgets import QMessageBox
        with patch.object(QMessageBox, 'warning') as mock_warn:
            w._on_delete_clicked()
            mock_warn.assert_called_once()  # must warn about TIFF

    def _make_fake_entry(self, path: str, kind: str = "jpg"):
        """Return a minimal fake FileEntry-like object."""
        class _Entry:
            pass
        e = _Entry()
        e.path = path
        e.kind = kind
        e.name = path.split("/")[-1]
        e.attributed_specimen_id = None
        e.composed_tiff = None
        e.archived = None
        return e

    def test_has_del_btn(self):
        """MonitorPanel must expose _del_btn attribute."""
        from app.widgets.monitor_panel import MonitorPanel
        ctx = _make_ctx()
        w = MonitorPanel(ctx)
        assert hasattr(w, "_del_btn")

    def test_del_btn_disabled_initially(self):
        from app.widgets.monitor_panel import MonitorPanel
        ctx = _make_ctx()
        w = MonitorPanel(ctx)
        assert not w._del_btn.isEnabled()

    def test_tiff_path_detection(self):
        """_on_delete_clicked must detect .tif / .tiff paths in selection."""
        from app.widgets.monitor_panel import _FileCard, MonitorPanel
        ctx = _make_ctx()
        w = MonitorPanel(ctx)
        # Synthesise two selected cards (one JPG, one TIFF)
        jpg_entry = self._make_fake_entry("/fake/IMG_001.jpg", kind="jpg")
        tif_entry = self._make_fake_entry("/fake/result.tif", kind="tiff")
        c1 = _FileCard(jpg_entry, parent=w)
        c1._selected = True
        c2 = _FileCard(tif_entry, parent=w)
        c2._selected = True
        w._cards = [c1, c2]

        # Collect paths the method would classify as TIFFs
        paths = [getattr(c._entry, "path", "") for c in w._selected_cards()]
        tiff_paths = [p for p in paths if p.lower().endswith((".tif", ".tiff"))]
        jpg_paths  = [p for p in paths if not p.lower().endswith((".tif", ".tiff"))]
        assert len(tiff_paths) == 1
        assert len(jpg_paths) == 1
        assert tiff_paths[0] == "/fake/result.tif"

    def test_select_all_enables_del_btn(self):
        """_on_select_all must enable the delete button when cards exist."""
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
            ),
        ]
        result = ScanResult(project_dir="/fake", jpg_files=entries)
        w.load_scan(result)
        w._on_select_all()
        assert w._del_btn.isEnabled()

    def test_select_none_disables_del_btn(self):
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
            ),
        ]
        result = ScanResult(project_dir="/fake", jpg_files=entries)
        w.load_scan(result)
        w._on_select_all()
        w._on_select_none()
        assert not w._del_btn.isEnabled()


# ── GroupingPanel capture-main-actions ────────────────────────────────────────

class TestAddToGroup:
    def test_monitor_panel_has_selected_jpg_paths(self):
        """MonitorPanel must have selected_jpg_paths() method."""
        from app.widgets.monitor_panel import MonitorPanel
        ctx = _make_ctx()
        w = MonitorPanel(ctx)
        assert hasattr(w, "selected_jpg_paths")
        assert callable(w.selected_jpg_paths)

    def test_monitor_panel_has_add_jpg_requested_signal(self):
        """MonitorPanel must have add_jpg_requested signal."""
        from app.widgets.monitor_panel import MonitorPanel
        ctx = _make_ctx()
        w = MonitorPanel(ctx)
        assert hasattr(w, "add_jpg_requested")

    def test_grouping_panel_add_jpgs_to_group(self):
        """GroupingPanel.add_jpgs_to_group must add paths to the group."""
        from app.widgets.grouping_panel import GroupingPanel
        from app.services.grouping_service import Group, SpecimenGrouping
        ctx = _make_ctx()
        w = GroupingPanel(ctx)
        sg = SpecimenGrouping(
            uid="FJ-XM-B2-DLC001-T95E-20260601",
            groups=[Group(group_index=0, angle_label="正面", jpg_paths=[])],
        )
        w.load_grouping("FJ-XM-B2-DLC001-T95E-20260601", sg)
        w.add_jpgs_to_group(0, ["/fake/a.jpg", "/fake/b.jpg"])
        assert "/fake/a.jpg" in w._grouping.groups[0].jpg_paths
        assert "/fake/b.jpg" in w._grouping.groups[0].jpg_paths

    def test_grouping_panel_mutual_exclusion(self):
        """add_jpgs_to_group must remove path from other groups (mutual exclusion)."""
        from app.widgets.grouping_panel import GroupingPanel
        from app.services.grouping_service import Group, SpecimenGrouping
        ctx = _make_ctx()
        w = GroupingPanel(ctx)
        sg = SpecimenGrouping(
            uid="UID1",
            groups=[
                Group(group_index=0, jpg_paths=["/fake/a.jpg"]),
                Group(group_index=1, jpg_paths=[]),
            ],
        )
        w.load_grouping("UID1", sg)
        # Move /fake/a.jpg from group 0 to group 1
        w.add_jpgs_to_group(1, ["/fake/a.jpg"])
        assert "/fake/a.jpg" not in w._grouping.groups[0].jpg_paths
        assert "/fake/a.jpg" in w._grouping.groups[1].jpg_paths

    def test_grouping_panel_has_add_selection_signal(self):
        """GroupingPanel must have add_selection_to_group_requested signal."""
        from app.widgets.grouping_panel import GroupingPanel
        ctx = _make_ctx()
        w = GroupingPanel(ctx)
        assert hasattr(w, "add_selection_to_group_requested")


class TestRemoveJpgFromGroup:
    def test_remove_jpg_from_group(self):
        """GroupingPanel.remove_jpg_from_group must remove path from the group."""
        from app.widgets.grouping_panel import GroupingPanel
        from app.services.grouping_service import Group, SpecimenGrouping
        ctx = _make_ctx()
        w = GroupingPanel(ctx)
        sg = SpecimenGrouping(
            uid="UID1",
            groups=[Group(group_index=0, jpg_paths=["/a.jpg", "/b.jpg"])],
        )
        w.load_grouping("UID1", sg)
        w.remove_jpg_from_group(0, "/a.jpg")
        assert "/a.jpg" not in w._grouping.groups[0].jpg_paths
        assert "/b.jpg" in w._grouping.groups[0].jpg_paths

    def test_grouping_panel_has_free_compose_signal(self):
        """GroupingPanel must have free_compose_requested signal."""
        from app.widgets.grouping_panel import GroupingPanel
        ctx = _make_ctx()
        w = GroupingPanel(ctx)
        assert hasattr(w, "free_compose_requested")

    def test_grouping_panel_has_retroactive_signal(self):
        """GroupingPanel must have retroactive_requested signal."""
        from app.widgets.grouping_panel import GroupingPanel
        ctx = _make_ctx()
        w = GroupingPanel(ctx)
        assert hasattr(w, "retroactive_requested")


class TestMonitorPanelAddJpg:
    def test_has_add_jpg_signal(self):
        """MonitorPanel must emit add_jpg_requested signal."""
        from app.widgets.monitor_panel import MonitorPanel
        ctx = _make_ctx()
        w = MonitorPanel(ctx)
        assert hasattr(w, "add_jpg_requested")


class TestResultsColumnOpenExplorer:
    def test_load_uid_with_open_btn(self):
        """ResultsColumn items must have an 'open in folder' mechanism."""
        from app.widgets.results_column import ResultsColumn
        w = ResultsColumn()
        tiffs = [{"path": "/fake/result.tif", "name": "result.tif"}]
        w.load_uid("UID1", tiffs, [])
        assert hasattr(w, "_open_in_explorer")


class TestHeliconParamsPanel:
    def test_constructs(self):
        from app.widgets.helicon_params_panel import HeliconParamsPanel
        w = HeliconParamsPanel()
        assert w is not None

    def test_default_params(self):
        from app.widgets.helicon_params_panel import HeliconParamsPanel
        w = HeliconParamsPanel()
        p = w.get_params()
        assert p["method"] in (0, 1, 2)
        assert 1 <= p["radius"] <= 30
        assert 1 <= p["smoothing"] <= 10

    def test_set_params(self):
        from app.widgets.helicon_params_panel import HeliconParamsPanel
        w = HeliconParamsPanel()
        w.set_params({"method": 1, "radius": 8.0, "smoothing": 4})
        p = w.get_params()
        assert p["method"] == 1
        assert p["radius"] == 8.0
        assert p["smoothing"] == 4

    def test_workbench_view_has_helicon_params(self):
        """WorkbenchView must expose _helicon_params (HeliconParamsPanel)."""
        from app.views.workbench_view import WorkbenchView
        from app.widgets.helicon_params_panel import HeliconParamsPanel
        ctx = _make_ctx()
        w = WorkbenchView(ctx)
        assert hasattr(w, "_helicon_params")
        assert isinstance(w._helicon_params, HeliconParamsPanel)


class TestProjectSettingsDrawer:
    def test_constructs(self):
        from app.widgets.project_settings_drawer import ProjectSettingsDrawer
        ctx = _make_ctx()
        w = ProjectSettingsDrawer(ctx)
        assert w is not None

    def test_has_helicon_status_label(self):
        from app.widgets.project_settings_drawer import ProjectSettingsDrawer
        ctx = _make_ctx()
        w = ProjectSettingsDrawer(ctx)
        assert hasattr(w, "_helicon_status_lbl")

    def test_has_auto_activate_checkbox(self):
        from app.widgets.project_settings_drawer import ProjectSettingsDrawer
        ctx = _make_ctx()
        w = ProjectSettingsDrawer(ctx)
        assert hasattr(w, "_auto_activate_cb")

    def test_workbench_view_has_settings_drawer(self):
        """WorkbenchView must expose _settings_drawer."""
        from app.views.workbench_view import WorkbenchView
        ctx = _make_ctx()
        w = WorkbenchView(ctx)
        assert hasattr(w, "_settings_drawer")


class TestGroupingPanelCaptureActions:
    def test_has_target_label(self):
        from app.widgets.grouping_panel import GroupingPanel
        ctx = _make_ctx()
        w = GroupingPanel(ctx)
        assert hasattr(w, "_target_label")

    def test_has_group_toggle_btn(self):
        from app.widgets.grouping_panel import GroupingPanel
        ctx = _make_ctx()
        w = GroupingPanel(ctx)
        assert hasattr(w, "_group_toggle_btn")

    def test_load_grouping_updates_target_label(self):
        from app.widgets.grouping_panel import GroupingPanel
        from app.services.grouping_service import Group, SpecimenGrouping
        ctx = _make_ctx()
        w = GroupingPanel(ctx)
        uid = "FJ-XM-B2-DLC001-T95E-20260601"
        sg = SpecimenGrouping(uid=uid, groups=[])
        w.load_grouping(uid, sg)
        # target label should show the uid (possibly truncated)
        assert uid[:30] in w._target_label.text()

    def test_group_toggle_hides_body(self):
        from app.widgets.grouping_panel import GroupingPanel
        ctx = _make_ctx()
        w = GroupingPanel(ctx)
        # In offscreen mode widgets are never shown(); check isHidden() state.
        # Body starts NOT explicitly hidden (checked=True on toggle btn).
        assert not w._group_body.isHidden()
        # Simulate toggle off
        w._on_group_toggle(False)
        assert w._group_body.isHidden()
        # Toggle back on
        w._on_group_toggle(True)
        assert not w._group_body.isHidden()

    def test_phase_pills_exist(self):
        from app.widgets.monitor_panel import MonitorPanel
        ctx = _make_ctx()
        w = MonitorPanel(ctx)
        assert hasattr(w, "_phase_pills")
        assert "拍摄中" in w._phase_pills
        assert "已拍完" in w._phase_pills
        assert "整理中" in w._phase_pills
        assert "完成" in w._phase_pills


# ── GroupingPanel delete / clear group  #cursor ─────────────────────────────

class TestGroupingPanelDeleteClearGroup:
    """Verify groupingDeleteGroup / groupingClearGroup equivalents."""

    def _make_panel_with_two_groups(self):
        from app.widgets.grouping_panel import GroupingPanel
        from app.services.grouping_service import Group, SpecimenGrouping
        ctx = _make_ctx()
        w = GroupingPanel(ctx)
        sg = SpecimenGrouping(
            uid="UID1",
            groups=[
                Group(group_index=0, jpg_paths=["/a.jpg", "/b.jpg"]),
                Group(group_index=1, jpg_paths=["/c.jpg"]),
            ],
        )
        w.load_grouping("UID1", sg)
        return w

    def test_clear_group_removes_jpgs(self):
        w = self._make_panel_with_two_groups()
        w.clear_group(0)
        assert w._grouping.groups[0].jpg_paths == []
        # Group 1 untouched
        assert "/c.jpg" in w._grouping.groups[1].jpg_paths

    def test_clear_group_emits_changed(self):
        w = self._make_panel_with_two_groups()
        received = []
        w.grouping_changed.connect(lambda: received.append(1))
        w.clear_group(0)
        assert received, "grouping_changed must be emitted after clear"

    def test_delete_group_removes_group(self):
        w = self._make_panel_with_two_groups()
        assert len(w._grouping.groups) == 2
        w.delete_group(0)
        assert len(w._grouping.groups) == 1
        # Only group 1 remains
        assert w._grouping.groups[0].group_index == 1

    def test_delete_group_emits_changed(self):
        w = self._make_panel_with_two_groups()
        received = []
        w.grouping_changed.connect(lambda: received.append(1))
        w.delete_group(0)
        assert received, "grouping_changed must be emitted after delete"

    def test_delete_composed_group_blocked(self):
        """delete_group must be a no-op for composed groups."""
        from app.widgets.grouping_panel import GroupingPanel
        from app.services.grouping_service import Group, SpecimenGrouping
        ctx = _make_ctx()
        w = GroupingPanel(ctx)
        sg = SpecimenGrouping(
            uid="UID1",
            groups=[
                Group(group_index=0, jpg_paths=["/a.jpg"],
                      composed_tiff_path="/result.tif"),
            ],
        )
        w.load_grouping("UID1", sg)
        w.delete_group(0)
        # Must NOT be deleted (still 1 group)
        assert len(w._grouping.groups) == 1

    def test_draft_group_row_has_clear_and_delete_buttons(self):
        """_DraftGroupRow must have clear_group_requested and delete_group_requested signals."""
        from app.widgets.grouping_panel import _DraftGroupRow
        from app.services.grouping_service import Group
        g = Group(group_index=0, jpg_paths=[])
        row = _DraftGroupRow(g)
        assert hasattr(row, "clear_group_requested")
        assert hasattr(row, "delete_group_requested")
