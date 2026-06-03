"""metadata_panel.py — Specimen metadata editor (right column).

Displays and allows editing of:
  - Collection / photo dates
  - Storage code
  - Coordinates (lon/lat text — CoordParser integration is a future task)
  - Collector / photographer / identifier
  - Notes / photo_notes
  - Taxonomy (4-level): taxon_group / order_name / family / genus
    (Latin fields only; Chinese name fields are intentionally user-filled,
     NOT auto-populated — see project constraint "中文字段不自动填充")
  - Scientific name (Latin)

The taxonomy floating autocomplete overlay is a placeholder: each field is
a simple QLineEdit.  A future agent will replace these with the full
taxonomy autocomplete widget (see CLAUDE.md "taxonomy 浮层另有 agent 做").

Signals
-------
metadata_changed(uid: str, field: str, value: str)
    Emitted on any field edit so WorkbenchView can schedule a DB save.
save_requested(uid: str)
    Emitted when the user clicks "保存".
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

if TYPE_CHECKING:
    from app.app_context import AppContext
    from app.models.specimen import Specimen


class MetadataPanel(QWidget):
    """Right-column metadata editor for the currently selected specimen.

    Signals
    -------
    metadata_changed(uid, field, value)
    save_requested(uid)
    """

    metadata_changed = pyqtSignal(str, str, str)
    save_requested = pyqtSignal(str)

    def __init__(self, ctx: "AppContext", parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self._uid: Optional[str] = None
        self._dirty = False
        self._setup_ui()

    # ── UI ────────────────────────────────────────────────────────────────────

    def _setup_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Header
        header = QHBoxLayout()
        header.setContentsMargins(8, 6, 8, 6)
        title = QLabel("元数据")
        title.setObjectName("Section")
        header.addWidget(title)
        header.addStretch()
        self._uid_badge = QLabel("—")
        self._uid_badge.setObjectName("Muted")
        header.addWidget(self._uid_badge)
        root.addLayout(header)

        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setStyleSheet("color: rgba(145,182,181,0.13);")
        root.addWidget(line)

        # Scrollable form
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        form_container = QWidget()
        form = QFormLayout(form_container)
        form.setContentsMargins(12, 12, 12, 12)
        form.setSpacing(8)
        form.setLabelAlignment(
            __import__("PyQt6.QtCore", fromlist=["Qt"]).Qt.AlignmentFlag.AlignRight
        )

        def _field(label: str, placeholder: str = "", *, attr: str) -> QLineEdit:
            lbl = QLabel(label)
            lbl.setObjectName("Muted")
            edit = QLineEdit()
            edit.setPlaceholderText(placeholder)
            edit.setFixedHeight(28)
            edit.textEdited.connect(lambda v, a=attr: self._on_field_edited(a, v))
            form.addRow(lbl, edit)
            return edit

        # ── Acquisition fields ───────────────────────────────────────────────
        form.addRow(_section_label("采集"))
        self._collector = _field("采集人", "Collector", attr="collector")
        self._collection_date = _field("采集日期", "YYYYMMDD", attr="collection_date")
        self._photo_date = _field("拍摄日期", "YYYYMMDD", attr="photo_date")
        self._photographer = _field("拍摄人", "Photographer", attr="photographer")
        self._identifier = _field("鉴定人", "Identifier", attr="identifier")

        # ── Location fields ──────────────────────────────────────────────────
        form.addRow(_section_label("地点 & 坐标"))
        self._geo_area = _field("地理区域", "geo_area", attr="geo_area")
        self._lon = _field("经度 (DD)", "e.g. 120.1234", attr="lon")
        self._lat = _field("纬度 (DD)", "e.g. 25.6789", attr="lat")

        # ── Storage ──────────────────────────────────────────────────────────
        form.addRow(_section_label("保存"))
        self._storage = _field("保存方式", "e.g. T95E / RD75E", attr="storage")

        # ── Taxonomy (Latin — no auto-fill for Chinese) ──────────────────────
        form.addRow(_section_label("分类（占位 — taxonomy 浮层待替换）"))
        self._taxon_group = _field("大类", "e.g. Mollusca", attr="taxon_group")
        self._order_name = _field("目", "Order", attr="order_name")
        self._family = _field("科", "Family", attr="family")
        self._genus = _field("属", "Genus", attr="genus")
        self._scientific_name = _field("种名", "e.g. Conus textile", attr="scientific_name")

        # ── Notes ────────────────────────────────────────────────────────────
        form.addRow(_section_label("备注"))
        notes_lbl = QLabel("标本备注")
        notes_lbl.setObjectName("Muted")
        self._notes = QTextEdit()
        self._notes.setPlaceholderText("标本备注")
        self._notes.setFixedHeight(60)
        self._notes.textChanged.connect(lambda: self._on_field_edited("notes", self._notes.toPlainText()))
        form.addRow(notes_lbl, self._notes)

        photo_notes_lbl = QLabel("拍摄备注")
        photo_notes_lbl.setObjectName("Muted")
        self._photo_notes = QTextEdit()
        self._photo_notes.setPlaceholderText("拍摄角度/光照备注")
        self._photo_notes.setFixedHeight(60)
        self._photo_notes.textChanged.connect(lambda: self._on_field_edited("photo_notes", self._photo_notes.toPlainText()))
        form.addRow(photo_notes_lbl, self._photo_notes)

        scroll.setWidget(form_container)
        root.addWidget(scroll)

        # Save / reset buttons
        btn_row = QHBoxLayout()
        btn_row.setContentsMargins(8, 6, 8, 8)
        self._save_btn = QPushButton("保存元数据")
        self._save_btn.setObjectName("Primary")
        self._save_btn.setFixedHeight(30)
        self._save_btn.clicked.connect(self._on_save)
        self._save_btn.setEnabled(False)
        btn_row.addWidget(self._save_btn)
        root.addLayout(btn_row)

    # ── Public API ────────────────────────────────────────────────────────────

    def load_specimen(self, specimen: "Specimen") -> None:
        """Populate all fields from a Specimen dataclass instance."""
        self._uid = specimen.uid
        self._dirty = False
        self._uid_badge.setText(specimen.uid[:25] + ("…" if len(specimen.uid) > 25 else ""))
        self._save_btn.setEnabled(False)

        def _set(edit: QLineEdit, val) -> None:
            edit.blockSignals(True)
            edit.setText(str(val) if val is not None else "")
            edit.blockSignals(False)

        _set(self._collector, specimen.collector)
        _set(self._collection_date, specimen.collection_date)
        _set(self._photo_date, specimen.photo_date)
        _set(self._photographer, specimen.photographer)
        _set(self._identifier, specimen.identifier)
        _set(self._geo_area, specimen.geo_area)
        _set(self._lon, str(specimen.lon) if specimen.lon is not None else "")
        _set(self._lat, str(specimen.lat) if specimen.lat is not None else "")
        _set(self._storage, specimen.storage)
        _set(self._taxon_group, specimen.taxon_group)
        _set(self._order_name, specimen.order_name)
        _set(self._family, specimen.family)
        _set(self._genus, specimen.genus)
        _set(self._scientific_name, specimen.scientific_name)

        self._notes.blockSignals(True)
        self._notes.setPlainText(specimen.notes or "")
        self._notes.blockSignals(False)

        self._photo_notes.blockSignals(True)
        self._photo_notes.setPlainText(specimen.photo_notes or "")
        self._photo_notes.blockSignals(False)

    def clear(self) -> None:
        """Reset all fields; called when no specimen is selected."""
        self._uid = None
        self._uid_badge.setText("—")
        self._save_btn.setEnabled(False)
        for edit in self._all_edits():
            edit.blockSignals(True)
            edit.clear()
            edit.blockSignals(False)
        for ta in (self._notes, self._photo_notes):
            ta.blockSignals(True)
            ta.clear()
            ta.blockSignals(False)

    # ── Internal ──────────────────────────────────────────────────────────────

    def _all_edits(self) -> list:
        return [
            self._collector, self._collection_date, self._photo_date,
            self._photographer, self._identifier,
            self._geo_area, self._lon, self._lat,
            self._storage,
            self._taxon_group, self._order_name, self._family,
            self._genus, self._scientific_name,
        ]

    def _on_field_edited(self, field: str, value: str) -> None:
        self._dirty = True
        self._save_btn.setEnabled(True)
        if self._uid:
            self.metadata_changed.emit(self._uid, field, value)

    def _on_save(self) -> None:
        if self._uid:
            self._dirty = False
            self._save_btn.setEnabled(False)
            self.save_requested.emit(self._uid)


# ── Helper ────────────────────────────────────────────────────────────────────

def _section_label(text: str) -> QLabel:
    """Return a styled section separator label for the form."""
    lbl = QLabel(f"— {text} —")
    lbl.setObjectName("Muted")
    lbl.setStyleSheet("font-size:11px; letter-spacing:1px; padding-top:8px;")
    return lbl
