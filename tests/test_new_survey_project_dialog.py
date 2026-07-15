"""Hierarchical survey-project builder behavior."""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QCheckBox, QDialog, QLineEdit

from app.widgets.new_survey_project_dialog import NewSurveyProjectDialog


def test_values_include_default_area_and_workspace(qtbot, tmp_path):
    dlg = NewSurveyProjectDialog(default_parent_dir=str(tmp_path))
    qtbot.addWidget(dlg)
    dlg._name.setText("广西调查2026")

    values = dlg.values()

    assert values["parent_dir"] == str(tmp_path)
    assert values["name"] == "广西调查2026"
    assert values["structure"] == [{
        "name": "区域1",
        "type": "区域",
        "is_workspace": False,
        "children": [{
            "name": "断面1",
            "type": "断面",
            "is_workspace": True,
            "children": [],
        }],
    }]


def test_default_last_level_is_a_photo_workspace(qtbot, tmp_path):
    dlg = NewSurveyProjectDialog(default_parent_dir=str(tmp_path))
    qtbot.addWidget(dlg)

    assert dlg.site_names() == ["区域1/断面1"]
    assert dlg._tree.topLevelItem(0).child(0).child(0).text(0) == "断面1"


def test_hierarchy_uses_plain_directory_language_and_readable_rows(qtbot, tmp_path):
    dlg = NewSurveyProjectDialog(default_parent_dir=str(tmp_path))
    qtbot.addWidget(dlg)
    area = dlg._project_item.child(0)
    transect = area.child(0)

    assert dlg._tree.columnCount() == 2
    assert dlg._tree.headerItem().text(0) == "目录名称（可直接输入）"
    assert dlg._tree.headerItem().text(1) == "照片保存位置"
    assert dlg._tree.uniformRowHeights() is False
    assert dlg._tree.contextMenuPolicy() == Qt.ContextMenuPolicy.CustomContextMenu
    workspace_check = dlg._tree.itemWidget(transect, 1)
    assert isinstance(workspace_check, QCheckBox)
    assert workspace_check.text() == "照片保存到这里"


def test_directory_context_menu_supports_basic_tree_actions(qtbot, tmp_path):
    dlg = NewSurveyProjectDialog(default_parent_dir=str(tmp_path))
    qtbot.addWidget(dlg)
    area = dlg._project_item.child(0)
    transect = area.child(0)

    root_actions = [
        action.text() for action in dlg._build_context_menu(dlg._project_item).actions()
        if not action.isSeparator()
    ]
    node_menu = dlg._build_context_menu(transect)
    node_actions = [
        action.text() for action in node_menu.actions() if not action.isSeparator()
    ]

    assert root_actions == ["新建第一级目录"]
    assert node_actions == [
        "新建下级目录",
        "新建同级目录",
        "重命名",
        "取消照片保存目录",
        "从计划中移除",
    ]

    before = area.childCount()
    next(action for action in node_menu.actions() if action.text() == "新建同级目录").trigger()
    assert area.childCount() == before + 1


def test_every_hierarchy_name_is_an_always_visible_input(qtbot, tmp_path):
    dlg = NewSurveyProjectDialog(default_parent_dir=str(tmp_path))
    qtbot.addWidget(dlg)
    area = dlg._project_item.child(0)
    transect = area.child(0)

    assert isinstance(dlg._tree.itemWidget(area, 0), QLineEdit)
    assert isinstance(dlg._tree.itemWidget(transect, 0), QLineEdit)

    dlg._name_edit(area).setText("北海区域")
    dlg._name_edit(transect).setText("断面A")

    assert dlg.values()["structure"][0]["name"] == "北海区域"
    assert dlg.values()["structure"][0]["type"] == "区域"
    assert dlg.site_names() == ["北海区域/断面A"]


def test_add_child_to_workspace_turns_parent_into_container(qtbot, tmp_path):
    dlg = NewSurveyProjectDialog(default_parent_dir=str(tmp_path))
    qtbot.addWidget(dlg)
    original_workspace = dlg._project_item.child(0).child(0)
    dlg._tree.setCurrentItem(original_workspace)

    dlg._add_child()

    assert dlg._is_workspace(original_workspace) is False
    assert original_workspace.childCount() == 1
    assert dlg._is_workspace(original_workspace.child(0)) is True


def test_add_sibling_preserves_internal_type_and_workspace_role(qtbot, tmp_path):
    dlg = NewSurveyProjectDialog(default_parent_dir=str(tmp_path))
    qtbot.addWidget(dlg)
    first = dlg._project_item.child(0).child(0)
    dlg._tree.setCurrentItem(first)

    dlg._add_sibling()

    sibling = dlg._project_item.child(0).child(1)
    assert dlg._node_type(sibling) == "断面"
    assert dlg._is_workspace(sibling) is True


def test_rejects_empty_name(qtbot, tmp_path):
    dlg = NewSurveyProjectDialog(default_parent_dir=str(tmp_path))
    qtbot.addWidget(dlg)
    dlg._name.setText("")

    dlg._try_accept()

    assert dlg.result() != QDialog.DialogCode.Accepted
    assert "项目名称" in dlg._err.text()


def test_rejects_duplicate_sibling_names(qtbot, tmp_path):
    dlg = NewSurveyProjectDialog(default_parent_dir=str(tmp_path))
    qtbot.addWidget(dlg)
    dlg._name.setText("广西调查2026")
    first = dlg._project_item.child(0).child(0)
    dlg._tree.setCurrentItem(first)
    dlg._add_sibling()
    dlg._name_edit(dlg._project_item.child(0).child(1)).setText(first.text(0))

    dlg._try_accept()

    assert dlg.result() != QDialog.DialogCode.Accepted
    assert "名称重复" in dlg._err.text()


def test_rejects_existing_non_empty_project(qtbot, tmp_path):
    (tmp_path / "已存在").mkdir()
    (tmp_path / "已存在" / "x.txt").write_text("x", encoding="utf-8")
    dlg = NewSurveyProjectDialog(default_parent_dir=str(tmp_path))
    qtbot.addWidget(dlg)
    dlg._name.setText("已存在")

    dlg._try_accept()

    assert dlg.result() != QDialog.DialogCode.Accepted


def test_accepts_valid_hierarchy(qtbot, tmp_path):
    dlg = NewSurveyProjectDialog(default_parent_dir=str(tmp_path))
    qtbot.addWidget(dlg)
    dlg._name.setText("广西调查2026")

    dlg._try_accept()

    assert dlg.result() == QDialog.DialogCode.Accepted


def test_preview_shows_destination_and_workspace_count(qtbot, tmp_path):
    dlg = NewSurveyProjectDialog(default_parent_dir=str(tmp_path))
    qtbot.addWidget(dlg)
    dlg._name.setText("广西调查2026")

    text = dlg._preview.text()

    assert "广西调查2026" in text
    assert "保存到" in text
    assert "1 个照片保存目录" in text


def test_rejects_create_with_zero_levels(qtbot, tmp_path):
    dlg = NewSurveyProjectDialog(default_parent_dir=str(tmp_path))
    qtbot.addWidget(dlg)
    dlg._name.setText("广西调查2026")
    # Remove every hierarchy row so the project would have zero workspaces.
    while dlg._project_item.childCount():
        dlg._project_item.removeChild(dlg._project_item.child(0))

    dlg._try_accept()

    assert dlg.result() != QDialog.DialogCode.Accepted
    assert "请至少添加一个层级" in dlg._err.text()


def test_dead_auto_naming_helpers_are_gone(qtbot, tmp_path):
    # Auto-naming was removed; _next_name / _new_node_counter must no longer exist.
    dlg = NewSurveyProjectDialog(default_parent_dir=str(tmp_path))
    qtbot.addWidget(dlg)

    assert not hasattr(dlg, "_next_name")
    assert not hasattr(dlg, "_new_node_counter")


def test_append_mode_targets_existing_project_subdirectory(qtbot, tmp_path):
    root = tmp_path / "广西调查2026"
    target = root / "北海区域"
    target.mkdir(parents=True)
    dlg = NewSurveyProjectDialog(
        append_target_dir=str(target), project_root=str(root)
    )
    qtbot.addWidget(dlg)

    values = dlg.values()

    assert dlg.windowTitle() == "追加项目层级"
    assert values["mode"] == "append"
    assert values["target_dir"] == str(target)
    assert values["project_root"] == str(root)
    assert values["structure"][0]["name"] == "断面1"
    assert values["structure"][0]["is_workspace"] is True


def test_append_mode_accepts_an_existing_container(qtbot, tmp_path):
    target = tmp_path / "项目A" / "区域A"
    target.mkdir(parents=True)
    dlg = NewSurveyProjectDialog(append_target_dir=str(target))
    qtbot.addWidget(dlg)

    dlg._try_accept()

    assert dlg.result() == QDialog.DialogCode.Accepted
