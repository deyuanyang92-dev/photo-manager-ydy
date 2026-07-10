"""tests/test_project_card_grid.py — 卡片多选 / 汇总 / 封面信号."""
from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("PyQt6")

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QMouseEvent
from PyQt6.QtCore import QPointF, QPoint

from app.widgets.project_card import ProjectCardGrid


def test_card_grid_multi_select_and_summarize(qtbot, tmp_path):
    grid = ProjectCardGrid()
    qtbot.addWidget(grid)
    grid.show()

    a = tmp_path / "a"
    b = tmp_path / "b"
    a.mkdir()
    b.mkdir()
    grid.set_entries([
        {"name": "A", "directory": str(a)},
        {"name": "B", "directory": str(b)},
    ])
    assert len(grid._cards) == 2

    received: list[list[str]] = []
    grid.summarize_requested.connect(lambda dirs: received.append(list(dirs)))

    grid.set_selected_directories([str(a), str(b)])
    assert set(grid.selected_directories()) == {str(a), str(b)}
    assert grid._btn_summarize.isEnabled()
    grid._btn_summarize.click()
    assert received and set(received[0]) == {str(a), str(b)}

    grid.clear_selection()
    assert grid.selected_directories() == []
    assert not grid._btn_summarize.isEnabled()


def test_card_grid_filter_hides_non_matching(qtbot, tmp_path):
    grid = ProjectCardGrid()
    qtbot.addWidget(grid)
    a = tmp_path / "alpha"
    b = tmp_path / "beta"
    a.mkdir()
    b.mkdir()
    grid.set_entries([
        {"name": "Alpha", "directory": str(a)},
        {"name": "Beta", "directory": str(b)},
    ])
    grid.set_filter_text("alp")
    assert grid._cards[str(a)].isHidden() is False
    assert grid._cards[str(b)].isHidden() is True
    grid.select_all()
    assert grid.selected_directories() == [str(a)]


def test_card_double_click_enters(qtbot, tmp_path):
    from PyQt6.QtGui import QMouseEvent
    from PyQt6.QtCore import QPointF

    grid = ProjectCardGrid()
    qtbot.addWidget(grid)
    a = tmp_path / "a"
    a.mkdir()
    grid.set_entries([{"name": "A", "directory": str(a)}])
    entered: list[str] = []
    grid.enter_requested.connect(lambda d: entered.append(d))
    card = grid._cards[str(a)]
    ev = QMouseEvent(
        QMouseEvent.Type.MouseButtonDblClick,
        QPointF(10, 10),
        QPointF(10, 10),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    card.mouseDoubleClickEvent(ev)
    assert entered == [str(a)]


def test_card_ctrl_click_toggles_selection(qtbot, tmp_path):
    grid = ProjectCardGrid()
    qtbot.addWidget(grid)
    grid.show()
    a = tmp_path / "a"
    b = tmp_path / "b"
    a.mkdir()
    b.mkdir()
    grid.set_entries([
        {"name": "A", "directory": str(a)},
        {"name": "B", "directory": str(b)},
    ])
    card_a = grid._cards[str(a)]
    card_b = grid._cards[str(b)]

    ev = QMouseEvent(
        QMouseEvent.Type.MouseButtonPress,
        QPointF(10, 10),
        QPointF(10, 10),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    card_a.mousePressEvent(ev)
    assert grid.selected_directories() == [str(a)]

    ev2 = QMouseEvent(
        QMouseEvent.Type.MouseButtonPress,
        QPointF(10, 10),
        QPointF(10, 10),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.ControlModifier,
    )
    card_b.mousePressEvent(ev2)
    assert set(grid.selected_directories()) == {str(a), str(b)}
