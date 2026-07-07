"""Interactive WYSIWYG canvas for the label designer."""
from __future__ import annotations

import math
from typing import Optional

from PyQt6.QtCore import QPoint, QRectF, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import QWidget

from app.utils.label_render import render_label_onto
from app.widgets import label_designer_support as _ld

class _DesignCanvas(QWidget):
    """Renders the label and lets the user click/drag elements."""

    selected = pyqtSignal(str, int, int)   # kind, row, field (field == index when kind=="element")
    drag_started = pyqtSignal()
    dragged = pyqtSignal(float, float)     # cumulative dx, dy in mm
    nudged = pyqtSignal(float, float)      # arrow-key step in mm
    element_resized = pyqtSignal(int, float, float, float, float)  # index, x, y, w, h (mm)
    multi_toggle = pyqtSignal(int)         # Ctrl-click toggles element index in group
    marquee = pyqtSignal(float, float, float, float)  # box-select rect x,y,w,h (mm)
    delete_pressed = pyqtSignal()          # Del / Backspace on the canvas
    edit_requested = pyqtSignal(int)       # double-click a text element → inline edit
    element_rotated = pyqtSignal(int, float)  # index, angle(deg) — rotation handle
    interaction_finished = pyqtSignal()       # drag/resize/rotate committed

    _HANDLES = ("nw", "n", "ne", "e", "se", "s", "sw", "w")
    _HANDLE_PX = 7

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setMinimumSize(360, 300)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setStyleSheet("background:#0c1e26;")
        self._tmpl: dict = {}
        self._dims: dict = {"w": 60, "h": 40}
        self._data: dict = {}
        self._pixmap: Optional[QPixmap] = None
        self._boxes: list = []
        self._origin = QPoint(0, 0)
        self._ppm = 1.0
        self._sel_kind = "none"
        self._sel_row = -1
        self._sel_field = -1
        self._multi: set = set()          # extra element indices (group selection)
        self._press_pt: Optional[QPoint] = None
        self._dragging = False
        # resize state
        self._resize_handle: Optional[str] = None
        self._resize_base: Optional[tuple] = None  # (x,y,w,h) mm at grab
        self._rotating: bool = False               # rotation-handle drag active
        # smart-assist state
        self._snap_enabled = True
        self._grid_mm = 1.0
        self._snap_px = 6
        self._guides: list = []           # [("v"|"h", mm)] live alignment guides
        self._user_guides: list = []      # persistent reference guides (designer-local)
        self._new_guide: Optional[str] = None   # axis being dragged out of a ruler
        self._new_guide_mm = 0.0
        self._marquee_start: Optional[QPoint] = None  # box-select anchor (widget px)
        self._marquee_rect = None                     # QRect during box-select
        self._show_guides = False         # margin/bleed overlay toggle
        self._safe_mm = 2.0
        self._bleed_mm = 0.0
        self._zoom = 1.0                  # 1.0 = fit-to-window (default)
        self._pan = QPoint(0, 0)          # extra origin offset (px)
        self._panning = False             # space-drag pan active

    def set_content(self, tmpl: dict, dims: dict, data: dict) -> None:
        self._tmpl = tmpl
        self._dims = dims
        self._data = data
        self._render_label_canvas()

    def set_selection(self, kind: str, row: int, field: int) -> None:
        self._sel_kind, self._sel_row, self._sel_field = kind, row, field
        self.update()

    def set_multi(self, indices) -> None:
        self._multi = set(indices or ())
        self.update()

    def _render_label_canvas(self) -> None:
        w_mm = max(1.0, float(self._dims.get("w", 60)))
        h_mm = max(1.0, float(self._dims.get("h", 40)))
        pad = 18
        avail_w = max(40, self.width() - 2 * pad)
        avail_h = max(40, self.height() - 2 * pad)
        fit = max(1.0, min(avail_w / w_mm, avail_h / h_mm))
        self._ppm = fit * self._zoom    # zoom multiplies the fit-to-window scale
        w_px = max(1, int(w_mm * self._ppm))
        h_px = max(1, int(h_mm * self._ppm))
        pm = QPixmap(w_px, h_px)
        pm.fill(QColor("white"))
        painter = QPainter(pm)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        self._boxes = []
        render_label_onto(painter, self._tmpl, self._dims, self._data,
                           px_per_mm=self._ppm, placeholder=True, fill_bg=False,
                           hit_boxes=self._boxes)
        painter.end()
        self._pixmap = pm
        self._origin = QPoint((self.width() - w_px) // 2 + self._pan.x(),
                              (self.height() - h_px) // 2 + self._pan.y())
        self.update()

    def set_zoom(self, z: float) -> None:
        self._zoom = min(8.0, max(0.2, float(z)))
        self._render_label_canvas()

    def zoom_by(self, factor: float) -> None:
        self.set_zoom(self._zoom * factor)

    def reset_zoom(self) -> None:
        self._zoom = 1.0
        self._pan = QPoint(0, 0)
        self._render_label_canvas()

    def wheelEvent(self, e) -> None:  # noqa: N802
        if e.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.zoom_by(1.15 if e.angleDelta().y() > 0 else 1 / 1.15)
            e.accept()
        else:
            super().wheelEvent(e)

    def resizeEvent(self, e) -> None:  # noqa: N802
        super().resizeEvent(e)
        self._render_label_canvas()

    def paintEvent(self, e) -> None:  # noqa: N802
        p = QPainter(self)
        p.fillRect(self.rect(), QColor("#0c1e26"))
        if self._pixmap is None:
            return
        p.drawPixmap(self._origin, self._pixmap)
        # label border — circle for circle shape, rect otherwise
        p.setPen(QPen(QColor("#345"), 1))
        ox, oy = self._origin.x(), self._origin.y()
        W, H = self._pixmap.width(), self._pixmap.height()
        if (self._tmpl.get("shape") or "rect").lower() == "circle":
            from PyQt6.QtCore import QRectF as _QRectF
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            p.drawEllipse(_QRectF(ox + 0.5, oy + 0.5, W - 1, H - 1))
        else:
            p.drawRect(ox, oy, W, H)

        # rulers (mm ticks in the margin) + persistent reference guides
        self._paint_rulers(p, ox, oy, W, H)
        if self._user_guides:
            pen = QPen(QColor("#3da9fc"), 1)
            p.setPen(pen)
            for axis, gmm in self._user_guides:
                if axis == "v":
                    x = int(ox + gmm * self._ppm)
                    p.drawLine(x, oy, x, oy + H)
                else:
                    y = int(oy + gmm * self._ppm)
                    p.drawLine(ox, y, ox + W, y)

        # margin / bleed guides (designer-local, never printed)
        if self._show_guides:
            self._paint_safe_bleed(p, ox, oy, W, H)

        # selection highlight
        box = self._selected_box()
        if box is not None:
            p.setPen(QPen(QColor("#29b9ab"), 2))
            p.setBrush(QColor(41, 185, 171, 40))
            p.drawRect(int(ox + box["x"]), int(oy + box["y"]),
                       int(box["w"]), int(box["h"]))
            # resize handles only for free-form elements
            if self._sel_kind == "element":
                p.setBrush(QColor("#29b9ab"))
                p.setPen(QPen(QColor("#0c1e26"), 1))
                for r in self._selection_handle_rects_for_box(box).values():
                    p.drawRect(r)

        # group (multi) selection highlight — dashed amber on every member
        if self._multi:
            pen = QPen(QColor("#f0a500"), 2)
            pen.setStyle(Qt.PenStyle.DashLine)
            p.setPen(pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            for b in self._boxes:
                if b.get("kind") == "element" and b.get("index") in self._multi:
                    p.drawRect(int(ox + b["x"]), int(oy + b["y"]),
                               int(b["w"]), int(b["h"]))

        # marquee box-select rubber band
        if self._marquee_rect is not None:
            pen = QPen(QColor("#29b9ab"), 1)
            pen.setStyle(Qt.PenStyle.DashLine)
            p.setPen(pen)
            p.setBrush(QColor(41, 185, 171, 30))
            p.drawRect(self._marquee_rect)

        # live alignment guides (red) during drag/resize
        if self._guides:
            pen = QPen(QColor("#ff4d4f"), 1)
            p.setPen(pen)
            for axis, mm in self._guides:
                if axis == "v":
                    x = int(ox + mm * self._ppm)
                    p.drawLine(x, oy, x, oy + H)
                else:
                    y = int(oy + mm * self._ppm)
                    p.drawLine(ox, y, ox + W, y)

    def _paint_rulers(self, p: QPainter, ox: int, oy: int, W: int, H: int) -> None:
        """Draw mm tick marks in the top + left margins around the label."""
        if self._ppm <= 0:
            return
        p.setPen(QPen(QColor("#5b7a83"), 1))
        w_mm = int(float(self._dims.get("w", 60)))
        h_mm = int(float(self._dims.get("h", 40)))
        step = 5 if self._ppm < 5 else 1   # coarser ticks when zoomed out
        for mm in range(0, w_mm + 1, step):
            x = int(ox + mm * self._ppm)
            tick = 6 if mm % 5 == 0 else 3
            p.drawLine(x, oy - tick, x, oy)
        for mm in range(0, h_mm + 1, step):
            y = int(oy + mm * self._ppm)
            tick = 6 if mm % 5 == 0 else 3
            p.drawLine(ox - tick, y, ox, y)
        # live preview of the guide being dragged out of a ruler
        if self._new_guide is not None:
            p.setPen(QPen(QColor("#3da9fc"), 1))
            if self._new_guide == "v":
                x = int(ox + self._new_guide_mm * self._ppm)
                p.drawLine(x, oy, x, oy + H)
            else:
                y = int(oy + self._new_guide_mm * self._ppm)
                p.drawLine(ox, y, ox + W, y)

    def _paint_safe_bleed(self, p: QPainter, ox: int, oy: int, W: int, H: int) -> None:
        pen = QPen(QColor("#8aa"))
        pen.setStyle(Qt.PenStyle.DashLine)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        s = int(self._safe_mm * self._ppm)
        if s > 0:
            p.drawRect(ox + s, oy + s, W - 2 * s, H - 2 * s)
        b = int(self._bleed_mm * self._ppm)
        if b > 0:
            p.drawRect(ox - b, oy - b, W + 2 * b, H + 2 * b)

    # ── selection / hit-testing ────────────────────────────────────────────────
    def _box_matches_sel(self, b: dict) -> bool:
        if b["kind"] != self._sel_kind:
            return False
        if b["kind"] == "element":
            return b.get("index") == self._sel_field
        return b.get("row") == self._sel_row and b.get("field") == self._sel_field

    def _selected_box(self) -> Optional[dict]:
        for b in self._boxes:
            if self._box_matches_sel(b):
                return b
        if self._sel_kind == "qr":
            for b in self._boxes:
                if b["kind"] == "qr":
                    return b
        return None

    @staticmethod
    def _box_sel(b: dict) -> tuple:
        """Map a hit-box to a (kind, row, field) selection tuple."""
        if b["kind"] == "element":
            return ("element", -1, int(b.get("index", -1)))
        return (b["kind"], int(b.get("row", -1)), int(b.get("field", -1)))

    def _hit_test_canvas_box(self, pt: QPoint) -> Optional[dict]:
        rx = pt.x() - self._origin.x()
        ry = pt.y() - self._origin.y()
        # last-drawn wins (elements appended last → topmost) → iterate reversed
        for box in reversed(self._boxes):
            if box["x"] <= rx <= box["x"] + box["w"] and box["y"] <= ry <= box["y"] + box["h"]:
                if box.get("kind") == "element":
                    el = self._element(int(box.get("index", -1)))
                    if el is not None and el.get("locked"):
                        continue  # locked layer is click-through in the designer
                return box
        return None

    def _selection_handle_rects_for_box(self, box: dict) -> dict:
        from PyQt6.QtCore import QRect
        ox, oy = self._origin.x(), self._origin.y()
        x, y, w, h = box["x"], box["y"], box["w"], box["h"]
        s = self._HANDLE_PX
        cx = {"w": x, "n": x + w / 2, "e": x + w, "s": x + w / 2}
        centers = {
            "nw": (x, y), "n": (x + w / 2, y), "ne": (x + w, y),
            "e": (x + w, y + h / 2), "se": (x + w, y + h),
            "s": (x + w / 2, y + h), "sw": (x, y + h), "w": (x, y + h / 2),
        }
        out = {}
        for name, (hx, hy) in centers.items():
            out[name] = QRect(int(ox + hx - s / 2), int(oy + hy - s / 2), s, s)
        # rotation grip floats above top-center — only for rotatable (non-line)
        el = self._element(int(box.get("index", -1))) \
            if box.get("kind") == "element" else None
        if el is not None and el.get("type") != "line":
            rx, ry = x + w / 2, y - 18
            out["rot"] = QRect(int(ox + rx - s / 2), int(oy + ry - s / 2), s, s)
        return out

    def _hit_handle(self, pt: QPoint) -> Optional[str]:
        if self._sel_kind != "element":
            return None
        box = self._selected_box()
        if box is None:
            return None
        for name, r in self._selection_handle_rects_for_box(box).items():
            if r.contains(pt):
                return name
        return None

    def mousePressEvent(self, e) -> None:  # noqa: N802
        self.setFocus()
        if self._panning:                 # space held → pan, not select
            self._press_pt = e.pos()
            return
        # dragging out of a ruler margin starts a new reference guide
        axis = self._ruler_axis(e.pos())
        if axis is not None:
            self._new_guide = axis
            self._new_guide_mm = self._guide_mm_at(e.pos(), axis)
            self.update()
            return
        # grabbing a resize handle takes priority over re-selecting / moving
        handle = self._hit_handle(e.pos())
        if handle is not None:
            box = self._selected_box()
            i = int(box.get("index", -1)) if box else -1
            el = self._element(i)
            if el is not None and handle == "rot":
                self._rotating = True
                self._press_pt = e.pos()
                self._dragging = False
                return
            if el is not None:
                self._resize_handle = handle
                self._resize_base = (float(el.get("x") or 0), float(el.get("y") or 0),
                                     float(el.get("w") or 0), float(el.get("h") or 0))
                self._press_pt = e.pos()
                self._dragging = False
                return
        ctrl = bool(e.modifiers() & Qt.KeyboardModifier.ControlModifier)
        b = self._hit_test_canvas_box(e.pos())
        if b is not None and b.get("kind") == "element" and ctrl:
            # Ctrl-click an element → toggle it in the group selection
            self.multi_toggle.emit(int(b.get("index", -1)))
            return
        if b is None:
            # empty area → begin a marquee box-select (committed on release)
            self._marquee_start = e.pos()
            self._marquee_rect = None
            self._press_pt = e.pos()
            self._dragging = False
            return
        kind, row, field = self._box_sel(b)
        self._sel_kind, self._sel_row, self._sel_field = kind, row, field
        self.selected.emit(kind, row, field)
        self._press_pt = e.pos()
        self._dragging = False
        self.update()

    def mouseDoubleClickEvent(self, e) -> None:  # noqa: N802
        b = self._hit_test_canvas_box(e.pos())
        if b is not None and b.get("kind") == "element":
            self.edit_requested.emit(int(b.get("index", -1)))
            return
        super().mouseDoubleClickEvent(e)

    def element_screen_rect(self, index: int):
        """Device-pixel QRect of element *index*'s box, or None."""
        from PyQt6.QtCore import QRect
        ox, oy = self._origin.x(), self._origin.y()
        for b in self._boxes:
            if b.get("kind") == "element" and b.get("index") == index:
                return QRect(int(ox + b["x"]), int(oy + b["y"]),
                             int(b["w"]), int(b["h"]))
        return None

    def _element(self, i: int) -> Optional[dict]:
        els = self._tmpl.get("elements") or []
        return els[i] if 0 <= i < len(els) else None

    def _guide_mm_at(self, pos: QPoint, axis: str) -> float:
        ox, oy = self._origin.x(), self._origin.y()
        if axis == "v":
            return (pos.x() - ox) / self._ppm if self._ppm else 0.0
        return (pos.y() - oy) / self._ppm if self._ppm else 0.0

    def mouseMoveEvent(self, e) -> None:  # noqa: N802
        if self._panning and self._press_pt is not None:
            delta = e.pos() - self._press_pt
            self._pan += delta
            self._press_pt = e.pos()
            # Pan only shifts the canvas origin — the label pixmap is unchanged,
            # so skip the expensive render_label_onto() rebuild and just move the
            # cached pixmap. (_origin is computed as centre + _pan, so shifting it
            # by the same delta keeps it consistent with _render_label_canvas().)
            self._origin += delta
            self.update()
            return
        if self._new_guide is not None:
            self._new_guide_mm = self._guide_mm_at(e.pos(), self._new_guide)
            self.update()
            return
        if self._marquee_start is not None:
            from PyQt6.QtCore import QRect
            self._marquee_rect = QRect(self._marquee_start, e.pos()).normalized()
            self.update()
            return
        if self._press_pt is None:
            return
        dx = e.pos().x() - self._press_pt.x()
        dy = e.pos().y() - self._press_pt.y()
        if not self._dragging and (abs(dx) + abs(dy)) > 3:
            self._dragging = True
            # fire once on first real movement → dialog pushes undo + captures
            # the move baseline (harmless/unused for the resize path)
            self.drag_started.emit()
        if not self._dragging:
            return
        if self._rotating:
            self._rotate_selected_element_to_pointer(e)
        elif self._resize_handle is not None:
            self._resize_selected_element_from_drag_delta(dx / self._ppm, dy / self._ppm)
        else:
            self.dragged.emit(dx / self._ppm, dy / self._ppm)

    def _rotate_selected_element_to_pointer(self, e) -> None:
        box = self._selected_box()
        if box is None:
            return
        cx = self._origin.x() + box["x"] + box["w"] / 2.0
        cy = self._origin.y() + box["y"] + box["h"] / 2.0
        ang = math.degrees(math.atan2(e.pos().y() - cy, e.pos().x() - cx)) + 90.0
        if e.modifiers() & Qt.KeyboardModifier.ShiftModifier:
            ang = round(ang / 15.0) * 15.0
        ang = round(ang % 360.0, 1)
        self.element_rotated.emit(int(box.get("index", -1)), ang)

    def _resize_selected_element_from_drag_delta(self, dx_mm: float, dy_mm: float) -> None:
        if self._resize_base is None:
            return
        bx, by, bw, bh = self._resize_base
        x, y, w, h = bx, by, bw, bh
        hnd = self._resize_handle
        if "e" in hnd:
            w = bw + dx_mm
        if "s" in hnd:
            h = bh + dy_mm
        if "w" in hnd:
            x = bx + dx_mm
            w = bw - dx_mm
        if "n" in hnd:
            y = by + dy_mm
            h = bh - dy_mm
        # clamp min size, pinning the opposite edge
        if w < _ld.MIN_EL_MM:
            if "w" in hnd:
                x = bx + bw - _ld.MIN_EL_MM
            w = _ld.MIN_EL_MM
        if h < _ld.MIN_EL_MM:
            if "n" in hnd:
                y = by + bh - _ld.MIN_EL_MM
            h = _ld.MIN_EL_MM
        box = self._selected_box()
        i = int(box.get("index", -1)) if box else -1
        self.element_resized.emit(i, round(x, 2), round(y, 2),
                                  round(w, 2), round(h, 2))

    def mouseReleaseEvent(self, e) -> None:  # noqa: N802
        if self._new_guide is not None:
            axis = self._new_guide
            mm = self._guide_mm_at(e.pos(), axis)
            self._new_guide = None
            limit = float(self._dims.get("w" if axis == "v" else "h", 60))
            if 0.0 <= mm <= limit:   # dropped inside the label → commit guide
                self.add_user_guide(axis, mm)
            self.update()
            return
        if self._marquee_start is not None:
            rect = self._marquee_rect
            self._marquee_start = None
            self._marquee_rect = None
            if rect is not None and (rect.width() > 3 or rect.height() > 3):
                ox, oy = self._origin.x(), self._origin.y()
                x_mm = (rect.x() - ox) / self._ppm
                y_mm = (rect.y() - oy) / self._ppm
                w_mm = rect.width() / self._ppm
                h_mm = rect.height() / self._ppm
                self.marquee.emit(x_mm, y_mm, w_mm, h_mm)
            else:
                # a bare click on empty space clears the selection
                self._sel_kind, self._sel_row, self._sel_field = "none", -1, -1
                self.selected.emit("none", -1, -1)
            self.update()
            return
        was_interacting = self._dragging
        self._press_pt = None
        self._dragging = False
        self._resize_handle = None
        self._resize_base = None
        self._rotating = False
        if self._guides:
            self._guides = []
            self.update()
        if was_interacting:
            self.interaction_finished.emit()

    def snap(self, x: float, y: float, w: float, h: float, skip_index: int = -1):
        """Snap an element's (x,y) to grid + neighbour edges/centers (mm).

        Returns ``(x, y, guides)`` where guides is a list of ``("v"|"h", mm)``
        alignment lines that were hit. ``skip_index`` excludes the moving
        element from the neighbour candidates.
        """
        if not self._snap_enabled or self._ppm <= 0:
            return x, y, []
        thr = self._snap_px / self._ppm
        guides: list = []
        w_mm = float(self._dims.get("w", 60))
        h_mm = float(self._dims.get("h", 40))
        # candidate vertical lines (x positions) and horizontal lines (y)
        vx = [0.0, w_mm / 2.0, w_mm]
        hy = [0.0, h_mm / 2.0, h_mm]
        for j, el in enumerate(self._tmpl.get("elements") or []):
            if j == skip_index or el.get("type") == "line":
                continue
            ex, ey = float(el.get("x") or 0), float(el.get("y") or 0)
            ew, eh = float(el.get("w") or 0), float(el.get("h") or 0)
            vx += [ex, ex + ew / 2, ex + ew]
            hy += [ey, ey + eh / 2, ey + eh]
        # persistent user reference guides are snap targets too
        for axis, gmm in self._user_guides:
            (vx if axis == "v" else hy).append(float(gmm))

        # try snapping left/center/right of the moving box to any vx
        def nearest_snap_target(edges, candidates):
            best = None
            for off, ev in edges:  # off = edge offset from x; ev = edge value
                for cv in candidates:
                    d = abs(ev - cv)
                    if d <= thr and (best is None or d < best[0]):
                        best = (d, cv - off, cv)  # new origin, guide line
            return best

        bx = nearest_snap_target([(0.0, x), (w / 2, x + w / 2), (w, x + w)], vx)
        if bx is not None:
            x = round(bx[1], 2)
            guides.append(("v", bx[2]))
        else:
            gx = round(x / self._grid_mm) * self._grid_mm
            if abs(gx - x) <= thr:
                x = gx
        by = nearest_snap_target([(0.0, y), (h / 2, y + h / 2), (h, y + h)], hy)
        if by is not None:
            y = round(by[1], 2)
            guides.append(("h", by[2]))
        else:
            gy = round(y / self._grid_mm) * self._grid_mm
            if abs(gy - y) <= thr:
                y = gy
        return x, y, guides

    def set_guides(self, guides: list) -> None:
        self._guides = guides or []
        self.update()

    # ── User reference guides (Phase 1b) ──────────────────────────────────────
    def add_user_guide(self, axis: str, mm: float) -> None:
        """Add a persistent reference guide (axis 'v'/'h', position in mm)."""
        if axis in ("v", "h"):
            self._user_guides.append((axis, round(float(mm), 2)))
            self.update()

    def clear_user_guides(self) -> None:
        self._user_guides = []
        self.update()

    def _ruler_axis(self, pos: QPoint) -> Optional[str]:
        """Return 'v' if *pos* is in the top ruler margin, 'h' if in the left
        ruler margin, else None. Dragging out of a ruler creates a guide."""
        if self._pixmap is None:
            return None
        ox, oy = self._origin.x(), self._origin.y()
        W, H = self._pixmap.width(), self._pixmap.height()
        if pos.y() < oy and ox <= pos.x() <= ox + W:
            return "v"
        if pos.x() < ox and oy <= pos.y() <= oy + H:
            return "h"
        return None

    def set_guide_overlay(self, show: bool, safe_mm: float = 2.0, bleed_mm: float = 0.0) -> None:
        self._show_guides = show
        self._safe_mm = safe_mm
        self._bleed_mm = bleed_mm
        self.update()

    def keyPressEvent(self, e) -> None:  # noqa: N802
        step = 0.5
        k = e.key()
        if k == Qt.Key.Key_Left:
            self.nudged.emit(-step, 0)
        elif k == Qt.Key.Key_Right:
            self.nudged.emit(step, 0)
        elif k == Qt.Key.Key_Up:
            self.nudged.emit(0, -step)
        elif k == Qt.Key.Key_Down:
            self.nudged.emit(0, step)
        elif k in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            self.delete_pressed.emit()
        elif k == Qt.Key.Key_Space:
            self._panning = True
            self.setCursor(Qt.CursorShape.OpenHandCursor)
        else:
            super().keyPressEvent(e)

    def keyReleaseEvent(self, e) -> None:  # noqa: N802
        if e.key() == Qt.Key.Key_Space:
            self._panning = False
            self.unsetCursor()
        else:
            super().keyReleaseEvent(e)

