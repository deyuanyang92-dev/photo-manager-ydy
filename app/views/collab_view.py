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
from typing import Optional

from PyQt6.QtCore import Qt, QTimer, pyqtSlot
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
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


# ── CollabView ────────────────────────────────────────────────────────────────

class CollabView(BaseView):
    """Collaboration module view.

    Requires ctx.collab_service (CollabService) to be present.  If the service
    is not available the view shows a "服务未启动" placeholder row and becomes
    effectively read-only.
    """

    view_id   = "collab"
    nav_title = "项目汇总"
    nav_icon  = "📋"

    def __init__(self, ctx: "AppContext") -> None:
        # Service is optional — the view degrades gracefully when absent
        self._service: Optional["CollabService"] = getattr(ctx, "collab_service", None)
        super().__init__(ctx)
        self._offline_queue = OfflineDraftQueue(ctx.settings._qs)
        self._retry_timer = QTimer(self)
        self._retry_timer.setInterval(30_000)
        self._retry_timer.timeout.connect(self._retry_offline_drafts)
        self._connect_service_signals()

    # ── BaseView contract ─────────────────────────────────────────────────

    def _setup_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)

        # ── Header row ────────────────────────────────────────────────────
        header = QHBoxLayout()
        self._status_badge = QLabel("⚪ 未发现其他设备")
        self._status_badge.setObjectName("CollabStatusBadge")
        bold = QFont()
        bold.setBold(True)
        bold.setPointSize(13)
        self._status_badge.setFont(bold)
        header.addWidget(self._status_badge)
        header.addStretch()

        self._debug_btn = QPushButton("🔧 调试")
        self._debug_btn.setCheckable(True)
        self._debug_btn.setFixedWidth(80)
        self._debug_btn.toggled.connect(self._toggle_debug_drawer)
        header.addWidget(self._debug_btn)
        root.addLayout(header)

        # ── Conflict banner (hidden by default) ───────────────────────────
        self._conflict_banner = QLabel()
        self._conflict_banner.setObjectName("ConflictBanner")
        self._conflict_banner.setStyleSheet(
            "background: #ff5252; color: white; padding: 8px 12px; "
            "border-radius: 4px; font-weight: bold;"
        )
        self._conflict_banner.setWordWrap(True)
        self._conflict_banner.hide()
        root.addWidget(self._conflict_banner)

        # ── Main splitter: device list | task table ────────────────────────
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        # Left — devices
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(8)

        dev_title = QLabel("在线设备")
        dev_title.setObjectName("SectionTitle")
        left_layout.addWidget(dev_title)

        self._device_list = QTableWidget(0, 3)
        self._device_list.setHorizontalHeaderLabels(["主机名", "地址", "延迟"])
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
        self._device_list.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._device_list.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._device_list.setAlternatingRowColors(True)
        self._device_list.verticalHeader().hide()
        left_layout.addWidget(self._device_list)

        # Manual IP connection
        manual_group = QFrame()
        manual_group.setObjectName("ManualConnectFrame")
        manual_group.setFrameShape(QFrame.Shape.StyledPanel)
        manual_layout = QVBoxLayout(manual_group)
        manual_layout.setContentsMargins(8, 8, 8, 8)
        manual_layout.setSpacing(6)

        manual_title = QLabel("手动连接（mDNS 跨网段兜底）")
        manual_title.setObjectName("Muted")
        manual_layout.addWidget(manual_title)

        ip_row = QHBoxLayout()
        self._ip_input = QLineEdit()
        self._ip_input.setPlaceholderText("IP 地址，如 192.168.1.100")
        self._ip_input.setObjectName("ManualIpInput")
        ip_row.addWidget(self._ip_input)

        self._port_input = QLineEdit("5050")
        self._port_input.setFixedWidth(60)
        self._port_input.setObjectName("ManualPortInput")
        ip_row.addWidget(self._port_input)

        self._connect_btn = QPushButton("连接")
        self._connect_btn.setFixedWidth(56)
        self._connect_btn.clicked.connect(self._on_manual_connect)
        ip_row.addWidget(self._connect_btn)
        manual_layout.addLayout(ip_row)
        left_layout.addWidget(manual_group)

        splitter.addWidget(left)

        # Right — task table
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(8)

        task_title = QLabel("任务清单")
        task_title.setObjectName("SectionTitle")
        right_layout.addWidget(task_title)

        self._task_table = QTableWidget(0, 4)
        self._task_table.setHorizontalHeaderLabels(["UID", "状态", "负责人", "更新时间"])
        self._task_table.horizontalHeader().setStretchLastSection(False)
        self._task_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch
        )
        for col in (1, 2, 3):
            self._task_table.horizontalHeader().setSectionResizeMode(
                col, QHeaderView.ResizeMode.ResizeToContents
            )
        self._task_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._task_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._task_table.setAlternatingRowColors(True)
        self._task_table.verticalHeader().hide()
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
        self._service.peers_changed.connect(self._refresh_devices)
        self._service.tasks_changed.connect(self._refresh_tasks)
        self._service.conflict_detected.connect(self._on_conflict)
        self._service.server_ready.connect(self._on_server_ready)

    # ── Slots ─────────────────────────────────────────────────────────────

    @pyqtSlot(int)
    def _on_server_ready(self, port: int) -> None:
        if self._service:
            self._debug_local_addr.setText(
                f"本机地址：{self._service.local_address()}"
            )

    @pyqtSlot()
    def _refresh_devices(self) -> None:
        if self._service is None:
            return
        peers = self._service.peers()
        self._device_list.setRowCount(len(peers))
        for row, peer in enumerate(peers):
            self._device_list.setItem(row, 0, _ro_item(peer.hostname or peer.ip))
            addr_text = f"{peer.ip}:{peer.port}"
            if peer.manual:
                addr_text += " ✎"
            self._device_list.setItem(row, 1, _ro_item(addr_text))
            lat = f"{peer.latency_ms:.0f} ms" if peer.latency_ms is not None else "—"
            self._device_list.setItem(row, 2, _ro_item(lat))

        n = len(peers)
        if n:
            self._status_badge.setText(f"🟢 {n} 台在线")
        else:
            self._status_badge.setText("⚪ 未发现其他设备")

    @pyqtSlot()
    def _refresh_tasks(self) -> None:
        if self._service is None:
            return
        tasks = self._service.store.all()
        # Sort by updated_at descending
        tasks.sort(key=lambda t: t.updated_at, reverse=True)
        self._task_table.setRowCount(len(tasks))
        for row, task in enumerate(tasks):
            uid_item = QTableWidgetItem(task.uid)
            uid_item.setFlags(uid_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self._task_table.setItem(row, 0, uid_item)

            status_val = task.status.value if hasattr(task.status, "value") else str(task.status)
            label = _STATUS_LABEL.get(status_val, status_val)
            colour = _STATUS_COLOURS.get(status_val, "#ffffff")
            status_item = QTableWidgetItem(label)
            status_item.setFlags(status_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            status_item.setBackground(_hex_to_qcolor(colour))
            self._task_table.setItem(row, 1, status_item)

            self._task_table.setItem(row, 2, _ro_item(task.assignee or "—"))
            ts = task.updated_at[:19].replace("T", " ") if task.updated_at else "—"
            self._task_table.setItem(row, 3, _ro_item(ts))

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

    def _set_manual_error(self, msg: str) -> None:
        self._conflict_banner.setText(f"⚠ {msg}")
        self._conflict_banner.show()
        QTimer.singleShot(4000, self._conflict_banner.hide)

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
        self._task_table.setRowCount(1)
        item = QTableWidgetItem("CollabService 未初始化 — 服务未启动")
        item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        self._task_table.setItem(0, 0, item)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _ro_item(text: str) -> QTableWidgetItem:
    item = QTableWidgetItem(text)
    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
    return item


def _hex_to_qcolor(hex_colour: str):  # type: ignore[return]
    """Convert #rrggbb to QColor (import deferred to avoid top-level Qt import)."""
    from PyQt6.QtGui import QColor
    return QColor(hex_colour)
