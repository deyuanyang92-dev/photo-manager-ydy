"""建完空项目后的落点(需求 2026-07-12)。

项目根是容器(非工作区), 进不去工作台 —— 所以建完必须落到项目树, 让用户在那里
「新建子目录」加断面, 并用「项目设置」填项目级默认值(采集人/地区代码/坐标/拍摄场地)。

§7 旧行为: 建完直接 enter_workspace(第一个采样点) → 跳工作台(对话框里一次问完采样点)。
见 docs/specs/2026-07-12-slim-new-project-and-settings-inheritance.md
"""
from __future__ import annotations

from pathlib import Path

import pytest
from PyQt6.QtWidgets import QDialog

from app.app_context import AppContext
from app.main_window import MainWindow


@pytest.fixture
def win(qtbot):
    # MainWindow 自己不注册页面 —— main.py 才注册。落点断言要真跳到项目树页, 所以这里
    # 照 main.py 的方式把所有 spec 注册进去(LazyViewSpec: 首次 navigate 才真正构造)。
    from app.views.registry import ALL_VIEW_SPECS

    w = MainWindow(AppContext())
    for spec in ALL_VIEW_SPECS:
        w.register_view(spec)
    qtbot.addWidget(w)
    return w


def _fake_dialog(monkeypatch, parent_dir: Path, name: str) -> None:
    class _FakeDlg:
        def __init__(self, *a, **kw):
            pass

        def exec(self):
            return QDialog.DialogCode.Accepted

        def values(self):
            return {"parent_dir": str(parent_dir), "name": name}

    monkeypatch.setattr(
        "app.widgets.new_survey_project_dialog.NewSurveyProjectDialog", _FakeDlg
    )
    monkeypatch.setattr("app.utils.ui.info", lambda *a, **kw: None)


def test_new_project_creates_container_only(win, tmp_path, monkeypatch):
    _fake_dialog(monkeypatch, tmp_path, "江苏盐城2026")

    win._on_new_survey_project()

    root = tmp_path / "江苏盐城2026"
    assert root.is_dir()
    assert (root / "_data" / "region.json").is_file()
    # 红线: 项目根是容器, 照片不得堆在项目根
    assert not (root / "incoming-jpg").exists()
    assert not (root / "results").exists()


def test_new_project_sets_tree_root_and_does_not_enter_workspace(
    win, tmp_path, monkeypatch
):
    _fake_dialog(monkeypatch, tmp_path, "江苏盐城2026")

    win._on_new_survey_project()

    root = tmp_path / "江苏盐城2026"
    assert win.ctx.settings.project_tree_root == str(root)
    # 没有采样点 → 没进任何工作区(项目根不是拍照工作区)
    assert win.ctx.current_project_dir is None


def test_new_project_lands_on_project_tree(win, tmp_path, monkeypatch):
    _fake_dialog(monkeypatch, tmp_path, "江苏盐城2026")

    win._on_new_survey_project()

    current = win._stack.currentWidget()
    assert getattr(current, "view_id", None) == "project_tree"


def test_topbar_new_child_creates_directly_under_current_project(
    win, tmp_path, monkeypatch
):
    """OM-style top-bar entry must target the project container, not a stale node."""
    root = tmp_path / "江苏盐城2026"
    root.mkdir()
    win.ctx.settings.project_tree_root = str(root)
    win.ctx.current_project_root = str(root)
    monkeypatch.setattr(
        "app.views.project_tree_view.QInputDialog.getText",
        lambda *a, **kw: ("断面A", True),
    )

    win._on_new_project_child()

    assert (root / "断面A").is_dir()
    assert not (root / "断面A" / "_data").exists()
