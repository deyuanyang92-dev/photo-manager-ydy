"""labels_view.py — 标签打印 4-step wizard view.

View ID : "labels"
Nav     : 🏷️  标签打印

Faithful Qt port of the web prototype's classic Step 1-4 label page.
Structure mirrors renderLabelsClassic() → renderLabelStep1/2/3/4() in app.js.

4-step wizard
─────────────
Step 1 — 选择标本
    label-proj-row  : project selector drop-down
    label-spec-actions : 全选 / 仅 RNA / 仅样品 / 清空
    label-spec-grid : checkbox cards (label-spec-uid + label-spec-name + 🧬 badge)
    label-bucket-card : 🧪样品瓶标签 (N) / 🧬RNAlater 组织管 (M or hint)

Step 2 — 选择模版
    Two label-bucket-col columns (sample / tissue):
      label-bucket-col-head: icon + title + count hint + 导入JSON / 模板管理
      label-template-picker: label-tmpl-card per built-in (内置 badge + 编辑)
                             + 新建自定义 card
      label-render preview + 编辑标签内容 field editor
    Paper size buttons: 25×10 … 100×70 + 自定义 + W×H inputs

Step 3 — 编辑预览 (WYSIWYG)
    Bucket toggle: [样品瓶] / [RNAlater 组织管]
    LabelEditorWidget (QGraphicsScene, QR draggable, 2 mm safety margin, undo/redo)

Step 4 — 纸张 / 尺寸 / 份数 + 输出
    Per-bucket paper-type radio + paper-size buttons + grid preview
    份数 spinner
    [打印样品瓶标签 (N)]  /  [打印 RNAlater 组织管标签 (M)]
    Warnings list + hint text

Red lines (never relax):
  • R-prefix specimen enters BOTH sample and tissue buckets.
  • QR ECC = Q, 2 mm printer margin.
  • QPrinter PDF output via QPrintDialog.
"""

from __future__ import annotations

import copy
import json
import os
import tempfile
from typing import Optional

from PyQt6.QtCore import Qt, QMarginsF, QSizeF, QRectF, QTimer, pyqtSignal
from PyQt6.QtGui import QPainter, QPageSize, QColor, QFont, QPixmap
from PyQt6.QtPrintSupport import QPrinter, QPrintDialog, QAbstractPrintDialog
from PyQt6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QSplitter,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
    QComboBox,
    QFrame,
    QInputDialog,
)

from app.views.base_view import BaseView
from app.services.label_service import (
    BUILTIN_TEMPLATES,
    PAPER_SIZES,
    LabelService,
    LabelTemplateLibrary,
    is_library_key,
    key_from_id,
    id_from_key,
)
from app.utils.label_core import (
    normalize_template,
    unique_id,
    has_rna_tissue,
    specimen_to_label_data,
    qr_metrics,
    calculate_grid,
)
from app.widgets.label_editor import LabelEditorWidget

if TYPE_CHECKING := False:
    from app.app_context import AppContext


# ─────────────────────────────────────────────────────────────────────────────
# Theme helpers (deep-teal palette matching the web oracle)
# ─────────────────────────────────────────────────────────────────────────────

_CSS_SECTION = """
QWidget#LabelSection {
    background: #10242a;
    border: 1px solid rgba(145,182,181,0.12);
    border-radius: 6px;
}
"""

_CSS_BUCKET_CARD = """
QFrame#BucketCard {
    background: #0c2027;
    border: 1px solid rgba(145,182,181,0.16);
    border-radius: 6px;
    padding: 6px 10px;
}
QFrame#BucketCard[bucket="sample"] {
    border-left: 3px solid #29b9ab;
}
QFrame#BucketCard[bucket="tissue"] {
    border-left: 3px solid #4a90d9;
}
"""

_CSS_TMPL_CARD = """
QFrame#TmplCard {
    background: #0c2027;
    border: 1px solid rgba(145,182,181,0.14);
    border-radius: 5px;
    padding: 4px 8px;
}
QFrame#TmplCard[selected="true"] {
    border: 1.5px solid #29b9ab;
    background: #112e36;
}
"""

_CSS_SPEC_ITEM = """
QFrame#SpecItem {
    background: #0c2027;
    border: 1px solid rgba(145,182,181,0.10);
    border-radius: 4px;
    padding: 2px 6px;
}
QFrame#SpecItem[selected="true"] {
    border: 1px solid #29b9ab;
    background: #0f2f38;
}
"""

_CSS_SIZE_BTN = """
QPushButton#SizeBtn {
    background: #0c2027;
    border: 1px solid rgba(145,182,181,0.18);
    border-radius: 4px;
    color: #cfe0db;
    padding: 3px 7px;
    font-size: 11px;
}
QPushButton#SizeBtn:checked {
    background: rgba(41,185,171,0.20);
    border: 1.5px solid #29b9ab;
    color: #29b9ab;
}
QPushButton#SizeBtn:hover {
    border-color: #29b9ab;
}
"""

_CSS_OUTLINE_BTN = """
QPushButton#OutlineBtn {
    background: transparent;
    border: 1px solid rgba(145,182,181,0.25);
    border-radius: 4px;
    color: #cfe0db;
    padding: 3px 8px;
    font-size: 12px;
}
QPushButton#OutlineBtn:hover {
    border-color: #29b9ab;
    color: #29b9ab;
}
"""

_CSS_PRIMARY_BTN = """
QPushButton#PrimaryBtn {
    background: qlineargradient(x1:0,y1:0,x2:0,y2:1,stop:0 #33c8ba,stop:1 #23a99c);
    border: none;
    border-radius: 5px;
    color: #08161b;
    font-weight: bold;
    padding: 7px 18px;
    font-size: 13px;
    min-height: 34px;
}
QPushButton#PrimaryBtn:hover { background: #31d4c4; }
QPushButton#PrimaryBtn:pressed { background: #1f9288; }
QPushButton#PrimaryBtn:disabled {
    background: #1d3a44;
    color: #5f7d7a;
}
"""

_CSS_TISSUE_BTN = """
QPushButton#TissueBtn {
    background: qlineargradient(x1:0,y1:0,x2:0,y2:1,stop:0 #5a9fd4,stop:1 #3d7aad);
    border: none;
    border-radius: 5px;
    color: #ffffff;
    font-weight: bold;
    padding: 7px 18px;
    font-size: 13px;
    min-height: 34px;
}
QPushButton#TissueBtn:hover { background: #6db3e8; }
QPushButton#TissueBtn:pressed { background: #2f6694; }
QPushButton#TissueBtn:disabled {
    background: #1d3a44;
    color: #5f7d7a;
}
"""

_CSS_STEP_NAV = """
QPushButton#StepBtn {
    background: #0c2027;
    border: 1px solid rgba(145,182,181,0.18);
    border-radius: 14px;
    color: #87a2a1;
    padding: 4px 14px;
    font-size: 12px;
}
QPushButton#StepBtn:checked {
    background: rgba(41,185,171,0.18);
    border: 1.5px solid #29b9ab;
    color: #29b9ab;
    font-weight: bold;
}
QPushButton#StepBtn:hover { border-color: #29b9ab; color: #29b9ab; }
"""

_CSS_FULL = (
    _CSS_SECTION + _CSS_BUCKET_CARD + _CSS_TMPL_CARD + _CSS_SPEC_ITEM
    + _CSS_SIZE_BTN + _CSS_OUTLINE_BTN + _CSS_PRIMARY_BTN
    + _CSS_TISSUE_BTN + _CSS_STEP_NAV
)

# Paper size keys in display order (mirrors web labelSizeKeys)
_PAPER_SIZE_ORDER = [
    "label_25x10", "label_30x15", "label_40x20", "label_50x30",
    "label_60x40", "label_70x50", "label_80x60", "label_100x70",
]


def _render_label_pixmap(tmpl: dict, dims: dict, data: dict, scale: float = 3.78) -> QPixmap:
    """Render a label to QPixmap for Step 2 preview. Mirrors renderLabelEl(forScreen=True)."""
    from app.widgets.label_editor import _generate_qr_pixmap
    w_mm = float(dims.get("w", 60))
    h_mm = float(dims.get("h", 40))
    w_px = max(1, int(w_mm * scale))
    h_px = max(1, int(h_mm * scale))
    pixmap = QPixmap(w_px, h_px)
    pixmap.fill(QColor("white"))
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    qr_cfg = tmpl.get("qr") or {}
    ecc = qr_cfg.get("ecc") or "Q"
    metrics = qr_metrics(tmpl, dims)
    if metrics is not None:
        qr_text = str(data.get(qr_cfg.get("content") or "uniqueId") or "")
        size_px = max(20, int(metrics["sizeMm"] * scale))
        qr_pm = _generate_qr_pixmap(qr_text, size_px, ecc)
        if qr_pm:
            painter.drawPixmap(int(metrics["x"] * scale), int(metrics["y"] * scale), qr_pm)
    qr_w_px = metrics["sizeMm"] * scale if (metrics and qr_cfg.get("position") == "right") else 0.0
    text_w_px = max(1.0, float(w_px) - qr_w_px - 2 * scale)
    y_cur = 2.0 * scale
    for row in (tmpl.get("rows") or []):
        parts: list[str] = []
        for f in (row.get("fields") or []):
            k = f.get("key") if isinstance(f, dict) else str(f)
            v = data.get(k)
            if v is not None:
                parts.append(str(v))
        text = (row.get("sep") or " ").join(parts)
        if row.get("prefix"):
            text = row["prefix"] + text
        if not text:
            continue
        font = QFont()
        font.setPointSizeF(float(row.get("size") or 9))
        st = row.get("style") or ""
        font.setBold("bold" in st)
        font.setItalic("italic" in st)
        painter.setFont(font)
        fm = painter.fontMetrics()
        lh = fm.height()
        painter.drawText(QRectF(2 * scale, y_cur, text_w_px, lh * 1.5),
                         Qt.TextFlag.TextWordWrap, text)
        y_cur += lh * float(row.get("lineHeight") or 1.3)
    painter.end()
    return pixmap


# ─────────────────────────────────────────────────────────────────────────────
# Small utility widgets
# ─────────────────────────────────────────────────────────────────────────────

def _outline_btn(text: str) -> QPushButton:
    b = QPushButton(text)
    b.setObjectName("OutlineBtn")
    return b


def _primary_btn(text: str) -> QPushButton:
    b = QPushButton(text)
    b.setObjectName("PrimaryBtn")
    b.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
    return b


def _tissue_btn(text: str) -> QPushButton:
    b = QPushButton(text)
    b.setObjectName("TissueBtn")
    b.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
    return b


def _section_label(text: str, large: bool = False) -> QLabel:
    lbl = QLabel(text)
    size = "15px" if large else "13px"
    lbl.setStyleSheet(f"color: #eef3ef; font-size: {size}; font-weight: bold; margin-bottom: 4px;")
    return lbl


def _muted_label(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setStyleSheet("color: #87a2a1; font-size: 11px;")
    lbl.setWordWrap(True)
    return lbl


def _sep_line() -> QFrame:
    f = QFrame()
    f.setFrameShape(QFrame.Shape.HLine)
    f.setStyleSheet("color: rgba(145,182,181,0.15);")
    return f


def _warnings_html(warnings: list[dict]) -> str:
    if not warnings:
        return "<span style='color:#36c98f'>✓ 无警告</span>"
    lines: list[str] = []
    for w in warnings:
        color = "#e66e63" if w.get("level") == "error" else "#f1bd57"
        lines.append(f"<span style='color:{color}'>● {w.get('message','')}</span>")
    return "<br>".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# Step 1 — 选择标本
# ─────────────────────────────────────────────────────────────────────────────

class _Step1Widget(QWidget):
    """Step 1: project selector + specimen grid + dual-bucket summary cards.

    Mirrors: renderLabelStep1({ classic: true })
    DOM classes: label-proj-row / label-spec-actions / label-spec-grid
                 label-bucket-row / label-bucket-card (sample & tissue)
    """

    selection_changed = pyqtSignal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setStyleSheet("background: #08161b; color: #eef3ef;")

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 16, 20, 16)
        root.setSpacing(12)

        # ── Section title ─────────────────────────────────────────────
        root.addWidget(_section_label("选择标本", large=True))

        # ── label-proj-row ────────────────────────────────────────────
        proj_row = QHBoxLayout()
        proj_row.setSpacing(8)
        proj_row.addWidget(QLabel("项目: "))
        self._proj_combo = QComboBox()
        self._proj_combo.setMinimumWidth(220)
        self._proj_combo.setStyleSheet(
            "QComboBox { background: #0f2127; border: 1px solid rgba(145,182,181,0.18);"
            " border-radius:4px; color:#eef3ef; padding: 3px 8px; }"
            "QComboBox::drop-down { border:none; }"
        )
        proj_row.addWidget(self._proj_combo)
        proj_row.addStretch()
        root.addLayout(proj_row)

        # ── label-spec-actions ────────────────────────────────────────
        actions_row = QHBoxLayout()
        actions_row.setSpacing(6)
        self._btn_all = _outline_btn("全选")
        self._btn_rna = _outline_btn("仅 RNA")
        self._btn_sample_only = _outline_btn("仅样品")
        self._btn_clear = _outline_btn("清空")
        self._btn_rna.setToolTip("只勾选 R 前缀（RNA 组织保存于 RNAlater）的标本")
        self._btn_sample_only.setToolTip("只勾选非 R 前缀的标本")
        for b in [self._btn_all, self._btn_rna, self._btn_sample_only, self._btn_clear]:
            actions_row.addWidget(b)
        actions_row.addStretch()
        root.addLayout(actions_row)

        # ── label-spec-grid (scroll area) ─────────────────────────────
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet("QScrollArea { background: transparent; }")

        self._grid_container = QWidget()
        self._grid_container.setStyleSheet("background: transparent;")
        self._grid_layout = QVBoxLayout(self._grid_container)
        self._grid_layout.setContentsMargins(0, 0, 0, 0)
        self._grid_layout.setSpacing(3)
        scroll.setWidget(self._grid_container)
        root.addWidget(scroll, stretch=1)

        # ── label-bucket-row (two bucket cards) ─────────────────────
        bucket_row = QHBoxLayout()
        bucket_row.setSpacing(12)

        self._sample_card = self._make_bucket_card("sample")
        self._tissue_card = self._make_bucket_card("tissue")
        bucket_row.addWidget(self._sample_card)
        bucket_row.addWidget(self._tissue_card)
        root.addLayout(bucket_row)

        # ── Internal state ────────────────────────────────────────────
        self._specimens: list[dict] = []
        self._projects: list[dict] = []
        self._project_idx: int = 0
        self._selected: set[int] = set()  # indices into self._specimens
        self._checkboxes: list[QCheckBox] = []

        # ── Wire action buttons ───────────────────────────────────────
        self._btn_all.clicked.connect(self._select_all)
        self._btn_rna.clicked.connect(self._select_rna_only)
        self._btn_sample_only.clicked.connect(self._select_sample_only)
        self._btn_clear.clicked.connect(self._select_none)
        self._proj_combo.currentIndexChanged.connect(self._on_project_changed)

    # ── Bucket card factory ───────────────────────────────────────────

    def _make_bucket_card(self, bucket: str) -> QFrame:
        card = QFrame()
        card.setObjectName("BucketCard")
        card.setProperty("bucket", bucket)
        card.setStyle(card.style())  # force QSS re-evaluation after property set
        card.setStyleSheet(_CSS_BUCKET_CARD)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(2)

        icon = "🧪" if bucket == "sample" else "🧬"
        name = "样品瓶标签" if bucket == "sample" else "RNAlater 组织管"

        icon_lbl = QLabel(icon)
        icon_lbl.setStyleSheet("font-size: 18px;")
        name_lbl = QLabel(f"<b>{name}</b>")
        name_lbl.setStyleSheet("color: #eef3ef; font-size: 13px;")
        count_lbl = QLabel("0 个")
        count_lbl.setStyleSheet("color: #87a2a1; font-size: 12px;")
        hint_lbl = QLabel("")
        hint_lbl.setStyleSheet("color: #5f7d7a; font-size: 11px;")

        head_row = QHBoxLayout()
        head_row.setSpacing(6)
        head_row.addWidget(icon_lbl)
        head_row.addWidget(name_lbl)
        head_row.addStretch()

        layout.addLayout(head_row)
        layout.addWidget(count_lbl)
        layout.addWidget(hint_lbl)

        if bucket == "sample":
            self._sample_count_lbl = count_lbl
            self._sample_hint_lbl = hint_lbl
        else:
            self._tissue_count_lbl = count_lbl
            self._tissue_hint_lbl = hint_lbl

        return card

    # ── Public API ────────────────────────────────────────────────────

    def set_projects(self, projects: list[dict]) -> None:
        """Populate the project drop-down."""
        self._projects = projects
        self._proj_combo.blockSignals(True)
        self._proj_combo.clear()
        for p in projects:
            name = p.get("name") or p.get("projectName") or "未命名项目"
            year = p.get("year") or ""
            self._proj_combo.addItem(f"{name} ({year})" if year else name)
        self._proj_combo.blockSignals(False)

    def set_specimens(self, specimens: list[dict]) -> None:
        """Populate specimen grid from a list of camelCase dicts."""
        self._specimens = specimens
        self._selected = set(range(len(specimens)))  # all checked by default
        self._rebuild_grid()
        self._update_bucket_cards()
        self.selection_changed.emit()

    def selected_indices(self) -> list[int]:
        return sorted(self._selected)

    def specimens(self) -> list[dict]:
        return self._specimens

    def select_only_uid(self, uid: str) -> bool:
        """Select exactly the specimen whose generated uniqueId equals *uid*."""
        match_idx: Optional[int] = None
        for i, sp in enumerate(self._specimens):
            data = specimen_to_label_data(sp)
            if data.get("uniqueId") == uid:
                match_idx = i
                break
        if match_idx is None:
            return False
        self._selected = {match_idx}
        self._sync_checkboxes()
        self._update_bucket_cards()
        self.selection_changed.emit()
        return True

    # ── Internal helpers ──────────────────────────────────────────────

    def _rebuild_grid(self) -> None:
        # Clear existing widgets
        while self._grid_layout.count():
            item = self._grid_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._checkboxes.clear()

        for i, sp in enumerate(self._specimens):
            data = specimen_to_label_data(sp)
            uid_str = data.get("uniqueId") or sp.get("id") or "?"
            name_str = sp.get("species") or sp.get("scientificName") or "未命名"
            is_rna = has_rna_tissue(sp)

            frame = QFrame()
            frame.setObjectName("SpecItem")
            frame.setProperty("selected", str(i in self._selected).lower())
            frame.setStyleSheet(_CSS_SPEC_ITEM)

            row = QHBoxLayout(frame)
            row.setContentsMargins(4, 3, 4, 3)
            row.setSpacing(6)

            cb = QCheckBox()
            cb.setChecked(i in self._selected)
            cb.setStyleSheet("QCheckBox::indicator { width:14px; height:14px; }")

            uid_lbl = QLabel(uid_str)
            uid_lbl.setStyleSheet(
                "font-family: 'Courier New', monospace; font-size: 11px; color: #eef3ef;"
            )
            uid_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)

            name_lbl = QLabel(name_str)
            name_lbl.setStyleSheet("font-size: 11px; color: #87a2a1;")

            row.addWidget(cb)
            row.addWidget(uid_lbl)
            row.addWidget(name_lbl)

            if is_rna:
                badge = QLabel("🧬")
                badge.setToolTip("R 前缀：已取 RNA 并保存于 RNAlater；将额外打印 RNAlater 组织管标签")
                badge.setStyleSheet("font-size: 12px;")
                row.addWidget(badge)

            row.addStretch()

            idx = i  # capture

            def _on_toggle(checked: bool, idx: int = idx, frame: QFrame = frame) -> None:
                if checked:
                    self._selected.add(idx)
                else:
                    self._selected.discard(idx)
                frame.setProperty("selected", str(idx in self._selected).lower())
                frame.setStyleSheet(_CSS_SPEC_ITEM)
                self._update_bucket_cards()
                self.selection_changed.emit()

            cb.toggled.connect(_on_toggle)
            self._checkboxes.append(cb)
            self._grid_layout.addWidget(frame)

        self._grid_layout.addStretch()

    def _update_bucket_cards(self) -> None:
        sample_n = 0
        tissue_n = 0
        for i in self._selected:
            if i < len(self._specimens):
                sample_n += 1
                if has_rna_tissue(self._specimens[i]):
                    tissue_n += 1
        self._sample_count_lbl.setText(f"{sample_n} 个")
        self._tissue_count_lbl.setText(f"{tissue_n} 个")

        if tissue_n == 0:
            self._tissue_hint_lbl.setText("选中标本无 R 前缀")
        else:
            self._tissue_hint_lbl.setText("")

        # Update default template hint on sample card
        self._sample_hint_lbl.setText("默认 标准 · 50×30")

    # ── Quick-select actions ──────────────────────────────────────────

    def _select_all(self) -> None:
        self._selected = set(range(len(self._specimens)))
        self._sync_checkboxes()
        self._update_bucket_cards()
        self.selection_changed.emit()

    def _select_rna_only(self) -> None:
        self._selected = {
            i for i, sp in enumerate(self._specimens) if has_rna_tissue(sp)
        }
        self._sync_checkboxes()
        self._update_bucket_cards()
        self.selection_changed.emit()

    def _select_sample_only(self) -> None:
        self._selected = {
            i for i, sp in enumerate(self._specimens) if not has_rna_tissue(sp)
        }
        self._sync_checkboxes()
        self._update_bucket_cards()
        self.selection_changed.emit()

    def _select_none(self) -> None:
        self._selected.clear()
        self._sync_checkboxes()
        self._update_bucket_cards()
        self.selection_changed.emit()

    def _sync_checkboxes(self) -> None:
        for i, cb in enumerate(self._checkboxes):
            cb.blockSignals(True)
            cb.setChecked(i in self._selected)
            cb.blockSignals(False)

    def _on_project_changed(self, idx: int) -> None:
        self._project_idx = idx


# ─────────────────────────────────────────────────────────────────────────────
# Step 2 — 选择模版
# ─────────────────────────────────────────────────────────────────────────────

# Paper size display buttons order (mirrors web labelSizeKeys)
_SIZE_BUTTONS = [
    ("label_25x10",  "25×10mm"),
    ("label_30x15",  "30×15mm"),
    ("label_40x20",  "40×20mm"),
    ("label_50x30",  "50×30mm"),
    ("label_60x40",  "60×40mm"),
    ("label_70x50",  "70×50mm"),
    ("label_80x60",  "80×60mm"),
    ("label_100x70", "100×70mm"),
    ("custom",       "自定义"),
]


class _BucketColWidget(QWidget):
    """One bucket column inside Step 2 (sample or tissue).

    Mirrors renderBucketColumn(bucket, items, { classic: true }).
    Contains:
      - head: icon + name + count hint + 导入JSON / 模板管理 buttons
      - label-template-picker: built-in cards + 新建自定义 card
      - paper size buttons
      - custom W×H inputs (when "自定义" selected)
      - 编辑标签内容 field-edit section
    """

    def __init__(self, bucket: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._bucket = bucket
        self._is_tissue = bucket == "tissue"
        # Use QSettings-backed library for persistence
        self._lib = LabelTemplateLibrary(bucket)
        # Load persisted selections
        self._selected_template_key: str = self._lib.selected_key()
        self._selected_size_key: str = self._lib.selected_size_key()
        self._custom_w: int = 30 if self._is_tissue else 50
        self._custom_h: int = 15 if self._is_tissue else 30
        self._specimens: list[dict] = []
        self._selected_indices: list[int] = []
        # Per-specimen labelEdits: {spec_idx: {field_key: value}}
        self._label_edits: dict[int, dict[str, str]] = {}

        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(8)

        # ── Head ──────────────────────────────────────────────────────
        head = QFrame()
        head.setStyleSheet(
            "QFrame { background: #10242a; border-radius: 6px; "
            "border: 1px solid rgba(145,182,181,0.12); padding: 6px 10px; }"
        )
        head_row = QHBoxLayout(head)
        head_row.setContentsMargins(8, 6, 8, 6)
        head_row.setSpacing(6)

        icon = "🧬" if self._is_tissue else "🧪"
        name = "RNAlater 组织管" if self._is_tissue else "样品瓶"

        icon_lbl = QLabel(icon)
        icon_lbl.setStyleSheet("font-size: 16px;")
        head_row.addWidget(icon_lbl)

        text_col = QVBoxLayout()
        text_col.setSpacing(1)
        self._head_name_lbl = QLabel(f"<b>{name}</b>")
        self._head_name_lbl.setStyleSheet("color: #eef3ef; font-size: 13px;")
        self._head_sub_lbl = QLabel("0 个标签 · 先选模板，再预览编辑")
        self._head_sub_lbl.setStyleSheet("color: #87a2a1; font-size: 11px;")
        text_col.addWidget(self._head_name_lbl)
        text_col.addWidget(self._head_sub_lbl)
        head_row.addLayout(text_col)
        head_row.addStretch()

        self._import_btn = _outline_btn("导入 JSON")
        self._manage_btn = _outline_btn("模板管理")
        self._import_btn.setFixedHeight(24)
        self._manage_btn.setFixedHeight(24)
        self._import_btn.clicked.connect(self._import_json)
        self._manage_btn.clicked.connect(self._open_manage_dialog)
        head_row.addWidget(self._import_btn)
        head_row.addWidget(self._manage_btn)
        root.addWidget(head)

        # ── label-template-picker ─────────────────────────────────────
        tmpl_scroll = QScrollArea()
        tmpl_scroll.setWidgetResizable(True)
        tmpl_scroll.setFrameShape(QFrame.Shape.NoFrame)
        tmpl_scroll.setFixedHeight(220)
        tmpl_scroll.setStyleSheet("QScrollArea { background: transparent; }")
        tmpl_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        tmpl_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        picker_container = QWidget()
        picker_container.setStyleSheet("background: transparent;")
        self._picker_layout = QHBoxLayout(picker_container)
        self._picker_layout.setContentsMargins(0, 0, 0, 0)
        self._picker_layout.setSpacing(6)
        self._picker_layout.addStretch()
        tmpl_scroll.setWidget(picker_container)
        root.addWidget(tmpl_scroll)
        self._tmpl_scroll = tmpl_scroll
        self._picker_container = picker_container

        # ── 大预览 (mirrors web label-preview-card) ───────────────────
        preview_frame = QFrame()
        preview_frame.setStyleSheet(
            "QFrame { background: #1a3540; border: 1px solid rgba(145,182,181,0.20);"
            " border-radius: 5px; padding: 4px; }"
        )
        preview_frame.setFixedHeight(130)
        preview_inner = QVBoxLayout(preview_frame)
        preview_inner.setContentsMargins(4, 4, 4, 4)
        preview_inner.setSpacing(3)
        self._large_preview_label = QLabel("实时预览")
        self._large_preview_label.setStyleSheet("color: #87a2a1; font-size: 10px;")
        self._large_preview_img = QLabel()
        self._large_preview_img.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._large_preview_img.setStyleSheet("background: transparent;")
        preview_inner.addWidget(self._large_preview_label)
        preview_inner.addWidget(self._large_preview_img, stretch=1)
        root.addWidget(preview_frame)

        # ── Paper size section ────────────────────────────────────────
        paper_section = QWidget()
        paper_section.setStyleSheet("background: transparent;")
        paper_col = QVBoxLayout(paper_section)
        paper_col.setContentsMargins(0, 0, 0, 0)
        paper_col.setSpacing(4)
        paper_col.addWidget(QLabel("纸张尺寸:"))

        size_row_w = QWidget()
        size_row_w.setStyleSheet("background: transparent;")
        self._size_row = QHBoxLayout(size_row_w)
        self._size_row.setContentsMargins(0, 0, 0, 0)
        self._size_row.setSpacing(4)
        self._size_btns: dict[str, QPushButton] = {}

        for key, label in _SIZE_BUTTONS:
            btn = QPushButton(label)
            btn.setObjectName("SizeBtn")
            btn.setCheckable(True)
            btn.setStyleSheet(_CSS_SIZE_BTN)
            btn.setFixedHeight(26)
            btn.clicked.connect(lambda checked, k=key: self._select_size(k))
            self._size_row.addWidget(btn)
            self._size_btns[key] = btn
        self._size_row.addStretch()
        paper_col.addWidget(size_row_w)

        # Custom dims inputs (hidden unless "自定义" selected)
        self._custom_dims_widget = QWidget()
        self._custom_dims_widget.setStyleSheet("background: transparent;")
        cdims_row = QHBoxLayout(self._custom_dims_widget)
        cdims_row.setContentsMargins(0, 0, 0, 0)
        cdims_row.setSpacing(4)
        cdims_row.addWidget(QLabel("宽"))
        self._w_input = QSpinBox()
        self._w_input.setRange(10, 300)
        self._w_input.setValue(self._custom_w)
        self._w_input.setSuffix(" mm")
        self._w_input.setFixedWidth(72)
        self._w_input.setStyleSheet(
            "QSpinBox { background:#0f2127; border:1px solid rgba(145,182,181,0.18);"
            " border-radius:4px; color:#eef3ef; padding:2px 4px; }"
        )
        cdims_row.addWidget(self._w_input)
        cdims_row.addWidget(QLabel("× 高"))
        self._h_input = QSpinBox()
        self._h_input.setRange(5, 300)
        self._h_input.setValue(self._custom_h)
        self._h_input.setSuffix(" mm")
        self._h_input.setFixedWidth(72)
        self._h_input.setStyleSheet(self._w_input.styleSheet())
        cdims_row.addWidget(self._h_input)
        cdims_row.addStretch()
        self._custom_dims_widget.setVisible(False)
        paper_col.addWidget(self._custom_dims_widget)

        root.addWidget(paper_section)

        self._w_input.valueChanged.connect(lambda v: setattr(self, '_custom_w', v))
        self._h_input.valueChanged.connect(lambda v: setattr(self, '_custom_h', v))

        # ── 编辑标签内容 (only for sample bucket, mirrors web) ─────────
        if not self._is_tissue:
            root.addWidget(_sep_line())
            self._edit_section_container = QWidget()
            self._edit_section_container.setStyleSheet("background: transparent;")
            edit_col = QVBoxLayout(self._edit_section_container)
            edit_col.setContentsMargins(0, 0, 0, 0)
            edit_col.setSpacing(4)
            self._edit_title_lbl = QLabel("编辑标签内容")
            self._edit_title_lbl.setStyleSheet(
                "color: #cfe0db; font-size: 12px; font-weight: bold;"
            )
            edit_col.addWidget(self._edit_title_lbl)
            self._edit_fields_widget = QWidget()
            self._edit_fields_widget.setStyleSheet("background: transparent;")
            self._edit_fields_layout = QVBoxLayout(self._edit_fields_widget)
            self._edit_fields_layout.setContentsMargins(0, 0, 0, 0)
            self._edit_fields_layout.setSpacing(3)
            edit_col.addWidget(self._edit_fields_widget)
            root.addWidget(self._edit_section_container)
            self._field_inputs: dict[str, QLineEdit] = {}
        else:
            self._field_inputs = {}

        root.addStretch()

        # Apply default size selection
        self._apply_size_selection()

    # ── Public API ────────────────────────────────────────────────────

    def refresh(self, specimens: list[dict], selected_indices: list[int]) -> None:
        """Rebuild template cards and field editor for a new specimen selection."""
        self._specimens = specimens
        self._selected_indices = selected_indices
        count = len(selected_indices)

        sub = f"{count} 个标签 · 先选模板，再预览编辑"
        if self._is_tissue:
            tissue_count = sum(
                1 for i in selected_indices
                if i < len(specimens) and has_rna_tissue(specimens[i])
            )
            sub = f"{tissue_count} 个标签 · 先选模板，再预览编辑"
        self._head_sub_lbl.setText(sub)

        self._rebuild_template_picker()
        if not self._is_tissue:
            self._rebuild_field_editor()

    def selected_template(self) -> dict:
        """Return the normalized active template dict."""
        key = self._selected_template_key
        # Library record: custom:<id>
        if is_library_key(key):
            rec_id = id_from_key(key)
            rec = self._lib.get(rec_id)
            if rec and rec.get("template"):
                return normalize_template(rec["template"])
            # Fallback if record was deleted
            default_key = "tissueCompact" if self._is_tissue else "standard"
            self._selected_template_key = default_key
            return normalize_template(BUILTIN_TEMPLATES[default_key])
        # Built-in template
        return normalize_template(BUILTIN_TEMPLATES.get(key, BUILTIN_TEMPLATES.get(
            "tissueCompact" if self._is_tissue else "standard"
        )))

    def selected_dims(self) -> dict:
        """Return label dimensions in mm."""
        key = self._selected_size_key
        if key == "custom":
            return {"w": self._custom_w, "h": self._custom_h}
        size = PAPER_SIZES.get(key)
        if size:
            return {"w": size["w"], "h": size["h"]}
        return {"w": 50, "h": 30}

    # ── Internal: rebuild template cards ─────────────────────────────

    def _rebuild_template_picker(self) -> None:
        # Remove all items
        while self._picker_layout.count() > 0:
            item = self._picker_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        # First specimen for preview
        first_data: dict = {}
        if self._selected_indices and self._specimens:
            idx = self._selected_indices[0]
            if idx < len(self._specimens):
                first_data = specimen_to_label_data(self._specimens[idx])

        dims = self.selected_dims()

        # ── Built-in templates ────────────────────────────────────────
        for key, tmpl in BUILTIN_TEMPLATES.items():
            is_tissue_tmpl = tmpl.get("flavor") == "tissue"
            if self._is_tissue and not is_tissue_tmpl:
                continue
            if not self._is_tissue and is_tissue_tmpl:
                continue
            card = self._make_template_card(key, tmpl, first_data, dims)
            self._picker_layout.addWidget(card)

        # ── Library custom templates ──────────────────────────────────
        for rec in self._lib.records():
            rec_key = key_from_id(rec["id"])
            card = self._make_template_card(
                rec_key, rec["template"], first_data, dims,
                badge="自定义", lib_rec=rec,
            )
            self._picker_layout.addWidget(card)

        # ── 新建自定义 card ───────────────────────────────────────────
        add_card = QFrame()
        add_card.setObjectName("TmplCard")
        add_card.setProperty("selected", "false")
        add_card.setStyleSheet(_CSS_TMPL_CARD)
        add_card.setFixedWidth(130)
        add_card.setCursor(Qt.CursorShape.PointingHandCursor)
        add_layout = QVBoxLayout(add_card)
        add_layout.setContentsMargins(6, 6, 6, 6)
        add_layout.setSpacing(4)
        title_row2 = QHBoxLayout()
        title_row2.addWidget(QLabel("<b>新建自定义</b>"))
        title_row2.addStretch()
        add_layout.addLayout(title_row2)
        hint2 = QLabel("从默认模板复制一份，可独立保存管理")
        hint2.setStyleSheet("color: #5f7d7a; font-size: 10px;")
        hint2.setWordWrap(True)
        add_layout.addWidget(hint2)
        add_layout.addStretch()

        def _new_custom_click(_e: object) -> None:
            base_key = "tissueCompact" if self._is_tissue else "standard"
            base = BUILTIN_TEMPLATES.get(base_key, {})
            rec = self._lib.clone_from_builtin(base, base.get("name", base_key))
            self._selected_template_key = key_from_id(rec["id"])
            self._lib.select_record(rec["id"])
            self._rebuild_template_picker()
            if not self._is_tissue:
                self._rebuild_field_editor()

        add_card.mousePressEvent = _new_custom_click  # type: ignore[method-assign]
        self._picker_layout.addWidget(add_card)
        self._picker_layout.addStretch()

        # Update large preview after rebuilding cards
        self._update_large_preview()

    def _update_large_preview(self) -> None:
        """Refresh the large label preview. Mirrors web label-preview-card."""
        first_data: dict = {}
        if self._selected_indices and self._specimens:
            idx = self._selected_indices[0]
            if idx < len(self._specimens):
                first_data = specimen_to_label_data(self._specimens[idx])
        tmpl = normalize_template(self.selected_template())
        dims = self.selected_dims()
        uid = first_data.get("uniqueId") or "—"
        self._large_preview_label.setText(
            f"实时预览 — {uid} · {dims.get('w', 0)}×{dims.get('h', 0)}mm"
        )
        pixmap = _render_label_pixmap(tmpl, dims, first_data, scale=3.78)
        # Scale to fit in the preview widget
        available_h = 90
        if pixmap.height() > 0:
            ratio = available_h / pixmap.height()
            scaled = pixmap.scaled(
                int(pixmap.width() * ratio), available_h,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            self._large_preview_img.setPixmap(scaled)
        else:
            self._large_preview_img.setText("—")

    def _make_template_card(
        self,
        key: str,
        tmpl: dict,
        first_data: dict,
        dims: dict,
        badge: str = "内置",
        lib_rec: Optional[dict] = None,
    ) -> QFrame:
        is_selected = key == self._selected_template_key
        card = QFrame()
        card.setObjectName("TmplCard")
        card.setProperty("selected", str(is_selected).lower())
        card.setStyleSheet(_CSS_TMPL_CARD)
        card.setFixedWidth(140)
        card.setCursor(Qt.CursorShape.PointingHandCursor)

        layout = QVBoxLayout(card)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(4)

        # Header row: badge + name + action button
        header_row = QHBoxLayout()
        header_row.setSpacing(4)
        badge_lbl = QLabel(badge)
        badge_color = "#4a90d9" if badge == "自定义" else "#29b9ab"
        badge_lbl.setStyleSheet(
            f"background: rgba({badge_color.replace('#','')},0.15);"  # fallback raw color
            f" color:{badge_color}; border-radius:3px; font-size:10px; padding: 1px 4px;"
        )
        # Use fixed colors for cleaner look
        if badge == "自定义":
            badge_lbl.setStyleSheet(
                "background: rgba(74,144,217,0.18); color:#4a90d9; border-radius:3px;"
                " font-size:10px; padding: 1px 4px;"
            )
        header_row.addWidget(badge_lbl)
        name_lbl = QLabel(f"<b>{tmpl.get('name', key)}</b>")
        name_lbl.setStyleSheet("color:#eef3ef; font-size:10px;")
        header_row.addWidget(name_lbl, stretch=1)

        if lib_rec is not None:
            # Library record: show a "管理" button with dropdown menu
            mgmt_btn = QPushButton("管理")
            mgmt_btn.setFixedHeight(18)
            mgmt_btn.setFixedWidth(38)
            mgmt_btn.setStyleSheet(
                "QPushButton { background:transparent; border:1px solid rgba(145,182,181,0.25);"
                " border-radius:3px; color:#87a2a1; font-size:9px; padding:0; }"
                "QPushButton:hover { color:#29b9ab; border-color:#29b9ab; }"
            )
            mgmt_btn.setToolTip("重命名、复制、导出、删除")
            rec_id = lib_rec["id"]
            rec_name = lib_rec["name"]

            def _show_mgmt_menu(checked: bool, _rid: str = rec_id, _rname: str = rec_name) -> None:
                menu = QMenu(self)
                menu.setStyleSheet(
                    "QMenu { background:#10242a; color:#cfe0db; border:1px solid rgba(145,182,181,0.18); }"
                    "QMenu::item:selected { background: rgba(41,185,171,0.25); }"
                )
                menu.addAction("✓ 选用", lambda: self._lib_use(_rid))
                menu.addAction("重命名", lambda: self._lib_rename(_rid, _rname))
                menu.addAction("复制", lambda: self._lib_copy(_rid))
                menu.addAction("导出 JSON", lambda: self._lib_export(_rid))
                menu.addSeparator()
                act_del = menu.addAction("删除", lambda: self._lib_delete(_rid, _rname))
                act_del.setEnabled(True)
                menu.exec(mgmt_btn.mapToGlobal(mgmt_btn.rect().bottomLeft()))

            mgmt_btn.clicked.connect(_show_mgmt_menu)
            header_row.addWidget(mgmt_btn)
        else:
            # Built-in template: show "编辑" (clone to library)
            edit_btn = QPushButton("编辑")
            edit_btn.setFixedHeight(18)
            edit_btn.setFixedWidth(36)
            edit_btn.setStyleSheet(
                "QPushButton { background:transparent; border:1px solid rgba(145,182,181,0.25);"
                " border-radius:3px; color:#87a2a1; font-size:9px; padding:0; }"
                "QPushButton:hover { color:#29b9ab; border-color:#29b9ab; }"
            )
            edit_btn.setToolTip("复制为可编辑（不污染预设）")
            edit_btn.clicked.connect(lambda checked, k=key, t=tmpl: self._clone_to_lib(k, t))
            header_row.addWidget(edit_btn)

        layout.addLayout(header_row)

        # Mini preview
        preview_frame = QFrame()
        preview_frame.setStyleSheet(
            "QFrame { background: white; border: 1px solid #ccc; border-radius: 2px; }"
        )
        preview_frame.setFixedHeight(55)
        preview_layout = QVBoxLayout(preview_frame)
        preview_layout.setContentsMargins(3, 2, 3, 2)
        preview_layout.setSpacing(1)
        norm = normalize_template(tmpl)
        for row in (norm.get("rows") or [])[:4]:
            fields = row.get("fields") or []
            parts = []
            for f in fields:
                k2 = f.get("key") if isinstance(f, dict) else str(f)
                val = first_data.get(k2)
                if val:
                    parts.append(str(val))
            text = (row.get("sep") or " ").join(parts) if parts else "—"
            size = max(8, int(row.get("size") or 9))
            pl = QLabel(text[:22])
            pl.setStyleSheet(
                f"color:#222; font-size:{min(size, 10)}px; background:transparent; border:none;"
            )
            preview_layout.addWidget(pl)
        preview_layout.addStretch()
        layout.addWidget(preview_frame)

        if lib_rec is not None:
            upd = lib_rec.get("updatedAt", "")[:10]
            meta_lbl = QLabel(f"自定义 · {upd}")
            meta_lbl.setStyleSheet("color:#5f7d7a; font-size:9px;")
            layout.addWidget(meta_lbl)

        # Click to select
        def _select(event: object, k: str = key, _lr: Optional[dict] = lib_rec) -> None:
            self._selected_template_key = k
            if _lr is not None:
                self._lib.select_record(_lr["id"])
            else:
                self._lib.set_selected_key(k)
            self._rebuild_template_picker()
            if not self._is_tissue:
                self._rebuild_field_editor()

        card.mousePressEvent = _select  # type: ignore[method-assign]
        return card

    def _clone_to_custom(self, src_key: str, src_tmpl: dict) -> None:
        import copy
        clone = copy.deepcopy(src_tmpl)
        clone["name"] = f"自定义（基于 {src_tmpl.get('name', src_key)}）"
        if self._is_tissue:
            clone["flavor"] = "tissue"
        # Legacy path used only if someone calls this directly; prefer _clone_to_lib
        rec = self._lib.upsert({"name": clone["name"], "source": src_key, "template": clone})
        self._selected_template_key = key_from_id(rec["id"])
        self._lib.select_record(rec["id"])
        self._rebuild_template_picker()
        if not self._is_tissue:
            self._rebuild_field_editor()

    def _clone_to_lib(self, src_key: str, src_tmpl: dict) -> None:
        """Clone a built-in template into the library."""
        rec = self._lib.clone_from_builtin(src_tmpl, src_tmpl.get("name", src_key))
        self._selected_template_key = key_from_id(rec["id"])
        self._rebuild_template_picker()
        if not self._is_tissue:
            self._rebuild_field_editor()

    def _import_json(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "导入标签模板 JSON", "", "JSON 文件 (*.json)"
        )
        if not path:
            return
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
            if not isinstance(data, dict) or "rows" not in data:
                raise ValueError("JSON 结构不正确，缺少 'rows' 字段")
            tmpl_name = data.get("name") or os.path.basename(path).replace(".json", "")
            rec = self._lib.upsert({
                "name": tmpl_name,
                "source": "import",
                "template": data,
            })
            self._selected_template_key = key_from_id(rec["id"])
            self._lib.select_record(rec["id"])
            self._rebuild_template_picker()
        except Exception as e:
            QMessageBox.warning(self, "导入失败", f"无法解析模板 JSON:\n{e}")

    def _open_manage_dialog(self) -> None:
        dlg = _TemplateManageDialog(self._lib, parent=self)
        dlg.exec()
        # Refresh picker after dialog closes (user may have renamed/deleted)
        self._rebuild_template_picker()

    # ── Library record quick-actions (called from card menus) ─────────

    def _lib_use(self, rec_id: str) -> None:
        self._selected_template_key = key_from_id(rec_id)
        self._lib.select_record(rec_id)
        self._rebuild_template_picker()
        if not self._is_tissue:
            self._rebuild_field_editor()

    def _lib_rename(self, rec_id: str, current_name: str) -> None:
        new_name, ok = QInputDialog.getText(self, "重命名", "新名称:", text=current_name)
        if ok and new_name.strip():
            self._lib.rename(rec_id, new_name.strip())
            self._rebuild_template_picker()

    def _lib_copy(self, rec_id: str) -> None:
        new_rec = self._lib.duplicate(rec_id)
        if new_rec:
            self._rebuild_template_picker()

    def _lib_export(self, rec_id: str) -> None:
        rec = self._lib.get(rec_id)
        if not rec:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "导出模板 JSON",
            rec["name"].replace(" ", "_") + ".json",
            "JSON 文件 (*.json)",
        )
        if path:
            try:
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(rec["template"], f, ensure_ascii=False, indent=2)
            except Exception as e:
                QMessageBox.warning(self, "导出失败", str(e))

    def _lib_delete(self, rec_id: str, name: str) -> None:
        resp = QMessageBox.question(
            self, "删除确认",
            f"确定删除自定义模板「{name}」？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if resp == QMessageBox.StandardButton.Yes:
            self._lib.delete(rec_id)
            # If deleted template was active, fall back to default
            if self._selected_template_key == key_from_id(rec_id):
                default = "tissueCompact" if self._is_tissue else "standard"
                self._selected_template_key = default
                self._lib.set_selected_key(default)
            self._rebuild_template_picker()

    # ── Internal: rebuild field editor ───────────────────────────────

    def _rebuild_field_editor(self) -> None:
        if self._is_tissue:
            return
        # Clear
        while self._edit_fields_layout.count():
            item = self._edit_fields_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._field_inputs.clear()

        if not self._selected_indices or not self._specimens:
            return

        idx = self._selected_indices[0]
        if idx >= len(self._specimens):
            return
        sp = self._specimens[idx]
        first_data = specimen_to_label_data(sp)
        sp_id = sp.get("id") or "?"
        self._edit_title_lbl.setText(f"编辑标签内容 ({sp_id})")

        tmpl = normalize_template(self.selected_template())
        field_set: list[str] = []
        seen: set[str] = set()
        for row in (tmpl.get("rows") or []):
            for f in (row.get("fields") or []):
                k = f.get("key") if isinstance(f, dict) else str(f)
                if k and k not in seen:
                    field_set.append(k)
                    seen.add(k)

        _field_names = {
            "uniqueId": "唯一编号", "headerId": "编号头", "storage": "保存方式",
            "shortDate": "日期段", "fullDate": "完整日期段",
            "speciesName": "物种名称", "latin": "拉丁名", "family": "科",
            "region": "地点", "collectorLabel": "采集人",
            "photographer": "拍摄者", "lon": "经度", "lat": "纬度",
            "geoArea": "采集地理区",
        }

        for field_key in field_set:
            row_w = QWidget()
            row_w.setStyleSheet("background: transparent;")
            row_layout = QHBoxLayout(row_w)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.setSpacing(6)

            lbl = QLabel((_field_names.get(field_key) or field_key) + ":")
            lbl.setFixedWidth(72)
            lbl.setStyleSheet("color:#cfe0db; font-size:11px;")

            # Per-specimen edits: {idx: {field: val}}
            sp_edits = self._label_edits.get(idx, {})
            current_val = sp_edits.get(field_key, first_data.get(field_key) or "")
            inp = QLineEdit(str(current_val))
            inp.setStyleSheet(
                "QLineEdit { background:#0f2127; border:1px solid rgba(145,182,181,0.18);"
                " border-radius:4px; color:#eef3ef; padding:2px 6px; font-size:11px; }"
                "QLineEdit:focus { border-color:#29b9ab; }"
            )

            def _on_field_change(text: str, _k: str = field_key, _idx: int = idx) -> None:
                if _idx not in self._label_edits:
                    self._label_edits[_idx] = {}
                self._label_edits[_idx][_k] = text

            inp.textChanged.connect(_on_field_change)
            row_layout.addWidget(lbl)
            row_layout.addWidget(inp)
            self._field_inputs[field_key] = inp
            self._edit_fields_layout.addWidget(row_w)

    def label_edits(self) -> dict[int, dict[str, str]]:
        """Return per-specimen field edits dict {idx: {field: value}}."""
        return dict(self._label_edits)

    # ── Internal: size selection ──────────────────────────────────────

    def _select_size(self, key: str) -> None:
        self._selected_size_key = key
        self._lib.set_selected_size_key(key)  # persist
        self._apply_size_selection()
        self._update_large_preview()

    def _apply_size_selection(self) -> None:
        for k, btn in self._size_btns.items():
            btn.setChecked(k == self._selected_size_key)
        is_custom = self._selected_size_key == "custom"
        self._custom_dims_widget.setVisible(is_custom)


# ─────────────────────────────────────────────────────────────────────────────
# Template library management dialog
# ─────────────────────────────────────────────────────────────────────────────

class _TemplateManageDialog(QDialog):
    """Modal dialog for CRUD management of the custom template library.

    Mirrors web openLabelTemplateManageMenu(bucket, x, y, rec.id).
    Operations: 选用 / 重命名 / 复制 / 导出 JSON / 删除.
    """

    def __init__(self, lib: LabelTemplateLibrary, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._lib = lib
        self._selected_id: Optional[str] = None
        self.setWindowTitle(
            f"模板库管理 — {'样品瓶' if lib._bucket == 'sample' else 'RNAlater 组织管'}"
        )
        self.setMinimumWidth(480)
        self.setStyleSheet(
            "QDialog { background: #08161b; color: #eef3ef; }"
            "QListWidget { background: #0c2027; border: 1px solid rgba(145,182,181,0.15);"
            " color: #eef3ef; border-radius:4px; }"
            "QListWidget::item:selected { background: rgba(41,185,171,0.25); }"
            "QPushButton { background:#10242a; border:1px solid rgba(145,182,181,0.20);"
            " border-radius:4px; color:#cfe0db; padding:4px 10px; }"
            "QPushButton:hover { border-color:#29b9ab; color:#29b9ab; }"
            "QPushButton:disabled { color:#4d6b68; border-color:rgba(145,182,181,0.08); }"
            "QLabel { color:#cfe0db; }"
        )
        self._build_ui()
        self._refresh_list()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(10)

        root.addWidget(QLabel("自定义模板库（点击选中，再使用下方操作）:"))

        self._list = QListWidget()
        self._list.setAlternatingRowColors(True)
        self._list.setMinimumHeight(180)
        self._list.itemSelectionChanged.connect(self._on_selection)
        root.addWidget(self._list)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(6)
        self._btn_use    = QPushButton("✓ 选用")
        self._btn_rename = QPushButton("重命名")
        self._btn_copy   = QPushButton("复制")
        self._btn_export = QPushButton("导出 JSON")
        self._btn_delete = QPushButton("删除")
        self._btn_delete.setStyleSheet(
            "QPushButton { background:#10242a; border:1px solid rgba(230,110,99,0.30);"
            " border-radius:4px; color:#e66e63; padding:4px 10px; }"
            "QPushButton:hover { border-color:#e66e63; }"
            "QPushButton:disabled { color:#4d6b68; border-color:rgba(145,182,181,0.08); }"
        )
        for b in [self._btn_use, self._btn_rename, self._btn_copy,
                  self._btn_export, self._btn_delete]:
            b.setEnabled(False)
            btn_row.addWidget(b)
        btn_row.addStretch()
        root.addLayout(btn_row)

        self._btn_use.clicked.connect(self._do_use)
        self._btn_rename.clicked.connect(self._do_rename)
        self._btn_copy.clicked.connect(self._do_copy)
        self._btn_export.clicked.connect(self._do_export)
        self._btn_delete.clicked.connect(self._do_delete)

        bottom = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        bottom.setStyleSheet(
            "QDialogButtonBox QPushButton { background:#10242a; border:1px solid rgba(145,182,181,0.18);"
            " color:#cfe0db; border-radius:4px; padding:4px 14px; }"
        )
        bottom.rejected.connect(self.accept)
        root.addWidget(bottom)

    def _refresh_list(self) -> None:
        self._list.clear()
        for rec in self._lib.records():
            item = QListWidgetItem(
                f"  {rec['name']}  ·  {rec.get('updatedAt', '')[:10]}"
            )
            item.setData(Qt.ItemDataRole.UserRole, rec["id"])
            self._list.addItem(item)
        self._on_selection()

    def _on_selection(self) -> None:
        items = self._list.selectedItems()
        has = bool(items)
        self._selected_id = items[0].data(Qt.ItemDataRole.UserRole) if has else None
        for b in [self._btn_use, self._btn_rename, self._btn_copy,
                  self._btn_export, self._btn_delete]:
            b.setEnabled(has)

    def _do_use(self) -> None:
        if self._selected_id:
            self._lib.select_record(self._selected_id)
            QMessageBox.information(self, "选用", "已选用该模板。")
            self._refresh_list()

    def _do_rename(self) -> None:
        if not self._selected_id:
            return
        rec = self._lib.get(self._selected_id)
        if not rec:
            return
        new_name, ok = QInputDialog.getText(
            self, "重命名", "新名称:", text=rec["name"]
        )
        if ok and new_name.strip():
            self._lib.rename(self._selected_id, new_name.strip())
            self._refresh_list()

    def _do_copy(self) -> None:
        if not self._selected_id:
            return
        new_rec = self._lib.duplicate(self._selected_id)
        if new_rec:
            QMessageBox.information(self, "复制", f"已复制为：{new_rec['name']}")
        self._refresh_list()

    def _do_export(self) -> None:
        if not self._selected_id:
            return
        rec = self._lib.get(self._selected_id)
        if not rec:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "导出模板 JSON",
            rec["name"].replace(" ", "_") + ".json",
            "JSON 文件 (*.json)",
        )
        if path:
            try:
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(rec["template"], f, ensure_ascii=False, indent=2)
                QMessageBox.information(self, "导出", f"已导出到：{path}")
            except Exception as e:
                QMessageBox.warning(self, "导出失败", str(e))

    def _do_delete(self) -> None:
        if not self._selected_id:
            return
        rec = self._lib.get(self._selected_id)
        name = rec["name"] if rec else self._selected_id
        resp = QMessageBox.question(
            self, "删除确认",
            f"确定删除自定义模板「{name}」？\n此操作不可恢复。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if resp == QMessageBox.StandardButton.Yes:
            self._lib.delete(self._selected_id)
            self._selected_id = None
            self._refresh_list()


class _Step2Widget(QWidget):
    """Step 2: template selection for sample + tissue buckets.

    Mirrors: renderLabelStep2({ classic: true }) + renderBucketColumn()
    """

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setStyleSheet("background: #08161b; color: #eef3ef;")

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 16, 20, 16)
        root.setSpacing(14)

        # Title row with persisted hint
        title_row = QHBoxLayout()
        title_row.setSpacing(8)
        title_row.addWidget(_section_label("模板库", large=True))
        hint = QLabel("✓ 模板 / 尺寸 / 排版已自动保存，下次进 Labels 自动沿用")
        hint.setStyleSheet("color: #36c98f; font-size: 11px;")
        title_row.addWidget(hint)
        title_row.addStretch()
        root.addLayout(title_row)

        # Horizontal splitter for two bucket columns
        self._splitter = QSplitter(Qt.Orientation.Horizontal)
        self._splitter.setHandleWidth(6)
        self._splitter.setStyleSheet(
            "QSplitter::handle { background: rgba(145,182,181,0.10); }"
        )

        # Sample bucket column (left)
        sample_scroll = QScrollArea()
        sample_scroll.setWidgetResizable(True)
        sample_scroll.setFrameShape(QFrame.Shape.NoFrame)
        sample_scroll.setStyleSheet("QScrollArea { background: transparent; }")
        self._sample_col = _BucketColWidget("sample")
        sample_scroll.setWidget(self._sample_col)
        self._splitter.addWidget(sample_scroll)

        # Tissue bucket column (right, may be hidden if no R-prefix)
        tissue_scroll = QScrollArea()
        tissue_scroll.setWidgetResizable(True)
        tissue_scroll.setFrameShape(QFrame.Shape.NoFrame)
        tissue_scroll.setStyleSheet("QScrollArea { background: transparent; }")
        self._tissue_col = _BucketColWidget("tissue")
        tissue_scroll.setWidget(self._tissue_col)
        self._splitter.addWidget(tissue_scroll)
        self._tissue_scroll = tissue_scroll

        root.addWidget(self._splitter, stretch=1)

        self._empty_hint = QLabel("未选择标本；仍可管理模板，选择标本后会实时预览。")
        self._empty_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_hint.setStyleSheet("color:#5f7d7a; font-size:13px;")
        self._empty_hint.setVisible(False)
        root.addWidget(self._empty_hint)

    def refresh(self, specimens: list[dict], selected_indices: list[int]) -> None:
        """Update both bucket columns."""
        has_selection = bool(selected_indices)
        self._empty_hint.setText("未选择标本；仍可管理模板，选择标本后会实时预览。")
        self._empty_hint.setVisible(not has_selection)
        self._splitter.setVisible(True)

        tissue_indices = [
            i for i in selected_indices
            if i < len(specimens) and has_rna_tissue(specimens[i])
        ]
        has_tissue = bool(tissue_indices)
        self._tissue_scroll.setVisible(True)

        self._sample_col.refresh(specimens, selected_indices)
        self._tissue_col.refresh(specimens, selected_indices if has_tissue else [])

    def selected_sample_template(self) -> dict:
        return self._sample_col.selected_template()

    def selected_tissue_template(self) -> dict:
        return self._tissue_col.selected_template()

    def selected_sample_dims(self) -> dict:
        return self._sample_col.selected_dims()

    def selected_tissue_dims(self) -> dict:
        return self._tissue_col.selected_dims()

    def label_edits(self) -> dict[int, dict[str, str]]:
        """Return per-specimen field edits from the sample bucket column."""
        return self._sample_col.label_edits()


# ─────────────────────────────────────────────────────────────────────────────
# Step 3 — 编辑预览 (WYSIWYG editor)
# ─────────────────────────────────────────────────────────────────────────────

class _Step3Widget(QWidget):
    """Step 3: WYSIWYG label editor with bucket toggle.

    Reuses LabelEditorWidget (QGraphicsScene, QR draggable, 2 mm safety margin).
    """

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setStyleSheet("background: #08161b; color: #eef3ef;")

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 16, 20, 16)
        root.setSpacing(12)

        root.addWidget(_section_label("标签预览", large=True))

        # Bucket toggle
        toggle_row = QHBoxLayout()
        toggle_row.setSpacing(6)
        self._sample_toggle = _outline_btn("🧪 样品瓶")
        self._tissue_toggle = _outline_btn("🧬 RNAlater 组织管")
        self._sample_toggle.setCheckable(True)
        self._tissue_toggle.setCheckable(True)
        self._sample_toggle.setChecked(True)
        self._sample_toggle.clicked.connect(lambda: self._switch_bucket("sample"))
        self._tissue_toggle.clicked.connect(lambda: self._switch_bucket("tissue"))
        toggle_row.addWidget(self._sample_toggle)
        toggle_row.addWidget(self._tissue_toggle)
        toggle_row.addStretch()
        root.addLayout(toggle_row)

        # Preview label
        self._preview_lbl = QLabel()
        self._preview_lbl.setStyleSheet("color:#87a2a1; font-size:11px;")
        root.addWidget(self._preview_lbl)

        # Editor container
        self._editor_container = QWidget()
        self._editor_container.setStyleSheet("background: transparent;")
        self._editor_layout = QVBoxLayout(self._editor_container)
        self._editor_layout.setContentsMargins(0, 0, 0, 0)
        self._editor_layout.setSpacing(0)
        root.addWidget(self._editor_container, stretch=1)

        self._editor: Optional[LabelEditorWidget] = None
        self._current_bucket: str = "sample"
        self._sample_template: dict = normalize_template(None)
        self._tissue_template: dict = normalize_template(None)
        self._sample_dims: dict = {"w": 50, "h": 30}
        self._tissue_dims: dict = {"w": 30, "h": 15}
        self._label_data: dict = {}

        # Tissue toggle visible state
        self._has_tissue: bool = False

    def refresh(
        self,
        sample_template: dict,
        tissue_template: dict,
        label_data: dict,
        sample_dims: Optional[dict] = None,
        tissue_dims: Optional[dict] = None,
        has_tissue: bool = False,
    ) -> None:
        self._sample_template = sample_template
        self._tissue_template = tissue_template
        self._label_data = label_data
        if sample_dims:
            self._sample_dims = sample_dims
        if tissue_dims:
            self._tissue_dims = tissue_dims
        self._has_tissue = has_tissue
        self._tissue_toggle.setVisible(has_tissue)
        if not has_tissue and self._current_bucket == "tissue":
            self._current_bucket = "sample"
            self._sample_toggle.setChecked(True)
            self._tissue_toggle.setChecked(False)
        self._rebuild_editor()

    @property
    def editor(self) -> Optional[LabelEditorWidget]:
        return self._editor

    def _switch_bucket(self, bucket: str) -> None:
        self._current_bucket = bucket
        self._sample_toggle.setChecked(bucket == "sample")
        self._tissue_toggle.setChecked(bucket == "tissue")
        self._rebuild_editor()

    def _rebuild_editor(self) -> None:
        # Remove existing editor widget
        while self._editor_layout.count():
            item = self._editor_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._editor = None

        if self._current_bucket == "sample":
            tmpl = self._sample_template
            dims = self._sample_dims
        else:
            tmpl = self._tissue_template
            dims = self._tissue_dims

        sp_uid = self._label_data.get("uniqueId") or "—"
        self._preview_lbl.setText(
            f"实时预览 — {sp_uid} · {dims.get('w', 0)}×{dims.get('h', 0)}mm"
            " · 点字段→打字 / 拖动定位 / QR 可拖"
        )

        editor = LabelEditorWidget(tmpl, dims, self._label_data)
        self._editor_layout.addWidget(editor)
        self._editor = editor


# ─────────────────────────────────────────────────────────────────────────────
# Step 4 — 纸张 / 尺寸 / 份数 + 输出
# ─────────────────────────────────────────────────────────────────────────────

class _Step4Widget(QWidget):
    """Step 4: per-bucket paper-type, copies spinner, print buttons, warnings.

    Mirrors: renderLabelStep3() (paper/copies) + renderLabelStep4() (output).
    """

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setStyleSheet("background: #08161b; color: #eef3ef;")

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 16, 20, 16)
        root.setSpacing(16)

        root.addWidget(_section_label("打印设置", large=True))

        # Label dims (updated by LabelsView._refresh_step4 via update_dims())
        self._sample_dims: dict = {"w": 50, "h": 30}
        self._tissue_dims: dict = {"w": 30, "h": 15}

        # Per-bucket paper-type rows (sample + tissue)
        paper_row = QHBoxLayout()
        paper_row.setSpacing(20)

        self._sample_paper_col = self._make_paper_col("sample")
        self._tissue_paper_col = self._make_paper_col("tissue")
        paper_row.addWidget(self._sample_paper_col)
        paper_row.addWidget(self._tissue_paper_col)
        root.addLayout(paper_row)

        # 份数 spinner
        copies_row = QHBoxLayout()
        copies_row.setSpacing(6)
        copies_row.addWidget(QLabel("每种份数:"))
        self._copies_spin = QSpinBox()
        self._copies_spin.setRange(1, 20)
        self._copies_spin.setValue(1)
        self._copies_spin.setFixedWidth(60)
        self._copies_spin.setStyleSheet(
            "QSpinBox { background:#0f2127; border:1px solid rgba(145,182,181,0.18);"
            " border-radius:4px; color:#eef3ef; padding:2px 4px; }"
        )
        copies_row.addWidget(self._copies_spin)
        copies_row.addStretch()
        root.addLayout(copies_row)

        root.addWidget(_sep_line())

        # Output section title
        root.addWidget(_section_label("输出"))

        # Summary info
        self._output_info = QLabel("样品瓶 0 · RNAlater 组织管 0 · 每种 1 份 → 总 0 张")
        self._output_info.setStyleSheet("color:#87a2a1; font-size:12px;")
        root.addWidget(self._output_info)

        # Print buttons
        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)
        self._btn_sample = _primary_btn("打印样品瓶标签 (0)")
        self._btn_tissue = _tissue_btn("打印 RNAlater 组织管标签 (0)")
        btn_row.addWidget(self._btn_sample)
        btn_row.addWidget(self._btn_tissue)
        root.addLayout(btn_row)

        # Warnings
        self._warnings_lbl = QLabel()
        self._warnings_lbl.setWordWrap(True)
        self._warnings_lbl.setTextFormat(Qt.TextFormat.RichText)
        self._warnings_lbl.setStyleSheet("font-size: 12px; padding: 4px 0;")
        root.addWidget(self._warnings_lbl)

        # Hint
        root.addWidget(_muted_label(
            "提示：两个按钮分别触发打印对话框；可在对话框里挑不同打印机/纸盘/纸张。"
        ))

        root.addStretch()

    # ── Paper-type column factory ─────────────────────────────────────

    def _make_paper_col(self, bucket: str) -> QGroupBox:
        icon = "🧪" if bucket == "sample" else "🧬"
        name = "样品瓶" if bucket == "sample" else "RNAlater 组织管"
        box = QGroupBox(f"{icon} {name}")
        box.setStyleSheet(
            "QGroupBox { color:#eef3ef; border:1px solid rgba(145,182,181,0.15);"
            " border-radius:5px; margin-top:8px; padding-top:10px; font-size:12px; }"
            "QGroupBox::title { subcontrol-origin:margin; left:8px; color:#cfe0db; }"
        )
        col = QVBoxLayout(box)
        col.setSpacing(4)

        # Paper type radios
        self_attr = f"_{bucket}_paper_type"
        setattr(self, self_attr, "label")
        radios: list[QRadioButton] = []
        for ptype, pname in [("a4", "A4纸"), ("a5", "A5纸"), ("label", "小标签纸")]:
            rb = QRadioButton(pname)
            rb.setChecked(ptype == "label")
            rb.setStyleSheet("color:#cfe0db; font-size:11px;")
            col.addWidget(rb)
            def _on_changed(
                checked: bool, _type: str = ptype,
                _attr: str = self_attr, _bucket: str = bucket,
            ) -> None:
                if checked:
                    setattr(self, _attr, _type)
                    self._update_grid_preview(_bucket)
            rb.toggled.connect(_on_changed)
            radios.append(rb)

        if bucket == "sample":
            self._sample_radios = radios
        else:
            self._tissue_radios = radios

        # Grid preview text
        col.addSpacing(6)
        grid_lbl = QLabel("🏷 标签纸: 每张 1 个标签")
        grid_lbl.setStyleSheet(
            "color:#5f7d7a; font-size:10px; background:#0a1e25;"
            " border:1px solid rgba(145,182,181,0.08); border-radius:3px; padding:4px 6px;"
        )
        grid_lbl.setWordWrap(True)
        col.addWidget(grid_lbl)

        # Visual grid cells (shown only for A4/A5)
        grid_visual = QWidget()
        grid_visual.setStyleSheet("background: transparent;")
        grid_visual.setVisible(False)
        grid_vis_layout = QGridLayout(grid_visual)
        grid_vis_layout.setContentsMargins(2, 2, 2, 2)
        grid_vis_layout.setSpacing(2)
        col.addWidget(grid_visual)

        if bucket == "sample":
            self._sample_grid_preview = grid_lbl
            self._sample_grid_visual = grid_visual
        else:
            self._tissue_grid_preview = grid_lbl
            self._tissue_grid_visual = grid_visual

        return box

    def _update_grid_preview(self, bucket: str) -> None:
        """Refresh grid text + visual cells. Mirrors web renderPaperColumn grid section."""
        paper_type = getattr(self, f"_{bucket}_paper_type", "label")
        dims = self._sample_dims if bucket == "sample" else self._tissue_dims
        preview_lbl = self._sample_grid_preview if bucket == "sample" else self._tissue_grid_preview
        visual = self._sample_grid_visual if bucket == "sample" else self._tissue_grid_visual
        icon = "🧪" if bucket == "sample" else "🧬"

        if paper_type in ("a4", "a5"):
            paper = PAPER_SIZES.get(paper_type, {})
            grid = calculate_grid(
                float(dims.get("w", 50)), float(dims.get("h", 30)),
                float(paper.get("w", 210)), float(paper.get("h", 297)),
            )
            paper_name = "A4" if paper_type == "a4" else "A5"
            preview_lbl.setText(
                f"🗒 {paper_name} 排版: {grid['cols']}列 × {grid['rows']}行 ="
                f" {grid['perPage']} 张/页"
            )
            lyt: QGridLayout = visual.layout()  # type: ignore[assignment]
            while lyt.count():
                item = lyt.takeAt(0)
                if item.widget():
                    item.widget().deleteLater()
            cols = grid["cols"]
            for i in range(min(grid["perPage"], 40)):
                cell = QFrame()
                cell.setStyleSheet(
                    "QFrame { background: rgba(41,185,171,0.18);"
                    " border: 1px solid rgba(41,185,171,0.40); border-radius: 1px; }"
                )
                cell.setFixedSize(12, 8)
                lyt.addWidget(cell, i // cols, i % cols)
            visual.setVisible(True)
        else:
            preview_lbl.setText(f"{icon} 标签纸: 每张 1 个标签")
            lyt = visual.layout()  # type: ignore[assignment]
            while lyt.count():
                item = lyt.takeAt(0)
                if item.widget():
                    item.widget().deleteLater()
            visual.setVisible(False)

    def update_dims(self, sample_dims: dict, tissue_dims: dict) -> None:
        """Called by LabelsView._refresh_step4 to propagate label dims for grid calc."""
        self._sample_dims = sample_dims
        self._tissue_dims = tissue_dims
        self._update_grid_preview("sample")
        self._update_grid_preview("tissue")

    # ── Public API ────────────────────────────────────────────────────

    def update_counts(
        self,
        sample_count: int,
        tissue_count: int,
        sample_warnings: list[dict],
        tissue_warnings: list[dict],
        copies: int = 1,
    ) -> None:
        self._btn_sample.setText(f"打印样品瓶标签 ({sample_count})")
        self._btn_tissue.setText(f"打印 RNAlater 组织管标签 ({tissue_count})")
        self._btn_sample.setEnabled(sample_count > 0)
        self._btn_tissue.setEnabled(tissue_count > 0)

        total = (sample_count + tissue_count) * copies
        self._output_info.setText(
            f"样品瓶 {sample_count} · RNAlater 组织管 {tissue_count}"
            f" · 每种 {copies} 份 → 总 {total} 张"
        )

        # Refresh grid previews (picks up updated counts via dims)
        self._update_grid_preview("sample")
        self._update_grid_preview("tissue")

        html = "<b>样品桶</b><br>" + _warnings_html(sample_warnings)
        if tissue_count > 0:
            html += "<br><b>组织管桶</b><br>" + _warnings_html(tissue_warnings)
        self._warnings_lbl.setText(html)

    @property
    def copies(self) -> int:
        return self._copies_spin.value()

    @property
    def sample_button(self) -> QPushButton:
        return self._btn_sample

    @property
    def tissue_button(self) -> QPushButton:
        return self._btn_tissue

    def sample_paper_type(self) -> str:
        return getattr(self, "_sample_paper_type", "label")

    def tissue_paper_type(self) -> str:
        return getattr(self, "_tissue_paper_type", "label")


# ─────────────────────────────────────────────────────────────────────────────
# _LayoutWorkbench — wraps the existing 4-step wizard as a pluggable layout
# ─────────────────────────────────────────────────────────────────────────────

from app.views.labels_layouts import _LabelLayoutBase  # noqa: E402


class _LayoutWorkbench(_LabelLayoutBase):
    """Layout W (default): The classic 4-step workbench layout.

    Wraps _Step1Widget / _Step2Widget / _Step3Widget / _Step4Widget using the
    same splitter/scroll arrangement as the original LabelsView._setup_ui().

    This class is the faithful behaviour-preserving extraction of the original
    LabelsView content area into the _LabelLayoutBase interface so LabelsView
    can swap it out for alternative layouts.
    """

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._specimens: list[dict] = []
        self._sample_job: Optional[dict] = None
        self._tissue_job: Optional[dict] = None
        self._setup_ui()

    def _setup_ui(self) -> None:
        self.setStyleSheet("background: #08161b; color: #eef3ef;" + _CSS_FULL)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self._step1 = _Step1Widget()
        self._step2 = _Step2Widget()
        self._step3 = _Step3Widget()
        self._step4 = _Step4Widget()
        self._step1.selection_changed.connect(self._refresh_after_selection_change)

        _scroll_style = (
            "QScrollArea { background: #08161b; border: none; }"
            "QScrollBar:vertical { background: #0c1e26; width: 8px; }"
            "QScrollBar::handle:vertical { background: rgba(145,182,181,0.25); border-radius: 4px; }"
            "QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }"
        )

        self._main_splitter = QSplitter(Qt.Orientation.Horizontal)
        self._main_splitter.setChildrenCollapsible(False)
        self._main_splitter.setHandleWidth(12)
        self._main_splitter.setStyleSheet(
            "QSplitter::handle { background: rgba(145,182,181,0.10); }"
        )

        self._left_scroll = QScrollArea()
        self._left_scroll.setWidgetResizable(True)
        self._left_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._left_scroll.setStyleSheet(_scroll_style)
        self._left_scroll.setWidget(self._step1)
        self._main_splitter.addWidget(self._left_scroll)

        self._content = QWidget()
        self._content.setStyleSheet("background: #08161b;")
        self._content_layout = QVBoxLayout(self._content)
        self._content_layout.setContentsMargins(0, 0, 0, 0)
        self._content_layout.setSpacing(0)
        for step_widget in (self._step2, self._step3, self._step4):
            self._content_layout.addWidget(step_widget)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setStyleSheet(_scroll_style)
        self._scroll.setWidget(self._content)
        self._main_splitter.addWidget(self._scroll)
        self._main_splitter.setSizes([340, 980])
        root.addWidget(self._main_splitter, stretch=1)

        # Wire print buttons to our signals
        self._step4.sample_button.clicked.connect(self.print_sample)
        self._step4.tissue_button.clicked.connect(self.print_tissue)

    # ── _LabelLayoutBase interface ────────────────────────────────────

    def load_specimens(self, specimens: list[dict]) -> None:
        self._specimens = specimens
        self._step1.set_specimens(specimens)
        self._refresh_after_selection_change()

    def select_uid(self, uid: str) -> bool:
        ok = self._step1.select_only_uid(uid)
        if ok:
            self._refresh_after_selection_change()
        return ok

    def get_sample_job(self) -> Optional[dict]:
        return self._sample_job

    def get_tissue_job(self) -> Optional[dict]:
        return self._tissue_job

    def on_activate(self) -> None:
        self._refresh_after_selection_change()

    # ── Step refresh chain (mirrors old LabelsView methods) ───────────

    def _refresh_after_selection_change(self) -> None:
        if not hasattr(self, "_step2"):
            return
        self._refresh_step2()
        self._refresh_step3()
        self._refresh_step4()

    def _refresh_step2(self) -> None:
        indices = self._step1.selected_indices()
        self._step2.refresh(self._specimens, indices)

    def _refresh_step3(self) -> None:
        indices = self._step1.selected_indices()
        first_data: dict = {}
        if indices and self._specimens:
            from app.utils.label_core import specimen_to_label_data as _s2ld
            sp = self._specimens[indices[0]] if indices[0] < len(self._specimens) else {}
            first_data = _s2ld(sp) if sp else {}
        sample_tmpl = self._step2.selected_sample_template()
        tissue_tmpl = self._step2.selected_tissue_template()
        sample_dims = self._step2.selected_sample_dims()
        tissue_dims = self._step2.selected_tissue_dims()
        has_tissue = any(
            has_rna_tissue(self._specimens[i])
            for i in indices
            if i < len(self._specimens)
        )
        self._step3.refresh(
            sample_tmpl, tissue_tmpl, first_data,
            sample_dims, tissue_dims, has_tissue=has_tissue,
        )

    def _refresh_step4(self) -> None:
        indices = self._step1.selected_indices()
        sample_tmpl = self._step2.selected_sample_template()
        tissue_tmpl = self._step2.selected_tissue_template()
        sample_dims = self._step2.selected_sample_dims()
        tissue_dims = self._step2.selected_tissue_dims()
        copies = self._step4.copies
        edits = self._step2.label_edits()

        sample_paper_type = self._step4.sample_paper_type()
        tissue_paper_type = self._step4.tissue_paper_type()
        sample_paper = PAPER_SIZES.get(sample_paper_type) if sample_paper_type in ("a4", "a5") else None
        tissue_paper = PAPER_SIZES.get(tissue_paper_type) if tissue_paper_type in ("a4", "a5") else None

        self._sample_job = LabelService.build_print_job(
            self._specimens, sample_tmpl, "sample",
            selected_indices=indices, dims=sample_dims, copies=copies,
            paper_type=sample_paper_type, paper=sample_paper, edits=edits,
        )
        self._tissue_job = LabelService.build_print_job(
            self._specimens, tissue_tmpl, "tissue",
            selected_indices=indices, dims=tissue_dims, copies=copies,
            paper_type=tissue_paper_type, paper=tissue_paper, edits=edits,
        )

        self._step4.update_dims(sample_dims, tissue_dims)

        sample_n = len(self._sample_job["items"])
        tissue_n = len(self._tissue_job["items"])

        self._step4.update_counts(
            sample_n,
            tissue_n,
            self._sample_job.get("warnings") or [],
            self._tissue_job.get("warnings") or [],
            copies=copies,
        )


# ─────────────────────────────────────────────────────────────────────────────
# Main view
# ─────────────────────────────────────────────────────────────────────────────

class LabelsView(BaseView):
    """标签打印页面 — 4-step wizard with pluggable layouts.

    Default layout: _LayoutWorkbench (faithful 4-step port).
    Alternative layouts: _LayoutDualBucket (E), _LayoutStream (F), _LayoutCinema (D).
    Layout is persisted via QSettings("PhotoPlatform", "Labels").
    """

    view_id = "labels"
    nav_title = "标签打印"
    nav_icon = "🏷️"

    # Maps combo index → (name, layout class)
    _LAYOUTS = [
        ("工作台", "_LayoutWorkbench"),
        ("影院",   "_LayoutCinema"),
        ("双桶",   "_LayoutDualBucket"),
        ("流式",   "_LayoutStream"),
    ]

    def __init__(self, ctx: "AppContext") -> None:
        # Must initialize __specimens before super().__init__ calls _setup_ui
        self._LabelsView__specimens: list[dict] = []  # backing store for _specimens property
        self._active_layout: Optional[_LabelLayoutBase] = None
        super().__init__(ctx)

    # ── Backward-compat properties ─────────────────────────────────────
    # Tests and external callers that access view._step1 / view._specimens
    # directly will still work as long as the active layout is _LayoutWorkbench.

    @property
    def _step1(self) -> Optional["_Step1Widget"]:
        """Return _step1 from the active workbench layout, if applicable."""
        if isinstance(self._active_layout, _LayoutWorkbench):
            return self._active_layout._step1
        return None

    @property
    def _step2(self) -> Optional["_Step2Widget"]:
        """Return _step2 from the active workbench layout, if applicable."""
        if isinstance(self._active_layout, _LayoutWorkbench):
            return self._active_layout._step2
        return None

    @property
    def _step3(self) -> Optional["_Step3Widget"]:
        """Return _step3 from the active workbench layout, if applicable."""
        if isinstance(self._active_layout, _LayoutWorkbench):
            return self._active_layout._step3
        return None

    @property
    def _step4(self) -> Optional["_Step4Widget"]:
        """Return _step4 from the active workbench layout, if applicable."""
        if isinstance(self._active_layout, _LayoutWorkbench):
            return self._active_layout._step4
        return None

    @property
    def _content_layout(self) -> Optional[QVBoxLayout]:
        """Return _content_layout from the active workbench layout, if applicable."""
        if isinstance(self._active_layout, _LayoutWorkbench):
            return self._active_layout._content_layout
        return None

    @property
    def _specimens(self) -> list[dict]:
        return self.__specimens

    @_specimens.setter
    def _specimens(self, v: list[dict]) -> None:
        self.__specimens = v

    def _setup_ui(self) -> None:
        self.setStyleSheet("background: #08161b; color: #eef3ef;" + _CSS_FULL)
        # Import layout classes (lazy to avoid circular import during class definition)
        from app.views.labels_layouts import (  # noqa: PLC0415
            _LayoutDualBucket,
            _LayoutStream,
            _LayoutCinema,
        )
        self._layout_classes = {
            "工作台": _LayoutWorkbench,
            "影院":   _LayoutCinema,
            "双桶":   _LayoutDualBucket,
            "流式":   _LayoutStream,
        }

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── Top nav bar: step anchors + layout switcher ───────────────
        nav_frame = QFrame()
        nav_frame.setStyleSheet(
            "QFrame { background: #091e24; border-bottom: 1px solid rgba(145,182,181,0.12); }"
        )
        nav_bar = QHBoxLayout(nav_frame)
        nav_bar.setContentsMargins(12, 6, 12, 6)
        nav_bar.setSpacing(6)

        self._step_btns: list[QPushButton] = []
        step_labels = ["标本", "模板", "预览", "打印"]
        for i, lbl in enumerate(step_labels):
            btn = QPushButton(lbl)
            btn.setObjectName("StepBtn")
            btn.setCheckable(True)
            btn.setFixedHeight(28)
            btn.setStyleSheet(_CSS_STEP_NAV)
            btn.clicked.connect(lambda checked, idx=i: self._go_to_step(idx))
            nav_bar.addWidget(btn)
            self._step_btns.append(btn)

        nav_bar.addStretch()

        # Layout selector QComboBox (right side of nav bar)
        layout_lbl = QLabel("布局:")
        layout_lbl.setStyleSheet("color: #87a2a1; font-size: 11px;")
        nav_bar.addWidget(layout_lbl)
        self._layout_combo = QComboBox()
        self._layout_combo.setStyleSheet(
            "QComboBox { background: #0f2127; border: 1px solid rgba(145,182,181,0.18);"
            " border-radius: 4px; color: #cfe0db; padding: 2px 8px; font-size: 11px; }"
            "QComboBox::drop-down { border: none; }"
            "QComboBox QAbstractItemView { background: #10242a; color: #cfe0db;"
            " border: 1px solid rgba(145,182,181,0.18); }"
        )
        for name, _ in self._LAYOUTS:
            self._layout_combo.addItem(name)
        self._layout_combo.setFixedHeight(26)
        self._layout_combo.currentIndexChanged.connect(self._switch_layout)
        nav_bar.addWidget(self._layout_combo)
        root.addWidget(nav_frame)

        # ── Main layout container (swappable) ─────────────────────────
        self._layout_container = QWidget()
        self._layout_container.setStyleSheet("background: #08161b;")
        lc_layout = QVBoxLayout(self._layout_container)
        lc_layout.setContentsMargins(0, 0, 0, 0)
        lc_layout.setSpacing(0)
        root.addWidget(self._layout_container, stretch=1)

        # Compat hidden prev/next buttons (used by some tests)
        self._btn_prev = QPushButton()
        self._btn_next = QPushButton()
        self._btn_prev.clicked.connect(self._prev_step)
        self._btn_next.clicked.connect(self._next_step)

        # ── Status bar ────────────────────────────────────────────────
        status_frame = QFrame()
        status_frame.setStyleSheet(
            "QFrame { background: #060f12; border-top: 1px solid rgba(145,182,181,0.08); }"
        )
        status_layout = QHBoxLayout(status_frame)
        status_layout.setContentsMargins(12, 4, 12, 4)
        status_layout.setSpacing(14)

        self._status_mode = QLabel("批量打印")
        self._status_mode.setStyleSheet("color: #5f7d7a; font-size: 11px;")
        self._status_selected = QLabel("选中 0 个")
        self._status_selected.setStyleSheet("color: #87a2a1; font-size: 11px;")
        self._status_sample = QLabel("样品 0")
        self._status_sample.setStyleSheet("color: #29b9ab; font-size: 11px;")
        self._status_tissue = QLabel("RNAlater 0")
        self._status_tissue.setStyleSheet("color: #4a90d9; font-size: 11px;")
        self._status_total = QLabel("共 0 张")
        self._status_total.setStyleSheet("color: #87a2a1; font-size: 11px;")
        self._status_warn = QLabel("")
        self._status_warn.setStyleSheet("color: #f1bd57; font-size: 11px;")

        for w in [self._status_mode, self._status_selected,
                  self._status_sample, self._status_tissue,
                  self._status_total, self._status_warn]:
            status_layout.addWidget(w)
        status_layout.addStretch()
        root.addWidget(status_frame)

        self._current_step: int = 0

        # Always start with 工作台 (index 0) so tests are stable.
        # QSettings restore is deferred via QTimer so event loop must be
        # running — tests that don't exec() the app never trigger it.
        self._switch_layout(0)

        self._go_to_step(0)

        # Defer QSettings restore until after event loop starts.
        QTimer.singleShot(0, self._restore_layout_from_settings)

    def _restore_layout_from_settings(self) -> None:
        """Restore the last-used layout from QSettings (deferred, safe for tests)."""
        from PyQt6.QtCore import QSettings  # noqa: PLC0415
        settings = QSettings("PhotoPlatform", "Labels")
        saved = settings.value("layout", "工作台")
        for i, (name, _cls) in enumerate(self._LAYOUTS):
            if name == saved and i != self._layout_combo.currentIndex():
                self._layout_combo.blockSignals(True)
                self._layout_combo.setCurrentIndex(i)
                self._layout_combo.blockSignals(False)
                self._switch_layout(i)
                break

    # ── Layout switching ───────────────────────────────────────────────

    def _switch_layout(self, idx: int) -> None:
        """Swap the active layout widget. Persists choice to QSettings."""
        from PyQt6.QtCore import QSettings  # noqa: PLC0415

        name = self._LAYOUTS[idx][0]
        cls_name = self._LAYOUTS[idx][1]

        # Persist
        settings = QSettings("PhotoPlatform", "Labels")
        settings.setValue("layout", name)

        # Look up class
        cls = self._layout_classes.get(name)
        if cls is None:
            # Fallback: workbench
            cls = _LayoutWorkbench

        # Remove old layout widget
        container_layout = self._layout_container.layout()
        if self._active_layout is not None:
            container_layout.removeWidget(self._active_layout)
            self._active_layout.hide()
            self._active_layout.deleteLater()
            self._active_layout = None

        # Build new layout
        new_layout = cls(parent=self._layout_container)
        new_layout.print_sample.connect(lambda: self._print("sample"))
        new_layout.print_tissue.connect(lambda: self._print("tissue"))
        container_layout.addWidget(new_layout)
        self._active_layout = new_layout

        # Update step button visibility: only meaningful for workbench layout
        for btn in self._step_btns:
            btn.setVisible(isinstance(new_layout, _LayoutWorkbench))

        # Feed current specimens to the new layout
        if self.__specimens:
            new_layout.load_specimens(self.__specimens)

    # ── on_activate ───────────────────────────────────────────────────

    def on_activate(self) -> None:
        """Called when user navigates to this page."""
        self._load_specimens()
        if self._active_layout is not None:
            self._active_layout.on_activate()
        pending_uid = getattr(self.ctx, "pending_label_uid", None)
        if isinstance(pending_uid, str) and pending_uid:
            self.select_uid(pending_uid)
            try:
                self.ctx.pending_label_uid = None
            except Exception:
                pass

    # ── Navigation (workbench step anchors) ───────────────────────────

    def _go_to_step(self, idx: int) -> None:
        self._current_step = idx
        for i, btn in enumerate(self._step_btns):
            btn.setChecked(i == idx)
        self._btn_prev.setEnabled(idx > 0)
        self._btn_next.setEnabled(idx < 3)

        # Only scroll for workbench layout
        if isinstance(self._active_layout, _LayoutWorkbench):
            layout = self._active_layout
            if idx == 1:
                layout._refresh_step2()
            elif idx == 2:
                layout._refresh_step3()
            elif idx == 3:
                layout._refresh_step4()
            # Scroll to relevant section
            if idx == 0 and hasattr(layout, "_left_scroll"):
                QTimer.singleShot(0, lambda: layout._left_scroll.verticalScrollBar().setValue(0))
            elif hasattr(layout, "_scroll"):
                steps = [layout._step2, layout._step3, layout._step4]
                target_idx = idx - 1
                if 0 <= target_idx < len(steps):
                    QTimer.singleShot(0, lambda w=steps[target_idx]:
                                      layout._scroll.ensureWidgetVisible(w, 0, 10))

    def _prev_step(self) -> None:
        if self._current_step > 0:
            self._go_to_step(self._current_step - 1)

    def _next_step(self) -> None:
        if self._current_step < 3:
            self._go_to_step(self._current_step + 1)

    # ── Data loading ──────────────────────────────────────────────────

    def _load_specimens(self) -> None:
        """Load specimens from DB via AppContext, then push to active layout."""
        specimens: list[dict] = []
        db = self.ctx.get_db()
        if db is not None:
            try:
                rows = db.execute(
                    "SELECT * FROM specimens ORDER BY id"
                ).fetchall()
                for row in rows:
                    d = dict(row)
                    specimens.append({
                        "province":       d.get("province"),
                        "site":           d.get("site"),
                        "station":        d.get("station"),
                        "id":             d.get("id"),
                        "storage":        d.get("storage"),
                        "collectionDate": d.get("collection_date"),
                        "photoDate":      d.get("photo_date"),
                        "species":        d.get("scientific_name_cn") or d.get("scientific_name"),
                        "latin":          d.get("scientific_name") or "",
                        "collector":      d.get("collector"),
                        "photographer":   d.get("photographer"),
                        "family":         d.get("family"),
                        "region":         d.get("geo_area") or "",
                        "lon":            str(d.get("lon") or ""),
                        "lat":            str(d.get("lat") or ""),
                        "geoArea":        d.get("geo_area") or "",
                        "photoNotes":     d.get("photo_notes") or "",
                    })
            except Exception:
                pass

        self.__specimens = specimens
        if self._active_layout is not None:
            self._active_layout.load_specimens(specimens)
        # Status bar: update selected count based on active layout
        self._refresh_status_bar()

    def _refresh_status_bar(self) -> None:
        """Refresh status bar from active layout's current job state."""
        if self._active_layout is None:
            return
        sample_job = self._active_layout.get_sample_job()
        tissue_job = self._active_layout.get_tissue_job()
        sample_n = len(sample_job["items"]) if sample_job else 0
        tissue_n = len(tissue_job["items"]) if tissue_job else 0
        copies = 1

        # Try to get selected count from workbench layout
        selected = 0
        if isinstance(self._active_layout, _LayoutWorkbench):
            selected = len(self._active_layout._step1.selected_indices())
            copies = self._active_layout._step4.copies
        else:
            selected = len(self.__specimens)

        self._update_status_bar(selected, sample_n, tissue_n, copies)

    def select_uid(self, uid: str) -> bool:
        """Select one specimen by UID in the active layout."""
        if self._active_layout is None:
            return False
        ok = self._active_layout.select_uid(uid)
        if ok:
            self._refresh_status_bar()
        return ok

    # ── Status bar ────────────────────────────────────────────────────

    def _update_status_bar(
        self, selected: int, sample_n: int, tissue_n: int, copies: int
    ) -> None:
        """Update status bar labels.  Mirrors renderLabelStatusBar()."""
        total = (sample_n + tissue_n) * copies
        self._status_selected.setText(f"选中 {selected} 个编号")
        self._status_sample.setText(f"样品 {sample_n}")
        self._status_tissue.setText(f"RNAlater {tissue_n}")
        self._status_total.setText(f"共 {total} 张")

        # First non-empty warning from both active-layout jobs
        all_warnings: list[dict] = []
        if self._active_layout is not None:
            sample_job = self._active_layout.get_sample_job()
            tissue_job = self._active_layout.get_tissue_job()
            if sample_job:
                all_warnings += (sample_job.get("warnings") or [])
            if tissue_job:
                all_warnings += (tissue_job.get("warnings") or [])
        # Filter out "empty" code (not useful in status bar)
        all_warnings = [w for w in all_warnings if w.get("code") != "empty"]
        if all_warnings:
            self._status_warn.setText(f"⚠ {all_warnings[0].get('message', '')}")
        else:
            self._status_warn.setText("")

    # ── Printing ───────────────────────────────────────────────────────

    def _print(self, bucket: str) -> None:
        """Print the given bucket using QPrinter → QPrintDialog."""
        if self._active_layout is None:
            QMessageBox.warning(self, "打印", "布局未就绪。")
            return

        job = (self._active_layout.get_sample_job() if bucket == "sample"
               else self._active_layout.get_tissue_job())
        if job is None:
            QMessageBox.warning(self, "打印", "请先完成前三步再打印。")
            return

        items = job.get("items") or []
        if not items:
            QMessageBox.information(
                self, "打印",
                "本桶没有可打印标签。\n"
                + ("（RNAlater 组织管标签仅对 R 前缀标本生成）"
                   if bucket == "tissue" else "")
            )
            return

        dims = job.get("dims") or {}
        w_mm = float(dims.get("w", 60))
        h_mm = float(dims.get("h", 40))

        paper_type = job.get("paperType") or "label"
        printer = QPrinter(QPrinter.PrinterMode.HighResolution)
        if paper_type in ("a4", "a5"):
            std = QPageSize.PageSizeId.A4 if paper_type == "a4" else QPageSize.PageSizeId.A5
            printer.setPageSize(QPageSize(std))
            printer.setPageMargins(QMarginsF(0, 0, 0, 0), QPageSize.Unit.Millimeter)
        else:
            page_size = QPageSize(QSizeF(w_mm, h_mm), QPageSize.Unit.Millimeter, "Custom")
            printer.setPageSize(page_size)
            printer.setPageMargins(QMarginsF(2, 2, 2, 2), QPageSize.Unit.Millimeter)
        printer.setOutputFormat(QPrinter.OutputFormat.PdfFormat)

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            tmp_path = f.name
        printer.setOutputFileName(tmp_path)

        dialog = QPrintDialog(printer, self)
        dialog.setOption(QAbstractPrintDialog.PrintDialogOption.PrintToFile, True)
        if dialog.exec() != QPrintDialog.DialogCode.Accepted:
            return

        self._paint_labels(printer, job)

    def _paint_labels(self, printer: QPrinter, job: dict) -> None:
        """Paint all label items. A4/A5 → grid; label paper → 1 per page."""
        from app.widgets.label_editor import _generate_qr_pixmap

        items = job.get("items") or []
        dims = job.get("dims") or {}
        tmpl = job.get("template") or {}
        paper_type = job.get("paperType") or "label"
        w_mm = float(dims.get("w", 60))
        h_mm = float(dims.get("h", 40))

        painter = QPainter()
        if not painter.begin(printer):
            return

        dpi = printer.resolution()
        mm_to_dot = dpi / 25.4

        if paper_type in ("a4", "a5"):
            paper = PAPER_SIZES.get(paper_type, {"w": 210, "h": 297})
            grid = calculate_grid(w_mm, h_mm, float(paper["w"]), float(paper["h"]))
            margin_mm = grid.get("margin", 8.0)
            gap_mm = grid.get("gap", 2.0)
            cols = grid["cols"]
            per_page = grid["perPage"]
            page_no = 0
            for slot_idx, item in enumerate(items):
                data = item.get("data") if isinstance(item, dict) else item
                if not data:
                    continue
                page = slot_idx // per_page
                if page > page_no:
                    printer.newPage()
                    page_no = page
                slot = slot_idx % per_page
                col = slot % cols
                row = slot // cols
                x_off = int((margin_mm + col * (w_mm + gap_mm)) * mm_to_dot)
                y_off = int((margin_mm + row * (h_mm + gap_mm)) * mm_to_dot)
                self._paint_one_label(painter, tmpl, dims, data, x_off, y_off, dpi, mm_to_dot)
        else:
            for page_idx, item in enumerate(items):
                if page_idx > 0:
                    printer.newPage()
                data = item.get("data") if isinstance(item, dict) else item
                if not data:
                    continue
                self._paint_one_label(painter, tmpl, dims, data, 0, 0, dpi, mm_to_dot)

        painter.end()

    def _paint_one_label(
        self,
        painter: QPainter,
        tmpl: dict,
        dims: dict,
        data: dict,
        x_off: int,
        y_off: int,
        dpi: int,
        mm_to_dot: float,
    ) -> None:
        """Paint one label at pixel offset (x_off, y_off)."""
        from app.widgets.label_editor import _generate_qr_pixmap
        w_mm = float(dims.get("w", 60))
        h_mm = float(dims.get("h", 40))
        w_dot = w_mm * mm_to_dot
        h_dot = h_mm * mm_to_dot
        painter.fillRect(x_off, y_off, int(w_dot), int(h_dot), QColor("white"))

        qr_cfg = tmpl.get("qr") or {}
        ecc = qr_cfg.get("ecc") or "Q"
        metrics = qr_metrics(tmpl, dims)
        if metrics is not None:
            qr_text = str(data.get(qr_cfg.get("content") or "uniqueId") or "")
            size_dot = int(metrics["sizeMm"] * mm_to_dot)
            pm = _generate_qr_pixmap(qr_text, size_dot, ecc)
            if pm:
                painter.drawPixmap(
                    x_off + int(metrics["x"] * mm_to_dot),
                    y_off + int(metrics["y"] * mm_to_dot),
                    pm,
                )

        qr_w_dot = (
            metrics["sizeMm"] * mm_to_dot
            if (metrics and qr_cfg.get("position") == "right")
            else 0.0
        )
        text_w_dot = max(1.0, w_dot - qr_w_dot - 2 * mm_to_dot)
        y_cursor = float(y_off) + 2 * mm_to_dot

        for row in (tmpl.get("rows") or []):
            parts: list[str] = []
            for f in (row.get("fields") or []):
                k = f.get("key") if isinstance(f, dict) else str(f)
                v = data.get(k)
                if v is not None:
                    parts.append(str(v))
            text = (row.get("sep") or " ").join(parts)
            if row.get("prefix"):
                text = row["prefix"] + text
            if not text:
                continue
            font = QFont()
            font.setPointSizeF(float(row.get("size") or 9) * dpi / 72.0)
            st = row.get("style") or ""
            font.setBold("bold" in st)
            font.setItalic("italic" in st)
            painter.setFont(font)
            fm = painter.fontMetrics()
            lh = fm.height()
            painter.drawText(
                QRectF(x_off + 2 * mm_to_dot, y_cursor, text_w_dot, lh * 1.5),
                Qt.TextFlag.TextWordWrap,
                text,
            )
            y_cursor += lh * float(row.get("lineHeight") or 1.3)
