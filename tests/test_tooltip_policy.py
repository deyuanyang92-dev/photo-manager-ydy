"""tests/test_tooltip_policy.py"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QWidget

from app.utils.tooltip_policy import suppress_popup_tooltip


def test_suppress_popup_tooltip_clears_text() -> None:
    app = QApplication.instance() or QApplication([])
    w = QWidget()
    w.setToolTip("long help")
    suppress_popup_tooltip(w)
    assert w.toolTip() == ""
