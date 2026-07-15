"""顶栏项目与照片保存位置面板契约.

契约（都在一个顶栏位置弹层里）:
1. 面板顶部有「最近」chips（来自真实 user_projects.json, 按 lastOpenedAt 降序,
   带项目上下文, 点一下 = 直接进入）;
2. 「项目 / 拍摄位置」行显示当前目标，点击进入唯一的完整项目树;
3. 明确提供新建调查项目、独立工作区、打开已有项目/工作区三种场景;
4. 顶栏不递归展开全量目录，兼容大项目库。
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtWidgets import QMenu, QPushButton

from app.app_context import AppContext
from app.widgets.workspace_breadcrumb import WorkspaceBreadcrumb


@pytest.fixture
def env(tmp_path, monkeypatch):
    jp = tmp_path / "user_projects.json"
    root = tmp_path / "航次2026"
    ws = root / "断面A"
    (ws / "_data").mkdir(parents=True)
    (ws / "_data" / "project.db").write_bytes(b"")
    other = tmp_path / "潮间带专项" / "B1"
    (other / "_data").mkdir(parents=True)
    (other / "_data" / "project.db").write_bytes(b"")

    now = time.time()
    jp.write_text(json.dumps({"version": 1, "projects": [
        {"id": "1", "name": "断面A", "directory": str(ws), "dir": str(ws),
         "root": str(root), "lastOpenedAt": now - 100},
        {"id": "2", "name": "B1", "directory": str(other), "dir": str(other),
         "root": str(tmp_path / "潮间带专项"), "lastOpenedAt": now - 10},
    ]}, ensure_ascii=False), encoding="utf-8")

    import app.services.project_service as ps
    ps.clear_project_list_cache(str(jp))
    monkeypatch.setattr(ps, "default_user_projects_json_path", lambda: str(jp))

    ctx = AppContext()
    ctx.current_project_dir = str(ws)
    ctx.current_project_root = str(root)
    return ctx, ws, other


def _panel(bc: WorkspaceBreadcrumb):
    menu = QMenu()
    return bc._add_location_panel(menu), menu


def test_panel_has_recent_chips_with_context(qtbot, env):
    ctx, ws, other = env
    bc = WorkspaceBreadcrumb(ctx)
    qtbot.addWidget(bc)

    panel, _menu = _panel(bc)
    chips = [b for b in panel.findChildren(QPushButton)
             if b.objectName() == "WorkspaceRecentChip"]
    assert chips, "面板顶部必须有「最近」chips —— 切换要一步到位"
    # 当前工作区(断面A)被排除, 剩下的第一个应是最近的 B1, 且带项目上下文
    assert "B1" in chips[0].text()
    assert "潮间带" in chips[0].text(), f"chip 必须带项目名, 不然满屏裸名没法认: {chips[0].text()!r}"


def test_recent_chip_click_switches_workspace(qtbot, env, monkeypatch):
    ctx, ws, other = env
    bc = WorkspaceBreadcrumb(ctx)
    qtbot.addWidget(bc)

    entered: list = []
    monkeypatch.setattr(bc, "_switch_to_recent", lambda path, root=None: entered.append(path))

    panel, _menu = _panel(bc)
    chip = next(b for b in panel.findChildren(QPushButton)
                if b.objectName() == "WorkspaceRecentChip")
    chip.click()
    assert entered and str(other) in entered[0]


def test_project_and_folder_rows_open_the_complete_project_tree(qtbot, env):
    ctx, ws, other = env
    bc = WorkspaceBreadcrumb(ctx)
    qtbot.addWidget(bc)

    panel, _menu = _panel(bc)
    proj_btn = panel.findChild(QPushButton, "WorkspaceLocationProject")
    folder_btn = panel.findChild(QPushButton, "WorkspaceLocationFolder")
    assert proj_btn is not None
    assert folder_btn is not None
    assert "航次2026" in proj_btn.text()
    assert "断面A" in folder_btn.text()

    got: list[str] = []
    bc.navigate_requested.connect(got.append)
    proj_btn.click()
    folder_btn.click()
    assert got == ["project_tree", "project_tree"]


def test_three_start_scenarios_and_project_tree_entry(qtbot, env):
    ctx, ws, other = env
    bc = WorkspaceBreadcrumb(ctx)
    qtbot.addWidget(bc)

    got: list = []
    bc.new_survey_project_requested.connect(lambda: got.append("survey"))
    bc.new_workspace_requested.connect(lambda: got.append("standalone"))
    bc.open_workspace_requested.connect(lambda: got.append("open"))
    bc.navigate_requested.connect(lambda target: got.append(target))

    panel, _menu = _panel(bc)
    texts = {b.text(): b for b in panel.findChildren(QPushButton)}
    texts["＋ 新建调查项目"].click()
    texts["＋ 独立工作区"].click()
    texts["打开已有项目或工作区"].click()
    texts["管理全部项目"].click()
    assert got == ["survey", "standalone", "open", "project_tree"]
