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

from PyQt6.QtCore import pyqtSignal
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

    def __init__(self, ctx: "AppContext", parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self._setup_ui()

    # ── UI ────────────────────────────────────────────────────────────────────

    def _setup_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(8)

        # Section header
        hdr = QLabel("命名生成器")
        hdr.setObjectName("Section")
        root.addWidget(hdr)

        # Form grid: label | field  (6 naming fields)
        grid = QGridLayout()
        grid.setSpacing(6)
        grid.setColumnMinimumWidth(0, 60)
        grid.setColumnStretch(1, 1)

        def _row(row: int, label: str, placeholder: str) -> QLineEdit:
            lbl = QLabel(label)
            lbl.setObjectName("Muted")
            edit = QLineEdit()
            edit.setPlaceholderText(placeholder)
            edit.setFixedHeight(28)
            grid.addWidget(lbl, row, 0)
            grid.addWidget(edit, row, 1)
            return edit

        self._province = _row(0, "地区", "如 FJ")
        self._site = _row(1, "样地", "如 YGLZ")
        self._station = _row(2, "站位", "如 B2")
        self._species_id = _row(3, "编号", "如 DLC001")
        self._storage = _row(4, "保存", "如 T95E / RD75E")
        self._collection_date = _row(5, "采集日期", "YYYYMMDD")
        self._photo_date = _row(6, "拍摄日期", "YYYYMMDD（选填）")

        # Sequence number
        seq_lbl = QLabel("序号")
        seq_lbl.setObjectName("Muted")
        self._seq = QSpinBox()
        self._seq.setMinimum(1)
        self._seq.setMaximum(999)
        self._seq.setValue(1)
        self._seq.setFixedHeight(28)
        grid.addWidget(seq_lbl, 7, 0)
        grid.addWidget(self._seq, 7, 1)

        root.addLayout(grid)

        # Divider
        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setStyleSheet("color: rgba(145,182,181,0.13);")
        root.addWidget(line)

        # Live preview
        preview_lbl = QLabel("标本唯一编号")
        preview_lbl.setObjectName("Muted")
        root.addWidget(preview_lbl)

        self._uid_preview = QLabel("—")
        self._uid_preview.setObjectName("Mono")
        self._uid_preview.setWordWrap(True)
        root.addWidget(self._uid_preview)

        result_lbl = QLabel("成果编号（含序号）")
        result_lbl.setObjectName("Muted")
        root.addWidget(result_lbl)

        self._result_preview = QLabel("—")
        self._result_preview.setObjectName("Mono")
        self._result_preview.setWordWrap(True)
        root.addWidget(self._result_preview)

        # R-prefix dual-label warning (hidden by default)
        self._rna_warning = QLabel(
            "⚠️  R 前缀（已取 RNA）— 需额外生成 RNAlater 组织管标签"
        )
        self._rna_warning.setStyleSheet(
            "color:#e6b04a; background:rgba(230,176,74,0.12);"
            " border:1px solid rgba(230,176,74,0.3); border-radius:4px;"
            " padding:4px 8px; font-size:12px;"
        )
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

        self._uid_preview.setText(uid if uid else "—")
        self._result_preview.setText(result_id if result_id else "—")

        # R-prefix dual-label warning
        if storage.upper().startswith("R"):
            self._rna_warning.show()
        else:
            self._rna_warning.hide()

        if uid:
            self.uid_generated.emit(uid)
        if result_id:
            self.result_id_generated.emit(result_id)

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
