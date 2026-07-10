"""Small dialogs for collab auxiliary actions (share scope, manual connect)."""
from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app.config import icons
from app.config.i18n import tr
from app.utils import ui
from app.widgets.collab_share_project_picker import CollabShareProjectPicker

if TYPE_CHECKING:
    from app.app_context import AppContext


class CollabShareScopeDialog(QDialog):
    """Pick which local workspaces are advertised to teammates."""

    def __init__(
        self,
        ctx: "AppContext",
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._ctx = ctx
        self.setObjectName("CollabShareScopeDialog")
        self.setWindowTitle(tr("共享项目范围"))
        self.setMinimumSize(620, 520)
        self.resize(680, 580)

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 16)
        root.setSpacing(14)

        header = QFrame()
        header.setObjectName("CollabShareDialogHeader")
        header_lay = QHBoxLayout(header)
        header_lay.setContentsMargins(14, 12, 14, 12)
        header_lay.setSpacing(12)
        icon_lbl = QLabel()
        icon_lbl.setPixmap(
            icons.icon("mdi6.folder-multiple-outline", color=icons.TONE_ACCENT).pixmap(28, 28)
        )
        icon_lbl.setFixedSize(32, 32)
        header_lay.addWidget(icon_lbl, 0, Qt.AlignmentFlag.AlignTop)
        title_col = QVBoxLayout()
        title_col.setSpacing(4)
        title = QLabel(tr("共享项目范围"))
        title.setObjectName("CollabShareDialogTitle")
        title_col.addWidget(title)
        hint = QLabel(
            tr("勾选要让队友在局域网看到的拍照工作区。列表来自最近打开的项目。")
        )
        hint.setObjectName("CollabShareDialogHint")
        hint.setWordWrap(True)
        title_col.addWidget(hint)
        header_lay.addLayout(title_col, 1)
        root.addWidget(header)

        self._picker = CollabShareProjectPicker(ctx, autoload=False)
        self._picker.reload()
        root.addWidget(self._picker, 1)

        buttons = QDialogButtonBox()
        ok_btn = buttons.addButton(tr("确定"), QDialogButtonBox.ButtonRole.AcceptRole)
        ok_btn.setObjectName("Primary")
        icons.set_button_icon(ok_btn, "mdi6.check", color=icons.TONE_ON_ACCENT, size=16)
        cancel_btn = buttons.addButton(tr("取消"), QDialogButtonBox.ButtonRole.RejectRole)
        cancel_btn.setObjectName("Outline")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)
        ui.center_on(self, parent)

    def accept(self) -> None:
        self._picker.apply_selection()
        super().accept()


class CollabManualConnectDialog(QDialog):
    """Enter teammate IP/port when LAN discovery fails."""

    def __init__(
        self,
        *,
        on_connect,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._on_connect = on_connect
        self.setWindowTitle("手动连接")
        self.setMinimumWidth(420)

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(10)

        hint = QLabel("局域网发现失败时，输入队友 IP 和端口。")
        hint.setObjectName("MutedSmall")
        hint.setWordWrap(True)
        root.addWidget(hint)

        row = QHBoxLayout()
        row.setSpacing(8)
        self._ip_input = QLineEdit()
        self._ip_input.setPlaceholderText("队友 IP，例如 192.168.1.100")
        self._ip_input.setObjectName("ManualIpInput")
        row.addWidget(self._ip_input, 1)
        self._port_input = QLineEdit("5050")
        self._port_input.setFixedWidth(72)
        self._port_input.setObjectName("ManualPortInput")
        row.addWidget(self._port_input)
        root.addLayout(row)

        self._error_label = QLabel("")
        self._error_label.setObjectName("MutedSmall")
        self._error_label.setWordWrap(True)
        self._error_label.hide()
        root.addWidget(self._error_label)

        buttons = QDialogButtonBox()
        connect_btn = buttons.addButton("连接", QDialogButtonBox.ButtonRole.AcceptRole)
        connect_btn.setObjectName("Primary")
        cancel_btn = buttons.addButton("取消", QDialogButtonBox.ButtonRole.RejectRole)
        connect_btn.clicked.connect(self._try_connect)
        cancel_btn.clicked.connect(self.reject)
        root.addWidget(buttons)

        self._ip_input.setFocus(Qt.FocusReason.OtherFocusReason)

    def _try_connect(self) -> None:
        ip = self._ip_input.text().strip()
        port_str = self._port_input.text().strip()
        if not ip:
            self._show_error("请输入 IP 地址")
            return
        try:
            port = int(port_str)
        except ValueError:
            self._show_error("端口号必须为数字")
            return
        self._error_label.hide()
        self._on_connect(ip, port)
        self.accept()

    def _show_error(self, message: str) -> None:
        self._error_label.setText(message)
        self._error_label.show()
