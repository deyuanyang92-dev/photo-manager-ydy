"""label_designer_dialog.py — free-form label designer (canvas + property panel).

Canvas-first WYSIWYG editor the user asked for: click any text field or the QR
on the left canvas → its full set of adjustable properties appears on the right;
drag to move; arrow keys nudge; toolbar adds fields/rows and manages templates.

The canvas paints the SAME pixmap the printer/preview produce (via
``render_label_onto``) and overlays interactive hit-boxes the renderer emits —
so what you arrange is exactly what prints (no DOM/PDF drift like the web).

Reused, unchanged:
  * rendering + hit-boxes : app.utils.label_render.render_label_onto
  * QR image              : app.widgets.label_editor._generate_qr_pixmap
  * template shape        : app.utils.label_core.normalize_template
  * template library      : app.services.label_service.LabelTemplateLibrary
"""
from __future__ import annotations

import copy
from typing import Optional

from PyQt6.QtCore import Qt, QPoint, QRectF, pyqtSignal
from PyQt6.QtGui import QColor, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import (
    QColorDialog,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPushButton,
    QSlider,
    QSpinBox,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from app.services.label_service import LabelTemplateLibrary, key_from_id, is_library_key, id_from_key
from app.utils.label_core import normalize_template, ELEMENT_DEFAULTS, normalize_element
from app.utils.label_render import render_label_onto


# Minimum free-form element size (mm) — resize handles clamp to this.
MIN_EL_MM = 2.0

# Free-form element types offered by the "+元素" toolbar menu (type → label).
ELEMENT_TYPE_LABELS: dict[str, str] = {
    "text": "文字", "field": "绑定字段", "line": "直线", "rect": "矩形",
    "ellipse": "椭圆", "image": "图片", "barcode": "条码",
}


def _default_element(etype: str, dims: dict) -> dict:
    """Build a normalized default element of *etype*, centered on the label."""
    el = normalize_element({"type": etype})
    if el is None:
        el = normalize_element({"type": "rect"})
    w_mm = float(dims.get("w", 60))
    h_mm = float(dims.get("h", 40))
    if etype == "line":
        el["x1"] = round(w_mm * 0.25, 1)
        el["y1"] = round(h_mm * 0.5, 1)
        el["x2"] = round(w_mm * 0.75, 1)
        el["y2"] = round(h_mm * 0.5, 1)
    else:
        ew = float(el.get("w") or 20.0)
        eh = float(el.get("h") or 10.0)
        el["x"] = round(max(0.0, (w_mm - ew) / 2.0), 1)
        el["y"] = round(max(0.0, (h_mm - eh) / 2.0), 1)
        if etype == "text":
            el["text"] = "文字"
    return el


# Placeable fields (key → Chinese label), ordered for the picker.
FIELD_LABELS: dict[str, str] = {
    "uniqueId": "唯一编号", "headerId": "编号头",
    "province": "省份", "site": "样点", "station": "站位",
    "storage": "保存方式", "shortDate": "日期段", "fullDate": "完整日期段",
    "collectionDate": "采集日期", "photoDate": "拍摄日期",
    "speciesName": "物种名称", "latin": "拉丁名", "family": "科",
    "region": "地点", "geoArea": "采集地理区", "lon": "经度", "lat": "纬度",
    "collector": "采集人", "collectorLabel": "采集人(带'采集')",
    "photographer": "拍摄者", "photoNotes": "拍摄备注",
    "rnaPreservative": "RNA保存液",
}


def _field_name(key: str) -> str:
    return FIELD_LABELS.get(key, key)


def _btn(text: str, checkable: bool = False) -> QPushButton:
    b = QPushButton(text)
    b.setCheckable(checkable)
    b.setStyleSheet(
        "QPushButton { background:#0f2127; border:1px solid rgba(145,182,181,0.22);"
        " border-radius:4px; color:#cfe0db; padding:3px 9px; font-size:12px; }"
        "QPushButton:checked { background:rgba(41,185,171,0.20); border-color:#29b9ab; color:#29b9ab; }"
        "QPushButton:hover { border-color:#29b9ab; }"
    )
    return b


# ── Canvas ─────────────────────────────────────────────────────────────────────

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

    def set_content(self, tmpl: dict, dims: dict, data: dict) -> None:
        self._tmpl = tmpl
        self._dims = dims
        self._data = data
        self._render()

    def set_selection(self, kind: str, row: int, field: int) -> None:
        self._sel_kind, self._sel_row, self._sel_field = kind, row, field
        self.update()

    def set_multi(self, indices) -> None:
        self._multi = set(indices or ())
        self.update()

    def _render(self) -> None:
        w_mm = max(1.0, float(self._dims.get("w", 60)))
        h_mm = max(1.0, float(self._dims.get("h", 40)))
        pad = 18
        avail_w = max(40, self.width() - 2 * pad)
        avail_h = max(40, self.height() - 2 * pad)
        self._ppm = max(1.0, min(avail_w / w_mm, avail_h / h_mm))
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
        self._origin = QPoint((self.width() - w_px) // 2, (self.height() - h_px) // 2)
        self.update()

    def resizeEvent(self, e) -> None:  # noqa: N802
        super().resizeEvent(e)
        self._render()

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
                for r in self._handle_rects(box).values():
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

    def _hit(self, pt: QPoint) -> Optional[dict]:
        rx = pt.x() - self._origin.x()
        ry = pt.y() - self._origin.y()
        # last-drawn wins (elements appended last → topmost) → iterate reversed
        for b in reversed(self._boxes):
            if b["x"] <= rx <= b["x"] + b["w"] and b["y"] <= ry <= b["y"] + b["h"]:
                return b
        return None

    def _handle_rects(self, box: dict) -> dict:
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
        return out

    def _hit_handle(self, pt: QPoint) -> Optional[str]:
        if self._sel_kind != "element":
            return None
        box = self._selected_box()
        if box is None:
            return None
        for name, r in self._handle_rects(box).items():
            if r.contains(pt):
                return name
        return None

    def mousePressEvent(self, e) -> None:  # noqa: N802
        self.setFocus()
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
            if el is not None:
                self._resize_handle = handle
                self._resize_base = (float(el.get("x") or 0), float(el.get("y") or 0),
                                     float(el.get("w") or 0), float(el.get("h") or 0))
                self._press_pt = e.pos()
                self._dragging = False
                return
        ctrl = bool(e.modifiers() & Qt.KeyboardModifier.ControlModifier)
        b = self._hit(e.pos())
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
        b = self._hit(e.pos())
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
        if self._resize_handle is not None:
            self._do_resize(dx / self._ppm, dy / self._ppm)
        else:
            self.dragged.emit(dx / self._ppm, dy / self._ppm)

    def _do_resize(self, dx_mm: float, dy_mm: float) -> None:
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
        if w < MIN_EL_MM:
            if "w" in hnd:
                x = bx + bw - MIN_EL_MM
            w = MIN_EL_MM
        if h < MIN_EL_MM:
            if "n" in hnd:
                y = by + bh - MIN_EL_MM
            h = MIN_EL_MM
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
        self._press_pt = None
        self._dragging = False
        self._resize_handle = None
        self._resize_base = None
        if self._guides:
            self._guides = []
            self.update()

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
        def _best(edges, candidates):
            best = None
            for off, ev in edges:  # off = edge offset from x; ev = edge value
                for cv in candidates:
                    d = abs(ev - cv)
                    if d <= thr and (best is None or d < best[0]):
                        best = (d, cv - off, cv)  # new origin, guide line
            return best

        bx = _best([(0.0, x), (w / 2, x + w / 2), (w, x + w)], vx)
        if bx is not None:
            x = round(bx[1], 2)
            guides.append(("v", bx[2]))
        else:
            gx = round(x / self._grid_mm) * self._grid_mm
            if abs(gx - x) <= thr:
                x = gx
        by = _best([(0.0, y), (h / 2, y + h / 2), (h, y + h)], hy)
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
        else:
            super().keyPressEvent(e)


# ── Property panel ──────────────────────────────────────────────────────────────

class _PropertyPanel(QWidget):
    """Contextual property editor; emits a semantic ``edit(dict)`` per change."""

    edit = pyqtSignal(dict)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setStyleSheet("background:#08161b; color:#eef3ef;")
        self.setMinimumWidth(280)
        self._root = QVBoxLayout(self)
        self._root.setContentsMargins(12, 12, 12, 12)
        self._root.setSpacing(8)
        self._tmpl: dict = {}
        self.show_for("none", -1, -1, {})

    def _clear(self) -> None:
        while self._root.count():
            item = self._root.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)

    def _title(self, text: str) -> None:
        lbl = QLabel(text)
        lbl.setStyleSheet("color:#eef3ef; font-size:13px; font-weight:bold;")
        self._root.addWidget(lbl)

    def _row(self, *widgets) -> None:
        h = QHBoxLayout()
        h.setSpacing(6)
        for w in widgets:
            if isinstance(w, str):
                lbl = QLabel(w)
                lbl.setStyleSheet("color:#87a2a1; font-size:12px;")
                h.addWidget(lbl)
            else:
                h.addWidget(w)
        h.addStretch()
        wrap = QWidget()
        wrap.setLayout(h)
        self._root.addWidget(wrap)

    def show_for(self, kind: str, row: int, field: int, tmpl: dict) -> None:
        self._tmpl = tmpl
        self._clear()
        if kind == "field":
            self._build_field(row, field)
        elif kind == "qr":
            self._build_qr()
        elif kind == "element":
            self._build_element(field)
        else:
            self._build_label()
        self._root.addStretch(1)

    # ----- free-form element -----
    def _build_element(self, index: int) -> None:
        els = self._tmpl.get("elements") or []
        if not (0 <= index < len(els)):
            return self._build_label()
        el = els[index]
        et = el.get("type")
        names = {"text": "文字", "field": "绑定字段", "line": "直线", "rect": "矩形",
                 "ellipse": "椭圆", "image": "图片", "barcode": "条码"}
        self._title(f"元素 · {names.get(et, et)}")

        def _spin(val, lo=-500.0, hi=500.0, step=0.5, suffix=" mm"):
            s = QDoubleSpinBox()
            s.setRange(lo, hi)
            s.setSingleStep(step)
            s.setSuffix(suffix)
            s.setValue(float(val or 0))
            return s

        if et == "line":
            x1 = _spin(el.get("x1")); y1 = _spin(el.get("y1"))
            x2 = _spin(el.get("x2")); y2 = _spin(el.get("y2"))
            for s, k in ((x1, "x1"), (y1, "y1"), (x2, "x2"), (y2, "y2")):
                s.valueChanged.connect(lambda v, kk=k: self.edit.emit(
                    {"op": "element_line", "index": index, kk: v}))
            self._row("起点", x1, y1)
            self._row("终点", x2, y2)
            wd = _spin(el.get("width"), 0.0, 20.0, 0.1)
            wd.valueChanged.connect(lambda v: self.edit.emit(
                {"op": "element_line", "index": index, "width": v}))
            self._row("粗细", wd)
        else:
            xs = _spin(el.get("x")); ys = _spin(el.get("y"))
            ws = _spin(el.get("w"), 0.5, 500.0); hs = _spin(el.get("h"), 0.5, 500.0)
            xs.valueChanged.connect(lambda v: self.edit.emit(
                {"op": "element_move", "index": index, "x": v, "y": el.get("y", 0)}))
            ys.valueChanged.connect(lambda v: self.edit.emit(
                {"op": "element_move", "index": index, "x": el.get("x", 0), "y": v}))
            ws.valueChanged.connect(lambda v: self.edit.emit(
                {"op": "element_resize", "index": index, "x": el.get("x", 0),
                 "y": el.get("y", 0), "w": v, "h": el.get("h", 0)}))
            hs.valueChanged.connect(lambda v: self.edit.emit(
                {"op": "element_resize", "index": index, "x": el.get("x", 0),
                 "y": el.get("y", 0), "w": el.get("w", 0), "h": v}))
            self._row("位置", xs, ys)
            self._row("大小", ws, hs)

        # rotation (not meaningful for line)
        if et != "line":
            rot = QSpinBox(); rot.setRange(-180, 180); rot.setSuffix("°")
            rot.setValue(int(float(el.get("rotation") or 0)))
            rot.valueChanged.connect(lambda v: self.edit.emit(
                {"op": "element_rotation", "index": index, "value": v}))
            self._row("旋转", rot)

        if et == "text":
            txt = QLineEdit(el.get("text") or "")
            txt.textChanged.connect(lambda t: self.edit.emit(
                {"op": "element_text", "index": index, "value": t}))
            self._row("内容", txt)
            self._text_style_rows(index, el)
        elif et == "field":
            combo = QComboBox()
            for key, name in FIELD_LABELS.items():
                combo.addItem(name, key)
            ci = combo.findData(el.get("key"))
            combo.setCurrentIndex(ci if ci >= 0 else 0)
            combo.currentIndexChanged.connect(lambda _i: self.edit.emit(
                {"op": "element_key", "index": index, "value": combo.currentData()}))
            self._row("字段", combo)
            self._text_style_rows(index, el)
        elif et in ("rect", "ellipse"):
            stroke = QPushButton(); stroke.setFixedSize(60, 22)
            self._update_color_btn(stroke, el.get("stroke") or "#000000")
            stroke.clicked.connect(lambda _=False, b=stroke: self._pick_element_color(
                index, "element_stroke", b))
            self._row("描边", stroke)
            sw = _spin(el.get("strokeWidth"), 0.0, 20.0, 0.1)
            sw.valueChanged.connect(lambda v: self.edit.emit(
                {"op": "element_strokeWidth", "index": index, "value": v}))
            self._row("线宽", sw)
            fill = QPushButton(); fill.setFixedSize(60, 22)
            self._update_color_btn(fill, el.get("fill") or "#ffffff")
            fill.clicked.connect(lambda _=False, b=fill: self._pick_element_color(
                index, "element_fill", b))
            nofill = _btn("无填充")
            nofill.clicked.connect(lambda: self.edit.emit(
                {"op": "element_fill", "index": index, "value": None}))
            self._row("填充", fill, nofill)
            if et == "rect":
                cr = _spin(el.get("cornerRadius"), 0.0, 30.0, 0.5)
                cr.valueChanged.connect(lambda v: self.edit.emit(
                    {"op": "element_cornerRadius", "index": index, "value": v}))
                self._row("圆角", cr)
        elif et == "image":
            pick = _btn("选择图片…")
            pick.clicked.connect(lambda: self._pick_element_image(index))
            self._row("图片", pick)
            ka = _btn("保持比例", True)
            ka.setChecked(el.get("keepAspect") is not False)
            ka.toggled.connect(lambda on: self.edit.emit(
                {"op": "element_keepAspect", "index": index, "value": on}))
            self._row(ka)
        elif et == "barcode":
            content = QLineEdit(el.get("content") or "")
            content.setPlaceholderText("字段key或字面值，如 uniqueId")
            content.textChanged.connect(lambda t: self.edit.emit(
                {"op": "element_content", "index": index, "value": t}))
            self._row("内容", content)
            st = _btn("显示文本", True); st.setChecked(el.get("showText") is not False)
            st.toggled.connect(lambda on: self.edit.emit(
                {"op": "element_showText", "index": index, "value": on}))
            self._row(st)

        # z-order + delete/duplicate
        zup, zdn = _btn("上移一层"), _btn("下移一层")
        zup.clicked.connect(lambda: self.edit.emit({"op": "element_z", "index": index, "value": 1}))
        zdn.clicked.connect(lambda: self.edit.emit({"op": "element_z", "index": index, "value": -1}))
        self._row(zup, zdn)
        dup, dele = _btn("复制元素"), _btn("删除元素")
        dup.clicked.connect(lambda: self.edit.emit({"op": "element_dup", "index": index}))
        dele.clicked.connect(lambda: self.edit.emit({"op": "element_del", "index": index}))
        self._row(dup, dele)

    def _text_style_rows(self, index: int, el: dict) -> None:
        size = QSpinBox(); size.setRange(4, 60)
        size.setValue(int(el.get("size") or 9))
        size.valueChanged.connect(lambda v: self.edit.emit(
            {"op": "element_size", "index": index, "value": v}))
        b = _btn("B", True); b.setChecked("bold" in (el.get("style") or ""))
        i = _btn("I", True); i.setChecked("italic" in (el.get("style") or ""))
        b.toggled.connect(lambda on: self.edit.emit({"op": "element_bold", "index": index, "value": on}))
        i.toggled.connect(lambda on: self.edit.emit({"op": "element_italic", "index": index, "value": on}))
        self._row("字号", size, b, i)
        al, ac, ar = _btn("左", True), _btn("中", True), _btn("右", True)
        {"left": al, "center": ac, "right": ar}.get(el.get("align") or "left", al).setChecked(True)
        al.clicked.connect(lambda: self.edit.emit({"op": "element_align", "index": index, "value": "left"}))
        ac.clicked.connect(lambda: self.edit.emit({"op": "element_align", "index": index, "value": "center"}))
        ar.clicked.connect(lambda: self.edit.emit({"op": "element_align", "index": index, "value": "right"}))
        self._row("对齐", al, ac, ar)
        color_btn = QPushButton(); color_btn.setFixedSize(60, 22)
        self._update_color_btn(color_btn, el.get("color") or "#000000")
        color_btn.clicked.connect(lambda _=False, b=color_btn: self._pick_element_color(
            index, "element_color", b))
        self._row("颜色", color_btn)

    def _pick_element_color(self, index: int, op: str, btn: QPushButton) -> None:
        c = QColorDialog.getColor(QColor(btn.text() or "#000000"), btn.window())
        if c.isValid():
            self._update_color_btn(btn, c.name())
            self.edit.emit({"op": op, "index": index, "value": c.name()})

    def _pick_element_image(self, index: int) -> None:
        import base64
        from app.utils.ui import get_open_file_name
        path = get_open_file_name(self.window(), "选择图片",
                                  filter="图片 (*.png *.jpg *.jpeg *.bmp)")
        if not path:
            return
        try:
            with open(path, "rb") as fh:
                raw = fh.read()
            b64 = base64.b64encode(raw).decode("ascii")
        except Exception:
            return
        self.edit.emit({"op": "element_image", "index": index, "data": b64})

    # ----- field / row -----
    def _build_field(self, row_idx: int, field_idx: int) -> None:
        rows = self._tmpl.get("rows") or []
        if not (0 <= row_idx < len(rows)):
            return self._build_label()
        row = rows[row_idx]
        fields = row.get("fields") or []
        fld = fields[field_idx] if 0 <= field_idx < len(fields) else {"key": "", "size": None, "style": ""}

        self._title(f"行 {row_idx + 1} · 字段 {field_idx + 1}")

        combo = QComboBox()
        for key, name in FIELD_LABELS.items():
            combo.addItem(name, key)
        ci = combo.findData(fld.get("key"))
        combo.setCurrentIndex(ci if ci >= 0 else 0)
        combo.currentIndexChanged.connect(
            lambda _i: self.edit.emit({"op": "field_key", "row": row_idx, "field": field_idx,
                                       "value": combo.currentData()}))
        self._row("字段", combo)

        size = QSpinBox()
        size.setRange(4, 40)
        size.setValue(int(fld.get("size") or row.get("size") or 9))
        size.valueChanged.connect(
            lambda v: self.edit.emit({"op": "field_size", "row": row_idx, "field": field_idx, "value": v}))
        b = _btn("B", True); b.setChecked("bold" in (fld.get("style") or ""))
        i = _btn("I", True); i.setChecked("italic" in (fld.get("style") or ""))
        b.toggled.connect(lambda on: self.edit.emit({"op": "field_bold", "row": row_idx, "field": field_idx, "value": on}))
        i.toggled.connect(lambda on: self.edit.emit({"op": "field_italic", "row": row_idx, "field": field_idx, "value": on}))
        self._row("字号", size, b, i)

        al, ac, ar = _btn("左", True), _btn("中", True), _btn("右", True)
        cur = row.get("align") or "left"
        {"left": al, "center": ac, "right": ar}[cur].setChecked(True)
        al.clicked.connect(lambda: self.edit.emit({"op": "row_align", "row": row_idx, "value": "left"}))
        ac.clicked.connect(lambda: self.edit.emit({"op": "row_align", "row": row_idx, "value": "center"}))
        ar.clicked.connect(lambda: self.edit.emit({"op": "row_align", "row": row_idx, "value": "right"}))
        self._row("对齐", al, ac, ar)

        wrap = _btn("换行", True); wrap.setChecked(row.get("wrap") is not False)
        wrap.toggled.connect(lambda on: self.edit.emit({"op": "row_wrap", "row": row_idx, "value": on}))
        self._row(wrap)

        prefix = QLineEdit(row.get("prefix") or "")
        prefix.setPlaceholderText("前缀")
        prefix.textChanged.connect(lambda t: self.edit.emit({"op": "row_prefix", "row": row_idx, "value": t}))
        sep = QLineEdit(row.get("sep") if row.get("sep") is not None else " ")
        sep.setPlaceholderText("分隔")
        sep.textChanged.connect(lambda t: self.edit.emit({"op": "row_sep", "row": row_idx, "value": t}))
        self._row("前缀", prefix)
        self._row("分隔", sep)

        # per-row line-height override (0 / 继承 = inherit template + global)
        lh = QDoubleSpinBox(); lh.setRange(0.0, 3.0); lh.setSingleStep(0.1)
        lh.setSpecialValueText("继承")   # 0.0 shows as 继承
        lh.setValue(float(row.get("lineHeight") or 0.0))
        lh.valueChanged.connect(lambda v: self.edit.emit(
            {"op": "row_lineHeight", "row": row_idx,
             "value": None if v <= 0.0 else v}))
        self._row("行高", lh)

        # nudge
        left, up, down, right = _btn("←"), _btn("↑"), _btn("↓"), _btn("→")
        reset = _btn("归零")
        S = 0.5
        left.clicked.connect(lambda: self.edit.emit({"op": "field_nudge", "row": row_idx, "field": field_idx, "dx": -S, "dy": 0}))
        right.clicked.connect(lambda: self.edit.emit({"op": "field_nudge", "row": row_idx, "field": field_idx, "dx": S, "dy": 0}))
        up.clicked.connect(lambda: self.edit.emit({"op": "field_nudge", "row": row_idx, "field": field_idx, "dx": 0, "dy": -S}))
        down.clicked.connect(lambda: self.edit.emit({"op": "field_nudge", "row": row_idx, "field": field_idx, "dx": 0, "dy": S}))
        reset.clicked.connect(lambda: self.edit.emit({"op": "field_reset", "row": row_idx, "field": field_idx}))
        self._row("微移", left, up, down, right, reset)

        color_btn = QPushButton()
        color_btn.setFixedSize(60, 22)
        color_btn.setToolTip("字段文字颜色")
        self._update_color_btn(color_btn, fld.get("color") or "#000000")
        color_btn.clicked.connect(
            lambda _=False, btn=color_btn: self._pick_field_color(row_idx, field_idx, btn))
        self._row("字色", color_btn)

        addf = _btn("+加字段")
        delf = _btn("×删字段")
        addf.clicked.connect(lambda: self.edit.emit({"op": "field_add", "row": row_idx}))
        delf.clicked.connect(lambda: self.edit.emit({"op": "field_del", "row": row_idx, "field": field_idx}))
        self._row(addf, delf)

        dup, dele = _btn("复制本行"), _btn("删除本行")
        mvu, mvd = _btn("上移↑"), _btn("下移↓")
        dup.clicked.connect(lambda: self.edit.emit({"op": "row_dup", "row": row_idx}))
        dele.clicked.connect(lambda: self.edit.emit({"op": "row_del", "row": row_idx}))
        mvu.clicked.connect(lambda: self.edit.emit({"op": "row_move", "row": row_idx, "value": -1}))
        mvd.clicked.connect(lambda: self.edit.emit({"op": "row_move", "row": row_idx, "value": 1}))
        self._row(dup, dele)
        self._row(mvu, mvd)

    # ----- QR -----
    def _build_qr(self) -> None:
        qr = self._tmpl.get("qr") or {}
        self._title("二维码 QR")
        positions = [("left", "左"), ("right", "右"), ("top", "上"),
                     ("bottom", "下"), ("free", "自由"), ("none", "无")]
        cur = qr.get("position") or "right"
        h = QHBoxLayout(); h.setSpacing(4)
        for key, name in positions:
            b = _btn(name, True); b.setChecked(key == cur)
            b.clicked.connect(lambda _=False, k=key: self.edit.emit({"op": "qr_position", "value": k}))
            h.addWidget(b)
        h.addStretch()
        ww = QWidget(); ww.setLayout(h); self._root.addWidget(ww)

        size = QSlider(Qt.Orientation.Horizontal)
        size.setRange(20, 70)
        size.setValue(int(round(float(qr.get("sizePct") or 0.4) * 100)))
        size.valueChanged.connect(lambda v: self.edit.emit({"op": "qr_size", "value": v / 100.0}))
        self._row("大小", size)

        content = QComboBox()
        for key, name in FIELD_LABELS.items():
            content.addItem(name, key)
        ci = content.findData(qr.get("content") or "uniqueId")
        content.setCurrentIndex(ci if ci >= 0 else 0)
        content.currentIndexChanged.connect(
            lambda _i: self.edit.emit({"op": "qr_content", "value": content.currentData()}))
        self._row("内容", content)

        cure = qr.get("ecc") or "Q"
        h2 = QHBoxLayout(); h2.setSpacing(4)
        lbl = QLabel("容错"); lbl.setStyleSheet("color:#87a2a1; font-size:12px;"); h2.addWidget(lbl)
        for lv in ("L", "M", "Q", "H"):
            b = _btn(lv, True); b.setChecked(lv == cure)
            b.clicked.connect(lambda _=False, v=lv: self.edit.emit({"op": "qr_ecc", "value": v}))
            h2.addWidget(b)
        h2.addStretch()
        ww2 = QWidget(); ww2.setLayout(h2); self._root.addWidget(ww2)
        hint = QLabel("提示：选「自由」后可在画布上拖动 QR 到任意位置。")
        hint.setStyleSheet("color:#5f7d7a; font-size:11px;")
        hint.setWordWrap(True)
        self._root.addWidget(hint)

    # ----- label level -----
    def _build_label(self) -> None:
        self._title("标签")
        lh = QSlider(Qt.Orientation.Horizontal)
        lh.setRange(80, 250)
        lh.setValue(int(round(float(self._tmpl.get("lineHeight") or 1.3) * 100)))
        lh.valueChanged.connect(lambda v: self.edit.emit({"op": "line_height", "value": v / 100.0}))
        self._row("全局行高", lh)

        # Shape selector
        _SHAPE_KEYS = ["rect", "circle", "roundrect"]
        _SHAPE_NAMES = ["矩形", "圆形", "圆角矩形"]
        shape_combo = QComboBox()
        for n in _SHAPE_NAMES:
            shape_combo.addItem(n)
        cur_shape = (self._tmpl.get("shape") or "rect").lower()
        shape_combo.setCurrentIndex(_SHAPE_KEYS.index(cur_shape) if cur_shape in _SHAPE_KEYS else 0)
        shape_combo.currentIndexChanged.connect(
            lambda i: self.edit.emit({"op": "tmpl_shape", "value": _SHAPE_KEYS[i]}))
        self._row("形状", shape_combo)

        # Background color
        bg_btn = QPushButton()
        bg_btn.setFixedSize(60, 22)
        bg_btn.setToolTip("标签背景色")
        self._update_color_btn(bg_btn, self._tmpl.get("bgColor") or "#ffffff")
        bg_btn.clicked.connect(
            lambda _=False, btn=bg_btn: self._pick_tmpl_color("bgColor", btn))
        self._row("背景色", bg_btn)

        # Corner radius (visible for rect/roundrect)
        corner_spin = QDoubleSpinBox()
        corner_spin.setRange(0.0, 10.0)
        corner_spin.setSingleStep(0.5)
        corner_spin.setSuffix(" mm")
        corner_spin.setValue(float(self._tmpl.get("cornerRadius") or 0.0))
        corner_spin.valueChanged.connect(
            lambda v: self.edit.emit({"op": "tmpl_cornerRadius", "value": round(v, 2)}))
        self._row("圆角", corner_spin)

        # Label dimensions (mm) — designer-editable; persisted as a custom size
        dims = getattr(self, "_dims", None) or {"w": 60, "h": 40}
        w_spin = QDoubleSpinBox(); w_spin.setRange(5.0, 300.0)
        w_spin.setSingleStep(1.0); w_spin.setSuffix(" mm")
        w_spin.setValue(float(dims.get("w", 60)))
        h_spin = QDoubleSpinBox(); h_spin.setRange(5.0, 300.0)
        h_spin.setSingleStep(1.0); h_spin.setSuffix(" mm")
        h_spin.setValue(float(dims.get("h", 40)))
        w_spin.valueChanged.connect(lambda v: self.edit.emit(
            {"op": "dims", "w": v, "h": h_spin.value()}))
        h_spin.valueChanged.connect(lambda v: self.edit.emit(
            {"op": "dims", "w": w_spin.value(), "h": v}))
        self._row("标签宽", w_spin)
        self._row("标签高", h_spin)

        dims_lbl = QLabel("点击画布上的文字/图形/QR 进行编辑；顶部「+元素」可加文字/图形/图片/条码。"
                          "选中元素后拖角缩放、拖动自动吸附对齐。")
        dims_lbl.setStyleSheet("color:#5f7d7a; font-size:12px;")
        dims_lbl.setWordWrap(True)
        self._root.addWidget(dims_lbl)

    # ----- color helpers -----
    def _update_color_btn(self, btn: QPushButton, color_str: str) -> None:
        c = QColor(color_str or "#ffffff")
        fg = "#000000" if c.lightness() > 128 else "#ffffff"
        btn.setStyleSheet(
            f"background:{c.name()};color:{fg};border:1px solid #888;border-radius:3px;")
        btn.setText(c.name())

    def _pick_tmpl_color(self, field: str, btn: QPushButton) -> None:
        c = QColorDialog.getColor(QColor(self._tmpl.get(field) or "#ffffff"), btn.window())
        if c.isValid():
            self._tmpl[field] = c.name()
            self._update_color_btn(btn, c.name())
            self.edit.emit({"op": f"tmpl_{field}", "value": c.name()})

    def _pick_field_color(self, row_idx: int, field_idx: int, btn: QPushButton) -> None:
        rows = self._tmpl.get("rows") or []
        if 0 <= row_idx < len(rows):
            flds = rows[row_idx].get("fields") or []
            cur = (flds[field_idx].get("color") if 0 <= field_idx < len(flds)
                   and isinstance(flds[field_idx], dict) else None) or "#000000"
        else:
            cur = "#000000"
        c = QColorDialog.getColor(QColor(cur), btn.window())
        if c.isValid():
            self._update_color_btn(btn, c.name())
            self.edit.emit({"op": "field_color", "row": row_idx, "field": field_idx,
                            "value": c.name()})


# ── Dialog ──────────────────────────────────────────────────────────────────────

class _FloatingToolbar(QWidget):
    """Compact quick-format bar that floats over a selected text/field element.

    Mirrors the web designer's floating toolbar (app.js:16279-16449): font size,
    bold/italic, alignment, colour — applied to the element under the cursor."""

    size_delta = pyqtSignal(int)      # font-size step (±)
    bold_toggled = pyqtSignal()
    italic_toggled = pyqtSignal()
    align_set = pyqtSignal(str)       # "left" | "center" | "right"
    color_pick = pyqtSignal()
    z_delta = pyqtSignal(int)         # raise / lower one layer

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._index = -1
        self.setStyleSheet(
            "background:#13303a; border:1px solid #29b9ab; border-radius:6px;")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(4, 2, 4, 2); lay.setSpacing(3)

        def mk(txt, tip, slot, w=26):
            b = _btn(txt); b.setToolTip(tip); b.setFixedWidth(w)
            b.clicked.connect(slot)
            lay.addWidget(b)
            return b
        mk("A−", "缩小字号", lambda: self.size_delta.emit(-1))
        mk("A＋", "放大字号", lambda: self.size_delta.emit(1))
        mk("B", "加粗", self.bold_toggled.emit)
        mk("I", "斜体", self.italic_toggled.emit)
        mk("⇤", "左对齐", lambda: self.align_set.emit("left"))
        mk("⇆", "居中", lambda: self.align_set.emit("center"))
        mk("⇥", "右对齐", lambda: self.align_set.emit("right"))
        mk("🎨", "颜色", self.color_pick.emit)
        mk("↑", "上移一层", lambda: self.z_delta.emit(1))
        mk("↓", "下移一层", lambda: self.z_delta.emit(-1))
        self.hide()

    def target_index(self) -> int:
        return self._index

    def show_for(self, index: int, rect) -> None:
        self._index = index
        self.adjustSize()
        if rect is not None and self.parent() is not None:
            x = max(0, rect.x())
            y = rect.y() - self.height() - 4
            if y < 0:
                y = rect.y() + rect.height() + 4
            self.move(x, y)
        self.show()
        self.raise_()

    def hide_bar(self) -> None:
        self._index = -1
        self.hide()


class LabelDesignerDialog(QDialog):
    """Full free-form label designer."""

    def __init__(
        self,
        template: Optional[dict],
        dims: Optional[dict],
        label_data: Optional[dict],
        library: Optional[LabelTemplateLibrary] = None,
        title: str = "标签设计器",
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumSize(940, 640)
        self.setStyleSheet("background:#0c1e26; color:#eef3ef;")
        self._tmpl = normalize_template(template)
        self._dims = dims or {"w": 60, "h": 40}
        self._data = label_data or {}
        self._lib = library
        self._undo: list = []
        self._redo: list = []
        self._multi: set = set()       # extra element indices for group ops
        self._clipboard: list = []     # copied elements (normalized dicts)
        self._inline_editor = None     # QLineEdit overlay during in-place edit
        self._inline_index = -1
        self._drag_baseline: Optional[tuple] = None
        self._selected_key: Optional[str] = None  # chosen library key on accept
        self._setup_ui()
        self._refresh()

    def _setup_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(8)

        # Toolbar
        bar = QHBoxLayout(); bar.setSpacing(6)
        add_field = _btn("+加字段 ▾")
        fmenu = QMenu(add_field)
        for key, name in FIELD_LABELS.items():
            fmenu.addAction(name, lambda _=False, k=key: self._add_row_with_field(k))
        add_field.setMenu(fmenu)
        add_row = _btn("+加行")
        add_row.clicked.connect(lambda: self._add_row_with_field("headerId"))
        qr_btn = _btn("QR 设置")
        qr_btn.clicked.connect(lambda: self._select("qr", -1, -1))

        add_el = _btn("+元素 ▾")
        emenu = QMenu(add_el)
        for etype, name in ELEMENT_TYPE_LABELS.items():
            emenu.addAction(name, lambda _=False, t=etype: self._add_element(t))
        add_el.setMenu(emenu)

        presets_btn = _btn("模板库 ▾")
        pmenu = QMenu(presets_btn)
        from app.services.label_presets import STARTER_PRESETS
        for pid, preset in STARTER_PRESETS.items():
            pmenu.addAction(preset.get("name") or pid,
                            lambda _=False, p=preset: self._apply_preset(p))
        presets_btn.setMenu(pmenu)

        self._guide_btn = _btn("辅助线", True)
        self._guide_btn.toggled.connect(self._toggle_guide_overlay)
        self._clear_guides_btn = _btn("清参考线")
        self._clear_guides_btn.setToolTip("从标尺拖出参考线；点此清除全部参考线")
        self._clear_guides_btn.clicked.connect(lambda: self._canvas.clear_user_guides())

        self._undo_btn = _btn("↶ 撤销")
        self._redo_btn = _btn("↷ 重做")
        self._undo_btn.clicked.connect(self._do_undo)
        self._redo_btn.clicked.connect(self._do_redo)
        saveas = _btn("另存为 ▾")
        smenu = QMenu(saveas)
        smenu.addAction("另存为新模板…", self._save_as_new)
        if self._lib is not None:
            smenu.addAction("重命名当前自定义…", self._rename_current)
            smenu.addAction("复制当前自定义", self._duplicate_current)
            smenu.addAction("删除当前自定义", self._delete_current)
        saveas.setMenu(smenu)
        for w in (add_field, add_row, add_el, qr_btn, presets_btn,
                  self._guide_btn, self._clear_guides_btn):
            bar.addWidget(w)
        bar.addStretch()
        bar.addWidget(self._undo_btn)
        bar.addWidget(self._redo_btn)
        bar.addWidget(saveas)
        root.addLayout(bar)

        # Align / distribute toolbar (operates on the group selection, else the
        # single element relative to the whole label). Mirrors pro design tools.
        abar = QHBoxLayout(); abar.setSpacing(4)
        abar.addWidget(QLabel("对齐"))
        for mode, txt, tip in (
            ("left", "⇤", "左对齐"), ("hcenter", "⇆", "水平居中"), ("right", "⇥", "右对齐"),
            ("top", "⤒", "顶对齐"), ("vcenter", "⇕", "垂直居中"), ("bottom", "⤓", "底对齐"),
        ):
            b = _btn(txt); b.setToolTip(tip); b.setFixedWidth(34)
            b.clicked.connect(lambda _=False, m=mode: self._align_elements(m))
            abar.addWidget(b)
        abar.addSpacing(10)
        abar.addWidget(QLabel("分布"))
        for axis, txt, tip in (("h", "↔", "水平等距分布"), ("v", "↕", "垂直等距分布")):
            b = _btn(txt); b.setToolTip(tip); b.setFixedWidth(34)
            b.clicked.connect(lambda _=False, a=axis: self._distribute_elements(a))
            abar.addWidget(b)
        abar.addSpacing(10)
        copy_b = _btn("复制"); copy_b.setToolTip("复制所选元素 (Ctrl+C)")
        copy_b.clicked.connect(self._copy_selection)
        paste_b = _btn("粘贴"); paste_b.setToolTip("粘贴元素 (Ctrl+V)")
        paste_b.clicked.connect(self._paste_clipboard)
        del_b = _btn("删除"); del_b.setToolTip("删除所选元素 (Del)")
        del_b.clicked.connect(self._delete_selection)
        for w in (copy_b, paste_b, del_b):
            abar.addWidget(w)
        abar.addStretch()
        root.addLayout(abar)

        from PyQt6.QtGui import QShortcut, QKeySequence
        QShortcut(QKeySequence.StandardKey.Copy, self, activated=self._copy_selection)
        QShortcut(QKeySequence.StandardKey.Paste, self, activated=self._paste_clipboard)
        QShortcut(QKeySequence("Ctrl+D"), self,
                  activated=lambda: (self._copy_selection(), self._paste_clipboard()))

        # Canvas | property panel
        self._canvas = _DesignCanvas()
        self._panel = _PropertyPanel()
        self._canvas.selected.connect(self._select)
        self._canvas.drag_started.connect(self._on_drag_start)
        self._canvas.dragged.connect(self._on_dragged)
        self._canvas.nudged.connect(self._on_nudged)
        self._canvas.element_resized.connect(self._on_element_resized)
        self._canvas.multi_toggle.connect(self._toggle_multi)
        self._canvas.marquee.connect(self._marquee_select)
        self._canvas.delete_pressed.connect(self._delete_selection)
        self._canvas.edit_requested.connect(self._begin_inline_edit)
        self._panel.edit.connect(self._apply_edit)

        # Floating quick-format toolbar (over the canvas, for text/field elements)
        self._float_bar = _FloatingToolbar(self._canvas)
        self._float_bar.size_delta.connect(self._float_size_delta)
        self._float_bar.bold_toggled.connect(lambda: self._float_style("bold"))
        self._float_bar.italic_toggled.connect(lambda: self._float_style("italic"))
        self._float_bar.align_set.connect(self._float_align)
        self._float_bar.color_pick.connect(self._float_color)
        self._float_bar.z_delta.connect(self._float_z)

        split = QSplitter(Qt.Orientation.Horizontal)
        split.addWidget(self._canvas)
        split.addWidget(self._panel)
        split.setStretchFactor(0, 6)
        split.setStretchFactor(1, 4)
        split.setSizes([580, 340])
        root.addWidget(split, stretch=1)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("确定")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

        self._sel = ("none", -1, -1)

    # ── State / undo ────────────────────────────────────────────────────────
    def _push_undo(self) -> None:
        self._undo.append(copy.deepcopy(self._tmpl))
        self._redo.clear()
        self._undo_btn.setEnabled(True)
        self._redo_btn.setEnabled(False)

    def _do_undo(self) -> None:
        if not self._undo:
            return
        self._redo.append(copy.deepcopy(self._tmpl))
        self._tmpl = self._undo.pop()
        self._undo_btn.setEnabled(bool(self._undo))
        self._redo_btn.setEnabled(True)
        self._refresh()

    def _do_redo(self) -> None:
        if not self._redo:
            return
        self._undo.append(copy.deepcopy(self._tmpl))
        self._tmpl = self._redo.pop()
        self._redo_btn.setEnabled(bool(self._redo))
        self._undo_btn.setEnabled(True)
        self._refresh()

    def _refresh(self) -> None:
        self._tmpl = normalize_template(self._tmpl)
        self._canvas.set_content(self._tmpl, self._dims, self._data)
        kind, row, field = self._sel
        self._canvas.set_selection(kind, row, field)
        self._canvas.set_multi(getattr(self, "_multi", set()))
        self._panel._dims = self._dims
        self._panel.show_for(kind, row, field, self._tmpl)
        self._sync_float_bar()

    def _select(self, kind: str, row: int, field: int) -> None:
        self._sel = (kind, row, field)
        self._multi = set()            # a plain click resets the group selection
        self._canvas.set_selection(kind, row, field)
        self._canvas.set_multi(self._multi)
        self._panel._dims = self._dims
        self._panel.show_for(kind, row, field, self._tmpl)
        self._sync_float_bar()

    # ── Floating quick-format toolbar (Phase 2) ───────────────────────────────
    def _sync_float_bar(self) -> None:
        bar = getattr(self, "_float_bar", None)
        if bar is None:
            return
        kind, _row, field = self._sel
        el = self._element_at(field) if kind == "element" else None
        if el is not None and el.get("type") in ("text", "field") and not self._multi:
            bar.show_for(field, self._canvas.element_screen_rect(field))
        else:
            bar.hide_bar()

    def _float_size_delta(self, d: int) -> None:
        i = self._float_bar.target_index()
        el = self._element_at(i)
        if el is None:
            return
        cur = float(el.get("size") or 9)
        self._apply_edit({"op": "element_size", "index": i, "value": max(4.0, cur + d)})

    def _float_style(self, flag: str) -> None:
        i = self._float_bar.target_index()
        el = self._element_at(i)
        if el is None:
            return
        on = flag not in (el.get("style") or "")
        self._apply_edit({"op": f"element_{flag}", "index": i, "value": on})

    def _float_align(self, a: str) -> None:
        i = self._float_bar.target_index()
        if self._element_at(i) is not None:
            self._apply_edit({"op": "element_align", "index": i, "value": a})

    def _float_color(self) -> None:
        i = self._float_bar.target_index()
        el = self._element_at(i)
        if el is None:
            return
        col = QColorDialog.getColor(QColor(el.get("color") or "#111111"), self, "文字颜色")
        if col.isValid():
            self._apply_edit({"op": "element_color", "index": i, "value": col.name()})

    def _float_z(self, d: int) -> None:
        i = self._float_bar.target_index()
        if self._element_at(i) is not None:
            self._apply_edit({"op": "element_z", "index": i, "value": d})

    # ── Multi-selection (Phase 1) ─────────────────────────────────────────────
    def _toggle_multi(self, index: int) -> None:
        """Ctrl-click: add/remove an element index from the group selection."""
        els = self._elements()
        if not (0 <= index < len(els)):
            return
        self._multi ^= {index}
        if index in self._multi:
            self._sel = ("element", -1, index)
        elif self._multi:
            self._sel = ("element", -1, min(self._multi))
        else:
            self._sel = ("none", -1, -1)
        self._canvas.set_selection(*self._sel)
        self._canvas.set_multi(self._multi)
        self._panel.show_for(*self._sel, self._tmpl)

    def _marquee_select(self, x: float, y: float, w: float, h: float) -> None:
        """Box-select every element whose bounding box intersects the rect."""
        rx2, ry2 = x + w, y + h
        hit = set()
        for i, el in enumerate(self._elements()):
            bx, by, bw, bh = self._el_bbox(el)
            if bx < rx2 and bx + bw > x and by < ry2 and by + bh > y:
                hit.add(i)
        self._multi = hit
        anchor = min(hit) if hit else -1
        self._sel = ("element", -1, anchor) if anchor >= 0 else ("none", -1, -1)
        self._canvas.set_selection(*self._sel)
        self._canvas.set_multi(self._multi)
        self._panel.show_for(*self._sel, self._tmpl)

    # ── In-place text editing (Phase 2) ───────────────────────────────────────
    def _begin_inline_edit(self, index: int) -> None:
        """Open a QLineEdit over a text element to edit its text in place."""
        el = self._element_at(index)
        if el is None or el.get("type") != "text":
            return
        self._commit_inline_edit()   # close any prior editor first
        rect = self._canvas.element_screen_rect(index)
        editor = QLineEdit(self._canvas)
        editor.setText(str(el.get("text") or ""))
        if rect is not None:
            editor.setGeometry(rect.adjusted(-1, -1, 1, 1))
        editor.setStyleSheet(
            "QLineEdit { background:#fffbe6; color:#111; border:1px solid #29b9ab; }")
        editor.returnPressed.connect(self._commit_inline_edit)
        editor.editingFinished.connect(self._commit_inline_edit)
        self._inline_editor = editor
        self._inline_index = index
        editor.show()
        editor.setFocus()
        editor.selectAll()

    def _commit_inline_edit(self) -> None:
        editor = self._inline_editor
        if editor is None:
            return
        self._inline_editor = None     # guard re-entrancy from editingFinished
        idx = self._inline_index
        self._inline_index = -1
        el = self._element_at(idx)
        text = editor.text()
        editor.deleteLater()
        if el is not None and el.get("type") == "text" and text != el.get("text"):
            self._push_undo()
            el["text"] = text
            self._sel = ("element", -1, idx)
            self._refresh()

    def _copy_selection(self) -> None:
        """Copy the selected elements (deep) into the designer clipboard."""
        els = self._elements()
        idx = self._selected_element_indices()
        self._clipboard = [copy.deepcopy(els[i]) for i in idx if 0 <= i < len(els)]

    def _paste_clipboard(self) -> None:
        """Paste clipboard elements offset by +2mm; select them as the group."""
        if not self._clipboard:
            return
        self._push_undo()
        els = self._elements()
        new_idx = set()
        for src in self._clipboard:
            el = copy.deepcopy(src)
            if el.get("type") == "line":
                for k in ("x1", "y1", "x2", "y2"):
                    el[k] = round(float(el.get(k) or 0) + 2.0, 2)
            else:
                el["x"] = round(float(el.get("x") or 0) + 2.0, 2)
                el["y"] = round(float(el.get("y") or 0) + 2.0, 2)
            els.append(el)
            new_idx.add(len(els) - 1)
        self._multi = new_idx
        self._sel = ("element", -1, min(new_idx))
        self._refresh()

    def _delete_selection(self) -> None:
        """Delete every element in the group selection (or the single anchor)."""
        idx = set(self._selected_element_indices())
        if not idx:
            return
        self._push_undo()
        els = self._elements()
        self._tmpl["elements"] = [el for i, el in enumerate(els) if i not in idx]
        self._multi = set()
        self._sel = ("none", -1, -1)
        self._refresh()

    # ── Presets / guides ──────────────────────────────────────────────────────
    def _apply_preset(self, preset: dict) -> None:
        self._push_undo()
        self._tmpl = normalize_template(copy.deepcopy(preset))
        self._sel = ("none", -1, -1)
        self._refresh()

    def _toggle_guide_overlay(self, on: bool) -> None:
        self._canvas.set_guide_overlay(on)

    def edited_dims(self) -> dict:
        """The (possibly designer-edited) label dimensions in mm."""
        return dict(self._dims)

    # ── Drag / nudge ──────────────────────────────────────────────────────────
    def _on_drag_start(self) -> None:
        self._push_undo()
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
            self._refresh()
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
        self._refresh()

    def _on_element_resized(self, index: int, x: float, y: float, w: float, h: float) -> None:
        el = self._element_at(index)
        if el is None:
            return
        nx, ny, guides = self._canvas.snap(x, y, w, h, skip_index=index)
        el["x"], el["y"], el["w"], el["h"] = round(nx, 2), round(ny, 2), round(w, 2), round(h, 2)
        self._canvas.set_guides(guides)
        self._refresh()

    def _on_nudged(self, dx_mm: float, dy_mm: float) -> None:
        kind, row, field = self._sel
        if kind not in ("field", "qr", "element"):
            return
        self._push_undo()
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
            self._on_drag_start()  # captures baseline + pushes undo again (harmless)
            self._on_dragged(dx_mm, dy_mm)
            return
        self._refresh()

    # ── Edits from property panel ─────────────────────────────────────────────
    def _apply_edit(self, ch: dict) -> None:
        op = ch.get("op")
        self._push_undo()
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
            self._tmpl["qr"]["position"] = ch["value"]
        elif op == "qr_size":
            self._tmpl["qr"]["sizePct"] = ch["value"]
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
        elif op == "field_color" and 0 <= r < len(rows):
            f = rows[r]["fields"][fi]
            if isinstance(f, dict):
                f["color"] = ch["value"]
        elif op and op.startswith("element_"):
            self._apply_element_edit(op, ch)
        elif op == "dims":
            self._dims = {"w": float(ch.get("w") or self._dims.get("w", 60)),
                          "h": float(ch.get("h") or self._dims.get("h", 40))}
        self._refresh()

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
            el["w"] = round(max(MIN_EL_MM, float(ch.get("w", el.get("w", MIN_EL_MM)))), 2)
            el["h"] = round(max(MIN_EL_MM, float(ch.get("h", el.get("h", MIN_EL_MM)))), 2)
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
            bx, by, _, _ = LabelDesignerDialog._el_bbox(el)
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
        self._push_undo()
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
        self._refresh()

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
        self._push_undo()
        cursor = start
        for i in order:
            b = boxes[i]
            if axis == "h":
                self._el_move_to(els[i], cursor, b[1])
            else:
                self._el_move_to(els[i], b[0], cursor)
            cursor += b[s] + gap
        self._refresh()

    def _add_element(self, etype: str) -> None:
        self._push_undo()
        els = self._elements()
        els.append(_default_element(etype, self._dims))
        self._sel = ("element", -1, len(els) - 1)
        self._refresh()

    def _add_row_with_field(self, key: str) -> None:
        self._push_undo()
        self._tmpl["rows"].append({
            "fields": [{"key": key, "style": "", "size": None, "offsetX": 0, "offsetY": 0}],
            "size": 9, "style": "", "align": "left", "wrap": True,
        })
        self._sel = ("field", len(self._tmpl["rows"]) - 1, 0)
        self._refresh()

    # ── Template library management ────────────────────────────────────────────
    def _save_as_new(self) -> None:
        name, ok = QInputDialog.getText(self, "另存为新模板", "模板名称:")
        if not ok or not name.strip():
            return
        if self._lib is not None:
            rec = self._lib.upsert({"name": name.strip(), "template": copy.deepcopy(self._tmpl)})
            self._lib.set_selected_key(key_from_id(rec["id"]))
            self._selected_key = key_from_id(rec["id"])
        QMessageBox.information(self, "已保存", f"已保存模板「{name.strip()}」。")

    def _current_custom_id(self) -> Optional[str]:
        if self._lib is None:
            return None
        key = self._lib.selected_key()
        return id_from_key(key) if is_library_key(key) else None

    def _rename_current(self) -> None:
        cid = self._current_custom_id()
        if not cid:
            QMessageBox.information(self, "重命名", "当前是内置模板，先「另存为新模板」。")
            return
        name, ok = QInputDialog.getText(self, "重命名", "新名称:")
        if ok and name.strip():
            self._lib.rename(cid, name.strip())

    def _duplicate_current(self) -> None:
        cid = self._current_custom_id()
        if not cid:
            QMessageBox.information(self, "复制", "当前是内置模板，先「另存为新模板」。")
            return
        rec = self._lib.duplicate(cid)
        if rec:
            self._lib.set_selected_key(key_from_id(rec["id"]))
            self._selected_key = key_from_id(rec["id"])

    def _delete_current(self) -> None:
        cid = self._current_custom_id()
        if not cid:
            QMessageBox.information(self, "删除", "内置模板不可删除。")
            return
        if QMessageBox.question(self, "删除", "确定删除当前自定义模板？") == QMessageBox.StandardButton.Yes:
            self._lib.delete(cid)
            self._selected_key = None

    # ── Result ─────────────────────────────────────────────────────────────────
    def edited_template(self) -> dict:
        return copy.deepcopy(self._tmpl)

    def selected_key(self) -> Optional[str]:
        """Library key chosen via 另存为 (or None — caller decides how to persist)."""
        return self._selected_key
