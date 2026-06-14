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
from PyQt6.QtWidgets import QApplication, QFrame, QLabel, QLineEdit
from PyQt6.QtCore import QSettings


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
    """Option 文本只放 code,detail 进 tooltip;全文说明由灰字行承担。"""

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
        from app.widgets.naming_panel import STANDARD_PRESERVATION_METHODS
        details = dict(STANDARD_PRESERVATION_METHODS)
        rows = [r for r in self._method_rows(panel)
                if r.data(Qt.ItemDataRole.UserRole) == "T95E"]
        assert rows, "T95E row missing from storage combo"
        item = rows[0]
        assert item.text() == "T95E", f"expected code-only text, got {item.text()!r}"
        assert item.toolTip() == details["T95E"]

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

    必填(红*): 地区/样地/物种缩写/保存方式/采集日期/拍摄日期
      - 地区/样地虽由项目级默认预填, 仍标必填(值须存在)
      - 拍摄日期 2026-06-14 改定必填(原选填)
    """
    labels = _labels_with_name(panel, "CompactFieldLabel")

    def has_star(kw):
        matches = [l for l in labels if kw in l.text()]
        assert matches, f"{kw} field label not found"
        return "*" in matches[0].text()

    for kw in ("地区", "样地", "物种缩写", "保存方式", "采集日期", "拍摄日期"):
        assert has_star(kw), f"{kw} should be required (*)"
    # 站位选填(缺则 UID 少一段, 非 bug)
    station = [l for l in labels if "站位" in l.text()]
    assert station, "站位 field label not found"
    assert "*" not in station[0].text(), "站位 should stay optional (no *)"
