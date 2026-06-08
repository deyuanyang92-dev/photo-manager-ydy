"""label_render.py — the ONE label renderer (canvas == preview == print).

Historically the preview (`_render_label_pixmap`) and the printer paint path
(`_paint_one_label`) carried two divergent copies of the label-drawing logic,
and neither honored `row.align`, per-field `size`/`style`/`offsetX/Y`,
`row.wrap`, or a free-positioned QR.  The WYSIWYG editor could therefore show
edits that never reached the printout.

`render_label_onto` is the single source of truth.  Preview, grid thumbnails,
the printer, and the designer canvas all draw through it, so what you arrange is
what prints.

Font sizing follows the proven preview formula (px = max(7, size) * px_per_mm /
3.78, where 3.78 px/mm = 96 dpi).  Driving the printer through the SAME pixel
formula — instead of `setPointSizeF` — makes the printout match the preview
exactly (and avoids the historic dpi double-scaling blow-up).

Backward compatible: a plain single-field, left-aligned, zero-offset row renders
identically to the old code, so existing builtin templates are unchanged.
"""
from __future__ import annotations

import base64
import io
from typing import Optional

import math

from PyQt6.QtCore import Qt, QRectF
from PyQt6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QFontMetricsF,
    QImage,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)

from app.utils.label_core import qr_metrics, resolve_line_height, resolve_wrap


def _generate_qr_pixmap(text: str, size_px: int, ecc: str = "Q") -> Optional[QPixmap]:
    """Generate a QR code QPixmap.

    Uses the ``qrcode`` library with error-correction level Q (25 % recovery).
    Returns None on import error (soft degradation when qrcode not installed).

    Lives here (the renderer) so the non-UI render path no longer imports from
    the widget layer.  ``app.widgets.label_editor`` re-exports this name for
    backward compatibility.
    """
    try:
        import qrcode  # type: ignore
        from qrcode.constants import (  # type: ignore
            ERROR_CORRECT_L,
            ERROR_CORRECT_M,
            ERROR_CORRECT_Q,
            ERROR_CORRECT_H,
        )
        ecc_map = {
            "L": ERROR_CORRECT_L,
            "M": ERROR_CORRECT_M,
            "Q": ERROR_CORRECT_Q,
            "H": ERROR_CORRECT_H,
        }
        qr = qrcode.QRCode(
            error_correction=ecc_map.get(ecc.upper(), ERROR_CORRECT_Q),
            box_size=max(1, size_px // 21),
            border=0,
        )
        qr.add_data(text or "")
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        buf.seek(0)
        qimage = QImage.fromData(buf.read())
        if qimage.isNull():
            return None
        pixmap = QPixmap.fromImage(qimage)
        return pixmap.scaled(size_px, size_px, Qt.AspectRatioMode.KeepAspectRatio,
                             Qt.TransformationMode.SmoothTransformation)
    except Exception:
        return None

# px-per-mm at 96 dpi — the reference the template `size` values were authored
# against.  Keep at 3.78 (not the exact 3.7795) to stay byte-identical with the
# previous preview output.
_PX_PER_MM_96 = 3.78
_PAD_MM = 2.0  # inner padding (matches the old 2*scale left/edge inset)
_GAP_MM = 1.0  # gap between QR and text when QR is on the left/right


def _seg_font(eff_size: float, style: str, px_per_mm: float,
              family: str = "") -> QFont:
    font = QFont()
    if family:
        font.setFamily(family)  # Qt falls back gracefully if unavailable
    px = max(1, round(max(7.0, float(eff_size)) * px_per_mm / _PX_PER_MM_96))
    font.setPixelSize(px)
    font.setBold("bold" in (style or ""))
    font.setItalic("italic" in (style or ""))
    return font


# Stroke dash styles for free-form line/rect/ellipse elements. ``solid`` (the
# default) returns None so the caller leaves the pen untouched → byte-identical
# to the pre-dash renderer.
_DASH_STYLES = {
    "dash": Qt.PenStyle.DashLine,
    "dot": Qt.PenStyle.DotLine,
    "dashdot": Qt.PenStyle.DashDotLine,
}


def _apply_dash(pen: QPen, el: dict) -> None:
    """Apply a dash pattern to *pen* in place, if the element requests one."""
    style = _DASH_STYLES.get(el.get("dash") or "solid")
    if style is not None:
        pen.setStyle(style)


def _draw_arrowhead(painter: QPainter, tip_x: float, tip_y: float,
                    from_x: float, from_y: float, color: QColor,
                    width_px: float) -> None:
    """Fill a triangular arrowhead at (tip), pointing away from (from)."""
    dx, dy = tip_x - from_x, tip_y - from_y
    dist = math.hypot(dx, dy)
    if dist < 1e-6:
        return
    ux, uy = dx / dist, dy / dist          # unit vector along the line
    size = max(3.0, width_px * 3.5)        # arrowhead length in px
    half = size * 0.5                      # half base width
    bx, by = tip_x - ux * size, tip_y - uy * size  # base centre
    px, py = -uy, ux                       # perpendicular
    path = QPainterPath()
    path.moveTo(tip_x, tip_y)
    path.lineTo(bx + px * half, by + py * half)
    path.lineTo(bx - px * half, by - py * half)
    path.closeSubpath()
    painter.save()
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QBrush(color))
    painter.drawPath(path)
    painter.restore()


def _make_gradient_brush(grad: dict, rect: QRectF, mono: bool = False):
    """Build a QBrush from a 2+-stop linear gradient dict, or None if invalid.

    ``grad`` = ``{"type":"linear","angle":<deg>,"stops":[[color,pos],...]}``.
    In *mono* mode the gradient collapses to its first stop (solid) for clean
    B&W laser output.
    """
    stops = grad.get("stops") if isinstance(grad, dict) else None
    if not isinstance(stops, list) or not stops:
        return None
    if mono:
        first = stops[0]
        return QBrush(QColor(first[0] if isinstance(first, (list, tuple)) else first))
    angle = math.radians(float(grad.get("angle", 0) or 0))
    # endpoints across the rect along the angle direction
    cx, cy = rect.center().x(), rect.center().y()
    hx, hy = math.cos(angle) * rect.width() / 2.0, math.sin(angle) * rect.height() / 2.0
    g = QLinearGradient(cx - hx, cy - hy, cx + hx, cy + hy)
    for stop in stops:
        if isinstance(stop, (list, tuple)) and len(stop) >= 2:
            g.setColorAt(min(1.0, max(0.0, float(stop[1] or 0))), QColor(stop[0]))
    return QBrush(g)


def _draw_shape_shadow(painter: QPainter, rect: QRectF, shadow: dict,
                       ppm: float, is_ellipse: bool, corner_px: float = 0.0) -> None:
    """Draw an offset filled silhouette behind a rect/ellipse (v1: no blur)."""
    if not isinstance(shadow, dict):
        return
    dx = float(shadow.get("dx", 0) or 0) * ppm
    dy = float(shadow.get("dy", 0) or 0) * ppm
    if dx == 0 and dy == 0:
        return
    off = QRectF(rect.x() + dx, rect.y() + dy, rect.width(), rect.height())
    painter.save()
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QBrush(QColor(shadow.get("color") or "#808080")))
    if is_ellipse:
        painter.drawEllipse(off)
    elif corner_px > 0:
        painter.drawRoundedRect(off, corner_px, corner_px)
    else:
        painter.drawRect(off)
    painter.restore()


def render_label_onto(
    painter: QPainter,
    tmpl: dict,
    dims: dict,
    data: dict,
    *,
    px_per_mm: float,
    x_off: float = 0.0,
    y_off: float = 0.0,
    placeholder: bool = True,
    fill_bg: bool = True,
    hit_boxes: Optional[list] = None,
) -> None:
    """Render one label onto *painter* at device offset (x_off, y_off).

    If *hit_boxes* is given, each drawn element's device-pixel rectangle is
    appended as ``{"kind": "field"|"qr", "row": int, "field": int, "x","y","w","h"}``
    — the designer canvas uses these for click-selection / drag, guaranteeing the
    interactive handles line up exactly with the rendered pixels.

    Parameters
    ----------
    painter     : an active QPainter (on a QPixmap, QPrinter page, or scene).
    tmpl        : a (normalized or raw) label template dict.
    dims        : ``{"w": mm, "h": mm}``.
    data        : flat label-data dict (``specimen_to_label_data`` output).
    px_per_mm   : device scale — for a pixmap this is the chosen px/mm; for a
                  printer it is ``dpi / 25.4``.
    x_off,y_off : top-left of this label on the device, in device pixels/dots.
    placeholder : when True, a fully-empty row renders "—" (template stays
                  visible without specimen data); when False (printing) the row
                  is skipped.
    fill_bg     : paint a white background rectangle first.
    """
    ppm = float(px_per_mm)
    w_mm = float(dims.get("w", 60))
    h_mm = float(dims.get("h", 40))
    w_px = w_mm * ppm
    h_px = h_mm * ppm

    # ── Shape / background / clip ─────────────────────────────────────────
    shape = (tmpl.get("shape") or "rect").lower()
    bg = QColor(tmpl.get("bgColor") or "#ffffff")
    corner_r_px = float(tmpl.get("cornerRadius") or 0.0) * ppm
    label_rect = QRectF(float(x_off), float(y_off), w_px, h_px)

    if fill_bg:
        if shape == "circle":
            circle_path = QPainterPath()
            circle_path.addEllipse(label_rect)
            painter.fillPath(circle_path, QBrush(bg))
            old_pen = painter.pen()
            painter.setPen(QPen(QColor("#aaaaaa"), 1.0))
            border_rect = QRectF(float(x_off) + 0.5, float(y_off) + 0.5, w_px - 1.0, h_px - 1.0)
            painter.drawEllipse(border_rect)
            painter.setPen(old_pen)
        elif corner_r_px > 0:
            rr_path = QPainterPath()
            rr_path.addRoundedRect(label_rect, corner_r_px, corner_r_px)
            painter.fillPath(rr_path, QBrush(bg))
        else:
            painter.fillRect(int(x_off), int(y_off), max(1, int(w_px)), max(1, int(h_px)), bg)

    # ── QR ────────────────────────────────────────────────────────────────
    qr_cfg = tmpl.get("qr") or {}
    metrics = qr_metrics(tmpl, dims)
    qr_size_px = 0.0
    qr_left = qr_right = False
    qr_top_reserved = 0.0
    qr_bottom_reserved = 0.0
    if metrics is not None:
        ecc = qr_cfg.get("ecc") or "Q"
        qr_text = str(data.get(qr_cfg.get("content") or "uniqueId") or "")
        size_px = max(8, int(metrics["sizeMm"] * ppm))
        pm = _generate_qr_pixmap(qr_text, size_px, ecc)
        if pm:
            painter.drawPixmap(
                int(x_off + metrics["x"] * ppm),
                int(y_off + metrics["y"] * ppm),
                pm,
            )
        if hit_boxes is not None:
            hit_boxes.append({
                "kind": "qr", "row": -1, "field": -1,
                "x": x_off + metrics["x"] * ppm, "y": y_off + metrics["y"] * ppm,
                "w": metrics["sizeMm"] * ppm, "h": metrics["sizeMm"] * ppm,
            })
        pos = qr_cfg.get("position")
        if pos == "right":
            qr_right = True
            qr_size_px = metrics["sizeMm"] * ppm
        elif pos == "left":
            qr_left = True
            qr_size_px = metrics["sizeMm"] * ppm
        elif pos == "bottom":
            qr_bottom_reserved = metrics["sizeMm"] * ppm
        elif pos == "top":
            qr_top_reserved = metrics["sizeMm"] * ppm

    # ── Text area (reserve horizontal room for left/right, vertical for top/bottom QR) ──
    pad = _PAD_MM * ppm
    gap = _GAP_MM * ppm
    left_x = x_off + pad + (qr_size_px + gap if qr_left else 0.0)
    right_x = x_off + w_px - pad - (qr_size_px + gap if qr_right else 0.0)
    avail_w = max(1.0, right_x - left_x)
    text_y_min = float(y_off) + pad + (qr_top_reserved + gap if qr_top_reserved else 0.0)
    text_y_max = float(y_off) + h_px - pad - (qr_bottom_reserved + gap if qr_bottom_reserved else 0.0)

    # ── Rows ────────────────────────────────────────────────────────────────
    y_cursor = text_y_min
    for row_idx, row in enumerate(tmpl.get("rows") or []):
        row = row or {}
        row_size = row.get("size") or 9
        row_style = row.get("style") or ""
        align = row.get("align") or "left"
        sep = row.get("sep") if row.get("sep") is not None else " "
        prefix = row.get("prefix") or ""

        # Build display segments: optional prefix, then each field.
        fields = row.get("fields") or []
        seg_specs: list[tuple[str, QFont]] = []
        any_field_text = False
        # offsets parallel to seg_specs (mm) — prefix has none.
        seg_off: list[tuple[float, float]] = []
        seg_field: list[int] = []  # field index per seg, or -1 for prefix/sep
        seg_colors: list[Optional[str]] = []  # per-seg color override (None = default)

        if prefix:
            seg_specs.append((prefix, _seg_font(row_size, row_style, ppm)))
            seg_off.append((0.0, 0.0))
            seg_field.append(-1)
            seg_colors.append(None)

        for i, f in enumerate(fields):
            if isinstance(f, dict):
                key = f.get("key") or ""
                fstyle = f.get("style") or row_style
                fsize = f.get("size") or row_size
                ox = float(f.get("offsetX") or 0.0)
                oy = float(f.get("offsetY") or 0.0)
                fcolor = f.get("color") or None
            else:
                key, fstyle, fsize, ox, oy = str(f), row_style, row_size, 0.0, 0.0
                fcolor = None
            v = data.get(key)
            text = "" if v is None else str(v)
            if text:
                any_field_text = True
            # separator between fields (not before the first field)
            if i > 0 and sep:
                seg_specs.append((sep, _seg_font(row_size, row_style, ppm)))
                seg_off.append((0.0, 0.0))
                seg_field.append(-1)
                seg_colors.append(None)
            seg_specs.append((text, _seg_font(fsize, fstyle, ppm)))
            seg_off.append((ox, oy))
            seg_field.append(i)
            seg_colors.append(fcolor)

        # Empty row handling (mirror old behaviour).
        if not any_field_text and not prefix:
            if placeholder:
                seg_specs = [("—", _seg_font(row_size, row_style, ppm))]
                seg_off = [(0.0, 0.0)]
                seg_field = [0 if fields else -1]
                seg_colors = [None]
            else:
                continue

        # Measure.
        widths = []
        max_h = 0.0
        for text, font in seg_specs:
            fm = QFontMetricsF(font)
            widths.append(fm.horizontalAdvance(text))
            max_h = max(max_h, fm.height())
        total_w = sum(widths)

        # Align the segment group within the available text area.
        if align == "center":
            start_x = left_x + max(0.0, (avail_w - total_w) / 2.0)
        elif align == "right":
            start_x = left_x + max(0.0, avail_w - total_w)
        else:
            start_x = left_x

        if y_cursor + max_h > text_y_max:
            break

        wrap = resolve_wrap(row)
        if not wrap:
            painter.save()
            painter.setClipRect(QRectF(left_x, y_cursor, avail_w, max_h))

        cursor_x = start_x
        for (text, font), w, (ox, oy), fidx, fclr in zip(
                seg_specs, widths, seg_off, seg_field, seg_colors):
            draw_x = cursor_x + ox * ppm
            draw_y = y_cursor + oy * ppm
            painter.setFont(font)
            if fclr:
                painter.setPen(QColor(fclr))
            painter.drawText(
                QRectF(draw_x, draw_y, w + 2.0, max_h),
                int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
                | int(Qt.TextFlag.TextSingleLine),
                text,
            )
            if fclr:
                painter.setPen(QColor("#000000"))
            if hit_boxes is not None and fidx >= 0:
                hit_boxes.append({
                    "kind": "field", "row": row_idx, "field": fidx,
                    "x": draw_x, "y": draw_y, "w": max(w, 4.0), "h": max_h,
                })
            cursor_x += w

        if not wrap:
            painter.restore()

        lh = max_h * resolve_line_height(tmpl, row)
        y_cursor += lh

    # ── Free-form element overlay (drawn above rows; list order = z-order) ────
    # When tmpl has no elements the loop body never runs, so the output stays
    # byte-identical to the row-only renderer (this path also drives the
    # printer — see the byte-identity gate in test_label_render.py).
    _draw_elements(painter, tmpl, data, ppm, float(x_off), float(y_off), hit_boxes)


# ── Free-form element layer ────────────────────────────────────────────────────

def _generate_barcode_pixmap(
    text: str, w_px: int, h_px: int, show_text: bool = True
) -> Optional[QPixmap]:
    """Generate a Code128 1D barcode QPixmap, or None (soft-degrade).

    Mirrors ``_generate_qr_pixmap``: ``python-barcode`` is an OPTIONAL runtime
    dependency. Import it inside the function and return None on any failure so
    a missing library degrades to a placeholder box instead of crashing the
    designer or the printer.
    """
    try:
        import barcode  # python-barcode
        from barcode.writer import ImageWriter

        code = barcode.get("code128", text or "0", writer=ImageWriter())
        buf = io.BytesIO()
        code.write(buf, options={
            "module_height": max(2.0, h_px / 8.0),
            "font_size": 8 if show_text else 0,
            "text_distance": 2 if show_text else 0,
            "write_text": bool(show_text),
            "quiet_zone": 1.0,
        })
        buf.seek(0)
        qimage = QImage.fromData(buf.read())
        if qimage.isNull():
            return None
        pm = QPixmap.fromImage(qimage)
        return pm.scaled(max(1, int(w_px)), max(1, int(h_px)),
                         Qt.AspectRatioMode.IgnoreAspectRatio,
                         Qt.TransformationMode.SmoothTransformation)
    except Exception:
        return None


def _load_image_pixmap(el: dict) -> Optional[QPixmap]:
    """Load an image element's pixmap from inline base64 ``data`` or a ``path``.

    Returns None when neither is present or the bytes won't decode — callers
    draw a placeholder box rather than crash.
    """
    try:
        data = el.get("data")
        if data:
            raw = base64.b64decode(data)
            img = QImage.fromData(raw)
        elif el.get("path"):
            img = QImage(str(el["path"]))
        else:
            return None
        if img.isNull():
            return None
        return QPixmap.fromImage(img)
    except Exception:
        return None


def _placeholder_box(painter: QPainter, rect: QRectF) -> None:
    """Dashed placeholder for an element whose content failed to load."""
    painter.save()
    pen = QPen(QColor("#bbbbbb"))
    pen.setStyle(Qt.PenStyle.DashLine)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawRect(rect)
    painter.restore()


def _draw_elements(
    painter: QPainter,
    tmpl: dict,
    data: dict,
    ppm: float,
    x_off: float,
    y_off: float,
    hit_boxes: Optional[list],
) -> None:
    """Draw the free-form ``elements`` overlay onto *painter*.

    Each element's device-pixel rectangle is appended to *hit_boxes* as
    ``{"kind":"element","index":i,"etype":type,"x","y","w","h"}`` (axis-aligned
    bounding box, even when rotated — v1) so the designer canvas can select and
    resize it. Coords are mm with origin at the label top-left.
    """
    elements = tmpl.get("elements")
    if not elements:
        return
    mono = bool(tmpl.get("monochrome"))
    for i, el in enumerate(elements):
        if not isinstance(el, dict):
            continue
        etype = el.get("type")
        if etype == "line":
            x1 = x_off + float(el.get("x1") or 0.0) * ppm
            y1 = y_off + float(el.get("y1") or 0.0) * ppm
            x2 = x_off + float(el.get("x2") or 0.0) * ppm
            y2 = y_off + float(el.get("y2") or 0.0) * ppm
            painter.save()
            op = float(el.get("opacity", 1.0) or 0.0)
            if op < 1.0 and not mono:
                painter.setOpacity(op)
            color = QColor(el.get("color") or "#000000")
            wpx = max(0.5, float(el.get("width") or 0.3) * ppm)
            pen = QPen(color, wpx)
            _apply_dash(pen, el)
            painter.setPen(pen)
            painter.drawLine(int(x1), int(y1), int(x2), int(y2))
            if el.get("arrowEnd"):
                _draw_arrowhead(painter, x2, y2, x1, y1, color, wpx)
            if el.get("arrowStart"):
                _draw_arrowhead(painter, x1, y1, x2, y2, color, wpx)
            painter.restore()
            if hit_boxes is not None:
                bx, by = min(x1, x2), min(y1, y2)
                hit_boxes.append({"kind": "element", "index": i, "etype": "line",
                                  "x": bx, "y": by,
                                  "w": max(abs(x2 - x1), 4.0),
                                  "h": max(abs(y2 - y1), 4.0)})
            continue

        # rect-based elements share an (x, y, w, h) box
        bx = x_off + float(el.get("x") or 0.0) * ppm
        by = y_off + float(el.get("y") or 0.0) * ppm
        bw = float(el.get("w") or 0.0) * ppm
        bh = float(el.get("h") or 0.0) * ppm
        rect = QRectF(bx, by, bw, bh)

        rotation = float(el.get("rotation") or 0.0)
        painter.save()
        op = float(el.get("opacity", 1.0) or 0.0)
        if op < 1.0 and not mono:
            painter.setOpacity(op)
        if rotation:
            cx, cy = bx + bw / 2.0, by + bh / 2.0
            painter.translate(cx, cy)
            painter.rotate(rotation)
            painter.translate(-cx, -cy)

        if etype in ("text", "field"):
            if etype == "field":
                v = data.get(el.get("key") or "")
                text = "" if v is None else str(v)
            else:
                text = str(el.get("text") or "")
            font = _seg_font(el.get("size") or 9, el.get("style") or "", ppm,
                             family=el.get("font") or "")
            painter.setFont(font)
            painter.setPen(QColor(el.get("color") or "#000000"))
            align = el.get("align") or "left"
            flag = {"center": Qt.AlignmentFlag.AlignHCenter,
                    "right": Qt.AlignmentFlag.AlignRight}.get(
                        align, Qt.AlignmentFlag.AlignLeft)
            tflag = int(flag | Qt.AlignmentFlag.AlignVCenter)
            if el.get("wrap"):
                tflag |= int(Qt.TextFlag.TextWordWrap)
            painter.drawText(rect, tflag, text)
        elif etype == "rect":
            cr = float(el.get("cornerRadius") or 0.0) * ppm
            if el.get("shadow") and not mono:
                _draw_shape_shadow(painter, rect, el["shadow"], ppm,
                                   is_ellipse=False, corner_px=cr)
            pen = QPen(QColor(el.get("stroke") or "#000000"),
                       max(0.5, float(el.get("strokeWidth") or 0.3) * ppm))
            _apply_dash(pen, el)
            painter.setPen(pen)
            grad = el.get("gradient")
            brush = _make_gradient_brush(grad, rect, mono) if grad else None
            if brush is None:
                fill = el.get("fill")
                brush = QBrush(QColor(fill)) if fill else Qt.BrushStyle.NoBrush
            painter.setBrush(brush)
            if cr > 0:
                painter.drawRoundedRect(rect, cr, cr)
            else:
                painter.drawRect(rect)
        elif etype == "ellipse":
            if el.get("shadow") and not mono:
                _draw_shape_shadow(painter, rect, el["shadow"], ppm,
                                   is_ellipse=True)
            pen = QPen(QColor(el.get("stroke") or "#000000"),
                       max(0.5, float(el.get("strokeWidth") or 0.3) * ppm))
            _apply_dash(pen, el)
            painter.setPen(pen)
            grad = el.get("gradient")
            brush = _make_gradient_brush(grad, rect, mono) if grad else None
            if brush is None:
                fill = el.get("fill")
                brush = QBrush(QColor(fill)) if fill else Qt.BrushStyle.NoBrush
            painter.setBrush(brush)
            painter.drawEllipse(rect)
        elif etype == "image":
            pm = _load_image_pixmap(el)
            if pm is None:
                _placeholder_box(painter, rect)
            elif el.get("keepAspect", True):
                scaled = pm.scaled(int(bw), int(bh),
                                   Qt.AspectRatioMode.KeepAspectRatio,
                                   Qt.TransformationMode.SmoothTransformation)
                ox = bx + (bw - scaled.width()) / 2.0
                oy = by + (bh - scaled.height()) / 2.0
                painter.drawPixmap(int(ox), int(oy), scaled)
            else:
                painter.drawPixmap(rect, pm, QRectF(pm.rect()))
        elif etype == "barcode":
            content = el.get("content") or ""
            text = str(data.get(content, content) or "")
            pm = _generate_barcode_pixmap(text, int(bw), int(bh),
                                          bool(el.get("showText", True)))
            if pm is None:
                _placeholder_box(painter, rect)
            else:
                painter.drawPixmap(int(bx), int(by), pm)
        painter.restore()

        if hit_boxes is not None and etype != "line":
            hit_boxes.append({"kind": "element", "index": i, "etype": etype,
                              "x": bx, "y": by, "w": bw, "h": bh})


# ── Pixmap convenience wrappers (preview / card thumbnails) ────────────────────
# The single renderer above paints onto any QPainter; these wrap it for the
# common "give me a QPixmap of this label" case used by previews and cards.

def render_label_pixmap(
    tmpl: dict,
    dims: dict,
    data: dict,
    scale: float = 3.78,
    placeholder: bool = True,
) -> QPixmap:
    """Render a label to a white QPixmap via the unified renderer.

    With ``placeholder=True`` (preview default) a fully-empty row renders "—" so
    the layout stays visible without specimen data; ``placeholder=False``
    (printing) skips empty rows.
    """
    w_mm = float(dims.get("w", 60))
    h_mm = float(dims.get("h", 40))
    w_px = max(1, int(w_mm * scale))
    h_px = max(1, int(h_mm * scale))
    pixmap = QPixmap(w_px, h_px)
    pixmap.fill(QColor("white"))
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    render_label_onto(
        painter, tmpl, dims, data,
        px_per_mm=scale, placeholder=placeholder, fill_bg=False,
    )
    painter.end()
    return pixmap


def render_label_preview(
    tmpl: dict,
    dims: dict,
    data: dict,
    box_w: int,
    box_h: int,
    dpr: float = 2.0,
    placeholder: bool = True,
) -> QPixmap:
    """Crisp label preview fitting a ``box_w × box_h`` box, keeping aspect.

    Renders at ``dpr×`` the on-screen size and tags the pixmap with that device
    pixel ratio for supersampled (sharp) text + QR at the box size.
    """
    w_mm = max(1.0, float(dims.get("w", 60)))
    h_mm = max(1.0, float(dims.get("h", 40)))
    scr = min(box_w / w_mm, box_h / h_mm)          # px-per-mm shown on screen
    pm = render_label_pixmap(tmpl, dims, data, scale=scr * dpr, placeholder=placeholder)
    pm.setDevicePixelRatio(dpr)
    return pm
