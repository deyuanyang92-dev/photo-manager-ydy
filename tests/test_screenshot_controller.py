"""Tests for the reusable screenshot controller."""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QWidget


_APP = QApplication.instance() or QApplication([])


def test_wsl_delegates_all_capture_modes_to_windows_snipper(monkeypatch):
    """WSL cannot reliably grab the real Windows desktop through Qt."""
    from app.utils import win_screenshot
    from app.widgets.screenshot_controller import ScreenshotController

    host = QWidget()
    current_view = QWidget(host)
    host.show()
    _APP.processEvents()

    launched = []
    overlays = []
    statuses = []

    monkeypatch.setattr(win_screenshot, "is_wsl", lambda: True)
    monkeypatch.setattr(
        win_screenshot,
        "launch_windows_snip",
        lambda: launched.append(True) or True,
    )
    monkeypatch.setattr(
        ScreenshotController,
        "_open_screenshot_overlay",
        lambda self, preset, screen: overlays.append((preset, screen)),
    )

    ctrl = ScreenshotController(
        host,
        view_provider=lambda: current_view,
        status_cb=statuses.append,
    )

    ctrl.capture_region()
    ctrl.capture_fullscreen()
    ctrl.capture_window()
    ctrl.capture_view()

    assert len(launched) == 4
    assert overlays == []
    assert statuses == ["已唤起 Windows 截图工具（Snipaste / 屏幕截图）"] * 4

    host.close()
