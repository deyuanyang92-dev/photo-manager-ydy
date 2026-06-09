"""collab_diagnostics_dialog.py — the collaboration "doctor" for novices.

Shows a traffic-light health summary plus one card per diagnostic, each in
plain Chinese with the problem, the cause and how to fix it.  Safe fixes are
offered as one-click buttons; risky ones (firewall, clock) are shown as copyable
instructions rather than applied silently.
"""
from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from app.utils.ui import info as _info

_HEALTH_COLOR = {"green": "#2e7d32", "yellow": "#f9a825", "red": "#c62828"}
_HEALTH_TEXT = {"green": "协作正常", "yellow": "有需要注意的问题", "red": "存在阻断性问题"}
_LEVEL_COLOR = {"ok": "#2e7d32", "warn": "#f9a825", "error": "#c62828"}
_LEVEL_ICON = {"ok": "✓", "warn": "!", "error": "✕"}


class CollabDiagnosticsDialog(QDialog):
    """Diagnostics doctor dialog.  Works with or without a live service."""

    group_adopted = pyqtSignal(str)   # new group code chosen via a one-click fix

    def __init__(self, svc, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._svc = svc
        self.setWindowTitle("协作诊断")
        self.setMinimumWidth(460)
        self._row_widgets: list[QWidget] = []

        root = QVBoxLayout(self)

        # Header: traffic light + overall text
        header = QHBoxLayout()
        self._light = QLabel("●")
        self._light.setStyleSheet("font-size: 20px;")
        self._summary = QLabel("")
        self._summary.setStyleSheet("font-weight: bold;")
        header.addWidget(self._light)
        header.addWidget(self._summary, stretch=1)
        root.addLayout(header)

        # Scrollable list of diagnostic cards
        self._list_host = QWidget()
        self._list_layout = QVBoxLayout(self._list_host)
        self._list_layout.setContentsMargins(0, 0, 0, 0)
        self._list_layout.setSpacing(8)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self._list_host)
        scroll.setMinimumHeight(220)
        root.addWidget(scroll, stretch=1)

        # Footer buttons
        footer = QHBoxLayout()
        recheck = QPushButton("重新检测")
        recheck.clicked.connect(self._recheck)
        scan_btn = QPushButton("搜索局域网队友")
        scan_btn.clicked.connect(self._scan)
        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(self.accept)
        footer.addWidget(recheck)
        footer.addWidget(scan_btn)
        footer.addStretch()
        footer.addWidget(close_btn)
        root.addLayout(footer)

        self._refresh()

    # ── data ──────────────────────────────────────────────────────────────

    def _rows_count(self) -> int:
        return len(self._row_widgets)

    def _diagnostics(self):
        if self._svc is None:
            return []
        return self._svc.run_diagnostics()

    def _refresh(self) -> None:
        # Clear old rows
        for w in self._row_widgets:
            w.setParent(None)
        self._row_widgets.clear()

        diags = self._diagnostics()
        health = self._svc.overall_health() if self._svc is not None else "red"
        self._light.setStyleSheet(
            f"font-size: 20px; color: {_HEALTH_COLOR.get(health, '#999')};")
        if self._svc is None:
            self._summary.setText("协作服务未启用")
        else:
            self._summary.setText(_HEALTH_TEXT.get(health, ""))

        if self._svc is None:
            card = self._make_card(
                "error", "协作未启用", "未检测到协作服务。", "在「设置 → 协作」启用局域网协作。", None)
            self._list_layout.addWidget(card)
            self._row_widgets.append(card)
            return

        for d in diags:
            card = self._make_card(d.level, d.title, d.detail, d.fix, d.action)
            self._list_layout.addWidget(card)
            self._row_widgets.append(card)

    def _make_card(self, level: str, title: str, detail: str, fix: str,
                   action: Optional[str]) -> QWidget:
        card = QFrame()
        card.setFrameShape(QFrame.Shape.StyledPanel)
        lay = QVBoxLayout(card)
        head = QLabel(f"{_LEVEL_ICON.get(level, '?')}  {title}")
        head.setStyleSheet(f"font-weight: bold; color: {_LEVEL_COLOR.get(level, '#333')};")
        lay.addWidget(head)
        if detail:
            d = QLabel(detail)
            d.setWordWrap(True)
            lay.addWidget(d)
        if fix:
            f = QLabel(f"建议:{fix}")
            f.setWordWrap(True)
            f.setObjectName("Muted")
            lay.addWidget(f)
        if action:
            btn = self._make_action_button(action)
            if btn is not None:
                lay.addWidget(btn, alignment=Qt.AlignmentFlag.AlignLeft)
        return card

    def _make_action_button(self, action: str) -> Optional[QPushButton]:
        if action == "adopt_group":
            others = self._mismatched_groups()
            if not others:
                return None
            target = others[0]
            btn = QPushButton(f"改用组码「{target}」")
            btn.clicked.connect(lambda: self._confirm_adopt(target))
            return btn
        if action == "open_firewall":
            btn = QPushButton("查看放行防火墙的方法")
            btn.clicked.connect(self._show_firewall_help)
            return btn
        return None

    # ── actions ───────────────────────────────────────────────────────────

    def _mismatched_groups(self) -> list[str]:
        if self._svc is None:
            return []
        mine = self._svc.group_code
        return sorted({p.group_code for p in self._svc.peers()
                       if p.group_code and p.group_code != mine})

    def _confirm_adopt(self, code: str) -> None:
        reply = QMessageBox.question(
            self, "改用组码",
            f"确定把本机协作组码改为「{code}」吗?\n改后将与该组设备同步。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel)
        if reply == QMessageBox.StandardButton.Yes:
            self._adopt_group(code)

    def _adopt_group(self, code: str) -> None:
        if self._svc is not None:
            self._svc.set_group_code(code)
        self.group_adopted.emit(code)
        self._refresh()

    def _show_firewall_help(self) -> None:
        port = 5050
        if self._svc is not None:
            port = getattr(self._svc, "_port", None) or 5050
        cmd = (f'netsh advfirewall firewall add rule name="Specimen Collab" '
               f'dir=in action=allow protocol=TCP localport={port}')
        _info(
            self, "放行防火墙(Windows)",
            "队友连不到你,通常是防火墙挡了入站连接。\n\n"
            "Windows:以管理员身份打开命令提示符,运行:\n\n"
            f"{cmd}\n\n"
            "macOS / Linux:在系统防火墙中放行该端口的 TCP 入站。",
        )

    def _recheck(self) -> None:
        if self._svc is not None:
            try:
                self._svc.run_probes()
            except Exception:  # noqa: BLE001
                pass
        self._refresh()

    def _scan(self) -> None:
        if self._svc is None:
            return
        try:
            found = self._svc.scan_lan()
        except Exception:  # noqa: BLE001
            found = []
        _info(self, "搜索完成", f"发现 {len(found)} 台设备。")
        self._refresh()
