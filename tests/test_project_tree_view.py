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


def _seed_projects_json(path: Path, projects: list) -> None:
    """Write a user_projects.json with the given project list."""
    import json
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"version": 1, "projects": projects}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _patch_recent_json(monkeypatch, tmp_path):
    """Isolate the recent-workspaces json to tmp_path; returns the path."""
    recent_json = tmp_path / "user_projects.json"
    monkeypatch.setattr(
        "app.services.project_service.default_user_projects_json_path",
        lambda: str(recent_json),
    )
    return recent_json


def test_no_root_empty_json_shows_placeholder(qtbot, tmp_path, ctx, monkeypatch):
    # No root AND no recorded projects -> the original empty-state placeholder.
    app_dir = tmp_path / "isolated-app"
    app_dir.mkdir()
    monkeypatch.chdir(app_dir)
    recent_json = _patch_recent_json(monkeypatch, tmp_path)
    _seed_projects_json(recent_json, [])

    view = ProjectTreeView(ctx)
    qtbot.addWidget(view)
    view.on_activate()
    assert view._tree.topLevelItemCount() == 0
    assert "未选根目录" in view._root_lbl.text()
    assert "没有已记录的项目" in view._empty_state.text()


def test_no_root_flat_lists_known_projects(qtbot, tmp_path, ctx, monkeypatch):
    # No root but projects recorded -> flat top-level list of every known project.
    app_dir = tmp_path / "isolated-app"
    app_dir.mkdir()
    monkeypatch.chdir(app_dir)
    recent_json = _patch_recent_json(monkeypatch, tmp_path)

    real_a = tmp_path / "ceshi6"
    real_b = tmp_path / "ceshi7"
    real_a.mkdir()
    _make_workspace(real_b)  # only b is a real workspace (has project.db)

    projects = [
        {"id": "1", "name": "甲", "directory": str(real_a)},          # plain folder
        {"id": "2", "name": "乙", "directory": str(real_b)},          # workspace
        {"id": "3", "name": "demo", "directory": str(tmp_path / "x"), "isDemo": True},
        {"id": "4", "name": "dup", "directory": str(real_a)},         # dup of id 1
    ]
    _seed_projects_json(recent_json, projects)

    view = ProjectTreeView(ctx)
    qtbot.addWidget(view)
    view.on_activate()

    # demo skipped + dup deduped -> 2 nodes, most-recent-first (json tail on top)
    assert view._tree.topLevelItemCount() == 2
    assert "全部已建项目" in view._root_lbl.text()
    assert "2" in view._root_lbl.text()
    labels = [view._tree.topLevelItem(i).text(0) for i in range(2)]
    # most-recent-first: dup(id4) was last in json but deduped against id1's dir,
    # so the tail-survivor is 乙(workspace) on top, then 甲.
    assert any("乙" in t and "工作区" in t for t in labels)
    assert any("甲" in t and "工作区" not in t for t in labels)
    # 乙 (the later entry) comes first
    assert "乙" in labels[0]
    assert "甲" in labels[1]


def test_flat_list_enter_workspace_with_root_none(qtbot, tmp_path, ctx, monkeypatch):
    # In flat-list mode (_root is None), entering a project works and the
    # workspace becomes its own root; _root stays None so re-activate stays flat.
    app_dir = tmp_path / "isolated-app"
    app_dir.mkdir()
    monkeypatch.chdir(app_dir)
    recent_json = _patch_recent_json(monkeypatch, tmp_path)
    leaf = tmp_path / "ceshi8"
    leaf.mkdir()
    _make_workspace(leaf)
    _seed_projects_json(recent_json, [{"id": "1", "name": "丙", "directory": str(leaf)}])

    view = ProjectTreeView(ctx)
    qtbot.addWidget(view)
    view.on_activate()

    top = view._tree.topLevelItem(0)
    top.setSelected(True)
    view._tree.setCurrentItem(top)

    with qtbot.waitSignal(view.enter_workspace_requested, timeout=1000):
        view._enter_selected()

    assert ctx.current_project_dir == str(leaf.resolve())
    assert ctx.current_project_root == str(leaf.resolve())
    assert view._root is None


def test_dead_directory_in_json_shown_as_folder(qtbot, tmp_path, ctx, monkeypatch):
    # A recorded project whose dir no longer exists (drive unmounted) still
    # shows up, as a plain 📁 (not a workspace); entering it does NOT crash.
    app_dir = tmp_path / "isolated-app"
    app_dir.mkdir()
    monkeypatch.chdir(app_dir)
    recent_json = _patch_recent_json(monkeypatch, tmp_path)
    ghost = tmp_path / "never-existed"
    _seed_projects_json(recent_json, [{"id": "1", "name": "幽灵", "directory": str(ghost)}])

    view = ProjectTreeView(ctx)
    qtbot.addWidget(view)
    view.on_activate()

    assert view._tree.topLevelItemCount() == 1
    assert "幽灵" in view._tree.topLevelItem(0).text(0)
    assert "工作区" not in view._tree.topLevelItem(0).text(0)

    # entering the dead node: ProjectUnavailableError is caught, no signal
    top = view._tree.topLevelItem(0)
    top.setSelected(True)
    view._tree.setCurrentItem(top)
    # the "盘未连接" path pops a modal box — stub it so headless test doesn't block
    monkeypatch.setattr("app.views.project_tree_view.ui.warn", lambda *a, **k: None)
    emitted = []
    view.enter_workspace_requested.connect(lambda p: emitted.append(p))
    try:
        view._enter_selected()
    except Exception as exc:  # pragma: no cover - defensive
        pytest.fail(f"_enter_selected raised on dead dir: {exc}")
    assert emitted == []


def test_pick_root_after_flat_list_reverts_to_scan(qtbot, tmp_path, ctx, monkeypatch):
    # Flat list populated, then user picks a real root -> tree reverts to scan mode.
    app_dir = tmp_path / "isolated-app"
    app_dir.mkdir()
    monkeypatch.chdir(app_dir)
    recent_json = _patch_recent_json(monkeypatch, tmp_path)
    leaf = tmp_path / "loose"
    leaf.mkdir()
    _seed_projects_json(recent_json, [{"id": "1", "name": "散", "directory": str(leaf)}])

    # build a real survey root with a subfolder
    sroot = tmp_path / "调查区"
    (sroot / "断面a").mkdir(parents=True)

    view = ProjectTreeView(ctx)
    qtbot.addWidget(view)
    view.on_activate()
    assert view._tree.topLevelItemCount() == 1  # flat list

    monkeypatch.setattr(
        "app.utils.ui.get_existing_directory", lambda *a, **k: str(sroot)
    )
    view._pick_root()

    assert view._root == str(sroot.resolve())
    top = view._tree.topLevelItem(0)
    assert top is not None
    assert "调查区" in top.text(0)
    assert top.childCount() == 1  # scan mode: subfolders as children
    assert view._tree.topLevelItemCount() == 1


def test_no_root_auto_discovers_workspace_candidates_near_cwd(qtbot, tmp_path, ctx, monkeypatch):
    recent_json = _patch_recent_json(monkeypatch, tmp_path)
    _seed_projects_json(recent_json, [])

    parent = tmp_path / "project-dump"
    app_dir = parent / "app"
    app_dir.mkdir(parents=True)
    monkeypatch.chdir(app_dir)

    ceshi6 = parent / "ceshi6"
    ceshi8 = parent / "ceshi8"
    _make_workspace(ceshi6)
    (ceshi8 / "_data").mkdir(parents=True)
    (ceshi8 / "incoming-jpg").mkdir()

    view = ProjectTreeView(ctx)
    qtbot.addWidget(view)
    view.on_activate()

    labels = [
        view._tree.topLevelItem(i).text(0)
        for i in range(view._tree.topLevelItemCount())
    ]
    assert any("ceshi6" in text and "工作区" in text for text in labels)
    assert any("ceshi8" in text and "可导入" in text for text in labels)


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
