"""test_taxonomy_view.py — Behavioral tests for TaxonomyView and its helpers.

Covers:
  - _RecordDialog: field values, required-field validation, history button visibility
  - _HistoryDialog: list population, rollback returns snapshot
  - _TaxonTableModel: records / checked state / column rebuild
  - TaxonomyView._import_rows: column alias mapping, skip incomplete rows, count
  - TaxonomyView._export_csv: CSV output format
  - TaxonomyView on_activate loads records into table
  - _TaxonFacetPanel: value counts, predicate modes, signals
  - TaxonomyView facet filter integration in _load_page
  - _WormsMatchDialog: dialog construction, no-match result
  - _TaxonReviewDialog: dialog construction, use/no-match result
  - Job panel: progress text update
  - Row context menu: no-crash smoke test
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

    def test_checked_persists_across_pages(self, qapp):
        """User's bug: select on page 1, page away, selection must survive so
        'WoRMS 更新所选' targets the originally-selected rows."""
        from PyQt6.QtCore import Qt
        from app.views.taxonomy_view import _TaxonTableModel, _COL_CHECK
        m = _TaxonTableModel()
        page1 = [{"recordId": f"user:p1-{i}"} for i in range(3)]
        page2 = [{"recordId": f"user:p2-{i}"} for i in range(3)]
        m.set_records(page1)
        m.setData(m.index(0, _COL_CHECK), Qt.CheckState.Checked, Qt.ItemDataRole.CheckStateRole)
        m.setData(m.index(2, _COL_CHECK), Qt.CheckState.Checked, Qt.ItemDataRole.CheckStateRole)
        m.set_records(page2)   # page turn must NOT wipe the page-1 checks
        assert set(m.checked_ids()) == {"user:p1-0", "user:p1-2"}

    def test_checked_changed_signal_fires(self, qapp):
        from PyQt6.QtCore import Qt
        from app.views.taxonomy_view import _TaxonTableModel, _COL_CHECK
        m = _TaxonTableModel()
        m.set_records([{"recordId": "user:x"}])
        fired = []
        m.checked_changed.connect(lambda: fired.append(1))
        m.setData(m.index(0, _COL_CHECK), Qt.CheckState.Checked, Qt.ItemDataRole.CheckStateRole)
        assert fired   # toggling a checkbox notifies the view to refresh the note

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


# ── Taxonomy chart ────────────────────────────────────────────────────────────

class TestTaxonomyChart:
    def test_chart_entries_count_orders(self, view, qapp):
        view.on_activate()
        entries = dict(view._chart_entries())
        assert entries["叶须虫目"] == 1
        assert entries["十足目"] == 1

    def test_chart_entries_respect_current_filter(self, view, qapp):
        view.on_activate()
        view._filter_text = "叶须虫"
        entries = dict(view._chart_entries())
        assert entries == {"叶须虫目": 1}

    def test_chart_toggle_opens_nonblocking_dialog(self, view, qapp):
        view.on_activate()
        view._btn_chart.setChecked(True)
        view._on_chart_toggle()

        assert view._show_chart is True
        assert view._chart_dialog is not None
        assert view._chart_dialog.isVisible()

        view._chart_dialog.close()
        qapp.processEvents()
        assert view._show_chart is False
        assert view._chart_dialog is None


# ── WoRMS update (startTaxonomyWormsJob) ──────────────────────────────────────

class TestTaxonomyWormsUpdate:
    def test_worms_update_ids_from_filter(self, view, qapp):
        view.on_activate()
        view._filter_text = "叶须虫"

        ids = view._worms_update_record_ids(selected_only=False)

        assert len(ids) == 1
        assert ids[0].startswith("seed:")

    def test_worms_update_creates_project_job(self, view, tmp_path, qapp):
        view.ctx.current_project_dir = str(tmp_path)
        view.on_activate()
        view._filter_text = "叶须虫"

        import unittest.mock as mock
        from PyQt6.QtWidgets import QMessageBox
        # Confirm dialog → Yes; stub the worker so no real WoRMS network runs.
        with mock.patch(
            "app.views.taxonomy_view.QMessageBox.question",
            return_value=QMessageBox.StandardButton.Yes,
        ), mock.patch.object(view, "_start_job_worker") as start:
            view._on_worms_update(selected_only=False)

        jobs_path = tmp_path / "_data" / "worms_jobs.json"
        data = json.loads(jobs_path.read_text(encoding="utf-8"))
        assert len(data["jobs"]) == 1
        assert data["jobs"][0]["source"] == "filtered"
        assert len(data["jobs"][0]["record_ids"]) == 1
        assert view.ctx.pending_worms_job_id == data["jobs"][0]["id"]
        start.assert_called_once()

    def test_mapping_status_surfaces_on_reload(self, view, tmp_path, qapp, monkeypatch):
        """After a match writes a mapping, reloading the page must surface that
        status on the row so the 审核 entry appears (results reflected in table)."""
        view.ctx.current_project_dir = str(tmp_path)
        created = view._svc.learn({
            "class": "Polychaeta", "order": "Phyllodocida", "family": "Polynoidae",
            "genus": "Halosydna", "species": "Halosydna brevisetosa",
            "classCn": "多毛纲", "speciesCn": "短毛海鳞虫",
        })
        rid = created["recordId"]
        svc = view._ensure_worms_svc()
        monkeypatch.setattr(svc, "search", lambda *a, **k: [])   # → not_found
        svc.match_one(dict(created))
        view.on_activate()   # reload runs _annotate_mappings

        row = next(
            (view._model.record_at(i) for i in range(view._model.rowCount())
             if view._model.record_at(i).get("recordId") == rid),
            None,
        )
        assert row is not None
        assert row.get("mappingStatus") == "not_found"


class TestWormsJobWorker:
    """_WormsJobWorker drives a job to completion off a stubbed service.

    run() is called directly (synchronously, no new thread) for determinism.
    """

    class _FakeSvc:
        def __init__(self, record_ids, statuses):
            self.job = {
                "id": "j1", "status": "running", "record_ids": list(record_ids),
                "cursor": 0, "counts": {},
            }
            self._statuses = dict(statuses)   # recordId -> status to return
            self.matched = []

        def get_job(self, job_id):
            return self.job

        def match_one(self, record):
            self.matched.append(record["recordId"])
            return self._statuses.get(record["recordId"], "not_found")

        def record_job_result(self, job_id, status):
            self.job["counts"][status] = self.job["counts"].get(status, 0) + 1
            self.job["cursor"] += 1
            if self.job["cursor"] >= len(self.job["record_ids"]):
                self.job["status"] = "completed"
            return self.job

        def update_job_status(self, job_id, status):
            self.job["status"] = status
            return self.job

    def test_worker_drives_job_to_completion(self, qapp):
        from app.views.taxonomy_view import _WormsJobWorker
        svc = self._FakeSvc(
            ["r1", "r2", "r3"],
            {"r1": "matched", "r2": "renamed", "r3": "not_found"},
        )
        records = {"r1": {"recordId": "r1", "species": "A b"},
                   "r2": {"recordId": "r2", "species": "C d"},
                   "r3": {"recordId": "r3", "species": "E f"}}
        progress, finished = [], []
        w = _WormsJobWorker(svc, "j1", lambda rid: records.get(rid))
        w.progress.connect(lambda c, t, ct: progress.append((c, t, dict(ct))))
        w.finished_job.connect(lambda j: finished.append(dict(j)))
        w.run()   # synchronous

        assert svc.matched == ["r1", "r2", "r3"]
        assert len(progress) == 3
        assert finished and finished[-1]["status"] == "completed"
        assert svc.job["counts"] == {"matched": 1, "renamed": 1, "not_found": 1}

    def test_worker_stops_when_paused(self, qapp):
        from app.views.taxonomy_view import _WormsJobWorker
        svc = self._FakeSvc(["r1", "r2"], {"r1": "matched", "r2": "matched"})
        svc.job["status"] = "paused"   # already paused before first tick
        finished = []
        w = _WormsJobWorker(svc, "j1", lambda rid: {"recordId": rid, "species": "x"})
        w.finished_job.connect(lambda j: finished.append(dict(j)))
        w.run()

        assert svc.matched == []            # nothing processed
        assert finished[-1]["status"] == "paused"

    def test_unresolvable_record_is_stale(self, qapp):
        from app.views.taxonomy_view import _WormsJobWorker
        svc = self._FakeSvc(["ghost"], {})
        w = _WormsJobWorker(svc, "j1", lambda rid: None)   # resolver returns None
        w.run()
        assert svc.matched == []
        assert svc.job["counts"] == {"stale": 1}
        assert svc.job["status"] == "completed"


# ── _TaxonFacetPanel ──────────────────────────────────────────────────────────

class TestTaxonFacetPanel:
    """Tests for the per-column facet filter panel.

    Mirrors fetchTaxonFacetValues / taxonFacetValueChecked /
    toggleTaxonFacetValue / renderTaxonFacetMenu logic in app.js.
    """

    @pytest.fixture
    def panel(self, qapp):
        from app.views.taxonomy_view import _TaxonFacetPanel
        recs = [
            {"class": "Polychaeta",   "classCn": "多毛纲"},
            {"class": "Polychaeta",   "classCn": "多毛纲"},
            {"class": "Malacostraca", "classCn": "软甲纲"},
            {"class": "",             "classCn": ""},
        ]
        return _TaxonFacetPanel("class", "纲(拉丁)", recs)

    def test_constructs(self, panel):
        assert panel is not None

    def test_unique_values_counts(self, panel):
        items = panel._unique_values()
        d = dict(items)
        assert d.get("Polychaeta") == 2
        assert d.get("Malacostraca") == 1

    def test_value_checked_all_mode(self, panel):
        panel._draft = {"mode": "all"}
        assert panel._value_checked("Polychaeta") is True
        assert panel._value_checked("") is True

    def test_value_checked_include_mode(self, panel):
        panel._draft = {"mode": "include", "values": ["Polychaeta"]}
        assert panel._value_checked("Polychaeta") is True
        assert panel._value_checked("Malacostraca") is False

    def test_value_checked_exclude_mode(self, panel):
        panel._draft = {"mode": "exclude", "excluded": ["Malacostraca"]}
        assert panel._value_checked("Polychaeta") is True
        assert panel._value_checked("Malacostraca") is False

    def test_value_checked_search_mode(self, panel):
        panel._draft = {"mode": "search", "search": "Poly", "excluded": []}
        assert panel._value_checked("Polychaeta") is True
        assert panel._value_checked("Malacostraca") is False

    def test_filter_applied_signal_none_on_clear(self, qapp):
        from app.views.taxonomy_view import _TaxonFacetPanel
        recs = [{"class": "Polychaeta"}]
        panel = _TaxonFacetPanel(
            "class", "纲", recs,
            current_predicate={"mode": "include", "values": ["Polychaeta"]}
        )
        results = []
        panel.filter_applied.connect(lambda k, p: results.append((k, p)))
        panel._on_clear()
        assert len(results) == 1
        assert results[0] == ("class", None)

    def test_filter_applied_signal_include_on_ok(self, qapp):
        from app.views.taxonomy_view import _TaxonFacetPanel
        recs = [{"class": "Polychaeta"}]
        pred = {"mode": "include", "values": ["Polychaeta"]}
        panel = _TaxonFacetPanel("class", "纲", recs, current_predicate=pred)
        results = []
        panel.filter_applied.connect(lambda k, p: results.append((k, p)))
        panel._on_apply()
        assert len(results) == 1
        col_key, returned_pred = results[0]
        assert col_key == "class"
        assert returned_pred is not None
        assert returned_pred.get("mode") == "include"

    def test_sort_requested_signal(self, panel, qapp):
        signals = []
        panel.sort_requested.connect(lambda col, d: signals.append((col, d)))
        panel.sort_requested.emit("class", "asc")
        assert signals == [("class", "asc")]


# ── TaxonomyView facet filter integration ────────────────────────────────────

class TestFacetFilterIntegration:
    """Test that _col_filters is applied during _load_page."""

    def test_include_filter_restricts_rows(self, view, qapp):
        view.on_activate()
        total_before = view._total

        view._col_filters["class"] = {"mode": "include", "values": ["Polychaeta"]}
        view._page = 1
        view._load_page()

        assert view._total < total_before
        for row in range(view._model.rowCount()):
            rec = view._model.record_at(row)
            assert rec is not None
            assert rec.get("class") == "Polychaeta"

    def test_exclude_filter_hides_rows(self, view, qapp):
        view.on_activate()

        view._col_filters["class"] = {"mode": "exclude", "excluded": ["Polychaeta"]}
        view._page = 1
        view._load_page()

        for row in range(view._model.rowCount()):
            rec = view._model.record_at(row)
            assert rec is not None
            assert rec.get("class") != "Polychaeta"

    def test_facet_filter_applied_clears_on_none(self, view, qapp):
        view.on_activate()
        view._col_filters["class"] = {"mode": "include", "values": ["Polychaeta"]}
        view._load_page()
        filtered_total = view._total

        view._on_facet_filter_applied("class", None)
        assert "class" not in view._col_filters
        assert view._total >= filtered_total

    def test_sort_asc_orders_records(self, view, qapp):
        view.on_activate()
        view._sort_col = "class"
        view._sort_dir = "asc"
        view._load_page()

        first_rec = view._model.record_at(0)
        last_rec  = view._model.record_at(view._model.rowCount() - 1)
        assert first_rec is not None and last_rec is not None
        assert first_rec.get("class", "") <= last_rec.get("class", "")

    def test_sort_desc_reverses_order(self, view, qapp):
        view.on_activate()
        view._sort_col = "class"
        view._sort_dir = "desc"
        view._load_page()

        first_rec = view._model.record_at(0)
        last_rec  = view._model.record_at(view._model.rowCount() - 1)
        assert first_rec is not None and last_rec is not None
        assert first_rec.get("class", "") >= last_rec.get("class", "")


# ── _WormsMatchDialog smoke tests ─────────────────────────────────────────────

class TestWormsMatchDialog:
    """Smoke tests for _WormsMatchDialog (no network — mock WormsService)."""

    @pytest.fixture
    def worms_svc(self):
        svc = MagicMock()
        svc.search.return_value = [
            {
                "scientificname": "Halosydna brevisetosa",
                "valid_name": "Halosydna brevisetosa",
                "AphiaID": 12345,
                "valid_AphiaID": 12345,
                "status": "accepted",
            }
        ]
        svc.classification.return_value = None
        svc.flatten_classification.return_value = []
        return svc

    def test_dialog_constructs(self, qapp, worms_svc):
        from app.views.taxonomy_view import _WormsMatchDialog, _WormsSearchWorker
        row = {"species": "Halosydna brevisetosa", "recordId": "seed:0"}
        import unittest.mock as mock
        with mock.patch.object(_WormsSearchWorker, "start"):
            dlg = _WormsMatchDialog(row, worms_svc)
        assert dlg is not None
        assert dlg.windowTitle() == "WoRMS 匹配物种"

    def test_dialog_get_result_none_before_accept(self, qapp, worms_svc):
        from app.views.taxonomy_view import _WormsMatchDialog, _WormsSearchWorker
        row = {"species": "X sp", "recordId": "seed:1"}
        import unittest.mock as mock
        with mock.patch.object(_WormsSearchWorker, "start"):
            dlg = _WormsMatchDialog(row, worms_svc)
        assert dlg.get_result() is None

    def test_on_no_match_sets_result(self, qapp, worms_svc):
        from app.views.taxonomy_view import _WormsMatchDialog, _WormsSearchWorker
        row = {"species": "X sp", "recordId": "seed:1"}
        import unittest.mock as mock
        with mock.patch.object(_WormsSearchWorker, "start"):
            dlg = _WormsMatchDialog(row, worms_svc)
        dlg._on_no_match()
        result = dlg.get_result()
        assert result is not None
        assert result.get("no_match") is True


# ── _TaxonReviewDialog smoke tests ────────────────────────────────────────────

class TestTaxonReviewDialog:
    def test_dialog_no_candidates(self, qapp):
        from app.views.taxonomy_view import _TaxonReviewDialog
        row = {"species": "Unknown sp", "recordId": "seed:0", "mappingCandidates": []}
        dlg = _TaxonReviewDialog(row)
        assert dlg.windowTitle() == "审核 WoRMS 匹配"
        assert dlg.get_result() is None

    def test_dialog_with_candidates(self, qapp):
        from app.views.taxonomy_view import _TaxonReviewDialog
        row = {
            "species": "Halosydna brevisetosa",
            "recordId": "seed:0",
            "mappingCandidates": [
                {"valid_name": "Halosydna brevisetosa", "AphiaID": 12345}
            ],
        }
        dlg = _TaxonReviewDialog(row)
        assert dlg is not None

    def test_on_no_match_returns_no_match(self, qapp):
        from app.views.taxonomy_view import _TaxonReviewDialog
        row = {"species": "X sp", "recordId": "seed:0", "mappingCandidates": []}
        dlg = _TaxonReviewDialog(row)
        dlg._on_no_match()
        result = dlg.get_result()
        assert result is not None
        assert result.get("no_match") is True

    def test_on_use_returns_aphia_id(self, qapp):
        from app.views.taxonomy_view import _TaxonReviewDialog
        row = {"species": "X sp", "recordId": "seed:0", "mappingCandidates": []}
        dlg = _TaxonReviewDialog(row)
        dlg._on_use({"AphiaID": 99999, "valid_AphiaID": 99999})
        result = dlg.get_result()
        assert result is not None
        assert result.get("aphia_id") == 99999


# ── Job panel (renderTaxonJobPanel) ───────────────────────────────────────────

class TestJobPanel:
    def test_panel_hidden_when_no_jobs(self, view, tmp_path, qapp):
        view.ctx.current_project_dir = str(tmp_path)
        data_dir = tmp_path / "_data"
        data_dir.mkdir()
        (data_dir / "worms_jobs.json").write_text('{"jobs": []}', encoding="utf-8")
        (data_dir / "worms_cache.json").write_text("{}", encoding="utf-8")

        view.show()
        view.on_activate()
        qapp.processEvents()
        assert not view._job_panel_frame.isVisible()

    def test_panel_progress_text_when_job_present(self, view, tmp_path, qapp):
        import uuid
        from datetime import datetime, timezone

        view.ctx.current_project_dir = str(tmp_path)
        data_dir = tmp_path / "_data"
        data_dir.mkdir()
        (data_dir / "worms_cache.json").write_text("{}", encoding="utf-8")

        now = datetime.now(timezone.utc).isoformat()
        jobs_data = {
            "jobs": [
                {
                    "id": str(uuid.uuid4()),
                    "status": "running",
                    "created_at": now,
                    "updated_at": now,
                    "created_by": "test",
                    "record_ids": ["seed:0", "seed:1"],
                    "cursor": 1,
                    "counts": {"matched": 1},
                    "source": "filtered",
                }
            ]
        }
        (data_dir / "worms_jobs.json").write_text(
            json.dumps(jobs_data), encoding="utf-8"
        )

        view.show()
        view._worms_svc = None
        view._refresh_job_panel()
        qapp.processEvents()

        assert "1 / 2" in view._job_progress_label.text()

    def test_panel_hides_when_no_jobs_found(self, view, qapp):
        import unittest.mock as mock
        with mock.patch(
            "app.views.taxonomy_view.WormsService.list_jobs", return_value=[]
        ):
            view._refresh_job_panel()
        assert not view._job_panel_frame.isVisible()


# ── Row context menu ──────────────────────────────────────────────────────────

class TestRowContextMenu:
    def test_context_menu_opens_without_crash(self, view, qapp):
        """Right-click on a seed row must not raise."""
        view.on_activate()
        assert view._model.rowCount() > 0

        import unittest.mock as mock
        from PyQt6.QtCore import QPoint
        with mock.patch("app.views.taxonomy_view.QMenu.exec"):
            view._on_row_context_menu(QPoint(40, 5))

    def test_context_menu_noop_for_off_table(self, view, qapp):
        """QPoint outside rows must not crash."""
        view.on_activate()
        import unittest.mock as mock
        from PyQt6.QtCore import QPoint
        with mock.patch("app.views.taxonomy_view.QMenu.exec"):
            view._on_row_context_menu(QPoint(40, 99999))
