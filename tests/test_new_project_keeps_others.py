"""新建项目后，之前的项目不许消失（用户 2026-07-13 二次报障，截图）。

第一次修复只动了 `ProjectTreeView.focus_project`，但**建项目的那条路径还在切模式**：

    app/main_window.py:1200
        self.ctx.settings.project_tree_root = res["root"]   # ← 树被钉成「单项目」模式

而且新建的项目是个**空容器目录**（不是工作区），压根**没被登记进 user_projects.json** ——
所以即使不切模式，「全部项目」里也看不到它。两个毛病叠在一起，才有了用户看到的
「新建项目后，之前项目全没了，只剩这一个」。

契约：
1. 建完项目 → 它出现在 `user_projects.json` 里（哪怕还是空容器，没有任何工作区）；
2. 建完项目 → **不切换**树的视图模式（`project_tree_view_mode` 保持 "all"），
   `project_tree_root` 不被钉死到新项目上；
3. 于是树里所有项目照旧并排，新项目只是被选中而已。
"""
from __future__ import annotations

import json
import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from app.app_context import AppContext


@pytest.fixture
def projects_json(tmp_path, monkeypatch):
    p = tmp_path / "user_projects.json"
    p.write_text(json.dumps({"version": 1, "projects": []}), encoding="utf-8")
    monkeypatch.setattr(
        "app.services.project_service.default_user_projects_json_path", lambda: str(p)
    )
    return p


def _entries(p: Path) -> list[dict]:
    return json.loads(p.read_text(encoding="utf-8"))["projects"]


def test_created_project_is_registered_in_projects_json(tmp_path, projects_json):
    """空容器项目也必须登记 —— 否则「全部项目」列表里永远看不到它。"""
    from app.services.project_scaffold_service import create_survey_project

    res = create_survey_project(str(tmp_path), name="北方多样性调查", sites=[])

    from app.services.project_service import register_project_root

    register_project_root(res["root"], name="北方多样性调查")

    dirs = [e.get("directory") for e in _entries(projects_json)]
    assert any(Path(d).name == "北方多样性调查" for d in dirs if d), dirs


def test_creating_second_project_keeps_the_first(tmp_path, projects_json):
    from app.services.project_scaffold_service import create_survey_project
    from app.services.project_service import register_project_root

    a = create_survey_project(str(tmp_path), name="项目A", sites=[])
    register_project_root(a["root"], name="项目A")
    b = create_survey_project(str(tmp_path), name="项目B", sites=[])
    register_project_root(b["root"], name="项目B")

    names = [Path(e["directory"]).name for e in _entries(projects_json)]
    assert "项目A" in names, f"建了项目B之后，项目A从列表里消失了: {names}"
    assert "项目B" in names, names


def test_new_project_does_not_pin_tree_to_single_project_mode(tmp_path, projects_json, monkeypatch):
    """建完项目不得把树切成「单项目」模式 —— 那会把其余项目全挡住。"""
    from app.services.project_scaffold_service import create_survey_project
    from app.services.project_service import register_project_root

    ctx = AppContext()
    ctx.settings.project_tree_view_mode = "all"
    ctx.settings.project_tree_root = None

    res = create_survey_project(str(tmp_path), name="新项目", sites=[])
    register_project_root(res["root"], name="新项目")

    # 建项目这个动作本身不许改视图模式
    assert ctx.settings.project_tree_view_mode == "all"


def test_new_project_parent_comes_from_catalogue_not_active_workspace(
    tmp_path, projects_json
):
    from app.services.project_service import (
        default_project_parent_directory,
        register_project_root,
    )

    projects_home = tmp_path / "全部项目"
    project = projects_home / "项目A"
    active_b2 = project / "断面A" / "B2"
    active_b2.mkdir(parents=True)
    register_project_root(str(project), name="项目A")

    assert default_project_parent_directory() == str(projects_home)
    assert default_project_parent_directory() != str(active_b2)
