"""label_step3_paper.py — Step 3「纸张 / 尺寸 / 份数」section.

Mirrors web ``renderLabelStep3`` + ``renderPaperColumn`` (app.js:17107-17196):
one column per active bucket with a paper-type radio (小标签纸 / A4纸 / A5纸), a
label-size button row (25×10 … 100×70 / 自定义), an optional custom W×H input, and
an A4/A5 grid-layout preview; below the columns a shared 每种份数 input.

Size selection persists per bucket via ``LabelTemplateLibrary.selected_size_key``;
paper type and custom dims are held in-memory (web keeps them in transient state).

Signals
-------
config_changed()  — paper / size / custom-dims / copies changed → rebuild jobs.
"""
from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import QSettings, pyqtSignal
from PyQt6.QtWidgets import (
    QButtonGroup,
    QDoubleSpinBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QRadioButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from app.services.label_service import (
    DEFAULT_SIZE_KEY,
    LABEL_COPIES_QSETTINGS_KEY,
    LABEL_PAPER_QSETTINGS_KEY,
    LABEL_SIZE_KEYS,
    PAPER_SIZES,
    LabelTemplateLibrary,
    resolve_dims,
)
from app.services.label_service import LabelService
from app.utils.label_core import calculate_grid

# Persist paper-type + copies in the same QSettings store the template library
# uses, so the workbench one-click direct print can reuse the user's last choice
# without opening the label studio. Canonical keys live in label_service.
_QSETTINGS_ORG = "PhotoPlatform"
_QSETTINGS_APP = "LabelTemplates"
_VALID_PAPER = ("label", "a4", "a5")


_BUCKET_META = {
    "sample": {"icon": "🧪", "title": "样品瓶"},
    "tissue": {"icon": "🧬", "title": "RNAlater 组织管"},
}

# ── Theme colours — resolved from the LIVE active theme (CHROME ONLY) ─────────
_C_BG = "#08161b"
_C_INPUT_BG = "#0c2027"
_C_TEXT = "#eef3ef"
_C_TEXT_SOFT = "#cfe0db"
_C_MUTED = "#87a2a1"
_C_ACCENT = "#29b9ab"
_C_SEL_BG = "rgba(41,185,171,0.20)"
_C_BORDER = "rgba(145,182,181,0.18)"
_C_BORDER_DIM = "rgba(145,182,181,0.12)"


def _refresh_palette() -> None:
    """Rebind the module `_C_*` chrome colours to the current theme tokens."""
    global _C_BG, _C_INPUT_BG, _C_TEXT, _C_TEXT_SOFT, _C_MUTED
    global _C_ACCENT, _C_SEL_BG, _C_BORDER, _C_BORDER_DIM
    from app.config.theme import TOKENS
    g = TOKENS.get
    _C_BG = g("bg", _C_BG)
    _C_INPUT_BG = g("input_bg", _C_INPUT_BG)
    _C_TEXT = g("text", _C_TEXT)
    _C_TEXT_SOFT = g("text", _C_TEXT_SOFT)
    _C_MUTED = g("muted", _C_MUTED)
    _C_ACCENT = g("accent", _C_ACCENT)
    _C_SEL_BG = g("panel_2", _C_SEL_BG)
    _C_BORDER = g("border", _C_BORDER)
    _C_BORDER_DIM = g("border", _C_BORDER_DIM)


def _css() -> str:
    return f"""
QPushButton#SizeBtn {{
    background: {_C_INPUT_BG}; border: 1px solid {_C_BORDER};
    color: {_C_TEXT_SOFT}; padding: 3px 8px; font-size: 11px; border-radius: 4px;
}}
QPushButton#SizeBtn:checked {{
    background: {_C_SEL_BG}; border: 1.5px solid {_C_ACCENT}; color: {_C_ACCENT};
}}
QFrame#PaperCol {{
    background: {_C_INPUT_BG}; border: 1px solid {_C_BORDER_DIM}; border-radius: 6px;
}}
"""


class LabelStep3Paper(QWidget):
    """Step 3 — per-bucket paper / size config + shared copies."""

    config_changed = pyqtSignal()

    def __init__(
        self,
        libs: dict[str, LabelTemplateLibrary],
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        _refresh_palette()
        self.setStyleSheet(f"background:{_C_BG}; color:{_C_TEXT};" + _css())
        self._libs = libs
        self._specimens: list[dict] = []
        self._selected_indices: list[int] = []
        self._qs = QSettings(_QSETTINGS_ORG, _QSETTINGS_APP)
        self._paper = {b: self._load_paper(b) for b in ("sample", "tissue")}
        # seed from any persisted custom dims (a size edited in the designer)
        self._custom_dims = {
            "sample": dict(libs["sample"].selected_custom_dims()),
            "tissue": dict(libs["tissue"].selected_custom_dims()),
        }
        self._size_btns: dict[str, dict] = {}
        self._paper_btns: dict[str, dict] = {}
        self._setup_ui()

    # ── UI ─────────────────────────────────────────────────────────────────

    def _setup_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(10)

        title = QLabel("纸张 / 尺寸 / 份数")
        title.setStyleSheet(f"color:{_C_ACCENT}; font-size:15px; font-weight:bold;")
        root.addWidget(title)

        self._cols_row = QVBoxLayout()
        self._cols_row.setSpacing(14)
        root.addLayout(self._cols_row)

        # Shared copies
        crow = QHBoxLayout()
        crow.addWidget(QLabel("每种份数:"))
        self._copies = QSpinBox()
        self._copies.setRange(1, 10)
        self._copies.setValue(self._load_copies())   # seed before connecting
        self._copies.valueChanged.connect(self._on_copies)
        crow.addWidget(self._copies)
        crow.addStretch()
        root.addLayout(crow)

    # ── Public API ───────────────────────────────────────────────────────────

    def set_data(self, specimens: list[dict], selected_indices: list[int]) -> None:
        self._specimens = list(specimens or [])
        self._selected_indices = list(selected_indices or [])
        self._rebuild()

    def copies(self) -> int:
        return self._copies.value()

    def paper_type(self, bucket: str) -> str:
        return self._paper.get(bucket, "label")

    def dims(self, bucket: str) -> dict:
        return resolve_dims(self._libs[bucket], self._custom_dims[bucket])

    # ── Build ──────────────────────────────────────────────────────────────────

    def _rebuild(self) -> None:
        while self._cols_row.count():
            it = self._cols_row.takeAt(0)
            w = it.widget()
            if w is not None:
                w.setParent(None)
        self._size_btns = {}
        self._paper_btns = {}

        buckets = LabelService.bucket(self._specimens, self._selected_indices)
        self._cols_row.addWidget(self._build_column("sample", len(buckets["samples"])))
        if buckets["tissues"]:
            self._cols_row.addWidget(self._build_column("tissue", len(buckets["tissues"])))
        self._cols_row.addStretch()

    def _build_column(self, bucket: str, count: int) -> QFrame:
        meta = _BUCKET_META[bucket]
        lib = self._libs[bucket]
        col = QFrame()
        col.setObjectName("PaperCol")
        cl = QVBoxLayout(col)
        cl.setContentsMargins(12, 10, 12, 10)
        cl.setSpacing(8)

        head = QHBoxLayout()
        head.addWidget(QLabel(meta["icon"]))
        strong = QLabel(meta["title"])
        strong.setStyleSheet(f"color:{_C_TEXT}; font-size:13px; font-weight:bold;")
        head.addWidget(strong)
        head.addWidget(QLabel(f"{count} 个"))
        head.addStretch()
        cl.addLayout(head)

        # Paper type radios
        cur_paper = self._paper.get(bucket, "label")
        prow = QHBoxLayout()
        prow.setSpacing(8)
        pgroup = QButtonGroup(col)
        pgroup.setExclusive(True)
        self._paper_btns[bucket] = {}
        for key, name in (("label", "小标签纸"), ("a4", "A4纸"), ("a5", "A5纸")):
            rb = QRadioButton(name)
            rb.setChecked(cur_paper == key)
            rb.toggled.connect(lambda on, bk=bucket, k=key: on and self._on_paper(bk, k))
            pgroup.addButton(rb)
            self._paper_btns[bucket][key] = rb
            prow.addWidget(rb)
        prow.addStretch()
        cl.addLayout(prow)

        # Size buttons
        cur_size = lib.selected_size_key() or DEFAULT_SIZE_KEY[bucket]
        srow_host = QWidget()
        srow = QGridLayout(srow_host)
        srow.setContentsMargins(0, 0, 0, 0)
        srow.setHorizontalSpacing(4)
        srow.setVerticalSpacing(4)
        sgroup = QButtonGroup(col)
        sgroup.setExclusive(True)
        self._size_btns[bucket] = {}
        for i, key in enumerate(LABEL_SIZE_KEYS + ["custom"]):
            label = PAPER_SIZES[key]["name"] if key in PAPER_SIZES else "自定义"
            btn = QPushButton(label)
            btn.setObjectName("SizeBtn")
            btn.setCheckable(True)
            btn.setChecked(cur_size == key)
            btn.clicked.connect(lambda _=False, bk=bucket, k=key: self._on_size(bk, k))
            sgroup.addButton(btn)
            self._size_btns[bucket][key] = btn
            srow.addWidget(btn, i // 5, i % 5)
        cl.addWidget(srow_host)

        # Custom W×H (only when size==custom)
        if cur_size == "custom":
            d = self._custom_dims[bucket]
            crow = QHBoxLayout()
            crow.addWidget(QLabel("宽 mm"))
            w_spin = QDoubleSpinBox()
            w_spin.setRange(5, 300)
            w_spin.setValue(float(d.get("w", 50)))
            w_spin.valueChanged.connect(lambda v, bk=bucket: self._on_custom(bk, "w", v))
            crow.addWidget(w_spin)
            crow.addWidget(QLabel("高 mm"))
            h_spin = QDoubleSpinBox()
            h_spin.setRange(3, 300)
            h_spin.setValue(float(d.get("h", 30)))
            h_spin.valueChanged.connect(lambda v, bk=bucket: self._on_custom(bk, "h", v))
            crow.addWidget(h_spin)
            crow.addStretch()
            cl.addLayout(crow)

        # A4/A5 grid layout preview
        if cur_paper in ("a4", "a5"):
            dims = self.dims(bucket)
            paper = PAPER_SIZES[cur_paper]
            grid = calculate_grid(dims["w"], dims["h"], float(paper["w"]), float(paper["h"]))
            info = QLabel(
                f"{paper['name']} 排版: {grid['cols']}列 × {grid['rows']}行 "
                f"= {grid['perPage']} 张/页"
            )
            info.setStyleSheet(f"color:{_C_MUTED}; font-size:11px;")
            cl.addWidget(info)

        return col

    # ── Handlers ────────────────────────────────────────────────────────────────

    def _on_paper(self, bucket: str, paper: str) -> None:
        self._paper[bucket] = paper
        self._qs.setValue(LABEL_PAPER_QSETTINGS_KEY[bucket], paper)
        self._rebuild()  # toggles grid preview / custom row visibility
        self.config_changed.emit()

    def _on_copies(self, _value: int = 0) -> None:
        self._qs.setValue(LABEL_COPIES_QSETTINGS_KEY, self._copies.value())
        self.config_changed.emit()

    # ── Persistence helpers ──────────────────────────────────────────────────

    def _load_paper(self, bucket: str) -> str:
        val = str(self._qs.value(LABEL_PAPER_QSETTINGS_KEY[bucket], "label") or "label")
        return val if val in _VALID_PAPER else "label"

    def _load_copies(self) -> int:
        try:
            return max(1, min(10, int(self._qs.value(LABEL_COPIES_QSETTINGS_KEY, 1) or 1)))
        except (TypeError, ValueError):
            return 1

    def _on_size(self, bucket: str, size_key: str) -> None:
        self._libs[bucket].set_selected_size_key(size_key)
        self._rebuild()
        self.config_changed.emit()

    def _on_custom(self, bucket: str, axis: str, value: float) -> None:
        self._custom_dims[bucket][axis] = value
        self.config_changed.emit()
