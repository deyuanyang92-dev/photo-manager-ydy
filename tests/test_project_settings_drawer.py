"""tests/test_project_settings_drawer.py — ProjectSettingsDrawer 5-tab structure."""
from __future__ import annotations

import sqlite3
from unittest.mock import MagicMock

import pytest

from app.db.db_manager import ensure_schema


@pytest.fixture
def db():
    conn = sqlite3.connect(":memory:")
    ensure_schema(conn)
    yield conn
    conn.close()


def _make_ctx(db=None):
    ctx = MagicMock()
    ctx.get_db.return_value = db
    ctx.current_project_dir = None
    ctx.settings.auto_activate_on_new_specimen = False
    ctx.settings.current_theme = "dark"
    return ctx


def test_drawer_constructs(qtbot):
    from app.widgets.project_settings_drawer import ProjectSettingsDrawer
    ctx = _make_ctx()
    d = ProjectSettingsDrawer(ctx)
    qtbot.addWidget(d)
    assert d is not None


def test_drawer_has_five_tabs(qtbot):
    from app.widgets.project_settings_drawer import ProjectSettingsDrawer
    ctx = _make_ctx()
    d = ProjectSettingsDrawer(ctx)
    qtbot.addWidget(d)
    assert d._tabs.count() == 5
    tab_texts = [d._tabs.tabText(i) for i in range(5)]
    assert "概要" in tab_texts
    assert "保存方式" in tab_texts
    assert "人员预设" in tab_texts
    assert "命名规则" in tab_texts
    assert "TIFF 元数据" in tab_texts


def test_personnel_edits_exist(qtbot):
    from app.widgets.project_settings_drawer import ProjectSettingsDrawer
    ctx = _make_ctx()
    d = ProjectSettingsDrawer(ctx)
    qtbot.addWidget(d)
    assert set(d._person_edits.keys()) == {"verifier", "logistics", "collector", "photographer", "identifier"}


def test_tiff_checks_count(qtbot):
    from app.widgets.project_settings_drawer import ProjectSettingsDrawer
    ctx = _make_ctx()
    d = ProjectSettingsDrawer(ctx)
    qtbot.addWidget(d)
    assert len(d._tiff_checks) == 17


def test_fields_disabled_when_no_db(qtbot):
    from app.widgets.project_settings_drawer import ProjectSettingsDrawer
    ctx = _make_ctx(db=None)
    d = ProjectSettingsDrawer(ctx)
    qtbot.addWidget(d)
    d.refresh()
    assert not d._province_edit.isEnabled()
    assert not d._person_edits["collector"].isEnabled()


def test_fields_enabled_after_refresh_with_db(qtbot, db):
    from app.widgets.project_settings_drawer import ProjectSettingsDrawer
    ctx = _make_ctx(db=db)
    d = ProjectSettingsDrawer(ctx)
    qtbot.addWidget(d)
    d.refresh()
    assert d._province_edit.isEnabled()
    assert d._person_edits["collector"].isEnabled()


def test_personnel_roundtrip(qtbot, db):
    from app.widgets.project_settings_drawer import ProjectSettingsDrawer
    from app.services.project_settings_service import load_setting, DEFAULT_PERSONNEL
    ctx = _make_ctx(db=db)
    d = ProjectSettingsDrawer(ctx)
    qtbot.addWidget(d)
    d.refresh()
    d._person_edits["collector"].setText("张三")
    d._save_personnel()
    data = load_setting(db, "personnel", DEFAULT_PERSONNEL)
    assert data["collector"] == "张三"


def test_tiff_fields_roundtrip(qtbot, db):
    from app.widgets.project_settings_drawer import ProjectSettingsDrawer
    from app.services.project_settings_service import load_setting, DEFAULT_TIFF_FIELDS
    ctx = _make_ctx(db=db)
    d = ProjectSettingsDrawer(ctx)
    qtbot.addWidget(d)
    d.refresh()
    d._tiff_checks["taxonGroup"].setChecked(True)
    d._save_tiff_fields()
    data = load_setting(db, "tiff_fields", DEFAULT_TIFF_FIELDS)
    assert data["taxonGroup"] is True
