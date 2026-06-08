"""test_label_designer.py — free-form Label Designer dialog.

Exercises the editing operations the canvas + property panel drive: add/remove/
reorder rows & fields, per-field size/style/align, field nudge, QR position/
content/ecc/size + free drag, undo/redo, canvas hit-testing, and library save.
"""
from __future__ import annotations

import os
import sys

import pytest

from PyQt6.QtCore import QPoint


@pytest.fixture(scope="module")
def qt_app():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt6.QtWidgets import QApplication
    existing = QApplication.instance()
    yield existing if existing is not None else QApplication(sys.argv[:1])


TMPL = {
    "name": "标准", "lineHeight": 1.3,
    "rows": [
        {"fields": [{"key": "headerId", "style": "bold", "size": 10, "offsetX": 0, "offsetY": 0}],
         "size": 10, "style": "bold", "align": "left", "wrap": True},
        {"fields": [{"key": "storage", "style": "", "size": 9, "offsetX": 0, "offsetY": 0}],
         "size": 9, "style": "", "align": "left", "wrap": True},
    ],
    "qr": {"content": "uniqueId", "position": "right", "sizePct": 0.4, "ecc": "Q"},
}
DIMS = {"w": 50, "h": 30}
DATA = {"headerId": "FJ-001", "storage": "T95E", "uniqueId": "FJ-001-T95E", "speciesName": "背鳞虫"}


def _dlg(qt_app, lib=None):
    from app.widgets.label_designer_dialog import LabelDesignerDialog
    d = LabelDesignerDialog(TMPL, DIMS, DATA, library=lib, title="t")
    d.resize(940, 640)
    return d


class TestFreeFormElements:
    def test_add_element_appends_and_selects(self, qt_app):
        d = _dlg(qt_app)
        assert d._tmpl.get("elements", []) == []
        d._add_element("text")
        assert len(d._tmpl["elements"]) == 1
        assert d._tmpl["elements"][0]["type"] == "text"
        assert d._sel == ("element", -1, 0)

    def test_add_each_element_type(self, qt_app):
        d = _dlg(qt_app)
        for t in ("text", "field", "line", "rect", "ellipse", "image", "barcode"):
            d._add_element(t)
        assert [e["type"] for e in d._tmpl["elements"]] == \
            ["text", "field", "line", "rect", "ellipse", "image", "barcode"]

    def test_element_move_updates_xy(self, qt_app):
        d = _dlg(qt_app)
        d._add_element("rect")
        d._apply_edit({"op": "element_move", "index": 0, "x": 7.5, "y": 3.0})
        assert d._tmpl["elements"][0]["x"] == 7.5
        assert d._tmpl["elements"][0]["y"] == 3.0

    def test_element_resize_clamps_min_size(self, qt_app):
        d = _dlg(qt_app)
        d._add_element("rect")
        d._apply_edit({"op": "element_resize", "index": 0,
                       "x": 0, "y": 0, "w": 0.1, "h": 0.1})
        el = d._tmpl["elements"][0]
        from app.widgets.label_designer_dialog import MIN_EL_MM
        assert el["w"] >= MIN_EL_MM and el["h"] >= MIN_EL_MM

    def test_element_delete_and_duplicate(self, qt_app):
        d = _dlg(qt_app)
        d._add_element("rect")
        d._apply_edit({"op": "element_dup", "index": 0})
        assert len(d._tmpl["elements"]) == 2
        d._apply_edit({"op": "element_del", "index": 0})
        assert len(d._tmpl["elements"]) == 1

    def test_element_z_reorder(self, qt_app):
        d = _dlg(qt_app)
        d._add_element("rect")     # index 0
        d._add_element("ellipse")  # index 1
        d._apply_edit({"op": "element_z", "index": 0, "value": 1})  # raise in z
        assert [e["type"] for e in d._tmpl["elements"]] == ["ellipse", "rect"]

    def test_add_element_is_undoable(self, qt_app):
        d = _dlg(qt_app)
        d._add_element("rect")
        assert len(d._tmpl["elements"]) == 1
        d._do_undo()
        assert d._tmpl.get("elements", []) == []


class TestAlignDistribute:
    """Phase 1 — pro-software alignment. Single element aligns to the label box;
    multiple elements align to their shared bounding box; distribute evens gaps."""

    def _rect(self, d, x, y, w=10, h=6):
        d._add_element("rect")
        el = d._tmpl["elements"][-1]
        el.update({"x": x, "y": y, "w": w, "h": h})
        return len(d._tmpl["elements"]) - 1

    def test_align_single_element_right_edge_to_label(self, qt_app):
        d = _dlg(qt_app)  # DIMS w=50
        i = self._rect(d, x=3, y=4, w=10, h=6)
        d._align_elements("right", indices=[i])
        assert d._tmpl["elements"][i]["x"] == 50 - 10

    def test_align_single_element_hcenter_to_label(self, qt_app):
        d = _dlg(qt_app)
        i = self._rect(d, x=3, y=4, w=10, h=6)
        d._align_elements("hcenter", indices=[i])
        assert d._tmpl["elements"][i]["x"] == (50 - 10) / 2

    def test_align_single_element_top_to_label(self, qt_app):
        d = _dlg(qt_app)
        i = self._rect(d, x=3, y=4, w=10, h=6)
        d._align_elements("top", indices=[i])
        assert d._tmpl["elements"][i]["y"] == 0

    def test_align_multiple_uses_selection_bbox_left(self, qt_app):
        d = _dlg(qt_app)
        a = self._rect(d, x=5, y=2)
        b = self._rect(d, x=20, y=10)
        d._align_elements("left", indices=[a, b])
        # both snap to the leftmost (x=5), not the label edge
        assert d._tmpl["elements"][a]["x"] == 5
        assert d._tmpl["elements"][b]["x"] == 5

    def test_align_is_undoable(self, qt_app):
        d = _dlg(qt_app)
        i = self._rect(d, x=3, y=4, w=10, h=6)
        d._align_elements("right", indices=[i])
        d._do_undo()
        assert d._tmpl["elements"][i]["x"] == 3

    def test_distribute_horizontal_evens_gaps(self, qt_app):
        d = _dlg(qt_app)
        a = self._rect(d, x=0, y=0, w=10, h=6)    # right edge 10
        b = self._rect(d, x=12, y=0, w=10, h=6)   # middle one
        c = self._rect(d, x=40, y=0, w=10, h=6)   # right edge 50
        d._distribute_elements("h", indices=[a, b, c])
        xs = sorted(d._tmpl["elements"][k]["x"] for k in (a, b, c))
        # ends pinned; middle centered so left-gap == right-gap
        assert xs[0] == 0 and xs[2] == 40
        assert xs[1] == 20  # 0..10, 20..30, 40..50 → equal 10mm gaps


class TestMultiSelect:
    """Phase 1 — element multi-selection drives group align/distribute/delete."""

    def _rect(self, d, x, y, w=10, h=6):
        d._add_element("rect")
        d._tmpl["elements"][-1].update({"x": x, "y": y, "w": w, "h": h})
        return len(d._tmpl["elements"]) - 1

    def test_toggle_multi_adds_and_removes(self, qt_app):
        d = _dlg(qt_app)
        a = self._rect(d, 1, 1)
        b = self._rect(d, 20, 1)
        d._toggle_multi(a)
        d._toggle_multi(b)
        assert d._multi == {a, b}
        d._toggle_multi(a)
        assert d._multi == {b}

    def test_marquee_selects_intersecting_elements(self, qt_app):
        d = _dlg(qt_app)
        a = self._rect(d, 1, 1, 10, 6)      # inside
        b = self._rect(d, 5, 2, 10, 6)      # inside
        c = self._rect(d, 40, 20, 8, 5)     # outside the marquee
        d._marquee_select(0, 0, 20, 12)
        assert d._multi == {a, b}
        assert c not in d._multi

    def test_group_align_uses_multi_selection(self, qt_app):
        d = _dlg(qt_app)
        a = self._rect(d, 5, 2)
        b = self._rect(d, 25, 9)
        d._toggle_multi(a)
        d._toggle_multi(b)
        d._align_elements("left")  # no indices arg → use _multi
        assert d._tmpl["elements"][a]["x"] == 5
        assert d._tmpl["elements"][b]["x"] == 5

    def test_delete_multi_removes_all_selected(self, qt_app):
        d = _dlg(qt_app)
        a = self._rect(d, 1, 1)
        b = self._rect(d, 20, 1)
        c = self._rect(d, 30, 1)
        d._toggle_multi(a)
        d._toggle_multi(c)
        d._delete_selection()
        assert len(d._tmpl["elements"]) == 1
        assert d._tmpl["elements"][0]["x"] == 20  # only b survives

    def test_selecting_anchor_clears_multi(self, qt_app):
        d = _dlg(qt_app)
        a = self._rect(d, 1, 1)
        b = self._rect(d, 20, 1)
        d._toggle_multi(a)
        d._toggle_multi(b)
        d._select("element", -1, a)  # plain click on one element
        assert d._multi == set()

    def test_copy_paste_clones_with_offset(self, qt_app):
        d = _dlg(qt_app)
        a = self._rect(d, 5, 5, 10, 6)
        d._select("element", -1, a)
        d._copy_selection()
        d._paste_clipboard()
        assert len(d._tmpl["elements"]) == 2
        pasted = d._tmpl["elements"][1]
        assert pasted["x"] == 7 and pasted["y"] == 7  # +2mm offset
        assert pasted["w"] == 10 and pasted["h"] == 6

    def test_paste_selects_pasted_as_group(self, qt_app):
        d = _dlg(qt_app)
        a = self._rect(d, 1, 1)
        b = self._rect(d, 20, 1)
        d._toggle_multi(a)
        d._toggle_multi(b)
        d._copy_selection()
        d._paste_clipboard()
        assert len(d._tmpl["elements"]) == 4
        assert d._multi == {2, 3}

    def test_paste_is_undoable(self, qt_app):
        d = _dlg(qt_app)
        a = self._rect(d, 5, 5)
        d._select("element", -1, a)
        d._copy_selection()
        d._paste_clipboard()
        d._do_undo()
        assert len(d._tmpl["elements"]) == 1


class TestRulersAndGuides:
    """Phase 1b — draggable reference guides; elements snap to them."""

    def _canvas(self, qt_app, ppm=4.0):
        from app.widgets.label_designer_dialog import _DesignCanvas
        from app.utils.label_core import normalize_template
        c = _DesignCanvas()
        c.resize(400, 300)
        c.set_content(normalize_template({"rows": []}), {"w": 50, "h": 30}, {})
        c._ppm = ppm   # deterministic snap threshold (6px / ppm)
        return c

    def test_add_user_guide_stored(self, qt_app):
        c = self._canvas(qt_app)
        c.add_user_guide("v", 25.0)
        c.add_user_guide("h", 10.0)
        assert ("v", 25.0) in c._user_guides
        assert ("h", 10.0) in c._user_guides

    def test_element_snaps_to_user_guide(self, qt_app):
        c = self._canvas(qt_app, ppm=4.0)  # thr = 6/4 = 1.5mm
        c.add_user_guide("v", 25.0)
        x, y, guides = c.snap(24.4, 5.0, 10.0, 6.0)
        assert x == 25.0
        assert ("v", 25.0) in guides

    def test_no_snap_when_far_from_guide(self, qt_app):
        c = self._canvas(qt_app, ppm=4.0)
        c.add_user_guide("v", 25.0)
        x, _y, _g = c.snap(10.0, 5.0, 10.0, 6.0)
        assert x == 10.0

    def test_clear_user_guides(self, qt_app):
        c = self._canvas(qt_app)
        c.add_user_guide("v", 25.0)
        c.clear_user_guides()
        assert c._user_guides == []

    def test_drag_from_top_ruler_creates_vertical_guide(self, qt_app):
        from PyQt6.QtCore import Qt, QPointF, QPoint
        from PyQt6.QtGui import QMouseEvent
        c = self._canvas(qt_app)
        ox, oy = c._origin.x(), c._origin.y()
        # press in the top margin (above the label pixmap), then release inside
        start = QPoint(ox + 40, max(0, oy - 8))
        for typ, pos in ((QMouseEvent.Type.MouseButtonPress, start),
                         (QMouseEvent.Type.MouseMove, QPoint(ox + 40, oy + 30)),
                         (QMouseEvent.Type.MouseButtonRelease, QPoint(ox + 40, oy + 30))):
            ev = QMouseEvent(typ, QPointF(pos), QPointF(pos),
                             Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
                             Qt.KeyboardModifier.NoModifier)
            {QMouseEvent.Type.MouseButtonPress: c.mousePressEvent,
             QMouseEvent.Type.MouseMove: c.mouseMoveEvent,
             QMouseEvent.Type.MouseButtonRelease: c.mouseReleaseEvent}[typ](ev)
        assert any(axis == "v" for axis, _ in c._user_guides)


class TestCanvasGroupWiring:
    """The canvas mouse/keyboard wiring drives the dialog's group selection."""

    def _ctrl_click(self, canvas, index):
        from PyQt6.QtCore import Qt, QPointF
        from PyQt6.QtGui import QMouseEvent
        box = next(b for b in canvas._boxes
                   if b.get("kind") == "element" and b.get("index") == index)
        ox, oy = canvas._origin.x(), canvas._origin.y()
        cx = ox + box["x"] + box["w"] / 2
        cy = oy + box["y"] + box["h"] / 2
        ev = QMouseEvent(
            QMouseEvent.Type.MouseButtonPress, QPointF(cx, cy), QPointF(cx, cy),
            Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.ControlModifier)
        canvas.mousePressEvent(ev)

    def test_ctrl_click_toggles_group(self, qt_app):
        d = _dlg(qt_app)
        d.resize(940, 640)
        d._add_element("rect")
        d._tmpl["elements"][0].update({"x": 5, "y": 5, "w": 14, "h": 8})
        d._add_element("rect")
        d._tmpl["elements"][1].update({"x": 5, "y": 18, "w": 14, "h": 8})
        d._refresh()
        self._ctrl_click(d._canvas, 0)
        self._ctrl_click(d._canvas, 1)
        assert d._multi == {0, 1}

    def test_delete_key_removes_selection(self, qt_app):
        d = _dlg(qt_app)
        d._add_element("rect")
        d._select("element", -1, 0)
        d._canvas.delete_pressed.emit()
        assert d._tmpl.get("elements", []) == []


class TestRowFieldEditing:
    def test_add_row_with_field(self, qt_app):
        d = _dlg(qt_app)
        n = len(d._tmpl["rows"])
        d._add_row_with_field("latin")
        assert len(d._tmpl["rows"]) == n + 1
        assert d._tmpl["rows"][-1]["fields"][0]["key"] == "latin"

    def test_field_size_bold_align(self, qt_app):
        d = _dlg(qt_app)
        d._select("field", 1, 0)
        d._apply_edit({"op": "field_size", "row": 1, "field": 0, "value": 15})
        d._apply_edit({"op": "field_bold", "row": 1, "field": 0, "value": True})
        d._apply_edit({"op": "row_align", "row": 1, "value": "center"})
        f = d._tmpl["rows"][1]["fields"][0]
        assert f["size"] == 15 and "bold" in f["style"]
        assert d._tmpl["rows"][1]["align"] == "center"

    def test_field_nudge_and_reset(self, qt_app):
        d = _dlg(qt_app)
        d._apply_edit({"op": "field_nudge", "row": 0, "field": 0, "dx": 2.0, "dy": -1.5})
        f = d._tmpl["rows"][0]["fields"][0]
        assert f["offsetX"] == 2.0 and f["offsetY"] == -1.5
        d._apply_edit({"op": "field_reset", "row": 0, "field": 0})
        assert f["offsetX"] == 0 and f["offsetY"] == 0

    def test_field_add_and_delete(self, qt_app):
        d = _dlg(qt_app)
        d._apply_edit({"op": "field_add", "row": 0})
        assert len(d._tmpl["rows"][0]["fields"]) == 2
        d._apply_edit({"op": "field_del", "row": 0, "field": 1})
        assert len(d._tmpl["rows"][0]["fields"]) == 1

    def test_delete_last_field_removes_row(self, qt_app):
        d = _dlg(qt_app)
        n = len(d._tmpl["rows"])
        d._apply_edit({"op": "field_del", "row": 0, "field": 0})  # row 0 has 1 field
        assert len(d._tmpl["rows"]) == n - 1

    def test_row_dup_del_move(self, qt_app):
        d = _dlg(qt_app)
        n = len(d._tmpl["rows"])
        d._apply_edit({"op": "row_dup", "row": 0})
        assert len(d._tmpl["rows"]) == n + 1
        d._apply_edit({"op": "row_move", "row": 0, "value": 1})  # swap 0,1
        d._apply_edit({"op": "row_del", "row": 0})
        assert len(d._tmpl["rows"]) == n


class TestQrEditing:
    def test_qr_position_content_ecc_size(self, qt_app):
        d = _dlg(qt_app)
        d._select("qr", -1, -1)
        d._apply_edit({"op": "qr_position", "value": "left"})
        d._apply_edit({"op": "qr_content", "value": "headerId"})
        d._apply_edit({"op": "qr_ecc", "value": "H"})
        d._apply_edit({"op": "qr_size", "value": 0.6})
        qr = d._tmpl["qr"]
        assert qr["position"] == "left" and qr["content"] == "headerId"
        assert qr["ecc"] == "H" and abs(qr["sizePct"] - 0.6) < 1e-6

    def test_qr_free_drag_sets_xy(self, qt_app):
        d = _dlg(qt_app)
        d._select("qr", -1, -1)
        d._on_drag_start()
        d._on_dragged(5.0, 3.0)
        qr = d._tmpl["qr"]
        assert qr["position"] == "free"
        assert qr["x"] >= 0 and qr["y"] >= 0


class TestUndoRedo:
    def test_undo_restores_previous(self, qt_app):
        d = _dlg(qt_app)
        n = len(d._tmpl["rows"])
        d._add_row_with_field("latin")
        assert len(d._tmpl["rows"]) == n + 1
        d._do_undo()
        assert len(d._tmpl["rows"]) == n
        d._do_redo()
        assert len(d._tmpl["rows"]) == n + 1


class TestCanvasHitTesting:
    def test_canvas_emits_hit_boxes_and_hits_field(self, qt_app):
        d = _dlg(qt_app)
        c = d._canvas
        c.resize(600, 400)
        c._render()
        field_boxes = [b for b in c._boxes if b["kind"] == "field"]
        assert field_boxes  # renderer emitted field hit-boxes
        b = field_boxes[0]
        center = QPoint(int(c._origin.x() + b["x"] + b["w"] / 2),
                        int(c._origin.y() + b["y"] + b["h"] / 2))
        hit = c._hit(center)
        assert hit is not None and hit["kind"] == "field"


class TestCanvasElementInteraction:
    def test_hit_handle_returns_se_corner(self, qt_app):
        d = _dlg(qt_app)
        d._add_element("rect")
        d._apply_edit({"op": "element_resize", "index": 0,
                       "x": 5, "y": 5, "w": 20, "h": 10})
        d._select("element", -1, 0)
        c = d._canvas
        c.resize(600, 400)
        c._render()
        box = c._selected_box()
        assert box is not None and box["etype"] == "rect"
        se = QPoint(int(c._origin.x() + box["x"] + box["w"]),
                    int(c._origin.y() + box["y"] + box["h"]))
        assert c._hit_handle(se) == "se"
        # a point in the middle is NOT a handle
        mid = QPoint(int(c._origin.x() + box["x"] + box["w"] / 2),
                     int(c._origin.y() + box["y"] + box["h"] / 2))
        assert c._hit_handle(mid) is None

    def test_snap_pulls_to_neighbour_edge(self, qt_app):
        d = _dlg(qt_app)
        d._add_element("rect")  # neighbour at x=...
        d._apply_edit({"op": "element_resize", "index": 0,
                       "x": 10, "y": 10, "w": 8, "h": 8})
        d._add_element("rect")  # moving element, index 1
        c = d._canvas
        c._ppm = 4.0  # thr_mm = 6/4 = 1.5mm
        # moving box left edge at x=10.4 should snap to neighbour left edge 10.0
        # and emit a vertical alignment guide
        x, y, guides = c.snap(10.4, 5, 8, 8, skip_index=1)
        assert abs(x - 10.0) < 1e-6
        assert any(axis == "v" for axis, _ in guides)
        # far from any element edge: falls back to grid snap (integer mm), no guide
        x2, _, g2 = c.snap(30.6, 5, 8, 8, skip_index=1)
        assert abs(x2 - 31.0) < 1e-6
        assert not any(axis == "v" for axis, _ in g2)

    def test_canvas_mouse_drag_moves_element(self, qt_app):
        """Full press→move path: drag_started must fire so the move baseline is
        captured and the element actually moves (regression guard)."""
        from PyQt6.QtGui import QMouseEvent
        from PyQt6.QtCore import QPointF, QEvent
        d = _dlg(qt_app)
        d._add_element("rect")
        d._apply_edit({"op": "element_resize", "index": 0,
                       "x": 10, "y": 10, "w": 12, "h": 8})
        d._select("element", -1, 0)
        c = d._canvas
        c.resize(600, 400)
        c._render()
        box = c._selected_box()
        # press at element center, then move +40px right / +24px down
        cx = c._origin.x() + box["x"] + box["w"] / 2
        cy = c._origin.y() + box["y"] + box["h"] / 2

        from PyQt6.QtCore import Qt
        def _ev(kind, x, y):
            return QMouseEvent(kind, QPointF(x, y), Qt.MouseButton.LeftButton,
                               Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
        c.mousePressEvent(_ev(QEvent.Type.MouseButtonPress, cx, cy))
        c.mouseMoveEvent(_ev(QEvent.Type.MouseMove, cx + 40, cy + 24))
        c.mouseReleaseEvent(_ev(QEvent.Type.MouseButtonRelease, cx + 40, cy + 24))
        el = d._tmpl["elements"][0]
        assert el["x"] > 10 and el["y"] > 10  # moved from its original spot

    def test_canvas_resize_signal_updates_geometry(self, qt_app):
        d = _dlg(qt_app)
        d._add_element("rect")
        d._apply_edit({"op": "element_resize", "index": 0,
                       "x": 5, "y": 5, "w": 20, "h": 10})
        # simulate the canvas emitting a resize
        d._on_element_resized(0, 5, 5, 25, 12)
        el = d._tmpl["elements"][0]
        assert el["w"] == 25 and el["h"] == 12

    def test_apply_preset_replaces_template(self, qt_app):
        from app.services.label_presets import STARTER_PRESETS
        d = _dlg(qt_app)
        d._apply_preset(STARTER_PRESETS["logo"])
        assert d._tmpl["elements"]  # logo preset has free-form elements
        assert any(e["type"] == "barcode" for e in d._tmpl["elements"])
        d._do_undo()  # preset apply is a single undo step
        assert d._tmpl["rows"]  # back to the original row-based TMPL


class TestResultAndLibrary:
    def test_edited_template_is_deepcopy(self, qt_app):
        d = _dlg(qt_app)
        a = d.edited_template()
        a["rows"].clear()
        assert d._tmpl["rows"]  # original untouched

    def test_save_as_new_adds_library_record(self, qt_app, tmp_path):
        from PyQt6.QtCore import QSettings
        from app.services.label_service import LabelTemplateLibrary, _MIGRATION_QSETTINGS_KEY

        ini = str(tmp_path / "designer_lib.ini")

        class _FakeLib(LabelTemplateLibrary):
            def __init__(self, bucket):
                self._bucket = bucket
                self._is_tissue = bucket == "tissue"
                self._qs = QSettings(ini, QSettings.Format.IniFormat)
                self._qs.setValue(_MIGRATION_QSETTINGS_KEY[bucket], "1")

        lib = _FakeLib("sample")
        d = _dlg(qt_app, lib=lib)
        n0 = len(lib.records())
        rec = lib.upsert({"name": "我的设计", "template": d.edited_template()})
        assert len(lib.records()) == n0 + 1
        assert rec["template"]["rows"]
