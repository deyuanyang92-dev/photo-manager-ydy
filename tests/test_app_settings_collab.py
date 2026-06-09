"""test_app_settings_collab.py — collaboration prefs persistence.

Covers the new AppSettings.collab_enabled / team_code properties used by the
LAN-collaboration subsystem (group-scoped sync + UID claim).

Run:
    QT_QPA_PLATFORM=offscreen python3 -m pytest tests/test_app_settings_collab.py -v
"""
from __future__ import annotations

import pytest

from PyQt6.QtWidgets import QApplication

from app.config.settings import AppSettings


_qapp: QApplication | None = None


def _get_app() -> QApplication:
    global _qapp
    if _qapp is None:
        _qapp = QApplication.instance() or QApplication([])
    return _qapp


@pytest.fixture()
def settings() -> AppSettings:
    _get_app()
    s = AppSettings()
    s._qs.clear()
    s._qs.sync()
    return s


class TestCollabEnabled:
    def test_default_is_false(self, settings: AppSettings) -> None:
        """Sync is OFF until the user opts in (safe default)."""
        assert settings.collab_enabled is False

    def test_set_true_persists(self, settings: AppSettings) -> None:
        settings.collab_enabled = True
        settings.sync()
        assert AppSettings().collab_enabled is True

    def test_set_false_persists(self, settings: AppSettings) -> None:
        settings.collab_enabled = True
        settings.collab_enabled = False
        settings.sync()
        assert AppSettings().collab_enabled is False


class TestTeamCode:
    def test_default_is_empty(self, settings: AppSettings) -> None:
        """Empty team code = no group = no sync (the isolation default)."""
        assert settings.team_code == ""

    def test_set_persists(self, settings: AppSettings) -> None:
        settings.team_code = "SMW-2026"
        settings.sync()
        assert AppSettings().team_code == "SMW-2026"

    def test_whitespace_trimmed(self, settings: AppSettings) -> None:
        settings.team_code = "  SMW-2026  "
        assert settings.team_code == "SMW-2026"

    def test_clear_to_empty(self, settings: AppSettings) -> None:
        settings.team_code = "X"
        settings.team_code = ""
        settings.sync()
        assert AppSettings().team_code == ""
