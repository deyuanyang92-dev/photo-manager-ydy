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
import logging
import os
import re
import shutil
import sqlite3
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

from PyQt6.QtCore import (
    QByteArray,
    QEvent,
    QFileSystemWatcher,
    QSettings,
    Qt,
    QTimer,
)
from PyQt6.QtGui import QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressDialog,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSplitter,
    QToolButton,
    QVBoxLayout,
    QWidget,
    QWIDGETSIZE_MAX,
)

from app.workers.helicon_worker import HeliconWorker

from app.config.theme import TOKENS
from app.services.compose_workflow_service import (
    SelectedComposeTarget as _SelectedComposeTarget,
    detect_external_tiff_candidate,
    free_compose_output_name as _free_compose_output_name,
    pending_tiff_paths,
    persist_composed_group,
    resolve_external_tiff_jpg_source,
)
from app.services.organize_workflow_service import (
    compose_batch_queue,
    inspect_organize_group,
    organize_batch_targets,
    plan_archive_worker,
    plan_organize_gate_check,
    prepare_existing_tiff_group,
)
from app.utils import ui
from app.utils.path_utils import equivalent_paths
from app.views.base_view import BaseView
from app.views.workbench_collab_drawers import WorkbenchCollabDrawerMixin
from app.views.workbench_media_workflow import WorkbenchMediaWorkflowMixin
from app.views.workbench_monitor_workflow import WorkbenchMonitorWorkflowMixin
from app.views.workbench_specimen_identity import WorkbenchSpecimenIdentityMixin
from app.widgets.grouping_panel import GroupingPanel
from app.widgets.helicon_params_panel import HeliconParamsPanel
from app.widgets.metadata_panel import MetadataPanel
from app.widgets.monitor_panel import MonitorPanel
from app.widgets.naming_panel import NamingPanel
from app.widgets.results_column import ResultsColumn
from app.widgets.taxon_card_panel import TaxonCardPanel
from app.widgets.specimen_sidebar import SpecimenSidebar

_WORKBENCH_OUTER_SPLITTER_STATE_KEY = "workbench/layout_outer_splitter"
_WORKBENCH_CENTRE_SPLITTER_STATE_KEY = "workbench/layout_centre_splitter"
# Column floors/ceilings keep drag-resize usable on typical desktop widths.
_SIDEBAR_WIDTH_FLOOR = 160
_SIDEBAR_WIDTH_CEIL = 276  # 侧栏卡片内容硬最小值≈276; 再宽只是留白, 窄屏时省下的宽给中/右栏
_CENTRE_WIDTH_FLOOR = 240
_RIGHT_RAIL_WIDTH_FLOOR = 200
_RIGHT_RAIL_WIDTH_CEIL = 360

# Extracted workbench dialog/panel classes — re-exported under their original
# names so `from app.views.workbench_view import _X` keeps working (tests do).
from app.widgets.workbench_notice_panel import _WorkflowNoticePanel  # noqa: F401
from app.widgets.compose_organise_dialog import (  # noqa: F401
    _ComposeOrganiseProgressDialog,
)
from app.widgets.compose_workbench_dialog import (  # noqa: F401
    _ComposeWorkbenchDialog,
    _ScaledImagePreview,
)
from app.widgets.workbench_batch_dialogs import (  # noqa: F401
    _BatchResultDialog,
    _RnaQueueDialog,
)
from app.widgets.workbench_dashboard import _WorkflowDashboard  # noqa: F401
from app.widgets.workbench_utility_dialogs import (  # noqa: F401
    _AutoGroupSourceDialog,
    _DrawerScrim,
    _RetroactiveScanDialog,
)


class WorkbenchView(WorkbenchSpecimenIdentityMixin, WorkbenchMediaWorkflowMixin, WorkbenchMonitorWorkflowMixin, WorkbenchCollabDrawerMixin, BaseView):
    """Daily-use workbench — specimen list | monitor + grouping | naming + metadata.

    view_id   = "workbench"
    nav_title = "工作台"
    nav_icon  = "🔬"
    """

    view_id = "workbench"
    nav_title = "照片工作区"
    nav_icon = "🔬"

    # ── Build UI ──────────────────────────────────────────────────────────────

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        panel = getattr(self, "_workflow_notice_panel", None)
        if panel is not None:
            try:
                panel.reposition()
            except Exception:
                pass
        dlg = getattr(self, "_compose_organise_progress_dialog", None)
        if dlg is not None and dlg.isVisible():
            try:
                dlg._place_launcher()
                dlg._reposition_panel()
            except Exception:
                pass

    def _ui_settings(self):
        """Return the app settings store, falling back to Qt defaults."""
        qs = getattr(getattr(self.ctx, "settings", None), "_qs", None)
        if hasattr(qs, "value") and hasattr(qs, "setValue"):
            return qs
        return QSettings()

    def _widget_natural_width(self, widget: QWidget) -> int:
        hint = widget.sizeHint().width()
        minimum = widget.minimumSizeHint().width()
        return max(1, hint, minimum)

    @staticmethod
    def _clamp_column_width(natural: int, floor: int, ceil: int) -> int:
        return max(floor, min(natural, ceil))

    def _sidebar_min_width(self) -> int:
        return self._clamp_column_width(
            self._widget_natural_width(self._sidebar),
            _SIDEBAR_WIDTH_FLOOR,
            _SIDEBAR_WIDTH_CEIL,
        )

    def _centre_min_width(self) -> int:
        # §7 旧: 用 _widget_natural_width(偏好宽) —— 那是"想要多宽", 不是"最少
        # 多宽"。中栏用偏好宽当 splitter 最小值会过度占宽, 把右栏挤穿。splitter
        # 的最小值应取控件的硬最小值(minimumSizeHint), 面板内部有滚动/换行能
        # 在更窄时正常工作(2026-07-11 三栏挤穿修复)。
        hard_min = max(
            self._monitor.minimumSizeHint().width(),
            self._results.minimumSizeHint().width(),
        )
        return max(_CENTRE_WIDTH_FLOOR, hard_min)

    def _right_rail_min_width(self) -> int:
        # §7 旧: 用 _widget_natural_width(偏好宽) —— 同中栏, 那是"想要多宽"不是
        # "最少多宽", 会过度占宽把窄屏挤穿。改用硬最小值(minimumSizeHint)。
        content = getattr(self, "_right_rail_widget", None)
        if content is not None:
            content.ensurePolished()
            layout = content.layout()
            if layout is not None:
                layout.activate()
        base = content.minimumSizeHint().width() if content is not None else 1
        scroll = getattr(self, "_right_scroll", None)
        if scroll is not None:
            base += scroll.verticalScrollBar().sizeHint().width()
        return max(_RIGHT_RAIL_WIDTH_FLOOR, base)

    def _on_sidebar_collapse_toggled(self, collapsed: bool) -> None:
        """编号栏收起 → 该列缩成细条(宽度让给中间); 展开 → 恢复。"""
        sizes = self._outer_splitter.sizes()
        if collapsed:
            self._sidebar_expanded_state = self._outer_splitter.saveState()
            # 细条固定宽:按钮 24 + 卡片左右边距(各 12) + 余量 = 56。
            strip = 56
            self._sidebar.setMinimumWidth(strip)
            self._sidebar.setMaximumWidth(strip)
            # 按 splitter 当前总宽重算:细条 + 右栏不变, 其余全给中间(诉求:
            # 方便调整/放大中间主视图)。直接算总宽避免与 min/max 约束打架。
            if len(sizes) == 3:
                total = sum(sizes)
                self._outer_splitter.setSizes(
                    [strip, max(1, total - strip - sizes[2]), sizes[2]]
                )
        else:
            self._sidebar.setMinimumWidth(self._sidebar_min_width())
            self._sidebar.setMaximumWidth(QWIDGETSIZE_MAX)
            state = getattr(self, "_sidebar_expanded_state", None)
            if state is not None:
                self._outer_splitter.restoreState(state)

    def _right_rail_collapsed_width(self) -> int:
        button = getattr(self, "_rail_collapse_btn", None)
        content = button.sizeHint().width() if button is not None else 1
        rail = getattr(self, "_right_rail_widget", None)
        if rail is not None and rail.layout() is not None:
            margins = rail.layout().contentsMargins()
            content += margins.left() + margins.right()
        return max(1, content)

    def _restore_splitter_state(
        self,
        splitter: QSplitter,
        key: str,
        default_sizes: list[int],
    ) -> bool:
        raw = self._ui_settings().value(key)
        state = raw if isinstance(raw, QByteArray) else None
        if state is None and isinstance(raw, (bytes, bytearray)):
            state = QByteArray(bytes(raw))
        restored = bool(state and splitter.restoreState(state))
        if not restored:
            splitter.setSizes([max(1, int(v)) for v in default_sizes])
        return restored

    def _restore_workbench_outer_splitter(self) -> None:
        defaults = [
            self._sidebar_min_width(),
            self._centre_min_width(),
            self._right_rail_min_width(),
        ]
        restored = self._restore_splitter_state(
            self._outer_splitter,
            _WORKBENCH_OUTER_SPLITTER_STATE_KEY,
            defaults,
        )
        # 坏状态守卫: QSettings 里可能存着旧版本布局的 splitter 状态(列数/顺序
        # 不同, 或各列被压到远低于最小值 → 内容互相挤穿)。restoreState 恢复出
        # 明显退化的尺寸时, 丢弃它、退回按最小值分布的默认(2026-07-11)。
        if restored:
            sizes = self._outer_splitter.sizes()
            degenerate = (
                len(sizes) != 3
                or any(s <= 1 for s in sizes)
                or sizes[0] < defaults[0] - 40      # 侧栏被压穿
                or sizes[2] < defaults[2] - 40      # 右栏被压穿
            )
            if degenerate:
                self._outer_splitter.setSizes([max(1, int(v)) for v in defaults])

    def _restore_workbench_centre_splitter(self) -> None:
        self._restore_splitter_state(
            self._centre_splitter,
            _WORKBENCH_CENTRE_SPLITTER_STATE_KEY,
            [
                max(1, self._monitor.sizeHint().height()),
                max(1, self._results.sizeHint().height()),
            ],
        )

    def _save_workbench_outer_splitter(self, *_args) -> None:
        if getattr(self, "_right_rail_collapsed", False):
            return
        self._ui_settings().setValue(
            _WORKBENCH_OUTER_SPLITTER_STATE_KEY,
            self._outer_splitter.saveState(),
        )

    def _save_workbench_centre_splitter(self, *_args) -> None:
        self._ui_settings().setValue(
            _WORKBENCH_CENTRE_SPLITTER_STATE_KEY,
            self._centre_splitter.saveState(),
        )

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
        body_lay.setContentsMargins(14, 18, 14, 18)  # 左右边距收窄, 给三栏腾宽(2026-07-11)
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
        outer.setOpaqueResize(True)
        self._outer_splitter = outer

        # ── Left: specimen sidebar ─────────────────────────────────────────
        self._sidebar = SpecimenSidebar(self.ctx)
        self._sidebar.setMinimumWidth(self._sidebar_min_width())
        self._sidebar.setMaximumWidth(QWIDGETSIZE_MAX)
        self._sidebar.specimen_selected.connect(self._on_specimen_selected)
        self._sidebar.selection_scope_changed.connect(
            self._on_specimen_selection_scope_changed
        )
        self._sidebar.show_all_requested.connect(self._on_show_all_results)
        self._sidebar.activate_requested.connect(self._on_sidebar_activate)
        self._sidebar.deactivate_requested.connect(self._on_sidebar_deactivate)
        self._sidebar.activate_no_selection.connect(
            lambda: self._status_message("请先在左侧选中要激活的编号。")
        )
        self._sidebar.new_specimen_requested.connect(self._on_new_specimen)
        self._sidebar.collab_manager_requested.connect(self._on_open_collab_panel)
        self._sidebar.sync_selected_requested.connect(self._on_sync_selected_uid)
        self._sidebar.sync_project_requested.connect(self._on_sync_project_files)
        self._sidebar.sync_selected_overwrite_requested.connect(
            lambda: self._on_sync_selected_uid(mode="overwrite")
        )
        self._sidebar.sync_project_overwrite_requested.connect(
            lambda: self._on_sync_project_files(mode="overwrite")
        )
        self._sidebar.collapse_toggled.connect(self._on_sidebar_collapse_toggled)
        self._sidebar.print_labels_requested.connect(self._on_print_labels)
        self._sidebar.delete_specimen_requested.connect(self._confirm_delete_specimen)
        self._sidebar.print_rna_queue_requested.connect(self._on_print_rna_queue)
        self._sidebar.phase_mark_requested.connect(self._on_phase_mark)
        # 批量(Fable 5, 2026-07-12): 侧栏多选右键 -> 一次处理整批编号
        self._sidebar.print_labels_many_requested.connect(self._on_print_labels_many)
        self._sidebar.delete_specimens_many_requested.connect(self._confirm_delete_specimens_many)
        self._sidebar.phase_mark_many_requested.connect(self._on_phase_mark_many)
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
        centre.setObjectName("WorkbenchVerticalSplitter")
        centre.setChildrenCollapsible(False)
        centre.setHandleWidth(14)
        centre.setOpaqueResize(True)
        self._centre_splitter = centre

        self._monitor = MonitorPanel(self.ctx)
        self._monitor.refresh_requested.connect(self._refresh_monitor)
        self._monitor.assign_requested.connect(self._on_assign_jpg)
        self._monitor.unassign_requested.connect(self._on_unassign_jpg)
        self._monitor.add_jpg_requested.connect(self._on_add_jpg_files)
        self._monitor.external_jpgs_dropped.connect(self._on_external_jpgs_dropped)
        self._monitor.clear_pending_requested.connect(self._on_clear_pending_queue)
        self._monitor.grouping_requested.connect(self._on_open_grouping)
        self._monitor.legacy_organize_requested.connect(
            self._on_legacy_photo_batch_organize
        )
        self._monitor.compose_implicit_requested.connect(self._on_compose_implicit)
        self._monitor.organise_selected_requested.connect(self._on_organise_selected)
        self._monitor.compose_implicit_organise_requested.connect(
            lambda: self._on_compose_implicit(organise=True)
        )
        self._monitor.auto_compress_toggled.connect(self._on_auto_compress_toggled)
        self._monitor.compose_preview_toggled.connect(self._on_compose_preview_toggled)
        self._sync_auto_archive_toggle()
        self._sync_compose_preview_toggle()
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
        self._grouping.tiff_naming_check_path_requested.connect(
            self._on_tiff_naming_check_path
        )
        self._grouping.helicon_params_requested.connect(self._open_grouping_helicon_params)
        self._grouping.import_tiff_requested.connect(self._persist_imported_group_tiff)  # #cursor
        self._grouping.archive_zip_registered.connect(self._on_archive_zip_registered)
        self._grouping.supp_process_requested.connect(self._on_supplementary_process)
        self._grouping.supp_files_dropped.connect(self._on_supplementary_dropped)
        # 批量[合成]/[合成+整理]/[整理] — workbench 驱动顺序队列(合成异步,需串行)。
        self._batch = None  # {"uid","queue":[group_index...],"organise":bool}
        self._last_scan_result = None
        self._monitor_scan_worker = None
        self._monitor_scan_request_id = 0
        self._monitor_scan_pending = False
        self._photo_import_worker = None
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
        self._results.restore_many_requested.connect(self._on_restore_archives_batch)
        self._results.specimen_requested.connect(self._on_specimen_selected)
        self._results.show_all_requested.connect(self._on_show_all_results)
        self._results.current_requested.connect(self._on_show_current_results)
        self._results.link_result_requested.connect(self._on_link_result_to_right_uid)
        self._results.unbind_result_requested.connect(self._on_unbind_result)
        self._results.rebind_result_requested.connect(self._on_rebind_result)
        self._results.tiff_naming_check_requested.connect(
            self._on_tiff_naming_check_path
        )
        self._results.tiff_naming_check_many_requested.connect(
            lambda paths: self._run_tiff_naming_check(paths=list(paths or []))
        )
        self._results.tiff_delete_requested.connect(self._on_delete_result_tiff_path)
        centre.addWidget(self._results)

        centre.setStretchFactor(0, 3)
        centre.setStretchFactor(1, 2)
        centre.setMinimumWidth(self._centre_min_width())
        self._restore_workbench_centre_splitter()
        centre.splitterMoved.connect(self._save_workbench_centre_splitter)
        outer.addWidget(centre)

        # ── Right rail: 编号与元数据 column.  Vertical stacking of the results in
        #    the centre column frees the horizontal budget the old tab hack was
        #    invented to reclaim, so the naming panel keeps a width floor (never
        #    clips the UID / copy buttons) as a plain column — no tabs.
        right = QWidget()
        right.setObjectName("RightRailContent")
        right_lay = QVBoxLayout(right)
        right_lay.setContentsMargins(14, 10, 12, 10)
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
        self._naming.add_requested.connect(self._on_naming_add)
        self._naming.update_requested.connect(self._on_naming_update_results)
        self._naming.delete_requested.connect(self._confirm_delete_specimen)
        self._naming.uid_generated.connect(self._on_naming_uid_generated)
        self._naming.uid_corrected.connect(self._on_uid_corrected)
        self._naming.storage_applied.connect(self._on_naming_storage_applied)
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
        # §7 旧: 直接 connect 命名卡内部控件的信号(掏私有成员)
        # self._naming._collection_date.textEdited.connect(lambda *_: self._schedule_rail_save())
        # self._naming._photo_date.textEdited.connect(lambda *_: self._schedule_rail_save())
        # self._naming._photo_notes.textChanged.connect(lambda: self._schedule_rail_save())
        # 新: 面板自己把这三个内部信号汇成 fields_edited, 触发点一一对应, 不增不减。
        self._naming.fields_edited.connect(self._schedule_rail_save)

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
        # 拍摄途中换人(用户 2026-07-12) —— 手改人员/场地后问一句「以后的新号也用它吗」。
        # 防抖: textEdited 每敲一个字符都会发, 直接弹框会在打字途中弹出。
        self._sticky_pending: Optional[tuple] = None
        self._sticky_asked: dict[str, str] = {}
        self._sticky_timer = QTimer(self)
        self._sticky_timer.setSingleShot(True)
        self._sticky_timer.setInterval(1500)
        self._sticky_timer.timeout.connect(self._ask_sticky_default)
        self._metadata.default_change_suggested.connect(self._on_default_change_suggested)
        right_lay.addWidget(self._metadata)

        # 卡4 协作状态（默认折叠）
        from app.widgets.collab_specimen_card import CollabSpecimenCard
        self._collab_card = CollabSpecimenCard(self.ctx)
        right_lay.addWidget(self._collab_card)
        right_lay.addStretch(1)

        right_scroll = QScrollArea()
        right_scroll.setObjectName("RightRailScroll")
        right_scroll.setWidget(right)
        right_scroll.setWidgetResizable(True)
        right_scroll.setFrameShape(QFrame.Shape.NoFrame)
        right_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        right_scroll.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOn
        )
        self._right_scroll = right_scroll
        self._right_rail_widget = right
        right_scroll.setMinimumWidth(self._right_rail_min_width())
        right_scroll.setMaximumWidth(QWIDGETSIZE_MAX)
        self._right_rail_collapsed = False
        self._last_expanded_rail_state: QByteArray | None = None
        outer.addWidget(right_scroll)

        # The side columns have content-derived minimums and no hard maximum.
        # If the user drags the splitters, the exact state is restored next run;
        # otherwise Qt distributes the workbench from widget size hints.
        outer.setStretchFactor(0, 0)
        outer.setStretchFactor(1, 1)
        outer.setStretchFactor(2, 0)
        self._restore_workbench_outer_splitter()
        outer.splitterMoved.connect(self._save_workbench_outer_splitter)

        # Fill the viewport so left | centre | right splitters drag-resize in
        # place.  A horizontal scroll wrapper made users pan the whole row instead.
        body_lay.addWidget(outer, stretch=1)

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
        self._settings_drawer.naming_rules_changed.connect(
            self._naming.refresh_naming_rules
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
        # Fallback: occasional full rescan catches missed events on WSL2 / SMB.
        # QFileSystemWatcher handles normal realtime updates; keeping this wide
        # avoids constant Windows-drive scans while the user is editing fields.
        self._fs_watcher = QFileSystemWatcher(self)
        self._fs_watcher.directoryChanged.connect(self._on_fs_changed)

        self._debounce_timer = QTimer(self)
        self._debounce_timer.setSingleShot(True)
        self._debounce_timer.setInterval(300)
        self._debounce_timer.timeout.connect(self._refresh_monitor)

        self._fallback_timer = QTimer(self)
        self._fallback_timer.setInterval(120000)
        self._fallback_timer.timeout.connect(self._refresh_monitor)

        # Track current UID for grouping edits
        self._current_uid: Optional[str] = None
        # 侧栏是否处于「多选编号」范围(决定退出多选时是否恢复当前编号视图)
        self._multi_scope_active: bool = False
        self._pending_grouping = None  # SpecimenGrouping awaiting save
        self.ctx.worms_fill_specimen = self.worms_fill_specimen
        self._refresh_workflow_dashboard()

        # Pre-create overlay last so it stacks above body / drawer scrims.
        self._compose_organise_progress_dialog = _ComposeOrganiseProgressDialog(self)
        self._compose_organise_progress_dialog.cancel_requested.connect(
            self._cancel_workflow_task
        )
        # Child widgets finish polishing while the remaining workbench panels
        # are constructed. Re-read the rail hint once the full tree exists so
        # a runtime font-scale change cannot leave the scrollbar overlapping
        # the form by a few pixels.
        self._right_scroll.setMinimumWidth(self._right_rail_min_width())

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

        def add_directory_path_indicator(label: str) -> QLabel:
            l = QLabel(label)
            l.setObjectName("DirLabel")
            lay.addWidget(l)
            path = QLabel("—")
            path.setObjectName("DirPath")
            lay.addWidget(path)
            return path

        self._dir_root = add_directory_path_indicator("工作目录")
        self._dir_incoming = add_directory_path_indicator("相机 JPG")
        self._dir_results = add_directory_path_indicator("成果")
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

        # Helicon status tag + bottom status bar
        installed = False
        try:
            from app.services.helicon_service import resolve_helicon_exe
            installed = bool(resolve_helicon_exe(self.ctx.settings))
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
        if hasattr(win, "refresh_helicon_status"):
            try:
                win.refresh_helicon_status()
            except Exception:
                pass

        # Dir strip
        if project_dir:
            self._dir_strip.show()
            self._dir_root.setText(project_dir)
            self._dir_incoming.setText("incoming-jpg/")
            self._dir_results.setText("results/")
        else:
            self._dir_strip.hide()
        self._refresh_workflow_dashboard()

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
        self._sync_auto_archive_toggle()
        self._sync_compose_preview_toggle()

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
        # Let the page paint before scanning directories.  The scan can touch a
        # large incoming/results folder, so doing it through the debounce timer
        # keeps returning to the workbench responsive and avoids a duplicate
        # immediate scan.
        self._debounce_timer.start(50)  # first refresh almost immediately
        if not self._fallback_timer.isActive():
            self._fallback_timer.start()

    def _sync_auto_archive_toggle(self) -> None:
        """Keep the toolbar auto-archive button in sync with persisted settings."""
        try:
            self._monitor.set_auto_archive_enabled(
                bool(getattr(self.ctx.settings, "auto_organize_after_compose", False))
            )
        except Exception:
            pass

    def _sync_compose_preview_toggle(self) -> None:
        """Keep the toolbar preview checkbox in sync with silent-compose setting."""
        try:
            self._monitor.set_compose_preview_enabled(
                not bool(getattr(self.ctx.settings, "silent_compose", False))
            )
        except Exception:
            pass

    def on_deactivate(self) -> None:
        """Called when navigating away; stop watchers and timers."""
        self._debounce_timer.stop()
        self._fallback_timer.stop()
        self._fs_watcher.removePaths(self._fs_watcher.directories())
        self._monitor_scan_pending = False
        self._monitor_scan_request_id += 1
        try:
            from PyQt6.QtGui import QPixmapCache
            from app.utils.image_thumbnail import clear_thumbnail_cache
            from app.widgets.monitor_panel import clear_file_thumb_cache

            clear_thumbnail_cache()
            clear_file_thumb_cache()
            QPixmapCache.clear()
        except Exception:  # noqa: BLE001
            pass

    def stop_background_work(self) -> None:
        """Cancel an in-flight Helicon compose so its subprocess + QThread
        cannot outlive app exit (orphaned helicon-focus*.exe holds /mnt
        handles → must-reboot lock leak)."""
        workers = list(getattr(self, "_helicon_workers", set()) or [])
        legacy_worker = getattr(self, "_helicon_worker", None)
        if legacy_worker is not None and legacy_worker not in workers:
            workers.append(legacy_worker)
        for w in workers:
            if w is not None and w.isRunning():
                try:
                    w.cancel()
                except Exception:  # noqa: BLE001
                    pass
                w.wait(3000)
        for worker in list(getattr(self, "_archive_workers", set()) or []):
            if worker is not None and worker.isRunning():
                worker.wait(3000)
        importer = getattr(self, "_photo_import_worker", None)
        if importer is not None and importer.isRunning():
            importer.wait(3000)
        sync_worker = getattr(self, "_collab_file_sync_worker", None)
        if sync_worker is not None and sync_worker.isRunning():
            sync_worker.wait(3000)
        batch_restore = getattr(self, "_batch_restore_worker", None)
        if batch_restore is not None and batch_restore.isRunning():
            batch_restore.cancel()
            batch_restore.wait(30000)

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

    UNBOUND_GROUP_LABEL = "未关联成果"

    def _project_uids(self) -> list[str]:
        """当前项目的全部编号(按 uid 排序)。"""
        db = self.ctx.get_db()
        project_dir = self.ctx.current_project_dir
        if not db or not project_dir:
            return []
        try:
            project_dirs = equivalent_paths(project_dir)
            owner_filter = ",".join("?" * len(project_dirs)) or "?"
            rows = db.execute(
                f"""
                SELECT uid
                FROM specimens
                WHERE owner_project_dir IN ({owner_filter})
                ORDER BY uid
                """,
                project_dirs or [project_dir],
            ).fetchall()
            return [str(row["uid"] if hasattr(row, "keys") else row[0]) for row in rows]
        except Exception:
            return []

    def _unbound_result_group(self) -> Optional[dict]:
        """results/ 里没挂任何编号的 TIF,单列一组「未关联成果」。

        没有这一组的话,「解绑」等于让 TIF 从界面上消失、再也无法改绑;
        外部软件直接丢进 results/ 的 TIF 同理也在这里现身。
        """
        db = self.ctx.get_db()
        project_dir = self.ctx.current_project_dir
        if not db or not project_dir:
            return None
        try:
            from app.services.capture_workflow_service import list_unbound_result_tiffs

            _incoming, results_sub = self._resolve_capture_subdirs()
            paths = list_unbound_result_tiffs(
                db, os.path.join(str(project_dir), results_sub)
            )
        except Exception:
            return None
        if not paths:
            return None
        return {
            "uid": self.UNBOUND_GROUP_LABEL,
            "tiffs": [{"path": p, "name": os.path.basename(p)} for p in paths],
            "zips": [],
        }

    def _on_show_all_results(self) -> None:
        """Show organized results for every specimen in the current project."""
        db = self.ctx.get_db()
        project_dir = self.ctx.current_project_dir
        if not db or not project_dir:
            self._results.clear()
            return

        groups = self._groups_for_uids(self._project_uids())
        # 未关联成果排在最后一组:解绑后的 TIF 从这里可以被重新绑定。
        unbound = self._unbound_result_group()
        if unbound:
            groups = groups + [unbound]
        self._results.load_many(groups)
        self._status_message(
            f"已展示全部成果：{len(groups)} 个编号，{self._count_results(groups)} 项。"
        )

    def _groups_for_uids(self, uids: list[str]) -> list[dict]:
        """按编号取「已整理成果」分组(TIFF/ZIP);无成果的编号不出现。

        「全部成果」与「所选编号成果」共用此取数,避免两处口径漂移。
        """
        db = self.ctx.get_db()
        if not db or not uids:
            return []
        groups: list[dict] = []
        try:
            from app.services.grouping_service import load_grouping
            from app.services.specimen_rename_service import (
                repair_grouping_result_files_for_uid,
            )

            for uid in uids:
                try:
                    repair_grouping_result_files_for_uid(db, uid)
                    grouping = load_grouping(db, uid)
                except Exception:
                    continue
                tiffs, zips = self._result_infos_from_grouping(grouping)
                if tiffs or zips:
                    groups.append({"uid": uid, "tiffs": tiffs, "zips": zips})
        except Exception:
            return []
        return groups

    @staticmethod
    def _count_results(groups: list[dict]) -> int:
        return sum(
            len({item.get("seq") or item.get("path") or item.get("name")
                 for item in g.get("tiffs", []) + g.get("zips", [])})
            for g in groups
        )

    def _on_specimen_selection_scope_changed(self, uids: list) -> None:
        """侧栏多选编号 → 成果区只显示这些编号的成果;退出多选才回到旧行为。

        单击也会发本信号(1 个编号),但那条路径已由 ``specimen_selected`` →
        ``_load_specimen`` 加载成果 —— 这里必须放行,否则每次单击重复查库+重建
        成果区(正是要治的卡顿)。仅在「从多选退回单选/清空」时恢复当前编号视图。
        """
        uid_list = [str(u) for u in (uids or []) if u]
        if len(uid_list) >= 2:
            self._multi_scope_active = True
            groups = self._groups_for_uids(uid_list)
            self._results.load_many(groups, title=f"所选 {len(uid_list)} 个编号成果")
            self._status_message(
                f"已展示所选 {len(uid_list)} 个编号："
                f"{len(groups)} 个有成果，{self._count_results(groups)} 项。"
            )
            return
        if getattr(self, "_multi_scope_active", False):
            self._multi_scope_active = False
            self._on_show_current_results()

    def _on_show_current_results(self) -> None:
        """Return the results panel to the currently selected specimen."""
        uid = self._current_uid or self._sidebar.current_uid()
        if not uid:
            self._results.clear()
            self._status_message("请先选择一个编号。")
            return
        try:
            from app.services.grouping_service import load_grouping
            grouping = load_grouping(self.ctx.get_db(), uid)
        except Exception:
            grouping = None
        self._refresh_results_column(uid, grouping)

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

    # ── 批量(侧栏多选) —— Fable 5, 2026-07-12 ────────────────────────────────
    # 场景(用户): "多选、批量处理是现代软件基本功能"。侧栏本来能 Ctrl 多选,
    #   但右键会把多选清空成一个 -> 20 个编号打标签要右键 20 次。
    # 做法: 批量走新信号, 一次确认 -> 循环调用**原来的单个实现**, 逐个成败汇总,
    #   不复制业务逻辑(单个路径怎么改, 批量自动跟随)。
    def _confirm_delete_specimens_many(self, uids: list) -> None:
        items = [str(u).strip() for u in (uids or []) if str(u).strip()]
        if not items:
            return
        if len(items) == 1:
            self._confirm_delete_specimen(items[0])
            return
        preview = "\n".join(items[:8]) + ("\n…" if len(items) > 8 else "")
        ret = QMessageBox.question(
            self,
            "删除标本编号",
            f"确定删除这 {len(items)} 个标本编号吗？\n\n{preview}\n\n"
            "只删除工作台中的编号记录和关联状态，不删除磁盘上的照片或成果文件。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if ret != QMessageBox.StandardButton.Yes:
            return
        db = self.ctx.get_db()
        if not db:
            return
        from app.services.capture_workflow_service import delete_specimens

        result = delete_specimens(db, items)
        if self._current_uid in result.deleted:
            self._current_uid = None
            for widget in (self._naming, self._metadata, self._taxon_card, self._grouping):
                clear = getattr(widget, "clear", None)
                if callable(clear):
                    clear()
        self._sidebar.refresh()
        self._refresh_batch_header()
        detail = f"已删除 {len(result.deleted)}/{len(result.requested)} 个编号"
        if result.failures:
            detail += f"，{len(result.failures)} 个失败"
            QMessageBox.warning(
                self,
                "批量删除完成（有失败项）",
                detail + "\n\n" + "\n".join(
                    f"{uid}：{reason}" for uid, reason in list(result.failures.items())[:6]
                ),
            )
        self._status_message(detail)

    def _on_print_labels_many(self, uids: list) -> None:
        items = [str(u).strip() for u in (uids or []) if str(u).strip()]
        if not items:
            return
        for uid in items:
            self._on_print_labels(uid)
        self._status_message(f"已提交 {len(items)} 个编号的标签打印")

    def _on_phase_mark_many(self, uids: list, status: str) -> None:
        items = [str(u).strip() for u in (uids or []) if str(u).strip()]
        if not items or not status:
            return
        ok = sum(1 for uid in items if self._set_phase(uid, status))
        self._sidebar.refresh_phases()
        self._refresh_batch_header()
        if ok == len(items):
            self._status_message(f"已标记 {ok} 个编号")
        else:
            self._status_message(f"已标记 {ok}/{len(items)} 个编号，其余失败")

    def _on_delete_specimen(self, uid: str) -> None:
        """Delete a specimen and local DB references owned by the workbench."""
        uid = str(uid or "").strip()
        if not uid:
            return
        db = self.ctx.get_db()
        if not db:
            return
        try:
            from app.services.capture_workflow_service import delete_specimen

            delete_specimen(db, uid)
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
        self._refresh_workflow_dashboard()

    def _refresh_workflow_dashboard(self) -> None:
        panel = getattr(self, "_workflow_dashboard", None)
        if panel is None:
            return
        try:
            panel.update_state(
                project_dir=getattr(self.ctx, "current_project_dir", None),
                active_uid=self._get_active_uid(),
                current_uid=getattr(self, "_current_uid", None),
                scan_result=getattr(self, "_last_scan_result", None),
            )
        except Exception:
            pass

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
                uid, status, seed_status=seed_status, force=True, broadcast=True,
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
            "province": self._naming.province(),
            "site": self._naming.site(),
            "station": self._naming.station(),
            "storage": self._naming.storage_code(),
            "collectionDate": self._naming.collection_date(),
            "photoDate": self._naming.photo_date(),
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
        self._refresh_workflow_dashboard()

    def _apply_draft_project_defaults(self, *, override_auto: bool = False) -> None:
        """把项目默认人员/坐标预填进拍摄界面右栏（仅空字段或自动字段）。

        新建标本、进入工作台无激活号、项目设置里改人员时都会走这里。
        用户仍可在右侧手改（不同站位/临时换人）；手填值不会被覆盖。
        """
        prefill = self._effective_prefill()
        # §7 旧字段集(2026-07-12 前): ("collector", "photographer", "identifier",
        #    "lon", "lat", "geo_area") —— 新增 photo_location(拍摄场地): 用户
        #    2026-07-12 "拍摄场地等信息…方便主界面右侧自动读取, 减少每次拍照都要填写"。
        self._metadata.apply_autofill({
            k: prefill[k]
            for k in ("collector", "photographer", "identifier",
                      "lon", "lat", "geo_area", "photo_location")
            if prefill.get(k)
        }, override_auto=override_auto)
        self._sync_uid_display_summary()

    _STICKY_LABELS = {
        "collector": "采集人",
        "photographer": "拍摄人",
        "identifier": "鉴定人",
        "photo_location": "拍摄场地",
    }

    def _on_default_change_suggested(self, field: str, value: str) -> None:
        """手改人员/场地 → 记下来, 停手 1.5s 后再问(防抖)。

        场景(用户 2026-07-12): "拍照过程有可能有变化, 主界面信息可以修改的, 项目中提前的
        信息可以被临时改动, 比如拍照人、鉴定人等, 这些信息会被记录。"
        改动本身早就落库留痕(手改 -> 脱离 _auto_fields -> autosave 到这个标本的行);
        这里只解决「下一个新号又回到项目默认」—— 中途换人得一个个手改的问题。
        """
        if self._sticky_asked.get(field) == value:
            return          # 同字段同值只问一次(每敲一个字符都会触发 textEdited)
        self._sticky_pending = (field, value)
        self._sticky_timer.start()

    def _ask_sticky_default(self) -> None:
        """停止输入后问: 以后的新号也用它吗? 选「以后都用」→ 写回**当前工作区**设置。

        写工作区(ctx.get_db())而不是项目根: 只影响这个断面, 不污染整个项目。
        选「只这一个」/忽略 → 维持现状(只改当前标本)。
        """
        if not self._sticky_pending:
            return
        field, value = self._sticky_pending
        self._sticky_pending = None
        if self._metadata.current_values().get(field, "").strip() != value:
            return          # 用户又改了 -> 这次不问, 等下一轮防抖
        self._sticky_asked[field] = value

        db = self.ctx.get_db()
        if not db:
            return
        label = self._STICKY_LABELS.get(field, field)
        from PyQt6.QtWidgets import QMessageBox
        resp = ui.question(
            self,
            "设为后续默认？",
            f"{label}改成了「{value}」。\n\n"
            f"以后在本工作区新建的编号也用这个{label}吗？\n"
            "（选「否」则只有当前这个编号用它；当前编号无论如何都已保存）",
        )
        if resp != QMessageBox.StandardButton.Yes:
            return
        try:
            from app.services import project_settings_service as pss
            if field == "photo_location":
                meta = pss.load_setting(db, "project_meta", pss.DEFAULT_PROJECT_META)
                meta["photo_location"] = value
                pss.save_setting(db, "project_meta", meta)
            else:
                personnel = pss.load_setting(db, "personnel", pss.DEFAULT_PERSONNEL)
                personnel[field] = value
                pss.save_setting(db, "personnel", personnel)
            db.commit()
        except Exception as exc:  # noqa: BLE001 —— 写默认失败不该影响已保存的标本
            logger.exception("写回工作区默认失败: %s=%s (%s)", field, value, exc)

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
        # 形状必须和 pss.effective_new_specimen_prefill 完全一致 —— 少一个键, 走兜底
        # 分支的调用方就会 KeyError。photo_location 于 2026-07-12 加入。
        empty = {"province": "", "site": "", "stations": {},
                 "collector": "", "photographer": "", "identifier": "",
                 "lon": "", "lat": "", "geo_area": "", "photo_location": ""}
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
            from app.services import niimbot_print_service
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
            available.update(niimbot_print_service.available_printer_ids())
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

            if quick_mode == "dialog":
                # The workbench print button should be one-click once the
                # project settings already resolve to concrete printers. Keep
                # the dialog only for incomplete routes.
                routes_ready = True
                for job in direct_jobs:
                    target = tissue_printer if job.get("bucket") == "tissue" else sample_printer
                    if not target:
                        routes_ready = False
                        break
                if routes_ready:
                    quick_mode = "direct"

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
                has_niimbot_printer = bool(niimbot_print_service.available_printers())
                if windows_print.is_available() and not has_niimbot_printer:
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

                    printer_name = str(dlg.selected_printer() or "")
                    if niimbot_print_service.is_niimbot_printer_id(printer_name):
                        printer_display = niimbot_print_service.print_jobs_to_niimbot(
                            direct_jobs,
                            printer_name=printer_name,
                            document_name=f"标本标签 {uid}",
                        )
                    elif windows_print.is_available():
                        ok, used_printer = windows_print.print_jobs_with_windows_dialog(
                            direct_jobs,
                            document_name=f"标本标签 {uid}",
                            printer_name=printer_name,
                            show_dialog=False,
                        )
                        if not ok:
                            return False
                        printer_display = used_printer or printer_name or "Windows 打印机"
                    else:
                        printer = label_print.build_printer(direct_jobs[0])
                        if printer_name:
                            printer.setPrinterName(printer_name)

                        if not label_print.paint_jobs(printer, direct_jobs):
                            return False
                        printer_display = printer_name or printer.printerName() or "默认打印机"
                    if record_print_jobs is not None:
                        try:
                            record_print_jobs(
                                db, direct_jobs, actor=actor,
                                printer_name=printer_display,
                            )
                        except Exception:
                            pass
                    printed = sum(len(j.get("labels") or []) for j in direct_jobs)
                    printers_used = [printer_display] if printed else []
                    printed_details = [f"{len(direct_jobs)} 个作业 → {printer_display}"]
            else:
                # ── Direct path: each job to its configured printer ──
                for job in direct_jobs:
                    target = tissue_printer if job.get("bucket") == "tissue" else sample_printer
                    if not target:
                        return False
                    if niimbot_print_service.is_niimbot_printer_id(target):
                        try:
                            target = niimbot_print_service.print_jobs_to_niimbot(
                                [job],
                                printer_name=target,
                                document_name=f"标本标签 {uid}",
                            )
                        except Exception as exc:
                            self._status_message(f"NIIMBOT 打印失败：{exc}", 7000)
                            return True
                    elif windows_print.is_available():
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
            from app.services import niimbot_print_service
            from app.services import project_settings_service as pss
            from app.services import rna_label_queue_service as rna_queue
            from app.services.label_print_executor import LabelPrintExecutor
            from app.utils.label_core import unique_id
            import app.utils.label_print as label_print
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
            available.update(niimbot_print_service.available_printer_ids())
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

            result = LabelPrintExecutor(
                ctx=self.ctx,
                parent=self,
                build_printer_fn=label_print.build_printer,
                paint_jobs_fn=label_print.paint_jobs,
            ).print_direct(
                [job],
                printer_name=tissue_printer,
                document_name="RNAlater 合版标签",
                grid_opts=grid_opts,
                cut_marks=bool(grid_opts.get("cutMarks")),
                draw_crop_marks=draw_crop_marks,
            )
            if not result.printed:
                QMessageBox.warning(self, "打印 RNA 合版", "打印任务发送失败。")
                return
            rna_queue.mark_printed(db, ordered_uids)
            self._sync_rna_queue_count()
            used_printer = result.printer_name or tissue_printer
            self._status_message(
                f"RNAlater 合版已发送到 {used_printer} · {len(ordered_uids)} 张"
            )
        except Exception as exc:
            QMessageBox.warning(self, "打印 RNA 合版", str(exc))

    def _status_message(self, text: str, msec: int = 4000) -> None:
        ui.show_status(self, text, msec)

    def _workflow_notice(
        self,
        title: str,
        detail: str = "",
        *,
        state: str = "busy",
        force_show: bool = False,
        task_key: str | None = None,
    ) -> None:
        try:
            try:
                self._monitor.clear_workflow_notice()
            except Exception:
                pass
            if str(title or "").startswith("合成+整理"):
                dlg = getattr(self, "_compose_organise_progress_dialog", None)
                if dlg is None:
                    dlg = _ComposeOrganiseProgressDialog(self)
                    dlg.cancel_requested.connect(self._cancel_workflow_task)
                    self._compose_organise_progress_dialog = dlg
                dlg.set_notice(
                    title,
                    detail,
                    state=state,
                    force_show=force_show,
                    task_key=task_key,
                )
                return
            dlg = getattr(self, "_workflow_notice_panel", None)
            if dlg is None:
                dlg = _WorkflowNoticePanel(self)
                self._workflow_notice_panel = dlg
            dlg.set_notice(
                title,
                detail,
                state=state,
                force_show=force_show,
                task_key=task_key,
            )
        except Exception:
            pass

    def _workflow_notice_text(self) -> tuple[str, str, str]:
        dlg = getattr(self, "_compose_organise_progress_dialog", None)
        if dlg is not None:
            try:
                return dlg.notice_text()
            except Exception:
                pass
        dlg = getattr(self, "_workflow_notice_panel", None)
        if dlg is None:
            return ("", "", "")
        try:
            return dlg.notice_text()
        except Exception:
            return ("", "", "")

    def _cancel_workflow_task(self, task_key: str) -> None:
        key = str(task_key or "")
        cancelled = False
        archive_map = getattr(self, "_archive_worker_by_task_key", {})
        worker = archive_map.get(key) if isinstance(archive_map, dict) else None
        if worker is not None:
            try:
                worker.cancel()
                cancelled = True
            except Exception:
                pass
        helicon_map = getattr(self, "_helicon_worker_by_task_key", {})
        hworker = helicon_map.get(key) if isinstance(helicon_map, dict) else None
        if hworker is not None:
            try:
                hworker.cancel()
                cancelled = True
            except Exception:
                pass
        if cancelled:
            self._workflow_notice(
                "合成+整理：正在取消",
                "正在停止当前后台任务；不会删除 JPG，未完成 ZIP 会被清理。",
                state="busy",
                task_key=key,
            )
            self._status_message("正在取消当前任务…")
        else:
            self._workflow_notice(
                "合成+整理无法取消",
                "当前阶段没有可取消的后台任务，可能已经完成或正在收尾登记。",
                state="info",
                task_key=key,
            )

    def _show_no_project(self) -> None:
        self._sidebar.refresh()  # clears list
        self._last_scan_result = None
        self._monitor.clear()
        self._grouping.clear()
        self._results.clear()
        self._metadata.clear()
        self._taxon_card.clear()
        self._naming.load_specimen({})  # 清命名卡残留字段
        self._current_uid = None
        self._refresh_header()
        self._refresh_workflow_dashboard()
        self._no_project_banner.show()
