"""第 12 版 command 智能指挥台浮层契约 (Opus 2026-07-16)。

三态渲染（态1 同项目采样点 / 态2 继续上次+最近项目 / 态3 搜索分组）、死路径可见、
键盘 ↑↓/Enter/Esc。headless（offscreen）。
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtCore import QEvent, Qt
from PyQt6.QtGui import QKeyEvent
from PyQt6.QtWidgets import QApplication, QPushButton

from app.widgets.workspace_command_popup import SwitchRow, WorkspaceCommandPopup


@pytest.fixture(scope="module", autouse=True)
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def _row(kind, label, path, root, current=False, exists=True, n=0, pinned=False):
    return SwitchRow(
        kind=kind, label=label, full_label=label, path=path, root=root,
        is_current=current, exists=exists, specimen_count=n,
        last_opened=None, pinned=pinned,
    )


def _key(popup, key):
    ev = QKeyEvent(QEvent.Type.KeyPress, key, Qt.KeyboardModifier.NoModifier)
    popup.keyPressEvent(ev)


def test_switchrow_holds_all_fields():
    r = _row("station", "日出海湾", "/p/盐城2026/日出海湾", "/p/盐城2026", current=True, n=42)
    assert r.kind == "station"
    assert r.is_current and r.exists
    assert r.specimen_count == 42
    assert r.pinned is False


def test_state1_shows_sibling_stations():
    pop = WorkspaceCommandPopup()
    pop.set_data(
        current_root="/p/盐城2026",
        stations=[
            _row("station", "日出海湾", "/p/盐城2026/日出海湾", "/p/盐城2026", current=True, n=42),
            _row("station", "月亮湾", "/p/盐城2026/月亮湾", "/p/盐城2026", n=0),
        ],
        recents=[_row("station", "北港岛", "/q/黄海/北港岛", "/q/黄海", n=17)],
        all_projects=[],
    )
    labels = [r.label for r in pop.visible_rows()]
    assert "日出海湾" in labels and "月亮湾" in labels     # 主区=同项目采样点
    assert "北港岛" in labels                              # 最近段也在


def test_state2_no_project_shows_continue_last():
    pop = WorkspaceCommandPopup()
    pop.set_data(
        current_root=None,
        stations=[],
        recents=[_row("station", "日出海湾", "/p/盐城2026/日出海湾", "/p/盐城2026", n=42)],
        all_projects=[_row("project", "盐城2026", "/p/盐城2026", "/p/盐城2026")],
    )
    rows = pop.visible_rows()
    assert rows and rows[0].label == "日出海湾"            # 首行=继续上次
    assert any(r.kind == "project" for r in rows)          # 最近项目段


def test_enter_row_emits_entered(qtbot):
    pop = WorkspaceCommandPopup()
    pop.set_data(
        current_root="/p/盐城2026",
        stations=[_row("station", "月亮湾", "/p/盐城2026/月亮湾", "/p/盐城2026")],
        recents=[], all_projects=[],
    )
    with qtbot.waitSignal(pop.entered, timeout=500) as sig:
        pop.activate_row(pop.visible_rows()[0])
    assert sig.args[0] == "/p/盐城2026/月亮湾"


def test_search_filters_and_groups():
    pop = WorkspaceCommandPopup()
    pop.set_data(
        current_root=None, stations=[],
        recents=[_row("station", "红礁", "/q/黄海/红礁", "/q/黄海", n=12)],
        all_projects=[
            _row("project", "黄海航次", "/q/黄海", "/q/黄海"),
            _row("project", "盐城2026", "/p/盐城2026", "/p/盐城2026"),
        ],
    )
    pop._search.setText("黄海")
    labels = [r.label for r in pop.visible_rows()]
    assert "黄海航次" in labels and "红礁" in labels        # 项目名 + 采样点路径均命中
    assert "盐城2026" not in labels                         # 非匹配不出现
    kinds = [r.kind for r in pop.visible_rows()]
    assert kinds.index("project") < kinds.index("station")  # 项目组在采样点组前


def test_dead_path_shown_not_filtered():
    pop = WorkspaceCommandPopup()
    pop.set_data(
        current_root="/p/盐城2026",
        stations=[_row("station", "月亮湾", "/p/盐城2026/月亮湾", "/p/盐城2026")],
        recents=[_row("station", "断了的盘", "/dead/x", "/dead", exists=False)],
        all_projects=[],
    )
    rows = pop.visible_rows()
    assert any((not r.exists) and r.label == "断了的盘" for r in rows)


def test_pinned_row_sorts_first():
    pop = WorkspaceCommandPopup()
    pop.set_data(
        current_root="/p/盐城2026",
        stations=[
            _row("station", "月亮湾", "/p/盐城2026/月亮湾", "/p/盐城2026"),
            _row("station", "日出海湾", "/p/盐城2026/日出海湾", "/p/盐城2026", pinned=True),
        ],
        recents=[], all_projects=[],
    )
    labels = [r.label for r in pop.visible_rows()]
    assert labels[0] == "日出海湾"                          # ★收藏置顶


def test_arrow_enter_navigates(qtbot):
    pop = WorkspaceCommandPopup()
    pop.set_data(
        current_root="/p/盐城2026",
        stations=[
            _row("station", "日出海湾", "/p/盐城2026/日出海湾", "/p/盐城2026", current=True),
            _row("station", "月亮湾", "/p/盐城2026/月亮湾", "/p/盐城2026"),
        ],
        recents=[], all_projects=[],
    )
    _key(pop, Qt.Key.Key_Down)                             # 选到第 2 行
    with qtbot.waitSignal(pop.entered, timeout=500) as sig:
        _key(pop, Qt.Key.Key_Enter)
    assert sig.args[0] == "/p/盐城2026/月亮湾"


def test_toggle_pin_emits_path(qtbot):
    pop = WorkspaceCommandPopup()
    pop.set_data(
        current_root="/p/盐城2026",
        stations=[_row("station", "月亮湾", "/p/盐城2026/月亮湾", "/p/盐城2026")],
        recents=[], all_projects=[],
    )
    pin = pop.findChild(QPushButton, "CommandRowPin")
    assert pin is not None
    with qtbot.waitSignal(pop.toggle_pin, timeout=500) as sig:
        pin.click()
    assert sig.args[0] == "/p/盐城2026/月亮湾"
