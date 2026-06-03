"""test_taxonomy_view.py — Behavioral tests for TaxonomyView and its helpers.

Covers:
  - _RecordDialog: field values, required-field validation, history button visibility
  - _HistoryDialog: list population, rollback returns snapshot
  - _TaxonTableModel: records / checked state / column rebuild
  - TaxonomyView._import_rows: column alias mapping, skip incomplete rows, count
  - TaxonomyView._export_csv: CSV output format
  - TaxonomyView on_activate loads records into table
"""
from __future__ import annotations

import csv
import io
import json
import sys
import tempfile
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest


# ── QApplication fixture ──────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def qapp():
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    yield app


# ── Service + seed fixture ────────────────────────────────────────────────────

SEED = [
    {
        "class": "Polychaeta",   "order": "Phyllodocida",
        "family": "Polynoidae",  "species": "Halosydna brevisetosa",
        "classCn": "多毛纲",     "orderCn": "叶须虫目",
        "familyCn": "多鳞虫科",  "genus": "Halosydna",
        "genusCn": "海鳞虫属",   "speciesCn": "短毛海鳞虫",
    },
    {
        "class": "Malacostraca", "order": "Decapoda",
        "family": "Portunidae",  "species": "Portunus trituberculatus",
        "classCn": "软甲纲",     "orderCn": "十足目",
        "familyCn": "梭子蟹科",  "genus": "Portunus",
        "genusCn": "梭子蟹属",   "speciesCn": "三疣梭子蟹",
    },
]


@pytest.fixture
def tmp_svc():
    """Yield a TaxonomyService backed by a temp dir."""
    import shutil
    from app.services.taxonomy_service import TaxonomyService
    d = Path(tempfile.mkdtemp())
    seed_p = d / "taxonomy_seed.json"
    user_p = d / "user_taxonomy.json"
    seed_p.write_text(json.dumps(SEED), encoding="utf-8")
    svc = TaxonomyService(seed_p, user_p)
    try:
        yield svc
    finally:
        shutil.rmtree(d)


@pytest.fixture
def mock_ctx():
    ctx = MagicMock()
    ctx.current_project_dir = None
    ctx.settings = MagicMock()
    ctx.settings.last_nav_index = 0
    return ctx


@pytest.fixture
def view(qapp, mock_ctx, tmp_svc):
    from app.views.taxonomy_view import TaxonomyView
    v = TaxonomyView(mock_ctx)
    v._svc = tmp_svc
    return v


# ── _RecordDialog ─────────────────────────────────────────────────────────────

class TestRecordDialog:
    def test_dialog_constructs_no_record(self, qapp):
        from app.views.taxonomy_view import _RecordDialog
        dlg = _RecordDialog()
        assert dlg.windowTitle() == "新增分类条目"

    def test_dialog_constructs_with_record(self, qapp):
        from app.views.taxonomy_view import _RecordDialog
        rec = {
            "class": "Polychaeta", "order": "Phyllodocida",
            "family": "Polynoidae", "species": "Halosydna brevisetosa",
            "recordId": "user:abc123",
        }
        dlg = _RecordDialog(record=rec)
        assert dlg.windowTitle() == "编辑分类条目"

    def test_dialog_prepopulates_fields(self, qapp):
        from app.views.taxonomy_view import _RecordDialog
        rec = {"class": "Polychaeta", "order": "Phyllodocida",
               "family": "Polynoidae", "species": "Halosydna brevisetosa"}
        dlg = _RecordDialog(record=rec)
        assert dlg._inputs["class"].text() == "Polychaeta"
        assert dlg._inputs["order"].text() == "Phyllodocida"

    def test_get_record_returns_all_fields(self, qapp):
        from app.views.taxonomy_view import _RecordDialog, _DIALOG_FIELDS
        dlg = _RecordDialog()
        result = dlg.get_record()
        assert set(result.keys()) == {k for k, _, _ in _DIALOG_FIELDS}

    def test_history_button_hidden_when_no_history(self, qapp):
        from app.views.taxonomy_view import _RecordDialog
        rec = {"class": "Polychaeta", "order": "Phyllodocida",
               "family": "Polynoidae", "species": "X", "recordId": "user:1"}
        dlg = _RecordDialog(record=rec)
        assert dlg._btn_history.isHidden()

    def test_history_button_visible_when_history_present(self, qapp):
        from app.views.taxonomy_view import _RecordDialog
        rec = {
            "class": "Polychaeta", "order": "Phyllodocida",
            "family": "Polynoidae", "species": "X", "recordId": "user:1",
            "history": [{"at": "2026-01-01T00:00:00Z",
                         "before": {"class": "Old", "classCn": "", "order": "O",
                                    "orderCn": "", "family": "F", "familyCn": "",
                                    "genus": "", "genusCn": "", "species": "X",
                                    "speciesCn": ""}}],
        }
        dlg = _RecordDialog(record=rec)
        assert not dlg._btn_history.isHidden()


# ── _HistoryDialog ────────────────────────────────────────────────────────────

class TestHistoryDialog:
    def test_dialog_constructs_with_history(self, qapp):
        from app.views.taxonomy_view import _HistoryDialog
        hist = [
            {"at": "2026-01-02T10:00:00Z",
             "before": {"class": "Poly", "classCn": "", "order": "Phyllo",
                        "orderCn": "", "family": "Poly-fam", "familyCn": "",
                        "genus": "", "genusCn": "", "species": "X sp",
                        "speciesCn": ""}},
        ]
        dlg = _HistoryDialog(history=hist)
        assert dlg._list.count() == 1

    def test_dialog_shows_newest_first(self, qapp):
        from app.views.taxonomy_view import _HistoryDialog
        hist = [
            {"at": "2026-01-01T00:00:00Z",
             "before": {"class": "A", "classCn": "", "order": "B",
                        "orderCn": "", "family": "C", "familyCn": "",
                        "genus": "", "genusCn": "", "species": "D sp",
                        "speciesCn": ""}},
            {"at": "2026-01-03T00:00:00Z",
             "before": {"class": "A", "classCn": "", "order": "B",
                        "orderCn": "", "family": "C", "familyCn": "",
                        "genus": "", "genusCn": "", "species": "D sp",
                        "speciesCn": ""}},
        ]
        dlg = _HistoryDialog(history=hist)
        # Newest (2026-01-03) should be first
        first_text = dlg._list.item(0).text()
        assert "2026-01-03" in first_text


# ── _TaxonTableModel ──────────────────────────────────────────────────────────

class TestTaxonTableModel:
    def test_set_records_updates_row_count(self, qapp):
        from app.views.taxonomy_view import _TaxonTableModel
        m = _TaxonTableModel()
        m.set_records(SEED)
        assert m.rowCount() == len(SEED)

    def test_row_number_column_shows_offset_plus_one(self, qapp):
        from PyQt6.QtCore import Qt
        from app.views.taxonomy_view import _TaxonTableModel, _COL_NUM
        m = _TaxonTableModel()
        m.set_records(SEED, page_offset=50)
        val = m.data(m.index(0, _COL_NUM), Qt.ItemDataRole.DisplayRole)
        assert val == "51"

    def test_checked_state_toggle(self, qapp):
        from PyQt6.QtCore import Qt
        from app.views.taxonomy_view import _TaxonTableModel, _COL_CHECK
        m = _TaxonTableModel()
        recs = [dict(r, recordId=f"user:test{i}") for i, r in enumerate(SEED)]
        m.set_records(recs)
        idx = m.index(0, _COL_CHECK)
        m.setData(idx, Qt.CheckState.Checked, Qt.ItemDataRole.CheckStateRole)
        assert "user:test0" in m.checked_ids()
        m.setData(idx, Qt.CheckState.Unchecked, Qt.ItemDataRole.CheckStateRole)
        assert "user:test0" not in m.checked_ids()

    def test_clear_checked(self, qapp):
        from PyQt6.QtCore import Qt
        from app.views.taxonomy_view import _TaxonTableModel, _COL_CHECK
        m = _TaxonTableModel()
        recs = [dict(r, recordId=f"user:test{i}") for i, r in enumerate(SEED)]
        m.set_records(recs)
        m.set_all_page_checked(True)
        assert len(m.checked_ids()) == len(SEED)
        m.clear_checked()
        assert len(m.checked_ids()) == 0

    def test_source_column_shows_seed_or_user(self, qapp):
        from PyQt6.QtCore import Qt
        from app.views.taxonomy_view import _TaxonTableModel, _COL_DATA_START
        m = _TaxonTableModel()
        recs = [
            {**SEED[0], "recordId": "seed:0"},
            {**SEED[1], "recordId": "user:abc"},
        ]
        m.set_records(recs)
        n_data = len(m.columns())
        src_col = _COL_DATA_START + n_data
        seed_val = m.data(m.index(0, src_col), Qt.ItemDataRole.DisplayRole)
        user_val = m.data(m.index(1, src_col), Qt.ItemDataRole.DisplayRole)
        assert seed_val == "种子"
        assert user_val == "用户"


# ── _import_rows ──────────────────────────────────────────────────────────────

class TestImportRows:
    @pytest.fixture
    def view_with_svc(self, qapp, mock_ctx, tmp_svc):
        from app.views.taxonomy_view import TaxonomyView
        v = TaxonomyView(mock_ctx)
        v._svc = tmp_svc
        return v

    def test_import_english_headers(self, view_with_svc):
        header = ["class", "order", "family", "species"]
        rows = [
            ["Polychaeta", "Phyllodocida", "Polynoidae", "Halosydna brevisetosa"],
        ]
        imported, skipped = view_with_svc._import_rows(header, rows)
        assert imported == 1
        assert skipped == 0
        assert view_with_svc._svc.user_count() == 1

    def test_import_chinese_headers(self, view_with_svc):
        header = ["纲", "目", "科", "种"]
        rows = [
            ["Polychaeta", "Phyllodocida", "Polynoidae", "Halosydna brevisetosa"],
        ]
        imported, skipped = view_with_svc._import_rows(header, rows)
        assert imported == 1
        assert skipped == 0

    def test_import_skips_incomplete_rows(self, view_with_svc):
        header = ["class", "order", "family", "species"]
        rows = [
            ["Polychaeta", "Phyllodocida", "", "Halosydna brevisetosa"],  # missing family
            ["Polychaeta", "Phyllodocida", "Polynoidae", "Halosydna brevisetosa"],
        ]
        imported, skipped = view_with_svc._import_rows(header, rows)
        assert imported == 1
        assert skipped == 1

    def test_import_optional_cn_fields(self, view_with_svc):
        header = ["class", "order", "family", "species", "classCn", "familyCn"]
        rows = [
            ["Polychaeta", "Phyllodocida", "Polynoidae",
             "Halosydna brevisetosa", "多毛纲", "多鳞虫科"],
        ]
        view_with_svc._import_rows(header, rows)
        recs, _ = view_with_svc._svc.all_records(source_filter="user")
        assert recs[0].get("classCn") == "多毛纲"

    def test_import_multiple_rows(self, view_with_svc):
        header = ["class", "order", "family", "species"]
        rows = [
            ["Polychaeta", "Phyllodocida", "Polynoidae", "Halosydna brevisetosa"],
            ["Malacostraca", "Decapoda", "Portunidae", "Portunus trituberculatus"],
            ["Incomplete", "", "Fam", "Sp"],  # missing order → skip
        ]
        imported, skipped = view_with_svc._import_rows(header, rows)
        assert imported == 2
        assert skipped == 1


# ── _export_csv ───────────────────────────────────────────────────────────────

class TestExportCsv:
    def test_csv_written_with_header(self, view, tmp_path, qapp):
        out = tmp_path / "test_export.csv"
        # Load some records first
        view._svc.learn({
            "class": "Polychaeta", "order": "Phyllodocida",
            "family": "Polynoidae", "species": "Halosydna brevisetosa",
        })
        view.on_activate()

        # Patch QFileDialog and QMessageBox to avoid blocking dialogs in offscreen
        import unittest.mock as mock
        with mock.patch(
            "app.views.taxonomy_view.QFileDialog.getSaveFileName",
            return_value=(str(out), "CSV 文件 (*.csv)"),
        ), mock.patch("app.views.taxonomy_view.QMessageBox.information"):
            view._export_csv(
                view._svc.all_records(page=0, page_size=99999)[0]
            )

        assert out.exists()
        content = out.read_text(encoding="utf-8-sig")
        reader = csv.reader(io.StringIO(content))
        rows = list(reader)
        assert len(rows) >= 2   # header + at least 1 data row
        # Last column of header should be "来源"
        assert rows[0][-1] == "来源"

    def test_csv_user_record_shows_correct_source(self, view, tmp_path, qapp):
        out = tmp_path / "test_export2.csv"
        view._svc.learn({
            "class": "Polychaeta", "order": "Phyllodocida",
            "family": "Polynoidae", "species": "Halosydna brevisetosa",
        })
        recs, _ = view._svc.all_records(source_filter="user")

        import unittest.mock as mock
        with mock.patch(
            "app.views.taxonomy_view.QFileDialog.getSaveFileName",
            return_value=(str(out), ""),
        ), mock.patch("app.views.taxonomy_view.QMessageBox.information"):
            view._export_csv(recs)

        content = out.read_text(encoding="utf-8-sig")
        reader = csv.reader(io.StringIO(content))
        rows = list(reader)
        # First data row should be "用户"
        assert rows[1][-1] == "用户"


# ── TaxonomyView on_activate ──────────────────────────────────────────────────

class TestViewOnActivate:
    def test_on_activate_loads_records(self, view, qapp):
        view.on_activate()
        assert view._model.rowCount() > 0

    def test_on_activate_updates_stats_label(self, view, qapp):
        view.on_activate()
        text = view._stats_label.text()
        assert "条" in text
        count = view._svc.seed_count() + view._svc.user_count()
        assert str(count) in text

    def test_on_activate_footer_shows_seed_and_user(self, view, qapp):
        view.on_activate()
        footer = view._footer_label.text()
        assert "种子库" in footer
        assert "用户" in footer

    def test_pagination_initial_page_is_1(self, view, qapp):
        view.on_activate()
        assert view._page == 1

    def test_total_equals_seed_count_initially(self, view, qapp):
        view.on_activate()
        assert view._total == len(SEED)
