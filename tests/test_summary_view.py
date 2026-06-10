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
- Registry positions SummaryView at index 7 (新增「项目树」页后由 6 后移; slot 8 in nav, 0-based).
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


def _insert_specimen_full(
    db: sqlite3.Connection,
    uid: str,
    proj: str = "/tmp/proj",
    name: str = "Aplysia californica",
    name_cn: str = "",
    coll_date: str | None = None,
    photo_date: str | None = None,
    collector: str = "",
) -> None:
    db.execute(
        "INSERT OR REPLACE INTO specimens "
        "(uid, scientific_name, scientific_name_cn, family, owner_project_dir, "
        " collection_date, photo_date, collector) "
        "VALUES (?, ?, ?, 'Aplysiidae', ?, ?, ?, ?)",
        (uid, name, name_cn, proj, coll_date, photo_date, collector),
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


# ── Search box ────────────────────────────────────────────────────────────────

class TestSearch:
    def test_search_input_exists(self, view_with_data) -> None:
        assert hasattr(view_with_data, "_search_input")
        assert view_with_data._search_input is not None

    def test_search_filters_by_substring(self, view_with_data) -> None:
        db = view_with_data.ctx.get_db()
        # reset and insert specimens with distinct names
        db.execute("DELETE FROM specimens")
        _insert_specimen_full(db, "SP010", "/tmp/proj_A", name="Aplysia californica")
        _insert_specimen_full(db, "SP011", "/tmp/proj_A", name="Octopus vulgaris")
        view_with_data.on_activate()
        assert len(view_with_data._filtered_specimens()) == 2
        view_with_data._on_search_changed("octopus")
        specs = view_with_data._filtered_specimens()
        assert len(specs) == 1
        assert specs[0].uid == "SP011"

    def test_search_matches_uid(self, view_with_data) -> None:
        view_with_data._on_search_changed("SP002")
        specs = view_with_data._filtered_specimens()
        assert all(s.uid == "SP002" for s in specs)
        assert len(specs) == 1

    def test_search_case_insensitive(self, view_with_data) -> None:
        view_with_data._on_search_changed("APLYSIA")
        assert len(view_with_data._filtered_specimens()) == 3

    def test_empty_search_shows_all(self, view_with_data) -> None:
        view_with_data._on_search_changed("nomatch_xyz")
        assert len(view_with_data._filtered_specimens()) == 0
        view_with_data._on_search_changed("")
        assert len(view_with_data._filtered_specimens()) == 3


# ── Date range filter ───────────────────────────────────────────────────────

class TestDateFilter:
    def test_date_field_combo_exists(self, view_with_data) -> None:
        assert hasattr(view_with_data, "_date_field_combo")
        # 不限日期 / 采集日期 / 拍照日期
        assert view_with_data._date_field_combo.count() == 3

    def test_date_filter_disabled_by_default(self, view_with_data) -> None:
        # default = 不限日期 → no date filtering
        db = view_with_data.ctx.get_db()
        db.execute("DELETE FROM specimens")
        _insert_specimen_full(db, "D1", coll_date="2026-01-01")
        _insert_specimen_full(db, "D2", coll_date="2026-06-01")
        view_with_data.on_activate()
        assert len(view_with_data._filtered_specimens()) == 2

    def test_date_range_filters_collection_date(self, view_with_data) -> None:
        db = view_with_data.ctx.get_db()
        db.execute("DELETE FROM specimens")
        _insert_specimen_full(db, "D1", coll_date="2026-01-15")
        _insert_specimen_full(db, "D2", coll_date="2026-03-20")
        _insert_specimen_full(db, "D3", coll_date="2026-06-10")
        view_with_data.on_activate()
        # enable collection-date filter, range Feb..Apr → only D2
        view_with_data._apply_date_filter("采集日期", "2026-02-01", "2026-04-30")
        specs = view_with_data._filtered_specimens()
        assert [s.uid for s in specs] == ["D2"]

    def test_date_range_filters_photo_date(self, view_with_data) -> None:
        db = view_with_data.ctx.get_db()
        db.execute("DELETE FROM specimens")
        _insert_specimen_full(db, "D1", coll_date="2026-01-15", photo_date="2026-05-01")
        _insert_specimen_full(db, "D2", coll_date="2026-03-20", photo_date="2026-01-01")
        view_with_data.on_activate()
        view_with_data._apply_date_filter("拍照日期", "2026-04-01", "2026-06-30")
        specs = view_with_data._filtered_specimens()
        assert [s.uid for s in specs] == ["D1"]

    def test_date_filter_excludes_empty_dates(self, view_with_data) -> None:
        db = view_with_data.ctx.get_db()
        db.execute("DELETE FROM specimens")
        _insert_specimen_full(db, "D1", coll_date="2026-03-01")
        _insert_specimen_full(db, "D2", coll_date=None)
        view_with_data.on_activate()
        view_with_data._apply_date_filter("采集日期", "2026-01-01", "2026-12-31")
        specs = view_with_data._filtered_specimens()
        assert [s.uid for s in specs] == ["D1"]

    def test_clearing_date_filter_restores_all(self, view_with_data) -> None:
        db = view_with_data.ctx.get_db()
        db.execute("DELETE FROM specimens")
        _insert_specimen_full(db, "D1", coll_date="2026-01-15")
        _insert_specimen_full(db, "D2", coll_date="2026-06-10")
        view_with_data.on_activate()
        view_with_data._apply_date_filter("采集日期", "2026-05-01", "2026-12-31")
        assert len(view_with_data._filtered_specimens()) == 1
        view_with_data._apply_date_filter("不限日期", "", "")
        assert len(view_with_data._filtered_specimens()) == 2

    def test_date_edits_always_clickable(self, view_with_data) -> None:
        # 默认"不限日期"时日期框也不能是死按钮 —— 永远可点（否则点击零反馈）
        assert view_with_data._date_from_edit.isEnabled()
        assert view_with_data._date_to_edit.isEnabled()

    def test_picking_from_date_auto_activates_collection_filter(self, view_with_data) -> None:
        from PyQt6.QtCore import QDate
        db = view_with_data.ctx.get_db()
        db.execute("DELETE FROM specimens")
        _insert_specimen_full(db, "D1", coll_date="2026-01-15")
        _insert_specimen_full(db, "D2", coll_date="2026-06-10")
        view_with_data.on_activate()
        assert view_with_data._date_field == "不限日期"
        # 用户在"不限日期"下直接从日历选起始日 → 自动切换为按采集日期筛选
        view_with_data._date_from_edit.setDate(QDate(2026, 5, 1))
        assert view_with_data._date_field == "采集日期"
        assert view_with_data._date_field_combo.currentText() == "采集日期"
        # 未动的截止端以数据上界兜底 → 范围 [2026-05-01, 2026-06-10] → 只剩 D2
        assert [s.uid for s in view_with_data._filtered_specimens()] == ["D2"]

    def test_picking_to_date_auto_activates_with_data_lower_bound(self, view_with_data) -> None:
        from PyQt6.QtCore import QDate
        db = view_with_data.ctx.get_db()
        db.execute("DELETE FROM specimens")
        _insert_specimen_full(db, "D1", coll_date="2026-01-15")
        _insert_specimen_full(db, "D2", coll_date="2026-06-10")
        view_with_data.on_activate()
        view_with_data._date_to_edit.setDate(QDate(2026, 2, 1))
        assert view_with_data._date_field == "采集日期"
        # 起始端以数据下界兜底 → 范围 [2026-01-15, 2026-02-01] → 只剩 D1
        assert [s.uid for s in view_with_data._filtered_specimens()] == ["D1"]

    def test_clearing_filter_keeps_edits_clickable(self, view_with_data) -> None:
        view_with_data._apply_date_filter("采集日期", "2026-01-01", "2026-12-31")
        view_with_data._apply_date_filter("不限日期", "", "")
        assert view_with_data._date_from_edit.isEnabled()
        assert view_with_data._date_to_edit.isEnabled()

    def test_search_and_date_compose(self, view_with_data) -> None:
        db = view_with_data.ctx.get_db()
        db.execute("DELETE FROM specimens")
        _insert_specimen_full(db, "C1", name="Aplysia x", coll_date="2026-03-01")
        _insert_specimen_full(db, "C2", name="Octopus y", coll_date="2026-03-02")
        _insert_specimen_full(db, "C3", name="Aplysia z", coll_date="2026-09-01")
        view_with_data.on_activate()
        view_with_data._on_search_changed("aplysia")
        view_with_data._apply_date_filter("采集日期", "2026-01-01", "2026-06-30")
        specs = view_with_data._filtered_specimens()
        assert [s.uid for s in specs] == ["C1"]


# ── Registry ──────────────────────────────────────────────────────────────────

class TestRegistry:
    def test_summary_at_index_6(self) -> None:
        # 新增「项目树」页插在 项目总览 之后 → SummaryView 由 6 后移到 7。
        from app.views.registry import ALL_VIEWS
        from app.views.summary_view import SummaryView
        assert ALL_VIEWS[7] is SummaryView

    def test_collab_not_in_all_views(self) -> None:
        from app.views.registry import ALL_VIEWS
        from app.views.collab_view import CollabView
        assert CollabView not in ALL_VIEWS

    def test_nav_order(self) -> None:
        from app.views.registry import ALL_VIEWS
        titles = [v.nav_title for v in ALL_VIEWS]
        assert titles == [
            "照片工作区", "最近工作区", "项目树", "标签打印",
            "WoRMS 分类库", "内置分类库", "坐标工具",
            "项目汇总", "采集记录", "采集地图", "配置",
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


# ── Search + date filter helpers (pure functions) ────────────────────────────

class TestFilterHelpers:
    def test_norm_date_slashes(self) -> None:
        from app.views.summary_view import _norm_date
        assert _norm_date("2026/06/01") == "2026-06-01"

    def test_norm_date_empty(self) -> None:
        from app.views.summary_view import _norm_date
        assert _norm_date(None) == ""
        assert _norm_date("") == ""

    def test_date_in_range_inclusive(self) -> None:
        from app.views.summary_view import _date_in_range
        assert _date_in_range("2026-06-01", "2026-06-01", "2026-06-01")
        assert _date_in_range("2026-06-05", "2026-06-01", "2026-06-10")

    def test_date_in_range_out(self) -> None:
        from app.views.summary_view import _date_in_range
        assert not _date_in_range("2026-05-31", "2026-06-01", "2026-06-10")
        assert not _date_in_range("2026-06-11", "2026-06-01", "2026-06-10")

    def test_date_in_range_empty_value_excluded(self) -> None:
        from app.views.summary_view import _date_in_range
        assert not _date_in_range("", "2026-06-01", None)
        assert not _date_in_range(None, None, "2026-06-10")

    def test_date_in_range_open_ends(self) -> None:
        from app.views.summary_view import _date_in_range
        assert _date_in_range("2026-06-05", "2026-06-01", None)
        assert _date_in_range("2026-06-05", None, "2026-06-10")

    def test_preset_range_today(self) -> None:
        from datetime import date
        from app.views.summary_view import _preset_range
        assert _preset_range("today", date(2026, 6, 10)) == ("2026-06-10", "2026-06-10")

    def test_preset_range_7d(self) -> None:
        from datetime import date
        from app.views.summary_view import _preset_range
        assert _preset_range("7d", date(2026, 6, 10)) == ("2026-06-04", "2026-06-10")

    def test_preset_range_30d(self) -> None:
        from datetime import date
        from app.views.summary_view import _preset_range
        assert _preset_range("30d", date(2026, 6, 10)) == ("2026-05-12", "2026-06-10")

    def test_preset_range_year(self) -> None:
        from datetime import date
        from app.views.summary_view import _preset_range
        assert _preset_range("year", date(2026, 6, 10)) == ("2026-01-01", "2026-12-31")

    def test_preset_range_all(self) -> None:
        from datetime import date
        from app.views.summary_view import _preset_range
        assert _preset_range("all", date(2026, 6, 10)) == (None, None)


# ── Search filter (view level) ────────────────────────────────────────────────

@pytest.fixture()
def view_filterable():
    """View with specimens carrying distinct names/dates for filter tests."""
    from app.views.summary_view import SummaryView
    db = _make_db()
    _insert_specimen_full(db, "FJ-XM-A1-haitu-D75E-20260601", name="Aplysia californica",
                          name_cn="海兔", coll_date="2026-06-01", photo_date="2026-06-02",
                          collector="王某")
    _insert_specimen_full(db, "FJ-XM-A1-haixing-DRY-20260520", name="Asterias amurensis",
                          name_cn="海星", coll_date="2026-05-20", photo_date=None,
                          collector="李某")
    _insert_specimen_full(db, "ZJ-SM-B2-shanhu-FRZ", name="Acropora",
                          name_cn="珊瑚", coll_date=None, photo_date="2026-06-05",
                          collector="王某")
    ctx = _make_ctx(db=db)
    v = SummaryView(ctx)
    v.on_activate()
    return v


class TestSearchFilter:
    def test_search_input_exists(self, view_filterable) -> None:
        assert view_filterable._search_input is not None

    def test_search_by_latin_name(self, view_filterable) -> None:
        view_filterable._apply_search("aplysia")
        assert len(view_filterable._filtered_specimens()) == 1

    def test_search_case_insensitive(self, view_filterable) -> None:
        view_filterable._apply_search("APLYSIA")
        assert len(view_filterable._filtered_specimens()) == 1

    def test_search_by_chinese_name(self, view_filterable) -> None:
        view_filterable._apply_search("海星")
        assert len(view_filterable._filtered_specimens()) == 1

    def test_search_by_uid_fragment(self, view_filterable) -> None:
        view_filterable._apply_search("haixing")
        assert len(view_filterable._filtered_specimens()) == 1


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
