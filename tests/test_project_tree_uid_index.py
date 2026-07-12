"""Tests for project-tree UID index keyboard selection behavior."""
from __future__ import annotations

import pytest

pytest.importorskip("PyQt6")

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QAbstractItemView

from app.widgets.project_tree_uid_index import ProjectTreeUidIndex


def test_uid_index_keeps_single_navigation_selection(qtbot):
    """编号索引只负责跳转，不应形成没有批量动作的假多选。"""
    from PyQt6.QtTest import QTest

    index = ProjectTreeUidIndex()
    qtbot.addWidget(index)
    index.set_entries([
        {"uid": "GXFCG-BLW-SC001-D79-20260618", "abbrev": "GXFCG-BLW-SC001", "count": 1},
        {"uid": "GXFCG-BLW-SC002-RD79-20260618", "abbrev": "GXFCG-BLW-SC002", "count": 2},
        {"uid": "GXFCG-BLW-PGC001-D-20260618", "abbrev": "GXFCG-BLW-PGC001", "count": 5},
    ])
    lst = index._list
    assert lst.selectionMode() == QAbstractItemView.SelectionMode.SingleSelection
    lst.show()
    lst.setFocus()
    lst.setCurrentRow(0)

    QTest.keyClick(lst, Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier)
    assert sum(lst.item(i).isSelected() for i in range(lst.count())) == 1

    lst.clearSelection()
    lst.setCurrentRow(0)
    QTest.keyClick(lst, Qt.Key.Key_Down, Qt.KeyboardModifier.ShiftModifier)
    assert sum(lst.item(i).isSelected() for i in range(lst.count())) == 1
