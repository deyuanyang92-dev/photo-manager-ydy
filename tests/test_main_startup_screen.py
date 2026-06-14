"""Startup screen selection regressions."""

from __future__ import annotations


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
