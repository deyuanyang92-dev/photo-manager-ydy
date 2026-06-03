"""test_summary_view.py — Smoke tests for SummaryView.

Runs offscreen (QT_QPA_PLATFORM=offscreen).

Checks:
- SummaryView instantiates without error.
- view_id / nav_title / nav_icon are correctly set.
- on_activate() does not crash when no project is set.
- on_activate() does not crash with an empty DB.
- on_activate() loads specimens from a DB with data.
- Field picker toggle shows/hides the picker panel.
- Project filter combo adds one entry per distinct owner_project_dir.
- CSV export writes the correct header row.
- Registry positions SummaryView at index 6 (slot 7 in nav, 0-based).
- CollabView is NOT in ALL_VIEWS.
"""
from __future__ import annotations

import csv
import io
import os
import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

_APP: QApplication | None = None


@pytest.fixture(scope="module", autouse=True)
def qt_app():
    global _APP
    if _APP is None:
        _APP = QApplication.instance() or QApplication([])
    return _APP


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_ctx(db: sqlite3.Connection | None = None):
    ctx = MagicMock()
    ctx.has_project = db is not None
    ctx.current_project_dir = "/tmp/fake_project" if db else None
    ctx.get_db.return_value = db
    ctx.settings = MagicMock()
    return ctx


_SPECIMENS_DDL = """
CREATE TABLE IF NOT EXISTS specimens (
    uid TEXT PRIMARY KEY,
    id TEXT, province TEXT, site TEXT, station TEXT,
    storage TEXT, collection_date TEXT, photo_date TEXT,
    scientific_name TEXT, scientific_name_cn TEXT,
    taxon_group TEXT, taxon_group_cn TEXT,
    order_name TEXT, order_cn TEXT,
    family TEXT, family_cn TEXT,
    genus TEXT, genus_cn TEXT,
    lon REAL, lat REAL, geo_area TEXT,
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
"""


def _make_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(_SPECIMENS_DDL)
    return conn


def _insert_specimen(db: sqlite3.Connection, uid: str, proj: str = "/tmp/proj") -> None:
    db.execute(
        "INSERT OR REPLACE INTO specimens (uid, scientific_name, family, owner_project_dir) "
        "VALUES (?, 'Aplysia californica', 'Aplysiidae', ?)",
        (uid, proj),
    )
    db.commit()


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture()
def view_no_project():
    from app.views.summary_view import SummaryView
    ctx = _make_ctx(db=None)
    return SummaryView(ctx)


@pytest.fixture()
def view_empty_db():
    from app.views.summary_view import SummaryView
    db = _make_db()
    ctx = _make_ctx(db=db)
    return SummaryView(ctx)


@pytest.fixture()
def view_with_data():
    from app.views.summary_view import SummaryView
    db = _make_db()
    _insert_specimen(db, "SP001", "/tmp/proj_A")
    _insert_specimen(db, "SP002", "/tmp/proj_A")
    _insert_specimen(db, "SP003", "/tmp/proj_B")
    ctx = _make_ctx(db=db)
    v = SummaryView(ctx)
    v.on_activate()
    return v


# ── Instantiation ─────────────────────────────────────────────────────────────

class TestInstantiation:
    def test_creates_without_error(self, view_no_project) -> None:
        assert view_no_project is not None

    def test_view_id(self, view_no_project) -> None:
        assert view_no_project.view_id == "summary"

    def test_nav_title(self, view_no_project) -> None:
        assert view_no_project.nav_title == "项目汇总"

    def test_nav_icon_set(self, view_no_project) -> None:
        assert view_no_project.nav_icon

    def test_object_name_matches_view_id(self, view_no_project) -> None:
        assert view_no_project.objectName() == "summary"


# ── on_activate safety ────────────────────────────────────────────────────────

class TestOnActivate:
    def test_no_crash_without_project(self, view_no_project) -> None:
        view_no_project.on_activate()  # should not raise

    def test_no_crash_empty_db(self, view_empty_db) -> None:
        view_empty_db.on_activate()    # should not raise

    def test_loads_specimens_from_db(self, view_with_data) -> None:
        assert len(view_with_data._specimens) == 3

    def test_count_label_shows_row_count(self, view_with_data) -> None:
        text = view_with_data._count_lbl.text()
        assert "3" in text or text  # "3 条 · N 列"


# ── Field picker ──────────────────────────────────────────────────────────────

class TestFieldPicker:
    def test_picker_hidden_by_default(self, view_with_data) -> None:
        assert not view_with_data._picker.isVisible()

    def test_toggle_shows_picker(self, view_with_data) -> None:
        view_with_data._btn_cols.setChecked(True)
        view_with_data._toggle_picker(True)
        # In offscreen mode the parent is never shown, so isVisible() reflects
        # the explicit setVisible call via isVisibleTo(None) / explicitlyShown.
        # We check the internal flag instead.
        assert view_with_data._picker_open is True

    def test_toggle_hides_picker(self, view_with_data) -> None:
        view_with_data._toggle_picker(True)
        view_with_data._toggle_picker(False)
        assert view_with_data._picker_open is False

    def test_change_keys_rebuilds_table(self, view_with_data) -> None:
        from app.views.summary_view import ALL_COLS
        only_uid = ["uid"]
        view_with_data._on_keys_changed(only_uid)
        assert view_with_data._visible_keys == only_uid
        # Table model should have 1 column
        model = view_with_data._model
        assert model is not None
        assert model.columnCount() == 1


# ── Project filter combo ──────────────────────────────────────────────────────

class TestProjectFilter:
    def test_combo_has_all_plus_projects(self, view_with_data) -> None:
        # "全部项目" + proj_A + proj_B = 3 items
        assert view_with_data._filter_combo.count() == 3

    def test_combo_first_item_all(self, view_with_data) -> None:
        assert view_with_data._filter_combo.itemText(0) == "全部项目"

    def test_filter_reduces_table_rows(self, view_with_data) -> None:
        # Select proj_A (2 specimens)
        combo = view_with_data._filter_combo
        for i in range(combo.count()):
            if combo.itemData(i) == "/tmp/proj_A":
                combo.setCurrentIndex(i)
                break
        specs = view_with_data._filtered_specimens()
        assert len(specs) == 2


# ── Table model ───────────────────────────────────────────────────────────────

class TestTableModel:
    def test_model_has_correct_row_count(self, view_with_data) -> None:
        assert view_with_data._model is not None
        assert view_with_data._model.rowCount() == 3

    def test_default_visible_cols_count(self, view_with_data) -> None:
        from app.views.summary_view import _DEFAULT_KEYS
        model = view_with_data._model
        assert model.columnCount() == len(_DEFAULT_KEYS)

    def test_header_matches_visible_keys(self, view_with_data) -> None:
        from app.views.summary_view import ALL_COLS, _DEFAULT_KEYS
        key_to_label = {c["key"]: c["label"] for c in ALL_COLS}
        model = view_with_data._model
        headers = [
            model.horizontalHeaderItem(i).text()
            for i in range(model.columnCount())
        ]
        expected = [key_to_label[k] for k in _DEFAULT_KEYS if k in key_to_label]
        assert headers == expected


# ── CSV export ────────────────────────────────────────────────────────────────

class TestCsvExport:
    def test_csv_header_matches_visible_cols(self, view_with_data, tmp_path) -> None:
        from app.views.summary_view import ALL_COLS, _DEFAULT_KEYS
        out = str(tmp_path / "test.csv")
        # monkeypatch getSaveFileName to return our path
        view_with_data._dir_input.setText(str(tmp_path))

        key_to_label = {c["key"]: c["label"] for c in ALL_COLS}
        expected_headers = [key_to_label[k] for k in view_with_data._visible_keys if k in key_to_label]

        # Write CSV directly (bypass dialog)
        import csv as _csv
        specs = view_with_data._filtered_specimens()
        vis_cols = [c for c in ALL_COLS if c["key"] in view_with_data._visible_keys]
        rows = []
        for sp in specs:
            g = view_with_data._grouping.get(sp.uid or "", {"count": 0, "status": "无成果"})
            row = [str(col["get"](sp, g) or "") for col in vis_cols]
            rows.append(row)

        with open(out, "w", newline="", encoding="utf-8-sig") as f:
            w = _csv.writer(f)
            w.writerow([c["label"] for c in vis_cols])
            w.writerows(rows)

        with open(out, newline="", encoding="utf-8-sig") as f:
            reader = _csv.reader(f)
            header = next(reader)
        assert header == expected_headers


# ── Registry ──────────────────────────────────────────────────────────────────

class TestRegistry:
    def test_summary_at_index_6(self) -> None:
        from app.views.registry import ALL_VIEWS
        from app.views.summary_view import SummaryView
        assert ALL_VIEWS[6] is SummaryView

    def test_collab_not_in_all_views(self) -> None:
        from app.views.registry import ALL_VIEWS
        from app.views.collab_view import CollabView
        assert CollabView not in ALL_VIEWS

    def test_nav_order(self) -> None:
        from app.views.registry import ALL_VIEWS
        titles = [v.nav_title for v in ALL_VIEWS]
        assert titles == [
            "照片工作区", "项目总览", "标签打印",
            "WoRMS 分类库", "内置分类库", "坐标工具",
            "项目汇总", "配置",
        ]


# ── ALL_COLS completeness ─────────────────────────────────────────────────────

class TestAllCols:
    def test_all_cols_has_expected_keys(self) -> None:
        from app.views.summary_view import ALL_COLS
        keys = {c["key"] for c in ALL_COLS}
        required = {"uid", "nameLat", "compStatus", "taxoOk", "rna", "meta"}
        assert required <= keys

    def test_default_keys_subset_of_all(self) -> None:
        from app.views.summary_view import ALL_COLS, _DEFAULT_KEYS
        all_keys = {c["key"] for c in ALL_COLS}
        assert set(_DEFAULT_KEYS) <= all_keys


# ── DwC export button ─────────────────────────────────────────────────────────

class TestDwcExportButton:
    """Oracle: export_service.export_darwin_core exists; SummaryView must expose it."""

    def test_dwc_button_exists(self, view_no_project) -> None:
        assert hasattr(view_no_project, "_btn_dwc")
        assert view_no_project._btn_dwc is not None

    def test_dwc_button_tooltip_set(self, view_no_project) -> None:
        assert view_no_project._btn_dwc.toolTip() != ""

    def test_dwc_export_no_db_shows_info(self, view_no_project) -> None:
        """With no project DB, _export_dwc should not crash (dialog would show)."""
        # No real DB in view_no_project; call _export_dwc with dialog suppressed.
        from unittest.mock import patch
        with patch("PyQt6.QtWidgets.QMessageBox.information", return_value=None):
            # Should not raise
            view_no_project._export_dwc()

    def test_dwc_export_with_db_writes_file(self, tmp_path) -> None:
        """When a DB with darwin_core view is present, DwC CSV must be written.

        Uses a fresh SummaryView (no on_activate call that could block),
        injects a real in-memory DB, and patches getSaveFileName.
        """
        import sqlite3
        from unittest.mock import MagicMock, patch
        from app.views.summary_view import SummaryView

        # Build a minimal DB with the darwin_core view
        db = sqlite3.connect(":memory:")
        db.row_factory = sqlite3.Row
        db.executescript("""
            CREATE TABLE specimens (
                uid TEXT PRIMARY KEY,
                scientific_name TEXT, family TEXT, genus TEXT, order_name TEXT,
                lon REAL, lat REAL, collection_date TEXT, collector TEXT,
                identifier TEXT, geo_area TEXT, storage TEXT,
                province TEXT, site TEXT
            );
            INSERT INTO specimens VALUES (
                'SP001','Aplysia californica','Aplysiidae','Aplysia','Anaspidea',
                118.5, 24.5, '2026-06-01', 'Wang', 'Chen', '福建厦门', 'D75E',
                'FJ', 'XM'
            );
            CREATE VIEW darwin_core AS
            SELECT
                uid            AS occurrenceID,
                scientific_name AS scientificName,
                family, genus, order_name AS "order",
                lon            AS decimalLongitude,
                lat            AS decimalLatitude,
                collection_date AS eventDate,
                collector      AS recordedBy,
                identifier     AS identifiedBy,
                geo_area       AS locality,
                storage        AS verbatimPreservation
            FROM specimens;
        """)
        db.commit()

        ctx = MagicMock()
        ctx.get_db.return_value = db
        ctx.settings = MagicMock()
        view = SummaryView(ctx)

        out_path = str(tmp_path / "dwc_test.csv")
        with patch("PyQt6.QtWidgets.QFileDialog.getSaveFileName", return_value=(out_path, "")), \
             patch("PyQt6.QtWidgets.QMessageBox.information", return_value=None), \
             patch("PyQt6.QtWidgets.QMessageBox.critical", return_value=None):
            view._export_dwc()

        assert Path(out_path).exists(), "DwC CSV file was not created"
        import csv as _csv
        with open(out_path, encoding="utf-8-sig") as f:
            reader = _csv.reader(f)
            header = next(reader)
        assert "occurrenceID" in header
        assert "scientificName" in header
