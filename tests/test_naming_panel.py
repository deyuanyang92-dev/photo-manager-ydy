"""Tests for NamingPanel widget (app/widgets/naming_panel.py).

Covers:
- 采集位置/编号规则 frames have no NamingGroupTitle label (titles removed)
- 日期 frame retains NamingGroupTitle (control group)
- 地区/样地 QLineEdit minimumWidth >= 60
- 拍照备注 QTextEdit auto-grows on content change
- ☰ sections button present in header
- Section visibility toggle persists to QSettings
"""
import pytest
from unittest.mock import MagicMock
from PyQt6.QtWidgets import QApplication, QFrame, QLabel, QLineEdit, QPushButton
from PyQt6.QtCore import QSettings, Qt


@pytest.fixture(scope="module")
def qapp():
    import sys
    app = QApplication.instance() or QApplication(sys.argv)
    return app


@pytest.fixture()
def ctx():
    mock = MagicMock()
    mock.get_db.return_value = None
    return mock


@pytest.fixture()
def panel(qapp, ctx):
    # Use a temporary QSettings scope so tests don't pollute user settings.
    QSettings.setDefaultFormat(QSettings.Format.IniFormat)
    from PyQt6.QtCore import QCoreApplication
    QCoreApplication.setOrganizationName("test_naming_panel")
    QCoreApplication.setApplicationName("test_naming_panel")
    QSettings().clear()

    from app.widgets.naming_panel import NamingPanel
    p = NamingPanel(ctx)
    p.show()
    yield p
    p.close()
    QSettings().clear()


def _labels_with_name(widget, obj_name):
    """Recursively collect QLabel children with given objectName."""
    found = []
    for child in widget.findChildren(QLabel):
        if child.objectName() == obj_name:
            found.append(child)
    return found


# ── Section title labels ───────────────────────────────────────────────────

class TestSectionTitles:
    def test_geo_group_has_no_title_label(self, panel):
        frame = panel._geo_group
        titles = _labels_with_name(frame, "NamingGroupTitle")
        assert titles == [], "采集位置 frame should have no NamingGroupTitle"

    def test_identity_group_has_no_title_label(self, panel):
        frame = panel._identity_group
        titles = _labels_with_name(frame, "NamingGroupTitle")
        assert titles == [], "编号规则 frame should have no NamingGroupTitle"

    def test_date_group_has_no_title_label(self, panel):
        # 紧凑化：日期段去掉「日期」段标题以省空间（字段仍在，整段可在 ☰ 菜单隐藏）。
        frame = panel._date_group
        titles = _labels_with_name(frame, "NamingGroupTitle")
        assert titles == [], "日期 frame should have no NamingGroupTitle (compact)"


# ── Adaptive field widths ──────────────────────────────────────────────────

class TestAdaptiveFields:
    def test_province_min_width(self, panel):
        assert panel._province.minimumWidth() >= 60

    def test_site_min_width(self, panel):
        assert panel._site.minimumWidth() >= 60


# ── Auto-grow notes ────────────────────────────────────────────────────────

class TestAutoGrowNotes:
    def test_notes_initial_height_reasonable(self, panel):
        h = panel._photo_notes.height()
        assert 40 <= h <= 100, f"initial height {h} out of expected range"

    def test_notes_grows_on_long_text(self, qapp, panel):
        initial_h = panel._photo_notes.height()
        long_text = "\n".join(["拍照现场备注，曝光异常"] * 8)
        panel._photo_notes.setPlainText(long_text)
        qapp.processEvents()
        grown_h = panel._photo_notes.height()
        assert grown_h > initial_h, (
            f"notes height {grown_h} should exceed initial {initial_h} after long text"
        )

    def test_notes_shrinks_on_clear(self, qapp, panel):
        long_text = "\n".join(["line"] * 10)
        panel._photo_notes.setPlainText(long_text)
        qapp.processEvents()
        panel._photo_notes.setPlainText("")
        qapp.processEvents()
        h = panel._photo_notes.height()
        assert h <= 80, f"height {h} should be near minimum after clearing"


# ── ☰ sections button ──────────────────────────────────────────────────────

class TestSectionsButton:
    def test_sections_btn_exists(self, panel):
        assert hasattr(panel, "_sections_btn"), "NamingPanel missing _sections_btn"

    def test_sections_btn_visible(self, panel):
        assert panel._sections_btn.isVisible()

    def test_sections_btn_tooltip(self, panel):
        assert panel._sections_btn.toolTip() != ""


# ── Section visibility + QSettings persistence ─────────────────────────────

class TestSectionVisibilityPersistence:
    def test_geo_group_visible_by_default(self, panel):
        assert panel._geo_group.isVisible()

    def test_toggle_geo_hides_frame(self, panel):
        panel._toggle_section("geo", panel._geo_group, False)
        assert not panel._geo_group.isVisible()
        panel._toggle_section("geo", panel._geo_group, True)  # restore

    def test_toggle_persists_to_qsettings(self, panel):
        panel._toggle_section("geo", panel._geo_group, False)
        val = panel._load_section_vis("geo")
        assert val is False
        panel._toggle_section("geo", panel._geo_group, True)  # restore

    def test_load_missing_key_returns_default(self, panel):
        val = panel._load_section_vis("nonexistent_key", default=True)
        assert val is True


# ── 保存方式下拉:收起只显缩写 (oracle app.js:9268-9271) ─────────────────────

class TestStorageComboDisplay:
    """Option 只显示 code；详细说明由下方灰字行承担。"""

    def _method_rows(self, panel):
        from PyQt6.QtCore import Qt
        model = panel._storage_combo.model()
        rows = []
        for i in range(model.rowCount()):
            item = model.item(i)
            code = item.data(Qt.ItemDataRole.UserRole)
            if code and code != "__custom__":
                rows.append(item)
        return rows

    def test_method_items_show_code_only(self, panel):
        from PyQt6.QtCore import Qt
        rows = [r for r in self._method_rows(panel)
                if r.data(Qt.ItemDataRole.UserRole) == "T95E"]
        assert rows, "T95E row missing from storage combo"
        item = rows[0]
        assert item.text() == "T95E", f"expected code-only text, got {item.text()!r}"
        assert item.toolTip() == ""

    def test_project_override_refreshes_detail(self, qapp):
        import sqlite3
        from unittest.mock import MagicMock
        from app.db.db_manager import ensure_schema
        from app.services.project_settings_service import save_setting
        from app.widgets.naming_panel import NamingPanel

        db = sqlite3.connect(":memory:")
        ensure_schema(db)
        save_setting(db, "custom_storages", [{
            "code": "T95E",
            "detail": "项目修改后的详细说明",
            "transcriptome": False,
        }])
        ctx = MagicMock()
        ctx.get_db.return_value = db
        p = NamingPanel(ctx)
        p._storage.setText("T95E")
        p.refresh_storage_methods()
        assert p._pres_detail.text() == "项目修改后的详细说明"
        p.close()
        db.close()

    def test_all_method_rows_text_equals_userrole_code(self, panel):
        from PyQt6.QtCore import Qt
        for item in self._method_rows(panel):
            code = item.data(Qt.ItemDataRole.UserRole)
            assert item.text() == code, \
                f"row text {item.text()!r} != code {code!r}"

    def test_storage_value_roundtrip_unchanged(self, panel):
        panel._on_storage_btn("R95E")
        assert panel._storage.text() == "R95E"
        assert "R95E" in panel.current_uid()


def test_date_section_visible_for_input(panel):
    """采集日期/拍摄日期字段必须可见 —— 用户手填，喂 UID 日期段 + 标本记录。

    曾被 `_date_group.hide()` 永久隐藏（错误），现改为默认可见的分区。
    """
    assert hasattr(panel, "_collection_date")
    assert hasattr(panel, "_photo_date")
    assert not panel._date_group.isHidden()


def test_required_fields_marked(panel):
    """标本 UID 必填字段带红*; 站位/成果序号/拍照备注 选填(无*)。

    必填(红*): 省/市/地区/样地/物种编号/保存方式/采集日期/拍摄日期
      - 省/市、地区/样地虽由项目级默认预填, 仍标必填(值须存在)
      - 拍摄日期 2026-06-14 改定必填(原选填)
    """
    labels = _labels_with_name(panel, "CompactFieldLabel")

    def has_star(kw):
        matches = [l for l in labels if kw in l.text()]
        assert matches, f"{kw} field label not found"
        return "*" in matches[0].text()

    for kw in ("省/市", "地区/样地", "物种编号", "保存方式", "采集日期", "拍摄日期"):
        assert has_star(kw), f"{kw} should be required (*)"
    # 站位选填(缺则 UID 少一段, 非 bug)
    station = [l for l in labels if "站位" in l.text()]
    assert station, "站位 field label not found"
    assert "*" not in station[0].text(), "站位 should stay optional (no *)"


def test_preview_add_state_has_separate_save_button(panel):
    buttons = panel.findChildren(QPushButton)
    assert panel._pin_btn.text() == "添加"
    assert panel._preview_save_btn in buttons
    assert panel._preview_save_btn.text() == "保存"
    assert panel._preview_save_btn.isVisible()
    assert panel._update_btn.isHidden()


def test_preview_save_and_add_buttons_visible_when_editing_existing(panel):
    panel.load_specimen({
        "uid": "GXFCG-BLW-SC001-D79-20260618",
        "province": "GXFCG",
        "site": "BLW",
        "id": "SC001",
        "storage": "D79",
        "collectionDate": "20260618",
        "photoDate": "20260618",
    })

    assert panel._preview_save_btn.text() == "保存"
    assert panel._preview_save_btn.isVisible()
    assert panel._pin_btn.text() == "添加"
    assert panel._pin_btn.isVisible()
    assert panel._update_btn.isVisible()


def test_load_specimen_prefers_uid_segments_over_stale_raw_fields(panel):
    panel.load_specimen({
        "uid": "GXFCG-BLW-SC002-RD79-20260618",
        "province": "GXFCG",
        "site": "BLW",
        "id": "RD79",
        "storage": "RD79",
        "collectionDate": "20260618",
        "photoDate": "20260618",
    })

    assert panel._species_id.text() == "SC002"
    assert panel._storage.text() == "RD79"
    assert panel.current_uid() == "GXFCG-BLW-SC002-RD79-20260618"


def test_storage_combo_shows_unlisted_storage_code(panel):
    panel._province.setText("GXFCG")
    panel._site.setText("BLW")
    panel._species_id.setText("SC002")
    panel._collection_date.setText("20260618")
    panel._photo_date.setText("20260618")
    panel._storage.setText("R")

    assert panel._storage_combo.currentText() == "R"
    assert panel._storage_combo.currentData(Qt.ItemDataRole.UserRole) == "R"
    assert panel.current_uid() == "GXFCG-BLW-SC002-R-20260618"


def test_uid_code_fields_auto_uppercase(panel):
    """地区/样地/站位/样品标签输入小写时，控件和 UID 预览都自动转大写。"""
    panel._province.setText("fj")
    panel._site.setText("d")
    panel._station.setText("f")
    panel._species_id.setText("dd001")
    panel._storage.setText("T95E")
    panel._collection_date.setText("20260612")
    panel._photo_date.setText("20260613")

    assert panel._province.text() == "FJ"
    assert panel._site.text() == "D"
    assert panel._station.text() == "F"
    assert panel._species_id.text() == "DD001"
    assert panel.current_uid() == "FJ-D-F-DD001-T95E-20260612-0613"


def test_field_sample_label_does_not_trigger_species_code_warning(panel):
    panel._province.setText("FJ")
    panel._site.setText("S1")
    panel._station.setText("A")
    panel._species_id.setText("MIX01")
    panel._storage.setText("T95E")
    panel._collection_date.setText("20260612")
    panel._photo_date.setText("20260612")

    panel._check_compliance(panel.current_uid())

    assert panel._compliance_warn.isHidden()


def test_project_naming_rules_control_required_stars_and_warning(qapp):
    import sqlite3
    from app.db.db_manager import ensure_schema
    from app.services.project_settings_service import DEFAULT_NAMING_RULES, save_setting
    from app.widgets.naming_panel import NamingPanel

    db = sqlite3.connect(":memory:")
    ensure_schema(db)
    rules = dict(DEFAULT_NAMING_RULES)
    rules["required"] = dict(DEFAULT_NAMING_RULES["required"])
    rules["required"]["station"] = True
    rules["required"]["photo_date"] = False
    save_setting(db, "naming_rules", rules)

    ctx = MagicMock()
    ctx.get_db.return_value = db
    p = NamingPanel(ctx)
    p.show()
    try:
        labels = _labels_with_name(p, "CompactFieldLabel")
        station = [l for l in labels if "站位" in l.text()][0]
        photo_date = [l for l in labels if "拍摄日期" in l.text()][0]
        assert "*" in station.text()
        assert "*" not in photo_date.text()

        p._province.setText("FJ")
        p._site.setText("SMW")
        p._species_id.setText("DD001")
        p._storage.setText("T95E")
        p._collection_date.setText("20260612")
        qapp.processEvents()
        assert p._compliance_warn.isVisible()
        assert "缺少必填：站位" in p._compliance_warn.text()
    finally:
        p.close()
        db.close()


def test_project_naming_components_can_include_taxonomy_and_notes(qapp):
    import sqlite3
    from app.db.db_manager import ensure_schema
    from app.services.project_settings_service import DEFAULT_NAMING_RULES, save_setting
    from app.widgets.naming_panel import NamingPanel

    db = sqlite3.connect(":memory:")
    ensure_schema(db)
    rules = dict(DEFAULT_NAMING_RULES)
    rules["components"] = [
        "province", "site", "species_id", "scientific_name", "notes", "date_seg"
    ]
    save_setting(db, "naming_rules", rules)

    ctx = MagicMock()
    ctx.get_db.return_value = db
    p = NamingPanel(ctx)
    p.show()
    try:
        p._province.setText("FJ")
        p._site.setText("D")
        p._species_id.setText("DD001")
        p._collection_date.setText("20260612")
        p._photo_date.setText("20260613")
        p.set_external_naming_values({
            "scientific_name": "Marphysa sanguinea",
            "notes": "red-form",
        })
        assert p.current_uid() == "FJ-D-DD001-Marphysa_sanguinea-red_form-20260612-0613"
    finally:
        p.close()
        db.close()


def test_project_naming_components_can_include_habitat_dynamic_field(qapp):
    import sqlite3
    from app.db.db_manager import ensure_schema
    from app.services.project_settings_service import DEFAULT_NAMING_RULES, save_setting
    from app.widgets.naming_panel import NamingPanel

    db = sqlite3.connect(":memory:")
    ensure_schema(db)
    rules = dict(DEFAULT_NAMING_RULES)
    rules["components"] = [
        "province", "site", "habitat", "species_id", "storage", "date_seg"
    ]
    rules["required"] = dict(DEFAULT_NAMING_RULES["required"])
    rules["required"]["habitat"] = True
    save_setting(db, "naming_rules", rules)

    ctx = MagicMock()
    ctx.get_db.return_value = db
    p = NamingPanel(ctx)
    p.show()
    try:
        assert "habitat" in p._dynamic_naming_edits
        p._province.setText("FJ")
        p._site.setText("SMW")
        p._species_id.setText("DLC001")
        p._storage.setText("T95E")
        p._collection_date.setText("20260612")
        qapp.processEvents()
        assert "生境" in p.missing_required_fields()

        p._dynamic_naming_edits["habitat"].setText("泥滩")
        assert p.current_uid() == "FJ-SMW-泥滩-DLC001-T95E-20260612"
        assert p.naming_extra_field_values()["habitat"] == "泥滩"
    finally:
        p.close()
        db.close()


def test_project_custom_naming_field_uses_project_label(qapp):
    import sqlite3
    from app.db.db_manager import ensure_schema
    from app.services.project_settings_service import DEFAULT_NAMING_RULES, save_setting
    from app.widgets.naming_panel import NamingPanel

    db = sqlite3.connect(":memory:")
    ensure_schema(db)
    rules = dict(DEFAULT_NAMING_RULES)
    rules["custom_fields"] = [{"key": "depth", "label": "水深"}]
    rules["components"] = ["province", "site", "depth", "species_id", "date_seg"]
    rules["required"] = dict(DEFAULT_NAMING_RULES["required"])
    rules["required"]["depth"] = True
    save_setting(db, "naming_rules", rules)

    ctx = MagicMock()
    ctx.get_db.return_value = db
    p = NamingPanel(ctx)
    p.show()
    try:
        assert "depth" in p._dynamic_naming_edits
        assert any("水深" in label.text() for label in p._field_labels.values())
        p._province.setText("FJ")
        p._site.setText("SMW")
        p._species_id.setText("DLC001")
        p._collection_date.setText("20260612")
        assert "水深" in p.missing_required_fields()
        p._dynamic_naming_edits["depth"].setText("12m")
        assert p.current_uid() == "FJ-SMW-12m-DLC001-20260612"
    finally:
        p.close()
        db.close()


class TestUidDisplaySummary:
    def test_default_summary_shows_people_and_photo_notes(self, panel):
        panel.set_display_metadata({
            "collector": "张三", "photographer": "李四",
            "notes": "标本备注", "photo_notes": "曝光异常，需要补拍",
        })
        assert panel._display_people.text() == "采集：张三  ·  拍摄：李四"
        assert "拍照备注：曝光异常" in panel._display_notes.text()
        assert "标本备注" not in panel._display_notes.text()

    def test_empty_values_have_no_dangling_separator(self, panel):
        panel.set_display_metadata({"collector": "张三", "photographer": ""})
        assert panel._display_people.text() == "采集：张三"

    def test_custom_field_selection_persists(self, panel):
        panel.set_display_fields({"collector", "notes"})
        assert panel._load_display_fields() == {"collector", "notes"}
        panel.set_display_metadata({"collector": "A", "notes": "N"})
        assert panel._display_people.text() == "采集：A"
        assert panel._display_notes.text() == "备注：N"

    def test_copy_uid_still_copies_only_uid(self, panel):
        panel._province.setText("FJ")
        panel._site.setText("XM")
        panel._station.setText("B2")
        panel._species_id.setText("DLC003")
        panel._storage.setText("T95E")
        panel._collection_date.setText("20260602")
        panel._photo_date.setText("20260602")
        panel.set_display_metadata({"collector": "张三"})
        panel._copy_uid()
        assert QApplication.clipboard().text() == panel.current_uid()
        assert "张三" not in QApplication.clipboard().text()
