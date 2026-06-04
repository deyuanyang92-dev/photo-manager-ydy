"""labels_layouts.py — Pluggable layout panels for the Labels printing page.

Three alternative layouts that implement _LabelLayoutBase:
  _LayoutDualBucket  (E) — two side-by-side bucket columns, compact specimen chips
  _LayoutStream      (F) — single-column flowing sections, collapsible
  _LayoutCinema      (D) — cinema-style: big preview dominant, specimen list on right

All layouts share the same deep-teal palette and import helpers from labels_view.
They communicate with LabelsView only via the _LabelLayoutBase signals/methods.
"""

from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import Qt, pyqtSignal, QTimer
from PyQt6.QtGui import QColor, QFont, QPixmap
from PyQt6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QSplitter,
    QVBoxLayout,
    QWidget,
    QComboBox,
)

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
    has_rna_tissue,
    specimen_to_label_data,
    qr_metrics,
    calculate_grid,
)


# ─────────────────────────────────────────────────────────────────────────────
# Base class
# ─────────────────────────────────────────────────────────────────────────────

class _LabelLayoutBase(QWidget):
    """Abstract base for pluggable Labels page layouts.

    All layouts must:
    - Emit print_sample / print_tissue signals when user requests print.
    - Implement load_specimens(specimens) to receive fresh specimen list.
    - Implement get_sample_job / get_tissue_job so LabelsView can do QPrinter.
    - Implement select_uid for external navigation.
    - Implement on_activate for per-visit refresh.
    """

    print_sample = pyqtSignal()
    print_tissue = pyqtSignal()

    def load_specimens(self, specimens: list[dict]) -> None:  # noqa: ARG002
        raise NotImplementedError

    def select_uid(self, uid: str) -> bool:  # noqa: ARG002
        return False

    def get_sample_job(self) -> dict | None:
        return None

    def get_tissue_job(self) -> dict | None:
        return None

    def on_activate(self) -> None:
        pass


# ─────────────────────────────────────────────────────────────────────────────
# Shared CSS (imported from labels_view at runtime to avoid circular import)
# ─────────────────────────────────────────────────────────────────────────────

def _get_css() -> str:
    """Lazy-import CSS constants from labels_view to avoid circular import."""
    from app.views.labels_view import _CSS_FULL  # noqa: PLC0415
    return _CSS_FULL


def _render_pixmap(tmpl: dict, dims: dict, data: dict, scale: float = 3.78):
    """Delegate to labels_view helper."""
    from app.views.labels_view import _render_label_pixmap  # noqa: PLC0415
    return _render_label_pixmap(tmpl, dims, data, scale)


# ─────────────────────────────────────────────────────────────────────────────
# Small UI helpers (independent; mirrors labels_view helpers)
# ─────────────────────────────────────────────────────────────────────────────

def _mk_outline_btn(text: str) -> QPushButton:
    b = QPushButton(text)
    b.setObjectName("OutlineBtn")
    return b


def _mk_primary_btn(text: str) -> QPushButton:
    b = QPushButton(text)
    b.setObjectName("PrimaryBtn")
    b.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
    return b


def _mk_tissue_btn(text: str) -> QPushButton:
    b = QPushButton(text)
    b.setObjectName("TissueBtn")
    b.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
    return b


def _mk_section_label(text: str, large: bool = False) -> QLabel:
    lbl = QLabel(text)
    size = "15px" if large else "13px"
    lbl.setStyleSheet(f"color: #eef3ef; font-size: {size}; font-weight: bold; margin-bottom: 4px;")
    return lbl


def _mk_muted_label(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setStyleSheet("color: #87a2a1; font-size: 11px;")
    lbl.setWordWrap(True)
    return lbl


def _mk_sep_line() -> QFrame:
    f = QFrame()
    f.setFrameShape(QFrame.Shape.HLine)
    f.setStyleSheet("color: rgba(145,182,181,0.15);")
    return f


_SCROLL_STYLE = (
    "QScrollArea { background: #08161b; border: none; }"
    "QScrollBar:vertical { background: #0c1e26; width: 8px; }"
    "QScrollBar::handle:vertical { background: rgba(145,182,181,0.25); border-radius: 4px; }"
    "QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }"
    "QScrollBar:horizontal { background: #0c1e26; height: 8px; }"
    "QScrollBar::handle:horizontal { background: rgba(145,182,181,0.25); border-radius: 4px; }"
)

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
QPushButton#SizeBtn:hover { border-color: #29b9ab; }
"""

_CHIP_CSS = """
QPushButton#ChipBtn {
    background: #0c2027;
    border: 1px solid rgba(145,182,181,0.18);
    border-radius: 10px;
    color: #cfe0db;
    padding: 2px 10px;
    font-size: 11px;
}
QPushButton#ChipBtn:checked {
    background: rgba(41,185,171,0.22);
    border: 1.5px solid #29b9ab;
    color: #29b9ab;
    font-weight: bold;
}
QPushButton#ChipBtn:hover { border-color: #29b9ab; }
"""

_TMPL_CHIP_CSS = """
QPushButton#TmplChip {
    background: #0c2027;
    border: 1px solid rgba(145,182,181,0.18);
    border-radius: 4px;
    color: #cfe0db;
    padding: 4px 10px;
    font-size: 11px;
}
QPushButton#TmplChip:checked {
    background: rgba(41,185,171,0.20);
    border: 1.5px solid #29b9ab;
    color: #29b9ab;
    font-weight: bold;
}
QPushButton#TmplChip:hover { border-color: #29b9ab; }
"""

_PAPER_SIZE_KEYS = [
    ("label_25x10",  "25×10"),
    ("label_30x15",  "30×15"),
    ("label_40x20",  "40×20"),
    ("label_50x30",  "50×30"),
    ("label_60x40",  "60×40"),
    ("label_70x50",  "70×50"),
    ("label_80x60",  "80×60"),
    ("label_100x70", "100×70"),
    ("custom",       "自定义"),
]


# ─────────────────────────────────────────────────────────────────────────────
# _LayoutDualBucket (E) — two side-by-side bucket columns
# ─────────────────────────────────────────────────────────────────────────────

class _LayoutDualBucket(_LabelLayoutBase):
    """Layout E: Compact specimen chips on top, two bucket columns below.

    Left column  = 🧪 样品瓶
    Right column = 🧬 RNAlater (visible only when RNA specimens selected)
    Each column has: template chips, big preview, paper controls, print button.
    """

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._specimens: list[dict] = []
        self._selected: set[int] = set()
        self._sample_job: dict | None = None
        self._tissue_job: dict | None = None
        self._sample_tmpl_key: str = "standard"
        self._tissue_tmpl_key: str = "tissueCompact"
        self._sample_size_key: str = "label_50x30"
        self._tissue_size_key: str = "label_30x15"
        self._sample_paper: str = "label"
        self._tissue_paper: str = "label"
        self._copies: int = 1

        # Library instances for template persistence
        self._sample_lib = LabelTemplateLibrary("sample")
        self._tissue_lib = LabelTemplateLibrary("tissue")
        self._sample_tmpl_key = self._sample_lib.selected_key()
        self._tissue_tmpl_key = self._tissue_lib.selected_key()
        self._sample_size_key = self._sample_lib.selected_size_key()
        self._tissue_size_key = self._tissue_lib.selected_size_key()

        self._setup_ui()
        self._rebuild_template_chips()

    def _setup_ui(self) -> None:
        self.setStyleSheet("background: #08161b; color: #eef3ef;" + _get_css()
                           + _CSS_SIZE_BTN + _CHIP_CSS + _TMPL_CHIP_CSS)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── Specimen chip toolbar ────────────────────────────────────────
        spec_frame = QFrame()
        spec_frame.setStyleSheet(
            "QFrame { background: #091e24; border-bottom: 1px solid rgba(145,182,181,0.10); }"
        )
        spec_frame.setFixedHeight(48)
        spec_outer = QHBoxLayout(spec_frame)
        spec_outer.setContentsMargins(10, 6, 10, 6)
        spec_outer.setSpacing(6)

        spec_scroll = QScrollArea()
        spec_scroll.setWidgetResizable(True)
        spec_scroll.setFrameShape(QFrame.Shape.NoFrame)
        spec_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        spec_scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        self._chip_container = QWidget()
        self._chip_container.setStyleSheet("background: transparent;")
        self._chip_row = QHBoxLayout(self._chip_container)
        self._chip_row.setContentsMargins(0, 0, 0, 0)
        self._chip_row.setSpacing(4)
        self._chip_row.addStretch()
        spec_scroll.setWidget(self._chip_container)
        spec_outer.addWidget(spec_scroll, stretch=1)

        # Action buttons
        for lbl, slot in [("全选", self._select_all), ("清空", self._select_none),
                           ("仅RNA", self._select_rna), ("仅样品", self._select_sample)]:
            btn = _mk_outline_btn(lbl)
            btn.setFixedHeight(28)
            btn.clicked.connect(slot)
            spec_outer.addWidget(btn)

        # Copies spinner
        spec_outer.addWidget(QLabel("份数"))
        self._copies_spin = QSpinBox()
        self._copies_spin.setRange(1, 20)
        self._copies_spin.setValue(1)
        self._copies_spin.setFixedWidth(55)
        self._copies_spin.setStyleSheet(
            "QSpinBox { background:#0f2127; border:1px solid rgba(145,182,181,0.18);"
            " border-radius:4px; color:#eef3ef; padding:2px; }"
        )
        self._copies_spin.valueChanged.connect(self._on_copies_changed)
        spec_outer.addWidget(self._copies_spin)
        root.addWidget(spec_frame)

        # ── Main splitter (two bucket columns) ──────────────────────────
        self._bucket_splitter = QSplitter(Qt.Orientation.Horizontal)
        self._bucket_splitter.setHandleWidth(10)
        self._bucket_splitter.setStyleSheet(
            "QSplitter::handle { background: rgba(145,182,181,0.08); }"
        )

        self._sample_col = self._make_bucket_col("sample")
        self._tissue_col = self._make_bucket_col("tissue")
        self._bucket_splitter.addWidget(self._sample_col)
        self._bucket_splitter.addWidget(self._tissue_col)
        self._bucket_splitter.setSizes([600, 600])

        root.addWidget(self._bucket_splitter, stretch=1)

    def _make_bucket_col(self, bucket: str) -> QWidget:
        """Build one bucket column (sample or tissue)."""
        is_tissue = bucket == "tissue"
        col = QWidget()
        col.setStyleSheet("background: #08161b;")
        layout = QVBoxLayout(col)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        # Header
        icon = "🧬" if is_tissue else "🧪"
        name = "RNAlater 组织管" if is_tissue else "样品瓶"
        head = QFrame()
        head.setStyleSheet(
            "QFrame { background: #10242a; border-radius: 6px;"
            " border: 1px solid rgba(145,182,181,0.12); }"
        )
        head_layout = QHBoxLayout(head)
        head_layout.setContentsMargins(10, 6, 10, 6)
        head_layout.setSpacing(6)
        icon_lbl = QLabel(icon)
        icon_lbl.setStyleSheet("font-size: 16px;")
        name_lbl = QLabel(f"<b>{name}</b>")
        name_lbl.setStyleSheet("color: #eef3ef; font-size: 13px;")
        head_layout.addWidget(icon_lbl)
        head_layout.addWidget(name_lbl)
        head_layout.addStretch()
        if is_tissue:
            self._tissue_count_lbl = QLabel("0 张")
            self._tissue_count_lbl.setStyleSheet("color: #4a90d9; font-size: 11px;")
            head_layout.addWidget(self._tissue_count_lbl)
        else:
            self._sample_count_lbl = QLabel("0 张")
            self._sample_count_lbl.setStyleSheet("color: #29b9ab; font-size: 11px;")
            head_layout.addWidget(self._sample_count_lbl)
        layout.addWidget(head)

        # Template chips (horizontal scroll)
        tmpl_scroll = QScrollArea()
        tmpl_scroll.setWidgetResizable(True)
        tmpl_scroll.setFrameShape(QFrame.Shape.NoFrame)
        tmpl_scroll.setFixedHeight(50)
        tmpl_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        tmpl_scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        tmpl_cont = QWidget()
        tmpl_cont.setStyleSheet("background: transparent;")
        tmpl_row = QHBoxLayout(tmpl_cont)
        tmpl_row.setContentsMargins(0, 0, 0, 0)
        tmpl_row.setSpacing(4)
        tmpl_scroll.setWidget(tmpl_cont)
        layout.addWidget(tmpl_scroll)

        if is_tissue:
            self._tissue_tmpl_cont = tmpl_cont
            self._tissue_tmpl_row = tmpl_row
        else:
            self._sample_tmpl_cont = tmpl_cont
            self._sample_tmpl_row = tmpl_row

        # Preview
        preview_frame = QFrame()
        preview_frame.setStyleSheet(
            "QFrame { background: #1a3540; border: 1px solid rgba(145,182,181,0.20);"
            " border-radius: 5px; }"
        )
        preview_frame.setFixedHeight(120)
        preview_inner = QVBoxLayout(preview_frame)
        preview_inner.setContentsMargins(4, 4, 4, 4)
        preview_lbl = QLabel("预览")
        preview_lbl.setStyleSheet("color: #87a2a1; font-size: 10px;")
        preview_img = QLabel()
        preview_img.setAlignment(Qt.AlignmentFlag.AlignCenter)
        preview_img.setStyleSheet("background: transparent;")
        preview_inner.addWidget(preview_lbl)
        preview_inner.addWidget(preview_img, stretch=1)
        layout.addWidget(preview_frame)

        if is_tissue:
            self._tissue_preview_img = preview_img
        else:
            self._sample_preview_img = preview_img

        # Paper type controls
        paper_row_w = QWidget()
        paper_row_w.setStyleSheet("background: transparent;")
        paper_layout = QHBoxLayout(paper_row_w)
        paper_layout.setContentsMargins(0, 0, 0, 0)
        paper_layout.setSpacing(4)
        paper_layout.addWidget(QLabel("纸张:"))

        paper_btns = {}
        for pkey, plbl in [("label", "标签纸"), ("a4", "A4"), ("a5", "A5")]:
            pb = QPushButton(plbl)
            pb.setObjectName("SizeBtn")
            pb.setCheckable(True)
            pb.setFixedHeight(26)
            pb.setChecked(pkey == "label")
            bucket_ref = bucket

            def _set_paper(checked: bool, _k: str = pkey, _b: str = bucket_ref) -> None:
                if checked:
                    if _b == "sample":
                        self._sample_paper = _k
                    else:
                        self._tissue_paper = _k
                    self._rebuild_jobs()

            pb.toggled.connect(_set_paper)
            paper_layout.addWidget(pb)
            paper_btns[pkey] = pb

        if is_tissue:
            self._tissue_paper_btns = paper_btns
        else:
            self._sample_paper_btns = paper_btns

        paper_layout.addStretch()
        layout.addWidget(paper_row_w)

        # Size buttons (compact, scrollable)
        size_scroll = QScrollArea()
        size_scroll.setWidgetResizable(True)
        size_scroll.setFrameShape(QFrame.Shape.NoFrame)
        size_scroll.setFixedHeight(32)
        size_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        size_scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        size_cont = QWidget()
        size_cont.setStyleSheet("background: transparent;")
        size_row = QHBoxLayout(size_cont)
        size_row.setContentsMargins(0, 0, 0, 0)
        size_row.setSpacing(3)

        for sk, sl in _PAPER_SIZE_KEYS:
            sb = QPushButton(sl + ("mm" if "×" in sl else ""))
            sb.setObjectName("SizeBtn")
            sb.setCheckable(True)
            sb.setFixedHeight(26)
            current_key = self._sample_size_key if not is_tissue else self._tissue_size_key
            sb.setChecked(sk == current_key)
            bkt = bucket

            def _set_size(checked: bool, _sk: str = sk, _b: str = bkt) -> None:
                if checked:
                    if _b == "sample":
                        self._sample_size_key = _sk
                        self._sample_lib.set_selected_size_key(_sk)
                    else:
                        self._tissue_size_key = _sk
                        self._tissue_lib.set_selected_size_key(_sk)
                    self._rebuild_jobs()

            sb.clicked.connect(_set_size)
            size_row.addWidget(sb)

        size_row.addStretch()
        size_scroll.setWidget(size_cont)
        layout.addWidget(size_scroll)

        # Print button
        if is_tissue:
            btn = _mk_tissue_btn("🖨 打印 RNAlater (0)")
            btn.clicked.connect(self.print_tissue)
            self._tissue_print_btn = btn
        else:
            btn = _mk_primary_btn("🖨 打印样品瓶 (0)")
            btn.clicked.connect(self.print_sample)
            self._sample_print_btn = btn
        layout.addWidget(btn)
        layout.addStretch()
        return col

    # ── Public interface ─────────────────────────────────────────────

    def load_specimens(self, specimens: list[dict]) -> None:
        self._specimens = specimens
        self._selected = set(range(len(specimens)))
        self._rebuild_chips()
        self._rebuild_template_chips()
        self._rebuild_jobs()

    def select_uid(self, uid: str) -> bool:
        for i, sp in enumerate(self._specimens):
            data = specimen_to_label_data(sp)
            if data.get("uniqueId") == uid:
                self._selected = {i}
                self._rebuild_chips()
                self._rebuild_jobs()
                return True
        return False

    def get_sample_job(self) -> dict | None:
        return self._sample_job

    def get_tissue_job(self) -> dict | None:
        return self._tissue_job

    def on_activate(self) -> None:
        self._rebuild_jobs()

    # ── Internal helpers ─────────────────────────────────────────────

    def _select_all(self) -> None:
        self._selected = set(range(len(self._specimens)))
        self._rebuild_chips()
        self._rebuild_jobs()

    def _select_none(self) -> None:
        self._selected.clear()
        self._rebuild_chips()
        self._rebuild_jobs()

    def _select_rna(self) -> None:
        self._selected = {i for i, sp in enumerate(self._specimens) if has_rna_tissue(sp)}
        self._rebuild_chips()
        self._rebuild_jobs()

    def _select_sample(self) -> None:
        self._selected = {i for i, sp in enumerate(self._specimens) if not has_rna_tissue(sp)}
        self._rebuild_chips()
        self._rebuild_jobs()

    def _on_copies_changed(self, val: int) -> None:
        self._copies = val
        self._rebuild_jobs()

    def _rebuild_chips(self) -> None:
        """Rebuild specimen chip row."""
        # Clear
        while self._chip_row.count():
            item = self._chip_row.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        for i, sp in enumerate(self._specimens):
            data = specimen_to_label_data(sp)
            uid_str = (data.get("uniqueId") or sp.get("id") or "?")[:12]
            btn = QPushButton(uid_str)
            btn.setObjectName("ChipBtn")
            btn.setCheckable(True)
            btn.setChecked(i in self._selected)
            btn.setFixedHeight(26)
            is_rna = has_rna_tissue(sp)
            if is_rna:
                btn.setToolTip("R前缀：RNA标本")

            def _toggle(checked: bool, _i: int = i) -> None:
                if checked:
                    self._selected.add(_i)
                else:
                    self._selected.discard(_i)
                self._rebuild_jobs()

            btn.toggled.connect(_toggle)
            self._chip_row.addWidget(btn)
        self._chip_row.addStretch()

    def _rebuild_template_chips(self) -> None:
        """Rebuild template chip rows for both buckets."""
        for bucket, row, key_attr, lib in [
            ("sample", self._sample_tmpl_row, "_sample_tmpl_key", self._sample_lib),
            ("tissue", self._tissue_tmpl_row, "_tissue_tmpl_key", self._tissue_lib),
        ]:
            while row.count():
                item = row.takeAt(0)
                if item.widget():
                    item.widget().deleteLater()

            is_tissue = bucket == "tissue"
            current_key = getattr(self, key_attr)

            for tkey, tmpl in BUILTIN_TEMPLATES.items():
                is_tissue_tmpl = tmpl.get("flavor") == "tissue"
                if is_tissue and not is_tissue_tmpl:
                    continue
                if not is_tissue and is_tissue_tmpl:
                    continue
                chip = QPushButton(tmpl.get("name", tkey))
                chip.setObjectName("TmplChip")
                chip.setCheckable(True)
                chip.setChecked(tkey == current_key)
                chip.setFixedHeight(28)
                b = bucket

                def _pick(checked: bool, _k: str = tkey, _b: str = b,
                          _attr: str = key_attr, _lib: LabelTemplateLibrary = lib) -> None:
                    if checked:
                        setattr(self, _attr, _k)
                        _lib.set_selected_key(_k)
                        self._rebuild_jobs()
                        self._update_previews()

                chip.clicked.connect(_pick)
                row.addWidget(chip)

            row.addStretch()

    def _get_dims(self, bucket: str) -> dict:
        key = self._sample_size_key if bucket == "sample" else self._tissue_size_key
        if key == "custom":
            return {"w": 50, "h": 30} if bucket == "sample" else {"w": 30, "h": 15}
        size = PAPER_SIZES.get(key)
        if size:
            return {"w": size["w"], "h": size["h"]}
        return {"w": 50, "h": 30}

    def _get_template(self, bucket: str) -> dict:
        key = self._sample_tmpl_key if bucket == "sample" else self._tissue_tmpl_key
        lib = self._sample_lib if bucket == "sample" else self._tissue_lib
        if is_library_key(key):
            rec_id = id_from_key(key)
            rec = lib.get(rec_id)
            if rec and rec.get("template"):
                return normalize_template(rec["template"])
            key = "tissueCompact" if bucket == "tissue" else "standard"
        return normalize_template(BUILTIN_TEMPLATES.get(key, BUILTIN_TEMPLATES.get(
            "tissueCompact" if bucket == "tissue" else "standard"
        )))

    def _rebuild_jobs(self) -> None:
        indices = sorted(self._selected)
        copies = self._copies_spin.value() if hasattr(self, "_copies_spin") else 1

        sample_tmpl = self._get_template("sample")
        tissue_tmpl = self._get_template("tissue")
        sample_dims = self._get_dims("sample")
        tissue_dims = self._get_dims("tissue")
        sample_paper_type = self._sample_paper
        tissue_paper_type = self._tissue_paper
        sample_paper = PAPER_SIZES.get(sample_paper_type) if sample_paper_type in ("a4", "a5") else None
        tissue_paper = PAPER_SIZES.get(tissue_paper_type) if tissue_paper_type in ("a4", "a5") else None

        self._sample_job = LabelService.build_print_job(
            self._specimens, sample_tmpl, "sample",
            selected_indices=indices, dims=sample_dims, copies=copies,
            paper_type=sample_paper_type, paper=sample_paper, edits={},
        )
        self._tissue_job = LabelService.build_print_job(
            self._specimens, tissue_tmpl, "tissue",
            selected_indices=indices, dims=tissue_dims, copies=copies,
            paper_type=tissue_paper_type, paper=tissue_paper, edits={},
        )

        sample_n = len(self._sample_job["items"]) if self._sample_job else 0
        tissue_n = len(self._tissue_job["items"]) if self._tissue_job else 0

        if hasattr(self, "_sample_count_lbl"):
            self._sample_count_lbl.setText(f"{sample_n} 张")
        if hasattr(self, "_tissue_count_lbl"):
            self._tissue_count_lbl.setText(f"{tissue_n} 张")
        if hasattr(self, "_sample_print_btn"):
            self._sample_print_btn.setText(f"🖨 打印样品瓶 ({sample_n})")
            self._sample_print_btn.setEnabled(sample_n > 0)
        if hasattr(self, "_tissue_print_btn"):
            self._tissue_print_btn.setText(f"🖨 打印 RNAlater ({tissue_n})")
            self._tissue_print_btn.setEnabled(tissue_n > 0)
            self._tissue_col.setVisible(tissue_n > 0 or any(
                has_rna_tissue(sp) for i, sp in enumerate(self._specimens) if i in self._selected
            ))

        self._update_previews()

    def _update_previews(self) -> None:
        indices = sorted(self._selected)
        first_data: dict = {}
        if indices and self._specimens:
            idx = indices[0]
            if idx < len(self._specimens):
                first_data = specimen_to_label_data(self._specimens[idx])

        for bucket, img_attr in [("sample", "_sample_preview_img"), ("tissue", "_tissue_preview_img")]:
            tmpl = self._get_template(bucket)
            dims = self._get_dims(bucket)
            img_lbl = getattr(self, img_attr, None)
            if img_lbl is None:
                continue
            if not first_data:
                img_lbl.clear()
                img_lbl.setText("选标本后预览")
                img_lbl.setStyleSheet("color: #5f7d7a; font-size: 11px; background: transparent;")
                continue
            pm = _render_pixmap(tmpl, dims, first_data, scale=3.0)
            h = 80
            if pm.height() > 0:
                r = h / pm.height()
                scaled = pm.scaled(
                    int(pm.width() * r), h,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
                img_lbl.setStyleSheet("background: transparent;")
                img_lbl.setPixmap(scaled)


# ─────────────────────────────────────────────────────────────────────────────
# _LayoutStream (F) — flowing single-column sections
# ─────────────────────────────────────────────────────────────────────────────

class _CollapsibleSection(QWidget):
    """A section widget with a clickable header that hides/shows its body."""

    def __init__(self, title: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setStyleSheet("background: transparent;")
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Header
        self._header = QFrame()
        self._header.setStyleSheet(
            "QFrame { background: #10242a; border: 1px solid rgba(145,182,181,0.12);"
            " border-radius: 5px; } QFrame:hover { border-color: rgba(41,185,171,0.35); }"
        )
        self._header.setCursor(Qt.CursorShape.PointingHandCursor)
        h_layout = QHBoxLayout(self._header)
        h_layout.setContentsMargins(10, 6, 10, 6)
        self._title_lbl = QLabel(title)
        self._title_lbl.setStyleSheet("color: #eef3ef; font-size: 13px; font-weight: bold;")
        self._arrow_lbl = QLabel("▾")
        self._arrow_lbl.setStyleSheet("color: #29b9ab; font-size: 12px;")
        h_layout.addWidget(self._title_lbl)
        h_layout.addStretch()
        h_layout.addWidget(self._arrow_lbl)
        root.addWidget(self._header)

        # Body
        self._body = QWidget()
        self._body.setStyleSheet("background: transparent;")
        self._body_layout = QVBoxLayout(self._body)
        self._body_layout.setContentsMargins(0, 6, 0, 6)
        self._body_layout.setSpacing(6)
        root.addWidget(self._body)

        self._expanded = True
        self._header.mousePressEvent = lambda e: self.toggle()  # type: ignore[method-assign]

    def toggle(self) -> None:
        self._expanded = not self._expanded
        self._body.setVisible(self._expanded)
        self._arrow_lbl.setText("▾" if self._expanded else "▸")

    def body(self) -> QWidget:
        return self._body

    def body_layout(self) -> QVBoxLayout:
        return self._body_layout

    def set_title(self, title: str) -> None:
        self._title_lbl.setText(title)


class _LayoutStream(_LabelLayoutBase):
    """Layout F: flowing single-column sections, each collapsible.

    Sections:
      1. 选标本 — specimen chips + action buttons
      2. 🧪 样品模板 — template chips, preview, controls
      3. 🧬 RNA模板 — same for tissue (visible only when RNA present)

    Toolbar at top: [打印样品瓶] [打印RNA] [份数]
    """

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._specimens: list[dict] = []
        self._selected: set[int] = set()
        self._sample_job: dict | None = None
        self._tissue_job: dict | None = None
        self._sample_tmpl_key: str = "standard"
        self._tissue_tmpl_key: str = "tissueCompact"
        self._sample_size_key: str = "label_50x30"
        self._tissue_size_key: str = "label_30x15"
        self._sample_paper: str = "label"
        self._tissue_paper: str = "label"

        self._sample_lib = LabelTemplateLibrary("sample")
        self._tissue_lib = LabelTemplateLibrary("tissue")
        self._sample_tmpl_key = self._sample_lib.selected_key()
        self._tissue_tmpl_key = self._tissue_lib.selected_key()
        _sk = self._sample_lib.selected_size_key()
        self._sample_size_key = _sk if _sk != "custom" else "label_50x30"
        _tk = self._tissue_lib.selected_size_key()
        self._tissue_size_key = _tk if _tk != "custom" else "label_30x15"

        self._setup_ui()
        self._rebuild_stream_template_chips()

    def _setup_ui(self) -> None:
        self.setStyleSheet("background: #08161b; color: #eef3ef;" + _get_css()
                           + _CSS_SIZE_BTN + _CHIP_CSS + _TMPL_CHIP_CSS)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── Top toolbar ──────────────────────────────────────────────────
        toolbar = QFrame()
        toolbar.setStyleSheet(
            "QFrame { background: #091e24; border-bottom: 1px solid rgba(145,182,181,0.10); }"
        )
        toolbar.setFixedHeight(44)
        tb_layout = QHBoxLayout(toolbar)
        tb_layout.setContentsMargins(12, 6, 12, 6)
        tb_layout.setSpacing(8)

        self._btn_print_sample = _mk_primary_btn("🖨 打印样品瓶 (0)")
        self._btn_print_sample.setFixedHeight(32)
        self._btn_print_sample.clicked.connect(self.print_sample)
        tb_layout.addWidget(self._btn_print_sample)

        self._btn_print_tissue = _mk_tissue_btn("🖨 打印RNA (0)")
        self._btn_print_tissue.setFixedHeight(32)
        self._btn_print_tissue.clicked.connect(self.print_tissue)
        tb_layout.addWidget(self._btn_print_tissue)

        tb_layout.addStretch()
        tb_layout.addWidget(QLabel("份数"))
        self._copies_spin = QSpinBox()
        self._copies_spin.setRange(1, 20)
        self._copies_spin.setValue(1)
        self._copies_spin.setFixedWidth(55)
        self._copies_spin.setStyleSheet(
            "QSpinBox { background:#0f2127; border:1px solid rgba(145,182,181,0.18);"
            " border-radius:4px; color:#eef3ef; padding:2px; }"
        )
        self._copies_spin.valueChanged.connect(lambda _v: self._rebuild_jobs())
        tb_layout.addWidget(self._copies_spin)
        root.addWidget(toolbar)

        # ── Scrollable content column ────────────────────────────────────
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet(_SCROLL_STYLE)

        content = QWidget()
        content.setStyleSheet("background: #08161b;")
        self._content_layout = QVBoxLayout(content)
        self._content_layout.setContentsMargins(12, 10, 12, 10)
        self._content_layout.setSpacing(8)

        # Section 1: Specimens
        self._sec_specimens = _CollapsibleSection("🔍 选标本")
        spec_body = self._sec_specimens.body_layout()

        # Action buttons row
        actions_row = QHBoxLayout()
        actions_row.setSpacing(4)
        for lbl, slot in [("全选", self._select_all), ("清空", self._select_none),
                           ("仅RNA", self._select_rna), ("仅样品", self._select_sample)]:
            b = _mk_outline_btn(lbl)
            b.setFixedHeight(26)
            b.clicked.connect(slot)
            actions_row.addWidget(b)
        actions_row.addStretch()
        spec_body.addLayout(actions_row)

        # Specimen chips (horizontal scroll)
        spec_scroll = QScrollArea()
        spec_scroll.setWidgetResizable(True)
        spec_scroll.setFrameShape(QFrame.Shape.NoFrame)
        spec_scroll.setFixedHeight(36)
        spec_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        spec_scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        self._stream_chip_cont = QWidget()
        self._stream_chip_cont.setStyleSheet("background: transparent;")
        self._stream_chip_row = QHBoxLayout(self._stream_chip_cont)
        self._stream_chip_row.setContentsMargins(0, 0, 0, 0)
        self._stream_chip_row.setSpacing(4)
        self._stream_chip_row.addStretch()
        spec_scroll.setWidget(self._stream_chip_cont)
        spec_body.addWidget(spec_scroll)
        self._content_layout.addWidget(self._sec_specimens)

        # Section 2: Sample template
        self._sec_sample = _CollapsibleSection("🧪 样品模板")
        self._build_template_section(self._sec_sample.body_layout(), "sample")
        self._content_layout.addWidget(self._sec_sample)

        # Section 3: Tissue template
        self._sec_tissue = _CollapsibleSection("🧬 RNA 模板")
        self._build_template_section(self._sec_tissue.body_layout(), "tissue")
        self._sec_tissue.setVisible(False)
        self._content_layout.addWidget(self._sec_tissue)

        self._content_layout.addStretch()
        scroll.setWidget(content)
        root.addWidget(scroll, stretch=1)

    def _build_template_section(self, layout: QVBoxLayout, bucket: str) -> None:
        """Build template chips + preview + controls into a QVBoxLayout."""
        is_tissue = bucket == "tissue"

        # Template chips
        tmpl_scroll = QScrollArea()
        tmpl_scroll.setWidgetResizable(True)
        tmpl_scroll.setFrameShape(QFrame.Shape.NoFrame)
        tmpl_scroll.setFixedHeight(36)
        tmpl_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        tmpl_scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        tmpl_cont = QWidget()
        tmpl_cont.setStyleSheet("background: transparent;")
        tmpl_row = QHBoxLayout(tmpl_cont)
        tmpl_row.setContentsMargins(0, 0, 0, 0)
        tmpl_row.setSpacing(4)
        tmpl_row.addStretch()
        tmpl_scroll.setWidget(tmpl_cont)
        layout.addWidget(tmpl_scroll)

        if is_tissue:
            self._stream_tissue_tmpl_row = tmpl_row
        else:
            self._stream_sample_tmpl_row = tmpl_row

        # Preview
        preview_frame = QFrame()
        preview_frame.setStyleSheet(
            "QFrame { background: #1a3540; border: 1px solid rgba(145,182,181,0.20);"
            " border-radius: 5px; }"
        )
        preview_frame.setFixedHeight(100)
        pv_inner = QVBoxLayout(preview_frame)
        pv_inner.setContentsMargins(4, 4, 4, 4)
        preview_img = QLabel()
        preview_img.setAlignment(Qt.AlignmentFlag.AlignCenter)
        preview_img.setStyleSheet("background: transparent;")
        pv_inner.addWidget(preview_img)
        layout.addWidget(preview_frame)

        if is_tissue:
            self._stream_tissue_preview = preview_img
        else:
            self._stream_sample_preview = preview_img

        # Inline controls row: 纸张 + 尺寸
        controls_row = QHBoxLayout()
        controls_row.setSpacing(4)
        controls_row.addWidget(QLabel("纸张:"))
        for pkey, plbl in [("label", "标签纸"), ("a4", "A4"), ("a5", "A5")]:
            pb = QPushButton(plbl)
            pb.setObjectName("SizeBtn")
            pb.setCheckable(True)
            pb.setFixedHeight(26)
            current_paper = self._sample_paper if not is_tissue else self._tissue_paper
            pb.setChecked(pkey == current_paper)
            b = bucket

            def _set_paper(checked: bool, _k: str = pkey, _b: str = b) -> None:
                if checked:
                    if _b == "sample":
                        self._sample_paper = _k
                    else:
                        self._tissue_paper = _k
                    self._rebuild_jobs()

            pb.toggled.connect(_set_paper)
            controls_row.addWidget(pb)

        controls_row.addSpacing(8)
        controls_row.addWidget(QLabel("尺寸:"))
        size_combo = QComboBox()
        size_combo.setStyleSheet(
            "QComboBox { background:#0f2127; border:1px solid rgba(145,182,181,0.18);"
            " border-radius:4px; color:#eef3ef; padding:3px 6px; }"
            "QComboBox::drop-down { border:none; }"
        )
        for sk, sl in _PAPER_SIZE_KEYS:
            size_combo.addItem(sl + ("mm" if "×" in sl else ""), sk)
        current_size = self._sample_size_key if not is_tissue else self._tissue_size_key
        for ci in range(size_combo.count()):
            if size_combo.itemData(ci) == current_size:
                size_combo.setCurrentIndex(ci)
                break
        b = bucket
        lib = self._sample_lib if not is_tissue else self._tissue_lib
        attr = "_sample_size_key" if not is_tissue else "_tissue_size_key"

        def _size_changed(idx: int, _b: str = b, _lib: LabelTemplateLibrary = lib,
                          _attr: str = attr) -> None:
            key = size_combo.itemData(idx)
            setattr(self, _attr, key)
            _lib.set_selected_size_key(key)
            self._rebuild_jobs()

        size_combo.currentIndexChanged.connect(_size_changed)
        controls_row.addWidget(size_combo)
        controls_row.addStretch()
        layout.addLayout(controls_row)

    # ── Public interface ─────────────────────────────────────────────

    def load_specimens(self, specimens: list[dict]) -> None:
        self._specimens = specimens
        self._selected = set(range(len(specimens)))
        self._rebuild_stream_chips()
        self._rebuild_stream_template_chips()
        self._rebuild_jobs()

    def select_uid(self, uid: str) -> bool:
        for i, sp in enumerate(self._specimens):
            data = specimen_to_label_data(sp)
            if data.get("uniqueId") == uid:
                self._selected = {i}
                self._rebuild_stream_chips()
                self._rebuild_jobs()
                return True
        return False

    def get_sample_job(self) -> dict | None:
        return self._sample_job

    def get_tissue_job(self) -> dict | None:
        return self._tissue_job

    def on_activate(self) -> None:
        self._rebuild_jobs()

    # ── Internal helpers ─────────────────────────────────────────────

    def _select_all(self) -> None:
        self._selected = set(range(len(self._specimens)))
        self._rebuild_stream_chips()
        self._rebuild_jobs()

    def _select_none(self) -> None:
        self._selected.clear()
        self._rebuild_stream_chips()
        self._rebuild_jobs()

    def _select_rna(self) -> None:
        self._selected = {i for i, sp in enumerate(self._specimens) if has_rna_tissue(sp)}
        self._rebuild_stream_chips()
        self._rebuild_jobs()

    def _select_sample(self) -> None:
        self._selected = {i for i, sp in enumerate(self._specimens) if not has_rna_tissue(sp)}
        self._rebuild_stream_chips()
        self._rebuild_jobs()

    def _rebuild_stream_chips(self) -> None:
        while self._stream_chip_row.count():
            item = self._stream_chip_row.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        for i, sp in enumerate(self._specimens):
            data = specimen_to_label_data(sp)
            uid_str = (data.get("uniqueId") or sp.get("id") or "?")[:12]
            btn = QPushButton(uid_str)
            btn.setObjectName("ChipBtn")
            btn.setCheckable(True)
            btn.setChecked(i in self._selected)
            btn.setFixedHeight(26)
            if has_rna_tissue(sp):
                btn.setToolTip("RNA标本")

            def _toggle(checked: bool, _i: int = i) -> None:
                if checked:
                    self._selected.add(_i)
                else:
                    self._selected.discard(_i)
                self._rebuild_jobs()

            btn.toggled.connect(_toggle)
            self._stream_chip_row.addWidget(btn)
        self._stream_chip_row.addStretch()

        sel_count = len(self._selected)
        self._sec_specimens.set_title(f"🔍 选标本 ({sel_count}/{len(self._specimens)})")

    def _rebuild_stream_template_chips(self) -> None:
        for bucket, row, key_attr, lib in [
            ("sample", self._stream_sample_tmpl_row, "_sample_tmpl_key", self._sample_lib),
            ("tissue", self._stream_tissue_tmpl_row, "_tissue_tmpl_key", self._tissue_lib),
        ]:
            while row.count():
                item = row.takeAt(0)
                if item.widget():
                    item.widget().deleteLater()

            is_tissue = bucket == "tissue"
            current_key = getattr(self, key_attr)

            for tkey, tmpl in BUILTIN_TEMPLATES.items():
                is_tissue_tmpl = tmpl.get("flavor") == "tissue"
                if is_tissue and not is_tissue_tmpl:
                    continue
                if not is_tissue and is_tissue_tmpl:
                    continue
                chip = QPushButton(tmpl.get("name", tkey))
                chip.setObjectName("TmplChip")
                chip.setCheckable(True)
                chip.setChecked(tkey == current_key)
                chip.setFixedHeight(28)

                def _pick(checked: bool, _k: str = tkey, _attr: str = key_attr,
                          _lib: LabelTemplateLibrary = lib) -> None:
                    if checked:
                        setattr(self, _attr, _k)
                        _lib.set_selected_key(_k)
                        self._rebuild_jobs()
                        self._update_stream_previews()

                chip.clicked.connect(_pick)
                row.addWidget(chip)
            row.addStretch()

    def _get_dims(self, bucket: str) -> dict:
        key = self._sample_size_key if bucket == "sample" else self._tissue_size_key
        if key == "custom":
            return {"w": 50, "h": 30} if bucket == "sample" else {"w": 30, "h": 15}
        size = PAPER_SIZES.get(key)
        if size:
            return {"w": size["w"], "h": size["h"]}
        return {"w": 50, "h": 30}

    def _get_template(self, bucket: str) -> dict:
        key = self._sample_tmpl_key if bucket == "sample" else self._tissue_tmpl_key
        lib = self._sample_lib if bucket == "sample" else self._tissue_lib
        if is_library_key(key):
            rec_id = id_from_key(key)
            rec = lib.get(rec_id)
            if rec and rec.get("template"):
                return normalize_template(rec["template"])
            key = "tissueCompact" if bucket == "tissue" else "standard"
        return normalize_template(BUILTIN_TEMPLATES.get(key, BUILTIN_TEMPLATES.get(
            "tissueCompact" if bucket == "tissue" else "standard"
        )))

    def _rebuild_jobs(self) -> None:
        indices = sorted(self._selected)
        copies = self._copies_spin.value() if hasattr(self, "_copies_spin") else 1

        sample_tmpl = self._get_template("sample")
        tissue_tmpl = self._get_template("tissue")
        sample_dims = self._get_dims("sample")
        tissue_dims = self._get_dims("tissue")

        sample_paper = PAPER_SIZES.get(self._sample_paper) if self._sample_paper in ("a4", "a5") else None
        tissue_paper = PAPER_SIZES.get(self._tissue_paper) if self._tissue_paper in ("a4", "a5") else None

        self._sample_job = LabelService.build_print_job(
            self._specimens, sample_tmpl, "sample",
            selected_indices=indices, dims=sample_dims, copies=copies,
            paper_type=self._sample_paper, paper=sample_paper, edits={},
        )
        self._tissue_job = LabelService.build_print_job(
            self._specimens, tissue_tmpl, "tissue",
            selected_indices=indices, dims=tissue_dims, copies=copies,
            paper_type=self._tissue_paper, paper=tissue_paper, edits={},
        )

        sample_n = len(self._sample_job["items"]) if self._sample_job else 0
        tissue_n = len(self._tissue_job["items"]) if self._tissue_job else 0

        if hasattr(self, "_btn_print_sample"):
            self._btn_print_sample.setText(f"🖨 打印样品瓶 ({sample_n})")
            self._btn_print_sample.setEnabled(sample_n > 0)
        if hasattr(self, "_btn_print_tissue"):
            self._btn_print_tissue.setText(f"🖨 打印RNA ({tissue_n})")
            self._btn_print_tissue.setEnabled(tissue_n > 0)

        has_rna = any(has_rna_tissue(self._specimens[i]) for i in indices if i < len(self._specimens))
        if hasattr(self, "_sec_tissue"):
            self._sec_tissue.setVisible(has_rna)

        self._update_stream_previews()

    def _update_stream_previews(self) -> None:
        indices = sorted(self._selected)
        first_data: dict = {}
        if indices and self._specimens:
            idx = indices[0]
            if idx < len(self._specimens):
                first_data = specimen_to_label_data(self._specimens[idx])

        for bucket, img_attr in [("sample", "_stream_sample_preview"), ("tissue", "_stream_tissue_preview")]:
            tmpl = self._get_template(bucket)
            dims = self._get_dims(bucket)
            img_lbl = getattr(self, img_attr, None)
            if img_lbl is None:
                continue
            if not first_data:
                img_lbl.clear()
                img_lbl.setText("选标本后预览")
                img_lbl.setStyleSheet("color: #5f7d7a; font-size: 11px; background: transparent;")
                continue
            pm = _render_pixmap(tmpl, dims, first_data, scale=3.0)
            h = 70
            if pm.height() > 0:
                r = h / pm.height()
                scaled = pm.scaled(
                    int(pm.width() * r), h,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
                img_lbl.setStyleSheet("background: transparent;")
                img_lbl.setPixmap(scaled)


# ─────────────────────────────────────────────────────────────────────────────
# _LayoutCinema (D) — big preview dominant
# ─────────────────────────────────────────────────────────────────────────────

class _LayoutCinema(_LabelLayoutBase):
    """Layout D: Cinema-style. Specimen navigator + large preview on left; list on right.

    Structure:
      Top toolbar: [打印样品] [打印RNA] [份数] | [🧪 样品][🧬 RNA] bucket toggle
      Main splitter:
        Left (stretch):
          Template chips (horizontal)
          Size + paper (compact row)
          BIG PREVIEW (max scale, grows with space)
          Navigator: [←] [1/N] [→] specimen navigator
        Right (fixed 200px):
          Specimen list (QListWidget with checkboxes)
          [全选][RNA][样品][清空] buttons
          Stats: 🧪N 🧬M
    """

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._specimens: list[dict] = []
        self._selected: set[int] = set()
        self._sample_job: dict | None = None
        self._tissue_job: dict | None = None
        self._sample_tmpl_key: str = "standard"
        self._tissue_tmpl_key: str = "tissueCompact"
        self._sample_size_key: str = "label_50x30"
        self._tissue_size_key: str = "label_30x15"
        self._sample_paper: str = "label"
        self._tissue_paper: str = "label"
        self._current_bucket: str = "sample"
        self._preview_idx: int = 0  # which specimen to preview

        self._sample_lib = LabelTemplateLibrary("sample")
        self._tissue_lib = LabelTemplateLibrary("tissue")
        self._sample_tmpl_key = self._sample_lib.selected_key()
        self._tissue_tmpl_key = self._tissue_lib.selected_key()
        _sk = self._sample_lib.selected_size_key()
        self._sample_size_key = _sk if _sk != "custom" else "label_50x30"
        _tk = self._tissue_lib.selected_size_key()
        self._tissue_size_key = _tk if _tk != "custom" else "label_30x15"

        self._setup_ui()
        self._rebuild_cinema_template_chips()

    def _setup_ui(self) -> None:
        self.setStyleSheet("background: #08161b; color: #eef3ef;" + _get_css()
                           + _CSS_SIZE_BTN + _CHIP_CSS + _TMPL_CHIP_CSS)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── Top toolbar ──────────────────────────────────────────────────
        toolbar = QFrame()
        toolbar.setStyleSheet(
            "QFrame { background: #091e24; border-bottom: 1px solid rgba(145,182,181,0.10); }"
        )
        toolbar.setFixedHeight(44)
        tb_layout = QHBoxLayout(toolbar)
        tb_layout.setContentsMargins(12, 6, 12, 6)
        tb_layout.setSpacing(8)

        self._btn_print_sample = _mk_primary_btn("🖨 打印样品 (0)")
        self._btn_print_sample.setFixedHeight(32)
        self._btn_print_sample.clicked.connect(self.print_sample)
        tb_layout.addWidget(self._btn_print_sample)

        self._btn_print_tissue = _mk_tissue_btn("🖨 打印RNA (0)")
        self._btn_print_tissue.setFixedHeight(32)
        self._btn_print_tissue.clicked.connect(self.print_tissue)
        tb_layout.addWidget(self._btn_print_tissue)

        tb_layout.addStretch()

        # Bucket toggle
        self._bucket_sample_btn = QPushButton("🧪 样品")
        self._bucket_sample_btn.setObjectName("ChipBtn")
        self._bucket_sample_btn.setCheckable(True)
        self._bucket_sample_btn.setChecked(True)
        self._bucket_sample_btn.setFixedHeight(30)
        self._bucket_sample_btn.clicked.connect(lambda: self._switch_bucket("sample"))
        tb_layout.addWidget(self._bucket_sample_btn)

        self._bucket_tissue_btn = QPushButton("🧬 RNA")
        self._bucket_tissue_btn.setObjectName("ChipBtn")
        self._bucket_tissue_btn.setCheckable(True)
        self._bucket_tissue_btn.setFixedHeight(30)
        self._bucket_tissue_btn.clicked.connect(lambda: self._switch_bucket("tissue"))
        tb_layout.addWidget(self._bucket_tissue_btn)

        tb_layout.addSpacing(12)
        tb_layout.addWidget(QLabel("份数"))
        self._copies_spin = QSpinBox()
        self._copies_spin.setRange(1, 20)
        self._copies_spin.setValue(1)
        self._copies_spin.setFixedWidth(55)
        self._copies_spin.setStyleSheet(
            "QSpinBox { background:#0f2127; border:1px solid rgba(145,182,181,0.18);"
            " border-radius:4px; color:#eef3ef; padding:2px; }"
        )
        self._copies_spin.valueChanged.connect(lambda _v: self._rebuild_jobs())
        tb_layout.addWidget(self._copies_spin)
        root.addWidget(toolbar)

        # ── Main splitter ────────────────────────────────────────────────
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setHandleWidth(8)
        splitter.setStyleSheet("QSplitter::handle { background: rgba(145,182,181,0.08); }")

        # Left panel (preview area)
        left = QWidget()
        left.setStyleSheet("background: #08161b;")
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(10, 10, 10, 10)
        left_layout.setSpacing(8)

        # Template chips
        tmpl_scroll = QScrollArea()
        tmpl_scroll.setWidgetResizable(True)
        tmpl_scroll.setFrameShape(QFrame.Shape.NoFrame)
        tmpl_scroll.setFixedHeight(36)
        tmpl_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        tmpl_scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        tmpl_cont = QWidget()
        tmpl_cont.setStyleSheet("background: transparent;")
        self._cinema_tmpl_row = QHBoxLayout(tmpl_cont)
        self._cinema_tmpl_row.setContentsMargins(0, 0, 0, 0)
        self._cinema_tmpl_row.setSpacing(4)
        self._cinema_tmpl_row.addStretch()
        tmpl_scroll.setWidget(tmpl_cont)
        left_layout.addWidget(tmpl_scroll)

        # Paper + size controls (compact row)
        ctrl_row = QHBoxLayout()
        ctrl_row.setSpacing(4)
        ctrl_row.addWidget(QLabel("纸张:"))
        self._cinema_paper_btns: dict[str, QPushButton] = {}
        for pkey, plbl in [("label", "标签纸"), ("a4", "A4"), ("a5", "A5")]:
            pb = QPushButton(plbl)
            pb.setObjectName("SizeBtn")
            pb.setCheckable(True)
            pb.setChecked(pkey == "label")
            pb.setFixedHeight(26)

            def _set_paper(checked: bool, _k: str = pkey) -> None:
                if checked:
                    if self._current_bucket == "sample":
                        self._sample_paper = _k
                    else:
                        self._tissue_paper = _k
                    self._rebuild_jobs()

            pb.toggled.connect(_set_paper)
            ctrl_row.addWidget(pb)
            self._cinema_paper_btns[pkey] = pb

        ctrl_row.addSpacing(8)
        ctrl_row.addWidget(QLabel("尺寸:"))
        self._cinema_size_combo = QComboBox()
        self._cinema_size_combo.setStyleSheet(
            "QComboBox { background:#0f2127; border:1px solid rgba(145,182,181,0.18);"
            " border-radius:4px; color:#eef3ef; padding:3px 6px; }"
            "QComboBox::drop-down { border:none; }"
        )
        for sk, sl in _PAPER_SIZE_KEYS:
            self._cinema_size_combo.addItem(sl + ("mm" if "×" in sl else ""), sk)
        for _ci in range(self._cinema_size_combo.count()):
            if self._cinema_size_combo.itemData(_ci) == self._sample_size_key:
                self._cinema_size_combo.setCurrentIndex(_ci)
                break
        self._cinema_size_combo.currentIndexChanged.connect(self._on_cinema_size_changed)
        ctrl_row.addWidget(self._cinema_size_combo)
        ctrl_row.addStretch()
        left_layout.addLayout(ctrl_row)

        # BIG PREVIEW
        self._cinema_preview_frame = QFrame()
        self._cinema_preview_frame.setStyleSheet(
            "QFrame { background: #1a3540; border: 2px solid rgba(41,185,171,0.30);"
            " border-radius: 8px; }"
        )
        preview_layout = QVBoxLayout(self._cinema_preview_frame)
        preview_layout.setContentsMargins(8, 8, 8, 8)
        self._cinema_preview_img = QLabel()
        self._cinema_preview_img.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._cinema_preview_img.setStyleSheet("background: transparent;")
        self._cinema_preview_img.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        preview_layout.addWidget(self._cinema_preview_img)
        left_layout.addWidget(self._cinema_preview_frame, stretch=1)

        # Navigator
        nav_row = QHBoxLayout()
        nav_row.setSpacing(8)
        self._nav_prev_btn = QPushButton("←")
        self._nav_prev_btn.setFixedSize(32, 32)
        self._nav_prev_btn.setStyleSheet(
            "QPushButton { background:#10242a; border:1px solid rgba(145,182,181,0.25);"
            " border-radius:4px; color:#cfe0db; } QPushButton:hover { border-color:#29b9ab; }"
        )
        self._nav_prev_btn.clicked.connect(self._nav_prev)
        self._nav_lbl = QLabel("—")
        self._nav_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._nav_lbl.setStyleSheet("color: #87a2a1; font-size: 12px; min-width: 80px;")
        self._nav_next_btn = QPushButton("→")
        self._nav_next_btn.setFixedSize(32, 32)
        self._nav_next_btn.setStyleSheet(self._nav_prev_btn.styleSheet())
        self._nav_next_btn.clicked.connect(self._nav_next)
        nav_row.addStretch()
        nav_row.addWidget(self._nav_prev_btn)
        nav_row.addWidget(self._nav_lbl)
        nav_row.addWidget(self._nav_next_btn)
        nav_row.addStretch()
        left_layout.addLayout(nav_row)

        splitter.addWidget(left)

        # Right panel (specimen list)
        right = QWidget()
        right.setStyleSheet("background: #08161b;")
        right.setFixedWidth(210)
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(6, 10, 10, 10)
        right_layout.setSpacing(6)

        right_layout.addWidget(_mk_section_label("标本列表"))

        self._cinema_list = QListWidget()
        self._cinema_list.setStyleSheet(
            "QListWidget { background: #0c2027; border: 1px solid rgba(145,182,181,0.15);"
            " color: #eef3ef; border-radius: 4px; }"
            "QListWidget::item:selected { background: rgba(41,185,171,0.25); }"
            "QListWidget::item:hover { background: rgba(41,185,171,0.10); }"
        )
        self._cinema_list.setSpacing(1)
        self._cinema_list.itemChanged.connect(self._on_list_item_changed)
        right_layout.addWidget(self._cinema_list, stretch=1)

        # Action buttons (compact)
        action_grid = QGridLayout()
        action_grid.setSpacing(3)
        for i, (lbl, slot) in enumerate([
            ("全选", self._select_all), ("RNA", self._select_rna),
            ("样品", self._select_sample), ("清空", self._select_none),
        ]):
            b = _mk_outline_btn(lbl)
            b.setFixedHeight(24)
            b.setStyleSheet(
                "QPushButton#OutlineBtn { background: transparent;"
                " border: 1px solid rgba(145,182,181,0.25); border-radius: 3px;"
                " color: #cfe0db; padding: 2px 6px; font-size: 11px; }"
                "QPushButton#OutlineBtn:hover { border-color: #29b9ab; }"
            )
            b.clicked.connect(slot)
            action_grid.addWidget(b, i // 2, i % 2)
        right_layout.addLayout(action_grid)

        # Stats
        self._cinema_stats = QLabel("🧪 0  🧬 0")
        self._cinema_stats.setStyleSheet("color: #87a2a1; font-size: 11px;")
        self._cinema_stats.setAlignment(Qt.AlignmentFlag.AlignCenter)
        right_layout.addWidget(self._cinema_stats)

        splitter.addWidget(right)
        splitter.setSizes([800, 210])
        root.addWidget(splitter, stretch=1)

    # ── Public interface ─────────────────────────────────────────────

    def load_specimens(self, specimens: list[dict]) -> None:
        self._specimens = specimens
        self._selected = set(range(len(specimens)))
        self._preview_idx = 0
        self._rebuild_cinema_list()
        self._rebuild_cinema_template_chips()
        self._rebuild_jobs()

    def select_uid(self, uid: str) -> bool:
        for i, sp in enumerate(self._specimens):
            data = specimen_to_label_data(sp)
            if data.get("uniqueId") == uid:
                self._selected = {i}
                self._preview_idx = i
                self._rebuild_cinema_list()
                self._rebuild_jobs()
                return True
        return False

    def get_sample_job(self) -> dict | None:
        return self._sample_job

    def get_tissue_job(self) -> dict | None:
        return self._tissue_job

    def on_activate(self) -> None:
        self._update_cinema_preview()

    # ── Internal helpers ─────────────────────────────────────────────

    def _switch_bucket(self, bucket: str) -> None:
        self._current_bucket = bucket
        self._bucket_sample_btn.setChecked(bucket == "sample")
        self._bucket_tissue_btn.setChecked(bucket == "tissue")
        self._rebuild_cinema_template_chips()
        self._sync_cinema_controls()
        self._update_cinema_preview()

    def _sync_cinema_controls(self) -> None:
        """Update paper/size controls to reflect current bucket."""
        current_paper = self._sample_paper if self._current_bucket == "sample" else self._tissue_paper
        for pk, pb in self._cinema_paper_btns.items():
            pb.blockSignals(True)
            pb.setChecked(pk == current_paper)
            pb.blockSignals(False)

        current_size = self._sample_size_key if self._current_bucket == "sample" else self._tissue_size_key
        self._cinema_size_combo.blockSignals(True)
        for ci in range(self._cinema_size_combo.count()):
            if self._cinema_size_combo.itemData(ci) == current_size:
                self._cinema_size_combo.setCurrentIndex(ci)
                break
        self._cinema_size_combo.blockSignals(False)

    def _on_cinema_size_changed(self, idx: int) -> None:
        key = self._cinema_size_combo.itemData(idx)
        if self._current_bucket == "sample":
            self._sample_size_key = key
            self._sample_lib.set_selected_size_key(key)
        else:
            self._tissue_size_key = key
            self._tissue_lib.set_selected_size_key(key)
        self._rebuild_jobs()

    def _select_all(self) -> None:
        self._selected = set(range(len(self._specimens)))
        self._rebuild_cinema_list()
        self._rebuild_jobs()

    def _select_none(self) -> None:
        self._selected.clear()
        self._rebuild_cinema_list()
        self._rebuild_jobs()

    def _select_rna(self) -> None:
        self._selected = {i for i, sp in enumerate(self._specimens) if has_rna_tissue(sp)}
        self._rebuild_cinema_list()
        self._rebuild_jobs()

    def _select_sample(self) -> None:
        self._selected = {i for i, sp in enumerate(self._specimens) if not has_rna_tissue(sp)}
        self._rebuild_cinema_list()
        self._rebuild_jobs()

    def _on_list_item_changed(self, item: QListWidgetItem) -> None:
        idx = item.data(Qt.ItemDataRole.UserRole)
        if item.checkState() == Qt.CheckState.Checked:
            self._selected.add(idx)
        else:
            self._selected.discard(idx)
        self._rebuild_jobs()

    def _rebuild_cinema_list(self) -> None:
        self._cinema_list.blockSignals(True)
        self._cinema_list.clear()
        for i, sp in enumerate(self._specimens):
            data = specimen_to_label_data(sp)
            uid_str = data.get("uniqueId") or sp.get("id") or "?"
            is_rna = has_rna_tissue(sp)
            display = f"{'🧬 ' if is_rna else ''}{uid_str}"
            item = QListWidgetItem(display)
            item.setData(Qt.ItemDataRole.UserRole, i)
            item.setCheckState(Qt.CheckState.Checked if i in self._selected else Qt.CheckState.Unchecked)
            self._cinema_list.addItem(item)
        self._cinema_list.blockSignals(False)
        self._update_cinema_stats()

    def _update_cinema_stats(self) -> None:
        sample_n = sum(1 for i in self._selected if i < len(self._specimens))
        tissue_n = sum(
            1 for i in self._selected
            if i < len(self._specimens) and has_rna_tissue(self._specimens[i])
        )
        self._cinema_stats.setText(f"🧪 {sample_n}  🧬 {tissue_n}")

    def _rebuild_cinema_template_chips(self) -> None:
        while self._cinema_tmpl_row.count():
            item = self._cinema_tmpl_row.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        is_tissue = self._current_bucket == "tissue"
        key_attr = "_tissue_tmpl_key" if is_tissue else "_sample_tmpl_key"
        lib = self._tissue_lib if is_tissue else self._sample_lib
        current_key = getattr(self, key_attr)

        for tkey, tmpl in BUILTIN_TEMPLATES.items():
            is_tissue_tmpl = tmpl.get("flavor") == "tissue"
            if is_tissue and not is_tissue_tmpl:
                continue
            if not is_tissue and is_tissue_tmpl:
                continue
            chip = QPushButton(tmpl.get("name", tkey))
            chip.setObjectName("TmplChip")
            chip.setCheckable(True)
            chip.setChecked(tkey == current_key)
            chip.setFixedHeight(28)

            def _pick(checked: bool, _k: str = tkey, _attr: str = key_attr,
                      _lib: LabelTemplateLibrary = lib) -> None:
                if checked:
                    setattr(self, _attr, _k)
                    _lib.set_selected_key(_k)
                    self._rebuild_jobs()
                    self._update_cinema_preview()

            chip.clicked.connect(_pick)
            self._cinema_tmpl_row.addWidget(chip)
        self._cinema_tmpl_row.addStretch()

    def _nav_prev(self) -> None:
        indices = sorted(self._selected)
        if not indices:
            return
        if self._preview_idx in indices:
            pos = indices.index(self._preview_idx)
            self._preview_idx = indices[(pos - 1) % len(indices)]
        else:
            self._preview_idx = indices[0]
        self._update_cinema_preview()

    def _nav_next(self) -> None:
        indices = sorted(self._selected)
        if not indices:
            return
        if self._preview_idx in indices:
            pos = indices.index(self._preview_idx)
            self._preview_idx = indices[(pos + 1) % len(indices)]
        else:
            self._preview_idx = indices[0]
        self._update_cinema_preview()

    def _get_dims(self, bucket: str) -> dict:
        key = self._sample_size_key if bucket == "sample" else self._tissue_size_key
        if key == "custom":
            return {"w": 50, "h": 30} if bucket == "sample" else {"w": 30, "h": 15}
        size = PAPER_SIZES.get(key)
        if size:
            return {"w": size["w"], "h": size["h"]}
        return {"w": 50, "h": 30}

    def _get_template(self, bucket: str) -> dict:
        key = self._sample_tmpl_key if bucket == "sample" else self._tissue_tmpl_key
        lib = self._sample_lib if bucket == "sample" else self._tissue_lib
        if is_library_key(key):
            rec_id = id_from_key(key)
            rec = lib.get(rec_id)
            if rec and rec.get("template"):
                return normalize_template(rec["template"])
            key = "tissueCompact" if bucket == "tissue" else "standard"
        return normalize_template(BUILTIN_TEMPLATES.get(key, BUILTIN_TEMPLATES.get(
            "tissueCompact" if bucket == "tissue" else "standard"
        )))

    def _rebuild_jobs(self) -> None:
        indices = sorted(self._selected)
        copies = self._copies_spin.value() if hasattr(self, "_copies_spin") else 1

        sample_tmpl = self._get_template("sample")
        tissue_tmpl = self._get_template("tissue")
        sample_dims = self._get_dims("sample")
        tissue_dims = self._get_dims("tissue")

        sample_paper = PAPER_SIZES.get(self._sample_paper) if self._sample_paper in ("a4", "a5") else None
        tissue_paper = PAPER_SIZES.get(self._tissue_paper) if self._tissue_paper in ("a4", "a5") else None

        self._sample_job = LabelService.build_print_job(
            self._specimens, sample_tmpl, "sample",
            selected_indices=indices, dims=sample_dims, copies=copies,
            paper_type=self._sample_paper, paper=sample_paper, edits={},
        )
        self._tissue_job = LabelService.build_print_job(
            self._specimens, tissue_tmpl, "tissue",
            selected_indices=indices, dims=tissue_dims, copies=copies,
            paper_type=self._tissue_paper, paper=tissue_paper, edits={},
        )

        sample_n = len(self._sample_job["items"]) if self._sample_job else 0
        tissue_n = len(self._tissue_job["items"]) if self._tissue_job else 0

        if hasattr(self, "_btn_print_sample"):
            self._btn_print_sample.setText(f"🖨 打印样品 ({sample_n})")
            self._btn_print_sample.setEnabled(sample_n > 0)
        if hasattr(self, "_btn_print_tissue"):
            self._btn_print_tissue.setText(f"🖨 打印RNA ({tissue_n})")
            self._btn_print_tissue.setEnabled(tissue_n > 0)

        has_rna = any(has_rna_tissue(self._specimens[i]) for i in indices if i < len(self._specimens))
        if hasattr(self, "_bucket_tissue_btn"):
            self._bucket_tissue_btn.setEnabled(has_rna)

        self._update_cinema_stats()
        self._update_cinema_preview()

    def _update_cinema_preview(self) -> None:
        indices = sorted(self._selected)
        if not indices:
            self._cinema_preview_img.clear()
            self._cinema_preview_img.setText("← 请先在左侧选择标本")
            self._cinema_preview_img.setStyleSheet("color: #5f7d7a; font-size: 11px; background: transparent;")
            self._nav_lbl.setText("—")
            return

        # Clamp preview_idx to valid selected index
        if self._preview_idx not in self._selected:
            self._preview_idx = indices[0]

        pos = indices.index(self._preview_idx)
        sp_idx = self._preview_idx
        data = specimen_to_label_data(self._specimens[sp_idx]) if sp_idx < len(self._specimens) else {}

        uid_str = (data.get("uniqueId") or "?")[:16]
        self._nav_lbl.setText(f"{uid_str}\n{pos+1}/{len(indices)}")

        bucket = self._current_bucket
        tmpl = self._get_template(bucket)
        dims = self._get_dims(bucket)

        # Scale to fit the preview frame
        frame_w = max(1, self._cinema_preview_frame.width() - 20)
        frame_h = max(1, self._cinema_preview_frame.height() - 20)
        w_mm = float(dims.get("w", 60))
        h_mm = float(dims.get("h", 40))
        scale_x = frame_w / max(1, w_mm)
        scale_y = frame_h / max(1, h_mm)
        scale = min(scale_x, scale_y, 12.0)  # cap at 12×

        pm = _render_pixmap(tmpl, dims, data, scale=scale)
        self._cinema_preview_img.setPixmap(pm)
