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


def _make_ctx(db=None, project_dir=None):
    ctx = MagicMock()
    ctx.get_db.return_value = db
    ctx.current_project_dir = project_dir
    ctx.current_project_root = None
    ctx.settings.auto_activate_on_new_specimen = False
    ctx.settings.delete_jpg_after_archive = True
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


def test_overview_shows_current_project_scope(qtbot, db, tmp_path):
    from app.widgets.project_settings_drawer import ProjectSettingsDrawer

    project_dir = tmp_path / "zhengli"
    project_dir.mkdir()
    ctx = _make_ctx(db=db, project_dir=str(project_dir))
    d = ProjectSettingsDrawer(ctx)
    qtbot.addWidget(d)

    d.refresh()

    assert "zhengli" in d._title_label.text()
    assert str(project_dir) in d._header_scope_lbl.text()
    assert "_data" in d._project_scope_lbl.text()


def test_naming_advanced_settings_are_collapsed_by_default(qtbot):
    from app.widgets.project_settings_drawer import ProjectSettingsDrawer

    d = ProjectSettingsDrawer(_make_ctx())
    qtbot.addWidget(d)

    assert d._naming_advanced_body.isHidden() is True
    d._toggle_naming_advanced()
    assert d._naming_advanced_body.isHidden() is False
    assert "收起" in d._naming_advanced_btn.text()


def test_template_manager_focuses_requested_bucket(qtbot, db, monkeypatch):
    from PyQt6.QtWidgets import QDialog, QWidget
    import app.widgets.label_step2_templates as label_step2_templates
    from app.widgets.project_settings_drawer import ProjectSettingsDrawer

    captured = {}

    class FakeTemplateManager(QWidget):
        def __init__(self, libs, parent=None, **kwargs):
            super().__init__(parent)
            captured["kwargs"] = kwargs

        def set_data(self, specimens, selected_indices):
            captured["specimens"] = specimens
            captured["selected_indices"] = selected_indices

    monkeypatch.setattr(label_step2_templates, "LabelStep2Templates", FakeTemplateManager)
    monkeypatch.setattr(QDialog, "exec", lambda self: QDialog.DialogCode.Accepted)

    ctx = _make_ctx(db=db)
    d = ProjectSettingsDrawer(ctx)
    qtbot.addWidget(d)

    d._open_template_manager("sample")

    assert captured["kwargs"]["visible_buckets"] == ["sample"]
    assert captured["kwargs"]["manager_mode"] is True
    assert captured["selected_indices"]


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


def test_drawer_has_delete_jpg_after_archive_checkbox(qtbot):
    from app.widgets.project_settings_drawer import ProjectSettingsDrawer
    ctx = _make_ctx()
    d = ProjectSettingsDrawer(ctx)
    qtbot.addWidget(d)
    assert hasattr(d, "_delete_jpg_after_archive_cb")
    assert d._delete_jpg_after_archive_cb.isChecked() is True


def test_delete_jpg_after_archive_toggle_persists(qtbot):
    from app.widgets.project_settings_drawer import ProjectSettingsDrawer
    ctx = _make_ctx()
    ctx.settings.flush_to_disk = MagicMock()
    d = ProjectSettingsDrawer(ctx)
    qtbot.addWidget(d)

    d._delete_jpg_after_archive_cb.setChecked(False)

    assert ctx.settings.delete_jpg_after_archive is False
    ctx.settings.flush_to_disk.assert_called()


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


def test_personnel_save_emits_new_defaults(qtbot, db):
    from app.widgets.project_settings_drawer import ProjectSettingsDrawer
    ctx = _make_ctx(db=db)
    d = ProjectSettingsDrawer(ctx)
    qtbot.addWidget(d)
    d.refresh()
    d._person_edits["collector"].setText("张三")
    d._person_edits["photographer"].setText("李四")

    with qtbot.waitSignal(d.personnel_changed, timeout=1000) as signal:
        d._save_personnel()

    assert signal.args[0]["collector"] == "张三"
    assert signal.args[0]["photographer"] == "李四"


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


def test_tiff_metadata_write_settings_roundtrip(qtbot, db):
    from app.widgets.project_settings_drawer import ProjectSettingsDrawer
    from app.services.project_settings_service import load_setting, DEFAULT_TIFF_METADATA_WRITE

    ctx = _make_ctx(db=db)
    d = ProjectSettingsDrawer(ctx)
    qtbot.addWidget(d)
    d.refresh()
    d._tiff_write_enabled_cb.setChecked(False)
    d._tiff_write_mode_combo.setCurrentIndex(d._tiff_write_mode_combo.findData("force"))
    d._save_tiff_metadata_write()

    data = load_setting(db, "tiff_metadata_write", DEFAULT_TIFF_METADATA_WRITE)
    assert data["enabled"] is False
    assert data["mode"] == "force"


def test_print_settings_roundtrip(qtbot, db):
    from app.widgets.project_settings_drawer import ProjectSettingsDrawer
    from app.services.project_settings_service import load_setting, DEFAULT_PRINT_SETTINGS
    ctx = _make_ctx(db=db)
    d = ProjectSettingsDrawer(ctx)
    qtbot.addWidget(d)
    d.refresh()
    assert d._quick_print_mode.findData("studio") == -1
    d._quick_print_mode.setCurrentIndex(d._quick_print_mode.findData("dialog"))
    d._print_tissue_cb.setChecked(True)
    d._save_print_settings()
    data = load_setting(db, "print_settings", DEFAULT_PRINT_SETTINGS)
    assert data["quick_print"] is True
    assert data["include_tissue"] is True
    assert data["sample_printer"] == str(d._sample_printer_combo.currentData() or "")
    assert data["tissue_printer"] == str(d._tissue_printer_combo.currentData() or "")
    assert data["sample_paper_type"] == str(d._sample_paper_combo.currentData())
    assert data["tissue_paper_type"] == str(d._tissue_paper_combo.currentData())
    assert data["sample_template_key"] == str(d._sample_template_combo.currentData())
    assert data["tissue_template_key"] == str(d._tissue_template_combo.currentData())
    assert data["tissue_strategy"] == str(d._tissue_strategy_combo.currentData())
    assert d._save_print_default_btn.text() == "设为全局默认"
    if d._sample_paper_combo.currentData() == "label":
        assert "无需排版" in d._sample_imposition_btn.text()
    else:
        assert "多标签排版" in d._sample_imposition_btn.text()


def test_print_settings_lists_niimbot_virtual_printer(qtbot, db, monkeypatch):
    from app.services import niimbot_print_service as niimbot
    from app.widgets.project_settings_drawer import ProjectSettingsDrawer

    monkeypatch.setattr(
        niimbot,
        "available_printers",
        lambda: [niimbot.NiimbotPrinter(
            id=niimbot.printer_id("COM5"),
            name="NIIMBOT B203 USB (COM5)",
            port="COM5",
        )],
    )
    ctx = _make_ctx(db=db)
    d = ProjectSettingsDrawer(ctx)
    qtbot.addWidget(d)

    d._refresh_printer_combo(d._sample_printer_combo, niimbot.printer_id("COM5"))

    assert d._sample_printer_combo.findData("NIIMBOT:B203:COM5") >= 0
    assert d._sample_printer_combo.currentData() == "NIIMBOT:B203:COM5"


def test_print_settings_shows_niimbot_media_warning(qtbot, db, monkeypatch):
    from app.services import niimbot_print_service as niimbot
    from app.widgets.project_settings_drawer import ProjectSettingsDrawer

    monkeypatch.setattr(
        niimbot,
        "available_printers",
        lambda: [niimbot.NiimbotPrinter(
            id=niimbot.printer_id("COM5"),
            name="NIIMBOT B203 USB (COM5)",
            port="COM5",
        )],
    )
    ctx = _make_ctx(db=db)
    d = ProjectSettingsDrawer(ctx)
    qtbot.addWidget(d)

    d._refresh_printer_combo(d._sample_printer_combo, niimbot.printer_id("COM5"))
    d._sample_paper_combo.setCurrentIndex(d._sample_paper_combo.findData("a4"))
    d._sync_niimbot_print_hint()

    assert not d._niimbot_print_hint.isHidden()
    assert "T40×30mm" in d._niimbot_print_hint.text()
    assert "A4/A5" in d._niimbot_print_hint.text()


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
    assert "无需排版" in d._sample_imposition_btn.text()
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


def test_naming_components_can_include_habitat_and_reorder(qtbot, db):
    from app.widgets.project_settings_drawer import ProjectSettingsDrawer
    from app.services.project_settings_service import load_setting, DEFAULT_NAMING_RULES

    ctx = _make_ctx(db=db)
    d = ProjectSettingsDrawer(ctx)
    qtbot.addWidget(d)
    d.refresh()

    assert "habitat" in d._naming_component_checks
    d._naming_component_checks["habitat"].setChecked(True)
    while d._naming_component_order.index("habitat") > 2:
        d._move_naming_component("habitat", -1)
    d._save_naming_rules()

    data = load_setting(db, "naming_rules", DEFAULT_NAMING_RULES)
    assert data["components"][:4] == ["province", "site", "habitat", "station"]


def test_naming_custom_field_appears_in_required_and_components(qtbot, db):
    from app.widgets.project_settings_drawer import ProjectSettingsDrawer
    from app.services.project_settings_service import load_setting, DEFAULT_NAMING_RULES

    ctx = _make_ctx(db=db)
    d = ProjectSettingsDrawer(ctx)
    qtbot.addWidget(d)
    d.refresh()

    d._naming_custom_fields._add_row("水深", "depth")
    d._save_naming_rules()

    assert "depth" in d._naming_required_checks
    assert "depth" in d._naming_component_checks
    d._naming_required_checks["depth"].setChecked(True)
    d._naming_component_checks["depth"].setChecked(True)
    d._save_naming_rules()

    data = load_setting(db, "naming_rules", DEFAULT_NAMING_RULES)
    assert data["custom_fields"] == [{"key": "depth", "label": "水深"}]
    assert data["required"]["depth"] is True
    assert "depth" in data["components"]


def test_storage_tab_selects_builtin_row_into_edit_form(qtbot, db):
    from app.widgets.project_settings_drawer import ProjectSettingsDrawer

    ctx = _make_ctx(db=db)
    d = ProjectSettingsDrawer(ctx)
    qtbot.addWidget(d)
    d.refresh()
    d._builtin_storage_table.selectRow(5)
    qtbot.wait(50)
    assert d._new_code_edit.text() == "T79"
    assert "梯度酒精" in d._new_detail_edit.toPlainText()


def test_storage_save_upserts_builtin_override(qtbot, db):
    from app.services.project_settings_service import load_custom_storages
    from app.widgets.project_settings_drawer import ProjectSettingsDrawer

    ctx = _make_ctx(db=db)
    d = ProjectSettingsDrawer(ctx)
    qtbot.addWidget(d)
    d.refresh()
    d._load_storage_edit_form("T79", "新的 T79 说明")
    d._save_custom_storage_option()
    custom = load_custom_storages(db)
    assert custom[0]["code"] == "T79"
    assert custom[0]["detail"] == "新的 T79 说明"


def test_storage_save_emits_change_and_supports_multiline(qtbot, db):
    from app.widgets.project_settings_drawer import ProjectSettingsDrawer

    d = ProjectSettingsDrawer(_make_ctx(db=db))
    qtbot.addWidget(d)
    d.refresh()
    d._load_storage_edit_form("D95E", "第一行\n第二行详细说明")
    with qtbot.waitSignal(d.storages_changed, timeout=1000):
        d._save_custom_storage_option()
    assert "已保存" in d._storage_save_status.text()
    assert d._new_code_edit.text() == "D95E"


def test_builtin_override_remains_visible_in_custom_list(qtbot, db):
    """Editing a built-in code is a project override and must not disappear."""
    from PyQt6.QtWidgets import QPushButton
    from app.widgets.project_settings_drawer import ProjectSettingsDrawer

    d = ProjectSettingsDrawer(_make_ctx(db=db))
    qtbot.addWidget(d)
    d.refresh()
    d._load_storage_edit_form("T79", "项目修正说明")
    d._save_custom_storage_option()

    texts = []
    for i in range(d._custom_list_lay.count()):
        widget = d._custom_list_lay.itemAt(i).widget()
        if widget:
            texts.extend(btn.text() for btn in widget.findChildren(QPushButton))
    assert any("T79" in text and "修改内置" in text for text in texts)


def test_new_storage_has_explicit_add_mode(qtbot, db):
    from app.widgets.project_settings_drawer import ProjectSettingsDrawer

    d = ProjectSettingsDrawer(_make_ctx(db=db))
    qtbot.addWidget(d)
    d.refresh()
    d._load_storage_edit_form("RD79", "旧说明")
    assert d._storage_save_btn.text() == "保存修改"
    assert d._new_code_edit.isReadOnly()

    d._new_storage_btn.click()
    assert d._storage_editor_title.text() == "新增保存方式"
    assert d._storage_save_btn.text() == "添加"
    assert d._new_code_edit.text() == ""
    assert not d._new_code_edit.isReadOnly()
