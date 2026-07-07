"""tests/test_project_tree_survey_view.py — ProjectTreeView T5 三栏接线 + 树多选.

Spec ``survey-summary-view`` Task T5 (§2 UI / §6 红线 / §7 注释保留).

覆盖:
- 多选两个工作区节点 → 右栏 QStackedWidget 切到物种名录页 (index 2) +
  SurveySummaryPanel.inventory() 非空 + 中间 UidGroupedGrid 收到合并 groups.
- 单选一张 → 右栏回到单张详情页 (index 0,现状行为).
- 多选 → SurveySummaryPanel.set_workspaces 被调用,labels 含节点 label.
- stop_background_work → UidGroupedGrid worker 线程 quit+wait (防 hang,
  memory: workbench-timer-leak-hang).
- 冒烟:单选仍能 _enter_selected (不破坏现有进入工作区流程).

用 monkeypatch ``project_service.get_project_results`` 返回 fixture groups,
不依赖真实照片库;物种名录用真实空 ``_data/project.db`` 工作区 + 插入 specimen 行
(taxon_inventory_service 直连 db 只 SELECT)。
"""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import pytest

pytest.importorskip("PyQt6")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt  # noqa: E402

from app.views.project_tree_view import ProjectTreeView  # noqa: E402


# ── fixtures / helpers ────────────────────────────────────────────────────────


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


def _make_workspace(p: Path) -> Path:
    (p / "_data").mkdir(parents=True, exist_ok=True)
    db_path = p / "_data" / "project.db"
    conn = sqlite3.connect(str(db_path))
    # 仅建 specimens 表 (taxon_inventory_service 只 SELECT 这几列).
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS specimens (
            uid TEXT PRIMARY KEY,
            scientific_name TEXT,
            scientific_name_cn TEXT,
            family TEXT,
            genus TEXT,
            order_name TEXT
        )
        """
    )
    conn.commit()
    conn.close()
    return db_path


def _insert_species(db_path: Path, rows: list[tuple]) -> None:
    conn = sqlite3.connect(str(db_path))
    for r in rows:
        conn.execute(
            "INSERT OR REPLACE INTO specimens "
            "(uid, scientific_name, scientific_name_cn, family, genus, order_name) "
            "VALUES (?,?,?,?,?,?)",
            r,
        )
    conn.commit()
    conn.close()


def _fake_get_results(project_dir: str) -> dict:
    """Deterministic per-断面 groups so the merged grid is observable."""
    name = Path(project_dir).name
    if "断面a" in name:
        return {
            "projectDir": project_dir,
            "total": 1,
            "groups": [
                {
                    "uid": "GD-SM-A1-001-A1-2024",
                    "items": [
                        {"path": str(Path(project_dir) / "a-1.tif"),
                         "name": "a-1.tif", "seq": 1},
                    ],
                }
            ],
            "ungrouped": [],
        }
    if "断面b" in name:
        return {
            "projectDir": project_dir,
            "total": 1,
            "groups": [
                {
                    "uid": "GD-SM-B2-002-B1-2024",
                    "items": [
                        {"path": str(Path(project_dir) / "b-1.tif"),
                         "name": "b-1.tif", "seq": 1},
                    ],
                }
            ],
            "ungrouped": [],
        }
    return {"projectDir": project_dir, "total": 0, "groups": [], "ungrouped": []}


def _find_child(top, needle: str):
    for i in range(top.childCount()):
        if needle in top.child(i).text(0):
            return top.child(i)
    return None


@pytest.fixture
def ctx():
    return _FakeCtx()


@pytest.fixture
def survey_root(tmp_path, ctx):
    """A rooted survey tree with two workspace children carrying real taxa."""
    root = tmp_path / "雷州半岛多样性"
    wsa = root / "断面a"
    wsb = root / "断面b"
    dba = _make_workspace(wsa)
    dbb = _make_workspace(wsb)
    _insert_species(
        dba,
        [
            ("GD-SM-A1-001-A1-2024", "Nereis diversicolor", "沙蚕", "Nereididae",
             "Nereis", "Phyllodocida"),
            ("GD-SM-A1-002-A1-2024", "Capitella capitata", "小头虫", "Capitellidae",
             "Capitella", "Capitellida"),
        ],
    )
    _insert_species(
        dbb,
        [
            # 一种与断面a相同 → 跨断面合并;一种断面b独有.
            ("GD-SM-B2-002-B1-2024", "Nereis diversicolor", "沙蚕", "Nereididae",
             "Nereis", "Phyllodocida"),
            ("GD-SM-B2-005-B1-2024", "Perinereis aibuhitensis", "双齿围沙蚕",
             "Nereididae", "Perinereis", "Phyllodocida"),
        ],
    )
    ctx.settings.project_tree_root = str(root)
    return root


# ── tests ─────────────────────────────────────────────────────────────────────


def test_tree_is_extended_selection(qtbot, survey_root, ctx):
    view = ProjectTreeView(ctx)
    qtbot.addWidget(view)
    view.on_activate()
    try:
        sm = view._tree.selectionMode()
        from PyQt6.QtWidgets import QAbstractItemView
        assert sm == QAbstractItemView.SelectionMode.ExtendedSelection
    finally:
        view.stop_background_work()


def test_multi_select_two_workspaces_shows_survey_and_merged_grid(
    qtbot, survey_root, ctx, monkeypatch
):
    monkeypatch.setattr(
        "app.services.project_service.get_project_results", _fake_get_results
    )
    view = ProjectTreeView(ctx)
    qtbot.addWidget(view)
    view.on_activate()
    try:
        top = view._tree.topLevelItem(0)
        item_a = _find_child(top, "断面a")
        item_b = _find_child(top, "断面b")
        assert item_a is not None and item_b is not None

        view._tree.clearSelection()
        item_a.setSelected(True)
        item_b.setSelected(True)
        view._on_tree_selection_changed()  # 显式触发,确定性断言

        # 右栏 → 物种名录页
        assert view._right_stack.currentIndex() == 2
        inv = view._survey_panel.inventory()
        species = {row["scientific_name"] for row in inv}
        # 跨断面合并后 3 种,沙蚕出现于两断面
        assert "Nereis diversicolor" in species
        assert any(
            row["scientific_name"] == "Nereis diversicolor"
            and len(row["sites"]) == 2
            for row in inv
        )

        # 中间 UidGroupedGrid 收到合并 groups:断面a 1 组 + 断面b 1 组 = 2 section
        # (headless 下 isVisible() 受顶层未 show 影响;用 isHidden() 验 setVisible 效果)
        assert not view._grid_panel.isHidden()
        assert view._uid_grid.section_count() == 2
    finally:
        view.stop_background_work()


def test_single_select_shows_detail_page(qtbot, survey_root, ctx, monkeypatch):
    monkeypatch.setattr(
        "app.services.project_service.get_project_results", _fake_get_results
    )
    view = ProjectTreeView(ctx)
    qtbot.addWidget(view)
    view.on_activate()
    try:
        top = view._tree.topLevelItem(0)
        item_a = _find_child(top, "断面a")
        assert item_a is not None

        view._tree.clearSelection()
        item_a.setSelected(True)
        view._on_tree_selection_changed()

        # 右栏 → 单张详情页 (现状)
        assert view._right_stack.currentIndex() == 0
        # 单选隐藏中间网格 (headless 用 isHidden 验 setVisible)
        assert view._grid_panel.isHidden()
        # 现有详情字段仍填充
        assert "断面a" in view._detail_name.text()
    finally:
        view.stop_background_work()


def test_multi_select_passes_labels_with_node_label(
    qtbot, survey_root, ctx, monkeypatch
):
    monkeypatch.setattr(
        "app.services.project_service.get_project_results", _fake_get_results
    )
    view = ProjectTreeView(ctx)
    qtbot.addWidget(view)
    view.on_activate()
    try:
        top = view._tree.topLevelItem(0)
        item_a = _find_child(top, "断面a")
        item_b = _find_child(top, "断面b")

        captured = {}
        orig = view._survey_panel.set_workspaces

        def spy(workspace_dirs, labels=None):
            captured["dirs"] = list(workspace_dirs)
            captured["labels"] = dict(labels) if labels else {}
            return orig(workspace_dirs, labels=labels)

        view._survey_panel.set_workspaces = spy

        view._tree.clearSelection()
        item_a.setSelected(True)
        item_b.setSelected(True)
        view._on_tree_selection_changed()

        # set_workspaces 被调用,两个工作区目录都传了
        assert len(captured.get("dirs", [])) == 2
        # labels 含节点 label (节点文本含断面名)
        label_vals = list(captured.get("labels", {}).values())
        assert any("断面a" in v for v in label_vals)
        assert any("断面b" in v for v in label_vals)
    finally:
        view.stop_background_work()


def test_stop_background_work_joins_grid_worker(qtbot, survey_root, ctx, monkeypatch):
    monkeypatch.setattr(
        "app.services.project_service.get_project_results", _fake_get_results
    )
    view = ProjectTreeView(ctx)
    qtbot.addWidget(view)
    view.on_activate()
    grid = view._uid_grid
    # 多选一次,让 worker 真正被使用
    top = view._tree.topLevelItem(0)
    item_a = _find_child(top, "断面a")
    item_b = _find_child(top, "断面b")
    view._tree.clearSelection()
    item_a.setSelected(True)
    item_b.setSelected(True)
    view._on_tree_selection_changed()

    view.stop_background_work()
    # quit + wait 后线程不再运行 —— 防 close→reopen→必须重启 (memory)
    assert not grid._thread.isRunning()


def test_single_select_enter_still_smoke(qtbot, survey_root, ctx, monkeypatch):
    """冒烟:多选改造后,单选进入工作区流程不被破坏."""
    # 隔离 recent-workspaces json 写入到 tmp,不动仓库 tracked 文件.
    recent_json = survey_root.parent / "user_projects.json"
    monkeypatch.setattr(
        "app.services.project_service.default_user_projects_json_path",
        lambda: str(recent_json),
    )
    view = ProjectTreeView(ctx)
    qtbot.addWidget(view)
    view.on_activate()
    try:
        top = view._tree.topLevelItem(0)
        item_a = _find_child(top, "断面a")
        assert item_a is not None
        view._tree.clearSelection()
        item_a.setSelected(True)
        view._tree.setCurrentItem(item_a)
        view._on_tree_selection_changed()

        # 进入工作区按钮可用 (单选路径未被多选改造破坏)
        assert view._btn_enter.isEnabled()

        with qtbot.waitSignal(view.enter_workspace_requested, timeout=2000):
            view._enter_selected()

        leaf = (survey_root / "断面a").resolve()
        assert ctx.current_project_dir == str(leaf)
    finally:
        view.stop_background_work()
