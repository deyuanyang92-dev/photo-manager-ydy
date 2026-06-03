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
  - Activate/deactivate: activation_service (mutual exclusion + event log).
  - Compose: helicon_service (QProcess + QProgressDialog; graceful no-Helicon).
  - Organise: organize_service gate + archive_service.archive_group.

Oracle: docs/modules/workbench.md, monitor.md; web app.js workspace render.
"""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMessageBox,
    QProgressDialog,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from app.views.base_view import BaseView
from app.widgets.grouping_panel import GroupingPanel
from app.widgets.metadata_panel import MetadataPanel
from app.widgets.monitor_panel import MonitorPanel
from app.widgets.naming_panel import NamingPanel
from app.widgets.results_column import ResultsColumn
from app.widgets.specimen_sidebar import SpecimenSidebar


class WorkbenchView(BaseView):
    """Daily-use workbench — specimen list | monitor + grouping | naming + metadata.

    view_id   = "workbench"
    nav_title = "工作台"
    nav_icon  = "🔬"
    """

    view_id = "workbench"
    nav_title = "照片工作区"
    nav_icon = "🔬"

    # ── Build UI ──────────────────────────────────────────────────────────────

    def _setup_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # NOTE: brand / project-switcher / global-action chrome now lives in
        # MainWindow's TopBar + ContextBar.  This view renders only the
        # three-column workbench content with generous whitespace.

        # ── Body container (header + dir-strip + splitter) ─────────────────
        body = QWidget()
        body_lay = QVBoxLayout(body)
        body_lay.setContentsMargins(28, 22, 28, 18)
        body_lay.setSpacing(18)
        root.addWidget(body, stretch=1)

        # ── Workspace header: title + project tag + helicon status ─────────
        body_lay.addLayout(self._build_header())

        # ── Directory info strip ───────────────────────────────────────────
        self._dir_strip = self._build_dir_strip()
        body_lay.addWidget(self._dir_strip)

        # ── Outer horizontal splitter: left | centre+right ─────────────────
        outer = QSplitter(Qt.Orientation.Horizontal)
        outer.setObjectName("WorkbenchSplitter")
        outer.setChildrenCollapsible(False)
        outer.setHandleWidth(18)

        # ── Left: specimen sidebar ─────────────────────────────────────────
        self._sidebar = SpecimenSidebar(self.ctx)
        self._sidebar.setMinimumWidth(210)
        self._sidebar.specimen_selected.connect(self._on_specimen_selected)
        self._sidebar.activate_requested.connect(self._on_sidebar_activate)
        self._sidebar.deactivate_requested.connect(self._on_sidebar_deactivate)
        self._sidebar.new_specimen_requested.connect(self._on_new_specimen)
        outer.addWidget(self._sidebar)

        # ── Centre ①: vertical splitter (monitor top, grouping bottom) ───────
        centre = QSplitter(Qt.Orientation.Vertical)
        centre.setChildrenCollapsible(False)
        centre.setHandleWidth(18)

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

        # ── Centre ②: 成果内容 column (composed TIFFs + archive ZIPs) ──────
        self._results = ResultsColumn()
        self._results.setMinimumWidth(200)
        outer.addWidget(self._results)

        # ── Right: naming + metadata ────────────────────────────────────────
        right = QWidget()
        right.setMinimumWidth(220)
        right_lay = QVBoxLayout(right)
        right_lay.setContentsMargins(0, 0, 0, 0)
        right_lay.setSpacing(18)

        self._naming = NamingPanel(self.ctx)
        self._naming.save_requested.connect(self._on_naming_save)
        right_lay.addWidget(self._naming, stretch=2)

        self._metadata = MetadataPanel(self.ctx)
        self._metadata.save_requested.connect(self._on_save_metadata)
        right_lay.addWidget(self._metadata, stretch=3)

        outer.addWidget(right)

        # Initial splitter proportions: sidebar : capture : results : right-panel
        outer.setSizes([220, 480, 240, 320])
        body_lay.addWidget(outer, stretch=1)

        # ── No-project banner ───────────────────────────────────────────────
        self._no_project_banner = QLabel(
            "未选择项目 — 请先在「项目总览」创建或打开一个项目"
        )
        self._no_project_banner.setObjectName("Muted")
        self._no_project_banner.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._no_project_banner.hide()
        body_lay.addWidget(self._no_project_banner)

        # Pending grouping-save debounce timer (500 ms)
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(500)
        self._save_timer.timeout.connect(self._flush_grouping_save)

        # Track current UID for grouping edits
        self._current_uid: Optional[str] = None
        self._pending_grouping = None  # SpecimenGrouping awaiting save

    # ── Header chrome builders ─────────────────────────────────────────────────

    def _build_header(self) -> QHBoxLayout:
        """Workspace title + project tag + Helicon status tag.

        Slim content-level header.  Global chrome (brand / project switcher /
        quick actions) lives in MainWindow's TopBar + ContextBar.
        """
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(12)
        title = QLabel("拍照工作台")
        title.setObjectName("WorkspaceTitle")
        row.addWidget(title)
        self._project_tag = QLabel("—")
        self._project_tag.setObjectName("TagSea")
        row.addWidget(self._project_tag)
        self._helicon_tag = QLabel("Helicon 未检测")
        self._helicon_tag.setObjectName("TagWarn")
        row.addWidget(self._helicon_tag)
        row.addStretch()
        return row

    def _build_dir_strip(self) -> QFrame:
        """Working-directory / camera-JPG / results path strip."""
        strip = QFrame()
        strip.setObjectName("DirStrip")
        lay = QHBoxLayout(strip)
        lay.setContentsMargins(12, 8, 12, 8)
        lay.setSpacing(18)

        def _item(label: str) -> QLabel:
            l = QLabel(label)
            l.setObjectName("DirLabel")
            lay.addWidget(l)
            path = QLabel("—")
            path.setObjectName("DirPath")
            lay.addWidget(path)
            return path

        self._dir_root = _item("工作目录")
        self._dir_incoming = _item("相机 JPG")
        self._dir_results = _item("成果")
        lay.addStretch()
        return strip

    def _refresh_header(self) -> None:
        """Update header tags + dir-strip + monitor batch from current state."""
        project_dir = self.ctx.current_project_dir
        name = Path(project_dir).name if project_dir else "（未选）"
        self._project_tag.setText(name)

        # Keep MainWindow's context bar (project + active badge) in sync.
        win = self.window()
        if hasattr(win, "refresh_context_bar"):
            try:
                win.refresh_context_bar()
            except Exception:
                pass

        # Helicon status tag
        installed = False
        try:
            from app.services.helicon_service import detect_helicon
            installed = bool(detect_helicon())
        except Exception:
            installed = False
        if installed:
            self._helicon_tag.setText("Helicon OK")
            self._helicon_tag.setObjectName("TagOk")
        else:
            self._helicon_tag.setText("Helicon 未检测")
            self._helicon_tag.setObjectName("TagWarn")
        self._helicon_tag.style().unpolish(self._helicon_tag)
        self._helicon_tag.style().polish(self._helicon_tag)

        # Dir strip
        if project_dir:
            self._dir_strip.show()
            self._dir_root.setText(project_dir)
            self._dir_incoming.setText("incoming-jpg/")
            self._dir_results.setText("results/")
        else:
            self._dir_strip.hide()

    # ── BaseView contract ─────────────────────────────────────────────────────

    def on_activate(self) -> None:
        """Called each time the user navigates to the workbench page."""
        if not self.ctx.has_project:
            self._show_no_project()
            return

        self._no_project_banner.hide()
        self._refresh_header()
        self._sidebar.refresh()
        self._refresh_monitor()

        # Re-select the previously active specimen if possible
        active_uid = self._get_active_uid()
        if active_uid:
            self._sidebar.select_uid(active_uid)
            self._load_specimen(active_uid)
        self._refresh_batch_header()

    # ── Specimen selection ────────────────────────────────────────────────────

    def _on_specimen_selected(self, uid: str) -> None:
        self._current_uid = uid
        self._load_specimen(uid)
        self._refresh_batch_header()

    def _refresh_batch_header(self) -> None:
        """Sync the monitor's batch-ident bar with the active specimen."""
        db = self.ctx.get_db()
        active_uid = self._get_active_uid()
        activated_at = None
        if db and active_uid:
            try:
                row = db.execute(
                    "SELECT activated_at FROM tasks WHERE uid = ?", (active_uid,)
                ).fetchone()
                if row:
                    activated_at = row[0]
            except Exception:
                pass
        batch_uid = active_uid or self._current_uid
        self._monitor.set_batch(batch_uid, active_uid, activated_at)

    def _on_new_specimen(self) -> None:
        """Start a fresh blank UID draft in the naming/metadata panels."""
        self._current_uid = None
        self._naming.load_specimen({})
        try:
            self._metadata.clear()
        except Exception:
            pass

    def _on_naming_save(self) -> None:
        """Persist the naming panel's current UID into the specimens table.

        Mirrors the web 「💾 保存」 button: upsert a specimen row keyed by the
        live-preview UID with the seven naming segments.  Chinese fields are
        never auto-filled (hard rule).
        """
        db = self.ctx.get_db()
        project_dir = self.ctx.current_project_dir
        if not db or not project_dir:
            QMessageBox.information(self, "保存", "请先打开一个项目工作区。")
            return
        uid = self._naming.current_uid()
        if not uid:
            QMessageBox.information(self, "保存", "编号尚未填写完整。")
            return
        n = self._naming
        try:
            db.execute(
                """
                INSERT INTO specimens (uid, id, province, site, station,
                                       storage, collection_date, photo_date,
                                       owner_project_dir)
                VALUES (?,?,?,?,?,?,?,?,?)
                ON CONFLICT(uid) DO UPDATE SET
                    id=excluded.id, province=excluded.province,
                    site=excluded.site, station=excluded.station,
                    storage=excluded.storage,
                    collection_date=excluded.collection_date,
                    photo_date=excluded.photo_date,
                    owner_project_dir=excluded.owner_project_dir
                """,
                (
                    uid,
                    n._species_id.text().strip(),
                    n._province.text().strip(),
                    n._site.text().strip(),
                    n._station.text().strip(),
                    n._storage.text().strip(),
                    n._collection_date.text().strip(),
                    n._photo_date.text().strip(),
                    project_dir,
                ),
            )
            db.commit()
            self._current_uid = uid
            self._sidebar.refresh()
            self._sidebar.select_uid(uid)
        except Exception as exc:
            QMessageBox.warning(self, "保存失败", str(exc))

    def _load_specimen(self, uid: str) -> None:
        """Load grouping + naming + metadata + results for *uid*."""
        self._current_uid = uid
        db = self.ctx.get_db()
        if not db:
            return

        # Load grouping
        grouping = None
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

        # Populate ② 成果内容 column from grouping data
        self._refresh_results_column(uid, grouping)

    # ── Monitor ───────────────────────────────────────────────────────────────

    def _refresh_monitor(self) -> None:
        """Re-scan the project directory and repopulate the monitor panel.

        Builds a full AttributionCtx by merging:
          - activation/manual-assign events from activation_service.read_activations
          - explicit_unassigns + path_to_uid from grouping_service
        """
        project_dir = self.ctx.current_project_dir
        if not project_dir:
            self._monitor.clear()
            return

        db = self.ctx.get_db()
        if not db:
            self._monitor.clear()
            return

        try:
            from app.services.monitor_service import scan_project
            from app.services.grouping_service import (
                get_explicit_unassigns,
                load_grouping,
            )
            from app.services.activation_service import read_activations

            # Base ctx from activation log (activations + assign_to_uid)
            attr = read_activations(project_dir)

            # Merge explicit_unassigns from grouping table (P0 blacklist)
            try:
                attr.explicit_unassigns = get_explicit_unassigns(db)
            except Exception:
                pass

            # Merge grouping path_to_uid (P1): all confirmed groups
            try:
                rows = db.execute(
                    "SELECT uid, jpg_paths FROM grouping"
                ).fetchall()
                import json as _json
                for row in rows:
                    uid = row[0]
                    paths = _json.loads(row[1] or "[]")
                    for p in paths:
                        resolved = str(Path(p).resolve())
                        attr.path_to_uid[resolved] = uid
            except Exception:
                pass

            result = scan_project(project_dir, db, attr=attr)
            self._monitor.load_scan(result)
        except FileNotFoundError:
            self._monitor.clear()
        except Exception:
            self._monitor.clear()

    def _on_sidebar_activate(self, uid: str) -> None:
        """Activate *uid* via activation_service and refresh the sidebar + monitor.

        Oracle: server.js:3844-3888 POST /api/specimen-log/activate.
        """
        project_dir = self.ctx.current_project_dir
        db = self.ctx.get_db()
        if not project_dir or not db or not uid:
            return
        try:
            from app.services.activation_service import activate as svc_activate
            svc_activate(project_dir, db, uid)
            self._sidebar.refresh()
            self._refresh_monitor()
            # Select and load the newly activated specimen
            self._sidebar.select_uid(uid)
            self._load_specimen(uid)
            self._refresh_batch_header()
        except Exception as exc:
            QMessageBox.warning(self, "激活失败", str(exc))

    def _on_sidebar_deactivate(self, uid: str) -> None:
        """Deactivate *uid* via activation_service and refresh.

        Oracle: server.js:3857-3861 (active=false path).
        """
        project_dir = self.ctx.current_project_dir
        db = self.ctx.get_db()
        if not project_dir or not db or not uid:
            return
        try:
            from app.services.activation_service import deactivate as svc_deactivate
            svc_deactivate(project_dir, db, uid)
            self._sidebar.refresh()
            self._refresh_monitor()
            self._refresh_batch_header()
        except Exception as exc:
            QMessageBox.warning(self, "去激活失败", str(exc))

    def _on_assign_jpg(self, path: str) -> None:
        """Manual attribution: assign *path* to the currently active specimen.

        Writes a manual-assign event so the attribution P2 table picks it up.
        If no specimen is active, show an informational message.

        Oracle: server.js:3891-3913 POST /api/specimen-log/assign.
        """
        project_dir = self.ctx.current_project_dir
        db = self.ctx.get_db()
        if not project_dir or not db or not path:
            return

        # Determine active specimen
        try:
            from app.services.activation_service import (
                get_active_uid,
                manual_assign,
            )
            active_uid = get_active_uid(db)
        except Exception:
            active_uid = None

        if not active_uid:
            QMessageBox.information(
                self,
                "手动归属",
                "请先激活一个标本，再手动归属 JPG。",
            )
            return

        try:
            manual_assign(project_dir, active_uid, [path])
            self._refresh_monitor()
        except Exception as exc:
            QMessageBox.warning(self, "手动归属失败", str(exc))

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
        """Compose the JPGs in the specified group via Helicon Focus CLI.

        Steps:
          1. Detect Helicon .exe (graceful failure if not found).
          2. Build output TIFF path using organize_service sequence.
          3. Call helicon_service.stack_single_subprocess with progress dialog.
          4. Update grouping DB with composedTiffPath + status="composed".

        Oracle: workbench.md, helicon.md; helicon_service.stack_single_subprocess.

        NOTE: QProcess real invocation requires a true machine with Helicon.
        """
        db = self.ctx.get_db()
        project_dir = self.ctx.current_project_dir
        if not db or not project_dir or not uid:
            return

        try:
            from app.services.grouping_service import load_grouping, save_grouping
            from app.services.helicon_service import detect_helicon, stack_single_subprocess
            from app.services.organize_service import organize_preview, build_result_basename

            grouping = load_grouping(db, uid)
            group = next(
                (g for g in grouping.groups if g.group_index == group_index), None
            )
            if group is None:
                QMessageBox.warning(self, "合成", f"找不到组 {group_index}")
                return

            if len(group.jpg_paths) < 2:
                QMessageBox.warning(
                    self, "合成", "该组 JPG 不足 2 张，无法合成。"
                )
                return

            # Check Helicon availability first
            exe = detect_helicon()
            if not exe:
                QMessageBox.warning(
                    self,
                    "未检测到 Helicon Focus",
                    "未在常见安装目录找到 Helicon Focus，请确认已安装并设置 "
                    "HELICON_FOCUS_PATH 环境变量指向可执行文件。",
                )
                return

            # Determine output path
            results_dir = os.path.join(project_dir, "results")
            os.makedirs(results_dir, exist_ok=True)
            incoming_dir = os.path.join(project_dir, "incoming-jpg")

            preview = organize_preview(db, uid, results_dir, incoming_dir)
            output_name = preview.suggested_tiff_name
            output_path = os.path.join(results_dir, output_name)

            # Progress dialog (NOTE: real subprocess is blocking; QProcess would
            # allow non-blocking — use subprocess for now as stack_single_subprocess
            # is the existing service API)
            progress = QProgressDialog(
                f"正在合成 {len(group.jpg_paths)} 张 JPG…",
                None,  # no cancel button (blocking call)
                0, 0,  # indeterminate
                self,
            )
            progress.setWindowModality(Qt.WindowModality.WindowModal)
            progress.setWindowTitle("Helicon 合成")
            progress.show()
            from PyQt6.QtWidgets import QApplication
            QApplication.processEvents()

            try:
                result = stack_single_subprocess(
                    jpg_paths=group.jpg_paths,
                    output_file=output_path,
                )
            finally:
                progress.close()

            if result.get("ok") and os.path.isfile(output_path):
                # Update grouping in DB
                from datetime import datetime, timezone
                now = datetime.now(tz=timezone.utc).isoformat()
                group.composed_tiff_path = output_path
                group.status = "composed"
                group.updated_at = now
                save_grouping(db, uid, grouping.groups)

                # Reload grouping panel
                self._grouping.load_grouping(uid, grouping)
                QMessageBox.information(
                    self,
                    "合成完成",
                    f"TIFF 已生成：{output_name}",
                )
            else:
                QMessageBox.warning(self, "合成失败", "Helicon 执行后未生成输出文件。")

        except RuntimeError as exc:
            QMessageBox.warning(self, "合成失败", str(exc))
        except Exception as exc:
            QMessageBox.warning(self, "合成失败", f"意外错误：{exc}")

    def _on_organise_requested(self, uid: str, group_index: int) -> None:
        """Organise (archive) the composed group.

        Gate checks (via organize_service._check_organize_gate):
          - uid must be active (or user explicitly bypasses)
          - group must have ≥2 JPGs
          - TIFF must already be composed

        delete_jpg is READ from settings; defaults to False (TIFF 永不删).
        Calls archive_service.archive_group (JPG→JXL→ZIP + optional delete).

        Oracle: server.js:3615-3840 organizeSpecimen; archive.js:67-190.
        """
        db = self.ctx.get_db()
        project_dir = self.ctx.current_project_dir
        if not db or not project_dir or not uid:
            return

        try:
            from app.services.grouping_service import load_grouping, save_grouping
            from app.services.organize_service import _check_organize_gate, OrganizeGateError
            from app.services.archive_service import archive_group

            grouping = load_grouping(db, uid)
            group = next(
                (g for g in grouping.groups if g.group_index == group_index), None
            )
            if group is None:
                QMessageBox.warning(self, "整理", f"找不到组 {group_index}")
                return

            if not group.composed_tiff_path:
                QMessageBox.warning(
                    self, "整理", "该组尚未合成，请先合成 TIFF 再整理。"
                )
                return

            # Gate check (uid must be active)
            try:
                groups_as_dicts = [
                    {"jpgPaths": g.jpg_paths} for g in grouping.groups
                ]
                _check_organize_gate(db, uid, groups_as_dicts)
            except OrganizeGateError as e:
                reply = QMessageBox.question(
                    self,
                    "整理确认",
                    f"{e}\n\n是否跳过激活检查继续整理？",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No,
                )
                if reply != QMessageBox.StandardButton.Yes:
                    return

            if not group.jpg_paths:
                QMessageBox.warning(self, "整理", "该组无 JPG 原片，无法归档。")
                return

            # Read delete_jpg setting (default False — TIFF 永不删，JPG 也默认保留)
            delete_jpg: bool = False
            try:
                delete_jpg = bool(
                    getattr(self.ctx.settings, "delete_jpg_after_archive", False)
                )
            except Exception:
                pass

            # Run archive
            result = archive_group(
                jpg_paths=group.jpg_paths,
                tiff_path=group.composed_tiff_path,
                project_dir=project_dir,
                delete_jpg=delete_jpg,
            )

            if result.ok:
                # Update grouping record with archive info
                from datetime import datetime, timezone
                now = datetime.now(tz=timezone.utc).isoformat()
                group.status = "organized"
                group.archive_zip = result.zip_path
                group.updated_at = now
                save_grouping(db, uid, grouping.groups)
                self._grouping.load_grouping(uid, grouping)
                self._refresh_monitor()
                self._refresh_results_column(uid, grouping)

                msg = (
                    f"归档完成：{Path(result.zip_path).name}\n"
                    f"压缩率：{result.saved_percent}%\n"
                )
                if result.delete_jpg:
                    msg += "JPG 原片已删除。"
                elif result.requested_delete_jpg and not result.delete_jpg:
                    msg += f"JPG 保留（{result.deletion_skipped_reason}）。"
                QMessageBox.information(self, "整理完成", msg)
            else:
                QMessageBox.warning(self, "整理失败", "归档过程出现错误。")

        except FileNotFoundError as exc:
            QMessageBox.warning(self, "整理失败", f"文件不存在：{exc}")
        except Exception as exc:
            QMessageBox.warning(self, "整理失败", f"意外错误：{exc}")

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

    # ── Results column ────────────────────────────────────────────────────────

    def _refresh_results_column(self, uid: str, grouping=None) -> None:
        """Populate the ② 成果内容 column from the specimen's grouping data.

        Collects all composed TIFF paths and archive ZIP paths from every group
        belonging to *uid*, then passes them to ResultsColumn.load_uid().
        """
        composed_tiffs: list[dict] = []
        archive_zips: list[dict] = []

        if grouping is not None:
            for g in grouping.groups:
                tiff_path = getattr(g, "composed_tiff_path", None)
                if tiff_path:
                    composed_tiffs.append({
                        "path": tiff_path,
                        "name": os.path.basename(tiff_path),
                    })
                zip_path = getattr(g, "archive_zip", None)
                if zip_path:
                    zip_size = 0
                    try:
                        zip_size = os.path.getsize(zip_path)
                    except OSError:
                        pass
                    archive_zips.append({
                        "path": zip_path,
                        "name": os.path.basename(zip_path),
                        "size": zip_size,
                    })

        self._results.load_uid(uid, composed_tiffs, archive_zips)

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _get_active_uid(self) -> Optional[str]:
        """Return the currently active specimen UID from the tasks table."""
        db = self.ctx.get_db()
        if not db:
            return None
        try:
            from app.services.activation_service import get_active_uid
            return get_active_uid(db)
        except Exception:
            return None

    def _show_no_project(self) -> None:
        self._sidebar.refresh()  # clears list
        self._monitor.clear()
        self._grouping.clear()
        self._results.clear()
        self._metadata.clear()
        self._refresh_header()
        self._no_project_banner.show()
