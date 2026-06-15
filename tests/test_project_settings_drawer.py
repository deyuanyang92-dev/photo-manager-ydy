"""tests/test_project_settings_drawer.py — ProjectSettingsDrawer tab structure."""
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


def test_drawer_has_printing_tab(qtbot):
    from app.widgets.project_settings_drawer import ProjectSettingsDrawer
    ctx = _make_ctx()
    d = ProjectSettingsDrawer(ctx)
    qtbot.addWidget(d)
    assert d._tabs.count() == 6
    tab_texts = [d._tabs.tabText(i) for i in range(d._tabs.count())]
    assert "概要" in tab_texts
    assert "保存" in tab_texts
    assert "人员" in tab_texts
    assert "命名" in tab_texts
    assert "TIFF" in tab_texts
    assert "打印" in tab_texts


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
    assert not d._sample_imposition_btn.isEnabled()
    assert not d._tissue_imposition_btn.isEnabled()


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


def test_print_settings_roundtrip(qtbot, db):
    from app.widgets.project_settings_drawer import ProjectSettingsDrawer
    from app.services.project_settings_service import load_setting, DEFAULT_PRINT_SETTINGS
    ctx = _make_ctx(db=db)
    d = ProjectSettingsDrawer(ctx)
    qtbot.addWidget(d)
    d.refresh()
    d._quick_print_mode.setCurrentIndex(d._quick_print_mode.findData(False))
    d._print_tissue_cb.setChecked(True)
    d._save_print_settings()
    data = load_setting(db, "print_settings", DEFAULT_PRINT_SETTINGS)
    assert data["quick_print"] is False
    assert data["include_tissue"] is True
    assert data["sample_printer"] == ""
    assert data["tissue_printer"] == ""
    assert data["sample_paper_type"] == ""
    assert data["tissue_paper_type"] == ""
    assert data["sample_template_key"] == ""
    assert data["tissue_template_key"] == ""
    assert data["tissue_strategy"] == "auto"
    assert d._save_print_default_btn.text() == "设为全局默认"
    assert "酒精标签排版设计" in d._sample_imposition_btn.text()
    assert "RNA 标签排版设计" in d._tissue_imposition_btn.text()


def test_imposition_buttons_follow_sheet_paper_selection(qtbot, db):
    from app.widgets.project_settings_drawer import ProjectSettingsDrawer
    ctx = _make_ctx(db=db)
    d = ProjectSettingsDrawer(ctx)
    qtbot.addWidget(d)
    d.refresh()

    d._sample_paper_combo.setCurrentIndex(d._sample_paper_combo.findData("label"))
    d._tissue_paper_combo.setCurrentIndex(d._tissue_paper_combo.findData("label"))
    assert not d._sample_imposition_btn.isEnabled()
    assert not d._tissue_imposition_btn.isEnabled()
    assert "选择 A4/A5" in d._sample_imposition_btn.text()
    assert "直接打印" in d._sample_imposition_btn.toolTip()

    d._sample_paper_combo.setCurrentIndex(d._sample_paper_combo.findData("a4"))
    d._tissue_paper_combo.setCurrentIndex(d._tissue_paper_combo.findData("a5"))
    assert d._sample_imposition_btn.isEnabled()
    assert d._tissue_imposition_btn.isEnabled()
    assert "A4 合版" in d._sample_imposition_btn.toolTip()
    assert "A5 合版" in d._tissue_imposition_btn.toolTip()


def test_print_imposition_designer_persists_from_settings_drawer(qtbot, db, monkeypatch):
    from PyQt6.QtWidgets import QDialog
    from app.services import label_service
    from app.widgets.label_imposition_dialog import LabelImpositionDialog
    from app.widgets.project_settings_drawer import ProjectSettingsDrawer

    ctx = _make_ctx(db=db)
    d = ProjectSettingsDrawer(ctx)
    qtbot.addWidget(d)
    d.refresh()
    d._sample_paper_combo.setCurrentIndex(d._sample_paper_combo.findData("a4"))
    label_service.persist_imposition("sample", {})
    monkeypatch.setattr(
        LabelImpositionDialog,
        "exec",
        lambda self: QDialog.DialogCode.Accepted,
    )
    monkeypatch.setattr(
        LabelImpositionDialog,
        "imposition",
        lambda self: {"marginMm": 5.0, "forceCols": 3},
    )

    d._open_imposition_designer("sample")

    assert label_service.persisted_imposition("sample") == {
        "marginMm": 5.0,
        "forceCols": 3,
    }
    label_service.persist_imposition("sample", {})


def test_naming_required_fields_roundtrip(qtbot, db):
    from app.widgets.project_settings_drawer import ProjectSettingsDrawer
    from app.services.project_settings_service import load_setting, DEFAULT_NAMING_RULES
    ctx = _make_ctx(db=db)
    d = ProjectSettingsDrawer(ctx)
    qtbot.addWidget(d)
    d.refresh()
    d._naming_required_checks["station"].setChecked(True)
    d._naming_required_checks["photo_date"].setChecked(False)
    d._save_naming_rules()
    data = load_setting(db, "naming_rules", DEFAULT_NAMING_RULES)
    assert data["required"]["station"] is True
    assert data["required"]["photo_date"] is False


def test_naming_components_roundtrip(qtbot, db):
    from app.widgets.project_settings_drawer import ProjectSettingsDrawer
    from app.services.project_settings_service import load_setting, DEFAULT_NAMING_RULES
    ctx = _make_ctx(db=db)
    d = ProjectSettingsDrawer(ctx)
    qtbot.addWidget(d)
    d.refresh()
    d._naming_component_checks["scientific_name"].setChecked(True)
    d._naming_component_checks["notes"].setChecked(True)
    d._save_naming_rules()
    data = load_setting(db, "naming_rules", DEFAULT_NAMING_RULES)
    assert "scientific_name" in data["components"]
    assert "notes" in data["components"]
