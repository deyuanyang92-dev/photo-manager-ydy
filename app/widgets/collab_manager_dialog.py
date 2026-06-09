"""collab_manager_dialog.py — CollabManagerDialog

Full-featured collaboration management dialog, matching `renderCollabManagerModal()`
and `renderCollabShareModal()` from the web prototype.

Panels:
  1. Share address — local IP:port + copy button
  2. Online devices — hostname / address / latency table
  3. Task list     — UID / status / assignee / photo-index
     Per-row actions (available to all): shooting / shot_done / organizing / done
     Admin-only actions: assign, void, resolve-conflict
  4. Manual IP fallback — IP + port + Connect
  5. Debug drawer (toggleable) — local address + peer latency + log

The dialog is opened by WorkbenchView._on_open_collab_manager().
It degrades gracefully when CollabService is None (shows "服务未启动" banner).
"""
from __future__ import annotations

import socket
from typing import Optional

from PyQt6.QtCore import Qt, QTimer, pyqtSlot
from PyQt6.QtGui import QColor, QFont
from PyQt6.QtWidgets import (
    QApplication,
    QDialog,
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
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

if False:  # TYPE_CHECKING
    from app.services.collab_service import CollabService, PeerInfo, TaskRecord, TaskStatus


# ── Status display helpers ────────────────────────────────────────────────────

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

# Transitions the user can trigger from the task table (same as web)
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


def _hex_qcolor(hex_colour: str) -> QColor:
    return QColor(hex_colour)


# ── Dialog ────────────────────────────────────────────────────────────────────

class CollabManagerDialog(QDialog):
    """Collaboration management dialog.

    Parameters
    ----------
    service:
        The running CollabService.  Pass None to show a degraded view.
    parent:
        Parent QWidget for dialog parenting / centering.
    """

    def __init__(self, service: Optional["CollabService"],
                 parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._svc = service
        self.setWindowTitle("协作管理")
        self.resize(860, 620)
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        self.setWindowModality(Qt.WindowModality.WindowModal)

        self._setup_ui()
        self._connect_signals()
        self._refresh()

    # ── UI ────────────────────────────────────────────────────────────────────

    def _setup_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 12)
        root.setSpacing(10)

        # ── Header row ─────────────────────────────────────────────────────
        hdr = QHBoxLayout()
        self._title_label = QLabel("协作编号任务")
        bold = QFont()
        bold.setBold(True)
        bold.setPointSize(14)
        self._title_label.setFont(bold)
        hdr.addWidget(self._title_label)
        hdr.addStretch()

        self._debug_btn = QPushButton("🔧 调试")
        self._debug_btn.setCheckable(True)
        self._debug_btn.setFixedWidth(80)
        self._debug_btn.toggled.connect(self._toggle_debug)
        hdr.addWidget(self._debug_btn)

        close_btn = QPushButton("关闭")
        close_btn.setFixedWidth(60)
        close_btn.clicked.connect(self.accept)
        hdr.addWidget(close_btn)
        root.addLayout(hdr)

        # ── Share address panel ────────────────────────────────────────────
        share_frame = QFrame()
        share_frame.setObjectName("ManualConnectFrame")
        share_frame.setFrameShape(QFrame.Shape.StyledPanel)
        share_lay = QHBoxLayout(share_frame)
        share_lay.setContentsMargins(10, 6, 10, 6)

        share_icon = QLabel("🌐")
        share_lay.addWidget(share_icon)

        self._share_addr = QLabel("局域网地址: —")
        self._share_addr.setObjectName("MutedSmall")
        self._share_addr.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        share_lay.addWidget(self._share_addr)
        share_lay.addStretch()

        copy_btn = QPushButton("复制地址")
        copy_btn.setFixedWidth(72)
        copy_btn.clicked.connect(self._on_copy_address)
        share_lay.addWidget(copy_btn)
        root.addWidget(share_frame)

        # ── Conflict / no-service banner ───────────────────────────────────
        self._banner = QLabel()
        self._banner.setObjectName("ConflictBanner")
        self._banner.setStyleSheet(
            "background: #ff5252; color: white; padding: 6px 12px; "
            "border-radius: 4px; font-weight: bold;"
        )
        self._banner.setWordWrap(True)
        self._banner.hide()
        root.addWidget(self._banner)

        # ── Summary row ────────────────────────────────────────────────────
        self._summary_label = QLabel("身份：— · 任务 0 个 · 离线")
        self._summary_label.setObjectName("Muted")
        root.addWidget(self._summary_label)

        # ── Main splitter: devices | tasks ──────────────────────────────
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )

        # Left — devices + manual connect
        left = QWidget()
        left_lay = QVBoxLayout(left)
        left_lay.setContentsMargins(0, 0, 0, 0)
        left_lay.setSpacing(8)

        dev_title = QLabel("在线设备")
        dev_title.setObjectName("SectionTitle")
        left_lay.addWidget(dev_title)

        self._device_table = QTableWidget(0, 3)
        self._device_table.setHorizontalHeaderLabels(["主机名", "地址", "延迟"])
        self._device_table.horizontalHeader().setStretchLastSection(False)
        self._device_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch
        )
        for col in (1, 2):
            self._device_table.horizontalHeader().setSectionResizeMode(
                col, QHeaderView.ResizeMode.ResizeToContents
            )
        self._device_table.setEditTriggers(
            QTableWidget.EditTrigger.NoEditTriggers
        )
        self._device_table.setSelectionBehavior(
            QTableWidget.SelectionBehavior.SelectRows
        )
        self._device_table.setAlternatingRowColors(True)
        self._device_table.verticalHeader().hide()
        left_lay.addWidget(self._device_table)

        # Manual IP
        manual_frame = QFrame()
        manual_frame.setObjectName("ManualConnectFrame")
        manual_frame.setFrameShape(QFrame.Shape.StyledPanel)
        man_lay = QVBoxLayout(manual_frame)
        man_lay.setContentsMargins(8, 8, 8, 8)
        man_lay.setSpacing(6)
        man_lay.addWidget(QLabel("手动连接（mDNS 跨网段兜底）"))

        ip_row = QHBoxLayout()
        self._ip_input = QLineEdit()
        self._ip_input.setPlaceholderText("IP 如 192.168.1.100")
        ip_row.addWidget(self._ip_input)
        self._port_input = QLineEdit("5050")
        self._port_input.setFixedWidth(60)
        ip_row.addWidget(self._port_input)
        self._conn_btn = QPushButton("连接")
        self._conn_btn.setFixedWidth(56)
        self._conn_btn.clicked.connect(self._on_manual_connect)
        ip_row.addWidget(self._conn_btn)
        man_lay.addLayout(ip_row)
        left_lay.addWidget(manual_frame)

        splitter.addWidget(left)

        # Right — task table
        right = QWidget()
        right_lay = QVBoxLayout(right)
        right_lay.setContentsMargins(0, 0, 0, 0)
        right_lay.setSpacing(8)

        task_hdr = QHBoxLayout()
        task_title = QLabel("任务清单")
        task_title.setObjectName("SectionTitle")
        task_hdr.addWidget(task_title)
        task_hdr.addStretch()
        sync_btn = QPushButton("刷新")
        sync_btn.setFixedWidth(52)
        sync_btn.clicked.connect(self._refresh)
        task_hdr.addWidget(sync_btn)
        right_lay.addLayout(task_hdr)

        # Columns: UID | 状态 | 负责人 | 更新 | 操作
        self._task_table = QTableWidget(0, 5)
        self._task_table.setHorizontalHeaderLabels(
            ["编号", "状态", "负责人", "更新时间", "操作"]
        )
        self._task_table.horizontalHeader().setStretchLastSection(False)
        self._task_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch
        )
        for col in (1, 2, 3, 4):
            self._task_table.horizontalHeader().setSectionResizeMode(
                col, QHeaderView.ResizeMode.ResizeToContents
            )
        self._task_table.setEditTriggers(
            QTableWidget.EditTrigger.NoEditTriggers
        )
        self._task_table.setSelectionBehavior(
            QTableWidget.SelectionBehavior.SelectRows
        )
        self._task_table.setAlternatingRowColors(True)
        self._task_table.verticalHeader().hide()
        right_lay.addWidget(self._task_table)

        splitter.addWidget(right)
        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 3)
        root.addWidget(splitter, stretch=1)

        # ── Debug drawer ────────────────────────────────────────────────
        self._debug_drawer = QFrame()
        self._debug_drawer.setObjectName("DebugDrawer")
        self._debug_drawer.setFrameShape(QFrame.Shape.StyledPanel)
        self._debug_drawer.setFixedHeight(110)
        dbg_lay = QVBoxLayout(self._debug_drawer)
        dbg_lay.setContentsMargins(8, 6, 8, 6)
        dbg_lay.setSpacing(4)

        self._dbg_addr = QLabel("本机地址：—")
        self._dbg_addr.setObjectName("Muted")
        dbg_lay.addWidget(self._dbg_addr)

        self._dbg_log = QLabel("节点：—")
        self._dbg_log.setObjectName("Muted")
        self._dbg_log.setWordWrap(True)
        self._dbg_log.setAlignment(Qt.AlignmentFlag.AlignTop)
        scroll = QScrollArea()
        scroll.setWidget(self._dbg_log)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        dbg_lay.addWidget(scroll)

        self._debug_drawer.hide()
        root.addWidget(self._debug_drawer)

        # No-service placeholder
        if self._svc is None:
            self._show_no_service()

    # ── Signals ───────────────────────────────────────────────────────────────

    def _connect_signals(self) -> None:
        if self._svc is None:
            return
        self._svc.peers_changed.connect(self._refresh_devices)
        self._svc.tasks_changed.connect(self._refresh_tasks)
        self._svc.conflict_detected.connect(self._on_conflict)
        self._svc.server_ready.connect(self._on_server_ready)

    # ── Refresh ───────────────────────────────────────────────────────────────

    def _refresh(self) -> None:
        self._refresh_share_addr()
        self._refresh_devices()
        self._refresh_tasks()
        self._refresh_summary()

    def _refresh_share_addr(self) -> None:
        if self._svc is None:
            self._share_addr.setText("局域网地址: — (服务未启动)")
            return
        addr = self._svc.local_address()
        self._share_addr.setText(f"局域网地址: {addr}")

    @pyqtSlot()
    def _refresh_devices(self) -> None:
        if self._svc is None:
            return
        peers = self._svc.peers()
        self._device_table.setRowCount(len(peers))
        for row, peer in enumerate(peers):
            self._device_table.setItem(
                row, 0, _ro_item(peer.hostname or peer.ip)
            )
            addr = f"{peer.ip}:{peer.port}" + (" ✎" if peer.manual else "")
            self._device_table.setItem(row, 1, _ro_item(addr))
            lat = (
                f"{peer.latency_ms:.0f} ms"
                if peer.latency_ms is not None
                else "—"
            )
            self._device_table.setItem(row, 2, _ro_item(lat))

    @pyqtSlot()
    def _refresh_tasks(self) -> None:
        if self._svc is None:
            return
        tasks = sorted(
            self._svc.store.all(), key=lambda t: t.updated_at, reverse=True
        )
        self._task_table.setRowCount(len(tasks))
        for row, task in enumerate(tasks):
            self._task_table.setItem(row, 0, _ro_item(task.uid))

            sv = task.status.value if hasattr(task.status, "value") else str(task.status)
            lbl = _STATUS_LABEL.get(sv, sv)
            colour = _STATUS_COLOURS.get(sv, "#ffffff")
            status_item = QTableWidgetItem(lbl)
            status_item.setFlags(status_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            status_item.setBackground(_hex_qcolor(colour))
            self._task_table.setItem(row, 1, status_item)

            self._task_table.setItem(row, 2, _ro_item(task.assignee or "—"))
            ts = task.updated_at[:19].replace("T", " ") if task.updated_at else "—"
            self._task_table.setItem(row, 3, _ro_item(ts))

            ops_widget = self._build_task_ops(task)
            self._task_table.setCellWidget(row, 4, ops_widget)

        if not tasks:
            self._task_table.setRowCount(1)
            item = QTableWidgetItem("暂无编号任务。创建编号后会出现在这里。")
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self._task_table.setItem(0, 0, item)
            self._task_table.setSpan(0, 0, 1, 5)

    def _refresh_summary(self) -> None:
        if self._svc is None:
            return
        n_tasks = len(self._svc.store.all())
        n_peers = len(self._svc.peers())
        online_txt = f"在线 {n_peers} 台" if n_peers else "离线/单机"
        self._summary_label.setText(
            f"本机: {socket.gethostname()} · 任务 {n_tasks} 个 · {online_txt}"
        )

    # ── Task ops widget ───────────────────────────────────────────────────────

    def _build_task_ops(self, task: "TaskRecord") -> QWidget:
        """Build inline action button row for one task row."""
        w = QWidget()
        lay = QHBoxLayout(w)
        lay.setContentsMargins(2, 2, 2, 2)
        lay.setSpacing(3)

        # Status transition buttons
        for new_status, label in _STATUS_TRANSITIONS:
            btn = QPushButton(label)
            btn.setFixedHeight(22)
            btn.setObjectName("Ghost")
            uid = task.uid  # capture
            ns = new_status
            btn.clicked.connect(
                lambda _checked=False, u=uid, s=ns: self._on_update_status(u, s)
            )
            lay.addWidget(btn)

        # Assign button
        assign_btn = QPushButton("分配")
        assign_btn.setFixedHeight(22)
        assign_btn.setObjectName("Ghost")
        assign_btn.clicked.connect(
            lambda _checked=False, u=task.uid: self._on_assign(u)
        )
        lay.addWidget(assign_btn)

        sv = task.status.value if hasattr(task.status, "value") else str(task.status)

        # Conflict resolution
        if sv == "conflict":
            resolve_btn = QPushButton("处理冲突")
            resolve_btn.setFixedHeight(22)
            resolve_btn.setObjectName("Ghost")
            resolve_btn.clicked.connect(
                lambda _checked=False, u=task.uid: self._on_resolve_conflict(u)
            )
            lay.addWidget(resolve_btn)

        # Admin-only: void
        void_btn = QPushButton("作废")
        void_btn.setFixedHeight(22)
        void_btn.setObjectName("Ghost")
        void_btn.setStyleSheet("color: #e57373;")
        void_btn.clicked.connect(
            lambda _checked=False, u=task.uid: self._on_void(u)
        )
        lay.addWidget(void_btn)

        lay.addStretch()
        return w

    # ── Slots ─────────────────────────────────────────────────────────────────

    @pyqtSlot(int)
    def _on_server_ready(self, port: int) -> None:
        self._refresh_share_addr()
        if self._svc:
            self._dbg_addr.setText(f"本机地址：{self._svc.local_address()}")

    @pyqtSlot(str)
    def _on_conflict(self, uid: str) -> None:
        msg = f'⚠ 编号冲突："{uid}" 已被其他设备占用，请更改编号。'
        self._banner.setText(msg)
        self._banner.show()
        QTimer.singleShot(8000, self._banner.hide)

    def _on_copy_address(self) -> None:
        if self._svc is None:
            return
        addr = self._svc.local_address()
        clipboard = QApplication.clipboard()
        if clipboard:
            clipboard.setText(addr)
        self._show_banner(f"已复制地址: {addr}", ok=True)

    def _on_manual_connect(self) -> None:
        ip = self._ip_input.text().strip()
        port_str = self._port_input.text().strip()
        if not ip:
            self._show_banner("请输入 IP 地址")
            return
        try:
            port = int(port_str)
        except ValueError:
            self._show_banner("端口号必须为数字")
            return
        if self._svc:
            self._svc.add_manual_peer(ip, port)
        self._ip_input.clear()

    def _on_update_status(self, uid: str, new_status: str) -> None:
        if self._svc is None:
            return
        try:
            from app.services.collab_service import TaskStatus
            self._svc.store.update_status(uid, TaskStatus(new_status))
            # Broadcast to online peers via simple update call
            self._broadcast_status_update(uid, new_status)
            self._refresh_tasks()
        except ValueError as exc:
            self._show_banner(f"状态更新失败：{exc}")

    def _broadcast_status_update(self, uid: str, new_status: str) -> None:
        """POST status update to all online peers (best-effort, silent on error)."""
        if self._svc is None:
            return
        peers = self._svc.peers()
        if not peers:
            return
        try:
            import httpx
        except ImportError:
            return
        payload = {
            "uid": uid,
            "status": new_status,
            "deviceId": socket.gethostname(),
        }
        for peer in peers:
            try:
                httpx.post(
                    f"{peer.base_url}/api/collab/tasks/update-status",
                    json=payload,
                    timeout=3.0,
                )
            except Exception:  # noqa: BLE001
                pass

    def _on_assign(self, uid: str) -> None:
        if self._svc is None:
            return
        task = self._svc.store.get(uid)
        current = task.assignee if task else ""
        name, ok = QInputDialog.getText(
            self, "分配编号", f"分配 {uid} 给谁拍摄？",
            text=current or ""
        )
        if not ok or not name.strip():
            return
        name = name.strip()
        try:
            from app.services.collab_service import TaskStatus
            task = self._svc.store.update_status(uid, TaskStatus.ASSIGNED, assignee=name)
        except ValueError:
            # Already assigned or in non-assignable state — just patch assignee
            t = self._svc.store.get(uid)
            if t:
                t.assignee = name
        self._broadcast_status_update(uid, "assigned")
        self._refresh_tasks()

    def _on_void(self, uid: str) -> None:
        if self._svc is None:
            return
        reply = QMessageBox.question(
            self, "确认作废",
            f"确定要作废编号 {uid} 吗？\n作废后不会删除照片文件，"
            f"该编号将被释放，可被任何人重新使用。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        try:
            # Release = delete locally + broadcast to peers → UID reclaimable.
            self._svc.release_task(uid)
            self._refresh_tasks()
        except Exception as exc:  # noqa: BLE001
            self._show_banner(f"作废失败：{exc}")

    def _on_resolve_conflict(self, uid: str) -> None:
        if self._svc is None:
            return
        status_choices = ["created", "assigned", "void"]
        status_labels  = ["已创建（重新激活）", "已指派", "作废"]
        from PyQt6.QtWidgets import QComboBox
        combo = QComboBox()
        for lbl in status_labels:
            combo.addItem(lbl)

        status_str, ok = QInputDialog.getItem(
            self, "处理冲突",
            f"编号 {uid} 冲突处理后状态：",
            status_labels, 0, False
        )
        if not ok:
            return
        idx = status_labels.index(status_str)
        chosen = status_choices[idx]
        try:
            from app.services.collab_service import TaskStatus
            self._svc.store.update_status(uid, TaskStatus(chosen))
            self._broadcast_status_update(uid, chosen)
            self._refresh_tasks()
        except ValueError as exc:
            self._show_banner(f"冲突处理失败：{exc}")

    def _toggle_debug(self, checked: bool) -> None:
        self._debug_drawer.setVisible(checked)
        if checked and self._svc:
            self._dbg_addr.setText(f"本机地址：{self._svc.local_address()}")
            peers = self._svc.peers()
            lines = [
                f"  {p.hostname or p.ip}:{p.port}"
                + (f"  {p.latency_ms:.0f}ms" if p.latency_ms else "")
                for p in peers
            ]
            self._dbg_log.setText(
                "在线节点：\n" + ("\n".join(lines) if lines else "  （无）")
            )

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _show_banner(self, msg: str, ok: bool = False) -> None:
        colour = "#4caf50" if ok else "#ff5252"
        self._banner.setStyleSheet(
            f"background: {colour}; color: white; padding: 6px 12px; "
            "border-radius: 4px; font-weight: bold;"
        )
        self._banner.setText(msg)
        self._banner.show()
        QTimer.singleShot(4000, self._banner.hide)

    def _show_no_service(self) -> None:
        self._task_table.setRowCount(1)
        item = QTableWidgetItem("CollabService 未初始化 — 服务未启动（缺少 fastapi/uvicorn/zeroconf）")
        item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        self._task_table.setItem(0, 0, item)
        self._task_table.setSpan(0, 0, 1, 5)
        self._summary_label.setText("协作服务未启动")
