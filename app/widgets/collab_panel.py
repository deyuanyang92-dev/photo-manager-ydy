"""collab_panel.py — One-stop collaboration side-panel.

A persistent drawer-style panel that integrates device management, task
list, activity feed, and quick settings — replacing the fragmented
Settings→协作 tab + CollabManagerDialog + CollabView workflow.

Pattern: follows ProjectSettingsDrawer — instant show/hide, positioned
at the right edge of the WorkbenchView.  Width fixed at 380 px.
"""

from __future__ import annotations

import socket
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
from app.widgets.activity_feed_widget import ActivityFeedWidget

if TYPE_CHECKING:
    from app.app_context import AppContext
    from app.services.collab_service import CollabService

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

        # Action buttons
        action_row = QHBoxLayout()
        self._diagnose_btn = QPushButton("诊断")
        self._diagnose_btn.setObjectName("Ghost")
        self._diagnose_btn.setFixedHeight(26)
        self._diagnose_btn.clicked.connect(self._on_diagnose)
        action_row.addWidget(self._diagnose_btn)

        self._scan_btn = QPushButton("搜索队友")
        self._scan_btn.setObjectName("Ghost")
        self._scan_btn.setFixedHeight(26)
        self._scan_btn.clicked.connect(self._on_scan)
        action_row.addWidget(self._scan_btn)

        self._setup_btn = QPushButton("设置向导")
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

        self._device_table = QTableWidget(0, 3)
        self._device_table.setHorizontalHeaderLabels(["主机名", "地址", "延迟"])
        self._device_table.horizontalHeader().setStretchLastSection(True)
        self._device_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch
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

        # Group code
        gc_row = QHBoxLayout()
        gc_label = QLabel("组码:")
        gc_label.setObjectName("MutedSmall")
        gc_label.setFixedWidth(40)
        self._group_code_label = QLabel("—")
        self._group_code_label.setObjectName("MutedSmall")
        gc_row.addWidget(gc_label)
        gc_row.addWidget(self._group_code_label, 1)
        self._edit_gc_btn = QPushButton("修改")
        self._edit_gc_btn.setObjectName("Ghost")
        self._edit_gc_btn.setFixedHeight(24)
        self._edit_gc_btn.clicked.connect(self._on_edit_group_code)
        gc_row.addWidget(self._edit_gc_btn)
        root.addLayout(gc_row)

        # Pairing
        pair_row = QHBoxLayout()
        self._pairing_show_btn = QPushButton("显示配对码")
        self._pairing_show_btn.setObjectName("Ghost")
        self._pairing_show_btn.setFixedHeight(24)
        self._pairing_show_btn.clicked.connect(self._on_show_pairing)
        pair_row.addWidget(self._pairing_show_btn)
        self._pairing_join_btn = QPushButton("加入配对码")
        self._pairing_join_btn.setObjectName("Ghost")
        self._pairing_join_btn.setFixedHeight(24)
        self._pairing_join_btn.clicked.connect(self._on_join_pairing)
        pair_row.addWidget(self._pairing_join_btn)
        pair_row.addStretch()
        root.addLayout(pair_row)

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
        svc.tasks_changed.connect(self._refresh_tasks)
        svc.tasks_changed.connect(self._refresh_health)
        svc.server_ready.connect(self._on_server_ready)
        svc.conflict_detected.connect(self._on_conflict)
        svc.diagnostics_changed.connect(self._refresh_health)
        svc.activity_logged.connect(self._refresh_activity)

    # ── Refresh slots ──────────────────────────────────────────────────────

    def _refresh_health(self) -> None:
        svc = self._svc
        if svc is None:
            self._health_dot.setStyleSheet("color: #9e9e9e;")
            self._health_text.setText("协作服务未启动")
            return
        health = svc.overall_health()
        colour = _HEALTH_COLOR.get(health, "#9e9e9e")
        self._health_dot.setStyleSheet(f"color: {colour}; font-size: 16px;")
        n_peers = len(svc.peers())
        if n_peers:
            self._health_text.setText(f"协作正常 · {n_peers} 台在线")
        elif svc.is_running():
            self._health_text.setText("已启动 · 等待队友")
        else:
            self._health_text.setText("未启动")

    def _refresh_devices(self) -> None:
        svc = self._svc
        if svc is None:
            return
        peers = svc.peers()
        self._dev_title.setText(f"在线设备 ({len(peers)})")
        self._device_table.setRowCount(len(peers))
        for row, peer in enumerate(peers):
            self._device_table.setItem(row, 0, _ro_item(peer.hostname or peer.ip))
            addr = f"{peer.ip}:{peer.port}" + (" ✎" if peer.manual else "")
            self._device_table.setItem(row, 1, _ro_item(addr))
            lat = f"{peer.latency_ms:.0f} ms" if peer.latency_ms is not None else "—"
            self._device_table.setItem(row, 2, _ro_item(lat))

    def _refresh_tasks(self) -> None:
        svc = self._svc
        if svc is None:
            return
        tasks = sorted(svc.store.all(), key=lambda t: t.updated_at, reverse=True)
        self._task_title.setText(f"任务清单 ({len(tasks)})")
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
            item = QTableWidgetItem("暂无编号任务。创建编号后会出现在这里。")
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self._task_table.setItem(0, 0, item)
            self._task_table.setSpan(0, 0, 1, 5)

    def _refresh_activity(self) -> None:
        svc = self._svc
        if svc is None:
            return
        entries = svc.activity_log.recent(50)
        self._activity_feed.set_entries(entries)

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

    def _on_close(self) -> None:
        self.hide()
        self.closed.emit()

    def _on_conflict(self, uid: str) -> None:
        # Refresh will show the conflict status in the table
        self._refresh_tasks()

    def _on_update_status(self, uid: str, new_status: str) -> None:
        if self._svc is None:
            return
        try:
            from app.services.collab_service import TaskStatus
            self._svc.store.update_status(uid, TaskStatus(new_status))
            self._broadcast_status_update(uid, new_status)
            self._svc._log_activity(
                "status_changed", uid,
                detail=f"编号 {uid} 状态变为 {_STATUS_LABEL.get(new_status, new_status)}",
            )
            self._svc.specimen_status_changed.emit(uid)
            self._refresh_tasks()
        except ValueError as exc:
            logger.warning("status update failed: %s", exc)

    def _on_assign(self, uid: str) -> None:
        if self._svc is None:
            return
        task = self._svc.store.get(uid)
        current = task.assignee if task else ""
        name, ok = QInputDialog.getText(
            self, "分配编号", f"分配 {uid} 给谁拍摄？", text=current or ""
        )
        if not ok or not name.strip():
            return
        name = name.strip()
        try:
            from app.services.collab_service import TaskStatus
            self._svc.store.update_status(uid, TaskStatus.ASSIGNED, assignee=name)
        except ValueError:
            t = self._svc.store.get(uid)
            if t:
                t.assignee = name
        self._broadcast_status_update(uid, "assigned")
        self._svc._log_activity("status_changed", uid, detail=f"编号 {uid} 分配给 {name}")
        self._svc.specimen_status_changed.emit(uid)
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

    def _broadcast_status_update(self, uid: str, new_status: str) -> None:
        """POST status update to all online peers (best-effort)."""
        if self._svc is None:
            return
        peers = self._svc.peers()
        if not peers:
            return
        try:
            import httpx
        except ImportError:
            return
        payload = {"uid": uid, "status": new_status, "deviceId": socket.gethostname()}
        for peer in peers:
            try:
                httpx.post(
                    f"{peer.base_url}/api/collab/tasks/update-status",
                    json=payload,
                    timeout=3.0,
                )
            except Exception:  # noqa: BLE001
                pass

    # ── Footer actions ─────────────────────────────────────────────────────

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
            self, "修改协作组码", "协作组码:", text=current
        )
        if not ok:
            return
        code = code.strip()
        svc.set_group_code(code)
        self.ctx.settings.setValue("collab/team_code", code)
        self._group_code_label.setText(code or "（未设置）")

    def _on_show_pairing(self) -> None:
        svc = self._svc
        if svc is None or not svc.is_running():
            return
        from app.widgets.collab_pairing import encode_pairing, make_qr_pixmap, qr_available
        addr = svc.local_address()
        parts = addr.split(":")
        ip = parts[0] if parts else ""
        port = int(parts[1]) if len(parts) > 1 else 5050
        code = encode_pairing(ip, port, svc.group_code)

        dlg = QMessageBox(self)
        dlg.setWindowTitle("配对码")
        dlg.setText(f"配对码 (发送给队友):\n\n{code}")
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
            svc.add_manual_peer(info.ip, info.port)
            if info.group_code:
                svc.set_group_code(info.group_code)
                self.ctx.settings.setValue("collab/team_code", info.group_code)

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
        self._scan_btn.setEnabled(False)
        self._scan_btn.setText("搜索中…")
        svc.scan_lan()
        QTimer.singleShot(5000, self._re_enable_scan)

    def _re_enable_scan(self) -> None:
        self._scan_btn.setEnabled(True)
        self._scan_btn.setText("搜索队友")

    def _on_setup_wizard(self) -> None:
        from app.widgets.collab_setup_wizard import CollabSetupWizard
        wizard = CollabSetupWizard(self.ctx, self)
        wizard.setup_completed.connect(self._on_wizard_done)
        wizard.exec()

    def _on_wizard_done(self, group_code: str, operator: str) -> None:
        self._svc = getattr(self.ctx, "collab_service", None)
        self._connect_signals()
        self._refresh_all()

    # ── Initial data load ──────────────────────────────────────────────────

    def refresh(self) -> None:
        """Full refresh — called when the panel is shown."""
        self._svc = getattr(self.ctx, "collab_service", None)
        self._refresh_all()

    def _refresh_all(self) -> None:
        svc = self._svc
        self._refresh_health()
        if svc is None:
            return
        self._refresh_devices()
        self._refresh_tasks()
        self._refresh_activity()
        self._share_label.setText(f"分享: {svc.local_address()}" if svc.is_running() else "分享: —")
        self._group_code_label.setText(svc.group_code or "（未设置）")
        # Show setup wizard button if not configured
        self._setup_btn.setVisible(not svc.group_code or not svc.is_running())


# ── Late import for logging ───────────────────────────────────────────────────
import logging
logger = logging.getLogger(__name__)
