"""Drag, resize, rotate, and nudge interactions for label designer."""
from __future__ import annotations

from typing import Optional


class LabelDesignerInteractionMixin:
    # ── Drag / nudge ──────────────────────────────────────────────────────────
    def _on_drag_start(self) -> None:
        self._save_template_snapshot_for_undo()
        kind, row, field = self._sel
        if kind == "field":
            f = self._tmpl["rows"][row]["fields"][field]
            self._drag_baseline = (float(f.get("offsetX") or 0), float(f.get("offsetY") or 0))
        elif kind == "qr":
            from app.utils.label_core import qr_metrics
            qr = self._tmpl["qr"]
            if qr.get("position") == "free":
                self._drag_baseline = (float(qr.get("x") or 0), float(qr.get("y") or 0))
            else:
                m = qr_metrics(self._tmpl, self._dims)
                self._drag_baseline = (float(m["x"]) if m else 0.0, float(m["y"]) if m else 0.0)
        elif kind == "element":
            el = self._element_at(field)
            if el is None:
                self._drag_baseline = None
            elif el.get("type") == "line":
                self._drag_baseline = ("line", float(el.get("x1") or 0), float(el.get("y1") or 0),
                                       float(el.get("x2") or 0), float(el.get("y2") or 0))
            else:
                self._drag_baseline = (float(el.get("x") or 0), float(el.get("y") or 0))
        else:
            self._drag_baseline = None

    def _element_at(self, i: int) -> Optional[dict]:
        els = self._tmpl.get("elements") or []
        return els[i] if 0 <= i < len(els) else None

    def _on_dragged(self, dx_mm: float, dy_mm: float) -> None:
        if self._drag_baseline is None:
            return
        kind, row, field = self._sel
        if kind == "element" and isinstance(self._drag_baseline, tuple) \
                and self._drag_baseline and self._drag_baseline[0] == "line":
            el = self._element_at(field)
            if el is not None:
                _, x1, y1, x2, y2 = self._drag_baseline
                el["x1"], el["y1"] = round(x1 + dx_mm, 2), round(y1 + dy_mm, 2)
                el["x2"], el["y2"] = round(x2 + dx_mm, 2), round(y2 + dy_mm, 2)
            self._refresh_canvas()
            return
        bx, by = self._drag_baseline
        if kind == "field":
            f = self._tmpl["rows"][row]["fields"][field]
            f["offsetX"] = round(bx + dx_mm, 2)
            f["offsetY"] = round(by + dy_mm, 2)
        elif kind == "qr":
            qr = self._tmpl["qr"]
            qr["position"] = "free"
            qr["x"] = round(max(0.0, bx + dx_mm), 2)
            qr["y"] = round(max(0.0, by + dy_mm), 2)
            qr.setdefault("sizeMm", round(min(self._dims["w"], self._dims["h"]) * float(qr.get("sizePct") or 0.4), 1))
        elif kind == "element":
            el = self._element_at(field)
            if el is None:
                return
            nx, ny = bx + dx_mm, by + dy_mm
            w = float(el.get("w") or 0)
            h = float(el.get("h") or 0)
            nx, ny, guides = self._canvas.snap(nx, ny, w, h, skip_index=field)
            el["x"] = round(nx, 2)
            el["y"] = round(ny, 2)
            self._canvas.set_guides(guides)
        self._refresh_canvas()

    def _on_element_resized(self, index: int, x: float, y: float, w: float, h: float) -> None:
        el = self._element_at(index)
        if el is None:
            return
        nx, ny, guides = self._canvas.snap(x, y, w, h, skip_index=index)
        el["x"], el["y"], el["w"], el["h"] = round(nx, 2), round(ny, 2), round(w, 2), round(h, 2)
        self._canvas.set_guides(guides)
        self._refresh_canvas()

    def _on_element_rotated(self, index: int, angle: float) -> None:
        el = self._element_at(index)
        if el is None:
            return
        el["rotation"] = round(float(angle), 1)
        self._refresh_canvas()

    def _finish_interaction(self) -> None:
        """Synchronize inspectors once after a drag/resize/rotate gesture."""
        self._refresh_inspectors()

    def _on_nudged(self, dx_mm: float, dy_mm: float) -> None:
        kind, row, field = self._sel
        if kind not in ("field", "qr", "element"):
            return
        self._save_template_snapshot_for_undo()
        if kind == "field":
            f = self._tmpl["rows"][row]["fields"][field]
            f["offsetX"] = round(float(f.get("offsetX") or 0) + dx_mm, 2)
            f["offsetY"] = round(float(f.get("offsetY") or 0) + dy_mm, 2)
        elif kind == "element":
            el = self._element_at(field)
            if el is None:
                return
            el["x"] = round(float(el.get("x") or 0) + dx_mm, 2)
            el["y"] = round(float(el.get("y") or 0) + dy_mm, 2)
        else:
            self._on_drag_start()  # captures baseline + saves another undo snapshot (harmless)
            self._on_dragged(dx_mm, dy_mm)
            return
        self._refresh_designer_state()

