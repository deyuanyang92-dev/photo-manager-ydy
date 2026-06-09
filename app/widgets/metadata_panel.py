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

from PyQt6.QtCore import Qt, QThread, QTimer, pyqtSignal
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
        self._root = root
        self._collapsed = False

        # Header: title + ☰ field menu + collapse
        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(10)
        title = QLabel("其它")
        title.setObjectName("CardTitle")
        header.addWidget(title)
        header.addStretch()
        self._fields_btn = QPushButton("☰")
        self._fields_btn.setObjectName("Ghost")
        self._fields_btn.setFixedSize(28, 26)
        self._fields_btn.setToolTip("字段显示控制")
        self._fields_btn.clicked.connect(self._open_fields_menu)
        header.addWidget(self._fields_btn)
        self._collapse_btn = QPushButton("▾")
        self._collapse_btn.setObjectName("Ghost")
        self._collapse_btn.setFixedSize(28, 26)
        self._collapse_btn.setToolTip("收起")
        self._collapse_btn.clicked.connect(
            lambda: self.set_collapsed(not self._collapsed)
        )
        header.addWidget(self._collapse_btn)
        root.addLayout(header)

        # Scrollable single-column form (label* | field | ?), mirrors the
        # reference layout.  (web renderMetaCard — flat, no sections.)
        from PyQt6.QtCore import QTimer
        from app.widgets._form_row import form_row
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        form_container = QWidget()
        form = QVBoxLayout(form_container)
        self._form = form
        form.setContentsMargins(12, 8, 12, 12)
        form.setSpacing(10)
        _LW = 84
        self._rows: dict[str, QWidget] = {}

        def _field(attr: str, label: str, placeholder: str = "", *,
                   required: bool = False, help_text: str = "") -> QLineEdit:
            edit = QLineEdit()
            edit.setPlaceholderText(placeholder)
            edit.setFixedHeight(28)
            edit.textEdited.connect(lambda v, a=attr: self._on_field_edited(a, v))
            row = form_row(label, edit, required=required,
                           help_text=help_text or None, label_width=_LW)
            self._rows[attr] = row
            form.addWidget(row)
            return edit

        # 采集人 / 拍摄人 / 鉴定人
        self._collector = _field("collector", "采集人", "Collector",
                                 help_text="采集人姓名")
        self._photographer = _field("photographer", "拍摄人", "Photographer",
                                    help_text="拍摄人姓名")
        self._identifier = _field("identifier", "鉴定人", "Identifier",
                                  help_text="物种鉴定人姓名")

        # 站位经度 — field + 📍 map button on the lon row (oracle app.js:10295)
        self._lon = QLineEdit()
        self._lon.setPlaceholderText("经度")
        self._lon.setFixedHeight(28)
        self._lon.textEdited.connect(lambda v: self._on_field_edited("lon", v))
        lon_composite = QWidget()
        lc = QHBoxLayout(lon_composite)
        lc.setContentsMargins(0, 0, 0, 0)
        lc.setSpacing(4)
        lc.addWidget(self._lon, 1)
        self._map_btn = QPushButton("📍")
        self._map_btn.setObjectName("Ghost")
        self._map_btn.setFixedSize(28, 28)
        self._map_btn.setToolTip("地图选点")
        self._map_btn.clicked.connect(self._on_map_pick)
        lc.addWidget(self._map_btn)
        self._lon.setToolTip("十进制度数，如 119.5；输入后自动反查地名")
        lon_row = form_row("站位经度", lon_composite, label_width=_LW,
                           help_text="十进制度数，如 119.5；输入后自动反查地名")
        self._rows["lon"] = lon_row
        form.addWidget(lon_row)

        # 站位纬度 — no button (oracle app.js:10307)
        self._lat = _field("lat", "站位纬度", "纬度",
                           help_text="十进制度数，如 26.3；输入后自动反查地名")

        # 采集地理区 — field + 📡 GPS button (oracle app.js:10336)
        self._geo_area = QLineEdit()
        self._geo_area.setPlaceholderText("自动反解或手动输入")
        self._geo_area.setFixedHeight(28)
        self._geo_area.textEdited.connect(self._on_geo_edited)
        geo_field = QWidget()
        gf = QHBoxLayout(geo_field)
        gf.setContentsMargins(0, 0, 0, 0)
        gf.setSpacing(4)
        gf.addWidget(self._geo_area, 1)
        self._gps_btn = QPushButton("📡")
        self._gps_btn.setObjectName("Ghost")
        self._gps_btn.setFixedSize(28, 28)
        self._gps_btn.setToolTip("获取当前位置")
        self._gps_btn.clicked.connect(self._on_gps_click)
        gf.addWidget(self._gps_btn)
        self._geo_area.setToolTip("采集地理区域；可由经纬度自动反查，或手动填写")
        geo_row = form_row("采集地理区", geo_field, label_width=_LW,
                           help_text="采集地理区域；可由经纬度自动反查，或手动填写")
        self._rows["geo_area"] = geo_row
        form.addWidget(geo_row)

        # Inline geocode status (replaces the old blocking QMessageBox dialogs).
        self._geo_status = QLabel("")
        self._geo_status.setObjectName("MutedSmall")
        self._geo_status.setWordWrap(True)
        status_cell = QWidget()
        sc = QHBoxLayout(status_cell)
        sc.setContentsMargins(_LW + 8, 0, 0, 0)
        sc.addWidget(self._geo_status, 1)
        form.addWidget(status_cell)
        form.addStretch(1)

        # Auto reverse-geocode debounce (web 300ms, app.js:10290) — inline, no popup.
        self._geo_autofilled = False
        self._geo_timer = QTimer(self)
        self._geo_timer.setSingleShot(True)
        self._geo_timer.setInterval(400)
        self._geo_timer.timeout.connect(self._do_auto_reverse)
        self._lon.textEdited.connect(lambda *_: self._geo_timer.start())
        self._lat.textEdited.connect(lambda *_: self._geo_timer.start())

        scroll.setWidget(form_container)
        root.addWidget(scroll)

    # ── Collapse + field-visibility ───────────────────────────────────────────

    def set_collapsed(self, collapsed: bool) -> None:
        from app.widgets._collapse import set_layout_children_visible
        self._collapsed = collapsed
        set_layout_children_visible(self._root, 1, not collapsed)
        self._collapse_btn.setText("▸" if collapsed else "▾")
        self._collapse_btn.setToolTip("展开" if collapsed else "收起")

    def is_collapsed(self) -> bool:
        return self._collapsed

    def _open_fields_menu(self) -> None:
        from PyQt6.QtWidgets import QMenu
        menu = QMenu(self)
        rows = [
            ("采集人", ["collector"]),
            ("拍摄人", ["photographer"]),
            ("鉴定人", ["identifier"]),
            ("站位经纬度", ["lon", "lat"]),
            ("采集地理区", ["geo_area"]),
        ]
        for label, keys in rows:
            act = menu.addAction(label)
            act.setCheckable(True)
            first = self._rows.get(keys[0])
            act.setChecked(first.isVisible() if first else True)
            act.toggled.connect(
                lambda on, ks=keys: self._set_rows_visible(ks, on)
            )
        menu.exec(self._fields_btn.mapToGlobal(self._fields_btn.rect().bottomLeft()))

    def _set_rows_visible(self, keys: list, visible: bool) -> None:
        for k in keys:
            row = self._rows.get(k)
            if row is not None:
                row.setVisible(visible)

    # ── Public API ────────────────────────────────────────────────────────────

    def load_specimen(self, specimen: "Specimen") -> None:
        """Populate all fields from a Specimen dataclass instance."""
        self._uid = specimen.uid
        self._dirty = False

        def _set(edit: QLineEdit, val) -> None:
            edit.blockSignals(True)
            edit.setText(str(val) if val is not None else "")
            edit.blockSignals(False)

        _set(self._collector, specimen.collector)
        _set(self._photographer, specimen.photographer)
        _set(self._identifier, specimen.identifier)
        _set(self._geo_area, specimen.geo_area)
        _set(self._lon, str(specimen.lon) if specimen.lon is not None else "")
        _set(self._lat, str(specimen.lat) if specimen.lat is not None else "")

    def clear(self) -> None:
        """Reset all fields; called when no specimen is selected."""
        self._uid = None
        for edit in self._all_edits():
            edit.blockSignals(True)
            edit.clear()
            edit.blockSignals(False)

    # ── Collection-record auto-fill ───────────────────────────────────────────

    _AUTOFILL_MAP_ATTRS = (
        "collector", "photographer", "identifier", "lon", "lat", "geo_area",
    )

    def current_values(self) -> dict:
        """Return the current text of every auto-fillable field (for empty check)."""
        edits = {
            "collector": self._collector, "photographer": self._photographer,
            "identifier": self._identifier, "lon": self._lon,
            "lat": self._lat, "geo_area": self._geo_area,
        }
        return {k: e.text() for k, e in edits.items()}

    def apply_autofill(self, values: dict) -> None:
        """Fill fields from a collection record, never overwriting non-empty ones.

        *values* is the subset returned by
        ``collection_record_service.autofill_values`` (already filtered to empty
        targets). Each filled field emits ``metadata_changed`` so the workbench
        persists it through the normal autosave path.
        """
        edits = {
            "collector": self._collector, "photographer": self._photographer,
            "identifier": self._identifier, "lon": self._lon,
            "lat": self._lat, "geo_area": self._geo_area,
        }
        for key in self._AUTOFILL_MAP_ATTRS:
            if key not in values:
                continue
            edit = edits[key]
            if edit.text().strip():
                continue  # double-guard: never clobber a user value
            val = values[key]
            text = "" if val is None else str(val)
            edit.blockSignals(True)
            edit.setText(text)
            edit.blockSignals(False)
            self._on_field_edited(key, text)

    # ── Internal ──────────────────────────────────────────────────────────────

    def _all_edits(self) -> list:
        return [
            self._collector, self._photographer, self._identifier,
            self._geo_area, self._lon, self._lat,
        ]

    def _on_geo_edited(self, value: str) -> None:
        """User manually edited 采集地理区 → stop auto-fill from overwriting it."""
        self._geo_autofilled = False
        self._on_field_edited("geo_area", value)

    def _do_auto_reverse(self) -> None:
        """Auto reverse-geocode lon/lat → 采集地理区, inline (no dialogs).

        Mirrors web metaReverseGeocode() app.js:13655 / debounce app.js:10290.
        Skips overwriting a user-typed place name; reports status inline.
        """
        lon_str = self._lon.text().strip()
        lat_str = self._lat.text().strip()
        if not lon_str or not lat_str:
            self._geo_status.setText("")
            return
        try:
            lon = float(lon_str)
            lat = float(lat_str)
        except ValueError:
            self._geo_status.setText("经纬度格式无效（应为十进制度数）")
            return
        # Don't clobber a manually-typed place name.
        if self._geo_area.text().strip() and not self._geo_autofilled:
            self._geo_status.setText("")
            return
        self._geo_status.setText("查询中…")
        self._geocode_worker = _NominatimWorker(lat, lon)
        self._geocode_worker.result_ready.connect(self._on_geocode_result)
        self._geocode_worker.error_occurred.connect(self._on_geocode_error)
        self._geocode_worker.start()

    def _on_geocode_result(self, name: str) -> None:
        if name:
            self._geo_area.blockSignals(True)
            self._geo_area.setText(name)
            self._geo_area.blockSignals(False)
            self._geo_autofilled = True
            self._geo_status.setText(f"已自动填入：{name}")
            self._on_field_edited("geo_area", name)
        else:
            self._geo_status.setText("未找到地名，可手填")

    def _on_geocode_error(self, msg: str) -> None:
        # Inline status only — never a blocking popup.
        self._geo_status.setText("反查失败，可手填")

    def _on_map_pick(self) -> None:
        """Open the map picker (or manual input if WebEngine absent); fill lon/lat."""
        from app.widgets.map_pick_dialog import MapPickDialog
        dlg = MapPickDialog(
            self, lon=self._lon.text().strip(), lat=self._lat.text().strip()
        )

        def _picked(lon: float, lat: float) -> None:
            self._lon.blockSignals(True)
            self._lon.setText(f"{lon:.6f}")
            self._lon.blockSignals(False)
            self._lat.blockSignals(True)
            self._lat.setText(f"{lat:.6f}")
            self._lat.blockSignals(False)
            self._on_field_edited("lon", f"{lon:.6f}")
            self._on_field_edited("lat", f"{lat:.6f}")
            self._geo_autofilled = True
            self._do_auto_reverse()

        dlg.picked.connect(_picked)
        dlg.exec()

    def _on_gps_click(self) -> None:
        """Get device/IP location → fill lon/lat + auto-reverse (oracle app.js:10339)."""
        self._gps_btn.setText("⏳")
        self._gps_btn.setEnabled(False)
        self._gps_worker = _GpsWorker()
        self._gps_worker.result_ready.connect(self._on_gps_result)
        self._gps_worker.error_occurred.connect(self._on_gps_error)
        self._gps_worker.start()

    def _on_gps_result(self, lat: float, lon: float) -> None:
        self._gps_btn.setText("📡")
        self._gps_btn.setEnabled(True)
        self._lon.blockSignals(True)
        self._lon.setText(f"{lon:.6f}")
        self._lon.blockSignals(False)
        self._lat.blockSignals(True)
        self._lat.setText(f"{lat:.6f}")
        self._lat.blockSignals(False)
        self._on_field_edited("lon", f"{lon:.6f}")
        self._on_field_edited("lat", f"{lat:.6f}")
        self._geo_autofilled = True
        self._do_auto_reverse()

    def _on_gps_error(self, _msg: str) -> None:
        self._gps_btn.setText("❌")
        self._geo_status.setText("定位失败，请手填经纬度")
        QTimer.singleShot(2000, lambda: (
            self._gps_btn.setText("📡"),
            self._gps_btn.setEnabled(True),
        ))

    def _on_field_edited(self, field: str, value: str) -> None:
        # No save button: edits autosave via the metadata_changed signal
        # (web scheduleRightPanelPersist, app.js:9098).
        self._dirty = True
        if self._uid:
            self.metadata_changed.emit(self._uid, field, value)


# ── Nominatim reverse-geocode worker  #cursor metaReverseGeocode ─────────────


class _NominatimWorker(QThread):
    """Background thread calling Nominatim reverse-geocode.

    Oracle: app.js metaReverseGeocode() app.js:13655.
    """

    result_ready = pyqtSignal(str)
    error_occurred = pyqtSignal(str)

    def __init__(self, lat: float, lon: float, parent=None) -> None:
        super().__init__(parent)
        self._lat = lat
        self._lon = lon

    def run(self) -> None:
        import json as _json
        import urllib.request
        url = (
            f"https://nominatim.openstreetmap.org/reverse"
            f"?format=json&zoom=18&accept-language=zh-CN"
            f"&lat={self._lat}&lon={self._lon}"
        )
        try:
            req = urllib.request.Request(
                url,
                headers={"User-Agent": "photo-workbench-qt/1.0 (research use)"},
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = _json.loads(resp.read().decode())
            name = _nominatim_to_zh(data)
            if name:
                self.result_ready.emit(name)
            else:
                self.error_occurred.emit("Nominatim 未返回有效地名")
        except Exception as exc:
            self.error_occurred.emit(str(exc))


class _GpsWorker(QThread):
    """IP-based geolocation worker — desktop fallback for navigator.geolocation.

    Oracle: app.js:10339 navigator.geolocation.getCurrentPosition.
    Uses ip-api.com (free, no key) since Qt desktop has no browser geolocation.
    """

    result_ready = pyqtSignal(float, float)   # lat, lon
    error_occurred = pyqtSignal(str)

    def run(self) -> None:
        try:
            import httpx
            r = httpx.get(
                "https://ipapi.co/json/",
                timeout=8.0,
                headers={"User-Agent": "photo-workbench-qt/1.0 (research use)"},
            )
            d = r.json()
            lat = d.get("latitude")
            lon = d.get("longitude")
            if lat is not None and lon is not None:
                self.result_ready.emit(float(lat), float(lon))
            else:
                self.error_occurred.emit("定位失败")
        except Exception as exc:
            self.error_occurred.emit(str(exc))


def _nominatim_to_zh(data: dict) -> str:
    """Extract a Chinese place-name from a Nominatim reverse result."""
    if not isinstance(data, dict):
        return ""
    display = data.get("display_name", "")
    if display:
        parts = [p.strip() for p in display.split(",") if p.strip()]
        if len(parts) > 4:
            parts = parts[:4]
        return ", ".join(parts[::-1]) if parts else display
    addr = data.get("address", {})
    for key in ("village", "town", "city_district", "suburb", "city",
                "county", "state_district", "state"):
        val = addr.get(key, "")
        if val:
            state = addr.get("state", "")
            return f"{state} {val}".strip() if state else val
    return ""


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
