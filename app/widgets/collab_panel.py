"""collab_panel.py — One-stop collaboration side-panel.

A persistent drawer-style panel that integrates device management, task
list, activity feed, and quick settings — replacing the fragmented
Settings→协作 tab + CollabManagerDialog + CollabView workflow.

Pattern: follows ProjectSettingsDrawer — instant show/hide, positioned
at the right edge of the WorkbenchView.  Width fixed at 380 px.
"""

from __future__ import annotations

import socket
from pathlib import Path
from typing import Optional, TYPE_CHECKING

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.config.icons import icon
from app.models.activity_log import ActivityEntry
from app.services.collab_status import build_collab_status
from app.widgets.activity_feed_widget import ActivityFeedWidget

if TYPE_CHECKING:
    from app.app_context import AppContext
    from app.services.collab_service import CollabService, PeerInfo, TaskRecord

# ── Status display helpers (shared with CollabManagerDialog) ──────────────────

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

_HEALTH_COLOR: dict[str, str] = {
    "green":  "#2e7d32",
    "yellow": "#f9a825",
    "red":    "#c62828",
}

_STATUS_TRANSITIONS = [
    ("shooting",   "拍摄中"),
    ("shot_done",  "已拍完"),
    ("organizing", "整理中"),
    ("done",       "完成"),
]


def _ro_item(text: str) -> QTableWidgetItem:
    item = QTableWidgetItem(text)
    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
    return item


def _project_display(project: Optional[str]) -> str:
    raw = str(project or "").strip()
    if not raw:
        return ""
    normalised = raw.replace("\\", "/").rstrip("/")
    name = Path(normalised).name
    return name or raw


# ── Panel ──────────────────────────────────────────────────────────────────────

class CollabPanel(QWidget):
    """One-stop collaboration side-panel."""

    closed = pyqtSignal()

    def __init__(self, ctx: "AppContext", parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self.setObjectName("CollabPanel")
        self.setFixedWidth(380)
        self.hide()

        self._svc: Optional[CollabService] = getattr(ctx, "collab_service", None)
        self._build_ui()
        self._connect_signals()

    # ── UI construction ────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        # Scrollable content
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        content = QWidget()
        root = QVBoxLayout(content)
        root.setContentsMargins(16, 16, 16, 12)
        root.setSpacing(12)

        # ── Header ──
        hdr = QHBoxLayout()
        title = QLabel("协作面板")
        title.setObjectName("CardTitle")
        hdr.addWidget(title)
        hdr.addStretch()

        self._close_btn = QPushButton("✕")
        self._close_btn.setObjectName("Ghost")
        self._close_btn.setFixedSize(28, 28)
        self._close_btn.clicked.connect(self._on_close)
        hdr.addWidget(self._close_btn)
        root.addLayout(hdr)

        # ── Health badge ──
        health_row = QHBoxLayout()
        self._health_dot = QLabel("●")
        self._health_dot.setFixedWidth(16)
        self._health_text = QLabel("协作状态未知")
        self._health_text.setObjectName("MutedSmall")
        health_row.addWidget(self._health_dot)
        health_row.addWidget(self._health_text, 1)
        root.addLayout(health_row)

        self._project_label = QLabel("当前项目：—")
        self._project_label.setObjectName("MutedSmall")
        root.addWidget(self._project_label)

        # Action buttons
        action_row = QHBoxLayout()
        self._diagnose_btn = QPushButton("诊断")
        self._diagnose_btn.setObjectName("Ghost")
        self._diagnose_btn.setFixedHeight(26)
        self._diagnose_btn.clicked.connect(self._on_diagnose)
        action_row.addWidget(self._diagnose_btn)

        self._peer_scan_btn = QPushButton("搜索队友")
        self._peer_scan_btn.setObjectName("Ghost")
        self._peer_scan_btn.setFixedHeight(26)
        self._peer_scan_btn.clicked.connect(self._on_scan)
        action_row.addWidget(self._peer_scan_btn)

        self._setup_btn = QPushButton("一键协作")
        self._setup_btn.setObjectName("Outline")
        self._setup_btn.setFixedHeight(26)
        self._setup_btn.clicked.connect(self._on_setup_wizard)
        action_row.addWidget(self._setup_btn)

        action_row.addStretch()
        root.addLayout(action_row)

        # ── Online devices (collapsible) ──
        dev_header = QHBoxLayout()
        self._dev_toggle = QPushButton("▾")
        self._dev_toggle.setObjectName("Ghost")
        self._dev_toggle.setFixedSize(20, 20)
        self._dev_toggle.clicked.connect(lambda: self._toggle_section(self._dev_body, self._dev_toggle))
        dev_header.addWidget(self._dev_toggle)
        self._dev_title = QLabel("在线设备 (0)")
        self._dev_title.setObjectName("Section")
        dev_header.addWidget(self._dev_title)
        dev_header.addStretch()
        root.addLayout(dev_header)

        self._dev_body = QWidget()
        dev_lay = QVBoxLayout(self._dev_body)
        dev_lay.setContentsMargins(0, 0, 0, 0)
        dev_lay.setSpacing(6)

        self._device_table = QTableWidget(0, 4)
        self._device_table.setHorizontalHeaderLabels(["设备名", "延迟", "状态", "操作"])
        self._device_table.horizontalHeader().setStretchLastSection(False)
        self._device_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch
        )
        self._device_table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.ResizeToContents
        )
        self._device_table.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeMode.ResizeToContents
        )
        self._device_table.horizontalHeader().setSectionResizeMode(
            3, QHeaderView.ResizeMode.ResizeToContents
        )
        self._device_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._device_table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        self._device_table.setMaximumHeight(150)
        dev_lay.addWidget(self._device_table)

        # Manual connect
        mc = QHBoxLayout()
        self._ip_input = QLineEdit()
        self._ip_input.setPlaceholderText("对方 IP")
        self._ip_input.setFixedWidth(130)
        self._port_input = QLineEdit("5050")
        self._port_input.setFixedWidth(55)
        conn_btn = QPushButton("连接")
        conn_btn.setObjectName("Ghost")
        conn_btn.setFixedHeight(24)
        conn_btn.clicked.connect(self._on_manual_connect)
        mc.addWidget(self._ip_input)
        mc.addWidget(self._port_input)
        mc.addWidget(conn_btn)
        mc.addStretch()
        dev_lay.addLayout(mc)
        root.addWidget(self._dev_body)

        # ── Task list (collapsible) ──
        task_header = QHBoxLayout()
        self._task_toggle = QPushButton("▾")
        self._task_toggle.setObjectName("Ghost")
        self._task_toggle.setFixedSize(20, 20)
        self._task_toggle.clicked.connect(lambda: self._toggle_section(self._task_body, self._task_toggle))
        task_header.addWidget(self._task_toggle)
        self._task_title = QLabel("任务清单 (0)")
        self._task_title.setObjectName("Section")
        task_header.addWidget(self._task_title)
        task_header.addStretch()
        root.addLayout(task_header)

        self._task_body = QWidget()
        task_lay = QVBoxLayout(self._task_body)
        task_lay.setContentsMargins(0, 0, 0, 0)
        task_lay.setSpacing(4)

        self._task_table = QTableWidget(0, 5)
        self._task_table.setHorizontalHeaderLabels(["编号", "状态", "负责人", "更新时间", "操作"])
        self._task_table.horizontalHeader().setStretchLastSection(True)
        self._task_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch
        )
        self._task_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._task_table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        self._task_table.setMaximumHeight(250)
        task_lay.addWidget(self._task_table)
        root.addWidget(self._task_body)

        # ── Activity feed (collapsible) ──
        act_header = QHBoxLayout()
        self._act_toggle = QPushButton("▾")
        self._act_toggle.setObjectName("Ghost")
        self._act_toggle.setFixedSize(20, 20)
        self._act_toggle.clicked.connect(lambda: self._toggle_section(self._act_body, self._act_toggle))
        act_header.addWidget(self._act_toggle)
        self._act_title = QLabel("活动流")
        self._act_title.setObjectName("Section")
        act_header.addWidget(self._act_title)
        act_header.addStretch()
        root.addLayout(act_header)

        self._act_body = QWidget()
        act_lay = QVBoxLayout(self._act_body)
        act_lay.setContentsMargins(0, 0, 0, 0)

        self._activity_feed = ActivityFeedWidget()
        self._activity_feed.setMaximumHeight(200)
        act_lay.addWidget(self._activity_feed)
        root.addWidget(self._act_body)

        # ── Footer: share / group / pairing ──
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setObjectName("Separator")
        root.addWidget(sep)

        # Share address
        share_row = QHBoxLayout()
        self._share_label = QLabel("分享: —")
        self._share_label.setObjectName("MutedSmall")
        share_row.addWidget(self._share_label, 1)
        self._copy_btn = QPushButton("复制")
        self._copy_btn.setObjectName("Ghost")
        self._copy_btn.setFixedHeight(24)
        self._copy_btn.clicked.connect(self._on_copy_addr)
        share_row.addWidget(self._copy_btn)
        root.addLayout(share_row)

        # ── 协作会话区 ──────────────────────────────────────────────────────
        sess_row = QHBoxLayout()
        self._session_status_label = QLabel("未配对团队")
        self._session_status_label.setObjectName("MutedSmall")
        sess_row.addWidget(self._session_status_label, 1)
        self._new_session_btn = QPushButton("新建团队")
        self._new_session_btn.setObjectName("Ghost")
        self._new_session_btn.setFixedHeight(24)
        self._new_session_btn.setToolTip(
            "建立协作团队，同事扫描后直接加入，无需填写 IP。"
            "加入一次永久有效；打开同名项目的队友自动同步数据。"
        )
        self._new_session_btn.clicked.connect(self._on_new_session)
        sess_row.addWidget(self._new_session_btn)
        self._leave_session_btn = QPushButton("退出团队")
        self._leave_session_btn.setObjectName("Ghost")
        self._leave_session_btn.setFixedHeight(24)
        self._leave_session_btn.hide()
        self._leave_session_btn.clicked.connect(self._on_leave_session)
        sess_row.addWidget(self._leave_session_btn)
        root.addLayout(sess_row)

        # Teams discovered on the subnet
        self._discovered_sessions_label = QLabel("发现的团队")
        self._discovered_sessions_label.setObjectName("MutedSmall")
        root.addWidget(self._discovered_sessions_label)
        self._discovered_sessions_widget = QWidget()
        self._discovered_sessions_lay = QVBoxLayout(self._discovered_sessions_widget)
        self._discovered_sessions_lay.setContentsMargins(0, 0, 0, 0)
        self._discovered_sessions_lay.setSpacing(4)
        root.addWidget(self._discovered_sessions_widget)

        # Subnet scan
        scan_row = QHBoxLayout()
        self._scan_btn = QPushButton("扫描局域网")
        self._scan_btn.setObjectName("Ghost")
        self._scan_btn.setFixedHeight(24)
        self._scan_btn.setToolTip("手动扫描子网，发现未通过 mDNS 被发现的设备。")
        self._scan_btn.clicked.connect(self._on_scan_subnet)
        scan_row.addWidget(self._scan_btn)
        # alias for test compatibility
        self._subnet_scan_btn = self._scan_btn
        scan_row.addStretch()
        root.addLayout(scan_row)

        root.addStretch()

        scroll.setWidget(content)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)

    # ── Section toggle ─────────────────────────────────────────────────────

    @staticmethod
    def _toggle_section(body: QWidget, toggle_btn: QPushButton) -> None:
        visible = body.isVisible()
        body.setVisible(not visible)
        toggle_btn.setText("▸" if visible else "▾")

    # ── Signal wiring ──────────────────────────────────────────────────────

    def _connect_signals(self) -> None:
        svc = self._svc
        if svc is None:
            return
        svc.peers_changed.connect(self._refresh_devices)
        svc.peers_changed.connect(self._refresh_health)
        svc.peers_changed.connect(self._refresh_session_ui)
        svc.tasks_changed.connect(self._refresh_tasks)
        svc.tasks_changed.connect(self._refresh_health)
        svc.server_ready.connect(self._on_server_ready)
        svc.conflict_detected.connect(self._on_conflict)
        svc.data_overwritten.connect(self._on_data_overwritten)
        svc.diagnostics_changed.connect(self._refresh_health)
        svc.activity_logged.connect(self._refresh_activity)
        svc.pairing_requested.connect(self._on_pairing_requested)
        svc.pairing_accepted.connect(self._on_pairing_accepted)
        svc.project_bind_suggested.connect(self._on_project_bind_suggested)
        if hasattr(svc, "photo_index_received"):
            svc.photo_index_received.connect(self._on_photo_index_received)

    # ── Refresh slots ──────────────────────────────────────────────────────

    def _refresh_session_ui(self) -> None:
        """Refresh the session status label and discovered sessions list."""
        svc = self._svc
        in_session = svc is not None and bool(svc.group_code)
        name = svc.session_name if svc and svc.session_name else ""
        code = svc.group_code if svc else ""

        if in_session and name:
            self._session_status_label.setText(f"团队：{name}（{code}）")
        elif in_session:
            self._session_status_label.setText(f"团队永久码：{code}")
        else:
            self._session_status_label.setText("未配对团队")

        self._new_session_btn.setVisible(not in_session)
        self._leave_session_btn.setVisible(in_session)

        # Rebuild discovered sessions list
        lay = self._discovered_sessions_lay
        while lay.count():
            item = lay.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()

        if svc is None:
            self._discovered_sessions_label.hide()
            return

        # Group peers by session code
        sessions: dict[str, list] = {}
        for peer in svc.peers():
            if peer.group_code and peer.group_code != svc.group_code:
                label = peer.session_name or peer.hostname or peer.ip
                if peer.group_code not in sessions:
                    sessions[peer.group_code] = {"name": label, "count": 0, "peer": peer}
                sessions[peer.group_code]["count"] += 1

        self._discovered_sessions_label.setVisible(bool(sessions))
        for sess_code, info in sessions.items():
            row = QWidget()
            row_lay = QHBoxLayout(row)
            row_lay.setContentsMargins(0, 0, 0, 0)
            row_lay.setSpacing(6)
            lbl = QLabel(f"{info['name']} ({info['count']} 台)")
            lbl.setObjectName("MutedSmall")
            row_lay.addWidget(lbl, 1)
            join_btn = QPushButton("加入")
            join_btn.setObjectName("Ghost")
            join_btn.setFixedHeight(22)
            _code = sess_code
            _name = info["name"]
            join_btn.clicked.connect(
                lambda _=False, c=_code, n=_name: self._on_join_session(c, n)
            )
            row_lay.addWidget(join_btn)
            lay.addWidget(row)

    def _refresh_health(self) -> None:
        svc = self._svc
        if svc is None:
            self._health_dot.setStyleSheet("color: #9e9e9e;")
            self._health_text.setText("协作服务未启动")
            self._project_label.setText("当前项目：—")
            return
        self._project_label.setText(f"当前项目：{self._local_project_display() or '—'}")
        peers = svc.peers()
        status = build_collab_status(svc, peers)
        health = svc.overall_health()
        colour = _HEALTH_COLOR.get(health, "#9e9e9e")
        if status.state in {"not_started", "missing_group"}:
            colour = "#9e9e9e"
        self._health_dot.setStyleSheet(f"color: {colour}; font-size: 16px;")
        self._health_text.setText(status.plain_status)

    def _refresh_devices(self) -> None:
        svc = self._svc
        if svc is None:
            return
        peers = svc.peers()
        # Order: same-team-same-project first, then teammates on other
        # projects, then unconnected strangers.
        syncing   = [p for p in peers if svc._data_sync_allowed(p)]
        teammates = [p for p in peers if svc._group_matches(p) and not svc._data_sync_allowed(p)]
        strangers = [p for p in peers if not svc._group_matches(p)]
        self._dev_title.setText(f"在线设备 ({len(peers)})")
        all_shown = syncing + teammates + strangers
        self._device_table.setRowCount(len(all_shown))
        for row, peer in enumerate(all_shown):
            self._device_table.setItem(row, 0, _ro_item(peer.hostname or peer.ip))
            lat = f"{peer.latency_ms:.0f} ms" if peer.latency_ms is not None else "—"
            self._device_table.setItem(row, 1, _ro_item(lat))
            if peer in syncing:
                status_item = _ro_item("● 同步中")
                status_item.setForeground(QColor("#2e7d32"))
                status_item.setToolTip("同团队且同项目；编号任务、标本记录和照片同步可用。")
                self._device_table.setItem(row, 2, status_item)
                self._device_table.setCellWidget(row, 3, None)
            elif peer in teammates:
                proj = _project_display(peer.project_name) or "其他项目"
                status_item = _ro_item(f"◐ 仅任务 · {proj}")
                status_item.setForeground(QColor("#f9a825"))
                status_item.setToolTip(
                    "同团队但项目不同；编号任务和进度可见，标本记录和照片/TIF/ZIP不互通。"
                )
                self._device_table.setItem(row, 2, status_item)
                self._device_table.setCellWidget(row, 3, None)
            else:
                status_item = _ro_item("○ 未连接")
                status_item.setForeground(QColor("#9e9e9e"))
                self._device_table.setItem(row, 2, status_item)
                connect_btn = QPushButton("连接")
                connect_btn.setObjectName("Ghost")
                connect_btn.setFixedHeight(22)
                connect_btn.setCursor(Qt.CursorShape.PointingHandCursor)
                _ip, _port = peer.ip, peer.port
                connect_btn.clicked.connect(
                    lambda _=False, ip=_ip, port=_port: self._on_connect_peer(ip, port)
                )
                self._device_table.setCellWidget(row, 3, connect_btn)

    def _refresh_tasks(self) -> None:
        svc = self._svc
        if svc is None:
            return
        all_tasks = sorted(svc.store.list_tasks(), key=lambda t: t.updated_at, reverse=True)
        tasks = self._visible_project_tasks(all_tasks)
        scope = "当前项目任务" if self._local_project_display() else "任务清单"
        self._task_title.setText(f"{scope} ({len(tasks)})")
        if hasattr(self._task_table, "clearSpans"):
            self._task_table.clearSpans()
        self._task_table.setRowCount(len(tasks))
        for row, task in enumerate(tasks):
            self._task_table.setItem(row, 0, _ro_item(task.uid))

            sv = task.status.value if hasattr(task.status, "value") else str(task.status)
            lbl = _STATUS_LABEL.get(sv, sv)
            colour = _STATUS_COLOURS.get(sv, "#ffffff")
            status_item = QTableWidgetItem(lbl)
            status_item.setFlags(status_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            status_item.setBackground(QColor(colour))
            self._task_table.setItem(row, 1, status_item)

            self._task_table.setItem(row, 2, _ro_item(task.assignee or "—"))
            ts = task.updated_at[:19].replace("T", " ") if task.updated_at else "—"
            self._task_table.setItem(row, 3, _ro_item(ts))

            ops = self._build_task_ops(task)
            self._task_table.setCellWidget(row, 4, ops)

        if not tasks:
            self._task_table.setRowCount(1)
            item = QTableWidgetItem("当前项目暂无协作任务。")
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self._task_table.setItem(0, 0, item)
            self._task_table.setSpan(0, 0, 1, 5)

    def _refresh_activity(self) -> None:
        svc = self._svc
        if svc is None:
            return
        entries = svc.activity_log.recent(50)
        self._activity_feed.set_entries(entries)

    def _local_project_display(self) -> str:
        svc_project = getattr(self._svc, "project_name", "") if self._svc else ""
        raw = str(
            svc_project
            or getattr(self.ctx, "current_project_dir", "")
            or getattr(getattr(self.ctx, "settings", None), "last_project_dir", "")
            or ""
        )
        return _project_display(raw)

    def _visible_project_tasks(self, tasks: list["TaskRecord"]) -> list["TaskRecord"]:
        local_project = self._local_project_display()
        if not local_project:
            return tasks
        visible: list["TaskRecord"] = []
        for task in tasks:
            task_project = _project_display(getattr(task, "project_name", ""))
            if not task_project or task_project == local_project:
                visible.append(task)
        return visible

    def _peer_media_sync_label(self, peer: "PeerInfo") -> str:
        svc = self._svc
        if svc is None:
            return "—"
        local_project_id = str(getattr(svc, "project_id", "") or "")
        peer_project_id = str(getattr(peer, "project_id", "") or "")
        if local_project_id and peer_project_id == local_project_id:
            return "可同步"
        return "仅任务"

    def _on_server_ready(self, port: int) -> None:
        svc = self._svc
        if svc:
            self._share_label.setText(f"分享: {svc.local_address()}")

    # ── Task ops (inline action buttons per row) ───────────────────────────

    def _build_task_ops(self, task: "TaskRecord") -> QWidget:
        w = QWidget()
        lay = QHBoxLayout(w)
        lay.setContentsMargins(2, 2, 2, 2)
        lay.setSpacing(3)

        for new_status, label in _STATUS_TRANSITIONS:
            btn = QPushButton(label)
            btn.setFixedHeight(20)
            btn.setObjectName("Ghost")
            btn.setFont(self.font())  # inherit small font
            uid, ns = task.uid, new_status
            btn.clicked.connect(lambda _, u=uid, s=ns: self._on_update_status(u, s))
            lay.addWidget(btn)

        # Assign
        assign_btn = QPushButton("分配")
        assign_btn.setFixedHeight(20)
        assign_btn.setObjectName("Ghost")
        uid = task.uid
        assign_btn.clicked.connect(lambda _, u=uid: self._on_assign(u))
        lay.addWidget(assign_btn)

        # Void
        void_btn = QPushButton("作废")
        void_btn.setFixedHeight(20)
        void_btn.setObjectName("Ghost")
        void_btn.setStyleSheet("color: #e57373;")
        void_btn.clicked.connect(lambda _, u=uid: self._on_void(u))
        lay.addWidget(void_btn)

        lay.addStretch()
        return w

    # ── Slot handlers ──────────────────────────────────────────────────────

    def close_panel(self) -> None:
        """Public API: dismiss the panel (hide + emit ``closed``)."""
        self._on_close()

    def _on_close(self) -> None:
        self.hide()
        self.closed.emit()

    def _on_conflict(self, uid: str) -> None:
        """Show a dialog when a UID creation conflict (409) is detected."""
        if not self.isVisible():
            self._refresh_tasks()
            return
        from app.utils import ui
        ui.warn(
            self,
            "编号冲突",
            f"编号 <b>{uid}</b> 已存在于局域网中的另一台设备。<br><br>"
            "请换一个编号，或与对方确认后由一方将其标记为「作废」。",
        )
        self._refresh_tasks()

    def _on_data_overwritten(self, overwrites: list) -> None:
        """Show a banner in the panel when LWW sync silently overwrites task states."""
        if not overwrites:
            return
        lines = []
        for o in overwrites[:5]:
            old = _STATUS_LABEL.get(o.get("old_status", ""), o.get("old_status", ""))
            new = _STATUS_LABEL.get(o.get("new_status", ""), o.get("new_status", ""))
            lines.append(f"· {o.get('uid', '?')}：{old} → {new}")
        if len(overwrites) > 5:
            lines.append(f"（另有 {len(overwrites) - 5} 条未显示）")
        detail = "\n".join(lines)
        self._show_sync_banner(
            f"同步更新了 {len(overwrites)} 条任务状态（远端版本较新）",
            detail,
        )
        self._refresh_tasks()

    def _show_sync_banner(self, title: str, detail: str = "") -> None:
        """Show a transient non-modal banner at the top of the panel."""
        banner = getattr(self, "_sync_banner", None)
        if banner is None:
            from PyQt6.QtWidgets import QFrame, QVBoxLayout
            banner = QFrame(self)
            banner.setObjectName("CollabSyncBanner")
            banner.setStyleSheet(
                "QFrame#CollabSyncBanner {"
                " background: #fff8e1; border: 1px solid #ffe082;"
                " border-radius: 6px; padding: 6px 10px;"
                "}"
            )
            banner_lay = QVBoxLayout(banner)
            banner_lay.setContentsMargins(6, 4, 6, 4)
            banner_lay.setSpacing(2)
            self._banner_title = QLabel()
            self._banner_title.setObjectName("MutedSmall")
            self._banner_title.setWordWrap(True)
            self._banner_detail = QLabel()
            self._banner_detail.setObjectName("MutedSmall")
            self._banner_detail.setWordWrap(True)
            banner_lay.addWidget(self._banner_title)
            banner_lay.addWidget(self._banner_detail)
            # Insert below header (index 1)
            root_lay = self.layout()
            if root_lay is not None:
                root_lay.insertWidget(1, banner)
            self._sync_banner = banner
            self._banner_hide_timer = QTimer(self)
            self._banner_hide_timer.setSingleShot(True)
            self._banner_hide_timer.timeout.connect(banner.hide)
        self._banner_title.setText(f"⟳ {title}")
        self._banner_detail.setText(detail)
        self._banner_detail.setVisible(bool(detail))
        self._sync_banner.show()
        self._banner_hide_timer.start(8000)

    def _on_update_status(self, uid: str, new_status: str) -> None:
        if self._svc is None:
            return
        ok, msg = self._svc.update_task_status(
            uid, new_status, force=True, broadcast=True,
        )
        if not ok:
            logger.warning("status update failed uid=%s: %s", uid, msg)
            return
        self._refresh_tasks()

    def _on_assign(self, uid: str) -> None:
        if self._svc is None:
            return
        task = self._svc.store.get_task(uid)
        current = task.assignee if task else ""
        name, ok = QInputDialog.getText(
            self, "分配编号", f"分配 {uid} 给谁拍摄？", text=current or ""
        )
        if not ok or not name.strip():
            return
        name = name.strip()
        self._svc.assign_task(uid, name)
        self._refresh_tasks()

    def _on_void(self, uid: str) -> None:
        if self._svc is None:
            return
        reply = QMessageBox.question(
            self, "确认作废",
            f"确定要作废编号 {uid} 吗？\n作废后该编号将被释放，可被任何人重新使用。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        self._svc.release_task(uid)
        self._refresh_tasks()

    def _broadcast_status_update(self, uid: str, new_status: str,
                                  assignee: Optional[str] = None) -> None:
        """Delegate to CollabService.broadcast_status_update (non-blocking)."""
        if self._svc is not None:
            self._svc.broadcast_status_update(uid, new_status, assignee=assignee)

    # ── Footer actions ─────────────────────────────────────────────────────

    def _persist_team_code(self, code: str) -> None:
        """Save the team code so it survives restarts (auto-reconnect).

        A non-empty code also enables collab-on-startup; clearing the code
        disables it.
        """
        try:
            self.ctx.settings.team_code = code
            self.ctx.settings.collab_enabled = bool(code)
            self.ctx.settings.flush_to_disk()
        except Exception:  # noqa: BLE001
            pass

    def _on_new_session(self) -> None:
        svc = self._svc
        if svc is None:
            return
        code = svc.create_session()
        self._persist_team_code(code)
        self._refresh_session_ui()
        self._refresh_devices()
        from app.utils import ui
        ui.info(self, "协作团队已创建",
                f"团队永久码：<b>{code}</b>\n\n"
                "同事打开协作面板后会看到你的团队，点「加入」即可。\n"
                "加入一次永久有效，重启软件自动重连；\n"
                "之后团队成员打开同名项目就会自动同步数据。")

    def _on_leave_session(self) -> None:
        svc = self._svc
        if svc is None:
            return
        svc.leave_session()
        self._persist_team_code("")
        self._refresh_session_ui()
        self._refresh_devices()

    def _on_join_session(self, code: str, name: str) -> None:
        svc = self._svc
        if svc is None:
            return
        svc.join_session(code, session_name=name)
        self._persist_team_code(code)
        self._refresh_session_ui()
        self._refresh_devices()
        from app.utils import ui
        ui.info(self, "已加入协作团队",
                f"成功加入「{name}」，加入一次永久有效。\n"
                "与你打开同名项目的队友会自动同步数据；\n"
                "打开其他项目的队友仅显示在线状态，数据互不影响。")

    def _on_connect_peer(self, ip: str, port: int) -> None:
        """User clicked '连接' on a discovered but unpaired peer."""
        svc = self._svc
        if svc is None:
            return
        ok = svc.request_pairing(ip, port)
        if ok:
            from app.utils import ui
            ui.info(self, "配对请求已发送",
                    f"已向 {ip} 发送协作邀请，等待对方确认。\n对方确认后将自动开始同步。")
        else:
            from app.utils import ui
            ui.warn(self, "连接失败",
                    f"无法连接到 {ip}:{port}。\n请确认对方软件已运行，且在同一网络中。")

    def _on_pairing_requested(self, from_ip: str, from_hostname: str,
                               their_code: str) -> None:
        """Show a confirmation dialog when another device wants to pair."""
        from PyQt6.QtWidgets import QMessageBox
        msg = QMessageBox(self)
        msg.setWindowTitle("收到协作邀请")
        msg.setText(f"<b>{from_hostname}</b>（{from_ip}）\n请求与你开始协作同步。")
        msg.setInformativeText("接受后双方将共享编号认领状态，实时同步。")
        msg.setStandardButtons(
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        msg.button(QMessageBox.StandardButton.Yes).setText("接受")
        msg.button(QMessageBox.StandardButton.No).setText("拒绝")
        msg.setDefaultButton(QMessageBox.StandardButton.Yes)
        if msg.exec() == QMessageBox.StandardButton.Yes:
            svc = self._svc
            if svc is not None:
                peer_port = svc._port or 5050
                # Find the peer's actual port from known peers
                with svc._peers_lock:
                    for p in svc._peers.values():
                        if p.ip == from_ip:
                            peer_port = p.port
                            break
                svc.accept_pairing(from_ip, peer_port, their_code)
                self._refresh_devices()
                self._refresh_health()

    def _on_project_bind_suggested(self, peer_name: str, project_name: str,
                                   sync_code: str) -> None:
        """Teammate has a same-named project with a different identity —
        ask whether to bind them into one synced project."""
        from PyQt6.QtWidgets import QMessageBox
        msg = QMessageBox(self)
        msg.setWindowTitle("发现同名项目")
        msg.setText(
            f"队友 <b>{peer_name}</b> 也在做「{project_name}」。\n"
            "是否把两边绑定为同一个项目并开始同步？"
        )
        msg.setInformativeText(
            "绑定后：编号认领和标本记录实时互通。\n"
            "如果这只是恰好同名的两个不同项目，请选「不绑定」。"
        )
        msg.setStandardButtons(
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        msg.button(QMessageBox.StandardButton.Yes).setText("绑定并同步")
        msg.button(QMessageBox.StandardButton.No).setText("不绑定")
        msg.setDefaultButton(QMessageBox.StandardButton.Yes)
        if msg.exec() != QMessageBox.StandardButton.Yes:
            return
        svc = self._svc
        if svc is None:
            return
        try:
            svc.apply_project_sync_code(sync_code)
        except Exception as exc:  # noqa: BLE001
            from app.utils import ui
            ui.warn(self, "绑定失败", f"无法绑定项目：{exc}")
            return
        # Freshly bound — pull the teammate's existing records right away
        try:
            svc.pull_all_specimens_from_session()
        except Exception:  # noqa: BLE001
            pass
        self._refresh_devices()
        self._refresh_health()

    def _on_pairing_accepted(self, ip: str, hostname: str) -> None:
        """Called when a peer we invited has accepted."""
        from app.utils import ui
        ui.info(self, "协作已建立",
                f"{hostname or ip} 已接受邀请，双方现在开始实时同步。")
        self._refresh_devices()
        self._refresh_health()

    def _on_scan_subnet(self) -> None:
        """Trigger a manual /24 subnet scan for collab peers."""
        svc = self._svc
        if svc is None:
            return
        self._scan_btn.setText("扫描中…")
        self._scan_btn.setEnabled(False)

        def _done(found: int) -> None:
            self._scan_btn.setText("扫描局域网")
            self._scan_btn.setEnabled(True)
            if found:
                self._refresh_devices()
                self._refresh_health()
            else:
                from app.utils import ui
                ui.info(self, "扫描完成", "未在子网中发现其他协作节点。\n请确认对方软件已运行，且在同一 Wi-Fi / 网段。")

        svc.scan_subnet_peers(on_done=_done)

    def _on_copy_addr(self) -> None:
        svc = self._svc
        if svc is None:
            return
        addr = svc.local_address()
        cb = QApplication.clipboard()
        if cb:
            cb.setText(addr)

    def _on_edit_group_code(self) -> None:
        svc = self._svc
        if svc is None:
            return
        current = svc.group_code
        code, ok = QInputDialog.getText(
            self, "修改团队永久码", "团队永久码:", text=current
        )
        if not ok:
            return
        code = code.strip()
        svc.set_group_code(code)
        self.ctx.settings.team_code = code
        try:
            self.ctx.settings.flush_to_disk()
        except Exception:  # noqa: BLE001
            pass
        self._group_code_label.setText(code or "（未设置）")

    def _on_show_pairing(self) -> None:
        svc = self._svc
        if svc is None or not svc.is_running():
            return
        from app.widgets.collab_pairing import (
            encode_pairing,
            generate_group_code,
            make_qr_pixmap,
            qr_available,
        )
        if not svc.group_code:
            new_code = generate_group_code()
            svc.set_group_code(new_code)
            self.ctx.settings.team_code = new_code
            try:
                self.ctx.settings.flush_to_disk()
            except Exception:  # noqa: BLE001
                pass
        addr = svc.local_address()
        parts = addr.split(":")
        ip = parts[0] if parts else ""
        port = int(parts[1]) if len(parts) > 1 else 5050
        code = encode_pairing(ip, port, svc.group_code)
        cb = QApplication.clipboard()
        if cb:
            cb.setText(code)

        dlg = QMessageBox(self)
        dlg.setWindowTitle("配对码")
        dlg.setText(f"配对码已复制:\n\n{code}")
        dlg.setDetailedText("")
        if qr_available():
            qr = make_qr_pixmap(code)
            if qr:
                dlg.setIconPixmap(qr)
        dlg.exec()

    def _on_join_pairing(self) -> None:
        from app.widgets.collab_pairing import decode_pairing
        code, ok = QInputDialog.getText(self, "加入配对码", "粘贴队友的配对码:")
        if not ok or not code.strip():
            return
        try:
            info = decode_pairing(code.strip())
        except ValueError as exc:
            QMessageBox.warning(self, "配对码无效", str(exc))
            return
        svc = self._svc
        if svc:
            if info.group_code:
                current = svc.group_code
                if current and current != info.group_code:
                    reply = QMessageBox.question(
                        self, "确认团队永久码",
                        f"连接码携带的团队永久码为「{info.group_code}」\n"
                        f"你当前的团队永久码为「{current}」\n\n"
                        f"是否将团队永久码改为「{info.group_code}」？",
                        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    )
                    if reply != QMessageBox.StandardButton.Yes:
                        return
                svc.set_group_code(info.group_code)
                self.ctx.settings.team_code = info.group_code
            self.ctx.settings.collab_enabled = True
            try:
                self.ctx.settings.flush_to_disk()
            except Exception:  # noqa: BLE001
                pass
            if not svc.is_running():
                project_dir = self._current_project_dir()
                svc.start(
                    project_name=project_dir,
                    group_code=info.group_code,
                    project_dir=project_dir,
                )
            svc.add_manual_peer(info.ip, info.port)
            self._refresh_all()

    def _on_manual_connect(self) -> None:
        """Manually add a peer from the IP/port inputs (mDNS-failure fallback).

        Mirrors ``_on_join_pairing``'s ``add_manual_peer`` path for the case
        where the user types an address directly instead of pasting a pairing
        code (VLANs / Windows Firewall block mDNS — see CollabService docs).
        """
        ip = self._ip_input.text().strip()
        port_text = self._port_input.text().strip()
        if not ip:
            QMessageBox.warning(self, "缺少 IP", "请填写对方 IP 地址")
            return
        try:
            port = int(port_text) if port_text else 5050
        except ValueError:
            QMessageBox.warning(self, "端口无效", f"端口必须是数字：{port_text}")
            return
        svc = self._svc
        if svc is None:
            QMessageBox.warning(self, "协作未启动", "协作服务尚未启动，无法添加对端")
            return
        svc.add_manual_peer(ip, port)
        QMessageBox.information(self, "已添加对端", f"已手动添加协作对端 {ip}:{port}")

    def _on_diagnose(self) -> None:
        svc = self._svc
        if svc is None:
            return
        svc.run_diagnostics()
        from app.widgets.collab_diagnostics_dialog import CollabDiagnosticsDialog
        dlg = CollabDiagnosticsDialog(svc, self)
        dlg.exec()

    def _on_scan(self) -> None:
        svc = self._svc
        if svc is None:
            return
        self._peer_scan_btn.setEnabled(False)
        self._peer_scan_btn.setText("搜索中…")
        svc.scan_lan()
        QTimer.singleShot(5000, self._re_enable_scan)

    def _re_enable_scan(self) -> None:
        self._peer_scan_btn.setEnabled(True)
        self._peer_scan_btn.setText("搜索队友")

    def _on_setup_wizard(self) -> None:
        from app.widgets.collab_setup_wizard import CollabSetupWizard
        wizard = CollabSetupWizard(self.ctx, self)
        wizard.setup_completed.connect(self._on_wizard_done)
        wizard.exec()

    def _on_wizard_done(self, group_code: str, operator: str) -> None:
        self._svc = getattr(self.ctx, "collab_service", None)
        self._connect_signals()
        self._refresh_all()

    def _current_project_dir(self) -> str:
        return str(
            getattr(self.ctx, "current_project_dir", "")
            or getattr(getattr(self.ctx, "settings", None), "last_project_dir", "")
            or ""
        )

    # ── Initial data load ──────────────────────────────────────────────────

    def refresh(self) -> None:
        """Full refresh — called when the panel is shown."""
        self._svc = getattr(self.ctx, "collab_service", None)
        self._refresh_all()

    def _on_photo_index_received(self, uid: str, kind: str, count: int, device_id: str) -> None:
        self._refresh_tasks()
        self._refresh_activity()

    def _refresh_all(self) -> None:
        svc = self._svc
        if svc is not None:
            svc.ensure_running(self.ctx)
        self._refresh_health()
        if svc is None:
            return
        self._refresh_devices()
        self._refresh_tasks()
        self._refresh_activity()
        self._share_label.setText(f"分享: {svc.local_address()}" if svc.is_running() else "分享: —")
        self._refresh_session_ui()
        status = build_collab_status(svc, svc.peers())
        self._setup_btn.setVisible(status.state in {"not_started", "missing_group"})


# ── Late import for logging ───────────────────────────────────────────────────
import logging
logger = logging.getLogger(__name__)
