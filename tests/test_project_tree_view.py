"""tests/test_project_tree_view.py — 项目树视图（headless, pytest-qt）."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

pytest.importorskip("PyQt6")

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QPixmap

from app.views.project_tree_view import ProjectTreeView, _KIND_ROLE


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


def _write_image(path: Path, color: QColor | None = None):
    path.parent.mkdir(parents=True, exist_ok=True)
    pm = QPixmap(40, 28)
    pm.fill(color or QColor("#0f766e"))
    assert pm.save(str(path))


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


def test_tree_has_context_menu(qtbot, tmp_path, ctx):
    root = tmp_path / "survey"
    _make_workspace(root)
    ctx.settings.project_tree_root = str(root)

    view = ProjectTreeView(ctx)
    qtbot.addWidget(view)
    view.on_activate()

    assert view._tree.contextMenuPolicy() == Qt.ContextMenuPolicy.CustomContextMenu


def test_open_directory_uses_shared_file_manager(qtbot, tmp_path, ctx, monkeypatch):
    view = ProjectTreeView(ctx)
    qtbot.addWidget(view)
    opened = []
    monkeypatch.setattr(
        "app.utils.file_manager.open_directory",
        lambda path: opened.append(path) or True,
    )

    view._open_directory(str(tmp_path))

    assert opened == [str(tmp_path)]


def test_rooted_tree_auto_selects_first_item(qtbot, tmp_path, ctx):
    root = tmp_path / "zhengli"
    _make_workspace(root)
    ctx.settings.project_tree_root = str(root)

    view = ProjectTreeView(ctx)
    qtbot.addWidget(view)
    view.on_activate()

    top = view._tree.topLevelItem(0)
    assert top is not None
    assert view._tree.currentItem() is top
    assert top.isSelected()
    assert view._btn_enter.isEnabled()
    assert view._detail_path.text() == str(root)


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


def test_enter_workspace_database_locked_warns_without_crashing(
    qtbot, tmp_path, ctx, monkeypatch
):
    app_dir = tmp_path / "isolated-app"
    app_dir.mkdir()
    monkeypatch.chdir(app_dir)
    recent_json = _patch_recent_json(monkeypatch, tmp_path)
    leaf = tmp_path / "busy"
    leaf.mkdir()
    _make_workspace(leaf)
    _seed_projects_json(recent_json, [{"id": "1", "name": "忙项目", "directory": str(leaf)}])

    view = ProjectTreeView(ctx)
    qtbot.addWidget(view)
    view.on_activate()
    top = view._tree.topLevelItem(0)
    top.setSelected(True)
    view._tree.setCurrentItem(top)

    def fail_enter(*_args, **_kwargs):
        raise sqlite3.OperationalError("database is locked")

    warnings = []
    monkeypatch.setattr("app.services.project_service.enter_workspace", fail_enter)
    monkeypatch.setattr(
        "app.views.project_tree_view.ui.warn",
        lambda _parent, title, text: warnings.append((title, text)),
    )
    emitted = []
    view.enter_workspace_requested.connect(lambda p: emitted.append(p))

    view._enter_selected()

    assert emitted == []
    assert warnings
    assert warnings[0][0] == "项目数据库正忙"


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


def test_kind_filter_selects_first_matching_workspace(qtbot, tmp_path, ctx):
    root = tmp_path / "调查区"
    workspace = root / "断面a"
    folder = root / "断面b"
    folder.mkdir(parents=True)
    _make_workspace(workspace)
    ctx.settings.project_tree_root = str(root)

    view = ProjectTreeView(ctx)
    qtbot.addWidget(view)
    view.on_activate()

    view._set_kind_filter("workspace")

    assert view._tree_count_lbl.text() == "1/3 个匹配"
    assert "工作区" in view._tree.currentItem().text(0)
    assert view._detail_kind.text() == "工作区"
    assert view._detail_path.text() == str(workspace)


def test_no_match_state_clears_selected_detail_actions(qtbot, tmp_path, ctx):
    root = tmp_path / "调查区"
    _make_workspace(root / "断面a")
    ctx.settings.project_tree_root = str(root)

    view = ProjectTreeView(ctx)
    qtbot.addWidget(view)
    view.on_activate()
    assert view._btn_enter.isEnabled()

    view._search.setText("不存在的节点")

    assert view._tree_count_lbl.text() == "0/2 个匹配"
    assert view._detail_kind.text() == "无匹配"
    assert view._detail_path.text() == ""
    assert not view._btn_enter.isEnabled()
    assert not view._btn_summary.isEnabled()
    assert not view._btn_station_species.isEnabled()
    assert not view._btn_station_import.isEnabled()


def test_detail_panel_shows_recent_media_preview(qtbot, tmp_path, ctx):
    root = tmp_path / "调查区"
    workspace = root / "断面a"
    _make_workspace(workspace)
    _write_image(workspace / "incoming-jpg" / "frame-001.jpg")
    _write_image(workspace / "results" / "result-001.png", QColor("#15803d"))
    ctx.settings.project_tree_root = str(root)

    view = ProjectTreeView(ctx)
    qtbot.addWidget(view)
    view.on_activate()

    top = view._tree.topLevelItem(0)
    target = next(top.child(i) for i in range(top.childCount())
                  if "断面a" in top.child(i).text(0))
    view._select_tree_item(target)

    assert not view._media_block.isHidden()
    assert view._media_count_lbl.text() == "2 个"
    assert view._media_grid.count() == 2
    assert view._media_empty_lbl.isHidden()


def test_detail_panel_media_preview_has_empty_state(qtbot, tmp_path, ctx):
    root = tmp_path / "调查区"
    _make_workspace(root / "断面a")
    ctx.settings.project_tree_root = str(root)

    view = ProjectTreeView(ctx)
    qtbot.addWidget(view)
    view.on_activate()

    assert not view._media_block.isHidden()
    assert view._media_count_lbl.text() == "0 个"
    assert view._media_grid.count() == 0
    assert not view._media_empty_lbl.isHidden()
    assert "JPG" in view._media_empty_lbl.text()


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


def test_double_click_multi_selection_does_not_enter(qtbot, tmp_path, ctx, monkeypatch):
    """多选(≥2)态下双击保持多断面汇总预览, 不误跳拍照界面 (spec §2, 用户投诉)."""
    root = tmp_path / "survey"
    for n in ("断面a", "断面b", "断面c"):
        (root / n).mkdir(parents=True)
    _make_workspace(root / "断面a")
    _make_workspace(root / "断面b")
    ctx.settings.project_tree_root = str(root)

    view = ProjectTreeView(ctx)
    qtbot.addWidget(view)
    view.on_activate()

    top = view._tree.topLevelItem(0)
    top.child(0).setSelected(True)
    top.child(1).setSelected(True)
    assert len(view._tree.selectedItems()) >= 2, "前置: 应已多选 2 节点"

    entered = []
    monkeypatch.setattr(view, "_enter_selected", lambda: entered.append(1))
    view._on_tree_double_clicked()
    assert not entered, "多选≥2 时双击不应进入拍照界面"


def test_double_click_single_selection_enters(qtbot, tmp_path, ctx, monkeypatch):
    """单选态下双击仍正常进入工作区 (回归保护)."""
    root = tmp_path / "survey"
    (root / "断面a").mkdir(parents=True)
    _make_workspace(root / "断面a")
    ctx.settings.project_tree_root = str(root)

    view = ProjectTreeView(ctx)
    qtbot.addWidget(view)
    view.on_activate()

    view._tree.clearSelection()
    top = view._tree.topLevelItem(0)
    top.child(0).setSelected(True)
    assert len(view._tree.selectedItems()) == 1

    entered = []
    monkeypatch.setattr(view, "_enter_selected", lambda: entered.append(1))
    view._on_tree_double_clicked()
    assert entered, "单选双击应进入工作区"


def test_scan_disk_registers_discovered_workspaces(qtbot, tmp_path, ctx, monkeypatch):
    """扫描磁盘: 发现的旧工作区登记到 user_projects.json (用户核心需求)."""
    import json as _json
    from app.services import project_service as ps
    from app.utils import ui as _ui

    scan_root = tmp_path / "disk"
    _make_workspace(scan_root / "old1")
    _make_workspace(scan_root / "old2")
    # 非工作区目录应被忽略
    (scan_root / "not_a_workspace").mkdir(parents=True)

    jp = tmp_path / "user_projects.json"
    jp.write_text(_json.dumps({"version": 1, "projects": []}), encoding="utf-8")
    monkeypatch.setattr(ps, "default_user_projects_json_path", lambda: str(jp))
    monkeypatch.setattr(_ui, "get_existing_directory", lambda *a, **k: str(scan_root))
    monkeypatch.setattr(_ui, "info", lambda *a, **k: None)

    ctx.settings.project_tree_root = None
    view = ProjectTreeView(ctx)
    qtbot.addWidget(view)
    view._scan_disk()

    projects = ps.list_projects(str(jp))
    dirs = {p.get("directory") for p in projects}
    assert any("old1" in (d or "") for d in dirs), "old1 工作区应被登记"
    assert any("old2" in (d or "") for d in dirs), "old2 工作区应被登记"
    assert not any("not_a_workspace" in (d or "") for d in dirs), "非工作区目录不应登记"


def test_add_workspace_manual_registers(qtbot, tmp_path, ctx, monkeypatch):
    """「添加工作区」手动选单目录 → 登记到 user_projects.json(不扫整盘)。"""
    import json as _json
    from app.services import project_service as ps
    from app.utils import ui as _ui

    ws = tmp_path / "手动工作区"
    ws.mkdir()

    jp = tmp_path / "user_projects.json"
    jp.write_text(_json.dumps({"version": 1, "projects": []}), encoding="utf-8")
    monkeypatch.setattr(ps, "default_user_projects_json_path", lambda: str(jp))
    monkeypatch.setattr(_ui, "get_existing_directory", lambda *a, **k: str(ws))
    monkeypatch.setattr(_ui, "info", lambda *a, **k: None)

    ctx.settings.project_tree_root = None
    view = ProjectTreeView(ctx)
    qtbot.addWidget(view)
    view._add_workspace_manual()

    dirs = {p.get("directory") for p in ps.list_projects(str(jp))}
    assert any("手动工作区" in (d or "") for d in dirs), "手动选的目录应被登记"


def test_show_all_projects_clears_root(qtbot, tmp_path, ctx) -> None:
    """「全部项目」清锁定 root → flat list 显示全部已登记项目。"""
    from app.services import project_service as ps

    jp = tmp_path / "user_projects.json"
    (tmp_path / "ws1" / "_data").mkdir(parents=True)
    (tmp_path / "ws2" / "_data").mkdir(parents=True)
    import json as _json
    jp.write_text(_json.dumps({"version": 1, "projects": [
        {"directory": str(tmp_path / "ws1"), "name": "ws1"},
        {"directory": str(tmp_path / "ws2"), "name": "ws2"},
    ]}), encoding="utf-8")

    ctx.settings.project_tree_root = str(tmp_path / "ws1")  # 锁 ws1
    view = ProjectTreeView(ctx)
    qtbot.addWidget(view)
    view.on_activate()
    # 锁 root → rooted scan ws1, 只 ws1
    assert view._tree.topLevelItemCount() >= 1
    view._show_all_projects()
    # 清 root → flat list 全部
    assert ctx.settings.project_tree_root is None
    assert view._root is None
    names = [view._tree.topLevelItem(i).text(0) for i in range(view._tree.topLevelItemCount())]
    assert any("ws1" in n for n in names) and any("ws2" in n for n in names), "flat 该显全部"


def test_select_all_filter_pure_nav(qtbot, tmp_path, ctx):
    """A 重构: 项目树纯导航. 「全部」filter 只切列表显隐 + 单选详情, 不再汇总.

    多选汇总 / 编号网格 / 字段筛选 已迁至「数据筛选」页 (DataFilterView, 自有测试).
    """
    root = tmp_path / "survey"
    for n in ("断面a", "断面b", "空文件夹"):
        (root / n).mkdir(parents=True)
    _make_workspace(root / "断面a")
    _make_workspace(root / "断面b")
    ctx.settings.project_tree_root = str(root)
    view = ProjectTreeView(ctx)
    qtbot.addWidget(view)
    view.on_activate()
    # 切来切去不应崩; A 后无汇总, 单选详情
    view._set_kind_filter("workspace")
    view._set_kind_filter("all")
    view._set_kind_filter("region" if "region" in view._kind_filter_buttons else "all")
    view._set_kind_filter("all")
    assert view._tree.topLevelItemCount() >= 1, "树仍在"
    assert not hasattr(view, "_right_stack"), "A 重构后项目树无 _right_stack"

