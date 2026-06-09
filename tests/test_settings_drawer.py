"""test_settings_drawer.py — ProjectSettingsDrawer opacity + scrim regression.

Headless (QT_QPA_PLATFORM=offscreen). Locks the root cause of the "settings
drawer bleeds through to the naming rail" bug:

  - drawer is a QWidget subclass → needs WA_StyledBackground for its QSS
    background to actually paint (otherwise it renders transparent);
  - generated theme.qss carries #SettingsDrawer + #DrawerScrim background rules;
  - workbench shows the scrim on open and hides it on close.

Pure visual results can't be pixel-asserted; these lock the mechanism so it
doesn't regress.
"""
from __future__ import annotations

import os
from unittest.mock import MagicMock

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication

_APP = None


@pytest.fixture(scope="module", autouse=True)
def qt_app():
    global _APP
    if _APP is None:
        _APP = QApplication.instance() or QApplication([])
    return _APP


def _make_ctx(project_dir=None):
    ctx = MagicMock()
    ctx.has_project = project_dir is not None
    ctx.current_project_dir = project_dir
    ctx.get_db.return_value = None
    ctx.settings = MagicMock()
    return ctx


# ── Drawer opacity mechanism ──────────────────────────────────────────────────

class TestDrawerOpacity:
    def test_object_name(self):
        from app.widgets.project_settings_drawer import ProjectSettingsDrawer
        d = ProjectSettingsDrawer(_make_ctx())
        assert d.objectName() == "SettingsDrawer"

    def test_styled_background_attribute_set(self):
        # The whole reason the drawer used to be transparent: a custom QWidget
        # subclass does not paint its stylesheet background without this.
        from app.widgets.project_settings_drawer import ProjectSettingsDrawer
        d = ProjectSettingsDrawer(_make_ctx())
        assert d.testAttribute(Qt.WidgetAttribute.WA_StyledBackground) is True


# ── Generated QSS carries opaque rules ────────────────────────────────────────

class TestThemeQss:
    def test_qss_has_drawer_and_scrim_backgrounds(self):
        from app.config.theme import build_theme_qss_file
        qss_path = build_theme_qss_file()
        qss = qss_path.read_text(encoding="utf-8")
        assert "#SettingsDrawer" in qss
        assert "#DrawerScrim" in qss
        # both must declare a background so nothing behind shows through
        drawer_block = qss[qss.index("#SettingsDrawer"):qss.index("#SettingsDrawer") + 200]
        assert "background" in drawer_block
        scrim_block = qss[qss.index("#DrawerScrim"):qss.index("#DrawerScrim") + 200]
        assert "background" in scrim_block


# ── Workbench scrim open/close ────────────────────────────────────────────────

class TestWorkbenchScrim:
    def test_scrim_shows_on_open_hides_on_close(self):
        from app.views.workbench_view import WorkbenchView
        w = WorkbenchView(_make_ctx())
        scrim = w._settings_scrim
        assert scrim.isHidden()           # hidden until opened
        w._on_open_settings()
        assert not scrim.isHidden()        # shown behind drawer
        w._settings_drawer._on_close()
        assert scrim.isHidden()            # hidden again after close
