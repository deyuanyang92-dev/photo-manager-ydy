"""tests/test_grouping_panel.py — GroupingPanel cross-group JPG drag-drop tests."""
from __future__ import annotations

import sqlite3
from unittest.mock import MagicMock, patch, call

import pytest

from PyQt6.QtCore import Qt, QMimeData, QPoint
from PyQt6.QtWidgets import QApplication, QListWidget


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

    # Get the two _DraftGroupRow widgets from the content layout
    draft_rows = [
        panel._content_lay.itemAt(i).widget()
        for i in range(panel._content_lay.count())
        if isinstance(panel._content_lay.itemAt(i).widget(), _DraftGroupRow)
    ]
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
