"""collab_setup_wizard.py — 2-step pairing wizard for collaboration.

Step 1: Everyone enters the same team pairing code (+ optional connection paste).
Step 2: Wait for teammates on LAN, then finish.

Triggered when ``svc.group_code`` is empty or service is not running.
"""

from __future__ import annotations

import logging
from typing import Optional, TYPE_CHECKING

from PyQt6.QtCore import QTimer, Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QApplication,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.config.icons import icon
from app.utils.ui import center_on
from app.widgets.collab_share_project_picker import CollabShareProjectPicker

if TYPE_CHECKING:
    from app.app_context import AppContext

logger = logging.getLogger(__name__)


class CollabSetupWizard(QDialog):
    """2-step collaboration pairing dialog."""

    setup_completed = pyqtSignal(str, str)  # group_code, operator

    def __init__(self, ctx: "AppContext", parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self.setWindowTitle("协作配对向导")
        self.setMinimumSize(520, 520)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

        self._step = 1
        self._pending_pair_info = None

        self._build_ui()
        center_on(self, parent)

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 16)
        root.setSpacing(14)

        self._step_label = QLabel("步骤 1/2：团队永久码与共享项目")
        self._step_label.setObjectName("CardTitle")
        root.addWidget(self._step_label)

        # ── Step 1 ──
        self._step1 = QWidget()
        s1 = QVBoxLayout(self._step1)
        s1.setContentsMargins(0, 0, 0, 0)
        s1.setSpacing(12)

        intro = QLabel(
            "几个人一起拍时，设置一个团队永久码，并勾选要共享的项目。"
        )
        intro.setObjectName("MutedSmall")
        intro.setWordWrap(True)
        s1.addWidget(intro)

        gc_lay = QHBoxLayout()
        gc_label = QLabel("团队永久码:")
        gc_label.setFixedWidth(88)
        self._group_code_edit = QLineEdit()
        self._group_code_edit.setPlaceholderText("输入队友给你的码，或随机生成")
        try:
            from app.widgets.collab_pairing import generate_group_code
            self._group_code_edit.setText(generate_group_code())
        except Exception:  # noqa: BLE001
            pass
        self._gen_btn = QPushButton("随机生成")
        self._gen_btn.setObjectName("Ghost")
        self._gen_btn.clicked.connect(self._on_generate_code)
        self._copy_team_btn = QPushButton("复制")
        self._copy_team_btn.setObjectName("Ghost")
        self._copy_team_btn.clicked.connect(self._on_copy_team_code)
        gc_lay.addWidget(gc_label)
        gc_lay.addWidget(self._group_code_edit, 1)
        gc_lay.addWidget(self._gen_btn)
        gc_lay.addWidget(self._copy_team_btn)
        s1.addLayout(gc_lay)

        team_hint = QLabel(
            "第一个人点「随机生成」，把码发给其他人；所有人填同一个码。"
            "保存后永久有效，软件重启或更新后会自动重连。需要换团队时重新输入并保存即可。"
        )
        team_hint.setObjectName("MutedSmall")
        team_hint.setWordWrap(True)
        s1.addWidget(team_hint)

        op_lay = QHBoxLayout()
        op_label = QLabel("我的名字:")
        op_label.setFixedWidth(88)
        self._operator_edit = QLineEdit()
        self._operator_edit.setPlaceholderText("例如 小王")
        existing = self._settings_value("user/current_user", "")
        if existing:
            self._operator_edit.setText(existing)
        op_lay.addWidget(op_label)
        op_lay.addWidget(self._operator_edit, 1)
        s1.addLayout(op_lay)

        self._share_picker = CollabShareProjectPicker(self.ctx)
        s1.addWidget(self._share_picker)

        self._adv_toggle = QPushButton("▸ 备用：粘贴连接码（局域网找不到时用）")
        self._adv_toggle.setObjectName("Ghost")
        self._adv_toggle.setCheckable(True)
        self._adv_toggle.toggled.connect(self._on_adv_toggled)
        s1.addWidget(self._adv_toggle)

        self._pairing_frame = QFrame()
        pf_lay = QVBoxLayout(self._pairing_frame)
        pf_lay.setContentsMargins(0, 0, 0, 0)
        pf_lay.setSpacing(6)
        pf_hint = QLabel("粘贴队友发来的连接码，会自动填入团队永久码并尝试直连。")
        pf_hint.setObjectName("MutedSmall")
        pf_hint.setWordWrap(True)
        pf_lay.addWidget(pf_hint)
        pf_row = QHBoxLayout()
        pf_label = QLabel("连接码:")
        pf_label.setFixedWidth(88)
        self._pairing_edit = QLineEdit()
        self._pairing_edit.setPlaceholderText("粘贴连接码")
        pf_row.addWidget(pf_label)
        pf_row.addWidget(self._pairing_edit, 1)
        pf_lay.addLayout(pf_row)
        self._pairing_frame.hide()
        s1.addWidget(self._pairing_frame)

        s1.addStretch()
        root.addWidget(self._step1)

        # ── Step 2 ──
        self._step2 = QWidget()
        s2 = QVBoxLayout(self._step2)
        s2.setContentsMargins(0, 0, 0, 0)
        s2.setSpacing(12)

        wait_hint = QLabel(
            "团队永久码已保存。下面选择要互传照片的项目；编号任务会随团队永久码自动同步。"
        )
        wait_hint.setObjectName("MutedSmall")
        wait_hint.setWordWrap(True)
        s2.addWidget(wait_hint)

        self._share_project_label = QLabel("")
        self._share_project_label.setObjectName("CollabScopeState")
        self._share_project_label.setWordWrap(True)
        s2.addWidget(self._share_project_label)

        proj_btn_row = QHBoxLayout()
        self._pick_project_btn = QPushButton("选择队友项目…")
        self._pick_project_btn.setObjectName("Primary")
        self._pick_project_btn.clicked.connect(self._open_project_picker)
        self._copy_project_btn = QPushButton("复制当前项目码")
        self._copy_project_btn.setObjectName("Ghost")
        self._copy_project_btn.clicked.connect(self._on_copy_project_code)
        proj_btn_row.addWidget(self._pick_project_btn)
        proj_btn_row.addWidget(self._copy_project_btn)
        proj_btn_row.addStretch()
        s2.addLayout(proj_btn_row)
        self._refresh_share_project_hint()

        team_row = QHBoxLayout()
        team_row.addWidget(QLabel("当前团队永久码:"))
        self._team_display = QLineEdit()
        self._team_display.setReadOnly(True)
        team_row.addWidget(self._team_display, 1)
        s2.addLayout(team_row)

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

        pair_lay = QHBoxLayout()
        pair_label = QLabel("连接码:")
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

        s2.addWidget(QLabel("已连接的设备:"))
        self._peer_table = QTableWidget(0, 2)
        self._peer_table.setHorizontalHeaderLabels(["主机名", "地址"])
        self._peer_table.horizontalHeader().setStretchLastSection(True)
        self._peer_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._peer_table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        self._peer_table.setMaximumHeight(160)
        s2.addWidget(self._peer_table)

        scan_lay = QHBoxLayout()
        self._scan_btn = QPushButton("搜索局域网队友")
        self._scan_btn.setObjectName("Outline")
        self._scan_btn.clicked.connect(self._on_scan)
        scan_lay.addWidget(self._scan_btn)
        scan_lay.addStretch()
        s2.addLayout(scan_lay)

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

        footer = QHBoxLayout()
        footer.addStretch()
        self._back_btn = QPushButton("← 上一步")
        self._back_btn.setObjectName("Ghost")
        self._back_btn.clicked.connect(self._go_back)
        self._back_btn.hide()
        footer.addWidget(self._back_btn)

        self._next_btn = QPushButton("开始配对")
        self._next_btn.setObjectName("AccentButton")
        self._next_btn.clicked.connect(self._go_next)
        footer.addWidget(self._next_btn)
        root.addLayout(footer)

    def _refresh_share_project_hint(self) -> None:
        svc = getattr(self.ctx, "collab_service", None)
        if svc is None:
            self._share_project_label.setText("共享项目：等待服务启动…")
            return
        from app.services.collab_project_bind import describe_project_sync_state
        peers = []
        try:
            peers = svc.peers()
        except Exception:  # noqa: BLE001
            pass
        self._share_project_label.setText(describe_project_sync_state(svc, peers))
        has_project = bool(getattr(svc, "project_id", ""))
        self._pick_project_btn.setEnabled(has_project)
        self._copy_project_btn.setEnabled(has_project)

    def _open_project_picker(self) -> None:
        from app.widgets.collab_project_bind_dialog import CollabProjectBindDialog
        dlg = CollabProjectBindDialog(self.ctx, self)
        dlg.applied.connect(self._refresh_share_project_hint)
        dlg.exec()

    def _on_copy_project_code(self) -> None:
        svc = getattr(self.ctx, "collab_service", None)
        if svc is None or not getattr(svc, "project_id", ""):
            return
        from app.services.project_identity_service import project_sync_code
        from pathlib import Path
        name = Path(str(getattr(self.ctx, "current_project_dir", "") or "")).name
        pid = str(getattr(svc, "project_id", ""))
        code = project_sync_code(pid, project_name=name or "project")
        QApplication.clipboard().setText(code)

    def _on_adv_toggled(self, checked: bool) -> None:
        self._pairing_frame.setVisible(checked)
        self._adv_toggle.setText(
            "▾ 备用：粘贴连接码（局域网找不到时用）" if checked
            else "▸ 备用：粘贴连接码（局域网找不到时用）"
        )

    def _on_generate_code(self) -> None:
        try:
            from app.widgets.collab_pairing import generate_group_code
            self._group_code_edit.setText(generate_group_code())
        except Exception:  # noqa: BLE001
            pass

    def _on_copy_team_code(self) -> None:
        text = self._group_code_edit.text().strip()
        if text:
            QApplication.clipboard().setText(text)

    def _go_next(self) -> None:
        if self._step == 1:
            self._pending_pair_info = None
            pairing_text = self._pairing_edit.text().strip() if self._adv_toggle.isChecked() else ""
            if pairing_text:
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
                    self._on_generate_code()
                    code = self._group_code_edit.text().strip()
                if not code:
                    self._group_code_edit.setFocus()
                    return

            self._start_service(code)
            self._share_picker.apply_selection()
            self._connect_pending_pair()
            self._step = 2
            self._step_label.setText("步骤 2/2：选择共享项目")
            self._team_display.setText(code)
            self._step1.hide()
            self._step2.show()
            self._back_btn.show()
            self._next_btn.setText("完成")
            self._refresh_share_fields()
            self._refresh_share_project_hint()

        elif self._step == 2:
            code = self._group_code_edit.text().strip()
            operator = self._operator_edit.text().strip()
            self.setup_completed.emit(code, operator)
            self.accept()

    def _go_back(self) -> None:
        if self._step == 2:
            self._step = 1
            self._step_label.setText("步骤 1/2：团队永久码与共享项目")
            self._step2.hide()
            self._step1.show()
            self._back_btn.hide()
            self._next_btn.setText("开始配对")

    def _start_service(self, group_code: str) -> None:
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

        svc = getattr(self.ctx, "collab_service", None)
        if svc is None:
            return

        svc.set_group_code(group_code)
        project_name = getattr(self.ctx, "current_project_dir", "") or getattr(s, "last_project_dir", "") or ""
        if not svc.is_running():
            svc.start(
                project_name=project_name,
                group_code=group_code,
                project_dir=getattr(self.ctx, "current_project_dir", None) or project_name,
            )

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
        self._refresh_share_project_hint()

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
