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
import hashlib
import sqlite3
import tempfile
import zipfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# ── Qt setup (offscreen) ──────────────────────────────────────────────────────
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QMessageBox, QScrollArea, QWidget, QWIDGETSIZE_MAX

# One shared QApplication instance for all tests in this module
_APP = None


@pytest.fixture(scope="module", autouse=True)
def qt_app():
    global _APP
    if _APP is None:
        _APP = QApplication.instance() or QApplication([])
    return _APP


@pytest.fixture(autouse=True)
def _teardown_leaked_workbench():
    """每个用例后停掉泄漏的 WorkbenchView 定时器。

    WorkbenchView.on_activate() 会启动文件监听和首轮延迟扫描；_wb() 不做清理，
    上个用例的 widget 定时器会在下个用例 setup 时对已关闭的 db / 即将销毁的
    tmp_path 跑 _refresh_monitor → 阻塞事件循环
    （首发现象：673 真·快速打印后，690 无打印机型用例 hang）。用现成的
    on_activate 的反操作 on_deactivate() 停两个定时器，再 deleteLater。
    """
    yield
    from app.views.workbench_view import WorkbenchView
    app = QApplication.instance()
    if app is None:
        return
    for w in app.topLevelWidgets():
        if isinstance(w, WorkbenchView):
            try:
                w.on_deactivate()
            except Exception:
                pass
            w.deleteLater()
    app.processEvents()


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
    ctx.settings.silent_compose = False
    ctx.settings.delete_jpg_after_archive = True
    ctx.collab_service = None
    return ctx


def _fake_zip_result(jpg_paths: list[str], zip_path: str, *, saved_percent: int = 0):
    """Create a valid JPG ZIP and return the real archive result type."""
    from app.services.archive_service import ZipResult

    manifest_files = []
    total_original = 0
    Path(zip_path).parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_STORED) as zf:
        for jpg_path in jpg_paths:
            data = Path(jpg_path).read_bytes()
            archive_name = Path(jpg_path).name
            zf.writestr(archive_name, data)
            total_original += len(data)
            manifest_files.append({
                "originalName": archive_name,
                "archiveName": archive_name,
                "originalSize": len(data),
                "compressedSize": len(data),
                "originalSha256": hashlib.sha256(data).hexdigest(),
                "zipCompression": "store",
            })
    zip_size = Path(zip_path).stat().st_size
    return ZipResult(
        zip_path=zip_path,
        zip_size=zip_size,
        file_count=len(jpg_paths),
        total_original=total_original,
        total_compressed=total_original,
        saved_percent=saved_percent,
        delete_jpg=False,
        requested_delete_jpg=False,
        deletion_skipped_reason="",
        manifest={
            "format": "jpg-zip",
            "method": "test-plain-jpg-zip",
            "files": manifest_files,
        },
        source_paths=tuple(jpg_paths),
    )


def test_grouping_save_failure_is_visible_and_returns_false(
    qtbot, tmp_path, monkeypatch
):
    from app.services import capture_workflow_service
    from app.views.workbench_view import WorkbenchView

    db = _make_db(str(tmp_path / "project.db"))
    w = WorkbenchView(_make_ctx(str(tmp_path), db))
    qtbot.addWidget(w)
    statuses = []
    notices = []
    monkeypatch.setattr(w, "_status_message", lambda text, *args: statuses.append(text))
    monkeypatch.setattr(w, "_workflow_notice", lambda *args, **kwargs: notices.append((args, kwargs)))
    monkeypatch.setattr(
        capture_workflow_service,
        "flush_visible_grouping",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("disk full")),
    )

    assert w._flush_grouping_save() is False
    assert any("分组保存失败" in text for text in statuses)
    assert notices and notices[-1][1]["state"] == "error"
    db.close()


def test_metadata_save_failure_is_visible_and_propagates_inside_outer_save(
    qtbot, tmp_path, monkeypatch
):
    from app.views.workbench_view import WorkbenchView

    db = _make_db(str(tmp_path / "project.db"))
    ctx = _make_ctx(str(tmp_path), db)
    w = WorkbenchView(ctx)
    qtbot.addWidget(w)
    statuses = []
    notices = []
    monkeypatch.setattr(w, "_status_message", lambda text, *args: statuses.append(text))
    monkeypatch.setattr(w, "_workflow_notice", lambda *args, **kwargs: notices.append((args, kwargs)))

    class _BrokenDb:
        def execute(self, *args, **kwargs):
            raise RuntimeError("database locked")

    ctx.get_db.return_value = _BrokenDb()
    with pytest.raises(RuntimeError, match="database locked"):
        w._on_save_metadata("UID-1", reload=False, commit=False)

    assert any("元数据保存失败" in text for text in statuses)
    assert notices and notices[-1][1]["state"] == "error"
    db.close()


def test_workflow_notice_panel_hides_current_task_until_new_task(qtbot):
    host = QWidget()
    host.resize(900, 600)
    qtbot.addWidget(host)
    host.show()
    from app.views.workbench_view import _WorkflowNoticePanel

    dlg = _WorkflowNoticePanel(host)
    qtbot.addWidget(dlg)

    dlg.set_notice(
        "合成+整理：正在整理",
        "正在打包第 1/2 张 JPG：a.jpg",
        state="busy",
        force_show=True,
        task_key="task-a",
    )
    assert dlg.isVisible()

    dlg._hide_current_task()
    assert not dlg.isVisible()
    assert dlg._launcher is not None
    assert dlg._launcher.isVisible()

    dlg.set_notice(
        "合成+整理：正在整理",
        "正在打包第 2/2 张 JPG：b.jpg",
        state="busy",
        task_key="task-a",
    )
    assert not dlg.isVisible()
    assert dlg._launcher.isVisible()
    assert dlg.notice_text()[2] == "正在打包第 2/2 张 JPG：b.jpg"

    dlg.set_notice(
        "补处理：正在整理",
        "正在归档旧照片。",
        state="busy",
        task_key="task-b",
    )
    assert dlg.isVisible()
    assert not dlg._launcher.isVisible()


def test_workflow_notice_panel_auto_hides_finished_notice(qtbot):
    host = QWidget()
    host.resize(900, 600)
    qtbot.addWidget(host)
    host.show()
    from app.views.workbench_view import _WorkflowNoticePanel

    panel = _WorkflowNoticePanel(host)
    qtbot.addWidget(panel)
    panel.set_notice(
        "合成+整理完成",
        "JPG 已写入 ZIP。",
        state="success",
        force_show=True,
        task_key="done-task",
    )

    assert panel.isVisible()
    panel._auto_hide_finished_notice()
    assert not panel.isVisible()


def test_compose_organise_notice_uses_single_progress_dialog(qtbot, tmp_path):
    from app.views.workbench_view import WorkbenchView

    db = _make_db(str(tmp_path / "project.db"))
    ctx = _make_ctx(str(tmp_path), db)
    w = WorkbenchView(ctx)
    qtbot.addWidget(w)

    w._workflow_notice(
        "合成+整理：正在合成 TIFF",
        "已接收 2 张 JPG。合成完成后会自动整理。",
        state="busy",
        force_show=True,
        task_key="compose-org",
    )
    w._workflow_notice(
        "合成+整理：正在整理",
        "TIFF 已生成，正在打包 JPG 原片并登记 ZIP。",
        state="busy",
        task_key="compose-org",
    )

    assert getattr(w, "_workflow_notice_panel", None) is None
    dlg = getattr(w, "_compose_organise_progress_dialog", None)
    assert dlg is not None
    assert dlg.findChild(QWidget, "ComposeOrganiseCard") is not None
    assert dlg.parentWidget() is w
    assert dlg.windowType() == Qt.WindowType.Widget
    assert not bool(dlg.windowFlags() & Qt.WindowType.FramelessWindowHint)
    assert not dlg.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
    assert dlg.stage_texts() == ("完成", "进行中")


def test_compose_organise_dialog_starts_hidden_until_task_notice(qtbot, tmp_path):
    from app.views.workbench_view import WorkbenchView

    db = _make_db(str(tmp_path / "project.db"))
    ctx = _make_ctx(str(tmp_path), db)
    w = WorkbenchView(ctx)
    qtbot.addWidget(w)
    w.show()
    qtbot.wait(50)

    dlg = getattr(w, "_compose_organise_progress_dialog", None)
    assert dlg is not None
    assert not dlg.isVisible()

    w._workflow_notice(
        "合成+整理：正在合成 TIFF",
        "已接收 2 张 JPG。",
        state="busy",
        force_show=True,
        task_key="compose-org",
    )
    assert dlg.isVisible()


def test_compose_organise_dialog_buttons_compact_hide_and_restore(qtbot, tmp_path):
    from app.views.workbench_view import WorkbenchView

    db = _make_db(str(tmp_path / "project.db"))
    ctx = _make_ctx(str(tmp_path), db)
    w = WorkbenchView(ctx)
    w.resize(900, 620)
    qtbot.addWidget(w)
    w.show()

    w._workflow_notice(
        "合成+整理：正在整理",
        "正在打包第 1/3 张 JPG：a.jpg",
        state="busy",
        force_show=True,
        task_key="compose-org-buttons",
    )
    dlg = getattr(w, "_compose_organise_progress_dialog", None)
    assert dlg is not None
    assert dlg.isVisible()
    assert dlg._detail.isVisible()
    received = []
    dlg.cancel_requested.connect(received.append)
    assert dlg._cancel_action.isEnabled()
    assert dlg._cancel_action.text() == "取消任务"

    qtbot.mouseClick(dlg._compact_action, Qt.MouseButton.LeftButton)
    assert dlg._compact is True
    assert not dlg._detail.isVisible()
    assert dlg._compact_action.text() == "展开详情"

    qtbot.mouseClick(dlg._hide_action, Qt.MouseButton.LeftButton)
    assert not dlg.isVisible()
    assert dlg._hidden_by_user is True
    assert dlg._launcher is not None
    assert dlg._launcher.isVisible()

    w._workflow_notice(
        "合成+整理：正在整理",
        "正在打包第 2/3 张 JPG：b.jpg",
        state="busy",
        task_key="compose-org-buttons",
    )
    assert not dlg.isVisible()
    assert dlg._launcher.isVisible()

    qtbot.mouseClick(dlg._launcher, Qt.MouseButton.LeftButton)
    assert dlg.isVisible()
    assert dlg._hidden_by_user is False
    assert not dlg._launcher.isVisible()

    qtbot.mouseClick(dlg._cancel_action, Qt.MouseButton.LeftButton)
    assert received == ["compose-org-buttons"]


def test_compose_organise_header_buttons_and_reject_hide_running_task(qtbot, tmp_path):
    from app.views.workbench_view import WorkbenchView

    db = _make_db(str(tmp_path / "project.db"))
    ctx = _make_ctx(str(tmp_path), db)
    w = WorkbenchView(ctx)
    w.resize(900, 620)
    qtbot.addWidget(w)
    w.show()

    w._workflow_notice(
        "合成+整理：正在整理",
        "正在打包第 1/3 张 JPG：a.jpg",
        state="busy",
        force_show=True,
        task_key="compose-org-header",
    )
    dlg = getattr(w, "_compose_organise_progress_dialog", None)
    assert dlg is not None

    qtbot.mouseClick(dlg._compact_btn, Qt.MouseButton.LeftButton)
    assert not dlg.isVisible()
    assert dlg._hidden_by_user is True
    assert dlg._launcher is not None
    assert dlg._launcher.isVisible()
    assert dlg._launcher.text() == "任务 (1 进行中)"

    qtbot.mouseClick(dlg._launcher, Qt.MouseButton.LeftButton)
    assert dlg.isVisible()
    assert dlg._hidden_by_user is False
    qtbot.mouseClick(dlg._hide_btn, Qt.MouseButton.LeftButton)
    assert not dlg.isVisible()
    assert dlg._hidden_by_user is True
    assert dlg._launcher is not None
    assert dlg._launcher.isVisible()

    w._workflow_notice(
        "合成+整理：正在整理",
        "正在打包第 2/3 张 JPG：b.jpg",
        state="busy",
        task_key="compose-org-header",
    )
    assert not dlg.isVisible()
    assert dlg._launcher.isVisible()

    qtbot.mouseClick(dlg._launcher, Qt.MouseButton.LeftButton)
    assert dlg.isVisible()
    dlg.close()
    assert not dlg.isVisible()
    assert dlg._launcher.isVisible()

    qtbot.mouseClick(dlg._launcher, Qt.MouseButton.LeftButton)
    assert dlg.isVisible()
    dlg.reject()
    assert not dlg.isVisible()
    assert dlg._launcher.isVisible()


def test_compose_organise_cancel_forwards_to_active_archive_worker(qtbot, tmp_path):
    from app.views.workbench_view import WorkbenchView

    db = _make_db(str(tmp_path / "project.db"))
    ctx = _make_ctx(str(tmp_path), db)
    w = WorkbenchView(ctx)
    qtbot.addWidget(w)

    class _DummyWorker:
        def __init__(self) -> None:
            self.cancel_called = False

        def cancel(self) -> None:
            self.cancel_called = True

    worker = _DummyWorker()
    w._archive_worker_by_task_key = {"task-archive": worker}
    w._workflow_notice(
        "合成+整理：正在整理",
        "正在打包第 1/3 张 JPG：a.jpg",
        state="busy",
        force_show=True,
        task_key="task-archive",
    )

    dlg = getattr(w, "_compose_organise_progress_dialog", None)
    assert dlg is not None
    qtbot.mouseClick(dlg._cancel_action, Qt.MouseButton.LeftButton)

    assert worker.cancel_called is True
    _stage, title, detail = w._workflow_notice_text()
    assert "正在取消" in title
    assert "不会删除 JPG" in detail


def test_compose_organise_finished_state_has_close_not_cancel(qtbot, tmp_path):
    from app.views.workbench_view import WorkbenchView

    db = _make_db(str(tmp_path / "project.db"))
    ctx = _make_ctx(str(tmp_path), db)
    w = WorkbenchView(ctx)
    w.resize(900, 620)
    qtbot.addWidget(w)
    w.show()

    w._workflow_notice(
        "合成+整理完成",
        "已生成 ZIP，JPG 可从成果 ZIP 撤销恢复。",
        state="success",
        force_show=True,
        task_key="task-finished",
    )
    dlg = getattr(w, "_compose_organise_progress_dialog", None)

    assert dlg is not None
    assert dlg.isVisible()
    assert dlg._overall_badge.text() == "状态：完成"
    assert not dlg._cancel_action.isVisible()
    assert dlg._ok_action.isEnabled()
    assert dlg._ok_action.text() == "确定"
    assert dlg._hide_action.text() == "关闭窗口"

    qtbot.mouseClick(dlg._ok_action, Qt.MouseButton.LeftButton)

    assert not dlg.isVisible()
    assert dlg._launcher is not None
    assert not dlg._launcher.isVisible()


def test_compose_organise_finished_close_controls_really_close(qtbot, tmp_path):
    from app.views.workbench_view import WorkbenchView

    db = _make_db(str(tmp_path / "project.db"))
    ctx = _make_ctx(str(tmp_path), db)
    w = WorkbenchView(ctx)
    w.resize(900, 620)
    qtbot.addWidget(w)
    w.show()

    w._workflow_notice(
        "合成+整理完成",
        "已生成 ZIP，4 张 JPG 已写入 ZIP。",
        state="success",
        force_show=True,
        task_key="task-finished-close",
    )
    dlg = getattr(w, "_compose_organise_progress_dialog", None)
    assert dlg is not None
    assert dlg.isVisible()

    qtbot.mouseClick(dlg._hide_action, Qt.MouseButton.LeftButton)

    assert not dlg.isVisible()
    assert dlg._launcher is not None
    assert not dlg._launcher.isVisible()

    w._workflow_notice(
        "合成+整理完成",
        "已生成 ZIP，4 张 JPG 已写入 ZIP。",
        state="success",
        force_show=True,
        task_key="task-finished-close",
    )
    assert dlg.isVisible()

    assert dlg.close() is True
    assert not dlg.isVisible()
    assert dlg._launcher is not None
    assert not dlg._launcher.isVisible()


def test_compose_organise_uses_operations_style_background_entry(qtbot, tmp_path):
    """Geneious-style: no blocking scrim; Hide/minimize sends task to background."""
    from app.views.workbench_view import WorkbenchView

    db = _make_db(str(tmp_path / "project.db"))
    ctx = _make_ctx(str(tmp_path), db)
    w = WorkbenchView(ctx)
    w.resize(900, 620)
    qtbot.addWidget(w)
    w.show()
    qtbot.wait(50)

    w._workflow_notice(
        "合成+整理完成",
        "已生成 demo.zip；3 张 JPG 已写入 ZIP 并从待处理区删除。",
        state="success",
        force_show=True,
        task_key="backdrop-regression",
    )
    dlg = w._compose_organise_progress_dialog
    assert dlg is not None
    assert dlg.isVisible()
    assert dlg.parentWidget() is w
    assert dlg._cancel_action.isHidden()
    assert dlg._ok_action.isVisible()

    qtbot.mouseClick(dlg._compact_btn, Qt.MouseButton.LeftButton)
    assert not dlg.isVisible()
    assert dlg._launcher.isVisible()
    assert dlg._launcher.text() == "任务 · 完成"

    qtbot.mouseClick(dlg._launcher, Qt.MouseButton.LeftButton)
    assert dlg.isVisible()
    qtbot.mouseClick(dlg._hide_action, Qt.MouseButton.LeftButton)
    assert not dlg.isVisible()
    assert not dlg._launcher.isVisible()

    w._workflow_notice(
        "合成+整理完成",
        "已生成 demo.zip；3 张 JPG 已写入 ZIP 并从待处理区删除。",
        state="success",
        force_show=True,
        task_key="backdrop-regression",
    )
    assert dlg.isVisible()
    qtbot.mouseClick(dlg._ok_action, Qt.MouseButton.LeftButton)
    assert not dlg.isVisible()
    assert not dlg._launcher.isVisible()


def test_workflow_dashboard_allows_jpg_compose_without_active_uid(qtbot, tmp_path):
    from app.services.monitor_service import FileEntry, ScanResult
    from app.views.workbench_view import WorkbenchView

    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    jpg = project_dir / "incoming-jpg" / "a.jpg"
    jpg.parent.mkdir()
    jpg.write_bytes(b"\xff\xd8\xff\xe0jpg")
    db = _make_db(str(project_dir / "_data.db"))
    ctx = _make_ctx(str(project_dir), db)
    w = WorkbenchView(ctx)
    qtbot.addWidget(w)

    w._apply_monitor_scan_result(ScanResult(
        project_dir=str(project_dir),
        jpg_files=[
            FileEntry(
                name=jpg.name,
                path=str(jpg),
                kind="jpg",
                size=jpg.stat().st_size,
                mtime="2026-07-06T00:00:00+00:00",
            )
        ],
        pending_count=1,
        incoming_jpg_dir=str(jpg.parent),
    ))

    assert not hasattr(w, "_workflow_dashboard")


def test_supp_compression_worker_cancel_cleans_partial_zip(qtbot, tmp_path, monkeypatch):
    from app.services import archive_service
    from app.workers.supp_compression_worker import SuppCompressionWorker

    jpg = tmp_path / "a.jpg"
    jpg.write_bytes(b"jpg")
    tiff = tmp_path / "result.tif"
    tiff.write_bytes(b"tif")
    out_dir = tmp_path / "results"
    out_dir.mkdir()
    partial_zip = out_dir / "result.zip"

    def _cancelled_archive(*_args, **_kwargs):
        partial_zip.write_bytes(b"partial zip")
        raise archive_service.ArchiveCancelled("用户取消")

    monkeypatch.setattr(archive_service, "archive_group", _cancelled_archive)

    worker = SuppCompressionWorker(
        [str(jpg)],
        str(tiff),
        str(tmp_path),
        output_dir=str(out_dir),
    )
    messages = []
    worker.cancelled.connect(messages.append)

    worker.run()

    assert messages == ["用户取消"]
    assert not partial_zip.exists()


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
        CREATE TABLE IF NOT EXISTS project_settings (
            setting_key TEXT PRIMARY KEY,
            value_json TEXT NOT NULL
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


class TestRightRailEditEntry:
    def test_sidebar_edit_request_loads_all_right_rail_cards(self, tmp_path):
        from app.views.workbench_view import WorkbenchView

        project_dir = str(tmp_path)
        db = _make_db(":memory:")
        raw = {
            "uid": "FJ-XM-B2-DLC001-T95E-20260601",
            "province": "FJ",
            "site": "XM",
            "station": "B2",
            "id": "DLC001",
            "storage": "T95E",
            "collectionDate": "20260601",
        }
        db.execute(
            """
            INSERT INTO specimens (
                uid, id, province, site, station, storage, collection_date,
                scientific_name, family, collector, owner_project_dir, raw_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                raw["uid"], "DLC001", "FJ", "XM", "B2", "T95E", "20260601",
                "Marphysa sp.", "Eunicidae", "采集人A", project_dir,
                json.dumps(raw, ensure_ascii=False),
            ),
        )
        db.commit()
        w = WorkbenchView(_make_ctx(project_dir, db))

        w._on_edit_specimen_requested(raw["uid"])

        assert w._current_uid == raw["uid"]
        assert w._naming.persisted_uid() == raw["uid"]
        assert w._naming._species_id.text() == "DLC001"
        assert w._taxon_card.field_values()["scientific_name"] == "Marphysa sp."
        assert w._metadata._collector.text() == "采集人A"


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

    def test_on_activate_defers_initial_monitor_scan(self, tmp_path):
        """Returning to the workbench should not synchronously scan twice."""
        from app.views.workbench_view import WorkbenchView
        project_dir = str(tmp_path / "proj-defer")
        Path(project_dir).mkdir(parents=True)
        (Path(project_dir) / "incoming-jpg").mkdir()
        (Path(project_dir) / "results").mkdir()
        (Path(project_dir) / "_data").mkdir()
        db = _make_db(str(tmp_path / "proj-defer" / "_data" / "project.db"))
        ctx = _make_ctx(project_dir=project_dir, db=db)
        w = WorkbenchView(ctx)
        calls = []
        w._refresh_monitor = lambda: calls.append("scan")

        w.on_activate()

        assert calls == []
        assert w._debounce_timer.isActive()
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

    def test_stop_background_work_waits_for_archive_workers(self):
        """Shutdown should wait for archive workers instead of destroying running QThreads."""
        from app.views.workbench_view import WorkbenchView

        w = WorkbenchView(_make_ctx(project_dir=None))
        waits = []

        class _Worker:
            def isRunning(self):
                return True

            def wait(self, ms):
                waits.append(ms)

        w._archive_workers = {_Worker()}

        w.stop_background_work()

        assert waits == [3000]

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

    def test_on_activate_clears_stale_naming_when_no_active_specimen(self, tmp_path):
        """切/进一个无激活标本的项目时,命名卡残留字段(上一项目的)必须清空。

        回归:此前 on_activate 无激活标本分支没清 naming_panel,上个项目最后加载
        的 province/site 等残留显示 → 用户在新空项目里误以为"默认了"。
        """
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
        # 模拟上一项目残留:命名卡字段非空 + _current_uid 指向旧号
        w._naming._province.setText("ZJ")
        w._naming._site.setText("SMW")
        w._naming._storage.setText("D95E")
        w._current_uid = "ZJ-SMW-DLC001-D95E-20260601"
        # 空项目无激活标本
        assert w._get_active_uid() is None
        w.on_activate()
        assert w._naming._province.text() == ""
        assert w._naming._site.text() == ""
        assert w._naming._storage.text() == ""
        assert w._current_uid is None
        db.close()


# ── 新增编号: 日期沿用上一号 (同断面连拍日期不变) ────────────────────────────

class TestNewSpecimenDateCarryOver:
    def _make_workbench(self, tmp_path):
        from app.views.workbench_view import WorkbenchView
        project_dir = str(tmp_path / "proj")
        Path(project_dir).mkdir(parents=True)
        (Path(project_dir) / "incoming-jpg").mkdir()
        (Path(project_dir) / "results").mkdir()
        (Path(project_dir) / "_data").mkdir()
        db = _make_db(str(Path(project_dir) / "_data" / "project.db"))
        ctx = _make_ctx(project_dir=project_dir, db=db)
        w = WorkbenchView(ctx)
        w.on_activate()
        return w, db

    def test_new_specimen_carries_over_dates(self, tmp_path):
        """新增编号沿用上一号的采集/拍摄日期(同断面连拍,日期不变)。"""
        w, db = self._make_workbench(tmp_path)
        try:
            # 模拟上一个激活标本已填日期
            w._naming._collection_date.setText("20260613")
            w._naming._photo_date.setText("20260613")
            # 新增编号
            w._on_new_specimen()
            # 日期沿用, 非留空
            assert w._naming._collection_date.text() == "20260613"
            assert w._naming._photo_date.text() == "20260613"
        finally:
            db.close()

    def test_new_specimen_carries_collection_context_not_species(self, tmp_path):
        """新增编号沿用工作区上下文，但清空物种编号和标本专属备注。"""
        w, db = self._make_workbench(tmp_path)
        try:
            n = w._naming
            n._province.setText("FJ")
            n._site.setText("XM")
            n._station.setText("B2")
            n._species_id.setText("DLC004")
            n._storage.setText("T95E")
            n._collection_date.setText("20260613")
            n._photo_date.setText("20260613")
            n._photo_notes.setPlainText("上一号拍照备注")
            w._metadata._collector.setText("张三")
            w._metadata._lon.setText("119.5")
            w._metadata._lat.setText("26.3")
            w._metadata._geo_area.setText("三门湾")

            w._on_new_specimen()

            assert n._province.text() == "FJ"
            assert n._site.text() == "XM"
            assert n._station.text() == "B2"
            assert n._storage.text() == "T95E"
            assert n._collection_date.text() == "20260613"
            assert n._photo_date.text() == "20260613"
            assert n._species_id.text() == ""
            assert n._photo_notes.toPlainText() == ""
            # 人员不沿用上一标本；新号使用项目人员默认（本项目未设则空）。
            assert w._metadata._collector.text() == ""
            assert w._metadata._lon.text() == "119.5"
            assert w._metadata._lat.text() == "26.3"
            assert w._metadata._geo_area.text() == "三门湾"
        finally:
            db.close()

    def test_new_specimen_dates_blank_when_no_previous(self, tmp_path):
        """无上一号(首次新增)时日期留空, 不报错。"""
        w, db = self._make_workbench(tmp_path)
        try:
            w._on_new_specimen()
            assert w._naming._collection_date.text() == ""
            assert w._naming._photo_date.text() == ""
        finally:
            db.close()


class TestRightRailSpecimenIdentityEdits:
    def _make_workbench(self, tmp_path):
        from app.views.workbench_view import WorkbenchView
        project_dir = str(tmp_path / "proj_identity")
        Path(project_dir).mkdir(parents=True)
        (Path(project_dir) / "incoming-jpg").mkdir()
        (Path(project_dir) / "results").mkdir()
        (Path(project_dir) / "_data").mkdir()
        db = _make_db(str(Path(project_dir) / "_data" / "project.db"))
        ctx = _make_ctx(project_dir=project_dir, db=db)
        w = WorkbenchView(ctx)
        w.on_activate()
        return w, db, project_dir

    def _insert_specimen(self, db, uid, project_dir, species_id="DLC001"):
        db.execute(
            """
            INSERT INTO specimens
              (uid, id, province, site, station, storage,
               collection_date, photo_date, owner_project_dir, raw_json)
            VALUES (?,?,?,?,?,?,?,?,?,?)
            """,
            (
                uid,
                species_id,
                "FJ",
                "XM",
                "B2",
                "T95E",
                "20260601",
                "",
                project_dir,
                None,
            ),
        )
        db.commit()

    def test_selected_specimen_save_renames_instead_of_adding(self, tmp_path):
        old_uid = "FJ-XM-B2-DLC001-T95E-20260601"
        new_uid = "FJ-XM-B2-DLC002-T95E-20260601"
        w, db, project_dir = self._make_workbench(tmp_path)
        try:
            self._insert_specimen(db, old_uid, project_dir)
            db.execute("INSERT INTO grouping (uid, group_index) VALUES (?, 0)", (old_uid,))
            db.execute("INSERT INTO tasks (uid) VALUES (?)", (old_uid,))
            db.commit()

            w._on_specimen_selected(old_uid)
            w._naming._species_id.setText("DLC002")
            w._on_naming_save()

            assert db.execute("SELECT 1 FROM specimens WHERE uid=?", (new_uid,)).fetchone()
            assert not db.execute("SELECT 1 FROM specimens WHERE uid=?", (old_uid,)).fetchone()
            assert db.execute("SELECT 1 FROM grouping WHERE uid=?", (new_uid,)).fetchone()
            assert db.execute("SELECT 1 FROM tasks WHERE uid=?", (new_uid,)).fetchone()
            raw = json.loads(db.execute(
                "SELECT raw_json FROM specimens WHERE uid=?", (new_uid,)
            ).fetchone()[0])
            assert raw["id"] == "DLC002"
            assert old_uid in raw["previousUniqueIds"]
        finally:
            db.close()

    def test_dynamic_naming_field_is_saved_to_raw_json(self, tmp_path):
        from app.services.project_settings_service import (
            DEFAULT_NAMING_RULES,
            save_setting,
        )

        w, db, _project_dir = self._make_workbench(tmp_path)
        try:
            rules = dict(DEFAULT_NAMING_RULES)
            rules["components"] = [
                "province", "site", "habitat", "species_id", "storage", "date_seg"
            ]
            rules["required"] = dict(DEFAULT_NAMING_RULES["required"])
            rules["required"]["habitat"] = True
            save_setting(db, "naming_rules", rules)
            w._naming.refresh_naming_rules()

            w._naming._province.setText("FJ")
            w._naming._site.setText("SMW")
            w._naming._species_id.setText("DLC001")
            w._naming._storage.setText("T95E")
            w._naming._collection_date.setText("20260612")
            w._naming._photo_date.setText("20260612")
            w._naming._dynamic_naming_edits["habitat"].setText("泥滩")

            w._on_naming_save()

            uid = "FJ-SMW-泥滩-DLC001-T95E-20260612"
            raw = json.loads(db.execute(
                "SELECT raw_json FROM specimens WHERE uid=?",
                (uid,),
            ).fetchone()[0])
            assert raw["habitat"] == "泥滩"
        finally:
            db.close()

    def test_project_custom_naming_field_is_saved_to_raw_json(self, tmp_path):
        from app.services.project_settings_service import (
            DEFAULT_NAMING_RULES,
            save_setting,
        )

        w, db, _project_dir = self._make_workbench(tmp_path)
        try:
            rules = dict(DEFAULT_NAMING_RULES)
            rules["custom_fields"] = [{"key": "depth", "label": "水深"}]
            rules["components"] = [
                "province", "site", "depth", "species_id", "storage", "date_seg"
            ]
            rules["required"] = dict(DEFAULT_NAMING_RULES["required"])
            rules["required"]["depth"] = True
            save_setting(db, "naming_rules", rules)
            w._naming.refresh_naming_rules()

            w._naming._province.setText("FJ")
            w._naming._site.setText("SMW")
            w._naming._species_id.setText("DLC001")
            w._naming._storage.setText("T95E")
            w._naming._collection_date.setText("20260612")
            w._naming._photo_date.setText("20260612")
            w._naming._dynamic_naming_edits["depth"].setText("12m")

            w._on_naming_save()

            uid = "FJ-SMW-12m-DLC001-T95E-20260612"
            raw = json.loads(db.execute(
                "SELECT raw_json FROM specimens WHERE uid=?",
                (uid,),
            ).fetchone()[0])
            assert raw["depth"] == "12m"
        finally:
            db.close()

    def test_add_draft_with_stale_current_uid_does_not_rename_old_specimen(self, tmp_path):
        old_uid = "GXFCG-BLW-SC001-D79-20260618"
        new_uid = "GXFCG-BLW-SC002-R-20260618"
        w, db, project_dir = self._make_workbench(tmp_path)
        try:
            self._insert_specimen(db, old_uid, project_dir, species_id="SC001")

            # The UI can still have the old row selected while the naming panel
            # is in an unsaved draft state.  Clicking "添加" must create a new
            # row, not migrate/delete the selected specimen.
            w._current_uid = old_uid
            w._naming.load_specimen({
                "province": "GXFCG",
                "site": "BLW",
                "id": "SC002",
                "storage": "R",
                "collectionDate": "20260618",
                "photoDate": "20260618",
            })
            assert w._naming.persisted_uid() == ""
            assert w._naming.current_uid() == new_uid
            assert w._naming._pin_btn.text() == "添加"

            w._on_naming_save()

            assert db.execute("SELECT 1 FROM specimens WHERE uid=?", (old_uid,)).fetchone()
            new_row = db.execute(
                "SELECT id, storage FROM specimens WHERE uid=?", (new_uid,)
            ).fetchone()
            assert new_row is not None
            assert new_row["id"] == "SC002"
            assert new_row["storage"] == "R"
            assert w._naming.persisted_uid() == new_uid
            assert w._naming._pin_btn.text() == "添加"
            assert not w._naming._preview_save_btn.isHidden()
            assert w._sidebar.current_uid() == new_uid
        finally:
            db.close()

    def test_uid_correction_reload_results_with_migrated_result_names(self, tmp_path):
        from app.services.specimen_rename_service import apply_storage_correction

        old_uid = "GXFCG-BLW-SC002-R-20260618"
        new_uid = "GXFCG-BLW-SC002-RD79-20260618"
        w, db, project_dir = self._make_workbench(tmp_path)
        try:
            self._insert_specimen(db, old_uid, project_dir, species_id="SC002")
            db.execute(
                """
                UPDATE specimens
                SET province='GXFCG', site='BLW', station='', storage='R',
                    collection_date='20260618', photo_date='20260618',
                    raw_json=?
                WHERE uid=?
                """,
                (
                    json.dumps({
                        "id": "SC002",
                        "province": "GXFCG",
                        "site": "BLW",
                        "station": "",
                        "storage": "R",
                        "collectionDate": "20260618",
                        "photoDate": "20260618",
                    }, ensure_ascii=False),
                    old_uid,
                ),
            )
            old_stem = "GXFCG-BLW-SC002-1-R-260618-广西防城港-白龙尾-独齿沙蚕-20260618"
            old_tif = Path(project_dir) / "results" / f"{old_stem}.tif"
            old_zip = Path(project_dir) / "results" / f"{old_stem}.zip"
            old_tif.write_bytes(b"tif")
            old_zip.write_bytes(b"zip")
            db.execute(
                """
                INSERT INTO grouping
                  (uid, group_index, composed_tiff_path, archive_zip,
                   status, result_sequence)
                VALUES (?, 0, ?, ?, 'organized', 1)
                """,
                (old_uid, str(old_tif), str(old_zip)),
            )
            db.commit()

            w._on_specimen_selected(old_uid)
            assert w._results._current_tiffs[0]["name"] == old_tif.name

            assert apply_storage_correction(db, old_uid, "RD79") == new_uid
            w._on_uid_corrected(old_uid, new_uid)

            assert w._results._current_tiffs[0]["name"].startswith(
                "GXFCG-BLW-SC002-1-RD79-260618"
            )
            assert w._results._current_zips[0]["name"].startswith(
                "GXFCG-BLW-SC002-1-RD79-260618"
            )
            assert "-R-" not in w._results._current_tiffs[0]["name"]
        finally:
            db.close()

    def test_update_button_renames_stale_result_files_to_current_uid(self, tmp_path):
        uid = "GXFCG-BLW-SC002-RD79-20260618"
        w, db, project_dir = self._make_workbench(tmp_path)
        try:
            db.execute(
                """
                INSERT INTO specimens (
                    uid, id, province, site, station, storage,
                    collection_date, photo_date, owner_project_dir, raw_json
                ) VALUES (?, 'SC002', 'GXFCG', 'BLW', '', 'RD79',
                          '20260618', '20260618', ?, ?)
                """,
                (
                    uid,
                    project_dir,
                    json.dumps({
                        "id": "SC002",
                        "province": "GXFCG",
                        "site": "BLW",
                        "station": "",
                        "storage": "RD79",
                        "collectionDate": "20260618",
                        "photoDate": "20260618",
                    }, ensure_ascii=False),
                ),
            )
            old_stem = "GXFCG-BLW-SC002-1-R-260618-广西防城港-白龙尾-独齿沙蚕-20260618"
            new_stem = "GXFCG-BLW-SC002-1-RD79-260618-广西防城港-白龙尾-独齿沙蚕-20260618"
            old_tif = Path(project_dir) / "results" / f"{old_stem}.tif"
            old_zip = Path(project_dir) / "results" / f"{old_stem}.zip"
            old_tif.write_bytes(b"tif")
            old_zip.write_bytes(b"zip")
            db.execute(
                """
                INSERT INTO grouping
                  (uid, group_index, composed_tiff_path, archive_zip,
                   status, result_sequence, raw_json)
                VALUES (?, 0, ?, ?, 'organized', 1, ?)
                """,
                (
                    uid,
                    str(old_tif),
                    str(old_zip),
                    json.dumps({
                        "outputName": old_stem,
                        "composedTiffPath": str(old_tif),
                        "archiveZip": str(old_zip),
                    }, ensure_ascii=False),
                ),
            )
            db.commit()

            w._current_uid = uid
            w._naming.load_specimen({
                "uid": uid,
                "id": "SC002",
                "province": "GXFCG",
                "site": "BLW",
                "station": "",
                "storage": "RD79",
                "collectionDate": "20260618",
                "photoDate": "20260618",
            })
            assert old_tif.is_file()

            w._on_naming_update_results()

            new_tif = old_tif.with_name(f"{new_stem}.tif")
            new_zip = old_zip.with_name(f"{new_stem}.zip")
            assert new_tif.is_file()
            assert new_zip.is_file()
            assert not old_tif.exists()
            assert not old_zip.exists()
            row = db.execute(
                "SELECT composed_tiff_path, archive_zip, raw_json FROM grouping WHERE uid=?",
                (uid,),
            ).fetchone()
            assert row["composed_tiff_path"] == str(new_tif)
            assert row["archive_zip"] == str(new_zip)
            raw = json.loads(row["raw_json"])
            assert raw["outputName"] == new_stem
            assert w._results._current_tiffs[0]["name"] == new_tif.name
            assert w._results._current_zips[0]["name"] == new_zip.name
        finally:
            db.close()

    def test_selecting_uid_repairs_stale_result_paths(self, tmp_path):
        uid = "GXFCG-BLW-SC002-RD79-20260618"
        w, db, project_dir = self._make_workbench(tmp_path)
        try:
            self._insert_specimen(db, uid, project_dir, species_id="SC002")
            db.execute(
                """
                UPDATE specimens
                SET province='GXFCG', site='BLW', station='', storage='RD79',
                    collection_date='20260618', photo_date='20260618',
                    raw_json=?
                WHERE uid=?
                """,
                (
                    json.dumps({
                        "id": "SC002",
                        "province": "GXFCG",
                        "site": "BLW",
                        "station": "",
                        "storage": "RD79",
                        "collectionDate": "20260618",
                        "photoDate": "20260618",
                    }, ensure_ascii=False),
                    uid,
                ),
            )
            old_stem = "GXFCG-BLW-SC002-1-R-20260618-广西防城港-白龙尾-独齿沙蚕-20260618"
            old_tif = Path(project_dir) / "results" / f"{old_stem}.tif"
            old_zip = Path(project_dir) / "results" / f"{old_stem}.zip"
            old_tif.write_bytes(b"tif")
            old_zip.write_bytes(b"zip")
            db.execute(
                """
                INSERT INTO grouping
                  (uid, group_index, composed_tiff_path, archive_zip,
                   status, result_sequence)
                VALUES (?, 0, ?, ?, 'organized', 1)
                """,
                (uid, str(old_tif), str(old_zip)),
            )
            db.commit()

            w._on_specimen_selected(uid)

            assert w._results._current_tiffs[0]["name"].startswith(
                "GXFCG-BLW-SC002-1-RD79-广西防城港"
            )
            assert w._results._current_zips[0]["name"].startswith(
                "GXFCG-BLW-SC002-1-RD79-广西防城港"
            )
            assert "-R-" not in w._results._current_tiffs[0]["name"]
        finally:
            db.close()

    def test_show_all_results_groups_outputs_by_uid(self, tmp_path):
        uid1 = "GXFCG-BLW-SC001-D79-20260618"
        uid2 = "GXFCG-BLW-SC002-RD79-20260618"
        w, db, project_dir = self._make_workbench(tmp_path)
        try:
            self._insert_specimen(db, uid1, project_dir, species_id="SC001")
            self._insert_specimen(db, uid2, project_dir, species_id="SC002")
            result_dir = Path(project_dir) / "results"
            tif1 = result_dir / "GXFCG-BLW-SC001-1-D79-20260618.tif"
            zip1 = result_dir / "GXFCG-BLW-SC001-1-D79-20260618.zip"
            tif2 = result_dir / "GXFCG-BLW-SC002-1-RD79-20260618.tif"
            zip2 = result_dir / "GXFCG-BLW-SC002-1-RD79-20260618.zip"
            for path in (tif1, zip1, tif2, zip2):
                path.write_bytes(b"result")
            db.executemany(
                """
                INSERT INTO grouping
                  (uid, group_index, composed_tiff_path, archive_zip,
                   status, result_sequence)
                VALUES (?, ?, ?, ?, 'organized', 1)
                """,
                [
                    (uid1, 0, str(tif1), str(zip1)),
                    (uid2, 0, str(tif2), str(zip2)),
                ],
            )
            db.commit()

            w._on_show_all_results()

            assert [g["uid"] for g in w._results._current_groups] == [uid1, uid2]
            assert w._results._count.text() == "2 编号 / 2 项"
            assert w._results._current_tiffs[0]["name"] == tif1.name
            assert w._results._current_tiffs[1]["name"] == tif2.name
        finally:
            db.close()

    def test_all_results_group_header_selects_uid(self, tmp_path):
        from app.widgets.results_column import _SpecimenResultHeader

        uid = "GXFCG-BLW-SC001-D79-20260618"
        w, db, project_dir = self._make_workbench(tmp_path)
        try:
            self._insert_specimen(db, uid, project_dir, species_id="SC001")
            result_dir = Path(project_dir) / "results"
            tif = result_dir / "GXFCG-BLW-SC001-1-D79-20260618.tif"
            zip_path = result_dir / "GXFCG-BLW-SC001-1-D79-20260618.zip"
            tif.write_bytes(b"result")
            zip_path.write_bytes(b"result")
            db.execute(
                """
                INSERT INTO grouping
                  (uid, group_index, composed_tiff_path, archive_zip,
                   status, result_sequence)
                VALUES (?, 0, ?, ?, 'organized', 1)
                """,
                (uid, str(tif), str(zip_path)),
            )
            db.commit()

            w._on_show_all_results()
            header = w._results.findChild(_SpecimenResultHeader)
            header.clicked.emit(uid)

            assert w._current_uid == uid
            assert w._results._title.text() == "成果"
            assert w._results._current_tiffs[0]["name"] == tif.name
        finally:
            db.close()

    def test_add_button_creates_current_uid_without_renaming_loaded_specimen(self, tmp_path):
        old_uid = "FJ-XM-B2-DLC001-T95E-20260601"
        new_uid = "FJ-XM-B2-DLC002-T95E-20260601"
        w, db, project_dir = self._make_workbench(tmp_path)
        try:
            self._insert_specimen(db, old_uid, project_dir)

            w._on_specimen_selected(old_uid)
            assert w._naming.persisted_uid() == old_uid
            w._naming._species_id.setText("DLC002")
            w._on_naming_add()

            assert db.execute("SELECT 1 FROM specimens WHERE uid=?", (old_uid,)).fetchone()
            assert db.execute("SELECT 1 FROM specimens WHERE uid=?", (new_uid,)).fetchone()
            assert w._naming.persisted_uid() == new_uid
            assert w._sidebar.current_uid() == new_uid
        finally:
            db.close()

    def test_selected_specimen_save_refuses_to_cover_existing_uid(self, tmp_path):
        old_uid = "FJ-XM-B2-DLC001-T95E-20260601"
        existing_uid = "FJ-XM-B2-DLC002-T95E-20260601"
        w, db, project_dir = self._make_workbench(tmp_path)
        try:
            self._insert_specimen(db, old_uid, project_dir)
            self._insert_specimen(db, existing_uid, project_dir, species_id="DLC002")
            w._on_specimen_selected(old_uid)
            w._naming._species_id.setText("DLC002")

            from PyQt6.QtWidgets import QMessageBox
            with pytest.MonkeyPatch.context() as mp:
                warnings = []
                mp.setattr(
                    QMessageBox,
                    "warning",
                    lambda *args, **kwargs: warnings.append(args) or QMessageBox.StandardButton.Ok,
                )
                w._on_naming_save()

            assert warnings
            assert db.execute("SELECT 1 FROM specimens WHERE uid=?", (old_uid,)).fetchone()
            assert db.execute("SELECT 1 FROM specimens WHERE uid=?", (existing_uid,)).fetchone()
            assert db.execute("SELECT COUNT(*) FROM specimens").fetchone()[0] == 2
        finally:
            db.close()

    def test_delete_selected_specimen_removes_db_references(self, tmp_path):
        uid = "FJ-XM-B2-DLC001-T95E-20260601"
        w, db, project_dir = self._make_workbench(tmp_path)
        try:
            self._insert_specimen(db, uid, project_dir)
            db.execute("INSERT INTO grouping (uid, group_index) VALUES (?, 0)", (uid,))
            db.execute("INSERT INTO tasks (uid) VALUES (?)", (uid,))
            db.commit()
            w._on_specimen_selected(uid)

            from PyQt6.QtWidgets import QMessageBox
            with pytest.MonkeyPatch.context() as mp:
                mp.setattr(
                    QMessageBox,
                    "warning",
                    lambda *args, **kwargs: QMessageBox.StandardButton.Ok,
                )
                w._on_delete_specimen(uid)

            assert not db.execute("SELECT 1 FROM specimens WHERE uid=?", (uid,)).fetchone()
            assert not db.execute("SELECT 1 FROM grouping WHERE uid=?", (uid,)).fetchone()
            assert not db.execute("SELECT 1 FROM tasks WHERE uid=?", (uid,)).fetchone()
            assert w._current_uid is None
        finally:
            db.close()

    def test_new_specimen_save_refuses_uid_existing_in_other_workspace(self, tmp_path):
        from app.db import db_manager
        from app.views.workbench_view import WorkbenchView

        root = tmp_path / "survey_global"
        ws_a = root / "断面A"
        ws_b = root / "断面B"
        ws_a.mkdir(parents=True)
        ws_b.mkdir(parents=True)
        db_a = db_manager.open_project_db(str(ws_a), create=True)
        db_b = db_manager.open_project_db(str(ws_b), create=True)
        uid = "FJ-XM-B2-DLC001-T95E-20260601"
        db_a.execute(
            "INSERT INTO specimens (uid, owner_project_dir) VALUES (?, ?)",
            (uid, str(ws_a)),
        )
        db_a.commit()
        ctx = _make_ctx(project_dir=str(ws_b), db=db_b)
        ctx.current_project_root = str(root)
        w = WorkbenchView(ctx)
        w._naming._province.setText("FJ")
        w._naming._site.setText("XM")
        w._naming._station.setText("B2")
        w._naming._species_id.setText("DLC001")
        w._naming._storage.setText("T95E")
        w._naming._collection_date.setText("20260601")

        from PyQt6.QtWidgets import QMessageBox
        with pytest.MonkeyPatch.context() as mp:
            warnings = []
            mp.setattr(
                QMessageBox,
                "warning",
                lambda *args, **kwargs: warnings.append(args) or QMessageBox.StandardButton.Ok,
            )
            w._on_naming_save()

        assert warnings
        assert "已存在于项目" in warnings[0][2]
        assert not db_b.execute("SELECT 1 FROM specimens WHERE uid=?", (uid,)).fetchone()


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

    def test_rna_filter_shows_only_r_prefix_storage(self, tmp_path):
        from app.widgets.specimen_sidebar import SpecimenSidebar
        project_dir = str(tmp_path)
        db = _make_db(str(tmp_path / "project.db"))
        db.execute(
            "INSERT INTO specimens (uid, storage, owner_project_dir) VALUES (?, ?, ?)",
            ("FJ-XM-B2-DLC001-T95E-20260601", "T95E", project_dir),
        )
        db.execute(
            "INSERT INTO specimens (uid, storage, owner_project_dir) VALUES (?, ?, ?)",
            ("FJ-XM-B2-DLC002-R95E-20260601", "R95E", project_dir),
        )
        db.commit()
        ctx = _make_ctx(project_dir=project_dir, db=db)
        w = SpecimenSidebar(ctx)
        w.refresh()

        assert w._list.count() == 2
        w._set_filter_mode("rna")
        assert w._list.count() == 1
        assert w._list.item(0).data(Qt.ItemDataRole.UserRole) == "FJ-XM-B2-DLC002-R95E-20260601"
        assert w._filter_all_btn.text() == "全部 1/2"
        assert w._filter_rna_btn.text() == "RNA编号 1"
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

    def test_row_print_button_signal(self, tmp_path):
        from PyQt6.QtWidgets import QPushButton
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
        row = w._list.itemWidget(w._list.item(0))
        print_btn = next(
            b for b in row.findChildren(QPushButton)
            if b.toolTip() == "按默认模板打印该编号标签"
        )
        print_btn.click()
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


class TestWorkbenchMonitorAttribution:
    def test_refresh_while_scan_running_invalidates_stale_result(self, tmp_path, monkeypatch):
        """归档完成触发刷新时，整理前启动的旧扫描不能再覆盖监控列表。"""
        from app.views.workbench_view import WorkbenchView

        db = _make_db(str(tmp_path / "project.db"))
        ctx = _make_ctx(project_dir=str(tmp_path), db=db)
        w = WorkbenchView(ctx)

        class _RunningWorker:
            request_id = 7

            def isRunning(self):
                return True

        w._monitor_scan_worker = _RunningWorker()
        w._monitor_scan_request_id = 7
        w._monitor_scan_pending = False
        applied = []
        monkeypatch.setenv("QT_QPA_PLATFORM", "xcb")
        monkeypatch.setattr(
            w, "_apply_monitor_scan_result", lambda result: applied.append(result)
        )
        monkeypatch.setattr(w, "_run_pending_monitor_scan", lambda: None)

        w._refresh_monitor()
        w._on_monitor_scan_finished(7, object())

        assert w._monitor_scan_pending is True
        assert w._monitor_scan_request_id == 8
        assert applied == []
        db.close()

    def test_no_active_clears_historical_jpg_attribution_for_monitor_display_only(self):
        from app.services.monitor_service import FileEntry, ScanResult
        from app.views.workbench_view import WorkbenchView

        w = WorkbenchView(_make_ctx())
        result = ScanResult(
            project_dir="/fake",
            jpg_files=[
                FileEntry(
                    name="IMG_001.jpg",
                    path="/fake/IMG_001.jpg",
                    kind="jpg",
                    size=1000,
                    mtime="2026-06-01T00:00:00+00:00",
                    attributed_specimen_id="OLD-UID",
                )
            ],
        )

        display_result = w._monitor_display_scan_result(result)

        assert display_result.jpg_files[0].attributed_specimen_id is None
        assert result.jpg_files[0].attributed_specimen_id == "OLD-UID"

    def test_apply_scan_keeps_raw_attribution_but_hides_monitor_owner_when_no_active(self):
        from app.services.monitor_service import FileEntry, ScanResult
        from app.views.workbench_view import WorkbenchView

        w = WorkbenchView(_make_ctx())
        result = ScanResult(
            project_dir="/fake",
            jpg_files=[
                FileEntry(
                    name="IMG_001.jpg",
                    path="/fake/IMG_001.jpg",
                    kind="jpg",
                    size=1000,
                    mtime="2026-06-01T00:00:00+00:00",
                    attributed_specimen_id="OLD-UID",
                )
            ],
        )

        w._apply_monitor_scan_result(result)

        assert w._last_scan_result.jpg_files[0].attributed_specimen_id == "OLD-UID"
        assert w._monitor._scan_result.jpg_files[0].attributed_specimen_id is None
        assert result.jpg_files[0].attributed_specimen_id == "OLD-UID"

    def test_display_filter_does_not_break_later_active_attributed_lookup(self, tmp_path):
        from app.services.monitor_service import FileEntry, ScanResult
        from app.views.workbench_view import WorkbenchView

        db = _make_db(":memory:")
        ctx = _make_ctx(project_dir=str(tmp_path), db=db)
        w = WorkbenchView(ctx)
        jpg = tmp_path / "IMG_001.jpg"
        result = ScanResult(
            project_dir=str(tmp_path),
            jpg_files=[
                FileEntry(
                    name=jpg.name,
                    path=str(jpg),
                    kind="jpg",
                    size=1000,
                    mtime="2026-06-01T00:00:00+00:00",
                    attributed_specimen_id="ACTIVE-UID",
                )
            ],
        )

        w._apply_monitor_scan_result(result)

        assert w._get_attributed_jpg_paths("ACTIVE-UID") == [str(jpg)]

    def test_active_keeps_jpg_attribution_for_monitor(self, monkeypatch):
        from app.services.monitor_service import FileEntry, ScanResult
        from app.views.workbench_view import WorkbenchView

        w = WorkbenchView(_make_ctx())
        monkeypatch.setattr(w, "_get_active_uid", lambda: "ACTIVE-UID")
        result = ScanResult(
            project_dir="/fake",
            jpg_files=[
                FileEntry(
                    name="IMG_001.jpg",
                    path="/fake/IMG_001.jpg",
                    kind="jpg",
                    size=1000,
                    mtime="2026-06-01T00:00:00+00:00",
                    attributed_specimen_id="ACTIVE-UID",
                )
            ],
        )

        display_result = w._monitor_display_scan_result(result)

        assert display_result.jpg_files[0].attributed_specimen_id == "ACTIVE-UID"
        assert display_result is result


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

    def test_workbench_result_infos_include_unorganized_composed_tiff(self):
        from app.views.workbench_view import WorkbenchView
        from app.services.grouping_service import Group, SpecimenGrouping

        w = WorkbenchView(_make_ctx())
        grouping = SpecimenGrouping(
            uid="UID1",
            groups=[
                Group(
                    group_index=0,
                    composed_tiff_path="/fake/unorganized.tif",
                    status="composed",
                )
            ],
        )

        tiffs, zips = w._result_infos_from_grouping(grouping)

        assert tiffs == [
            {
                "path": "/fake/unorganized.tif",
                "name": "unorganized.tif",
                "seq": None,
                "owner_uid": "UID1",
                "group_index": 0,
                "registered": True,
            }
        ]
        assert zips == []

    def test_results_column_mode_buttons_emit_requests(self, qtbot):
        from app.widgets.results_column import ResultsColumn

        w = ResultsColumn()
        qtbot.addWidget(w)

        with qtbot.waitSignal(w.show_all_requested, timeout=1000):
            w._all_mode_btn.click()

        with qtbot.waitSignal(w.current_requested, timeout=1000):
            w._current_mode_btn.click()

    def test_results_column_mode_buttons_track_loaded_scope(self, qtbot):
        from app.widgets.results_column import ResultsColumn

        w = ResultsColumn()
        qtbot.addWidget(w)
        w.load_many([
            {
                "uid": "UID-1",
                "tiffs": [{"path": "/fake/a.tif", "name": "a.tif", "seq": 1}],
                "zips": [{"path": "/fake/a.zip", "name": "a.zip", "seq": 1}],
            }
        ])

        assert w._all_mode_btn.isChecked()
        assert not w._current_mode_btn.isChecked()

        w.load_uid("UID-1", [], [])

        assert w._current_mode_btn.isChecked()
        assert not w._all_mode_btn.isChecked()

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
        from app.services.project_settings_service import save_setting
        save_setting(db, "print_settings", {
            "quick_print": True,
            "quick_print_mode": "direct",
            "sample_printer": "",
            "tissue_printer": "",
            "sample_paper_type": "label",
            "tissue_paper_type": "label",
            "tissue_strategy": "direct",
        })
        ctx = _make_ctx(project_dir=project_dir, db=db)
        return WorkbenchView(ctx), ctx, uid, db

    def test_quick_print_rna_defaults_to_sample_and_tissue_jobs(self, tmp_path, monkeypatch):
        from PyQt6.QtPrintSupport import QPrinterInfo
        import app.utils.label_print as lp
        captured = {}

        def _fake_paint(printer, jobs, **kw):
            captured.setdefault("buckets", []).extend(j["bucket"] for j in jobs)
            return True

        monkeypatch.setattr(QPrinterInfo, "defaultPrinterName",
                            staticmethod(lambda: "FakePrinter"))
        # 不构造真 QPrinter —— 离屏环境里 setPrinterName("FakePrinter") 会触发
        # CUPS spooler 查询，QPrinter 析构/事件处理在后续测试里阻塞事件循环
        # （拖死 test_quick_print_no_default_printer_returns_false）。control-flow
        # 单测只需 build_printer 返回桩对象。
        monkeypatch.setattr(lp, "build_printer", lambda job, **kw: MagicMock())
        monkeypatch.setattr(lp, "paint_jobs", _fake_paint)
        w, ctx, uid, db = self._wb(tmp_path, "RD75E")   # R-prefix → tissue too
        assert w._quick_print_labels(uid) is True
        assert captured["buckets"] == ["sample", "tissue"]
        db.close()

    def test_quick_print_can_skip_tissue_when_project_setting_disabled(self, tmp_path, monkeypatch):
        from PyQt6.QtPrintSupport import QPrinterInfo
        from app.services.project_settings_service import save_setting
        import app.utils.label_print as lp
        captured = {}

        def _fake_paint(printer, jobs, **kw):
            captured.setdefault("buckets", []).extend(j["bucket"] for j in jobs)
            return True

        monkeypatch.setattr(QPrinterInfo, "defaultPrinterName",
                            staticmethod(lambda: "FakePrinter"))
        monkeypatch.setattr(lp, "build_printer", lambda job, **kw: MagicMock())
        monkeypatch.setattr(lp, "paint_jobs", _fake_paint)
        w, ctx, uid, db = self._wb(tmp_path, "RD75E")
        save_setting(db, "print_settings", {
            "quick_print": True,
            "quick_print_mode": "direct",
            "include_tissue": False,
            "sample_printer": "",
            "tissue_printer": "",
        })
        assert w._quick_print_labels(uid) is True
        assert captured["buckets"] == ["sample"]
        db.close()

    def test_quick_print_uses_project_template_keys_and_reports_them(self, tmp_path, monkeypatch):
        from PyQt6.QtPrintSupport import QPrinterInfo
        from app.services.project_settings_service import save_setting
        import app.utils.label_print as lp
        captured = {}
        messages = []

        def _fake_paint(printer, jobs, **kw):
            captured.setdefault("templates", []).extend(
                j["template"]["name"] for j in jobs
            )
            return True

        monkeypatch.setattr(QPrinterInfo, "defaultPrinterName",
                            staticmethod(lambda: "FakePrinter"))
        monkeypatch.setattr(lp, "build_printer", lambda job, **kw: MagicMock())
        monkeypatch.setattr(lp, "paint_jobs", _fake_paint)
        w, ctx, uid, db = self._wb(tmp_path, "RD75E")
        monkeypatch.setattr(w, "_status_message", lambda text, msec=4000: messages.append(text))
        save_setting(db, "print_settings", {
            "quick_print": True,
            "quick_print_mode": "direct",
            "include_tissue": True,
            "sample_printer": "",
            "tissue_printer": "",
            "sample_paper_type": "label",
            "tissue_paper_type": "label",
            "tissue_strategy": "direct",
            "sample_template_key": "detailed",
            "tissue_template_key": "tissueMini",
        })
        assert w._quick_print_labels(uid) is True
        assert captured["templates"] == ["详细", "RNAlater 组织管 25×10"]
        assert "样品瓶: 详细" in messages[-1]
        assert "RNAlater: RNAlater 组织管 25×10" in messages[-1]
        db.close()

    def test_quick_print_uses_global_template_default_when_project_has_none(self, tmp_path, monkeypatch):
        from PyQt6.QtPrintSupport import QPrinterInfo
        from app.services.project_settings_service import (
            DEFAULT_PRINT_SETTINGS,
            load_global_print_defaults,
            save_global_print_defaults,
        )
        import app.utils.label_print as lp
        captured = {}

        def _fake_paint(printer, jobs, **kw):
            captured.setdefault("templates", []).extend(
                j["template"]["name"] for j in jobs
            )
            return True

        old = load_global_print_defaults()
        try:
            save_global_print_defaults({
                **DEFAULT_PRINT_SETTINGS,
                "sample_template_key": "detailed",
                "tissue_template_key": "tissueMini",
            })
            monkeypatch.setattr(QPrinterInfo, "defaultPrinterName",
                                staticmethod(lambda: "FakePrinter"))
            monkeypatch.setattr(lp, "build_printer", lambda job, **kw: MagicMock())
            monkeypatch.setattr(lp, "paint_jobs", _fake_paint)
            w, ctx, uid, db = self._wb(tmp_path, "RD75E")
            assert w._quick_print_labels(uid) is True
            assert captured["templates"] == ["详细", "RNAlater 组织管 25×10"]
            db.close()
        finally:
            save_global_print_defaults(old)

    def test_quick_print_can_queue_tissue_for_same_printer_sheet_mode(self, tmp_path, monkeypatch):
        from PyQt6.QtPrintSupport import QPrinterInfo
        import app.services.label_service as label_service
        import app.utils.label_print as lp
        from app.services import rna_label_queue_service as rna_queue
        captured = {}

        def _fake_paint(printer, jobs, **kw):
            captured.setdefault("buckets", []).extend(j["bucket"] for j in jobs)
            return True

        monkeypatch.setattr(QPrinterInfo, "defaultPrinterName",
                            staticmethod(lambda: "FakePrinter"))
        monkeypatch.setattr(label_service, "persisted_paper_type",
                            lambda bucket: "a4" if bucket == "tissue" else "label")
        monkeypatch.setattr(lp, "build_printer", lambda job, **kw: MagicMock())
        monkeypatch.setattr(lp, "paint_jobs", _fake_paint)
        w, ctx, uid, db = self._wb(tmp_path, "RD75E")
        from app.services.project_settings_service import save_setting
        save_setting(db, "print_settings", {
            "quick_print": True,
            "quick_print_mode": "direct",
            "sample_printer": "",
            "tissue_printer": "",
            "sample_paper_type": "label",
            "tissue_paper_type": "a4",
            "tissue_strategy": "auto",
        })
        assert w._quick_print_labels(uid) is True
        assert captured["buckets"] == ["sample"]
        assert rna_queue.pending_uids(db) == [uid]
        db.close()

    def test_quick_print_setting_can_force_studio_fallback(self, tmp_path, monkeypatch):
        from PyQt6.QtPrintSupport import QPrinterInfo
        from app.services.project_settings_service import save_setting
        import app.utils.label_print as lp

        monkeypatch.setattr(QPrinterInfo, "defaultPrinterName",
                            staticmethod(lambda: "FakePrinter"))
        monkeypatch.setattr(lp, "build_printer", lambda job, **kw: MagicMock())
        monkeypatch.setattr(lp, "paint_jobs", lambda printer, jobs, **kw: True)
        w, ctx, uid, db = self._wb(tmp_path, "D95E")
        save_setting(db, "print_settings", {
            "quick_print": False,
            "include_tissue": False,
        })
        assert w._quick_print_labels(uid) is False
        db.close()

    def test_quick_print_dialog_mode_prints_direct_when_printer_is_configured(
        self, tmp_path, monkeypatch
    ):
        from PyQt6.QtPrintSupport import QPrinterInfo
        from app.services.project_settings_service import save_setting
        import app.utils.label_print as lp

        monkeypatch.setattr(QPrinterInfo, "defaultPrinterName",
                            staticmethod(lambda: "FakePrinter"))
        monkeypatch.setattr(lp, "build_printer", lambda job, **kw: MagicMock())
        monkeypatch.setattr(lp, "paint_jobs", lambda printer, jobs, **kw: True)

        import app.widgets.print_dialog as print_dialog
        monkeypatch.setattr(
            print_dialog,
            "PrintJobDialog",
            lambda *a, **k: pytest.fail("configured quick print should not prompt"),
        )

        w, ctx, uid, db = self._wb(tmp_path, "D95E")
        save_setting(db, "print_settings", {
            "quick_print": True,
            "quick_print_mode": "dialog",
            "sample_printer": "",
            "sample_paper_type": "label",
            "include_tissue": False,
        })

        assert w._quick_print_labels(uid) is True
        db.close()

    def test_quick_print_dialog_mode_routes_niimbot_selection(
        self, tmp_path, monkeypatch
    ):
        from PyQt6.QtPrintSupport import QPrinterInfo
        from app.services import niimbot_print_service as niimbot
        from app.services.project_settings_service import save_setting
        import app.utils.label_print as lp
        import app.utils.windows_print as windows_print

        captured = {}

        class Dialog:
            def __init__(self, jobs, parent=None):
                captured["dialog_jobs"] = jobs

            def exec(self):
                from PyQt6.QtWidgets import QDialog
                return QDialog.DialogCode.Accepted

            def selected_printer(self):
                return niimbot.printer_id("COM5")

        monkeypatch.setattr(QPrinterInfo, "defaultPrinterName",
                            staticmethod(lambda: ""))
        monkeypatch.setattr(QPrinterInfo, "availablePrinters",
                            staticmethod(lambda: []))
        monkeypatch.setattr(windows_print, "is_available", lambda: True)
        monkeypatch.setattr(windows_print, "windows_default_printer_name", lambda: "")
        monkeypatch.setattr(windows_print, "windows_printer_names", lambda: [])
        monkeypatch.setattr(
            windows_print,
            "print_jobs_with_windows_dialog",
            lambda *a, **k: pytest.fail("Windows bridge should not handle NIIMBOT"),
        )
        monkeypatch.setattr(
            niimbot,
            "available_printers",
            lambda: [niimbot.NiimbotPrinter(
                id=niimbot.printer_id("COM5"),
                name="NIIMBOT B203 USB (COM5)",
                port="COM5",
            )],
        )
        monkeypatch.setattr(
            niimbot,
            "available_printer_ids",
            lambda: {niimbot.printer_id("COM5")},
        )

        def fake_niimbot_print(jobs, **kw):
            captured["niimbot_jobs"] = jobs
            captured["niimbot_kw"] = kw
            return "NIIMBOT B203 USB (COM5)"

        monkeypatch.setattr(niimbot, "print_jobs_to_niimbot", fake_niimbot_print)
        monkeypatch.setattr(lp, "build_printer", lambda *a, **k: pytest.fail("Qt printer should not run"))
        monkeypatch.setattr(lp, "paint_jobs", lambda *a, **k: pytest.fail("Qt paint should not run"))

        import app.widgets.print_dialog as print_dialog
        monkeypatch.setattr(print_dialog, "PrintJobDialog", Dialog)

        w, ctx, uid, db = self._wb(tmp_path, "D95E")
        save_setting(db, "print_settings", {
            "quick_print": True,
            "quick_print_mode": "dialog",
            "sample_printer": "",
            "sample_paper_type": "label",
            "include_tissue": False,
        })

        assert w._quick_print_labels(uid) is True
        assert captured["niimbot_kw"]["printer_name"] == "NIIMBOT:B203:COM5"
        db.close()

    def test_quick_print_no_default_printer_returns_false(self, tmp_path, monkeypatch):
        from PyQt6.QtPrintSupport import QPrinterInfo
        monkeypatch.setattr(QPrinterInfo, "defaultPrinterName",
                            staticmethod(lambda: ""))
        w, ctx, uid, db = self._wb(tmp_path, "D95E")
        assert w._quick_print_labels(uid) is False
        db.close()

    def test_on_print_labels_does_not_fall_back_to_studio(self, tmp_path, monkeypatch):
        from PyQt6.QtPrintSupport import QPrinterInfo
        monkeypatch.setattr(QPrinterInfo, "defaultPrinterName",
                            staticmethod(lambda: ""))   # no printer → fallback
        w, ctx, uid, db = self._wb(tmp_path, "D95E")
        ctx.pending_label_uid = None
        w._on_print_labels(uid)
        assert ctx.pending_label_uid is None
        db.close()

    def test_sidebar_print_button_fallback_shows_status(self, tmp_path, monkeypatch):
        from PyQt6.QtPrintSupport import QPrinterInfo
        from PyQt6.QtWidgets import QPushButton
        monkeypatch.setattr(QPrinterInfo, "defaultPrinterName",
                            staticmethod(lambda: ""))   # no printer → fallback
        w, ctx, uid, db = self._wb(tmp_path, "D95E")
        ctx.pending_label_uid = None
        messages = []
        monkeypatch.setattr(w, "_status_message", lambda text, msec=4000: messages.append(text))
        w._sidebar.refresh()
        row = w._sidebar._list.itemWidget(w._sidebar._list.item(0))
        print_btn = next(
            b for b in row.findChildren(QPushButton)
            if b.toolTip() == "按默认模板打印该编号标签"
        )

        print_btn.click()

        assert ctx.pending_label_uid is None
        assert messages and "未能开始打印" in messages[-1]
        db.close()

    def test_on_print_labels_quick_path_no_fallback(self, tmp_path, monkeypatch):
        from PyQt6.QtPrintSupport import QPrinterInfo
        import app.utils.label_print as lp
        monkeypatch.setattr(QPrinterInfo, "defaultPrinterName",
                            staticmethod(lambda: "FakePrinter"))
        monkeypatch.setattr(lp, "build_printer", lambda job, **kw: MagicMock())
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
    """Test that MonitorPanel._delete_selected_pending_files identifies TIFF in selection and deletes JPGs."""

    def test_actual_jpg_deletion(self, tmp_path):
        """_delete_selected_pending_files must actually call os.unlink on confirmed JPG paths."""
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
            w._delete_selected_pending_files()
        assert not os.path.exists(jpg_path), "JPG must be deleted after confirm"

    def test_tiff_delete_asks_confirm_then_deletes(self, tmp_path):
        """TIFF 可删：删前弹确认框，确认才删。"""
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
            w._delete_selected_pending_files()
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
        """_delete_selected_pending_files must detect .tif / .tiff paths in selection."""
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

    def test_drop_parser_accepts_jpg_and_tiff(self, tmp_path):
        """Dragging external Helicon JPG+TIF keeps both file types."""
        from PyQt6.QtCore import QMimeData, QUrl
        from app.widgets.monitor_panel import MonitorPanel

        jpg = tmp_path / "P6202064.JPG"
        tif = tmp_path / "HeliconFocus.tif"
        txt = tmp_path / "note.txt"
        jpg.write_bytes(b"jpg")
        tif.write_bytes(b"tif")
        txt.write_text("skip")
        mime = QMimeData()
        mime.setUrls([
            QUrl.fromLocalFile(str(jpg)),
            QUrl.fromLocalFile(str(tif)),
            QUrl.fromLocalFile(str(txt)),
        ])

        paths = MonitorPanel._jpg_paths_from_mime(mime)

        assert paths == [str(jpg), str(tif)]


class TestWorkbenchImportMedia:
    def test_add_photos_imports_jpg_and_tiff_to_capture_workspace(self, tmp_path):
        """Main add/drop path supports external Helicon workflow: JPG + TIF."""
        from app.views.workbench_view import WorkbenchView

        project = tmp_path / "project"
        camera = tmp_path / "camera"
        project.mkdir()
        camera.mkdir()
        jpg = camera / "P6202064.JPG"
        tif = camera / "HeliconFocus.tif"
        jpg.write_bytes(b"jpg")
        tif.write_bytes(b"tif")
        w = WorkbenchView(_make_ctx(project_dir=str(project)))
        w._refresh_monitor = MagicMock()

        imported = w._import_media_paths([str(jpg), str(tif)], source="添加照片")

        assert sorted(Path(p).name for p in imported) == ["HeliconFocus.tif", "P6202064.JPG"]
        assert (project / "incoming-jpg" / "P6202064.JPG").read_bytes() == b"jpg"
        assert (project / "incoming-jpg" / "HeliconFocus.tif").read_bytes() == b"tif"
        w._refresh_monitor.assert_called_once()

    def test_add_photos_button_starts_background_import(self, tmp_path, monkeypatch):
        """User-facing add-photo action must not copy files on the GUI thread."""
        from PyQt6.QtWidgets import QFileDialog
        from app.views.workbench_view import WorkbenchView

        class _Signal:
            def __init__(self):
                self.callbacks = []

            def connect(self, callback):
                self.callbacks.append(callback)

        class FakeImportWorker:
            instances = []

            def __init__(self, source_paths, incoming_dir, parent=None):
                self.source_paths = list(source_paths)
                self.incoming_dir = incoming_dir
                self.parent = parent
                self.started_import = _Signal()
                self.completed = _Signal()
                self.failed = _Signal()
                self.finished = _Signal()
                self.started = False
                FakeImportWorker.instances.append(self)

            def isRunning(self):
                return self.started

            def start(self):
                self.started = True

            def deleteLater(self):
                pass

        project = tmp_path / "project"
        camera = tmp_path / "camera"
        project.mkdir()
        camera.mkdir()
        jpg = camera / "P6202064.JPG"
        jpg.write_bytes(b"jpg")

        monkeypatch.setattr(
            QFileDialog,
            "getOpenFileNames",
            lambda *args, **kwargs: ([str(jpg)], ""),
        )
        monkeypatch.setattr(
            "app.workers.photo_import_worker.PhotoImportWorker",
            FakeImportWorker,
        )

        w = WorkbenchView(_make_ctx(project_dir=str(project)))
        w._import_media_paths = MagicMock()
        w._refresh_monitor = MagicMock()

        w._on_add_jpg_files()

        assert len(FakeImportWorker.instances) == 1
        worker = FakeImportWorker.instances[0]
        assert worker.source_paths == [str(jpg)]
        assert Path(worker.incoming_dir) == project / "incoming-jpg"
        assert worker.started is True
        w._import_media_paths.assert_not_called()
        w._refresh_monitor.assert_not_called()
        assert not w._monitor._add_btn.isEnabled()

    def test_background_import_finish_refreshes_monitor(self, tmp_path):
        from app.services.photo_import_service import PhotoImportResult
        from app.views.workbench_view import WorkbenchView

        project = tmp_path / "project"
        project.mkdir()
        jpg = project / "incoming-jpg" / "P6202064.JPG"
        result = PhotoImportResult(
            imported_paths=[str(jpg)],
            imported_jpg_paths=[str(jpg)],
        )
        w = WorkbenchView(_make_ctx(project_dir=str(project)))
        w._refresh_monitor = MagicMock()

        w._on_photo_import_finished(
            result,
            source="添加照片",
            incoming_label="incoming-jpg",
            project_dir=str(project),
        )

        w._refresh_monitor.assert_called_once()

    def test_clear_pending_queue_uses_safe_clear_service_not_delete(self, tmp_path, monkeypatch):
        from app.services.photo_import_service import PendingClearResult
        from app.views.workbench_view import WorkbenchView

        project = tmp_path / "project"
        project.mkdir()
        incoming_file = project / "incoming-jpg" / "wrong.tif"
        calls = []

        def fake_clear_pending(project_dir, paths):
            calls.append((project_dir, list(paths)))
            return PendingClearResult(stashed_paths=[str(project / "_data" / "cleared-pending" / "wrong.tif")])

        monkeypatch.setattr(
            "app.services.photo_import_service.clear_pending_imports",
            fake_clear_pending,
        )
        w = WorkbenchView(_make_ctx(project_dir=str(project)))
        w._refresh_monitor = MagicMock()
        w._monitor._on_select_none = MagicMock()
        w._monitor._delete_paths = MagicMock()
        w._status_message = MagicMock()

        w._on_clear_pending_queue([str(incoming_file)])

        assert calls == [(str(project), [str(incoming_file)])]
        w._monitor._delete_paths.assert_not_called()
        w._monitor._on_select_none.assert_called_once()
        w._refresh_monitor.assert_called_once()
        w._status_message.assert_called_once()


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
    def __init__(self, d=None): self._d = dict(d or {})
    def value(self, k, default=None): return self._d.get(k, default)
    def setValue(self, k, v): self._d[k] = v


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

    def test_settings_drawer_naming_rule_signal_refreshes_naming_panel(self, tmp_path):
        from app.services.project_settings_service import (
            DEFAULT_NAMING_RULES,
            save_setting,
        )
        from app.views.workbench_view import WorkbenchView

        project_dir = str(tmp_path / "proj")
        Path(project_dir).mkdir(parents=True)
        db = _make_db(":memory:")
        ctx = _make_ctx(project_dir=project_dir, db=db)
        w = WorkbenchView(ctx)
        try:
            assert "depth" not in w._naming._dynamic_naming_edits

            rules = dict(DEFAULT_NAMING_RULES)
            rules["custom_fields"] = [{"key": "depth", "label": "水深"}]
            rules["components"] = [
                "province", "site", "depth", "species_id", "date_seg"
            ]
            rules["required"] = dict(DEFAULT_NAMING_RULES["required"])
            rules["required"]["depth"] = True
            save_setting(db, "naming_rules", rules)

            w._settings_drawer.naming_rules_changed.emit()

            assert "depth" in w._naming._dynamic_naming_edits
            assert any("水深" in label.text() for label in w._naming._field_labels.values())
        finally:
            db.close()


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
        w._set_group_editor_expanded(False)
        assert w._group_body.isHidden()
        # Toggle back on
        w._set_group_editor_expanded(True)
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

    def test_delete_composed_group_removes_record_but_keeps_tiff(self, tmp_path):
        """删除分组只删记录；已关联 TIFF 时也不能碰文件本体。"""
        from app.widgets.grouping_panel import GroupingPanel
        from app.services.grouping_service import Group, SpecimenGrouping
        tiff_path = tmp_path / "result.tif"
        tiff_path.write_bytes(b"TIF")
        ctx = _make_ctx()
        w = GroupingPanel(ctx)
        sg = SpecimenGrouping(
            uid="UID1",
            groups=[
                Group(group_index=0, jpg_paths=["/a.jpg"],
                      composed_tiff_path=str(tiff_path)),
            ],
        )
        w.load_grouping("UID1", sg)
        w.delete_group(0)
        assert w._grouping.groups == []
        assert tiff_path.exists()

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
        assert dlg._list.item(0).toolTip() == ""

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

    def test_live_dup_check_does_not_scan_other_workspaces(self, tmp_path, monkeypatch):
        from app.services import specimen_catalog_service as catalog
        from app.widgets.naming_panel import NamingPanel

        def fail_global_scan(*_args, **_kwargs):
            raise AssertionError("live duplicate check must stay local")

        monkeypatch.setattr(catalog, "conflicting_uid_hits", fail_global_scan)
        db = _make_db(str(tmp_path / "p.db"))
        ctx = _make_ctx(db=db)
        w = NamingPanel(ctx)
        w._check_duplicate("FJ-XM-B2-DLC001-T95E-20260601")
        assert w._dup_warn.isHidden()
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

    def test_acknowledge_existing_uid_clears_dup_warn(self, tmp_path):
        from app.widgets.naming_panel import NamingPanel
        db = _make_db(str(tmp_path / "p.db"))
        uid = "GXFCG-BLW-SC001-D79-20260618"
        db.execute(
            "INSERT INTO specimens (uid, owner_project_dir) VALUES (?, ?)",
            (uid, "/some/project"),
        )
        db.commit()
        ctx = _make_ctx(db=db)
        w = NamingPanel(ctx)
        w._province.setText("GXFCG")
        w._site.setText("BLW")
        w._species_id.setText("SC001")
        w._storage.setText("D79")
        w._collection_date.setText("20260618")
        w._photo_date.setText("20260618")
        w._update_preview()
        assert not w._dup_warn.isHidden()
        w.acknowledge_existing_uid(uid)
        assert w._dup_warn.isHidden()
        assert w.persisted_uid() == uid
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
            w._auto_fill_geo_area_from_lon_lat()
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

    def test_compose_workbench_dialog_renders_tiff_preview(self, tmp_path, qapp):
        from PIL import Image

        from app.views.workbench_view import _ComposeWorkbenchDialog

        jpg1 = tmp_path / "a.jpg"
        jpg2 = tmp_path / "b.jpg"
        tiff = tmp_path / "out.tif"
        jpg1.write_bytes(b"jpg1")
        jpg2.write_bytes(b"jpg2")
        Image.new("RGB", (64, 40), "#336699").save(tiff)

        dlg = _ComposeWorkbenchDialog(
            [str(jpg1), str(jpg2)],
            str(tiff),
            {"method": 1, "radius": 8, "smoothing": 4},
        )

        assert dlg._tiff_preview.source_pixmap() is not None
        assert not dlg._tiff_preview.source_pixmap().isNull()

    def test_compose_workbench_preview_supports_windows_zoom(self, tmp_path, qapp):
        from PIL import Image

        from app.views.workbench_view import _ComposeWorkbenchDialog

        jpg1 = tmp_path / "a.jpg"
        tiff = tmp_path / "out.tif"
        jpg1.write_bytes(b"jpg1")
        Image.new("RGB", (64, 40), "#336699").save(tiff)

        dlg = _ComposeWorkbenchDialog(
            [str(jpg1)],
            str(tiff),
            {"method": 1, "radius": 8, "smoothing": 4},
        )

        source_width = dlg._tiff_preview.source_pixmap().width()

        dlg._tiff_preview.set_zoom_percent(150)
        assert dlg._tiff_preview._fit_to_window is False
        assert dlg._tiff_preview._zoom_percent == 150
        expected_width = int(source_width * 1.5)
        assert abs(dlg._tiff_preview.pixmap().width() - expected_width) <= 1

        dlg._tiff_preview.actual_size()
        assert dlg._tiff_preview._zoom_percent == 100
        assert dlg._tiff_preview.pixmap().width() == source_width

        dlg._tiff_preview.fit_to_window()
        assert dlg._tiff_preview._fit_to_window is True

    def test_compose_workbench_dialog_renders_source_thumbnails(self, tmp_path, qapp):
        from PIL import Image

        from app.views.workbench_view import _ComposeWorkbenchDialog

        jpg1 = tmp_path / "a.jpg"
        jpg2 = tmp_path / "b.jpg"
        tiff = tmp_path / "out.tif"
        Image.new("RGB", (32, 24), "#884422").save(jpg1)
        Image.new("RGB", (32, 24), "#228844").save(jpg2)
        Image.new("RGB", (64, 40), "#336699").save(tiff)

        dlg = _ComposeWorkbenchDialog(
            [str(jpg1), str(jpg2)],
            str(tiff),
            {"method": 1, "radius": 8, "smoothing": 4},
        )

        assert len(dlg._checks) == 2
        assert all(not checkbox.icon().isNull() for checkbox, _ in dlg._checks)


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

    def test_background_finish_does_not_reselect_old_uid(self, tmp_path):
        """Background compose/organize must not steal focus from the current specimen."""
        w, collab, db = self._make_workbench_with_collab(tmp_path)
        uid = "FJ-XM-B2-DLC001-T95E-20260601"
        w._sidebar = MagicMock()

        w._on_helicon_finished(uid, select_uid=False)
        w._on_organize_finished(uid, select_uid=False)

        assert w._sidebar.refresh.call_count == 2
        w._sidebar.select_uid.assert_not_called()
        assert [
            args for args, _kwargs in collab.post_photo_index.call_args_list
        ] == [(uid, "tiff"), (uid, "zip")]
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

    def test_right_rail_has_visible_vertical_scrollbar_boundary(self):
        from app.views.workbench_view import WorkbenchView
        w = WorkbenchView(_make_ctx())

        assert w._right_scroll.objectName() == "RightRailScroll"
        assert (
            w._right_scroll.verticalScrollBarPolicy()
            == Qt.ScrollBarPolicy.ScrollBarAlwaysOn
        )

    def test_right_rail_drag_floor_keeps_form_clear_of_scrollbar(self):
        from app.views.workbench_view import WorkbenchView
        w = WorkbenchView(_make_ctx())

        # §7 旧: required 用 _widget_natural_width(偏好宽) —— 2026-07-11 起右栏
        # 最小值改用硬最小值(minimumSizeHint), header 拆行后能收窄而不裁字段
        # (窄屏三栏才放得下)。契约:最小值 ≥ 内容硬最小 + 竖滚动条宽即可。
        required = (
            w._right_rail_widget.minimumSizeHint().width()
            + w._right_scroll.verticalScrollBar().sizeHint().width()
        )

        assert w._right_scroll.minimumWidth() >= required

    def test_workbench_splitters_keep_clear_drag_targets(self):
        import app.views.workbench_view as workbench_view
        from app.views.workbench_view import WorkbenchView
        ctx = _make_ctx()
        ctx.settings._qs = _FakeQS()
        w = WorkbenchView(ctx)

        assert w._outer_splitter.objectName() == "WorkbenchSplitter"
        assert w._outer_splitter.handleWidth() >= 14
        assert not w._outer_splitter.childrenCollapsible()
        assert w._centre_splitter.objectName() == "WorkbenchVerticalSplitter"
        assert w._centre_splitter.handleWidth() >= 14
        assert not w._centre_splitter.childrenCollapsible()
        assert w._sidebar.maximumWidth() == QWIDGETSIZE_MAX
        assert w._right_scroll.maximumWidth() == QWIDGETSIZE_MAX
        assert w._sidebar.minimumWidth() == w._sidebar_min_width()
        w._right_scroll.setMinimumWidth(w._right_rail_min_width())
        assert w._right_scroll.minimumWidth() == w._right_rail_min_width()

        w._save_workbench_outer_splitter()
        w._save_workbench_centre_splitter()
        assert workbench_view._WORKBENCH_OUTER_SPLITTER_STATE_KEY in ctx.settings._qs._d
        assert workbench_view._WORKBENCH_CENTRE_SPLITTER_STATE_KEY in ctx.settings._qs._d
        assert w.findChild(QScrollArea, "WorkbenchScroll") is None

        w._toggle_right_rail()
        assert w._right_scroll.minimumWidth() == w._right_rail_collapsed_width()
        assert w._right_scroll.maximumWidth() == w._right_scroll.minimumWidth()

        w._toggle_right_rail()
        assert w._right_scroll.minimumWidth() == w._right_rail_min_width()
        assert w._right_scroll.maximumWidth() == QWIDGETSIZE_MAX

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
        w._taxon_card._extra_identifications.setPlainText(
            "Taxon beta | 中文乙 | FamB | GenusB | mixed tube"
        )
        w._flush_rail_save()
        row = db.execute(
            "SELECT collector, collection_date, photo_notes, notes, family_cn, raw_json "
            "FROM specimens WHERE uid=?", (uid,)
        ).fetchone()
        assert row["collector"] == "COLL"
        assert row["collection_date"] == "20260101"
        assert row["photo_notes"] == "PN"
        assert row["notes"] == "NOTE"
        assert row["family_cn"] == "芋螺科"
        raw = json.loads(row["raw_json"])
        assert raw["additional_identifications"] == [{
            "scientific_name": "Taxon beta",
            "scientific_name_cn": "中文乙",
            "family": "FamB",
            "genus": "GenusB",
            "notes": "mixed tube",
        }]
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
        assert MW.call_args.kwargs["delete_jpg"] is False
        assert w._supp_pending is not None
        assert w._supp_pending.uid == "FJ-XM-B2-DLC001-T95E-20260601"
        db.close()

    def test_supplementary_default_uses_two_phase_deletion(self, qt_app, tmp_path):
        """Default deletion setting is DEFERRED: worker archives with
        delete_jpg=False; actual deletion happens only after finalize succeeds
        (commit_jpg_deletion_after_archive), mirroring the organize path."""
        from unittest.mock import patch, MagicMock
        from app.views.workbench_view import WorkbenchView

        proj, db = self._project_with_specimen(tmp_path)
        ctx = _make_ctx(proj, db)
        w = WorkbenchView(ctx)
        incoming = os.path.join(proj, "incoming-jpg")
        jpg = os.path.join(incoming, "a.jpg")
        tiff = os.path.join(incoming, "FJ-XM-B2-DLC001-1-T95E-20260601.tif")
        Path(jpg).write_bytes(b"x")
        Path(tiff).write_bytes(b"x")

        with patch("app.workers.supp_compression_worker.SuppCompressionWorker") as MW, \
                patch("app.utils.ui.warn"), patch("app.utils.ui.info"):
            inst = MagicMock()
            MW.return_value = inst
            w._run_supplementary([jpg, tiff])

        inst.start.assert_called_once()
        assert MW.call_args.kwargs["delete_jpg"] is False
        assert w._supp_request_delete_jpg is True
        db.close()

    def test_supplementary_jpgs_survive_when_finalize_fails(self, qt_app, tmp_path):
        """RED LINE: if finalize_supplementary_archive raises, the loose JPGs
        must NOT have been deleted (two-phase deletion regression test)."""
        from unittest.mock import patch
        from app.views.workbench_view import WorkbenchView
        from app.services.supplementary_service import SuppGroup
        from app.services.archive_service import ZipResult

        proj, db = self._project_with_specimen(tmp_path)
        ctx = _make_ctx(proj, db)
        w = WorkbenchView(ctx)

        incoming = os.path.join(proj, "incoming-jpg")
        jpg = os.path.join(incoming, "a.jpg")
        tiff = os.path.join(incoming, "FJ-XM-B2-DLC001-1-T95E-20260601.tif")
        zip_ = os.path.join(incoming, "FJ-XM-B2-DLC001-1-T95E-20260601.zip")
        Path(jpg).write_bytes(b"\xff\xd8jpg")
        Path(tiff).write_bytes(b"tiffdata")
        Path(zip_).write_bytes(b"zipdata-zipdata-zipdata-zipdata")

        w._supp_pending = SuppGroup(
            jpg_paths=[jpg],
            tiff_path=tiff,
            uid="FJ-XM-B2-DLC001-T95E-20260601",
            specimen={"uid": "FJ-XM-B2-DLC001-T95E-20260601"},
        )
        w._supp_request_delete_jpg = True  # user setting wants deletion

        result = ZipResult(
            zip_path=zip_,
            zip_size=32,
            file_count=1,
            total_original=3,
            total_compressed=3,
            saved_percent=0,
            delete_jpg=False,
            requested_delete_jpg=False,
            deletion_skipped_reason="",
            manifest={"format": "jpg-zip", "files": []},
            ok=True,
        )

        def _boom(*_a, **_k):
            raise RuntimeError("simulated finalize failure")

        with patch(
            "app.services.capture_workflow_service.finalize_supplementary_archive",
            side_effect=_boom,
        ), patch("app.utils.ui.info"), patch("app.utils.ui.warn"):
            w._on_supp_finished(result)

        assert os.path.isfile(jpg), "finalize 失败时 JPG 原片必须仍在磁盘上"
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
        # Source TIFF moved into results; this is organise, not deletion.
        assert not os.path.isfile(tiff)
        db.close()

    def test_no_project_supplementary_spawns_local_worker(self, qt_app, tmp_path):
        """无项目补处理：JPG+TIF 可直接本地整理，ZIP 输出到 TIF 同目录。"""
        from unittest.mock import patch, MagicMock
        from app.views.workbench_view import WorkbenchView

        ctx = _make_ctx(project_dir=None, db=None)
        w = WorkbenchView(ctx)
        jpg = tmp_path / "a.jpg"
        tiff = tmp_path / "external-result.tif"
        jpg.write_bytes(b"\xff\xd8jpg")
        tiff.write_bytes(b"II*\x00tif")

        with patch("app.workers.supp_compression_worker.SuppCompressionWorker") as MW, \
                patch("app.utils.ui.warn"), patch("app.utils.ui.info"):
            inst = MagicMock()
            MW.return_value = inst
            w._run_supplementary([str(jpg), str(tiff)])

        inst.start.assert_called_once()
        _, args, kwargs = MW.mock_calls[0]
        assert args[0] == [str(jpg)]
        assert args[1] == str(tiff)
        assert args[2] == str(tmp_path)
        assert kwargs["output_dir"] == str(tmp_path)
        assert w._supp_pending.uid == ""


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

        assert ctx.collab_service.store.get_task("U1").status is TaskStatus.SHOOTING
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

        assert ctx.collab_service.store.get_task("U1").status is TaskStatus.DONE
        assert activation_service.get_collab_status(db, "U1") == "done"
        assert w._monitor._phase_pills["done"].isChecked()
        assert not w._monitor._phase_pills["shooting"].isChecked()

    def test_click_without_active_uid_is_noop(self, tmp_path):
        w, ctx, db = self._make_view(tmp_path)
        w._on_phase_clicked("shooting")  # 无激活编号,不应崩溃
        assert ctx.collab_service.store.get_task("shooting") is None
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
        assert ctx.collab_service.store.get_task("OTHER").status is TaskStatus.ORGANIZING
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
        assert ctx.collab_service.store.get_task("B").status is TaskStatus.DONE

        w._on_phase_mark("B", "organizing")  # 回退

        assert ctx.collab_service.store.get_task("B").status is TaskStatus.ORGANIZING
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
        assert ctx.collab_service.store.get_task(uid).status is TaskStatus.SHOOTING
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
        assert ctx.collab_service.store.get_task(uid).status is TaskStatus.ORGANIZING

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

    def test_open_grouping_without_activation_creates_unassigned_grouping(self, tmp_path):
        """未激活编号时，新组暂存为未归属，保存新编号后可迁入该编号。"""
        from app.services.grouping_service import ADHOC_GROUPING_UID, load_grouping

        w, ctx, db = self._make_view(tmp_path)
        w._on_open_grouping()
        assert w._grouping._uid == ADHOC_GROUPING_UID
        assert not w._grouping._add_btn.isHidden()
        jpg = tmp_path / "P001.JPG"
        jpg.write_bytes(b"jpg")
        group_index = w._grouping._append_group(jpg_paths=[str(jpg)])
        w._grouping._rebuild()
        assert group_index == 0
        assert len(w._grouping._grouping.groups) == 1

        uid = self._fill_new_specimen(w)
        w._on_naming_save()

        assert w._grouping._uid == uid
        assert len(load_grouping(db, uid).groups) == 1
        assert load_grouping(db, ADHOC_GROUPING_UID).groups == []
        assert w._sidebar.current_uid() == uid
        row = w._sidebar._all_items[0]
        assert row["uid"] == uid
        assert row["progress"]["total"] == 1

    def test_blank_unassigned_group_is_not_claimed_on_save(self, tmp_path):
        """未归属空组不应在保存新编号时变成目标编号的空角度。"""
        from app.services.grouping_service import ADHOC_GROUPING_UID, load_grouping

        w, ctx, db = self._make_view(tmp_path)
        w._on_open_grouping()
        assert w._grouping._uid == ADHOC_GROUPING_UID
        w._grouping._add_group()
        assert len(w._grouping._grouping.groups) == 1

        uid = self._fill_new_specimen(w)
        w._on_naming_save()

        assert load_grouping(db, uid).groups == []
        assert load_grouping(db, ADHOC_GROUPING_UID).groups == []

    def test_right_save_does_not_claim_unassigned_group_for_selected_inactive_uid(self, tmp_path):
        """左侧选中未激活编号时，新组仍保持未归属，不自动挂到该编号。"""
        from app.services.grouping_service import ADHOC_GROUPING_UID, load_grouping

        w, ctx, db = self._make_view(tmp_path)
        uid = self._fill_new_specimen(w)
        w._on_naming_save()

        # Simulate an existing specimen being edited in the right rail without
        # using the grouping-popup entry point as an implicit activation path.
        w._load_specimen(uid)
        assert w._grouping._uid == ADHOC_GROUPING_UID

        w._grouping._add_group()
        assert len(w._grouping._grouping.groups) == 1

        w._metadata._collector.setText("李四")
        w._on_save_metadata(uid)

        assert w._grouping._uid == ADHOC_GROUPING_UID
        assert len(w._grouping._grouping.groups) == 1
        assert load_grouping(db, uid).groups == []

    def test_active_specimen_second_angle_is_not_duplicate_uid(self, tmp_path):
        """当前激活 voucher 的成果序号2不是新标本，不应报 voucher 重复。"""
        from app.services import activation_service

        w, ctx, db = self._make_view(tmp_path)
        uid = "GXFCG-BLW-BZC002-R-20260618"
        db.execute(
            """
            INSERT INTO specimens (
                uid, id, province, site, storage, collection_date, photo_date,
                owner_project_dir
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (uid, "BZC002", "GXFCG", "BLW", "R", "20260618", "20260618", ctx.current_project_dir),
        )
        db.commit()
        activation_service.activate(ctx.current_project_dir, db, uid)
        w._current_uid = None

        n = w._naming
        n._province.setText("GXFCG")
        n._site.setText("BLW")
        n._species_id.setText("BZC002")
        n._storage.setText("R")
        n._collection_date.setText("20260618")
        n._photo_date.setText("20260618")
        n._seq.setValue(2)
        n._update_preview()

        assert n.current_uid() == uid
        assert n.current_result_id() == "GXFCG-BLW-BZC002-2-R-20260618"
        assert n._dup_warn.isHidden()
        assert n.persisted_uid() == uid
        assert not n._preview_save_btn.isHidden()
        assert n._pin_btn.text() == "添加"

    def test_active_uid_still_conflicts_when_editing_different_specimen(self, tmp_path):
        """右侧正在编辑 B 时，不能把左侧激活的 A 当作同一标本放过。"""
        from app.services import activation_service

        w, ctx, db = self._make_view(tmp_path)
        active_uid = "GXFCG-BLW-BZC002-R-20260618"
        editing_uid = "GXFCG-BLW-SC001-D79-20260618"
        for uid, sid, storage in (
            (active_uid, "BZC002", "R"),
            (editing_uid, "SC001", "D79"),
        ):
            db.execute(
                """
                INSERT INTO specimens (
                    uid, id, province, site, storage, collection_date, photo_date,
                    owner_project_dir
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (uid, sid, "GXFCG", "BLW", storage, "20260618", "20260618", ctx.current_project_dir),
            )
        db.commit()
        activation_service.activate(ctx.current_project_dir, db, active_uid)

        w._load_specimen(editing_uid)
        n = w._naming
        n._species_id.setText("BZC002")
        n._storage.setText("R")
        n._update_preview()

        assert n.current_uid() == active_uid
        assert not n._dup_warn.isHidden()
        assert n.persisted_uid() == editing_uid

    def test_save_blocks_incomplete_hard_required_uid(self, tmp_path, monkeypatch):
        """只填地区/日期生成的短 UID 不得写入标本列表。"""
        from PyQt6.QtWidgets import QMessageBox
        w, ctx, db = self._make_view(tmp_path)
        n = w._naming
        n._province.setText("FJ")
        n._collection_date.setText("20260601")
        n._photo_date.setText("20260601")
        n._update_preview()
        warnings = []
        monkeypatch.setattr(
            QMessageBox,
            "warning",
            staticmethod(lambda *args, **kwargs: warnings.append(args)),
        )

        w._on_naming_save()

        assert db.execute("SELECT count(*) FROM specimens").fetchone()[0] == 0
        assert warnings
        assert "地区/样地" in warnings[0][2]
        assert "物种编号" in warnings[0][2]
        assert "保存方式" in warnings[0][2]


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

    def test_fallback_interval_120s(self, tmp_path):
        w, _, db = self._make_view(tmp_path)
        assert w._fallback_timer.interval() == 120000
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


class TestSuppAutonameByActive:
    """补处理兜底：外部名 TIF + 有激活编号 → 自动按激活编号成果名改名再归档。"""

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

    def _jpg(self, tmp_path):
        j = tmp_path / "a.jpg"; j.write_bytes(b"\xff\xd8\xff")
        return str(j)

    def test_external_tif_renamed_by_active(self, tmp_path):
        from app.services import activation_service
        w, ctx, db, project_dir = self._make_view(tmp_path)
        activation_service.activate(project_dir, db, self.UID)
        tif = Path(project_dir) / "results" / "HeliconFocus.tif"
        tif.write_bytes(b"II*\x00")
        out = w._supp_autoname_tiff_by_active(db, project_dir, [self._jpg(tmp_path), str(tif)])
        tif_out = [p for p in out if p.lower().endswith((".tif", ".tiff"))][0]
        assert Path(tif_out).name == "FJ-XM-B2-DLC001-1-T95E-20260601.tif"  # 按激活编号
        assert not tif.exists()

    def test_no_active_keeps_external_name(self, tmp_path):
        w, ctx, db, project_dir = self._make_view(tmp_path)
        tif = Path(project_dir) / "results" / "HeliconFocus.tif"
        tif.write_bytes(b"II*\x00")
        out = w._supp_autoname_tiff_by_active(db, project_dir, [self._jpg(tmp_path), str(tif)])
        assert any("HeliconFocus" in p for p in out)        # 无激活→不改名
        assert tif.exists()

    def test_conforming_tif_unchanged(self, tmp_path):
        from app.services import activation_service
        w, ctx, db, project_dir = self._make_view(tmp_path)
        db.execute("INSERT INTO specimens(uid, owner_project_dir) VALUES(?,?)",
                   (self.UID, project_dir)); db.commit()
        activation_service.activate(project_dir, db, self.UID)
        tif = Path(project_dir) / "results" / "FJ-XM-B2-DLC001-1-T95E-20260601.tif"
        tif.write_bytes(b"II*\x00")                          # 名已规范且标本在库
        out = w._supp_autoname_tiff_by_active(db, project_dir, [self._jpg(tmp_path), str(tif)])
        assert any(p == str(tif) for p in out)               # 反查到→原样不动
        assert tif.exists()


class TestOpenGroupingLoadsActive:
    """打开分组工具时, 有激活则载入激活编号；无激活则进入未归属任务。"""

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

    def test_load_active_uid_without_specimen_row_fills_naming_fields(self, tmp_path):
        """激活编号即使还没保存 specimens 行，右侧也应从 UID 拆出编号字段。"""
        from app.views.workbench_view import WorkbenchView

        project_dir = str(tmp_path / "proj")
        Path(project_dir, "_data").mkdir(parents=True)
        db = _make_db(str(tmp_path / "proj" / "_data" / "project.db"))
        ctx = _make_ctx(project_dir=project_dir, db=db)
        ctx.collab_service = None
        w = WorkbenchView(ctx)

        w._load_specimen("GXFCG-BLW-B2-SC004-RD79-20260618")

        assert w._naming._province.text() == "GXFCG"
        assert w._naming._site.text() == "BLW"
        assert w._naming._station.text() == "B2"
        assert w._naming._species_id.text() == "SC004"
        assert w._naming._storage.text() == "RD79"
        assert w._naming._collection_date.text() == "20260618"

    def test_open_without_activation_uses_unassigned_not_naming_draft(self, tmp_path):
        """不激活：命名表单草稿不能成为分组归属，只能进入未归属任务。"""
        from app.views.workbench_view import WorkbenchView
        from app.services import activation_service
        from app.services.grouping_service import ADHOC_GROUPING_UID
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
        assert w._grouping._uid == ADHOC_GROUPING_UID
        assert not w._grouping._add_btn.isHidden()
        assert uid not in w._grouping._uid_label.text()           # 草稿编号不显示
        w._grouping._add_group()
        assert len(w._grouping._grouping.groups) == 1
        assert w._grouping._grouping.groups[0].angle_label == "结果1"

    def test_open_without_current_rebinds_stale_grouping_panel(self, tmp_path):
        """分组面板残留旧编号时，无激活再打开应切到空的未归属任务。"""
        from app.views.workbench_view import WorkbenchView
        from app.services import activation_service
        from app.services.grouping_service import (
            ADHOC_GROUPING_UID,
            Group,
            SpecimenGrouping,
        )

        stale_uid = "GXFCG-BLW-SC001-D79-20260618"
        project_dir = str(tmp_path / "proj")
        Path(project_dir, "_data").mkdir(parents=True)
        db = _make_db(str(tmp_path / "proj" / "_data" / "project.db"))
        ctx = _make_ctx(project_dir=project_dir, db=db)
        ctx.collab_service = None
        w = WorkbenchView(ctx)
        w._grouping.load_grouping(
            stale_uid,
            SpecimenGrouping(uid=stale_uid, groups=[Group(group_index=3)]),
        )
        w._current_uid = None
        assert activation_service.get_active_uid(db) is None

        w._on_open_grouping()

        assert w._grouping._uid == ADHOC_GROUPING_UID
        assert w._grouping._grouping.groups == []
        assert not w._grouping._add_btn.isHidden()

    def test_open_with_selected_inactive_uid_starts_unassigned_task(self, tmp_path):
        """左侧选中未激活编号时，新组不得归属该编号，只能暂存未归属。"""
        from app.views.workbench_view import WorkbenchView
        from app.services import activation_service
        from app.services.grouping_service import (
            ADHOC_GROUPING_UID,
            Group,
            load_grouping,
            save_grouping,
        )

        uid = "GXFCG-BLW-SC001-D79-20260618"
        project_dir = str(tmp_path / "proj")
        Path(project_dir, "_data").mkdir(parents=True)
        db = _make_db(str(tmp_path / "proj" / "_data" / "project.db"))
        save_grouping(db, uid, [
            Group(group_index=0, angle_label="角度1", status="organized"),
        ], clean_phantoms=False)
        ctx = _make_ctx(project_dir=project_dir, db=db)
        ctx.collab_service = None
        w = WorkbenchView(ctx)

        assert activation_service.get_active_uid(db) is None
        w._load_specimen(uid)
        assert w._current_uid == uid

        w._on_open_grouping()

        assert w._grouping._uid == ADHOC_GROUPING_UID
        assert w._grouping._grouping.groups == []
        assert not w._grouping._add_btn.isHidden()
        w._grouping._add_group()
        assert len(w._grouping._grouping.groups) == 1
        w._flush_grouping_save()
        assert len(load_grouping(db, ADHOC_GROUPING_UID).groups) == 1
        groups = load_grouping(db, uid).groups
        assert len(groups) == 1
        assert groups[0].angle_label == "角度1"

    def test_open_without_activation_ignores_stale_current_uid(self, tmp_path):
        """取消激活后即使 _current_uid 残留旧编号，重新打开也应切到未归属任务。"""
        from app.views.workbench_view import WorkbenchView
        from app.services import activation_service
        from app.services.grouping_service import (
            ADHOC_GROUPING_UID,
            Group,
            SpecimenGrouping,
        )

        stale_uid = "GXFCG-BLW-BZC003-R-20260618"
        project_dir = str(tmp_path / "proj")
        Path(project_dir, "_data").mkdir(parents=True)
        db = _make_db(str(tmp_path / "proj" / "_data" / "project.db"))
        ctx = _make_ctx(project_dir=project_dir, db=db)
        ctx.collab_service = None
        w = WorkbenchView(ctx)
        w._grouping.load_grouping(
            stale_uid,
            SpecimenGrouping(uid=stale_uid, groups=[Group(group_index=2)]),
        )
        w._current_uid = stale_uid
        assert activation_service.get_active_uid(db) is None

        w._on_open_grouping()

        assert w._grouping._uid == ADHOC_GROUPING_UID
        assert w._grouping._grouping.groups == []
        assert not w._grouping._add_btn.isHidden()

    def test_activate_existing_uid_does_not_claim_current_adhoc_grouping(self, tmp_path):
        """激活只切换拍摄目标，不能把无编号整理单元自动挂到该标本。"""
        from app.views.workbench_view import WorkbenchView
        from app.services.grouping_service import (
            ADHOC_GROUPING_UID,
            Group,
            SpecimenGrouping,
            load_grouping,
            save_grouping,
        )

        uid = "GXFCG-BLW-SC001-D79-20260618"
        project_dir = str(tmp_path / "proj")
        Path(project_dir, "_data").mkdir(parents=True)
        db = _make_db(str(tmp_path / "proj" / "_data" / "project.db"))
        db.execute(
            """
            INSERT INTO specimens (
                uid, id, province, site, storage, collection_date, photo_date,
                owner_project_dir
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (uid, "SC001", "GXFCG", "BLW", "D79", "20260618", "20260618", project_dir),
        )
        save_grouping(
            db,
            uid,
            [Group(group_index=0, angle_label="角度1")],
            clean_phantoms=False,
        )

        jpg = tmp_path / "P001.JPG"
        jpg.write_bytes(b"jpg")
        adhoc_group = Group(group_index=0, angle_label="结果1", jpg_paths=[str(jpg)])
        save_grouping(db, ADHOC_GROUPING_UID, [adhoc_group], clean_phantoms=False)
        db.commit()
        ctx = _make_ctx(project_dir=project_dir, db=db)
        ctx.collab_service = None
        w = WorkbenchView(ctx)
        w._grouping.load_grouping(
            ADHOC_GROUPING_UID,
            SpecimenGrouping(
                uid=ADHOC_GROUPING_UID,
                groups=[adhoc_group],
            ),
        )

        w._on_sidebar_activate(uid)

        groups = load_grouping(db, uid).groups
        assert len(groups) == 1
        assert groups[0].angle_label == "角度1"
        assert groups[0].jpg_paths == []
        adhoc_groups = load_grouping(db, ADHOC_GROUPING_UID).groups
        assert len(adhoc_groups) == 1
        assert adhoc_groups[0].jpg_paths == [str(jpg)]
        assert w._grouping._uid == uid
        assert len(w._grouping._grouping.groups) == 1

    def test_deactivate_starts_fresh_unassigned_grouping_task(self, tmp_path):
        """去激活后再点新组，应进入新的未归属任务，从结果1开始。"""
        from app.views.workbench_view import WorkbenchView
        from app.services import activation_service
        from app.services.grouping_service import (
            ADHOC_GROUPING_UID,
            Group,
            save_grouping,
        )

        uid = "GXFCG-BLW-SC001-D79-20260618"
        project_dir = str(tmp_path / "proj")
        Path(project_dir, "_data").mkdir(parents=True)
        db = _make_db(str(tmp_path / "proj" / "_data" / "project.db"))
        save_grouping(db, uid, [
            Group(group_index=0, angle_label="角度1"),
            Group(group_index=1, angle_label="角度2"),
        ], clean_phantoms=False)
        save_grouping(db, ADHOC_GROUPING_UID, [
            Group(group_index=8, angle_label="旧临时组"),
        ], clean_phantoms=False)
        ctx = _make_ctx(project_dir=project_dir, db=db)
        ctx.collab_service = None
        w = WorkbenchView(ctx)

        activation_service.activate(project_dir, db, uid)
        w._load_specimen(uid)
        assert w._current_uid == uid
        assert w._grouping._uid == uid
        assert len(w._grouping._grouping.groups) == 2

        w._on_sidebar_deactivate(uid)

        assert activation_service.get_active_uid(db) is None
        assert w._current_uid is None
        assert w._grouping._uid == ADHOC_GROUPING_UID
        assert w._grouping._grouping.groups == []
        assert not w._grouping._add_btn.isHidden()

        w._grouping._add_group()
        groups = w._grouping._grouping.groups
        assert len(groups) == 1
        assert groups[0].group_index == 0
        assert groups[0].angle_label == "结果1"


class TestPostHocTiffRecognition:
    """事后整理：从已有 TIF 文件名识别编号，并允许用户补正保存方式。"""

    def test_storage_only_does_not_overwrite_group_output(self, tmp_path):
        from app.services.grouping_service import ADHOC_GROUPING_UID, Group, SpecimenGrouping
        from app.views.workbench_view import WorkbenchView

        project_dir = str(tmp_path / "proj")
        Path(project_dir, "_data").mkdir(parents=True)
        db = _make_db(str(tmp_path / "proj" / "_data" / "project.db"))
        w = WorkbenchView(_make_ctx(project_dir=project_dir, db=db))
        original = "GXFCG-BLW-SC004-2-R-20260618-广西防城港-白龙尾-独齿沙蚕-20260618"
        group = Group(group_index=2, output_name=original)
        w._grouping.load_grouping(
            ADHOC_GROUPING_UID,
            SpecimenGrouping(uid=ADHOC_GROUPING_UID, groups=[group]),
        )

        w._naming._storage.setText("RD79")

        assert w._sync_grouping_outputs_from_naming() is False
        assert group.output_name == original

    def test_storage_change_does_not_rewrite_organized_group_output(self, tmp_path):
        from app.services.grouping_service import Group, SpecimenGrouping
        from app.views.workbench_view import WorkbenchView

        project_dir = str(tmp_path / "proj")
        Path(project_dir, "_data").mkdir(parents=True)
        db = _make_db(str(tmp_path / "proj" / "_data" / "project.db"))
        w = WorkbenchView(_make_ctx(project_dir=project_dir, db=db))
        uid = "GXFCG-BLW-SC001-D79-20260618"
        group = Group(
            group_index=0,
            output_name="",
            status="organized",
            archive_zip=str(tmp_path / "GXFCG-BLW-SC001-1-D79-20260618.zip"),
        )
        w._grouping.load_grouping(uid, SpecimenGrouping(uid=uid, groups=[group]))
        n = w._naming
        n._province.setText("GXFCG")
        n._site.setText("BLW")
        n._species_id.setText("SC001")
        n._storage.setText("RD79")
        n._collection_date.setText("20260618")

        assert w._sync_grouping_outputs_from_naming() is False
        assert group.output_name == ""

    def test_recognized_tiff_fills_right_panel_and_syncs_output_name(self, tmp_path):
        from app.services.grouping_service import ADHOC_GROUPING_UID, Group, SpecimenGrouping
        from app.views.workbench_view import WorkbenchView

        project_dir = str(tmp_path / "proj")
        Path(project_dir, "_data").mkdir(parents=True)
        db = _make_db(str(tmp_path / "proj" / "_data" / "project.db"))
        w = WorkbenchView(_make_ctx(project_dir=project_dir, db=db))
        tiff = tmp_path / (
            "GXFCG-BLW-SC004-2-R-20260618-"
            "广西防城港-白龙尾-独齿沙蚕-20260618.tif"
        )
        tiff.write_bytes(b"tif")
        group = Group(group_index=2, composed_tiff_path=str(tiff), output_name=tiff.stem)
        w._grouping.load_grouping(
            ADHOC_GROUPING_UID,
            SpecimenGrouping(uid=ADHOC_GROUPING_UID, groups=[group]),
        )
        w._naming._storage.setText("RD79")

        w._apply_tiff_filename_recognition(str(tiff))

        assert w._naming._province.text() == "GXFCG"
        assert w._naming._site.text() == "BLW"
        assert w._naming._species_id.text() == "SC004"
        assert w._naming._storage.text() == "RD79"
        assert w._naming._collection_date.text() == "20260618"
        assert w._grouping._uid == "GXFCG-BLW-SC004-RD79-20260618"
        assert group.output_name != "3-RD79"
        assert "GXFCG-BLW-SC004-2-RD79" in group.output_name

    def test_imported_different_tiff_overwrites_stale_right_panel(self, tmp_path):
        from app.views.workbench_view import WorkbenchView

        project_dir = str(tmp_path / "proj")
        Path(project_dir, "_data").mkdir(parents=True)
        db = _make_db(str(tmp_path / "proj" / "_data" / "project.db"))
        w = WorkbenchView(_make_ctx(project_dir=project_dir, db=db))
        n = w._naming
        n._province.setText("GXFCG")
        n._site.setText("BLW")
        n._species_id.setText("SC001")
        n._storage.setText("D79")
        n._collection_date.setText("20260618")
        w._current_uid = "GXFCG-BLW-SC001-D79-20260618"

        tiff = tmp_path / "GXQZ-SNW-XTC003-1-R-260619.tif"
        tiff.write_bytes(b"tif")

        w._apply_tiff_filename_recognition(str(tiff), overwrite=True)

        assert n._province.text() == "GXQZ"
        assert n._site.text() == "SNW"
        assert n._species_id.text() == "XTC003"
        assert n._storage.text() == "R"
        assert n._collection_date.text() == "20260619"
        assert w._current_uid is None

    def test_import_tiff_save_stops_pending_debounce_and_keeps_jpgs(
        self, tmp_path, monkeypatch
    ):
        from app.services.grouping_service import (
            Group,
            SpecimenGrouping,
            load_grouping,
        )
        from app.views.workbench_view import WorkbenchView

        uid = "GXFCG-BLW-SC006-R-20260618"
        project_dir = str(tmp_path / "proj")
        Path(project_dir, "_data").mkdir(parents=True)
        db = _make_db(str(tmp_path / "proj" / "_data" / "project.db"))
        ctx = _make_ctx(project_dir=project_dir, db=db)
        ctx.collab_service = None
        w = WorkbenchView(ctx)
        monkeypatch.setattr(w, "_refresh_monitor", lambda: None)
        tif = tmp_path / "external.tif"
        tif.write_bytes(b"II*\x00")
        missing_jpg = str(tmp_path / "temporarily-hidden.JPG")
        w._grouping.load_grouping(
            uid,
            SpecimenGrouping(
                uid=uid,
                groups=[
                    Group(
                        group_index=0,
                        jpg_paths=[missing_jpg],
                        composed_tiff_path=str(tif),
                        status="composed",
                    )
                ],
            ),
        )
        w._save_timer.start()

        w._persist_imported_group_tiff(uid, 0)

        assert not w._save_timer.isActive()
        saved = load_grouping(db, uid).groups[0]
        assert saved.composed_tiff_path == str(tif)
        assert saved.jpg_paths == [missing_jpg]

    def test_tiff_naming_check_uses_selected_group_tiff_without_folder_prompt(
        self, tmp_path, monkeypatch
    ):
        from app.services.grouping_service import Group, SpecimenGrouping
        from app.views import workbench_view
        from app.views.workbench_view import WorkbenchView
        from app.widgets import tiff_naming_audit_dialog

        uid = "GXFCG-BLW-JinSC003-R-20260618"
        project_dir = str(tmp_path / "proj")
        Path(project_dir, "_data").mkdir(parents=True)
        db = _make_db(str(tmp_path / "proj" / "_data" / "project.db"))
        ctx = _make_ctx(project_dir=project_dir, db=db)
        ctx.collab_service = None
        w = WorkbenchView(ctx)
        tif0 = tmp_path / "GXFCG-BLW-JinSC002-1-R-20260618.tif"
        tif1 = tmp_path / "GXFCG-BLW-JinSC003-2-R-20260618-广西防城港.tif"
        tif0.write_bytes(b"tif")
        tif1.write_bytes(b"tif")
        w._grouping.load_grouping(
            uid,
            SpecimenGrouping(
                uid=uid,
                groups=[
                    Group(group_index=0, composed_tiff_path=str(tif0)),
                    Group(group_index=1, composed_tiff_path=str(tif1)),
                ],
            ),
        )
        w._grouping._selected_group_indexes.add(1)
        monkeypatch.setattr(
            workbench_view.ui,
            "get_existing_directory",
            lambda *a, **k: (_ for _ in ()).throw(
                AssertionError("folder prompt not expected")
            ),
        )
        captured = {}

        class _FakeAuditDialog:
            def __init__(self, audit, parent=None):
                captured["audit"] = audit

            def exec(self):
                captured["shown"] = True
                return 0

        monkeypatch.setattr(
            tiff_naming_audit_dialog,
            "TiffNamingAuditDialog",
            _FakeAuditDialog,
        )

        w._on_tiff_naming_check()

        audit = captured["audit"]
        assert captured["shown"] is True
        assert [item.name for item in audit.items] == [tif1.name]
        assert audit.items[0].valid is True
        assert audit.items[0].sequence == 2

    def test_delete_result_tiff_path_uses_registered_group_undo(
        self, tmp_path, monkeypatch
    ):
        from app.services.grouping_service import Group, save_grouping
        from app.views.workbench_view import WorkbenchView

        project_dir = str(tmp_path / "proj")
        Path(project_dir, "_data").mkdir(parents=True)
        db = _make_db(str(tmp_path / "proj" / "_data" / "project.db"))
        uid = "GXFCG-BLW-BZC003-R-20260618"
        tif = tmp_path / "GXFCG-BLW-BZC003-4-R-20260618.tif"
        tif.write_bytes(b"tif")
        save_grouping(
            db,
            uid,
            [Group(group_index=4, composed_tiff_path=str(tif))],
            clean_phantoms=False,
        )
        ctx = _make_ctx(project_dir=project_dir, db=db)
        ctx.collab_service = None
        w = WorkbenchView(ctx)
        called = {}
        monkeypatch.setattr(
            w,
            "_on_undo_compose",
            lambda undo_uid, group_index: called.update(
                {"uid": undo_uid, "group_index": group_index}
            ),
        )

        w._on_delete_result_tiff_path(str(tif))

        assert called == {"uid": uid, "group_index": 4}

    def test_results_column_shows_unorganized_imported_tiff(self, tmp_path):
        from app.services.grouping_service import Group, SpecimenGrouping
        from app.views.workbench_view import WorkbenchView

        project_dir = str(tmp_path / "proj")
        Path(project_dir, "_data").mkdir(parents=True)
        db = _make_db(str(tmp_path / "proj" / "_data" / "project.db"))
        w = WorkbenchView(_make_ctx(project_dir=project_dir, db=db))
        imported_tif = tmp_path / "GXQZ-SNW-XTC003-1-R-260619.tif"
        imported_tif.write_bytes(b"tif")
        organized_tif = tmp_path / "GXFCG-BLW-SC001-2-D79-20260618.tif"
        organized_tif.write_bytes(b"tif")
        organized_zip = organized_tif.with_suffix(".zip")
        organized_zip.write_bytes(b"zip")
        grouping = SpecimenGrouping(uid="UID", groups=[
            Group(group_index=0, composed_tiff_path=str(imported_tif), status="composed"),
            Group(
                group_index=1,
                composed_tiff_path=str(organized_tif),
                archive_zip=str(organized_zip),
                status="organized",
            ),
        ])

        w._refresh_results_column("UID", grouping)

        assert [Path(x["path"]).name for x in w._results._current_tiffs] == [
            imported_tif.name,
            organized_tif.name
        ]
        assert [Path(x["path"]).name for x in w._results._current_zips] == [
            organized_zip.name
        ]

    def test_organized_group_with_zip_is_noop_when_organize_requested(
        self, tmp_path, monkeypatch
    ):
        from app.services.grouping_service import Group, load_grouping, save_grouping
        from app.views.workbench_view import WorkbenchView

        project_dir = str(tmp_path / "proj")
        Path(project_dir, "_data").mkdir(parents=True)
        db = _make_db(str(tmp_path / "proj" / "_data" / "project.db"))
        uid = "GXFCG-BLW-SC001-R-20260618"
        tif = tmp_path / "result.tif"
        zipf = tmp_path / "result.zip"
        tif.write_bytes(b"tif")
        zipf.write_bytes(b"zip")
        save_grouping(
            db,
            uid,
            [
                Group(
                    group_index=0,
                    composed_tiff_path=str(tif),
                    archive_zip=str(zipf),
                    status="organized",
                )
            ],
            clean_phantoms=False,
        )
        w = WorkbenchView(_make_ctx(project_dir=project_dir, db=db))
        monkeypatch.setattr(w, "_status_message", lambda *_a, **_k: None)

        assert w._on_organise_requested(uid, 0) is True

        saved = load_grouping(db, uid).groups[0]
        assert saved.status == "organized"
        assert saved.archive_zip == str(zipf)

    def test_link_existing_result_pair_to_right_uid_moves_from_old_uid(
        self, tmp_path, monkeypatch
    ):
        from app.services.grouping_service import Group, load_grouping, save_grouping
        from app.views.workbench_view import WorkbenchView

        project_dir = str(tmp_path / "proj")
        Path(project_dir, "_data").mkdir(parents=True)
        db = _make_db(str(tmp_path / "proj" / "_data" / "project.db"))
        old_uid = "GXHP-SL-YMC002-R-20260616"
        target_uid = "GXHP-SL-YMC001-R-T95E-20260616"
        tif = tmp_path / "GXHP-SL-YMC001-2-R-260616.tif"
        zipf = tmp_path / "GXHP-SL-YMC001-2-R-260616.zip"
        tif.write_bytes(b"tif")
        zipf.write_bytes(b"zip")
        save_grouping(db, old_uid, [
            Group(
                group_index=0,
                composed_tiff_path=str(tif),
                archive_zip=str(zipf),
                status="organized",
            )
        ], clean_phantoms=False)
        w = WorkbenchView(_make_ctx(project_dir=project_dir, db=db))
        monkeypatch.setattr(w._naming, "current_uid", lambda: target_uid)
        monkeypatch.setattr(w, "_refresh_monitor", lambda: None)

        w._on_link_result_to_right_uid(str(tif), str(zipf))

        assert load_grouping(db, old_uid).groups == []
        target_groups = load_grouping(db, target_uid).groups
        assert len(target_groups) == 1
        assert target_groups[0].composed_tiff_path == str(tif.resolve())
        assert target_groups[0].archive_zip == str(zipf.resolve())
        assert target_groups[0].status == "organized"

    def test_organize_rename_suggestion_uses_corrected_storage(self, tmp_path, monkeypatch):
        from PyQt6.QtWidgets import QDialog

        from app.services.grouping_service import ADHOC_GROUPING_UID, Group, SpecimenGrouping
        from app.views.workbench_view import WorkbenchView

        project_dir = str(tmp_path / "proj")
        Path(project_dir, "_data").mkdir(parents=True)
        db = _make_db(str(tmp_path / "proj" / "_data" / "project.db"))
        w = WorkbenchView(_make_ctx(project_dir=project_dir, db=db))
        tiff = tmp_path / (
            "GXFCG-BLW-SC002-2-R-260618-"
            "广西防城港-白龙尾-独齿沙蚕-20260618.tif"
        )
        tiff.write_bytes(b"tif")
        group = Group(group_index=1, composed_tiff_path=str(tiff), output_name=tiff.stem)
        grouping = SpecimenGrouping(uid=ADHOC_GROUPING_UID, groups=[group])
        w._grouping.load_grouping(ADHOC_GROUPING_UID, grouping)

        n = w._naming
        n._province.setText("GXFCG")
        n._site.setText("BLW")
        n._species_id.setText("SC002")
        n._storage.setText("RD79")
        n._collection_date.setText("20260618")
        n._photo_date.setText("20260618")

        seen = {}

        class FakeRenameDialog:
            def __init__(self, current_name, suggested_name, parent=None):
                seen["current_name"] = current_name
                seen["suggested_name"] = suggested_name

            def exec(self):
                return QDialog.DialogCode.Rejected

        import app.widgets.tiff_rename_dialog as rename_dialog

        monkeypatch.setattr(rename_dialog, "TiffRenameDialog", FakeRenameDialog)

        result = w._maybe_rename_tiff_before_organize(
            db, ADHOC_GROUPING_UID, grouping, group, project_dir
        )

        assert result is False
        assert seen["suggested_name"] == (
            "GXFCG-BLW-SC002-2-RD79-260618-广西防城港-白龙尾-独齿沙蚕-20260618.tif"
        )


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

    def test_compose_implicit_no_active_with_auto_archive_still_does_not_guess(
        self, tmp_path, monkeypatch
    ):
        w, ctx, db = self._make_view(
            tmp_path,
            [str(tmp_path / "a.jpg"), str(tmp_path / "b.jpg")],
        )
        ctx.settings.auto_organize_after_compose = True
        monkeypatch.setattr(w, "_get_active_uid", lambda: None)
        monkeypatch.setattr(w._monitor, "selected_jpg_paths", lambda: [])
        calls = []
        monkeypatch.setattr(
            w,
            "_on_compose_requested",
            lambda uid, idx: calls.append((uid, idx)),
        )

        w._on_compose_implicit()

        assert calls == []

    def test_compose_implicit_with_active_but_no_selection_is_noop_when_auto_off(
        self, tmp_path, monkeypatch
    ):
        w, ctx, db = self._make_view(
            tmp_path,
            [str(tmp_path / "a.jpg"), str(tmp_path / "b.jpg")],
        )
        ctx.settings.auto_organize_after_compose = False
        monkeypatch.setattr(w, "_get_active_uid", lambda: self.UID)
        monkeypatch.setattr(w._monitor, "selected_jpg_paths", lambda: [])
        monkeypatch.setattr(w._monitor, "auto_compress_enabled", lambda: False)
        calls = []
        monkeypatch.setattr(
            w,
            "_compose_group_headless",
            lambda uid, idx, done: calls.append((uid, idx)),
        )
        monkeypatch.setattr(
            w,
            "_on_compose_requested",
            lambda uid, idx: calls.append((uid, idx)),
        )

        w._on_compose_implicit()

        assert calls == []

    def test_compose_implicit_with_active_auto_archive_uses_unoccupied_jpgs(
        self, tmp_path, monkeypatch
    ):
        from app.services.grouping_service import load_grouping

        jpgs = [str(tmp_path / "a.jpg"), str(tmp_path / "b.jpg")]
        w, ctx, db = self._make_view(tmp_path, jpgs)
        ctx.settings.auto_organize_after_compose = True
        monkeypatch.setattr(w, "_get_active_uid", lambda: self.UID)
        monkeypatch.setattr(w._monitor, "selected_jpg_paths", lambda: [])
        calls = []
        monkeypatch.setattr(
            w,
            "_on_compose_requested",
            lambda uid, idx: calls.append((uid, idx)),
        )

        w._on_compose_implicit()

        assert calls == [(self.UID, 0)]
        grouping = load_grouping(db, self.UID)
        assert grouping.groups[0].jpg_paths == jpgs

    def test_compose_implicit_with_active_toolbar_auto_archive_uses_unoccupied_jpgs(
        self, tmp_path, monkeypatch
    ):
        from app.services.grouping_service import load_grouping

        jpgs = [str(tmp_path / "a.jpg"), str(tmp_path / "b.jpg")]
        w, ctx, db = self._make_view(tmp_path, jpgs)
        ctx.settings.auto_organize_after_compose = False
        monkeypatch.setattr(w, "_get_active_uid", lambda: self.UID)
        monkeypatch.setattr(w._monitor, "selected_jpg_paths", lambda: [])
        monkeypatch.setattr(w._monitor, "auto_compress_enabled", lambda: True)
        calls = []
        monkeypatch.setattr(
            w,
            "_on_compose_requested",
            lambda uid, idx: calls.append((uid, idx)),
        )

        w._on_compose_implicit()

        assert calls == [(self.UID, 0)]
        grouping = load_grouping(db, self.UID)
        assert grouping.groups[0].jpg_paths == jpgs

    def test_selected_owned_jpgs_confirm_output_target_without_activation(self, tmp_path, monkeypatch):
        from app.views.workbench_view import _SelectedComposeTarget
        from app.services.grouping_service import load_grouping

        selected = [str(tmp_path / "a.jpg"), str(tmp_path / "b.jpg")]
        w, ctx, db = self._make_view(tmp_path, [])
        monkeypatch.setattr(w, "_get_active_uid", lambda: None)
        monkeypatch.setattr(w._monitor, "selected_jpg_paths", lambda: selected)
        monkeypatch.setattr(w._monitor, "selected_jpg_owner_uids", lambda: [self.UID])
        prompts = []

        def _prompt(jpg_count, *, organise, default_uid=""):
            prompts.append((jpg_count, organise, default_uid))
            return _SelectedComposeTarget(uid=default_uid)

        monkeypatch.setattr(w, "_prompt_selected_compose_target", _prompt)
        calls = []
        monkeypatch.setattr(w, "_on_compose_requested", lambda uid, idx: calls.append((uid, idx)))

        w._on_compose_implicit()

        assert prompts == [(2, False, self.UID)]
        assert calls == [(self.UID, 0)]
        grouping = load_grouping(db, self.UID)
        assert grouping.groups[0].jpg_paths == selected

    def test_selected_jpgs_use_active_uid_without_prompt(self, tmp_path, monkeypatch):
        from app.services.grouping_service import load_grouping

        selected = [str(tmp_path / "a.jpg"), str(tmp_path / "b.jpg")]
        active_uid = "GXFCG-BLW-BZC003-R-20260618"
        w, ctx, db = self._make_view(tmp_path, [])
        monkeypatch.setattr(w, "_get_active_uid", lambda: active_uid)
        monkeypatch.setattr(w._monitor, "selected_jpg_paths", lambda: selected)
        monkeypatch.setattr(w._monitor, "selected_jpg_owner_uids", lambda: [self.UID])
        monkeypatch.setattr(
            w,
            "_prompt_selected_compose_target",
            lambda *_a, **_k: pytest.fail("active UID should auto-name without prompt"),
        )
        assigned = []
        monkeypatch.setattr(w, "_assign_selected_jpgs_to_uid", lambda uid, paths: assigned.append((uid, paths)))
        calls = []
        monkeypatch.setattr(w, "_on_compose_requested", lambda uid, idx: calls.append((uid, idx)))

        w._on_compose_implicit()

        assert assigned == [(active_uid, selected)]
        assert calls == [(active_uid, 0)]
        grouping = load_grouping(db, active_uid)
        assert grouping.groups[0].jpg_paths == selected

    def test_selected_unowned_jpgs_require_output_name_without_activation(self, tmp_path, monkeypatch):
        from app.views.workbench_view import _SelectedComposeTarget
        from app.services.grouping_service import ADHOC_GROUPING_UID, load_grouping

        selected = [str(tmp_path / "a.jpg"), str(tmp_path / "b.jpg")]
        w, ctx, db = self._make_view(tmp_path, [])
        monkeypatch.setattr(w, "_get_active_uid", lambda: None)
        monkeypatch.setattr(w._monitor, "selected_jpg_paths", lambda: selected)
        monkeypatch.setattr(w._monitor, "selected_jpg_owner_uids", lambda: [])
        monkeypatch.setattr(
            w,
            "_prompt_selected_compose_target",
            lambda *_a, **_k: _SelectedComposeTarget(
                uid=ADHOC_GROUPING_UID,
                output_name="手填输出名",
            ),
        )
        calls = []
        monkeypatch.setattr(w, "_on_compose_requested", lambda uid, idx: calls.append((uid, idx)))

        w._on_compose_implicit()

        assert calls == [(ADHOC_GROUPING_UID, 0)]
        grouping = load_grouping(db, ADHOC_GROUPING_UID)
        assert grouping.groups[0].jpg_paths == selected
        assert grouping.groups[0].output_name == "手填输出名"

    def test_selected_unowned_jpgs_can_assign_to_uid_for_auto_sequence(self, tmp_path, monkeypatch):
        from app.views.workbench_view import _SelectedComposeTarget
        from app.services.grouping_service import load_grouping

        selected = [str(tmp_path / "a.jpg"), str(tmp_path / "b.jpg")]
        target_uid = "GXFCG-BLW-SC002-R-20260618"
        w, ctx, db = self._make_view(tmp_path, [])
        monkeypatch.setattr(w, "_get_active_uid", lambda: None)
        monkeypatch.setattr(w._monitor, "selected_jpg_paths", lambda: selected)
        monkeypatch.setattr(w._monitor, "selected_jpg_owner_uids", lambda: [])
        monkeypatch.setattr(
            w,
            "_prompt_selected_compose_target",
            lambda *_a, **_k: _SelectedComposeTarget(
                uid=target_uid,
                assign_to_uid=True,
            ),
        )
        assigned = []
        monkeypatch.setattr(w, "_assign_selected_jpgs_to_uid", lambda uid, paths: assigned.append((uid, paths)))
        calls = []
        monkeypatch.setattr(w, "_on_compose_requested", lambda uid, idx: calls.append((uid, idx)))

        w._on_compose_implicit()

        assert calls == [(target_uid, 0)]
        assert assigned == [(target_uid, selected)]
        grouping = load_grouping(db, target_uid)
        assert grouping.groups[0].jpg_paths == selected
        assert grouping.groups[0].output_name is None

    def test_prompt_assigns_to_prefilled_uid_after_user_confirms(self, tmp_path, monkeypatch):
        from PyQt6.QtWidgets import QDialog

        w, ctx, db = self._make_view(tmp_path, [])
        monkeypatch.setattr(
            QDialog,
            "exec",
            lambda self: QDialog.DialogCode.Accepted,
        )

        target = w._prompt_selected_compose_target(
            2,
            organise=True,
            default_uid=self.UID,
        )

        assert target is not None
        assert target.uid == self.UID
        assert target.assign_to_uid is True

    def test_selected_jpgs_take_priority_for_implicit_group(self, tmp_path):
        from app.services.grouping_service import load_grouping
        attributed = [str(tmp_path / f"a{i}.jpg") for i in range(4)]
        selected = attributed[1:3]
        w, ctx, db = self._make_view(tmp_path, attributed)
        idx = w._build_implicit_group(self.UID, selected)
        assert idx == 0
        g = load_grouping(db, self.UID)
        assert g.groups[0].jpg_paths == selected

    def test_silent_compose_implicit_uses_headless_path(self, tmp_path, monkeypatch):
        selected = [str(tmp_path / "a.jpg"), str(tmp_path / "b.jpg")]
        w, ctx, db = self._make_view(tmp_path, [])
        ctx.settings.silent_compose = True
        monkeypatch.setattr(w, "_get_active_uid", lambda: self.UID)
        monkeypatch.setattr(w._monitor, "selected_jpg_paths", lambda: selected)
        monkeypatch.setattr(w, "_assign_selected_jpgs_to_uid", lambda uid, paths: None)
        calls = []
        monkeypatch.setattr(
            w,
            "_compose_group_headless",
            lambda uid, idx, done, **kw: (
                calls.append((uid, idx, kw.get("background"))),
                done(True),
            ),
        )
        w._on_compose_implicit()
        assert calls == [(self.UID, 0, False)]

    def test_compose_implicit_organise_runs_in_background_even_with_preview_on(
        self, tmp_path, monkeypatch
    ):
        selected = [str(tmp_path / "a.jpg"), str(tmp_path / "b.jpg")]
        w, ctx, db = self._make_view(tmp_path, [])
        ctx.settings.silent_compose = False
        monkeypatch.setattr(w, "_get_active_uid", lambda: self.UID)
        monkeypatch.setattr(w._monitor, "selected_jpg_paths", lambda: selected)
        monkeypatch.setattr(w, "_assign_selected_jpgs_to_uid", lambda uid, paths: None)
        calls = []

        def _fake_headless(uid, idx, done, **kw):
            calls.append(("compose-background", idx, kw.get("background")))
            done(True)

        monkeypatch.setattr(w, "_compose_group_headless", _fake_headless)
        monkeypatch.setattr(
            w,
            "_on_compose_requested",
            lambda *a, **k: pytest.fail("合成+整理应直接后台执行，不应等待预览框"),
        )
        monkeypatch.setattr(
            w,
            "_on_organise_requested",
            lambda uid, idx, **kw: calls.append(
                ("organise", idx, kw.get("silent_batch"), callable(kw.get("on_complete")))
            ) or True,
        )

        w._on_compose_implicit(organise=True)

        assert calls == [
            ("compose-background", 0, True),
            ("organise", 0, True, True),
        ]

    def test_interactive_compose_notifies_callback_after_preview_save(
        self, tmp_path, monkeypatch
    ):
        from app.services.grouping_service import ADHOC_GROUPING_UID, Group, save_grouping
        import app.services.helicon_service as helicon_service
        import app.views.workbench_view as workbench_view

        jpgs = []
        for name in ("a.jpg", "b.jpg"):
            path = tmp_path / name
            path.write_bytes(b"\xff\xd8\xff")
            jpgs.append(str(path))
        w, ctx, db = self._make_view(tmp_path, [])
        save_grouping(
            db,
            ADHOC_GROUPING_UID,
            [Group(group_index=0, jpg_paths=jpgs, output_name="preview-output")],
            clean_phantoms=False,
        )
        monkeypatch.setattr(w, "_show_compose_preview", lambda paths: list(paths))
        monkeypatch.setattr(helicon_service, "detect_helicon", lambda: "/fake/Helicon.exe")

        def _fake_stack(jpg_paths, output_path, params, on_finished, on_failed, **kwargs):
            Path(output_path).parent.mkdir(parents=True, exist_ok=True)
            Path(output_path).write_bytes(b"II*\x00fake")
            on_finished(output_path)

        class FakeComposeDialog:
            ACTION_SAVE = "save"
            ACTION_CANCEL = "cancel"
            ACTION_RECOMPOSE = "recompose"

            def __init__(self, jpg_paths, tiff_path, params, **kwargs):
                self._params = dict(params)

            def exec(self):
                return 0

            def action(self):
                return self.ACTION_SAVE

            def params(self):
                return self._params

        monkeypatch.setattr(w, "_run_helicon_stack", _fake_stack)
        monkeypatch.setattr(workbench_view, "_ComposeWorkbenchDialog", FakeComposeDialog)
        done = []

        w._on_compose_requested(
            ADHOC_GROUPING_UID,
            0,
            on_composed=lambda ok: done.append(ok),
        )

        assert done == [True]

    def test_toolbar_preview_toggle_controls_silent_compose(self, tmp_path):
        w, ctx, db = self._make_view(tmp_path, [])

        w._on_compose_preview_toggled(False)
        assert ctx.settings.silent_compose is True

        w._on_compose_preview_toggled(True)
        assert ctx.settings.silent_compose is False

    def test_toolbar_preview_state_syncs_from_silent_setting(self, tmp_path):
        w, ctx, db = self._make_view(tmp_path, [])

        ctx.settings.silent_compose = True
        w._sync_compose_preview_toggle()
        assert w._monitor.compose_preview_enabled() is False

        ctx.settings.silent_compose = False
        w._sync_compose_preview_toggle()
        assert w._monitor.compose_preview_enabled() is True

    def test_compose_implicit_organise_runs_after_headless_success(self, tmp_path, monkeypatch):
        selected = [str(tmp_path / "a.jpg"), str(tmp_path / "b.jpg")]
        w, ctx, db = self._make_view(tmp_path, [])
        ctx.settings.silent_compose = True
        monkeypatch.setattr(w, "_get_active_uid", lambda: self.UID)
        monkeypatch.setattr(w._monitor, "selected_jpg_paths", lambda: selected)
        monkeypatch.setattr(w, "_assign_selected_jpgs_to_uid", lambda uid, paths: None)
        calls = []
        monkeypatch.setattr(
            w,
            "_compose_group_headless",
            lambda uid, idx, done, **kw: (
                calls.append(
                    (
                        "compose",
                        idx,
                        kw.get("background"),
                        kw.get("show_progress_dialog"),
                    )
                ),
                done(True),
            ),
        )
        monkeypatch.setattr(
            w,
            "_on_organise_requested",
            lambda uid, idx, **kw: calls.append(
                ("organise", idx, kw.get("silent_batch"), callable(kw.get("on_complete")))
            ) or True,
        )
        w._on_compose_implicit(organise=True)
        assert calls == [("compose", 0, True, False), ("organise", 0, True, True)]

    def test_compose_implicit_organise_reports_done_after_archive_callback(
        self, tmp_path, monkeypatch
    ):
        selected = [str(tmp_path / "a.jpg"), str(tmp_path / "b.jpg")]
        w, ctx, db = self._make_view(tmp_path, [])
        ctx.settings.silent_compose = True
        monkeypatch.setattr(w, "_get_active_uid", lambda: self.UID)
        monkeypatch.setattr(w._monitor, "selected_jpg_paths", lambda: selected)
        monkeypatch.setattr(w, "_assign_selected_jpgs_to_uid", lambda uid, paths: None)
        monkeypatch.setattr(
            w,
            "_compose_group_headless",
            lambda uid, idx, done, **kw: done(True),
        )
        archive_done = {}
        monkeypatch.setattr(
            w,
            "_on_organise_requested",
            lambda uid, idx, **kw: archive_done.setdefault(
                "callback",
                kw.get("on_complete"),
            ) or True,
        )
        messages = []
        monkeypatch.setattr(w, "_status_message", lambda msg, *a, **k: messages.append(msg))

        w._on_compose_implicit(organise=True)

        assert "合成+整理完成" not in messages
        archive_done["callback"](True)
        assert messages[-1] == "合成+整理完成"

    def test_compose_implicit_organise_archives_and_consumes_selected_jpgs(
        self, qtbot, tmp_path, monkeypatch
    ):
        from app.services import archive_service
        from app.services.grouping_service import load_grouping

        w, ctx, db = self._make_view(tmp_path, [])
        project_dir = Path(ctx.current_project_dir)
        incoming = project_dir / "incoming-jpg"
        results = project_dir / "results"
        incoming.mkdir(parents=True, exist_ok=True)
        results.mkdir(parents=True, exist_ok=True)
        selected = []
        for name in ("P6202147-4.JPG", "P6202147.JPG"):
            path = incoming / name
            path.write_bytes(b"\xff\xd8\xff\xe0jpg")
            selected.append(str(path))

        ctx.settings.delete_jpg_after_archive = True
        ctx.settings.jxl_effort_method = "standard"
        ctx.settings.jxl_concurrency = 1
        monkeypatch.setattr(w, "_get_active_uid", lambda: self.UID)
        monkeypatch.setattr(w._monitor, "selected_jpg_paths", lambda: list(selected))
        monkeypatch.setattr(w._monitor, "selected_jpg_owner_uids", lambda: [])
        monkeypatch.setattr(w, "_assign_selected_jpgs_to_uid", lambda uid, paths: None)
        monkeypatch.setattr(w, "_refresh_monitor", lambda: None)
        monkeypatch.setattr(w, "_on_helicon_finished", lambda *a, **k: None)
        monkeypatch.setattr(w, "_on_organize_finished", lambda *a, **k: None)
        monkeypatch.setattr(archive_service, "has_cjxl", lambda: False)
        monkeypatch.setattr(archive_service, "has_djxl", lambda: False)
        messages = []
        monkeypatch.setattr(w, "_status_message", lambda msg, *a, **k: messages.append(msg))

        def _fake_stack(
            jpg_paths,
            output_path,
            params,
            on_finished,
            on_failed,
            **kwargs,
        ):
            assert list(jpg_paths) == selected
            assert kwargs.get("show_progress_dialog") is False
            Path(output_path).parent.mkdir(parents=True, exist_ok=True)
            Path(output_path).write_bytes(b"II*\x00fake-tiff")
            on_finished(output_path)

        monkeypatch.setattr(w, "_run_helicon_stack", _fake_stack)

        w._on_compose_implicit(organise=True)

        qtbot.waitUntil(
            lambda: bool(load_grouping(db, self.UID).groups)
            and load_grouping(db, self.UID).groups[0].status == "organized",
            timeout=5000,
        )

        saved = load_grouping(db, self.UID).groups[0]
        assert saved.archive_zip
        assert Path(saved.archive_zip).is_file()
        assert Path(saved.composed_tiff_path).parent == results
        assert all(not Path(path).exists() for path in selected)
        assert any("正在整理" in msg for msg in messages)
        assert messages[-1] == "合成+整理完成"
        assert w._workflow_notice_text()[0] == "完成"
        assert w._workflow_notice_text()[1] == "合成+整理完成"
        assert "JPG 已写入 ZIP" in w._workflow_notice_text()[2]
        assert getattr(w, "_workflow_notice_panel", None) is None
        assert not w._monitor._workflow_notice.isVisible()

    def test_selected_organise_without_active_keeps_tiff_name(self, tmp_path, monkeypatch):
        from app.services.grouping_service import ADHOC_GROUPING_UID, load_grouping
        w, ctx, db = self._make_view(tmp_path, [])
        jpg = tmp_path / "a.jpg"; jpg.write_bytes(b"\xff\xd8\xff")
        tiff = tmp_path / "HeliconFocus.tif"; tiff.write_bytes(b"II*\x00")
        monkeypatch.setattr(w, "_get_active_uid", lambda: None)
        monkeypatch.setattr(w._monitor, "selected_jpg_paths", lambda: [str(jpg)])
        monkeypatch.setattr(w._monitor, "selected_tiff_paths", lambda: [str(tiff)])
        calls = []
        monkeypatch.setattr(
            w,
            "_on_organise_requested",
            lambda uid, idx, **kw: calls.append((uid, idx, kw)),
        )

        w._on_organise_selected()

        grouping = load_grouping(db, ADHOC_GROUPING_UID)
        assert grouping.groups[0].composed_tiff_path == str(tiff)
        assert calls[0][0] == ADHOC_GROUPING_UID
        assert calls[0][2]["allow_single_jpg"] is True

    def test_selected_organise_with_active_renames_tiff_to_uid(self, tmp_path, monkeypatch):
        from app.services.grouping_service import load_grouping
        w, ctx, db = self._make_view(tmp_path, [])
        project_dir = Path(ctx.current_project_dir)
        (project_dir / "results").mkdir(exist_ok=True)
        (project_dir / "incoming-jpg").mkdir(exist_ok=True)
        jpg = tmp_path / "a.jpg"; jpg.write_bytes(b"\xff\xd8\xff")
        tiff = tmp_path / "HeliconFocus.tif"; tiff.write_bytes(b"II*\x00")
        monkeypatch.setattr(w, "_get_active_uid", lambda: self.UID)
        monkeypatch.setattr(w._monitor, "selected_jpg_paths", lambda: [str(jpg)])
        monkeypatch.setattr(w._monitor, "selected_tiff_paths", lambda: [str(tiff)])
        calls = []
        monkeypatch.setattr(
            w,
            "_on_organise_requested",
            lambda uid, idx, **kw: calls.append((uid, idx, kw)),
        )

        w._on_organise_selected()

        expected = tmp_path / "FJ-XM-B2-DLC001-1-T95E-20260601.tif"
        assert expected.is_file()
        assert not tiff.exists()
        grouping = load_grouping(db, self.UID)
        assert grouping.groups[0].composed_tiff_path == str(expected)
        assert calls[0][0] == self.UID
        assert calls[0][2]["allow_single_jpg"] is True

    def test_selected_organise_warns_when_tiff_name_mismatches_active_uid(
        self, tmp_path, monkeypatch
    ):
        w, ctx, db = self._make_view(tmp_path, [])
        jpg = tmp_path / "a.jpg"; jpg.write_bytes(b"\xff\xd8\xff")
        tiff = tmp_path / "GZQ-SNW-XTC003-1-R-20260619-3.tif"; tiff.write_bytes(b"II*\x00")
        monkeypatch.setattr(w, "_get_active_uid", lambda: self.UID)
        monkeypatch.setattr(w._monitor, "selected_jpg_paths", lambda: [str(jpg)])
        monkeypatch.setattr(w._monitor, "selected_tiff_paths", lambda: [str(tiff)])
        calls = []
        monkeypatch.setattr(
            w,
            "_on_organise_requested",
            lambda uid, idx, **kw: calls.append((uid, idx, kw)),
        )
        questions = []
        monkeypatch.setattr(
            QMessageBox,
            "question",
            lambda *args, **kwargs: questions.append(args) or QMessageBox.StandardButton.No,
        )

        w._on_organise_selected()

        assert questions
        assert calls == []
        assert tiff.is_file()

    def test_selected_organise_does_not_warn_for_generic_external_tiff(
        self, tmp_path, monkeypatch
    ):
        w, ctx, db = self._make_view(tmp_path, [])
        jpg = tmp_path / "a.jpg"; jpg.write_bytes(b"\xff\xd8\xff")
        tiff = tmp_path / "HeliconFocus.tif"; tiff.write_bytes(b"II*\x00")
        monkeypatch.setattr(w, "_get_active_uid", lambda: self.UID)
        monkeypatch.setattr(w._monitor, "selected_jpg_paths", lambda: [str(jpg)])
        monkeypatch.setattr(w._monitor, "selected_tiff_paths", lambda: [str(tiff)])
        calls = []
        monkeypatch.setattr(
            w,
            "_on_organise_requested",
            lambda uid, idx, **kw: calls.append((uid, idx, kw)),
        )
        monkeypatch.setattr(
            QMessageBox,
            "question",
            lambda *args, **kwargs: pytest.fail("generic TIFF name should not warn"),
        )

        w._on_organise_selected()

        assert calls and calls[0][0] == self.UID

    def test_auto_compress_toggle_seeds_existing_tiffs(self, tmp_path):
        from app.services.monitor_service import FileEntry, ScanResult
        w, ctx, db = self._make_view(tmp_path, [])
        tiff = tmp_path / "old.tif"
        tiff.write_bytes(b"II*\x00")
        w._last_scan_result = ScanResult(
            project_dir=str(tmp_path),
            tiff_files=[FileEntry(
                name="old.tif", path=str(tiff), kind="tiff", size=4,
                mtime="2026-06-13T00:00:00+00:00", has_zip=False,
            )],
        )

        w._on_auto_compress_toggled(True)

        assert str(tiff.resolve()) in w._auto_known_tiffs
        assert ctx.settings.auto_organize_after_compose is True

    def test_auto_archive_toolbar_state_is_seeded_from_settings(self, tmp_path):
        w, ctx, db = self._make_view(tmp_path, [])
        ctx.settings.auto_organize_after_compose = True

        from app.views.workbench_view import WorkbenchView

        seeded = WorkbenchView(ctx)

        assert seeded._monitor.auto_compress_enabled() is True

    def test_auto_archive_toolbar_state_resyncs_from_settings(self, tmp_path):
        w, ctx, db = self._make_view(tmp_path, [])
        assert w._monitor.auto_compress_enabled() is False

        ctx.settings.auto_organize_after_compose = True
        w._sync_auto_archive_toggle()

        assert w._monitor.auto_compress_enabled() is True

    def test_auto_compress_new_tiff_uses_active_uid_jpgs(self, tmp_path, monkeypatch):
        from app.services.monitor_service import FileEntry, ScanResult
        jpgs = [str(tmp_path / "a.jpg"), str(tmp_path / "b.jpg")]
        w, ctx, db = self._make_view(tmp_path, jpgs)
        new_tiff = tmp_path / "new.tif"
        new_tiff.write_bytes(b"II*\x00")
        monkeypatch.setattr(w._monitor, "auto_compress_enabled", lambda: True)
        monkeypatch.setattr(w, "_get_active_uid", lambda: self.UID)
        calls = []
        monkeypatch.setattr(
            w,
            "_organise_jpgs_with_tiff",
            lambda paths, tiff, **kw: calls.append((paths, tiff, kw)) or True,
        )
        result = ScanResult(
            project_dir=str(tmp_path),
            tiff_files=[FileEntry(
                name="new.tif", path=str(new_tiff), kind="tiff", size=4,
                mtime="2026-06-13T00:00:00+00:00", has_zip=False,
            )],
        )

        w._maybe_auto_process_new_tiff(result)

        assert len(calls) == 1
        assert calls[0][0] == jpgs
        assert calls[0][1] == str(new_tiff.resolve())
        assert calls[0][2]["silent"] is True
        assert callable(calls[0][2]["on_complete"])
        assert str(new_tiff.resolve()) not in w._auto_known_tiffs

    def test_auto_compress_new_tiff_uses_settings_auto_archive(self, tmp_path, monkeypatch):
        from app.services.monitor_service import FileEntry, ScanResult

        jpgs = [str(tmp_path / "a.jpg"), str(tmp_path / "b.jpg")]
        w, ctx, db = self._make_view(tmp_path, jpgs)
        ctx.settings.auto_organize_after_compose = True
        new_tiff = tmp_path / "settings-auto.tif"
        new_tiff.write_bytes(b"II*\x00")
        monkeypatch.setattr(w._monitor, "auto_compress_enabled", lambda: False)
        monkeypatch.setattr(w, "_get_active_uid", lambda: self.UID)
        calls = []
        monkeypatch.setattr(
            w,
            "_organise_jpgs_with_tiff",
            lambda paths, tiff, **kw: calls.append((paths, tiff, kw)) or True,
        )

        w._maybe_auto_process_new_tiff(ScanResult(
            project_dir=str(tmp_path),
            tiff_files=[FileEntry(
                name="settings-auto.tif", path=str(new_tiff), kind="tiff", size=4,
                mtime="2026-06-13T00:00:00+00:00", has_zip=False,
            )],
        ))

        assert len(calls) == 1
        assert calls[0][0] == jpgs

    def test_auto_compress_without_active_uses_selected_jpgs_for_external_tif(
        self, tmp_path, monkeypatch
    ):
        from app.services.monitor_service import FileEntry, ScanResult

        selected = [str(tmp_path / "selected-a.jpg"), str(tmp_path / "selected-b.jpg")]
        w, ctx, db = self._make_view(tmp_path, [])
        new_tiff = tmp_path / "external.tif"
        new_tiff.write_bytes(b"II*\x00")
        monkeypatch.setattr(w._monitor, "auto_compress_enabled", lambda: True)
        monkeypatch.setattr(w, "_get_active_uid", lambda: None)
        monkeypatch.setattr(w._monitor, "selected_jpg_paths", lambda: selected)
        calls = []
        monkeypatch.setattr(
            w,
            "_organise_jpgs_with_tiff",
            lambda paths, tiff, **kw: calls.append((paths, tiff, kw)) or True,
        )

        w._maybe_auto_process_new_tiff(ScanResult(
            project_dir=str(tmp_path),
            tiff_files=[FileEntry(
                name="external.tif", path=str(new_tiff), kind="tiff", size=4,
                mtime="2026-06-13T00:00:00+00:00", has_zip=False,
            )],
        ))

        assert len(calls) == 1
        assert calls[0][0] == selected
        assert calls[0][1] == str(new_tiff.resolve())
        assert calls[0][2]["silent"] is True
        assert callable(calls[0][2]["on_complete"])

    def test_auto_compress_marks_tiff_seen_after_archive_callback(self, tmp_path, monkeypatch):
        from app.services.monitor_service import FileEntry, ScanResult
        jpgs = [str(tmp_path / "a.jpg"), str(tmp_path / "b.jpg")]
        w, ctx, db = self._make_view(tmp_path, jpgs)
        new_tiff = tmp_path / "new.tif"
        new_tiff.write_bytes(b"II*\x00")
        monkeypatch.setattr(w._monitor, "auto_compress_enabled", lambda: True)
        monkeypatch.setattr(w, "_get_active_uid", lambda: self.UID)
        done = {}
        monkeypatch.setattr(
            w,
            "_organise_jpgs_with_tiff",
            lambda paths, tiff, **kw: done.setdefault("callback", kw["on_complete"]) or True,
        )
        result = ScanResult(
            project_dir=str(tmp_path),
            tiff_files=[FileEntry(
                name="new.tif", path=str(new_tiff), kind="tiff", size=4,
                mtime="2026-06-13T00:00:00+00:00", has_zip=False,
            )],
        )

        w._maybe_auto_process_new_tiff(result)

        assert str(new_tiff.resolve()) not in w._auto_known_tiffs
        assert w._auto_tiff_busy is True
        done["callback"](True)
        assert str(new_tiff.resolve()) in w._auto_known_tiffs
        assert w._auto_tiff_busy is False

    def test_auto_compress_without_active_does_not_mark_tiff_seen(self, tmp_path, monkeypatch):
        from app.services.monitor_service import FileEntry, ScanResult
        w, ctx, db = self._make_view(tmp_path, [])
        new_tiff = tmp_path / "new.tif"
        new_tiff.write_bytes(b"II*\x00")
        monkeypatch.setattr(w._monitor, "auto_compress_enabled", lambda: True)
        monkeypatch.setattr(w, "_get_active_uid", lambda: None)
        result = ScanResult(
            project_dir=str(tmp_path),
            tiff_files=[FileEntry(
                name="new.tif", path=str(new_tiff), kind="tiff", size=4,
                mtime="2026-06-13T00:00:00+00:00", has_zip=False,
            )],
        )

        w._maybe_auto_process_new_tiff(result)

        assert str(new_tiff.resolve()) not in w._auto_known_tiffs

    def test_auto_compress_multiple_new_tiffs_processes_one_per_refresh(self, tmp_path, monkeypatch):
        from app.services.monitor_service import FileEntry, ScanResult
        jpgs = [str(tmp_path / "a.jpg"), str(tmp_path / "b.jpg")]
        w, ctx, db = self._make_view(tmp_path, jpgs)
        tiffs = [tmp_path / "a.tif", tmp_path / "b.tif"]
        for tiff in tiffs:
            tiff.write_bytes(b"II*\x00")
        monkeypatch.setattr(w._monitor, "auto_compress_enabled", lambda: True)
        monkeypatch.setattr(w, "_get_active_uid", lambda: self.UID)
        calls = []
        monkeypatch.setattr(
            w,
            "_organise_jpgs_with_tiff",
            lambda paths, tiff, **kw: calls.append(tiff) or True,
        )
        result = ScanResult(
            project_dir=str(tmp_path),
            tiff_files=[
                FileEntry(
                    name=tiff.name, path=str(tiff), kind="tiff", size=4,
                    mtime="2026-06-13T00:00:00+00:00", has_zip=False,
                )
                for tiff in tiffs
            ],
        )

        w._maybe_auto_process_new_tiff(result)

        assert calls == [str(tiffs[0].resolve())]
        assert str(tiffs[0].resolve()) not in w._auto_known_tiffs
        assert str(tiffs[1].resolve()) not in w._auto_known_tiffs

    def test_auto_compress_uses_only_unoccupied_attributed_jpgs(self, tmp_path, monkeypatch):
        from app.services.grouping_service import Group, save_grouping
        from app.services.monitor_service import FileEntry, ScanResult
        occupied = [str(tmp_path / "old1.jpg"), str(tmp_path / "old2.jpg")]
        fresh = [str(tmp_path / "new1.jpg"), str(tmp_path / "new2.jpg")]
        w, ctx, db = self._make_view(tmp_path, occupied + fresh)
        save_grouping(
            db,
            self.UID,
            [Group(group_index=0, jpg_paths=occupied, status="organized")],
            clean_phantoms=False,
        )
        tiff = tmp_path / "new.tif"
        tiff.write_bytes(b"II*\x00")
        monkeypatch.setattr(w._monitor, "auto_compress_enabled", lambda: True)
        monkeypatch.setattr(w, "_get_active_uid", lambda: self.UID)
        calls = []
        monkeypatch.setattr(
            w,
            "_organise_jpgs_with_tiff",
            lambda paths, tiff_path, **kw: calls.append(paths) or True,
        )

        w._maybe_auto_process_new_tiff(ScanResult(
            project_dir=str(tmp_path),
            tiff_files=[FileEntry(
                name="new.tif", path=str(tiff), kind="tiff", size=4,
                mtime="2026-06-13T00:00:00+00:00", has_zip=False,
            )],
        ))

        assert calls == [fresh]

    def test_auto_compress_failed_organise_does_not_mark_tiff_seen(self, tmp_path, monkeypatch):
        from app.services.monitor_service import FileEntry, ScanResult
        jpgs = [str(tmp_path / "a.jpg"), str(tmp_path / "b.jpg")]
        w, ctx, db = self._make_view(tmp_path, jpgs)
        tiff = tmp_path / "new.tif"
        tiff.write_bytes(b"II*\x00")
        monkeypatch.setattr(w._monitor, "auto_compress_enabled", lambda: True)
        monkeypatch.setattr(w, "_get_active_uid", lambda: self.UID)
        monkeypatch.setattr(w, "_organise_jpgs_with_tiff", lambda *a, **k: False)

        w._maybe_auto_process_new_tiff(ScanResult(
            project_dir=str(tmp_path),
            tiff_files=[FileEntry(
                name="new.tif", path=str(tiff), kind="tiff", size=4,
                mtime="2026-06-13T00:00:00+00:00", has_zip=False,
            )],
        ))

        assert str(tiff.resolve()) not in w._auto_known_tiffs


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


class TestOrganiseUsesPanelMemory:
    """整理应读分组面板内存，不能仅查 DB（否则界面「组1」→ 内部 0 会报找不到）。"""

    UID = "FJ-XM-B2-DLC001-T95E-20260601"

    def test_organise_finds_unsaved_panel_group(self, qtbot, tmp_path, monkeypatch):
        from unittest.mock import patch
        from app.services.grouping_service import Group, SpecimenGrouping
        from app.views.workbench_view import WorkbenchView

        project_dir = str(tmp_path / "proj")
        Path(project_dir, "_data").mkdir(parents=True)
        Path(project_dir, "incoming-jpg").mkdir()
        Path(project_dir, "results").mkdir()
        db = _make_db(str(tmp_path / "proj" / "_data" / "project.db"))
        ctx = _make_ctx(project_dir=project_dir, db=db)

        j1 = tmp_path / "a.jpg"
        j2 = tmp_path / "b.jpg"
        j1.write_bytes(b"j")
        j2.write_bytes(b"j")
        tiff = Path(project_dir) / "incoming-jpg" / "FJ-XM-B2-DLC001-1-T95E-20260601.tif"
        tiff.write_bytes(b"t")

        grouping = SpecimenGrouping(
            uid=self.UID,
            groups=[
                Group(
                    group_index=0,
                    jpg_paths=[str(j1), str(j2)],
                    composed_tiff_path=str(tiff),
                    status="composed",
                )
            ],
        )

        w = WorkbenchView(ctx)
        qtbot.addWidget(w)
        w.on_activate()
        w._grouping.load_grouping(self.UID, grouping)

        monkeypatch.setattr(w, "_maybe_rename_tiff_before_organize", lambda *a, **k: None)
        with patch(
            "app.services.organize_service._check_organize_gate",
            return_value=None,
        ), patch(
            "app.workers.supp_compression_worker.SuppCompressionWorker.start",
            return_value=None,
        ):
            ok = w._on_organise_requested(self.UID, 0)

        assert ok is True
        db.close()


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

    def test_undo_organised_group_restores_jpgs_and_keeps_tiff(
        self, tmp_path, monkeypatch
    ):
        from PyQt6.QtWidgets import QMessageBox
        from app.services.archive_service import archive_group
        from app.services.grouping_service import Group, load_grouping, save_grouping

        w, ctx, db = self._make_view(tmp_path)
        project_dir = Path(ctx.current_project_dir)
        incoming = project_dir / "incoming-jpg"
        incoming.mkdir(parents=True, exist_ok=True)
        tiff = project_dir / "results" / "T.tif"
        tiff.write_bytes(b"II*\x00")
        j1 = incoming / "a.jpg"
        j2 = incoming / "b.jpg"
        j1.write_bytes(b"\xff\xd8\xff-a")
        j2.write_bytes(b"\xff\xd8\xff-b")

        archived = archive_group(
            [str(j1), str(j2)],
            str(tiff),
            str(project_dir),
            delete_jpg=True,
            output_dir=str(project_dir / "results"),
        )
        assert not j1.exists()
        assert not j2.exists()
        save_grouping(
            db,
            "U1",
            [
                Group(
                    group_index=0,
                    jpg_paths=[str(j1), str(j2)],
                    composed_tiff_path=str(tiff),
                    archive_zip=archived.zip_path,
                    status="organized",
                )
            ],
            clean_phantoms=False,
        )
        monkeypatch.setattr(
            QMessageBox,
            "question",
            lambda *a, **k: QMessageBox.StandardButton.Yes,
        )
        monkeypatch.setattr(w, "_refresh_monitor", lambda: None)
        messages = []
        monkeypatch.setattr(w, "_status_message", lambda msg, *a, **k: messages.append(msg))

        w._on_undo_compose("U1", 0)

        saved = load_grouping(db, "U1").groups[0]
        assert tiff.exists()
        assert j1.read_bytes() == b"\xff\xd8\xff-a"
        assert j2.read_bytes() == b"\xff\xd8\xff-b"
        assert saved.status == "composed"
        assert saved.archive_zip in (None, "")
        assert not Path(archived.zip_path).exists()
        assert not (project_dir / "_retired-zip").exists()
        assert messages and "撤销整理完成" in messages[-1]


# ── 场景6/7：自动归档（默认关；开时可取激活编号未占用 JPG） ────────────────────


class TestAutoOrganizeAfterCompose:
    """自动归档打开时，合成成功后自动把源 JPG 打包 ZIP+命名+移 results。"""

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
        monkeypatch.setattr(w._monitor, "auto_compress_enabled", lambda: False)
        called = []
        monkeypatch.setattr(w, "_on_organise_requested",
                            lambda u, g: called.append((u, g)))
        w._maybe_auto_organize("U1", 0)
        assert called == []

    def test_auto_organize_runs_when_toolbar_auto_archive_on(self, tmp_path, monkeypatch):
        w, ctx, db = self._make_view(tmp_path)
        ctx.settings.auto_organize_after_compose = False
        monkeypatch.setattr(w._monitor, "auto_compress_enabled", lambda: True)
        called = []
        monkeypatch.setattr(w, "_on_organise_requested",
                            lambda u, g: called.append((u, g)))
        w._maybe_auto_organize("U1", 0)
        assert called == [("U1", 0)]


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


class TestBatchComposeOrganise:
    """批量[合成]/[合成+整理] 顺序队列 — 修复异步链路断裂。

    旧 bug:`_on_compose_and_organise_all` 异步发起合成后立刻读 composed 列表 →
    刚合成的组拿不到 composed_tiff_path → 整理空跑。新设计:workbench 顺序队列,
    每组合成完成(异步回调)→ 同步整理该组 → 下一组。批量不弹确认框。
    """

    def _build(self, tmp_path):
        from app.views.workbench_view import WorkbenchView
        from app.services.grouping_service import Group, save_grouping
        project_dir = str(tmp_path)
        db = _make_db(str(tmp_path / "project.db"))
        uid = "FJ-XM-B2-DLC001-T95E-20260601"
        db.execute(
            """INSERT INTO specimens
               (uid, id, province, site, station, storage,
                collection_date, photo_date, owner_project_dir)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (uid, "DLC001", "FJ", "XM", "B2", "T95E",
             "20260601", "20260601", project_dir),
        )
        # 真实 JPG 文件 — save_grouping 默认 clean_phantoms 会剔除不存在的路径。
        jdir = tmp_path / "incoming-jpg"
        jdir.mkdir(parents=True, exist_ok=True)
        jpgs = []
        for i in range(1, 5):
            p = jdir / f"{i}.jpg"
            p.write_bytes(b"\xff\xd8\xff\xe0jpg")
            jpgs.append(str(p))
        # 两个未合成(draft)组,各 2 JPG
        save_grouping(db, uid, [
            Group(group_index=0, angle_label="正面", jpg_paths=jpgs[0:2]),
            Group(group_index=1, angle_label="背面", jpg_paths=jpgs[2:4]),
        ])
        db.commit()
        ctx = _make_ctx(project_dir=project_dir, db=db)
        self._jpgs = jpgs
        return WorkbenchView(ctx), ctx, uid, db

    def _patch_helicon_present(self, monkeypatch):
        import app.services.helicon_service as hs
        monkeypatch.setattr(hs, "detect_helicon", lambda: "/fake/HeliconFocus.exe")

    def test_compose_and_organise_chains_each_composed_group(self, tmp_path, monkeypatch):
        """核心回归:两组都被合成 + 整理,顺序交替。证明整理读到的是合成后状态。"""
        w, ctx, uid, db = self._build(tmp_path)
        self._patch_helicon_present(monkeypatch)
        calls = []

        def _fake_headless(u, idx, on_done, **kw):
            calls.append(("compose", idx, kw.get("background")))
            on_done(True)

        def _fake_organise(u, idx, **kw):
            calls.append(("organise", idx))

        monkeypatch.setattr(w, "_compose_group_headless", _fake_headless)
        monkeypatch.setattr(w, "_on_organise_requested", _fake_organise)

        w._start_compose_batch(uid, organise=True)

        assert calls == [
            ("compose", 0, True), ("organise", 0),
            ("compose", 1, True), ("organise", 1),
        ]
        assert w._batch is None  # 队列耗尽 → 状态清空
        db.close()

    def test_compose_only_never_organises(self, tmp_path, monkeypatch):
        """[⚡合成] 批量:只合成,绝不整理。"""
        w, ctx, uid, db = self._build(tmp_path)
        self._patch_helicon_present(monkeypatch)
        calls = []
        monkeypatch.setattr(
            w, "_compose_group_headless",
            lambda u, idx, on_done, **kw: (
                calls.append(("compose", idx, kw.get("background"))),
                on_done(True),
            ),
        )
        monkeypatch.setattr(
            w, "_on_organise_requested",
            lambda u, idx, **kw: calls.append(("organise", idx)),
        )

        w._start_compose_batch(uid, organise=False)

        assert ("organise", 0) not in calls and ("organise", 1) not in calls
        assert calls == [("compose", 0, True), ("compose", 1, True)]
        db.close()

    def test_compose_batch_only_runs_checked_groups(self, tmp_path, monkeypatch):
        """勾选组后，顶部[合成]只处理勾选组；不勾选时才处理全部。"""
        from app.services.grouping_service import load_grouping
        w, ctx, uid, db = self._build(tmp_path)
        self._patch_helicon_present(monkeypatch)
        w._grouping.load_grouping(uid, load_grouping(db, uid))
        w._grouping._track_group_selection_state(1, True)
        calls = []
        monkeypatch.setattr(
            w, "_compose_group_headless",
            lambda u, idx, on_done, **kw: (calls.append(idx), on_done(True)),
        )

        w._start_compose_batch(uid, organise=False)

        assert calls == [1]
        db.close()

    def test_compose_batch_reports_progress(self, tmp_path, monkeypatch):
        """批量合成期间状态栏显示当前组序号和总数。"""
        w, ctx, uid, db = self._build(tmp_path)
        self._patch_helicon_present(monkeypatch)
        messages = []
        monkeypatch.setattr(w, "_batch_status", messages.append)
        monkeypatch.setattr(
            w, "_compose_group_headless",
            lambda u, idx, on_done, **kw: on_done(True),
        )

        w._start_compose_batch(uid, organise=False)

        assert "批量合成 1/2：组 0" in messages
        assert "批量合成 2/2：组 1" in messages
        assert messages[-1] == "批量合成完成。"
        db.close()

    def test_organise_batch_only_runs_checked_composed_groups(self, tmp_path, monkeypatch):
        """勾选组后，顶部[整理]只处理勾选的已合成组。"""
        from app.services.grouping_service import Group, save_grouping, load_grouping
        w, ctx, uid, db = self._build(tmp_path)
        save_grouping(db, uid, [
            Group(group_index=0, jpg_paths=self._jpgs[0:2],
                  composed_tiff_path=str(tmp_path / "a.tif"), status="composed"),
            Group(group_index=1, jpg_paths=self._jpgs[2:4],
                  composed_tiff_path=str(tmp_path / "b.tif"), status="composed"),
        ])
        db.commit()
        w._grouping.load_grouping(uid, load_grouping(db, uid))
        w._grouping._track_group_selection_state(1, True)
        calls = []
        monkeypatch.setattr(
            w, "_on_organise_requested",
            lambda u, idx, **kw: calls.append(idx),
        )

        w._organise_all_batch(uid)

        assert calls == [1]
        db.close()

    def test_organise_batch_waits_for_archive_callback_before_next_group(
        self, tmp_path, monkeypatch
    ):
        """批量整理必须串行，避免多个归档 worker 同时写同一个分组状态。"""
        from app.services.grouping_service import Group, save_grouping

        w, ctx, uid, db = self._build(tmp_path)
        save_grouping(db, uid, [
            Group(group_index=0, jpg_paths=self._jpgs[0:2],
                  composed_tiff_path=str(tmp_path / "a.tif"), status="composed"),
            Group(group_index=1, jpg_paths=self._jpgs[2:4],
                  composed_tiff_path=str(tmp_path / "b.tif"), status="composed"),
        ])
        callbacks = []
        calls = []

        def _fake_organise(u, idx, **kw):
            calls.append((idx, kw.get("silent_batch"), callable(kw.get("on_complete"))))
            callbacks.append(kw.get("on_complete"))
            return True

        monkeypatch.setattr(w, "_on_organise_requested", _fake_organise)

        w._organise_all_batch(uid, silent_batch=True)

        assert calls == [(0, True, True)]
        assert getattr(w, "_organise_batch", None) is not None

        callbacks.pop(0)(True)
        assert calls == [(0, True, True), (1, True, True)]

        callbacks.pop(0)(True)
        assert getattr(w, "_organise_batch", None) is None
        db.close()

    def test_failed_group_not_organised_but_batch_continues(self, tmp_path, monkeypatch):
        """某组合成失败(on_done False)→ 该组不整理,但队列继续下一组。"""
        w, ctx, uid, db = self._build(tmp_path)
        self._patch_helicon_present(monkeypatch)
        calls = []

        def _fake_headless(u, idx, on_done, **kw):
            calls.append(("compose", idx, kw.get("background")))
            on_done(idx != 0)  # 组0失败,组1成功

        monkeypatch.setattr(w, "_compose_group_headless", _fake_headless)
        monkeypatch.setattr(
            w, "_on_organise_requested",
            lambda u, idx, **kw: calls.append(("organise", idx)),
        )

        w._start_compose_batch(uid, organise=True)

        assert calls == [
            ("compose", 0, True),           # 组0 合成失败 → 不整理
            ("compose", 1, True), ("organise", 1),
        ]
        assert w._batch is None
        db.close()

    def test_no_helicon_aborts_batch(self, tmp_path, monkeypatch):
        """无 Helicon → 整批中止,不合成任何组。"""
        from PyQt6.QtWidgets import QMessageBox
        w, ctx, uid, db = self._build(tmp_path)
        import app.services.helicon_service as hs
        monkeypatch.setattr(hs, "detect_helicon", lambda: None)
        monkeypatch.setattr(QMessageBox, "warning",
                            staticmethod(lambda *a, **k: None))
        called = []
        monkeypatch.setattr(
            w, "_compose_group_headless",
            lambda u, idx, on_done, **kw: called.append(idx),
        )
        w._start_compose_batch(uid, organise=True)
        assert called == []
        assert w._batch is None
        db.close()

    def test_compose_and_organise_archives_existing_tiff_without_helicon(
        self, tmp_path, monkeypatch
    ):
        """已有 TIF 的组点[合成+整理]应直接归档，不应被 Helicon 检查拦截。"""
        from app.services.grouping_service import Group, save_grouping, load_grouping
        from app.services import archive_service
        from PyQt6.QtWidgets import QMessageBox

        w, ctx, uid, db = self._build(tmp_path)
        tif = tmp_path / "existing.tif"
        tif.write_bytes(b"II*\x00")
        save_grouping(db, uid, [
            Group(
                group_index=0,
                jpg_paths=self._jpgs,
                composed_tiff_path=str(tif),
                status="composed",
            )
        ])
        w._grouping.load_grouping(uid, load_grouping(db, uid))

        import app.services.helicon_service as hs
        monkeypatch.setattr(hs, "detect_helicon", lambda: None)
        monkeypatch.setattr(
            QMessageBox, "warning", staticmethod(lambda *a, **k: None)
        )
        archived_jpgs = []

        def _fake_archive(
            jpg_paths, tiff_path, project_dir, delete_jpg,
            method="maximum", concurrency=1, progress_callback=None,
            cancel_callback=None, output_dir=None,
        ):
            archived_jpgs.extend(jpg_paths)
            assert output_dir == str(tmp_path / "results")
            zip_path = str(Path(output_dir) / Path(tiff_path).with_suffix(".zip").name)
            return _fake_zip_result(jpg_paths, zip_path, saved_percent=20)

        monkeypatch.setattr(archive_service, "archive_group", _fake_archive)

        w._start_compose_batch(uid, organise=True)

        # 整理由后台线程执行；等待完成，同时保持 Qt 事件循环运行。
        import time
        deadline = time.monotonic() + 3
        while getattr(w, "_archive_workers", set()) and time.monotonic() < deadline:
            QApplication.processEvents()
            time.sleep(0.01)

        assert archived_jpgs == self._jpgs
        saved = load_grouping(db, uid).groups[0]
        assert saved.status == "organized"
        assert saved.composed_tiff_path == str(tmp_path / "results" / tif.name)
        assert saved.archive_zip == str(tmp_path / "results" / tif.with_suffix(".zip").name)
        db.close()

    def test_organise_aborts_if_group_changes_while_archive_worker_runs(
        self, qtbot, tmp_path, monkeypatch
    ):
        """Late group edits must not be registered or deleted as archived."""
        from app.services import archive_service
        from app.services.grouping_service import Group, load_grouping, save_grouping

        w, ctx, uid, db = self._build(tmp_path)
        tif = tmp_path / "race-result.tif"
        tif.write_bytes(b"II*\x00")
        late_jpg = tmp_path / "incoming-jpg" / "late.jpg"
        late_jpg.write_bytes(b"\xff\xd8late")
        save_grouping(
            db,
            uid,
            [
                Group(
                    group_index=0,
                    jpg_paths=list(self._jpgs),
                    composed_tiff_path=str(tif),
                    status="composed",
                )
            ],
        )
        w._grouping.load_grouping(uid, load_grouping(db, uid))

        def _fake_archive(jpg_paths, tiff_path, project_dir, delete_jpg, **kwargs):
            assert delete_jpg is False
            w._grouping.grouping_state().groups[0].jpg_paths.append(str(late_jpg))
            zip_path = str(Path(kwargs["output_dir"]) / Path(tiff_path).with_suffix(".zip").name)
            return _fake_zip_result(jpg_paths, zip_path)

        monkeypatch.setattr(archive_service, "archive_group", _fake_archive)

        assert w._on_organise_requested(uid, 0, silent_batch=True) is True
        qtbot.waitUntil(
            lambda: not getattr(w, "_archive_workers", set()),
            timeout=5000,
        )

        saved = load_grouping(db, uid).groups[0]
        assert saved.status == "composed"
        assert saved.archive_zip in {None, ""}
        assert all(Path(path).is_file() for path in self._jpgs)
        assert late_jpg.is_file()
        assert w._grouping.isEnabled()
        assert "发生变化" in w._last_organise_failure_reason
        db.close()

    def test_organise_moves_external_imported_tiff_into_project_results(
        self, qtbot, tmp_path, monkeypatch
    ):
        """导入旧目录 TIF 后整理，应把 TIF+ZIP 收进当前项目 results/。

        用户场景：当前项目是 zhegnli，但从白龙尾旧照片目录导入 TIF。
        整理完成后成果不能继续指向旧目录，否则后续数据管理仍散在原始目录。
        """
        from app.services.grouping_service import Group, save_grouping, load_grouping
        from app.services import archive_service

        w, ctx, uid, db = self._build(tmp_path)
        old_dir = tmp_path / "广西防城港-20260618-白龙尾"
        old_dir.mkdir()
        old_tif = old_dir / "FJ-XM-B2-DLC001-1-T95E-20260601.tif"
        old_tif.write_bytes(b"II*\x00external")
        save_grouping(db, uid, [
            Group(
                group_index=0,
                jpg_paths=self._jpgs,
                composed_tiff_path=str(old_tif),
                status="composed",
            )
        ])
        w._grouping.load_grouping(uid, load_grouping(db, uid))

        def _fake_archive(jpg_paths, tiff_path, project_dir, delete_jpg, **kwargs):
            assert delete_jpg is False
            assert kwargs.get("output_dir") == str(tmp_path / "results")
            zip_path = str(Path(kwargs["output_dir"]) / Path(tiff_path).with_suffix(".zip").name)
            return _fake_zip_result(jpg_paths, zip_path, saved_percent=20)

        monkeypatch.setattr(archive_service, "archive_group", _fake_archive)

        assert w._on_organise_requested(uid, 0, silent_batch=True) is True

        qtbot.waitUntil(
            lambda: not getattr(w, "_archive_workers", set()),
            timeout=5000,
        )

        saved = load_grouping(db, uid).groups[0]
        project_results = tmp_path / "results"
        assert Path(saved.composed_tiff_path).parent == project_results
        assert Path(saved.archive_zip).parent == project_results
        assert Path(saved.composed_tiff_path).is_file()
        assert Path(saved.archive_zip).is_file()
        assert not old_tif.exists()
        assert not old_tif.with_suffix(".zip").exists()
        db.close()

    def test_organise_tif_only_registers_to_uid_without_zip(
        self, tmp_path, monkeypatch
    ):
        """只有 TIF、没有 JPG 时，也能把成果登记到当前编号。"""
        from app.services.grouping_service import Group, save_grouping, load_grouping
        from app.services import tiff_metadata_service

        w, ctx, uid, db = self._build(tmp_path)
        old_dir = tmp_path / "external-results"
        old_dir.mkdir()
        tif = old_dir / "GXFCG-BLW-BZC003-4-R-20260618.tif"
        tif.write_bytes(b"II*\x00external")
        save_grouping(db, uid, [
            Group(
                group_index=0,
                jpg_paths=[],
                composed_tiff_path=str(tif),
                status="composed",
            )
        ])
        w._grouping.load_grouping(uid, load_grouping(db, uid))
        w._current_uid = "GXFCG-BLW-SC999-R-20260618"
        w._sidebar = MagicMock()
        monkeypatch.setattr(
            tiff_metadata_service,
            "write_result_tiff_metadata",
            lambda *a, **k: {},
        )

        assert w._on_organise_requested(uid, 0, silent_batch=True) is True

        saved = load_grouping(db, uid).groups[0]
        assert saved.status == "organized"
        assert Path(saved.composed_tiff_path).parent == tmp_path / "results"
        assert Path(saved.composed_tiff_path).is_file()
        assert saved.archive_zip in (None, "")
        assert not tif.exists()
        w._sidebar.select_uid.assert_not_called()
        db.close()

    def test_headless_compose_uses_suggested_name_and_saves(self, tmp_path, monkeypatch):
        """_compose_group_headless:无 output_name 覆盖 → 用 suggested_tiff_name,
        合成完成持久化 status='composed' + composed_tiff_path。无确认框。"""
        from app.services.grouping_service import load_grouping
        w, ctx, uid, db = self._build(tmp_path)

        captured = {}

        def _fake_stack(jpgs, out_path, params, on_finished, on_failed, **kwargs):
            captured["out"] = out_path
            captured["jpgs"] = list(jpgs)
            Path(out_path).parent.mkdir(parents=True, exist_ok=True)
            Path(out_path).write_bytes(b"II*\x00fake-tiff")
            on_finished(out_path)

        monkeypatch.setattr(w, "_run_helicon_stack", _fake_stack)

        done = []
        w._compose_group_headless(uid, 0, lambda ok: done.append(ok))

        assert done == [True]
        # 产出名 = 建议成果名(编号-序号.tif),非弹框选择
        assert Path(captured["out"]).name.startswith("FJ-XM-B2-DLC001")
        assert captured["jpgs"] == self._jpgs[0:2]
        g0 = next(g for g in load_grouping(db, uid).groups if g.group_index == 0)
        assert g0.status == "composed"
        assert g0.composed_tiff_path == captured["out"]
        db.close()

    def test_headless_compose_honours_output_name_override(self, tmp_path, monkeypatch):
        """每组 output_name 覆盖值优先于建议名(去 .tif 后缀再加 .tif)。"""
        from app.services.grouping_service import Group, save_grouping
        w, ctx, uid, db = self._build(tmp_path)
        # 给组0 设输出命名覆盖(用真实 JPG 路径,免被 clean_phantoms 剔除)
        save_grouping(db, uid, [
            Group(group_index=0, jpg_paths=self._jpgs[0:2],
                  output_name="我的标本X"),
            Group(group_index=1, jpg_paths=self._jpgs[2:4]),
        ])
        db.commit()
        captured = {}

        def _fake_stack(jpgs, out_path, params, on_finished, on_failed, **kwargs):
            captured["out"] = out_path
            Path(out_path).parent.mkdir(parents=True, exist_ok=True)
            Path(out_path).write_bytes(b"II*\x00fake-tiff")
            on_finished(out_path)

        monkeypatch.setattr(w, "_run_helicon_stack", _fake_stack)
        w._compose_group_headless(uid, 0, lambda ok: None)
        assert Path(captured["out"]).name == "我的标本X.tif"
        db.close()


class TestAdhocGrouping:
    """Ad-hoc grouping is the unassigned target when no specimen is active."""

    def _build_adhoc(self, tmp_path, n_groups=2):
        from app.views.workbench_view import WorkbenchView
        from app.services.grouping_service import (
            Group, save_grouping, ADHOC_GROUPING_UID,
        )
        project_dir = str(tmp_path)
        db = _make_db(str(tmp_path / "project.db"))
        jdir = tmp_path / "incoming-jpg"
        jdir.mkdir(parents=True, exist_ok=True)
        jpgs = []
        for i in range(1, n_groups * 2 + 1):
            p = jdir / f"{i}.jpg"
            p.write_bytes(b"\xff\xd8\xff\xe0jpg")
            jpgs.append(str(p))
        groups = [
            Group(group_index=i, jpg_paths=jpgs[i * 2:i * 2 + 2])
            for i in range(n_groups)
        ]
        save_grouping(db, ADHOC_GROUPING_UID, groups)
        db.commit()
        ctx = _make_ctx(project_dir=project_dir, db=db)
        self._jpgs = jpgs
        return WorkbenchView(ctx), ctx, db

    def test_open_grouping_without_uid_binds_unassigned_and_shows_controls(self, tmp_path):
        from app.views.workbench_view import WorkbenchView
        from app.services.grouping_service import ADHOC_GROUPING_UID
        db = _make_db(str(tmp_path / "project.db"))
        ctx = _make_ctx(project_dir=str(tmp_path), db=db)
        w = WorkbenchView(ctx)
        w._current_uid = None
        w._on_open_grouping()
        assert w._grouping._uid == ADHOC_GROUPING_UID
        assert not w._grouping._add_btn.isHidden()
        assert not w._grouping._toolbar_widget.isHidden()
        db.close()

    def test_open_grouping_without_project_binds_unassigned_in_memory(self, tmp_path):
        """无项目/无激活编号时，仍可建立内存中的未归属分组。"""
        from app.views.workbench_view import WorkbenchView
        from app.services.grouping_service import ADHOC_GROUPING_UID
        ctx = _make_ctx(project_dir=None, db=None)
        w = WorkbenchView(ctx)
        w._current_uid = None
        w._on_open_grouping()
        assert w._grouping._uid == ADHOC_GROUPING_UID
        assert not w._grouping._add_btn.isHidden()
        assert not w._grouping._toolbar_widget.isHidden()
        assert w._grouping._auto_group_drop.isVisible()

    def test_resolve_output_name_adhoc_defaults_to_group_seq(self, tmp_path):
        from app.services.grouping_service import ADHOC_GROUPING_UID, Group
        w, ctx, db = self._build_adhoc(tmp_path)
        res = str(tmp_path / "results")
        inc = str(tmp_path / "incoming-jpg")
        n0, s0 = w._resolve_compose_output_name(
            db, ADHOC_GROUPING_UID, Group(group_index=0), res, inc)
        n1, s1 = w._resolve_compose_output_name(
            db, ADHOC_GROUPING_UID, Group(group_index=1), res, inc)
        assert (n0, s0) == ("1.tif", 1)
        assert (n1, s1) == ("2.tif", 2)
        db.close()

    def test_resolve_output_name_override_wins(self, tmp_path):
        from app.services.grouping_service import ADHOC_GROUPING_UID, Group
        w, ctx, db = self._build_adhoc(tmp_path)
        res = str(tmp_path / "results")
        inc = str(tmp_path / "incoming-jpg")
        n, _ = w._resolve_compose_output_name(
            db, ADHOC_GROUPING_UID,
            Group(group_index=0, output_name="我的X"), res, inc)
        assert n == "我的X.tif"
        db.close()

    def test_resolve_output_name_real_uid_uses_suggested(self, tmp_path):
        from app.services.grouping_service import Group
        w, ctx, db = self._build_adhoc(tmp_path)
        uid = "FJ-XM-B2-DLC001-T95E-20260601"
        db.execute(
            """INSERT INTO specimens
               (uid, id, province, site, station, storage,
                collection_date, photo_date, owner_project_dir)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (uid, "DLC001", "FJ", "XM", "B2", "T95E",
             "20260601", "20260601", str(tmp_path)),
        )
        db.commit()
        res = str(tmp_path / "results")
        inc = str(tmp_path / "incoming-jpg")
        n, s = w._resolve_compose_output_name(
            db, uid, Group(group_index=0), res, inc)
        assert n.startswith("FJ-XM-B2-DLC001") and n.endswith(".tif")
        assert isinstance(s, int) and s >= 1
        db.close()

    def test_resolve_output_name_real_uid_advances_after_existing_tif(self, tmp_path):
        from app.services.grouping_service import Group

        w, ctx, db = self._build_adhoc(tmp_path)
        uid = "FJ-XM-B2-DLC001-T95E-20260601"
        res = tmp_path / "results"
        inc = tmp_path / "incoming-jpg"
        res.mkdir(exist_ok=True)
        inc.mkdir(exist_ok=True)
        (res / "FJ-XM-B2-DLC001-1-T95E-20260601.tif").write_bytes(b"II*\x00")

        n, s = w._resolve_compose_output_name(
            db,
            uid,
            Group(group_index=0),
            str(res),
            str(inc),
        )

        assert n == "FJ-XM-B2-DLC001-2-T95E-20260601.tif"
        assert s == 2
        db.close()

    def test_headless_compose_adhoc_names_group_seq(self, tmp_path, monkeypatch):
        from app.services.grouping_service import ADHOC_GROUPING_UID, load_grouping
        w, ctx, db = self._build_adhoc(tmp_path)
        captured = {}

        def _fake_stack(jpgs, out_path, params, on_finished, on_failed, **kwargs):
            captured["out"] = out_path
            Path(out_path).parent.mkdir(parents=True, exist_ok=True)
            Path(out_path).write_bytes(b"II*\x00fake-tiff")
            on_finished(out_path)

        monkeypatch.setattr(w, "_run_helicon_stack", _fake_stack)
        done = []
        w._compose_group_headless(ADHOC_GROUPING_UID, 1, lambda ok: done.append(ok))
        assert done == [True]
        assert Path(captured["out"]).name == "2.tif"  # 组1 → 2.tif
        db.close()

    def test_organise_without_project_writes_zip_beside_tiff(
        self, qtbot, tmp_path, monkeypatch
    ):
        """无项目时允许本地整理：ZIP 与 TIF 同目录，名字取 TIF 基础名。"""
        from app.services.grouping_service import ADHOC_GROUPING_UID, Group, SpecimenGrouping
        from app.services import archive_service
        from app.views.workbench_view import WorkbenchView

        ctx = _make_ctx(project_dir=None, db=None)
        w = WorkbenchView(ctx)
        jpg1 = tmp_path / "a.jpg"
        jpg2 = tmp_path / "b.jpg"
        tiff = tmp_path / "sample-result.tif"
        jpg1.write_bytes(b"\xff\xd8a")
        jpg2.write_bytes(b"\xff\xd8b")
        tiff.write_bytes(b"II*\x00tif")
        grouping = SpecimenGrouping(
            uid=ADHOC_GROUPING_UID,
            groups=[
                Group(
                    group_index=0,
                    jpg_paths=[str(jpg1), str(jpg2)],
                    composed_tiff_path=str(tiff),
                    status="composed",
                )
            ],
        )
        w._grouping.load_grouping(ADHOC_GROUPING_UID, grouping)

        captured = {}

        def _fake_archive(jpg_paths, tiff_path, project_dir, delete_jpg, **kwargs):
            captured["output_dir"] = kwargs.get("output_dir")
            captured["project_dir"] = project_dir
            zip_path = str(Path(kwargs["output_dir"]) / "sample-result.zip")
            return _fake_zip_result(jpg_paths, zip_path)

        monkeypatch.setattr(archive_service, "archive_group", _fake_archive)

        assert w._on_organise_requested(
            ADHOC_GROUPING_UID, 0, silent_batch=True
        ) is True

        qtbot.waitUntil(
            lambda: not getattr(w, "_archive_workers", set()),
            timeout=5000,
        )

        saved = w._grouping._grouping.groups[0]
        assert captured["output_dir"] == str(tmp_path)
        assert captured["project_dir"] == str(tmp_path)
        assert saved.status == "organized"
        assert saved.composed_tiff_path == str(tiff)
        assert saved.archive_zip == str(tmp_path / "sample-result.zip")

    def test_batch_adhoc_one_shot_silent_no_prompts(self, qtbot, tmp_path, monkeypatch):
        """无编号 [合成+整理] 一条龙:两组都 合成→打包→移results,全程零确认框。"""
        from app.services.grouping_service import (
            ADHOC_GROUPING_UID, load_grouping,
        )
        from PyQt6.QtWidgets import QMessageBox
        from app.services import archive_service
        w, ctx, db = self._build_adhoc(tmp_path)
        self._patch_helicon_present(monkeypatch)

        def _fake_stack(jpgs, out_path, params, on_finished, on_failed, **kwargs):
            assert kwargs.get("show_progress_dialog") is False
            Path(out_path).parent.mkdir(parents=True, exist_ok=True)
            Path(out_path).write_bytes(b"II*\x00fake-tiff")
            on_finished(out_path)

        monkeypatch.setattr(w, "_run_helicon_stack", _fake_stack)

        def _fake_archive(jpg_paths, tiff_path, project_dir, delete_jpg, **kwargs):
            z = str(Path(tiff_path).with_suffix(".zip"))
            return _fake_zip_result(jpg_paths, z, saved_percent=10)

        monkeypatch.setattr(archive_service, "archive_group", _fake_archive)

        prompts = []
        monkeypatch.setattr(
            QMessageBox, "question",
            staticmethod(lambda *a, **k: prompts.append(a) or QMessageBox.StandardButton.No),
        )
        monkeypatch.setattr(QMessageBox, "information",
                            staticmethod(lambda *a, **k: prompts.append(a)))
        monkeypatch.setattr(QMessageBox, "warning",
                            staticmethod(lambda *a, **k: prompts.append(a)))

        w._start_compose_batch(ADHOC_GROUPING_UID, organise=True)

        qtbot.waitUntil(
            lambda: all(
                g.status == "organized"
                for g in load_grouping(db, ADHOC_GROUPING_UID).groups
            ),
            timeout=5000,
        )

        # 两组都已整理 → status organized
        groups = {g.group_index: g for g in load_grouping(db, ADHOC_GROUPING_UID).groups}
        assert groups[0].status == "organized"
        assert groups[1].status == "organized"
        # 全程零弹框(无激活拦截/无 1.tif 改名/无成功提示)
        assert prompts == []
        db.close()

    def _patch_helicon_present(self, monkeypatch):
        import app.services.helicon_service as hs
        monkeypatch.setattr(hs, "detect_helicon", lambda: "/fake/HeliconFocus.exe")


class TestMissingMetaReminder:
    """激活下一个编号时，上一个若缺 保存方式/采集日期/拍摄日期 → 提醒回填。"""

    def _seed(self, tmp_path, storage, cdate, pdate):
        from app.views.workbench_view import WorkbenchView
        db = _make_db(str(tmp_path / "project.db"))
        uid = "FJ-XM-B2-DLC001-T95E-20260601"
        db.execute(
            """INSERT INTO specimens
               (uid, id, province, site, station, storage,
                collection_date, photo_date, owner_project_dir)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (uid, "DLC001", "FJ", "XM", "B2", storage, cdate, pdate, str(tmp_path)),
        )
        db.commit()
        ctx = _make_ctx(project_dir=str(tmp_path), db=db)
        return WorkbenchView(ctx), uid, db

    def test_missing_dates_listed(self, tmp_path):
        w, uid, db = self._seed(tmp_path, "T95E", "", "")
        assert w._missing_meta_fields(uid) == ["采集日期", "拍摄日期"]
        db.close()

    def test_complete_specimen_lists_nothing(self, tmp_path):
        w, uid, db = self._seed(tmp_path, "T95E", "20260601", "20260601")
        assert w._missing_meta_fields(uid) == []
        db.close()

    def test_missing_storage_listed(self, tmp_path):
        w, uid, db = self._seed(tmp_path, "", "20260601", "")
        assert w._missing_meta_fields(uid) == ["保存方式", "拍摄日期"]
        db.close()


class TestCollectionDateSoftRequired:
    """采集日期=核心字段写入UID；保存时空值强提醒但允许继续(兼容未知日期)。"""

    def _wb(self, tmp_path):
        from app.views.workbench_view import WorkbenchView
        db = _make_db(str(tmp_path / "project.db"))
        ctx = _make_ctx(project_dir=str(tmp_path), db=db)
        ctx.collab_service = None  # 跳过协作 UID 占用分支
        w = WorkbenchView(ctx)
        n = w._naming
        n._province.setText("FJ")
        n._site.setText("XM")
        n._station.setText("B2")
        n._species_id.setText("DLC001")
        n._storage.setText("T95E")
        n._update_preview()
        return w, n, db

    def test_empty_date_back_cancels_save(self, tmp_path, monkeypatch):
        from PyQt6.QtWidgets import QMessageBox
        w, n, db = self._wb(tmp_path)
        n._collection_date.setText("")
        n._update_preview()
        monkeypatch.setattr(
            QMessageBox, "question",
            staticmethod(lambda *a, **k: QMessageBox.StandardButton.Cancel),
        )
        w._on_naming_save()
        assert db.execute("SELECT count(*) FROM specimens").fetchone()[0] == 0
        db.close()

    def test_empty_date_proceed_saves_dateless_uid(self, tmp_path, monkeypatch):
        from PyQt6.QtWidgets import QMessageBox
        w, n, db = self._wb(tmp_path)
        n._collection_date.setText("")
        n._update_preview()
        monkeypatch.setattr(
            QMessageBox, "question",
            staticmethod(lambda *a, **k: QMessageBox.StandardButton.Save),
        )
        w._on_naming_save()
        rows = db.execute("SELECT uid FROM specimens").fetchall()
        assert len(rows) == 1
        assert rows[0][0] == "FJ-XM-B2-DLC001-T95E"  # 无日期段(兼容)
        db.close()

    def test_filled_date_no_prompt_uid_has_date(self, tmp_path, monkeypatch):
        from PyQt6.QtWidgets import QMessageBox
        w, n, db = self._wb(tmp_path)
        n._collection_date.setText("20260613")
        n._update_preview()
        calls = {"q": 0}

        def _q(*a, **k):
            calls["q"] += 1
            return QMessageBox.StandardButton.Yes

        monkeypatch.setattr(QMessageBox, "question", staticmethod(_q))
        w._on_naming_save()
        rows = db.execute("SELECT uid FROM specimens").fetchall()
        assert len(rows) == 1
        assert rows[0][0].endswith("-20260613")  # 日期写入编号
        assert calls["q"] == 0  # 填了不弹问
        db.close()


class TestRestoreLastProject:
    """启动自动恢复上次项目 — 免得每次重启回到空项目。只恢复有效 workspace。"""

    def test_restores_valid_workspace(self, tmp_path):
        import main
        root = tmp_path / "survey"
        workspace = root / "section-a"
        (workspace / "_data").mkdir(parents=True)
        # 0-byte file is a VALID empty sqlite db; non-db bytes would make
        # enter_workspace correctly refuse to restore ("file is not a database").
        (workspace / "_data" / "project.db").write_bytes(b"")
        ctx = MagicMock()
        ctx.settings.last_project_dir = str(workspace)
        ctx.settings.project_tree_root = str(root)
        win = MagicMock()
        assert main._restore_last_project(ctx, win) is True
        assert ctx.current_project_dir == str(workspace)
        assert ctx.current_project_root == str(root)
        win.refresh_context_bar.assert_called_once()

    def test_restores_workspace_saved_as_wsl_path_on_windows(self, tmp_path, monkeypatch):
        import main
        import app.utils.path_utils as path_utils
        from app.utils.path_utils import windows_to_wsl

        root = tmp_path / "survey"
        workspace = root / "section-a"
        (workspace / "_data").mkdir(parents=True)
        (workspace / "_data" / "project.db").write_bytes(b"")
        root_wsl = windows_to_wsl(str(root))
        workspace_wsl = windows_to_wsl(str(workspace))
        if not root_wsl or not workspace_wsl:
            pytest.skip("Requires a Windows drive path")

        monkeypatch.setattr(path_utils.sys, "platform", "win32")
        ctx = MagicMock()
        ctx.settings.last_project_dir = workspace_wsl
        ctx.settings.project_tree_root = root_wsl
        win = MagicMock()

        assert main._restore_last_project(ctx, win) is True
        assert ctx.current_project_dir == str(workspace)
        assert ctx.current_project_root == str(root)

    def test_skips_invalid_dir(self, tmp_path):
        import main
        ctx = MagicMock()
        ctx.settings.last_project_dir = str(tmp_path / "nope")
        win = MagicMock()
        assert main._restore_last_project(ctx, win) is False

    def test_skips_non_workspace_dir(self, tmp_path):
        import main
        ctx = MagicMock()
        ctx.settings.last_project_dir = str(tmp_path)  # 无 _data/project.db
        win = MagicMock()
        assert main._restore_last_project(ctx, win) is False

    def test_skips_when_no_last_project(self, tmp_path):
        import main
        ctx = MagicMock()
        ctx.settings.last_project_dir = None
        win = MagicMock()
        assert main._restore_last_project(ctx, win) is False


class TestAutoGroupOrganize:
    def test_monitor_legacy_entry_opens_grouping_then_scans(self, qtbot, tmp_path):
        from app.views.workbench_view import WorkbenchView

        db = _make_db(str(tmp_path / "project.db"))
        ctx = _make_ctx(str(tmp_path), db)
        w = WorkbenchView(ctx)
        qtbot.addWidget(w)
        calls = []
        w._on_open_grouping = lambda: calls.append("open")
        w._on_auto_group_organize = lambda: calls.append("scan")

        w._on_legacy_photo_batch_organize()

        assert calls == ["open", "scan"]
        db.close()

    def test_folder_picker_parents_to_grouping_dialog(self, qtbot, tmp_path):
        from unittest.mock import patch
        from PyQt6.QtWidgets import QDialog
        from app.views.workbench_view import WorkbenchView

        project_dir = str(tmp_path)
        db = _make_db(str(tmp_path / "project.db"))
        ctx = _make_ctx(project_dir, db)
        w = WorkbenchView(ctx)
        qtbot.addWidget(w)
        w.on_activate()
        w._grouping_dialog.show()

        seen = []

        def fake_get_dir(parent, caption, start=""):
            seen.append(parent)
            return ""

        with patch("app.views.workbench_view._AutoGroupSourceDialog") as MockChooser, \
             patch("app.views.workbench_view.ui.get_existing_directory", fake_get_dir):
            MockChooser.MODE_FOLDER = "folder"
            MockChooser.MODE_PROJECT = "project"
            inst = MockChooser.return_value
            inst.exec.return_value = QDialog.DialogCode.Accepted
            inst.selected_source_mode.return_value = "folder"
            w._on_auto_group_organize()

        assert seen == [w._grouping_dialog]
        db.close()

    def test_works_without_open_project(self, qtbot):
        from unittest.mock import patch
        from PyQt6.QtWidgets import QDialog
        from app.views.workbench_view import WorkbenchView

        ctx = _make_ctx(None, None)
        w = WorkbenchView(ctx)
        qtbot.addWidget(w)
        w.on_activate()
        w._grouping_dialog.show()

        seen = []

        def fake_get_dir(parent, caption, start=""):
            seen.append((parent, start))
            return ""

        with patch("app.views.workbench_view._AutoGroupSourceDialog") as MockChooser, \
             patch("app.views.workbench_view.ui.get_existing_directory", fake_get_dir), \
             patch("app.views.workbench_view.ui.warn") as mock_warn:
            MockChooser.MODE_FOLDER = "folder"
            MockChooser.MODE_PROJECT = "project"
            inst = MockChooser.return_value
            inst.exec.return_value = QDialog.DialogCode.Accepted
            inst.selected_source_mode.return_value = "folder"
            w._on_auto_group_organize()

        assert seen
        mock_warn.assert_not_called()

    def test_uses_staged_files_without_folder_picker(self, qtbot, tmp_path):
        from unittest.mock import patch
        from app.views.workbench_view import WorkbenchView

        jpg = tmp_path / "a.jpg"
        tif = tmp_path / "FJ-XM-B2-DLC001-1-T95E-20260601.tif"
        jpg.write_bytes(b"j")
        tif.write_bytes(b"t")
        ctx = _make_ctx(None, None)
        w = WorkbenchView(ctx)
        qtbot.addWidget(w)
        w.on_activate()
        w._grouping.add_auto_group_staged([str(jpg), str(tif)])

        with patch.object(w, "_pick_auto_group_source_folder") as mock_pick, \
             patch("app.widgets.retroactive_modal.RetroactiveModal.exec") as mock_exec:
            w._on_auto_group_organize()

        mock_pick.assert_not_called()
        mock_exec.assert_not_called()
        assert w._grouping.has_auto_group_preview()
        assert w._grouping._auto_group_btn.text() == "执行整理归档"

        with patch("app.widgets.retroactive_modal.RetroactiveModal.exec",
                   return_value=0):
            w._on_auto_group_organize()


class TestThreeColumnLayoutFits:
    """三栏最小宽度之和必须放得进常见窗口(2026-07-11 用户报障:中/右栏挤穿)。

    根因:监控面板工具栏 8 个文字按钮硬撑 600px → 中栏最小值顶到 717,
    侧栏 300 + 中 717 + 右 507 = 1524 > 窗口 → Qt 强行等分压扁, 内容互挤。
    """

    def test_column_min_widths_fit_common_window(self):
        from app.views.workbench_view import WorkbenchView
        ctx = _make_ctx()
        v = WorkbenchView(ctx)
        total = (
            v._sidebar_min_width()
            + v._centre_min_width()
            + v._right_rail_min_width()
        )
        # 用户屏幕可见宽约 1155(小屏/远程桌面常见)。三栏最小和必须放得进,
        # 否则内容被裁到够不着(2026-07-11 用户报障)。留余量锁在 1130。
        assert total <= 1130, (
            f"三栏最小宽度之和 {total}px 超过 1130px → 小屏会被裁掉够不着"
        )

    def test_centre_min_uses_hard_minimum_not_preferred(self):
        """中栏最小值应取硬最小值(minimumSizeHint), 不是贪心的偏好宽。"""
        from app.views.workbench_view import WorkbenchView
        ctx = _make_ctx()
        v = WorkbenchView(ctx)
        # 工具栏放进横向滚动容器后, 监控面板硬最小值应显著小于旧的 717。
        assert v._monitor.minimumSizeHint().width() < 600, (
            "监控面板硬最小值仍过大 → 工具栏未被横向滚动容器解放"
        )

    def test_degenerate_saved_splitter_state_is_discarded(self):
        """QSettings 存了坏的旧布局(各列压穿)→ 恢复时应丢弃、退回默认分布。"""
        from PyQt6.QtCore import QByteArray, QSettings
        from app.views.workbench_view import (
            WorkbenchView,
            _WORKBENCH_OUTER_SPLITTER_STATE_KEY,
        )

        ctx = _make_ctx()
        v = WorkbenchView(ctx)
        v.resize(1310, 780)
        # 造一个把三栏压到极窄的退化状态存进 QSettings
        v._outer_splitter.setSizes([50, 50, 50])
        bad = v._outer_splitter.saveState()
        real_qs = QSettings("SpecimenPhotoWorkbench", "workbench-layout-test")
        real_qs.setValue(_WORKBENCH_OUTER_SPLITTER_STATE_KEY, bad)
        # 用真实 QSettings 让守卫走恢复路径
        v._ui_settings = lambda: real_qs
        v.show()
        v._restore_workbench_outer_splitter()
        sizes = v._outer_splitter.sizes()
        # 守卫应拒绝退化状态 → 侧栏/右栏不再被压到远低于最小值
        assert sizes[0] >= v._sidebar_min_width() - 40
        assert sizes[2] >= v._right_rail_min_width() - 40
        v.close()
        real_qs.clear()


class TestComposeOrganiseProgressBar:
    """合成+整理弹窗:字号正常化 + 真进度条(2026-07-11 用户报障字体异常)。"""

    def _dlg(self):
        from PyQt6.QtWidgets import QWidget
        from app.widgets.compose_organise_dialog import _ComposeOrganiseProgressDialog
        host = QWidget()
        return _ComposeOrganiseProgressDialog(host)

    def test_progress_advances_by_stage(self):
        d = self._dlg()
        d.set_notice("合成+整理：正在合成", "", state="busy", task_key="t")
        assert d._progress.value() == 30
        d.set_notice("合成+整理：正在整理", "", state="busy", task_key="t")
        assert d._progress.value() == 65
        d.set_notice("合成+整理完成", "", state="success", task_key="t")
        assert d._progress.value() == 100

    def test_progress_bar_exists_and_hidden_text(self):
        from PyQt6.QtWidgets import QProgressBar
        d = self._dlg()
        assert isinstance(d._progress, QProgressBar)
        assert not d._progress.isTextVisible()

    def test_typography_matches_compact_workbench_scale(self):
        d = self._dlg()
        qss = d.styleSheet()
        assert "font-size:14px" not in qss
        assert "font-weight:700" not in qss
        assert (
            "QLabel#ComposeOrganiseTitle {"
            in qss
            and "font-size:13px; font-weight:600" in qss
        )
        assert "font-size:11px; font-weight:600" in qss

    def test_typography_follows_global_font_scale(self):
        from app.config.theme import apply_theme, set_typography

        try:
            set_typography(scale=1.3)
            apply_theme("classic_light")
            qss = self._dlg().styleSheet()
            assert "font-size:17px; font-weight:600" in qss
            assert "font-size:16px; font-weight:600" in qss
            assert "font-size:14px; font-weight:600" in qss
        finally:
            set_typography(scale=1.0)
            apply_theme("classic_light")


class TestHeliconCancelRelease:
    """取消 Helicon 合成必须释放 worker, 不泄漏线程(2026-07-11 审查发现)。

    根因: HeliconWorker 取消时 run() 直接 return 不 emit 信号,
    _release_helicon_worker(挂在 finished/failed)永不触发 → 每取消一次
    泄漏一个 QThread + set/map 残留。修复: 取消后 wait 让线程结束再主动释放。
    """

    def test_cancel_removes_worker_from_active_set(self, monkeypatch, tmp_path):
        import app.views.workbench_compose_workflow as cw
        from app.views.workbench_view import WorkbenchView

        w = WorkbenchView(_make_ctx(project_dir=str(tmp_path)))

        # 假 worker: 不起真子进程, cancel/wait/deleteLater 都 no-op 但可断言。
        class _FakeWorker:
            def __init__(self, cmd=None, output_path=None, parent=None):
                self.finished = _FakeSig()
                self.failed = _FakeSig()
                self.canceled = _FakeSig()
                self._started = False

            def start(self):
                self._started = True

            def cancel(self):
                pass

            def wait(self, _ms=0):
                return True   # 线程已结束

            def deleteLater(self):
                pass

        class _FakeSig:
            def __init__(self):
                self._slots = []

            def connect(self, fn):
                self._slots.append(fn)

            def emit(self, *a):
                for fn in list(self._slots):
                    fn(*a)

        import app.services.helicon_service as hs
        monkeypatch.setattr(cw, "HeliconWorker", _FakeWorker)
        monkeypatch.setattr(hs, "build_helicon_cmd", lambda **k: ["echo", "ok"])
        monkeypatch.setattr(w, "_helicon_output_opts",
                            lambda: {"tiff_compression": None, "quality": None,
                                     "output_format": "tif"}, raising=False)

        results = []
        w._run_helicon_stack(
            ["/a.jpg", "/b.jpg"], str(tmp_path / "out.tif"),
            {"method": "B", "radius": "8", "smoothing": "4"},
            on_finished=lambda p: results.append(("ok", p)),
            on_failed=lambda m: results.append(("fail", m)),
            show_progress_dialog=True,
        )
        assert len(w._helicon_workers) == 1
        worker = next(iter(w._helicon_workers))

        # 模拟用户点取消 → progress.canceled 触发 _cancel_running_helicon_worker
        w._helicon_progress.canceled.emit()

        assert worker not in w._helicon_workers, "取消后 worker 必须从活动集合移除(不泄漏)"
        assert ("fail", "用户取消") in results


class TestBatchMutualExclusion:
    """批量合成/批量整理互斥(A3, 2026-07-11 审查发现)。

    根因: _start_compose_batch 无守卫, 批量跑一半再点一次 → self._batch 被
    直接覆盖 → 在飞回调读到新队列, 旧队列组变孤儿、同组 double-archive。
    修复: 双向 running-guard, 任一批量在跑都拒绝再启动并给可见提示。
    """

    def _wb(self, tmp_path):
        from app.views.workbench_view import WorkbenchView
        return WorkbenchView(_make_ctx(project_dir=str(tmp_path)))

    def test_second_compose_batch_does_not_clobber_running_one(self, tmp_path):
        w = self._wb(tmp_path)
        running = {"uid": "U1", "queue": [1, 2], "organise": True,
                   "total": 2, "done": 0, "label": "批量合成+整理",
                   "task_key": "batch-compose:U1:organise"}
        w._batch = dict(running)
        w._start_compose_batch("U2", organise=False)
        assert w._batch == running, "在飞批次不得被第二次点击覆盖"

    def test_compose_batch_blocked_while_organise_batch_running(self, tmp_path):
        w = self._wb(tmp_path)
        w._batch = None
        w._organise_batch = {"uid": "U1", "queue": [1], "total": 1,
                             "done": 0, "label": "批量整理"}
        w._start_compose_batch("U2", organise=True)
        assert w._batch is None, "整理批在跑时不得启动合成批"

    def test_organise_batch_blocked_while_compose_batch_running(self, tmp_path):
        w = self._wb(tmp_path)
        w._organise_batch = None
        w._batch = {"uid": "U1", "queue": [1], "organise": True,
                    "total": 1, "done": 0, "label": "批量合成+整理",
                    "task_key": "k"}
        w._organise_all_batch("U2")
        assert w._organise_batch is None, "合成批在跑时不得再起整理批"


# ─────────────────────────────────────────────────────────────────────────────
# C4: 写库失败不许被静默吞掉 (Fable 5, 2026-07-12, 用户: "c4啊,这个不是很重要吗")
#
# 场景: 这四条路径都会先动磁盘/内存(删 TIFF、改文件名、迁编号、抽回原片), 再把结果
#   写进 project.db。写库那一步原来包在 `except Exception: pass` / `return ""` 里 ——
#   数据库忙、被别的窗口锁住、磁盘满时, 磁盘已经变了、数据库没变, 界面却照样报成功。
#   下次打开工作区: 组还在但 TIFF 没了 / 文件名对不上 / 照片挂在已删除的旧编号下。
# 要求: 写库失败必须**看得见**(状态栏 + 错误通知/弹窗), 绝不装成功。
# ─────────────────────────────────────────────────────────────────────────────
class TestWriteFailuresAreVisible:

    @staticmethod
    def _wb(qtbot, tmp_path):
        from app.views.workbench_view import WorkbenchView
        db = _make_db(str(tmp_path / "project.db"))
        w = WorkbenchView(_make_ctx(str(tmp_path), db))
        qtbot.addWidget(w)
        return w, db

    def test_save_grouping_or_warn_surfaces_failure(self, qtbot, tmp_path, monkeypatch):
        """共用写库网关: 失败 -> False + 状态栏 + error 通知; 不抛不吞。"""
        w, db = self._wb(qtbot, tmp_path)
        statuses, notices = [], []
        monkeypatch.setattr(w, "_status_message", lambda t, *a: statuses.append(t))
        monkeypatch.setattr(w, "_workflow_notice", lambda *a, **k: notices.append((a, k)))
        from app.services import grouping_service
        monkeypatch.setattr(
            grouping_service, "save_grouping",
            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("database is locked")),
        )

        ok = w._save_grouping_or_warn(db, "UID-1", [], what="撤销合成")
        assert ok is False
        assert any("撤销合成" in s and "保存失败" in s for s in statuses)
        assert notices and notices[-1][1]["state"] == "error"
        db.close()

    def test_save_grouping_or_warn_returns_true_on_success(self, qtbot, tmp_path, monkeypatch):
        w, db = self._wb(qtbot, tmp_path)
        called = {}
        from app.services import grouping_service
        monkeypatch.setattr(
            grouping_service, "save_grouping",
            lambda db_, uid, groups, **k: called.update(uid=uid, groups=groups),
        )
        assert w._save_grouping_or_warn(db, "UID-1", [], what="整理") is True
        assert called["uid"] == "UID-1"
        db.close()

    def test_uid_migration_reraises_real_db_error(self, qtbot, tmp_path, monkeypatch):
        """UID 迁移里 photo_assignments 的 UPDATE 原来被 `except: pass` 吞掉 ——
        照片仍挂旧编号, 而旧编号行已被 DELETE -> 照片成孤儿。真错必须冒泡到
        「编号迁移失败」弹窗(整笔事务回滚), 只有"老库没这张表"才允许放过。
        """
        w, db, real = self._wb_with_flaky_db(qtbot, tmp_path, monkeypatch,
                                             "database is locked")
        real.execute("INSERT INTO specimens(uid) VALUES ('OLD-1')")
        real.commit()

        warned = []
        import app.views.workbench_specimen_identity as ident
        monkeypatch.setattr(ident.QMessageBox, "warning", lambda *a, **k: warned.append(a))

        w._finalize_uid_rename("OLD-1", "NEW-1")

        assert warned, "写库真错必须弹「编号迁移失败」, 不许静默"
        # 事务回滚 -> 旧行还在, 没有半迁移的残局
        assert real.execute("SELECT 1 FROM specimens WHERE uid='OLD-1'").fetchone()
        real.close()

    def test_uid_migration_still_tolerates_missing_legacy_table(self, qtbot, tmp_path, monkeypatch):
        """老库没有 photo_assignments 表 —— 合法情况, 迁移照常完成, 不报错。"""
        w, db, real = self._wb_with_flaky_db(qtbot, tmp_path, monkeypatch,
                                             "no such table: photo_assignments")
        real.execute("INSERT INTO specimens(uid) VALUES ('OLD-2')")
        real.commit()

        warned = []
        import app.views.workbench_specimen_identity as ident
        monkeypatch.setattr(ident.QMessageBox, "warning", lambda *a, **k: warned.append(a))

        w._finalize_uid_rename("OLD-2", "NEW-2")
        assert not warned, "老库缺表是合法情况, 不该报错"
        real.close()

    @staticmethod
    def _wb_with_flaky_db(qtbot, tmp_path, monkeypatch, boom_msg):
        """工作台 + 一个「photo_assignments 一写就炸」的代理连接。

        sqlite3.Connection.execute 是只读属性, monkeypatch 不了 -> 用代理对象。
        """
        import sqlite3 as _sq
        from app.views.workbench_view import WorkbenchView

        real = _make_db(str(tmp_path / "project.db"))

        class _FlakyConn:
            def __init__(self, conn):
                self._c = conn

            def execute(self, sql, *a, **k):
                if "photo_assignments" in sql:
                    raise _sq.OperationalError(boom_msg)
                return self._c.execute(sql, *a, **k)

            def __enter__(self):
                return self._c.__enter__()

            def __exit__(self, *exc):
                return self._c.__exit__(*exc)

            def __getattr__(self, name):
                return getattr(self._c, name)

        flaky = _FlakyConn(real)
        w = WorkbenchView(_make_ctx(str(tmp_path), flaky))
        qtbot.addWidget(w)
        monkeypatch.setattr(w._naming, "persisted_uid", lambda: None)
        return w, flaky, real
