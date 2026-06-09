"""tests/test_project_tree_view.py — 项目树视图（headless, pytest-qt）."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

pytest.importorskip("PyQt6")

from app.views.project_tree_view import ProjectTreeView


class _FakeSettings:
    def __init__(self):
        self._root = None

    @property
    def project_tree_root(self):
        return self._root

    @project_tree_root.setter
    def project_tree_root(self, v):
        self._root = v


class _FakeCtx:
    def __init__(self):
        self.settings = _FakeSettings()
        self.current_project_dir = None
        self.current_project_root = None

    def get_db(self):
        return None


def _make_workspace(p: Path):
    (p / "_data").mkdir(parents=True, exist_ok=True)
    sqlite3.connect(str(p / "_data" / "project.db")).close()


@pytest.fixture
def ctx():
    return _FakeCtx()


def test_builds_tree_from_root(qtbot, tmp_path, ctx):
    root = tmp_path / "雷州半岛多样性"
    for n in ("断面a", "断面b", "断面c"):
        (root / n).mkdir(parents=True)
    _make_workspace(root / "断面a")
    ctx.settings.project_tree_root = str(root)

    view = ProjectTreeView(ctx)
    qtbot.addWidget(view)
    view.on_activate()

    top = view._tree.topLevelItem(0)
    assert "雷州半岛多样性" in top.text(0)
    child_texts = [top.child(i).text(0) for i in range(top.childCount())]
    assert any("断面a" in t for t in child_texts)
    # workspace nodes (have project.db) are tagged 工作区; plain folders/regions are not
    assert any("断面a" in t and "工作区" in t for t in child_texts)
    assert any("断面b" in t and "工作区" not in t for t in child_texts)


def test_no_root_shows_placeholder(qtbot, ctx):
    view = ProjectTreeView(ctx)
    qtbot.addWidget(view)
    view.on_activate()
    assert view._tree.topLevelItemCount() == 0
    assert "未选根目录" in view._root_lbl.text()


def test_enter_node_sets_ctx_and_root(qtbot, tmp_path, ctx, monkeypatch):
    root = tmp_path / "proj"
    leaf = root / "断面a"
    leaf.mkdir(parents=True)
    ctx.settings.project_tree_root = str(root)

    # Isolate the recent-list write to a tmp file (don't touch the repo's
    # tracked data/user_projects.json).
    recent_json = tmp_path / "user_projects.json"
    monkeypatch.setattr(
        "app.services.project_service.default_user_projects_json_path",
        lambda: str(recent_json),
    )

    view = ProjectTreeView(ctx)
    qtbot.addWidget(view)
    view.on_activate()

    top = view._tree.topLevelItem(0)
    # select the 断面a child
    target = next(top.child(i) for i in range(top.childCount())
                  if "断面a" in top.child(i).text(0))
    target.setSelected(True)
    view._tree.setCurrentItem(target)

    with qtbot.waitSignal(view.enter_workspace_requested, timeout=1000):
        view._enter_selected()

    assert ctx.current_project_dir == str(leaf)
    assert ctx.current_project_root == str(root)
    # entering must lazily create the workspace layout
    assert (leaf / "_data").is_dir()
    assert (leaf / "incoming-jpg").is_dir()
    # entering also records the node into the recent-workspaces list so it
    # surfaces in 最近工作区 (the two views share one source of truth)
    import json
    recorded = json.loads(recent_json.read_text(encoding="utf-8"))["projects"]
    assert any(p.get("directory") == str(leaf.resolve()) for p in recorded)
