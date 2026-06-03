"""metadata_panel.py — Specimen metadata editor (right column).

Displays and allows editing of:
  - Collection / photo dates
  - Storage code
  - Coordinates (lon/lat text — CoordParser integration is a future task)
  - Collector / photographer / identifier
  - Notes / photo_notes
  - Taxonomy (4-level): taxon_group / order_name / family / genus
    via TaxonomyInputPanel (4-level autocomplete overlay with Latin names).
    Chinese name fields are intentionally user-filled, NOT auto-populated
    (see project constraint "中文字段不自动填充").
  - Scientific name (Latin) — part of TaxonomyInputPanel

Signals
-------
metadata_changed(uid: str, field: str, value: str)
    Emitted on any field edit so WorkbenchView can schedule a DB save.
save_requested(uid: str)
    Emitted when the user clicks "保存".
"""
from __future__ import annotations

import math
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor, QPainter, QPen
from PyQt6.QtWidgets import (
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

if TYPE_CHECKING:
    from app.app_context import AppContext
    from app.models.specimen import Specimen


class MetaScoreRing(QWidget):
    """Circular progress ring showing metadata completeness (0-100 %).

    Mirrors the web prototype's ``meta-score-ring`` element: a thin arc
    that sweeps from the 12-o'clock position clockwise, coloured by score
    (green ≥80, amber ≥50, red <50).

    Usage:
        ring = MetaScoreRing()
        ring.set_score(75)   # 75 % completeness
    """

    _RING_DIAMETER = 56
    _RING_WIDTH = 6   # pen width

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._score: int = 0
        size = self._RING_DIAMETER + self._RING_WIDTH * 2
        self.setFixedSize(size, size)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

    def set_score(self, score: int) -> None:
        """Set completeness percentage (0-100) and repaint."""
        self._score = max(0, min(100, score))
        self.update()

    def score(self) -> int:
        return self._score

    def paintEvent(self, event) -> None:  # noqa: ARG002
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        w = self._RING_WIDTH
        d = self._RING_DIAMETER
        # Leave room for pen width on all sides
        rect_x = w
        rect_y = w
        rect_w = d
        rect_h = d

        # Track ring (muted background arc)
        track_pen = QPen(QColor("#1e3a40"))
        track_pen.setWidth(w)
        track_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(track_pen)
        painter.drawArc(rect_x, rect_y, rect_w, rect_h, 0, 360 * 16)

        # Score arc
        if self._score > 0:
            if self._score >= 80:
                color = "#36c98f"   # green
            elif self._score >= 50:
                color = "#e8aa60"   # amber
            else:
                color = "#e05a5a"   # red

            score_pen = QPen(QColor(color))
            score_pen.setWidth(w)
            score_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(score_pen)
            # Qt arc: startAngle in 1/16th degrees; positive = counter-clockwise.
            # Start at 12 o'clock (90°); sweep clockwise = negative angle.
            start_angle = 90 * 16
            span_angle = -int(round(self._score / 100.0 * 360 * 16))
            painter.drawArc(rect_x, rect_y, rect_w, rect_h, start_angle, span_angle)

        # Percentage text centred
        painter.setPen(QPen(QColor("#b0ccd0")))
        font = painter.font()
        font.setPointSize(9)
        font.setBold(True)
        painter.setFont(font)
        full_rect = self.rect()
        painter.drawText(full_rect, Qt.AlignmentFlag.AlignCenter, f"{self._score}%")

        painter.end()


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
        card = QFrame(self)
        card.setObjectName("PanelCard")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(card)
        from app.config.effects import apply_card_shadow
        apply_card_shadow(card)

        root = QVBoxLayout(card)
        root.setContentsMargins(20, 16, 20, 16)
        root.setSpacing(12)

        # Header: title + score ring + UID badge
        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(10)
        title = QLabel("标本与拍摄元数据")
        title.setObjectName("CardTitle")
        header.addWidget(title)
        header.addStretch()
        self._score_ring = MetaScoreRing()
        self._score_ring.setToolTip("元数据完整度（5 项核心字段）")
        header.addWidget(self._score_ring)
        self._uid_badge = QLabel("—")
        self._uid_badge.setObjectName("Mono")
        header.addWidget(self._uid_badge)
        root.addLayout(header)

        line = QFrame()
        line.setObjectName("Divider")
        line.setFixedHeight(1)
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
        form.addRow(_section_label("分类（4 级自动补全）"))

        # Build TaxonomyInputPanel with TaxonomyService
        # Graceful fallback: if taxonomy data files are unavailable, keep
        # plain QLineEdit stubs so the panel doesn't crash.
        self._taxonomy_panel: Optional[QWidget] = None
        try:
            from app.services.taxonomy_service import TaxonomyService
            from app.widgets.taxonomy_input import TaxonomyInputPanel

            # Locate seed and user taxonomy files relative to this package
            _here = Path(__file__).parent.parent.parent  # project root
            seed_path = _here / "data" / "taxonomy_seed.json"
            user_path = _here / "data" / "user_taxonomy.json"

            svc = TaxonomyService(seed_path, user_path)
            self._taxonomy_panel = TaxonomyInputPanel(svc, form_container)
            self._taxonomy_panel.value_committed.connect(self._on_taxonomy_committed)
            form.addRow(self._taxonomy_panel)
        except Exception:
            # Fallback: plain QLineEdits (service unavailable)
            self._taxonomy_panel = None

        # Proxy QLineEdit attributes expected by workbench_view._on_save_metadata
        # and load_specimen.  When the taxonomy panel is active these properties
        # wrap get_value() / set_value() on the panel.
        if self._taxonomy_panel is None:
            # Plain-linedit fallback (original stub behaviour)
            self._taxon_group = _field("大类", "e.g. Mollusca", attr="taxon_group")
            self._order_name = _field("目", "Order", attr="order_name")
            self._family = _field("科", "Family", attr="family")
            self._genus = _field("属", "Genus", attr="genus")
            self._scientific_name = _field("种名", "e.g. Conus textile", attr="scientific_name")
        else:
            # Create invisible proxy QLineEdits so workbench_view can read .text()
            # without knowing about the overlay.
            self._taxon_group = _invisible_line_edit()
            self._order_name = _invisible_line_edit()
            self._family = _invisible_line_edit()
            self._genus = _invisible_line_edit()
            self._scientific_name = _invisible_line_edit()

        # ── WoRMS quick-fill button (oracle: renderWormsPopupOverlay ~12685) ───
        # Opens a lightweight search dialog; fills Latin fields only.
        # Chinese *Cn fields are never touched (invariant enforced in callback).
        worms_btn_row = QHBoxLayout()
        worms_btn_row.setContentsMargins(0, 2, 0, 2)
        self._worms_quick_btn = QPushButton("WoRMS 查")
        self._worms_quick_btn.setObjectName("WormsFill")
        self._worms_quick_btn.setToolTip("从 WoRMS 快捷查找物种，填充拉丁分类信息（不覆盖中文）")
        self._worms_quick_btn.setFixedHeight(28)
        self._worms_quick_btn.setStyleSheet(
            "QPushButton#WormsFill { font-size:11px; padding:2px 10px; }"
        )
        self._worms_quick_btn.clicked.connect(self._on_worms_quick_fill)
        worms_btn_row.addStretch()
        worms_btn_row.addWidget(self._worms_quick_btn)
        form.addRow(worms_btn_row)

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
        self._save_btn.setFixedHeight(34)
        from app.config import icons as _icons
        _icons.set_button_icon(self._save_btn, "mdi6.content-save-outline",
                               color=_icons.TONE_ON_ACCENT, size=15)
        self._save_btn.clicked.connect(self._on_save)
        self._save_btn.setEnabled(False)
        btn_row.addWidget(self._save_btn)
        root.addLayout(btn_row)

    # ── Public API ────────────────────────────────────────────────────────────

    @staticmethod
    def _compute_score(specimen: "Specimen") -> int:
        """Metadata completeness 0-100; mirrors server.js _spMetaScore (5 fields)."""
        fields = [specimen.scientific_name, specimen.family,
                  specimen.collector, specimen.lon, specimen.lat]
        filled = sum(1 for f in fields if f is not None and str(f).strip() != "")
        return round(filled / len(fields) * 100)

    def load_specimen(self, specimen: "Specimen") -> None:
        """Populate all fields from a Specimen dataclass instance."""
        self._uid = specimen.uid
        self._dirty = False
        self._uid_badge.setText(specimen.uid[:25] + ("…" if len(specimen.uid) > 25 else ""))
        self._save_btn.setEnabled(False)
        self._score_ring.set_score(self._compute_score(specimen))

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

        # Populate taxonomy: either via TaxonomyInputPanel or plain QLineEdits
        taxon_values = {
            "taxonGroup":     str(specimen.taxon_group or ""),
            "order":          str(specimen.order_name or ""),
            "family":         str(specimen.family or ""),
            "scientificName": str(specimen.scientific_name or ""),
        }
        if self._taxonomy_panel is not None:
            try:
                from app.widgets.taxonomy_input import TaxonomyInputPanel
                if isinstance(self._taxonomy_panel, TaxonomyInputPanel):
                    self._taxonomy_panel.set_context(taxon_values)
                    self._taxonomy_panel.set_values(taxon_values)
            except Exception:
                pass
        # Always keep proxy QLineEdits in sync so workbench_view can read them
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
        self._score_ring.set_score(0)
        for edit in self._all_edits():
            edit.blockSignals(True)
            edit.clear()
            edit.blockSignals(False)
        for ta in (self._notes, self._photo_notes):
            ta.blockSignals(True)
            ta.clear()
            ta.blockSignals(False)
        if self._taxonomy_panel is not None:
            try:
                from app.widgets.taxonomy_input import TaxonomyInputPanel
                if isinstance(self._taxonomy_panel, TaxonomyInputPanel):
                    self._taxonomy_panel.clear_all()
            except Exception:
                pass

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

    def _on_taxonomy_committed(self, changed: dict) -> None:
        """Called when TaxonomyInputPanel commits a selection.

        Updates proxy QLineEdits so workbench_view._on_save_metadata can read
        .text() as usual.  Emits metadata_changed for each updated field.

        Hard rule: Chinese fields (*Cn) are NEVER auto-filled here.
        Mapping: taxonGroup → taxon_group, order → order_name,
                 family → family, scientificName → scientific_name.
        """
        _sp_to_db: dict[str, tuple[str, QLineEdit]] = {
            "taxonGroup":     ("taxon_group",     self._taxon_group),
            "order":          ("order_name",      self._order_name),
            "family":         ("family",          self._family),
            "scientificName": ("scientific_name", self._scientific_name),
        }
        for sp_key, value in changed.items():
            db_field, proxy_edit = _sp_to_db.get(sp_key, (None, None))
            if db_field and proxy_edit is not None:
                proxy_edit.blockSignals(True)
                proxy_edit.setText(value)
                proxy_edit.blockSignals(False)
                self._on_field_edited(db_field, value)

    def _on_worms_quick_fill(self) -> None:
        """Open WormsQuickFillDialog pre-filled with current taxon/scientific name.

        On selection, fills Latin taxonomy fields into the specimen via ctx.
        Chinese fields (*Cn) are never overwritten — enforced by ctx.worms_fill_specimen.

        Oracle: renderWormsPopupOverlay / doWormsPopupSearch in app.js ~12685.
        """
        from pathlib import Path as _Path
        from app.services.worms_service import WormsService
        from app.views.worms_view import WormsQuickFillDialog

        # Build a WormsService for this widget (uses project data dir if available)
        try:
            project_dir = getattr(self.ctx, "current_project_dir", None)
            _data = (_Path(project_dir) / "_data") if project_dir else \
                    (_Path.home() / ".photo_workbench" / "data")
            _data.mkdir(parents=True, exist_ok=True)
            svc = WormsService(
                cache_path=str(_data / "worms_cache.json"),
                jobs_path=str(_data / "worms_jobs.json"),
            )
        except Exception:
            return

        # Pre-fill from current scientific name or taxon group
        initial = (
            self._scientific_name.text().strip()
            or self._taxon_group.text().strip()
        )

        # Callback: delegate to ctx.worms_fill_specimen then refresh proxy edits
        def _fill(rec: dict) -> None:
            fill_fn = getattr(self.ctx, "worms_fill_specimen", None)
            if callable(fill_fn):
                fill_fn(rec)
            # Also update proxy edits directly so the panel reflects the change
            # without a full reload.  Mirrors wormsFillToSpecimen field mapping:
            #   class  → taxon_group, order → order_name, family → family,
            #   Species → scientific_name.  Chinese fields: never touched.
            mapping = {
                "class":  ("taxon_group",     self._taxon_group),
                "order":  ("order_name",      self._order_name),
                "family": ("family",          self._family),
                "genus":  ("genus",           self._genus),
            }
            for worms_key, (db_field, proxy) in mapping.items():
                val = rec.get(worms_key, "")
                if val:
                    proxy.blockSignals(True)
                    proxy.setText(val)
                    proxy.blockSignals(False)
                    self._on_field_edited(db_field, val)
            if rec.get("rank") == "Species" and rec.get("scientificname"):
                self._scientific_name.blockSignals(True)
                self._scientific_name.setText(rec["scientificname"])
                self._scientific_name.blockSignals(False)
                self._on_field_edited("scientific_name", rec["scientificname"])

        dlg = WormsQuickFillDialog(svc, _fill, initial_query=initial, parent=self)
        dlg.exec()

    def _on_save(self) -> None:
        if self._uid:
            self._dirty = False
            self._save_btn.setEnabled(False)
            self.save_requested.emit(self._uid)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _section_label(text: str) -> QLabel:
    """Return a styled section header for the metadata form."""
    lbl = QLabel(text)
    lbl.setObjectName("Section")
    lbl.setStyleSheet("padding-top:10px;")
    return lbl


def _invisible_line_edit() -> QLineEdit:
    """Return a hidden QLineEdit used as a proxy for taxonomy panel values.

    workbench_view._on_save_metadata reads .text() on these proxies to collect
    taxonomy field values; the actual visible input is in TaxonomyInputPanel.
    """
    edit = QLineEdit()
    edit.hide()
    return edit
