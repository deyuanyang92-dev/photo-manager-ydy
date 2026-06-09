# ============================================================================
# 已退役 (2026-06-07): 孤儿"第二设计"右细节面板。App 从未实例化。其尺寸快捷
# 按钮优点已移植进 app/views/labels_view.py。保留可回退；确认后可删。
# ============================================================================
"""label_detail_panel.py — right detail pane of the Label Print Studio.

For the specimen + bucket currently selected in the master list, this pane lets
the user pick a template, size, paper and copies, and shows a big live WYSIWYG
preview.  Editing opens a modal (``LabelEditorDialog``).

Template / size resolution and persistence reuse the existing, unchanged
``LabelTemplateLibrary`` (QSettings-backed, one library per bucket) and
``BUILTIN_TEMPLATES`` — so behavior matches the old Step-2 column; only the
presentation is new.  Sample vs RNAlater bucketing is decided elsewhere
(``has_rna_tissue`` in label_core); this pane only previews/configures.

Signals
-------
config_changed()      — template / size / paper / copies changed (rebuild jobs).
bucket_changed(str)   — preview bucket toggled ("sample" | "tissue").
"""
from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QRadioButton,
    QSizePolicy,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from app.services.label_service import (
    BUILTIN_TEMPLATES,
    PAPER_SIZES,
    LabelTemplateLibrary,
    id_from_key,
    is_library_key,
    key_from_id,
)
from app.utils.label_core import normalize_template


_DEFAULT_TEMPLATE = {"sample": "standard", "tissue": "tissueCompact"}
_DEFAULT_SIZE = {"sample": "label_50x30", "tissue": "label_30x15"}
_SIZE_KEYS = [
    "label_25x10", "label_30x15", "label_40x20", "label_50x30",
    "label_60x40", "label_70x50", "label_80x60", "label_100x70",
]

_CSS_TOGGLE = """
QPushButton#BucketToggle {
    background: #0c2027; border: 1px solid rgba(145,182,181,0.18);
    color: #87a2a1; padding: 5px 14px; font-size: 12px; border-radius: 5px;
}
QPushButton#BucketToggle:checked {
    background: rgba(41,185,171,0.18); border-color: #29b9ab;
    color: #29b9ab; font-weight: bold;
}
QPushButton#BucketToggle[bucket="tissue"]:checked {
    background: rgba(74,144,217,0.18); border-color: #4a90d9; color: #9cc6f0;
}
QPushButton#BucketToggle:disabled { color: #3f5a58; border-color: rgba(145,182,181,0.08); }
"""

_CSS_CHIP = """
QPushButton#SizeChip {
    background: #0c2027; border: 1px solid rgba(145,182,181,0.18);
    color: #cfe0db; padding: 3px 7px; font-size: 11px; border-radius: 4px;
}
QPushButton#SizeChip:checked {
    background: rgba(41,185,171,0.20); border: 1.5px solid #29b9ab; color: #29b9ab;
}
"""


class LabelDetailPanel(QWidget):
    config_changed = pyqtSignal()
    bucket_changed = pyqtSignal(str)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setStyleSheet(
            "background:#08161b; color:#eef3ef;" + _CSS_TOGGLE + _CSS_CHIP
        )
        self._libs = {
            "sample": LabelTemplateLibrary("sample"),
            "tissue": LabelTemplateLibrary("tissue"),
        }
        self._bucket = "sample"
        self._has_rna = False
        self._label_data: dict = {}
        self._custom_dims = {
            "sample": dict(self._libs["sample"].selected_custom_dims()),
            "tissue": dict(self._libs["tissue"].selected_custom_dims()),
        }
        self._paper = {"sample": "label", "tissue": "label"}
        self._size_chips: dict[str, QPushButton] = {}
        self._tmpl_combo_guard = False
        self._setup_ui()
        self._reload_for_bucket()

    # ── UI ─────────────────────────────────────────────────────────────────

    def _setup_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 12)
        root.setSpacing(12)

        # Bucket toggle
        brow = QHBoxLayout()
        brow.setSpacing(8)
        self._bgroup = QButtonGroup(self)
        self._bgroup.setExclusive(True)
        self._btn_sample = QPushButton("🧪 样品瓶标签")
        self._btn_tissue = QPushButton("🧬 RNAlater 组织管")
        for b, bk in ((self._btn_sample, "sample"), (self._btn_tissue, "tissue")):
            b.setObjectName("BucketToggle")
            b.setProperty("bucket", bk)
            b.setCheckable(True)
            b.clicked.connect(lambda _=False, k=bk: self._switch_bucket(k))
            self._bgroup.addButton(b)
            brow.addWidget(b)
        self._btn_sample.setChecked(True)
        brow.addStretch()
        root.addLayout(brow)

        # Template row: combo + 编辑 / 另存为
        trow = QHBoxLayout()
        trow.setSpacing(8)
        trow.addWidget(QLabel("模板:"))
        self._tmpl_combo = QComboBox()
        self._tmpl_combo.setMinimumWidth(200)
        self._tmpl_combo.setStyleSheet(
            "QComboBox { background:#0f2127; border:1px solid rgba(145,182,181,0.18);"
            " border-radius:4px; color:#eef3ef; padding:3px 8px; }"
        )
        self._tmpl_combo.currentIndexChanged.connect(self._on_template_combo)
        trow.addWidget(self._tmpl_combo, stretch=1)
        self._edit_btn = QPushButton("✎ 编辑")
        self._edit_btn.setObjectName("BucketToggle")
        self._edit_btn.clicked.connect(self._open_editor)
        trow.addWidget(self._edit_btn)
        root.addLayout(trow)

        # Size chips
        srow = QHBoxLayout()
        srow.setSpacing(4)
        srow.addWidget(QLabel("尺寸:"))
        self._size_group = QButtonGroup(self)
        self._size_group.setExclusive(True)
        for key in _SIZE_KEYS + ["custom"]:
            label = PAPER_SIZES[key]["name"] if key in PAPER_SIZES else "自定义"
            chip = QPushButton(label)
            chip.setObjectName("SizeChip")
            chip.setCheckable(True)
            chip.clicked.connect(lambda _=False, k=key: self._on_size(k))
            self._size_group.addButton(chip)
            self._size_chips[key] = chip
            srow.addWidget(chip)
        srow.addStretch()
        root.addLayout(srow)

        # Custom W×H (hidden unless custom)
        self._custom_row = QWidget()
        crow = QHBoxLayout(self._custom_row)
        crow.setContentsMargins(0, 0, 0, 0)
        crow.setSpacing(6)
        crow.addWidget(QLabel("宽 mm"))
        self._w_spin = QDoubleSpinBox()
        self._w_spin.setRange(5, 300)
        self._w_spin.valueChanged.connect(self._on_custom_dims)
        crow.addWidget(self._w_spin)
        crow.addWidget(QLabel("高 mm"))
        self._h_spin = QDoubleSpinBox()
        self._h_spin.setRange(3, 300)
        self._h_spin.valueChanged.connect(self._on_custom_dims)
        crow.addWidget(self._h_spin)
        crow.addStretch()
        root.addWidget(self._custom_row)
        self._custom_row.hide()

        # Preview
        self._preview = QLabel("实时预览")
        self._preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._preview.setMinimumHeight(280)
        self._preview.setStyleSheet(
            "background:#0c1e26; border:1px solid rgba(145,182,181,0.16);"
            " border-radius:6px; color:#5f7d7a;"
        )
        self._preview.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        root.addWidget(self._preview, stretch=1)

        # Paper + copies
        prow = QHBoxLayout()
        prow.setSpacing(10)
        prow.addWidget(QLabel("纸张:"))
        self._paper_group = QButtonGroup(self)
        self._paper_btns: dict[str, QRadioButton] = {}
        for key, name in (("label", "小标签纸"), ("a4", "A4纸"), ("a5", "A5纸")):
            rb = QRadioButton(name)
            rb.toggled.connect(lambda on, k=key: on and self._on_paper(k))
            self._paper_group.addButton(rb)
            self._paper_btns[key] = rb
            prow.addWidget(rb)
        self._paper_btns["label"].setChecked(True)
        prow.addSpacing(20)
        prow.addWidget(QLabel("份数:"))
        self._copies = QSpinBox()
        self._copies.setRange(1, 99)
        self._copies.setValue(1)
        self._copies.valueChanged.connect(lambda _=0: self.config_changed.emit())
        prow.addWidget(self._copies)
        prow.addStretch()
        root.addLayout(prow)

    # ── Public API ───────────────────────────────────────────────────────────

    def set_context(self, label_data: Optional[dict], has_rna: bool, bucket: str) -> None:
        """Set the specimen being previewed + which bucket, enabling RNAlater
        only when the specimen actually went to RNA."""
        self._label_data = label_data or {}
        self._has_rna = bool(has_rna)
        self._btn_tissue.setEnabled(self._has_rna)
        target = bucket if (bucket != "tissue" or self._has_rna) else "sample"
        if target != self._bucket:
            self._bucket = target
            (self._btn_sample if target == "sample" else self._btn_tissue).setChecked(True)
            self._reload_for_bucket()
        self._refresh_preview()

    def current_bucket(self) -> str:
        return self._bucket

    def selected_template(self, bucket: str) -> dict:
        return self._resolve_template(bucket)

    def selected_dims(self, bucket: str) -> dict:
        return self._resolve_dims(bucket)

    def paper_type(self, bucket: str) -> str:
        return self._paper.get(bucket, "label")

    @property
    def copies(self) -> int:
        return self._copies.value()

    # ── Template / size resolution (mirrors old _BucketColWidget) ─────────────

    def _resolve_template(self, bucket: str) -> dict:
        lib = self._libs[bucket]
        default_key = _DEFAULT_TEMPLATE[bucket]
        key = lib.selected_key() or default_key
        if is_library_key(key):
            rec = lib.get(id_from_key(key))
            if rec and rec.get("template"):
                return normalize_template(rec["template"])
            key = default_key
        return normalize_template(BUILTIN_TEMPLATES.get(key) or BUILTIN_TEMPLATES[default_key])

    def _resolve_dims(self, bucket: str) -> dict:
        lib = self._libs[bucket]
        size_key = lib.selected_size_key() or _DEFAULT_SIZE[bucket]
        if size_key == "custom":
            d = self._custom_dims[bucket]
            return {"w": float(d.get("w", 50)), "h": float(d.get("h", 30))}
        ps = PAPER_SIZES.get(size_key)
        if ps:
            return {"w": float(ps["w"]), "h": float(ps["h"])}
        return {"w": 50.0, "h": 30.0}

    # ── Bucket / template / size handlers ─────────────────────────────────────

    def _switch_bucket(self, bucket: str) -> None:
        if bucket == "tissue" and not self._has_rna:
            self._btn_sample.setChecked(True)
            return
        self._bucket = bucket
        self._reload_for_bucket()
        self._refresh_preview()
        self.bucket_changed.emit(bucket)

    def _reload_for_bucket(self) -> None:
        """Rebuild template combo + size chips + paper for the active bucket."""
        bucket = self._bucket
        lib = self._libs[bucket]

        # Template combo: builtins for this bucket + custom library records.
        self._tmpl_combo_guard = True
        self._tmpl_combo.clear()
        is_tissue = bucket == "tissue"
        for key, tmpl in BUILTIN_TEMPLATES.items():
            if (tmpl.get("flavor") == "tissue") == is_tissue:
                self._tmpl_combo.addItem(f"内置 · {tmpl.get('name', key)}", key)
        for rec in lib.records():
            self._tmpl_combo.addItem(f"自定义 · {rec.get('name', '?')}", key_from_id(rec["id"]))
        cur_key = lib.selected_key() or _DEFAULT_TEMPLATE[bucket]
        idx = self._tmpl_combo.findData(cur_key)
        self._tmpl_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self._tmpl_combo_guard = False

        # Size chips
        size_key = lib.selected_size_key() or _DEFAULT_SIZE[bucket]
        chip = self._size_chips.get(size_key)
        if chip is not None:
            chip.setChecked(True)
        self._custom_row.setVisible(size_key == "custom")
        d = self._custom_dims[bucket]
        self._w_spin.blockSignals(True)
        self._h_spin.blockSignals(True)
        self._w_spin.setValue(float(d.get("w", 50)))
        self._h_spin.setValue(float(d.get("h", 30)))
        self._w_spin.blockSignals(False)
        self._h_spin.blockSignals(False)

        # Paper
        self._paper_btns[self._paper.get(bucket, "label")].setChecked(True)

    def _on_template_combo(self, _idx: int) -> None:
        if self._tmpl_combo_guard:
            return
        key = self._tmpl_combo.currentData()
        if key:
            self._libs[self._bucket].set_selected_key(key)
            self._refresh_preview()
            self.config_changed.emit()

    def _on_size(self, size_key: str) -> None:
        self._libs[self._bucket].set_selected_size_key(size_key)
        self._custom_row.setVisible(size_key == "custom")
        self._refresh_preview()
        self.config_changed.emit()

    def _on_custom_dims(self, _v: float) -> None:
        self._custom_dims[self._bucket] = {"w": self._w_spin.value(), "h": self._h_spin.value()}
        self._refresh_preview()
        self.config_changed.emit()

    def _on_paper(self, paper: str) -> None:
        self._paper[self._bucket] = paper
        self.config_changed.emit()

    # ── Editor (modal) ────────────────────────────────────────────────────────

    def _open_editor(self) -> None:
        from app.widgets.label_designer_dialog import LabelDesignerDialog

        bucket = self._bucket
        tmpl = self._resolve_template(bucket)
        dims = self._resolve_dims(bucket)
        lib = self._libs[bucket]
        bucket_name = "样品瓶" if bucket == "sample" else "RNAlater 组织管"
        dlg = LabelDesignerDialog(
            tmpl, dims, self._label_data, library=lib,
            title=f"标签设计器 — {bucket_name}·{tmpl.get('name', '')}", parent=self,
        )
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        if dlg.selected_key() is None:
            # User edited but did not 另存为 → persist as / into a custom record.
            new_tmpl = dlg.edited_template()
            cur = lib.selected_key()
            if is_library_key(cur):
                rid = id_from_key(cur)
                name = (lib.get(rid) or {}).get("name") or "自定义"
                rec = lib.upsert({"id": rid, "name": name, "template": new_tmpl})
            else:
                base = tmpl.get("name") or "自定义"
                rec = lib.upsert({"name": f"{base} (编辑)", "template": new_tmpl})
            lib.set_selected_key(key_from_id(rec["id"]))
            # persist a dimension edited inside the designer as the custom size
            new_dims = dlg.edited_dims()
            if (round(float(new_dims.get("w", 0)), 2) != round(float(dims.get("w", 0)), 2)
                    or round(float(new_dims.get("h", 0)), 2) != round(float(dims.get("h", 0)), 2)):
                lib.set_custom_dims(float(new_dims["w"]), float(new_dims["h"]))
                lib.set_selected_size_key("custom")
                self._custom_dims[bucket] = {"w": float(new_dims["w"]), "h": float(new_dims["h"])}
        self._reload_for_bucket()
        self._refresh_preview()
        self.config_changed.emit()

    # ── Preview ────────────────────────────────────────────────────────────────

    def _refresh_preview(self) -> None:
        from app.views.labels_view import _render_label_preview

        tmpl = self._resolve_template(self._bucket)
        dims = self._resolve_dims(self._bucket)
        box_w = max(120, self._preview.width() - 24)
        box_h = max(120, self._preview.height() - 24)
        pm = _render_label_preview(tmpl, dims, self._label_data, box_w, box_h)
        if pm is not None and not pm.isNull():
            self._preview.setPixmap(pm)
        else:
            self._preview.setText("—")

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._refresh_preview()
