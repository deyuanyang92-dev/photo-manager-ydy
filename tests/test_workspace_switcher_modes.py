"""Top-bar project switcher keeps the current UI and offers a second mode."""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from PyQt6.QtWidgets import (
    QApplication,
    QComboBox,
    QLabel,
    QLineEdit,
    QPushButton,
    QToolButton,
    QWidget,
)

from app.config.settings import AppSettings
from app.widgets.workspace_breadcrumb import WorkspaceBreadcrumb


_APP = None

MODE_MARKERS = {
    "classic": "WorkspaceLocationSwitcher",
    "navigator": "WorkspaceNavigatorSwitcher",
    "triple": "WorkspaceTripleLocation",
    "om_capture": "WorkspaceOmLocation",
    "dual": "WorkspaceDualProject",
    "breadcrumb": "WorkspaceBreadcrumbSegment",
    "omnibox": "WorkspaceOmnibox",
    "history": "WorkspaceHistoryLocation",
    "scenes": "WorkspaceSceneNewProject",
    "instrument": "WorkspaceInstrumentPanel",
    "locator": "WorkspaceLocatorSwitcher",
}


@pytest.fixture(scope="module", autouse=True)
def qt_app():
    global _APP
    if _APP is None:
        _APP = QApplication.instance() or QApplication([])
    return _APP


class _Ctx:
    def __init__(self, workspace: str, root: str, settings: AppSettings) -> None:
        self.current_project_dir = workspace
        self.current_project_root = root
        self.settings = settings


@pytest.fixture()
def project_paths(tmp_path):
    root = tmp_path / "广西调查2026"
    workspace = root / "北海区域" / "断面A"
    workspace.mkdir(parents=True)
    return str(root), str(workspace)


def test_workspace_switcher_mode_defaults_to_classic_and_persists() -> None:
    settings = AppSettings()
    settings._qs.clear()

    assert settings.workspace_switcher_mode == "classic"

    settings.workspace_switcher_mode = "navigator"
    settings.flush_to_disk()
    assert AppSettings().workspace_switcher_mode == "navigator"

    settings._qs.setValue("ui/workspace_switcher_mode", "unknown")
    assert settings.workspace_switcher_mode == "classic"


@pytest.mark.parametrize("mode", MODE_MARKERS)
def test_workspace_switcher_mode_accepts_all_ten_designs(mode: str) -> None:
    settings = AppSettings()
    settings._qs.clear()

    settings.workspace_switcher_mode = mode

    assert settings.workspace_switcher_mode == mode


def test_classic_mode_is_kept_as_the_default(project_paths) -> None:
    root, workspace = project_paths
    settings = AppSettings()
    settings._qs.clear()
    widget = WorkspaceBreadcrumb(_Ctx(workspace, root, settings))

    assert widget._leaf_btn.objectName() == "WorkspaceLocationSwitcher"
    menu = widget._leaf_btn.menu()
    widget._populate_workspace_menu(menu)
    assert menu.findChild(QWidget, "WorkspaceLocationPanel") is not None
    assert menu.findChild(QWidget, "WorkspaceNavigatorPanel") is None


def test_mode_selector_switches_to_navigator_and_rebuilds(project_paths) -> None:
    root, workspace = project_paths
    settings = AppSettings()
    settings._qs.clear()
    widget = WorkspaceBreadcrumb(_Ctx(workspace, root, settings))
    menu = widget._leaf_btn.menu()
    widget._populate_workspace_menu(menu)
    selector = menu.findChild(QComboBox, "WorkspaceSwitcherMode")

    assert selector is not None
    assert selector.currentData() == "classic"

    selector.setCurrentIndex(selector.findData("navigator"))
    QApplication.processEvents()

    assert settings.workspace_switcher_mode == "navigator"
    assert widget._leaf_btn.objectName() == "WorkspaceNavigatorSwitcher"
    navigator_menu = widget._leaf_btn.menu()
    widget._populate_workspace_menu(navigator_menu)
    assert navigator_menu.findChild(QWidget, "WorkspaceNavigatorPanel") is not None


def test_navigator_mode_stays_focused_on_the_top_interface(project_paths) -> None:
    root, workspace = project_paths
    settings = AppSettings()
    settings._qs.clear()
    settings.workspace_switcher_mode = "navigator"
    widget = WorkspaceBreadcrumb(_Ctx(workspace, root, settings))
    menu = widget._leaf_btn.menu()
    widget._populate_workspace_menu(menu)
    panel = menu.findChild(QWidget, "WorkspaceNavigatorPanel")

    assert panel is not None
    assert panel.findChild(QLineEdit, "WorkspaceNavigatorSearch") is not None
    labels = [label.text() for label in panel.findChildren(QLabel)]
    assert any("广西调查2026" in text for text in labels)
    assert any("北海区域 › 断面A" in text for text in labels)

    buttons = [button.text() for button in panel.findChildren(QPushButton)]
    assert "＋ 新建调查项目" in buttons
    assert "＋ 独立工作区" in buttons
    assert "＋ 追加到当前项目" in buttons
    assert "打开已有项目或工作区" in buttons
    assert not any("删除" in text or "移动" in text for text in buttons)


@pytest.mark.parametrize("mode,marker", MODE_MARKERS.items())
def test_all_modes_build_a_distinct_topbar_structure(
    project_paths, mode: str, marker: str
) -> None:
    root, workspace = project_paths
    settings = AppSettings()
    settings._qs.clear()
    settings.workspace_switcher_mode = mode

    widget = WorkspaceBreadcrumb(_Ctx(workspace, root, settings))

    assert widget.findChild(QWidget, marker) is not None
    assert widget.property("switcherMode") == mode


def test_style_button_lists_all_designs_and_switches_immediately(project_paths) -> None:
    root, workspace = project_paths
    settings = AppSettings()
    settings._qs.clear()
    widget = WorkspaceBreadcrumb(_Ctx(workspace, root, settings))
    style_button = widget.findChild(QToolButton, "WorkspaceSwitcherStyleButton")

    assert style_button is not None
    actions = [action for action in style_button.menu().actions() if action.isCheckable()]
    assert len(actions) == len(MODE_MARKERS)
    assert sum(action.isChecked() for action in actions) == 1

    triple = next(action for action in actions if action.data() == "triple")
    triple.trigger()
    QApplication.processEvents()

    assert settings.workspace_switcher_mode == "triple"
    assert widget.findChild(QWidget, "WorkspaceTripleLocation") is not None


def test_locator_mode_shows_project_slash_folder_without_independent_label(
    project_paths,
) -> None:
    root, workspace = project_paths
    settings = AppSettings()
    settings._qs.clear()
    settings.workspace_switcher_mode = "locator"
    widget = WorkspaceBreadcrumb(_Ctx(workspace, root, settings))

    leaf = widget.findChild(QToolButton, "WorkspaceLocatorSwitcher")
    assert leaf is not None
    assert "独立工作区" not in leaf.text()
    assert "广西调查2026" in leaf.text()
    assert " / " in leaf.text()
    assert "北海区域" in leaf.text() or "断面A" in leaf.text()

    menu = leaf.menu()
    widget._populate_workspace_menu(menu)
    panel = menu.findChild(QWidget, "WorkspaceLocatorPanel")
    assert panel is not None
    assert panel.findChild(QLineEdit, "WorkspaceLocatorSearch") is not None
    labels = [label.text() for label in panel.findChildren(QLabel)]
    assert any(text == "当前" for text in labels)
    assert any(text == "最近拍摄位置" for text in labels)
    assert any(text == "当前项目的相邻位置" for text in labels)
    buttons = [button.text() for button in panel.findChildren(QPushButton)]
    assert "＋ 新建调查项目" in buttons
    assert "管理全部项目 →" in buttons
    assert not any("删除" in text for text in buttons)


def test_triple_mode_has_exactly_three_user_functions(project_paths) -> None:
    root, workspace = project_paths
    settings = AppSettings()
    settings._qs.clear()
    settings.workspace_switcher_mode = "triple"
    widget = WorkspaceBreadcrumb(_Ctx(workspace, root, settings))
    got = []
    widget.new_survey_project_requested.connect(lambda: got.append("project"))
    widget.new_project_child_requested.connect(lambda: got.append("workspace"))

    location = widget.findChild(QToolButton, "WorkspaceTripleLocation")
    project = widget.findChild(QPushButton, "WorkspaceTripleProject")
    workspace_button = widget.findChild(QPushButton, "WorkspaceTripleWorkspace")

    assert location is not None and location.menu() is not None
    assert project is not None and workspace_button is not None
    project.click()
    workspace_button.click()

    assert got == ["project", "workspace"]
