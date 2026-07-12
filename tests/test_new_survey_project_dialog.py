"""新建项目对话框 —— 精简为 2 个字段(需求 2026-07-12)。

旧版一次问 6 个字段 + 采样点列表, 因为项目根是容器(非工作区)、设置抽屉又只挂在工作台上,
那个对话框是设置项目级默认值的**唯一机会**(见 spec §1.2)。补上「右键项目 → 项目设置」后,
这里只留最少必填项: 项目名称 + 建在哪里。
见 docs/specs/2026-07-12-slim-new-project-and-settings-inheritance.md
"""
from __future__ import annotations

from PyQt6.QtWidgets import QDialog

from app.widgets.new_survey_project_dialog import NewSurveyProjectDialog


def test_values_only_name_and_parent_dir(qtbot, tmp_path):
    dlg = NewSurveyProjectDialog(default_parent_dir=str(tmp_path))
    qtbot.addWidget(dlg)
    dlg._name.setText("江苏盐城2026")

    assert dlg.values() == {"parent_dir": str(tmp_path), "name": "江苏盐城2026"}


def test_removed_fields_are_gone(qtbot, tmp_path):
    """采样点多行框和 4 个元数据字段不再存在 —— 它们改由项目设置抽屉填。"""
    dlg = NewSurveyProjectDialog(default_parent_dir=str(tmp_path))
    qtbot.addWidget(dlg)

    for attr in ("_sites", "_location", "_year", "_collector", "_province"):
        assert not hasattr(dlg, attr), f"{attr} 应已移除(§7 注释保留)"


def test_site_names_is_empty_for_back_compat(qtbot, tmp_path):
    """site_names() 保留但恒为空 —— 采样点改在项目树里建。"""
    dlg = NewSurveyProjectDialog(default_parent_dir=str(tmp_path))
    qtbot.addWidget(dlg)

    assert dlg.site_names() == []


def test_rejects_empty_name(qtbot, tmp_path):
    dlg = NewSurveyProjectDialog(default_parent_dir=str(tmp_path))
    qtbot.addWidget(dlg)
    dlg._name.setText("")

    dlg._try_accept()

    assert dlg.result() != QDialog.DialogCode.Accepted
    assert dlg._err.text().startswith("⚠")


def test_rejects_existing_non_empty_dir(qtbot, tmp_path):
    (tmp_path / "已存在").mkdir()
    (tmp_path / "已存在" / "x.txt").write_text("x", encoding="utf-8")
    dlg = NewSurveyProjectDialog(default_parent_dir=str(tmp_path))
    qtbot.addWidget(dlg)
    dlg._name.setText("已存在")

    dlg._try_accept()

    assert dlg.result() != QDialog.DialogCode.Accepted


def test_accepts_name_and_dir(qtbot, tmp_path):
    dlg = NewSurveyProjectDialog(default_parent_dir=str(tmp_path))
    qtbot.addWidget(dlg)
    dlg._name.setText("江苏盐城2026")

    dlg._try_accept()

    assert dlg.result() == QDialog.DialogCode.Accepted


def test_preview_mentions_empty_project(qtbot, tmp_path):
    dlg = NewSurveyProjectDialog(default_parent_dir=str(tmp_path))
    qtbot.addWidget(dlg)
    dlg._name.setText("江苏盐城2026")

    text = dlg._preview.text()

    assert "江苏盐城2026/" in text
    assert "采样点" in text  # 提示之后再建
