"""tiff_rename_dialog.py — 「TIFF 命名需确认」确认框。

外部 Helicon GUI 合成的 TIFF（外部名）按激活编号的成果名重命名前的确认框，复刻 Web
原型 renderTiffRenameModal（app.js:17548）：显示当前名 + 建议名（可编辑）+
[取消] / [确认改名并整理]。建议名由调用方按目标编号算好（organize_preview），默认绑激活
编号但用户可改（守 S5：不静默自动改、可调整）。

纯交互；磁盘改名走 organize_service.rename_tiff，归档走调用方。
"""
from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QLineEdit,
    QVBoxLayout,
    QWidget,
)


class TiffRenameDialog(QDialog):
    """确认把一个命名不规范的 TIFF 改成 *suggested_name*。

    用法::

        dlg = TiffRenameDialog(current_name, suggested_name, parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            new_name = dlg.new_name()   # 用户最终确认（可能已编辑）的文件名
    """

    def __init__(
        self,
        current_name: str,
        suggested_name: str,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("TIFF 命名需确认")
        self.setModal(True)
        self._build_ui(current_name, suggested_name)

    def _build_ui(self, current_name: str, suggested_name: str) -> None:
        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 16, 18, 16)
        lay.setSpacing(10)

        hint = QLabel("当前文件名不符合成果命名规范。确认后将按编号重命名并继续整理。")
        hint.setWordWrap(True)
        lay.addWidget(hint)

        cur = QLabel(f"当前：{current_name}")
        cur.setObjectName("MutedSmall")
        cur.setWordWrap(True)
        cur.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        lay.addWidget(cur)

        lay.addWidget(QLabel("建议文件名（可改）："))
        self._name_edit = QLineEdit(suggested_name)
        self._name_edit.setMinimumWidth(360)
        lay.addWidget(self._name_edit)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        ok_btn = btns.button(QDialogButtonBox.StandardButton.Ok)
        ok_btn.setText("确认改名并整理")
        ok_btn.setObjectName("Primary")
        btns.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        lay.addWidget(btns)

    def new_name(self) -> str:
        """用户确认的文件名（去空白）。"""
        return self._name_edit.text().strip()
