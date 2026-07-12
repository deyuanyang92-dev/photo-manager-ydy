"""项目树的「死按键」—— 用户 2026-07-13：「我进入某个文件目录，选择也进入不了」。

实测复现（offscreen 脚本）：

    打开页面后自动选中的节点数: 2 ['断面A', '断面B']
    「设为当前拍摄目录」按钮 enabled = False
    按钮文字 = 多选时不进入拍照

根因：``_reload_project_tree`` 在加载完成后**替用户全选了所有拍摄目录**（为了让右侧汇总
立刻有数），而多选状态下「设为当前拍摄目录」是**故意禁用**的。用户什么都还没点，按钮就
已经是死的 —— 一个显眼的大绿按钮，点不动，也没人告诉他为什么。

契约：
1. 打开页面 → **单选**第一个拍摄目录（不是全选）；进入按钮必须是活的。
2. 用户主动 Ctrl/Shift 多选 → 进入按钮仍然可用，作用于**主选中项**（anchor），
   按钮文字说清进的是哪一个。多选的意义是「看汇总」，不该让「进入」变砖。
"""
from __future__ import annotations

import json
import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtCore import QItemSelectionModel

from app.app_context import AppContext
from app.views.project_tree_view import ProjectTreeView


@pytest.fixture
def ctx(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "app.services.project_service.default_user_projects_json_path",
        lambda: str(tmp_path / "user_projects.json"),
    )
    c = AppContext()
    c.settings.project_tree_root = None
    c.settings.project_tree_view_mode = "all"
    return c


def _mk_workspace(parent: Path, name: str) -> Path:
    ws = parent / name
    (ws / "_data").mkdir(parents=True, exist_ok=True)
    (ws / "_data" / "project.db").write_bytes(b"")
    return ws


def _seed_two_workspaces(tmp_path: Path) -> Path:
    proj = tmp_path / "航次2026"
    proj.mkdir()
    _mk_workspace(proj, "断面A")
    _mk_workspace(proj, "断面B")
    (tmp_path / "user_projects.json").write_text(
        json.dumps(
            {"version": 1, "projects": [{"name": "航次2026", "directory": str(proj)}]},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return proj


def _find_item(view: ProjectTreeView, name: str):
    def _walk(item):
        if item.text(0) == name:
            return item
        for i in range(item.childCount()):
            hit = _walk(item.child(i))
            if hit is not None:
                return hit
        return None

    for i in range(view._tree.topLevelItemCount()):
        hit = _walk(view._tree.topLevelItem(i))
        if hit is not None:
            return hit
    return None


def test_page_open_does_not_auto_multi_select(qtbot, tmp_path, ctx):
    """打开页面不得替用户全选 —— 全选会让「设为当前拍摄目录」直接变死按键。"""
    _seed_two_workspaces(tmp_path)

    view = ProjectTreeView(ctx)
    qtbot.addWidget(view)
    view.on_activate()

    selected = view._tree.selectedItems()
    assert len(selected) <= 1, (
        f"打开页面就自动多选了 {len(selected)} 个: "
        f"{[i.text(0) for i in selected]} —— 进入按钮会因此禁用"
    )
    assert view._btn_enter.isEnabled(), "刚打开页面, 进入按钮就是死的"
    view.teardown() if hasattr(view, "teardown") else None


def test_enter_button_stays_alive_when_multi_selecting(qtbot, tmp_path, ctx):
    """用户主动多选看汇总时, 进入按钮仍然可用 —— 作用于主选中项, 不许变砖。"""
    _seed_two_workspaces(tmp_path)

    view = ProjectTreeView(ctx)
    qtbot.addWidget(view)
    view.on_activate()

    a = _find_item(view, "断面A")
    b = _find_item(view, "断面B")
    assert a is not None and b is not None

    view._tree.clearSelection()   # 清掉打开页面时的初始单选(否则算 3 个)
    a.setSelected(True)
    b.setSelected(True)
    view._tree.setCurrentItem(b, 0, QItemSelectionModel.SelectionFlag.NoUpdate)
    view._update_detail_panel_for_selected_project()

    assert len(view._tree.selectedItems()) == 2
    assert view._btn_enter.isEnabled(), "多选时进入按钮不该变成死按键"
    assert "断面" in view._btn_enter.text(), (
        f"按钮该说清进的是哪一个, 现在是: {view._btn_enter.text()!r}"
    )


def test_enter_targets_the_anchor_not_the_first_selected(qtbot, tmp_path, ctx):
    """多选时, 按钮说进哪个就得进哪个 —— 不能界面写「断面B」却进了「断面A」。"""
    _seed_two_workspaces(tmp_path)

    view = ProjectTreeView(ctx)
    qtbot.addWidget(view)
    view.on_activate()

    a = _find_item(view, "断面A")
    b = _find_item(view, "断面B")
    view._tree.clearSelection()
    a.setSelected(True)
    b.setSelected(True)
    view._tree.setCurrentItem(b, 0, QItemSelectionModel.SelectionFlag.NoUpdate)

    from app.views.project_tree_view import _PATH_ROLE

    assert view._selected_path() == b.data(0, _PATH_ROLE), (
        "主选中项是断面B, _selected_path 却指向别的节点"
    )
