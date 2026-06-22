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

from PyQt6.QtCore import Qt, QFileSystemWatcher, QTimer
from PyQt6.QtGui import QColor, QPainter, QPixmap
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

from app.config import icons
from app.config.theme import TOKENS
from app.utils import ui
from app.views.base_view import BaseView
from app.widgets.grouping_panel import GroupingPanel
from app.widgets.helicon_params_panel import HeliconParamsPanel
from app.widgets.metadata_panel import MetadataPanel
from app.widgets.monitor_panel import MonitorPanel
from app.widgets.naming_panel import NamingPanel
from app.widgets.results_column import ResultsColumn
from app.widgets.taxon_card_panel import TaxonCardPanel
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


class _RnaQueueDialog(QDialog):
    """RNAlater sheet queue preview with explicit print/clear actions."""

    def __init__(self, uids: list[str], job: dict, grid_opts: dict, parent=None) -> None:
        super().__init__(parent)
        self.action: str = ""
        self.setWindowTitle("RNAlater 合版队列")
        self.resize(880, 620)

        root = QHBoxLayout(self)
        left = QVBoxLayout()
        root.addLayout(left, 0)

        title = QLabel(f"待打印 RNAlater 标签：{len(uids)}")
        title.setStyleSheet("font-size:15px;font-weight:700;")
        left.addWidget(title)

        self._list = QListWidget()
        self._list.setMinimumWidth(300)
        for uid in uids:
            self._list.addItem(uid)
        left.addWidget(self._list, 1)

        hint = QLabel("确认排版后打印；清空只会移出待打印队列，不删除标本记录。")
        hint.setWordWrap(True)
        hint.setObjectName("MutedSmall")
        left.addWidget(hint)

        btn_row = QHBoxLayout()
        print_btn = QPushButton("打印合版")
        print_btn.setObjectName("Primary")
        clear_btn = QPushButton("清空队列")
        clear_btn.setObjectName("Outline")
        close_btn = QPushButton("关闭")
        print_btn.clicked.connect(lambda: self._finish("print"))
        clear_btn.clicked.connect(lambda: self._finish("clear"))
        close_btn.clicked.connect(self.reject)
        btn_row.addWidget(print_btn)
        btn_row.addWidget(clear_btn)
        btn_row.addWidget(close_btn)
        left.addLayout(btn_row)

        preview_box = QVBoxLayout()
        root.addLayout(preview_box, 1)
        pv_title = QLabel("合版预览")
        pv_title.setStyleSheet("font-size:13px;font-weight:700;")
        preview_box.addWidget(pv_title)
        self._preview = QLabel()
        self._preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._preview.setMinimumSize(500, 560)
        self._preview.setStyleSheet("background:#ffffff;border:1px solid #c8d0d8;")
        preview_box.addWidget(self._preview, 1)

        self._render_preview(job, grid_opts)

    def _finish(self, action: str) -> None:
        self.action = action
        self.accept()

    def _render_preview(self, job: dict, grid_opts: dict) -> None:
        from app.utils.label_sheet import compute_sheet_geometry, paint_sheet_page

        w, h = 520, 560
        pm = QPixmap(w, h)
        pm.fill(QColor("#f3f6f8"))
        painter = QPainter(pm)
        try:
            paper_type = job.get("paperType") or "a4"
            geom = compute_sheet_geometry(
                job.get("dims") or {},
                paper_type,
                job.get("paper"),
                grid_opts,
                w,
                h,
            )
            info = paint_sheet_page(
                painter,
                job,
                grid_opts,
                0,
                geom,
                cut_marks=bool(grid_opts.get("cutMarks", True)),
            )
            painter.setPen(QColor("#475569"))
            painter.drawText(
                14,
                h - 14,
                f"{paper_type.upper()} · 每页 {info.get('per_page', 0)} 个 · 共 {info.get('total_pages', 1)} 页",
            )
        finally:
            painter.end()
        self._preview.setPixmap(pm)


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
        t = TOKENS
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 14)
        root.setSpacing(12)

        head = QHBoxLayout()
        title = QLabel("合成工作台")
        title.setStyleSheet(f"font-size: 17px; font-weight: 700; color: {t['text']};")
        head.addWidget(title)
        if angle_label:
            badge = QLabel(angle_label)
            badge.setStyleSheet(
                f"color:{t['accent']}; border:1px solid {t['accent_glow']};"
                " border-radius:5px; padding:2px 8px; font-size:12px;"
            )
            head.addWidget(badge)
        fname = QLabel(Path(self._tiff_path).name)
        fname.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        fname.setStyleSheet(f"color:{t['muted']}; font-size:12px;")
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
        pv_title.setStyleSheet(f"font-size: 13px; font-weight: 700; color:{t['text']};")
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
            f"color:{t['muted']}; background:{t['panel_inset']}; border:1px dashed {t['border_medium']};"
            " border-radius:8px; padding:28px; font-size:12px;"
        )
        pv_lay.addWidget(status, 1)
        hint = QLabel("调整右侧参数后可重合成预览；保存后写入当前分组结果。")
        hint.setWordWrap(True)
        hint.setStyleSheet(f"color:{t['muted_dim']}; font-size:11px;")
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

    def __init__(self, project_dir: str, parent=None, results_subdir: str = "results") -> None:
        super().__init__(parent)
        self.setWindowTitle("存量整理 — 选择扫描范围")
        self._project_dir = project_dir
        self._results_subdir = results_subdir or "results"
        self._build_ui()

    def _build_ui(self) -> None:
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 16, 16, 16)
        lay.setSpacing(12)

        lay.addWidget(QLabel("子目录："))
        self._subdir_combo = QComboBox()
        self._subdir_combo.addItem("全部", None)
        results_dir = Path(self._project_dir) / self._results_subdir
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


class _DrawerScrim(QWidget):
    """Dimmed backdrop behind the settings drawer; click anywhere to dismiss."""

    def __init__(self, on_click, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("DrawerScrim")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._on_click = on_click
        self.hide()

    def mousePressEvent(self, event) -> None:  # noqa: N802 (Qt override)
        self._on_click()


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
        body_lay.setContentsMargins(24, 18, 24, 18)
        body_lay.setSpacing(14)
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
        outer.setHandleWidth(14)

        # ── Left: specimen sidebar ─────────────────────────────────────────
        self._sidebar = SpecimenSidebar(self.ctx)
        self._sidebar.setMinimumWidth(250)
        self._sidebar.setMaximumWidth(330)
        self._sidebar.specimen_selected.connect(self._on_specimen_selected)
        self._sidebar.activate_requested.connect(self._on_sidebar_activate)
        self._sidebar.deactivate_requested.connect(self._on_sidebar_deactivate)
        self._sidebar.new_specimen_requested.connect(self._on_new_specimen)
        self._sidebar.collab_manager_requested.connect(self._on_open_collab_panel)
        self._sidebar.print_labels_requested.connect(self._on_print_labels)
        self._sidebar.delete_specimen_requested.connect(self._confirm_delete_specimen)
        self._sidebar.print_rna_queue_requested.connect(self._on_print_rna_queue)
        self._sidebar.phase_mark_requested.connect(self._on_phase_mark)
        outer.addWidget(self._sidebar)

        # Wire collab service signals → sidebar strip refresh + collab card refresh
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
            # Refresh collab card when tasks change (if a specimen is selected)
            svc.tasks_changed.connect(self._refresh_collab_card)
            # Peer-synced status changes flow back into the phase pills.
            svc.tasks_changed.connect(self._refresh_batch_header)
        self._sidebar.update_collab_status(svc)

        # ── Centre ①: vertical splitter (monitor top, grouping bottom) ───────
        centre = QSplitter(Qt.Orientation.Vertical)
        centre.setChildrenCollapsible(False)
        centre.setHandleWidth(14)

        self._monitor = MonitorPanel(self.ctx)
        self._monitor.refresh_requested.connect(self._refresh_monitor)
        self._monitor.assign_requested.connect(self._on_assign_jpg)
        self._monitor.unassign_requested.connect(self._on_unassign_jpg)
        self._monitor.add_jpg_requested.connect(self._on_add_jpg_files)
        self._monitor.grouping_requested.connect(self._on_open_grouping)
        self._monitor.compose_implicit_requested.connect(self._on_compose_implicit)
        self._monitor.organise_selected_requested.connect(self._on_organise_selected)
        self._monitor.compose_implicit_organise_requested.connect(
            lambda: self._on_compose_implicit(organise=True)
        )
        self._monitor.auto_compress_toggled.connect(self._on_auto_compress_toggled)
        self._monitor.settings_requested.connect(self._on_open_settings)
        self._monitor.phase_clicked.connect(self._on_phase_clicked)
        centre.addWidget(self._monitor)

        # 分组工具 lives in an on-demand popup, NOT permanently in the main
        # column.  The web oracle keeps this panel collapsed by default and opens
        # it from a 监控区 "分组工具" toggle (app.js:568 / 8595-8627); a non-modal
        # dialog is the desktop equivalent and keeps the work column clean
        # (监控区↑ / 结果区↓).  The panel instance + every signal wire are
        # unchanged — it is just re-homed into the dialog.
        self._grouping = GroupingPanel(self.ctx)
        self._grouping.compose_requested.connect(self._on_compose_requested)
        self._grouping.organise_requested.connect(self._on_organise_requested)
        self._grouping.undo_compose_requested.connect(self._on_undo_compose)
        self._grouping.grouping_changed.connect(self._on_grouping_changed)
        self._grouping.add_selection_to_group_requested.connect(self._on_add_selection_to_group)
        self._grouping.free_compose_requested.connect(self._on_free_compose)
        self._grouping.retroactive_requested.connect(self._on_retroactive_scan)
        self._grouping.auto_group_organize_requested.connect(
            self._on_auto_group_organize
        )
        self._grouping.tiff_naming_check_requested.connect(
            self._on_tiff_naming_check
        )
        self._grouping.helicon_params_requested.connect(self._open_grouping_helicon_params)
        self._grouping.import_tiff_requested.connect(self._on_import_tiff)  # #cursor
        self._grouping.archive_zip_registered.connect(self._on_archive_zip_registered)
        self._grouping.supp_process_requested.connect(self._on_supplementary_process)
        self._grouping.supp_files_dropped.connect(self._on_supplementary_dropped)
        # 批量[合成]/[合成+整理]/[整理] — workbench 驱动顺序队列(合成异步,需串行)。
        self._batch = None  # {"uid","queue":[group_index...],"organise":bool}
        self._last_scan_result = None
        self._auto_known_tiffs: set[str] = set()
        self._auto_tiff_busy = False
        self._grouping.compose_all_requested.connect(
            lambda uid: self._start_compose_batch(uid, organise=False))
        self._grouping.compose_and_organise_all_requested.connect(
            lambda uid: self._start_compose_batch(uid, organise=True))
        self._grouping.organise_all_requested.connect(self._organise_all_batch)
        # 参数对象依然为合成流程提供 get_params()，但 UI 只从“更多”弹出。
        self._helicon_params = HeliconParamsPanel()
        self._seed_helicon_defaults()
        self._grouping_dialog = self._build_grouping_dialog(self._grouping)

        # 成果内容 (composed TIFFs + archive ZIPs) — stacked BELOW the monitor in
        # the main column, mirroring the web oracle's 监控区(top) / 结果区(bottom)
        # workspace (app.js:4995, renderFinalResults app.js:9017).  Keeping the
        # results visible in the work column (not hidden behind a tab) preserves
        # at-a-glance compose/compress state.
        self._results = ResultsColumn()
        self._results.restore_requested.connect(self._on_restore_archive)
        centre.addWidget(self._results)

        centre.setSizes([440, 360])
        centre.setMinimumWidth(520)
        outer.addWidget(centre)

        # ── Right rail: 编号与元数据 column.  Vertical stacking of the results in
        #    the centre column frees the horizontal budget the old tab hack was
        #    invented to reclaim, so the naming panel keeps a width floor (never
        #    clips the UID / copy buttons) as a plain column — no tabs.
        right = QWidget()
        right_lay = QVBoxLayout(right)
        right_lay.setContentsMargins(0, 0, 0, 0)
        right_lay.setSpacing(12)

        # Right-rail command strip.  Keep the global save action visible without
        # turning it into a banner that competes with the form itself.
        rail_toolbar = QHBoxLayout()
        rail_toolbar.setContentsMargins(0, 0, 0, 0)
        rail_toolbar.setSpacing(8)

        rail_toolbar.addStretch(1)

        # 栏顶「收起命名 / 展开命名」整体折叠按钮（web rightPanelCollapsed）
        self._rail_collapse_btn = QPushButton("收起")
        self._rail_collapse_btn.setObjectName("Ghost")
        self._rail_collapse_btn.setFixedHeight(30)
        self._rail_collapse_btn.setToolTip("收起 / 展开整条命名栏")
        self._rail_collapse_btn.clicked.connect(self._toggle_right_rail)
        rail_toolbar.addWidget(self._rail_collapse_btn)
        right_lay.addLayout(rail_toolbar)

        # 卡1 照片编号
        self._naming = NamingPanel(self.ctx)
        self._naming.save_requested.connect(self._on_naming_save)
        self._naming.delete_requested.connect(self._confirm_delete_specimen)
        self._naming.uid_corrected.connect(self._on_uid_corrected)
        self._naming.open_project_settings.connect(self._on_open_settings)
        self._naming.keys_committed.connect(self._apply_collection_autofill)
        right_lay.addWidget(self._naming)           # natural height, no compress

        # Right-rail autosave debounce (web scheduleRightPanelPersist, 500ms).
        # 卡2/卡3 have no save button — edits persist live; reload=False keeps
        # the focused input's cursor.
        self._rail_save_timer = QTimer(self)
        self._rail_save_timer.setSingleShot(True)
        self._rail_save_timer.setInterval(500)
        self._rail_save_timer.timeout.connect(self._flush_rail_save)
        # 卡1 non-key fields (日期/拍照备注) autosave like web input-persist.
        # KEY segments (地区/样地/站位/物种/保存方式) still go through the 保存
        # button / storage-correction path — autosaving them would change the UID.
        self._naming._collection_date.textEdited.connect(lambda *_: self._schedule_rail_save())
        self._naming._photo_date.textEdited.connect(lambda *_: self._schedule_rail_save())
        self._naming._photo_notes.textChanged.connect(lambda: self._schedule_rail_save())

        # 卡2 分类标签（独立卡，对齐 web renderTaxonNotesCard）
        self._taxon_card = TaxonCardPanel(self.ctx)
        self._taxon_card.save_requested.connect(
            lambda: self._on_save_metadata(self._current_uid) if self._current_uid else None
        )
        self._taxon_card.taxon_changed.connect(lambda *_: self._schedule_rail_save())
        self._taxon_card.taxon_changed.connect(lambda *_: self._sync_uid_display_summary())
        self._taxon_card.open_edit_requested.connect(self._on_open_taxon_edit)
        right_lay.addWidget(self._taxon_card)

        # 卡3 元数据（已瘦身，无分类；编辑即存）
        self._metadata = MetadataPanel(self.ctx)
        self._metadata.save_requested.connect(self._on_save_metadata)
        self._metadata.metadata_changed.connect(lambda *_: self._schedule_rail_save())
        self._metadata.metadata_changed.connect(lambda *_: self._sync_uid_display_summary())
        right_lay.addWidget(self._metadata)

        # 卡4 协作状态（默认折叠）
        from app.widgets.collab_specimen_card import CollabSpecimenCard
        self._collab_card = CollabSpecimenCard(self.ctx)
        right_lay.addWidget(self._collab_card)
        right_lay.addStretch(1)

        right_scroll = QScrollArea()
        right_scroll.setObjectName("ColumnScroll")
        right_scroll.setWidget(right)
        right_scroll.setWidgetResizable(True)
        right_scroll.setFrameShape(QFrame.Shape.NoFrame)
        right_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        right_scroll.setMinimumWidth(320)   # web bindPanelResize 下限
        right_scroll.setMaximumWidth(500)   # web bindPanelResize 上限
        self._right_scroll = right_scroll
        self._right_rail_widget = right
        self._right_rail_collapsed = False
        outer.addWidget(right_scroll)

        # 3-zone proportions: sidebar : centre stage (monitor/grouping/results)
        # : naming rail.
        outer.setSizes([280, 760, 380])

        # The three columns' min widths sum to ~1166 px (250+520+320 + handles).
        # On narrower windows (≤1166, e.g. 1024 remote desktops / WSLg HiDPI),
        # childrenCollapsible=False means the splitter can't shrink below that,
        # so the rightmost rail (保存方式) was clipped off the window edge.
        # Hosting the splitter in a horizontal scroll area makes the overflow
        # scrollable instead of clipped.  On wide windows widgetResizable lets
        # the splitter fill the viewport, no scrollbar shows, layout identical.
        outer_scroll = QScrollArea()
        outer_scroll.setObjectName("WorkbenchScroll")
        outer_scroll.setWidget(outer)
        outer_scroll.setWidgetResizable(True)
        outer_scroll.setFrameShape(QFrame.Shape.NoFrame)
        outer_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        outer_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        body_lay.addWidget(outer_scroll, stretch=1)

        # ── Project settings drawer (overlay, hidden by default) ────────────
        from app.widgets.project_settings_drawer import ProjectSettingsDrawer
        self._settings_scrim = _DrawerScrim(self._close_settings, parent=self)
        self._settings_drawer = ProjectSettingsDrawer(self.ctx, parent=self)
        self._settings_drawer.setFixedWidth(380)
        self._settings_drawer.closed.connect(self._settings_scrim.hide)
        self._settings_drawer.personnel_changed.connect(
            self._on_project_personnel_changed
        )
        self._settings_drawer.storages_changed.connect(
            self._naming.refresh_storage_methods
        )

        # ── Collab panel drawer (overlay, hidden by default) ───────────────
        from app.widgets.collab_panel import CollabPanel
        self._collab_scrim = _DrawerScrim(self._close_collab_panel, parent=self)
        self._collab_panel = CollabPanel(self.ctx, parent=self)
        self._collab_panel.closed.connect(self._collab_scrim.hide)

        # ── No-project banner ───────────────────────────────────────────────
        self._no_project_banner = QLabel(
            "未选择工作区 — 请先在「项目树」进入一个断面，或在「最近工作区」打开"
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

        # ── File-system real-time monitoring (replaces 2 s poll) ─────────
        # Primary: QFileSystemWatcher pushes OS-level directory change events.
        # Debounce: 300 ms window merges rapid bursts (camera burst / batch copy).
        # Fallback: 30 s full-rescan timer catches missed events on WSL2 / SMB.
        self._fs_watcher = QFileSystemWatcher(self)
        self._fs_watcher.directoryChanged.connect(self._on_fs_changed)

        self._debounce_timer = QTimer(self)
        self._debounce_timer.setSingleShot(True)
        self._debounce_timer.setInterval(300)
        self._debounce_timer.timeout.connect(self._refresh_monitor)

        self._fallback_timer = QTimer(self)
        self._fallback_timer.setInterval(30000)
        self._fallback_timer.timeout.connect(self._refresh_monitor)

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
        row.setSpacing(10)
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
        settings_btn = QPushButton("设置")
        settings_btn.setObjectName("Ghost")
        settings_btn.setFixedHeight(26)
        settings_btn.clicked.connect(self._on_open_settings)
        row.addWidget(settings_btn)
        return row

    def _build_dir_strip(self) -> QFrame:
        """Working-directory / camera-JPG / results path strip."""
        strip = QFrame()
        strip.setObjectName("DirStrip")
        lay = QHBoxLayout(strip)
        lay.setContentsMargins(10, 6, 10, 6)
        lay.setSpacing(12)

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
        self._sync_rna_queue_count()
        self._refresh_monitor()

        # Re-select the previously active specimen if possible
        active_uid = self._get_active_uid()
        if active_uid:
            self._sidebar.select_uid(active_uid)
            self._load_specimen(active_uid)
        else:
            # 无激活标本(空项目/新建):清命名卡,防上一项目最后加载的
            # province/site 等字段残留显示(用户会误以为"被默认了")。
            self._naming.load_specimen({})
            self._current_uid = None
            try:
                self._apply_draft_project_defaults()
            except Exception:
                pass
        self._refresh_batch_header()

        # Start filesystem watcher + fallback poll
        self._setup_fs_watcher()
        self._debounce_timer.start(50)  # first refresh almost immediately
        if not self._fallback_timer.isActive():
            self._fallback_timer.start()

    def on_deactivate(self) -> None:
        """Called when navigating away; stop watchers and timers."""
        self._debounce_timer.stop()
        self._fallback_timer.stop()
        self._fs_watcher.removePaths(self._fs_watcher.directories())

    def stop_background_work(self) -> None:
        """Cancel an in-flight Helicon compose so its subprocess + QThread
        cannot outlive app exit (orphaned helicon-focus*.exe holds /mnt
        handles → must-reboot lock leak)."""
        w = getattr(self, "_helicon_worker", None)
        if w is not None and w.isRunning():
            try:
                w.cancel()
            except Exception:  # noqa: BLE001
                pass
            w.wait(3000)

    # ── Filesystem watcher helpers ──────────────────────────────────────────

    def _resolve_capture_subdirs(self) -> tuple[str, str]:
        """解析当前项目的 incoming / results 子目录名（监听+扫描共用）。

        incoming 目录名不写死：优先用设置页配置（`project/incoming_subdir`，默认
        incoming-jpg）；若配置的目录不存在但遗留的「新拍JPG」存在，则用「新拍JPG」
        （复用 project_service.LEGACY_INCOMING_JPG_DIR）。results 同理（默认 results）。
        """
        s = getattr(self.ctx, "settings", None)
        inc = getattr(s, "incoming_subdir", None)
        res = getattr(s, "results_subdir", None)
        inc = inc if isinstance(inc, str) and inc else "incoming-jpg"
        res = res if isinstance(res, str) and res else "results"
        project_dir = getattr(self.ctx, "current_project_dir", None)
        if project_dir:
            from app.services.project_service import LEGACY_INCOMING_JPG_DIR
            if not os.path.isdir(os.path.join(project_dir, inc)) and \
               os.path.isdir(os.path.join(project_dir, LEGACY_INCOMING_JPG_DIR)):
                inc = LEGACY_INCOMING_JPG_DIR
        return inc, res

    def _setup_fs_watcher(self) -> None:
        """Watch the resolved incoming + results dirs for OS-level change events."""
        self._fs_watcher.removePaths(self._fs_watcher.directories())
        project_dir = self.ctx.current_project_dir
        if not project_dir:
            return
        inc, res = self._resolve_capture_subdirs()
        for sub in (inc, res):
            d = os.path.join(project_dir, sub)
            os.makedirs(d, exist_ok=True)
            self._fs_watcher.addPath(d)

    def _on_fs_changed(self, _path: str) -> None:
        """Debounced handler: merge rapid file events into one refresh."""
        if not self._debounce_timer.isActive():
            self._debounce_timer.start(300)

    # ── Specimen selection ────────────────────────────────────────────────────

    def _on_specimen_selected(self, uid: str) -> None:
        self._current_uid = uid
        self._load_specimen(uid)
        self._refresh_batch_header()

    def _on_edit_specimen_requested(self, uid: str) -> None:
        """Compatibility entry used by the sidebar edit action."""
        if not uid:
            return
        self._on_specimen_selected(uid)

    def _confirm_delete_specimen(self, uid: str) -> None:
        """Confirm before deleting a specimen record from the workbench DB."""
        uid = str(uid or "").strip()
        if not uid:
            return
        ret = QMessageBox.question(
            self,
            "删除标本编号",
            f"确定删除这个标本编号吗？\n\n{uid}\n\n"
            "只删除工作台中的编号记录和关联状态，不删除磁盘上的照片或成果文件。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if ret == QMessageBox.StandardButton.Yes:
            self._on_delete_specimen(uid)

    def _on_delete_specimen(self, uid: str) -> None:
        """Delete a specimen and local DB references owned by the workbench."""
        uid = str(uid or "").strip()
        if not uid:
            return
        db = self.ctx.get_db()
        if not db:
            return
        try:
            with db:
                db.execute("DELETE FROM grouping WHERE uid=?", (uid,))
                db.execute("DELETE FROM tasks WHERE uid=?", (uid,))
                try:
                    db.execute("UPDATE photo_assignments SET specimen_uid=NULL WHERE specimen_uid=?", (uid,))
                except Exception:
                    pass
                db.execute("DELETE FROM specimens WHERE uid=?", (uid,))
            if self._current_uid == uid:
                self._current_uid = None
                for widget in (self._naming, self._metadata, self._taxon_card, self._grouping):
                    clear = getattr(widget, "clear", None)
                    if callable(clear):
                        clear()
            self._sidebar.refresh()
            self._refresh_batch_header()
        except Exception as exc:
            QMessageBox.warning(self, "删除失败", str(exc))

    def _refresh_batch_header(self) -> None:
        """Sync the monitor's batch-ident bar with the active specimen."""
        db = self.ctx.get_db()
        active_uid = self._get_active_uid()
        activated_at = None
        phase = None
        if db and active_uid:
            try:
                row = db.execute(
                    "SELECT activated_at FROM tasks WHERE uid = ?", (active_uid,)
                ).fetchone()
                if row:
                    activated_at = row[0]
            except Exception:
                pass
            phase = self._collab_phase_for(active_uid)
        batch_uid = active_uid or self._current_uid
        self._monitor.set_batch(batch_uid, active_uid, activated_at)
        if active_uid:
            self._monitor.set_phase(phase)

    def _collab_phase_for(self, uid: str) -> Optional[str]:
        """Return confirmed collab phase from memory first, then project DB."""
        svc = getattr(self.ctx, "collab_service", None)
        try:
            from app.services.activation_service import resolve_phase
            return resolve_phase(svc, self.ctx.get_db(), uid)
        except Exception:
            return None

    def _set_phase(self, uid: str, status: str) -> bool:
        """Mark *uid* to phase *status* — manual human marking, any uid, any jump.

        Writes the project DB and (when collab is running) syncs peers with
        ``force=True`` so out-of-order / backward marks are honoured, mirroring
        the oracle's free assignment (app.js:3303).  Does NOT require *uid* to
        be the active specimen, so the sidebar phase dots can mark any 编号.
        Returns True on success.
        """
        if not uid:
            return False
        allowed = set(getattr(self._monitor, "_phase_pills", {}).keys())
        if status not in allowed:
            self._status_message(f"未知阶段：{status}")
            return False

        db = self.ctx.get_db()
        seed_status = self._collab_phase_for(uid)
        svc = getattr(self.ctx, "collab_service", None)
        if svc is not None:
            ok, msg = svc.update_task_status(
                uid, status, seed_status=seed_status, force=True
            )
            if not ok:
                self._status_message(f"阶段未变更：{msg}")
                return False

        if db is not None:
            try:
                from app.services.activation_service import set_collab_status
                set_collab_status(db, uid, status)
            except Exception as exc:
                self._status_message(f"阶段保存失败：{exc}")
                return False

        self._status_message("阶段已更新")
        self._refresh_batch_header()
        try:
            self._sidebar.update_collab_status(svc)
            self._sidebar.refresh_phases()
            self._refresh_collab_card()
        except Exception:
            pass
        return True

    def _on_phase_clicked(self, status: str) -> None:
        """Batch-bar phase pill: marks the *active* specimen's phase."""
        uid = self._get_active_uid()
        if not uid:
            self._monitor.set_phase(None)
            self._status_message("请先激活一个编号，再标记拍摄阶段")
            return
        if self._set_phase(uid, status):
            self._monitor.set_phase(status)
        else:
            self._refresh_batch_header()

    def _on_phase_mark(self, uid: str, status: str) -> None:
        """Sidebar phase-dot click: mark any 编号's phase (no activation needed)."""
        self._set_phase(uid, status)

    def _on_new_specimen(self) -> None:
        """Start a fresh blank UID draft in the naming/metadata panels.

        The draft is pre-filled with the project's inherited 地区/样地 + 人员
        defaults (resolved up the folder tree) so the user never re-types them
        per specimen — see project_settings_service.effective_new_specimen_prefill.

        采集/拍摄日期沿用上一号(sticky): 同一断面连拍时日期通常不变, 新编号继承当前
        命名卡上的日期而非留空, 减手输; 用户仍可改。无上一号(首次新增)则留空。 (2026-06-14)
        """
        # Capture the currently-shown collection context BEFORE clearing the
        # draft. 新增编号通常是同一工作区/同一站位连续拍摄：地点、站位、
        # 日期、保存方式、人员和坐标应沿用；物种编号/备注/分类仍按新标本清空。
        prev_naming_context = {
            "province": self._naming._province.text().strip(),
            "site": self._naming._site.text().strip(),
            "station": self._naming._station.text().strip(),
            "storage": self._naming._storage.text().strip(),
            "collectionDate": self._naming._collection_date.text().strip(),
            "photoDate": self._naming._photo_date.text().strip(),
        }
        try:
            # Personnel are project defaults for every new specimen, not sticky
            # values from the previous specimen. Location context may carry over
            # and remains auto-marked so a station record can replace it.
            prev_meta_context = {
                key: str(value or "").strip()
                for key, value in self._metadata.current_values().items()
                if key in ("lon", "lat", "geo_area")
            }
        except Exception:
            prev_meta_context = {}

        self._current_uid = None
        prefill = self._effective_prefill()
        self._naming.load_specimen({
            "province": prev_naming_context["province"] or prefill.get("province", ""),
            "site": prev_naming_context["site"] or prefill.get("site", ""),
            "station": prev_naming_context["station"],
            "storage": prev_naming_context["storage"],
            "collectionDate": prev_naming_context["collectionDate"],
            "photoDate": prev_naming_context["photoDate"],
        })
        try:
            self._metadata.clear()
            self._taxon_card.clear()
            # 项目级预填（自动，非手动）：人员三项 + 默认坐标/地理区。clear() 先
            # 清空，故都填进空字段并标记为「自动」。选定具体站位后，采集记录会以
            # override_auto 覆盖这里的项目默认坐标（见 _apply_collection_autofill）。
            self._apply_draft_project_defaults()
            self._metadata.apply_autofill({
                k: v
                for k, v in prev_meta_context.items()
                if v
            }, override_auto=True)
        except Exception:
            pass

    def _apply_draft_project_defaults(self, *, override_auto: bool = False) -> None:
        """把项目默认人员/坐标预填进拍摄界面右栏（仅空字段或自动字段）。

        新建标本、进入工作台无激活号、项目设置里改人员时都会走这里。
        用户仍可在右侧手改（不同站位/临时换人）；手填值不会被覆盖。
        """
        prefill = self._effective_prefill()
        self._metadata.apply_autofill({
            k: prefill[k]
            for k in ("collector", "photographer", "identifier",
                      "lon", "lat", "geo_area")
            if prefill.get(k)
        }, override_auto=override_auto)
        self._sync_uid_display_summary()

    def _on_project_personnel_changed(self, personnel: dict) -> None:
        """Apply project personnel defaults to empty/auto fields on the right rail.

        Works for new drafts and for older specimens whose personnel columns were
        never filled — never overwrites values already saved on the specimen.
        """
        values = {
            key: str(personnel.get(key) or "").strip()
            for key in ("collector", "photographer", "identifier")
        }
        if not any(values.values()):
            return
        before = self._metadata.current_values()
        self._metadata.apply_autofill(values, override_auto=True)
        after = self._metadata.current_values()
        if self._current_uid and before != after:
            self._schedule_rail_save()
        self._sync_uid_display_summary()

    def _effective_prefill(self) -> dict:
        """Inherited new-specimen defaults for the current project, or empties."""
        empty = {"province": "", "site": "", "stations": {},
                 "collector": "", "photographer": "", "identifier": "",
                 "lon": "", "lat": "", "geo_area": ""}
        project_dir = getattr(self.ctx, "current_project_dir", None)
        if not project_dir:
            return empty
        try:
            from app.services import project_settings_service as pss
            return pss.effective_new_specimen_prefill(
                project_dir, root=self.ctx.current_project_root
            )
        except Exception:
            return empty

    def _on_print_labels(self, uid: str) -> None:
        """Print this specimen's labels.

        Fast path (一键直接打印): route sample and RNAlater labels to the project
        configured printers. R-prefix specimens may send tissue labels directly
        or queue them for A4/A5 sheet printing.

        Fallback: open the label studio pre-selected with *uid*, so the user can
        still tune fields / 留白 / 纸张 when there is no default printer or the
        template needs adjusting.
        """
        if not uid:
            return
        if self._quick_print_labels(uid):
            return
        self._status_message("未能开始打印，请检查项目设置中的打印机、模板和纸张。")

    def _quick_print_labels(self, uid: str) -> bool:
        """Send *uid*'s labels to printer per ``quick_print_mode`` setting.

        ``quick_print_mode``:
          - ``"direct"`` — send straight to configured printer (no dialog)
          - ``"dialog"`` — show PrintJobDialog for printer selection
          - ``"studio"`` — return False, caller falls back to label studio

        Returns ``False`` when the caller should open the studio instead.
        """
        try:
            from PyQt6.QtPrintSupport import QPrinterInfo
            from app.services import label_service
            from app.services import project_settings_service as pss
            import app.utils.label_print as label_print

            project_dir = getattr(self.ctx, "current_project_dir", None)
            project_root = getattr(self.ctx, "current_project_root", None)
            if not isinstance(project_root, str):
                project_root = None
            db = self.ctx.get_db()
            if db is None:
                return False
            print_settings = (
                pss.effective_print_settings(
                    project_dir, root=project_root
                )
                if project_dir else dict(pss.DEFAULT_PRINT_SETTINGS)
            )
            local_print_settings = pss.load_setting_if_present(db, "print_settings")
            if local_print_settings is not None:
                print_settings = pss.merge_print_settings(print_settings, local_print_settings)
                if (
                    "quick_print_mode" not in local_print_settings
                    and "quick_print" in local_print_settings
                ):
                    print_settings["quick_print_mode"] = (
                        "direct" if bool(local_print_settings["quick_print"]) else "studio"
                    )

            # read quick_print_mode with backward compat for old quick_print bool.
            # DEFAULT_PRINT_SETTINGS now carries quick_print_mode="direct", so a
            # legacy project with only quick_print=False needs an explicit remap.
            quick_mode = str(print_settings.get("quick_print_mode") or "")
            if not quick_mode:
                quick_mode = "direct" if bool(print_settings.get("quick_print", True)) else "studio"
            elif quick_mode == "direct" and not bool(print_settings.get("quick_print", True)):
                quick_mode = "studio"
            if quick_mode == "studio":
                return False

            from app.utils import windows_print
            if windows_print.is_available():
                default_printer = windows_print.windows_default_printer_name()
                available = set(windows_print.windows_printer_names())
            else:
                default_printer = QPrinterInfo.defaultPrinterName()
                available = {
                    p.printerName()
                    for p in QPrinterInfo.availablePrinters()
                    if p.printerName()
                }
            sample_printer = self._resolve_quick_printer(
                str(print_settings.get("sample_printer") or ""),
                default_printer,
                available,
            )
            tissue_printer = self._resolve_quick_printer(
                str(print_settings.get("tissue_printer") or ""),
                default_printer,
                available,
            )
            if quick_mode == "direct" and not sample_printer:
                return False

            sample_paper_type = str(print_settings.get("sample_paper_type") or "")
            tissue_paper_type = str(print_settings.get("tissue_paper_type") or "")
            paper_types = {
                k: v for k, v in {
                    "sample": sample_paper_type,
                    "tissue": tissue_paper_type,
                }.items() if v in {"label", "a4", "a5"}
            } or None
            template_keys = {
                k: v for k, v in {
                    "sample": str(print_settings.get("sample_template_key") or ""),
                    "tissue": str(print_settings.get("tissue_template_key") or ""),
                }.items() if v
            } or None

            specimens = label_service.load_specimen_dicts(db)
            jobs = label_service.LabelService.quick_print_jobs_for_specimen(
                specimens, uid, paper_types=paper_types, template_keys=template_keys
            )
            if not print_settings.get("include_tissue", True):
                jobs = [j for j in jobs if j.get("bucket") != "tissue"]
            if not jobs:
                return False

            direct_jobs: list[dict] = []
            queued_tissue = 0
            for job in jobs:
                if job.get("bucket") == "tissue":
                    if quick_mode == "direct" and not tissue_printer:
                        return False
                    effective_tissue_paper = tissue_paper_type or label_service.persisted_paper_type("tissue")
                    if self._should_queue_tissue_label(
                        print_settings,
                        sample_printer=sample_printer,
                        tissue_printer=tissue_printer,
                        tissue_paper_type=effective_tissue_paper,
                    ):
                        from app.services import rna_label_queue_service as rna_queue
                        queued_tissue += rna_queue.enqueue(db, [uid])
                        self._sync_rna_queue_count()
                        continue
                direct_jobs.append(job)

            if not direct_jobs:
                if queued_tissue:
                    from app.services import rna_label_queue_service as rna_queue
                    self._status_message(f"RNAlater 标签已加入合版队列：当前 {rna_queue.pending_count(db)} 张")
                return True

            printed = 0
            printers_used: list[str] = []
            printed_details: list[str] = []
            try:
                from app.services.activity_audit_service import default_actor, record_print_jobs
                actor = default_actor(self.ctx)
            except Exception:
                record_print_jobs = None
                actor = ""

            if quick_mode == "dialog":
                # ── Dialog path: user picks printer, all jobs printed together ──
                if windows_print.is_available():
                    ok, printer_name = windows_print.print_jobs_with_windows_dialog(
                        direct_jobs, document_name=f"标本标签 {uid}"
                    )
                    if not ok:
                        # User cancelled the system dialog; this is handled and
                        # must not navigate to the template-design workspace.
                        return True
                    if record_print_jobs is not None:
                        try:
                            record_print_jobs(
                                db, direct_jobs, actor=actor,
                                printer_name=printer_name or "Windows 打印机",
                            )
                        except Exception:
                            pass
                    printed = sum(len(j.get("labels") or []) for j in direct_jobs)
                    printer_display = printer_name or "Windows 打印机"
                    printers_used = [printer_display] if printed else []
                    printed_details = [f"{len(direct_jobs)} 个作业 → {printer_display}"]
                else:
                    from app.widgets.print_dialog import PrintJobDialog
                    dlg = PrintJobDialog(direct_jobs, self)
                    if dlg.exec() != QDialog.DialogCode.Accepted:
                        if queued_tissue:
                            from app.services import rna_label_queue_service as rna_queue
                            self._status_message(f"RNAlater 标签已加入合版队列：当前 {rna_queue.pending_count(db)} 张")
                        return False

                    printer_name = dlg.selected_printer()
                    printer = label_print.build_printer(direct_jobs[0])
                    if printer_name:
                        printer.setPrinterName(printer_name)

                    if not label_print.paint_jobs(printer, direct_jobs):
                        return False
                    if record_print_jobs is not None:
                        try:
                            record_print_jobs(
                                db, direct_jobs, actor=actor,
                                printer_name=printer_name or printer.printerName() or "default",
                            )
                        except Exception:
                            pass
                    printed = sum(len(j.get("labels") or []) for j in direct_jobs)
                    printer_display = printer_name or printer.printerName() or "默认打印机"
                    printers_used = [printer_display] if printed else []
                    printed_details = [f"{len(direct_jobs)} 个作业 → {printer_display}"]
            else:
                # ── Direct path: each job to its configured printer ──
                for job in direct_jobs:
                    target = tissue_printer if job.get("bucket") == "tissue" else sample_printer
                    if not target:
                        return False
                    if windows_print.is_available():
                        ok, used_printer = windows_print.print_jobs_with_windows_dialog(
                            [job], document_name=f"标本标签 {uid}",
                            printer_name=target, show_dialog=False,
                        )
                        if not ok:
                            return False
                        target = used_printer or target
                    else:
                        printer = label_print.build_printer(job)
                        printer.setPrinterName(target)
                        if not label_print.paint_jobs(printer, [job]):
                            return False
                    if record_print_jobs is not None:
                        try:
                            record_print_jobs(db, [job], actor=actor, printer_name=target)
                        except Exception:
                            pass
                    printed += len(job.get("labels") or [])
                    if target not in printers_used:
                        printers_used.append(target)
                    printed_details.append(self._quick_print_job_label(job, target))

            msg_parts = []
            if printed:
                msg_parts.append(
                    f"已发送 {printed} 张标签到 {'、'.join(printers_used)}"
                    f"（{'; '.join(printed_details)}）"
                )
            if queued_tissue:
                from app.services import rna_label_queue_service as rna_queue
                msg_parts.append(f"RNAlater 标签已加入合版队列：当前 {rna_queue.pending_count(db)} 张")
            if msg_parts:
                self._status_message("；".join(msg_parts))
            return True
        except Exception:
            return False

    def _sync_rna_queue_count(self) -> None:
        db = None
        try:
            db = self.ctx.get_db()
        except Exception:
            db = None
        count = 0
        if db is not None:
            try:
                from app.services import rna_label_queue_service as rna_queue
                count = rna_queue.pending_count(db)
            except Exception:
                count = 0
        try:
            self._sidebar.set_rna_queue_count(count)
        except Exception:
            pass

    @staticmethod
    def _quick_print_job_label(job: dict, printer_name: str) -> str:
        bucket = "RNAlater" if job.get("bucket") == "tissue" else "样品瓶"
        tmpl = job.get("template") or {}
        template_name = str(tmpl.get("name") or "未命名模板")
        paper = str(job.get("paperType") or "label").upper()
        return f"{bucket}: {template_name} / {paper} / {printer_name}"

    @staticmethod
    def _resolve_quick_printer(configured: str, default_printer: str, available: set[str]) -> str:
        configured = (configured or "").strip()
        if configured:
            return configured if configured in available else ""
        return default_printer or ""

    @staticmethod
    def _should_queue_tissue_label(
        settings: dict,
        *,
        sample_printer: str,
        tissue_printer: str,
        tissue_paper_type: str,
    ) -> bool:
        strategy = str(settings.get("tissue_strategy") or "auto")
        if strategy == "queue":
            return True
        if strategy == "direct":
            return False
        return (
            bool(sample_printer)
            and sample_printer == tissue_printer
            and tissue_paper_type in {"a4", "a5"}
        )

    def _on_print_rna_queue(self) -> None:
        """Open the RNAlater sheet queue preview, then print or clear."""
        try:
            from PyQt6.QtPrintSupport import QPrinterInfo
            from app.services import label_service
            from app.services import project_settings_service as pss
            from app.services import rna_label_queue_service as rna_queue
            from app.utils.label_core import unique_id
            from app.utils.label_print import build_printer, paint_jobs
            from app.utils.label_sheet import draw_crop_marks

            db = self.ctx.get_db()
            project_dir = getattr(self.ctx, "current_project_dir", None)
            project_root = getattr(self.ctx, "current_project_root", None)
            if not isinstance(project_root, str):
                project_root = None
            if db is None or not project_dir:
                self._status_message("请先打开一个项目工作区。")
                return

            uids = rna_queue.pending_uids(db)
            if not uids:
                self._status_message("没有待打印的 RNAlater 合版标签。")
                self._sync_rna_queue_count()
                return

            settings = pss.effective_print_settings(
                project_dir, root=project_root
            )
            local_print_settings = pss.load_setting_if_present(db, "print_settings")
            if local_print_settings is not None:
                settings = pss.merge_print_settings(settings, local_print_settings)

            default_printer = QPrinterInfo.defaultPrinterName()
            available = {
                p.printerName()
                for p in QPrinterInfo.availablePrinters()
                if p.printerName()
            }
            tissue_printer = self._resolve_quick_printer(
                str(settings.get("tissue_printer") or ""),
                default_printer,
                available,
            )
            if not tissue_printer:
                QMessageBox.information(self, "打印 RNA 合版", "未找到 RNAlater 标签打印机。")
                return

            specimens = label_service.load_specimen_dicts(db)
            wanted = set(uids)
            indices = [i for i, sp in enumerate(specimens) if unique_id(sp) in wanted]
            if not indices:
                self._status_message("队列里的 RNAlater 标签没有对应标本记录。")
                return

            lib = label_service.LabelTemplateLibrary("tissue")
            paper_type = (
                str(settings.get("tissue_paper_type") or "")
                or label_service.persisted_paper_type("tissue")
            )
            if paper_type not in {"a4", "a5"}:
                paper_type = str((settings.get("tissue_sheet") or {}).get("paper") or "a4")
            if paper_type not in {"a4", "a5"}:
                paper_type = "a4"
            grid_opts = label_service.persisted_imposition("tissue")
            if "cutMarks" not in grid_opts:
                grid_opts = dict(grid_opts)
                grid_opts["cutMarks"] = True

            job = label_service.LabelService.build_print_job(
                specimens,
                label_service.resolve_template(lib),
                "tissue",
                selected_indices=indices,
                dims=label_service.resolve_dims(lib, lib.selected_custom_dims()),
                copies=1,
                paper_type=paper_type,
                paper=label_service.PAPER_SIZES.get(paper_type),
            )
            if not (job.get("items") or []):
                self._status_message("队列中没有可生成的 RNAlater 标签。")
                return
            job["gridOpts"] = grid_opts

            ordered_uids = [unique_id(specimens[i]) for i in indices]
            dlg = _RnaQueueDialog(ordered_uids, job, grid_opts, self)
            if dlg.exec() != QDialog.DialogCode.Accepted:
                return
            if dlg.action == "clear":
                changed = rna_queue.clear_pending(db)
                self._sync_rna_queue_count()
                self._status_message(f"已清空 RNAlater 待打印队列：{changed} 张")
                return
            if dlg.action != "print":
                return

            printer = build_printer(job, grid_opts)
            printer.setPrinterName(tissue_printer)
            ok = paint_jobs(
                printer,
                [job],
                grid_opts=grid_opts,
                cut_marks=bool(grid_opts.get("cutMarks")),
                draw_crop_marks=draw_crop_marks,
            )
            if not ok:
                QMessageBox.warning(self, "打印 RNA 合版", "打印任务发送失败。")
                return
            try:
                from app.services.activity_audit_service import default_actor, record_print_jobs
                record_print_jobs(
                    db,
                    [job],
                    actor=default_actor(self.ctx),
                    printer_name=tissue_printer,
                )
            except Exception:
                pass
            rna_queue.mark_printed(db, ordered_uids)
            self._sync_rna_queue_count()
            self._status_message(f"RNAlater 合版已发送到 {tissue_printer} · {len(ordered_uids)} 张")
        except Exception as exc:
            QMessageBox.warning(self, "打印 RNA 合版", str(exc))

    def _status_message(self, text: str, msec: int = 4000) -> None:
        ui.show_status(self, text, msec)

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
            self._status_message("请先打开一个项目工作区。")
            return
        uid = self._naming.current_uid()
        if not uid:
            self._status_message("编号尚未填写完整。")
            return
        missing_required = [
            field for field in self._naming.missing_required_fields()
            if field not in ("采集日期", "拍照日期")
        ]
        if missing_required:
            QMessageBox.warning(
                self,
                "编号信息未填写完整",
                "请先补全必填字段：" + "、".join(missing_required),
            )
            self._naming._check_compliance(uid)
            return
        old_uid = (
            self._current_uid
            if self._current_uid and self._current_uid != uid
            else None
        )
        old_raw: dict = {}
        if old_uid:
            try:
                row = db.execute("SELECT raw_json FROM specimens WHERE uid=?", (old_uid,)).fetchone()
                old_raw = json.loads(row["raw_json"] or "{}") if row else {}
                if not isinstance(old_raw, dict):
                    old_raw = {}
            except Exception:
                old_raw = {}

        # 采集日期软必填：它是编号核心字段、会写入 UID 日期段。空着强提醒，但允许继续
        # （兼容采集日期确实未知的标本——编号自动少一段）。默认「返回填写」。
        if not self._naming._collection_date.text().strip():
            reply = QMessageBox.question(
                self, "采集日期未填",
                "采集日期是编号核心字段，会写入唯一编号。\n\n"
                "未知可留空继续（编号自动少日期段），或返回填写。",
                QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            if reply != QMessageBox.StandardButton.Save:
                return

        # ── UID uniqueness (mirrors web server.js HTTP 409) ─────────────────
        # A specimen UID is a museum-style catalogue number.
        # (a) Cross-workspace: scan every OTHER known workspace's project.db;
        #     if this UID is already owned there → refuse (global unique).
        # (b) Same-workspace cover: editing a loaded specimen (_current_uid) and
        #     changing its fields to another UID that already exists here →
        #     refuse (would overwrite a different specimen via upsert).
        # Re-saving the very same UID (update self, incl. _current_uid is None
        # and the row already exists) is allowed — PRIMARY KEY upsert handles it.
        from app.services import specimen_catalog_service as _catalog
        try:
            _current_resolved = str(Path(project_dir).resolve())
        except OSError:
            _current_resolved = project_dir
        _other_hits = [
            h for h in _catalog.find_uid(
                uid,
                current_project_dir=project_dir,
                current_project_root=getattr(self.ctx, "current_project_root", None),
            )
            if h.project_dir != _current_resolved
        ]
        _local_owner = db.execute(
            "SELECT owner_project_dir FROM specimens WHERE uid=?", (uid,)
        ).fetchone()
        _local_cover = (
            _local_owner is not None
            and self._current_uid is not None
            and self._current_uid != uid
        )
        if _other_hits:
            QMessageBox.warning(
                self, "编号已被占用",
                "⚠ " + _catalog.format_uid_conflict(uid, _other_hits),
            )
            self._naming.show_dup_warn(True)
            return
        if _local_cover:
            QMessageBox.warning(
                self, "编号已被占用",
                f"⚠ 编号 {uid} 已存在于本项目（另一个标本），不能覆盖。请修改字段后再保存。",
            )
            self._naming.show_dup_warn(True)
            return

        # ── Collaboration UID claim ───────────────────────────────────────
        # When collaboration is active (running + a group code), claim a NEW
        # UID across the LAN so no teammate can reuse it.  Re-saving a UID that
        # is already a local specimen is an update, not a new claim.
        svc = getattr(self.ctx, "collab_service", None)
        if svc is not None and svc.is_running() and svc.group_code:
            is_local = db.execute(
                "SELECT 1 FROM specimens WHERE uid=?", (uid,)
            ).fetchone() is not None
            if not is_local:
                ok, msg = svc.create_task(uid, assignee=self._collab_operator())
                if not ok:
                    QMessageBox.warning(
                        self, "编号已被占用",
                        f"编号 {uid} 已被占用：{msg}\n请改用其他编号后再保存。",
                    )
                    self._naming._apply_sequence_suggestion()
                    return

        n = self._naming
        from app.utils.naming import coalesce_specimen_dates
        collection_date, photo_date = coalesce_specimen_dates(
            n._collection_date.text().strip(),
            n._photo_date.text().strip(),
        )
        try:
            db.execute(
                """
                INSERT INTO specimens (uid, id, province, site, station,
                                       storage, collection_date, photo_date,
                                       photo_notes, owner_project_dir)
                VALUES (?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(uid) DO UPDATE SET
                    id=excluded.id, province=excluded.province,
                    site=excluded.site, station=excluded.station,
                    storage=excluded.storage,
                    collection_date=excluded.collection_date,
                    photo_date=excluded.photo_date,
                    photo_notes=excluded.photo_notes,
                    owner_project_dir=excluded.owner_project_dir
                """,
                (
                    uid,
                    n._species_id.text().strip(),
                    n._province.text().strip(),
                    n._site.text().strip(),
                    n._station.text().strip(),
                    n._storage.text().strip(),
                    collection_date,
                    photo_date,
                    n._photo_notes.toPlainText().strip(),
                    project_dir,
                ),
            )
            db.commit()
            if collection_date and not n._collection_date.text().strip():
                n._collection_date.setText(collection_date)
            if photo_date and not n._photo_date.text().strip():
                n._photo_date.setText(photo_date)
            # 命名行已建/更新 → 标记为当前标本，再 flush 右栏三卡。
            # 关键修复（场景1 疑点1+2）：新草稿 _current_uid 原为 None，metadata
            # autosave 整段被 _schedule_rail_save 跳过；保存只写命名段 → 用户在
            # metadata 卡填的 采集人/经纬度/地理区/分类 会静默丢失。这里先设
            # _current_uid（行已存在），再调 _on_save_metadata 把右栏一并入库，
            # 使「保存」= 存全部。
            self._current_uid = uid
            self._on_save_metadata(uid, reload=False)
            if old_uid:
                self._finalize_uid_rename(old_uid, uid, old_raw)
            self._sidebar.refresh()
            self._sidebar.select_uid(uid)
            # 「新建编号后自动激活」开关（默认关，复刻 oracle
            # autoActivateOnNewSpecimen app.js:9396-9397）：开则保存即把此号设为
            # 当前激活标本，省去手动点激活；关则不动激活（守 oracle 默认）。
            if bool(getattr(self.ctx.settings, "auto_activate_on_new_specimen", False)):
                self._on_sidebar_activate(uid)
        except Exception as exc:
            QMessageBox.warning(self, "保存失败", str(exc))

    def _finalize_uid_rename(self, old_uid: str, new_uid: str, old_raw: Optional[dict] = None) -> None:
        """Move DB references after right-rail editing changes the generated UID."""
        if not old_uid or not new_uid or old_uid == new_uid:
            return
        db = self.ctx.get_db()
        if not db:
            return
        raw = dict(old_raw or {})
        n = self._naming
        raw.update({
            "uid": new_uid,
            "id": n._species_id.text().strip(),
            "province": n._province.text().strip(),
            "site": n._site.text().strip(),
            "station": n._station.text().strip(),
            "storage": n._storage.text().strip(),
            "collectionDate": n._collection_date.text().strip(),
            "photoDate": n._photo_date.text().strip(),
        })
        prev = raw.get("previousUniqueIds") or []
        if not isinstance(prev, list):
            prev = []
        if old_uid not in prev:
            prev.append(old_uid)
        raw["previousUniqueIds"] = prev
        try:
            from app.services.specimen_rename_service import migrate_uid_references
            with db:
                migrate_uid_references(db, old_uid, new_uid)
                try:
                    db.execute(
                        "UPDATE photo_assignments SET specimen_uid=? WHERE specimen_uid=?",
                        (new_uid, old_uid),
                    )
                except Exception:
                    pass
                db.execute("DELETE FROM specimens WHERE uid=?", (old_uid,))
                db.execute(
                    "UPDATE specimens SET raw_json=? WHERE uid=?",
                    (json.dumps(raw, ensure_ascii=False), new_uid),
                )
        except Exception as exc:
            QMessageBox.warning(self, "编号迁移失败", str(exc))

    def _seed_helicon_defaults(self) -> None:
        """Seed the compose params panel from saved Helicon defaults (QSettings).

        Keeps the per-compose panel in sync with the 「保存为默认」 stored by the
        top-bar Helicon 配置 dialog. Failures are non-fatal (panel keeps its own
        hardcoded defaults).
        """
        try:
            from app.views.settings_view import (
                _K_HELICON_METHOD, _K_HELICON_RADIUS, _K_HELICON_SMOOTHING,
            )
            qs = self.ctx.settings._qs
            self._helicon_params.set_params({
                "method": int(qs.value(_K_HELICON_METHOD, 1)),
                "radius": float(qs.value(_K_HELICON_RADIUS, 8.0)),
                "smoothing": int(qs.value(_K_HELICON_SMOOTHING, 4)),
            })
        except Exception:
            pass

    def _build_grouping_dialog(self, panel: QWidget) -> QDialog:
        """Host the GroupingPanel in a non-modal popup.

        Non-modal so the user can still drag/select files in the monitor while
        the grouping tool is open (the monitor → group flow relies on it).
        """
        dlg = QDialog(self)
        dlg.setObjectName("GroupingDialog")
        dlg.setWindowTitle("分组工具")
        dlg.setModal(False)
        lay = QVBoxLayout(dlg)
        # Pad so the inner WorkbenchSection card's drop-shadow is not clipped —
        # the card floats on the soft-gray dialog bg (QDialog#GroupingDialog).
        lay.setContentsMargins(14, 14, 14, 14)
        # Collapse header row is redundant inside a dedicated popup.
        header = getattr(panel, "_group_header_widget", None)
        if header is not None:
            header.setVisible(False)
        divider = getattr(panel, "_header_divider", None)
        if divider is not None:
            divider.setVisible(False)
        lay.addWidget(panel)
        dlg.resize(760, 560)
        return dlg

    def _open_grouping_helicon_params(self) -> None:
        """从分组工具的“更多”按需弹出 Helicon 参数。"""
        dlg = getattr(self, "_grouping_helicon_params_dialog", None)
        if dlg is None:
            dlg = QDialog(self._grouping_dialog)
            dlg.setWindowTitle("Helicon 合成参数")
            layout = QVBoxLayout(dlg)
            layout.setContentsMargins(16, 16, 16, 16)
            layout.addWidget(self._helicon_params)
            dlg.resize(720, 330)
            self._grouping_helicon_params_dialog = dlg
        dlg.show()
        dlg.raise_()
        dlg.activateWindow()

    def _on_open_grouping(self) -> None:
        """Open (or re-focus) the grouping/compose popup — web 分组工具 toggle.

        智能：打开时若分组面板还没绑定标本，自动取一个编号：
        当前载入 → 激活编号 → 连编号都没有则临时分组。**命名表单未激活的草稿编号不
        再绑定/显示**（用户：没激活不用显示）——此前回退到命名草稿的档已砍，与 web
        oracle `namingTargetSpecimen` 分道，桌面端有意取舍。
        """
        if not getattr(self._grouping, "_uid", None):
            from app.services.grouping_service import (
                ADHOC_GROUPING_UID,
                SpecimenGrouping,
                load_grouping,
            )
            uid = (
                self._current_uid
                or self._get_active_uid()
                or ADHOC_GROUPING_UID  # 没载入也没激活 → 临时分组,输出默认 组序.tif
            )
            db = self.ctx.get_db()
            try:
                if db:
                    grouping = load_grouping(db, uid)
                else:
                    grouping = SpecimenGrouping(uid=uid, groups=[])
                self._grouping.load_grouping(uid, grouping)
                if uid == ADHOC_GROUPING_UID:
                    # 临时分组:标题友好化,免得显示 "~未命名" 这个内部 key。
                    self._grouping._uid_label.setText("未命名（临时分组）")
                    self._grouping._target_label.setText("临时")
            except Exception:
                pass
        dlg = self._grouping_dialog
        dlg.show()
        dlg.raise_()
        dlg.activateWindow()

    def _toggle_right_rail(self) -> None:
        """Collapse/expand the whole naming rail (web rightPanelCollapsed)."""
        self._right_rail_collapsed = not self._right_rail_collapsed
        c = self._right_rail_collapsed
        for w in (self._naming, self._taxon_card, self._metadata):
            w.setVisible(not c)
        if c:
            self._right_scroll.setMinimumWidth(48)
            self._right_scroll.setMaximumWidth(48)
        else:
            self._right_scroll.setMinimumWidth(280)
            self._right_scroll.setMaximumWidth(480)
        self._rail_collapse_btn.setText("展开" if c else "收起")

    def _on_open_taxon_edit(self) -> None:
        """Open the 「一次编辑五级」 taxon modal and write the result back."""
        from app.widgets.taxon_edit_dialog import TaxonEditDialog
        dlg = TaxonEditDialog(self._taxon_card.field_values(), parent=self)
        if dlg.exec():
            self._taxon_card.apply_values(dlg.result_values())
            if self._current_uid:
                self._on_save_metadata(self._current_uid)

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
                    "photo_notes": sp.photo_notes,
                }
                sp_dict["uid"] = sp.uid
                sp_dict.setdefault("photo_notes", sp.photo_notes)
                self._naming.load_specimen(sp_dict)
                self._metadata.load_specimen(sp)
                self._taxon_card.load_specimen(sp)
                self._sync_uid_display_summary()
                self._collab_card.load_specimen(uid)
        except Exception:
            pass

        # Populate ② 成果内容 column from grouping data
        self._refresh_results_column(uid, grouping)

        # Backfill empty capture fields from a matching 采集记录. load_specimen sets
        # the four location keys via direct setText (no keys_committed signal), so
        # _apply_collection_autofill would otherwise never run on the load path →
        # 已填的 采集人/拍摄人/坐标 在右栏不显示。非破坏：只填空字段。
        # 容错：采集记录表缺失/查询异常不能拖垮标本加载本身。
        try:
            self._apply_collection_autofill()
        except Exception:
            pass
        # 项目 personnel 默认值回填空字段。_on_project_personnel_changed 只灌新草稿
        # （_current_uid 为 None），已存标本的空 采集人/拍摄人 不会被它覆盖 → 建号时
        # personnel 还没设、之后才设的标本加载后永远空。空不是事实，加载时补默认值。
        # 非破坏：apply_autofill 只填空字段，已存值不动。采集记录优先级更高（已先跑）。
        try:
            self._backfill_personnel_defaults()
        except Exception:
            pass

    def _sync_uid_display_summary(self) -> None:
        """Mirror existing metadata into the UID card's non-identity summary."""
        values = {}
        try:
            values.update(self._metadata.current_values())
        except Exception:
            pass
        try:
            values.update(self._taxon_card.field_values())
        except Exception:
            pass
        try:
            values["photo_notes"] = self._naming._photo_notes.toPlainText().strip()
            self._naming.set_display_metadata(values)
        except Exception:
            pass

    # ── Collection-record auto-fill ─────────────────────────────────────────────

    def _apply_collection_autofill(self) -> None:
        """Fill empty capture fields from a matching 采集记录 (野外采集记录簿).

        Triggered when the four location keys (地区/样地/站位/采集日期) are
        finished editing or picked from the record menu. Non-destructive: only
        empty fields are filled (collection_record_service.autofill_values).
        Fields the capture cards lack (生境/潮水/…) stay in the record only.
        """
        db = self.ctx.get_db()
        if not db:
            return
        province, site, station, col_date = self._naming.current_keys()
        if not (province and site and station and col_date):
            return
        from app.services import collection_record_service as crs
        rec = crs.lookup_record(db, province, site, station, col_date)
        if not rec:
            return
        # 优先级 项目默认 < 站位记录 < 手动/已存：把「自动填」字段当作空看待，让站位
        # 采集记录能覆盖项目默认坐标；受保护字段（用户手填/加载已存的非空值）保留真实
        # 值 → autofill_values 视为已填、不再返回，从而不被覆盖。
        auto = self._metadata.auto_fields()
        current = {
            k: ("" if (k in auto or not v.strip()) else v)
            for k, v in self._metadata.current_values().items()
        }
        current["photo_date"] = self._naming._photo_date.text()
        vals = crs.autofill_values(rec, current)
        if not vals:
            return
        if "photo_date" in vals and not self._naming._photo_date.text().strip():
            self._naming._photo_date.setText(str(vals["photo_date"]))
        # override_auto=True：覆盖项目默认（自动）坐标，但 apply_autofill 内部仍
        # 跳过手动字段。
        self._metadata.apply_autofill(vals, override_auto=True)
        # Persist for an already-saved specimen; a brand-new draft persists when
        # the user hits 保存 (fields are read straight off the panels then).
        if self._current_uid:
            self._schedule_rail_save()

    def _backfill_personnel_defaults(self) -> None:
        """把项目 personnel 默认值回填到已加载标本的【空】采集人/拍摄人/鉴定人。

        与 _on_project_personnel_changed 互补：后者只灌新草稿（_current_uid 为
        None 时），不碰已存标本。但已存标本的空 personnel 字段（建号时项目默认值
        还没设、之后才设的情况）应能从项目默认值补上。非破坏：apply_autofill 只填
        空字段，已存值（含采集记录已回填的）保留。
        """
        prefill = self._effective_prefill()
        values = {
            k: str(prefill.get(k) or "").strip()
            for k in ("collector", "photographer", "identifier")
        }
        if not any(values.values()):
            return
        before = self._metadata.current_values()
        self._metadata.apply_autofill(values, override_auto=True)
        after = self._metadata.current_values()
        if self._current_uid and before != after:
            self._schedule_rail_save()
        self._sync_uid_display_summary()

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
                from app.services.monitor_service import _resolved
                for row in rows:
                    uid = row[0]
                    paths = _json.loads(row[1] or "[]")
                    for p in paths:
                        attr.path_to_uid[_resolved(p)] = uid
            except Exception:
                pass

            inc, res = self._resolve_capture_subdirs()
            result = scan_project(
                project_dir, db, attr=attr,
                incoming_subdir=inc, results_subdir=res,
            )
            self._last_scan_result = result
            self._monitor.load_scan(result)
            self._maybe_auto_process_new_tiff(result)
        except FileNotFoundError:
            self._monitor.clear()
        except Exception:
            self._monitor.clear()

    def _pending_tiff_paths(self, scan_result) -> set[str]:
        return {
            str(Path(entry.path).resolve())
            for entry in getattr(scan_result, "tiff_files", [])
            if not bool(getattr(entry, "has_zip", False))
        }

    def _on_auto_compress_toggled(self, on: bool) -> None:
        if on:
            self._auto_known_tiffs = self._pending_tiff_paths(self._last_scan_result) if self._last_scan_result else set()
            self._status_message("自动压缩已开启：只处理之后新出现的 TIFF")
        else:
            self._status_message("自动压缩已关闭")

    def _maybe_auto_process_new_tiff(self, scan_result) -> None:
        try:
            enabled = self._monitor.auto_compress_enabled()
        except Exception:
            enabled = False
        current = self._pending_tiff_paths(scan_result)
        if not enabled:
            self._auto_known_tiffs = current
            return
        new_paths = [p for p in sorted(current) if p not in self._auto_known_tiffs]
        if not new_paths or self._auto_tiff_busy:
            return

        uid = self._get_active_uid()
        if not uid:
            self._status_message("发现新 TIFF；请先激活编号后再自动压缩")
            return
        jpg_paths = self._unoccupied_jpg_paths(uid, self._get_attributed_jpg_paths(uid))
        if not jpg_paths:
            self._status_message("发现新 TIFF；当前激活编号没有可归档 JPG")
            return

        self._auto_tiff_busy = True
        try:
            if self._organise_jpgs_with_tiff(jpg_paths, new_paths[0], silent=True):
                self._auto_known_tiffs.add(new_paths[0])
                self._status_message("新 TIFF 已自动压缩归档")
        finally:
            self._auto_tiff_busy = False

    def _missing_meta_fields(self, uid: str) -> list:
        """返回该编号缺失的关键字段标签：保存方式 / 采集日期 / 拍摄日期。

        拍摄当时可能还不知道怎么处理标本（保存方式/日期没填），切到下一个号时用来
        提醒回填，免得遗漏。左侧点该号即可随时编辑补填。
        """
        db = self.ctx.get_db()
        if not db or not uid:
            return []
        try:
            row = db.execute(
                "SELECT storage, collection_date, photo_date FROM specimens WHERE uid = ?",
                (uid,),
            ).fetchone()
        except Exception:
            return []
        if not row:
            return []
        missing = []
        if not (row[0] and str(row[0]).strip()):
            missing.append("保存方式")
        if not (row[1] and str(row[1]).strip()):
            missing.append("采集日期")
        if not (row[2] and str(row[2]).strip()):
            missing.append("拍摄日期")
        return missing

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
            result = svc_activate(project_dir, db, uid)
            prev_uid = result.get("previous_uid") if isinstance(result, dict) else None
            self._sidebar.refresh()
            self._refresh_monitor()
            # Select and load the newly activated specimen
            self._sidebar.select_uid(uid)
            self._load_specimen(uid)
            # 激活即置「拍摄中」：仅当此号尚无阶段（None/空/created）时默认 shooting，
            # 已有更高阶段（已拍完/整理中/完成）保留不动 —— 对齐 oracle
            # activateSpecimen 的 status: existing!=="created" ? existing : "shooting"
            # (app.js:3531-3534) + collabUpdateTaskStatus(uid, status||shooting) (:3556)。
            phase = self._collab_phase_for(uid)
            if phase in (None, "", "created"):
                self._set_phase(uid, "shooting")
            else:
                self._refresh_batch_header()
            # 切换激活号提醒：旧号在其激活期间到达的照片仍归旧号，不会改归新号
            # (oracle app.js:3517-3520)。用状态栏提示（非阻塞 toast 等价）。
            if prev_uid and prev_uid != uid:
                segs = prev_uid.split("-")
                short = segs[3] if len(segs) > 3 else prev_uid
                self._status_message(
                    f"已切到新号。提醒：旧号「{short}」此前拍的照片仍归旧号"
                    "（不推荐频繁切换）", 6000,
                )
                # 资料未填完提醒：离开旧号时若它缺 保存方式/采集日期/拍摄日期 → 弹提醒，
                # 免得拍完忘了回填（拍摄当时可能还没决定怎么处理标本）。
                missing = self._missing_meta_fields(prev_uid)
                if missing:
                    QMessageBox.information(
                        self, "上一个编号资料未填完",
                        f"编号「{short}」还缺：{'、'.join(missing)}。\n"
                        "已激活下一个；左侧点该编号可随时回填编辑。",
                    )
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
            # 主动归属 → 解除"取消归属"黑名单(P0)，否则取消后归不回
            # (oracle server.js:4216-4219)。
            try:
                from app.services.grouping_service import remove_explicit_unassign
                remove_explicit_unassign(db, path)
            except Exception:
                pass
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
        """Resolve monitor selection → add JPGs (+ optional TIFF) to a group."""
        jpg_paths = self._monitor.selected_jpg_paths()
        tiff_paths = self._monitor.selected_tiff_paths()
        if not jpg_paths and not tiff_paths:
            self._status_message("请先在上方监控区选中 JPG 或 TIFF。")
            return
        if len(tiff_paths) > 1:
            self._status_message("一次只能关联 1 个 TIFF。")
            return
        tiff_path = tiff_paths[0] if tiff_paths else None
        self._grouping.drop_external_files(group_index, jpg_paths, tiff_path)
        uid = self._current_uid or getattr(self._grouping, "_uid", None)
        project_dir = self.ctx.current_project_dir
        if uid and project_dir and jpg_paths:
            try:
                from app.services.activation_service import manual_assign
                manual_assign(project_dir, uid, jpg_paths)
            except Exception:
                pass
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
            self._status_message("请先打开一个项目。")
            return

        from PyQt6.QtWidgets import QFileDialog
        paths, _ = QFileDialog.getOpenFileNames(
            self, "选择 JPG 照片",
            filter="JPG 照片 (*.jpg *.jpeg *.JPG *.JPEG)",
        )
        if not paths:
            return

        inc, _res = self._resolve_capture_subdirs()
        incoming_dir = os.path.join(project_dir, inc)
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
            self._status_message("请先打开一个项目。")
            return

        jpg_paths = self._monitor.selected_jpg_paths()
        if not jpg_paths:
            self._status_message("请先在监控区选中要合成的 JPG。")
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

        inc, _res = self._resolve_capture_subdirs()
        incoming_dir = os.path.join(project_dir, inc)
        os.makedirs(incoming_dir, exist_ok=True)
        output_name = _free_compose_output_name(incoming_dir, user_name.strip() or None)
        output_path = os.path.join(incoming_dir, output_name)
        # Honor 输出格式 (tif/jpg) — swap extension so -save: matches the encoder.
        output_path = self._with_output_ext(output_path, self._helicon_output_opts()["format"])
        output_name = os.path.basename(output_path)

        params = self._helicon_params.get_params()

        def _on_finished(tiff_path):
            if os.path.isfile(output_path):
                QMessageBox.information(self, "无号合成完成",
                                        f"TIFF 已保存到 {inc}/：\n{output_name}")
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
            self._status_message("请先打开一个项目。")
            return

        _inc0, _res0 = self._resolve_capture_subdirs()
        pre = _RetroactiveScanDialog(project_dir, parent=self, results_subdir=_res0)
        if pre.exec() != QDialog.DialogCode.Accepted:
            return
        selected_subdir = pre.selected_subdir()

        try:
            from app.services.retroactive_service import scan_project_retroactive
            # 存量整理也用项目配置的 incoming/results 子目录（与监控/合成一致）。
            inc, res = self._resolve_capture_subdirs()
            result = scan_project_retroactive(
                project_dir, db, subdir=selected_subdir,
                incoming_subdir=inc, results_subdir=res,
            )
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
            panel_uid = getattr(self._grouping, "_uid", None)
            if panel_uid:
                try:
                    from app.services.grouping_service import load_grouping
                    db = self.ctx.get_db()
                    if db is not None:
                        self._grouping.load_grouping(
                            panel_uid, load_grouping(db, panel_uid)
                        )
                except Exception:
                    pass
            self._refresh_monitor()

    def _on_auto_group_organize(self) -> None:
        """Scan a legacy mixed folder and preview JPG…TIFF time batches."""
        project_dir = self.ctx.current_project_dir
        if not project_dir or not self.ctx.get_db():
            self._status_message("请先打开一个项目。")
            return

        folder = ui.get_existing_directory(
            self,
            "选择需要自动分组整理的 JPG / TIF 目录",
            start=project_dir,
        )
        if not folder:
            return

        fallback_uid = getattr(self._grouping, "_uid", None)
        try:
            from app.services.grouping_service import ADHOC_GROUPING_UID
            if fallback_uid == ADHOC_GROUPING_UID:
                fallback_uid = None
            from app.services.retroactive_service import scan_folder_auto_groups
            result = scan_folder_auto_groups(
                folder,
                fallback_uid=fallback_uid,
            )
        except Exception as exc:
            ui.warn(self, "自动分组扫描失败", str(exc))
            return

        total_groups = sum(
            len(sp.get("groups", [])) for sp in result.get("specimens", [])
        )
        if not total_groups:
            ui.info(
                self,
                "自动分组整理",
                "没有识别到完整的 JPG…TIF 批次。请检查文件时间顺序。",
            )
            return

        from app.widgets.retroactive_modal import RetroactiveModal
        dlg = RetroactiveModal(self.ctx, result, parent=self)
        if dlg.exec() == RetroactiveModal.DialogCode.Accepted:
            panel_uid = getattr(self._grouping, "_uid", None)
            if panel_uid:
                try:
                    from app.services.grouping_service import load_grouping
                    db = self.ctx.get_db()
                    if db is not None:
                        self._grouping.load_grouping(
                            panel_uid, load_grouping(db, panel_uid)
                        )
                except Exception:
                    pass
            self._refresh_monitor()
            scan_folder = result.get("scanFolder") or folder
            self._offer_tiff_naming_check(scan_folder)

    def _offer_tiff_naming_check(self, folder: str | None) -> None:
        """Ask user to run the read-only TIF naming audit on *folder*."""
        if not folder or not os.path.isdir(folder):
            return
        if ui.question(
            self,
            "检查 TIF 命名",
            "整理已完成。是否立即检查 TIF 命名并提取标本编号？",
        ):
            self._run_tiff_naming_check(folder)

    def _run_tiff_naming_check(self, folder: str | None = None) -> None:
        """Scan *folder* (or ask user) for TIF naming compliance and show audit."""
        project_dir = self.ctx.current_project_dir
        if not project_dir:
            self._status_message("请先打开一个项目。")
            return

        if not folder:
            folder = ui.get_existing_directory(
                self,
                "选择需要检查 TIF 命名的目录",
                start=project_dir,
            )
        if not folder:
            return

        current_uid = getattr(self._grouping, "_uid", None)
        try:
            from app.services.grouping_service import ADHOC_GROUPING_UID
            if current_uid == ADHOC_GROUPING_UID:
                current_uid = None
            from app.services.project_settings_service import (
                DEFAULT_NAMING_RULES,
                load_setting,
            )
            from app.services.tiff_naming_service import inspect_tiff_names
            from app.utils.naming import component_values_from_specimen

            db = self.ctx.get_db()
            rules = (
                load_setting(db, "naming_rules", DEFAULT_NAMING_RULES)
                if db
                else DEFAULT_NAMING_RULES
            )
            components = rules.get("components", DEFAULT_NAMING_RULES["components"])

            specimen_values = None
            if current_uid and db:
                row = db.execute(
                    "SELECT * FROM specimens WHERE uid = ?", (current_uid,)
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
                    specimen_values = component_values_from_specimen(sp_dict)

            audit = inspect_tiff_names(
                folder,
                current_uid=current_uid,
                naming_components=components,
                specimen_values=specimen_values,
            )
        except Exception as exc:
            ui.warn(self, "TIF 命名检查失败", str(exc))
            return

        if audit.total == 0:
            ui.info(self, "TIF 命名检查", "所选目录中没有 TIF/TIFF 文件。")
            return

        from app.widgets.tiff_naming_audit_dialog import TiffNamingAuditDialog
        TiffNamingAuditDialog(audit, parent=self).exec()

    def _on_tiff_naming_check(self) -> None:
        """Run the independent, read-only TIFF filename audit."""
        self._run_tiff_naming_check()

    # ── Grouping ──────────────────────────────────────────────────────────────

    def _get_grouping_for_uid(self, uid: str):
        """Load grouping from DB when a project is open, else from panel memory."""
        from app.services.grouping_service import SpecimenGrouping, load_grouping

        panel = self._grouping
        if getattr(panel, "_uid", None) == uid and panel._grouping is not None:
            return panel._grouping
        db = self.ctx.get_db()
        if db:
            return load_grouping(db, uid)
        return SpecimenGrouping(uid=uid, groups=[])

    def _save_grouping_for_uid(self, uid: str, groups: list) -> None:
        """Persist grouping to DB when available; always refresh the open panel."""
        from app.services.grouping_service import SpecimenGrouping, save_grouping

        db = self.ctx.get_db()
        if db:
            save_grouping(db, uid, groups, clean_phantoms=False)
        panel = self._grouping
        if getattr(panel, "_uid", None) == uid:
            panel.load_grouping(uid, SpecimenGrouping(uid=uid, groups=groups))

    def _ensure_compose_incoming_dir(self, group) -> tuple[str, str]:
        """Return (incoming_dir, results_dir) for Helicon output.

        With a project: use configured incoming/results subdirs.
        Without: write beside the group's JPGs (or a temp fallback).
        """
        import tempfile

        project_dir = self.ctx.current_project_dir
        if project_dir:
            inc, res = self._resolve_capture_subdirs()
            incoming = os.path.join(project_dir, inc)
            results = os.path.join(project_dir, res)
            os.makedirs(incoming, exist_ok=True)
            return incoming, results
        if group and group.jpg_paths:
            incoming = os.path.dirname(os.path.abspath(group.jpg_paths[0]))
            os.makedirs(incoming, exist_ok=True)
            return incoming, incoming
        incoming = os.path.join(tempfile.gettempdir(), "specimen-photo-workbench")
        os.makedirs(incoming, exist_ok=True)
        return incoming, incoming

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
        if not uid:
            return

        try:
            from app.services.helicon_service import detect_helicon

            grouping = self._get_grouping_for_uid(uid)
            group = next(
                (g for g in grouping.groups if g.group_index == group_index), None
            )
            if group is None:
                QMessageBox.warning(self, "合成", f"找不到组 {group_index}")
                return

            from app.services.helicon_service import resolve_existing_image_path
            resolved_jpgs: list[str] = []
            missing_jpgs: list[str] = []
            for p in group.jpg_paths:
                r = resolve_existing_image_path(p)
                if r:
                    resolved_jpgs.append(r)
                else:
                    missing_jpgs.append(p)
            if missing_jpgs:
                sample = "\n".join(missing_jpgs[:5])
                extra = f"\n…等 {len(missing_jpgs)} 个" if len(missing_jpgs) > 5 else ""
                QMessageBox.warning(
                    self,
                    "合成",
                    f"以下 JPG 在磁盘上找不到：\n{sample}{extra}\n\n"
                    "请确认照片仍在 incoming-jpg 目录；WSL 下请用 /mnt/n/... 打开项目。",
                )
                return
            group.jpg_paths = resolved_jpgs

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

            # Determine output path — TIFF lands in incoming first;
            # organize step moves it to results/ (oracle app.js:4336,8867).
            incoming_dir, results_dir = self._ensure_compose_incoming_dir(group)

            # 输出名统一走 _resolve_compose_output_name:覆盖值 > 编号-序号 > 组序.tif。
            # _seq:真编号=preview.next_seq;临时分组(ad-hoc)=组序。
            output_name, _seq = self._resolve_compose_output_name(
                db, uid, group, results_dir, incoming_dir)
            output_path = os.path.join(incoming_dir, output_name)  # incoming, not results
            # Honor 输出格式 (tif/jpg); default tif keeps the lossless archival master.
            output_path = self._with_output_ext(output_path, self._helicon_output_opts()["format"])
            output_name = os.path.basename(output_path)

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
                    group.result_sequence = _seq
                    self._save_grouping_for_uid(uid, grouping.groups)

                    if db is not None:
                        try:
                            from app.services.organize_service import _bump_seq_hint
                            if _seq is not None:
                                _bump_seq_hint(db, uid, _seq)
                        except Exception:
                            pass

                    self._grouping.load_grouping(uid, grouping)
                    self._refresh_results_column(uid, grouping)
                    self._on_helicon_finished(uid)
                    QMessageBox.information(self, "合成完成", f"TIFF 已生成：{output_name}")
                    # 合成永远手动；但若「合成后自动整理」开关开 → 自动把源 JPG
                    # 打包压缩+命名+移 results（省掉手动点[整理]）。开关默认关。
                    self._maybe_auto_organize(uid, group.group_index)

                def _on_failed(msg: str):
                    if msg != "用户取消":
                        QMessageBox.warning(self, "合成失败", msg)

                self._run_helicon_stack(jpg_paths, out_path, cur_params, _on_finished, _on_failed)

            _do_compose(group.jpg_paths, output_path, params)

        except RuntimeError as exc:
            QMessageBox.warning(self, "合成失败", str(exc))
        except Exception as exc:
            QMessageBox.warning(self, "合成失败", f"意外错误：{exc}")

    def _helicon_output_opts(self) -> dict:
        """Read Helicon output options from settings (mirrors oracle 输出选项).

        Returns ``{format, tiff_compression, quality}``. Default output is TIFF
        (per the software's design: 输出 TIF 或 JPG, 默认 TIF). For TIF the chosen
        TIFF compression is returned and quality is None; for JPG, quality is
        returned and tiff_compression is None — so only the relevant CLI flag
        (-tif: / -j:) gets emitted, exactly like the oracle.
        """
        from app.views.settings_view import (
            _K_HELICON_OUTPUT_FORMAT,
            _K_HELICON_QUALITY,
            _K_HELICON_TIFF_COMPRESSION,
        )
        qs = self.ctx.settings._qs
        fmt = "jpg" if str(qs.value(_K_HELICON_OUTPUT_FORMAT, "tif")).lower() == "jpg" else "tif"
        if fmt == "jpg":
            return {
                "format": "jpg",
                "tiff_compression": None,
                "quality": int(qs.value(_K_HELICON_QUALITY, 95)),
            }
        return {
            "format": "tif",
            "tiff_compression": str(qs.value(_K_HELICON_TIFF_COMPRESSION, "u")) or "u",
            "quality": None,
        }

    @staticmethod
    def _with_output_ext(path: str, fmt: str) -> str:
        """Swap *path*'s extension to match output format (tif/jpg).

        Helicon infers the encoder from the -save: extension, so it MUST agree
        with the -tif:/-j: flag (oracle app.js:7283-7291).
        """
        base, _ = os.path.splitext(path)
        return base + (".jpg" if fmt == "jpg" else ".tif")

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

        # Wire the output options (输出格式 / TIFF 压缩 / JPEG 质量) into the CLI —
        # oracle app.js:7290-7291 (-tif: for tif, -j: for jpg). Previously these
        # settings were saved but never applied (output was always uncompressed tif).
        opts = self._helicon_output_opts()
        try:
            cmd = build_helicon_cmd(
                jpg_paths=jpg_paths,
                output_file=output_path,
                method=str(params["method"]),
                radius=str(params["radius"]),
                smoothing=str(params["smoothing"]),
                tiff_compression=opts["tiff_compression"],
                quality=opts["quality"],
                input_list_dir=os.path.dirname(os.path.abspath(output_path)),
            )
        except FileNotFoundError as exc:
            on_failed(str(exc))
            return
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

    # ── 批量[合成]/[合成+整理] 顺序队列 ────────────────────────────────────────
    # 合成是异步(HeliconWorker QThread,回调完成),整理是同步。批量绝不能紧循环
    # emit——会同时启动多个 worker 互相覆盖,且整理会在合成完成前读到空 composed。
    # 故由 workbench 串行驱动:合成完成(异步回调)→ 同步整理该组 → 下一组。
    # 批量时走 `_compose_group_headless`(无预览/结果确认框),满足"一键直合"。

    def _resolve_compose_output_name(self, db, uid, group, results_dir, incoming_dir):
        """统一的「输出 TIF 名」解析(合成单组/批量共用)。返回 (name.tif, seq)。

        优先级:
          ① 用户在该组「输出 TIF」框手填的覆盖值(去后缀+.tif)
          ② 有真编号 → organize_preview 建议成果名(编号-序号.tif)
          ③ 无编号(临时分组 ad-hoc) → 组序.tif(组0→1.tif, 组1→2.tif)
        seq:真编号取 preview.next_seq;ad-hoc 取 group_index+1。
        """
        from app.services.grouping_service import ADHOC_GROUPING_UID
        is_adhoc = (uid == ADHOC_GROUPING_UID) or db is None
        seq = (group.group_index + 1) if is_adhoc else None
        if is_adhoc:
            if getattr(group, "output_name", None):
                return Path(group.output_name).stem + ".tif", seq
            return f"{group.group_index + 1}.tif", seq
        # 真编号:用 organize_preview 取建议名 + 序号
        from app.services.organize_service import organize_preview
        preview = organize_preview(db, uid, results_dir, incoming_dir)
        if getattr(group, "output_name", None):
            return Path(group.output_name).stem + ".tif", preview.next_seq
        return preview.suggested_tiff_name, preview.next_seq

    def _start_compose_batch(self, uid: str, organise: bool) -> None:
        """启动批量合成队列。organise=True 时每组合成完后立即整理该组。"""
        if not uid:
            return
        grouping = self._get_grouping_for_uid(uid)
        selected = set()
        try:
            selected = set(self._grouping.selected_group_indexes())
        except Exception:
            selected = set()
        queue = [
            g.group_index for g in grouping.groups
            if not g.composed_tiff_path
            and (not selected or g.group_index in selected)
        ]
        if not queue:
            # 无待合成组:合成+整理 → 退而整理已合成组;纯合成 → 状态栏提示。
            if organise:
                self._organise_all_batch(uid, silent_batch=True)
            else:
                self._batch_status("无待合成组。")
            return
        # 只有确实存在待合成组时才需要 Helicon。已有 TIF 的组点
        # [合成+整理]应直接进入归档，不能被合成工具检测提前拦截。
        from app.services.helicon_service import detect_helicon
        if not detect_helicon():
            QMessageBox.warning(
                self, "未检测到 Helicon Focus",
                "未找到 Helicon Focus，无法批量合成。请安装并设置 "
                "HELICON_FOCUS_PATH 环境变量。",
            )
            return
        self._batch = {
            "uid": uid,
            "queue": queue,
            "organise": organise,
            "total": len(queue),
            "done": 0,
        }
        self._compose_next_in_batch()

    def _compose_next_in_batch(self) -> None:
        """取队首组合成;队列空 → 清状态 + 状态栏提示完成。"""
        b = self._batch
        if not b:
            return
        if not b["queue"]:
            self._batch = None
            self._batch_status("批量合成完成。")
            return
        uid = b["uid"]
        idx = b["queue"].pop(0)
        done = int(b.get("done", 0))
        total = int(b.get("total", done + 1))
        self._batch_status(f"批量合成 {done + 1}/{total}：组 {idx}")
        self._compose_group_headless(
            uid, idx, lambda ok: self._batch_group_done(ok, uid, idx)
        )

    def _batch_group_done(self, success: bool, uid: str, group_index: int) -> None:
        """单组合成回调：需要整理时，等待后台归档结束再处理下一组。"""
        if self._batch is None:
            return
        self._batch["done"] = int(self._batch.get("done", 0)) + 1
        if success and self._batch.get("organise"):
            # 一条龙:批量整理走静默模式——跳过激活拦截/TIF改名框/同名确认/成功提示,
            # 但 JPG删除四闸 + TIFF永不自动删 红线照常(都在 archive_group 内,未碰)。
            started = self._on_organise_requested(
                uid,
                group_index,
                silent_batch=True,
                on_complete=lambda _ok: self._compose_next_in_batch(),
            )
            if not started:
                self._compose_next_in_batch()
            return
        elif not success:
            done = int(self._batch.get("done", 0))
            total = int(self._batch.get("total", done))
            self._batch_status(f"组 {group_index} 合成失败，继续下一组（{done}/{total}）")
        self._compose_next_in_batch()

    def _batch_status(self, msg: str) -> None:
        """非阻塞反馈(状态栏);批量回调里绝不用模态框——会卡死且打断链路。"""
        try:
            self.window().statusBar().showMessage(msg, 4000)
        except Exception:
            pass

    def _compose_group_headless(self, uid: str, group_index: int, on_done) -> None:
        """批量用:无确认框合成单组。完成调 on_done(success: bool)。

        复刻 `_on_compose_requested` 成功路径的保存块,但剥掉预览框/结果框
        (满足"一键直合不弹框")。组 JPG < 2 直接 on_done(False) 跳过。
        产出名:每组 output_name 覆盖 > 否则 organize_preview 的建议成果名。
        """
        db = self.ctx.get_db()
        if not uid:
            on_done(False)
            return
        try:
            grouping = self._get_grouping_for_uid(uid)
            group = next(
                (g for g in grouping.groups if g.group_index == group_index), None
            )
            if group is None or len(group.jpg_paths) < 2:
                on_done(False)  # 批量不弹隐式兜底问句,JPG 不足直接跳过
                return

            incoming_dir, results_dir = self._ensure_compose_incoming_dir(group)

            output_name, _seq = self._resolve_compose_output_name(
                db, uid, group, results_dir, incoming_dir)
            output_path = os.path.join(incoming_dir, output_name)
            output_path = self._with_output_ext(
                output_path, self._helicon_output_opts()["format"]
            )
            params = self._helicon_params.get_params()

            def _ok(tiff_path):
                if not os.path.isfile(output_path):
                    on_done(False)
                    return
                from datetime import datetime, timezone
                now = datetime.now(tz=timezone.utc).isoformat()
                group.composed_tiff_path = output_path
                group.status = "composed"
                group.updated_at = now
                group.result_sequence = _seq
                self._save_grouping_for_uid(uid, grouping.groups)
                if db is not None:
                    try:
                        from app.services.organize_service import _bump_seq_hint
                        if _seq is not None:
                            _bump_seq_hint(db, uid, _seq)
                    except Exception:
                        pass
                self._grouping.load_grouping(uid, grouping)
                self._refresh_results_column(uid, grouping)
                self._on_helicon_finished(uid)
                on_done(True)

            def _fail(msg: str):
                on_done(False)

            self._run_helicon_stack(group.jpg_paths, output_path, params, _ok, _fail)
        except Exception:
            on_done(False)

    def _organise_all_batch(self, uid: str, silent_batch: bool = False) -> None:
        """[🗜整理] 批量:逐组同步整理「已合成未归档」组(整理本身是同步阻塞)。"""
        if not uid:
            return
        grouping = self._get_grouping_for_uid(uid)
        selected = set()
        try:
            selected = set(self._grouping.selected_group_indexes())
        except Exception:
            selected = set()
        targets = [
            g.group_index for g in grouping.groups
            if g.composed_tiff_path and g.status != "organized"
            and (not selected or g.group_index in selected)
        ]
        for idx in targets:
            self._on_organise_requested(uid, idx, silent_batch=silent_batch)

    def _on_organise_selected(self) -> None:
        """整理监控区选中的 JPG + TIFF，不重新合成。

        有激活编号：TIFF 自动按激活编号的下一个成果名重命名，ZIP 同名。
        无激活编号：保留 TIFF 文件名，ZIP 直接用 TIFF stem 命名。
        """
        try:
            jpg_paths = self._monitor.selected_jpg_paths()
            tiff_paths = self._monitor.selected_tiff_paths()
        except Exception:
            jpg_paths, tiff_paths = [], []
        if len(tiff_paths) != 1 or not jpg_paths:
            QMessageBox.information(
                self,
                "整理",
                "请选中至少 1 张 JPG 原片和 1 个 TIFF 成片。",
            )
            return
        self._organise_jpgs_with_tiff(jpg_paths, tiff_paths[0], silent=True)

    def _organise_jpgs_with_tiff(
        self,
        jpg_paths: list[str],
        tiff_path: str,
        *,
        silent: bool,
    ) -> bool:
        """把已有 JPG + TIFF 登记成已合成组，然后走统一整理/压缩入口。"""
        db = self.ctx.get_db()
        project_dir = self.ctx.current_project_dir
        if not db or not project_dir:
            self._status_message("请先打开项目")
            return False
        from app.services.grouping_service import (
            ADHOC_GROUPING_UID,
            Group,
            load_grouping,
            save_grouping,
        )
        from app.services.organize_service import organize_preview, rename_tiff

        active_uid = self._get_active_uid()
        uid = active_uid or ADHOC_GROUPING_UID

        if active_uid:
            try:
                inc, res = self._resolve_capture_subdirs()
                preview = organize_preview(
                    db,
                    active_uid,
                    os.path.join(project_dir, res),
                    os.path.join(project_dir, inc),
                )
                if Path(tiff_path).name != preview.suggested_tiff_name:
                    tiff_path = rename_tiff(tiff_path, preview.suggested_tiff_name)
            except Exception as exc:
                if silent:
                    self._status_message(f"TIFF 按编号命名失败：{exc}")
                else:
                    QMessageBox.warning(self, "整理", f"TIFF 按编号命名失败：{exc}")
                return False

        grouping = load_grouping(db, uid)
        next_idx = max([g.group_index for g in grouping.groups], default=-1) + 1
        grouping.groups.append(
            Group(
                group_index=next_idx,
                jpg_paths=jpg_paths,
                composed_tiff_path=tiff_path,
                status="composed",
            )
        )
        save_grouping(db, uid, grouping.groups, clean_phantoms=False)
        self._grouping.load_grouping(uid, grouping)
        return bool(self._on_organise_requested(
            uid,
            next_idx,
            silent_batch=silent,
            allow_single_jpg=True,
        ))

    def _maybe_auto_organize(self, uid: str, group_index: int) -> None:
        """合成成功后的自动整理钩子。

        「合成后自动整理归档」开关（`auto_organize_after_compose`，默认关）打开时，
        直接复用手动整理入口 `_on_organise_requested`：把这组源 JPG 打包压缩
        (JPG→JXL+ZIP) + 命名 + 移到 results/。合成本身仍是手动（软件无法判断哪些
        JPG 该合成）。整理的安全闸（整理门 / 同名 ZIP 覆盖确认）照常生效；常见情况
        （标本激活、无同名冲突）会直通无弹窗。绝不在此自动删 TIFF。
        """
        if bool(getattr(self.ctx.settings, "auto_organize_after_compose", False)):
            self._on_organise_requested(uid, group_index)

    def _maybe_rename_tiff_before_organize(self, db, uid, grouping, group, project_dir):
        """整理前的 TIFF 命名网关。返回 None=无需改名 / True=已改名 / False=用户取消。

        合成 TIFF 名不符成果规范时（导入的外部 Helicon TIFF），弹确认框按本组编号的下个
        成果名建议改名（可改），确认则磁盘改名 + 更新 group.composed_tiff_path + 持久化。
        """
        from app.utils.naming import validate_uid
        from app.services.organize_service import organize_preview, rename_tiff
        from app.services.grouping_service import save_grouping
        from app.widgets.tiff_rename_dialog import TiffRenameDialog

        tiff_path = group.composed_tiff_path
        current = Path(tiff_path).name
        if validate_uid(Path(tiff_path).stem):
            return None  # 已规范，无需改名

        inc, res = self._resolve_capture_subdirs()
        try:
            preview = organize_preview(
                db, uid,
                os.path.join(project_dir, res),
                os.path.join(project_dir, inc),
            )
            suggested = preview.suggested_tiff_name
        except Exception:
            suggested = current

        dlg = TiffRenameDialog(current, suggested, parent=self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return False  # 取消 → 中止整理
        new_name = dlg.new_name()
        if not new_name:
            return False
        try:
            new_path = rename_tiff(tiff_path, new_name)
        except Exception as exc:
            QMessageBox.warning(self, "整理", f"TIFF 改名失败：{exc}")
            return False
        group.composed_tiff_path = new_path
        try:
            save_grouping(db, uid, grouping.groups, clean_phantoms=False)
        except Exception:
            pass
        return True

    def _on_organise_requested(
        self,
        uid: str,
        group_index: int,
        silent_batch: bool = False,
        allow_single_jpg: bool = False,
        on_complete=None,
    ) -> bool:
        """Organise (archive) the composed group.

        silent_batch=True(批量[合成+整理]一条龙):跳过激活拦截框、TIF改名框、
        同名ZIP确认框、成功提示框,失败静默返回——给"一键直跑"用。红线不变:
        JPG删除四闸 + TIFF永不自动删 都在 archive_group 内,silent 不绕。


        Gate checks (via organize_service._check_organize_gate):
          - uid must be active (or user explicitly bypasses)
          - group must have ≥2 JPGs
          - TIFF must already be composed

        delete_jpg is READ from settings; defaults to True after verified archival.
        Calls archive_service.archive_group (JPG→JXL→ZIP + optional delete).

        Oracle: server.js:3615-3840 organizeSpecimen; archive.js:67-190.
        """
        db = self.ctx.get_db()
        project_dir = self.ctx.current_project_dir
        if not db or not project_dir or not uid:
            return False

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
                return False

            if not group.composed_tiff_path:
                if not silent_batch:
                    QMessageBox.warning(
                        self, "整理", "该组尚未合成，请先合成 TIFF 再整理。"
                    )
                return False

            # 整理前：若合成 TIFF 名不符成果命名规范（多见于导入的外部 Helicon TIFF），
            # 弹「TIFF 命名需确认」框，按本组编号成果名建议改名（守 S5：默认本组号、可改）。
            # 取消则中止整理；in-app 合成的 TIFF 本就规范，此路不触发。
            # silent_batch:批量时输出名是我们自己定的(编号-序号 / 组序.tif),不弹改名。
            if not silent_batch and self._maybe_rename_tiff_before_organize(
                db, uid, grouping, group, project_dir
            ) is False:
                return False

            # Gate check (uid must be active)。silent_batch=允许未激活(临时分组/一条龙)。
            if allow_single_jpg:
                if not silent_batch:
                    try:
                        _check_organize_gate(
                            db, uid, [{"jpgPaths": [1, 2]}], allow_inactive=False
                        )
                    except OrganizeGateError as e:
                        reply = QMessageBox.question(
                            self,
                            "整理确认",
                            f"{e}\n\n是否跳过激活检查继续整理？",
                            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                            QMessageBox.StandardButton.No,
                        )
                        if reply != QMessageBox.StandardButton.Yes:
                            return False
            else:
                try:
                    groups_as_dicts = [
                        {"jpgPaths": g.jpg_paths} for g in grouping.groups
                    ]
                    _check_organize_gate(db, uid, groups_as_dicts,
                                         allow_inactive=silent_batch)
                except OrganizeGateError as e:
                    if silent_batch:
                        return False  # 静默跳过(如 JPG 不足),不弹框
                    reply = QMessageBox.question(
                        self,
                        "整理确认",
                        f"{e}\n\n是否跳过激活检查继续整理？",
                        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                        QMessageBox.StandardButton.No,
                    )
                    if reply != QMessageBox.StandardButton.Yes:
                        return False

            if not group.jpg_paths:
                if not silent_batch:
                    QMessageBox.warning(self, "整理", "该组无 JPG 原片，无法归档。")
                return False

            # Default workflow: verified ZIP replaces loose JPGs. TIFF is never deleted.
            delete_jpg: bool = True
            try:
                delete_jpg = bool(
                    getattr(self.ctx.settings, "delete_jpg_after_archive", True)
                )
            except Exception:
                pass

            # 用项目配置的 incoming/results 子目录（用户可改目录名 / 遗留 新拍JPG）。
            inc, res = self._resolve_capture_subdirs()

            # ── Collision guard: if ZIP already exists at the target path,  #cursor
            #    warn and let user choose overwrite / skip. ──────────────
            tiff_stem = Path(group.composed_tiff_path).stem
            results_dir_for_check = str(Path(project_dir) / res)
            existing_zip = os.path.join(results_dir_for_check, tiff_stem + ".zip")
            if os.path.isfile(existing_zip):
                if silent_batch:
                    return False  # 已有同名归档 → 静默跳过(不静默覆盖)
                reply_col = QMessageBox.question(
                    self,
                    "归档文件已存在",
                    f"同名归档 ZIP 已存在：\n{Path(existing_zip).name}\n\n"
                    "是否覆盖并重新归档？",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No,
                )
                if reply_col != QMessageBox.StandardButton.Yes:
                    return False

            # Archive in a worker thread.  A 60-photo lossless JXL archive can
            # take several minutes; doing this in the GUI thread made the whole
            # window appear hung and gave the user no indication that work was
            # still in progress.
            from app.workers.supp_compression_worker import SuppCompressionWorker

            progress = QProgressDialog("准备压缩 JPG…", "", 0, len(group.jpg_paths), self)
            progress.setWindowTitle("整理 JPG 原片")
            progress.setCancelButton(None)
            progress.setWindowModality(Qt.WindowModality.NonModal)
            progress.setMinimumDuration(0)
            progress.setValue(0)

            worker = SuppCompressionWorker(
                jpg_paths=group.jpg_paths,
                tiff_path=group.composed_tiff_path,
                project_dir=project_dir,
                delete_jpg=delete_jpg,
                method=getattr(self.ctx.settings, "jxl_effort_method", "standard"),
                concurrency=getattr(self.ctx.settings, "jxl_concurrency", 4),
                parent=self,
            )
            if not hasattr(self, "_archive_workers"):
                self._archive_workers = set()
            self._archive_workers.add(worker)

            def _release_worker() -> None:
                progress.close()
                self._archive_workers.discard(worker)
                worker.deleteLater()

            def _archive_progress(current: int, total: int, filename: str) -> None:
                progress.setMaximum(total)
                progress.setValue(max(0, current - 1))
                progress.setLabelText(
                    f"正在压缩第 {current}/{total} 张\n{filename}"
                )

            def _archive_failed(message: str) -> None:
                _release_worker()
                self._batch_status(f"整理失败：{message}")
                if not silent_batch:
                    QMessageBox.warning(self, "整理失败", message or "归档过程出现错误。")
                if on_complete is not None:
                    on_complete(False)

            def _archive_finished(result) -> None:
                if not result.ok:
                    _archive_failed("归档过程出现错误。")
                    return
                _release_worker()
                # ── Move TIFF + ZIP from incoming → results/ (oracle app.js:4336)
                import shutil
                _results_dir = os.path.join(project_dir, res)
                os.makedirs(_results_dir, exist_ok=True)
                _tiff_src = group.composed_tiff_path
                _zip_src = result.zip_path
                _moved_tiff = _tiff_src

                def _in_incoming(p: str) -> bool:
                    # 路径里出现解析后的 incoming 子目录名（incoming-jpg / 新拍JPG /
                    # 自定义）即视为在 incoming，需移到 results。比写死 "incoming-jpg"
                    # 子串更稳（项目改了目录名也认）。
                    return bool(p) and inc in os.path.normpath(p).split(os.sep)

                for _src in [_tiff_src, _zip_src]:
                    if _src and os.path.isfile(_src) and _in_incoming(_src):
                        _dst = os.path.join(_results_dir, os.path.basename(_src))
                        if not os.path.exists(_dst):
                            shutil.move(_src, _dst)
                        if _src == _tiff_src:
                            _moved_tiff = _dst

                # Update grouping record with archive info
                from datetime import datetime, timezone
                now = datetime.now(tz=timezone.utc).isoformat()
                group.status = "organized"
                group.composed_tiff_path = _moved_tiff  # update to results/ path
                group.archive_zip = (
                    os.path.join(_results_dir, os.path.basename(result.zip_path))
                    if _in_incoming(result.zip_path)
                    else result.zip_path
                )
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
                if not silent_batch:
                    QMessageBox.information(self, "整理完成", msg)
                    self._offer_tiff_naming_check(_results_dir)
                else:
                    self._batch_status(
                        f"整理完成：{Path(group.archive_zip).name}（{result.file_count} 张 JPG）"
                    )
                if on_complete is not None:
                    on_complete(True)

            worker.progress.connect(_archive_progress)
            worker.finished.connect(_archive_finished)
            worker.failed.connect(_archive_failed)
            progress.show()
            worker.start()
            return True

        except FileNotFoundError as exc:
            if not silent_batch:
                QMessageBox.warning(self, "整理失败", f"文件不存在：{exc}")
            return False
        except Exception as exc:
            if not silent_batch:
                QMessageBox.warning(self, "整理失败", f"意外错误：{exc}")
            return False

    # ── 补处理 (supplementary archival) ────────────────────────────────────────
    #   Archive a selected JPG + TIFF bundle WITHOUT requiring an active specimen.
    #   The specimen identity is read from the TIFF filename (oracle
    #   processSelectedMonitorFiles / startSmartCompression, app.js:4407/4282).

    def _on_supplementary_process(self) -> None:
        """补处理 button clicked → consume the monitor selection."""
        from app.utils import ui
        paths = self._monitor.selected_all_paths()
        if not paths:
            ui.info(self, "补处理", "请先在监控区选择 JPG 原片与 TIFF 成片")
            return
        self._run_supplementary(paths)

    def _on_supplementary_dropped(self, paths: list) -> None:
        """Files dropped onto the 补处理 button → archive them directly."""
        if paths:
            self._run_supplementary(list(paths))

    def _supp_autoname_tiff_by_active(self, db, project_dir, paths: list) -> list:
        """补处理前的兜底：外部名 TIF + 有激活编号 → 自动按激活编号成果名改名。

        只在「TIF 文件名反查不到标本」且「有激活编号」时改名；TIF 名本就规范则原样。
        返回（可能已把 TIF 路径替换为新名后的）路径列表。
        """
        try:
            from app.services.supplementary_service import resolve_specimen_for_tiff
            from app.services.organize_service import organize_preview, rename_tiff
        except Exception:
            return paths
        tiffs = [p for p in paths if str(p).lower().endswith((".tif", ".tiff"))]
        if len(tiffs) != 1:
            return paths
        tiff = tiffs[0]
        try:
            if resolve_specimen_for_tiff(db, Path(tiff).name) is not None:
                return paths  # 名能反查 → 不动
        except Exception:
            return paths
        active = self._get_active_uid()
        if not active:
            return paths  # 无激活编号 → 维持原状(会在 validate 报命名不规范)
        try:
            inc, res = self._resolve_capture_subdirs()
            preview = organize_preview(
                db, active,
                os.path.join(project_dir, res),
                os.path.join(project_dir, inc),
            )
            new_path = rename_tiff(tiff, preview.suggested_tiff_name)
        except Exception:
            return paths
        return [new_path if p == tiff else p for p in paths]

    def _run_supplementary(self, paths: list) -> None:
        from app.services.supplementary_service import (
            validate_supp_group,
            SuppGroupError,
        )
        from app.workers.supp_compression_worker import SuppCompressionWorker
        from app.utils import ui

        db = self.ctx.get_db()
        project_dir = self.ctx.current_project_dir
        if not db or not project_dir:
            ui.info(self, "补处理", "请先打开一个项目。")
            return

        # 激活编号兜底命名：补处理本来只从 TIF 文件名反查标本；若 TIF 是外部名(反查
        # 不到)但当前有激活编号 → 自动按激活编号的成果名给 TIF 改名，再走补处理。
        # 落地"激活 → 自动命名"（用户设计），免得外部 Helicon 的 TIF 因名不规范被卡。
        paths = self._supp_autoname_tiff_by_active(db, project_dir, list(paths))

        # Validate selection → resolve specimen from TIFF name.
        try:
            grp = validate_supp_group(db, paths)
        except SuppGroupError as exc:
            ui.warn(self, "补处理", str(exc))
            return

        # Collision guard: same-named result already in results/ (decision①: → results/).
        _inc, res = self._resolve_capture_subdirs()
        results_dir = Path(project_dir) / res
        tiff_stem = Path(grp.tiff_path).stem
        existing_zip = results_dir / f"{tiff_stem}.zip"
        existing_tiff = results_dir / Path(grp.tiff_path).name
        if existing_zip.is_file() or (
            existing_tiff.is_file()
            and str(existing_tiff) != str(Path(grp.tiff_path))
        ):
            reply = ui.question(
                self,
                "归档文件已存在",
                f"results/ 下已存在同名成果：\n{tiff_stem}.*\n\n是否覆盖并重新归档？",
            )
            if reply != QMessageBox.StandardButton.Yes:
                return

        delete_jpg: bool = True
        try:
            delete_jpg = bool(
                getattr(self.ctx.settings, "delete_jpg_after_archive", True)
            )
        except Exception:
            pass

        # Stash for the finished handler (move-to-results + UI refresh).
        self._supp_pending = grp
        self._supp_worker = SuppCompressionWorker(
            grp.jpg_paths,
            grp.tiff_path,
            project_dir,
            delete_jpg=delete_jpg,
            method=getattr(self.ctx.settings, "jxl_effort_method", "standard"),
            concurrency=getattr(self.ctx.settings, "jxl_concurrency", 4),
            parent=self,
        )
        self._supp_worker.started_archiving.connect(self._on_supp_started)
        self._supp_worker.finished.connect(self._on_supp_finished)
        self._supp_worker.failed.connect(self._on_supp_failed)
        self._supp_worker.start()

    def _on_supp_started(self, jpg_count: int, tiff_stem: str) -> None:
        try:
            win = self.window()
            bar = win.statusBar() if hasattr(win, "statusBar") else None
            if bar is not None:
                bar.showMessage(f"正在归档 {jpg_count} 张原片 → {tiff_stem}.zip", 4000)
        except Exception:
            pass

    def _on_supp_finished(self, result) -> None:
        """Move TIFF + ZIP into results/ (decision①), then refresh + toast."""
        from app.utils import ui
        grp = getattr(self, "_supp_pending", None)
        self._supp_pending = None
        project_dir = self.ctx.current_project_dir
        if not result or not getattr(result, "ok", False) or grp is None or not project_dir:
            ui.warn(self, "补处理", "归档过程出现错误。")
            return

        import shutil

        _inc, res = self._resolve_capture_subdirs()
        results_dir = Path(project_dir) / res
        results_dir.mkdir(exist_ok=True)

        # Move the ZIP into results/ (zip is not a red-line artifact; replace OK).
        final_zip = Path(result.zip_path)
        try:
            if final_zip.is_file() and final_zip.parent != results_dir:
                dest_zip = results_dir / final_zip.name
                if dest_zip.is_file():
                    dest_zip.unlink()
                shutil.move(str(final_zip), str(dest_zip))
                final_zip = dest_zip
        except Exception:
            pass

        # Move the TIFF into results/ (data preserved — moved, never deleted).
        try:
            src_tiff = Path(grp.tiff_path)
            if src_tiff.is_file() and src_tiff.parent != results_dir:
                dest_tiff = results_dir / src_tiff.name
                shutil.move(str(src_tiff), str(dest_tiff))  # replaces same-named result
        except Exception:
            pass

        # Refresh monitor; refresh results column if the archived specimen is loaded.
        self._refresh_monitor()
        try:
            from app.services.grouping_service import load_grouping
            db = self.ctx.get_db()
            if db is not None and getattr(self, "_current_uid", None) == grp.uid:
                self._refresh_results_column(grp.uid, load_grouping(db, grp.uid))
        except Exception:
            pass
        self._on_organize_finished(grp.uid)

        msg = (
            f"归档完成：{final_zip.name}\n"
            f"压缩率：{result.saved_percent}%\n"
        )
        if result.delete_jpg:
            msg += "JPG 原片已删除。"
        elif result.requested_delete_jpg and not result.delete_jpg:
            msg += f"JPG 保留（{result.deletion_skipped_reason}）。"
        else:
            msg += "JPG 原片已保留。"
        ui.info(self, "补处理完成", msg)

    def _on_supp_failed(self, message: str) -> None:
        from app.utils import ui
        self._supp_pending = None
        ui.warn(self, "补处理", f"归档失败: {message}")

    # ── 还原归档 JPG ──────────────────────────────────────────────────────────

    def _on_restore_archive(self, zip_path: str) -> None:
        """Recover the original JPGs from a result ZIP into a user-chosen folder.

        Read-only against the archive + additive (writes new JPGs, deletes
        nothing). Heavy djxl work runs off-thread in RestoreWorker.
        """
        from app.utils import ui
        from PyQt6.QtWidgets import QMessageBox
        from app.workers.restore_worker import RestoreWorker

        if not zip_path or not Path(zip_path).is_file():
            ui.warn(self, "还原原片", "归档文件不存在。")
            return

        out = ui.get_existing_directory(self, "选择还原 JPG 的输出文件夹")
        if not out:
            return

        overwrite = False
        try:
            if any(True for _ in os.scandir(out)):  # 目录非空
                reply = ui.question(
                    self, "目标文件夹非空",
                    "目标文件夹已有文件。同名 JPG 是否覆盖？\n（选「否」则跳过已存在的文件）",
                )
                overwrite = (reply == QMessageBox.StandardButton.Yes)
        except Exception:
            pass

        count = 0
        try:
            import zipfile
            with zipfile.ZipFile(zip_path) as zf:
                count = sum(1 for n in zf.namelist() if n != "manifest.json")
        except Exception:
            pass

        self._restore_worker = RestoreWorker(
            zip_path, out, overwrite=overwrite, file_count=count, parent=self
        )
        self._restore_worker.started.connect(self._on_restore_started)
        self._restore_worker.finished.connect(self._on_restore_finished)
        self._restore_worker.failed.connect(self._on_restore_failed)
        self._restore_worker.start()

    def _on_restore_started(self, count: int) -> None:
        try:
            bar = self.window().statusBar()
            if bar is not None:
                n = f"{count} 张" if count else "原片"
                bar.showMessage(f"正在还原 {n} JPG …", 4000)
        except Exception:
            pass

    def _on_restore_finished(self, result) -> None:
        from app.utils import ui
        if result is None:
            ui.critical(self, "还原原片", "还原过程出现错误。")
            return
        if not getattr(result, "ok", False):
            reason = getattr(result, "reason", "") or "；".join(result.failures[:3])
            ui.critical(self, "还原失败", reason or "还原失败，未输出文件。")
            return

        msg = f"已还原 {result.count} 张 JPG →\n{result.output_dir}"
        if result.skipped:
            msg += f"\n已跳过 {len(result.skipped)} 个已存在文件。"
        if result.failures:
            msg += f"\n{len(result.failures)} 个失败：" + "；".join(result.failures[:3])
        ui.info(self, "还原完成", msg)

    def _on_restore_failed(self, message: str) -> None:
        from app.utils import ui
        ui.critical(self, "还原原片", f"还原失败: {message}")

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

    def _on_archive_zip_registered(self, uid: str, group_index: int) -> None:
        """Persist an existing ZIP association selected in the grouping panel."""
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
            QMessageBox.warning(self, "注册 ZIP", f"保存失败：{exc}")

    def _on_undo_compose(self, uid: str, group_index: int) -> None:
        """撤销合成 = 删除这张合成 TIFF + 把关联 JPG 解组放回自由池。

        用户选定语义（拍照区核心 = 中间 JPG ↔ 对应 TIFF 的关联）：TIFF 一旦删除，
        关联失去意义 → 这组 JPG 退出分组、回到监控自由池（未分组，可重新分组/重拍）。
        因删 TIFF 不可恢复 → 删前弹确认框（默认否）。取消则全保留、原样不动。
        """
        db = self.ctx.get_db()
        if not db:
            return
        from app.services.grouping_service import load_grouping, save_grouping
        grouping = load_grouping(db, uid)
        target = next(
            (g for g in grouping.groups
             if g.group_index == group_index and g.composed_tiff_path),
            None,
        )
        if target is None:
            return

        reply = QMessageBox.question(
            self, "撤销合成",
            "撤销将删除这张合成 TIFF（不可恢复），并把关联的 JPG 放回自由池"
            "（可重新分组/合成）。确认？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        # ① 删除 TIFF（用户主权；非自动流程，手动确认后删）。
        try:
            if target.composed_tiff_path and os.path.isfile(target.composed_tiff_path):
                os.unlink(target.composed_tiff_path)
        except OSError as exc:
            QMessageBox.warning(self, "撤销合成", f"TIFF 删除失败：{exc}")
            return

        # ② JPG 解关联：移除整组 → 这些 JPG 回到自由池（未分组）。
        grouping.groups = [g for g in grouping.groups if g.group_index != group_index]
        try:
            save_grouping(db, uid, grouping.groups, clean_phantoms=False)
            self._grouping.load_grouping(uid, grouping)
            self._refresh_monitor()
            self._refresh_results_column(uid, grouping)
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

    def _schedule_rail_save(self) -> None:
        """Debounce a right-rail autosave (卡2/卡3 live edits)."""
        if self._current_uid:
            self._rail_save_timer.start()

    def _flush_rail_save(self) -> None:
        if self._current_uid:
            self._on_save_metadata(self._current_uid, reload=False)

    def _on_save_metadata(self, uid: str, reload: bool = True) -> None:
        """Persist right-rail edits to the DB specimens table.

        Mirrors the web whole-`sp` persist (scheduleRightPanelPersist): one save
        gathers every right-rail field across the three cards —
        卡1 命名(日期/保存方式/拍照备注), 卡2 分类(拉丁/中名/备注), 卡3 元数据
        (采集人/拍摄人/鉴定人/经纬度/地理区).  ``reload=False`` for autosave so the
        focused input does not lose its cursor mid-edit.
        """
        db = self.ctx.get_db()
        if not db:
            return
        panel = self._metadata
        naming = self._naming
        fields: dict[str, str] = {
            # 卡3 元数据
            "collector":       panel._collector.text(),
            "photographer":    panel._photographer.text(),
            "identifier":      panel._identifier.text(),
            "geo_area":        panel._geo_area.text(),
            # 卡1 命名（日期 / 保存方式 / 拍照备注）
            "collection_date": naming._collection_date.text(),
            "photo_date":      naming._photo_date.text(),
            "storage":         naming._storage.text(),
            "photo_notes":     naming._photo_notes.toPlainText(),
        }
        # 卡2 分类字段（拉丁 + 中名 + 备注）来自独立的「分类标签」卡片
        fields.update(self._taxon_card.field_values())
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

        # Refresh naming panel with latest values if storage changed.  Skipped
        # for autosave (reload=False) so the focused input keeps its cursor.
        if reload:
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
                seq = getattr(g, "result_sequence", None)
                tiff_path = getattr(g, "composed_tiff_path", None)
                if tiff_path:
                    composed_tiffs.append({
                        "path": tiff_path,
                        "name": os.path.basename(tiff_path),
                        "seq": seq,
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
                        "seq": seq,
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

    def _build_implicit_group(
        self, uid: str, jpg_paths: Optional[list[str]] = None
    ) -> Optional[int]:
        """隐式消耗模型：把激活编号下「未占用」JPG（已归属但还没进任何组）建成一个新组。

        返回新组 group_index；无未占用 JPG（<2 张）则返回 None。占用 = 已在任何分组里
        （草稿或已合成）。一次合成消耗一批；再拍的 JPG 又是未占用 → 下次再合成成新组。
        镜像 web composeImplicitActiveBatch（app.js:5660）。
        """
        db = self.ctx.get_db()
        if not db or not uid:
            return None
        from app.services.grouping_service import load_grouping, save_grouping, Group
        attributed = list(jpg_paths) if jpg_paths is not None else self._get_attributed_jpg_paths(uid)
        un_occupied = self._unoccupied_jpg_paths(uid, attributed)
        if len(un_occupied) < 2:
            return None
        grouping = load_grouping(db, uid)
        next_idx = max([g.group_index for g in grouping.groups], default=-1) + 1
        grouping.groups.append(
            Group(group_index=next_idx, jpg_paths=un_occupied, status="pending")
        )
        save_grouping(db, uid, grouping.groups, clean_phantoms=False)
        self._grouping.load_grouping(uid, grouping)
        return next_idx

    def _unoccupied_jpg_paths(self, uid: str, jpg_paths: list[str]) -> list[str]:
        """Filter out JPGs already present in any group for this UID."""
        db = self.ctx.get_db()
        if not db or not uid:
            return []
        try:
            from app.services.grouping_service import load_grouping
            grouping = load_grouping(db, uid)
        except Exception:
            return list(jpg_paths)
        occupied: set[str] = set()
        for group in grouping.groups:
            for path in group.jpg_paths:
                try:
                    occupied.add(str(Path(path).resolve()))
                except Exception:
                    occupied.add(path)
        unoccupied: list[str] = []
        for path in jpg_paths:
            try:
                key = str(Path(path).resolve())
            except Exception:
                key = path
            if key not in occupied:
                unoccupied.append(path)
        return unoccupied

    def _on_compose_implicit(self, organise: bool = False) -> None:
        """主界面[合成]：隐式合成激活编号下未占用的新 JPG → 自动命名 编号-序号。

        这是拍照主流程（拍→合成→拍→合成自动成组+命名），无需打开分组工具。
        """
        uid = self._get_active_uid()
        if not uid:
            self._status_message("请先激活一个编号，再合成")
            return
        selected = []
        try:
            selected = self._monitor.selected_jpg_paths()
        except Exception:
            selected = []
        idx = self._build_implicit_group(uid, selected or None)
        if idx is None:
            self._status_message("没有可合成的未占用 JPG（至少 2 张）")
            return
        silent = bool(getattr(self.ctx.settings, "silent_compose", False))
        if silent or organise:
            self._compose_group_headless(
                uid,
                idx,
                lambda ok: self._implicit_compose_done(ok, uid, idx, organise),
            )
            return
        self._on_compose_requested(uid, idx)

    def _implicit_compose_done(
        self, success: bool, uid: str, group_index: int, organise: bool
    ) -> None:
        if not success:
            self._status_message("合成失败或未生成成果")
            return
        if organise:
            self._on_organise_requested(uid, group_index, silent_batch=True)
            self._status_message("合成+整理完成")
        else:
            self._status_message("合成完成，成果已生成在 incoming")

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

    def _collab_operator(self) -> str | None:
        """Current operator name (for task assignee), read safely from settings."""
        try:
            raw = self.ctx.settings._qs.value("user/current_user", "")
            name = str(raw).strip()
            return name or None
        except Exception:
            return None

    def _on_open_collab_panel(self) -> None:
        """Show collab panel drawer positioned at the right edge."""
        self._collab_panel.refresh()
        try:
            win_rect = self.rect()
            self._collab_scrim.setGeometry(win_rect)
            self._collab_scrim.show()
            self._collab_scrim.raise_()
            p = self._collab_panel
            p.setGeometry(
                win_rect.right() - p.width(), 0,
                p.width(), win_rect.height()
            )
            p.show()
            p.raise_()
        except Exception:
            self._collab_panel.show()

    def _close_collab_panel(self) -> None:
        """Dismiss the collab panel and its backdrop scrim."""
        self._collab_scrim.hide()
        self._collab_panel._on_close()

    def _refresh_collab_card(self) -> None:
        """Refresh the right-rail collab card when tasks change."""
        if self._current_uid:
            self._collab_card.load_specimen(self._current_uid)

    def _on_open_settings(self) -> None:
        """Show project settings drawer positioned at the right edge."""
        self._settings_drawer.refresh()
        try:
            win_rect = self.rect()
            # Backdrop scrim covers the whole view, drawer sits on top of it.
            self._settings_scrim.setGeometry(win_rect)
            self._settings_scrim.show()
            self._settings_scrim.raise_()
            dw = self._settings_drawer
            dw.setGeometry(
                win_rect.right() - dw.width(), 0,
                dw.width(), win_rect.height()
            )
            dw.show()
            dw.raise_()
        except Exception:
            self._settings_drawer.show()

    def _close_settings(self) -> None:
        """Dismiss the settings drawer and its backdrop scrim."""
        self._settings_scrim.hide()
        self._settings_drawer._on_close()

    def _show_no_project(self) -> None:
        self._sidebar.refresh()  # clears list
        self._monitor.clear()
        self._grouping.clear()
        self._results.clear()
        self._metadata.clear()
        self._taxon_card.clear()
        self._naming.load_specimen({})  # 清命名卡残留字段
        self._current_uid = None
        self._refresh_header()
        self._no_project_banner.show()
