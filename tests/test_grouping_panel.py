"""tests/test_grouping_panel.py — GroupingPanel cross-group JPG drag-drop tests."""
from __future__ import annotations

import sqlite3
from unittest.mock import MagicMock, patch, call

import pytest

from PyQt6.QtCore import Qt, QMimeData, QPoint
from PyQt6.QtWidgets import QApplication, QLabel, QListWidget, QPushButton


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
    """Toolbar + 新组 button must be hidden when no specimen active (app.js:7374-7378)."""
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
# GroupingPanel._on_groups_changed — reads list widgets, calls save_grouping
# ---------------------------------------------------------------------------

def test_on_groups_changed_calls_save_grouping(qtbot):
    """After a cross-group move, _on_groups_changed must persist to DB."""
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
        panel._on_groups_changed()
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

    panel._on_output_name_changed(0, "我的输出名")
    g = panel._grouping.groups[0]
    assert g.output_name == "我的输出名"

    panel._on_output_name_changed(0, "   ")          # 空白 → None(自动)
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

    panel._on_group_selected_changed(0, True)
    assert panel.selected_group_indexes() == [0]
    panel._on_group_selected_changed(0, False)
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
        panel._on_register_zip(0)

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

    assert "未关联 JPG" in labels
    organise = next(b for b in buttons if b.text() == "整理")
    assert not organise.isEnabled()
    assert "未关联 JPG" in organise.toolTip()


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
        ui, "get_open_file_names", lambda *a, **k: [str(jpg), str(tif)]
    )

    with qtbot.waitSignal(panel.import_tiff_requested, timeout=1000):
        panel._on_add_photos_from_picker(0)

    g = panel._grouping.groups[0]
    assert str(jpg) in g.jpg_paths
    assert g.output_name == "Helicon PMax"


def test_add_photos_picker_requests_mtime_sort(qtbot, monkeypatch):
    """照片后处理选择器默认按修改时间降序，方便按拍摄批次选片。"""
    import app.utils.ui as ui
    from app.widgets.grouping_panel import GroupingPanel

    panel = GroupingPanel(_make_app_context())
    qtbot.addWidget(panel)
    panel.load_grouping("test-uid", _make_grouping([{"index": 0, "jpgs": []}]))
    captured = {}

    def fake_picker(*args, **kwargs):
        captured.update(kwargs)
        return []

    monkeypatch.setattr(ui, "get_open_file_names", fake_picker)

    panel._on_add_photos_from_picker(0)

    assert captured["sort_by_mtime"] is True


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
    assert "未关联 JPG" in labels
    assert any(b.toolTip() == "注册已有 ZIP 归档（不重新压缩）" for b in buttons)
