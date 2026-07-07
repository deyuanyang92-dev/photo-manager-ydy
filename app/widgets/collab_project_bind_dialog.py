"""collab_project_bind_dialog.py — Pick a teammate project to sync with.

Three paths in one dialog:
  1. Same-name one-click bind (highlighted when available)
  2. Searchable team project list
  3. Paste project sync code (advanced)
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QApplication,
    QDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.services.collab_project_bind import (
    TeamProjectBindOption,
    filter_bind_options,
    list_bind_options,
    same_name_options,
)
from app.utils.ui import center_on

if TYPE_CHECKING:
    from app.app_context import AppContext


class CollabProjectBindDialog(QDialog):
    """Unified project binding dialog for the collaboration centre."""

    applied = pyqtSignal()

    def __init__(self, ctx: "AppContext", parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self._svc = getattr(ctx, "collab_service", None)
        self.setWindowTitle("项目配对")
        self.setMinimumSize(560, 420)

        self._all_options: list[TeamProjectBindOption] = []
        self._same_name: list[TeamProjectBindOption] = []

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 12)
        root.setSpacing(10)

        intro = QLabel(
            "选择队友正在做的项目完成配对，之后标本和照片/TIF/ZIP 才会互相同步。"
            "本地文件夹名和磁盘位置可以不同。"
        )
        intro.setWordWrap(True)
        intro.setObjectName("MutedSmall")
        root.addWidget(intro)

        self._same_name_frame = QFrame()
        self._same_name_frame.setObjectName("CollabGuide")
        sn_lay = QVBoxLayout(self._same_name_frame)
        sn_lay.setContentsMargins(10, 8, 10, 8)
        sn_title = QLabel("同名项目 · 一键配对")
        sn_title.setObjectName("CollabStepTitle")
        sn_lay.addWidget(sn_title)
        self._same_name_detail = QLabel("")
        self._same_name_detail.setWordWrap(True)
        self._same_name_detail.setObjectName("CollabStepDetail")
        sn_lay.addWidget(self._same_name_detail)
        self._same_name_btn = QPushButton("配对同名项目")
        self._same_name_btn.setObjectName("Primary")
        self._same_name_btn.clicked.connect(self._bind_first_same_name)
        sn_lay.addWidget(self._same_name_btn)
        root.addWidget(self._same_name_frame)
        self._same_name_frame.hide()

        search_row = QHBoxLayout()
        search_row.addWidget(QLabel("搜索项目名："))
        self._search_edit = QLineEdit()
        self._search_edit.setPlaceholderText("输入队友的项目名过滤，留空显示全部")
        self._search_edit.textChanged.connect(self._reload_table)
        search_row.addWidget(self._search_edit, 1)
        root.addLayout(search_row)

        self._table = QTableWidget(0, 3)
        self._table.setHorizontalHeaderLabels(["项目名", "队友", "操作"])
        self._table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self._table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self._table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.verticalHeader().hide()
        root.addWidget(self._table, 1)

        adv_toggle = QPushButton("▸ 高级：粘贴项目码")
        adv_toggle.setObjectName("Ghost")
        adv_toggle.setCheckable(True)
        adv_toggle.toggled.connect(self._toggle_advanced)
        root.addWidget(adv_toggle)
        self._adv_toggle = adv_toggle

        self._adv_frame = QWidget()
        adv_lay = QVBoxLayout(self._adv_frame)
        adv_lay.setContentsMargins(0, 0, 0, 0)
        adv_lay.addWidget(QLabel("粘贴队友发来的项目码（SPP-PROJECT:…）："))
        self._code_edit = QLineEdit()
        self._code_edit.setPlaceholderText("粘贴项目码")
        adv_lay.addWidget(self._code_edit)
        paste_row = QHBoxLayout()
        paste_row.addStretch()
        paste_btn = QPushButton("确认绑定")
        paste_btn.clicked.connect(self._apply_pasted_code)
        paste_row.addWidget(paste_btn)
        adv_lay.addLayout(paste_row)
        self._adv_frame.hide()
        root.addWidget(self._adv_frame)

        footer = QHBoxLayout()
        footer.addStretch()
        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(self.reject)
        footer.addWidget(close_btn)
        root.addLayout(footer)

        self._reload_options()
        center_on(self, parent)

    def _reload_options(self) -> None:
        svc = self._svc
        if svc is None:
            self._all_options = []
            self._same_name = []
        else:
            peers = svc.peers()
            self._all_options = list_bind_options(svc, peers)
            self._same_name = same_name_options(svc, peers)
        self._refresh_same_name_banner()
        self._reload_table()

    def _refresh_same_name_banner(self) -> None:
        if not self._same_name:
            self._same_name_frame.hide()
            return
        opt = self._same_name[0]
        peers = "、".join(opt.peer_names[:3])
        self._same_name_detail.setText(
            f"队友 {peers} 也在做「{opt.name}」。点下面按钮即可绑定为同一项目。"
        )
        self._same_name_frame.show()

    def _reload_table(self) -> None:
        filtered = filter_bind_options(self._all_options, self._search_edit.text())
        self._table.setRowCount(len(filtered))
        for row, opt in enumerate(filtered):
            name_item = QTableWidgetItem(opt.name + (" · 同名" if opt.same_name else ""))
            name_item.setFlags(name_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self._table.setItem(row, 0, name_item)
            peer_item = QTableWidgetItem("、".join(opt.peer_names) or "—")
            peer_item.setFlags(peer_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self._table.setItem(row, 1, peer_item)
            btn = QPushButton("选择")
            btn.clicked.connect(lambda _checked=False, o=opt: self._bind_option(o))
            self._table.setCellWidget(row, 2, btn)

        if not filtered:
            self._table.setRowCount(1)
            empty = QTableWidgetItem("暂无队友项目。请确认队友已加入同一团队并打开了项目。")
            empty.setFlags(empty.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self._table.setItem(0, 0, empty)
            self._table.setSpan(0, 0, 1, 3)

    def _toggle_advanced(self, checked: bool) -> None:
        self._adv_frame.setVisible(checked)
        self._adv_toggle.setText("▾ 高级：粘贴项目码" if checked else "▸ 高级：粘贴项目码")

    def _bind_first_same_name(self) -> None:
        if self._same_name:
            self._bind_option(self._same_name[0])

    def _bind_option(self, opt: TeamProjectBindOption) -> None:
        self._confirm_and_apply(opt.code, opt.name, "、".join(opt.peer_names))

    def _apply_pasted_code(self) -> None:
        raw = self._code_edit.text().strip()
        if not raw:
            return
        try:
            from app.services.project_identity_service import parse_project_sync_code
            parsed = parse_project_sync_code(raw)
        except ValueError:
            QMessageBox.warning(self, "关联队友项目", "项目码格式不正确。")
            return
        name = parsed.get("projectName") or "队友项目"
        self._confirm_and_apply(raw, name, "")

    def _confirm_and_apply(self, code: str, remote_name: str, peer_hint: str) -> None:
        svc = self._svc
        if svc is None or not hasattr(svc, "apply_project_sync_code"):
            QMessageBox.warning(self, "关联队友项目", "协作服务未启动。")
            return
        try:
            from app.services.project_identity_service import parse_project_sync_code
            parsed = parse_project_sync_code(code)
        except ValueError:
            QMessageBox.warning(self, "关联队友项目", "项目码无效。")
            return
        if parsed["projectId"] == getattr(svc, "project_id", ""):
            QMessageBox.information(self, "关联队友项目", "当前项目已经与该队友项目同步。")
            self.applied.emit()
            self.accept()
            return

        peer_line = f"\n队友：{peer_hint}" if peer_hint else ""
        ret = QMessageBox.warning(
            self,
            "确认关联项目",
            f"把本机当前项目绑定到「{remote_name}」？{peer_line}\n\n"
            "绑定后同团队设备可以互相同步标本和照片；"
            "本地文件夹名可以保持不变。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if ret != QMessageBox.StandardButton.Yes:
            return
        try:
            svc.apply_project_sync_code(code)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "关联队友项目", f"绑定失败：{exc}")
            return
        try:
            svc.pull_all_specimens_from_session()
        except Exception:  # noqa: BLE001
            pass
        QMessageBox.information(self, "关联队友项目", f"已绑定到「{remote_name}」，开始与队友同步。")
        self.applied.emit()
        self.accept()
