"""test_screenshot_overlay.py — screenshot overlay modal behavior.

Run:
    QT_QPA_PLATFORM=offscreen python3 -m pytest tests/test_screenshot_overlay.py -v
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QPixmap
from PyQt6.QtWidgets import QApplication, QWidget

_APP = QApplication.instance() or QApplication([])


def _solid(w: int, h: int, color: QColor) -> QPixmap:
    pm = QPixmap(w, h)
    pm.fill(color)
    return pm


def test_overlay_is_application_modal():
    from app.widgets.screenshot_overlay import ScreenshotOverlay

    overlay = ScreenshotOverlay()
    assert overlay.windowModality() == Qt.WindowModality.ApplicationModal


# ── WSLg/XWayland black-screen capture regression ───────────────────────────
# Under WSLg the X11 root grab (QScreen.grabWindow(0)) returns an all-black
# pixmap, so every screenshot came out black. The overlay must detect that and
# composite the app's own top-level windows (QWidget.grab works everywhere).

def test_blank_detector_flags_null_and_black():
    from app.widgets.screenshot_overlay import _pixmap_is_blank

    assert _pixmap_is_blank(QPixmap()) is True
    assert _pixmap_is_blank(_solid(64, 64, QColor(0, 0, 0))) is True


def test_blank_detector_passes_real_content():
    from app.widgets.screenshot_overlay import _pixmap_is_blank

    assert _pixmap_is_blank(_solid(64, 64, QColor(255, 255, 255))) is False
    assert _pixmap_is_blank(_solid(64, 64, QColor(10, 0, 0))) is False


def test_composite_captures_visible_app_window():
    from app.widgets.screenshot_overlay import (
        _composite_top_levels,
        _pixmap_is_blank,
    )

    w = QWidget()
    w.setStyleSheet("background:#ffffff;")
    w.resize(200, 120)
    w.show()
    _APP.processEvents()

    canvas = _composite_top_levels(w.screen(), exclude=None)
    assert not canvas.isNull()
    assert not _pixmap_is_blank(canvas)
    w.close()


def test_start_falls_back_when_root_grab_is_black():
    """Core regression: a black root grab must NOT survive as the frozen
    background — the overlay composites app windows instead."""
    from app.widgets.screenshot_overlay import (
        ScreenshotOverlay,
        _pixmap_is_blank,
    )

    main = QWidget()
    main.setStyleSheet("background:#ffffff;")
    main.resize(240, 160)
    main.show()
    _APP.processEvents()

    overlay = ScreenshotOverlay(main)
    overlay._grab_root = lambda screen: _solid(640, 480, QColor(0, 0, 0))
    overlay.start(None, screen=main.screen())

    assert not _pixmap_is_blank(overlay._frozen), "frozen background is still black"
    overlay.close()
    main.close()


def test_start_keeps_native_root_grab():
    """On a healthy platform the real root grab has content and is kept
    verbatim — the fallback only triggers on a blank grab."""
    from app.widgets.screenshot_overlay import ScreenshotOverlay

    main = QWidget()
    main.show()
    _APP.processEvents()

    native = _solid(640, 480, QColor(123, 45, 67))
    overlay = ScreenshotOverlay(main)
    overlay._grab_root = lambda screen: native
    overlay.start(None, screen=main.screen())

    assert overlay._frozen.cacheKey() == native.cacheKey()
    overlay.close()
    main.close()
