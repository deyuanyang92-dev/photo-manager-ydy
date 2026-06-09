"""test_app_context.py — AppContext.current_project_root (folder-tree-inherit).

`current_project_root` becomes a first-class field (was an ad-hoc attribute set
only by ProjectTreeView). It bounds the settings-inheritance walk in
`project_settings_service.get_effective`, so EVERY workspace-entry path must set
it consistently — otherwise inheritance silently walks to the filesystem root.

Run:
    QT_QPA_PLATFORM=offscreen python3 -m pytest tests/test_app_context.py -v
"""
from __future__ import annotations

import sqlite3

import pytest

from PyQt6.QtWidgets import QApplication

from app.app_context import AppContext
from app.config.settings import AppSettings


_qapp: QApplication | None = None


def _get_app() -> QApplication:
    global _qapp
    if _qapp is None:
        _qapp = QApplication.instance() or QApplication([])
    return _qapp


@pytest.fixture()
def ctx() -> AppContext:
    _get_app()
    # Start from a clean QSettings so persistence assertions are deterministic.
    s = AppSettings()
    s._qs.clear()
    s._qs.sync()
    return AppContext()


class TestCurrentProjectRoot:
    def test_default_is_none(self, ctx: AppContext) -> None:
        assert ctx.current_project_root is None

    def test_setter_persists_to_settings(self, ctx: AppContext) -> None:
        ctx.current_project_root = "/srv/survey-root"
        assert ctx.current_project_root == "/srv/survey-root"
        # Persisted via the existing project_tree_root key.
        assert AppSettings().project_tree_root == "/srv/survey-root"

    def test_independent_of_project_dir(self, ctx: AppContext) -> None:
        # Setting the workspace dir must NOT implicitly set the root, and vice
        # versa — the two are distinct (a workspace can be its own root).
        ctx.current_project_dir = "/srv/survey-root/断面a"
        assert ctx.current_project_root is None

    def test_set_none_clears(self, ctx: AppContext) -> None:
        ctx.current_project_root = "/srv/x"
        ctx.current_project_root = None
        assert ctx.current_project_root is None


class TestGetDbFailure:
    def test_current_project_db_error_clears_project(self, ctx: AppContext, monkeypatch) -> None:
        ctx.current_project_dir = "/readonly/project"

        def fail_open(_path: str):
            raise sqlite3.OperationalError("attempt to write a readonly database")

        monkeypatch.setattr("app.app_context.open_project_db", fail_open)

        assert ctx.get_db() is None
        assert ctx.current_project_dir is None
        assert AppSettings().last_project_dir is None
        assert isinstance(ctx.last_db_error, sqlite3.OperationalError)

    def test_explicit_project_db_error_does_not_clear_current_project(
        self, ctx: AppContext, monkeypatch
    ) -> None:
        ctx.current_project_dir = "/active/project"

        def fail_open(_path: str):
            raise sqlite3.OperationalError("attempt to write a readonly database")

        monkeypatch.setattr("app.app_context.open_project_db", fail_open)

        assert ctx.get_db("/readonly/project") is None
        assert ctx.current_project_dir == "/active/project"
