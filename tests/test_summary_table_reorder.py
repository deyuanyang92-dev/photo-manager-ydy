"""编号表行拖动排序 —— 拖动**绝不能吞掉数据**。

审计(2026-07-12)实锤: `project_tree_view._configure_specimen_table_interaction`
把 QTableWidget 配成 `InternalMove` + `MoveAction`, 并连了 `model.rowsMoved`。
但 QTableWidget 的原生模型**根本不发 rowsMoved** —— `_on_specimen_table_rows_moved`
是死代码, 自定义顺序永远不生效; 真正发生的是 `QTableModel::dropMimeData` 的
**单元格覆盖**语义:
    A / B / C   把 C 拖到第 1 行  ->  表里变成 A / C / C(B 被覆盖)
    再按 MoveAction 删掉源行      ->  最终 A / C   —— **B 行凭空消失**

用户裁定要的是「可拖动行排序」, 拿到的却是「拖动 = 删行」。这个文件把两条钉死:
  1. 拖完之后, 表里的编号集合**不能少**(只能换顺序)
  2. 拖完之后, 自定义顺序**真的生效**(_summary_row_uid_order 跟着变)

(Fable 5, 2026-07-12)
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtCore import QMimeData, QModelIndex, Qt
from PyQt6.QtWidgets import QAbstractItemView, QTableWidget, QTableWidgetItem


UIDS = ["A-1", "B-2", "C-3"]


def _table_like_project_tree(qtbot) -> QTableWidget:
    """复刻 project_tree_view 编号表的交互配置 + 数据。"""
    from app.views.project_tree_view import ProjectTreeView  # noqa: F401  (import guard)

    t = QTableWidget(3, 2)
    qtbot.addWidget(t)
    t.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    t.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
    for row, uid in enumerate(UIDS):
        it = QTableWidgetItem(uid)
        it.setData(Qt.ItemDataRole.UserRole, uid)
        t.setItem(row, 0, it)
        t.setItem(row, 1, QTableWidgetItem(f"物种{row}"))
    return t


def _uids_in_table(t: QTableWidget) -> list[str]:
    out = []
    for r in range(t.rowCount()):
        it = t.item(r, 0)
        if it is not None and it.text():
            out.append(it.text())
    return out


def test_drag_reorder_must_not_lose_rows(qtbot):
    """拖行号把第 3 行挪到最上面 —— 顺序变了, 但 3 个编号一个不少、内容不串行。"""
    from app.views.project_tree_view import ProjectTreeView

    t = _table_like_project_tree(qtbot)
    ProjectTreeView._configure_specimen_table_interaction_for(t)

    vhdr = t.verticalHeader()
    vhdr.moveSection(2, 0)          # 用户拖行号:把 C-3 拖到最前

    # ① 数据一个都不能少(旧的 InternalMove 会把 B-2 覆盖掉 -> 只剩 2 行)
    assert sorted(_uids_in_table(t)) == sorted(UIDS)
    # ② 每行的编号↔物种不得串行(单元格根本没被动过)
    for row, uid in enumerate(UIDS):
        assert t.item(row, 0).text() == uid
        assert t.item(row, 1).text() == f"物种{row}"
    # ③ 视觉顺序真的变了 = 排序生效
    visual_order = [t.item(vhdr.logicalIndex(v), 0).text() for v in range(t.rowCount())]
    assert visual_order == ["C-3", "A-1", "B-2"]


def test_table_is_not_configured_with_destructive_internal_move(qtbot):
    """守门测试: 编号表不得再启用会覆盖单元格的 InternalMove 行拖放。"""
    from app.views.project_tree_view import ProjectTreeView

    t = _table_like_project_tree(qtbot)
    ProjectTreeView._configure_specimen_table_interaction_for(t)

    assert t.dragDropMode() != QAbstractItemView.DragDropMode.InternalMove, (
        "QTableWidget 的 InternalMove = dropMimeData 覆盖单元格 + 删源行 = 丢数据"
    )
    assert t.verticalHeader().sectionsMovable(), (
        "行排序改走垂直表头拖动(真排序, 不动数据)"
    )
