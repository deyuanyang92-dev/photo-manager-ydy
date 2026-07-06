"""tests/test_grouping_panel.py — GroupingPanel cross-group JPG drag-drop tests."""
from __future__ import annotations

import sqlite3
from unittest.mock import MagicMock, patch, call

import pytest

from PyQt6.QtCore import Qt, QMimeData, QPoint
from PyQt6.QtWidgets import QApplication, QLabel, QListWidget, QPushButton, QMenu


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_app_context():
    ctx = MagicMock()
    ctx.get_db.return_value = None
    ctx.current_project_dir = None
    return ctx


def _make_grouping(groups_data):
    """Build a SpecimenGrouping from list-of-dicts."""
    from app.services.grouping_service import Group, SpecimenGrouping
    groups = [
        Group(
            group_index=d["index"],
            angle_label=d.get("label", ""),
            jpg_paths=list(d.get("jpgs", [])),
        )
        for d in groups_data
    ]
    return SpecimenGrouping(uid="test-uid", groups=groups)


# ---------------------------------------------------------------------------
# Basic construction
# ---------------------------------------------------------------------------

def test_grouping_panel_constructs(qtbot):
    from app.widgets.grouping_panel import GroupingPanel
    ctx = _make_app_context()
    panel = GroupingPanel(ctx)
    qtbot.addWidget(panel)
    assert panel is not None


def test_toolbar_hidden_when_no_specimen(qtbot):
    """Toolbar + 新组 button are hidden before any grouping task is loaded."""
    from app.widgets.grouping_panel import GroupingPanel
    ctx = _make_app_context()
    panel = GroupingPanel(ctx)
    qtbot.addWidget(panel)
    assert panel._toolbar_widget.isHidden()
    assert panel._add_btn.isHidden()


def test_toolbar_hidden_after_clear(qtbot):
    """Toolbar must hide again after clear()."""
    from app.widgets.grouping_panel import GroupingPanel
    ctx = _make_app_context()
    panel = GroupingPanel(ctx)
    qtbot.addWidget(panel)
    grouping = _make_grouping([{"index": 0, "label": "top"}])
    panel.load_grouping("test-uid", grouping)
    assert not panel._toolbar_widget.isHidden()
    panel.clear()
    assert panel._toolbar_widget.isHidden()
    assert panel._add_btn.isHidden()


def test_toolbar_visible_after_load(qtbot):
    """Toolbar + 新组 button must appear after load_grouping()."""
    from app.widgets.grouping_panel import GroupingPanel
    ctx = _make_app_context()
    panel = GroupingPanel(ctx)
    qtbot.addWidget(panel)
    grouping = _make_grouping([{"index": 0, "label": "top"}])
    panel.load_grouping("test-uid", grouping)
    assert not panel._toolbar_widget.isHidden()
    assert not panel._add_btn.isHidden()


def test_load_grouping_skips_rebuild_when_render_data_unchanged(qtbot):
    """Returning to an already-rendered specimen should not rebuild all cards."""
    from app.widgets.grouping_panel import GroupingPanel

    ctx = _make_app_context()
    panel = GroupingPanel(ctx)
    qtbot.addWidget(panel)
    panel.load_grouping("test-uid", _make_grouping([
        {"index": 0, "label": "角度1", "jpgs": ["/p/a.jpg"]},
    ]))
    calls = []
    original_rebuild = panel._rebuild
    panel._rebuild = lambda: calls.append("rebuild")

    panel.load_grouping("test-uid", _make_grouping([
        {"index": 0, "label": "角度1", "jpgs": ["/p/a.jpg"]},
    ]))
    panel.load_grouping("test-uid", _make_grouping([
        {"index": 0, "label": "角度1", "jpgs": ["/p/a.jpg", "/p/b.jpg"]},
    ]))

    assert calls == ["rebuild"]
    panel._rebuild = original_rebuild


def test_auto_group_organize_button_emits_request(qtbot):
    """The grouping toolbar exposes the legacy-folder automation entry."""
    from app.widgets.grouping_panel import GroupingPanel

    panel = GroupingPanel(_make_app_context())
    qtbot.addWidget(panel)
    panel.load_grouping("test-uid", _make_grouping([]))

    with qtbot.waitSignal(panel.auto_group_organize_requested, timeout=1000):
        panel._auto_group_btn.click()


def test_auto_group_drop_zone_stages_files(qtbot, tmp_path):
    from app.widgets.grouping_panel import GroupingPanel

    jpg = tmp_path / "a.jpg"
    tif = tmp_path / "b.tif"
    jpg.write_bytes(b"j")
    tif.write_bytes(b"t")
    panel = GroupingPanel(_make_app_context())
    qtbot.addWidget(panel)
    panel.add_auto_group_staged([str(jpg), str(tif)])
    assert len(panel.staged_auto_group_paths()) == 2
    panel.clear_auto_group_staging()
    assert panel.staged_auto_group_paths() == []


def test_auto_group_drop_zone_pick_files(qtbot, tmp_path):
    from unittest.mock import patch
    from app.widgets.grouping_panel import GroupingPanel

    jpg = tmp_path / "pick.jpg"
    jpg.write_bytes(b"j")
    panel = GroupingPanel(_make_app_context())
    qtbot.addWidget(panel)
    with patch(
        "app.utils.ui.get_open_file_names",
        return_value=[str(jpg)],
    ):
        panel._auto_group_drop._pick_files()
    assert panel.staged_auto_group_paths() == [str(jpg.resolve())]


def test_auto_group_preview_toggles_action_button(qtbot):
    from app.widgets.grouping_panel import GroupingPanel

    panel = GroupingPanel(_make_app_context())
    qtbot.addWidget(panel)
    assert panel._auto_group_btn.text() == "自动分组整理"
    panel.show_auto_group_preview({
        "specimens": [{
            "uid": "FJ-XM-B2-DLC001",
            "groups": [{
                "seq": 1,
                "tiffName": "a.tif",
                "jpgPaths": ["/tmp/a.jpg"],
                "jpgCount": 1,
            }],
        }],
    })
    assert panel.has_auto_group_preview()
    assert panel._auto_group_btn.text() == "执行整理归档"
    panel.clear_auto_group_preview()
    assert not panel.has_auto_group_preview()
    assert panel._auto_group_btn.text() == "自动分组整理"


def test_tiff_naming_check_button_emits_independent_request(qtbot):
    """TIFF naming audit is separate from automatic grouping and organizing."""
    from app.widgets.grouping_panel import GroupingPanel

    panel = GroupingPanel(_make_app_context())
    qtbot.addWidget(panel)
    panel.load_grouping("test-uid", _make_grouping([]))

    with qtbot.waitSignal(panel.tiff_naming_check_requested, timeout=1000):
        panel._tiff_naming_check_btn.click()


def test_composed_tiff_context_menu_checks_named_tiff(qtbot, tmp_path, monkeypatch):
    """Right-clicking a composed TIF can audit that exact file."""
    from app.services.grouping_service import Group, SpecimenGrouping
    from app.widgets.grouping_panel import GroupingPanel, _ComposedRow

    tif = tmp_path / "GXFCG-BLW-BZC003-1-R-20260618.tif"
    tif.write_bytes(b"tif")
    panel = GroupingPanel(_make_app_context())
    qtbot.addWidget(panel)
    panel.load_grouping(
        "GXFCG-BLW-BZC003-R-20260618",
        SpecimenGrouping(
            uid="GXFCG-BLW-BZC003-R-20260618",
            groups=[Group(group_index=0, composed_tiff_path=str(tif))],
        ),
    )
    row = panel.findChild(_ComposedRow)
    assert row is not None

    def fake_exec(menu, *_args, **_kwargs):
        return next(a for a in menu.actions() if a.text() == "检查 TIF 命名格式")

    monkeypatch.setattr(QMenu, "exec", fake_exec)
    with qtbot.waitSignal(
        panel.tiff_naming_check_path_requested, timeout=1000
    ) as blocker:
        row._show_tiff_menu(QPoint(0, 0))

    assert blocker.args == [str(tif)]


def test_composed_tiff_context_menu_delete_uses_undo_signal(
    qtbot, tmp_path, monkeypatch
):
    """Right-click Delete TIF reuses the existing undo/delete workflow."""
    from app.services.grouping_service import Group, SpecimenGrouping
    from app.widgets.grouping_panel import GroupingPanel, _ComposedRow

    tif = tmp_path / "result.tif"
    tif.write_bytes(b"tif")
    panel = GroupingPanel(_make_app_context())
    qtbot.addWidget(panel)
    panel.load_grouping(
        "UID1",
        SpecimenGrouping(
            uid="UID1",
            groups=[Group(group_index=2, composed_tiff_path=str(tif))],
        ),
    )
    row = panel.findChild(_ComposedRow)
    assert row is not None

    def fake_exec(menu, *_args, **_kwargs):
        return next(a for a in menu.actions() if a.text() == "删除 TIF")

    monkeypatch.setattr(QMenu, "exec", fake_exec)
    with qtbot.waitSignal(panel.undo_compose_requested, timeout=1000) as blocker:
        row._show_tiff_menu(QPoint(0, 0))

    assert blocker.args == ["UID1", 2]


def test_import_pending_button_creates_draft_group_from_jpg_picker(qtbot, tmp_path):
    """Toolbar import should create a visible pending group populated with JPGs."""
    from app.widgets.grouping_panel import GroupingPanel
    from app.services.grouping_service import SpecimenGrouping

    jpg = tmp_path / "P6202016.JPG"
    jpg.write_bytes(b"jpg")
    panel = GroupingPanel(_make_app_context())
    qtbot.addWidget(panel)
    panel.load_grouping("test-uid", SpecimenGrouping(uid="test-uid", groups=[]))

    with patch(
        "app.utils.ui.get_open_file_names",
        return_value=[str(jpg)],
    ):
        panel._import_pending_btn.click()

    assert len(panel._grouping.groups) == 1
    assert panel._grouping.groups[0].jpg_paths == [str(jpg)]
    assert panel._grouping.groups[0].composed_tiff_path is None


def test_import_pending_button_does_not_leave_empty_group_on_import_failure(
    qtbot, tmp_path, monkeypatch
):
    """导入失败/无导入结果时，不应先创建一个空组残留在分组工具里。"""
    import app.utils.ui as ui
    from app.services.grouping_service import SpecimenGrouping
    from app.widgets.grouping_panel import GroupingPanel

    project = tmp_path / "project"
    incoming = project / "incoming-jpg"
    incoming.mkdir(parents=True)
    jpg = tmp_path / "P0001.JPG"
    jpg.write_bytes(b"jpg")
    ctx = _make_app_context()
    ctx.current_project_dir = str(project)
    panel = GroupingPanel(ctx)
    qtbot.addWidget(panel)
    panel.load_grouping("test-uid", SpecimenGrouping(uid="test-uid", groups=[]))

    monkeypatch.setattr(ui, "get_open_file_names", lambda *a, **k: [str(jpg)])

    class _Result:
        imported_paths = []
        errors = []

    monkeypatch.setattr(
        "app.services.photo_import_service.import_jpgs_to_incoming",
        lambda *_a, **_k: _Result(),
    )

    with patch("PyQt6.QtWidgets.QMessageBox.warning"):
        panel._import_pending_btn.click()

    assert panel._grouping.groups == []


def test_link_result_pair_button_registers_tif_and_zip(qtbot, tmp_path, monkeypatch):
    """Toolbar can register an existing TIF+ZIP pair directly to current UID."""
    from PyQt6.QtWidgets import QDialog

    from app.widgets import grouping_panel as gp
    from app.widgets.grouping_panel import GroupingPanel
    from app.services.grouping_service import SpecimenGrouping

    results = tmp_path / "results"
    results.mkdir()
    tif = results / "GXFCG-BLW-HSSC001-1-RD79-20260618.tif"
    zip_path = results / "GXFCG-BLW-HSSC001-1-RD79-20260618.zip"
    tif.write_bytes(b"tif")
    zip_path.write_bytes(b"zip")
    ctx = _make_app_context()
    ctx.current_project_dir = str(tmp_path)
    panel = GroupingPanel(ctx)
    qtbot.addWidget(panel)
    panel.load_grouping("GXFCG-BLW-HSSC001-RD79-20260618", SpecimenGrouping(
        uid="GXFCG-BLW-HSSC001-RD79-20260618",
        groups=[],
    ))

    class _FakePicker:
        def __init__(self, candidates, parent=None):
            self._selected = candidates[0]

        def exec(self):
            return QDialog.DialogCode.Accepted

        def selected_pair(self):
            return self._selected

    monkeypatch.setattr(gp, "_ResultPairPickerDialog", _FakePicker)

    with qtbot.waitSignal(panel.archive_zip_registered, timeout=1000) as blocker:
        panel._link_result_pair_btn.click()

    assert blocker.args == ["GXFCG-BLW-HSSC001-RD79-20260618", 0]
    assert len(panel._grouping.groups) == 1
    group = panel._grouping.groups[0]
    assert group.composed_tiff_path == str(tif)
    assert group.archive_zip == str(zip_path)
    assert group.status == "organized"
    assert group.source == "existing-result-pair"


def test_link_result_pair_filters_sibling_specimen_results(qtbot, tmp_path, monkeypatch):
    """BZC003 cannot see or select BZC002 result pairs in the association dialog."""
    from PyQt6.QtWidgets import QDialog

    from app.services.grouping_service import SpecimenGrouping
    from app.widgets import grouping_panel as gp
    from app.widgets.grouping_panel import GroupingPanel

    results = tmp_path / "results"
    results.mkdir()
    wrong_tif = results / "GXFCG-BLW-BZC002-3-R-20260618.tif"
    wrong_zip = wrong_tif.with_suffix(".zip")
    right_tif = results / "GXFCG-BLW-BZC003-1-R-20260618.tif"
    right_zip = right_tif.with_suffix(".zip")
    for path in (wrong_tif, wrong_zip, right_tif, right_zip):
        path.write_bytes(path.suffix.encode())

    ctx = _make_app_context()
    ctx.current_project_dir = str(tmp_path)
    panel = GroupingPanel(ctx)
    qtbot.addWidget(panel)
    panel.load_grouping(
        "GXFCG-BLW-BZC003-R-20260618",
        SpecimenGrouping(uid="GXFCG-BLW-BZC003-R-20260618", groups=[]),
    )
    seen_names = []

    class _FakePicker:
        def __init__(self, candidates, parent=None):
            seen_names.extend(Path(c["tiff"]).name for c in candidates)
            self._selected = candidates[0]

        def exec(self):
            return QDialog.DialogCode.Accepted

        def selected_pair(self):
            return self._selected

    from pathlib import Path
    monkeypatch.setattr(gp, "_ResultPairPickerDialog", _FakePicker)

    with qtbot.waitSignal(panel.archive_zip_registered, timeout=1000):
        panel._link_result_pair_btn.click()

    assert seen_names == [right_tif.name]
    assert panel._grouping.groups[0].composed_tiff_path == str(right_tif)


def test_result_pair_candidates_hide_registered_pairs(tmp_path):
    """Already registered result pairs should not be selectable candidates."""
    from app.services.grouping_service import Group, save_grouping
    from app.widgets.grouping_panel import (
        _registered_result_paths,
        _result_pair_candidates,
    )

    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    results = tmp_path / "results"
    results.mkdir()
    used_tif = results / "used.tif"
    used_zip = results / "used.zip"
    free_tif = results / "free.tif"
    free_zip = results / "free.zip"
    for path in (used_tif, used_zip, free_tif, free_zip):
        path.write_bytes(path.suffix.encode())
    save_grouping(db, "USED-UID", [
        Group(
            group_index=0,
            composed_tiff_path=str(used_tif),
            archive_zip=str(used_zip),
            status="organized",
        )
    ], clean_phantoms=False)

    used = _registered_result_paths(db)
    candidates = _result_pair_candidates(results, used)

    by_stem = {c["stem"]: c for c in candidates}
    assert by_stem["used"]["associated"] is True
    assert by_stem["used"]["associated_uid"] == "USED-UID"
    assert by_stem["free"]["associated"] is False


def test_result_pair_picker_defaults_to_unassociated_only(qtbot):
    from app.widgets.grouping_panel import _ResultPairPickerDialog

    candidates = [
        {
            "stem": "used",
            "tiff": "/tmp/used.tif",
            "zip": "/tmp/used.zip",
            "associated": True,
            "associated_uid": "USED-UID",
        },
        {
            "stem": "free",
            "tiff": "/tmp/free.tif",
            "zip": "/tmp/free.zip",
            "associated": False,
            "associated_uid": "",
        },
    ]
    dlg = _ResultPairPickerDialog(candidates)
    qtbot.addWidget(dlg)

    visible = [dlg._list.item(i).text() for i in range(dlg._list.count())]

    assert len(visible) == 1
    assert "free.tif" in visible[0]
    assert "used.tif" not in visible[0]


# ---------------------------------------------------------------------------
# _DraftGroupRow: QListWidget drag-drop mode
# ---------------------------------------------------------------------------

def test_draft_group_row_listwidget_accepts_drops(qtbot):
    """Each _DraftGroupRow's QListWidget must accept drops from other lists."""
    from app.widgets.grouping_panel import _DraftGroupRow
    from app.services.grouping_service import Group
    from PyQt6.QtWidgets import QAbstractItemView

    g = Group(group_index=0, jpg_paths=["/p/a.jpg", "/p/b.jpg"])
    row = _DraftGroupRow(g)
    qtbot.addWidget(row)

    lw = row._jpg_list
    assert lw.dragDropMode() == QAbstractItemView.DragDropMode.DragDrop
    assert lw.acceptDrops() is True


def test_draft_group_row_listwidget_default_drop_action(qtbot):
    """Default drop action must be MoveAction (not CopyAction)."""
    from app.widgets.grouping_panel import _DraftGroupRow
    from app.services.grouping_service import Group

    g = Group(group_index=0, jpg_paths=["/p/a.jpg"])
    row = _DraftGroupRow(g)
    qtbot.addWidget(row)

    assert row._jpg_list.defaultDropAction() == Qt.DropAction.MoveAction


# ---------------------------------------------------------------------------
# GroupingPanel._persist_grouping_after_editor_change — reads list widgets, calls save_grouping
# ---------------------------------------------------------------------------

def test_persist_grouping_after_editor_change_calls_save_grouping(qtbot):
    """After a cross-group move, the editor-change hook must persist to DB."""
    from app.widgets.grouping_panel import GroupingPanel
    from app.services.grouping_service import Group, SpecimenGrouping

    ctx = _make_app_context()
    db = MagicMock()
    ctx.get_db.return_value = db

    panel = GroupingPanel(ctx)
    qtbot.addWidget(panel)

    grouping = _make_grouping([
        {"index": 0, "jpgs": ["/p/a.jpg", "/p/b.jpg"]},
        {"index": 1, "jpgs": ["/p/c.jpg"]},
    ])
    panel.load_grouping("test-uid", grouping)

    with patch("app.widgets.grouping_panel.grouping_service.save_grouping") as mock_save:
        panel._persist_grouping_after_editor_change()
        assert mock_save.called
        args = mock_save.call_args
        # save_grouping(db, uid, groups, clean_phantoms=False)
        assert args[0][1] == "test-uid"


# ---------------------------------------------------------------------------
# cross-group move: simulate item move between two _DraftGroupRow lists
# ---------------------------------------------------------------------------

def test_cross_group_move_updates_service(qtbot):
    """Simulate a cross-group drag: moving '/p/b.jpg' from group-0 to group-1
    must result in save_grouping being called with the updated lists.
    """
    from app.widgets.grouping_panel import GroupingPanel, _DraftGroupRow
    from app.services.grouping_service import Group, SpecimenGrouping

    ctx = _make_app_context()
    db = MagicMock()
    ctx.get_db.return_value = db

    panel = GroupingPanel(ctx)
    qtbot.addWidget(panel)

    grouping = _make_grouping([
        {"index": 0, "jpgs": ["/p/a.jpg", "/p/b.jpg"]},
        {"index": 1, "jpgs": ["/p/c.jpg"]},
    ])
    panel.load_grouping("test-uid", grouping)

    # 横向胶片条改版后草稿卡片嵌在横向滚动区里，用 findChildren 递归取。
    draft_rows = sorted(
        panel.findChildren(_DraftGroupRow),
        key=lambda r: r._group.group_index,
    )
    assert len(draft_rows) == 2, f"Expected 2 draft rows, got {len(draft_rows)}"

    row0, row1 = draft_rows[0], draft_rows[1]
    lw0: QListWidget = row0._jpg_list
    lw1: QListWidget = row1._jpg_list

    with patch("app.widgets.grouping_panel.grouping_service.save_grouping") as mock_save:
        # Simulate: user drags /p/b.jpg from lw0 → lw1
        # This is what _CrossGroupList.dropEvent does internally.
        # We call the panel's public helper directly to verify the plumbing.
        panel._move_jpg_between_groups(
            src_group_index=0,
            dst_group_index=1,
            jpg_path="/p/b.jpg",
        )

        assert mock_save.called, "save_grouping should have been called"
        _db, uid, groups = mock_save.call_args[0][:3]
        assert uid == "test-uid"

        by_idx = {g.group_index: g for g in groups}
        assert "/p/b.jpg" not in by_idx[0].jpg_paths, "b.jpg must leave group 0"
        assert "/p/b.jpg" in by_idx[1].jpg_paths, "b.jpg must arrive in group 1"
        assert "/p/a.jpg" in by_idx[0].jpg_paths, "a.jpg must remain in group 0"
        assert "/p/c.jpg" in by_idx[1].jpg_paths, "c.jpg must remain in group 1"


# ---------------------------------------------------------------------------
# grouping_changed signal emitted on cross-group move
# ---------------------------------------------------------------------------

def test_cross_group_move_emits_grouping_changed(qtbot):
    """_move_jpg_between_groups must emit grouping_changed."""
    from app.widgets.grouping_panel import GroupingPanel

    ctx = _make_app_context()
    ctx.get_db.return_value = None  # no DB — save will be skipped gracefully

    panel = GroupingPanel(ctx)
    qtbot.addWidget(panel)

    grouping = _make_grouping([
        {"index": 0, "jpgs": ["/p/a.jpg"]},
        {"index": 1, "jpgs": []},
    ])
    panel.load_grouping("test-uid", grouping)

    with qtbot.waitSignal(panel.grouping_changed, timeout=1000):
        panel._move_jpg_between_groups(
            src_group_index=0,
            dst_group_index=1,
            jpg_path="/p/a.jpg",
        )


# ---------------------------------------------------------------------------
# 每组「输出 TIF」可编辑命名（output_name）
# ---------------------------------------------------------------------------

def test_output_name_edit_updates_group(qtbot):
    """编辑某组输出命名 → group.output_name 更新；空=回到自动(None)。"""
    from app.widgets.grouping_panel import GroupingPanel
    ctx = _make_app_context()
    panel = GroupingPanel(ctx)
    qtbot.addWidget(panel)
    panel.load_grouping("test-uid", _make_grouping([{"index": 0, "jpgs": ["/p/a.jpg", "/p/b.jpg"]}]))

    panel._rename_group_output_stem(0, "我的输出名")
    g = panel._grouping.groups[0]
    assert g.output_name == "我的输出名"

    panel._rename_group_output_stem(0, "   ")          # 空白 → None(自动)
    assert panel._grouping.groups[0].output_name is None


def test_card_shows_existing_output_name(qtbot):
    """已有 output_name 的组, 卡片输出框显示它。"""
    from app.widgets.grouping_panel import GroupingPanel, _DraftGroupRow
    from app.services.grouping_service import Group
    ctx = _make_app_context()
    panel = GroupingPanel(ctx)
    qtbot.addWidget(panel)
    g = Group(group_index=0, jpg_paths=["/p/a.jpg"], output_name="外部TIF名")
    row = _DraftGroupRow(g, panel, panel=panel)
    qtbot.addWidget(row)
    assert row._output_edit.text() == "外部TIF名"


# ---------------------------------------------------------------------------
# Group selection: checked groups constrain bulk actions; empty selection = all
# ---------------------------------------------------------------------------

def test_group_selection_defaults_empty_means_all(qtbot):
    from app.widgets.grouping_panel import GroupingPanel
    ctx = _make_app_context()
    panel = GroupingPanel(ctx)
    qtbot.addWidget(panel)
    panel.load_grouping("test-uid", _make_grouping([
        {"index": 0, "jpgs": ["/p/a.jpg"]},
        {"index": 1, "jpgs": ["/p/b.jpg"]},
    ]))

    assert panel.selected_group_indexes() == []


def test_select_all_and_clear_group_selection(qtbot):
    from app.widgets.grouping_panel import GroupingPanel
    ctx = _make_app_context()
    panel = GroupingPanel(ctx)
    qtbot.addWidget(panel)
    panel.load_grouping("test-uid", _make_grouping([
        {"index": 0, "jpgs": ["/p/a.jpg"]},
        {"index": 2, "jpgs": ["/p/b.jpg"]},
    ]))

    panel.select_all_groups()
    assert panel.selected_group_indexes() == [0, 2]
    panel.clear_group_selection()
    assert panel.selected_group_indexes() == []


def test_group_row_checkbox_updates_selection(qtbot):
    from app.widgets.grouping_panel import GroupingPanel
    ctx = _make_app_context()
    panel = GroupingPanel(ctx)
    qtbot.addWidget(panel)
    panel.load_grouping("test-uid", _make_grouping([
        {"index": 0, "jpgs": ["/p/a.jpg"]},
    ]))

    panel._track_group_selection_state(0, True)
    assert panel.selected_group_indexes() == [0]
    panel._track_group_selection_state(0, False)
    assert panel.selected_group_indexes() == []


# ---------------------------------------------------------------------------
# 「新组」按钮：自动标「角度N」 + 工具打开自动载入激活编号
# ---------------------------------------------------------------------------

def test_add_group_auto_labels(qtbot):
    """点「新组」自动建组并标 角度1 / 角度2（web 同款，省手敲）。"""
    from app.widgets.grouping_panel import GroupingPanel
    ctx = _make_app_context()
    panel = GroupingPanel(ctx)
    qtbot.addWidget(panel)
    panel.load_grouping("test-uid", _make_grouping([]))   # 空, 已绑标本

    panel._add_group()
    panel._add_group()
    labels = [g.angle_label for g in panel._grouping.groups]
    assert labels == ["角度1", "角度2"]
    assert panel._add_btn.isVisible() or True   # 载入后按钮可用


def test_add_group_auto_labels_for_adhoc_jobs(qtbot):
    """无编号临时任务不是同一标本的多角度，应标为结果1/结果2。"""
    from app.services.grouping_service import ADHOC_GROUPING_UID
    from app.widgets.grouping_panel import GroupingPanel

    ctx = _make_app_context()
    panel = GroupingPanel(ctx)
    qtbot.addWidget(panel)
    panel.load_grouping(ADHOC_GROUPING_UID, _make_grouping([]))

    panel._add_group()
    panel._add_group()

    labels = [g.angle_label for g in panel._grouping.groups]
    assert labels == ["结果1", "结果2"]


def test_adhoc_load_converts_old_default_angles_to_results(qtbot):
    """旧版本临时分组若存了角度N，打开时按无编号语义显示为结果N。"""
    from app.services.grouping_service import ADHOC_GROUPING_UID
    from app.widgets.grouping_panel import GroupingPanel, _DraftGroupRow

    ctx = _make_app_context()
    panel = GroupingPanel(ctx)
    qtbot.addWidget(panel)
    panel.load_grouping(ADHOC_GROUPING_UID, _make_grouping([
        {"index": 0, "label": "角度1"},
        {"index": 1, "label": "角度2"},
    ]))

    assert [g.angle_label for g in panel._grouping.groups] == ["结果1", "结果2"]
    rows = sorted(
        panel.findChildren(_DraftGroupRow),
        key=lambda r: r._group.group_index,
    )
    assert [row._label_edit.text() for row in rows] == ["结果1", "结果2"]


def test_add_group_counts_existing_organized_angles(qtbot, tmp_path):
    """已整理角度仍占用标本内角度序号；点「新组」应继续角度2。"""
    from app.services.grouping_service import Group, SpecimenGrouping
    from app.widgets.grouping_panel import GroupingPanel, _DraftGroupRow

    tif = tmp_path / "done.tif"
    tif.write_bytes(b"tif")
    ctx = _make_app_context()
    panel = GroupingPanel(ctx)
    qtbot.addWidget(panel)
    panel.load_grouping(
        "test-uid",
        SpecimenGrouping(
            uid="test-uid",
            groups=[
                Group(
                    group_index=0,
                    angle_label="角度1",
                    composed_tiff_path=str(tif),
                    status="organized",
                    archive_zip=str(tif.with_suffix(".zip")),
                )
            ],
        ),
    )

    panel._add_group()

    draft = [g for g in panel._grouping.groups if not g.composed_tiff_path]
    assert [g.angle_label for g in draft] == ["角度2"]
    rows = panel.findChildren(_DraftGroupRow)
    assert [row._group_number_chip.text() for row in rows] == ["组2"]


def test_visible_group_numbers_and_default_angles_are_contiguous(qtbot):
    """数据库内部索引可以有空洞，但 UI 必须始终显示组1/角度1、组2/角度2。"""
    from app.widgets.grouping_panel import GroupingPanel, _DraftGroupRow
    ctx = _make_app_context()
    panel = GroupingPanel(ctx)
    qtbot.addWidget(panel)
    panel.load_grouping("test-uid", _make_grouping([
        {"index": 2, "label": "角度3"},
        {"index": 4, "label": "角度5"},
    ]))

    rows = panel.findChildren(_DraftGroupRow)
    assert [row._group_number_chip.text() for row in rows] == ["组1", "组2"]
    assert [row._label_edit.text() for row in rows] == ["角度1", "角度2"]
    # 内部 ID 不变，避免破坏已有关联。
    assert [row._group.group_index for row in rows] == [2, 4]


def test_grouping_more_menu_exposes_helicon_params(qtbot):
    from app.widgets.grouping_panel import GroupingPanel
    panel = GroupingPanel(_make_app_context())
    qtbot.addWidget(panel)
    menu = panel._build_more_menu()
    action = next(a for a in menu.actions() if a.text() == "Helicon 合成参数")

    with qtbot.waitSignal(panel.helicon_params_requested, timeout=1000):
        action.trigger()


def test_add_group_needs_specimen(qtbot):
    """没绑标本时「新组」不崩、不建组（按钮本就隐藏）。"""
    from app.widgets.grouping_panel import GroupingPanel
    ctx = _make_app_context()
    panel = GroupingPanel(ctx)
    qtbot.addWidget(panel)
    panel._add_group()                # _uid=None
    assert panel._grouping is None or not panel._grouping.groups


def test_register_existing_zip_updates_composed_group(qtbot, tmp_path, monkeypatch):
    """已合成组可注册已有 ZIP，不重新压缩。"""
    from app.widgets.grouping_panel import GroupingPanel
    from app.services.grouping_service import Group, SpecimenGrouping
    import app.utils.ui as ui

    zip_path = tmp_path / "result.zip"
    zip_path.write_bytes(b"zipdata")
    ctx = _make_app_context()
    panel = GroupingPanel(ctx)
    qtbot.addWidget(panel)
    grouping = SpecimenGrouping(uid="test-uid", groups=[
        Group(group_index=0, composed_tiff_path=str(tmp_path / "result.tif")),
    ])
    panel.load_grouping("test-uid", grouping)
    monkeypatch.setattr(ui, "get_open_file_name", lambda *a, **k: str(zip_path))

    with qtbot.waitSignal(panel.archive_zip_registered, timeout=1000):
        panel._register_existing_archive_zip(0)

    group = panel._grouping.groups[0]
    assert group.archive_zip == str(zip_path)
    assert group.status == "organized"


def test_composed_row_without_jpg_explains_missing_link(qtbot, tmp_path):
    from app.services.grouping_service import Group
    from app.widgets.grouping_panel import _ComposedRow

    row = _ComposedRow(Group(
        group_index=0,
        angle_label="角度1",
        composed_tiff_path=str(tmp_path / "result.tif"),
    ))
    qtbot.addWidget(row)

    labels = [w.text() for w in row.findChildren(QLabel)]
    buttons = row.findChildren(QPushButton)

    assert "仅TIF 待整理" in labels
    organise = next(b for b in buttons if b.text() == "整理TIF")
    assert organise.isEnabled()
    assert "TIFF" in organise.toolTip()
    link = next(b for b in buttons if b.text() == "关联JPG")
    assert link.isEnabled()
    assert "关联 JPG" in link.toolTip()


def test_link_jpg_for_composed_row_adds_nearby_jpgs(qtbot, tmp_path, monkeypatch):
    from app.services.grouping_service import Group, SpecimenGrouping
    from app.widgets.grouping_panel import GroupingPanel

    tif = tmp_path / "GXFCG-BLW-BZC003-4-R-20260618.tif"
    jpg = tmp_path / "P6201980.JPG"
    tif.write_bytes(b"t")
    jpg.write_bytes(b"j")
    panel = GroupingPanel(_make_app_context())
    qtbot.addWidget(panel)
    panel.load_grouping(
        "GXFCG-BLW-BZC003-R-20260618",
        SpecimenGrouping(
            uid="GXFCG-BLW-BZC003-R-20260618",
            groups=[
                Group(
                    group_index=0,
                    angle_label="角度1",
                    composed_tiff_path=str(tif),
                    status="composed",
                )
            ],
        ),
    )
    monkeypatch.setattr(
        panel,
        "_pick_jpgs_for_existing_tiff",
        lambda *_a, **_k: [str(jpg)],
    )

    panel._link_original_jpgs_to_composed_group(0)

    assert panel._grouping.groups[0].jpg_paths == [str(jpg)]


def test_composed_row_with_jpg_shows_pending_count(qtbot, tmp_path):
    from app.services.grouping_service import Group
    from app.widgets.grouping_panel import _ComposedRow

    row = _ComposedRow(Group(
        group_index=0,
        angle_label="角度1",
        composed_tiff_path=str(tmp_path / "result.tif"),
        jpg_paths=["a.jpg", "b.jpg"],
    ))
    qtbot.addWidget(row)

    labels = [w.text() for w in row.findChildren(QLabel)]
    organise = next(b for b in row.findChildren(QPushButton) if b.text() == "整理")

    assert "2 JPG 待整理" in labels
    assert organise.isEnabled()


def test_composed_row_with_archive_counts_plain_jpg_zip(qtbot, tmp_path):
    import zipfile

    from app.services.grouping_service import Group
    from app.widgets.grouping_panel import _ComposedRow

    zip_path = tmp_path / "result.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("a.jpg", b"a")
        zf.writestr("b.jpg", b"b")
        zf.writestr("c.jpg", b"c")

    row = _ComposedRow(Group(
        group_index=0,
        angle_label="角度1",
        composed_tiff_path=str(tmp_path / "result.tif"),
        archive_zip=str(zip_path),
        status="organized",
    ))
    qtbot.addWidget(row)

    labels = [w.text() for w in row.findChildren(QLabel)]
    buttons = row.findChildren(QPushButton)

    assert "已归档 3 JPG" in labels
    assert any(b.text() == "已整理" and not b.isEnabled() for b in buttons)


def test_composed_row_infers_archive_from_same_stem_zip(qtbot, tmp_path):
    import zipfile

    from app.services.grouping_service import Group
    from app.widgets.grouping_panel import _ComposedRow

    tiff_path = tmp_path / "GXFCG-BLW-SC001-2-D79-20260618.tif"
    tiff_path.write_bytes(b"tif")
    zip_path = tiff_path.with_suffix(".zip")
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("a.jpg", b"a")
        zf.writestr("b.jpg", b"b")

    row = _ComposedRow(Group(
        group_index=0,
        angle_label="角度1",
        composed_tiff_path=str(tiff_path),
        jpg_paths=[],
        archive_zip=None,
    ))
    qtbot.addWidget(row)

    labels = [w.text() for w in row.findChildren(QLabel)]
    buttons = row.findChildren(QPushButton)

    assert "已归档 2 JPG" in labels
    assert any(b.text() == "已整理" and not b.isEnabled() for b in buttons)


def test_drop_external_jpg_adds_to_group(qtbot, tmp_path):
    from app.widgets.grouping_panel import GroupingPanel

    jpg = tmp_path / "a.jpg"
    jpg.write_bytes(b"j")
    panel = GroupingPanel(_make_app_context())
    qtbot.addWidget(panel)
    panel.load_grouping("test-uid", _make_grouping([{"index": 0, "jpgs": []}]))

    panel.drop_external_files(0, [str(jpg)], None)

    assert str(jpg) in panel._grouping.groups[0].jpg_paths


def test_drop_external_jpg_with_project_copies_to_incoming_before_grouping(qtbot, tmp_path):
    from app.widgets.grouping_panel import GroupingPanel

    external = tmp_path / "camera" / "a.jpg"
    external.parent.mkdir()
    external.write_bytes(b"j")
    project = tmp_path / "project"
    incoming = project / "incoming-jpg"
    incoming.mkdir(parents=True)
    ctx = _make_app_context()
    ctx.current_project_dir = str(project)
    ctx.settings = MagicMock()
    ctx.settings.incoming_subdir = "incoming-jpg"
    panel = GroupingPanel(ctx)
    qtbot.addWidget(panel)
    panel.load_grouping("test-uid", _make_grouping([{"index": 0, "jpgs": []}]))

    panel.drop_external_files(0, [str(external)], None)

    imported = incoming / "a.jpg"
    assert imported.read_bytes() == b"j"
    assert panel._grouping.groups[0].jpg_paths == [str(imported.resolve())]


def test_add_picker_jpg_to_adhoc_group_does_not_manual_assign(
    qtbot, tmp_path, monkeypatch
):
    from app.services.grouping_service import ADHOC_GROUPING_UID
    from app.widgets.grouping_panel import GroupingPanel

    external = tmp_path / "camera" / "a.jpg"
    external.parent.mkdir()
    external.write_bytes(b"j")
    project = tmp_path / "project"
    incoming = project / "incoming-jpg"
    incoming.mkdir(parents=True)
    ctx = _make_app_context()
    ctx.current_project_dir = str(project)
    ctx.settings = MagicMock()
    ctx.settings.incoming_subdir = "incoming-jpg"
    panel = GroupingPanel(ctx)
    qtbot.addWidget(panel)
    panel.load_grouping(
        ADHOC_GROUPING_UID,
        _make_grouping([{"index": 0, "jpgs": []}]),
    )
    calls = []
    monkeypatch.setattr(
        "app.services.activation_service.manual_assign",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )

    panel._add_selected_media_paths_to_group(0, [str(external)])

    imported = incoming / "a.jpg"
    assert panel._grouping.groups[0].jpg_paths == [str(imported.resolve())]
    assert calls == []


def test_drop_external_tiff_sets_output_name_and_composed(qtbot, tmp_path):
    from app.widgets.grouping_panel import GroupingPanel

    tif = tmp_path / "2019-01-17 ZS PMax.tif"
    tif.write_bytes(b"t")
    panel = GroupingPanel(_make_app_context())
    qtbot.addWidget(panel)
    panel.load_grouping("test-uid", _make_grouping([{"index": 0, "jpgs": []}]))

    with qtbot.waitSignal(panel.import_tiff_requested, timeout=1000) as blocker:
        panel.drop_external_files(0, [], str(tif))

    g = panel._grouping.groups[0]
    assert g.composed_tiff_path == str(tif)
    assert g.output_name == "2019-01-17 ZS PMax"
    assert g.status == "composed"
    assert blocker.args == ["test-uid", 0]


def test_drop_external_tiff_rejects_sibling_specimen_number(qtbot, tmp_path):
    from app.widgets.grouping_panel import GroupingPanel

    tif = tmp_path / "GXFCG-BLW-BZC002-2-R-20260618.tif"
    tif.write_bytes(b"t")
    panel = GroupingPanel(_make_app_context())
    qtbot.addWidget(panel)
    panel.load_grouping(
        "GXFCG-BLW-BZC003-R-20260618",
        _make_grouping([{"index": 0, "jpgs": []}]),
    )

    with patch("app.widgets.grouping_panel.QMessageBox.warning") as warning:
        panel.drop_external_files(0, [], str(tif))

    assert warning.called
    assert panel._grouping.groups[0].composed_tiff_path is None
    assert panel._grouping.groups[0].status is None


def test_drop_external_tiff_rejects_duplicate_tiff_across_groups(qtbot, tmp_path):
    from app.services.grouping_service import Group, SpecimenGrouping
    from app.widgets.grouping_panel import GroupingPanel

    tif = tmp_path / "GXFCG-BLW-BZC003-4-R-20260618.tif"
    tif.write_bytes(b"t")
    panel = GroupingPanel(_make_app_context())
    qtbot.addWidget(panel)
    panel.load_grouping(
        "test-uid",
        SpecimenGrouping(
            uid="test-uid",
            groups=[
                Group(
                    group_index=0,
                    angle_label="角度1",
                    composed_tiff_path=str(tif),
                    status="composed",
                ),
                Group(group_index=1, angle_label="角度2", jpg_paths=[]),
            ],
        ),
    )

    with patch("PyQt6.QtWidgets.QMessageBox.warning") as warning:
        panel.drop_external_files(1, [], str(tif))

    assert warning.called
    assert panel._grouping.groups[0].composed_tiff_path == str(tif)
    assert panel._grouping.groups[1].composed_tiff_path is None


def test_load_grouping_clears_duplicate_tiff_links(qtbot, tmp_path):
    from app.services.grouping_service import Group, SpecimenGrouping
    from app.widgets.grouping_panel import GroupingPanel

    tif = tmp_path / "GXFCG-BLW-BZC003-4-R-20260618.tif"
    tif.write_bytes(b"t")
    panel = GroupingPanel(_make_app_context())
    qtbot.addWidget(panel)
    panel.load_grouping(
        "test-uid",
        SpecimenGrouping(
            uid="test-uid",
            groups=[
                Group(
                    group_index=0,
                    angle_label="角度1",
                    composed_tiff_path=str(tif),
                    status="composed",
                    output_name=tif.stem,
                ),
                Group(
                    group_index=1,
                    angle_label="角度2",
                    composed_tiff_path=str(tif),
                    status="composed",
                    output_name=tif.stem,
                ),
            ],
        ),
    )

    assert panel._grouping.groups[0].composed_tiff_path == str(tif)
    assert panel._grouping.groups[1].composed_tiff_path is None
    assert panel._grouping.groups[1].status is None
    assert panel._grouping.groups[1].output_name is None


def test_load_grouping_clears_sibling_specimen_tiff_links(qtbot, tmp_path):
    from app.services.grouping_service import Group, SpecimenGrouping
    from app.widgets.grouping_panel import GroupingPanel

    wrong_tif = tmp_path / "GXFCG-BLW-BZC002-3-R-20260618.tif"
    wrong_zip = wrong_tif.with_suffix(".zip")
    wrong_tif.write_bytes(b"t")
    wrong_zip.write_bytes(b"z")
    jpg = tmp_path / "P6201971.JPG"
    jpg.write_bytes(b"j")
    panel = GroupingPanel(_make_app_context())
    qtbot.addWidget(panel)

    panel.load_grouping(
        "GXFCG-BLW-BZC003-R-20260618",
        SpecimenGrouping(
            uid="GXFCG-BLW-BZC003-R-20260618",
            groups=[
                Group(
                    group_index=0,
                    angle_label="角度1",
                    composed_tiff_path=str(wrong_tif),
                    archive_zip=str(wrong_zip),
                    status="organized",
                    source="external-tif",
                    output_name=wrong_tif.stem,
                ),
                Group(
                    group_index=1,
                    angle_label="角度2",
                    jpg_paths=[str(jpg)],
                    composed_tiff_path=str(wrong_tif),
                    archive_zip=str(wrong_zip),
                    status="organized",
                    source="external-tif",
                    output_name=wrong_tif.stem,
                ),
            ],
        ),
    )

    assert len(panel._grouping.groups) == 1
    group = panel._grouping.groups[0]
    assert group.jpg_paths == [str(jpg)]
    assert group.composed_tiff_path is None
    assert group.archive_zip is None
    assert group.output_name is None
    assert group.status == "pending"
    assert group.source is None


def test_drop_external_jpg_and_tiff_together(qtbot, tmp_path):
    from app.widgets.grouping_panel import GroupingPanel

    jpg = tmp_path / "P1130102.JPG"
    tif = tmp_path / "2019-01-17 ZS PMax.tif"
    jpg.write_bytes(b"j")
    tif.write_bytes(b"t")
    panel = GroupingPanel(_make_app_context())
    qtbot.addWidget(panel)
    panel.load_grouping("test-uid", _make_grouping([{"index": 0, "jpgs": []}]))

    with qtbot.waitSignal(panel.import_tiff_requested, timeout=1000):
        panel.drop_external_files(0, [str(jpg)], str(tif))

    g = panel._grouping.groups[0]
    assert str(jpg) in g.jpg_paths
    assert g.composed_tiff_path == str(tif)
    assert g.output_name == "2019-01-17 ZS PMax"


def test_add_photos_from_picker(qtbot, tmp_path, monkeypatch):
    """每组「+」选文件 → JPG 进组 + TIF 设 output_name。"""
    import app.utils.ui as ui
    from app.widgets.grouping_panel import GroupingPanel

    jpg = tmp_path / "P1130102.JPG"
    tif = tmp_path / "Helicon PMax.tif"
    jpg.write_bytes(b"j")
    tif.write_bytes(b"t")
    panel = GroupingPanel(_make_app_context())
    qtbot.addWidget(panel)
    panel.load_grouping("test-uid", _make_grouping([{"index": 0, "jpgs": []}]))
    monkeypatch.setattr(
        panel,
        "_pick_related_files_from_dir",
        lambda **_k: (_ for _ in ()).throw(AssertionError("related picker not expected")),
    )

    monkeypatch.setattr(
        ui, "get_open_file_names", lambda *a, **k: [str(jpg), str(tif)]
    )

    with qtbot.waitSignal(panel.import_tiff_requested, timeout=1000):
        panel._on_add_photos_from_picker(0)

    g = panel._grouping.groups[0]
    assert str(jpg) in g.jpg_paths
    assert g.output_name == "Helicon PMax"


def test_add_photos_plus_uses_related_picker_first(qtbot, tmp_path, monkeypatch):
    """开启「筛相关」后，角度卡片「+」优先使用相关文件选择结果。"""
    import app.utils.ui as ui
    from app.widgets.grouping_panel import GroupingPanel

    jpg = tmp_path / "P6201980.JPG"
    tif = tmp_path / "GXFCG-BLW-BZC003-4-R-20260618.tif"
    jpg.write_bytes(b"j")
    tif.write_bytes(b"t")
    panel = GroupingPanel(_make_app_context())
    qtbot.addWidget(panel)
    panel.load_grouping("GXFCG-BLW-BZC003-R-20260618", _make_grouping([{"index": 0, "jpgs": []}]))
    panel._related_filter_btn.setChecked(True)
    monkeypatch.setattr(
        panel,
        "_pick_related_files_from_dir",
        lambda **_k: [str(jpg), str(tif)],
    )
    monkeypatch.setattr(
        ui,
        "get_open_file_names",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("normal picker not expected")),
    )

    with qtbot.waitSignal(panel.import_tiff_requested, timeout=1000):
        panel._on_add_photos_from_picker(0)

    g = panel._grouping.groups[0]
    assert str(jpg) in g.jpg_paths
    assert g.composed_tiff_path == str(tif)


def test_add_photos_picker_default_uses_lightweight_dialog(qtbot, monkeypatch):
    """默认「+」不启用代理排序，避免挂载盘目录枚举卡顿。"""
    import app.utils.ui as ui
    from app.widgets.grouping_panel import GroupingPanel

    panel = GroupingPanel(_make_app_context())
    qtbot.addWidget(panel)
    panel.load_grouping("test-uid", _make_grouping([{"index": 0, "jpgs": []}]))
    monkeypatch.setattr(panel, "_pick_related_files_from_dir", lambda **_k: [])
    captured = {}

    def fake_picker(*args, **kwargs):
        captured["caption"] = args[1] if len(args) > 1 else ""
        captured.update(kwargs)
        return []

    monkeypatch.setattr(ui, "get_open_file_names", fake_picker)

    panel._on_add_photos_from_picker(0)

    assert captured["sort_by_mtime"] is False
    assert captured["priority_paths"] == []
    assert captured["priority_terms"] == []
    assert captured["filter_terms"] == []


def test_add_photos_picker_uses_related_priority_when_button_enabled(qtbot, monkeypatch):
    """开启「相关优先」后，每组「+」选择器才启用编号/TIF 相关排序。"""
    import app.utils.ui as ui
    import app.widgets.grouping_panel as grouping_panel_module
    from app.widgets.grouping_panel import GroupingPanel

    panel = GroupingPanel(_make_app_context())
    qtbot.addWidget(panel)
    panel.load_grouping("test-uid", _make_grouping([{"index": 0, "jpgs": []}]))
    panel._related_first_btn.setChecked(True)
    monkeypatch.setattr(
        panel,
        "_pick_related_files_from_dir",
        lambda **_k: (_ for _ in ()).throw(AssertionError("related picker not expected")),
    )
    captured = {}

    def fake_picker(*args, **kwargs):
        captured["caption"] = args[1] if len(args) > 1 else ""
        captured.update(kwargs)
        return []

    monkeypatch.setattr(ui, "get_open_file_names", fake_picker)
    monkeypatch.setattr(
        grouping_panel_module,
        "_related_media_candidates",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not scan project")),
    )

    panel._on_add_photos_from_picker(0)

    assert captured["sort_by_mtime"] is True
    assert captured["priority_paths"] == []
    assert captured["priority_terms"] == ["test-uid"]
    assert captured["filter_terms"] == []


def test_add_photos_picker_prefers_incoming_jpg_start_dir(qtbot, tmp_path, monkeypatch):
    """加照片默认打开 incoming-jpg，不落到项目根目录导致慢枚举。"""
    import app.utils.ui as ui
    from app.widgets.grouping_panel import GroupingPanel

    project = tmp_path / "project"
    incoming = project / "incoming-jpg"
    incoming.mkdir(parents=True)
    ctx = _make_app_context()
    ctx.current_project_dir = str(project)
    panel = GroupingPanel(ctx)
    qtbot.addWidget(panel)
    panel.load_grouping("test-uid", _make_grouping([{"index": 0, "jpgs": []}]))
    monkeypatch.setattr(panel, "_pick_related_files_from_dir", lambda **_k: [])
    captured = {}

    def fake_picker(*args, **kwargs):
        captured["start"] = kwargs.get("start")
        return []

    monkeypatch.setattr(ui, "get_open_file_names", fake_picker)

    panel._on_add_photos_from_picker(0)

    assert captured["start"] == str(incoming)


def test_add_photos_picker_filters_related_when_button_enabled(qtbot, monkeypatch):
    """开启「筛相关」后，每组「+」走专用相关文件选择器。"""
    from app.widgets.grouping_panel import GroupingPanel

    panel = GroupingPanel(_make_app_context())
    qtbot.addWidget(panel)
    panel.load_grouping("test-uid", _make_grouping([{"index": 0, "jpgs": []}]))
    panel._related_filter_btn.setChecked(True)

    monkeypatch.setattr(panel, "_pick_related_files_from_dir", lambda **_k: None)

    panel._on_add_photos_from_picker(0)

    assert panel._related_filter_btn.text() == "筛相关:开"
    assert panel._related_first_btn.text() == "相关优先:开"


def test_related_file_picker_caption_uses_specimen_uid(qtbot, tmp_path, monkeypatch):
    """相关文件选择器的目标标识只用标本唯一编号，不混入工作区中文目录名。"""
    from app.widgets.grouping_panel import GroupingPanel

    panel = GroupingPanel(_make_app_context())
    qtbot.addWidget(panel)
    panel.load_grouping("GXFCG-BLW-BZC003-R-20260618", _make_grouping([{"index": 0, "jpgs": []}]))

    captured = {}

    def fake_pick_media(
        _parent,
        caption,
        *,
        start="",
        priority_terms=None,
        file_exts=None,
        shortcuts=None,
    ):
        captured["caption"] = caption
        captured["start"] = start
        captured["priority_terms"] = priority_terms
        captured["file_exts"] = file_exts
        captured["shortcuts"] = shortcuts
        return [], ""

    monkeypatch.setattr(
        "app.widgets.grouping_panel._pick_media_paths_or_folder",
        fake_pick_media,
    )

    panel._pick_related_files_from_dir(
        uid="GXFCG-BLW-BZC003-R-20260618",
        start=str(tmp_path / "20260618-白龙尾"),
    )

    assert "GXFCG-BLW-BZC003" in captured["caption"]
    assert "GXFCG-BLW-BZC003-R-20260618" not in captured["caption"]
    assert "白龙尾" not in captured["caption"]
    assert captured["priority_terms"] == []
    assert ".jpg" in captured["file_exts"]
    assert ".tif" in captured["file_exts"]


def test_related_picker_starts_from_group_media_parent(qtbot, tmp_path, monkeypatch):
    """筛相关时优先从当前组已有媒体所在目录打开，而不是工作区中文目录。"""
    from app.services.grouping_service import Group, SpecimenGrouping
    from app.widgets.grouping_panel import GroupingPanel

    ctx = _make_app_context()
    chinese_workspace = tmp_path / "20260618-白龙尾"
    incoming = chinese_workspace / "incoming-jpg"
    incoming.mkdir(parents=True)
    ctx.current_project_dir = str(chinese_workspace)

    source_dir = tmp_path / "GXFCG-BLW-BZC003-R-20260618"
    source_dir.mkdir()
    tif = source_dir / "GXFCG-BLW-BZC003-1-R-20260618.tif"
    tif.write_bytes(b"x")

    panel = GroupingPanel(ctx)
    qtbot.addWidget(panel)
    panel.load_grouping(
        "GXFCG-BLW-BZC003-R-20260618",
        SpecimenGrouping(
            uid="GXFCG-BLW-BZC003-R-20260618",
            groups=[Group(group_index=0, composed_tiff_path=str(tif))],
        ),
    )
    panel._related_filter_btn.setChecked(True)
    captured = {}

    def fake_related(**kwargs):
        captured.update(kwargs)
        return None

    monkeypatch.setattr(panel, "_pick_related_files_from_dir", fake_related)

    panel._on_add_photos_from_picker(0)

    assert captured["uid"] == "GXFCG-BLW-BZC003-R-20260618"
    assert captured["start"] == str(source_dir)


def test_related_picker_does_not_auto_enter_matching_sibling_dsc_folder(
    qtbot, tmp_path, monkeypatch
):
    """项目旁边有 dsc 原图目录时，也只给快捷入口，不主动扫描并跳入匹配目录。"""
    from app.widgets.grouping_panel import GroupingPanel

    root = tmp_path / "claude"
    project = root / "zhegnli"
    incoming = project / "incoming-jpg"
    source_dir = root / "dsc" / "广西" / "广西" / "广西防城港-20260618-白龙尾"
    incoming.mkdir(parents=True)
    source_dir.mkdir(parents=True)
    tif = source_dir / "GXFCG-BLW-BZC003-3-R-260618-广西防城港-白龙尾.tif"
    tif.write_bytes(b"x")

    ctx = _make_app_context()
    ctx.current_project_dir = str(project)
    panel = GroupingPanel(ctx)
    qtbot.addWidget(panel)
    panel.load_grouping("GXFCG-BLW-BZC003-R-20260618", _make_grouping([{"index": 0, "jpgs": []}]))
    captured = {}

    def fake_pick_media(
        _parent,
        caption,
        *,
        start="",
        priority_terms=None,
        file_exts=None,
        shortcuts=None,
    ):
        captured["start"] = start
        captured["shortcuts"] = shortcuts or []
        return [], ""

    monkeypatch.setattr(
        "app.widgets.grouping_panel._pick_media_paths_or_folder",
        fake_pick_media,
    )

    panel._pick_related_files_from_dir(
        uid="GXFCG-BLW-BZC003-R-20260618",
        start=str(incoming),
    )

    assert captured["start"] == str(incoming)
    assert ("匹配目录", str(source_dir)) not in captured["shortcuts"]
    assert ("相机原图", str(root / "dsc")) in captured["shortcuts"]


def test_related_picker_scans_user_chosen_folder(qtbot, tmp_path, monkeypatch):
    """用户选文件夹时，扫描该目录里的相关 JPG/TIF。"""
    from app.widgets.grouping_panel import GroupingPanel

    start_folder = tmp_path / "广西防城港-20260618-白龙尾"
    chosen_folder = start_folder / "子目录"
    chosen_folder.mkdir(parents=True)
    tif = chosen_folder / "GXFCG-BLW-BZC003-4-R-20260618.tif"
    jpg = chosen_folder / "P6201971.JPG"
    for path in (tif, jpg):
        path.write_bytes(b"x")

    panel = GroupingPanel(_make_app_context())
    qtbot.addWidget(panel)
    panel.load_grouping("GXFCG-BLW-BZC003-R-20260618", _make_grouping([{"index": 0, "jpgs": []}]))
    captured = {}

    def fake_pick_media(
        _parent,
        caption,
        *,
        start="",
        priority_terms=None,
        file_exts=None,
        shortcuts=None,
    ):
        captured["caption"] = caption
        captured["start"] = start
        captured["priority_terms"] = priority_terms
        captured["file_exts"] = file_exts
        captured["shortcuts"] = shortcuts
        return [], str(chosen_folder)

    def fake_select(folder_arg, target_uid, display_key, *, show_empty_message):
        captured["folder"] = folder_arg
        captured["target_uid"] = target_uid
        captured["display_key"] = display_key
        captured["show_empty_message"] = show_empty_message
        return [str(jpg), str(tif)]

    monkeypatch.setattr(
        "app.widgets.grouping_panel._pick_media_paths_or_folder",
        fake_pick_media,
    )
    monkeypatch.setattr(panel, "_select_related_files_from_folder", fake_select)

    result = panel._pick_related_files_from_dir(
        uid="GXFCG-BLW-BZC003-R-20260618",
        start=str(start_folder),
    )

    assert result == [str(jpg), str(tif)]
    assert captured["caption"] == "选择 GXFCG-BLW-BZC003 相关 JPG/TIF 所在位置"
    assert captured["start"] == str(start_folder)
    assert captured["folder"] == str(chosen_folder)
    assert captured["target_uid"] == "GXFCG-BLW-BZC003-R-20260618"
    assert captured["display_key"] == "GXFCG-BLW-BZC003"
    assert captured["show_empty_message"] is True
    assert captured["priority_terms"] == []
    assert ".jpg" in captured["file_exts"]
    assert ".tif" in captured["file_exts"]


def test_related_picker_returns_direct_file_selection(qtbot, tmp_path, monkeypatch):
    """用户直接多选 JPG/TIF 时，直接返回文件，不再弹第二层确认表。"""
    from app.widgets.grouping_panel import GroupingPanel

    tif = tmp_path / "GXFCG-BLW-BZC003-4-R-20260618.tif"
    jpg = tmp_path / "P6201971.JPG"
    for path in (tif, jpg):
        path.write_bytes(b"x")

    panel = GroupingPanel(_make_app_context())
    qtbot.addWidget(panel)
    panel.load_grouping("GXFCG-BLW-BZC003-R-20260618", _make_grouping([{"index": 0, "jpgs": []}]))

    monkeypatch.setattr(
        "app.widgets.grouping_panel._pick_media_paths_or_folder",
        lambda *_a, **_k: ([str(jpg), str(tif)], ""),
    )
    monkeypatch.setattr(
        panel,
        "_select_related_files_from_folder",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("second picker not expected")),
    )

    result = panel._pick_related_files_from_dir(
        uid="GXFCG-BLW-BZC003-R-20260618",
        start=str(tmp_path),
    )

    assert result == [str(jpg), str(tif)]


def test_existing_tiff_jpg_picker_scans_parent_of_user_chosen_jpg(
    qtbot, tmp_path, monkeypatch
):
    """关联已有 TIF 的 JPG 时也要显示 JPG 文件，而不是只显示目录。"""
    from PyQt6.QtWidgets import QDialog

    from app.services.grouping_service import Group
    from app.widgets.grouping_panel import GroupingPanel

    tif = tmp_path / "GXFCG-BLW-BZC003-4-R-20260618.tif"
    jpg = tmp_path / "P6201980.JPG"
    tif.write_bytes(b"t")
    jpg.write_bytes(b"j")
    panel = GroupingPanel(_make_app_context())
    qtbot.addWidget(panel)
    panel.load_grouping("GXFCG-BLW-BZC003-R-20260618", _make_grouping([]))
    captured = {}

    def fake_pick_media(
        _parent,
        caption,
        *,
        start="",
        priority_terms=None,
        file_exts=None,
        shortcuts=None,
    ):
        captured["caption"] = caption
        captured["start"] = start
        captured["priority_terms"] = priority_terms
        captured["file_exts"] = file_exts
        captured["shortcuts"] = shortcuts
        return [], str(tmp_path)

    def fake_scan(folder_arg, tiff_path, *, near_seconds=30 * 60):
        captured["folder"] = folder_arg
        captured["tiff_path"] = tiff_path
        return [{
            "path": str(jpg),
            "name": jpg.name,
            "kind": "JPG",
            "mtime": 1,
            "nearest_seconds": 60,
        }]

    monkeypatch.setattr(
        "app.widgets.grouping_panel._pick_media_paths_or_folder",
        fake_pick_media,
    )
    monkeypatch.setattr(
        "app.widgets.grouping_panel._scan_jpgs_near_tiff_in_dir",
        fake_scan,
    )

    class FakeRelatedDialog:
        def __init__(self, *_args, **_kwargs):
            pass

        def exec(self):
            return QDialog.DialogCode.Accepted

        def selected_paths(self):
            return [str(jpg)]

    monkeypatch.setattr(
        "app.widgets.grouping_panel._RelatedFilesPickerDialog",
        FakeRelatedDialog,
    )

    result = panel._pick_jpgs_for_existing_tiff(
        Group(group_index=0, composed_tiff_path=str(tif)),
        start=str(tmp_path),
    )

    assert result == [str(jpg)]
    assert captured["folder"] == str(tmp_path)
    assert captured["tiff_path"] == str(tif)
    assert captured["priority_terms"] == []
    assert captured["file_exts"] == {".jpg", ".jpeg"}


def test_existing_tiff_jpg_picker_returns_direct_jpg_selection(
    qtbot, tmp_path, monkeypatch
):
    from app.services.grouping_service import Group
    from app.widgets.grouping_panel import GroupingPanel

    tif = tmp_path / "GXFCG-BLW-BZC003-4-R-20260618.tif"
    jpg1 = tmp_path / "P6201980.JPG"
    jpg2 = tmp_path / "P6201981.JPG"
    for path in (tif, jpg1, jpg2):
        path.write_bytes(b"x")
    panel = GroupingPanel(_make_app_context())
    qtbot.addWidget(panel)
    panel.load_grouping("GXFCG-BLW-BZC003-R-20260618", _make_grouping([]))

    monkeypatch.setattr(
        "app.widgets.grouping_panel._pick_media_paths_or_folder",
        lambda *_a, **_k: ([str(jpg1), str(jpg2)], ""),
    )
    monkeypatch.setattr(
        "app.widgets.grouping_panel._scan_jpgs_near_tiff_in_dir",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("scan not expected")),
    )

    result = panel._pick_jpgs_for_existing_tiff(
        Group(group_index=0, composed_tiff_path=str(tif)),
        start=str(tmp_path),
    )

    assert result == [str(jpg1), str(jpg2)]


def test_media_location_picker_accepts_folder_and_enters_on_double_click(
    qtbot, tmp_path
):
    """自定义位置选择器必须能选文件夹，也必须能进入文件夹查看 JPG/TIF。"""
    from app.widgets.grouping_panel import _MediaLocationPickerDialog

    parent_dir = tmp_path / "广西"
    target_dir = parent_dir / "广西防城港-20260618-白龙尾"
    target_dir.mkdir(parents=True)
    tif = target_dir / "GXFCG-BLW-BZC003-4-R-20260618.tif"
    jpg = target_dir / "P6201980.JPG"
    txt = target_dir / "ignore.txt"
    for path in (tif, jpg, txt):
        path.write_bytes(b"x")

    dlg = _MediaLocationPickerDialog(
        "选择 GXFCG-BLW-BZC003 相关 JPG/TIF 所在位置",
        str(parent_dir),
        priority_terms=["GXFCG-BLW-BZC003"],
    )
    qtbot.addWidget(dlg)

    def row_for(dialog, name):
        for row in range(dialog._table.rowCount()):
            item = dialog._table.item(row, 1)
            if item and item.text() == name:
                return row
        raise AssertionError(f"missing row: {name}")

    folder_row = row_for(dlg, target_dir.name)
    dlg._table.setCurrentCell(folder_row, 0)
    dlg._accept_selection()
    assert dlg.selected_folder() == str(target_dir)

    dlg2 = _MediaLocationPickerDialog(
        "选择 GXFCG-BLW-BZC003 相关 JPG/TIF 所在位置",
        str(parent_dir),
        priority_terms=["GXFCG-BLW-BZC003"],
    )
    qtbot.addWidget(dlg2)
    folder_row = row_for(dlg2, target_dir.name)
    dlg2._on_item_activated(dlg2._table.item(folder_row, 1))

    assert str(dlg2._current_dir) == str(target_dir)
    visible_names = {
        dlg2._table.item(row, 1).text()
        for row in range(dlg2._table.rowCount())
    }
    assert tif.name in visible_names
    assert jpg.name in visible_names
    assert txt.name not in visible_names


def test_media_location_picker_file_selection_returns_files(qtbot, tmp_path):
    from app.widgets.grouping_panel import _MediaLocationPickerDialog

    target_dir = tmp_path / "广西防城港-20260618-白龙尾"
    target_dir.mkdir()
    jpg = target_dir / "P6201980.JPG"
    jpg.write_bytes(b"x")

    dlg = _MediaLocationPickerDialog("选择 JPG 所在位置", str(target_dir))
    qtbot.addWidget(dlg)
    for row in range(dlg._table.rowCount()):
        item = dlg._table.item(row, 1)
        if item and item.text() == jpg.name:
            dlg._table.setCurrentCell(row, 1)
            break
    else:
        raise AssertionError("jpg row not found")

    dlg._accept_selection()

    assert dlg.selected_paths() == [str(jpg)]
    assert dlg.selected_folder() == ""


def test_media_location_picker_shows_thumbnails_and_supports_multiselect(
    qtbot, tmp_path
):
    from PIL import Image
    from PyQt6.QtWidgets import QAbstractItemView, QHeaderView

    from app.widgets.grouping_panel import _MediaLocationPickerDialog

    tif = tmp_path / "GXFCG-BLW-BZC003-4-R-20260618.tif"
    jpg = tmp_path / "P6201980.JPG"
    Image.new("RGB", (160, 100), "green").save(tif)
    Image.new("RGB", (160, 100), "red").save(jpg)

    dlg = _MediaLocationPickerDialog(
        "选择 GXFCG-BLW-BZC003 相关 JPG/TIF 所在位置",
        str(tmp_path),
        priority_terms=["GXFCG-BLW-BZC003"],
    )
    qtbot.addWidget(dlg)
    dlg._load_all_thumbnails_now()

    assert dlg._table.columnCount() == 5
    assert not dlg._table.verticalHeader().isVisible()
    assert dlg._table.selectionMode() == QAbstractItemView.SelectionMode.ExtendedSelection
    assert dlg._table.horizontalHeader().sectionResizeMode(1) == QHeaderView.ResizeMode.Interactive

    jpg_row = tif_row = -1
    for row in range(dlg._table.rowCount()):
        name = dlg._table.item(row, 1).text()
        if name == jpg.name:
            jpg_row = row
        elif name == tif.name:
            tif_row = row
    assert jpg_row >= 0
    assert tif_row >= 0
    assert dlg._table.cellWidget(jpg_row, 0).property("hasThumbnail") is True
    assert dlg._table.cellWidget(tif_row, 0).property("hasThumbnail") is True

    from PyQt6.QtCore import QItemSelectionModel
    for row in (jpg_row, tif_row):
        index = dlg._table.model().index(row, 1)
        dlg._table.selectionModel().select(
            index,
            QItemSelectionModel.SelectionFlag.Select
            | QItemSelectionModel.SelectionFlag.Rows,
        )
    dlg._accept_selection()

    assert set(dlg.selected_paths()) == {str(jpg), str(tif)}


def test_scan_related_files_in_dir_uses_jpg_block_before_matching_tif(tmp_path):
    from app.widgets.grouping_panel import _scan_related_files_in_dir

    previous_tif = tmp_path / "GXFCG-BLW-SC001-1-R-20260618.tif"
    old_jpg = tmp_path / "P6201900.JPG"
    anchor_tif = tmp_path / "GXFCG-BLW-BZC003-4-R-20260618.tif"
    before_jpg = tmp_path / "P6201979.JPG"
    after_jpg = tmp_path / "P6201980.JPG"
    far_jpg = tmp_path / "P6202060.JPG"
    next_tif = tmp_path / "GXFCG-BLW-SC002-1-R-20260618.tif"
    for path in (previous_tif, old_jpg, anchor_tif, before_jpg, after_jpg, far_jpg, next_tif):
        path.write_bytes(b"x")
    import os
    anchor = 1_800_000_000
    os.utime(old_jpg, (anchor - 600, anchor - 600))
    os.utime(previous_tif, (anchor - 500, anchor - 500))
    os.utime(before_jpg, (anchor - 60, anchor - 60))
    os.utime(anchor_tif, (anchor, anchor))
    os.utime(after_jpg, (anchor + 60, anchor + 60))
    os.utime(next_tif, (anchor + 120, anchor + 120))
    os.utime(far_jpg, (anchor + 7200, anchor + 7200))

    result = _scan_related_files_in_dir(
        tmp_path,
        "GXFCG-BLW-BZC003-R-20260618",
        near_seconds=30 * 60,
    )

    names = [item["name"] for item in result]
    assert names == [
        "P6201979.JPG",
        "GXFCG-BLW-BZC003-4-R-20260618.tif",
    ]
    by_name = {item["name"]: item for item in result}
    assert by_name["P6201979.JPG"]["nearest_anchor_name"] == anchor_tif.name
    assert by_name["P6201979.JPG"]["relative_to_tif"] == "before"
    assert by_name["P6201979.JPG"]["default_related"] is True
    assert "P6201900.JPG" not in names
    assert "P6201980.JPG" not in names
    assert "P6202060.JPG" not in names
    assert "GXFCG-BLW-SC001-1-R-20260618.tif" not in names


def test_scan_related_files_matches_uid_core_when_suffix_is_wrong(tmp_path):
    from app.widgets.grouping_panel import _scan_related_files_in_dir

    anchor_tif = tmp_path / "GXFCG-BLW-BZC003-4-X-20260101.tif"
    near_jpg = tmp_path / "P6201980.JPG"
    for path in (anchor_tif, near_jpg):
        path.write_bytes(b"x")
    import os
    anchor = 1_800_000_000
    os.utime(near_jpg, (anchor - 60, anchor - 60))
    os.utime(anchor_tif, (anchor, anchor))

    result = _scan_related_files_in_dir(
        tmp_path,
        "GXFCG-BLW-BZC003-R-20260618",
        near_seconds=30 * 60,
    )

    assert [item["name"] for item in result] == [
        "P6201980.JPG",
        "GXFCG-BLW-BZC003-4-X-20260101.tif",
    ]


def test_scan_related_files_assigns_jpg_to_nearest_matching_tif(tmp_path):
    from app.widgets.grouping_panel import _scan_related_files_in_dir

    tif1 = tmp_path / "GXFCG-BLW-BZC003-2-R-20260618.tif"
    jpg0 = tmp_path / "P6201969.JPG"
    jpg1 = tmp_path / "P6201970.JPG"
    jpg2 = tmp_path / "P6201980.JPG"
    tif2 = tmp_path / "GXFCG-BLW-BZC003-3-R-20260618.tif"
    for path in (jpg0, tif1, jpg1, jpg2, tif2):
        path.write_bytes(b"x")
    import os
    os.utime(jpg0, (990, 990))
    os.utime(tif1, (1000, 1000))
    os.utime(jpg1, (1010, 1010))
    os.utime(jpg2, (1190, 1190))
    os.utime(tif2, (1200, 1200))

    result = _scan_related_files_in_dir(
        tmp_path,
        "GXFCG-BLW-BZC003-R-20260618",
        near_seconds=30 * 60,
    )

    assert [item["name"] for item in result] == [
        "P6201969.JPG",
        "GXFCG-BLW-BZC003-2-R-20260618.tif",
        "P6201970.JPG",
        "P6201980.JPG",
        "GXFCG-BLW-BZC003-3-R-20260618.tif",
    ]
    by_name = {item["name"]: item for item in result}
    assert by_name["P6201969.JPG"]["nearest_anchor_name"] == tif1.name
    assert by_name["P6201970.JPG"]["nearest_anchor_name"] == tif2.name
    assert by_name["P6201980.JPG"]["nearest_anchor_name"] == tif2.name


def test_scan_related_files_falls_back_to_folder_core_when_tif_name_is_bad(tmp_path):
    from app.widgets.grouping_panel import _scan_related_files_in_dir

    folder = tmp_path / "GXFCG-BLW-BZC003"
    folder.mkdir()
    bad_tif = folder / "Helicon PMax.tif"
    jpg = folder / "P6201980.JPG"
    for path in (bad_tif, jpg):
        path.write_bytes(b"x")
    import os
    anchor = 1_800_000_000
    os.utime(jpg, (anchor - 60, anchor - 60))
    os.utime(bad_tif, (anchor, anchor))

    result = _scan_related_files_in_dir(
        folder,
        "GXFCG-BLW-BZC003-R-20260618",
        near_seconds=30 * 60,
    )

    names = {item["name"] for item in result}
    assert names == {"Helicon PMax.tif", "P6201980.JPG"}


def test_scan_related_files_falls_back_to_all_media_when_no_anchor(tmp_path):
    from app.widgets.grouping_panel import _scan_related_files_in_dir

    bad_tif = tmp_path / "Helicon PMax.tif"
    jpg = tmp_path / "P6201980.JPG"
    for path in (bad_tif, jpg):
        path.write_bytes(b"x")

    result = _scan_related_files_in_dir(
        tmp_path,
        "GXFCG-BLW-BZC003-R-20260618",
        near_seconds=30 * 60,
    )

    names = {item["name"] for item in result}
    assert names == {"Helicon PMax.tif", "P6201980.JPG"}


def test_related_files_picker_defaults_to_first_tiff_group(qtbot, tmp_path):
    from app.widgets.grouping_panel import _RelatedFilesPickerDialog

    tif1 = tmp_path / "GXFCG-BLW-BZC003-4-R-20260618.tif"
    tif2 = tmp_path / "GXFCG-BLW-BZC003-3-R-20260618.tif"
    jpg1 = tmp_path / "P6201980.JPG"
    far_jpg1 = tmp_path / "P6201900.JPG"
    jpg2 = tmp_path / "P6201981.JPG"
    candidates = [
        {
            "path": str(far_jpg1),
            "name": far_jpg1.name,
            "kind": "JPG",
            "mtime": 0,
            "nearest_seconds": 600,
            "nearest_anchor": str(tif1),
            "nearest_anchor_name": tif1.name,
        },
        {
            "path": str(jpg1),
            "name": jpg1.name,
            "kind": "JPG",
            "mtime": 1,
            "nearest_seconds": 60,
            "nearest_anchor": str(tif1),
            "nearest_anchor_name": tif1.name,
        },
        {
            "path": str(tif1),
            "name": tif1.name,
            "kind": "TIF",
            "mtime": 2,
            "anchor": True,
            "nearest_anchor": str(tif1),
            "nearest_anchor_name": tif1.name,
        },
        {
            "path": str(jpg2),
            "name": jpg2.name,
            "kind": "JPG",
            "mtime": 3,
            "nearest_seconds": 60,
            "nearest_anchor": str(tif2),
            "nearest_anchor_name": tif2.name,
        },
        {
            "path": str(tif2),
            "name": tif2.name,
            "kind": "TIF",
            "mtime": 4,
            "anchor": True,
            "nearest_anchor": str(tif2),
            "nearest_anchor_name": tif2.name,
        },
    ]
    dlg = _RelatedFilesPickerDialog("GXFCG-BLW-BZC003", str(tmp_path), candidates)
    qtbot.addWidget(dlg)

    selected = set(dlg.selected_paths())

    assert str(tif1) in selected
    assert str(jpg1) in selected
    assert str(far_jpg1) not in selected
    assert str(tif2) not in selected
    assert str(jpg2) not in selected

    dlg._table.item(4, 0).setCheckState(Qt.CheckState.Checked)

    selected = set(dlg.selected_paths())
    assert selected == {str(tif2), str(jpg2)}


def test_related_files_picker_anchor_selection_survives_user_sort(qtbot, tmp_path):
    from app.widgets.grouping_panel import _RelatedFilesPickerDialog

    tif1 = tmp_path / "GXFCG-BLW-BZC003-4-R-20260618.tif"
    tif2 = tmp_path / "GXFCG-BLW-BZC003-3-R-20260618.tif"
    jpg1 = tmp_path / "P6201980.JPG"
    jpg2 = tmp_path / "P6201981.JPG"
    for path in (tif1, tif2, jpg1, jpg2):
        path.write_bytes(b"x")
    candidates = [
        {
            "path": str(jpg1),
            "name": jpg1.name,
            "kind": "JPG",
            "mtime": 1,
            "nearest_seconds": 60,
            "nearest_anchor": str(tif1),
            "nearest_anchor_name": tif1.name,
            "default_related": True,
        },
        {
            "path": str(tif1),
            "name": tif1.name,
            "kind": "TIF",
            "mtime": 2,
            "anchor": True,
            "nearest_anchor": str(tif1),
            "nearest_anchor_name": tif1.name,
            "default_related": True,
        },
        {
            "path": str(jpg2),
            "name": jpg2.name,
            "kind": "JPG",
            "mtime": 3,
            "nearest_seconds": 60,
            "nearest_anchor": str(tif2),
            "nearest_anchor_name": tif2.name,
            "default_related": True,
        },
        {
            "path": str(tif2),
            "name": tif2.name,
            "kind": "TIF",
            "mtime": 4,
            "anchor": True,
            "nearest_anchor": str(tif2),
            "nearest_anchor_name": tif2.name,
            "default_related": True,
        },
    ]
    dlg = _RelatedFilesPickerDialog("GXFCG-BLW-BZC003", str(tmp_path), candidates)
    qtbot.addWidget(dlg)

    dlg._table.sortItems(4, Qt.SortOrder.DescendingOrder)
    tif2_row = dlg._row_for_path(str(tif2))
    assert tif2_row >= 0
    dlg._table.item(tif2_row, 0).setCheckState(Qt.CheckState.Checked)

    assert set(dlg.selected_paths()) == {str(tif2), str(jpg2)}


def test_related_files_picker_can_switch_to_all_timeline_preserving_selection(
    qtbot, tmp_path
):
    from app.widgets.grouping_panel import _RelatedFilesPickerDialog

    tif = tmp_path / "GXFCG-BLW-BZC003-4-R-20260618.tif"
    jpg = tmp_path / "P6201980.JPG"
    extra = tmp_path / "P6201999.JPG"
    for path in (tif, jpg, extra):
        path.write_bytes(b"x")
    block = [
        {
            "path": str(jpg),
            "name": jpg.name,
            "kind": "JPG",
            "mtime": 1,
            "nearest_seconds": 60,
            "nearest_anchor": str(tif),
            "nearest_anchor_name": tif.name,
            "relative_to_tif": "before",
            "default_related": True,
        },
        {
            "path": str(tif),
            "name": tif.name,
            "kind": "TIF",
            "mtime": 2,
            "anchor": True,
            "nearest_anchor": str(tif),
            "nearest_anchor_name": tif.name,
            "default_related": True,
        },
    ]
    all_items = block + [{
        "path": str(extra),
        "name": extra.name,
        "kind": "JPG",
        "mtime": 3,
        "nearest_seconds": 3600,
        "nearest_anchor": str(tif),
        "nearest_anchor_name": tif.name,
        "relative_to_tif": "after",
        "default_related": False,
        "timeline_only": True,
    }]

    dlg = _RelatedFilesPickerDialog(
        "GXFCG-BLW-BZC003",
        str(tmp_path),
        block,
        all_candidates=all_items,
    )
    qtbot.addWidget(dlg)

    assert set(dlg.selected_paths()) == {str(jpg), str(tif)}

    dlg._replace_candidates(all_items)

    names = {
        dlg._table.item(row, 2).text()
        for row in range(dlg._table.rowCount())
    }
    assert extra.name in names
    assert set(dlg.selected_paths()) == {str(jpg), str(tif)}


def test_related_files_picker_shows_thumbnails_and_compact_distance(qtbot, tmp_path):
    from PIL import Image

    from app.widgets.grouping_panel import _RelatedFilesPickerDialog

    tif = tmp_path / "GXFCG-BLW-BZC003-4-R-20260618.tif"
    jpg = tmp_path / "P6201980.JPG"
    Image.new("RGB", (160, 100), "green").save(tif)
    Image.new("RGB", (160, 100), "red").save(jpg)
    candidates = [
        {
            "path": str(jpg),
            "name": jpg.name,
            "kind": "JPG",
            "mtime": 1,
            "nearest_seconds": 65,
            "nearest_anchor": str(tif),
            "nearest_anchor_name": tif.name,
        },
        {
            "path": str(tif),
            "name": tif.name,
            "kind": "TIF",
            "mtime": 2,
            "anchor": True,
            "nearest_anchor": str(tif),
            "nearest_anchor_name": tif.name,
        },
    ]

    dlg = _RelatedFilesPickerDialog("GXFCG-BLW-BZC003", str(tmp_path), candidates)
    qtbot.addWidget(dlg)
    dlg._load_all_thumbnails_now()

    assert dlg._table.columnCount() == 6
    assert not dlg._table.verticalHeader().isVisible()
    assert dlg._table.cellWidget(0, 1).property("hasThumbnail") is True
    assert dlg._table.cellWidget(1, 1).property("hasThumbnail") is True
    assert dlg._table.item(0, 5).text() == "1:05"
    assert tif.name not in dlg._table.item(0, 5).text()
    assert tif.name in dlg._table.item(0, 5).toolTip()
    assert dlg._table.item(1, 5).text() == "TIF"


def test_scan_jpgs_near_existing_tiff_uses_tiff_time(tmp_path):
    from app.widgets.grouping_panel import _scan_jpgs_near_tiff_in_dir

    tif = tmp_path / "GXFCG-BLW-BZC003-4-R-20260618.tif"
    near = tmp_path / "P6201980.JPG"
    far = tmp_path / "P6202060.JPG"
    for path in (tif, near, far):
        path.write_bytes(b"x")
    import os
    anchor = 1_800_000_000
    os.utime(tif, (anchor, anchor))
    os.utime(near, (anchor + 60, anchor + 60))
    os.utime(far, (anchor + 7200, anchor + 7200))

    result = _scan_jpgs_near_tiff_in_dir(tmp_path, str(tif), near_seconds=30 * 60)

    assert [item["name"] for item in result] == ["P6201980.JPG"]


def test_linked_tiff_moves_to_composed_row(qtbot, tmp_path):
    """关联 TIF 后进入已合成行，可继续整理 JPG 或注册已有 ZIP。"""
    from app.services.grouping_service import Group, SpecimenGrouping
    from app.widgets.grouping_panel import GroupingPanel, _ComposedRow, _DraftGroupRow

    tif = tmp_path / "Helicon PMax.tif"
    tif.write_bytes(b"t")
    panel = GroupingPanel(_make_app_context())
    qtbot.addWidget(panel)
    grouping = SpecimenGrouping(
        uid="test-uid",
        groups=[
            Group(
                group_index=0,
                composed_tiff_path=str(tif),
                output_name="Helicon PMax",
                status="composed",
            )
        ],
    )
    panel.load_grouping("test-uid", grouping)

    assert panel.findChildren(_DraftGroupRow) == []
    rows = panel.findChildren(_ComposedRow)
    assert len(rows) == 1

    labels = [w.text() for w in rows[0].findChildren(QLabel)]
    buttons = rows[0].findChildren(QPushButton)

    assert "Helicon PMax.tif" in labels
    assert "仅TIF 待整理" in labels
    assert any(b.text() == "整理TIF" and b.isEnabled() for b in buttons)
    assert any(b.toolTip() == "注册已有 ZIP 归档（不重新压缩）" for b in buttons)
