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


def test_adaptive_geometry_is_centered_and_smaller_than_work_area():
    from PyQt6.QtCore import QRect, QSize
    from main import _adaptive_window_geometry

    available = QRect(0, 0, 2560, 1400)

    result = _adaptive_window_geometry(available, QSize(940, 600))

    assert result.width() == 2048
    assert result.height() == 1120
    assert result.width() >= 940
    assert result.height() >= 600
    assert result.center() == available.center()


def test_fallback_placement_uses_one_normal_show_transition():
    from PyQt6.QtCore import QRect, QSize, Qt
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

        def minimumSize(self):
            return QSize(940, 600)

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

    names = [name for name, _value in calls]
    assert names.count("normal") == 1
    assert "maximized" not in names
    geometry = next(value for name, value in calls if name == "geometry")
    assert geometry.width() < 1920
    assert geometry.height() < 1040


def test_saved_normal_window_is_not_forced_maximized():
    from PyQt6.QtCore import QRect, Qt
    from main import _show_main_window_at_startup

    calls = []

    class _Screen:
        def availableGeometry(self):
            return QRect(0, 0, 1920, 1040)

    class _App:
        def screens(self):
            return [_Screen()]

    class _Window:
        def frameGeometry(self):
            return QRect(100, 80, 1200, 760)

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

    placement = _show_main_window_at_startup(
        _Window(), _App(), _Screen(), has_saved_geometry=True
    )

    assert placement == "restored-normal"
    assert ("normal", None) in calls
    assert ("maximized", None) not in calls


def test_saved_maximized_window_remains_maximized():
    from PyQt6.QtCore import QRect, Qt
    from main import _show_main_window_at_startup

    calls = []

    class _Screen:
        def availableGeometry(self):
            return QRect(0, 0, 1920, 1040)

    class _App:
        def screens(self):
            return [_Screen()]

    class _Window:
        def frameGeometry(self):
            return QRect(0, 0, 1920, 1040)

        def windowState(self):
            return Qt.WindowState.WindowMaximized

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

    placement = _show_main_window_at_startup(
        _Window(), _App(), _Screen(), has_saved_geometry=True
    )

    assert placement == "restored-maximized"
    assert ("maximized", None) in calls
    assert ("normal", None) not in calls


def test_post_show_stabilizer_restores_minimized_first_start_at_80_percent():
    from PyQt6.QtCore import QRect, QSize, Qt
    from main import _stabilize_main_window_after_show

    calls = []

    class _Target:
        def availableGeometry(self):
            return QRect(0, 0, 2560, 1400)

    class _Window:
        def isMinimized(self):
            return True

        def windowState(self):
            return Qt.WindowState.WindowMinimized

        def minimumSize(self):
            return QSize(940, 600)

        def setGeometry(self, rect):
            calls.append(("geometry", rect))

        def setWindowState(self, state):
            calls.append(("state", state))

        def showNormal(self):
            calls.append(("normal", None))

        def raise_(self):
            calls.append(("raise", None))

        def activateWindow(self):
            calls.append(("activate", None))

    changed = _stabilize_main_window_after_show(
        _Window(), _Target(), use_adaptive_geometry=True
    )

    assert changed
    geometry = next(value for name, value in calls if name == "geometry")
    assert geometry.width() == 2048
    assert geometry.height() == 1120
    state = next(value for name, value in calls if name == "state")
    assert not state & Qt.WindowState.WindowMinimized
    assert ("normal", None) in calls


def test_post_show_stabilizer_leaves_unminimized_first_start_untouched():
    # 首次原生启动(无存档几何=use_adaptive_geometry=True)但窗口并未被最小化时,
    # 稳定器不得回抢焦点(raise_/activateWindow),否则会在用户 alt-tab 后夺回焦点。
    from PyQt6.QtCore import QRect, QSize, Qt
    from main import _stabilize_main_window_after_show

    calls = []

    class _Target:
        def availableGeometry(self):
            return QRect(0, 0, 2560, 1400)

    class _Window:
        def isMinimized(self):
            return False

        def windowState(self):
            return Qt.WindowState.WindowActive

        def minimumSize(self):
            return QSize(940, 600)

        def setGeometry(self, rect):
            calls.append(("geometry", rect))

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

    changed = _stabilize_main_window_after_show(
        _Window(), _Target(), use_adaptive_geometry=True
    )

    assert changed is False
    assert ("raise", None) not in calls
    assert ("activate", None) not in calls


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
