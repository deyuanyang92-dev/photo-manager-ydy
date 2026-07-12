"""admin_delete_dialog.py — 删除 TIF 的管理员确认框(密码 + 操作人 + 原因)。

用户 2026-07-12: "TIF 可以删, 只是需要管理员。要输入密码, 给出删除的操作人。"

界面故意做得"重"一点 —— 删的是无损母片, 该让人停一秒:
    ┌ 删除 TIF ─────────────────────────────┐
    │ ⚠ 即将永久删除 2 个 TIF 母片, 不可恢复  │
    │   FJ-XM-A01-1-T95E-20260601.tif        │
    │   …                                    │
    │ 操作人  [____________]  (必填, 会记入档案)│
    │ 管理员密码 [**********]                 │
    │ 原因(可选) [__________]                 │
    │              [取消]  [确认删除]          │
    └────────────────────────────────────────┘
操作人默认填上一次用过的名字(ctx.edit_actor), 免得每次重打。

(Fable 5, 2026-07-12)
"""
from __future__ import annotations

from typing import Any, Optional

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QVBoxLayout,
    QWidget,
)

from app.services.tiff_delete_gate import TiffDeleteDenied, check_admin


class AdminDeleteDialog(QDialog):
    """返回 (actor, password, reason); 取消返回 None(见 ask_admin_delete)。"""

    def __init__(
        self,
        parent: Optional[QWidget],
        paths: list[str],
        *,
        default_actor: str = "",
        config_path: Optional[str] = None,
    ) -> None:
        super().__init__(parent)
        self._config_path = config_path
        self.setWindowTitle("删除 TIF（需要管理员）")
        self.setMinimumWidth(460)

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(10)

        head = QLabel(
            f"⚠ 即将永久删除 {len(paths)} 个 TIF 母片，删除后不可恢复。"
        )
        head.setObjectName("UnattributedWarning")
        head.setWordWrap(True)
        root.addWidget(head)

        names = "\n".join(str(p).split("/")[-1].split("\\")[-1] for p in paths[:8])
        if len(paths) > 8:
            names += f"\n… 另外 {len(paths) - 8} 个"
        files = QLabel(names)
        files.setObjectName("MutedSmall")
        files.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        root.addWidget(files)

        form = QFormLayout()
        form.setSpacing(8)
        self._actor = QLineEdit(default_actor)
        self._actor.setPlaceholderText("必填，会记入删除档案")
        self._pwd = QLineEdit()
        self._pwd.setEchoMode(QLineEdit.EchoMode.Password)
        self._pwd.setPlaceholderText("管理员密码")
        self._reason = QLineEdit()
        self._reason.setPlaceholderText("可选，例如：拍虚了要重拍")
        form.addRow("操作人", self._actor)
        form.addRow("管理员密码", self._pwd)
        form.addRow("原因", self._reason)
        root.addLayout(form)

        self._err = QLabel("")
        self._err.setObjectName("UnattributedWarning")
        self._err.hide()
        root.addWidget(self._err)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Ok
        )
        ok_btn = btns.button(QDialogButtonBox.StandardButton.Ok)
        ok_btn.setText("确认删除")
        ok_btn.setObjectName("Danger")
        btns.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        btns.accepted.connect(self._try_accept)
        btns.rejected.connect(self.reject)
        root.addWidget(btns)

    def _try_accept(self) -> None:
        """密码/操作人当场校验 —— 错了留在框里改, 不关框(免得重填一遍)。"""
        try:
            check_admin(self._actor.text(), self._pwd.text(), config_path=self._config_path)
        except TiffDeleteDenied as exc:
            self._err.setText(f"⚠ {exc}")
            self._err.show()
            self._pwd.selectAll()
            self._pwd.setFocus()
            return
        self.accept()

    def values(self) -> tuple[str, str, str]:
        return (
            self._actor.text().strip(),
            self._pwd.text(),
            self._reason.text().strip(),
        )


def ask_admin_delete(
    parent: Optional[QWidget],
    ctx: Any,
    paths: list[str],
    *,
    config_path: Optional[str] = None,
) -> Optional[tuple[str, str, str]]:
    """弹管理员删除框。通过 -> (actor, password, reason); 取消 -> None。

    通过后把操作人记到 ctx.edit_actor, 下次默认填上(同一套「谁在操作」的身份,
    与数据筛选页的编辑锁共用)。
    """
    default_actor = str(getattr(ctx, "edit_actor", "") or "")
    dlg = AdminDeleteDialog(parent, list(paths), default_actor=default_actor,
                            config_path=config_path)
    if dlg.exec() != QDialog.DialogCode.Accepted:
        return None
    actor, pwd, reason = dlg.values()
    try:
        ctx.edit_actor = actor
    except Exception:  # noqa: BLE001 —— ctx 可能是只读桩
        pass
    return actor, pwd, reason
