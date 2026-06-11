"""Tests for the screenshot hub view."""
from __future__ import annotations

import os
from unittest.mock import MagicMock

import pytest
from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtGui import QColor, QPixmap
from PyQt6.QtWidgets import QApplication, QVBoxLayout, QWidget

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="module", autouse=True)
def qt_app():
    return QApplication.instance() or QApplication([])


class _FakeController(QObject):
    captured = pyqtSignal(QPixmap)

    def __init__(self) -> None:
        super().__init__()
        self.calls: list[str] = []

    def capture_region(self) -> None:
        self.calls.append("region")

    def capture_fullscreen(self) -> None:
        self.calls.append("fullscreen")

    def capture_window(self) -> None:
        self.calls.append("window")

    def capture_view(self) -> None:
        self.calls.append("view")


def _ctx(project_dir: str | None = None):
    ctx = MagicMock()
    ctx.current_project_dir = project_dir or ""
    return ctx


def _png(path) -> None:
    pix = QPixmap(40, 24)
    pix.fill(QColor("#22c55e"))
    assert pix.save(str(path), "PNG")


def test_screenshot_view_identity(qt_app):
    from app.views.screenshot_view import ScreenshotView

    assert ScreenshotView.view_id == "screenshot"
    assert ScreenshotView.nav_title == "截图"


def test_screenshot_view_constructs_without_project(qt_app):
    from app.views.screenshot_view import ScreenshotView

    view = ScreenshotView(_ctx())
    assert view._recent_list.count() == 0
    assert not view._open_dir_btn.isEnabled()


def test_screenshot_view_lists_recent_project_shots(qt_app, tmp_path):
    from app.views.screenshot_view import ScreenshotView

    shots = tmp_path / "results" / "screenshots"
    shots.mkdir(parents=True)
    old = shots / "截图-20260609-100000.png"
    new = shots / "截图-20260609-110000.png"
    _png(old)
    _png(new)
    os.utime(old, (100, 100))
    os.utime(new, (200, 200))

    view = ScreenshotView(_ctx(str(tmp_path)))
    assert view._recent_list.count() == 2
    assert view._recent_list.item(0).text() == new.name
    assert view._recent_list.item(1).text() == old.name
    assert view._recent_list.currentItem().text() == new.name


def test_capture_buttons_call_main_window_controller(qt_app, tmp_path):
    from app.views.screenshot_view import ScreenshotView

    host = QWidget()
    host._shot_ctrl = _FakeController()
    host.screenshot_shortcut_seq = lambda: "Ctrl+Alt+S"
    lay = QVBoxLayout(host)
    view = ScreenshotView(_ctx(str(tmp_path)))
    lay.addWidget(view)

    view.on_activate()
    view._region_btn.click()
    view._fullscreen_btn.click()
    view._window_btn.click()
    view._view_btn.click()

    assert host._shot_ctrl.calls == ["region", "fullscreen", "window", "view"]
    assert view._shortcut_lbl.text() == "区域截图快捷键：Ctrl+Alt+S"


def test_registry_omits_screenshot_nav_entry():
    from app.views.registry import ALL_VIEWS
    from app.views.screenshot_view import ScreenshotView

    assert ScreenshotView not in ALL_VIEWS
