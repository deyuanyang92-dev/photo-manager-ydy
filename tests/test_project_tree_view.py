"""tests/test_project_tree_view.py — 项目树视图（headless, pytest-qt）."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

pytest.importorskip("PyQt6")

from PyQt6.QtCore import QItemSelectionModel, Qt
from PyQt6.QtGui import QColor, QPixmap
from PyQt6.QtWidgets import QAbstractItemView, QDialog, QHBoxLayout, QLabel

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
    # 用户只看普通文件夹名；是否已准备拍摄保留在内部 kind，不污染树标签。
    assert any(t == "断面a" for t in child_texts)
    assert any(t == "断面b" for t in child_texts)
    assert any(
        top.child(i).text(0) == "断面a"
        and top.child(i).data(0, _KIND_ROLE) == "workspace"
        for i in range(top.childCount())
    )


def test_tree_has_context_menu(qtbot, tmp_path, ctx):
    root = tmp_path / "survey"
    _make_workspace(root)
    ctx.settings.project_tree_root = str(root)

    view = ProjectTreeView(ctx)
    qtbot.addWidget(view)
    view.on_activate()

    assert view._tree.contextMenuPolicy() == Qt.ContextMenuPolicy.CustomContextMenu
    assert view._tree.rootIsDecorated(), "顶层项目必须显示展开箭头，才能进入子目录层级"


def test_unavailable_project_context_menu_only_offers_recovery_actions(
    qtbot, tmp_path, ctx, monkeypatch
):
    """磁盘未连接时不展示重命名、封面、汇总等必然失败的假操作。"""
    from PyQt6.QtWidgets import QMenu
    from app.services import project_service as ps

    jp = _patch_recent_json(monkeypatch, tmp_path)
    missing = tmp_path / "已拔出的移动硬盘" / "项目A"
    _seed_projects_json(jp, [{"name": "项目A", "directory": str(missing)}])
    ctx.settings.project_tree_view_mode = "all"
    view = ProjectTreeView(ctx)
    qtbot.addWidget(view)
    view.on_activate()
    item = view._tree.topLevelItem(0)
    captured = []
    monkeypatch.setattr(QMenu, "exec", lambda menu, *args: captured.extend(
        action.text() for action in menu.actions() if not action.isSeparator()
    ))

    view._show_tree_context_menu(view._tree.visualItemRect(item).center())

    assert "指到新位置…" in captured
    assert "复制路径" in captured
    assert "属性" in captured
    assert "打开文件夹" not in captured
    assert "重命名…" not in captured
    assert "汇总导出…" not in captured


def test_open_directory_uses_shared_file_manager(qtbot, tmp_path, ctx, monkeypatch):
    from app.utils.file_manager import OpenDirectoryResult

    view = ProjectTreeView(ctx)
    qtbot.addWidget(view)
    opened = []
    monkeypatch.setattr(
        "app.utils.file_manager.open_directory_detailed",
        lambda path: opened.append(path) or OpenDirectoryResult(True, path),
    )

    view._open_directory(str(tmp_path))

    assert opened == [str(tmp_path)]


def test_open_directory_displays_the_system_error(qtbot, tmp_path, ctx, monkeypatch):
    from app.utils.file_manager import OpenDirectoryResult

    view = ProjectTreeView(ctx)
    qtbot.addWidget(view)
    warnings = []
    monkeypatch.setattr(
        "app.utils.file_manager.open_directory_detailed",
        lambda path: OpenDirectoryResult(False, path, "ShellExecute access denied"),
    )
    monkeypatch.setattr(
        "app.views.project_tree_view.ui.warn",
        lambda _parent, title, text: warnings.append((title, text)),
    )

    view._open_directory(str(tmp_path))

    assert warnings
    assert "ShellExecute access denied" in warnings[0][1]


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


def test_selecting_empty_child_clears_parent_workspace_data(qtbot, tmp_path, ctx, monkeypatch):
    """有数据工作区 -> 空子目录时，旧编号和照片不能继续留在中栏。"""
    from app.db.db_manager import open_project_db_private

    root = tmp_path / "ceshi10"
    empty_child = root / "bb"
    empty_child.mkdir(parents=True)
    db = open_project_db_private(str(root), create=True)
    try:
        db.execute("INSERT INTO specimens (uid) VALUES (?)", ("PARENT-001",))
        db.commit()
    finally:
        db.close()
    ctx.settings.project_tree_root = str(root)

    view = ProjectTreeView(ctx)
    qtbot.addWidget(view)
    monkeypatch.setattr(view, "_refresh_survey_overview", lambda *_a, **_k: None)
    view.on_activate()

    qtbot.waitUntil(
        lambda: (
            getattr(view, "_current_summary_result", None) is not None
            and view._specimen_table.rowCount() == 1
        ),
        timeout=8000,
    )
    assert view._current_ws_dirs == [str(root)]

    top = view._tree.topLevelItem(0)
    child = next(
        top.child(i) for i in range(top.childCount())
        if top.child(i).text(0) == "bb"
    )
    view.show()
    qtbot.waitExposed(view)
    view._tree.scrollToItem(child)
    qtbot.mouseClick(
        view._tree.viewport(),
        Qt.MouseButton.LeftButton,
        pos=view._tree.visualItemRect(child).center(),
    )

    assert view._tree.currentItem() is child
    assert child.isSelected()
    assert view._effective_scope_labeled() == []
    assert view._current_ws_dirs == []
    assert view._current_summary_result is None
    assert view._specimen_table.rowCount() == 0
    assert not view._grid_idle_hint.isHidden()
    assert "bb" in view._grid_idle_hint.text()
    view._stop_summary_query_worker(wait_ms=2000)


def test_plain_item_click_recovers_when_qt_keeps_old_tree_anchor(qtbot, tmp_path, ctx):
    root = tmp_path / "ceshi10"
    child_path = root / "bb"
    child_path.mkdir(parents=True)
    _make_workspace(root)
    ctx.settings.project_tree_root = str(root)

    view = ProjectTreeView(ctx)
    qtbot.addWidget(view)
    view.on_activate()
    top = view._tree.topLevelItem(0)
    child = next(
        top.child(i) for i in range(top.childCount())
        if top.child(i).text(0) == "bb"
    )

    view._tree.blockSignals(True)
    try:
        view._tree.clearSelection()
        view._tree.setCurrentItem(top)
        top.setSelected(True)
    finally:
        view._tree.blockSignals(False)
    view._selection_items = [top]
    view._scope_labeled = view._labeled_workspaces_from_items([top])

    view._on_tree_item_clicked(child, 0)

    assert view._tree.currentItem() is child
    assert child.isSelected()
    assert not top.isSelected()
    assert view._effective_scope_labeled() == []
    assert view._current_ws_dirs == []
    assert view._specimen_table.rowCount() == 0


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
    # §7 旧文案「（未选根目录）」—— 空态现在直接告诉用户下一步怎么做(2026-07-13)
    assert "还没有项目" in view._root_lbl.text()
    # §7 旧空态文案(「还没有选择调查根目录，也没有已记录的项目」) —— 只描述状态、不给出路。
    #   新文案直接告诉用户下一步: 点「＋ 项目」新建; 有旧数据就「扫描磁盘」找回(2026-07-13)。
    assert "＋ 项目" in view._empty_state.text()
    assert "扫描磁盘" in view._empty_state.text()


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
    # §7 旧文案「（全部已建项目 · N）」—— 标题栏现在给「N 个项目 · 数据位置」,
    #   取代那条被 360px 截断的绝对路径(用户 2026-07-13 截图)。
    assert "个项目" in view._root_lbl.text()
    assert "2" in view._root_lbl.text()
    labels = [view._tree.topLevelItem(i).text(0) for i in range(2)]
    # most-recent-first: dup(id4) was last in json but deduped against id1's dir,
    # so the tail-survivor is 乙(workspace) on top, then 甲.
    assert any(t == "乙" for t in labels)
    assert any(t == "甲" for t in labels)
    assert any(
        view._tree.topLevelItem(i).text(0) == "乙"
        and view._tree.topLevelItem(i).data(0, _KIND_ROLE) == "workspace"
        for i in range(view._tree.topLevelItemCount())
    )
    # 乙 (the later entry) comes first
    assert "乙" in labels[0]
    assert "甲" in labels[1]


def test_distinct_case_paths_not_grouped_on_case_sensitive_fs(
    qtbot, tmp_path, ctx, monkeypatch
):
    # BUG: grouping key used casefold() unconditionally, so two genuinely
    # distinct paths differing only in case were merged into one tree node on
    # a case-sensitive filesystem (Linux/macOS). They must stay separate there.
    app_dir = tmp_path / "isolated-app"
    app_dir.mkdir()
    monkeypatch.chdir(app_dir)
    recent_json = _patch_recent_json(monkeypatch, tmp_path)

    lower = tmp_path / "ceshi"
    upper = tmp_path / "CESHI"
    lower.mkdir()
    # Claude Code 修改 2026-07-14 — codex 在 Windows 批跑发现此测试报错(非预期的
    # skip): 大小写不敏感文件系统上 "CESHI" 和已存在的 "ceshi" 是同一路径,
    # upper.mkdir() 会直接抛 FileExistsError, 原代码在 mkdir 之后才判断是否该
    # skip, 根本走不到那一行。改成 try/except, 撞到就地判 skip。
    try:
        upper.mkdir()
    except FileExistsError:
        pytest.skip("filesystem is case-insensitive; grouping-by-case is moot")
    # Only meaningful where the filesystem actually keeps the two distinct.
    if lower.resolve() == upper.resolve() or not upper.exists():
        pytest.skip("filesystem is case-insensitive; grouping-by-case is moot")

    projects = [
        {"id": "1", "name": "小写", "directory": str(lower)},
        {"id": "2", "name": "大写", "directory": str(upper)},
    ]
    _seed_projects_json(recent_json, projects)

    view = ProjectTreeView(ctx)
    qtbot.addWidget(view)
    view.on_activate()

    assert view._tree.topLevelItemCount() == 2
    labels = {view._tree.topLevelItem(i).text(0) for i in range(2)}
    assert labels == {"小写", "大写"}


def test_project_facets_filter_registered_metadata(qtbot, tmp_path, ctx, monkeypatch):
    recent_json = _patch_recent_json(monkeypatch, tmp_path)
    projects = []
    for name, year, region, collector in (
        ("项目A", "2026", "广东", "李明"),
        ("项目B", "2025", "广东", "张华"),
        ("项目C", "2026", "海南", "李明"),
    ):
        directory = tmp_path / name
        directory.mkdir()
        projects.append({
            "name": name,
            "directory": str(directory),
            "year": year,
            "location": region,
            "collector": collector,
        })
    _seed_projects_json(recent_json, projects)
    ctx.settings.project_tree_view_mode = "all"

    view = ProjectTreeView(ctx)
    qtbot.addWidget(view)
    view.on_activate()

    view._project_region_filter.setCurrentIndex(
        view._project_region_filter.findData("广东")
    )
    assert sum(
        not view._tree.topLevelItem(i).isHidden()
        for i in range(view._tree.topLevelItemCount())
    ) == 2

    view._project_collector_filter.setCurrentIndex(
        view._project_collector_filter.findData("李明")
    )
    view._project_year_filter.setCurrentIndex(
        view._project_year_filter.findData("2026")
    )
    assert view._tree_count_lbl.text() == "1/3 个项目"
    visible = [
        view._tree.topLevelItem(i).text(0)
        for i in range(view._tree.topLevelItemCount())
        if not view._tree.topLevelItem(i).isHidden()
    ]
    assert visible == ["项目A"]

    view._clear_project_filters()
    assert all(
        not view._tree.topLevelItem(i).isHidden()
        for i in range(view._tree.topLevelItemCount())
    )


def test_project_facets_use_compact_toolbar_with_contextual_reset(
    qtbot, tmp_path, ctx, monkeypatch
):
    recent_json = _patch_recent_json(monkeypatch, tmp_path)
    project = tmp_path / "项目A"
    project.mkdir()
    _seed_projects_json(recent_json, [{
        "name": "项目A",
        "directory": str(project),
        "year": "2026",
        "location": "广西",
        "collector": "李明",
    }])
    ctx.settings.project_tree_view_mode = "all"

    view = ProjectTreeView(ctx)
    qtbot.addWidget(view)
    view.on_activate()

    assert view._project_filter_label.text() == "筛选"
    assert view._project_year_filter.itemText(0) == "全部时间"
    assert view._project_region_filter.itemText(0) == "全部地区"
    assert view._project_collector_filter.itemText(0) == "全部采集人"
    assert view._project_year_filter.maximumWidth() <= 160
    assert view._clear_project_filters_btn.isHidden()

    view._project_region_filter.setCurrentIndex(
        view._project_region_filter.findData("广西")
    )
    assert not view._clear_project_filters_btn.isHidden()
    assert view._clear_project_filters_btn.text() == "清除筛选"

    view._clear_project_filters()
    assert view._clear_project_filters_btn.isHidden()


def test_recent_workspace_under_registered_root_is_not_a_duplicate_project(
    qtbot, tmp_path, ctx, monkeypatch
):
    recent_json = _patch_recent_json(monkeypatch, tmp_path)
    root = tmp_path / "广西"
    workspace = root / "区域1" / "断面1"
    workspace.mkdir(parents=True)
    _make_workspace(workspace)
    _seed_projects_json(recent_json, [
        {
            "name": "广西",
            "directory": str(root),
            "root": str(root),
            "year": "2026",
            "location": "广西",
        },
        {
            "name": "广西 / 区域1 / 断面1",
            "directory": str(workspace),
            "root": str(root),
            "lastOpenedAt": 2,
        },
    ])
    ctx.settings.project_tree_view_mode = "all"

    view = ProjectTreeView(ctx)
    qtbot.addWidget(view)
    view.on_activate()

    assert view._tree.topLevelItemCount() == 1
    project_item = view._tree.topLevelItem(0)
    assert project_item.text(0) == "广西"
    region_item = next(
        project_item.child(i)
        for i in range(project_item.childCount())
        if project_item.child(i).text(0) == "区域1"
    )
    assert any(
        region_item.child(i).text(0) == "断面1"
        for i in range(region_item.childCount())
    )


def test_current_workspace_is_selected_without_hiding_other_projects(
    qtbot, tmp_path, ctx, monkeypatch
):
    from app.views.project_tree_view import _PATH_ROLE

    recent_json = _patch_recent_json(monkeypatch, tmp_path)
    project_a = tmp_path / "项目A"
    project_b = tmp_path / "项目B"
    project_a.mkdir()
    project_b.mkdir()
    _seed_projects_json(recent_json, [
        {"name": "项目A", "directory": str(project_a)},
        {"name": "项目B", "directory": str(project_b)},
    ])
    ctx.settings.project_tree_view_mode = "all"
    ctx.current_project_dir = str(project_b)
    ctx.current_project_root = str(project_b)

    view = ProjectTreeView(ctx)
    qtbot.addWidget(view)
    view.on_activate()

    assert view._tree.topLevelItemCount() == 2
    assert ctx.settings.project_tree_view_mode == "all"
    assert Path(view._tree.currentItem().data(0, _PATH_ROLE)).resolve() == project_b.resolve()


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
    assert any(text == "ceshi6" for text in labels)
    assert any(
        view._tree.topLevelItem(i).text(0) == "ceshi6"
        and view._tree.topLevelItem(i).data(0, _KIND_ROLE) == "workspace"
        for i in range(view._tree.topLevelItemCount())
    )
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
    assert view._tree.currentItem().text(0) == "断面a"
    assert view._tree.currentItem().data(0, _KIND_ROLE) == "workspace"
    assert view._detail_kind.text() == "拍摄目录"
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
    assert str(scan_root.resolve()) in ctx.settings.project_scan_roots


def test_scan_disk_registers_project_root_without_duplicate_children(
    qtbot, tmp_path, ctx, monkeypatch
):
    """扫描软件创建的项目时登记项目根，不把内部断面重复列成顶层项目。"""
    import json as _json
    from app.services import project_service as ps
    from app.utils import ui as _ui

    scan_root = tmp_path / "固定项目位置"
    project = scan_root / "航次2026"
    (project / "_data").mkdir(parents=True)
    (project / "_data" / "region.json").write_text("{}", encoding="utf-8")
    _make_workspace(project / "断面A")

    jp = tmp_path / "user_projects.json"
    jp.write_text(_json.dumps({"version": 1, "projects": []}), encoding="utf-8")
    monkeypatch.setattr(ps, "default_user_projects_json_path", lambda: str(jp))
    monkeypatch.setattr(_ui, "get_existing_directory", lambda *a, **k: str(scan_root))
    monkeypatch.setattr(_ui, "info", lambda *a, **k: None)

    view = ProjectTreeView(ctx)
    qtbot.addWidget(view)
    view._scan_disk()

    projects = ps.list_projects(str(jp))
    assert len(projects) == 1
    assert projects[0]["name"] == "航次2026"
    assert projects[0]["isProjectRoot"] is True


def test_empty_catalog_recovers_from_saved_scan_location(
    qtbot, tmp_path, ctx, monkeypatch
):
    """升级后索引为空时，进入项目树应从用户保存的固定位置自动恢复。"""
    from app.services import project_service as ps

    scan_root = tmp_path / "固定项目位置"
    project = scan_root / "历史项目"
    (project / "_data").mkdir(parents=True)
    (project / "_data" / "region.json").write_text("{}", encoding="utf-8")
    jp = tmp_path / "user_projects.json"
    jp.write_text('{"version": 1, "projects": []}', encoding="utf-8")
    monkeypatch.setattr(ps, "default_user_projects_json_path", lambda: str(jp))
    ctx.settings.project_scan_roots = [str(scan_root)]
    ctx.settings.project_tree_view_mode = "all"

    view = ProjectTreeView(ctx)
    qtbot.addWidget(view)
    view.on_activate()

    projects = ps.list_projects(str(jp))
    assert len(projects) == 1
    assert projects[0]["name"] == "历史项目"


def test_optional_project_library_directory_is_saved_scanned_and_used_for_new_project(
    qtbot, tmp_path, ctx, monkeypatch
):
    """统一保存目录是可选默认值，同时不替代扫描/导入能力。"""
    from app.services import project_service as ps
    from app.utils import ui as _ui

    library = tmp_path / "标本项目库"
    existing = library / "已有项目"
    (existing / "_data").mkdir(parents=True)
    (existing / "_data" / "region.json").write_text("{}", encoding="utf-8")
    jp = tmp_path / "user_projects.json"
    jp.write_text('{"version": 1, "projects": []}', encoding="utf-8")
    monkeypatch.setattr(ps, "default_user_projects_json_path", lambda: str(jp))
    monkeypatch.setattr(_ui, "get_existing_directory", lambda *a, **k: str(library))
    monkeypatch.setattr(_ui, "info", lambda *a, **k: None)

    view = ProjectTreeView(ctx)
    qtbot.addWidget(view)
    view._choose_project_library_directory()

    assert ctx.settings.project_library_dir == str(library.resolve())
    assert str(library.resolve()) in ctx.settings.project_scan_roots
    assert view._btn_library_dir.text() == "项目库目录 ✓"
    assert [p["name"] for p in ps.list_projects(str(jp))] == ["已有项目"]

    captured = {}

    class _CancelledDialog:
        def __init__(self, *args, **kwargs):
            captured.update(kwargs)

        def exec(self):
            return QDialog.DialogCode.Rejected

    monkeypatch.setattr(
        "app.widgets.new_survey_project_dialog.NewSurveyProjectDialog",
        _CancelledDialog,
    )
    view._new_region()
    assert captured["default_parent_dir"] == str(library.resolve())


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


def test_manual_import_recognizes_existing_project_root(qtbot, tmp_path, ctx, monkeypatch):
    from app.services import project_service as ps
    from app.utils import ui as _ui

    project = tmp_path / "旧版本项目"
    (project / "_data").mkdir(parents=True)
    (project / "_data" / "region.json").write_text("{}", encoding="utf-8")
    jp = tmp_path / "user_projects.json"
    jp.write_text('{"version": 1, "projects": []}', encoding="utf-8")
    monkeypatch.setattr(ps, "default_user_projects_json_path", lambda: str(jp))
    monkeypatch.setattr(_ui, "get_existing_directory", lambda *a, **k: str(project))
    monkeypatch.setattr(_ui, "info", lambda *a, **k: None)

    view = ProjectTreeView(ctx)
    qtbot.addWidget(view)
    view._add_workspace_manual()

    projects = ps.list_projects(str(jp))
    assert len(projects) == 1
    assert projects[0].get("isProjectRoot") is True


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
    # §7 旧断言: assert len(view._tree.selectedItems()) == 3
    #   —— 冻结的是「一打开页面就自动全选所有拍摄目录」这个行为, 而它正是「死按键」的成因:
    #   多选状态下「设为当前拍摄目录」被禁用 → 用户什么都没点, 大绿按钮已经是死的
    #   (用户 2026-07-13 报障)。现在打开只单选第一个; 全选是**用户主动**点按钮的结果 ——
    #   本测试真正要测的是下面那半段。
    assert len(view._tree.selectedItems()) <= 1
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
    """行排序走**垂直表头拖动**, 不是单元格 InternalMove。

    §7 旧断言(Fable 5, 2026-07-12 改): 曾经要求
        table.dragDropMode() == InternalMove / dragEnabled / acceptDrops
    —— 那正是丢数据的配置: QTableWidget 不发 rowsMoved, InternalMove 实际走
    dropMimeData 覆盖单元格 + 删源行, 用户拖一次就少一行数据
    (见 tests/test_summary_table_reorder.py)。
    """
    view = ProjectTreeView(ctx)
    qtbot.addWidget(view)
    try:
        table = view._specimen_table
        assert table.dragDropMode() == QAbstractItemView.DragDropMode.NoDragDrop
        assert table.acceptDrops() is False
        assert table.verticalHeader().sectionsMovable() is True   # 行排序:拖行号
        hdr = table.horizontalHeader()
        assert hdr.sectionsMovable() is True                      # 列排序:拖表头
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


def test_ux_v2_keeps_project_scope_switch_visible(qtbot, ctx):
    """精简模式仍保留「全部/按根」，避免用户误以为其他项目消失。"""
    view = ProjectTreeView(ctx)
    qtbot.addWidget(view)
    view.show()
    try:
        assert ctx.settings.project_tree_ux_v2 is True
        assert view._mode_row_host.isHidden() is False
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


def test_new_subfolder_button_exists_in_toolbar(qtbot, tmp_path, ctx):
    """「新建子目录」提到工具栏(需求 2026-07-12), 不再只藏在右键菜单里。"""
    root = tmp_path / "survey"
    _make_workspace(root)
    ctx.settings.project_tree_root = str(root)

    view = ProjectTreeView(ctx)
    qtbot.addWidget(view)
    view.on_activate()

    assert hasattr(view, "_btn_new_subfolder")
    assert view._btn_new_subfolder.isVisible() or not view._btn_new_subfolder.isHidden()
    assert hasattr(view, "_btn_new_project")
    assert view._btn_new_project.isVisible() or not view._btn_new_project.isHidden()
    assert "追加层级" in view._btn_new_subfolder.text()


def _patch_append_dialog(
    monkeypatch,
    *,
    name: str,
    is_workspace: bool = False,
    captured: dict | None = None,
):
    class _FakeAppendDialog:
        def __init__(self, *args, **kwargs):
            self.target = kwargs["append_target_dir"]
            self.root = kwargs.get("project_root", "")
            if captured is not None:
                captured.update(kwargs)

        def exec(self):
            return QDialog.DialogCode.Accepted

        def values(self):
            return {
                "mode": "append",
                "target_dir": self.target,
                "project_root": self.root,
                "structure": [{
                    "name": name,
                    "type": "断面",
                    "is_workspace": is_workspace,
                    "children": [],
                }],
            }

    monkeypatch.setattr(
        "app.widgets.new_survey_project_dialog.NewSurveyProjectDialog",
        _FakeAppendDialog,
    )
    monkeypatch.setattr("app.utils.ui.info", lambda *args, **kwargs: None)


def test_new_subfolder_button_creates_plain_dir_not_workspace(
    qtbot, tmp_path, ctx, monkeypatch
):
    """点「新建子目录」建的是**空壳**, 不是工作区(需求 2026-07-12)。

    "然后我可以在目录中, 自由创建子目录" —— 中间层必须能当纯容器, 只有真正进去拍的
    那层才初始化为工作区。所以这里绝不能建成 workspace。
    """
    from app.services import project_tree_service as pts

    root = tmp_path / "survey"
    (root / "_data").mkdir(parents=True)
    (root / "_data" / "region.json").write_text("{}", encoding="utf-8")
    ctx.settings.project_tree_root = str(root)

    view = ProjectTreeView(ctx)
    qtbot.addWidget(view)
    view.on_activate()

    _patch_append_dialog(monkeypatch, name="断面A", is_workspace=False)
    view._new_subfolder()

    child = root / "断面A"
    assert child.is_dir()
    assert not pts.is_workspace(str(child))
    assert not (child / "_data").exists()


def test_new_child_appears_under_project_and_is_selected_in_all_projects(
    qtbot, tmp_path, ctx, monkeypatch
):
    """全局项目树中创建子目录后，要立即显示层级并允许下一步进入。"""
    from app.services import project_service as ps
    from app.views.project_tree_view import _PATH_ROLE

    project = tmp_path / "b"
    (project / "_data").mkdir(parents=True)
    (project / "_data" / "region.json").write_text("{}", encoding="utf-8")
    jp = _patch_recent_json(monkeypatch, tmp_path)
    _seed_projects_json(jp, [
        {"name": "b", "directory": str(project), "isProjectRoot": True},
    ])
    ctx.settings.project_tree_view_mode = "all"
    ctx.settings.project_tree_root = None
    _patch_append_dialog(monkeypatch, name="子目录1", is_workspace=False)

    view = ProjectTreeView(ctx)
    qtbot.addWidget(view)
    view.on_activate()
    root_item = view._tree.topLevelItem(0)
    view._select_tree_item(root_item)
    view._new_subfolder()

    root_item = view._tree.topLevelItem(0)
    assert root_item.childCount() == 1
    assert root_item.text(1) == "1"
    child_item = root_item.child(0)
    assert child_item.text(0) == "子目录1"
    assert root_item.isExpanded()
    assert view._tree.currentItem() is child_item
    assert Path(child_item.data(0, _PATH_ROLE)) == project / "子目录1"
    assert view._btn_enter.isEnabled()


def test_geneious_style_project_library_keeps_arbitrary_folder_hierarchy(
    qtbot, tmp_path, ctx, monkeypatch
):
    """项目库按 项目→断面→采样点 展示，不压平成当前工作区列表。"""
    project = tmp_path / "项目b"
    leaf = project / "断面A" / "采样点1"
    leaf.mkdir(parents=True)
    (project / "_data").mkdir()
    (project / "_data" / "region.json").write_text("{}", encoding="utf-8")
    jp = _patch_recent_json(monkeypatch, tmp_path)
    _seed_projects_json(jp, [
        {"name": "项目b", "directory": str(project), "isProjectRoot": True},
    ])
    ctx.settings.project_tree_view_mode = "all"
    ctx.settings.project_tree_root = None

    view = ProjectTreeView(ctx)
    qtbot.addWidget(view)
    view.on_activate()

    titles = [label.text() for label in view.findChildren(QLabel)]
    assert "项目库" in titles
    project_item = view._tree.topLevelItem(0)
    assert project_item.text(0) == "项目b"
    assert project_item.text(1) == "1"
    assert project_item.isExpanded()
    section_item = project_item.child(0)
    assert section_item.text(0) == "断面A"
    assert section_item.text(1) == "1"
    assert section_item.child(0).text(0) == "采样点1"


def test_project_settings_action_opens_dialog_on_selected_node(
    qtbot, tmp_path, ctx, monkeypatch
):
    """「项目设置」按钮/菜单在选中节点上开设置对话框(需求 2026-07-12)。"""
    root = tmp_path / "survey"
    _make_workspace(root)
    ctx.settings.project_tree_root = str(root)

    view = ProjectTreeView(ctx)
    qtbot.addWidget(view)
    view.on_activate()

    captured = {}

    def _fake_open(parent, ctx_arg, project_dir):
        captured["project_dir"] = project_dir

    monkeypatch.setattr(
        "app.widgets.project_settings_dialog.open_project_settings_dialog",
        _fake_open,
    )
    view._open_node_settings()

    assert captured.get("project_dir")


def test_focus_project_selects_new_project_without_hiding_others(
    qtbot, tmp_path, ctx, monkeypatch
):
    """聚焦新项目只能改变选中项，不能把全部项目切成单项目模式。"""
    old = tmp_path / "旧项目"
    old.mkdir()
    new_root = tmp_path / "江苏盐城2026"
    new_root.mkdir()
    recent_json = _patch_recent_json(monkeypatch, tmp_path)
    _seed_projects_json(recent_json, [
        {"name": "旧项目", "directory": str(old), "isProjectRoot": True},
        {"name": "江苏盐城2026", "directory": str(new_root), "isProjectRoot": True},
    ])
    ctx.settings.project_tree_view_mode = "all"
    ctx.settings.project_tree_root = None

    view = ProjectTreeView(ctx)
    qtbot.addWidget(view)
    view.on_activate()

    view.focus_project(str(new_root))

    assert ctx.settings.project_tree_view_mode == "all"
    assert view._root is None
    top_names = [
        view._tree.topLevelItem(i).text(0)
        for i in range(view._tree.topLevelItemCount())
    ]
    assert any("旧项目" in name for name in top_names), top_names
    assert any("江苏盐城2026" in name for name in top_names), top_names
    cur = view._tree.currentItem()
    assert cur is not None and "江苏盐城2026" in cur.text(0)


def test_project_tree_plus_project_keeps_existing_projects(
    qtbot, tmp_path, ctx, monkeypatch
):
    """截图中的「＋项目」入口创建新项目后，旧项目必须仍在全部项目树中。"""
    old = tmp_path / "旧项目"
    old.mkdir()
    recent_json = _patch_recent_json(monkeypatch, tmp_path)
    _seed_projects_json(recent_json, [
        {"name": "旧项目", "directory": str(old), "isProjectRoot": True},
    ])
    # 模拟用户此前只浏览旧项目根；新建项目必须自动回到全部项目。
    ctx.settings.project_tree_view_mode = "rooted"
    ctx.settings.project_tree_root = str(old)

    class _FakeDialog:
        def __init__(self, *args, **kwargs):
            pass

        def exec(self):
            return QDialog.DialogCode.Accepted

        def values(self):
            return {"parent_dir": str(tmp_path), "name": "新项目"}

    monkeypatch.setattr(
        "app.widgets.new_survey_project_dialog.NewSurveyProjectDialog", _FakeDialog
    )
    monkeypatch.setattr("app.utils.ui.info", lambda *args, **kwargs: None)

    view = ProjectTreeView(ctx)
    qtbot.addWidget(view)
    view.on_activate()
    view._new_region()

    top_names = [
        view._tree.topLevelItem(i).text(0)
        for i in range(view._tree.topLevelItemCount())
    ]
    assert ctx.settings.project_tree_view_mode == "all"
    assert any("旧项目" in name for name in top_names), top_names
    assert any("新项目" in name for name in top_names), top_names


def test_all_projects_switch_is_visible_in_compact_ui(qtbot, ctx):
    """数据范围开关不能藏在省略号里，否则用户无法发现怎样恢复全部项目。"""
    ctx.settings.project_tree_ux_v2 = True
    view = ProjectTreeView(ctx)
    qtbot.addWidget(view)
    view.show()
    view._apply_ux_profile()

    assert view._mode_row_host.isVisible()
    assert view._btn_mode_all.isVisible()


def test_new_subfolder_never_builds_outside_current_root(qtbot, tmp_path, ctx, monkeypatch):
    """安全闸: 选中节点若不在当前根之下, 绝不把子目录建到那里去。"""
    from app.views.project_tree_view import _PATH_ROLE

    root = tmp_path / "本项目"
    root.mkdir()
    outsider = tmp_path / "别的项目"
    outsider.mkdir()
    ctx.settings.project_tree_root = str(root)

    view = ProjectTreeView(ctx)
    qtbot.addWidget(view)
    view.on_activate()
    view._root = str(root)

    # 伪造一个「选中了别的项目」的状态
    monkeypatch.setattr(view, "_selected_path", lambda: str(outsider))
    captured = {}
    _patch_append_dialog(
        monkeypatch,
        name="断面A",
        is_workspace=False,
        captured=captured,
    )
    view._new_subfolder()

    assert captured["append_target_dir"] == str(root)
    assert (root / "断面A").is_dir()            # 退回当前根
    assert not (outsider / "断面A").exists()    # 绝不建到别人家
