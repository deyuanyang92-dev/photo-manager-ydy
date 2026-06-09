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

    def test_date_group_retains_title_label(self, panel):
        frame = panel._date_group
        titles = _labels_with_name(frame, "NamingGroupTitle")
        assert len(titles) == 1, "日期 frame should keep its NamingGroupTitle"
        assert titles[0].text() == "日期"


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
