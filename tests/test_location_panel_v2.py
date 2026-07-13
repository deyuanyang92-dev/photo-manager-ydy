"""照片保存设置面板 v2 —— 用户 2026-07-13 拍板的四件事全要:

    "我要不要新建项目? 要! 要不要新建工作区? 要! 要不要切换工作区? 要!
     然后要不要用户便捷? 要!"

契约（都在顶栏 📁▾ 的「照片保存设置」面板里，一步到位）:
1. 面板顶部有「最近」chips（来自真实 user_projects.json, 按 lastOpenedAt 降序,
   带项目上下文, 点一下 = 直接进入）;
2. 「项目」行是**可点的下拉**（列出磁盘上的其他项目, 点了切换）, 不再是死的 QLabel;
3. 「保存目录」行同理（列出本项目内全部目录）;
4. ＋项目 / ＋下级目录 两个新建按钮保留（信号不变）。
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


def test_project_and_folder_rows_are_clickable_dropdowns(qtbot, env):
    ctx, ws, other = env
    bc = WorkspaceBreadcrumb(ctx)
    qtbot.addWidget(bc)

    panel, _menu = _panel(bc)
    proj_btn = panel.findChild(QPushButton, "WorkspaceLocationProject")
    folder_btn = panel.findChild(QPushButton, "WorkspaceLocationFolder")
    assert proj_btn is not None, "「项目」行必须是可点的下拉(切换项目), 不能是死 QLabel"
    assert folder_btn is not None, "「保存目录」行必须是可点的下拉(切换目录)"
    assert "航次2026" in proj_btn.text()


def test_create_buttons_keep_their_signals(qtbot, env):
    ctx, ws, other = env
    bc = WorkspaceBreadcrumb(ctx)
    qtbot.addWidget(bc)

    got: list = []
    bc.new_survey_project_requested.connect(lambda: got.append("project"))
    bc.new_project_child_requested.connect(lambda: got.append("child"))

    panel, _menu = _panel(bc)
    texts = {b.text(): b for b in panel.findChildren(QPushButton)}
    assert any("项目" in t and "＋" in t for t in texts), texts.keys()
    next(b for t, b in texts.items() if "＋" in t and "项目" in t and "下级" not in t).click()
    next(b for t, b in texts.items() if "下级目录" in t).click()
    assert got == ["project", "child"]
