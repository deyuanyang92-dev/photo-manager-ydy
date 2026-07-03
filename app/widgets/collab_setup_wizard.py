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
    QApplication,
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
from app.utils.ui import center_on

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
        self._pending_pair_info = None

        self._build_ui()
        center_on(self, parent)

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
        self._rb_create = QRadioButton("第一台电脑：创建协作")
        self._rb_create.setChecked(True)
        self._rb_join = QRadioButton("其他电脑：粘贴配对码加入")
        self._rb_group.addButton(self._rb_create, 0)
        self._rb_group.addButton(self._rb_join, 1)
        self._rb_group.idToggled.connect(self._on_mode_toggled)
        rb_lay.addWidget(self._rb_create)
        rb_lay.addWidget(self._rb_join)
        rb_lay.addStretch()
        s1.addLayout(rb_lay)

        # Group code
        self._group_code_frame = QWidget()
        gc_lay = QHBoxLayout(self._group_code_frame)
        gc_lay.setContentsMargins(0, 0, 0, 0)
        gc_label = QLabel("协作组码:")
        gc_label.setFixedWidth(80)
        self._group_code_edit = QLineEdit()
        self._group_code_edit.setPlaceholderText("自动生成")
        try:
            from app.widgets.collab_pairing import generate_group_code
            self._group_code_edit.setText(generate_group_code())
        except Exception:  # noqa: BLE001
            pass
        gc_lay.addWidget(gc_label)
        gc_lay.addWidget(self._group_code_edit, 1)
        s1.addWidget(self._group_code_frame)

        # Operator name
        op_lay = QHBoxLayout()
        op_label = QLabel("我的名字:")
        op_label.setFixedWidth(80)
        self._operator_edit = QLineEdit()
        self._operator_edit.setPlaceholderText("例如 小王")
        # Pre-fill from settings
        existing = self._settings_value("user/current_user", "")
        if existing:
            self._operator_edit.setText(existing)
        op_lay.addWidget(op_label)
        op_lay.addWidget(self._operator_edit, 1)
        s1.addLayout(op_lay)

        # Pairing code (join mode only)
        self._pairing_frame = QFrame()
        pf_lay = QHBoxLayout(self._pairing_frame)
        pf_lay.setContentsMargins(0, 0, 0, 0)
        pf_label = QLabel("配对码:")
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

        # Pairing display
        pair_lay = QHBoxLayout()
        pair_label = QLabel("配对码:")
        pair_label.setFixedWidth(80)
        self._pairing_display = QLineEdit()
        self._pairing_display.setReadOnly(True)
        self._pairing_display.setText("等待启动…")
        self._copy_pairing_btn = QPushButton("复制")
        self._copy_pairing_btn.setObjectName("Ghost")
        self._copy_pairing_btn.clicked.connect(self._on_copy_pairing)
        pair_lay.addWidget(pair_label)
        pair_lay.addWidget(self._pairing_display, 1)
        pair_lay.addWidget(self._copy_pairing_btn)
        s2.addLayout(pair_lay)

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
        self._update_mode_ui()

    # ── Step navigation ────────────────────────────────────────────────────

    def _on_mode_toggled(self, btn_id: int, checked: bool) -> None:
        if not checked:
            return
        self._is_create = btn_id == 0
        self._update_mode_ui()

    def _update_mode_ui(self) -> None:
        self._group_code_frame.setVisible(self._is_create)
        self._pairing_frame.setVisible(not self._is_create)
        self._next_btn.setText("开启协作" if self._is_create else "加入协作")

    def _go_next(self) -> None:
        if self._step == 1:
            self._pending_pair_info = None
            if not self._is_create:
                pairing_text = self._pairing_edit.text().strip()
                if not pairing_text:
                    self._pairing_edit.setFocus()
                    return
                try:
                    from app.widgets.collab_pairing import decode_pairing
                    self._pending_pair_info = decode_pairing(pairing_text)
                except ValueError:
                    self._pairing_edit.setFocus()
                    return
                code = self._pending_pair_info.group_code
                self._group_code_edit.setText(code)
            else:
                code = self._group_code_edit.text().strip()
                if not code:
                    try:
                        from app.widgets.collab_pairing import generate_group_code
                        code = generate_group_code()
                        self._group_code_edit.setText(code)
                    except Exception:  # noqa: BLE001
                        pass
                if not code:
                    self._group_code_edit.setFocus()
                    return

            # Start the service
            self._start_service(code)
            self._connect_pending_pair()
            self._step = 2
            self._step_label.setText("步骤 2/2: 连接队友")
            self._step1.hide()
            self._step2.show()
            self._back_btn.show()
            self._next_btn.setText("完成")
            self._refresh_share_fields()

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
            self._update_mode_ui()

    # ── Service helpers ────────────────────────────────────────────────────

    def _start_service(self, group_code: str) -> None:
        """Configure and start the collab service."""
        svc = getattr(self.ctx, "collab_service", None)
        if svc is None:
            return

        # Persist settings
        s = self.ctx.settings
        self._set_setting("collab/enabled", True)
        self._set_setting("collab/team_code", group_code)
        try:
            s.collab_enabled = True
            s.team_code = group_code
            s.flush_to_disk()
        except Exception:  # noqa: BLE001
            pass

        operator = self._operator_edit.text().strip()
        if operator:
            self._set_setting("user/current_user", operator)

        # Configure service
        svc.set_group_code(group_code)
        project_name = getattr(self.ctx, "current_project_dir", "") or getattr(s, "last_project_dir", "") or ""
        if not svc.is_running():
            svc.start(
                project_name=project_name,
                group_code=group_code,
                project_dir=getattr(self.ctx, "current_project_dir", None) or project_name,
            )

        # Wire signals for step 2 updates
        svc.server_ready.connect(self._on_server_ready)
        svc.peers_changed.connect(self._refresh_peers)

    def _connect_pending_pair(self) -> None:
        svc = getattr(self.ctx, "collab_service", None)
        info = self._pending_pair_info
        if svc is None or info is None:
            return
        svc.add_manual_peer(info.ip, info.port)

    def _on_server_ready(self, port: int) -> None:
        self._refresh_share_fields()

    def _refresh_share_fields(self) -> None:
        svc = getattr(self.ctx, "collab_service", None)
        if not svc:
            return
        addr = svc.local_address()
        self._addr_display.setText(addr)
        try:
            from app.widgets.collab_pairing import encode_pairing
            ip, port_s = addr.rsplit(":", 1)
            code = encode_pairing(ip, int(port_s), svc.group_code)
            self._pairing_display.setText(code)
            QApplication.clipboard().setText(code)
        except Exception:  # noqa: BLE001
            self._pairing_display.setText("暂时无法生成")

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
        QApplication.clipboard().setText(self._addr_display.text())

    def _on_copy_pairing(self) -> None:
        text = self._pairing_display.text().strip()
        if text and text not in {"等待启动…", "暂时无法生成"}:
            QApplication.clipboard().setText(text)

    def _settings_value(self, key: str, default: str = "") -> str:
        settings = getattr(self.ctx, "settings", None)
        qs = getattr(settings, "_qs", settings)
        try:
            return str(qs.value(key, default, type=str))
        except Exception:  # noqa: BLE001
            return default

    def _set_setting(self, key: str, value: object) -> None:
        settings = getattr(self.ctx, "settings", None)
        qs = getattr(settings, "_qs", settings)
        try:
            qs.setValue(key, value)
        except Exception:  # noqa: BLE001
            pass

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
