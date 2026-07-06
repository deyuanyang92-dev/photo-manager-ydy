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
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.services.collab_offline_queue import OfflineDraftQueue
from app.services.collab_status import build_collab_status
from app.views.base_view import BaseView

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
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(12)

        # ── Header: global collaboration centre ──────────────────────────
        header_frame = QFrame()
        header_frame.setObjectName("CollabHeader")
        header_layout = QVBoxLayout(header_frame)
        header_layout.setContentsMargins(14, 12, 14, 12)
        header_layout.setSpacing(10)

        header = QHBoxLayout()
        title_col = QVBoxLayout()
        title_col.setSpacing(2)
        title = QLabel("协作中心")
        title.setObjectName("CollabTitle")
        title_col.addWidget(title)
        self._scope_label = QLabel("协作组：—")
        self._scope_label.setObjectName("CollabScope")
        title_col.addWidget(self._scope_label)
        self._connection_label = QLabel("本机地址：启动后显示")
        self._connection_label.setObjectName("CollabScope")
        title_col.addWidget(self._connection_label)
        header.addLayout(title_col, 1)

        self._status_badge = QLabel("⚪ 协作未启动")
        self._status_badge.setObjectName("CollabStatusBadge")
        bold = QFont()
        bold.setBold(True)
        bold.setPointSize(12)
        self._status_badge.setFont(bold)
        header.addWidget(self._status_badge)

        self._debug_btn = QPushButton("诊断")
        self._debug_btn.setObjectName("Ghost")
        self._debug_btn.setCheckable(True)
        self._debug_btn.setFixedWidth(66)
        self._debug_btn.toggled.connect(self._toggle_debug_drawer)
        header.addWidget(self._debug_btn)
        header_layout.addLayout(header)

        metrics = QHBoxLayout()
        metrics.setSpacing(8)
        self._local_project_badge = QLabel("本机项目：—")
        self._local_project_badge.setObjectName("CollabMetric")
        metrics.addWidget(self._local_project_badge)
        self._device_count_badge = QLabel("在线设备：0")
        self._device_count_badge.setObjectName("CollabMetric")
        metrics.addWidget(self._device_count_badge)
        self._project_count_badge = QLabel("项目：0")
        self._project_count_badge.setObjectName("CollabMetric")
        metrics.addWidget(self._project_count_badge)
        self._task_count_badge = QLabel("任务：0")
        self._task_count_badge.setObjectName("CollabMetric")
        metrics.addWidget(self._task_count_badge)
        self._conflict_count_badge = QLabel("冲突：0")
        self._conflict_count_badge.setObjectName("CollabMetric")
        metrics.addWidget(self._conflict_count_badge)
        metrics.addStretch()
        header_layout.addLayout(metrics)
        root.addWidget(header_frame)

        guide = QFrame()
        guide.setObjectName("CollabGuide")
        guide_layout = QHBoxLayout(guide)
        guide_layout.setContentsMargins(12, 10, 12, 10)
        guide_layout.setSpacing(12)

        guide_text = QVBoxLayout()
        guide_text.setSpacing(3)
        self._next_step_label = QLabel("下一步：—")
        self._next_step_label.setObjectName("CollabGuideTitle")
        guide_text.addWidget(self._next_step_label)
        self._next_step_detail = QLabel("先让电脑互相看见，再确认哪些电脑允许同步照片。")
        self._next_step_detail.setObjectName("CollabGuideDetail")
        self._next_step_detail.setWordWrap(True)
        guide_text.addWidget(self._next_step_detail)
        guide_layout.addLayout(guide_text, 1)

        root.addWidget(guide)

        # Three visible actions in the order a new operator actually needs them.
        self._setup_btn = QPushButton("启动/加入")
        self._setup_btn.setObjectName("Primary")
        self._setup_btn.setMinimumWidth(112)
        self._setup_btn.clicked.connect(self._on_setup_wizard)

        self._share_btn = QPushButton("复制连接地址")
        self._share_btn.setObjectName("Outline")
        self._share_btn.setMinimumWidth(112)
        self._share_btn.clicked.connect(self._on_share_addr)

        self._manual_toggle_btn = QPushButton("手动连接")
        self._manual_toggle_btn.setObjectName("Ghost")
        self._manual_toggle_btn.setCheckable(True)
        self._manual_toggle_btn.setMinimumWidth(86)
        self._manual_toggle_btn.toggled.connect(self._toggle_manual_connect)

        self._project_code_btn = QPushButton("绑定同一项目")
        self._project_code_btn.setObjectName("Outline")
        self._project_code_btn.setMinimumWidth(112)
        self._project_code_btn.clicked.connect(self._on_project_sync_code)
        self._bind_project_btn = self._project_code_btn

        steps = QHBoxLayout()
        steps.setSpacing(10)
        steps.addWidget(
            self._make_step_panel(
                "1",
                "组队",
                "同一团队使用同一个协作组码。",
                self._setup_btn,
            ),
            1,
        )
        steps.addWidget(
            self._make_step_panel(
                "2",
                "连接",
                "自动发现优先；找不到时再发连接地址或手动输入。",
                self._share_btn,
                self._manual_toggle_btn,
            ),
            1,
        )
        steps.addWidget(
            self._make_step_panel(
                "3",
                "照片同步",
                "确认为同一项目后，才同步照片/TIF/ZIP。",
                self._project_code_btn,
            ),
            1,
        )
        root.addLayout(steps)

        # ── Conflict banner (hidden by default) ───────────────────────────
        self._conflict_banner = QLabel()
        self._conflict_banner.setObjectName("ConflictBanner")
        self._conflict_banner.setWordWrap(True)
        self._conflict_banner.hide()
        root.addWidget(self._conflict_banner)

        # Manual IP connection: novice flow keeps this hidden until requested.
        manual_group = QFrame()
        manual_group.setObjectName("ManualConnectFrame")
        manual_group.setFrameShape(QFrame.Shape.StyledPanel)
        manual_layout = QHBoxLayout(manual_group)
        manual_layout.setContentsMargins(10, 9, 10, 9)
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
        self._connect_btn.setFixedWidth(64)
        self._connect_btn.clicked.connect(self._on_manual_connect)
        manual_layout.addWidget(self._connect_btn)
        manual_group.hide()
        self._manual_group = manual_group
        root.addWidget(manual_group)

        # ── Main splitter: device list | task table ────────────────────────
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        # Left — devices
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(8)

        dev_title = QLabel("在线设备")
        dev_title.setObjectName("Section")
        left_layout.addWidget(dev_title)

        self._device_list = QTableWidget(0, 6)
        self._device_list.setHorizontalHeaderLabels(["主机名", "项目", "组码", "照片", "地址", "延迟"])
        self._device_list.horizontalHeader().setStretchLastSection(False)
        self._device_list.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch
        )
        self._device_list.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.ResizeToContents
        )
        self._device_list.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeMode.ResizeToContents
        )
        self._device_list.horizontalHeader().setSectionResizeMode(
            3, QHeaderView.ResizeMode.ResizeToContents
        )
        self._device_list.horizontalHeader().setSectionResizeMode(
            4, QHeaderView.ResizeMode.ResizeToContents
        )
        self._device_list.horizontalHeader().setSectionResizeMode(
            5, QHeaderView.ResizeMode.ResizeToContents
        )
        self._device_list.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._device_list.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._device_list.setAlternatingRowColors(True)
        self._device_list.verticalHeader().hide()
        left_layout.addWidget(self._device_list)

        splitter.addWidget(left)

        # Right — task table
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(8)

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
        right_layout.addLayout(task_header)

        self._task_table = QTableWidget(0, 5)
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
        right_layout.addWidget(self._task_table)

        splitter.addWidget(right)
        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 3)
        root.addWidget(splitter, stretch=1)

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
        number: str,
        title: str,
        detail: str,
        *buttons: QPushButton,
    ) -> QFrame:
        panel = QFrame()
        panel.setObjectName("CollabStepPanel")
        panel.setMinimumHeight(96)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(8)

        title_row = QHBoxLayout()
        title_row.setSpacing(8)
        badge = QLabel(number)
        badge.setObjectName("CollabStepBadge")
        badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        badge.setFixedSize(22, 22)
        title_row.addWidget(badge)
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

    def on_activate(self) -> None:
        """Refresh devices + tasks from the service."""
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
        self._connected_service = self._service

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
                "暂无在线设备。让队友加入同一协作组，或把本机连接地址发给对方手动连接。"
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
            self._scope_label.setText("协作组：—")
            self._connection_label.setText("本机地址：启动后显示")
            self._local_project_badge.setText("本机项目：—")
            self._device_count_badge.setText("在线设备：0")
            self._project_count_badge.setText("项目：0")
            self._task_count_badge.setText("任务：0")
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
        self._local_project_badge.setText(f"本机项目：{local_project}")
        self._device_count_badge.setText(f"在线设备：{len(peers)}")
        self._project_count_badge.setText(f"项目：{len(projects)}")
        self._task_count_badge.setText(f"任务：{len(tasks)}")
        self._conflict_count_badge.setText(f"冲突：{conflict_count}")
        self._conflict_count_badge.setObjectName(
            "CollabMetricDanger" if conflict_count else "CollabMetric"
        )
        self._conflict_count_badge.style().unpolish(self._conflict_count_badge)
        self._conflict_count_badge.style().polish(self._conflict_count_badge)
        self._refresh_next_step(peers)

    def _refresh_next_step(self, peers: list["PeerInfo"]) -> None:
        if not hasattr(self, "_next_step_label"):
            return
        status = build_collab_status(self._service, peers)
        self._next_step_label.setText(status.next_step_label)
        self._next_step_detail.setText(status.next_step_detail)
        self._setup_btn.setEnabled(status.setup_enabled)
        if status.state in {"not_started", "missing_group"}:
            self._setup_btn.setText("启动/加入协作")
            self._setup_btn.setToolTip("创建协作组，或输入队友给你的组码加入")
        elif status.state == "no_service":
            self._setup_btn.setText("打开项目后启用")
            self._setup_btn.setToolTip("打开项目后才能启动协作")
        else:
            self._setup_btn.setText("管理协作组")
            self._setup_btn.setToolTip("调整协作组码、操作人和连接方式")
        self._bind_project_btn.setEnabled(status.bind_project_enabled)
        self._project_code_btn.setEnabled(status.bind_project_enabled)
        self._share_btn.setEnabled(status.state not in {"no_service", "not_started"})
        manual_enabled = status.state not in {"no_service", "not_started"}
        self._manual_toggle_btn.setEnabled(manual_enabled)
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

    def _on_project_sync_code(self) -> None:
        svc = self._service
        if svc is None or not getattr(svc, "project_id", ""):
            QMessageBox.information(
                self,
                "绑定同一项目",
                "请先打开项目并启用协作服务。",
            )
            return
        dlg = _ProjectSyncCodeDialog(self.ctx, self)
        dlg.applied.connect(self._on_project_sync_code_applied)
        dlg.exec()

    def _on_project_sync_code_applied(self) -> None:
        self._refresh_devices()
        self._refresh_tasks()

    def _on_setup_wizard(self) -> None:
        from app.widgets.collab_setup_wizard import CollabSetupWizard
        wizard = CollabSetupWizard(self.ctx, self)
        wizard.setup_completed.connect(self._on_setup_wizard_done)
        wizard.exec()

    def _on_setup_wizard_done(self, group_code: str, operator: str) -> None:
        self._service = getattr(self.ctx, "collab_service", self._service)
        self._connect_service_signals()
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
            self._service.update_task_status(uid, status)
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
        self.setWindowTitle("绑定同一项目")
        self.setMinimumWidth(560)

        project_name = _project_display(
            getattr(self._svc, "project_name", "")
            or getattr(ctx, "current_project_dir", "")
            or ""
        ) or "—"
        local_code = ""
        if self._svc is not None and hasattr(self._svc, "project_sync_code"):
            local_code = self._svc.project_sync_code()

        layout = QVBoxLayout(self)
        layout.setSpacing(10)
        layout.addWidget(QLabel(f"当前项目：{project_name}"))
        note = QLabel(
            "这个码不是连接地址，只决定哪些电脑被确认是同一个项目。绑定后，同协作组设备才能互相同步照片/TIF/ZIP；盘符和目录可以不同。"
        )
        note.setWordWrap(True)
        layout.addWidget(note)

        layout.addWidget(QLabel("把本机作为正确项目时，复制这个码给队友："))
        self._local_code = QLineEdit(local_code)
        self._local_code.setReadOnly(True)
        self._local_code.setPlaceholderText("当前项目尚未生成同步码")
        layout.addWidget(self._local_code)

        copy_btn = QPushButton("复制本机项目码")
        copy_btn.clicked.connect(self._copy_local_code)
        copy_btn.setEnabled(bool(local_code))
        layout.addWidget(copy_btn)

        layout.addWidget(QLabel("本机要加入队友项目时，粘贴队友给你的码："))
        self._join_code = QLineEdit()
        self._join_code.setPlaceholderText("粘贴队友的项目码")
        layout.addWidget(self._join_code)

        action_row = QHBoxLayout()
        action_row.addStretch()
        apply_btn = QPushButton("确认加入")
        apply_btn.clicked.connect(self._apply_join_code)
        action_row.addWidget(apply_btn)
        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(self.close)
        action_row.addWidget(close_btn)
        layout.addLayout(action_row)

    def _copy_local_code(self) -> None:
        code = self._local_code.text().strip()
        if code:
            QApplication.clipboard().setText(code)

    def _apply_join_code(self) -> None:
        svc = self._svc
        if svc is None or not hasattr(svc, "apply_project_sync_code"):
            QMessageBox.warning(self, "绑定同一项目", "协作服务未启动。")
            return
        raw = self._join_code.text().strip()
        try:
            from app.services.project_identity_service import parse_project_sync_code
            parsed = parse_project_sync_code(raw)
        except ValueError:
            QMessageBox.warning(self, "绑定同一项目", "项目码格式不正确。")
            return

        new_project_id = parsed["projectId"]
        if new_project_id == getattr(svc, "project_id", ""):
            QMessageBox.information(self, "绑定同一项目", "当前项目已经使用这个项目码。")
            return

        remote_name = _project_display(parsed.get("projectName", "")) or "队友项目"
        ret = QMessageBox.warning(
            self,
            "确认绑定同一项目",
            f"即将把当前项目加入“{remote_name}”的照片同步身份。\n\n"
            "只有确认两边是同一个采集项目时才继续；确认后同组设备可以互相同步照片/TIF/ZIP。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if ret != QMessageBox.StandardButton.Yes:
            return
        try:
            svc.apply_project_sync_code(raw)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "绑定同一项目", f"写入项目码失败：{exc}")
            return
        if hasattr(svc, "project_sync_code"):
            self._local_code.setText(svc.project_sync_code())
        QMessageBox.information(self, "绑定同一项目", "当前项目已绑定到同一个照片同步项目。")
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
