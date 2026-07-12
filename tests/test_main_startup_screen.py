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
