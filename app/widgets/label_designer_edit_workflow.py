"""Template mutation and element alignment logic for label designer."""
from __future__ import annotations

import copy
import uuid
from typing import Optional

from app.utils.label_core import qr_metrics
from app.widgets import label_designer_support as _ld


class LabelDesignerEditMixin:
    # ── Edits from property panel ─────────────────────────────────────────────
    def _apply_edit(self, ch: dict) -> None:
        op = ch.get("op")
        self._save_template_snapshot_for_undo()
        rows = self._tmpl["rows"]
        r = ch.get("row", -1)
        fi = ch.get("field", -1)

        def _set_style(obj, flag, on):
            s = set((obj.get("style") or "").split())
            s.discard("")
            s.add(flag) if on else s.discard(flag)
            obj["style"] = " ".join(sorted(s))

        if op == "field_key" and 0 <= r < len(rows):
            rows[r]["fields"][fi]["key"] = ch["value"]
        elif op == "field_size" and 0 <= r < len(rows):
            rows[r]["fields"][fi]["size"] = ch["value"]
        elif op == "field_bold":
            _set_style(rows[r]["fields"][fi], "bold", ch["value"])
        elif op == "field_italic":
            _set_style(rows[r]["fields"][fi], "italic", ch["value"])
        elif op == "field_nudge":
            f = rows[r]["fields"][fi]
            f["offsetX"] = round(float(f.get("offsetX") or 0) + ch["dx"], 2)
            f["offsetY"] = round(float(f.get("offsetY") or 0) + ch["dy"], 2)
        elif op == "field_reset":
            rows[r]["fields"][fi]["offsetX"] = 0
            rows[r]["fields"][fi]["offsetY"] = 0
        elif op == "field_add" and 0 <= r < len(rows):
            rows[r]["fields"].append({"key": "speciesName", "style": "", "size": None, "offsetX": 0, "offsetY": 0})
        elif op == "field_del" and 0 <= r < len(rows):
            if len(rows[r]["fields"]) > 1:
                del rows[r]["fields"][fi]
                self._sel = ("field", r, 0)
            else:
                del rows[r]
                self._sel = ("none", -1, -1)
        elif op == "row_align":
            rows[r]["align"] = ch["value"]
        elif op == "row_wrap":
            rows[r]["wrap"] = ch["value"]
        elif op == "row_prefix":
            rows[r]["prefix"] = ch["value"]
        elif op == "row_sep":
            rows[r]["sep"] = ch["value"]
        elif op == "row_lineHeight":
            if ch.get("value") is None:
                rows[r].pop("lineHeight", None)   # None → inherit template/global
            else:
                rows[r]["lineHeight"] = round(float(ch["value"]), 2)
        elif op == "row_dup":
            rows.insert(r + 1, copy.deepcopy(rows[r]))
            self._sel = ("field", r + 1, 0)
        elif op == "row_del":
            del rows[r]
            self._sel = ("none", -1, -1)
        elif op == "row_move":
            j = r + ch["value"]
            if 0 <= j < len(rows):
                rows[r], rows[j] = rows[j], rows[r]
                self._sel = ("field", j, 0)
        elif op == "qr_position":
            qr = self._tmpl["qr"]
            new_position = ch["value"]
            if new_position == "free" and qr.get("position") != "free":
                # Convert the currently rendered placement into explicit free
                # coordinates.  Merely changing the mode would reset x/y to
                # zero and make the QR jump to the label's top-left corner.
                current = qr_metrics(self._tmpl, self._dims)
                if current is not None:
                    qr["x"] = round(float(current["x"]), 2)
                    qr["y"] = round(float(current["y"]), 2)
                    qr["sizeMm"] = round(float(current["sizeMm"]), 2)
                else:
                    size = min(float(self._dims["w"]), float(self._dims["h"])) \
                        * float(qr.get("sizePct") or 0.4)
                    qr["x"] = round(max(0.0, (float(self._dims["w"]) - size) / 2), 2)
                    qr["y"] = round(max(0.0, (float(self._dims["h"]) - size) / 2), 2)
                    qr["sizeMm"] = round(size, 2)
            qr["position"] = new_position
        elif op == "qr_size":
            qr = self._tmpl["qr"]
            size_pct = float(ch["value"])
            qr["sizePct"] = size_pct
            if qr.get("position") == "free":
                # Free placement uses the explicit millimetre size, so keep it
                # synchronized with the percentage controlled by the slider.
                w, h = float(self._dims["w"]), float(self._dims["h"])
                size = min(w, h) * size_pct
                qr["sizeMm"] = round(size, 2)
                qr["x"] = round(min(max(0.0, float(qr.get("x") or 0)), max(0.0, w - size)), 2)
                qr["y"] = round(min(max(0.0, float(qr.get("y") or 0)), max(0.0, h - size)), 2)
        elif op == "qr_content":
            self._tmpl["qr"]["content"] = ch["value"]
        elif op == "qr_ecc":
            self._tmpl["qr"]["ecc"] = ch["value"]
        elif op == "line_height":
            self._tmpl["lineHeight"] = ch["value"]
        elif op == "tmpl_shape":
            self._tmpl["shape"] = ch["value"]
        elif op == "tmpl_bgColor":
            self._tmpl["bgColor"] = ch["value"]
        elif op == "tmpl_cornerRadius":
            self._tmpl["cornerRadius"] = ch["value"]
        elif op == "tmpl_monochrome":
            self._tmpl["monochrome"] = bool(ch["value"])
        elif op == "field_color" and 0 <= r < len(rows):
            f = rows[r]["fields"][fi]
            if isinstance(f, dict):
                f["color"] = ch["value"]
        elif op and op.startswith("element_"):
            multi = sorted(i for i in getattr(self, "_multi", set())
                           if 0 <= i < len(self._elements()))
            if op in _ld._BATCH_OPS and len(multi) >= 2:
                for j in multi:               # one undo frame (already pushed)
                    c = dict(ch); c["index"] = j
                    self._apply_element_edit(op, c)
            else:
                self._apply_element_edit(op, ch)
        elif op == "dims":
            self._dims = {"w": float(ch.get("w") or self._dims.get("w", 60)),
                          "h": float(ch.get("h") or self._dims.get("h", 40))}
        # Structural edits (selection/list changed) rebuild the property panel;
        # value edits (the common case — slider drag, spinbox step, typed text)
        # only refresh the canvas + layers so the widget being edited survives.
        if op in _ld._STRUCTURAL_OPS:
            self._refresh_designer_state()
        else:
            self._refresh_live()

    # ── Free-form element edits ────────────────────────────────────────────────
    def _elements(self) -> list:
        if not isinstance(self._tmpl.get("elements"), list):
            self._tmpl["elements"] = []
        return self._tmpl["elements"]

    def _apply_element_edit(self, op: str, ch: dict) -> None:
        els = self._elements()
        i = ch.get("index", -1)
        if not (0 <= i < len(els)):
            return
        el = els[i]
        if op == "element_move":
            el["x"] = round(float(ch.get("x", el.get("x", 0))), 2)
            el["y"] = round(float(ch.get("y", el.get("y", 0))), 2)
        elif op == "element_resize":
            el["x"] = round(float(ch.get("x", el.get("x", 0))), 2)
            el["y"] = round(float(ch.get("y", el.get("y", 0))), 2)
            el["w"] = round(max(_ld.MIN_EL_MM, float(ch.get("w", el.get("w", _ld.MIN_EL_MM)))), 2)
            el["h"] = round(max(_ld.MIN_EL_MM, float(ch.get("h", el.get("h", _ld.MIN_EL_MM)))), 2)
        elif op == "element_line":
            for k in ("x1", "y1", "x2", "y2", "width"):
                if k in ch:
                    el[k] = round(float(ch[k]), 2)
        elif op == "element_text":
            el["text"] = ch.get("value", "")
        elif op == "element_size":
            el["size"] = ch.get("value")
        elif op == "element_bold":
            self._toggle_style(el, "bold", ch.get("value"))
        elif op == "element_italic":
            self._toggle_style(el, "italic", ch.get("value"))
        elif op == "element_align":
            el["align"] = ch.get("value")
        elif op == "element_color":
            el["color"] = ch.get("value")
        elif op == "element_stroke":
            el["stroke"] = ch.get("value")
        elif op == "element_fill":
            el["fill"] = ch.get("value")  # None clears fill
        elif op == "element_strokeWidth":
            el["strokeWidth"] = round(float(ch.get("value") or 0.0), 2)
        elif op == "element_cornerRadius":
            el["cornerRadius"] = round(float(ch.get("value") or 0.0), 2)
        elif op == "element_rotation":
            el["rotation"] = round(float(ch.get("value") or 0.0), 1)
        elif op == "element_opacity":
            el["opacity"] = min(1.0, max(0.0, float(ch.get("value", 1.0) or 0.0)))
        elif op == "element_dash":
            el["dash"] = ch.get("value") or "solid"
        elif op == "element_font":
            el["font"] = ch.get("value") or ""
        elif op == "element_arrowStart":
            el["arrowStart"] = bool(ch.get("value"))
        elif op == "element_arrowEnd":
            el["arrowEnd"] = bool(ch.get("value"))
        elif op == "element_wrap":
            el["wrap"] = bool(ch.get("value"))
        elif op == "element_gradient":
            el["gradient"] = ch.get("value")  # dict or None clears it
        elif op == "element_shadow":
            el["shadow"] = ch.get("value")    # dict or None clears it
        elif op == "element_hidden":
            el["hidden"] = bool(ch.get("value"))
        elif op == "element_locked":
            el["locked"] = bool(ch.get("value"))
        elif op == "element_apply_style":
            for k, v in (ch.get("style_dict") or {}).items():
                el[k] = copy.deepcopy(v)
        elif op == "element_key":
            el["key"] = ch.get("value")
        elif op == "element_content":
            el["content"] = ch.get("value")
        elif op == "element_showText":
            el["showText"] = bool(ch.get("value"))
        elif op == "element_keepAspect":
            el["keepAspect"] = bool(ch.get("value"))
        elif op == "element_image":
            el["data"] = ch.get("data")
            el["path"] = None
        elif op == "element_dup":
            els.insert(i + 1, copy.deepcopy(el))
            self._sel = ("element", -1, i + 1)
        elif op == "element_del":
            del els[i]
            self._sel = ("none", -1, -1)
        elif op == "element_z":
            j = i + int(ch.get("value", 0))
            if 0 <= j < len(els):
                els[i], els[j] = els[j], els[i]
                self._sel = ("element", -1, j)

    @staticmethod
    def _toggle_style(obj: dict, flag: str, on) -> None:
        s = set((obj.get("style") or "").split())
        s.discard("")
        s.add(flag) if on else s.discard(flag)
        obj["style"] = " ".join(sorted(s))

    # ── Align / distribute (Phase 1) ──────────────────────────────────────────
    @staticmethod
    def _el_bbox(el: dict) -> tuple:
        """Axis-aligned (x, y, w, h) in mm for any element type."""
        if el.get("type") == "line":
            x1, y1 = float(el.get("x1") or 0), float(el.get("y1") or 0)
            x2, y2 = float(el.get("x2") or 0), float(el.get("y2") or 0)
            return min(x1, x2), min(y1, y2), abs(x2 - x1), abs(y2 - y1)
        return (float(el.get("x") or 0), float(el.get("y") or 0),
                float(el.get("w") or 0), float(el.get("h") or 0))

    @staticmethod
    def _el_move_to(el: dict, x: float, y: float) -> None:
        """Move an element so its bounding box top-left is at (x, y)."""
        if el.get("type") == "line":
            bx, by, _, _ = LabelDesignerEditMixin._el_bbox(el)
            dx, dy = x - bx, y - by
            el["x1"] = round(float(el.get("x1") or 0) + dx, 2)
            el["y1"] = round(float(el.get("y1") or 0) + dy, 2)
            el["x2"] = round(float(el.get("x2") or 0) + dx, 2)
            el["y2"] = round(float(el.get("y2") or 0) + dy, 2)
        else:
            el["x"], el["y"] = round(x, 2), round(y, 2)

    def _selected_element_indices(self) -> list:
        """Indices targeted by group ops: the multi-set, else the single anchor."""
        multi = sorted(getattr(self, "_multi", set()))
        if multi:
            return multi
        kind, _row, field = self._sel
        return [field] if kind == "element" and field >= 0 else []

    # ── Persistent grouping (Phase 5) ─────────────────────────────────────────
    def _group_selection(self) -> None:
        """Assign a shared group id to every selected element (≥2)."""
        idx = [i for i in self._selected_element_indices()
               if 0 <= i < len(self._elements())]
        if len(idx) < 2:
            return
        self._save_template_snapshot_for_undo()
        els = self._elements()
        gid = uuid.uuid4().hex[:8]
        for j in idx:
            els[j]["group"] = gid
        self._refresh_designer_state()

    def _ungroup_selection(self) -> None:
        """Clear the group id from the selection's whole group(s)."""
        els = self._elements()
        idx = [i for i in self._selected_element_indices() if 0 <= i < len(els)]
        gids = {els[j].get("group") for j in idx}
        gids.discard(None)
        if not gids:
            return
        self._save_template_snapshot_for_undo()
        for e in els:
            if e.get("group") in gids:
                e["group"] = None
        self._refresh_designer_state()

    def _group_peers(self, index: int) -> set:
        """All element indices sharing *index*'s group (incl. itself)."""
        els = self._elements()
        if not (0 <= index < len(els)):
            return set()
        gid = els[index].get("group")
        if gid is None:
            return {index}
        return {i for i, e in enumerate(els) if e.get("group") == gid}

    # ── Format painter + batch edit (Phase 6) ─────────────────────────────────
    def _capture_style(self, index: int) -> dict:
        """Snapshot the copyable style keys of element *index* (no geometry)."""
        el = self._element_at(index)
        if el is None:
            return {}
        return {k: copy.deepcopy(el[k]) for k in _ld._STYLE_KEYS if k in el}

    def _apply_element_edit_multi(self, op: str, ch: dict) -> None:
        """Apply *op* to every selected element in one undo frame."""
        idx = [i for i in self._selected_element_indices()
               if 0 <= i < len(self._elements())]
        if not idx:
            return
        self._save_template_snapshot_for_undo()
        for j in idx:
            c = dict(ch)
            c["index"] = j
            self._apply_element_edit(op, c)
        self._refresh_designer_state()

    def _reorder_element(self, src: int, dst: int) -> None:
        """Move element *src* to list position *dst* (z-order drag-reorder)."""
        els = self._elements()
        if not (0 <= src < len(els)) or not (0 <= dst < len(els)) or src == dst:
            return
        self._save_template_snapshot_for_undo()
        el = els.pop(src)
        els.insert(dst, el)
        self._sel = ("element", -1, dst)
        self._refresh_designer_state()

    def _align_elements(self, mode: str, indices: Optional[list] = None) -> None:
        """Align elements. Reference is the selection's bounding box when ≥2 are
        selected, else the whole label box (artboard) for a single element."""
        els = self._elements()
        idx = indices if indices is not None else self._selected_element_indices()
        idx = [i for i in idx if 0 <= i < len(els)]
        if not idx:
            return
        if len(idx) >= 2:
            boxes = [self._el_bbox(els[i]) for i in idx]
            ref_x = min(b[0] for b in boxes)
            ref_y = min(b[1] for b in boxes)
            ref_r = max(b[0] + b[2] for b in boxes)
            ref_b = max(b[1] + b[3] for b in boxes)
        else:
            ref_x, ref_y = 0.0, 0.0
            ref_r = float(self._dims.get("w", 60))
            ref_b = float(self._dims.get("h", 40))
        self._save_template_snapshot_for_undo()
        for i in idx:
            el = els[i]
            x, y, w, h = self._el_bbox(el)
            if mode == "left":
                x = ref_x
            elif mode == "right":
                x = ref_r - w
            elif mode == "hcenter":
                x = ref_x + (ref_r - ref_x - w) / 2.0
            elif mode == "top":
                y = ref_y
            elif mode == "bottom":
                y = ref_b - h
            elif mode == "vcenter":
                y = ref_y + (ref_b - ref_y - h) / 2.0
            self._el_move_to(el, x, y)
        self._refresh_designer_state()

    def _distribute_elements(self, axis: str, indices: Optional[list] = None) -> None:
        """Even the gaps between ≥3 elements along *axis* ('h' or 'v').

        End elements stay pinned; interior ones are repositioned so the empty
        space between successive bounding boxes is equal.
        """
        els = self._elements()
        idx = indices if indices is not None else self._selected_element_indices()
        idx = [i for i in idx if 0 <= i < len(els)]
        if len(idx) < 3:
            return
        a = 0 if axis == "h" else 1   # bbox tuple offset for position
        s = 2 if axis == "h" else 3   # bbox tuple offset for size
        order = sorted(idx, key=lambda i: self._el_bbox(els[i])[a])
        boxes = {i: self._el_bbox(els[i]) for i in order}
        start = boxes[order[0]][a]
        end = boxes[order[-1]][a] + boxes[order[-1]][s]
        total_size = sum(boxes[i][s] for i in order)
        gap = (end - start - total_size) / (len(order) - 1)
        self._save_template_snapshot_for_undo()
        cursor = start
        for i in order:
            b = boxes[i]
            if axis == "h":
                self._el_move_to(els[i], cursor, b[1])
            else:
                self._el_move_to(els[i], b[0], cursor)
            cursor += b[s] + gap
        self._refresh_designer_state()

    def _add_element(self, etype: str, points: list | None = None) -> None:
        self._save_template_snapshot_for_undo()
        els = self._elements()
        el = _ld._default_element(etype, self._dims)
        if etype == "shape" and points:
            el["points"] = copy.deepcopy(points)
        els.append(el)
        self._sel = ("element", -1, len(els) - 1)
        self._refresh_designer_state()

    def _add_row_with_field(self, key: str) -> None:
        self._save_template_snapshot_for_undo()
        self._tmpl["rows"].append({
            "fields": [{"key": key, "style": "", "size": None, "offsetX": 0, "offsetY": 0}],
            "size": 9, "style": "", "align": "left", "wrap": True,
        })
        self._sel = ("field", len(self._tmpl["rows"]) - 1, 0)
        self._refresh_designer_state()

