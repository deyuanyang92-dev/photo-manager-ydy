"""screenshot_annotations.py — annotation data + painting for the screenshot editor.

Pure Qt drawing, no widgets. One :class:`Annotation` per drawn mark; the overlay
keeps an ordered list (draw order = z-order) and replays it both for the live
preview and for the final composited result, so on-screen == saved pixels.

Tools
-----
RECT       outline rectangle
ARROW      line with a solid arrowhead at the end point
PEN        freehand polyline
TEXT       a text string anchored at one point
HIGHLIGHT  translucent filled rectangle (marker pen)
MOSAIC     pixelated patch sampled from the underlying frozen pixmap
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum

from PyQt6.QtCore import QPoint, QPointF, QRect, Qt
from PyQt6.QtGui import QColor, QFont, QPainter, QPen, QPixmap, QPolygonF


class Tool(str, Enum):
    RECT = "rect"
    ARROW = "arrow"
    PEN = "pen"
    TEXT = "text"
    HIGHLIGHT = "highlight"
    MOSAIC = "mosaic"


# Tools that are dragged out as a 2-point rubber band (origin → current).
DRAG_TOOLS = {Tool.RECT, Tool.ARROW, Tool.HIGHLIGHT, Tool.MOSAIC}

MOSAIC_BLOCK = 9  # source pixels per mosaic cell


@dataclass
class Annotation:
    tool: Tool
    points: list[QPoint] = field(default_factory=list)
    color: QColor = field(default_factory=lambda: QColor(255, 64, 64))
    width: int = 3
    text: str = ""
    font_pt: int = 16

    def bounds_rect(self) -> QRect:
        if len(self.points) >= 2:
            return QRect(self.points[0], self.points[-1]).normalized()
        if self.points:
            return QRect(self.points[0], self.points[0])
        return QRect()


def paint_annotation(
    painter: QPainter, ann: Annotation, source: QPixmap | None = None
) -> None:
    """Draw *ann* with *painter* (already in overlay-local logical coords).

    *source* is the frozen background pixmap; required for MOSAIC so it can
    sample the pixels it pixelates.
    """
    painter.save()
    pen = QPen(ann.color, ann.width)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)

    if ann.tool is Tool.RECT:
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(ann.bounds_rect())

    elif ann.tool is Tool.HIGHLIGHT:
        c = QColor(ann.color)
        c.setAlpha(90)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(c)
        painter.drawRect(ann.bounds_rect())

    elif ann.tool is Tool.ARROW and len(ann.points) >= 2:
        _draw_arrow(painter, pen, ann.points[0], ann.points[-1])

    elif ann.tool is Tool.PEN and len(ann.points) >= 2:
        painter.setPen(pen)
        painter.drawPolyline(QPolygonF([QPointF(p) for p in ann.points]))

    elif ann.tool is Tool.TEXT and ann.points and ann.text:
        f = QFont()
        f.setPointSize(ann.font_pt)
        painter.setFont(f)
        painter.setPen(QPen(ann.color))
        # Anchor the text by its top-left, baseline pushed down one line.
        painter.drawText(
            QPoint(ann.points[0].x(), ann.points[0].y() + ann.font_pt), ann.text
        )

    elif ann.tool is Tool.MOSAIC and source is not None and len(ann.points) >= 2:
        rect = ann.bounds_rect()
        patch = _mosaic_patch(source, rect)
        if patch is not None:
            painter.drawPixmap(rect, patch)

    painter.restore()


def _draw_arrow(painter: QPainter, pen: QPen, a: QPoint, b: QPoint) -> None:
    painter.setPen(pen)
    painter.drawLine(a, b)
    ang = math.atan2(b.y() - a.y(), b.x() - a.x())
    size = max(10.0, pen.widthF() * 3.5)
    for da in (math.radians(150), math.radians(-150)):
        x = b.x() + size * math.cos(ang + da)
        y = b.y() + size * math.sin(ang + da)
        painter.drawLine(b, QPoint(int(x), int(y)))


def _mosaic_patch(source: QPixmap, rect: QRect) -> QPixmap | None:
    if rect.width() < 1 or rect.height() < 1:
        return None
    dpr = source.devicePixelRatio()
    phys = QRect(
        int(rect.x() * dpr), int(rect.y() * dpr),
        int(rect.width() * dpr), int(rect.height() * dpr),
    )
    chunk = source.copy(phys)
    if chunk.isNull():
        return None
    small_w = max(1, chunk.width() // (MOSAIC_BLOCK))
    small_h = max(1, chunk.height() // (MOSAIC_BLOCK))
    small = chunk.scaled(
        small_w, small_h,
        Qt.AspectRatioMode.IgnoreAspectRatio,
        Qt.TransformationMode.FastTransformation,
    )
    pix = small.scaled(
        chunk.width(), chunk.height(),
        Qt.AspectRatioMode.IgnoreAspectRatio,
        Qt.TransformationMode.FastTransformation,
    )
    pix.setDevicePixelRatio(dpr)
    return pix
