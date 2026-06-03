"""label_editor.py — WYSIWYG label editor widget.

QGraphicsScene-based editor where:
  - Each text row is a ``QGraphicsTextItem`` (editable, movable).
  - The QR code placeholder is a ``QGraphicsPixmapItem`` (movable, draggable).
  - A dashed safety-margin rectangle (default 2 mm) is drawn as a background.
  - All scene coordinates are in mm; the view applies a mm→px scale.
  - An ``QUndoStack`` (max 30) tracks template changes.
  - QR codes are generated with the ``qrcode`` library at error-correction Q.

Usage
-----
    editor = LabelEditorWidget(template, dims, label_data)
    editor.template_changed.connect(my_slot)   # emitted on every edit
"""

from __future__ import annotations

import io
from typing import Optional

from PyQt6.QtCore import Qt, QRectF, QPointF, pyqtSignal
from PyQt6.QtGui import (
    QColor,
    QPen,
    QBrush,
    QPixmap,
    QImage,
    QUndoStack,
    QUndoCommand,
    QFont,
    QPainter,
)
from PyQt6.QtWidgets import (
    QGraphicsScene,
    QGraphicsView,
    QGraphicsTextItem,
    QGraphicsRectItem,
    QGraphicsPixmapItem,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QPushButton,
    QLabel,
    QSizePolicy,
)

from app.utils.label_core import (
    normalize_template,
    qr_metrics,
    resolve_line_height,
)

# ── Constants ─────────────────────────────────────────────────────────────────

_SAFETY_MARGIN_MM: float = 2.0   # default dashed-border inset
_SCENE_SCALE: float = 3.5        # px per mm (≈ 90 DPI effective)
_UNDO_LIMIT: int = 30
_QR_ECC_MAP = {"L": 1, "M": 0, "Q": 3, "H": 2}   # qrcode.constants.ERROR_CORRECT_*


def _mm_to_px(mm: float) -> float:
    return mm * _SCENE_SCALE


def _px_to_mm(px: float) -> float:
    return px / _SCENE_SCALE


# ── Undo command ──────────────────────────────────────────────────────────────

class _MoveQrCommand(QUndoCommand):
    """Undo/redo: move QR item to a new mm position."""

    def __init__(
        self,
        editor: "LabelEditorWidget",
        old_pos: QPointF,
        new_pos: QPointF,
        parent: Optional[QUndoCommand] = None,
    ) -> None:
        super().__init__("Move QR", parent)
        self._editor = editor
        self._old = old_pos
        self._new = new_pos

    def undo(self) -> None:
        self._editor._set_qr_pos_mm(self._old.x(), self._old.y(), push_undo=False)

    def redo(self) -> None:
        self._editor._set_qr_pos_mm(self._new.x(), self._new.y(), push_undo=False)


class _EditTextCommand(QUndoCommand):
    """Undo/redo: text row content change."""

    def __init__(
        self,
        item: "QGraphicsTextItem",
        old_text: str,
        new_text: str,
        parent: Optional[QUndoCommand] = None,
    ) -> None:
        super().__init__("Edit text", parent)
        self._item = item
        self._old = old_text
        self._new = new_text

    def undo(self) -> None:
        self._item.setPlainText(self._old)

    def redo(self) -> None:
        self._item.setPlainText(self._new)


# ── QR generation helper ──────────────────────────────────────────────────────

def _generate_qr_pixmap(text: str, size_px: int, ecc: str = "Q") -> Optional[QPixmap]:
    """Generate a QR code QPixmap.

    Uses the ``qrcode`` library with error-correction level Q (25 % recovery).
    Returns None on import error (soft degradation when qrcode not installed).
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


# ── Scene ─────────────────────────────────────────────────────────────────────

class LabelScene(QGraphicsScene):
    """QGraphicsScene with mm coordinate system.

    Origin (0, 0) = top-left corner of the label area.
    Scene unit = mm * _SCENE_SCALE px.
    """

    def __init__(
        self,
        template: dict,
        dims: dict,
        label_data: dict,
        undo_stack: QUndoStack,
        parent: Optional[object] = None,
    ) -> None:
        super().__init__(parent)  # type: ignore[arg-type]
        self._template = normalize_template(template)
        self._dims = dims
        self._label_data = label_data
        self._undo_stack = undo_stack
        self._qr_item: Optional[QGraphicsPixmapItem] = None
        self._text_items: list[QGraphicsTextItem] = []
        self._safety_rect: Optional[QGraphicsRectItem] = None
        self._build()

    # ── Build ──────────────────────────────────────────────────────────────

    def _build(self) -> None:
        """(Re)populate the scene from current template + label_data."""
        self.clear()
        self._qr_item = None
        self._text_items = []
        self._safety_rect = None

        w_mm = self._dims.get("w", 60.0)
        h_mm = self._dims.get("h", 40.0)
        w_px = _mm_to_px(w_mm)
        h_px = _mm_to_px(h_mm)

        # Background
        bg = QGraphicsRectItem(0, 0, w_px, h_px)
        bg.setBrush(QBrush(QColor("white")))
        bg.setPen(QPen(QColor("#aaaaaa"), 1))
        bg.setZValue(-2)
        self.addItem(bg)

        # Safety margin dashed rect
        m_px = _mm_to_px(_SAFETY_MARGIN_MM)
        safety = QGraphicsRectItem(m_px, m_px, w_px - 2 * m_px, h_px - 2 * m_px)
        pen = QPen(QColor("#ff7700"), 0.7, Qt.PenStyle.DashLine)
        safety.setPen(pen)
        safety.setBrush(QBrush(Qt.BrushStyle.NoBrush))
        safety.setZValue(-1)
        self.addItem(safety)
        self._safety_rect = safety

        # QR code
        metrics = qr_metrics(self._template, self._dims)
        qr_cfg = self._template.get("qr") or {}
        ecc = qr_cfg.get("ecc") or "Q"
        uid_value = self._label_data.get(qr_cfg.get("content") or "uniqueId") or ""

        if metrics is not None:
            size_px = max(20, int(_mm_to_px(metrics["sizeMm"])))
            pixmap = _generate_qr_pixmap(str(uid_value), size_px, ecc)
            if pixmap is None:
                # Fallback: grey placeholder square
                fallback = QPixmap(size_px, size_px)
                fallback.fill(QColor("#cccccc"))
                pixmap = fallback
            qr_item = QGraphicsPixmapItem(pixmap)
            qr_item.setPos(_mm_to_px(metrics["x"]), _mm_to_px(metrics["y"]))
            qr_item.setFlags(
                QGraphicsPixmapItem.GraphicsItemFlag.ItemIsMovable
                | QGraphicsPixmapItem.GraphicsItemFlag.ItemIsSelectable
            )
            qr_item.setZValue(10)
            self.addItem(qr_item)
            self._qr_item = qr_item

        # Text rows
        tmpl = self._template
        qr_w_mm = metrics["sizeMm"] if metrics and qr_cfg.get("position") == "right" else 0.0
        text_w_px = _mm_to_px(max(1.0, w_mm - qr_w_mm - 1))
        y_px = _mm_to_px(2.0)   # 2 mm top padding

        for row in tmpl.get("rows") or []:
            fields = row.get("fields") or []
            parts: list[str] = []
            for f in fields:
                key = f.get("key") if isinstance(f, dict) else str(f)
                val = self._label_data.get(key)
                if val is not None:
                    parts.append(str(val))
            text = (row.get("sep") or " ").join(parts) if parts else ""
            if row.get("prefix"):
                text = row["prefix"] + text

            item = QGraphicsTextItem(text)
            size_pt = row.get("size") or 9
            font = QFont()
            font.setPointSizeF(float(size_pt))
            style = row.get("style") or ""
            if "bold" in style:
                font.setBold(True)
            if "italic" in style:
                font.setItalic(True)
            item.setFont(font)
            item.setTextWidth(text_w_px)
            item.setFlags(
                QGraphicsTextItem.GraphicsItemFlag.ItemIsSelectable
                | QGraphicsTextItem.GraphicsItemFlag.ItemIsMovable
            )
            item.setTextInteractionFlags(
                Qt.TextInteractionFlag.TextEditorInteraction
            )
            item.setPos(_mm_to_px(1.5), y_px)   # 1.5 mm left margin
            item.setZValue(5)
            self.addItem(item)
            self._text_items.append(item)

            lh = resolve_line_height(tmpl, row)
            y_px += item.boundingRect().height() * lh

        self.setSceneRect(0, 0, w_px, h_px)

    # ── Public API ─────────────────────────────────────────────────────────

    def rebuild(self, template: Optional[dict] = None, dims: Optional[dict] = None,
                label_data: Optional[dict] = None) -> None:
        """Rebuild scene with new template / dims / data."""
        if template is not None:
            self._template = normalize_template(template)
        if dims is not None:
            self._dims = dims
        if label_data is not None:
            self._label_data = label_data
        self._build()

    def set_qr_pos_mm(self, x_mm: float, y_mm: float) -> None:
        """Move QR item to (x_mm, y_mm) — wraps with undo command."""
        if self._qr_item is None:
            return
        old_pos = self._qr_item.pos()
        new_pos = QPointF(_mm_to_px(x_mm), _mm_to_px(y_mm))
        if old_pos == new_pos:
            return
        old_mm = QPointF(_px_to_mm(old_pos.x()), _px_to_mm(old_pos.y()))
        new_mm = QPointF(x_mm, y_mm)
        self._undo_stack.push(_MoveQrCommand(self, old_mm, new_mm))

    def _set_qr_pos_mm(self, x_mm: float, y_mm: float, push_undo: bool = True) -> None:
        """Internal: move QR item; push_undo=False skips undo-command creation."""
        if self._qr_item is None:
            return
        self._qr_item.setPos(_mm_to_px(x_mm), _mm_to_px(y_mm))
        if push_undo:
            self.set_qr_pos_mm(x_mm, y_mm)

    @property
    def qr_item(self) -> Optional[QGraphicsPixmapItem]:
        return self._qr_item

    @property
    def text_items(self) -> list[QGraphicsTextItem]:
        return list(self._text_items)


# ── Main editor widget ────────────────────────────────────────────────────────

class LabelEditorWidget(QWidget):
    """WYSIWYG label editor widget.

    Signals
    -------
    template_changed(dict) — emitted after each structural edit (text row
        content change, QR drag, etc.).  The dict is the updated template.
    """

    template_changed = pyqtSignal(dict)

    def __init__(
        self,
        template: Optional[dict],
        dims: Optional[dict],
        label_data: Optional[dict],
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._template = normalize_template(template)
        self._dims = dims or {"w": 60, "h": 40}
        self._label_data = label_data or {}
        self._undo_stack = QUndoStack(self)
        self._undo_stack.setUndoLimit(_UNDO_LIMIT)
        self._setup_ui()

    # ── UI ─────────────────────────────────────────────────────────────────

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        # Toolbar: undo / redo
        toolbar = QHBoxLayout()
        toolbar.setSpacing(6)

        self._undo_btn = QPushButton("↶ 撤销")
        self._undo_btn.setEnabled(False)
        self._undo_btn.clicked.connect(self._undo_stack.undo)
        toolbar.addWidget(self._undo_btn)

        self._redo_btn = QPushButton("↷ 重做")
        self._redo_btn.setEnabled(False)
        self._redo_btn.clicked.connect(self._undo_stack.redo)
        toolbar.addWidget(self._redo_btn)

        toolbar.addStretch()

        dim_text = f"{self._dims.get('w', '?')}×{self._dims.get('h', '?')} mm"
        dim_label = QLabel(dim_text)
        dim_label.setStyleSheet("color: #888; font-size: 11px;")
        toolbar.addWidget(dim_label)
        self._dim_label = dim_label

        layout.addLayout(toolbar)

        # Scene + View
        self._scene = LabelScene(
            self._template, self._dims, self._label_data, self._undo_stack
        )
        self._view = QGraphicsView(self._scene)
        self._view.setRenderHint(QPainter.RenderHint.Antialiasing)
        self._view.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        self._view.setBackgroundBrush(QBrush(QColor("#2b2b2b")))
        self._view.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        self._view.setMinimumHeight(120)
        layout.addWidget(self._view)

        # Fit scene in view
        self._view.fitInView(self._scene.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)

        # Connect undo stack state
        self._undo_stack.canUndoChanged.connect(self._undo_btn.setEnabled)
        self._undo_stack.canRedoChanged.connect(self._redo_btn.setEnabled)

    # ── Public API ─────────────────────────────────────────────────────────

    def update_label(
        self,
        template: Optional[dict] = None,
        dims: Optional[dict] = None,
        label_data: Optional[dict] = None,
    ) -> None:
        """Rebuild the scene with new template / dims / label_data."""
        if template is not None:
            self._template = normalize_template(template)
        if dims is not None:
            self._dims = dims
            self._dim_label.setText(f"{dims.get('w', '?')}×{dims.get('h', '?')} mm")
        if label_data is not None:
            self._label_data = label_data
        self._scene.rebuild(self._template, self._dims, self._label_data)
        self._view.fitInView(self._scene.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)

    @property
    def undo_stack(self) -> QUndoStack:
        return self._undo_stack

    @property
    def scene(self) -> LabelScene:
        return self._scene

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        if hasattr(self, "_view") and self._scene:
            self._view.fitInView(
                self._scene.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio
            )
