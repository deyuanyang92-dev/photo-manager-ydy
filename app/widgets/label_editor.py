"""label_editor.py — WYSIWYG label editor widget.

QGraphicsScene-based editor where:
  - Each text row is a ``QGraphicsTextItem`` (editable, movable).
  - The QR code placeholder is a ``QGraphicsPixmapItem`` (movable, draggable).
  - A dashed safety-margin rectangle (default 2 mm) is drawn as a background.
  - All scene coordinates are in mm; the view applies a mm→px scale.
  - An ``QUndoStack`` (max 30) tracks template changes.
  - QR codes are generated with the ``qrcode`` library at error-correction Q.

Structural row editor (mirrors renderEditorModeBar + renderRowFloatingToolbar):
  - ``_RowEditorPanel`` lists all rows with add / delete / reorder / style controls.
  - Displayed below the QGraphicsView in the LabelEditorWidget layout.

Preview context menu (mirrors renderLabelPreviewContextMenu):
  - Right-click on the QGraphicsView opens a QMenu with: copy text, QR position
    toggle, and add-row shortcut.

Usage
-----
    editor = LabelEditorWidget(template, dims, label_data)
    editor.template_changed.connect(my_slot)   # emitted on every edit
"""

from __future__ import annotations

import copy
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
    QKeySequence,
    QShortcut,
)
from PyQt6.QtWidgets import (
    QApplication,
    QComboBox,
    QDoubleSpinBox,
    QFrame,
    QGraphicsItem,
    QGraphicsScene,
    QGraphicsView,
    QGraphicsTextItem,
    QGraphicsRectItem,
    QGraphicsPixmapItem,
    QMenu,
    QScrollArea,
    QSlider,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QPushButton,
    QLabel,
    QSizePolicy,
    QSpinBox,
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


# ── Undo commands ─────────────────────────────────────────────────────────────

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


class _RowsCommand(QUndoCommand):
    """Undo/redo: any structural change to template rows.

    Saves full rows snapshot before and after for simple rollback.
    Mirrors JS saveCustomTemplate(bucket, tmpl) with undo stack support.
    """

    def __init__(
        self,
        editor: "LabelEditorWidget",
        old_rows: list,
        new_rows: list,
        description: str = "Rows edit",
        parent: Optional[QUndoCommand] = None,
    ) -> None:
        super().__init__(description, parent)
        self._editor = editor
        self._old = copy.deepcopy(old_rows)
        self._new = copy.deepcopy(new_rows)

    def undo(self) -> None:
        self._editor._apply_rows(copy.deepcopy(self._old))

    def redo(self) -> None:
        self._editor._apply_rows(copy.deepcopy(self._new))


# ── Field-key display names ───────────────────────────────────────────────────

_FIELD_NAMES: dict[str, str] = {
    "uniqueId": "唯一编号",
    "headerId": "编号头",
    "storage": "保存方式",
    "shortDate": "日期段",
    "fullDate": "完整日期段",
    "speciesName": "物种名称",
    "latin": "拉丁名",
    "family": "科",
    "region": "地点",
    "collectorLabel": "采集人",
    "photographer": "拍摄者",
    "lon": "经度",
    "lat": "纬度",
    "geoArea": "采集地理区",
    "rnaPreservative": "RNA保存液",
}

_ALL_FIELD_KEYS = list(_FIELD_NAMES.keys()) + [
    "province", "site", "station", "speciesId", "date",
    "collectionDate", "photoDate", "photoNotes", "collector",
]


# ── Row editor panel ──────────────────────────────────────────────────────────

class _RowEditorPanel(QWidget):
    """Structural row editor for a label template.

    Shows each row as a card with:
      - row index + field summary label
      - font size spinner
      - bold / italic toggles
      - field-add dropdown
      - ↑ / ↓ reorder buttons
      - ✕ delete button
    Plus an "+ 新增行" button at the top.

    Mirrors: ``renderEditorModeBar(bucket)`` + ``renderRowFloatingToolbar(bucket)``
    from app.js (web oracle).
    """

    rows_changed = pyqtSignal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._rows: list[dict] = []
        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(4)

        # Header bar
        hdr = QHBoxLayout()
        hdr.setSpacing(6)
        title = QLabel("行结构编辑器")
        title.setStyleSheet("color: #cfe0db; font-size: 11px; font-weight: bold;")
        hdr.addWidget(title)
        hdr.addStretch()
        add_btn = QPushButton("+ 新增行")
        add_btn.setFixedHeight(22)
        add_btn.setStyleSheet(
            "QPushButton { background: rgba(41,185,171,0.18); border: 1px solid #29b9ab;"
            " border-radius: 3px; color: #29b9ab; font-size: 10px; padding: 0 6px; }"
            "QPushButton:hover { background: rgba(41,185,171,0.30); }"
        )
        add_btn.clicked.connect(self._add_row)
        hdr.addWidget(add_btn)
        root.addLayout(hdr)

        # Scroll area for row cards
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet("QScrollArea { background: transparent; }")
        scroll.setMaximumHeight(180)

        self._cards_widget = QWidget()
        self._cards_widget.setStyleSheet("background: transparent;")
        self._cards_layout = QVBoxLayout(self._cards_widget)
        self._cards_layout.setContentsMargins(0, 0, 0, 0)
        self._cards_layout.setSpacing(2)
        self._cards_layout.addStretch()
        scroll.setWidget(self._cards_widget)
        root.addWidget(scroll)

    # ── Public API ────────────────────────────────────────────────────────

    def set_rows(self, rows: list) -> None:
        self._rows = copy.deepcopy(rows or [])
        self._rebuild_cards()

    def get_rows(self) -> list:
        return copy.deepcopy(self._rows)

    # ── Internal: rebuild cards ───────────────────────────────────────────

    def _rebuild_cards(self) -> None:
        while self._cards_layout.count():
            item = self._cards_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        for ri, row in enumerate(self._rows):
            self._cards_layout.addWidget(self._make_row_card(ri, row))
        self._cards_layout.addStretch()

    def _make_row_card(self, ri: int, row: dict) -> QFrame:
        card = QFrame()
        card.setStyleSheet(
            "QFrame { background: #0c2027; border: 1px solid rgba(145,182,181,0.12);"
            " border-radius: 4px; padding: 2px; }"
        )
        row_layout = QHBoxLayout(card)
        row_layout.setContentsMargins(4, 2, 4, 2)
        row_layout.setSpacing(4)

        # Row index badge
        idx_lbl = QLabel(f"#{ri + 1}")
        idx_lbl.setFixedWidth(22)
        idx_lbl.setStyleSheet("color: #5f7d7a; font-size: 10px;")
        row_layout.addWidget(idx_lbl)

        # Field summary
        fields = row.get("fields") or []
        field_names = []
        for f in fields:
            k = f.get("key") if isinstance(f, dict) else str(f)
            field_names.append(_FIELD_NAMES.get(k, k) or k)
        summary = " · ".join(field_names) if field_names else "（空行）"
        summary_lbl = QLabel(summary)
        summary_lbl.setStyleSheet("color: #cfe0db; font-size: 10px;")
        summary_lbl.setMinimumWidth(80)
        row_layout.addWidget(summary_lbl, stretch=1)

        # Font size spinner
        size_spin = QSpinBox()
        size_spin.setRange(5, 24)
        size_spin.setValue(int(row.get("size") or 9))
        size_spin.setFixedWidth(50)
        size_spin.setFixedHeight(20)
        size_spin.setStyleSheet(
            "QSpinBox { background:#0f2127; border:1px solid rgba(145,182,181,0.18);"
            " border-radius:3px; color:#eef3ef; font-size:10px; padding:1px; }"
        )
        size_spin.setToolTip("字号 pt")

        def _size_changed(val: int, _ri: int = ri) -> None:
            if _ri < len(self._rows):
                self._rows[_ri]["size"] = val
                self.rows_changed.emit()

        size_spin.valueChanged.connect(_size_changed)
        row_layout.addWidget(size_spin)

        # Bold / italic
        style = str(row.get("style") or "")
        bold_btn = QPushButton("B")
        bold_btn.setFixedSize(20, 20)
        bold_btn.setCheckable(True)
        bold_btn.setChecked("bold" in style)
        bold_btn.setStyleSheet(
            "QPushButton { background: transparent; border:1px solid rgba(145,182,181,0.18);"
            " border-radius:3px; color:#87a2a1; font-weight:bold; font-size:10px; }"
            "QPushButton:checked { color:#29b9ab; border-color:#29b9ab; }"
        )

        def _bold_toggled(checked: bool, _ri: int = ri) -> None:
            if _ri < len(self._rows):
                parts = [p for p in (self._rows[_ri].get("style") or "").split() if p != "bold"]
                if checked:
                    parts.append("bold")
                self._rows[_ri]["style"] = " ".join(parts)
                self.rows_changed.emit()

        bold_btn.toggled.connect(_bold_toggled)
        row_layout.addWidget(bold_btn)

        italic_btn = QPushButton("I")
        italic_btn.setFixedSize(20, 20)
        italic_btn.setCheckable(True)
        italic_btn.setChecked("italic" in style)
        italic_btn.setStyleSheet(
            "QPushButton { background: transparent; border:1px solid rgba(145,182,181,0.18);"
            " border-radius:3px; color:#87a2a1; font-style:italic; font-size:10px; }"
            "QPushButton:checked { color:#29b9ab; border-color:#29b9ab; }"
        )

        def _italic_toggled(checked: bool, _ri: int = ri) -> None:
            if _ri < len(self._rows):
                parts = [p for p in (self._rows[_ri].get("style") or "").split() if p != "italic"]
                if checked:
                    parts.append("italic")
                self._rows[_ri]["style"] = " ".join(parts)
                self.rows_changed.emit()

        italic_btn.toggled.connect(_italic_toggled)
        row_layout.addWidget(italic_btn)

        # Reorder: ↑ ↓
        up_btn = QPushButton("↑")
        up_btn.setFixedSize(20, 20)
        up_btn.setEnabled(ri > 0)
        _btn_style = (
            "QPushButton { background:transparent; border:1px solid rgba(145,182,181,0.15);"
            " border-radius:3px; color:#87a2a1; font-size:10px; }"
            "QPushButton:hover { color:#29b9ab; border-color:#29b9ab; }"
            "QPushButton:disabled { color:#2d4a52; border-color:rgba(145,182,181,0.06); }"
        )
        up_btn.setStyleSheet(_btn_style)
        up_btn.setToolTip("上移行")
        up_btn.clicked.connect(lambda checked, _ri=ri: self._move_row(_ri, -1))
        row_layout.addWidget(up_btn)

        down_btn = QPushButton("↓")
        down_btn.setFixedSize(20, 20)
        down_btn.setEnabled(ri < len(self._rows) - 1)
        down_btn.setStyleSheet(_btn_style)
        down_btn.setToolTip("下移行")
        down_btn.clicked.connect(lambda checked, _ri=ri: self._move_row(_ri, +1))
        row_layout.addWidget(down_btn)

        # Field picker dropdown
        add_field_combo = QComboBox()
        add_field_combo.setFixedHeight(20)
        add_field_combo.setFixedWidth(80)
        add_field_combo.setStyleSheet(
            "QComboBox { background:#0f2127; border:1px solid rgba(145,182,181,0.15);"
            " border-radius:3px; color:#87a2a1; font-size:9px; padding:1px 2px; }"
            "QComboBox::drop-down { border:none; width:12px; }"
        )
        add_field_combo.addItem("+ 字段", "")
        for k in _ALL_FIELD_KEYS:
            add_field_combo.addItem(_FIELD_NAMES.get(k, k), k)
        add_field_combo.setToolTip("添加字段到此行")

        def _add_field(idx: int, _ri: int = ri, _combo: QComboBox = add_field_combo) -> None:
            if idx <= 0:
                return
            key = _combo.itemData(idx)
            if key and _ri < len(self._rows):
                existing_keys = [
                    (f.get("key") if isinstance(f, dict) else f)
                    for f in (self._rows[_ri].get("fields") or [])
                ]
                if key not in existing_keys:
                    self._rows[_ri].setdefault("fields", [])
                    self._rows[_ri]["fields"].append({
                        "key": key, "style": "", "size": None, "offsetX": 0, "offsetY": 0,
                    })
                    self.rows_changed.emit()
                    self._rebuild_cards()
            _combo.setCurrentIndex(0)

        add_field_combo.currentIndexChanged.connect(_add_field)
        row_layout.addWidget(add_field_combo)

        # Delete row
        del_btn = QPushButton("✕")
        del_btn.setFixedSize(20, 20)
        del_btn.setStyleSheet(
            "QPushButton { background:transparent; border:1px solid rgba(230,110,99,0.25);"
            " border-radius:3px; color:#e66e63; font-size:10px; }"
            "QPushButton:hover { border-color:#e66e63; background:rgba(230,110,99,0.12); }"
        )
        del_btn.setToolTip("删除此行")
        del_btn.clicked.connect(lambda checked, _ri=ri: self._delete_row(_ri))
        row_layout.addWidget(del_btn)

        return card

    # ── Row mutation helpers ──────────────────────────────────────────────

    def _add_row(self) -> None:
        new_row: dict = {
            "fields": [{"key": "speciesName", "style": "", "size": None,
                        "offsetX": 0, "offsetY": 0}],
            "size": 9, "style": "", "align": "left", "wrap": True,
        }
        self._rows.append(new_row)
        self.rows_changed.emit()
        self._rebuild_cards()

    def _delete_row(self, ri: int) -> None:
        if 0 <= ri < len(self._rows):
            self._rows.pop(ri)
            self.rows_changed.emit()
            self._rebuild_cards()

    def _move_row(self, ri: int, delta: int) -> None:
        j = ri + delta
        if 0 <= j < len(self._rows):
            self._rows[ri], self._rows[j] = self._rows[j], self._rows[ri]
            self.rows_changed.emit()
            self._rebuild_cards()


# ── Constrained field text item ───────────────────────────────────────────────

class ConstrainedFieldItem(QGraphicsTextItem):
    """QGraphicsTextItem that clamps vertical movement within its row bounds.

    Horizontal movement is unconstrained. Vertical movement is clamped so the
    item stays within [row_top, row_bottom - item_height].
    """

    def __init__(
        self,
        text: str,
        row_top: float,
        row_bottom: float,
        parent=None,
    ) -> None:
        super().__init__(text, parent)
        self._row_top = row_top
        self._row_bottom = row_bottom
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges)

    def itemChange(self, change, value):
        if change == QGraphicsItem.GraphicsItemChange.ItemPositionChange:
            h = self.boundingRect().height()
            clamped_y = max(self._row_top, min(value.y(), self._row_bottom - h))
            return QPointF(value.x(), clamped_y)
        return super().itemChange(change, value)


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

    qr_moved = pyqtSignal(float, float)

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

            lh = resolve_line_height(tmpl, row)
            item = ConstrainedFieldItem(text, row_top=y_px, row_bottom=y_px + 1)
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
            row_height = item.boundingRect().height() * lh
            item._row_bottom = y_px + row_height
            item.setFlags(
                ConstrainedFieldItem.GraphicsItemFlag.ItemIsSelectable
                | ConstrainedFieldItem.GraphicsItemFlag.ItemIsMovable
                | ConstrainedFieldItem.GraphicsItemFlag.ItemSendsGeometryChanges
            )
            item.setTextInteractionFlags(
                Qt.TextInteractionFlag.TextEditorInteraction
            )
            item.setPos(_mm_to_px(1.5), y_px)   # 1.5 mm left margin
            item.setZValue(5)
            self.addItem(item)
            self._text_items.append(item)

            y_px += row_height

        self.setSceneRect(0, 0, w_px, h_px)

    # ── Public API ─────────────────────────────────────────────────────────────

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
        self.qr_moved.emit(_mm_to_px(x_mm), _mm_to_px(y_mm))
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
        # Enable right-click context menu (mirrors renderLabelPreviewContextMenu)
        self._view.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._view.customContextMenuRequested.connect(self._show_preview_context_menu)
        layout.addWidget(self._view)

        # Fit scene in view
        self._view.fitInView(self._scene.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)

        # Connect undo stack state
        self._undo_stack.canUndoChanged.connect(self._undo_btn.setEnabled)
        self._undo_stack.canRedoChanged.connect(self._redo_btn.setEnabled)

        # Keyboard shortcuts — mirrors handleLabelsKeydown() in app.js
        undo_sc = QShortcut(QKeySequence.StandardKey.Undo, self)
        undo_sc.activated.connect(self._undo_stack.undo)
        redo_sc = QShortcut(QKeySequence.StandardKey.Redo, self)
        redo_sc.activated.connect(self._undo_stack.redo)

        # QR position spinboxes (mm)
        self._updating_spins = False
        qr_pos_bar = QHBoxLayout()
        qr_pos_bar.setSpacing(4)
        qr_lbl = QLabel("QR 位置")
        qr_lbl.setStyleSheet("color: #87a2a1; font-size: 10px;")
        qr_pos_bar.addWidget(qr_lbl)

        x_lbl = QLabel("X:")
        x_lbl.setStyleSheet("color: #87a2a1; font-size: 10px;")
        qr_pos_bar.addWidget(x_lbl)
        self._qr_x_spin = QDoubleSpinBox()
        self._qr_x_spin.setRange(0, 200)
        self._qr_x_spin.setSuffix(" mm")
        self._qr_x_spin.setDecimals(1)
        self._qr_x_spin.setFixedWidth(80)
        self._qr_x_spin.setFixedHeight(22)
        self._qr_x_spin.setStyleSheet(
            "QDoubleSpinBox { background:#0f2127; border:1px solid rgba(145,182,181,0.18);"
            " border-radius:3px; color:#eef3ef; font-size:10px; padding:1px; }"
        )
        self._qr_x_spin.valueChanged.connect(self._on_qr_spin_changed)
        qr_pos_bar.addWidget(self._qr_x_spin)

        y_lbl = QLabel("Y:")
        y_lbl.setStyleSheet("color: #87a2a1; font-size: 10px;")
        qr_pos_bar.addWidget(y_lbl)
        self._qr_y_spin = QDoubleSpinBox()
        self._qr_y_spin.setRange(0, 200)
        self._qr_y_spin.setSuffix(" mm")
        self._qr_y_spin.setDecimals(1)
        self._qr_y_spin.setFixedWidth(80)
        self._qr_y_spin.setFixedHeight(22)
        self._qr_y_spin.setStyleSheet(
            "QDoubleSpinBox { background:#0f2127; border:1px solid rgba(145,182,181,0.18);"
            " border-radius:3px; color:#eef3ef; font-size:10px; padding:1px; }"
        )
        self._qr_y_spin.valueChanged.connect(self._on_qr_spin_changed)
        qr_pos_bar.addWidget(self._qr_y_spin)
        qr_pos_bar.addStretch()
        layout.addLayout(qr_pos_bar)

        # Connect scene → spin
        self._scene.qr_moved.connect(self._on_qr_moved)

        # Initialise spins from current QR position
        if self._scene.qr_item is not None:
            pos = self._scene.qr_item.pos()
            self._on_qr_moved(pos.x(), pos.y())

        # Row structural editor panel (mirrors renderEditorModeBar +
        # renderRowFloatingToolbar from app.js)
        self._row_editor = _RowEditorPanel(self)
        self._row_editor.set_rows(list(self._template.get("rows") or []))
        self._row_editor.rows_changed.connect(self._on_rows_changed)
        layout.addWidget(self._row_editor)

        # ── QR controls panel (mirrors web QR box in renderTemplateEditor) ──
        qr_frame = QFrame()
        qr_frame.setStyleSheet(
            "QFrame { background: #0c2027; border: 1px solid rgba(145,182,181,0.12);"
            " border-radius: 4px; padding: 4px; }"
        )
        qr_layout = QVBoxLayout(qr_frame)
        qr_layout.setContentsMargins(6, 4, 6, 4)
        qr_layout.setSpacing(4)

        qr_title = QLabel("QR 二维码位置")
        qr_title.setStyleSheet("color: #cfe0db; font-size: 10px; font-weight: bold;")
        qr_layout.addWidget(qr_title)

        qr_pos_row = QHBoxLayout()
        qr_pos_row.setSpacing(3)
        self._qr_pos_btns: dict[str, QPushButton] = {}
        for pos, name in [("left","左"), ("right","右"), ("top","上"), ("bottom","下"), ("none","无")]:
            btn = QPushButton(name)
            btn.setFixedSize(28, 20)
            btn.setCheckable(True)
            btn.setStyleSheet(
                "QPushButton { background:#0f2127; border:1px solid rgba(145,182,181,0.18);"
                " border-radius:3px; color:#87a2a1; font-size:10px; }"
                "QPushButton:checked { background:rgba(41,185,171,0.20);"
                " border-color:#29b9ab; color:#29b9ab; }"
            )
            btn.clicked.connect(lambda checked, _p=pos: self._set_qr_position(_p))
            qr_pos_row.addWidget(btn)
            self._qr_pos_btns[pos] = btn
        qr_pos_row.addStretch()
        qr_layout.addLayout(qr_pos_row)

        qr_size_row = QHBoxLayout()
        qr_size_row.setSpacing(6)
        qr_size_row.addWidget(QLabel("大小"))
        self._qr_size_slider = QSlider(Qt.Orientation.Horizontal)
        self._qr_size_slider.setRange(20, 70)
        self._qr_size_slider.setSingleStep(1)
        self._qr_size_slider.setFixedHeight(16)
        self._qr_size_slider.setStyleSheet(
            "QSlider::groove:horizontal { height:4px; background:#1a3540; border-radius:2px; }"
            "QSlider::handle:horizontal { background:#29b9ab; width:10px; height:10px;"
            " border-radius:5px; margin:-3px 0; }"
        )
        self._qr_size_lbl = QLabel("40%")
        self._qr_size_lbl.setFixedWidth(32)
        self._qr_size_lbl.setStyleSheet("color:#87a2a1; font-size:10px;")
        self._qr_size_slider.valueChanged.connect(self._on_qr_size_changed)
        qr_size_row.addWidget(self._qr_size_slider)
        qr_size_row.addWidget(self._qr_size_lbl)
        qr_layout.addLayout(qr_size_row)
        layout.addWidget(qr_frame)

        # ── Global line-height panel (mirrors web 全局排版 section) ──────
        lh_frame = QFrame()
        lh_frame.setStyleSheet(qr_frame.styleSheet())
        lh_layout = QVBoxLayout(lh_frame)
        lh_layout.setContentsMargins(6, 4, 6, 4)
        lh_layout.setSpacing(4)

        lh_title = QLabel("全局行高")
        lh_title.setStyleSheet("color: #cfe0db; font-size: 10px; font-weight: bold;")
        lh_layout.addWidget(lh_title)

        lh_preset_row = QHBoxLayout()
        lh_preset_row.setSpacing(4)
        for preset_name, preset_val in [("紧凑 1.1", 1.1), ("正常 1.3", 1.3), ("宽松 1.6", 1.6)]:
            pbtn = QPushButton(preset_name)
            pbtn.setFixedHeight(20)
            pbtn.setStyleSheet(
                "QPushButton { background:#0f2127; border:1px solid rgba(145,182,181,0.18);"
                " border-radius:3px; color:#87a2a1; font-size:9px; padding:0 4px; }"
                "QPushButton:hover { color:#29b9ab; border-color:#29b9ab; }"
            )
            pbtn.clicked.connect(lambda checked, v=preset_val: self._set_line_height(v))
            lh_preset_row.addWidget(pbtn)
        lh_preset_row.addStretch()
        lh_layout.addLayout(lh_preset_row)

        lh_slider_row = QHBoxLayout()
        lh_slider_row.setSpacing(6)
        lh_slider_row.addWidget(QLabel("行高"))
        self._lh_slider = QSlider(Qt.Orientation.Horizontal)
        self._lh_slider.setRange(80, 200)
        self._lh_slider.setSingleStep(5)
        self._lh_slider.setFixedHeight(16)
        self._lh_slider.setStyleSheet(self._qr_size_slider.styleSheet())
        self._lh_lbl = QLabel("1.30")
        self._lh_lbl.setFixedWidth(32)
        self._lh_lbl.setStyleSheet("color:#87a2a1; font-size:10px;")
        self._lh_slider.valueChanged.connect(self._on_lh_changed)
        lh_slider_row.addWidget(self._lh_slider)
        lh_slider_row.addWidget(self._lh_lbl)
        lh_layout.addLayout(lh_slider_row)
        layout.addWidget(lh_frame)

        # Sync controls to initial template state
        self._sync_qr_controls()
        self._sync_lh_controls()

    # ── QR spin callbacks ──────────────────────────────────────────────────

    def _on_qr_spin_changed(self) -> None:
        if self._updating_spins:
            return
        x_mm = self._qr_x_spin.value()
        y_mm = self._qr_y_spin.value()
        self._scene._set_qr_pos_mm(x_mm, y_mm, push_undo=False)

    def _on_qr_moved(self, x_px: float, y_px: float) -> None:
        self._updating_spins = True
        self._qr_x_spin.setValue(_px_to_mm(x_px))
        self._qr_y_spin.setValue(_px_to_mm(y_px))
        self._updating_spins = False

    # ── Row editor callbacks ───────────────────────────────────────────────

    def _on_rows_changed(self) -> None:
        """Called when _RowEditorPanel emits rows_changed. Push undo + rebuild scene."""
        new_rows = self._row_editor.get_rows()
        old_rows = list(self._template.get("rows") or [])
        cmd = _RowsCommand(self, old_rows, new_rows, "Row edit")
        self._undo_stack.push(cmd)

    def _apply_rows(self, rows: list) -> None:
        """Apply a rows list to the template (called by _RowsCommand undo/redo)."""
        self._template = copy.deepcopy(self._template)
        self._template["rows"] = rows
        self._row_editor.set_rows(rows)
        self._scene.rebuild(self._template, self._dims, self._label_data)
        self._view.fitInView(self._scene.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)
        self.template_changed.emit(copy.deepcopy(self._template))

    # ── Preview right-click context menu ──────────────────────────────────
    # Mirrors renderLabelPreviewContextMenu() in app.js

    def _show_preview_context_menu(self, pos: "QPointF") -> None:
        """Show right-click context menu on the label preview view."""
        menu = QMenu(self)
        menu.setStyleSheet(
            "QMenu { background: #10242a; color: #cfe0db;"
            " border: 1px solid rgba(145,182,181,0.20); }"
            "QMenu::item:selected { background: rgba(41,185,171,0.25); }"
            "QMenu::separator { background: rgba(145,182,181,0.12); height: 1px;"
            " margin: 2px 6px; }"
        )

        copy_act = menu.addAction("复制标签文本")
        copy_act.triggered.connect(self._copy_label_text)

        menu.addSeparator()

        qr_positions = ["right", "bottom", "left", "top", "none"]
        pos_names = {"right": "右", "bottom": "下", "left": "左", "top": "上", "none": "无"}
        cur_pos = (self._template.get("qr") or {}).get("position") or "right"
        next_pos = qr_positions[(qr_positions.index(cur_pos) + 1) % len(qr_positions)]
        qr_act = menu.addAction(
            f"切换 QR 位置（{pos_names.get(cur_pos, cur_pos)} → {pos_names.get(next_pos, next_pos)}）"
        )
        qr_act.triggered.connect(self._cycle_qr_position)

        menu.addSeparator()

        add_row_act = menu.addAction("+ 新增文本行")
        add_row_act.triggered.connect(self._row_editor._add_row)

        menu.exec(self._view.mapToGlobal(pos))

    def _copy_label_text(self) -> None:
        """Copy plain-text label summary to clipboard."""
        from app.utils.label_core import label_data_text
        text = label_data_text(self._label_data)
        QApplication.clipboard().setText(text)

    def _cycle_qr_position(self) -> None:
        """Cycle QR position right→bottom→left→top→none→right."""
        order = ["right", "bottom", "left", "top", "none"]
        tmpl = copy.deepcopy(self._template)
        qr_cfg = tmpl.setdefault("qr", {})
        cur = qr_cfg.get("position") or "right"
        qr_cfg["position"] = order[(order.index(cur) + 1) % len(order)]
        self._template = tmpl
        self._scene.rebuild(self._template, self._dims, self._label_data)
        self._view.fitInView(self._scene.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)
        self.template_changed.emit(copy.deepcopy(self._template))

    # ── QR position / size controls ────────────────────────────────────────

    def _set_qr_position(self, pos: str) -> None:
        tmpl = copy.deepcopy(self._template)
        qr = tmpl.setdefault("qr", {})
        qr["position"] = pos
        self._template = tmpl
        self._sync_qr_controls()
        self._scene.rebuild(self._template, self._dims, self._label_data)
        self._view.fitInView(self._scene.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)
        self.template_changed.emit(copy.deepcopy(self._template))

    def _on_qr_size_changed(self, value: int) -> None:
        pct = value / 100.0
        self._qr_size_lbl.setText(f"{value}%")
        tmpl = copy.deepcopy(self._template)
        qr = tmpl.setdefault("qr", {})
        qr["sizePct"] = pct
        self._template = tmpl
        self._scene.rebuild(self._template, self._dims, self._label_data)
        self._view.fitInView(self._scene.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)
        self.template_changed.emit(copy.deepcopy(self._template))

    def _sync_qr_controls(self) -> None:
        qr = self._template.get("qr") or {}
        cur_pos = qr.get("position") or "right"
        for p, btn in self._qr_pos_btns.items():
            btn.setChecked(p == cur_pos)
        pct = int((qr.get("sizePct") or 0.4) * 100)
        self._qr_size_slider.blockSignals(True)
        self._qr_size_slider.setValue(max(20, min(70, pct)))
        self._qr_size_slider.blockSignals(False)
        self._qr_size_lbl.setText(f"{pct}%")

    # ── Line-height controls ────────────────────────────────────────────────

    def _set_line_height(self, value: float) -> None:
        tmpl = copy.deepcopy(self._template)
        tmpl["lineHeight"] = value
        for row in (tmpl.get("rows") or []):
            row.pop("lineHeight", None)
        self._template = tmpl
        self._sync_lh_controls()
        self._scene.rebuild(self._template, self._dims, self._label_data)
        self._view.fitInView(self._scene.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)
        self.template_changed.emit(copy.deepcopy(self._template))

    def _on_lh_changed(self, value: int) -> None:
        lh = value / 100.0
        self._lh_lbl.setText(f"{lh:.2f}")
        tmpl = copy.deepcopy(self._template)
        tmpl["lineHeight"] = lh
        for row in (tmpl.get("rows") or []):
            row.pop("lineHeight", None)
        self._template = tmpl
        self._scene.rebuild(self._template, self._dims, self._label_data)
        self._view.fitInView(self._scene.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)
        self.template_changed.emit(copy.deepcopy(self._template))

    def _sync_lh_controls(self) -> None:
        lh = float(self._template.get("lineHeight") or 1.3)
        v = int(lh * 100)
        self._lh_slider.blockSignals(True)
        self._lh_slider.setValue(max(80, min(200, v)))
        self._lh_slider.blockSignals(False)
        self._lh_lbl.setText(f"{lh:.2f}")

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
        self._row_editor.set_rows(list(self._template.get("rows") or []))
        self._sync_qr_controls()
        self._sync_lh_controls()
        self._view.fitInView(self._scene.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)

    @property
    def undo_stack(self) -> QUndoStack:
        return self._undo_stack

    @property
    def scene(self) -> LabelScene:
        return self._scene

    @property
    def row_editor(self) -> _RowEditorPanel:
        """The structural row editor panel."""
        return self._row_editor

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        if hasattr(self, "_view") and self._scene:
            self._view.fitInView(
                self._scene.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio
            )
