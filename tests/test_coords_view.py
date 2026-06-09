"""test_coords_view.py — Smoke + interaction tests for CoordsView.

Runs headless (QT_QPA_PLATFORM=offscreen).

Covers:
  - CoordsView instantiates and shows without crashing.
  - All expected widgets are present (input, badge, cs-tab buttons, struct toggle,
    batch toggle, place search input).
  - Typing a valid DD coord shows green badge and CS cards.
  - Typing an invalid string shows red badge.
  - Clearing input hides badge and CS section.
  - CS tab switching updates the card values.
  - Structured DMS inputs sync to main input.
  - Batch parse: valid rows populate table; invalid rows show error.
  - Batch format toggle DD / DMS / DDM works.
  - Batch CS toggle wgs84 / gcj02 / bd09 works (numeric shift for China coords).
  - Batch CSV export contains correct header.
  - on_activate() is callable without crashing.
"""
from __future__ import annotations

import os
from unittest.mock import MagicMock

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QLabel, QLineEdit, QPushButton

from app.app_context import AppContext
from app.views.coords_view import CoordsView

# ── Qt singleton ──────────────────────────────────────────────────────────────

_APP = None


@pytest.fixture(scope="module", autouse=True)
def qt_app():
    global _APP
    if _APP is None:
        _APP = QApplication.instance() or QApplication([])
    return _APP


# ── Helpers ────────────────────────────────────────────────────────────────────

def _make_ctx():
    ctx = MagicMock(spec=AppContext)
    ctx.has_project = False
    ctx.current_project_dir = None
    ctx.settings = MagicMock()
    return ctx


def _view() -> CoordsView:
    v = CoordsView(_make_ctx())
    v.resize(1024, 768)
    v.show()
    QApplication.processEvents()
    return v


# ══════════════════════════════════════════════════════════════════════════════
# Construction smoke test
# ══════════════════════════════════════════════════════════════════════════════

class TestConstruction:
    def test_instantiates(self):
        v = _view()
        assert v is not None
        assert v.view_id == "coords"
        assert v.nav_title == "坐标工具"

    def test_input_edit_present(self):
        v = _view()
        assert v._input_edit is not None
        assert isinstance(v._input_edit, QLineEdit)

    def test_badge_initially_hidden(self):
        v = _view()
        assert not v._badge.isVisible()

    def test_cs_section_initially_hidden(self):
        v = _view()
        assert not v._cs_section.isVisible()

    def test_struct_widget_initially_hidden(self):
        v = _view()
        assert not v._struct_widget.isVisible()

    def test_batch_body_initially_hidden(self):
        v = _view()
        assert not v._batch_body.isVisible()

    def test_cs_tab_buttons_all_present(self):
        v = _view()
        assert set(v._cs_tab_btns.keys()) == {"dd", "dms", "ddm"}

    def test_on_activate_no_crash(self):
        v = _view()
        v.on_activate()  # must not raise


# ══════════════════════════════════════════════════════════════════════════════
# Input → badge
# ══════════════════════════════════════════════════════════════════════════════

class TestBadge:
    def test_valid_dd_shows_green_badge(self):
        v = _view()
        v._input_edit.setText("29.11492 N 121.76421 E")
        QApplication.processEvents()
        assert v._badge.isVisible()
        assert "lat" in v._badge.text().lower() or "29" in v._badge.text()
        # Valid badge uses the active theme's accent colour (theme-following).
        from app.config.theme import TOKENS
        assert TOKENS["accent"] in v._badge.styleSheet()

    def test_invalid_input_shows_red_badge(self):
        v = _view()
        v._input_edit.setText("not a coord at all !!")
        QApplication.processEvents()
        assert v._badge.isVisible()
        # Invalid badge uses the active theme's danger colour (theme-following).
        from app.config.theme import TOKENS
        assert TOKENS["danger"] in v._badge.styleSheet()

    def test_clear_hides_badge(self):
        v = _view()
        v._input_edit.setText("29.11492 N 121.76421 E")
        QApplication.processEvents()
        v._input_edit.clear()
        QApplication.processEvents()
        assert not v._badge.isVisible()

    def test_dms_input_shows_badge(self):
        v = _view()
        v._input_edit.setText("29°06'53.7\"N 121°45'51.2\"E")
        QApplication.processEvents()
        assert v._badge.isVisible()
        assert "DMS" in v._badge.text() or "度分秒" in v._badge.text()

    def test_iso6709_input(self):
        v = _view()
        v._input_edit.setText("+29.11492+121.76421/")
        QApplication.processEvents()
        assert v._badge.isVisible()
        from app.config.theme import TOKENS
        assert TOKENS["accent"] in v._badge.styleSheet()


# ══════════════════════════════════════════════════════════════════════════════
# CS cards
# ══════════════════════════════════════════════════════════════════════════════

class TestCsCards:
    def test_cs_section_visible_after_valid_input(self):
        v = _view()
        v._input_edit.setText("29.11492 N 121.76421 E")
        QApplication.processEvents()
        assert v._cs_section.isVisible()

    def test_three_cs_cards_present(self):
        v = _view()
        v._input_edit.setText("29.11492 N 121.76421 E")
        QApplication.processEvents()
        # Count cards inside cs_cards_widget
        count = v._cs_cards_lay.count()
        assert count == 3, f"expected 3 CS cards, got {count}"

    def test_cs_section_hidden_after_clear(self):
        v = _view()
        v._input_edit.setText("29.11492 N 121.76421 E")
        QApplication.processEvents()
        v._input_edit.clear()
        QApplication.processEvents()
        assert not v._cs_section.isVisible()

    def test_cs_tab_dd_selected_by_default(self):
        v = _view()
        assert v._cs_tab == "dd"
        assert v._cs_tab_btns["dd"].isChecked()
        assert not v._cs_tab_btns["dms"].isChecked()

    def test_cs_tab_switch_to_dms(self):
        v = _view()
        v._input_edit.setText("29.11492 N 121.76421 E")
        QApplication.processEvents()
        v._on_cs_tab("dms")
        QApplication.processEvents()
        assert v._cs_tab == "dms"
        assert v._cs_tab_btns["dms"].isChecked()
        assert not v._cs_tab_btns["dd"].isChecked()
        # Cards still 3
        assert v._cs_cards_lay.count() == 3

    def test_cs_tab_switch_to_ddm(self):
        v = _view()
        v._input_edit.setText("29.11492 N 121.76421 E")
        QApplication.processEvents()
        v._on_cs_tab("ddm")
        assert v._cs_tab == "ddm"


# ══════════════════════════════════════════════════════════════════════════════
# Structured DMS
# ══════════════════════════════════════════════════════════════════════════════

class TestStructuredDms:
    def test_toggle_shows_widget(self):
        v = _view()
        assert not v._struct_widget.isVisible()
        v._on_struct_toggle()
        QApplication.processEvents()
        assert v._struct_widget.isVisible()

    def test_toggle_twice_hides(self):
        v = _view()
        v._on_struct_toggle()
        v._on_struct_toggle()
        QApplication.processEvents()
        assert not v._struct_widget.isVisible()

    def test_struct_sync_fills_main_input(self):
        v = _view()
        v._on_struct_toggle()
        # Set 29°06'53.7"N 121°45'51.2"E via struct fields
        v._struct_lat_d.setText("29")
        v._struct_lat_m.setText("6")
        v._struct_lat_s.setText("53.7")
        v._struct_lat_dir.setCurrentText("N")
        v._struct_lon_d.setText("121")
        v._struct_lon_m.setText("45")
        v._struct_lon_s.setText("51.2")
        v._struct_lon_dir.setCurrentText("E")
        QApplication.processEvents()
        txt = v._input_edit.text()
        assert "29" in txt
        assert "121" in txt

    def test_struct_sync_updates_parsed(self):
        v = _view()
        v._on_struct_toggle()
        v._struct_lat_d.setText("29")
        v._struct_lat_m.setText("6")
        v._struct_lat_s.setText("53.7")
        v._struct_lat_dir.setCurrentText("N")
        v._struct_lon_d.setText("121")
        v._struct_lon_m.setText("45")
        v._struct_lon_s.setText("51.2")
        v._struct_lon_dir.setCurrentText("E")
        QApplication.processEvents()
        assert v._parsed is not None
        assert abs(v._parsed["lat"] - 29.114917) < 0.001
        assert abs(v._parsed["lon"] - 121.764222) < 0.001

    def test_apply_place_populates_struct_fields_when_open(self):
        # 搜索地名「填入」→ when the DMS panel is open, the lat/lon must appear in
        # the structured 度分秒 fields (regression: they used to stay empty).
        v = _view()
        v._on_struct_toggle()  # open DMS panel
        QApplication.processEvents()
        v._apply_place(29.05122, 121.74451)
        QApplication.processEvents()
        assert v._struct_lat_d.text() == "29"
        assert v._struct_lon_d.text() == "121"
        assert v._struct_lat_dir.currentText() == "N"
        assert v._struct_lon_dir.currentText() == "E"

    def test_apply_place_shows_coord_below_search_box(self):
        # 填入 echoes the chosen lat/lon below the 地名搜索 box (visible without
        # scrolling up to the badge or opening the DMS panel).
        v = _view()
        assert not v._place_applied_lbl.isVisible()
        v._apply_place(29.05122, 121.74451)
        QApplication.processEvents()
        assert v._place_applied_lbl.isVisible()
        assert "29.05122" in v._place_applied_lbl.text()
        assert "121.74451" in v._place_applied_lbl.text()

    def test_apply_place_no_echo_loop_corrupts_input(self):
        # Populating the struct fields must not echo back and corrupt the value.
        v = _view()
        v._on_struct_toggle()
        v._apply_place(29.05122, 121.74451)
        QApplication.processEvents()
        assert v._parsed is not None
        assert abs(v._parsed["lat"] - 29.05122) < 0.001
        assert abs(v._parsed["lon"] - 121.74451) < 0.001


# ══════════════════════════════════════════════════════════════════════════════
# Batch conversion
# ══════════════════════════════════════════════════════════════════════════════

class TestBatch:
    _VALID_INPUT = "29.11492, 121.76421\n24.48921N 118.18432E"
    _MIXED_INPUT = "29.11492, 121.76421\nnot-a-coord"

    def test_toggle_shows_body(self):
        v = _view()
        assert not v._batch_body.isVisible()
        v._on_batch_toggle()
        QApplication.processEvents()
        assert v._batch_body.isVisible()

    def test_parse_valid_rows(self):
        v = _view()
        v._batch_textarea.setPlainText(self._VALID_INPUT)
        v._on_batch_parse()
        QApplication.processEvents()
        assert len(v._batch_rows) == 2
        for row in v._batch_rows:
            assert row["error"] is None
            assert abs(row["lat"]) <= 90
            assert abs(row["lon"]) <= 180

    def test_parse_invalid_row(self):
        v = _view()
        v._batch_textarea.setPlainText(self._MIXED_INPUT)
        v._on_batch_parse()
        ok = [r for r in v._batch_rows if r["error"] is None]
        err = [r for r in v._batch_rows if r["error"] is not None]
        assert len(ok) == 1
        assert len(err) == 1

    def test_table_visible_after_parse(self):
        v = _view()
        v._on_batch_toggle()
        v._batch_textarea.setPlainText(self._VALID_INPUT)
        v._on_batch_parse()
        QApplication.processEvents()
        assert v._batch_table.isVisible()

    def test_table_row_count(self):
        v = _view()
        v._batch_textarea.setPlainText(self._VALID_INPUT)
        v._on_batch_parse()
        assert v._batch_table.rowCount() == 2

    def test_batch_header_layout(self):
        # Bug fix: 东经 (lon) header must sit above its data column, not float
        # far right. Caused by setStretchLastSection(True) over-widening the last
        # column. Fix: all columns content-width + user-draggable (Interactive),
        # no stretch. See plan rippling-foraging-meadow.
        from PyQt6.QtWidgets import QHeaderView

        v = _view()
        v._batch_textarea.setPlainText(self._VALID_INPUT)
        v._on_batch_parse()
        QApplication.processEvents()
        hdr = v._batch_table.horizontalHeader()
        # Header label still present (regression guard — never blank it out).
        assert v._batch_table.horizontalHeaderItem(3).text() == "东经"
        # No column absorbs all extra width (东经 would clip otherwise).
        assert hdr.stretchLastSection() is False
        # Every column is drag-resizable (Interactive), per user request.
        for c in range(4):
            assert hdr.sectionResizeMode(c) == QHeaderView.ResizeMode.Interactive

    def test_batch_col_width_survives_refresh(self):
        # User-dragged column widths must persist across format/CS toggles
        # (refresh re-runs but must not reset widths after the first sizing).
        v = _view()
        v._batch_textarea.setPlainText(self._VALID_INPUT)
        v._on_batch_parse()
        QApplication.processEvents()
        v._batch_table.setColumnWidth(1, 400)  # simulate a user drag
        v._on_batch_fmt("dms")                  # triggers _refresh_batch_table
        QApplication.processEvents()
        assert v._batch_table.columnWidth(1) == 400

    def test_batch_fmt_dd(self):
        v = _view()
        v._batch_textarea.setPlainText("29.11492N 121.76421E")
        v._on_batch_parse()
        v._on_batch_fmt("dd")
        QApplication.processEvents()
        lat_text = v._batch_table.item(0, 2).text()
        assert "29" in lat_text
        assert "°" not in lat_text

    def test_batch_fmt_dms(self):
        v = _view()
        v._batch_textarea.setPlainText("29.11492N 121.76421E")
        v._on_batch_parse()
        v._on_batch_fmt("dms")
        QApplication.processEvents()
        lat_text = v._batch_table.item(0, 2).text()
        # Web batchFormatCoord (app.js:13596) uses ′ U+2032 and ″ U+2033, not ASCII
        assert "°" in lat_text and "′" in lat_text and "″" in lat_text

    def test_batch_fmt_ddm(self):
        v = _view()
        v._batch_textarea.setPlainText("29.11492N 121.76421E")
        v._on_batch_parse()
        v._on_batch_fmt("ddm")
        QApplication.processEvents()
        lat_text = v._batch_table.item(0, 2).text()
        # Web batchFormatCoord DDM: d°m′ (U+2032 prime), no seconds
        assert "°" in lat_text and "′" in lat_text
        assert "″" not in lat_text  # DDM has no seconds

    def test_batch_cs_gcj02_shifts_china_coords(self):
        """GCJ-02 conversion must shift coords inside mainland China."""
        v = _view()
        v._batch_textarea.setPlainText("29.11492N 121.76421E")
        v._on_batch_parse()
        v._on_batch_cs("wgs84")
        QApplication.processEvents()
        lat_wgs = float(v._batch_table.item(0, 2).text())

        v._on_batch_cs("gcj02")
        QApplication.processEvents()
        lat_gcj = float(v._batch_table.item(0, 2).text())

        # Must differ by > 0.001° inside mainland China
        assert abs(lat_wgs - lat_gcj) > 0.001

    def test_batch_cs_bd09_shifts_china_coords(self):
        v = _view()
        v._batch_textarea.setPlainText("29.11492N 121.76421E")
        v._on_batch_parse()
        v._on_batch_cs("wgs84")
        QApplication.processEvents()
        lat_wgs = float(v._batch_table.item(0, 2).text())

        v._on_batch_cs("bd09")
        QApplication.processEvents()
        lat_bd = float(v._batch_table.item(0, 2).text())
        assert abs(lat_wgs - lat_bd) > 0.001

    def test_csv_export_has_header(self):
        """CSV header must contain 7-column web-spec fields (app.js:13615).

        Header: ﻿序号,原始,格式,纬度,经度,转换结果,错误 (with UTF-8 BOM).
        """
        v = _view()
        v._batch_textarea.setPlainText(self._VALID_INPUT)
        v._on_batch_parse()
        csv_text = v._batch_to_csv()
        # Strip BOM for assertion
        first_line = csv_text.lstrip("﻿").splitlines()[0]
        assert "序号" in first_line
        assert "原始" in first_line
        assert "转换结果" in first_line

    def test_csv_export_row_count(self):
        v = _view()
        v._batch_textarea.setPlainText(self._VALID_INPUT)
        v._on_batch_parse()
        csv_text = v._batch_to_csv()
        lines = [ln for ln in csv_text.splitlines() if ln.strip()]
        # header + 2 data rows
        assert len(lines) == 3

    def test_example_select_fills_textarea(self):
        v = _view()
        v._on_batch_toggle()
        # Simulate selecting "dd" example (index 2 in combobox: 0=placeholder, 1=mixed, 2=dd)
        # Find index by key
        for i in range(v._example_combo.count()):
            if v._example_combo.itemData(i) == "dd":
                v._example_combo.setCurrentIndex(i)
                QApplication.processEvents()
                break
        text = v._batch_textarea.toPlainText()
        assert len(text.strip()) > 0
        assert len(v._batch_rows) > 0

    def test_controls_visible_after_parse(self):
        """Controls should be explicitly shown after parsing; check parent-visible path."""
        v = _view()
        v._on_batch_toggle()           # open batch body first
        v._batch_textarea.setPlainText(self._VALID_INPUT)
        v._on_batch_parse()
        QApplication.processEvents()
        # isVisible() propagates from parent; batch_body is open so all should show
        assert v._batch_body.isVisible()
        assert v._batch_controls.isVisible()
        assert v._batch_actions.isVisible()


# ══════════════════════════════════════════════════════════════════════════════
# Esc closes map modal  (mirrors coordMapEscHandler in app.js line 13556)
# ══════════════════════════════════════════════════════════════════════════════

class TestEscCloseMap:
    def test_open_map_sets_flag(self):
        """_on_open_map() must set _map_open = True."""
        v = _view()
        assert not v._map_open
        v._on_open_map()
        QApplication.processEvents()
        assert v._map_open

    def test_esc_closes_map_overlay(self):
        """Pressing Escape on the overlay widget must close it (mirrors Esc handler in JS)."""
        from PyQt6.QtCore import QEvent
        from PyQt6.QtGui import QKeyEvent
        from PyQt6.QtCore import Qt

        v = _view()
        v._on_open_map()
        QApplication.processEvents()
        assert v._map_overlay is not None
        assert v._map_overlay.isVisible()

        # Simulate Esc key press on the overlay via its event filter
        overlay = v._map_overlay
        esc_event = QKeyEvent(
            QEvent.Type.KeyPress,
            Qt.Key.Key_Escape,
            Qt.KeyboardModifier.NoModifier,
        )
        QApplication.sendEvent(overlay, esc_event)
        QApplication.processEvents()

        # After Esc, map should be closed
        assert not v._map_open
