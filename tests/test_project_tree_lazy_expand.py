"""test_project_tree_lazy_expand.py — 项目树懒展开(大规模性能阶段 2).

Claude Code 2026-07-15 — flat-list 模式(几千项目)打开时不再对每个项目全量 scan_tree,
只浅探一层决定是否显示展开箭头; 真实子树等点开/多选递归时才 _ensure_children_loaded。

放在独立文件而非 test_project_tree_view.py: 那个文件的单进程测试数已逼近本仓库
已知的 Qt teardown 累积崩溃阈值(CLAUDE.md 有记录), 往里加测试会把偶发崩溃顶成必崩。
本文件测试少、独立进程跑, 不受影响。
"""
from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("PyQt6")

from app.views.project_tree_view import (
    ProjectTreeView,
    _KIND_ROLE,
    _LAZY_ROLE,
)


# 复用 test_project_tree_view.py 里已经完备的假 ctx/settings(涵盖全部设置属性),
# 只是把 view mode 保持在 flat(无 root)以走懒展开路径。import 不会触发那边的测试。
from tests.test_project_tree_view import _FakeCtx  # noqa: E402


def _make_workspace(p: Path):
    (p / "_data").mkdir(parents=True, exist_ok=True)
    import sqlite3
    sqlite3.connect(str(p / "_data" / "project.db")).close()


@pytest.fixture
def ctx():
    return _FakeCtx()


@pytest.fixture
def view(qtbot, ctx):
    v = ProjectTreeView(ctx)
    qtbot.addWidget(v)
    return v


def _lazy_project(tmp_path) -> Path:
    proj = tmp_path / "盐城2026"
    (proj / "断面一").mkdir(parents=True)
    _make_workspace(proj / "断面一")
    (proj / "断面二").mkdir(parents=True)
    return proj


def test_build_item_lazy_node_shows_arrow_without_children(view, tmp_path):
    """懒节点: 不建真实子项, 但用 ShowIndicator 显示展开箭头, 计数列=浅探数。"""
    proj = _lazy_project(tmp_path)
    node = {
        "name": "盐城2026", "path": str(proj), "has_data": False,
        "is_candidate": False, "unavailable": False, "children": [],
        "lazy": True, "lazy_count": view._shallow_child_count(str(proj)),
        "project_meta": {},
    }
    item = view._build_item(node)
    assert item.data(0, _LAZY_ROLE) is True
    assert item.childCount() == 0, "懒节点不建真实子项(等展开)"
    assert item.text(1) == "2", "计数列显示浅探得到的子目录数"
    from PyQt6.QtWidgets import QTreeWidgetItem as QTWI
    assert item.childIndicatorPolicy() == QTWI.ChildIndicatorPolicy.ShowIndicator


def test_ensure_children_loaded_builds_real_subtree(view, tmp_path):
    """_ensure_children_loaded 现扫子树 -> 真实子项(断面一/断面二)建出来, 懒标记清除。"""
    proj = _lazy_project(tmp_path)
    node = {
        "name": "盐城2026", "path": str(proj), "has_data": False,
        "is_candidate": False, "unavailable": False, "children": [],
        "lazy": True, "lazy_count": 2, "project_meta": {},
    }
    item = view._build_item(node)
    view._ensure_children_loaded(item)  # 等价于用户点开展开箭头
    assert item.data(0, _LAZY_ROLE) is None, "加载后懒标记应清除"
    names = {item.child(i).text(0).split("  ·  ", 1)[0] for i in range(item.childCount())}
    assert "断面一" in names and "断面二" in names
    kinds = {
        item.child(i).text(0).split("  ·  ", 1)[0]: item.child(i).data(0, _KIND_ROLE)
        for i in range(item.childCount())
    }
    assert kinds.get("断面一") == "workspace"


def test_ensure_children_loaded_is_idempotent(view, tmp_path):
    """重复调用不重复建子项(第二次是 no-op)。"""
    proj = _lazy_project(tmp_path)
    node = {
        "name": "p", "path": str(proj), "has_data": False, "is_candidate": False,
        "unavailable": False, "children": [], "lazy": True, "lazy_count": 2,
        "project_meta": {},
    }
    item = view._build_item(node)
    view._ensure_children_loaded(item)
    n1 = item.childCount()
    view._ensure_children_loaded(item)
    assert item.childCount() == n1, "第二次加载必须 no-op, 不重复建子项"


def test_collect_workspaces_loads_lazy_children(view, tmp_path):
    """跨工作区工具的递归收集必须先加载懒节点, 否则漏掉没展开过的工作区。"""
    proj = _lazy_project(tmp_path)
    node = {
        "name": "盐城2026", "path": str(proj), "has_data": False,
        "is_candidate": False, "unavailable": False, "children": [],
        "lazy": True, "lazy_count": 2, "project_meta": {},
    }
    item = view._build_item(node)
    assert item.data(0, _LAZY_ROLE) is True  # 还没展开
    acc: list[str] = []
    view._collect_workspaces_from_item(item, acc)  # 应触发懒加载
    assert any(p.endswith("断面一") for p in acc), \
        f"多选收集必须加载懒节点并找到工作区, 实际: {acc}"


def test_shallow_child_count_ignores_reserved_and_hidden(tmp_path, ctx):
    view = ProjectTreeView.__new__(ProjectTreeView)  # 不建 UI, 只测纯方法
    proj = tmp_path / "p"
    (proj / "断面一").mkdir(parents=True)
    (proj / "断面二").mkdir(parents=True)
    (proj / "_data").mkdir()          # 保留目录, 不算
    (proj / "results").mkdir()        # 保留目录, 不算
    (proj / ".git").mkdir()           # 隐藏, 不算
    (proj / "readme.txt").write_text("x")  # 文件, 不算
    assert view._shallow_child_count(str(proj)) == 2
