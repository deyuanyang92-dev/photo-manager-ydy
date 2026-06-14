"""tests/test_label_imposition_dialog.py — 排版设计 dialog (offscreen).

Covers control↔dict sync, normalization (unified margin / equal gaps),
presets, canvas guide hit-testing + drag px→mm snapping, click-to-set 起始格,
live imposition_changed signal, and restore-defaults.
"""

from __future__ import annotations

import sys

import pytest

from PyQt6.QtWidgets import QApplication

from app.services.label_service import BUILTIN_TEMPLATES


@pytest.fixture(scope="module")
def qt_app():
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv[:1])
    yield app


def _job(n: int = 4):
    return {
        "items": [{"idx": i, "data": {"uniqueId": f"U{i}"}} for i in range(n)],
        "template": BUILTIN_TEMPLATES["standard"],
        "dims": {"w": 50, "h": 30},
        "paperType": "a4",
        "paper": {"w": 210, "h": 297},
    }


def _dlg(qt_app, imposition=None, n=4):
    from app.widgets.label_imposition_dialog import LabelImpositionDialog
    d = LabelImpositionDialog(_job(n), imposition or {})
    d.resize(1000, 760)
    return d


class TestControlsSync:
    def test_initial_controls_reflect_dict_and_fallbacks(self, qt_app):
        d = _dlg(qt_app, {"marginMm": 5.0, "forceCols": 2, "cutMarks": True})
        assert d._chk_unify.isChecked()
        assert all(s.value() == 5.0 for s in d._margin_spins.values())
        assert d._gap_x.value() == 2.0 and d._gap_y.value() == 2.0   # default
        assert d._cols.value() == 2 and d._rows.value() == 0          # 0=自动
        assert d._chk_cuts.isChecked()
        assert not d._chk_shrink.isChecked()
        d.deleteLater()

    def test_per_side_dict_unchecks_unify(self, qt_app):
        d = _dlg(qt_app, {"marginLeftMm": 12.0, "marginMm": 5.0})
        assert not d._chk_unify.isChecked()
        assert d._margin_spins["marginLeftMm"].value() == 12.0
        assert d._margin_spins["marginRightMm"].value() == 5.0   # fallback
        d.deleteLater()

    def test_unified_margin_writes_single_key(self, qt_app):
        d = _dlg(qt_app)
        d._margin_spins["marginTopMm"].setValue(6.0)
        imp = d.imposition()
        assert imp.get("marginMm") == 6.0
        assert not any(k in imp for k in
                       ("marginTopMm", "marginBottomMm",
                        "marginLeftMm", "marginRightMm"))
        # unify mode mirrors all four spins
        assert d._margin_spins["marginLeftMm"].value() == 6.0
        d.deleteLater()

    def test_per_side_edit_writes_four_keys_drops_uniform(self, qt_app):
        d = _dlg(qt_app, {"marginMm": 5.0})
        d._chk_unify.setChecked(False)
        d._margin_spins["marginLeftMm"].setValue(15.0)
        imp = d.imposition()
        assert "marginMm" not in imp
        assert imp["marginLeftMm"] == 15.0 and imp["marginTopMm"] == 5.0
        d.deleteLater()

    def test_equal_gaps_normalize_to_gap_mm(self, qt_app):
        d = _dlg(qt_app)
        d._gap_x.setValue(4.0)
        assert d.imposition() == {"gapXMm": 4.0, "gapYMm": 2.0}
        d._gap_y.setValue(4.0)
        assert d.imposition() == {"gapMm": 4.0}
        d.deleteLater()

    def test_orientation_radio(self, qt_app):
        d = _dlg(qt_app)
        d._rb_landscape.setChecked(True)
        assert d.imposition().get("orientation") == "landscape"
        d._rb_portrait.setChecked(True)
        assert "orientation" not in d.imposition()
        d.deleteLater()

    def test_shrink_checkbox(self, qt_app):
        d = _dlg(qt_app)
        d._cols.setValue(5)
        d._chk_shrink.setChecked(True)
        imp = d.imposition()
        assert imp == {"forceCols": 5, "shrinkToFit": True}
        d.deleteLater()

    def test_start_slot_max_tracks_per_page(self, qt_app):
        d = _dlg(qt_app)                       # 50×30 A4 → 24/page
        assert d._start.maximum() == 23
        d._cols.setValue(1)
        d._rows.setValue(2)                    # 2/page
        assert d._start.maximum() == 1
        d.deleteLater()

    def test_every_change_emits_imposition_changed(self, qt_app):
        d = _dlg(qt_app)
        got = []
        d.imposition_changed.connect(got.append)
        d._gap_x.setValue(1.0)
        d._chk_cuts.setChecked(True)
        d._start.setValue(2)
        assert len(got) == 3
        assert got[-1] == {"gapXMm": 1.0, "gapYMm": 2.0,
                           "cutMarks": True, "startSlot": 2}
        d.deleteLater()

    def test_restore_defaults_clears_dict(self, qt_app):
        d = _dlg(qt_app, {"marginMm": 3.0, "forceCols": 4, "cutMarks": True,
                          "orientation": "landscape"})
        d._restore_defaults()
        assert d.imposition() == {}
        assert d._chk_unify.isChecked()
        assert d._margin_spins["marginTopMm"].value() == 8.0
        assert d._rb_portrait.isChecked()
        d.deleteLater()

    def test_presets(self, qt_app):
        d = _dlg(qt_app, {"marginLeftMm": 20.0})
        d._apply_preset(4.0, 0.0)              # 紧凑
        imp = d.imposition()
        assert imp.get("marginMm") == 4.0 and imp.get("gapMm") == 0.0
        assert "marginLeftMm" not in imp
        d.deleteLater()


class TestCanvas:
    def _canvas(self, qt_app, imposition=None, n=4):
        d = _dlg(qt_app, imposition, n)
        c = d._canvas
        c.resize(620, 760)
        return d, c

    def test_hit_test_margin_and_gap(self, qt_app):
        d, c = self._canvas(qt_app)
        g = c.geometry_info()
        ppm = g["px_per_mm"]
        px, py = g["page_x"], g["page_y"]
        mid_y = py + g["page_h_px"] / 2
        assert c._hit_test(px + 8 * ppm, mid_y) == "marginLeftMm"
        assert c._hit_test(px + g["page_w_px"] - 8 * ppm, mid_y) == "marginRightMm"
        mid_x = px + g["page_w_px"] / 2
        assert c._hit_test(mid_x, py + 8 * ppm) == "marginTopMm"
        assert c._hit_test(mid_x, py + g["page_h_px"] - 8 * ppm) == "marginBottomMm"
        # gapX guide = left edge of column 1 (margin 8 + label 50 + gap 2)
        row0_y = py + (8 + 15) * ppm
        assert c._hit_test(px + 60 * ppm, row0_y) == "gapXMm"
        # dead zone → None
        assert c._hit_test(px + 30 * ppm, mid_y + 200) is None
        d.deleteLater()

    def test_drag_margin_snaps_half_mm(self, qt_app):
        d, c = self._canvas(qt_app)
        g = c.geometry_info()
        ppm = g["px_per_mm"]
        px = g["page_x"]
        assert c._mm_for("marginLeftMm", px + 10.3 * ppm, 0) == 10.5
        assert c._mm_for("marginLeftMm", px + 10.2 * ppm, 0) == 10.0
        assert c._mm_for("marginLeftMm", px - 50, 0) == 0.0          # clamp
        right = px + g["page_w_px"]
        assert c._mm_for("marginRightMm", right - 6.0 * ppm, 0) == 6.0
        d.deleteLater()

    def test_drag_gap_mm_from_col1_edge(self, qt_app):
        d, c = self._canvas(qt_app)
        g = c.geometry_info()
        ppm = g["px_per_mm"]
        px = g["page_x"]
        # col-1 left edge dragged to 8 + 50 + 3.0 → gapX 3.0
        assert c._mm_for("gapXMm", px + 61 * ppm, 0) == 3.0
        assert c._mm_for("gapXMm", px + 40 * ppm, 0) == 0.0          # clamp ≥0
        d.deleteLater()

    def test_guide_drag_updates_dialog_dict(self, qt_app):
        d, c = self._canvas(qt_app)
        c.guide_dragged.emit("marginLeftMm", 11.0)
        imp = d.imposition()
        assert imp["marginLeftMm"] == 11.0
        assert not d._chk_unify.isChecked()    # per-side drag leaves unify mode
        d.deleteLater()

    def test_click_cell_sets_start_slot(self, qt_app):
        d, c = self._canvas(qt_app)
        g = c.geometry_info()
        ppm = g["px_per_mm"]
        # slot 4 = col 1, row 1 → centre at margin + label/2 + (label+gap)
        x = g["page_x"] + (8 + 52 + 25) * ppm
        y = g["page_y"] + (8 + 32 + 15) * ppm
        assert c._slot_at(x, y) == 4
        c.cell_clicked.emit(4)
        assert d.imposition().get("startSlot") == 4
        assert d._start.value() == 4
        c.cell_clicked.emit(0)
        assert "startSlot" not in d.imposition()
        d.deleteLater()
