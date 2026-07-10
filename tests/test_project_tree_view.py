"""tests/test_project_tree_view.py — 项目树视图（headless, pytest-qt）."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

pytest.importorskip("PyQt6")

from PyQt6.QtCore import QItemSelectionModel, Qt
from PyQt6.QtGui import QColor, QPixmap
from PyQt6.QtWidgets import QAbstractItemView, QHBoxLayout, QLabel

from app.views.project_tree_view import ProjectTreeView, _KIND_ROLE


class _FakeSettings:
    def __init__(self):
        self._root = None
        self._view_mode = "rooted"
        self._layout_mode = "tree"
        self._thumb_size = 112

    @property
    def project_tree_root(self):
        return self._root

    @project_tree_root.setter
    def project_tree_root(self, v):
        self._root = v
        if v is not None:
            self._view_mode = "rooted"

    @property
    def project_tree_view_mode(self):
        return self._view_mode

    @project_tree_view_mode.setter
    def project_tree_view_mode(self, v):
        self._view_mode = v

    @property
    def project_tree_layout_mode(self):
        return self._layout_mode

    @project_tree_layout_mode.setter
    def project_tree_layout_mode(self, v):
        self._layout_mode = v

    @property
    def project_tree_ux_v2(self):
        return getattr(self, "_ux_v2", True)

    @project_tree_ux_v2.setter
    def project_tree_ux_v2(self, v):
        self._ux_v2 = bool(v)

    @property
    def project_tree_tip_dismissed(self):
        return getattr(self, "_tip_dismissed", False)

    @project_tree_tip_dismissed.setter
    def project_tree_tip_dismissed(self, v):
        self._tip_dismissed = bool(v)

    @property
    def project_tree_thumb_size(self):
        return getattr(self, "_thumb_size", __import__("app.config.project_tree_layout", fromlist=["DEFAULT_THUMB_SIZE"]).DEFAULT_THUMB_SIZE)

    @project_tree_thumb_size.setter
    def project_tree_thumb_size(self, v):
        from app.config.project_tree_layout import clamp_thumb_size
        self._thumb_size = clamp_thumb_size(v)

    @property
    def project_tree_grid_density(self):
        from app.config.project_tree_layout import DEFAULT_GRID_DENSITY_INDEX
        return getattr(self, "_grid_density", DEFAULT_GRID_DENSITY_INDEX)

    @project_tree_grid_density.setter
    def project_tree_grid_density(self, v):
        from app.config.project_tree_layout import clamp_density_index
        self._grid_density = clamp_density_index(v)

    @property
    def project_tree_grid_sort(self):
        from app.config.project_tree_layout import DEFAULT_GRID_SORT
        return getattr(self, "_grid_sort", DEFAULT_GRID_SORT)

    @project_tree_grid_sort.setter
    def project_tree_grid_sort(self, v):
        from app.config.project_tree_layout import normalize_grid_sort
        self._grid_sort = normalize_grid_sort(v)

    @property
    def project_tree_grid_caption(self):
        from app.config.project_tree_layout import DEFAULT_GRID_CAPTION
        return getattr(self, "_grid_caption", DEFAULT_GRID_CAPTION)

    @project_tree_grid_caption.setter
    def project_tree_grid_caption(self, v):
        from app.config.project_tree_layout import normalize_grid_caption_mode
        self._grid_caption = normalize_grid_caption_mode(v)

    @property
    def project_tree_content_mode(self):
        from app.config.project_tree_layout import DEFAULT_CONTENT_MODE
        return getattr(self, "_content_mode", DEFAULT_CONTENT_MODE)

    @project_tree_content_mode.setter
    def project_tree_content_mode(self, v):
        from app.config.project_tree_layout import normalize_content_mode
        self._content_mode = normalize_content_mode(v)

    @property
    def project_tree_show_photos(self):
        return getattr(self, "_show_photos", True)

    @project_tree_show_photos.setter
    def project_tree_show_photos(self, v):
        self._show_photos = bool(v)

    @property
    def project_tree_split_state(self):
        return getattr(self, "_split_state", None)

    @project_tree_split_state.setter
    def project_tree_split_state(self, v):
        self._split_state = v

    @property
    def project_tree_grid_inner_split_state(self):
        return getattr(self, "_grid_inner_split_state", None)

    @project_tree_grid_inner_split_state.setter
    def project_tree_grid_inner_split_state(self, v):
        self._grid_inner_split_state = v

    @property
    def project_tree_summary_body_split_state(self):
        return getattr(self, "_summary_body_split_state", None)

    @project_tree_summary_body_split_state.setter
    def project_tree_summary_body_split_state(self, v):
        self._summary_body_split_state = v

    @property
    def performance_mode(self):
        return False

    @property
    def project_tree_preview_master_size(self):
        from app.config.project_tree_layout import DEFAULT_PREVIEW_MASTER_SIZE
        return DEFAULT_PREVIEW_MASTER_SIZE

    @project_tree_preview_master_size.setter
    def project_tree_preview_master_size(self, v):
        pass

    @property
    def project_tree_summary_visible_columns(self):
        from app.services.cross_workspace_query_service import DEFAULT_SUMMARY_VISIBLE_KEYS

        return getattr(self, "_summary_visible_columns", list(DEFAULT_SUMMARY_VISIBLE_KEYS))

    @project_tree_summary_visible_columns.setter
    def project_tree_summary_visible_columns(self, keys):
        self._summary_visible_columns = list(keys or [])


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


def test_card_enter_runs_unified_entry_not_just_signal(
    qtbot, tmp_path, ctx, monkeypatch
):
    """v0.55 回归: 卡片「进入」只 emit 无人连接的信号 → 空操作。
    必须走 enter_workspace 统一入口(设 ctx/建目录/记最近), 信号照发(兼容)。"""
    recent_json = _patch_recent_json(monkeypatch, tmp_path)
    leaf = tmp_path / "断面A"
    leaf.mkdir()
    _make_workspace(leaf)
    _seed_projects_json(recent_json, [{"id": "1", "name": "断面A", "directory": str(leaf)}])

    view = ProjectTreeView(ctx)
    qtbot.addWidget(view)
    view.on_activate()

    entered = []
    monkeypatch.setattr(
        "app.services.project_service.enter_workspace",
        lambda _ctx, path, **_kw: entered.append(str(path)),
    )
    emitted = []
    view.enter_workspace_requested.connect(lambda p: emitted.append(p))

    view._enter_workspace_from_card(str(leaf))

    assert entered == [str(leaf)], "卡片进入必须真正调用 enter_workspace"
    assert emitted == [str(leaf)]


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


def test_selection_survives_locked_workspace_db(qtbot, tmp_path, ctx, monkeypatch):
    """v0.56 治理: 聚合遇 database is locked 不炸 slot、收起中栏不留半截界面.

    查询已移入 SummaryQueryWorker(主线程不再假死), 失败经 failed 信号异步
    回 _on_summary_query_failed → 与旧同步守护同款处置(收起中栏+状态栏提示),
    故此测试等待异步失败落地。(wait 已安全: conftest._flush_deferred_deletions
    每测试后冲洗销毁队列, 2026-07-10 二分定位。)
    """
    root = tmp_path / "survey"
    (root / "断面a").mkdir(parents=True)
    _make_workspace(root / "断面a")
    ctx.settings.project_tree_root = str(root)

    view = ProjectTreeView(ctx)
    qtbot.addWidget(view)
    # 右栏概览(mini-map/物种面板)非被测对象, stub 掉降低测试面
    monkeypatch.setattr(view, "_refresh_survey_overview", lambda *_a, **_k: None)
    view.on_activate()

    import sqlite3 as _sq

    from app.services import cross_workspace_query_service as cwq

    def boom(*_a, **_k):
        raise _sq.OperationalError("database is locked")

    monkeypatch.setattr(cwq, "query_summary_scope", boom)

    view._tree.clearSelection()
    view._tree.topLevelItem(0).child(0).setSelected(True)

    qtbot.waitUntil(
        lambda: not view._data_summary_panel.isVisible(), timeout=5000
    )
    view._stop_summary_query_worker(wait_ms=2000)


def test_summary_query_runs_in_worker_and_applies_result(
    qtbot, tmp_path, ctx, monkeypatch
):
    """数据汇总查询在 worker 线程执行(主线程不假死), 结果异步回填表格/统计."""
    import threading

    root = tmp_path / "survey"
    (root / "断面a").mkdir(parents=True)
    _make_workspace(root / "断面a")
    ctx.settings.project_tree_root = str(root)

    view = ProjectTreeView(ctx)
    qtbot.addWidget(view)
    monkeypatch.setattr(view, "_refresh_survey_overview", lambda *_a, **_k: None)
    view.on_activate()

    from app.services import cross_workspace_query_service as cwq

    seen_threads: list = []
    real_query = cwq.query_summary_scope

    def spy(*a, **k):
        seen_threads.append(threading.current_thread())
        return real_query(*a, **k)

    monkeypatch.setattr(cwq, "query_summary_scope", spy)

    view._tree.clearSelection()
    view._tree.topLevelItem(0).child(0).setSelected(True)

    qtbot.waitUntil(
        lambda: getattr(view, "_current_summary_result", None) is not None,
        timeout=8000,
    )
    assert seen_threads, "sanity: 查询应被执行"
    assert seen_threads[0] is not threading.main_thread(), (
        "查询必须在 worker 线程, 不得阻塞 Qt 主线程"
    )
    view._stop_summary_query_worker(wait_ms=2000)


def test_warmup_worker_retired_not_orphaned(qtbot, tmp_path, ctx, monkeypatch):
    """v0.56: 预热线程 200ms 内没停时必须进退休名单继续追踪,
    不得直接覆盖引用变孤儿(视图销毁时 QThread destroyed-while-running 崩溃)."""
    from types import SimpleNamespace

    from app.views import project_tree_view as ptv

    view = ProjectTreeView(ctx)
    qtbot.addWidget(view)

    class StaleWorker:
        def __init__(self):
            self.cancelled = False

        def isRunning(self):
            return True

        def cancel(self):
            self.cancelled = True

        def wait(self, _ms=0):
            return False  # 模拟 200ms 内停不下来

        def setParent(self, _p):
            pass

    started = {}

    class FakeNewWorker:
        class _Sig:
            def connect(self, *_a):
                pass

        finished_result = _Sig()

        def __init__(self, paths, parent=None):
            started["paths"] = list(paths)

        def start(self):
            started["started"] = True

    monkeypatch.setattr(
        "app.workers.tiff_preview_warmup_worker.TiffPreviewWarmupWorker",
        FakeNewWorker,
    )
    monkeypatch.setattr(
        "app.services.tiff_preview_warmup_service.collect_tif_paths_from_summary",
        lambda *_a, **_k: ["/tmp/x.tif"],
    )

    stale = StaleWorker()
    view._tif_preview_warmup_worker = stale
    ptv._RETIRED_WARMUP_WORKERS.clear()

    view._schedule_tiff_preview_warmup(SimpleNamespace(specimens=[{}], groups=[]))

    assert stale.cancelled, "滞留 worker 应先被 cancel"
    assert stale in ptv._RETIRED_WARMUP_WORKERS, "未停线程必须进退休名单, 不得遗弃"
    assert started.get("started"), "新 worker 应照常启动"


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
    ctx.settings.project_tree_view_mode = "rooted"
    view = ProjectTreeView(ctx)
    qtbot.addWidget(view)
    view.on_activate()
    # 锁 root → rooted scan ws1, 只 ws1
    assert view._tree.topLevelItemCount() >= 1
    view._show_all_projects()
    assert ctx.settings.project_tree_view_mode == "all"
    assert view._root is None
    names = [view._tree.topLevelItem(i).text(0) for i in range(view._tree.topLevelItemCount())]
    assert any("ws1" in n for n in names) and any("ws2" in n for n in names), "flat 该显全部"


def test_grid_quick_buttons_apply_column_presets(qtbot, ctx):
    from app.config import project_tree_layout as ptl

    view = ProjectTreeView(ctx)
    qtbot.addWidget(view)
    assert set(view._grid_cols_buttons.keys()) == set(ptl.GRID_QUICK_COLUMN_PRESETS)
    view._on_grid_cols_clicked(8)
    assert ptl.columns_for_density_index(ctx.settings.project_tree_grid_density) == 8
    assert view._grid_cols_buttons[8].isChecked()
    view._on_grid_cols_clicked(1)
    assert ptl.columns_for_density_index(ctx.settings.project_tree_grid_density) == 1
    assert view._grid_cols_buttons[1].isChecked()


def test_select_all_filter_with_three_column_grid(qtbot, tmp_path, ctx, monkeypatch):
    """全部项目模式: 「全选工作区」选中全部已登记项目."""
    import json as _json
    from app.services import project_service as ps

    jp = tmp_path / "user_projects.json"
    for name in ("ws1", "ws2", "ws3"):
        _make_workspace(tmp_path / name)
    jp.write_text(_json.dumps({
        "version": 1,
        "projects": [
            {"directory": str(tmp_path / n), "name": n}
            for n in ("ws1", "ws2", "ws3")
        ],
    }), encoding="utf-8")
    monkeypatch.setattr(ps, "default_user_projects_json_path", lambda: str(jp))

    ctx.settings.project_tree_view_mode = "all"
    view = ProjectTreeView(ctx)
    qtbot.addWidget(view)
    view.on_activate()
    assert len(view._tree.selectedItems()) == 3
    view._tree.clearSelection()
    view._btn_select_all_ws.click()
    assert len(view._tree.selectedItems()) == 3
    assert len(view._effective_scope_labeled()) == 3
    assert hasattr(view, "_right_stack"), "三栏应有右栏 stack"
    assert hasattr(view, "_grid_panel"), "三栏应有中栏网格"


def test_default_view_mode_is_all_projects(qtbot, tmp_path, ctx, monkeypatch):
    """默认进入「全部项目」，不因上次根目录而隐藏其它项目。"""
    import json as _json
    from app.services import project_service as ps

    jp = tmp_path / "user_projects.json"
    _make_workspace(tmp_path / "ws-a")
    _make_workspace(tmp_path / "ws-b")
    jp.write_text(_json.dumps({
        "version": 1,
        "projects": [
            {"directory": str(tmp_path / "ws-a"), "name": "ws-a"},
            {"directory": str(tmp_path / "ws-b"), "name": "ws-b"},
        ],
    }), encoding="utf-8")
    monkeypatch.setattr(ps, "default_user_projects_json_path", lambda: str(jp))

    ctx.settings.project_tree_root = str(tmp_path / "ws-a")
    ctx.settings.project_tree_view_mode = "all"
    view = ProjectTreeView(ctx)
    qtbot.addWidget(view)
    view.on_activate()

    assert view._btn_mode_all.isChecked()
    names = [view._tree.topLevelItem(i).text(0) for i in range(view._tree.topLevelItemCount())]
    assert any("ws-a" in n for n in names)
    assert any("ws-b" in n for n in names)


def test_relocate_selected_path_updates_registry(qtbot, tmp_path, ctx, monkeypatch):
    import json as _json
    from app.services import project_service as ps
    from app.utils import ui as _ui

    old = tmp_path / "old-path"
    new = tmp_path / "new-path"
    _make_workspace(old)
    new.mkdir()
    _make_workspace(new)

    jp = tmp_path / "user_projects.json"
    jp.write_text(_json.dumps({
        "version": 1,
        "projects": [{"directory": str(old), "name": "old-path"}],
    }), encoding="utf-8")
    monkeypatch.setattr(ps, "default_user_projects_json_path", lambda: str(jp))
    monkeypatch.setattr(_ui, "get_existing_directory", lambda *a, **k: str(new))
    monkeypatch.setattr(_ui, "info", lambda *a, **k: None)

    ctx.settings.project_tree_view_mode = "all"
    view = ProjectTreeView(ctx)
    qtbot.addWidget(view)
    view.on_activate()
    view._tree.setCurrentItem(view._tree.topLevelItem(0))
    view._relocate_selected_path()

    projects = ps.list_projects(str(jp))
    assert projects[0]["directory"] == str(new.resolve())


def test_content_mode_migrates_overview_to_data_summary() -> None:
    from app.config import project_tree_layout as ptl

    assert ptl.normalize_content_mode("overview") == "data_summary"
    assert ptl.DEFAULT_CONTENT_MODE == "data_summary"
    assert ptl.CONTENT_MODES == (("data_summary", "数据汇总"),)


def test_toggle_photo_panel_hides_grid_and_expands_table(qtbot, tmp_path, ctx, monkeypatch):
    """数据量大时：关掉成片，编号表占满中间栏."""
    import json as _json
    from app.config import project_tree_layout as ptl
    from app.services import project_service as ps

    jp = tmp_path / "user_projects.json"
    _make_workspace(tmp_path / "ws1")
    jp.write_text(_json.dumps({
        "version": 1,
        "projects": [{"directory": str(tmp_path / "ws1"), "name": "ws1"}],
    }), encoding="utf-8")
    monkeypatch.setattr(ps, "default_user_projects_json_path", lambda: str(jp))
    ctx.settings.project_tree_view_mode = "all"
    view = ProjectTreeView(ctx)
    qtbot.addWidget(view)
    view.on_activate()
    try:
        assert view._btn_toggle_photos.isChecked()
        assert not view._photo_block.isHidden()
        view._btn_toggle_photos.click()
        assert view._photo_block.isHidden()
        assert view.ctx.settings.project_tree_show_photos is False
        split = view._summary_body_split
        assert split.count() == 2
        sizes = split.sizes()
        assert sizes[1] == 0
        assert sizes[0] >= ptl.SUMMARY_BODY_TABLE_MIN
        view._btn_toggle_photos.click()
        assert not view._photo_block.isHidden()
        assert view.ctx.settings.project_tree_show_photos is True
    finally:
        view.stop_background_work()


def test_summary_body_splitter_allows_resize(qtbot, ctx):
    from app.config import project_tree_layout as ptl

    view = ProjectTreeView(ctx)
    qtbot.addWidget(view)
    view.resize(900, 700)
    view.show()
    qtbot.waitExposed(view)
    view._grid_body.show()
    view._data_summary_panel.show()
    view._photo_block.show()
    try:
        split = view._summary_body_split
        assert split is not None
        assert split.orientation() == Qt.Orientation.Vertical
        assert split.count() == 2
        assert split.widget(0) is view._summary_table_host
        assert split.widget(1) is view._photo_block
        split.resize(800, 500)
        split.setSizes([120, 380])
        sizes = split.sizes()
        assert sizes[0] >= ptl.SUMMARY_BODY_TABLE_MIN
        assert sizes[1] >= ptl.SUMMARY_BODY_PHOTO_MIN
        assert view._specimen_table.maximumHeight() == 16777215
    finally:
        view.stop_background_work()


def test_specimen_summary_table_uses_windows_extended_selection(qtbot, ctx):
    """编号列表应支持 Windows 常用 Ctrl+A / Shift 范围多选."""
    from PyQt6.QtTest import QTest

    view = ProjectTreeView(ctx)
    qtbot.addWidget(view)
    try:
        view._summary_visible_columns = [("uid", "编号")]
        view._rebuild_specimen_table_structure()
        view._populate_specimen_table([
            {"uid": "GXFCG-BLW-SC001-D79-20260618"},
            {"uid": "GXFCG-BLW-SC002-RD79-20260618"},
            {"uid": "GXFCG-BLW-PGC001-D-20260618"},
        ])
        table = view._specimen_table
        assert table.selectionMode() == QAbstractItemView.SelectionMode.ExtendedSelection
        table.setFocus()
        table.setCurrentCell(0, 0)

        QTest.keyClick(table, Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier)
        selected_rows = {ix.row() for ix in table.selectionModel().selectedRows()}
        assert selected_rows == {0, 1, 2}

        table.clearSelection()
        table.setCurrentCell(0, 0)
        QTest.keyClick(table, Qt.Key.Key_Down, Qt.KeyboardModifier.ShiftModifier)
        selected_rows = {ix.row() for ix in table.selectionModel().selectedRows()}
        assert selected_rows == {0, 1}
    finally:
        view.stop_background_work()


def test_specimen_table_supports_row_and_column_reorder(qtbot, ctx):
    view = ProjectTreeView(ctx)
    qtbot.addWidget(view)
    try:
        table = view._specimen_table
        assert table.dragDropMode() == QAbstractItemView.DragDropMode.InternalMove
        assert table.dragEnabled() is True
        assert table.acceptDrops() is True
        hdr = table.horizontalHeader()
        assert hdr.sectionsMovable() is True
    finally:
        view.stop_background_work()


def test_groups_for_summary_display_filters_selected_uids(qtbot, ctx):
    view = ProjectTreeView(ctx)
    qtbot.addWidget(view)
    try:
        view._current_merged = [
            {"uid": "A", "items": [{"path": "/a1.jpg"}]},
            {"uid": "B", "items": [{"path": "/b1.jpg"}, {"path": "/b2.jpg"}]},
            {"uid": "C", "items": []},
        ]
        view._summary_visible_columns = [("uid", "编号")]
        view._rebuild_specimen_table_structure()
        view._populate_specimen_table([
            {"uid": "A"},
            {"uid": "B"},
            {"uid": "C"},
        ])
        filtered = view._groups_for_summary_display(uid_filter={"B"})
        assert [g["uid"] for g in filtered] == ["B"]
        assert sum(len(g.get("items") or []) for g in filtered) == 2
    finally:
        view.stop_background_work()


def test_table_selection_filters_summary_grid(qtbot, ctx):
    view = ProjectTreeView(ctx)
    qtbot.addWidget(view)
    applied: list[list] = []

    def _capture(groups):
        applied.append(list(groups))

    view._apply_summary_groups_to_grid = _capture  # type: ignore[method-assign]
    try:
        view._current_merged = [
            {"uid": "A", "items": [{"path": "/a1.jpg"}]},
            {"uid": "B", "items": [{"path": "/b1.jpg"}]},
        ]
        view._summary_visible_columns = [("uid", "编号")]
        view._rebuild_specimen_table_structure()
        view._populate_specimen_table([{"uid": "A"}, {"uid": "B"}])
        view._specimen_table.clearSelection()
        view._on_specimen_table_selection_changed()
        assert [g["uid"] for g in applied[-1]] == ["A", "B"]

        view._specimen_table.setCurrentCell(
            1,
            0,
            QItemSelectionModel.SelectionFlag.ClearAndSelect
            | QItemSelectionModel.SelectionFlag.Rows,
        )
        view._on_specimen_table_selection_changed()
        assert [g["uid"] for g in applied[-1]] == ["B"]
        assert "已选 1 编号" in view._grid_count_lbl.text()
    finally:
        view.stop_background_work()


def test_order_specimen_rows_respects_manual_uid_order(qtbot, ctx):
    view = ProjectTreeView(ctx)
    qtbot.addWidget(view)
    try:
        rows = [
            {"uid": "A"},
            {"uid": "B"},
            {"uid": "C"},
        ]
        view._summary_row_uid_order = ["C", "A", "B"]
        ordered = view._order_specimen_rows(rows)
        assert [r["uid"] for r in ordered] == ["C", "A", "B"]
    finally:
        view.stop_background_work()


def test_ux_v2_hides_legacy_header_chips(qtbot, ctx):
    """精简模式：顶栏隐藏「全部/按根」chip，退回后恢复."""
    view = ProjectTreeView(ctx)
    qtbot.addWidget(view)
    view.show()
    try:
        assert ctx.settings.project_tree_ux_v2 is True
        assert view._mode_row_host.isHidden() is True
        assert view._btn_pick.isHidden() is True
        assert view._btn_display.isHidden() is False
        assert view._thumb_row_host.isHidden() is True
        assert view._right_action_bar.isHidden() is False

        ctx.settings.project_tree_ux_v2 = False
        view._apply_ux_profile()
        assert view._mode_row_host.isHidden() is False
        assert view._btn_pick.isHidden() is False
        assert view._btn_display.isHidden() is True
        assert view._thumb_row_host.isHidden() is False
        assert view._right_action_bar.isHidden() is True
    finally:
        view.stop_background_work()


def test_kind_filter_all_does_not_auto_select_in_ux_v2(qtbot, tmp_path, ctx, monkeypatch):
    """精简模式：点「全部」只过滤，不强制全选."""
    import json as _json
    from app.services import project_service as ps

    jp = tmp_path / "user_projects.json"
    for name in ("ws1", "ws2"):
        _make_workspace(tmp_path / name)
    jp.write_text(_json.dumps({
        "version": 1,
        "projects": [
            {"name": "ws1", "directory": str(tmp_path / "ws1")},
            {"name": "ws2", "directory": str(tmp_path / "ws2")},
        ],
    }), encoding="utf-8")
    monkeypatch.setattr(ps, "default_user_projects_json_path", lambda: jp)
    ctx.settings.project_tree_view_mode = "all"
    ctx.settings.project_tree_ux_v2 = True

    view = ProjectTreeView(ctx)
    qtbot.addWidget(view)
    try:
        view.on_activate()
        view._tree.clearSelection()
        assert view._tree.selectedItems() == []
        view._set_kind_filter("workspace")
        view._set_kind_filter("all")
        # 精简模式不应因点「全部」而自动全选
        assert len(view._tree.selectedItems()) == 0
    finally:
        view.stop_background_work()


def test_kind_filter_all_auto_selects_in_legacy_ux(qtbot, tmp_path, ctx, monkeypatch):
    """旧版：点「全部」仍会全选工作区（可退回行为）."""
    import json as _json
    from app.services import project_service as ps

    jp = tmp_path / "user_projects.json"
    for name in ("ws1", "ws2"):
        _make_workspace(tmp_path / name)
    jp.write_text(_json.dumps({
        "version": 1,
        "projects": [
            {"name": "ws1", "directory": str(tmp_path / "ws1")},
            {"name": "ws2", "directory": str(tmp_path / "ws2")},
        ],
    }), encoding="utf-8")
    monkeypatch.setattr(ps, "default_user_projects_json_path", lambda: jp)
    ctx.settings.project_tree_view_mode = "all"
    ctx.settings.project_tree_ux_v2 = False

    view = ProjectTreeView(ctx)
    qtbot.addWidget(view)
    try:
        view.on_activate()
        view._tree.clearSelection()
        view._set_kind_filter("workspace")
        view._set_kind_filter("all")
        assert len(view._tree.selectedItems()) >= 2
    finally:
        view.stop_background_work()


def test_summarize_from_cards_selects_tree_workspaces(qtbot, tmp_path, ctx, monkeypatch):
    """卡片「查看汇总」切到树视图并选中对应工作区."""
    import json as _json
    from app.services import project_service as ps
    from app.views.project_tree_view import _PATH_ROLE

    jp = tmp_path / "user_projects.json"
    ws1 = tmp_path / "ws1"
    ws2 = tmp_path / "ws2"
    for p in (ws1, ws2):
        _make_workspace(p)
    jp.write_text(_json.dumps({
        "version": 1,
        "projects": [
            {"name": "ws1", "directory": str(ws1)},
            {"name": "ws2", "directory": str(ws2)},
        ],
    }), encoding="utf-8")
    monkeypatch.setattr(ps, "default_user_projects_json_path", lambda: jp)
    ctx.settings.project_tree_view_mode = "all"
    ctx.settings.project_tree_layout_mode = "cards"

    view = ProjectTreeView(ctx)
    qtbot.addWidget(view)
    try:
        view.on_activate()
        assert view._body_stack.currentIndex() == 1
        view._summarize_from_cards([str(ws1), str(ws2)])
        assert view._body_stack.currentIndex() == 0
        selected = {
            str(Path(it.data(0, _PATH_ROLE)).resolve())
            for it in view._tree.selectedItems()
            if it.data(0, _PATH_ROLE)
        }
        assert selected == {str(ws1.resolve()), str(ws2.resolve())}
    finally:
        view.stop_background_work()


def test_layout_switch_preserves_selection(qtbot, tmp_path, ctx, monkeypatch):
    """树↔卡片切换时保留选中工作区."""
    import json as _json
    from app.services import project_service as ps
    from app.views.project_tree_view import _PATH_ROLE

    jp = tmp_path / "user_projects.json"
    ws1 = tmp_path / "ws1"
    ws2 = tmp_path / "ws2"
    for p in (ws1, ws2):
        _make_workspace(p)
    jp.write_text(_json.dumps({
        "version": 1,
        "projects": [
            {"name": "ws1", "directory": str(ws1)},
            {"name": "ws2", "directory": str(ws2)},
        ],
    }), encoding="utf-8")
    monkeypatch.setattr(ps, "default_user_projects_json_path", lambda: jp)
    ctx.settings.project_tree_view_mode = "all"
    ctx.settings.project_tree_layout_mode = "tree"

    view = ProjectTreeView(ctx)
    qtbot.addWidget(view)
    try:
        view.on_activate()
        view._select_tree_paths([str(ws1), str(ws2)])
        assert len(view._tree.selectedItems()) >= 2
        view._set_layout_mode("cards")
        assert set(view._card_grid.selected_directories()) == {
            str(ws1), str(ws2),
        } or {
            str(ws1.resolve()), str(ws2.resolve()),
        }.issubset({
            str(Path(d).resolve()) for d in view._card_grid.selected_directories()
        })
        selected_cards = {
            str(Path(d).resolve()) for d in view._card_grid.selected_directories()
        }
        assert selected_cards == {str(ws1.resolve()), str(ws2.resolve())}

        view._set_layout_mode("tree")
        selected_tree = {
            str(Path(it.data(0, _PATH_ROLE)).resolve())
            for it in view._tree.selectedItems()
            if it.data(0, _PATH_ROLE)
        }
        assert selected_tree == {str(ws1.resolve()), str(ws2.resolve())}
    finally:
        view.stop_background_work()


def test_tip_bar_can_be_dismissed(qtbot, ctx):
    view = ProjectTreeView(ctx)
    qtbot.addWidget(view)
    view.show()
    try:
        ctx.settings.project_tree_ux_v2 = True
        ctx.settings.project_tree_tip_dismissed = False
        view._apply_ux_profile()
        assert view._tip_bar.isHidden() is False
        view._dismiss_tip_bar()
        assert ctx.settings.project_tree_tip_dismissed is True
        assert view._tip_bar.isHidden() is True
    finally:
        view.stop_background_work()


def test_compact_project_tree_chrome_uses_horizontal_header_and_metrics(qtbot, ctx):
    view = ProjectTreeView(ctx)
    qtbot.addWidget(view)
    try:
        page_margins = view.layout().contentsMargins()
        assert page_margins.top() == 8
        assert view.layout().spacing() == 8

        header = view.findChild(type(view._tip_bar), "ProjectTreeHeader")
        title = view.findChild(QLabel, "ProjectTreeTitle")
        assert header is not None
        assert title is not None
        assert header.layout().indexOf(title) >= 0
        assert header.layout().indexOf(view._root_lbl) >= 0
        assert header.layout().contentsMargins().top() == 6

        tip_margins = view._tip_bar.layout().contentsMargins()
        assert tip_margins.top() == 4
        assert view._metric_regions is None
        left_layout = view._tree_metrics_inline.parentWidget().layout()
        metric_layout = left_layout.itemAt(0).layout()
        assert isinstance(metric_layout, QHBoxLayout)
        assert metric_layout.indexOf(view._tree_metrics_inline) >= 0
    finally:
        view.stop_background_work()

