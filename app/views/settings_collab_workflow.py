"""Collaboration settings actions and status refresh."""
from __future__ import annotations

import os
import platform

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QKeySequence
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QKeySequenceEdit,
    QLabel,
    QLineEdit,
    QListWidget,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from app.config.i18n import tr
from app.config.version import APP_VERSION
from app.services.collab_status import build_collab_status
from app.utils import diagnostics
from app.views import settings_view_support as _sv


class SettingsCollabWorkflowMixin:
    """Collaboration settings actions and status refresh."""

    # ── Collaboration ─────────────────────────────────────────────────────

    def _save_collab(self) -> None:
        """Persist collab enable + team code, and push code to a live service."""
        self.ctx.settings.collab_enabled = self._collab_enabled_chk.isChecked()
        code = self._collab_team_code_edit.text().strip()
        self.ctx.settings.team_code = code
        self.ctx.settings.flush_to_disk()
        svc = getattr(self.ctx, "collab_service", None)
        if svc is not None:
            try:
                svc.set_group_code(code)
            except Exception:  # noqa: BLE001
                pass
        self._refresh_collab_health()

    def _on_collab_enabled_toggled(self, on: bool) -> None:
        """Persist the flag and start/stop the live service immediately."""
        self._save_collab()
        svc = self.ctx.ensure_collab_service() if on else getattr(self.ctx, "collab_service", None)
        if svc is None:
            return
        try:
            if on and not svc.is_running():
                svc.start(
                    project_name=self.ctx.current_project_dir or "",
                    group_code=self._collab_team_code_edit.text().strip(),
                    project_dir=self.ctx.current_project_dir or "",
                )
            elif not on and svc.is_running():
                svc.stop()
        except Exception:  # noqa: BLE001
            pass
        self._refresh_collab_addr()
        self._refresh_collab_members()
        self._refresh_collab_health(recompute=True)

    def _copy_collab_addr(self) -> None:
        from PyQt6.QtWidgets import QApplication
        text = self._collab_addr_edit.text().strip()
        if text and text != "—":
            QApplication.clipboard().setText(text)

    def _on_add_manual_peer(self) -> None:
        svc = getattr(self.ctx, "collab_service", None)
        if svc is None:
            return
        ip = self._collab_peer_ip_edit.text().strip()
        port_raw = self._collab_peer_port_edit.text().strip()
        if not ip or not port_raw.isdigit():
            return
        try:
            svc.add_manual_peer(ip, int(port_raw))
            self._collab_peer_ip_edit.clear()
            self._collab_peer_port_edit.clear()
        except Exception:  # noqa: BLE001
            pass

    def _refresh_collab_addr(self) -> None:
        svc = getattr(self.ctx, "collab_service", None)
        if svc is None:
            self._collab_addr_edit.setText("—")
            return
        try:
            if not svc.is_running():
                self._collab_addr_edit.setText("—")
                return
            self._collab_addr_edit.setText(svc.local_address())
        except Exception:  # noqa: BLE001
            self._collab_addr_edit.setText("—")

    def _refresh_collab_members(self) -> None:
        svc = getattr(self.ctx, "collab_service", None)
        self._collab_members_list.clear()
        if svc is None:
            self._collab_members_empty.setText("协作服务未启动")
            self._collab_members_empty.show()
            self._collab_members_list.hide()
            return
        try:
            for peer in svc.peers():
                name = peer.hostname or peer.ip
                self._collab_members_list.addItem(f"{name}  ({peer.ip}:{peer.port})")
        except Exception:  # noqa: BLE001
            pass
        if self._collab_members_list.count():
            self._collab_members_empty.hide()
            self._collab_members_list.show()
        else:
            self._collab_members_empty.setText("暂无在线成员")
            self._collab_members_empty.show()
            self._collab_members_list.hide()

    _HEALTH_COLOR = {"green": "#2e7d32", "yellow": "#f9a825", "red": "#c62828"}
    _HEALTH_LABEL = {"green": "正常", "yellow": "有注意事项", "red": "有阻断问题"}
    _COLLAB_STATUS_COLOR = {
        "no_service": "#999",
        "not_started": "#999",
        "missing_group": "#f9a825",
        "no_peers": "#999",
        "different_group": "#f9a825",
        "tasks_only": "#f9a825",
        "media_ready": "#2e7d32",
    }

    def _refresh_collab_health(self, recompute: bool = False) -> None:
        svc = getattr(self.ctx, "collab_service", None)
        status = build_collab_status(svc, [])
        if svc is None:
            self._collab_health_light.setStyleSheet("color: #999;")
            self._collab_health_text.setText(status.plain_status)
            return
        peers = []
        try:
            peers = svc.peers()
        except Exception:  # noqa: BLE001
            peers = []
        status = build_collab_status(svc, peers)
        diagnostics = []
        health = "green"
        try:
            diagnostics = svc.run_diagnostics() if recompute else svc.diagnostics()
            health = svc.overall_health()
        except Exception:  # noqa: BLE001
            health = "red"
            diagnostics = []
        color = self._COLLAB_STATUS_COLOR.get(status.state, "#999")
        if health == "red":
            color = self._HEALTH_COLOR["red"]
        self._collab_health_light.setStyleSheet(f"color: {color};")
        label = status.plain_status
        if status.state in {"tasks_only", "media_ready"}:
            label = f"{label} · {status.next_step_label}"
        reasons = [
            d.title for d in diagnostics
            if getattr(d, "code", "") != "ok" and getattr(d, "title", "")
        ]
        reasons = [reason for reason in reasons if reason not in label]
        if reasons:
            label = f"{label}；诊断：{'；'.join(reasons[:2])}"
        self._collab_health_text.setText(label)

    def _on_collab_diagnose(self) -> None:
        from app.widgets.collab_diagnostics_dialog import CollabDiagnosticsDialog
        svc = getattr(self.ctx, "collab_service", None)
        dlg = CollabDiagnosticsDialog(svc, parent=self)
        # Persist a group code adopted via a one-click fix.
        dlg.group_adopted.connect(self._on_group_adopted)
        dlg.exec()
        self._refresh_collab_health(recompute=True)

    def _on_group_adopted(self, code: str) -> None:
        self._collab_team_code_edit.setText(code)
        self._save_collab()

    def _on_collab_scan(self) -> None:
        svc = self.ctx.ensure_collab_service()
        if svc is None:
            from app.utils.ui import info as _info
            _info(self, "搜索局域网", "请先启用协作。")
            return
        try:
            found = svc.scan_lan()
        except Exception:  # noqa: BLE001
            found = []
        from app.utils.ui import info as _info
        _info(self, "搜索完成", f"发现 {len(found)} 台设备。")
        self._refresh_collab_members()
        self._refresh_collab_health(recompute=True)

    def _on_collab_show_pairing(self) -> None:
        from app.utils.ui import info as _info
        from app.widgets.collab_pairing import encode_pairing, generate_group_code
        from PyQt6.QtWidgets import QApplication
        svc = self.ctx.ensure_collab_service()
        if svc is None:
            _info(self, "配对码", "请先启用协作。")
            return
        if not self._collab_team_code_edit.text().strip():
            self._collab_team_code_edit.setText(generate_group_code())
        self._collab_enabled_chk.setChecked(True)
        self._save_collab()
        try:
            if not svc.is_running():
                svc.start(
                    project_name=self.ctx.current_project_dir or "",
                    group_code=self._collab_team_code_edit.text().strip(),
                    project_dir=self.ctx.current_project_dir or "",
                )
        except Exception:  # noqa: BLE001
            pass
        addr = svc.local_address()
        try:
            ip, port = addr.split(":")
            code = encode_pairing(ip, int(port), svc.group_code)
        except Exception:  # noqa: BLE001
            _info(self, "配对码", "暂时无法生成配对码。")
            return
        QApplication.clipboard().setText(code)
        _info(self, "我的配对码",
              f"配对码已复制，发给队友粘贴即可连接:\n\n{code}")

    def _on_collab_join_pairing(self) -> None:
        from app.utils.ui import info as _info, warn as _warn
        from app.widgets.collab_pairing import decode_pairing
        svc = self.ctx.ensure_collab_service()
        if svc is None:
            _info(self, "配对码", "协作服务不可用。")
            return
        raw = self._collab_pairing_input.text().strip()
        try:
            pi = decode_pairing(raw)
        except ValueError as exc:
            _warn(self, "配对码无效", str(exc))
            return
        if pi.group_code:
            self._collab_team_code_edit.setText(pi.group_code)
        self._collab_enabled_chk.setChecked(True)
        self._save_collab()
        try:
            if not svc.is_running():
                svc.start(
                    project_name=self.ctx.current_project_dir or "",
                    group_code=self._collab_team_code_edit.text().strip(),
                    project_dir=self.ctx.current_project_dir or "",
                )
        except Exception:  # noqa: BLE001
            pass
        try:
            svc.add_manual_peer(pi.ip, pi.port)
        except Exception:  # noqa: BLE001
            pass
        self._collab_pairing_input.clear()
        _info(self, "已加入", f"已连接 {pi.ip}:{pi.port}。")
        self._refresh_collab_members()
        self._refresh_collab_health(recompute=True)
