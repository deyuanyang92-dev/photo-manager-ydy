"""Focused coverage for the user-facing visual proposal switcher."""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtWidgets import QApplication

from app.app_context import AppContext
from app.config.i18n import set_language
from app.config.theme import COMPARISON_THEME_KEYS, THEME_CLASSIC_LIGHT
from app.main_window import MainWindow


@pytest.fixture()
def window():
    app = QApplication.instance() or QApplication([])
    set_language("zh")
    ctx = AppContext()
    previous = ctx.settings.current_theme
    ctx.settings.current_theme = "classic_light"
    win = MainWindow(ctx)
    yield win
    win.close()
    ctx.settings.current_theme = previous
    ctx.settings.flush_to_disk()
    app.setStyleSheet("")


def test_curated_switcher_keeps_original_first(window: MainWindow) -> None:
    assert COMPARISON_THEME_KEYS == (
        "classic_light",
        "lab_light",
        "graphite_focus",
    )
    assert tuple(window._theme_actions) == COMPARISON_THEME_KEYS
    assert window._theme_actions["classic_light"].isChecked()
    assert THEME_CLASSIC_LIGHT["accent"] == "#0f766e"


def test_theme_switch_is_live_persistent_and_reversible(window: MainWindow) -> None:
    app = QApplication.instance()
    assert app is not None

    window._theme_actions["lab_light"].trigger()
    assert window.ctx.settings.current_theme == "lab_light"
    assert window._theme_actions["lab_light"].isChecked()
    assert "#f7f8fb" in app.styleSheet()

    assert window.apply_visual_theme("graphite_focus") == "graphite_focus"
    assert window._theme_actions["graphite_focus"].isChecked()
    assert "#111827" in app.styleSheet()

    assert window.apply_visual_theme("classic_light") == "classic_light"
    assert window._theme_actions["classic_light"].isChecked()
    assert "#f4f6f8" in app.styleSheet()


def test_unknown_visual_proposal_falls_back_to_original(window: MainWindow) -> None:
    assert window.apply_visual_theme("missing-theme") == "classic_light"
    assert window.ctx.settings.current_theme == "classic_light"
    assert window._theme_actions["classic_light"].isChecked()
