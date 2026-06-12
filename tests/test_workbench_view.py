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
import json
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
    # Default OFF so a bare MagicMock's truthiness doesn't auto-trigger.
    ctx.settings.auto_activate_on_new_specimen = False
    ctx.settings.auto_organize_after_compose = False
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

    def test_fs_watcher_starts_on_activate(self, tmp_path):
        """on_activate must start filesystem watcher + fallback timer."""
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
        assert hasattr(w, "_fs_watcher")
        assert w._fs_watcher.directories()
        assert w._debounce_timer.isActive() or w._debounce_timer.isSingleShot()
        assert w._fallback_timer.isActive()
        db.close()

    def test_fs_watcher_stops_on_deactivate(self, tmp_path):
        """on_deactivate must stop timers and clear watcher paths."""
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
        assert not w._debounce_timer.isActive()
        assert not w._fallback_timer.isActive()
        assert not w._fs_watcher.directories()
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

    def test_rna_warning_tooltip_for_r_prefix(self):
        """R-prefix storage: RNA note is tooltip-only, not a permanent banner."""
        from app.widgets.naming_panel import NamingPanel
        ctx = _make_ctx()
        w = NamingPanel(ctx)
        w._storage.setText("RD75E")
        w._update_preview()
        assert w._rna_warning.isHidden()
        assert "RNAlater" in w._storage_combo.toolTip()

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

    def test_copy_current_uid_to_clipboard(self, tmp_path, qt_app):
        from app.widgets.specimen_sidebar import SpecimenSidebar
        project_dir = str(tmp_path)
        db = _make_db(str(tmp_path / "project.db"))
        uid = "FJ-XM-B2-DLC001-T95E-20260601"
        db.execute(
            "INSERT INTO specimens (uid, owner_project_dir) VALUES (?, ?)",
            (uid, project_dir),
        )
        db.commit()
        ctx = _make_ctx(project_dir=project_dir, db=db)
        w = SpecimenSidebar(ctx)
        w.refresh()
        w.select_uid(uid)
        assert w.copy_current_uid() is True
        assert QApplication.clipboard().text() == uid
        db.close()

    def test_print_current_labels_signal(self, tmp_path):
        from app.widgets.specimen_sidebar import SpecimenSidebar
        project_dir = str(tmp_path)
        db = _make_db(str(tmp_path / "project.db"))
        uid = "FJ-XM-B2-DLC001-T95E-20260601"
        db.execute(
            "INSERT INTO specimens (uid, owner_project_dir) VALUES (?, ?)",
            (uid, project_dir),
        )
        db.commit()
        ctx = _make_ctx(project_dir=project_dir, db=db)
        w = SpecimenSidebar(ctx)
        received = []
        w.print_labels_requested.connect(received.append)
        w.refresh()
        w.select_uid(uid)
        assert w.print_current_labels() is True
        assert received == [uid]
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
        # 分类字段已迁到独立的「分类标签」卡片（TaxonCardPanel）；元数据卡不再持有。
        from app.widgets.taxon_card_panel import TaxonCardPanel
        tc = TaxonCardPanel(ctx)
        tc.load_specimen(sp)
        assert tc.field_values()["scientific_name"] == "Conus textile"


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


class TestLabelsUidSelection:
    def test_select_uid_selects_only_matching_specimen(self, tmp_path):
        from app.views.labels_view import LabelsView
        project_dir = str(tmp_path)
        db = _make_db(str(tmp_path / "project.db"))
        uid1 = "FJ-XM-B2-DLC001-T95E-20260601"
        uid2 = "FJ-XM-B2-BLC001-T95E-20260601"
        for uid, sid in ((uid1, "DLC001"), (uid2, "BLC001")):
            db.execute(
                """
                INSERT INTO specimens (
                    uid, id, province, site, station, storage,
                    collection_date, photo_date, owner_project_dir
                )
                VALUES (?, ?, 'FJ', 'XM', 'B2', 'T95E', '20260601', '20260601', ?)
                """,
                (uid, sid, project_dir),
            )
        db.commit()
        ctx = _make_ctx(project_dir=project_dir, db=db)
        view = LabelsView(ctx)
        view.on_activate()
        assert view.select_uid(uid2) is True
        selected = view._step1.selected_indices()
        assert len(selected) == 1
        assert view._specimens[selected[0]]["id"] == "BLC001"
        db.close()


class TestWorkbenchQuickPrint:
    """一键直接打印: 用持久化模板直出默认打印机, 跳过预览/对话框;
    无默认打印机 / 无可打印内容时降级回标签工作室。"""

    def _wb(self, tmp_path, storage):
        from app.views.workbench_view import WorkbenchView
        project_dir = str(tmp_path)
        db = _make_db(str(tmp_path / "project.db"))
        uid = f"FJ-XM-B2-DLC001-{storage}-20260601"
        db.execute(
            """INSERT INTO specimens
               (uid, id, province, site, station, storage,
                collection_date, photo_date, owner_project_dir)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (uid, "DLC001", "FJ", "XM", "B2", storage,
             "20260601", "20260601", project_dir),
        )
        db.commit()
        ctx = _make_ctx(project_dir=project_dir, db=db)
        return WorkbenchView(ctx), ctx, uid, db

    def test_quick_print_rna_prints_two_jobs(self, tmp_path, monkeypatch):
        from PyQt6.QtPrintSupport import QPrinterInfo
        import app.utils.label_print as lp
        captured = {}

        def _fake_paint(printer, jobs, **kw):
            captured["buckets"] = [j["bucket"] for j in jobs]
            return True

        monkeypatch.setattr(QPrinterInfo, "defaultPrinterName",
                            staticmethod(lambda: "FakePrinter"))
        monkeypatch.setattr(lp, "paint_jobs", _fake_paint)
        w, ctx, uid, db = self._wb(tmp_path, "RD75E")   # R-prefix → tissue too
        assert w._quick_print_labels(uid) is True
        assert captured["buckets"] == ["sample", "tissue"]
        db.close()

    def test_quick_print_no_default_printer_returns_false(self, tmp_path, monkeypatch):
        from PyQt6.QtPrintSupport import QPrinterInfo
        monkeypatch.setattr(QPrinterInfo, "defaultPrinterName",
                            staticmethod(lambda: ""))
        w, ctx, uid, db = self._wb(tmp_path, "D95E")
        assert w._quick_print_labels(uid) is False
        db.close()

    def test_on_print_labels_falls_back_to_studio(self, tmp_path, monkeypatch):
        from PyQt6.QtPrintSupport import QPrinterInfo
        monkeypatch.setattr(QPrinterInfo, "defaultPrinterName",
                            staticmethod(lambda: ""))   # no printer → fallback
        w, ctx, uid, db = self._wb(tmp_path, "D95E")
        w._on_print_labels(uid)
        assert ctx.pending_label_uid == uid   # studio handoff set
        db.close()

    def test_on_print_labels_quick_path_no_fallback(self, tmp_path, monkeypatch):
        from PyQt6.QtPrintSupport import QPrinterInfo
        import app.utils.label_print as lp
        monkeypatch.setattr(QPrinterInfo, "defaultPrinterName",
                            staticmethod(lambda: "FakePrinter"))
        monkeypatch.setattr(lp, "paint_jobs", lambda printer, jobs, **kw: True)
        w, ctx, uid, db = self._wb(tmp_path, "D95E")
        w._on_print_labels(uid)
        # quick print succeeded → no studio handoff.
        assert ctx.pending_label_uid != uid
        db.close()


class TestWorkbenchWormsFill:
    def test_worms_fill_updates_latin_fields_not_chinese(self, tmp_path):
        from app.views.workbench_view import WorkbenchView
        project_dir = str(tmp_path)
        db = _make_db(str(tmp_path / "project.db"))
        uid = "FJ-XM-B2-DLC001-T95E-20260601"
        db.execute(
            """
            INSERT INTO specimens (
                uid, id, owner_project_dir, scientific_name_cn,
                family_cn, raw_json
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                uid,
                "DLC001",
                project_dir,
                "中文种名",
                "中文科名",
                json.dumps({"scientificNameCn": "中文种名", "familyCn": "中文科名"}, ensure_ascii=False),
            ),
        )
        db.commit()
        ctx = _make_ctx(project_dir=project_dir, db=db)
        w = WorkbenchView(ctx)
        w._current_uid = uid
        filled_uid = w.worms_fill_specimen({
            "AphiaID": 123,
            "scientificname": "Diopatra cuprea",
            "class": "Polychaeta",
            "order": "Eunicida",
            "family": "Onuphidae",
            "genus": "Diopatra",
            "status": "accepted",
        })
        assert filled_uid == uid
        row = db.execute("SELECT * FROM specimens WHERE uid = ?", (uid,)).fetchone()
        assert row["scientific_name"] == "Diopatra cuprea"
        assert row["taxon_group"] == "Polychaeta"
        assert row["order_name"] == "Eunicida"
        assert row["family"] == "Onuphidae"
        assert row["genus"] == "Diopatra"
        assert row["scientific_name_cn"] == "中文种名"
        assert row["family_cn"] == "中文科名"
        raw = json.loads(row["raw_json"])
        assert raw["worms_aphia_id"] == 123
        assert raw["scientificNameCn"] == "中文种名"
        assert raw["familyCn"] == "中文科名"
        assert raw["taxonomyConfirmed"] is False
        db.close()


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

    def test_tiff_delete_asks_confirm_then_deletes(self, tmp_path):
        """TIFF 可删（用户推翻旧「TIFF 永不删」UI 封锁）：删前弹确认框，确认才删。"""
        from app.widgets.monitor_panel import MonitorPanel, _FileCard
        ctx = _make_ctx()
        w = MonitorPanel(ctx)

        tif = tmp_path / "result.tif"
        tif.write_bytes(b"II*\x00")

        class _Entry:
            path = str(tif)
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
        with patch.object(QMessageBox, "question",
                          return_value=QMessageBox.StandardButton.Yes) as mq:
            w._on_delete_clicked()
            mq.assert_called_once()      # 弹了确认框
        assert not tif.exists()          # 确认 → 真删

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

    def test_method_radios_replicate_helicon_desktop(self):
        # Strict replication of Helicon Focus desktop: three rendering-method
        # radios whose labels describe the algorithm (weighted average / depth
        # map / pyramid), not bare A/B/C.
        from app.widgets.helicon_params_panel import HeliconParamsPanel
        w = HeliconParamsPanel()
        labels = [rb.text().lower() for rb in w._method_radios]
        assert len(labels) == 3
        assert "weighted average" in labels[0]
        assert "depth map" in labels[1]
        assert "pyramid" in labels[2]

    def test_method_c_disables_radius_but_keeps_value(self):
        # Helicon: Radius is used only by methods A/B; the desktop greys it out
        # for Method C. The stored value must survive so it persists/round-trips.
        from app.widgets.helicon_params_panel import HeliconParamsPanel
        w = HeliconParamsPanel()
        w.set_params({"method": 2, "radius": 7, "smoothing": 5})
        assert not w._radius_slider.isEnabled()
        assert not w._radius_spin.isEnabled()
        assert w._smooth_slider.isEnabled()  # smoothing still applies to C
        assert w.get_params()["radius"] == 7
        # switching back to A re-enables radius
        w.set_params({"method": 0})
        assert w._radius_slider.isEnabled()

    def test_helicon_desktop_ranges(self):
        # Ranges follow Helicon Focus desktop/help, not the old web prototype
        # cap that stopped Radius at 8. Official examples use Radius=22.
        from app.widgets.helicon_params_panel import HeliconParamsPanel
        w = HeliconParamsPanel()
        assert w._radius_spin.minimum() == 1.0
        assert w._radius_spin.maximum() == 30.0
        assert w._radius_spin.singleStep() == 0.5
        assert w._smooth_spin.minimum() == 1
        assert w._smooth_spin.maximum() == 10
        # float radius round-trips
        w.set_params({"radius": 22.5})
        assert w.get_params()["radius"] == 22.5
        # whole radius returns as int -> CLI -rp:30 not -rp:30.0
        w.set_params({"radius": 30})
        assert w.get_params()["radius"] == 30
        assert isinstance(w.get_params()["radius"], int)

    def test_radius_slider_spin_synced(self):
        from app.widgets.helicon_params_panel import HeliconParamsPanel
        w = HeliconParamsPanel()
        w._radius_spin.setValue(22.5)         # float, 1-30 step 0.5
        assert w._radius_slider.value() == 45  # 22.5 x 2 (int slider scaling)
        assert w.get_params()["radius"] == 22.5
        w._smooth_spin.setValue(10)
        assert w._smooth_slider.value() == 10
        assert w.get_params()["smoothing"] == 10

    def test_reset_button_restores_defaults(self):
        # Helicon default reset -> Method B / Radius 8 / Smoothing 4.
        from app.widgets.helicon_params_panel import HeliconParamsPanel
        w = HeliconParamsPanel()
        w.set_params({"method": 0, "radius": 2, "smoothing": 1})
        fired = []
        w.params_changed.connect(lambda: fired.append(1))
        w._reset_btn.click()
        p = w.get_params()
        assert p["method"] == 1    # B (default)
        assert p["radius"] == 8
        assert p["smoothing"] == 4
        assert fired               # params_changed emitted → settings auto-save

    def test_workbench_view_has_helicon_params(self):
        """WorkbenchView must expose _helicon_params (HeliconParamsPanel)."""
        from app.views.workbench_view import WorkbenchView
        from app.widgets.helicon_params_panel import HeliconParamsPanel
        ctx = _make_ctx()
        w = WorkbenchView(ctx)
        assert hasattr(w, "_helicon_params")
        assert isinstance(w._helicon_params, HeliconParamsPanel)


class _FakeQS:
    """Minimal QSettings stand-in: dict-backed value(key, default)."""
    def __init__(self, d): self._d = d
    def value(self, k, default=None): return self._d.get(k, default)


class TestHeliconOutputWiring:
    """Output options (format / tiff_compression / quality) reach the Helicon CLI."""

    def test_with_output_ext(self):
        from app.views.workbench_view import WorkbenchView
        assert WorkbenchView._with_output_ext("/a/b/x.tif", "jpg").endswith("/x.jpg")
        assert WorkbenchView._with_output_ext("/a/b/x.tif", "tif").endswith("/x.tif")
        # non-ascii stem preserved
        assert WorkbenchView._with_output_ext("/a/自由合成-1.tif", "jpg").endswith("自由合成-1.jpg")

    def test_output_opts_tif_default(self):
        from app.views.workbench_view import WorkbenchView
        from app.views.settings_view import _K_HELICON_TIFF_COMPRESSION
        w = WorkbenchView(_make_ctx())
        w.ctx.settings._qs = _FakeQS({_K_HELICON_TIFF_COMPRESSION: "lzw"})
        o = w._helicon_output_opts()
        assert o["format"] == "tif"
        assert o["tiff_compression"] == "lzw"
        assert o["quality"] is None

    def test_output_opts_jpg(self):
        from app.views.workbench_view import WorkbenchView
        from app.views.settings_view import _K_HELICON_OUTPUT_FORMAT, _K_HELICON_QUALITY
        w = WorkbenchView(_make_ctx())
        w.ctx.settings._qs = _FakeQS({_K_HELICON_OUTPUT_FORMAT: "jpg", _K_HELICON_QUALITY: 88})
        o = w._helicon_output_opts()
        assert o["format"] == "jpg"
        assert o["quality"] == 88
        assert o["tiff_compression"] is None

    def test_opts_flow_into_cli_args(self):
        # tif → -tif:<comp> and no -j:; jpg → -j:<q> and no -tif:
        from app.services.helicon_service import build_helicon_args
        tif = build_helicon_args(["a.jpg"], "out.tif", method="1", radius="8",
                                 smoothing="4", tiff_compression="lzw", quality=None)
        assert "-tif:lzw" in tif and not any(a.startswith("-j:") for a in tif)
        jpg = build_helicon_args(["a.jpg"], "out.jpg", method="1", radius="8",
                                 smoothing="4", tiff_compression=None, quality=88)
        assert "-j:88" in jpg and not any(a.startswith("-tif:") for a in jpg)


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
        assert "shooting" in w._phase_pills
        assert "shot_done" in w._phase_pills
        assert "organizing" in w._phase_pills
        assert "done" in w._phase_pills
        assert w._phase_pills["shooting"].text() == "拍摄中"
        assert w._phase_pills["shot_done"].text() == "已拍完"
        assert w._phase_pills["organizing"].text() == "整理中"
        assert w._phase_pills["done"].text() == "完成"


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

    def test_draft_group_row_has_import_tiff_signal(self):
        """_DraftGroupRow must have import_tiff_requested signal (#cursor groupingImportTiff)."""
        from app.widgets.grouping_panel import _DraftGroupRow
        from app.services.grouping_service import Group
        g = Group(group_index=0, jpg_paths=[])
        row = _DraftGroupRow(g)
        assert hasattr(row, "import_tiff_requested")

    def test_grouping_panel_has_import_tiff_signal(self):
        """GroupingPanel must expose import_tiff_requested signal."""
        from app.widgets.grouping_panel import GroupingPanel
        ctx = _make_ctx()
        w = GroupingPanel(ctx)
        assert hasattr(w, "import_tiff_requested")


# ── groupingImportTiff (TIFF import dialog) ────────────────────────────────────

class TestTiffImportDialog:
    """Tests for _TiffImportDialog in grouping_panel."""

    def test_constructs_empty_candidates(self):
        from app.widgets.grouping_panel import _TiffImportDialog
        dlg = _TiffImportDialog(group_index=0, tiff_candidates=[])
        assert dlg is not None
        assert dlg.windowTitle() == "导入 TIF → 组 0"

    def test_constructs_with_candidates(self, tmp_path):
        tif = str(tmp_path / "test.tif")
        Path(tif).write_bytes(b"")
        from app.widgets.grouping_panel import _TiffImportDialog
        dlg = _TiffImportDialog(group_index=1, tiff_candidates=[tif])
        assert dlg._list.count() == 1
        assert dlg._list.item(0).toolTip() == tif

    def test_selected_path_empty_by_default(self):
        from app.widgets.grouping_panel import _TiffImportDialog
        dlg = _TiffImportDialog(group_index=0, tiff_candidates=[])
        assert dlg.selected_path() == ""

    def test_prefills_existing_tiff(self, tmp_path):
        tif = str(tmp_path / "old.tif")
        from app.widgets.grouping_panel import _TiffImportDialog
        dlg = _TiffImportDialog(group_index=0, tiff_candidates=[], existing_tiff=tif)
        assert dlg._path_edit.text() == tif


# ── findDuplicateSpecimen (NamingPanel dup check) ─────────────────────────────

class TestNamingPanelDupCheck:
    """Tests for _check_duplicate and _check_compliance in NamingPanel."""

    def test_no_dup_warn_when_uid_absent_from_db(self, tmp_path):
        from app.widgets.naming_panel import NamingPanel
        db = _make_db(str(tmp_path / "p.db"))
        ctx = _make_ctx(db=db)
        w = NamingPanel(ctx)
        w._check_duplicate("FJ-XM-B2-DLC001-T95E-20260601")
        assert w._dup_warn.isHidden(), "dup_warn must be hidden when UID not in DB"
        db.close()

    def test_dup_warn_shown_when_uid_exists(self, tmp_path):
        from app.widgets.naming_panel import NamingPanel
        db = _make_db(str(tmp_path / "p.db"))
        uid = "FJ-XM-B2-DLC001-T95E-20260601"
        db.execute(
            "INSERT INTO specimens (uid, owner_project_dir) VALUES (?, ?)",
            (uid, "/some/project"),
        )
        db.commit()
        ctx = _make_ctx(db=db)
        w = NamingPanel(ctx)
        w._check_duplicate(uid)
        # isHidden() is reliable even when widget has no parent window
        assert not w._dup_warn.isHidden(), "dup_warn must be shown after duplicate found"
        db.close()

    def test_compliance_no_warn_empty(self):
        from app.widgets.naming_panel import NamingPanel
        ctx = _make_ctx()
        w = NamingPanel(ctx)
        w._check_compliance("")
        assert w._compliance_warn.isHidden()

    def test_compliance_warns_bad_date(self):
        from app.widgets.naming_panel import NamingPanel
        ctx = _make_ctx()
        w = NamingPanel(ctx)
        w._province.setText("FJ")
        w._collection_date.setText("2026060")  # 7 chars, not 8
        w._check_compliance("FJ-X-B2-DLC001-T95E-2026060")
        assert not w._compliance_warn.isHidden(), "compliance_warn must be shown"
        assert "8 位" in w._compliance_warn.text()


# ── metaReverseGeocode (MetadataPanel) ───────────────────────────────────────

class TestMetadataPanelGeocode:
    """Tests for the auto reverse-geocode + map-pick UX in MetadataPanel."""

    def test_map_pick_button_exists(self):
        from app.widgets.metadata_panel import MetadataPanel
        ctx = _make_ctx()
        w = MetadataPanel(ctx)
        assert hasattr(w, "_map_btn")
        assert w._map_btn is not None

    def test_auto_reverse_invalid_coords_inline_status(self):
        """Invalid lon/lat sets an inline status, never opens a dialog."""
        from app.widgets.metadata_panel import MetadataPanel
        import unittest.mock as _mock
        ctx = _make_ctx()
        w = MetadataPanel(ctx)
        w._lon.setText("abc")
        w._lat.setText("25.6")
        with _mock.patch("app.utils.ui.warn") as warn_mock:
            w._do_auto_reverse()
            warn_mock.assert_not_called()
        assert w._geo_status.text()  # inline status set

    def test_nominatim_to_zh_parses_display(self):
        """_nominatim_to_zh must extract place name from Nominatim response."""
        from app.widgets.metadata_panel import _nominatim_to_zh
        data = {
            "display_name": "鼓浪屿, 厦门市, 福建省, 中国",
        }
        result = _nominatim_to_zh(data)
        assert result  # non-empty

    def test_nominatim_to_zh_empty_input(self):
        from app.widgets.metadata_panel import _nominatim_to_zh
        assert _nominatim_to_zh({}) == ""
        assert _nominatim_to_zh(None) == ""


# ── Pre-compose preview dialog ────────────────────────────────────────────────

class TestComposePreviewDialog:
    """Tests for _show_compose_preview in WorkbenchView (#cursor renderComposePreviewModal)."""

    def test_show_compose_preview_exists(self, tmp_path):
        from app.views.workbench_view import WorkbenchView
        project_dir = str(tmp_path / "proj")
        Path(project_dir).mkdir(parents=True)
        (Path(project_dir) / "incoming-jpg").mkdir()
        (Path(project_dir) / "results").mkdir()
        (Path(project_dir) / "_data").mkdir()
        db = _make_db(str(tmp_path / "proj/_data/project.db"))
        ctx = _make_ctx(project_dir=project_dir, db=db)
        w = WorkbenchView(ctx)
        assert hasattr(w, "_show_compose_preview")
        assert callable(w._show_compose_preview)
        db.close()

    def test_compose_workbench_dialog_defaults(self, tmp_path, qapp):
        from app.views.workbench_view import _ComposeWorkbenchDialog

        jpg1 = tmp_path / "a.jpg"
        jpg2 = tmp_path / "b.jpg"
        tiff = tmp_path / "out.tif"
        jpg1.write_bytes(b"jpg1")
        jpg2.write_bytes(b"jpg2")
        tiff.write_bytes(b"tiff")

        dlg = _ComposeWorkbenchDialog(
            [str(jpg1), str(jpg2)],
            str(tiff),
            {"method": 1, "radius": 4.5, "smoothing": 3},
            angle_label="背面",
        )

        assert dlg.windowTitle() == "合成工作台"
        assert dlg.selected_jpgs() == [str(jpg1), str(jpg2)]
        assert dlg.params()["method"] == 1
        # Helicon radius is float (step 0.5) and fractional values round-trip.
        assert dlg.params()["radius"] == 4.5
        assert dlg.params()["smoothing"] == 3


# ── _BatchResultDialog ────────────────────────────────────────────

class TestBatchResultDialog:
    """Tests for _BatchResultDialog and FileResult in workbench_view / retroactive_service."""

    def test_batch_result_dialog_row_count(self):
        """3 FileResult items → table has 3 rows."""
        from app.services.retroactive_service import FileResult
        from app.views.workbench_view import _BatchResultDialog
        results = [
            FileResult(name="a.jpg", ok=True, size_bytes=1024, error=""),
            FileResult(name="b.jpg", ok=True, size_bytes=2048, error=""),
            FileResult(name="c.jpg", ok=False, size_bytes=0, error="打包失败"),
        ]
        dlg = _BatchResultDialog(results)
        assert dlg._table.rowCount() == 3

    def test_batch_result_dialog_summary(self):
        """2 ok 1 fail → summary label shows correct counts."""
        from app.services.retroactive_service import FileResult
        from app.views.workbench_view import _BatchResultDialog
        results = [
            FileResult(name="a.jpg", ok=True, size_bytes=1024, error=""),
            FileResult(name="b.jpg", ok=True, size_bytes=2048, error=""),
            FileResult(name="c.jpg", ok=False, size_bytes=0, error="失败"),
        ]
        dlg = _BatchResultDialog(results)
        text = dlg._summary.text()
        assert "2" in text
        assert "1" in text

    def test_batch_result_dialog_constructs_empty(self):
        """_BatchResultDialog with empty list must not crash."""
        from app.views.workbench_view import _BatchResultDialog
        dlg = _BatchResultDialog([])
        assert dlg._table.rowCount() == 0

    def test_file_result_fields(self):
        """FileResult must have name, ok, size_bytes, error fields."""
        from app.services.retroactive_service import FileResult
        r = FileResult(name="x.jpg", ok=True, size_bytes=512, error="")
        assert r.name == "x.jpg"
        assert r.ok is True
        assert r.size_bytes == 512
        assert r.error == ""


# ── Retroactive subdir selector ──────────────────────────────────────────────

class TestRetroactiveSubdirSelector:
    """_RetroactiveScanDialog must expose a subdir combo populated from results/."""

    def test_subdir_dialog_constructs(self, tmp_path):
        """_RetroactiveScanDialog must construct without error."""
        from app.views.workbench_view import _RetroactiveScanDialog
        project_dir = str(tmp_path)
        (tmp_path / "results").mkdir()
        dlg = _RetroactiveScanDialog(project_dir)
        assert dlg is not None

    def test_subdir_combo_has_all_option(self, tmp_path):
        """Combo must include '全部' as the first option (data=None)."""
        from app.views.workbench_view import _RetroactiveScanDialog
        project_dir = str(tmp_path)
        (tmp_path / "results").mkdir()
        dlg = _RetroactiveScanDialog(project_dir)
        assert dlg._subdir_combo.itemText(0) == "全部"
        assert dlg._subdir_combo.itemData(0) is None

    def test_subdir_combo_populated_with_subdirs(self, tmp_path):
        """Combo must list subdirectories of results/ alphabetically."""
        from app.views.workbench_view import _RetroactiveScanDialog
        project_dir = str(tmp_path)
        results = tmp_path / "results"
        results.mkdir()
        (results / "alpha").mkdir()
        (results / "beta").mkdir()
        (results / "not_a_dir.txt").write_bytes(b"")
        dlg = _RetroactiveScanDialog(project_dir)
        items = [dlg._subdir_combo.itemText(i) for i in range(dlg._subdir_combo.count())]
        assert "全部" in items
        assert "alpha" in items
        assert "beta" in items
        assert "not_a_dir.txt" not in items

    def test_selected_subdir_none_for_all(self, tmp_path):
        """selected_subdir() must return None when '全部' is chosen."""
        from app.views.workbench_view import _RetroactiveScanDialog
        project_dir = str(tmp_path)
        (tmp_path / "results").mkdir()
        dlg = _RetroactiveScanDialog(project_dir)
        dlg._subdir_combo.setCurrentIndex(0)
        assert dlg.selected_subdir() is None

    def test_selected_subdir_returns_name(self, tmp_path):
        """selected_subdir() must return the directory name when one is chosen."""
        from app.views.workbench_view import _RetroactiveScanDialog
        project_dir = str(tmp_path)
        results = tmp_path / "results"
        results.mkdir()
        (results / "week01").mkdir()
        dlg = _RetroactiveScanDialog(project_dir)
        idx = dlg._subdir_combo.findText("week01")
        assert idx >= 0
        dlg._subdir_combo.setCurrentIndex(idx)
        assert dlg.selected_subdir() == "week01"


# ── Collab post_photo_index wiring ────────────────────────────────────────────

class TestCollabPostPhotoIndex:
    """WorkbenchView must call collab_service.post_photo_index after compose/organize."""

    def _make_workbench_with_collab(self, tmp_path):
        from app.views.workbench_view import WorkbenchView
        project_dir = str(tmp_path / "proj")
        Path(project_dir).mkdir(parents=True)
        (Path(project_dir) / "incoming-jpg").mkdir()
        (Path(project_dir) / "results").mkdir()
        (Path(project_dir) / "_data").mkdir()
        db = _make_db(str(tmp_path / "proj/_data/project.db"))
        ctx = _make_ctx(project_dir=project_dir, db=db)
        collab = MagicMock()
        ctx.collab_service = collab
        w = WorkbenchView(ctx)
        return w, collab, db

    def test_post_photo_index_called_after_helicon_finish(self, tmp_path):
        """_on_helicon_finished must call collab_service.post_photo_index(uid, 'tiff')."""
        w, collab, db = self._make_workbench_with_collab(tmp_path)
        uid = "FJ-XM-B2-DLC001-T95E-20260601"
        w._current_uid = uid
        w._on_helicon_finished(uid)
        collab.post_photo_index.assert_called_once_with(uid, "tiff")
        db.close()

    def test_post_photo_index_called_after_organize(self, tmp_path):
        """_on_organize_finished must call collab_service.post_photo_index(uid, 'zip')."""
        w, collab, db = self._make_workbench_with_collab(tmp_path)
        uid = "FJ-XM-B2-DLC001-T95E-20260601"
        w._current_uid = uid
        w._on_organize_finished(uid)
        collab.post_photo_index.assert_called_once_with(uid, "zip")
        db.close()

    def test_post_photo_index_no_crash_when_no_collab(self, tmp_path):
        """Must not crash when collab_service is None."""
        from app.views.workbench_view import WorkbenchView
        project_dir = str(tmp_path / "proj2")
        Path(project_dir).mkdir(parents=True)
        db = _make_db(str(tmp_path / "proj2" / "project.db"))
        ctx = _make_ctx(project_dir=project_dir, db=db)
        ctx.collab_service = None
        w = WorkbenchView(ctx)
        w._current_uid = "FJ-XM-B2-DLC001-T95E-20260601"
        w._on_helicon_finished("FJ-XM-B2-DLC001-T95E-20260601")
        w._on_organize_finished("FJ-XM-B2-DLC001-T95E-20260601")
        db.close()

    def test_post_photo_index_no_crash_on_collab_exception(self, tmp_path):
        """Must silently swallow exceptions from post_photo_index."""
        w, collab, db = self._make_workbench_with_collab(tmp_path)
        collab.post_photo_index.side_effect = RuntimeError("network gone")
        uid = "FJ-XM-B2-DLC001-T95E-20260601"
        w._current_uid = uid
        w._on_helicon_finished(uid)
        w._on_organize_finished(uid)
        db.close()


# ── Right-rail web-faithful structure (1:1 还原右侧栏) ─────────────────────────

class TestRightRailWebFaithful:
    """卡1 命名 / 卡2 分类 / 卡3 元数据 field ownership mirrors the web right rail.

    Oracle: renderNamingCard (app.js:9147), renderTaxonNotesCard (9933),
    renderMetaCard (10203).  日期/保存方式/拍照备注→卡1; 备注→卡2; 卡3 扁平且无
    保存按钮(编辑即存).
    """

    def test_naming_card_has_photo_notes_and_no_extras(self):
        from app.widgets.naming_panel import NamingPanel
        n = NamingPanel(_make_ctx())
        # 拍照备注 textarea added (was missing)
        assert hasattr(n, "_photo_notes")
        # 保存方式说明灰字 row present
        assert hasattr(n, "_pres_detail")
        # storage free-text proxy is hidden (no 自定义编码 field in web)
        assert n._storage.isHidden()
        # 成果编号(含序号) preview is not shown in the web naming card
        assert n._result_preview.isHidden()

    def test_metadata_card_stripped_to_web_fields(self):
        from app.widgets.metadata_panel import MetadataPanel
        m = MetadataPanel(_make_ctx())
        # dates / storage / notes / photo_notes / score ring moved out of 卡3
        for gone in ("_collection_date", "_photo_date", "_storage",
                     "_notes", "_photo_notes", "_save_btn", "_score_ring"):
            assert not hasattr(m, gone), f"metadata panel must not own {gone}"
        # keeps its core web fields
        for kept in ("_collector", "_photographer", "_identifier",
                     "_lon", "_lat", "_geo_area"):
            assert hasattr(m, kept)

    def test_metadata_autosave_emits_change_no_save_button(self, qtbot):
        from app.widgets.metadata_panel import MetadataPanel
        m = MetadataPanel(_make_ctx())
        qtbot.addWidget(m)
        m._uid = "FJ-XM-B2-DLC001-T95E-20260601"
        seen = []
        m.metadata_changed.connect(lambda u, f, v: seen.append((f, v)))
        m._collector.setText("X")  # programmatic
        m._on_field_edited("collector", "X")
        assert ("collector", "X") in seen

    def test_taxon_card_owns_notes(self):
        from app.widgets.taxon_card_panel import TaxonCardPanel
        t = TaxonCardPanel(_make_ctx())
        assert hasattr(t, "_notes")
        t._notes.setPlainText("野外备注")
        assert t.field_values().get("notes") == "野外备注"

    def test_rail_autosave_persists_across_three_cards(self, tmp_path):
        from app.views.workbench_view import WorkbenchView
        project_dir = str(tmp_path)
        db = _make_db(str(tmp_path / "project.db"))
        uid = "FJ-XM-B2-DLC001-T95E-20260601"
        db.execute(
            "INSERT INTO specimens (uid, id, owner_project_dir) VALUES (?,?,?)",
            (uid, "DLC001", project_dir),
        )
        db.commit()
        ctx = _make_ctx(project_dir=project_dir, db=db)
        w = WorkbenchView(ctx)
        w._load_specimen(uid)
        w._naming._photo_notes.setPlainText("PN")
        w._naming._collection_date.setText("20260101")
        w._metadata._collector.setText("COLL")
        w._taxon_card._notes.setPlainText("NOTE")
        w._taxon_card._cn["family_cn"].setText("芋螺科")
        w._flush_rail_save()
        row = db.execute(
            "SELECT collector, collection_date, photo_notes, notes, family_cn "
            "FROM specimens WHERE uid=?", (uid,)
        ).fetchone()
        assert row["collector"] == "COLL"
        assert row["collection_date"] == "20260101"
        assert row["photo_notes"] == "PN"
        assert row["notes"] == "NOTE"
        assert row["family_cn"] == "芋螺科"
        db.close()



# ── 补处理 (supplementary archival) integration ───────────────────────────────

class TestSupplementaryArchival:
    """End-to-end glue for 补处理: validate → archive → move to results/.

    Core requirement: works with NO active specimen (no tasks row).
    ui.warn / ui.info are patched away — they pop modal boxes that would hang
    the offscreen test runner.
    """

    def _project_with_specimen(self, tmp_path):
        proj = str(tmp_path / "proj")
        os.makedirs(os.path.join(proj, "incoming-jpg"), exist_ok=True)
        db = _make_db(os.path.join(proj, "project.db"))
        # Specimen exists; NO tasks row → nothing activated.
        db.execute("INSERT INTO specimens (uid) VALUES (?)",
                   ("FJ-XM-B2-DLC001-T95E-20260601",))
        db.commit()
        return proj, db

    def test_invalid_selection_no_worker(self, qt_app, tmp_path):
        from unittest.mock import patch
        from app.views.workbench_view import WorkbenchView
        proj, db = self._project_with_specimen(tmp_path)
        ctx = _make_ctx(proj, db)
        ctx.settings.delete_jpg_after_archive = False
        w = WorkbenchView(ctx)
        jpg = os.path.join(proj, "incoming-jpg", "a.jpg")
        Path(jpg).write_bytes(b"x")
        # A lone JPG (no TIFF) is invalid → SuppGroupError → no worker spawned.
        with patch("app.utils.ui.warn"), patch("app.utils.ui.info"):
            w._run_supplementary([jpg])
        assert getattr(w, "_supp_worker", None) is None
        db.close()

    def test_no_active_specimen_spawns_worker(self, qt_app, tmp_path):
        """Valid JPG+TIFF, specimen exists, NOTHING activated → worker starts."""
        from unittest.mock import patch, MagicMock
        from app.views.workbench_view import WorkbenchView
        proj, db = self._project_with_specimen(tmp_path)
        ctx = _make_ctx(proj, db)
        ctx.settings.delete_jpg_after_archive = False
        w = WorkbenchView(ctx)
        incoming = os.path.join(proj, "incoming-jpg")
        jpg = os.path.join(incoming, "a.jpg")
        tiff = os.path.join(incoming, "FJ-XM-B2-DLC001-1-T95E-20260601.tif")
        Path(jpg).write_bytes(b"x")
        Path(tiff).write_bytes(b"x")
        assert db.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 0
        # Stub the worker so nothing actually compresses; assert it was created.
        with patch("app.workers.supp_compression_worker.SuppCompressionWorker") as MW, \
                patch("app.utils.ui.warn"), patch("app.utils.ui.info"):
            inst = MagicMock()
            MW.return_value = inst
            w._run_supplementary([jpg, tiff])
        inst.start.assert_called_once()
        assert w._supp_pending is not None
        assert w._supp_pending.uid == "FJ-XM-B2-DLC001-T95E-20260601"
        db.close()

    def test_finished_moves_tiff_and_zip_to_results(self, qt_app, tmp_path):
        """_on_supp_finished moves both the TIFF and ZIP into results/ (decision①)."""
        from unittest.mock import patch, MagicMock
        from app.views.workbench_view import WorkbenchView
        from app.services.supplementary_service import SuppGroup

        proj, db = self._project_with_specimen(tmp_path)
        ctx = _make_ctx(proj, db)
        w = WorkbenchView(ctx)

        incoming = os.path.join(proj, "incoming-jpg")
        tiff = os.path.join(incoming, "FJ-XM-B2-DLC001-1-T95E-20260601.tif")
        zip_ = os.path.join(incoming, "FJ-XM-B2-DLC001-1-T95E-20260601.zip")
        Path(tiff).write_bytes(b"tiffdata")
        Path(zip_).write_bytes(b"zipdata-zipdata-zipdata-zipdata")

        w._supp_pending = SuppGroup(
            jpg_paths=[os.path.join(incoming, "a.jpg")],
            tiff_path=tiff,
            uid="FJ-XM-B2-DLC001-T95E-20260601",
            specimen={"uid": "FJ-XM-B2-DLC001-T95E-20260601"},
        )

        result = MagicMock()
        result.ok = True
        result.zip_path = zip_
        result.saved_percent = 42
        result.delete_jpg = False
        result.requested_delete_jpg = False
        result.deletion_skipped_reason = ""

        with patch("app.utils.ui.info"), patch("app.utils.ui.warn"):
            w._on_supp_finished(result)

        results_dir = os.path.join(proj, "results")
        assert os.path.isfile(os.path.join(results_dir, os.path.basename(tiff))), \
            "TIFF must be moved into results/"
        assert os.path.isfile(os.path.join(results_dir, os.path.basename(zip_))), \
            "ZIP must be moved into results/"
        # Source TIFF moved (data preserved at dest, never destroyed).
        assert not os.path.isfile(tiff)
        db.close()


# ── 阶段按钮 → collab store + DB 持久化接线 ──────────────────────────────────

class TestPhasePillWiring:
    """点击 pill → TaskStore + tasks.raw_json + pill 高亮三处一致;
    重启(空 store)后由 DB 回读;非法迁移不崩溃。"""

    def _make_view(self, tmp_path, db=None):
        from app.views.workbench_view import WorkbenchView
        from app.services.collab_service import CollabService
        if db is None:
            db = _make_db(":memory:")
        ctx = _make_ctx(project_dir=str(tmp_path), db=db)
        ctx.collab_service = CollabService()
        return WorkbenchView(ctx), ctx, db

    def test_phase_click_updates_store_db_and_pill(self, tmp_path):
        from app.services import activation_service
        from app.services.collab_service import TaskStatus
        w, ctx, db = self._make_view(tmp_path)
        activation_service.activate(str(tmp_path), db, "U1")
        w._refresh_batch_header()

        w._on_phase_clicked("shooting")

        assert ctx.collab_service.store.get("U1").status is TaskStatus.SHOOTING
        assert activation_service.get_collab_status(db, "U1") == "shooting"
        assert w._monitor._phase_pills["shooting"].isChecked()

    def test_phase_readback_from_db_after_restart(self, tmp_path):
        from app.services import activation_service
        db = _make_db(":memory:")
        activation_service.activate(str(tmp_path), db, "U1")
        activation_service.set_collab_status(db, "U1", "organizing")

        # 新实例 = 模拟重启:TaskStore 为空,只剩 DB 里的状态
        w, ctx, _ = self._make_view(tmp_path, db=db)
        w._refresh_batch_header()

        assert w._monitor._phase_pills["organizing"].isChecked()

    def test_pill_jump_allowed_via_force(self, tmp_path):
        """批次条 pill = 人工标记,force=True 放开状态机:SHOOTING→DONE 跳格成功。

        (服务层默认 force=False 的严格状态机仍由 test_collab_service 守住。)
        """
        from app.services import activation_service
        from app.services.collab_service import TaskStatus
        w, ctx, db = self._make_view(tmp_path)
        activation_service.activate(str(tmp_path), db, "U1")
        w._refresh_batch_header()
        w._on_phase_clicked("shooting")

        w._on_phase_clicked("done")  # 跳格,人工标记应成功

        assert ctx.collab_service.store.get("U1").status is TaskStatus.DONE
        assert activation_service.get_collab_status(db, "U1") == "done"
        assert w._monitor._phase_pills["done"].isChecked()
        assert not w._monitor._phase_pills["shooting"].isChecked()

    def test_click_without_active_uid_is_noop(self, tmp_path):
        w, ctx, db = self._make_view(tmp_path)
        w._on_phase_clicked("shooting")  # 无激活编号,不应崩溃
        assert ctx.collab_service.store.get("shooting") is None
        assert all(not b.isChecked() for b in w._monitor._phase_pills.values())

    # ── _on_phase_mark: 侧边栏点点 → 标记任意编号(无需激活) ──────────────────

    def test_mark_non_active_uid_persists(self, tmp_path):
        """对非激活编号标记阶段成功,且不影响当前激活编号。"""
        from app.services import activation_service
        from app.services.collab_service import TaskStatus
        w, ctx, db = self._make_view(tmp_path)
        activation_service.activate(str(tmp_path), db, "ACTIVE")  # 激活另一个

        w._on_phase_mark("OTHER", "organizing")  # OTHER 未激活

        assert activation_service.get_collab_status(db, "OTHER") == "organizing"
        assert ctx.collab_service.store.get("OTHER").status is TaskStatus.ORGANIZING
        # 激活编号未被改动 / 仍激活
        assert activation_service.get_active_uid(db) == "ACTIVE"

    def test_mark_does_not_require_activation(self, tmp_path):
        """无任何激活编号时,点点仍能标记。"""
        from app.services import activation_service
        w, ctx, db = self._make_view(tmp_path)
        assert activation_service.get_active_uid(db) is None

        w._on_phase_mark("SOLO", "shooting")

        assert activation_service.get_collab_status(db, "SOLO") == "shooting"

    def test_mark_backward_allowed_via_force(self, tmp_path):
        """完成→整理中 回退,人工标记应成功(状态机本禁回退)。"""
        from app.services import activation_service
        from app.services.collab_service import TaskStatus
        w, ctx, db = self._make_view(tmp_path)
        w._on_phase_mark("B", "shooting")
        w._on_phase_mark("B", "shot_done")
        w._on_phase_mark("B", "organizing")
        w._on_phase_mark("B", "done")
        assert ctx.collab_service.store.get("B").status is TaskStatus.DONE

        w._on_phase_mark("B", "organizing")  # 回退

        assert ctx.collab_service.store.get("B").status is TaskStatus.ORGANIZING
        assert activation_service.get_collab_status(db, "B") == "organizing"


# ── 场景2：激活即置「拍摄中」+ 切换激活号提醒（对齐 oracle app.js:3517-3556） ──


class TestActivateBehaviour:
    def _make_view(self, tmp_path):
        from app.views.workbench_view import WorkbenchView
        from app.services.collab_service import CollabService
        project_dir = str(tmp_path / "proj")
        Path(project_dir, "_data").mkdir(parents=True)
        db = _make_db(str(tmp_path / "proj" / "_data" / "project.db"))
        ctx = _make_ctx(project_dir=project_dir, db=db)
        ctx.collab_service = CollabService()
        return WorkbenchView(ctx), ctx, db

    def test_activate_sets_shooting_when_no_phase(self, tmp_path):
        from app.services import activation_service
        from app.services.collab_service import TaskStatus
        w, ctx, db = self._make_view(tmp_path)
        w._on_sidebar_activate("FJ-XM-B2-AAA001-T95E-20260601")
        uid = "FJ-XM-B2-AAA001-T95E-20260601"
        assert ctx.collab_service.store.get(uid).status is TaskStatus.SHOOTING
        assert activation_service.get_collab_status(db, uid) == "shooting"

    def test_activate_keeps_existing_later_phase(self, tmp_path):
        from app.services.collab_service import TaskStatus
        w, ctx, db = self._make_view(tmp_path)
        uid = "FJ-XM-B2-AAA001-T95E-20260601"
        # 推进到 organizing
        for s in ("shooting", "shot_done", "organizing"):
            w._on_phase_mark(uid, s)
        w._on_sidebar_activate(uid)
        # 激活不得把已有更高阶段重置回 shooting
        assert ctx.collab_service.store.get(uid).status is TaskStatus.ORGANIZING

    def test_switch_active_warns_old_keeps_photos(self, tmp_path, monkeypatch):
        w, ctx, db = self._make_view(tmp_path)
        msgs = []
        monkeypatch.setattr(w, "_status_message",
                            lambda *a, **k: msgs.append(a[0] if a else ""))
        w._on_sidebar_activate("FJ-XM-B2-AAA001-T95E-20260601")
        w._on_sidebar_activate("FJ-XM-B2-BBB002-T95E-20260601")  # 切号
        assert any("仍归旧号" in m for m in msgs)
        assert any("AAA001" in m for m in msgs)  # 提到旧号短码

    def test_first_activate_no_switch_warning(self, tmp_path, monkeypatch):
        w, ctx, db = self._make_view(tmp_path)
        msgs = []
        monkeypatch.setattr(w, "_status_message",
                            lambda *a, **k: msgs.append(a[0] if a else ""))
        w._on_sidebar_activate("FJ-XM-B2-AAA001-T95E-20260601")  # 首次激活
        assert not any("仍归旧号" in m for m in msgs)


# ── 场景1 修复1：保存按钮 = 存全部（命名 + metadata 一并入库） ───────────────


class TestSaveButtonPersistsMetadata:
    """新号「先填 metadata 再点保存」时，采集人/经纬度/地理区不能丢。

    旧 bug：_on_naming_save 只写命名段；metadata autosave 因新草稿
    _current_uid=None 整段跳过 → metadata 静默丢失。修复后保存须 flush 右栏。
    """

    def _make_view(self, tmp_path):
        from app.views.workbench_view import WorkbenchView
        project_dir = str(tmp_path / "proj")
        Path(project_dir, "_data").mkdir(parents=True)
        db = _make_db(str(tmp_path / "proj" / "_data" / "project.db"))
        ctx = _make_ctx(project_dir=project_dir, db=db)
        ctx.collab_service = None  # 单机
        w = WorkbenchView(ctx)
        return w, ctx, db

    def _fill_new_specimen(self, w):
        n = w._naming
        n._province.setText("FJ"); n._site.setText("XM"); n._station.setText("B2")
        n._species_id.setText("DLC001"); n._storage.setText("T95E")
        n._collection_date.setText("20260601")
        m = w._metadata
        m._collector.setText("张三")
        m._lon.setText("119.5"); m._lat.setText("26.3")
        m._geo_area.setText("三门湾")
        return n.current_uid()

    def test_save_persists_metadata_for_new_specimen(self, tmp_path):
        w, ctx, db = self._make_view(tmp_path)
        uid = self._fill_new_specimen(w)
        assert uid

        w._on_naming_save()

        row = db.execute(
            "SELECT collector, lon, lat, geo_area FROM specimens WHERE uid=?",
            (uid,),
        ).fetchone()
        assert row is not None
        assert row["collector"] == "张三"
        assert row["lon"] == 26.3 or row["lon"] == 119.5  # 经度存入
        assert row["lon"] == 119.5
        assert row["lat"] == 26.3
        assert row["geo_area"] == "三门湾"

    def test_save_still_persists_naming_segments(self, tmp_path):
        """修复不得破坏原有命名段保存。"""
        w, ctx, db = self._make_view(tmp_path)
        uid = self._fill_new_specimen(w)
        w._on_naming_save()
        row = db.execute(
            "SELECT id, province, station, storage FROM specimens WHERE uid=?",
            (uid,),
        ).fetchone()
        assert row["id"] == "DLC001"
        assert row["province"] == "FJ"
        assert row["station"] == "B2"
        assert row["storage"] == "T95E"


# ── 场景1 修复3：新建即激活开关（默认关，opt-in） ──────────────────────────────


class TestAutoActivateOnSave:
    """设置「新建编号后自动激活」(autoActivateOnNewSpecimen) 开启时，
    保存新号即把它设为当前激活标本；默认关时不动激活（守 oracle 默认）。"""

    def _make_view(self, tmp_path):
        from app.views.workbench_view import WorkbenchView
        project_dir = str(tmp_path / "proj")
        Path(project_dir, "_data").mkdir(parents=True)
        db = _make_db(str(tmp_path / "proj" / "_data" / "project.db"))
        ctx = _make_ctx(project_dir=project_dir, db=db)
        ctx.collab_service = None
        w = WorkbenchView(ctx)
        return w, ctx, db

    def _fill(self, w):
        n = w._naming
        n._province.setText("FJ"); n._site.setText("XM"); n._station.setText("B2")
        n._species_id.setText("DLC001"); n._storage.setText("T95E")
        n._collection_date.setText("20260601")
        return n.current_uid()

    def test_on_activates_saved_specimen(self, tmp_path):
        from app.services import activation_service
        w, ctx, db = self._make_view(tmp_path)
        ctx.settings.auto_activate_on_new_specimen = True
        uid = self._fill(w)
        w._on_naming_save()
        assert activation_service.get_active_uid(db) == uid

    def test_off_leaves_no_active(self, tmp_path):
        from app.services import activation_service
        w, ctx, db = self._make_view(tmp_path)
        ctx.settings.auto_activate_on_new_specimen = False
        uid = self._fill(w)
        w._on_naming_save()
        assert activation_service.get_active_uid(db) is None


# ── QFileSystemWatcher integration ───────────────────────────────────


class TestFileSystemWatcher:
    """QFileSystemWatcher replaces 2 s polling with OS-level events."""

    @staticmethod
    def _make_view(tmp_path):
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
        return w, ctx, db

    def test_watcher_exists(self, tmp_path):
        w, _, db = self._make_view(tmp_path)
        assert hasattr(w, "_fs_watcher")
        db.close()

    def test_watcher_has_directory_changed_signal(self, tmp_path):
        w, _, db = self._make_view(tmp_path)
        # directoryChanged signal should be connected
        assert w._fs_watcher.receivers(w._fs_watcher.directoryChanged) > 0
        db.close()

    def test_debounce_timer_is_single_shot(self, tmp_path):
        w, _, db = self._make_view(tmp_path)
        assert w._debounce_timer.isSingleShot() is True
        db.close()

    def test_debounce_interval_300ms(self, tmp_path):
        w, _, db = self._make_view(tmp_path)
        assert w._debounce_timer.interval() == 300
        db.close()

    def test_fallback_interval_30s(self, tmp_path):
        w, _, db = self._make_view(tmp_path)
        assert w._fallback_timer.interval() == 30000
        db.close()

    def test_on_activate_watches_directories(self, tmp_path):
        w, _, db = self._make_view(tmp_path)
        w.on_activate()
        dirs = w._fs_watcher.directories()
        assert len(dirs) >= 2
        assert any("incoming-jpg" in d for d in dirs)
        assert any("results" in d for d in dirs)
        db.close()

    def test_on_activate_starts_fallback_timer(self, tmp_path):
        w, _, db = self._make_view(tmp_path)
        w.on_activate()
        assert w._fallback_timer.isActive()
        db.close()

    def test_on_deactivate_clears_watcher(self, tmp_path):
        w, _, db = self._make_view(tmp_path)
        w.on_activate()
        w.on_deactivate()
        assert not w._fs_watcher.directories()
        assert not w._fallback_timer.isActive()
        assert not w._debounce_timer.isActive()
        db.close()

    def test_on_fs_changed_starts_debounce(self, tmp_path):
        w, _, db = self._make_view(tmp_path)
        w.on_activate()
        # Stop any existing debounce from on_activate
        w._debounce_timer.stop()
        assert not w._debounce_timer.isActive()
        w._on_fs_changed("/fake/path")
        assert w._debounce_timer.isActive()
        db.close()

    def test_on_fs_changed_does_not_restart_running_debounce(self, tmp_path):
        """If debounce already active, don't reset its countdown."""
        w, _, db = self._make_view(tmp_path)
        w.on_activate()
        assert w._debounce_timer.isActive()
        remaining = w._debounce_timer.remainingTime()
        w._on_fs_changed("/fake/path")
        # Timer should still be running with same remaining time (not restarted)
        assert w._debounce_timer.isActive()
        # remainingTime should be roughly the same (within 50ms tolerance)
        assert abs(w._debounce_timer.remainingTime() - remaining) < 50
        db.close()

    def test_creates_missing_directories(self, tmp_path):
        """Watched dirs are auto-created if absent (new project)."""
        from app.views.workbench_view import WorkbenchView
        project_dir = str(tmp_path / "newproj")
        Path(project_dir).mkdir(parents=True)
        (Path(project_dir) / "_data").mkdir()
        db_path = str(tmp_path / "newproj" / "_data" / "project.db")
        db = _make_db(db_path)
        ctx = _make_ctx(project_dir=project_dir, db=db)
        w = WorkbenchView(ctx)
        # incoming-jpg/ and results/ don't exist yet
        w.on_activate()
        assert (Path(project_dir) / "incoming-jpg").is_dir()
        assert (Path(project_dir) / "results").is_dir()
        db.close()

    def test_no_old_auto_refresh_timer(self, tmp_path):
        """_auto_refresh_timer should no longer exist."""
        w, _, db = self._make_view(tmp_path)
        assert not hasattr(w, "_auto_refresh_timer")
        db.close()


# ── 外部TIFF：整理时检测命名不规范 → 确认改名（触发点1） ──────────────────────


class TestOpenGroupingLoadsActive:
    """打开分组工具时, 若面板未绑标本 → 自动载入激活/当前编号, 让「新组」立即可用。"""

    def test_open_loads_active_uid(self, tmp_path):
        from app.views.workbench_view import WorkbenchView
        from app.services import activation_service
        project_dir = str(tmp_path / "proj")
        Path(project_dir, "_data").mkdir(parents=True)
        db = _make_db(str(tmp_path / "proj" / "_data" / "project.db"))
        ctx = _make_ctx(project_dir=project_dir, db=db)
        ctx.collab_service = None
        w = WorkbenchView(ctx)
        activation_service.activate(project_dir, db, "FJ-XM-B2-DLC001-T95E-20260601")
        w._grouping.clear()                       # 面板未绑标本
        assert getattr(w._grouping, "_uid", None) is None

        w._on_open_grouping()

        assert w._grouping._uid == "FJ-XM-B2-DLC001-T95E-20260601"  # 自动载入了激活号

    def test_open_uses_naming_draft_without_activation(self, tmp_path):
        """不激活、不选中——只在右侧命名表单填了编号 → 开工具也能绑定它、可加组。"""
        from app.views.workbench_view import WorkbenchView
        from app.services import activation_service
        project_dir = str(tmp_path / "proj")
        Path(project_dir, "_data").mkdir(parents=True)
        db = _make_db(str(tmp_path / "proj" / "_data" / "project.db"))
        ctx = _make_ctx(project_dir=project_dir, db=db)
        ctx.collab_service = None
        w = WorkbenchView(ctx)
        w._grouping.clear()
        w._current_uid = None
        assert activation_service.get_active_uid(db) is None      # 没激活
        # 只填命名表单 → 实时预览编号
        n = w._naming
        n._province.setText("FJ"); n._site.setText("XM"); n._station.setText("B2")
        n._species_id.setText("DLC001"); n._storage.setText("T95E")
        n._collection_date.setText("20260601")
        uid = n.current_uid()
        assert uid

        w._on_open_grouping()
        assert w._grouping._uid == uid                            # 绑到命名草稿编号
        # 且能加组
        w._grouping._add_group()
        assert len(w._grouping._grouping.groups) == 1


class TestImplicitCompose:
    """主界面[合成] = 把激活编号下「未占用」JPG（已归属、还没进任何组）建成新组。
    占用 = 已在任何组。一次消耗一批；再拍的又是未占用 → 下次再成新组。"""

    UID = "FJ-XM-B2-DLC001-T95E-20260601"

    def _make_view(self, tmp_path, attributed):
        from app.views.workbench_view import WorkbenchView
        project_dir = str(tmp_path / "proj")
        Path(project_dir, "_data").mkdir(parents=True)
        db = _make_db(str(tmp_path / "proj" / "_data" / "project.db"))
        ctx = _make_ctx(project_dir=project_dir, db=db)
        ctx.collab_service = None
        w = WorkbenchView(ctx)
        w._get_attributed_jpg_paths = lambda uid: list(attributed)  # stub 归属扫描
        return w, ctx, db

    def test_loose_jpgs_form_new_group(self, tmp_path):
        from app.services.grouping_service import load_grouping
        j = [str(tmp_path / f"{i}.jpg") for i in range(3)]
        w, ctx, db = self._make_view(tmp_path, j)
        idx = w._build_implicit_group(self.UID)
        assert idx == 0
        g = load_grouping(db, self.UID)
        assert len(g.groups) == 1
        assert set(g.groups[0].jpg_paths) == set(j)

    def test_occupied_jpgs_excluded(self, tmp_path):
        from app.services.grouping_service import Group, save_grouping, load_grouping
        a = [str(tmp_path / f"a{i}.jpg") for i in range(2)]
        b = [str(tmp_path / f"b{i}.jpg") for i in range(2)]
        w, ctx, db = self._make_view(tmp_path, a + b)
        save_grouping(db, self.UID, [Group(group_index=0, jpg_paths=a,
                      composed_tiff_path=str(tmp_path / "t.tif"), status="composed")],
                      clean_phantoms=False)
        idx = w._build_implicit_group(self.UID)
        assert idx == 1
        g = load_grouping(db, self.UID)
        new = next(x for x in g.groups if x.group_index == 1)
        assert set(new.jpg_paths) == set(b)

    def test_no_unoccupied_returns_none(self, tmp_path):
        from app.services.grouping_service import Group, save_grouping
        a = [str(tmp_path / f"a{i}.jpg") for i in range(2)]
        w, ctx, db = self._make_view(tmp_path, a)
        save_grouping(db, self.UID, [Group(group_index=0, jpg_paths=a,
                      composed_tiff_path=str(tmp_path / "t.tif"), status="composed")],
                      clean_phantoms=False)
        assert w._build_implicit_group(self.UID) is None

    def test_fewer_than_two_returns_none(self, tmp_path):
        w, ctx, db = self._make_view(tmp_path, [str(tmp_path / "solo.jpg")])
        assert w._build_implicit_group(self.UID) is None

    def test_compose_implicit_no_active_is_noop(self, tmp_path):
        w, ctx, db = self._make_view(tmp_path, [])
        w._on_compose_implicit()  # 无激活编号, 不崩


class TestOrganizeRenamesNonconformingTiff:
    """整理一个名不符规范的 TIFF（如导入的 HeliconFocus.tif）→ 弹确认框按编号成果名
    改名；确认则改名+更新 group+继续；取消则不改名、中止整理。"""

    UID = "FJ-XM-B2-DLC001-T95E-20260601"

    def _make_view(self, tmp_path):
        from app.views.workbench_view import WorkbenchView
        project_dir = str(tmp_path / "proj")
        Path(project_dir, "_data").mkdir(parents=True)
        Path(project_dir, "results").mkdir()
        db = _make_db(str(tmp_path / "proj" / "_data" / "project.db"))
        ctx = _make_ctx(project_dir=project_dir, db=db)
        ctx.collab_service = None
        return WorkbenchView(ctx), ctx, db, project_dir

    def _setup(self, tmp_path, db, project_dir, tiff_name):
        from app.services.grouping_service import Group, save_grouping, load_grouping
        tiff = Path(project_dir) / "results" / tiff_name
        tiff.write_bytes(b"II*\x00")
        j1 = tmp_path / "a.jpg"; j1.write_bytes(b"\xff\xd8\xff")
        j2 = tmp_path / "b.jpg"; j2.write_bytes(b"\xff\xd8\xff")
        save_grouping(db, self.UID, [Group(
            group_index=0, jpg_paths=[str(j1), str(j2)],
            composed_tiff_path=str(tiff), status="composed")],
            clean_phantoms=False)
        grouping = load_grouping(db, self.UID)
        return grouping, grouping.groups[0], str(tiff)

    def test_rename_confirmed(self, tmp_path, monkeypatch):
        from PyQt6.QtWidgets import QDialog
        from app.widgets.tiff_rename_dialog import TiffRenameDialog
        w, ctx, db, project_dir = self._make_view(tmp_path)
        grouping, group, tiff = self._setup(tmp_path, db, project_dir, "HeliconFocus.tif")
        monkeypatch.setattr(TiffRenameDialog, "exec",
                            lambda self: QDialog.DialogCode.Accepted)
        res = w._maybe_rename_tiff_before_organize(db, self.UID, grouping, group, project_dir)
        assert res is True
        assert not os.path.exists(tiff)                              # 旧名没了
        assert Path(group.composed_tiff_path).name == "FJ-XM-B2-DLC001-1-T95E-20260601.tif"
        assert os.path.isfile(group.composed_tiff_path)

    def test_rename_cancelled_aborts(self, tmp_path, monkeypatch):
        from PyQt6.QtWidgets import QDialog
        from app.widgets.tiff_rename_dialog import TiffRenameDialog
        w, ctx, db, project_dir = self._make_view(tmp_path)
        grouping, group, tiff = self._setup(tmp_path, db, project_dir, "HeliconFocus.tif")
        monkeypatch.setattr(TiffRenameDialog, "exec",
                            lambda self: QDialog.DialogCode.Rejected)
        res = w._maybe_rename_tiff_before_organize(db, self.UID, grouping, group, project_dir)
        assert res is False
        assert os.path.exists(tiff)                                  # 没改名

    def test_conforming_name_noop(self, tmp_path):
        w, ctx, db, project_dir = self._make_view(tmp_path)
        grouping, group, tiff = self._setup(
            tmp_path, db, project_dir, "FJ-XM-B2-DLC001-1-T95E-20260601.tif")
        res = w._maybe_rename_tiff_before_organize(db, self.UID, grouping, group, project_dir)
        assert res is None                                           # 已规范, 不弹框
        assert os.path.exists(tiff)


# ── 场景10：撤销合成 = 删TIFF + JPG解关联放回自由池（带确认） ────────────────────


class TestUndoComposeDeletesTiff:
    """撤销合成：删除这张合成 TIFF（不可恢复，带确认）+ 把关联 JPG 解组放回自由池
    （TIFF 没了，关联失去意义）。取消则全保留。"""

    def _make_view(self, tmp_path):
        from app.views.workbench_view import WorkbenchView
        project_dir = str(tmp_path / "proj")
        Path(project_dir, "_data").mkdir(parents=True)
        Path(project_dir, "results").mkdir()
        db = _make_db(str(tmp_path / "proj" / "_data" / "project.db"))
        ctx = _make_ctx(project_dir=project_dir, db=db)
        ctx.collab_service = None
        return WorkbenchView(ctx), ctx, db

    def _setup_composed(self, tmp_path, db):
        from app.services.grouping_service import Group, save_grouping
        tiff = tmp_path / "proj" / "results" / "T.tif"
        tiff.write_bytes(b"II*\x00")
        j1 = tmp_path / "a.jpg"; j1.write_bytes(b"\xff\xd8\xff")
        j2 = tmp_path / "b.jpg"; j2.write_bytes(b"\xff\xd8\xff")
        g = Group(group_index=0, jpg_paths=[str(j1), str(j2)],
                  composed_tiff_path=str(tiff), status="composed")
        save_grouping(db, "U1", [g], clean_phantoms=False)
        return str(tiff), [str(j1), str(j2)]

    def test_undo_confirmed_deletes_tiff_and_ungroups(self, tmp_path, monkeypatch):
        from PyQt6.QtWidgets import QMessageBox
        from app.services.grouping_service import load_grouping
        w, ctx, db = self._make_view(tmp_path)
        tiff, jpgs = self._setup_composed(tmp_path, db)
        monkeypatch.setattr(QMessageBox, "question",
                            lambda *a, **k: QMessageBox.StandardButton.Yes)
        w._on_undo_compose("U1", 0)
        assert not os.path.exists(tiff)                       # TIFF 删除
        g = load_grouping(db, "U1")
        all_paths = [p for gr in g.groups for p in gr.jpg_paths]
        assert jpgs[0] not in all_paths and jpgs[1] not in all_paths  # JPG 解关联
        assert len(g.groups) == 0                             # 组消失

    def test_undo_cancelled_keeps_everything(self, tmp_path, monkeypatch):
        from PyQt6.QtWidgets import QMessageBox
        from app.services.grouping_service import load_grouping
        w, ctx, db = self._make_view(tmp_path)
        tiff, jpgs = self._setup_composed(tmp_path, db)
        monkeypatch.setattr(QMessageBox, "question",
                            lambda *a, **k: QMessageBox.StandardButton.No)
        w._on_undo_compose("U1", 0)
        assert os.path.exists(tiff)                           # 取消→TIFF 保留
        g = load_grouping(db, "U1")
        assert len(g.groups) == 1                             # 组还在


# ── 场景6/7：合成后自动整理归档（开关，默认关；合成仍手动） ────────────────────


class TestAutoOrganizeAfterCompose:
    """合成永远手动；开关「合成后自动整理归档」打开时，合成成功后自动把源 JPG
    打包压缩+命名+移 results（= 自动跑整理）。默认关。"""

    def _make_view(self, tmp_path):
        from app.views.workbench_view import WorkbenchView
        project_dir = str(tmp_path / "proj")
        Path(project_dir, "_data").mkdir(parents=True)
        db = _make_db(str(tmp_path / "proj" / "_data" / "project.db"))
        ctx = _make_ctx(project_dir=project_dir, db=db)
        ctx.collab_service = None
        return WorkbenchView(ctx), ctx, db

    def test_auto_organize_runs_when_toggle_on(self, tmp_path, monkeypatch):
        w, ctx, db = self._make_view(tmp_path)
        ctx.settings.auto_organize_after_compose = True
        called = []
        monkeypatch.setattr(w, "_on_organise_requested",
                            lambda u, g: called.append((u, g)))
        w._maybe_auto_organize("U1", 0)
        assert called == [("U1", 0)]

    def test_no_auto_organize_when_toggle_off(self, tmp_path, monkeypatch):
        w, ctx, db = self._make_view(tmp_path)
        ctx.settings.auto_organize_after_compose = False
        called = []
        monkeypatch.setattr(w, "_on_organise_requested",
                            lambda u, g: called.append((u, g)))
        w._maybe_auto_organize("U1", 0)
        assert called == []


# ── 场景3：incoming/results 子目录可配置 + 新拍JPG 遗留兼容 ────────────────────


class TestConfigurableIncomingDir:
    """监控的监听+扫描必须认 设置的 incoming/results 子目录 + 遗留 新拍JPG，
    而非写死 incoming-jpg。"""

    def _build(self, tmp_path, *, incoming_name, configured, put_jpg=None):
        from app.views.workbench_view import WorkbenchView
        project_dir = str(tmp_path / "proj")
        Path(project_dir, "_data").mkdir(parents=True)
        Path(project_dir, "results").mkdir()
        inc_dir = Path(project_dir, incoming_name)
        inc_dir.mkdir()
        if put_jpg:
            (inc_dir / put_jpg).write_bytes(b"\xff\xd8\xff")  # jpg-ish
        db = _make_db(str(tmp_path / "proj" / "_data" / "project.db"))
        ctx = _make_ctx(project_dir=project_dir, db=db)
        ctx.settings.incoming_subdir = configured
        ctx.settings.results_subdir = "results"
        return WorkbenchView(ctx), ctx, db, project_dir

    def test_resolve_falls_back_to_legacy_xinpai(self, tmp_path):
        # 配置是 incoming-jpg(默认) 但项目只有 新拍JPG
        w, ctx, db, _ = self._build(tmp_path, incoming_name="新拍JPG",
                                    configured="incoming-jpg")
        inc, res = w._resolve_capture_subdirs()
        assert inc == "新拍JPG"
        assert res == "results"
        db.close()

    def test_resolve_uses_custom_configured_subdir(self, tmp_path):
        w, ctx, db, _ = self._build(tmp_path, incoming_name="我的JPG",
                                    configured="我的JPG")
        inc, _res = w._resolve_capture_subdirs()
        assert inc == "我的JPG"
        db.close()

    def test_watcher_watches_resolved_incoming(self, tmp_path):
        w, ctx, db, _ = self._build(tmp_path, incoming_name="新拍JPG",
                                    configured="incoming-jpg")
        w.on_activate()
        dirs = w._fs_watcher.directories()
        assert any("新拍JPG" in d for d in dirs)
        db.close()

    def test_scan_reads_resolved_incoming(self, tmp_path):
        # jpg 放进 新拍JPG；扫描应读到 → seen_files 记下该文件名
        w, ctx, db, _ = self._build(tmp_path, incoming_name="新拍JPG",
                                    configured="incoming-jpg", put_jpg="a.jpg")
        w._refresh_monitor()
        names = [r[0] for r in db.execute("SELECT name FROM seen_files").fetchall()]
        assert "a.jpg" in names   # 写死 incoming-jpg 时为空 → 红
        db.close()
