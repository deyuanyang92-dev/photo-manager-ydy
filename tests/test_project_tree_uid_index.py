"""Tests for project-tree UID index keyboard selection behavior."""
from __future__ import annotations

import pytest

pytest.importorskip("PyQt6")

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QAbstractItemView

from app.widgets.project_tree_uid_index import ProjectTreeUidIndex


def test_uid_index_supports_ctrl_a_and_shift_range(qtbot):
    """编号 rail 应兼容 Windows 列表习惯：Ctrl+A 与 Shift 范围选择."""
    from PyQt6.QtTest import QTest

    index = ProjectTreeUidIndex()
    qtbot.addWidget(index)
    index.set_entries([
        {"uid": "GXFCG-BLW-SC001-D79-20260618", "abbrev": "GXFCG-BLW-SC001", "count": 1},
        {"uid": "GXFCG-BLW-SC002-RD79-20260618", "abbrev": "GXFCG-BLW-SC002", "count": 2},
        {"uid": "GXFCG-BLW-PGC001-D-20260618", "abbrev": "GXFCG-BLW-PGC001", "count": 5},
    ])
    lst = index._list
    assert lst.selectionMode() == QAbstractItemView.SelectionMode.ExtendedSelection
    lst.show()
    lst.setFocus()
    lst.setCurrentRow(0)

    QTest.keyClick(lst, Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier)
    assert [lst.item(i).isSelected() for i in range(lst.count())] == [True, True, True]

    lst.clearSelection()
    lst.setCurrentRow(0)
    QTest.keyClick(lst, Qt.Key.Key_Down, Qt.KeyboardModifier.ShiftModifier)
    assert [lst.item(i).isSelected() for i in range(lst.count())] == [True, True, False]
