"""collab_setup_wizard.py — 2-step quick-start wizard for collaboration.

Step 1: Create or join a collaboration group (enter group code + operator name).
Step 2: Wait for teammates to connect, then start collaborating.

Triggered by CollabPanel when ``svc.group_code`` is empty or service is not running.
"""

from __future__ import annotations

import logging
from typing import Optional, TYPE_CHECKING

from PyQt6.QtCore import QTimer, Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QButtonGroup,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QRadioButton,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.config.icons import icon
from app.utils.ui import centre_on_screen

if TYPE_CHECKING:
    from app.app_context import AppContext

logger = logging.getLogger(__name__)


class CollabSetupWizard(QDialog):
    """2-step collaboration setup dialog."""

    # Emitted when the user completes the wizard (group_code, operator_name).
    setup_completed = pyqtSignal(str, str)  # group_code, operator

    def __init__(self, ctx: "AppContext", parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self.setWindowTitle("协作设置向导")
        self.setMinimumSize(480, 420)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

        self._step = 1
        self._is_create = True  # True = create, False = join

        self._build_ui()
        centre_on_screen(self)

    # ── UI construction ────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 16)
        root.setSpacing(16)

        # Step indicator
        self._step_label = QLabel("步骤 1/2: 创建或加入协作组")
        self._step_label.setObjectName("CardTitle")
        root.addWidget(self._step_label)

        # ── Step 1 content ──
        self._step1 = QWidget()
        s1 = QVBoxLayout(self._step1)
        s1.setContentsMargins(0, 0, 0, 0)
        s1.setSpacing(12)

        # Radio buttons
        rb_lay = QHBoxLayout()
        self._rb_group = QButtonGroup(self)
        self._rb_create = QRadioButton("创建新协作组")
        self._rb_create.setChecked(True)
        self._rb_join = QRadioButton("加入已有协作组")
        self._rb_group.addButton(self._rb_create, 0)
        self._rb_group.addButton(self._rb_join, 1)
        self._rb_group.idToggled.connect(self._on_mode_toggled)
        rb_lay.addWidget(self._rb_create)
        rb_lay.addWidget(self._rb_join)
        rb_lay.addStretch()
        s1.addLayout(rb_lay)

        # Group code
        gc_lay = QHBoxLayout()
        gc_label = QLabel("协作组码:")
        gc_label.setFixedWidth(80)
        self._group_code_edit = QLineEdit()
        self._group_code_edit.setPlaceholderText("例如 SMW-2026")
        gc_lay.addWidget(gc_label)
        gc_lay.addWidget(self._group_code_edit, 1)
        s1.addLayout(gc_lay)

        # Operator name
        op_lay = QHBoxLayout()
        op_label = QLabel("我的名字:")
        op_label.setFixedWidth(80)
        self._operator_edit = QLineEdit()
        self._operator_edit.setPlaceholderText("例如 小王")
        # Pre-fill from settings
        existing = self.ctx.settings.value("user/current_user", "", type=str)
        if existing:
            self._operator_edit.setText(existing)
        op_lay.addWidget(op_label)
        op_lay.addWidget(self._operator_edit, 1)
        s1.addLayout(op_lay)

        # Pairing code (join mode only)
        self._pairing_frame = QFrame()
        pf_lay = QHBoxLayout(self._pairing_frame)
        pf_lay.setContentsMargins(0, 0, 0, 0)
        pf_label = QLabel("或粘贴配对码:")
        pf_label.setFixedWidth(80)
        self._pairing_edit = QLineEdit()
        self._pairing_edit.setPlaceholderText("粘贴队友的配对码")
        pf_lay.addWidget(pf_label)
        pf_lay.addWidget(self._pairing_edit, 1)
        self._pairing_frame.hide()
        s1.addWidget(self._pairing_frame)

        s1.addStretch()
        root.addWidget(self._step1)

        # ── Step 2 content (hidden initially) ──
        self._step2 = QWidget()
        s2 = QVBoxLayout(self._step2)
        s2.setContentsMargins(0, 0, 0, 0)
        s2.setSpacing(12)

        # Address display
        addr_lay = QHBoxLayout()
        addr_label = QLabel("本机地址:")
        addr_label.setFixedWidth(80)
        self._addr_display = QLineEdit()
        self._addr_display.setReadOnly(True)
        self._addr_display.setText("等待启动…")
        self._copy_btn = QPushButton("复制")
        self._copy_btn.setObjectName("Ghost")
        self._copy_btn.clicked.connect(self._on_copy_addr)
        addr_lay.addWidget(addr_label)
        addr_lay.addWidget(self._addr_display, 1)
        addr_lay.addWidget(self._copy_btn)
        s2.addLayout(addr_lay)

        # Peer table
        s2.addWidget(QLabel("已连接的设备:"))
        self._peer_table = QTableWidget(0, 2)
        self._peer_table.setHorizontalHeaderLabels(["主机名", "地址"])
        self._peer_table.horizontalHeader().setStretchLastSection(True)
        self._peer_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._peer_table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        self._peer_table.setMaximumHeight(160)
        s2.addWidget(self._peer_table)

        # Scan button
        scan_lay = QHBoxLayout()
        self._scan_btn = QPushButton("搜索局域网队友")
        self._scan_btn.setObjectName("Outline")
        self._scan_btn.clicked.connect(self._on_scan)
        scan_lay.addWidget(self._scan_btn)
        scan_lay.addStretch()
        s2.addLayout(scan_lay)

        # Manual connect
        mc_lay = QHBoxLayout()
        mc_label = QLabel("手动连接:")
        self._manual_ip = QLineEdit()
        self._manual_ip.setPlaceholderText("对方 IP")
        self._manual_ip.setFixedWidth(140)
        self._manual_port = QLineEdit("5050")
        self._manual_port.setFixedWidth(60)
        self._connect_btn = QPushButton("连接")
        self._connect_btn.setObjectName("Ghost")
        self._connect_btn.clicked.connect(self._on_manual_connect)
        mc_lay.addWidget(mc_label)
        mc_lay.addWidget(self._manual_ip)
        mc_lay.addWidget(self._manual_port)
        mc_lay.addWidget(self._connect_btn)
        mc_lay.addStretch()
        s2.addLayout(mc_lay)

        s2.addStretch()
        self._step2.hide()
        root.addWidget(self._step2)

        # ── Footer buttons ──
        footer = QHBoxLayout()
        footer.addStretch()
        self._back_btn = QPushButton("← 上一步")
        self._back_btn.setObjectName("Ghost")
        self._back_btn.clicked.connect(self._go_back)
        self._back_btn.hide()
        footer.addWidget(self._back_btn)

        self._next_btn = QPushButton("下一步 →")
        self._next_btn.setObjectName("AccentButton")
        self._next_btn.clicked.connect(self._go_next)
        footer.addWidget(self._next_btn)
        root.addLayout(footer)

    # ── Step navigation ────────────────────────────────────────────────────

    def _on_mode_toggled(self, btn_id: int, checked: bool) -> None:
        if not checked:
            return
        self._is_create = btn_id == 0
        self._pairing_frame.setVisible(not self._is_create)

    def _go_next(self) -> None:
        if self._step == 1:
            # Validate inputs
            code = self._group_code_edit.text().strip()
            if not code:
                self._group_code_edit.setFocus()
                return

            # Handle pairing code for join mode
            if not self._is_create:
                pairing_text = self._pairing_edit.text().strip()
                if pairing_text:
                    try:
                        from app.widgets.collab_pairing import decode_pairing
                        info = decode_pairing(pairing_text)
                        code = info.group_code
                        self._group_code_edit.setText(code)
                        # Auto-connect to the peer
                        svc = getattr(self.ctx, "collab_service", None)
                        if svc:
                            svc.add_manual_peer(info.ip, info.port)
                    except ValueError:
                        pass  # Invalid pairing code, ignore

            # Start the service
            self._start_service(code)
            self._step = 2
            self._step_label.setText("步骤 2/2: 等待队友连接")
            self._step1.hide()
            self._step2.show()
            self._back_btn.show()
            self._next_btn.setText("完成，开始协作")

        elif self._step == 2:
            # Finish
            code = self._group_code_edit.text().strip()
            operator = self._operator_edit.text().strip()
            self.setup_completed.emit(code, operator)
            self.accept()

    def _go_back(self) -> None:
        if self._step == 2:
            self._step = 1
            self._step_label.setText("步骤 1/2: 创建或加入协作组")
            self._step2.hide()
            self._step1.show()
            self._back_btn.hide()
            self._next_btn.setText("下一步 →")

    # ── Service helpers ────────────────────────────────────────────────────

    def _start_service(self, group_code: str) -> None:
        """Configure and start the collab service."""
        svc = getattr(self.ctx, "collab_service", None)
        if svc is None:
            return

        # Persist settings
        s = self.ctx.settings
        s.setValue("collab/enabled", True)
        s.setValue("collab/team_code", group_code)

        operator = self._operator_edit.text().strip()
        if operator:
            s.setValue("user/current_user", operator)

        # Configure service
        svc.set_group_code(group_code)
        project_name = self.ctx.settings.value("last_project_dir", "", type=str)
        if not svc.is_running():
            svc.start(project_name=project_name, group_code=group_code)

        # Wire signals for step 2 updates
        svc.server_ready.connect(self._on_server_ready)
        svc.peers_changed.connect(self._refresh_peers)

    def _on_server_ready(self, port: int) -> None:
        svc = getattr(self.ctx, "collab_service", None)
        if svc:
            self._addr_display.setText(svc.local_address())

    def _refresh_peers(self) -> None:
        svc = getattr(self.ctx, "collab_service", None)
        if svc is None:
            return
        peers = svc.peers()
        self._peer_table.setRowCount(len(peers))
        for i, p in enumerate(peers):
            self._peer_table.setItem(i, 0, QTableWidgetItem(p.hostname or p.ip))
            self._peer_table.setItem(i, 1, QTableWidgetItem(f"{p.ip}:{p.port}"))

    # ── Button handlers ────────────────────────────────────────────────────

    def _on_copy_addr(self) -> None:
        from PyQt6.QtWidgets import QApplication
        QApplication.clipboard().setText(self._addr_display.text())

    def _on_scan(self) -> None:
        svc = getattr(self.ctx, "collab_service", None)
        if svc:
            self._scan_btn.setEnabled(False)
            self._scan_btn.setText("搜索中…")
            svc.scan_lan()
            # Re-enable after a delay (scan is async)
            QTimer.singleShot(5000, self._re_enable_scan)

    def _re_enable_scan(self) -> None:
        self._scan_btn.setEnabled(True)
        self._scan_btn.setText("搜索局域网队友")

    def _on_manual_connect(self) -> None:
        ip = self._manual_ip.text().strip()
        port_text = self._manual_port.text().strip()
        if not ip:
            return
        try:
            port = int(port_text)
        except ValueError:
            port = 5050
        svc = getattr(self.ctx, "collab_service", None)
        if svc:
            svc.add_manual_peer(ip, port)
