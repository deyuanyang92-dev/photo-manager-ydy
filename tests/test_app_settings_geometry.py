from PyQt6.QtCore import QByteArray

from app.config.settings import AppSettings
from app.config.window_layout import WINDOW_GEOMETRY_POLICY_VERSION


def test_legacy_window_geometry_is_ignored_until_saved_by_current_policy():
    settings = AppSettings()
    settings._qs.clear()
    legacy = QByteArray(b"legacy-window-geometry")
    settings._qs.setValue("window/geometry", legacy)

    assert settings.restore_geometry() is None

    current = QByteArray(b"current-window-geometry")
    settings.save_geometry(current)

    assert settings.restore_geometry() == current


def test_current_policy_migrates_the_first_broken_restore_version():
    # Version 1 was written while the real Windows window could still reopen
    # minimized or at the pre-migration small geometry. It must be invalidated
    # once so the corrected 80% first-start placement actually becomes visible.
    assert WINDOW_GEOMETRY_POLICY_VERSION >= 2
