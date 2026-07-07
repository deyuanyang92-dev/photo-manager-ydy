"""collab_view.py — Collaboration module view (CollabView).

Displays:
  • Online devices panel   — 🟢 N 台在线 / ⚪ 未发现其他设备
  • Task list              — per-UID status + assignee, coloured by state
  • Conflict banner        — shown when a 409 is detected
  • Manual connection row  — IP + port input + Connect button (mDNS fallback)
  • Debug drawer           — local address, peers with latency, sync log

Contract (BaseView):
  view_id   = "collab"
  nav_title = "协作"
  nav_icon  = "👥"

  on_activate() — refreshes device list and task table from the in-memory store.
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import Qt, QTimer, pyqtSignal, pyqtSlot
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.services.collab_offline_queue import OfflineDraftQueue
from app.services.collab_status import build_collab_status
from app.views.base_view import BaseView
from app.widgets.collab_share_project_picker import CollabShareProjectPicker

try:
    from app.config.effects import apply_card_shadow as _apply_card_shadow
except ImportError:  # pragma: no cover
    def _apply_card_shadow(widget, **_kw):  # type: ignore[misc]
        return None

if False:  # TYPE_CHECKING
    from app.app_context import AppContext
    from app.services.collab_service import CollabService, PeerInfo, TaskRecord


# ── Colour palette for task status ───────────────────────────────────────────

_STATUS_COLOURS: dict[str, str] = {
    "created":    "#6eb5ff",
    "assigned":   "#a8d8ea",
    "shooting":   "#f6d365",
    "shot_done":  "#b8f0b8",
    "organizing": "#ffd180",
    "done":       "#69f0ae",
    "void":       "#9e9e9e",
    "conflict":   "#ff5252",
}

_STATUS_LABEL: dict[str, str] = {
    "created":    "已创建",
    "assigned":   "已指派",
    "shooting":   "拍摄中",
    "shot_done":  "拍摄完成",
    "organizing": "整理中",
    "done":       "完成",
    "void":       "作废",
    "conflict":   "冲突",
}

_TASK_COL_UID = 0
_TASK_COL_PROJECT = 1
_TASK_COL_STATUS = 2
_TASK_COL_ASSIGNEE = 3
_TASK_COL_UPDATED = 4


def _elevate_card(frame: QFrame) -> None:
    """Soft drop shadow so setup/activity panels read as cards."""
    _apply_card_shadow(frame, blur=20, y=3, alpha=32)


# ── CollabView ────────────────────────────────────────────────────────────────

class CollabView(BaseView):
    """Collaboration module view.

    Requires ctx.collab_service (CollabService) to be present.  If the service
    is not available the view shows a "服务未启动" placeholder row and becomes
    effectively read-only.
    """

    view_id   = "collab"
    nav_title = "协作"
    nav_icon  = "👥"

    def __init__(self, ctx: "AppContext") -> None:
        # Service is optional — the view degrades gracefully when absent
        self._service: Optional["CollabService"] = getattr(ctx, "collab_service", None)
        self._connected_service: Optional["CollabService"] = None
        self._project_filter = ""
        super().__init__(ctx)
        self._offline_queue = OfflineDraftQueue(ctx.settings._qs)
        self._retry_timer = QTimer(self)
        self._retry_timer.setInterval(30_000)
        self._retry_timer.timeout.connect(self._retry_offline_drafts)
        self._connect_service_signals()

    # ── BaseView contract ─────────────────────────────────────────────────

    def _setup_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 16, 20, 16)
        root.setSpacing(14)

        # ── Header ───────────────────────────────────────────────────────
        header_frame = QFrame()
        header_frame.setObjectName("CollabHeader")
        _elevate_card(header_frame)
        header_layout = QHBoxLayout(header_frame)
        header_layout.setContentsMargins(18, 14, 18, 14)
        header_layout.setSpacing(16)

        title_col = QVBoxLayout()
        title_col.setSpacing(4)
        title = QLabel("协作中心")
        title.setObjectName("CollabTitle")
        title_col.addWidget(title)
        self._scope_label = QLabel("团队永久码：—")
        self._scope_label.setObjectName("CollabScope")
        title_col.addWidget(self._scope_label)
        self._connection_label = QLabel("")
        self._connection_label.setObjectName("CollabScope")
        self._connection_label.hide()
        title_col.addWidget(self._connection_label)
        header_layout.addLayout(title_col, stretch=2)

        stats_frame = QFrame()
        stats_frame.setObjectName("CollabStatsBar")
        stats_layout = QHBoxLayout(stats_frame)
        stats_layout.setContentsMargins(12, 8, 12, 8)
        stats_layout.setSpacing(10)
        self._local_project_badge = QLabel("本机项目：—")
        self._local_project_badge.setObjectName("CollabMetric")
        stats_layout.addWidget(self._local_project_badge)
        self._device_count_badge = QLabel("在线 0")
        self._device_count_badge.setObjectName("CollabMetric")
        stats_layout.addWidget(self._device_count_badge)
        self._project_count_badge = QLabel("项目 0")
        self._project_count_badge.setObjectName("CollabMetric")
        stats_layout.addWidget(self._project_count_badge)
        self._task_count_badge = QLabel("任务 0")
        self._task_count_badge.setObjectName("CollabMetric")
        stats_layout.addWidget(self._task_count_badge)
        self._conflict_count_badge = QLabel("冲突 0")
        self._conflict_count_badge.setObjectName("CollabMetric")
        stats_layout.addWidget(self._conflict_count_badge)
        header_layout.addWidget(stats_frame, stretch=3)

        status_col = QVBoxLayout()
        status_col.setSpacing(8)
        self._status_badge = QLabel("⚪ 协作未启动")
        self._status_badge.setObjectName("CollabStatusBadge")
        self._status_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        bold = QFont()
        bold.setBold(True)
        bold.setPointSize(11)
        self._status_badge.setFont(bold)
        status_col.addWidget(self._status_badge)
        self._debug_btn = QPushButton("诊断")
        self._debug_btn.setObjectName("Ghost")
        self._debug_btn.setCheckable(True)
        self._debug_btn.toggled.connect(self._toggle_debug_drawer)
        status_col.addWidget(self._debug_btn, alignment=Qt.AlignmentFlag.AlignHCenter)
        header_layout.addLayout(status_col)
        root.addWidget(header_frame)

        self._next_step_label = QLabel("下一步：—")
        self._next_step_label.setObjectName("CollabGuideTitle")
        self._next_step_detail = QLabel(
            "选择一种协作方式：团队永久码用于自动连接，项目码共享用于绑定同一个采集项目。"
        )
        self._next_step_detail.setObjectName("CollabGuideDetail")
        self._next_step_detail.setWordWrap(True)

        self._setup_btn = QPushButton("设置永久码")
        self._setup_btn.setObjectName("Primary")
        self._setup_btn.setMinimumHeight(34)
        self._setup_btn.clicked.connect(self._on_setup_wizard)

        self._share_btn = QPushButton("复制连接码")
        self._share_btn.setObjectName("Outline")
        self._share_btn.setMinimumHeight(34)
        self._share_btn.clicked.connect(self._on_share_addr)

        self._manual_toggle_btn = QPushButton("手动连接")
        self._manual_toggle_btn.setObjectName("Ghost")
        self._manual_toggle_btn.setCheckable(True)
        self._manual_toggle_btn.toggled.connect(self._toggle_manual_connect)

        self._shared_scope_btn = QPushButton("共享项目范围")
        self._shared_scope_btn.setObjectName("Outline")
        self._shared_scope_btn.setMinimumHeight(34)
        self._shared_scope_btn.setCheckable(True)
        self._shared_scope_btn.toggled.connect(self._toggle_shared_scope_panel)

        self._pick_project_btn = QPushButton("配对项目")
        self._pick_project_btn.setObjectName("Outline")
        self._pick_project_btn.setMinimumHeight(34)
        self._pick_project_btn.clicked.connect(self._on_pick_team_project)

        self._same_name_btn = QPushButton("同名一键配对")
        self._same_name_btn.setObjectName("Outline")
        self._same_name_btn.setMinimumHeight(34)
        self._same_name_btn.clicked.connect(self._on_same_name_bind_quick)
        self._same_name_btn.hide()

        self._project_code_btn = QPushButton("打开项目码共享")
        self._project_code_btn.setObjectName("Primary")
        self._project_code_btn.setMinimumHeight(34)
        self._project_code_btn.clicked.connect(self._on_project_sync_code)
        self._bind_project_btn = self._pick_project_btn

        self._team_scope_state = QLabel("团队永久码：未设置")
        self._team_scope_state.setObjectName("CollabScopeState")
        self._project_scope_state = QLabel("项目码共享：可单独使用；不需要先设置团队永久码。")
        self._project_scope_state.setObjectName("CollabScopeState")
        self._project_scope_state.setWordWrap(True)

        self._conflict_banner = QLabel()
        self._conflict_banner.setObjectName("ConflictBanner")
        self._conflict_banner.setWordWrap(True)
        self._conflict_banner.hide()
        root.addWidget(self._conflict_banner)

        guide_panel = QFrame()
        guide_panel.setObjectName("CollabGuide")
        _elevate_card(guide_panel)
        guide_layout = QVBoxLayout(guide_panel)
        guide_layout.setContentsMargins(16, 14, 16, 14)
        guide_layout.setSpacing(6)
        guide_layout.addWidget(self._next_step_label)
        guide_layout.addWidget(self._next_step_detail)
        root.addWidget(guide_panel)

        entry_row = QHBoxLayout()
        entry_row.setSpacing(14)
        entry_row.addWidget(self._make_unified_pairing_panel(), stretch=1)
        entry_row.addWidget(self._make_project_code_panel(), stretch=1)
        root.addLayout(entry_row)

        self._shared_scope_panel = self._make_shared_project_scope_panel()
        self._shared_scope_panel.hide()
        root.addWidget(self._shared_scope_panel)
        self._connect_panel = None

        manual_group = QFrame()
        manual_group.setObjectName("ManualConnectFrame")
        manual_layout = QHBoxLayout(manual_group)
        manual_layout.setContentsMargins(14, 10, 14, 10)
        manual_layout.setSpacing(8)
        manual_title = QLabel("手动连接")
        manual_title.setObjectName("Muted")
        manual_layout.addWidget(manual_title)
        self._ip_input = QLineEdit()
        self._ip_input.setPlaceholderText("队友 IP，例如 192.168.1.100")
        self._ip_input.setObjectName("ManualIpInput")
        manual_layout.addWidget(self._ip_input, 1)
        self._port_input = QLineEdit("5050")
        self._port_input.setFixedWidth(64)
        self._port_input.setObjectName("ManualPortInput")
        manual_layout.addWidget(self._port_input)
        self._connect_btn = QPushButton("连接")
        self._connect_btn.setFixedWidth(72)
        self._connect_btn.clicked.connect(self._on_manual_connect)
        manual_layout.addWidget(self._connect_btn)
        manual_group.hide()
        self._manual_group = manual_group
        root.addWidget(manual_group)

        # ── Activity hub (always visible) ─────────────────────────────────
        activity_hub = QFrame()
        activity_hub.setObjectName("CollabActivityHub")
        _elevate_card(activity_hub)
        self._activity_hub = activity_hub
        activity_layout = QVBoxLayout(activity_hub)
        activity_layout.setContentsMargins(16, 14, 16, 14)
        activity_layout.setSpacing(12)

        hub_title = QLabel("设备与任务")
        hub_title.setObjectName("CollabHubTitle")
        activity_layout.addWidget(hub_title)

        self._empty_banner = QFrame()
        self._empty_banner.setObjectName("CollabEmptyBanner")
        empty_lay = QVBoxLayout(self._empty_banner)
        empty_lay.setContentsMargins(20, 18, 20, 18)
        empty_lay.setSpacing(6)
        empty_icon = QLabel("👥")
        empty_icon.setObjectName("CollabEmptyIcon")
        empty_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_lay.addWidget(empty_icon)
        empty_title = QLabel("还没有在线设备或协作任务")
        empty_title.setObjectName("CollabEmptyTitle")
        empty_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_lay.addWidget(empty_title)
        empty_detail = QLabel(
            "完成上方「团队永久码」或「项目码共享」后，队友上线时设备会出现在下方；"
            "在照片工作区保存编号后，任务会自动同步到这里。"
        )
        empty_detail.setObjectName("CollabEmptyDetail")
        empty_detail.setWordWrap(True)
        empty_detail.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_lay.addWidget(empty_detail)
        activity_layout.addWidget(self._empty_banner)

        device_panel = QFrame()
        device_panel.setObjectName("CollabActivityPane")
        device_layout = QVBoxLayout(device_panel)
        device_layout.setContentsMargins(0, 0, 0, 0)
        device_layout.setSpacing(8)
        dev_title = QLabel("在线设备")
        dev_title.setObjectName("Section")
        device_layout.addWidget(dev_title)
        self._device_list = QTableWidget(0, 6)
        self._device_list.setObjectName("CollabDeviceTable")
        self._device_list.setHorizontalHeaderLabels(
            ["主机名", "项目", "团队永久码", "照片", "地址", "延迟"]
        )
        self._device_list.horizontalHeader().setStretchLastSection(False)
        self._device_list.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch
        )
        for col in (1, 2, 3, 4, 5):
            self._device_list.horizontalHeader().setSectionResizeMode(
                col, QHeaderView.ResizeMode.ResizeToContents
            )
        self._device_list.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._device_list.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._device_list.setAlternatingRowColors(True)
        self._device_list.verticalHeader().hide()
        self._device_list.setMinimumHeight(140)
        device_layout.addWidget(self._device_list)
        activity_layout.addWidget(device_panel)

        task_panel = QFrame()
        task_panel.setObjectName("CollabActivityPane")
        task_layout = QVBoxLayout(task_panel)
        task_layout.setContentsMargins(0, 0, 0, 0)
        task_layout.setSpacing(8)
        task_header = QHBoxLayout()
        task_title = QLabel("任务清单")
        task_title.setObjectName("Section")
        task_header.addWidget(task_title)
        task_header.addStretch()
        filter_label = QLabel("项目")
        filter_label.setObjectName("MutedSmall")
        task_header.addWidget(filter_label)
        self._project_combo = QComboBox()
        self._project_combo.setObjectName("ProjectFilterCombo")
        self._project_combo.setMinimumWidth(150)
        self._project_combo.currentIndexChanged.connect(self._on_project_filter_changed)
        task_header.addWidget(self._project_combo)
        task_layout.addLayout(task_header)

        self._task_table = QTableWidget(0, 5)
        self._task_table.setObjectName("CollabTaskTable")
        self._task_table.setHorizontalHeaderLabels(["UID", "项目", "状态", "负责人", "更新时间"])
        self._task_table.horizontalHeader().setStretchLastSection(False)
        self._task_table.horizontalHeader().setSectionResizeMode(
            _TASK_COL_UID, QHeaderView.ResizeMode.Stretch
        )
        for col in (_TASK_COL_PROJECT, _TASK_COL_STATUS, _TASK_COL_ASSIGNEE, _TASK_COL_UPDATED):
            self._task_table.horizontalHeader().setSectionResizeMode(
                col, QHeaderView.ResizeMode.ResizeToContents
            )
        self._task_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._task_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._task_table.setAlternatingRowColors(True)
        self._task_table.verticalHeader().hide()
        self._task_table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._task_table.customContextMenuRequested.connect(self._on_task_context_menu)
        self._task_table.setMinimumHeight(200)
        task_layout.addWidget(self._task_table)
        activity_layout.addWidget(task_panel, stretch=1)

        root.addWidget(activity_hub, stretch=1)
        self._activity_splitter = activity_hub  # compat for older refresh hooks

        self._activity_empty_panel = QWidget()
        self._activity_empty_panel.hide()

        # ── Debug drawer (collapsed by default) ───────────────────────────
        self._debug_drawer = QFrame()
        self._debug_drawer.setObjectName("DebugDrawer")
        self._debug_drawer.setFrameShape(QFrame.Shape.StyledPanel)
        self._debug_drawer.setFixedHeight(140)
        debug_layout = QVBoxLayout(self._debug_drawer)
        debug_layout.setContentsMargins(8, 6, 8, 6)
        debug_layout.setSpacing(4)

        self._debug_local_addr = QLabel("本机地址：—")
        self._debug_local_addr.setObjectName("Muted")
        debug_layout.addWidget(self._debug_local_addr)

        self._debug_log = QLabel("日志：—")
        self._debug_log.setObjectName("Muted")
        self._debug_log.setWordWrap(True)
        self._debug_log.setAlignment(Qt.AlignmentFlag.AlignTop)

        scroll = QScrollArea()
        scroll.setWidget(self._debug_log)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        debug_layout.addWidget(scroll)

        self._debug_drawer.hide()
        root.addWidget(self._debug_drawer)

        # ── No-service placeholder in task table ──────────────────────────
        if self._service is None:
            self._show_no_service_placeholder()
        else:
            self._refresh_summary([], [])

    def _make_step_panel(
        self,
        title: str,
        detail: str,
        *buttons: QPushButton,
    ) -> QFrame:
        panel = QFrame()
        panel.setObjectName("CollabStepPanel")
        panel.setMinimumHeight(82)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(8)

        title_row = QHBoxLayout()
        title_row.setSpacing(8)
        title_lbl = QLabel(title)
        title_lbl.setObjectName("CollabStepTitle")
        title_row.addWidget(title_lbl, 1)
        layout.addLayout(title_row)

        detail_lbl = QLabel(detail)
        detail_lbl.setObjectName("CollabStepDetail")
        detail_lbl.setWordWrap(True)
        layout.addWidget(detail_lbl, 1)

        action_row = QHBoxLayout()
        action_row.setSpacing(8)
        for button in buttons:
            action_row.addWidget(button)
        action_row.addStretch()
        layout.addLayout(action_row)
        return panel

    def _make_unified_pairing_panel(self) -> QFrame:
        """Team permanent code card. Project-code sharing is separate."""
        panel = QFrame()
        panel.setObjectName("CollabStepPanel")
        panel.setProperty("role", "entry")
        panel.setMinimumHeight(168)
        panel.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        _elevate_card(panel)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(10)

        title_row = QHBoxLayout()
        title_row.setSpacing(8)
        badge = QLabel("方式一")
        badge.setObjectName("CollabEntryBadge")
        title_row.addWidget(badge)
        title_lbl = QLabel("团队永久码")
        title_lbl.setObjectName("CollabStepTitle")
        title_row.addWidget(title_lbl)
        title_row.addStretch()
        layout.addLayout(title_row)

        detail_lbl = QLabel(
            "几个人填同一个团队永久码。保存后永久有效，软件重启或更新后自动连接。"
        )
        detail_lbl.setObjectName("CollabStepDetail")
        detail_lbl.setWordWrap(True)
        layout.addWidget(detail_lbl, 1)

        layout.addWidget(self._team_scope_state)

        team_row = QHBoxLayout()
        team_row.setSpacing(8)
        team_row.addWidget(self._setup_btn)
        team_row.addWidget(self._share_btn)
        team_row.addWidget(self._shared_scope_btn)
        team_row.addWidget(self._manual_toggle_btn)
        team_row.addStretch()
        layout.addLayout(team_row)
        return panel

    def _make_project_code_panel(self) -> QFrame:
        """Independent project-code sharing card."""
        panel = QFrame()
        panel.setObjectName("CollabStepPanel")
        panel.setProperty("role", "entry")
        panel.setMinimumHeight(168)
        panel.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        _elevate_card(panel)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(10)

        title_row = QHBoxLayout()
        title_row.setSpacing(8)
        badge = QLabel("方式二")
        badge.setObjectName("CollabEntryBadge")
        title_row.addWidget(badge)
        title_lbl = QLabel("项目码共享")
        title_lbl.setObjectName("CollabStepTitle")
        title_row.addWidget(title_lbl)
        title_row.addStretch()
        layout.addLayout(title_row)

        detail_lbl = QLabel(
            "不用先设置团队永久码。选择本机项目或文件夹，复制项目码给队友；"
            "也可以粘贴队友项目码，把本机项目连接到同一个采集项目。"
        )
        detail_lbl.setObjectName("CollabStepDetail")
        detail_lbl.setWordWrap(True)
        layout.addWidget(detail_lbl, 1)

        layout.addWidget(self._project_scope_state)

        project_row = QHBoxLayout()
        project_row.setSpacing(8)
        for button in (
            self._project_code_btn,
            self._pick_project_btn,
            self._same_name_btn,
        ):
            project_row.addWidget(button)
        project_row.addStretch()
        layout.addLayout(project_row)
        return panel

    def _make_shared_project_scope_panel(self) -> QFrame:
        """Optional project scope list used after team-code setup."""
        panel = QFrame()
        panel.setObjectName("CollabOptionalPanel")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(8)

        title_row = QHBoxLayout()
        title_row.setSpacing(8)
        title_lbl = QLabel("共享项目范围")
        title_lbl.setObjectName("CollabStepTitle")
        title_row.addWidget(title_lbl)
        hint_lbl = QLabel("团队永久码使用这个范围；项目码共享不需要先设置这里")
        hint_lbl.setObjectName("MutedSmall")
        title_row.addWidget(hint_lbl)
        title_row.addStretch()
        self._broadcast_toggle_btn = QPushButton("收起")
        self._broadcast_toggle_btn.setObjectName("Ghost")
        self._broadcast_toggle_btn.clicked.connect(lambda: self._toggle_shared_scope_panel(False))
        title_row.addWidget(self._broadcast_toggle_btn)
        layout.addLayout(title_row)

        self._broadcast_body = QWidget()
        body_layout = QVBoxLayout(self._broadcast_body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(8)
        self._share_picker = CollabShareProjectPicker(self.ctx, autoload=False)
        self._share_picker.selection_changed.connect(self._on_share_selection_changed)
        body_layout.addWidget(self._share_picker)
        layout.addWidget(self._broadcast_body)
        return panel

    def _toggle_shared_scope_panel(self, checked: bool) -> None:
        if hasattr(self, "_shared_scope_panel"):
            self._shared_scope_panel.setVisible(checked)
        if hasattr(self, "_shared_scope_btn"):
            self._shared_scope_btn.setChecked(checked)
            self._shared_scope_btn.setText("收起共享范围" if checked else "共享项目范围")

    def _make_scope_panel(
        self,
        title: str,
        detail: str,
        state_label: QLabel,
        *buttons: QPushButton,
    ) -> QFrame:
        panel = QFrame()
        panel.setObjectName("CollabStepPanel")
        panel.setMinimumHeight(148)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(8)

        title_lbl = QLabel(title)
        title_lbl.setObjectName("CollabStepTitle")
        layout.addWidget(title_lbl)
        layout.addWidget(state_label)

        detail_lbl = QLabel(detail)
        detail_lbl.setObjectName("CollabStepDetail")
        detail_lbl.setWordWrap(True)
        layout.addWidget(detail_lbl, 1)

        action_row = QHBoxLayout()
        action_row.setSpacing(8)
        for button in buttons:
            action_row.addWidget(button)
        action_row.addStretch()
        layout.addLayout(action_row)
        return panel

    def on_activate(self) -> None:
        """Refresh devices + tasks from the service."""
        self._service = getattr(self.ctx, "collab_service", self._service)
        if self._service is not None:
            self._service.ensure_running(self.ctx)
            self._connect_service_signals()
        if hasattr(self, "_share_picker"):
            self._share_picker.reload()
        self._refresh_devices()
        self._refresh_tasks()
        if self._service:
            self._debug_local_addr.setText(
                f"本机地址：{self._service.local_address()}"
            )
        if not self._retry_timer.isActive():
            self._retry_timer.start()

    # ── Signal wiring ─────────────────────────────────────────────────────

    def _connect_service_signals(self) -> None:
        if self._service is None:
            return
        if self._connected_service is self._service:
            return
        self._service.peers_changed.connect(self._refresh_devices)
        self._service.tasks_changed.connect(self._refresh_tasks)
        self._service.conflict_detected.connect(self._on_conflict)
        self._service.server_ready.connect(self._on_server_ready)
        if hasattr(self._service, "project_bind_suggested"):
            self._service.project_bind_suggested.connect(self._on_project_bind_suggested)
        if hasattr(self._service, "photo_index_received"):
            self._service.photo_index_received.connect(self._on_photo_index_received)
        self._connected_service = self._service

    @pyqtSlot(str, str, int, str)
    def _on_photo_index_received(self, uid: str, kind: str, count: int, device_id: str) -> None:
        self._refresh_tasks()
        if self._service is not None:
            self._refresh_devices()

    # ── Slots ─────────────────────────────────────────────────────────────

    @pyqtSlot(int)
    def _on_server_ready(self, port: int) -> None:
        if self._service:
            addr = self._service.local_address()
            self._debug_local_addr.setText(f"本机地址：{addr}")
            self._connection_label.setText(f"本机地址：{addr}")

    @pyqtSlot()
    def _refresh_devices(self) -> None:
        if self._service is None:
            return
        peers = self._service.peers()
        if hasattr(self._device_list, "clearSpans"):
            self._device_list.clearSpans()
        self._device_list.setRowCount(len(peers))
        for row, peer in enumerate(peers):
            self._device_list.setItem(row, 0, _ro_item(peer.hostname or peer.ip))
            self._device_list.setItem(row, 1, _ro_item(_project_display(peer.project_name)))
            self._device_list.setItem(row, 2, _ro_item(peer.group_code or "—"))
            media_label = self._peer_media_sync_label(peer)
            media_item = _ro_item(media_label)
            if media_label == "仅任务":
                media_item.setToolTip("同组但项目同步码不同；任务可见，照片不会同步。")
            elif media_label == "可同步":
                media_item.setToolTip("同组且项目同步码相同，可以同步照片/TIF/ZIP。")
            self._device_list.setItem(row, 3, media_item)
            addr_text = f"{peer.ip}:{peer.port}"
            if peer.manual:
                addr_text += " ✎"
            self._device_list.setItem(row, 4, _ro_item(addr_text))
            lat = f"{peer.latency_ms:.0f} ms" if peer.latency_ms is not None else "—"
            self._device_list.setItem(row, 5, _ro_item(lat))

        if not peers:
            self._device_list.setRowCount(1)
            empty = QTableWidgetItem(
                "暂无在线设备。让队友填写同一个团队永久码，或复制连接码手动连接。"
            )
            empty.setFlags(empty.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self._device_list.setItem(0, 0, empty)
            self._device_list.setSpan(0, 0, 1, self._device_list.columnCount())

        self._status_badge.setText(
            build_collab_status(self._service, peers).status_badge
        )
        tasks = self._service.store.list_tasks()
        self._refresh_project_filter(tasks, peers)
        self._refresh_summary(tasks, peers)
        self._refresh_activity_visibility(peers, tasks)

    @pyqtSlot()
    def _refresh_tasks(self) -> None:
        if self._service is None:
            return
        tasks = self._service.store.list_tasks()
        # Sort by updated_at descending
        tasks.sort(key=lambda t: t.updated_at, reverse=True)
        peers = self._service.peers()
        self._refresh_project_filter(tasks, peers)
        visible_tasks = self._filtered_tasks(tasks)
        if hasattr(self._task_table, "clearSpans"):
            self._task_table.clearSpans()
        self._task_table.setRowCount(len(visible_tasks))
        for row, task in enumerate(visible_tasks):
            uid_item = QTableWidgetItem(task.uid)
            uid_item.setFlags(uid_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self._task_table.setItem(row, _TASK_COL_UID, uid_item)

            project_item = _ro_item(_project_display(task.project_name))
            self._task_table.setItem(row, _TASK_COL_PROJECT, project_item)

            status_val = task.status.value if hasattr(task.status, "value") else str(task.status)
            label = _STATUS_LABEL.get(status_val, status_val)
            colour = _STATUS_COLOURS.get(status_val, "#ffffff")
            status_item = QTableWidgetItem(label)
            status_item.setFlags(status_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            status_item.setBackground(_hex_to_qcolor(colour))
            self._task_table.setItem(row, _TASK_COL_STATUS, status_item)

            self._task_table.setItem(row, _TASK_COL_ASSIGNEE, _ro_item(task.assignee or "—"))
            ts = task.updated_at[:19].replace("T", " ") if task.updated_at else "—"
            self._task_table.setItem(row, _TASK_COL_UPDATED, _ro_item(ts))

        if not visible_tasks:
            self._task_table.setRowCount(1)
            empty = QTableWidgetItem(
                "暂无协作任务。进入照片工作区保存新编号后，任务会在这里同步给同组电脑。"
            )
            empty.setFlags(empty.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self._task_table.setItem(0, 0, empty)
            self._task_table.setSpan(0, 0, 1, self._task_table.columnCount())
        self._refresh_summary(tasks, peers)
        self._refresh_activity_visibility(peers, tasks)

    def _refresh_activity_visibility(
        self,
        peers: list["PeerInfo"],
        tasks: list["TaskRecord"],
    ) -> None:
        has_content = bool(peers) or bool(tasks)
        if hasattr(self, "_empty_banner"):
            self._empty_banner.setVisible(not has_content)

    def _refresh_project_filter(self, tasks: list["TaskRecord"], peers: list["PeerInfo"]) -> None:
        if not hasattr(self, "_project_combo"):
            return
        current = self._project_filter
        projects = {
            _project_display(getattr(task, "project_name", ""))
            for task in tasks
            if _project_display(getattr(task, "project_name", ""))
        }
        projects.update(
            _project_display(getattr(peer, "project_name", ""))
            for peer in peers
            if _project_display(getattr(peer, "project_name", ""))
        )
        local_project = _project_display(self._local_project_name())
        if local_project:
            projects.add(local_project)

        ordered = sorted(projects, key=lambda text: text.casefold())
        self._project_combo.blockSignals(True)
        self._project_combo.clear()
        self._project_combo.addItem("全部项目", "")
        for project in ordered:
            self._project_combo.addItem(project, project)
        idx = self._project_combo.findData(current)
        if idx < 0:
            current = ""
            idx = 0
        self._project_filter = current
        self._project_combo.setCurrentIndex(idx)
        self._project_combo.blockSignals(False)

    def _filtered_tasks(self, tasks: list["TaskRecord"]) -> list["TaskRecord"]:
        if not self._project_filter:
            return tasks
        return [
            task for task in tasks
            if _project_display(getattr(task, "project_name", "")) == self._project_filter
        ]

    def _refresh_summary(self, tasks: list["TaskRecord"], peers: list["PeerInfo"]) -> None:
        if self._service is None:
            self._scope_label.setText("团队永久码：—")
            self._connection_label.setText("本机地址：启动后显示")
            self._local_project_badge.setText("本机 —")
            self._device_count_badge.setText("在线 0")
            self._project_count_badge.setText("项目 0")
            self._task_count_badge.setText("任务 0")
            self._conflict_count_badge.setText("冲突：0")
            self._refresh_next_step([])
            return

        status = build_collab_status(self._service, peers)
        local_project = _project_display(self._local_project_name()) or "—"
        projects = {
            _project_display(getattr(task, "project_name", ""))
            for task in tasks
            if _project_display(getattr(task, "project_name", ""))
        }
        if local_project != "—":
            projects.add(local_project)
        projects.update(
            _project_display(getattr(peer, "project_name", ""))
            for peer in peers
            if _project_display(getattr(peer, "project_name", ""))
        )
        conflict_count = sum(
            1 for task in tasks
            if (task.status.value if hasattr(task.status, "value") else str(task.status)) == "conflict"
        )
        self._scope_label.setText(status.scope_label)
        if status.state in {"no_service", "not_started"}:
            self._connection_label.setText("本机地址：启动后显示")
        else:
            self._connection_label.setText(f"本机地址：{self._service.local_address()}")
        self._local_project_badge.setText(f"本机 {local_project}")
        self._device_count_badge.setText(f"在线 {len(peers)}")
        self._project_count_badge.setText(f"项目 {len(projects)}")
        self._task_count_badge.setText(f"任务 {len(tasks)}")
        self._conflict_count_badge.setText(f"冲突 {conflict_count}")
        self._conflict_count_badge.setObjectName(
            "CollabMetricDanger" if conflict_count else "CollabMetric"
        )
        self._conflict_count_badge.style().unpolish(self._conflict_count_badge)
        self._conflict_count_badge.style().polish(self._conflict_count_badge)
        self._refresh_scope_cards(peers)
        self._refresh_next_step(peers)

    def _refresh_scope_cards(self, peers: list["PeerInfo"]) -> None:
        svc = self._service
        if svc is None:
            self._team_scope_state.setText("团队永久码：未启动")
            self._project_scope_state.setText("项目码共享：可单独使用；协作服务未启动时只生成/连接项目码。")
            self._same_name_btn.hide()
            return

        code = str(getattr(svc, "group_code", "") or "")
        if code:
            self._team_scope_state.setText(f"团队永久码：{code}")
        else:
            self._team_scope_state.setText("团队永久码：未设置")
            self._project_scope_state.setText(
                "项目码共享：可单独使用；共享项目范围只影响团队永久码模式。"
            )
            self._same_name_btn.hide()
            return

        from app.services.collab_project_bind import (
            describe_project_sync_state,
            same_name_options,
        )
        self._project_scope_state.setText(describe_project_sync_state(svc, peers))
        same = same_name_options(svc, peers)
        if same and getattr(svc, "project_id", ""):
            self._same_name_btn.show()
            self._same_name_btn.setToolTip(
                f"队友也在做「{same[0].name}」，一键绑定为同一项目"
            )
        else:
            self._same_name_btn.hide()

    def _refresh_next_step(self, peers: list["PeerInfo"]) -> None:
        if not hasattr(self, "_next_step_label"):
            return
        status = build_collab_status(self._service, peers)
        self._next_step_label.setText(status.next_step_label)
        self._next_step_detail.setText(status.next_step_detail)
        self._setup_btn.setEnabled(status.setup_enabled)
        if status.state in {"no_service", "not_started", "missing_group"}:
            self._setup_btn.setText("设置永久码")
            self._setup_btn.setToolTip("输入或生成团队永久码（保存后自动重连）")
        else:
            self._setup_btn.setText("修改永久码")
            self._setup_btn.setToolTip("查看或修改团队永久码、名字和连接方式")
        self._bind_project_btn.setEnabled(status.bind_project_enabled)
        self._pick_project_btn.setEnabled(status.bind_project_enabled)
        show_connect = status.state in {"no_peers", "different_group"} and self._service
        if getattr(self, "_connect_panel", None) is not None:
            self._connect_panel.setVisible(bool(show_connect))
        self._same_name_btn.setEnabled(status.bind_project_enabled)
        self._project_code_btn.setEnabled(True)
        self._share_btn.setEnabled(status.state not in {"no_service", "not_started"})
        self._share_btn.setVisible(self._share_btn.isEnabled())
        manual_enabled = status.state not in {"no_service", "not_started"}
        self._manual_toggle_btn.setEnabled(manual_enabled)
        self._manual_toggle_btn.setVisible(manual_enabled)
        if not manual_enabled and self._manual_toggle_btn.isChecked():
            self._manual_toggle_btn.setChecked(False)

    def _peer_media_sync_label(self, peer: "PeerInfo") -> str:
        svc = self._service
        if svc is None:
            return "—"
        if getattr(peer, "group_code", "") != svc.group_code:
            return "不同组"
        local_project_id = str(getattr(svc, "project_id", "") or "")
        peer_project_id = str(getattr(peer, "project_id", "") or "")
        if local_project_id and peer_project_id == local_project_id:
            return "可同步"
        return "仅任务"

    def _local_project_name(self) -> str:
        svc_project = getattr(self._service, "project_name", "") if self._service else ""
        if svc_project:
            return str(svc_project)
        return str(
            getattr(self.ctx, "current_project_dir", "")
            or getattr(getattr(self.ctx, "settings", None), "last_project_dir", "")
            or ""
        )

    @pyqtSlot(str)
    def _on_conflict(self, uid: str) -> None:
        msg = f'⚠ 编号冲突：“{uid}” 已被其他设备占用，请更改编号。'
        self._conflict_banner.setText(msg)
        self._conflict_banner.show()
        # Auto-hide after 8 s
        QTimer.singleShot(8000, self._conflict_banner.hide)

    def _on_manual_connect(self) -> None:
        ip = self._ip_input.text().strip()
        port_str = self._port_input.text().strip()
        if not ip:
            self._set_manual_error("请输入 IP 地址")
            return
        try:
            port = int(port_str)
        except ValueError:
            self._set_manual_error("端口号必须为数字")
            return
        if self._service:
            self._service.add_manual_peer(ip, port)
        self._ip_input.clear()

    def _on_project_filter_changed(self) -> None:
        self._project_filter = str(self._project_combo.currentData() or "")
        self._refresh_tasks()

    def _set_manual_error(self, msg: str) -> None:
        self._conflict_banner.setText(f"⚠ {msg}")
        self._conflict_banner.show()
        QTimer.singleShot(4000, self._conflict_banner.hide)

    def _on_share_addr(self) -> None:
        dlg = _CollabShareDialog(self.ctx, self)
        dlg.exec()

    def _on_pick_team_project(self) -> None:
        from app.widgets.collab_project_bind_dialog import CollabProjectBindDialog
        dlg = CollabProjectBindDialog(self.ctx, self)
        dlg.applied.connect(self._on_project_bind_applied)
        dlg.exec()

    def _on_same_name_bind_quick(self) -> None:
        from app.services.collab_project_bind import same_name_options
        from app.widgets.collab_project_bind_dialog import CollabProjectBindDialog

        svc = self._service
        if svc is None:
            return
        opts = same_name_options(svc, svc.peers())
        if not opts:
            self._on_pick_team_project()
            return
        dlg = CollabProjectBindDialog(self.ctx, self)
        dlg._bind_option(opts[0])

    def _on_project_bind_applied(self) -> None:
        self._refresh_devices()
        self._refresh_tasks()

    def _on_project_bind_suggested(self, peer_name: str, project_name: str, sync_code: str) -> None:
        msg = QMessageBox(self)
        msg.setWindowTitle("发现同名项目")
        msg.setText(
            f"队友 <b>{peer_name}</b> 也在做「{project_name}」。\n"
            "是否绑定为同一项目并开始同步？"
        )
        msg.setInformativeText(
            "绑定后标本和照片/TIF 会互通；若只是恰好同名，请选「暂不绑定」。"
        )
        msg.setStandardButtons(
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        msg.button(QMessageBox.StandardButton.Yes).setText("绑定并同步")
        msg.button(QMessageBox.StandardButton.No).setText("暂不绑定")
        if msg.exec() != QMessageBox.StandardButton.Yes:
            return
        svc = self._service
        if svc is None:
            return
        try:
            svc.apply_project_sync_code(sync_code)
            svc.pull_all_specimens_from_session()
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "绑定失败", str(exc))
            return
        self._on_project_bind_applied()

    def _on_project_sync_code(self) -> None:
        dlg = _ProjectSyncCodeDialog(self.ctx, self)
        dlg.applied.connect(self._on_project_bind_applied)
        dlg.exec()

    def _on_project_sync_code_applied(self) -> None:
        self._on_project_bind_applied()

    def _on_share_selection_changed(self) -> None:
        if not hasattr(self, "_share_picker"):
            return
        self._share_picker.apply_selection()
        self._refresh_scope_cards(self._service.peers() if self._service else [])
        self._refresh_devices()

    def _on_setup_wizard(self) -> None:
        from app.widgets.collab_setup_wizard import CollabSetupWizard
        wizard = CollabSetupWizard(self.ctx, self)
        wizard.setup_completed.connect(self._on_setup_wizard_done)
        wizard.exec()

    def _on_setup_wizard_done(self, group_code: str, operator: str) -> None:
        self._service = getattr(self.ctx, "collab_service", self._service)
        self._connect_service_signals()
        if hasattr(self, "_share_picker"):
            self._share_picker.reload()
        self._refresh_devices()
        self._refresh_tasks()

    def _on_task_context_menu(self, pos) -> None:
        row = self._task_table.rowAt(pos.y())
        if row < 0:
            return
        uid_item = self._task_table.item(row, 0)
        if uid_item is None:
            return
        uid = uid_item.text()

        status_item = self._task_table.item(row, _TASK_COL_STATUS)
        if status_item is None:
            return
        status_label = status_item.text() if status_item else ""
        is_conflict = status_label == _STATUS_LABEL.get("conflict", "冲突")

        menu = QMenu(self)
        assign_act = menu.addAction("分配给我")
        status_menu = menu.addMenu("更改状态")
        status_actions: dict = {}
        for ns, label in (
            ("shooting", "拍摄中"),
            ("shot_done", "已拍完"),
            ("organizing", "整理中"),
            ("done", "完成"),
        ):
            status_actions[status_menu.addAction(label)] = ns
        void_act = menu.addAction("作废")
        resolve_act = None
        if is_conflict:
            resolve_act = menu.addAction("处理冲突")

        action = menu.exec(self._task_table.viewport().mapToGlobal(pos))
        svc = self._service
        if not svc or action is None:
            return

        if action == assign_act:
            operator = getattr(getattr(self.ctx, "settings", None), "operator_name", "")
            try:
                svc.assign_task(uid, operator)
            except Exception:
                pass
        elif action == void_act:
            try:
                svc.void_task(uid)
            except Exception:
                pass
        elif resolve_act is not None and action == resolve_act:
            try:
                svc.resolve_conflict(uid)
            except Exception:
                pass
        elif action in status_actions:
            self._update_task_status(uid, status_actions[action])
        self.on_activate()

    def _toggle_debug_drawer(self, checked: bool) -> None:
        self._debug_drawer.setVisible(checked)
        if checked and self._service:
            self._debug_local_addr.setText(
                f"本机地址：{self._service.local_address()}"
            )
            peers = self._service.peers()
            lines = [f"  {p.hostname or p.ip}:{p.port}  延迟={p.latency_ms:.0f}ms" if p.latency_ms else
                     f"  {p.hostname or p.ip}:{p.port}" for p in peers]
            body = "\n".join(lines) if lines else "  （无在线节点）"
            self._debug_log.setText(f"在线节点：\n{body}")

    def _toggle_manual_connect(self, checked: bool) -> None:
        self._manual_group.setVisible(checked)
        self._manual_toggle_btn.setText("收起手动" if checked else "手动连接")
        if checked:
            self._ip_input.setFocus(Qt.FocusReason.OtherFocusReason)

    def _retry_offline_drafts(self) -> None:
        if self._service is None:
            return
        self._offline_queue.retry_all(self._service)

    def _update_task_status(self, uid: str, status: str) -> None:
        """Update task status, queuing as offline draft on network failure."""
        if self._service is None:
            return
        try:
            self._service.update_task_status(
                uid, status, force=True, broadcast=True,
            )
        except Exception:
            self._offline_queue.mark_draft(uid, status)

    def _show_no_service_placeholder(self) -> None:
        if hasattr(self._task_table, "clearSpans"):
            self._task_table.clearSpans()
        self._task_table.setRowCount(1)
        item = QTableWidgetItem("CollabService 未初始化 — 服务未启动")
        item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        self._task_table.setItem(0, 0, item)
        self._task_table.setSpan(0, 0, 1, self._task_table.columnCount())
        self._status_badge.setText("⚪ 协作服务未启动")
        self._refresh_summary([], [])
        self._refresh_activity_visibility([], [])


# ── Helpers ───────────────────────────────────────────────────────────────────

def _ro_item(text: str) -> QTableWidgetItem:
    item = QTableWidgetItem(text)
    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
    return item


def _project_display(project: Optional[str]) -> str:
    raw = str(project or "").strip()
    if not raw:
        return ""
    normalised = raw.replace("\\", "/").rstrip("/")
    path = Path(normalised)
    name = path.name
    return name or raw


def _hex_to_qcolor(hex_colour: str):  # type: ignore[return]
    """Convert #rrggbb to QColor (import deferred to avoid top-level Qt import)."""
    from PyQt6.QtGui import QColor
    return QColor(hex_colour)


# ── Project sync code dialog ─────────────────────────────────────────────────

class _ProjectSyncCodeDialog(QDialog):
    """Share or adopt a stable project identity for media sync."""

    applied = pyqtSignal()

    def __init__(self, ctx: "AppContext", parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self._svc = getattr(ctx, "collab_service", None)
        self.setWindowTitle("项目码共享")
        self.setMinimumSize(780, 430)

        current_dir = str(getattr(ctx, "current_project_dir", "") or "")
        self._project_options = []
        try:
            from app.services.collab_share_registry import list_local_share_candidates
            extras = [current_dir] if current_dir else None
            self._project_options = list_local_share_candidates(extra_directories=extras)
        except Exception:  # noqa: BLE001
            self._project_options = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(12)
        note = QLabel(
            "项目码不是连接地址，也不包含本机绝对路径。它只声明“这两个文件夹是同一个采集项目”。"
            "团队永久码负责自动连接；项目码负责照片/TIF/ZIP 同步范围。"
        )
        note.setWordWrap(True)
        layout.addWidget(note)

        flow = QLabel(
            "多人使用：第一台电脑复制项目码发给所有队友；第 2、3、4 台电脑都选择自己的本机项目，"
            "粘贴同一个项目码并连接。"
        )
        flow.setObjectName("MutedSmall")
        flow.setWordWrap(True)
        layout.addWidget(flow)

        project_row = QHBoxLayout()
        project_row.setSpacing(8)
        project_row.addWidget(QLabel("本机项目："))
        self._local_project_combo = QComboBox()
        self._local_project_combo.setMinimumWidth(260)
        for project in self._project_options:
            self._local_project_combo.addItem(project.name, project.directory)
        self._local_project_combo.currentIndexChanged.connect(self._refresh_local_code)
        project_row.addWidget(self._local_project_combo, 1)
        browse_btn = QPushButton("选择文件夹…")
        browse_btn.setObjectName("Ghost")
        browse_btn.clicked.connect(self._browse_local_project)
        project_row.addWidget(browse_btn)
        layout.addLayout(project_row)

        self._local_path_label = QLabel("")
        self._local_path_label.setObjectName("MutedSmall")
        self._local_path_label.setWordWrap(True)
        layout.addWidget(self._local_path_label)

        panels = QHBoxLayout()
        panels.setSpacing(12)

        send_panel = QFrame()
        send_panel.setObjectName("CollabStepPanel")
        send_lay = QVBoxLayout(send_panel)
        send_lay.setContentsMargins(12, 10, 12, 10)
        send_lay.setSpacing(8)
        send_title = QLabel("1. 本机项目码")
        send_title.setObjectName("CollabStepTitle")
        send_lay.addWidget(send_title)
        send_hint = QLabel("把这个码发给所有参与同一采集项目的电脑。")
        send_hint.setObjectName("MutedSmall")
        send_hint.setWordWrap(True)
        send_lay.addWidget(send_hint)
        self._local_code = QLineEdit()
        self._local_code.setReadOnly(True)
        self._local_code.setPlaceholderText("选择项目后生成项目码")
        send_lay.addWidget(self._local_code)

        self._copy_local_btn = QPushButton("复制所选项目码")
        self._copy_local_btn.clicked.connect(self._copy_local_code)
        send_lay.addWidget(self._copy_local_btn)
        panels.addWidget(send_panel, 1)

        join_panel = QFrame()
        join_panel.setObjectName("CollabStepPanel")
        join_lay = QVBoxLayout(join_panel)
        join_lay.setContentsMargins(12, 10, 12, 10)
        join_lay.setSpacing(8)
        join_title = QLabel("2. 连接队友项目码")
        join_title.setObjectName("CollabStepTitle")
        join_lay.addWidget(join_title)
        join_hint = QLabel("其他电脑在这里粘贴项目码，把所选本机项目连接到同一个采集项目。")
        join_hint.setObjectName("MutedSmall")
        join_hint.setWordWrap(True)
        join_lay.addWidget(join_hint)
        self._join_code = QLineEdit()
        self._join_code.setPlaceholderText("粘贴队友的项目码")
        join_lay.addWidget(self._join_code)
        self._apply_btn = QPushButton("连接到所选项目")
        self._apply_btn.setObjectName("Primary")
        self._apply_btn.clicked.connect(self._apply_join_code)
        join_lay.addWidget(self._apply_btn)
        panels.addWidget(join_panel, 1)
        layout.addLayout(panels)

        self._project_status_label = QLabel("项目状态：—")
        self._project_status_label.setObjectName("MutedSmall")
        self._project_status_label.setWordWrap(True)
        layout.addWidget(self._project_status_label)

        action_row = QHBoxLayout()
        action_row.addStretch()
        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(self.close)
        action_row.addWidget(close_btn)
        layout.addLayout(action_row)
        self._refresh_local_code()

    def _copy_local_code(self) -> None:
        code = self._local_code.text().strip()
        if code:
            directory = self._selected_project_directory()
            if directory:
                self._share_directory(directory)
            QApplication.clipboard().setText(code)

    def _selected_project_directory(self) -> str:
        return str(self._local_project_combo.currentData() or "").strip()

    def _selected_project_name(self) -> str:
        idx = self._local_project_combo.currentIndex()
        if 0 <= idx < len(self._project_options):
            return str(getattr(self._project_options[idx], "name", "") or "")
        directory = self._selected_project_directory()
        return Path(directory).name if directory else ""

    def _share_directory(self, directory: str) -> None:
        if not directory:
            return
        try:
            from app.services.collab_share_registry import (
                load_shared_dirs,
                save_shared_dirs,
            )
            settings = getattr(self.ctx, "settings", None)
            qs = getattr(settings, "_qs", settings)
            selected = load_shared_dirs(qs)
            try:
                selected.add(str(Path(directory).resolve()))
            except OSError:
                selected.add(directory)
            save_shared_dirs(qs, selected)
            svc = self._svc
            if svc is not None and hasattr(svc, "set_shared_project_dirs"):
                svc.set_shared_project_dirs(selected)
        except Exception:  # noqa: BLE001
            pass

    def _add_project_option(self, directory: str) -> bool:
        from app.services.project_tree_service import is_workspace
        from app.services.collab_share_registry import (
            LocalShareProject,
            read_project_id_for_directory,
        )

        raw = str(directory or "").strip()
        if not raw:
            return False
        try:
            resolved = str(Path(raw).resolve())
        except OSError:
            resolved = raw
        if not is_workspace(resolved):
            QMessageBox.warning(self, "项目码共享", "请选择已经创建的拍照工作区。")
            return False
        for i in range(self._local_project_combo.count()):
            if str(self._local_project_combo.itemData(i)) == resolved:
                self._local_project_combo.setCurrentIndex(i)
                return True
        project = LocalShareProject(
            directory=resolved,
            name=Path(resolved).name or resolved,
            project_id=read_project_id_for_directory(resolved),
        )
        self._project_options.append(project)
        self._local_project_combo.addItem(project.name, project.directory)
        self._local_project_combo.setCurrentIndex(self._local_project_combo.count() - 1)
        return True

    def _browse_local_project(self) -> None:
        directory = QFileDialog.getExistingDirectory(
            self,
            "选择本机拍照工作区",
            self._selected_project_directory() or str(getattr(self.ctx, "current_project_dir", "") or ""),
        )
        if directory:
            self._add_project_option(directory)

    def _refresh_local_code(self) -> None:
        code = ""
        path_text = "未找到可生成项目码的本机项目。请先打开或新建项目。"
        status_text = "项目状态：未选择本机项目。"
        directory = self._selected_project_directory()
        name = self._selected_project_name()
        if directory:
            path_text = f"本机绝对路径：{directory}"
            try:
                from app.services.project_identity_service import project_sync_code
                from app.services.collab_share_registry import (
                    project_id_for_directory,
                    read_project_id_for_directory,
                )
                existing = read_project_id_for_directory(directory)
                pid = existing or project_id_for_directory(directory)
                code = project_sync_code(pid, project_name=name or Path(directory).name)
                status_text = (
                    f"项目状态：已绑定项目码 {pid[:8]}…"
                    if existing else
                    f"项目状态：已为所选项目生成项目码 {pid[:8]}…"
                )
            except Exception:  # noqa: BLE001
                code = ""
                status_text = "项目状态：无法生成项目码，请确认这是有效拍照工作区。"
        elif self._svc is not None and hasattr(self._svc, "project_sync_code"):
            code = self._svc.project_sync_code()

        self._local_path_label.setText(path_text)
        self._local_code.setText(code)
        self._copy_local_btn.setEnabled(bool(code))
        if hasattr(self, "_apply_btn"):
            self._apply_btn.setEnabled(bool(directory))
        if hasattr(self, "_project_status_label"):
            self._project_status_label.setText(status_text)

    def _apply_join_code(self) -> None:
        directory = self._selected_project_directory()
        if not directory:
            QMessageBox.warning(self, "项目码共享", "请先选择本机项目。")
            return
        raw = self._join_code.text().strip()
        try:
            from app.services.project_identity_service import parse_project_sync_code
            parsed = parse_project_sync_code(raw)
        except ValueError:
            QMessageBox.warning(self, "绑定同一项目", "项目码格式不正确。")
            return

        new_project_id = parsed["projectId"]
        try:
            from app.services.collab_share_registry import read_project_id_for_directory
            current_id = read_project_id_for_directory(directory)
        except Exception:  # noqa: BLE001
            current_id = ""
        if new_project_id == current_id:
            QMessageBox.information(self, "绑定同一项目", "所选项目已经使用这个项目码。")
            self._share_directory(directory)
            self._project_status_label.setText(
                f"项目状态：所选项目已连接到 {new_project_id[:8]}…，可把同一个项目码给更多电脑使用。"
            )
            return

        remote_name = _project_display(parsed.get("projectName", "")) or "队友项目"
        ret = QMessageBox.warning(
            self,
            "确认绑定同一项目",
            f"即将把本机项目「{Path(directory).name}」连接到“{remote_name}”。\n\n"
            "只有确认两边是同一个采集项目时才继续；确认后同组设备可以互相同步照片/TIF/ZIP。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if ret != QMessageBox.StandardButton.Yes:
            return
        try:
            from app.services.collab_share_registry import apply_project_sync_code_to_directory
            applied_id = apply_project_sync_code_to_directory(directory, raw)
            svc = self._svc
            service_dir = str(getattr(svc, "_project_dir", "") or "") if svc is not None else ""
            try:
                same_as_service = bool(service_dir) and Path(service_dir).resolve() == Path(directory).resolve()
            except OSError:
                same_as_service = service_dir == directory
            if same_as_service and svc is not None:
                svc._project_id = applied_id
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "绑定同一项目", f"写入项目码失败：{exc}")
            return
        self._share_directory(directory)
        self._refresh_local_code()
        self._project_status_label.setText(
            f"项目状态：已连接到队友项目码 {applied_id[:8]}…；第 3、4 台电脑可重复粘贴同一个码。"
        )
        QMessageBox.information(self, "绑定同一项目", "所选项目已绑定到同一个照片同步项目。")
        self.applied.emit()


# ── Share address dialog ──────────────────────────────────────────────────────

class _CollabShareDialog(QDialog):
    """Dialog showing the local LAN address for sharing with other operators."""

    def __init__(self, ctx: "AppContext", parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("本机连接地址")
        self.setMinimumWidth(340)

        addr = ""
        svc = getattr(ctx, "collab_service", None)
        if svc is not None and hasattr(svc, "local_address"):
            addr = svc.local_address() or ""

        layout = QVBoxLayout(self)
        layout.setSpacing(10)
        layout.addWidget(QLabel("把这个地址发给队友，用于手动连接本机："))

        self._addr_edit = QLineEdit(addr)
        self._addr_edit.setReadOnly(True)
        layout.addWidget(self._addr_edit)

        copy_btn = QPushButton("复制本机连接地址")
        copy_btn.clicked.connect(lambda: QApplication.clipboard().setText(addr))
        layout.addWidget(copy_btn)

        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(self.close)
        layout.addWidget(close_btn)
