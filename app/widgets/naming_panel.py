"""naming_panel.py — 7-segment specimen UID / result-ID generator.

Provides a form with the six/seven naming segments, a live preview of
the generated UID and result-ID, and an optional R-prefix dual-label
warning when the storage code starts with "R".

Uses ``app.utils.naming.build_uid`` and ``build_result_id`` for
real-time assembly.

Signals
-------
uid_generated(uid: str)
    Emitted whenever the preview UID changes (user edits any field).
result_id_generated(result_id: str)
    Emitted whenever the preview result-ID changes.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from app.utils.naming import build_result_id, build_uid, specimen_date_seg

if TYPE_CHECKING:
    from app.app_context import AppContext


class NamingPanel(QWidget):
    """7-segment naming generator with live UID/result-ID preview.

    Signals
    -------
    uid_generated(str)
    result_id_generated(str)
    """

    uid_generated = pyqtSignal(str)
    result_id_generated = pyqtSignal(str)
    save_requested = pyqtSignal()

    def __init__(self, ctx: "AppContext", parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self._setup_ui()

    # ── UI ────────────────────────────────────────────────────────────────────

    def _setup_ui(self) -> None:
        card = QFrame(self)
        card.setObjectName("PanelCard")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(card)

        root = QVBoxLayout(card)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(10)

        # Card header: title + save button (web: 「照片编号」+「💾 保存」)
        hdr_row = QHBoxLayout()
        hdr_row.setContentsMargins(0, 0, 0, 0)
        hdr = QLabel("照片编号")
        hdr.setObjectName("CardTitle")
        hdr_row.addWidget(hdr)
        hdr_row.addStretch()
        save_btn = QPushButton("💾 保存")
        save_btn.setObjectName("Outline")
        save_btn.setFixedHeight(26)
        save_btn.setToolTip("把当前输入存到本地，刷新不丢")
        save_btn.clicked.connect(self.save_requested.emit)
        hdr_row.addWidget(save_btn)
        root.addLayout(hdr_row)

        # Form grid: 2-column compact label-over-field rows (web naming-fields)
        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(9)

        def _field(row: int, col: int, label: str, placeholder: str,
                   *, auto: bool = False) -> QLineEdit:
            wrap = QVBoxLayout()
            wrap.setSpacing(4)
            lbl = QLabel(label)
            lbl.setObjectName("MutedSmall")
            edit = QLineEdit()
            edit.setPlaceholderText(placeholder)
            edit.setFixedHeight(30)
            if auto:
                edit.setObjectName("AutoField")
            wrap.addWidget(lbl)
            wrap.addWidget(edit)
            grid.addLayout(wrap, row, col)
            return edit

        # Auto-derived fields shown dashed (web: .auto class)
        self._province = _field(0, 0, "地区", "如 FJ", auto=True)
        self._site = _field(0, 1, "样地", "如 YGLZ", auto=True)
        self._station = _field(1, 0, "站位", "如 B2", auto=True)
        self._species_id = _field(1, 1, "物种拼音缩写编号", "如 DLC001")
        self._storage = _field(2, 0, "保存方式", "如 T95E / RD75E")
        self._collection_date = _field(2, 1, "采集日期", "YYYYMMDD")
        self._photo_date = _field(3, 0, "拍摄日期", "YYYYMMDD（选填）")

        # Sequence number (right of photo date)
        seq_wrap = QVBoxLayout()
        seq_wrap.setSpacing(4)
        seq_lbl = QLabel("成果序号")
        seq_lbl.setObjectName("MutedSmall")
        self._seq = QSpinBox()
        self._seq.setMinimum(1)
        self._seq.setMaximum(999)
        self._seq.setValue(1)
        self._seq.setFixedHeight(30)
        seq_wrap.addWidget(seq_lbl)
        seq_wrap.addWidget(self._seq)
        grid.addLayout(seq_wrap, 3, 1)
        root.addLayout(grid)

        # ── Live preview blocks ──
        preview_lbl = QLabel("标本唯一编号")
        preview_lbl.setObjectName("MutedSmall")
        root.addWidget(preview_lbl)
        self._uid_preview = QLabel("—")
        self._uid_preview.setObjectName("PreviewEmpty")
        self._uid_preview.setWordWrap(True)
        self._uid_preview.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        root.addWidget(self._uid_preview)

        result_lbl = QLabel("成果编号（含序号）")
        result_lbl.setObjectName("MutedSmall")
        root.addWidget(result_lbl)
        self._result_preview = QLabel("—")
        self._result_preview.setObjectName("PreviewEmpty")
        self._result_preview.setWordWrap(True)
        self._result_preview.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        root.addWidget(self._result_preview)

        # R-prefix dual-label warning (hidden by default)
        self._rna_warning = QLabel(
            "⚠️  R 前缀（已取 RNA）— 需额外生成 RNAlater 组织管标签"
        )
        self._rna_warning.setObjectName("RnaWarning")
        self._rna_warning.setWordWrap(True)
        self._rna_warning.hide()
        root.addWidget(self._rna_warning)

        # Copy buttons
        btn_row = QHBoxLayout()
        copy_uid_btn = QPushButton("复制 UID")
        copy_uid_btn.setFixedHeight(28)
        copy_uid_btn.clicked.connect(self._copy_uid)
        btn_row.addWidget(copy_uid_btn)

        copy_rid_btn = QPushButton("复制成果编号")
        copy_rid_btn.setFixedHeight(28)
        copy_rid_btn.clicked.connect(self._copy_result_id)
        btn_row.addWidget(copy_rid_btn)
        root.addLayout(btn_row)

        root.addStretch()

        # Wire all edits to live-preview
        for widget in (
            self._province, self._site, self._station,
            self._species_id, self._storage,
            self._collection_date, self._photo_date,
        ):
            widget.textChanged.connect(self._update_preview)
        self._seq.valueChanged.connect(self._update_preview)

    # ── Public API ────────────────────────────────────────────────────────────

    def load_specimen(self, sp: dict) -> None:
        """Populate fields from a specimen dict (uses sp field names from Specimen model).

        Chinese fields (taxonGroupCn, orderCn …) are NOT auto-filled — user
        fills those manually per the project constraint.
        """
        self._province.setText(sp.get("province") or "")
        self._site.setText(sp.get("site") or "")
        self._station.setText(sp.get("station") or "")
        self._species_id.setText(sp.get("id") or "")
        self._storage.setText(sp.get("storage") or "")
        self._collection_date.setText(sp.get("collectionDate") or sp.get("collection_date") or "")
        self._photo_date.setText(sp.get("photoDate") or sp.get("photo_date") or "")
        # seq hint
        hint = sp.get("nextResultSequenceHint") or sp.get("next_result_sequence_hint") or 1
        try:
            self._seq.setValue(int(hint))
        except (ValueError, TypeError):
            self._seq.setValue(1)
        self._update_preview()

    def current_uid(self) -> str:
        return self._uid_preview.text() if self._uid_preview.text() != "—" else ""

    def current_result_id(self) -> str:
        return self._result_preview.text() if self._result_preview.text() != "—" else ""

    # ── Internal ──────────────────────────────────────────────────────────────

    def _update_preview(self) -> None:
        province = self._province.text().strip()
        site = self._site.text().strip()
        station = self._station.text().strip()
        species_id = self._species_id.text().strip()
        storage = self._storage.text().strip()
        col_date = self._collection_date.text().strip()
        photo_date = self._photo_date.text().strip()
        seq = self._seq.value()

        date_seg = specimen_date_seg(col_date or None, photo_date or None)

        uid = build_uid(
            province=province or None,
            site=site or None,
            station=station or None,
            species_id=species_id or None,
            storage=storage or None,
            date_seg=date_seg or None,
        )
        result_id = build_result_id(
            province=province or None,
            site=site or None,
            station=station or None,
            species_id=species_id or None,
            storage=storage or None,
            date_seg=date_seg or None,
            seq=seq,
        )

        self._set_preview(self._uid_preview, uid)
        self._set_preview(self._result_preview, result_id)

        # R-prefix dual-label warning
        if storage.upper().startswith("R"):
            self._rna_warning.show()
        else:
            self._rna_warning.hide()

        if uid:
            self.uid_generated.emit(uid)
        if result_id:
            self.result_id_generated.emit(result_id)

    def _set_preview(self, label: QLabel, value: str) -> None:
        """Set preview text and swap filled/empty styling via object name."""
        filled = bool(value)
        label.setText(value if filled else "—")
        label.setObjectName("PreviewBlock" if filled else "PreviewEmpty")
        label.style().unpolish(label)
        label.style().polish(label)

    def _copy_uid(self) -> None:
        from PyQt6.QtWidgets import QApplication
        uid = self.current_uid()
        if uid:
            QApplication.clipboard().setText(uid)

    def _copy_result_id(self) -> None:
        from PyQt6.QtWidgets import QApplication
        rid = self.current_result_id()
        if rid:
            QApplication.clipboard().setText(rid)
