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

import json
import os
import tempfile
from typing import Optional

from PyQt6.QtCore import Qt, QMarginsF, QSizeF
from PyQt6.QtGui import QPainter, QPageSize, QColor, QFont
from PyQt6.QtPrintSupport import QPrinter, QPrintDialog, QAbstractPrintDialog
from PyQt6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
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
)

from app.views.base_view import BaseView
from app.services.label_service import (
    BUILTIN_TEMPLATES,
    PAPER_SIZES,
    LabelService,
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

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setStyleSheet("background: #08161b; color: #eef3ef;")

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 12, 16, 12)
        root.setSpacing(8)

        # ── Step title ────────────────────────────────────────────────
        root.addWidget(_section_label("Step 1: 选择标本", large=True))

        # ── label-proj-row ────────────────────────────────────────────
        proj_row = QHBoxLayout()
        proj_row.setSpacing(6)
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
        actions_row.setSpacing(4)
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
        bucket_row.setSpacing(8)

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

    def selected_indices(self) -> list[int]:
        return sorted(self._selected)

    def specimens(self) -> list[dict]:
        return self._specimens

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

    def _select_rna_only(self) -> None:
        self._selected = {
            i for i, sp in enumerate(self._specimens) if has_rna_tissue(sp)
        }
        self._sync_checkboxes()
        self._update_bucket_cards()

    def _select_sample_only(self) -> None:
        self._selected = {
            i for i, sp in enumerate(self._specimens) if not has_rna_tissue(sp)
        }
        self._sync_checkboxes()
        self._update_bucket_cards()

    def _select_none(self) -> None:
        self._selected.clear()
        self._sync_checkboxes()
        self._update_bucket_cards()

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
        self._selected_template_key: str = "tissueCompact" if self._is_tissue else "standard"
        self._selected_size_key: str = "label_30x15" if self._is_tissue else "label_50x30"
        self._custom_w: int = 30 if self._is_tissue else 50
        self._custom_h: int = 15 if self._is_tissue else 30
        self._specimens: list[dict] = []
        self._selected_indices: list[int] = []
        self._custom_template: Optional[dict] = None  # user-imported JSON
        self._field_edits: dict[str, str] = {}  # field overrides for first specimen

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
        self._manage_btn.clicked.connect(self._show_manage_hint)
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
        if key == "custom" and self._custom_template:
            return normalize_template(self._custom_template)
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

    def field_edits(self) -> dict[str, str]:
        return dict(self._field_edits)

    # ── Internal: rebuild template cards ─────────────────────────────

    def _rebuild_template_picker(self) -> None:
        # Remove all except stretch at end
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

        for key, tmpl in BUILTIN_TEMPLATES.items():
            is_tissue_tmpl = tmpl.get("flavor") == "tissue"
            if self._is_tissue and not is_tissue_tmpl:
                continue
            if not self._is_tissue and is_tissue_tmpl:
                continue

            card = self._make_template_card(key, tmpl, first_data, dims)
            self._picker_layout.addWidget(card)

        # Custom template card (from import)
        if self._custom_template:
            custom_card = self._make_template_card(
                "custom", self._custom_template, first_data, dims, badge="自定义"
            )
            self._picker_layout.addWidget(custom_card)

        # 新建自定义 card
        add_card = QFrame()
        add_card.setObjectName("TmplCard")
        add_card.setProperty("selected", "false")
        add_card.setStyleSheet(_CSS_TMPL_CARD)
        add_card.setFixedWidth(130)
        add_card.setCursor(Qt.CursorShape.PointingHandCursor)
        add_layout = QVBoxLayout(add_card)
        add_layout.setContentsMargins(6, 6, 6, 6)
        add_layout.setSpacing(4)
        title_row = QHBoxLayout()
        title_row.addWidget(QLabel("<b>新建自定义</b>"))
        title_row.addStretch()
        add_layout.addLayout(title_row)
        hint = QLabel("从当前默认模板复制一份，可独立保存管理")
        hint.setStyleSheet("color: #5f7d7a; font-size: 10px;")
        hint.setWordWrap(True)
        add_layout.addWidget(hint)
        add_layout.addStretch()

        def _new_custom() -> None:
            QMessageBox.information(
                self, "新建自定义",
                "请使用「导入 JSON」上传自定义模板文件，\n"
                "或在第 3 步 WYSIWYG 编辑器中直接编辑当前模板。"
            )

        add_card.mousePressEvent = lambda _e: _new_custom()  # type: ignore[method-assign]
        self._picker_layout.addWidget(add_card)
        self._picker_layout.addStretch()

    def _make_template_card(
        self,
        key: str,
        tmpl: dict,
        first_data: dict,
        dims: dict,
        badge: str = "内置",
    ) -> QFrame:
        is_selected = key == self._selected_template_key
        card = QFrame()
        card.setObjectName("TmplCard")
        card.setProperty("selected", str(is_selected).lower())
        card.setStyleSheet(_CSS_TMPL_CARD)
        card.setFixedWidth(130)
        card.setCursor(Qt.CursorShape.PointingHandCursor)

        layout = QVBoxLayout(card)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(4)

        # Header row: badge + name + 编辑 button
        header_row = QHBoxLayout()
        header_row.setSpacing(4)
        badge_lbl = QLabel(badge)
        badge_lbl.setStyleSheet(
            "background: rgba(41,185,171,0.15); color:#29b9ab; border-radius:3px;"
            " font-size:10px; padding: 1px 4px;"
        )
        header_row.addWidget(badge_lbl)
        name_lbl = QLabel(f"<b>{tmpl.get('name', key)}</b>")
        name_lbl.setStyleSheet("color:#eef3ef; font-size:11px;")
        header_row.addWidget(name_lbl, stretch=1)
        edit_btn = QPushButton("编辑")
        edit_btn.setFixedHeight(20)
        edit_btn.setFixedWidth(36)
        edit_btn.setStyleSheet(
            "QPushButton { background:transparent; border:1px solid rgba(145,182,181,0.25);"
            " border-radius:3px; color:#87a2a1; font-size:10px; padding:0; }"
            "QPushButton:hover { color:#29b9ab; border-color:#29b9ab; }"
        )
        edit_btn.setToolTip("复制为可编辑（不污染预设）")
        edit_btn.clicked.connect(lambda checked, k=key, t=tmpl: self._clone_to_custom(k, t))
        header_row.addWidget(edit_btn)
        layout.addLayout(header_row)

        # Mini preview (simple text lines — no live QGraphics for performance)
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

        # Click to select
        def _select(event: object, k: str = key) -> None:
            self._selected_template_key = k
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
        self._custom_template = clone
        self._selected_template_key = "custom"
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
            if self._is_tissue:
                data["flavor"] = "tissue"
            self._custom_template = data
            self._selected_template_key = "custom"
            self._rebuild_template_picker()
        except Exception as e:
            QMessageBox.warning(self, "导入失败", f"无法解析模板 JSON:\n{e}")

    def _show_manage_hint(self) -> None:
        QMessageBox.information(
            self, "模板管理",
            "当前版本支持「导入 JSON」上传自定义模板。\n"
            "模板导入后可在第 3 步 WYSIWYG 编辑器中预览与微调。"
        )

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

            current_val = self._field_edits.get(field_key, first_data.get(field_key) or "")
            inp = QLineEdit(str(current_val))
            inp.setStyleSheet(
                "QLineEdit { background:#0f2127; border:1px solid rgba(145,182,181,0.18);"
                " border-radius:4px; color:#eef3ef; padding:2px 6px; font-size:11px; }"
                "QLineEdit:focus { border-color:#29b9ab; }"
            )
            inp.textChanged.connect(
                lambda text, k=field_key: self._field_edits.update({k: text})
            )
            row_layout.addWidget(lbl)
            row_layout.addWidget(inp)
            self._field_inputs[field_key] = inp
            self._edit_fields_layout.addWidget(row_w)

    # ── Internal: size selection ──────────────────────────────────────

    def _select_size(self, key: str) -> None:
        self._selected_size_key = key
        self._apply_size_selection()

    def _apply_size_selection(self) -> None:
        for k, btn in self._size_btns.items():
            btn.setChecked(k == self._selected_size_key)
        is_custom = self._selected_size_key == "custom"
        self._custom_dims_widget.setVisible(is_custom)


class _Step2Widget(QWidget):
    """Step 2: template selection for sample + tissue buckets.

    Mirrors: renderLabelStep2({ classic: true }) + renderBucketColumn()
    """

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setStyleSheet("background: #08161b; color: #eef3ef;")

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 12, 16, 12)
        root.setSpacing(10)

        # Title row with persisted hint
        title_row = QHBoxLayout()
        title_row.setSpacing(8)
        title_row.addWidget(_section_label("Step 2: 选择模版", large=True))
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

        self._empty_hint = QLabel("请先在 Step 1 选择标本。")
        self._empty_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_hint.setStyleSheet("color:#5f7d7a; font-size:13px;")
        self._empty_hint.setVisible(False)
        root.addWidget(self._empty_hint)

    def refresh(self, specimens: list[dict], selected_indices: list[int]) -> None:
        """Update both bucket columns."""
        has_selection = bool(selected_indices)
        self._empty_hint.setVisible(not has_selection)
        self._splitter.setVisible(has_selection)

        if not has_selection:
            return

        tissue_indices = [
            i for i in selected_indices
            if i < len(specimens) and has_rna_tissue(specimens[i])
        ]
        has_tissue = bool(tissue_indices)
        self._tissue_scroll.setVisible(has_tissue)

        self._sample_col.refresh(specimens, selected_indices)
        if has_tissue:
            self._tissue_col.refresh(specimens, selected_indices)

    def selected_sample_template(self) -> dict:
        return self._sample_col.selected_template()

    def selected_tissue_template(self) -> dict:
        return self._tissue_col.selected_template()

    def selected_sample_dims(self) -> dict:
        return self._sample_col.selected_dims()

    def selected_tissue_dims(self) -> dict:
        return self._tissue_col.selected_dims()

    def field_edits(self) -> dict[str, str]:
        return self._sample_col.field_edits()


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
        root.setContentsMargins(16, 12, 16, 12)
        root.setSpacing(8)

        root.addWidget(_section_label("Step 3: 编辑预览", large=True))

        # Bucket toggle
        toggle_row = QHBoxLayout()
        toggle_row.setSpacing(4)
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
        root.setContentsMargins(16, 12, 16, 12)
        root.setSpacing(12)

        root.addWidget(_section_label("Step 4: 纸张 / 尺寸 / 份数", large=True))

        # Per-bucket paper-type rows (sample + tissue)
        paper_row = QHBoxLayout()
        paper_row.setSpacing(16)

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
            def _on_changed(checked: bool, _type: str = ptype, _attr: str = self_attr) -> None:
                if checked:
                    setattr(self, _attr, _type)
            rb.toggled.connect(_on_changed)
            radios.append(rb)

        if bucket == "sample":
            self._sample_radios = radios
        else:
            self._tissue_radios = radios

        return box

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
# Main view
# ─────────────────────────────────────────────────────────────────────────────

class LabelsView(BaseView):
    """标签打印页面 — 4-step wizard.

    Faithful Qt port of the web prototype's classic Step 1-4 flow.
    """

    view_id = "labels"
    nav_title = "标签打印"
    nav_icon = "🏷️"

    def __init__(self, ctx: "AppContext") -> None:
        self._specimens: list[dict] = []
        self._sample_job: Optional[dict] = None
        self._tissue_job: Optional[dict] = None
        super().__init__(ctx)

    def _setup_ui(self) -> None:
        self.setStyleSheet("background: #08161b; color: #eef3ef;" + _CSS_FULL)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── Top step navigation bar ────────────────────────────────────
        nav_frame = QFrame()
        nav_frame.setStyleSheet(
            "QFrame { background: #091e24; border-bottom: 1px solid rgba(145,182,181,0.12); }"
        )
        nav_bar = QHBoxLayout(nav_frame)
        nav_bar.setContentsMargins(12, 6, 12, 6)
        nav_bar.setSpacing(6)

        self._step_btns: list[QPushButton] = []
        step_labels = [
            "1 选标本",
            "2 选模版",
            "3 编辑",
            "4 打印",
        ]
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
        root.addWidget(nav_frame)

        # ── Stacked pages ──────────────────────────────────────────────
        self._stack = QStackedWidget()
        self._stack.setStyleSheet("background: #08161b;")

        self._step1 = _Step1Widget()
        self._step2 = _Step2Widget()
        self._step3 = _Step3Widget()
        self._step4 = _Step4Widget()

        self._stack.addWidget(self._step1)
        self._stack.addWidget(self._step2)
        self._stack.addWidget(self._step3)
        self._stack.addWidget(self._step4)
        root.addWidget(self._stack, stretch=1)

        # ── Bottom prev/next bar ───────────────────────────────────────
        bottom_frame = QFrame()
        bottom_frame.setStyleSheet(
            "QFrame { background: #091e24; border-top: 1px solid rgba(145,182,181,0.10); }"
        )
        bottom_bar = QHBoxLayout(bottom_frame)
        bottom_bar.setContentsMargins(12, 6, 12, 6)
        bottom_bar.setSpacing(8)

        self._btn_prev = _outline_btn("← 上一步")
        self._btn_next = _outline_btn("下一步 →")
        self._btn_prev.setFixedWidth(90)
        self._btn_next.setFixedWidth(90)
        self._btn_prev.clicked.connect(self._prev_step)
        self._btn_next.clicked.connect(self._next_step)

        bottom_bar.addWidget(self._btn_prev)
        bottom_bar.addStretch()
        bottom_bar.addWidget(self._btn_next)
        root.addWidget(bottom_frame)

        # Wire print buttons
        self._step4.sample_button.clicked.connect(lambda: self._print("sample"))
        self._step4.tissue_button.clicked.connect(lambda: self._print("tissue"))

        self._current_step: int = 0
        self._go_to_step(0)

    # ── on_activate ───────────────────────────────────────────────────

    def on_activate(self) -> None:
        """Called when user navigates to this page."""
        self._load_specimens()

    # ── Navigation ────────────────────────────────────────────────────

    def _go_to_step(self, idx: int) -> None:
        self._current_step = idx
        self._stack.setCurrentIndex(idx)
        for i, btn in enumerate(self._step_btns):
            btn.setChecked(i == idx)
        self._btn_prev.setEnabled(idx > 0)
        self._btn_next.setEnabled(idx < 3)

        # Trigger step-specific refresh on entry
        if idx == 1:
            self._refresh_step2()
        elif idx == 2:
            self._refresh_step3()
        elif idx == 3:
            self._refresh_step4()

    def _prev_step(self) -> None:
        if self._current_step > 0:
            self._go_to_step(self._current_step - 1)

    def _next_step(self) -> None:
        if self._current_step < 3:
            self._go_to_step(self._current_step + 1)

    # ── Data loading ──────────────────────────────────────────────────

    def _load_specimens(self) -> None:
        """Load specimens from DB via AppContext, populate Step 1."""
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

        self._specimens = specimens
        self._step1.set_specimens(specimens)

    # ── Step 2 refresh ─────────────────────────────────────────────────

    def _refresh_step2(self) -> None:
        indices = self._step1.selected_indices()
        self._step2.refresh(self._specimens, indices)

    # ── Step 3 refresh ─────────────────────────────────────────────────

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

    # ── Step 4 refresh ─────────────────────────────────────────────────

    def _refresh_step4(self) -> None:
        indices = self._step1.selected_indices()
        sample_tmpl = self._step2.selected_sample_template()
        tissue_tmpl = self._step2.selected_tissue_template()
        sample_dims = self._step2.selected_sample_dims()
        tissue_dims = self._step2.selected_tissue_dims()
        copies = self._step4.copies

        self._sample_job = LabelService.build_print_job(
            self._specimens, sample_tmpl, "sample",
            selected_indices=indices, dims=sample_dims, copies=copies,
        )
        self._tissue_job = LabelService.build_print_job(
            self._specimens, tissue_tmpl, "tissue",
            selected_indices=indices, dims=tissue_dims, copies=copies,
        )

        self._step4.update_counts(
            len(self._sample_job["items"]),
            len(self._tissue_job["items"]),
            self._sample_job.get("warnings") or [],
            self._tissue_job.get("warnings") or [],
            copies=copies,
        )

    # ── Printing ───────────────────────────────────────────────────────

    def _print(self, bucket: str) -> None:
        """Print the given bucket using QPrinter → QPrintDialog."""
        job = self._sample_job if bucket == "sample" else self._tissue_job
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

        printer = QPrinter(QPrinter.PrinterMode.HighResolution)
        page_size = QPageSize(
            QSizeF(w_mm, h_mm),
            QPageSize.Unit.Millimeter,
            "Custom",
        )
        printer.setPageSize(page_size)
        printer.setPageMargins(
            QMarginsF(2, 2, 2, 2), QPageSize.Unit.Millimeter
        )
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
        """Paint all label items onto QPrinter pages (QPainter)."""
        from app.utils.label_core import qr_metrics as _qr_metrics
        from app.widgets.label_editor import _generate_qr_pixmap, _mm_to_px

        items = job.get("items") or []
        dims = job.get("dims") or {}
        tmpl = job.get("template") or {}
        w_mm = float(dims.get("w", 60))
        h_mm = float(dims.get("h", 40))
        qr_cfg = tmpl.get("qr") or {}
        ecc = qr_cfg.get("ecc") or "Q"

        painter = QPainter()
        if not painter.begin(printer):
            return

        dpi = printer.resolution()
        mm_to_dot = dpi / 25.4

        for page_idx, item in enumerate(items):
            if page_idx > 0:
                printer.newPage()

            data = item.get("data") if isinstance(item, dict) else item
            if not data:
                continue

            w_dot = w_mm * mm_to_dot
            h_dot = h_mm * mm_to_dot

            painter.fillRect(0, 0, int(w_dot), int(h_dot), QColor("white"))

            metrics = _qr_metrics(tmpl, dims)
            if metrics is not None:
                qr_content_key = qr_cfg.get("content") or "uniqueId"
                qr_text = str(data.get(qr_content_key) or "")
                size_dot = int(metrics["sizeMm"] * mm_to_dot)
                pixmap = _generate_qr_pixmap(qr_text, size_dot, ecc)
                if pixmap:
                    x_dot = int(metrics["x"] * mm_to_dot)
                    y_dot = int(metrics["y"] * mm_to_dot)
                    painter.drawPixmap(x_dot, y_dot, pixmap)

            from PyQt6.QtCore import QRectF as _QRectF
            qr_w_dot = (
                (metrics["sizeMm"] * mm_to_dot)
                if (metrics and qr_cfg.get("position") == "right")
                else 0.0
            )
            text_w_dot = max(1.0, w_dot - qr_w_dot - 2 * mm_to_dot)
            y_cursor = 2 * mm_to_dot

            for row in (tmpl.get("rows") or []):
                fields = row.get("fields") or []
                parts: list[str] = []
                for f in fields:
                    key = f.get("key") if isinstance(f, dict) else str(f)
                    val = data.get(key)
                    if val is not None:
                        parts.append(str(val))
                text = (row.get("sep") or " ").join(parts)
                if row.get("prefix"):
                    text = row["prefix"] + text
                if not text:
                    continue

                size_pt = row.get("size") or 9
                font = QFont()
                font.setPointSizeF(float(size_pt) * dpi / 72.0)
                style = row.get("style") or ""
                font.setBold("bold" in style)
                font.setItalic("italic" in style)
                painter.setFont(font)

                fm = painter.fontMetrics()
                line_h = fm.height()
                rect = _QRectF(2 * mm_to_dot, y_cursor, text_w_dot, line_h * 1.5)
                painter.drawText(rect, Qt.TextFlag.TextWordWrap, text)
                lh = float(row.get("lineHeight") or 1.3)
                y_cursor += line_h * lh

        painter.end()
