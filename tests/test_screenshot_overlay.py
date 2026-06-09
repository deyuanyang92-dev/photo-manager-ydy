"""test_screenshot_overlay.py — screenshot overlay modal behavior.

Run:
    QT_QPA_PLATFORM=offscreen python3 -m pytest tests/test_screenshot_overlay.py -v
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication

_APP = QApplication.instance() or QApplication([])


def test_overlay_is_application_modal():
    from app.widgets.screenshot_overlay import ScreenshotOverlay

    overlay = ScreenshotOverlay()
    assert overlay.windowModality() == Qt.WindowModality.ApplicationModal
