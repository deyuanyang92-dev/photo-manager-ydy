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

import json
import os
import sqlite3
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QProgressDialog,
    QPushButton,
    QScrollArea,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.workers.helicon_worker import HeliconWorker

from app.views.base_view import BaseView
from app.widgets.grouping_panel import GroupingPanel
from app.widgets.helicon_params_panel import HeliconParamsPanel
from app.widgets.metadata_panel import MetadataPanel
from app.widgets.monitor_panel import MonitorPanel
from app.widgets.naming_panel import NamingPanel
from app.widgets.results_column import ResultsColumn
from app.widgets.specimen_sidebar import SpecimenSidebar


class _BatchResultDialog(QDialog):
    """Batch retroactive archive result detail dialog.

    Shows a per-file table with status, size, and error column.
    Replaces the plain QMessageBox.information summary after batch archiving.
    """

    def __init__(self, results: list, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("批量归档结果")
        self.resize(700, 400)
        layout = QVBoxLayout(self)

        ok_count = sum(1 for r in results if r.ok)
        fail_count = len(results) - ok_count
        self._summary = QLabel(f"✓ {ok_count} 成功  ✗ {fail_count} 失败")
        layout.addWidget(self._summary)

        self._table = QTableWidget(len(results), 4)
        self._table.setHorizontalHeaderLabels(["文件名", "状态", "大小", "错误"])
        self._table.horizontalHeader().setStretchLastSection(True)
        for i, r in enumerate(results):
            self._table.setItem(i, 0, QTableWidgetItem(r.name))
            status_item = QTableWidgetItem("✓" if r.ok else "✗")
            status_item.setForeground(QColor("green" if r.ok else "red"))
            self._table.setItem(i, 1, status_item)
            size_str = f"{r.size_bytes // 1024} KB" if r.size_bytes else "-"
            self._table.setItem(i, 2, QTableWidgetItem(size_str))
            self._table.setItem(i, 3, QTableWidgetItem(r.error or ""))
        layout.addWidget(self._table)

        btn = QPushButton("关闭")
        btn.clicked.connect(self.accept)
        layout.addWidget(btn)


def _free_compose_output_name(incoming_dir: str, user_name: Optional[str]) -> str:
    """Return a unique output TIFF name for free-compose.

    If user_name is given, sanitize and use it.
    Otherwise auto-generate "自由合成-N.tif" incrementing N until no conflict.
    Oracle: app.js freeComposeSelected(), auto-naming "自由合成-N".
    """
    import re
    if user_name:
        safe = re.sub(r'[\\/:*?"<>|]', "_", user_name.strip())
        if safe and not safe.lower().endswith(".tif"):
            safe += ".tif"
        if safe and not os.path.exists(os.path.join(incoming_dir, safe)):
            return safe
    n = 1
    while True:
        candidate = f"自由合成-{n}.tif"
        if not os.path.exists(os.path.join(incoming_dir, candidate)):
            return candidate
        n += 1


class _ComposeWorkbenchDialog(QDialog):
    """Post-compose preview workspace.

    Mirrors the web compose page at a desktop scale: left source JPG checklist,
    center TIFF preview/status, right Helicon params, footer save/cancel/recompose.
    """

    ACTION_SAVE = "save"
    ACTION_CANCEL = "cancel"
    ACTION_RECOMPOSE = "recompose"

    def __init__(
        self,
        jpg_paths: list[str],
        tiff_path: str,
        params: dict,
        *,
        angle_label: str = "",
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._jpg_paths = list(jpg_paths)
        self._tiff_path = tiff_path
        self._action = self.ACTION_CANCEL
        self._checks: list[tuple[QCheckBox, str]] = []
        self._params_panel = HeliconParamsPanel()
        self._params_panel.set_params(params)
        self.setWindowTitle("合成工作台")
        self.setMinimumSize(920, 560)
        self._build_ui(angle_label)

    def _build_ui(self, angle_label: str) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 14)
        root.setSpacing(12)

        head = QHBoxLayout()
        title = QLabel("合成工作台")
        title.setStyleSheet("font-size: 17px; font-weight: 700; color: #eef3ef;")
        head.addWidget(title)
        if angle_label:
            badge = QLabel(angle_label)
            badge.setStyleSheet(
                "color:#29b9ab; border:1px solid rgba(41,185,171,0.35);"
                " border-radius:5px; padding:2px 8px; font-size:12px;"
            )
            head.addWidget(badge)
        fname = QLabel(Path(self._tiff_path).name)
        fname.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        fname.setStyleSheet("color:#87a2a1; font-size:12px;")
        head.addWidget(fname, 1)
        root.addLayout(head)

        body = QSplitter(Qt.Orientation.Horizontal)
        body.setChildrenCollapsible(False)
        body.setHandleWidth(12)

        sources = QFrame()
        sources.setObjectName("Panel")
        src_lay = QVBoxLayout(sources)
        src_lay.setContentsMargins(12, 12, 12, 12)
        src_lay.setSpacing(8)
        src_lay.addWidget(QLabel("源图"))
        src_list = QListWidget()
        src_list.setAlternatingRowColors(True)
        for path in self._jpg_paths:
            item = QListWidgetItem(src_list)
            cb = QCheckBox(Path(path).name)
            cb.setChecked(True)
            cb.setToolTip(path)
            self._checks.append((cb, path))
            src_list.setItemWidget(item, cb)
            item.setSizeHint(cb.sizeHint())
        src_lay.addWidget(src_list, 1)
        body.addWidget(sources)

        preview = QFrame()
        preview.setObjectName("Panel")
        pv_lay = QVBoxLayout(preview)
        pv_lay.setContentsMargins(16, 16, 16, 16)
        pv_lay.setSpacing(10)
        pv_title = QLabel("TIFF 预览")
        pv_title.setStyleSheet("font-size: 13px; font-weight: 700; color:#eef3ef;")
        pv_lay.addWidget(pv_title)
        status = QLabel()
        if os.path.isfile(self._tiff_path):
            size_mb = os.path.getsize(self._tiff_path) / (1024 * 1024)
            status.setText(f"已生成 TIFF\n{self._tiff_path}\n\n大小：{size_mb:.1f} MB")
        else:
            status.setText(f"未找到 TIFF\n{self._tiff_path}")
        status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        status.setWordWrap(True)
        status.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        status.setStyleSheet(
            "color:#87a2a1; background:#061c1e; border:1px dashed rgba(145,182,181,0.22);"
            " border-radius:8px; padding:28px; font-size:12px;"
        )
        pv_lay.addWidget(status, 1)
        hint = QLabel("调整右侧参数后可重合成预览；保存后写入当前分组结果。")
        hint.setWordWrap(True)
        hint.setStyleSheet("color:#5f7d7a; font-size:11px;")
        pv_lay.addWidget(hint)
        body.addWidget(preview)

        body.addWidget(self._params_panel)
        body.setSizes([240, 460, 220])
        root.addWidget(body, 1)

        foot = QHBoxLayout()
        foot.addStretch()
        cancel = QPushButton("取消（退回 TIFF）")
        cancel.setObjectName("Outline")
        cancel.clicked.connect(self._cancel)
        foot.addWidget(cancel)
        recompose = QPushButton("重合成预览")
        recompose.setObjectName("Outline")
        recompose.clicked.connect(self._recompose)
        foot.addWidget(recompose)
        save = QPushButton("保存到结果")
        save.setObjectName("Primary")
        save.clicked.connect(self._save)
        foot.addWidget(save)
        root.addLayout(foot)

    def selected_jpgs(self) -> list[str]:
        return [path for cb, path in self._checks if cb.isChecked()]

    def params(self) -> dict:
        return self._params_panel.get_params()

    def action(self) -> str:
        return self._action

    def _save(self) -> None:
        self._action = self.ACTION_SAVE
        self.accept()

    def _cancel(self) -> None:
        self._action = self.ACTION_CANCEL
        self.reject()

    def _recompose(self) -> None:
        self._action = self.ACTION_RECOMPOSE
        self.accept()


class _RetroactiveScanDialog(QDialog):
    """Pre-scan dialog: choose results/ subdirectory before retroactive scan.

    Presents a combo populated with subdirectories of project results/.
    '全部' (data=None) scans the whole results/ tree; a named entry restricts
    the scan to results/<subdir>/.
    """

    def __init__(self, project_dir: str, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("存量整理 — 选择扫描范围")
        self._project_dir = project_dir
        self._build_ui()

    def _build_ui(self) -> None:
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 16, 16, 16)
        lay.setSpacing(12)

        lay.addWidget(QLabel("子目录："))
        self._subdir_combo = QComboBox()
        self._subdir_combo.addItem("全部", None)
        results_dir = Path(self._project_dir) / "results"
        if results_dir.exists():
            for d in sorted(results_dir.iterdir()):
                if d.is_dir():
                    self._subdir_combo.addItem(d.name, d.name)
        lay.addWidget(self._subdir_combo)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        ok_btn = btns.button(QDialogButtonBox.StandardButton.Ok)
        if ok_btn:
            ok_btn.setText("开始扫描")
        cancel_btn = btns.button(QDialogButtonBox.StandardButton.Cancel)
        if cancel_btn:
            cancel_btn.setText("取消")
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        lay.addWidget(btns)

    def selected_subdir(self) -> Optional[str]:
        """Return the selected subdirectory name, or None for 全部."""
        return self._subdir_combo.currentData()


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
        self._sidebar.collab_manager_requested.connect(self._on_open_collab_manager)
        self._sidebar.print_labels_requested.connect(self._on_print_labels)
        outer.addWidget(self._sidebar)

        # Wire collab service signals → sidebar strip refresh
        svc = getattr(self.ctx, "collab_service", None)
        if svc is not None:
            svc.peers_changed.connect(
                lambda: self._sidebar.update_collab_status(svc)
            )
            svc.tasks_changed.connect(
                lambda: self._sidebar.update_collab_status(svc)
            )
            svc.server_ready.connect(
                lambda _port: self._sidebar.update_collab_status(svc)
            )
        self._sidebar.update_collab_status(svc)

        # ── Centre ①: vertical splitter (monitor top, grouping bottom) ───────
        centre = QSplitter(Qt.Orientation.Vertical)
        centre.setChildrenCollapsible(False)
        centre.setHandleWidth(18)

        self._monitor = MonitorPanel(self.ctx)
        self._monitor.refresh_requested.connect(self._refresh_monitor)
        self._monitor.assign_requested.connect(self._on_assign_jpg)
        self._monitor.unassign_requested.connect(self._on_unassign_jpg)
        self._monitor.add_jpg_requested.connect(self._on_add_jpg_files)
        centre.addWidget(self._monitor)

        self._grouping = GroupingPanel(self.ctx)
        self._grouping.compose_requested.connect(self._on_compose_requested)
        self._grouping.organise_requested.connect(self._on_organise_requested)
        self._grouping.undo_compose_requested.connect(self._on_undo_compose)
        self._grouping.grouping_changed.connect(self._on_grouping_changed)
        self._grouping.add_selection_to_group_requested.connect(self._on_add_selection_to_group)
        self._grouping.free_compose_requested.connect(self._on_free_compose)
        self._grouping.retroactive_requested.connect(self._on_retroactive_scan)
        self._grouping.import_tiff_requested.connect(self._on_import_tiff)  # #cursor
        centre.addWidget(self._grouping)

        centre.setSizes([300, 250])
        outer.addWidget(centre)

        # ── Centre ②: 成果内容 column (composed TIFFs + archive ZIPs) ──────
        self._results = ResultsColumn()
        self._results.setMinimumWidth(200)
        outer.addWidget(self._results)

        # ── Right: naming + metadata (scrollable — never compress/overlap) ──
        right = QWidget()
        right_lay = QVBoxLayout(right)
        right_lay.setContentsMargins(0, 0, 6, 0)
        right_lay.setSpacing(18)

        self._naming = NamingPanel(self.ctx)
        self._naming.save_requested.connect(self._on_naming_save)
        self._naming.uid_corrected.connect(self._on_uid_corrected)
        right_lay.addWidget(self._naming)           # natural height, no compress

        self._helicon_params = HeliconParamsPanel()
        right_lay.addWidget(self._helicon_params)

        self._metadata = MetadataPanel(self.ctx)
        self._metadata.save_requested.connect(self._on_save_metadata)
        right_lay.addWidget(self._metadata)
        right_lay.addStretch(1)

        right_scroll = QScrollArea()
        right_scroll.setObjectName("ColumnScroll")
        right_scroll.setWidget(right)
        right_scroll.setWidgetResizable(True)
        right_scroll.setFrameShape(QFrame.Shape.NoFrame)
        right_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        right_scroll.setMinimumWidth(320)
        outer.addWidget(right_scroll)

        # Initial splitter proportions: sidebar : capture : results : right-panel
        outer.setSizes([220, 480, 240, 320])
        body_lay.addWidget(outer, stretch=1)

        # ── Project settings drawer (overlay, hidden by default) ────────────
        from app.widgets.project_settings_drawer import ProjectSettingsDrawer
        self._settings_drawer = ProjectSettingsDrawer(self.ctx, parent=self)
        self._settings_drawer.setFixedWidth(380)

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

        # Auto-refresh monitor directory every 2 s (mirrors web startMonitorPoll)
        self._auto_refresh_timer = QTimer(self)
        self._auto_refresh_timer.setInterval(2000)
        self._auto_refresh_timer.timeout.connect(self._refresh_monitor)

        # Track current UID for grouping edits
        self._current_uid: Optional[str] = None
        self._pending_grouping = None  # SpecimenGrouping awaiting save
        self.ctx.worms_fill_specimen = self.worms_fill_specimen

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
        settings_btn = QPushButton("⚙ 设置")
        settings_btn.setObjectName("Ghost")
        settings_btn.setFixedHeight(28)
        settings_btn.clicked.connect(self._on_open_settings)
        row.addWidget(settings_btn)
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

        # Start auto-poll (mirrors web startMonitorPoll)
        if not self._auto_refresh_timer.isActive():
            self._auto_refresh_timer.start()

    def on_deactivate(self) -> None:
        """Called when navigating away; stop auto-poll."""
        self._auto_refresh_timer.stop()

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

    def _on_print_labels(self, uid: str) -> None:
        """Open the labels page with exactly *uid* selected."""
        if not uid:
            return
        self.ctx.pending_label_uid = uid
        win = self.window()
        nav = getattr(win, "navigate_to", None)
        if callable(nav):
            nav("labels")
            labels_view = getattr(win, "_views", {}).get("labels")
            selector = getattr(labels_view, "select_uid", None)
            if callable(selector):
                selector(uid)

    def _on_uid_corrected(self, old_uid: str, new_uid: str) -> None:
        """Handle UID change after storage correction in NamingPanel.

        Updates _current_uid and refreshes the sidebar.
        """
        if self._current_uid == old_uid:
            self._current_uid = new_uid
        self._sidebar.refresh()
        if new_uid:
            self._sidebar.select_uid(new_uid)

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
                sp_dict = sp.raw or {
                    "province": sp.province,
                    "site": sp.site,
                    "station": sp.station,
                    "id": sp.id,
                    "storage": sp.storage,
                    "collection_date": sp.collection_date,
                    "photo_date": sp.photo_date,
                }
                sp_dict["uid"] = sp.uid
                self._naming.load_specimen(sp_dict)
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

    def _on_add_selection_to_group(self, group_index: int) -> None:
        """Resolve monitor selection and add JPGs to the specified group.

        Oracle: app.js groupingAddSelectedToGroup() app.js:5258–5271.
        """
        jpg_paths = self._monitor.selected_jpg_paths()
        if not jpg_paths:
            QMessageBox.information(self, "加入分组", "请先在上方监控区选中要入组的 JPG。")
            return
        self._on_add_to_group(group_index, jpg_paths)
        self._monitor._on_select_none()

    def _on_add_to_group(self, group_index: int, jpg_paths: list[str]) -> None:
        """Add selected monitor JPGs to the specified grouping group."""
        self._grouping.add_jpgs_to_group(group_index, jpg_paths)
        # Also mark those paths as manually assigned to the current uid
        uid = self._current_uid
        project_dir = self.ctx.current_project_dir
        if uid and project_dir and jpg_paths:
            try:
                from app.services.activation_service import manual_assign
                manual_assign(project_dir, uid, jpg_paths)
            except Exception:
                pass

    def _on_add_jpg_files(self) -> None:
        """Open file picker for JPGs → copy to incoming-jpg/.

        Oracle: app.js importJpgFiles() app.js:7944–7975.
        """
        project_dir = self.ctx.current_project_dir
        if not project_dir:
            QMessageBox.information(self, "添加照片", "请先打开一个项目。")
            return

        from PyQt6.QtWidgets import QFileDialog
        paths, _ = QFileDialog.getOpenFileNames(
            self, "选择 JPG 照片",
            filter="JPG 照片 (*.jpg *.jpeg *.JPG *.JPEG)",
        )
        if not paths:
            return

        incoming_dir = os.path.join(project_dir, "incoming-jpg")
        os.makedirs(incoming_dir, exist_ok=True)
        import shutil
        errors = []
        for src in paths:
            dest = os.path.join(incoming_dir, os.path.basename(src))
            try:
                if os.path.abspath(src) != os.path.abspath(dest):
                    shutil.copy2(src, dest)
            except OSError as e:
                errors.append(str(e))

        if errors:
            QMessageBox.warning(self, "导入部分失败", "\n".join(errors[:5]))
        self._refresh_monitor()

    def _on_free_compose(self) -> None:
        """Free compose: selected monitor JPGs → Helicon → incoming-jpg/.

        Stub — full implementation in Task 7.
        Oracle: app.js freeComposeSelected() app.js:7982–8010.
        """
        project_dir = self.ctx.current_project_dir
        if not project_dir:
            QMessageBox.information(self, "无号合成", "请先打开一个项目。")
            return

        jpg_paths = self._monitor.selected_jpg_paths()
        if not jpg_paths:
            QMessageBox.information(self, "无号合成", "请先在监控区选中要合成的 JPG。")
            return

        from app.services.helicon_service import detect_helicon
        exe = detect_helicon()
        if not exe:
            QMessageBox.warning(self, "未检测到 Helicon Focus",
                                "请确认 Helicon Focus 已安装并配置路径。")
            return

        from PyQt6.QtWidgets import QInputDialog
        user_name, ok = QInputDialog.getText(
            self, "无号合成", "输出文件名（留空自动命名）：", text=""
        )
        if not ok:
            return

        incoming_dir = os.path.join(project_dir, "incoming-jpg")
        os.makedirs(incoming_dir, exist_ok=True)
        output_name = _free_compose_output_name(incoming_dir, user_name.strip() or None)
        output_path = os.path.join(incoming_dir, output_name)

        params = self._helicon_params.get_params()

        def _on_finished(tiff_path):
            if os.path.isfile(output_path):
                QMessageBox.information(self, "无号合成完成",
                                        f"TIFF 已保存到 incoming-jpg/：\n{output_name}")
                self._refresh_monitor()
            else:
                QMessageBox.warning(self, "无号合成失败", "Helicon 执行后未生成输出文件。")

        def _on_failed(msg: str):
            if msg != "用户取消":
                QMessageBox.warning(self, "无号合成失败", msg)

        self._run_helicon_stack(jpg_paths, output_path, params, _on_finished, _on_failed)

    def _on_retroactive_scan(self) -> None:
        """Launch retroactive organize modal.

        Oracle: app.js retroactiveScan() + renderRetroactiveModal().
        """
        project_dir = self.ctx.current_project_dir
        db = self.ctx.get_db()
        if not project_dir or not db:
            QMessageBox.information(self, "存量整理", "请先打开一个项目。")
            return

        pre = _RetroactiveScanDialog(project_dir, parent=self)
        if pre.exec() != QDialog.DialogCode.Accepted:
            return
        selected_subdir = pre.selected_subdir()

        try:
            from app.services.retroactive_service import scan_project_retroactive
            result = scan_project_retroactive(project_dir, db, subdir=selected_subdir)
        except Exception as exc:
            QMessageBox.warning(self, "扫描失败", str(exc))
            return

        total_groups = sum(len(sp["groups"]) for sp in result.get("specimens", []))
        if not total_groups and not result.get("unnamedTiffs"):
            QMessageBox.information(
                self, "存量整理",
                "没找到可整理的 TIF 成片（需 results/ 里有按编号命名的 TIF）。"
            )
            return

        from app.widgets.retroactive_modal import RetroactiveModal
        dlg = RetroactiveModal(self.ctx, result, parent=self)
        if dlg.exec() == RetroactiveModal.DialogCode.Accepted:
            self._refresh_monitor()

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
            from app.services.helicon_service import detect_helicon
            from app.services.organize_service import organize_preview, build_result_basename

            grouping = load_grouping(db, uid)
            group = next(
                (g for g in grouping.groups if g.group_index == group_index), None
            )
            if group is None:
                QMessageBox.warning(self, "合成", f"找不到组 {group_index}")
                return

            if len(group.jpg_paths) < 2:
                # ── Implicit-batch fallback  #cursor ─────────────────────────
                # Mirrors web composeImplicitActiveBatch() app.js:5660–5706.
                # If group is empty/insufficient, offer to use all JPGs
                # currently attributed to this specimen in the monitor scan.
                attributed_paths = self._get_attributed_jpg_paths(uid)
                if len(attributed_paths) >= 2:
                    reply = QMessageBox.question(
                        self,
                        "该组 JPG 不足",
                        f"该分组 JPG 不足 2 张，但检测到 {len(attributed_paths)} 张"
                        f" 已归属到此标本的 JPG。\n\n"
                        "是否用这些照片作为隐式批次执行合成？",
                        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                        QMessageBox.StandardButton.No,
                    )
                    if reply != QMessageBox.StandardButton.Yes:
                        return
                    group.jpg_paths = attributed_paths
                else:
                    QMessageBox.warning(
                        self, "合成", "该组 JPG 不足 2 张，无法合成。"
                    )
                    return

            # ── Pre-compose preview dialog  #cursor renderComposePreviewModal ─
            # Mirrors web renderComposePreviewModal() app.js:6597.
            # Shows JPG list so user can confirm / deselect before Helicon runs.
            selected_jpgs = self._show_compose_preview(group.jpg_paths)
            if selected_jpgs is None:
                return  # User cancelled
            if len(selected_jpgs) < 2:
                QMessageBox.warning(self, "合成", "选中的 JPG 不足 2 张，无法合成。")
                return
            group.jpg_paths = selected_jpgs

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

            params = self._helicon_params.get_params()

            def _do_compose(jpg_paths, out_path, cur_params):
                def _on_finished(tiff_path):
                    if not os.path.isfile(out_path):
                        QMessageBox.warning(self, "合成失败", "Helicon 执行后未生成输出文件。")
                        return
                    dlg = _ComposeWorkbenchDialog(
                        jpg_paths,
                        out_path,
                        cur_params,
                        angle_label=group.angle_label,
                        parent=self,
                    )
                    dlg.exec()
                    action = dlg.action()
                    new_params = dlg.params()
                    self._helicon_params.set_params(new_params)

                    if action == _ComposeWorkbenchDialog.ACTION_RECOMPOSE:
                        selected = dlg.selected_jpgs()
                        if len(selected) < 2:
                            QMessageBox.warning(self, "合成", "选中的 JPG 不足 2 张，无法重合成。")
                            return
                        self._retire_tiff(out_path)
                        group.jpg_paths = selected
                        _do_compose(selected, out_path, new_params)
                        return

                    if action == _ComposeWorkbenchDialog.ACTION_CANCEL:
                        self._retire_tiff(out_path)
                        return

                    # Save to result: persist grouping only after preview approval.
                    from datetime import datetime, timezone
                    now = datetime.now(tz=timezone.utc).isoformat()
                    group.composed_tiff_path = out_path
                    group.status = "composed"
                    group.updated_at = now
                    group.result_sequence = preview.next_seq
                    save_grouping(db, uid, grouping.groups)

                    try:
                        from app.services.organize_service import _bump_seq_hint
                        _bump_seq_hint(db, uid, preview.next_seq)
                    except Exception:
                        pass

                    self._grouping.load_grouping(uid, grouping)
                    self._refresh_results_column(uid, grouping)
                    self._on_helicon_finished(uid)
                    QMessageBox.information(self, "合成完成", f"TIFF 已生成：{output_name}")

                def _on_failed(msg: str):
                    if msg != "用户取消":
                        QMessageBox.warning(self, "合成失败", msg)

                self._run_helicon_stack(jpg_paths, out_path, cur_params, _on_finished, _on_failed)

            _do_compose(group.jpg_paths, output_path, params)

        except RuntimeError as exc:
            QMessageBox.warning(self, "合成失败", str(exc))
        except Exception as exc:
            QMessageBox.warning(self, "合成失败", f"意外错误：{exc}")

    def _run_helicon_stack(
        self,
        jpg_paths: list[str],
        output_path: str,
        params: dict,
        on_finished,
        on_failed,
    ) -> None:
        """Launch HeliconWorker (non-blocking) with a cancellable progress dialog.

        *on_finished(tiff_path: Path)* is called on success.
        *on_failed(msg: str)* is called on error or cancel.
        """
        from app.services.helicon_service import build_helicon_cmd

        try:
            cmd = build_helicon_cmd(
                jpg_paths=jpg_paths,
                output_file=output_path,
                method=str(params["method"]),
                radius=str(params["radius"]),
                smoothing=str(params["smoothing"]),
            )
        except RuntimeError as exc:
            on_failed(str(exc))
            return

        progress = QProgressDialog(
            f"正在合成 {len(jpg_paths)} 张 JPG…",
            "取消",
            0,
            0,
            self,
        )
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setWindowTitle("Helicon 合成")

        worker = HeliconWorker(cmd=cmd, output_path=output_path, parent=self)
        self._helicon_worker = worker  # keep reference alive

        def _on_done(tiff_path):
            progress.close()
            on_finished(tiff_path)

        def _on_fail(msg: str):
            progress.close()
            on_failed(msg)

        def _on_cancel():
            worker.cancel()
            on_failed("用户取消")

        worker.finished.connect(_on_done)
        worker.failed.connect(_on_fail)
        progress.canceled.connect(_on_cancel)

        progress.show()
        worker.start()
        self._helicon_progress = progress  # keep reference alive

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

            # ── Collision guard: if ZIP already exists at the target path,  #cursor
            #    warn and let user choose overwrite / skip. ──────────────
            tiff_stem = Path(group.composed_tiff_path).stem
            results_dir_for_check = str(Path(project_dir) / "results")
            existing_zip = os.path.join(results_dir_for_check, tiff_stem + ".zip")
            if os.path.isfile(existing_zip):
                reply_col = QMessageBox.question(
                    self,
                    "归档文件已存在",
                    f"同名归档 ZIP 已存在：\n{Path(existing_zip).name}\n\n"
                    "是否覆盖并重新归档？",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No,
                )
                if reply_col != QMessageBox.StandardButton.Yes:
                    return

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
                self._on_organize_finished(uid)

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

    def _on_import_tiff(self, uid: str, group_index: int) -> None:
        """Persist the imported TIFF association from grouping panel to DB.

        Called after grouping_panel._on_import_tiff successfully updated the
        in-memory grouping.  Flushes the updated grouping to DB and refreshes
        the results column.

        Oracle: app.js groupingImportTiff() app.js:6057.
        """
        db = self.ctx.get_db()
        if not db or not uid:
            return
        try:
            from app.services.grouping_service import save_grouping
            grouping = getattr(self._grouping, "_grouping", None)
            if grouping:
                save_grouping(db, uid, grouping.groups)
                self._refresh_results_column(uid, grouping)
                self._refresh_monitor()
        except Exception as exc:
            QMessageBox.warning(self, "导入 TIFF", f"保存失败：{exc}")

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

    # ── WoRMS fill hook ───────────────────────────────────────────────────────

    def worms_fill_specimen(self, rec: dict) -> str:
        """Fill current specimen with WoRMS Latin taxonomy fields.

        Mirrors web ``wormsFillToSpecimen``: Latin class/order/family/genus/
        species are updated, ``taxonomyConfirmed`` is reset in raw_json, and
        Chinese fields are left untouched.
        """
        uid = self._current_uid or self._get_active_uid()
        if not uid:
            raise RuntimeError("需先在工作区选择或激活标本")
        db = self.ctx.get_db()
        if not db:
            raise RuntimeError("请先打开项目工作区")

        row = db.execute("SELECT * FROM specimens WHERE uid = ?", (uid,)).fetchone()
        if row is None:
            raise RuntimeError(f"当前标本不存在: {uid}")

        try:
            raw = json.loads(row["raw_json"]) if row["raw_json"] else {}
            if not isinstance(raw, dict):
                raw = {}
        except Exception:
            raw = {}

        from app.services.worms_service import WormsService
        raw = WormsService.merge_worms_into_record(raw, rec)
        if rec.get("class"):
            raw["taxonGroup"] = rec["class"]
        if rec.get("order"):
            raw["order"] = rec["order"]
        if rec.get("family"):
            raw["family"] = rec["family"]
        if rec.get("genus"):
            raw["genus"] = rec["genus"]
        if rec.get("scientificname"):
            raw["scientificName"] = rec["scientificname"]
        raw["taxonomyConfirmed"] = False

        db.execute(
            """
            UPDATE specimens
            SET taxon_group = ?, order_name = ?, family = ?, genus = ?,
                scientific_name = ?, raw_json = ?
            WHERE uid = ?
            """,
            (
                rec.get("class") or row["taxon_group"],
                rec.get("order") or row["order_name"],
                rec.get("family") or row["family"],
                rec.get("genus") or row["genus"],
                rec.get("scientificname") or row["scientific_name"],
                json.dumps(raw, ensure_ascii=False),
                uid,
            ),
        )
        db.commit()
        self._load_specimen(uid)
        return uid

    # ── Collab photo-index hooks ──────────────────────────────────────────────

    def _on_helicon_finished(self, uid: str) -> None:
        """Broadcast tiff photo-index to collab peers (oracle: collabPostPhotoIndex)."""
        svc = getattr(self.ctx, "collab_service", None)
        if svc is not None:
            try:
                svc.post_photo_index(uid, "tiff")
            except Exception:
                pass

    def _on_organize_finished(self, uid: str) -> None:
        """Broadcast zip photo-index to collab peers (oracle: collabPostPhotoIndex)."""
        svc = getattr(self.ctx, "collab_service", None)
        if svc is not None:
            try:
                svc.post_photo_index(uid, "zip")
            except Exception:
                pass

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

    def _get_attributed_jpg_paths(self, uid: str) -> list[str]:
        """Return paths of all JPGs currently attributed to *uid* in the monitor.

        Used as implicit-batch fallback when a group has < 2 JPGs.
        Mirrors web composeImplicitActiveBatch() app.js:5660–5706.
        """
        project_dir = self.ctx.current_project_dir
        db = self.ctx.get_db()
        if not project_dir or not db:
            return []
        try:
            from app.services.monitor_service import scan_project
            from app.services.grouping_service import get_explicit_unassigns
            from app.services.activation_service import read_activations
            import json as _json

            attr = read_activations(project_dir)
            try:
                attr.explicit_unassigns = get_explicit_unassigns(db)
            except Exception:
                pass
            try:
                rows = db.execute("SELECT uid, jpg_paths FROM grouping").fetchall()
                for row in rows:
                    row_uid = row[0]
                    paths = _json.loads(row[1] or "[]")
                    for p in paths:
                        attr.path_to_uid[str(Path(p).resolve())] = row_uid
            except Exception:
                pass

            result = scan_project(project_dir, db, attr=attr)
            return [
                f.path for f in result.jpg_files
                if f.attributed_specimen_id == uid and f.path
            ]
        except Exception:
            return []

    def _show_compose_preview(self, jpg_paths: list[str]) -> Optional[list[str]]:
        """Pre-compose JPG checklist dialog.

        Mirrors web renderComposePreviewModal() app.js:6597 — simplified Qt
        version: shows all JPG filenames as a checkable list so the user can
        confirm or deselect files before Helicon runs.

        Returns:
            list[str]: selected (checked) JPG paths if user confirms.
            None:      if the user cancelled.
        """
        from PyQt6.QtWidgets import (
            QCheckBox,
            QDialog,
            QDialogButtonBox,
            QScrollArea,
        )

        dlg = QDialog(self)
        dlg.setWindowTitle("合成预览 — 确认原片")
        dlg.setMinimumWidth(460)

        root_lay = QVBoxLayout(dlg)
        root_lay.setContentsMargins(16, 16, 16, 16)
        root_lay.setSpacing(10)

        info = QLabel(
            f"即将合成 {len(jpg_paths)} 张 JPG。\n取消勾选可从本次合成中排除："
        )
        info.setObjectName("Muted")
        info.setWordWrap(True)
        root_lay.addWidget(info)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setMaximumHeight(260)
        inner = QWidget()
        inner_lay = QVBoxLayout(inner)
        inner_lay.setContentsMargins(4, 4, 4, 4)
        inner_lay.setSpacing(4)

        checkboxes: list[tuple[QCheckBox, str]] = []
        for p in jpg_paths:
            cb = QCheckBox(Path(p).name)
            cb.setChecked(True)
            cb.setToolTip(p)
            inner_lay.addWidget(cb)
            checkboxes.append((cb, p))
        inner_lay.addStretch()
        scroll.setWidget(inner)
        root_lay.addWidget(scroll)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        ok_btn = btns.button(QDialogButtonBox.StandardButton.Ok)
        if ok_btn:
            ok_btn.setText("✓ 开始合成")
        cancel_btn = btns.button(QDialogButtonBox.StandardButton.Cancel)
        if cancel_btn:
            cancel_btn.setText("取消")
        btns.accepted.connect(dlg.accept)
        btns.rejected.connect(dlg.reject)
        root_lay.addWidget(btns)

        # Center on parent screen (dual-monitor safe)
        try:
            from app.utils.ui import center_on
            center_on(dlg, self)
        except Exception:
            pass

        if dlg.exec() != QDialog.DialogCode.Accepted:
            return None

        return [path for cb, path in checkboxes if cb.isChecked()]

    def _on_open_collab_manager(self) -> None:
        """Open the CollabManagerDialog from the sidebar 'collab_manager_requested' signal."""
        from app.widgets.collab_manager_dialog import CollabManagerDialog
        svc = getattr(self.ctx, "collab_service", None)
        dlg = CollabManagerDialog(svc, parent=self)
        dlg.exec()

    def _on_open_settings(self) -> None:
        """Show project settings drawer positioned at the right edge."""
        self._settings_drawer.refresh()
        self._settings_drawer.show()
        # Position at right edge of this widget
        try:
            win_rect = self.rect()
            dw = self._settings_drawer
            dw.setGeometry(
                win_rect.right() - dw.width(), 0,
                dw.width(), win_rect.height()
            )
        except Exception:
            pass

    def _show_no_project(self) -> None:
        self._sidebar.refresh()  # clears list
        self._monitor.clear()
        self._grouping.clear()
        self._results.clear()
        self._metadata.clear()
        self._refresh_header()
        self._no_project_banner.show()
