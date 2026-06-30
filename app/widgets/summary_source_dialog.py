"""Project-source picker for the interactive summary table."""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app.utils import ui

_PATH_ROLE = Qt.ItemDataRole.UserRole


def _theme():
    try:
        from app.config.theme import TOKENS
        return TOKENS.get
    except Exception:  # pragma: no cover
        return lambda key, default=None: default


class SummarySourceDialog(QDialog):
    """Let users choose recent projects and add ad-hoc project directories."""

    def __init__(
        self,
        projects: list[dict],
        *,
        selected_dirs: Optional[list[str]] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._selected = {self._resolve(d) for d in (selected_dirs or []) if d}
        self.setWindowTitle("选择汇总项目")
        self.resize(720, 520)
        self._build_ui()
        self._apply_style()
        for project in projects:
            self._add_project(project, checked=None)
        ui.center_on(self, parent)

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(10)

        title = QLabel("选择汇总项目")
        title.setObjectName("PaneTitle")
        root.addWidget(title)

        hint = QLabel("勾选需要汇总的项目；未出现在列表中的工作区可直接添加目录。")
        hint.setWordWrap(True)
        hint.setObjectName("Muted")
        root.addWidget(hint)

        self._list = QListWidget()
        self._list.setSelectionMode(QListWidget.SelectionMode.NoSelection)
        root.addWidget(self._list, 1)

        tools = QHBoxLayout()
        btn_all = QPushButton("全选")
        btn_all.clicked.connect(lambda: self._set_all(True))
        btn_none = QPushButton("清空")
        btn_none.clicked.connect(lambda: self._set_all(False))
        btn_add = QPushButton("添加项目目录…")
        btn_add.clicked.connect(self._add_directory)
        tools.addWidget(btn_all)
        tools.addWidget(btn_none)
        tools.addStretch()
        tools.addWidget(btn_add)
        root.addLayout(tools)

        actions = QHBoxLayout()
        actions.addStretch()
        cancel = QPushButton("取消")
        cancel.clicked.connect(self.reject)
        apply = QPushButton("应用")
        apply.setObjectName("Primary")
        apply.clicked.connect(self._accept_if_valid)
        actions.addWidget(cancel)
        actions.addWidget(apply)
        root.addLayout(actions)

    def _apply_style(self) -> None:
        g = _theme()
        bg = g("bg", "#0a1e24")
        panel = g("panel_2", "#0e2329")
        border = g("border", "#21424a")
        text = g("text", "#c8dcd6")
        muted = g("muted", "#7fa49b")
        accent = g("accent", "#4fd1b8")
        accent_fg = g("accent_fg", "#ffffff")
        self.setStyleSheet(
            f"QDialog{{background:{bg};}}"
            f"QLabel{{color:{text};background:transparent;}}"
            f"QLabel#PaneTitle{{font-weight:600;font-size:15px;}}"
            f"QLabel#Muted{{color:{muted};font-size:12px;}}"
            f"QPushButton{{background:{panel};color:{text};border:1px solid {border};"
            f"border-radius:5px;padding:5px 12px;font-size:13px;}}"
            f"QPushButton:hover{{background:{border};}}"
            f"QPushButton#Primary{{background:{accent};color:{accent_fg};border:1px solid {accent};}}"
            f"QListWidget{{background:{bg};color:{text};border:1px solid {border};"
            f"border-radius:6px;font-size:13px;}}"
            f"QListWidget::item{{padding:5px 4px;}}"
        )

    @staticmethod
    def _resolve(path: str) -> str:
        try:
            return str(Path(path).resolve())
        except OSError:
            return str(path)

    def _add_project(self, project: dict, *, checked: Optional[bool]) -> None:
        directory = project.get("directory") or project.get("dir") or ""
        if not directory:
            return
        resolved = self._resolve(directory)
        for i in range(self._list.count()):
            if self._list.item(i).data(_PATH_ROLE) == resolved:
                return
        name = project.get("name") or project.get("label") or Path(resolved).name
        code = project.get("project_code") or project.get("projectCode") or ""
        text = f"{name}"
        if code:
            text += f"  ·  {code}"
        text += f"\n{resolved}"
        item = QListWidgetItem(text)
        item.setData(_PATH_ROLE, resolved)
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
        is_checked = (resolved in self._selected) if checked is None else checked
        item.setCheckState(Qt.CheckState.Checked if is_checked else Qt.CheckState.Unchecked)
        self._list.addItem(item)

    def _set_all(self, checked: bool) -> None:
        state = Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
        for i in range(self._list.count()):
            self._list.item(i).setCheckState(state)

    def _add_directory(self) -> None:
        start = ""
        if self._list.count():
            first = self._list.item(0).data(_PATH_ROLE)
            if first:
                start = str(Path(first).parent)
        path = ui.get_existing_directory(self, "添加项目目录", start)
        if not path:
            return
        resolved = self._resolve(path)
        db_path = Path(resolved) / "_data" / "project.db"
        if not db_path.exists():
            ui.warn(self, "添加项目目录", "该目录未找到 _data/project.db；汇总时会自动跳过。")
        self._add_project({"directory": resolved, "name": Path(resolved).name}, checked=True)

    def selected_dirs(self) -> list[str]:
        out: list[str] = []
        for i in range(self._list.count()):
            item = self._list.item(i)
            if item.checkState() == Qt.CheckState.Checked:
                directory = item.data(_PATH_ROLE)
                if directory:
                    out.append(directory)
        return out

    def _accept_if_valid(self) -> None:
        if not self.selected_dirs():
            ui.warn(self, "选择汇总项目", "请至少勾选一个项目。")
            return
        self.accept()
