"""项目树 = 一棵统一的树（用户 2026-07-12 报障 + 需求 R-006）。

用户实测 bug（截图）：在项目树点「＋ 项目」建了新项目后，**之前的项目全从树里消失了**。
根因不是数据丢失，而是 `focus_project()` 把树切成了「单项目(rooted)」模式：

    self.ctx.settings.project_tree_root = root_path
    self.ctx.settings.project_tree_view_mode = "rooted"   # ← 只看得见这一个项目

而另一个模式「全部项目(all)」虽然列得出所有项目，`_load_known_projects_nodes()` 造的节点却是
`children: []` —— 假树，展不开。于是：想看全部就没层级，想看层级就只剩一个。

本文件锁死统一后的契约：
1. 顶层 = **所有**已记录项目并排；
2. 每个项目能展开它磁盘上的真实子树（任意层：项目 → 子目录 → … → 拍摄目录）；
3. 新建项目后，新项目被选中，**其余项目仍在树里**。
"""
from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

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


def _seed(path: Path, projects: list) -> None:
    import json
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"version": 1, "projects": projects}, ensure_ascii=False),
        encoding="utf-8",
    )


def _mk_project(tmp_path: Path, name: str) -> Path:
    d = tmp_path / name
    d.mkdir(parents=True, exist_ok=True)
    return d


def _mk_workspace(parent: Path, name: str) -> Path:
    """真实工作区 = 目录里有 _data/project.db（pts.is_workspace 的判据）。"""
    ws = parent / name
    (ws / "_data").mkdir(parents=True, exist_ok=True)
    (ws / "_data" / "project.db").write_bytes(b"")
    return ws


def _titles(view: ProjectTreeView) -> list[str]:
    return [
        view._tree.topLevelItem(i).text(0)
        for i in range(view._tree.topLevelItemCount())
    ]


def test_all_projects_are_top_level_nodes(qtbot, tmp_path, ctx, monkeypatch):
    """顶层 = 所有已记录项目并排（不是只有一个）。"""
    a = _mk_project(tmp_path, "北方多样性调查")
    b = _mk_project(tmp_path, "潮间带专项")
    _seed(tmp_path / "user_projects.json", [
        {"name": "北方多样性调查", "directory": str(a)},
        {"name": "潮间带专项", "directory": str(b)},
    ])

    view = ProjectTreeView(ctx)
    qtbot.addWidget(view)
    view.on_activate()

    names = _titles(view)
    assert any("北方多样性调查" in n for n in names), names
    assert any("潮间带专项" in n for n in names), names


def test_project_node_expands_to_real_subtree(qtbot, tmp_path, ctx, monkeypatch):
    """项目节点能展开磁盘上的真实子树：项目 → 子目录 → 拍摄目录（任意层）。"""
    proj = _mk_project(tmp_path, "北方多样性调查")
    sub = proj / "江苏盐城-2026"
    sub.mkdir()
    _mk_workspace(sub, "断面B")           # 项目 → 子目录 → 拍摄目录
    _mk_workspace(proj, "断面A")          # 项目 → 拍摄目录（无中间层）
    _seed(tmp_path / "user_projects.json", [
        {"name": "北方多样性调查", "directory": str(proj)},
    ])

    view = ProjectTreeView(ctx)
    qtbot.addWidget(view)
    view.on_activate()

    top = view._tree.topLevelItem(0)
    assert top is not None
    child_names = [top.child(i).text(0) for i in range(top.childCount())]
    assert any("江苏盐城-2026" in n for n in child_names), child_names
    assert any("断面A" in n for n in child_names), child_names

    subnode = next(
        top.child(i) for i in range(top.childCount())
        if "江苏盐城-2026" in top.child(i).text(0)
    )
    grand = [subnode.child(i).text(0) for i in range(subnode.childCount())]
    assert any("断面B" in n for n in grand), grand


def test_focus_project_keeps_other_projects_in_tree(qtbot, tmp_path, ctx, monkeypatch):
    """核心回归：建完新项目把焦点钉到它身上，**其余项目不得消失**。"""
    old = _mk_project(tmp_path, "旧项目")
    new = _mk_project(tmp_path, "新项目")
    _seed(tmp_path / "user_projects.json", [
        {"name": "旧项目", "directory": str(old)},
        {"name": "新项目", "directory": str(new)},
    ])

    view = ProjectTreeView(ctx)
    qtbot.addWidget(view)
    view.on_activate()

    view.focus_project(str(new))

    names = _titles(view)
    assert any("旧项目" in n for n in names), f"旧项目被挤掉了: {names}"
    assert any("新项目" in n for n in names), names

    current = view._tree.currentItem()
    assert current is not None
    assert "新项目" in current.text(0)


# ── 基本操作：右键菜单必须有 重命名 / 移动 / 删除（R-009） ─────────────────────

def test_context_menu_has_rename_move_delete(qtbot, tmp_path, ctx, monkeypatch):
    """用户 R-009: "这种属于基本操作，我都不应该提，你都应该加入"。

    同时锁住：汇总能力（汇总导出 / 数据筛选）不得因为加了这三个动作而丢失。
    """
    proj = _mk_project(tmp_path, "北方多样性调查")
    _seed(tmp_path / "user_projects.json", [
        {"name": "北方多样性调查", "directory": str(proj)},
    ])

    view = ProjectTreeView(ctx)
    qtbot.addWidget(view)
    view.on_activate()

    top = view._tree.topLevelItem(0)
    assert top is not None
    view._tree.setCurrentItem(top)

    captured: list[str] = []

    class _FakeMenu:
        def __init__(self, *a, **kw):
            pass

        def addAction(self, text):
            captured.append(text)
            from PyQt6.QtGui import QAction
            return QAction(text)

        def addSeparator(self):
            pass

        def exec(self, *a, **kw):
            return None

    monkeypatch.setattr("app.views.project_tree_view.QMenu", _FakeMenu)
    from PyQt6.QtCore import QPoint

    rect = view._tree.visualItemRect(top)
    view._show_tree_context_menu(rect.center())

    assert "重命名…" in captured, captured
    assert "移动到项目…" in captured, captured
    assert "删除…" in captured, captured
    assert "汇总导出…" in captured, captured
    assert "数据筛选…" in captured, captured
