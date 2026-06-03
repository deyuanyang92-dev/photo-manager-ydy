"""workbench_view.py — Daily-use workbench main view.

Integrates the five sub-widgets into a QSplitter three-column layout:

  Left   | Centre (top: monitor, bottom: grouping) | Right
  ────────────────────────────────────────────────────────
  Specimen │  Monitor panel (incoming-jpg / results)  │ Naming
  Sidebar  │  ──────────────────────────────────────  │ + Metadata
           │  Grouping panel (draft + composed)        │

The view wires up all inter-widget signals and drives the service layer:
  - on_activate(): scans the project via monitor_service and loads the
    last-active specimen.
  - Selecting a specimen: loads its grouping + metadata.
  - Compose / organise / undo signals: placeholder stubs — real
    helicon_service / archive_service integration deferred to the
    caller's wiring pass (see CLAUDE.md constraint: only build listed files).

Oracle: docs/modules/workbench.md, monitor.md; web app.js workspace render.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (
    QLabel,
    QMessageBox,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from app.views.base_view import BaseView
from app.widgets.grouping_panel import GroupingPanel
from app.widgets.metadata_panel import MetadataPanel
from app.widgets.monitor_panel import MonitorPanel
from app.widgets.naming_panel import NamingPanel
from app.widgets.specimen_sidebar import SpecimenSidebar


class WorkbenchView(BaseView):
    """Daily-use workbench — specimen list | monitor + grouping | naming + metadata.

    view_id   = "workbench"
    nav_title = "工作台"
    nav_icon  = "🔬"
    """

    view_id = "workbench"
    nav_title = "工作台"
    nav_icon = "🔬"

    # ── Build UI ──────────────────────────────────────────────────────────────

    def _setup_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── Outer horizontal splitter: left | centre+right ─────────────────
        outer = QSplitter(Qt.Orientation.Horizontal)
        outer.setChildrenCollapsible(False)

        # ── Left: specimen sidebar ─────────────────────────────────────────
        self._sidebar = SpecimenSidebar(self.ctx)
        self._sidebar.setMinimumWidth(160)
        self._sidebar.specimen_selected.connect(self._on_specimen_selected)
        outer.addWidget(self._sidebar)

        # ── Centre: vertical splitter (monitor top, grouping bottom) ───────
        centre = QSplitter(Qt.Orientation.Vertical)
        centre.setChildrenCollapsible(False)

        self._monitor = MonitorPanel(self.ctx)
        self._monitor.refresh_requested.connect(self._refresh_monitor)
        self._monitor.assign_requested.connect(self._on_assign_jpg)
        self._monitor.unassign_requested.connect(self._on_unassign_jpg)
        centre.addWidget(self._monitor)

        self._grouping = GroupingPanel(self.ctx)
        self._grouping.compose_requested.connect(self._on_compose_requested)
        self._grouping.organise_requested.connect(self._on_organise_requested)
        self._grouping.undo_compose_requested.connect(self._on_undo_compose)
        self._grouping.grouping_changed.connect(self._on_grouping_changed)
        centre.addWidget(self._grouping)

        centre.setSizes([300, 250])
        outer.addWidget(centre)

        # ── Right: naming + metadata ────────────────────────────────────────
        right = QWidget()
        right.setMinimumWidth(220)
        right_lay = QVBoxLayout(right)
        right_lay.setContentsMargins(0, 0, 0, 0)
        right_lay.setSpacing(0)

        self._naming = NamingPanel(self.ctx)
        right_lay.addWidget(self._naming, stretch=2)

        self._metadata = MetadataPanel(self.ctx)
        self._metadata.save_requested.connect(self._on_save_metadata)
        right_lay.addWidget(self._metadata, stretch=3)

        outer.addWidget(right)

        # Initial splitter proportions: 1 : 3 : 1.5
        outer.setSizes([180, 600, 280])
        root.addWidget(outer)

        # ── No-project banner ───────────────────────────────────────────────
        self._no_project_banner = QLabel(
            "未选择项目 — 请先在「项目总览」创建或打开一个项目"
        )
        self._no_project_banner.setObjectName("Muted")
        self._no_project_banner.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._no_project_banner.hide()
        root.addWidget(self._no_project_banner)

        # Pending grouping-save debounce timer (500 ms)
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(500)
        self._save_timer.timeout.connect(self._flush_grouping_save)

        # Track current UID for grouping edits
        self._current_uid: Optional[str] = None
        self._pending_grouping = None  # SpecimenGrouping awaiting save

    # ── BaseView contract ─────────────────────────────────────────────────────

    def on_activate(self) -> None:
        """Called each time the user navigates to the workbench page."""
        if not self.ctx.has_project:
            self._show_no_project()
            return

        self._no_project_banner.hide()
        self._sidebar.refresh()
        self._refresh_monitor()

        # Re-select the previously active specimen if possible
        active_uid = self._get_active_uid()
        if active_uid:
            self._sidebar.select_uid(active_uid)
            self._load_specimen(active_uid)

    # ── Specimen selection ────────────────────────────────────────────────────

    def _on_specimen_selected(self, uid: str) -> None:
        self._current_uid = uid
        self._load_specimen(uid)

    def _load_specimen(self, uid: str) -> None:
        """Load grouping + naming + metadata for *uid*."""
        self._current_uid = uid
        db = self.ctx.get_db()
        if not db:
            return

        # Load grouping
        try:
            from app.services.grouping_service import load_grouping
            grouping = load_grouping(db, uid)
            self._grouping.load_grouping(uid, grouping)
        except Exception:
            self._grouping.clear()

        # Load specimen record for naming + metadata panels
        try:
            row = db.execute(
                "SELECT * FROM specimens WHERE uid = ?", (uid,)
            ).fetchone()
            if row:
                from app.models.specimen import Specimen
                sp = Specimen.from_row(row)
                self._naming.load_specimen(sp.raw or {
                    "province": sp.province,
                    "site": sp.site,
                    "station": sp.station,
                    "id": sp.id,
                    "storage": sp.storage,
                    "collection_date": sp.collection_date,
                    "photo_date": sp.photo_date,
                })
                self._metadata.load_specimen(sp)
        except Exception:
            pass

    # ── Monitor ───────────────────────────────────────────────────────────────

    def _refresh_monitor(self) -> None:
        """Re-scan the project directory and repopulate the monitor panel."""
        project_dir = self.ctx.current_project_dir
        if not project_dir:
            self._monitor.clear()
            return

        db = self.ctx.get_db()
        if not db:
            self._monitor.clear()
            return

        try:
            from app.services.monitor_service import (
                AttributionCtx,
                scan_project,
            )
            from app.services.grouping_service import get_explicit_unassigns

            # Build minimal attribution context (no activation events here;
            # full activation wiring is done when the caller integrates
            # specimen-log reading — see NOTES.md and workbench.md)
            explicit_unassigns = set()
            try:
                explicit_unassigns = get_explicit_unassigns(db)
            except Exception:
                pass

            attr = AttributionCtx(explicit_unassigns=explicit_unassigns)

            result = scan_project(project_dir, db, attr=attr)
            self._monitor.load_scan(result)
        except FileNotFoundError:
            self._monitor.clear()
        except Exception:
            self._monitor.clear()

    def _on_assign_jpg(self, path: str) -> None:
        """Manual attribution: wire to grouping_service / monitor log when caller integrates."""
        # Stub — emits a signal; full integration needs specimen-log write
        # (server.js POST /api/specimen-log/assign equivalent).
        # Called when user presses "归属" on a JPG card.
        pass

    def _on_unassign_jpg(self, path: str) -> None:
        """Explicit unassign: adds path to the P0 blacklist."""
        db = self.ctx.get_db()
        if not db or not path:
            return
        try:
            from app.services.grouping_service import add_explicit_unassign
            add_explicit_unassign(db, path)
            self._refresh_monitor()
        except Exception:
            pass

    # ── Grouping ──────────────────────────────────────────────────────────────

    def _on_compose_requested(self, uid: str, group_index: int) -> None:
        """Stub — caller wires helicon_service.compose_group() here."""
        # Oracle: workbench.md groupingComposeSelected → helicon.js stackSingle
        pass

    def _on_organise_requested(self, uid: str, group_index: int) -> None:
        """Stub — caller wires archive_service.archive_group() here."""
        pass

    def _on_undo_compose(self, uid: str, group_index: int) -> None:
        """Undo compose: clear composedTiffPath, move TIFF to _retired-tiff/."""
        db = self.ctx.get_db()
        if not db:
            return
        try:
            from app.services.grouping_service import load_grouping, save_grouping
            grouping = load_grouping(db, uid)
            for g in grouping.groups:
                if g.group_index == group_index and g.composed_tiff_path:
                    # Move TIFF to _retired-tiff/ (TIFF never deleted — hard rule 3)
                    self._retire_tiff(g.composed_tiff_path)
                    g.retired_tiff_paths.append(g.composed_tiff_path)
                    g.composed_tiff_path = None
                    g.status = "pending"
                    break
            save_grouping(db, uid, grouping.groups)
            self._grouping.load_grouping(uid, grouping)
        except Exception:
            pass

    def _retire_tiff(self, tiff_path: str) -> None:
        """Move a TIFF to the project's _retired-tiff/ directory."""
        try:
            import shutil
            src = Path(tiff_path)
            if not src.is_file():
                return
            project_dir = self.ctx.current_project_dir
            if not project_dir:
                return
            retired_dir = Path(project_dir) / "_retired-tiff"
            retired_dir.mkdir(exist_ok=True)
            dest = retired_dir / src.name
            # Avoid overwriting — add a numeric suffix if needed
            if dest.exists():
                stem, suffix = src.stem, src.suffix
                i = 1
                while dest.exists():
                    dest = retired_dir / f"{stem}_{i}{suffix}"
                    i += 1
            shutil.move(str(src), str(dest))
        except Exception:
            pass

    def _on_grouping_changed(self) -> None:
        """Debounce-save grouping to DB after edits."""
        self._pending_grouping = None  # will re-read from grouping panel
        self._save_timer.start()

    def _flush_grouping_save(self) -> None:
        """Persist current in-memory grouping to the DB."""
        uid = self._current_uid
        if not uid:
            return
        db = self.ctx.get_db()
        if not db:
            return
        # The GroupingPanel holds the authoritative in-memory state via its
        # _grouping attribute; reach in safely.
        grouping = getattr(self._grouping, "_grouping", None)
        if not grouping:
            return
        try:
            from app.services.grouping_service import save_grouping
            save_grouping(db, uid, grouping.groups)
        except Exception:
            pass

    # ── Metadata save ─────────────────────────────────────────────────────────

    def _on_save_metadata(self, uid: str) -> None:
        """Persist metadata edits to the DB specimens table."""
        db = self.ctx.get_db()
        if not db:
            return
        # Collect values from the metadata panel's form fields
        panel = self._metadata
        fields: dict[str, str] = {
            "collector":       panel._collector.text(),
            "collection_date": panel._collection_date.text(),
            "photo_date":      panel._photo_date.text(),
            "photographer":    panel._photographer.text(),
            "identifier":      panel._identifier.text(),
            "geo_area":        panel._geo_area.text(),
            "storage":         panel._storage.text(),
            "taxon_group":     panel._taxon_group.text(),
            "order_name":      panel._order_name.text(),
            "family":          panel._family.text(),
            "genus":           panel._genus.text(),
            "scientific_name": panel._scientific_name.text(),
            "notes":           panel._notes.toPlainText(),
            "photo_notes":     panel._photo_notes.toPlainText(),
        }
        lon_str = panel._lon.text().strip()
        lat_str = panel._lat.text().strip()

        set_clauses = ", ".join(f"{k} = ?" for k in fields)
        values = list(fields.values())

        try:
            lon_val: Optional[float] = float(lon_str) if lon_str else None
        except ValueError:
            lon_val = None
        try:
            lat_val: Optional[float] = float(lat_str) if lat_str else None
        except ValueError:
            lat_val = None

        try:
            db.execute(
                f"UPDATE specimens SET {set_clauses}, lon = ?, lat = ? WHERE uid = ?",
                values + [lon_val, lat_val, uid],
            )
            db.commit()
        except Exception:
            pass

        # Refresh naming panel with latest values if storage changed
        self._load_specimen(uid)

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _get_active_uid(self) -> Optional[str]:
        """Return the currently active specimen UID from the tasks table."""
        db = self.ctx.get_db()
        if not db:
            return None
        try:
            row = db.execute(
                "SELECT uid FROM tasks WHERE is_active = 1 LIMIT 1"
            ).fetchone()
            return row[0] if row else None
        except Exception:
            return None

    def _show_no_project(self) -> None:
        self._sidebar.refresh()  # clears list
        self._monitor.clear()
        self._grouping.clear()
        self._metadata.clear()
        self._no_project_banner.show()
