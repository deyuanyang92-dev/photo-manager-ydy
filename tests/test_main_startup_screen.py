"""Startup screen selection regressions."""

from __future__ import annotations

import logging


def test_choose_startup_screen_prefers_primary_screen():
    from main import _choose_startup_screen

    origin_screen = object()
    primary_screen = object()
    cursor_screen = object()

    assert (
        _choose_startup_screen(
            [origin_screen, primary_screen, cursor_screen],
            primary_screen,
            cursor_screen,
        )
        is primary_screen
    )


def test_choose_startup_screen_falls_back_to_primary_not_origin_rule():
    from main import _choose_startup_screen

    origin_screen = object()
    primary_screen = object()

    assert (
        _choose_startup_screen([origin_screen, primary_screen], primary_screen, None)
        is primary_screen
    )


def test_choose_startup_screen_uses_cursor_when_primary_missing():
    from main import _choose_startup_screen

    first = object()
    cursor_screen = object()

    assert _choose_startup_screen([first, cursor_screen], None, cursor_screen) is cursor_screen


def test_choose_startup_screen_falls_back_to_first_screen():
    from main import _choose_startup_screen

    first = object()
    second = object()

    assert _choose_startup_screen([first, second], None, None) is first


def test_windows_first_run_defaults_to_performance_mode():
    from main import _should_default_performance_mode

    assert _should_default_performance_mode(
        is_wsl=False,
        platform="win32",
        low_memory=False,
        setting_present=False,
    )


def test_explicit_rendering_preference_is_never_overridden():
    from main import _should_default_performance_mode

    assert not _should_default_performance_mode(
        is_wsl=True,
        platform="win32",
        low_memory=True,
        setting_present=True,
    )


def test_fallback_placement_uses_one_show_transition():
    from PyQt6.QtCore import QRect, Qt
    from main import _place_main_window

    calls = []

    class _Target:
        def availableGeometry(self):
            return QRect(0, 0, 1920, 1040)

    class _Window:
        def setGeometry(self, rect):
            calls.append(("geometry", rect))

        def windowState(self):
            return Qt.WindowState(0)

        def setWindowState(self, state):
            calls.append(("state", state))

        def showNormal(self):
            calls.append(("normal", None))

        def showMaximized(self):
            calls.append(("maximized", None))

        def raise_(self):
            calls.append(("raise", None))

        def activateWindow(self):
            calls.append(("activate", None))

    _place_main_window(_Window(), _Target())

    assert "normal" not in [name for name, _value in calls]
    assert [name for name, _value in calls].count("maximized") == 1


def test_qt_message_handler_routes_warning_to_logging(monkeypatch, caplog):
    import main
    from PyQt6.QtCore import QtMsgType

    installed = []
    monkeypatch.setattr(main, "_QT_MESSAGE_HANDLER", None)
    monkeypatch.setattr(main, "_QT_PREVIOUS_MESSAGE_HANDLER", None)

    def _install(handler):
        installed.append(handler)
        return None

    main._install_qt_message_handler(_install)
    with caplog.at_level(logging.WARNING, logger="qt"):
        installed[0](QtMsgType.QtWarningMsg, None, "layout warning")

    assert "layout warning" in caplog.text
