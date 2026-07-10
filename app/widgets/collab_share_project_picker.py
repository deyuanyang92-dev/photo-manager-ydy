"""Checkbox list: pick which local projects to advertise on the LAN team."""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from app.config import icons
from app.config.i18n import tr
from app.services.collab_share_registry import (
    default_shared_dirs,
    list_local_share_candidates,
    load_shared_dirs,
    save_shared_dirs,
)

if TYPE_CHECKING:
    from app.app_context import AppContext


def _short_project_id(project_id: str) -> str:
    pid = str(project_id or "").strip()
    return f"{pid[:8]}…" if pid else tr("未生成")


def _short_path(directory: str) -> str:
    text = str(directory or "").strip()
    if len(text) <= 56:
        return text
    return f"…{text[-52:]}"


class _ShareProjectRow(QFrame):
    """One shareable project: checkbox + name + muted path/code line."""

    toggled = pyqtSignal(bool)

    def __init__(
        self,
        project,
        *,
        checked: bool = False,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.project = project
        self.setObjectName("CollabShareProjectRow")
        self.setFrameShape(QFrame.Shape.StyledPanel)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(12)

        self._check = QCheckBox(getattr(project, "name", tr("项目")))
        self._check.setObjectName("CollabShareProjectName")
        self._check.setChecked(checked)
        self._check.toggled.connect(self.toggled.emit)
        layout.addWidget(self._check, 1)

        meta_col = QVBoxLayout()
        meta_col.setSpacing(2)
        meta_col.setContentsMargins(0, 0, 0, 0)
        pid = _short_project_id(getattr(project, "project_id", ""))
        self._code_label = QLabel(tr("项目码 {code}").format(code=pid))
        self._code_label.setObjectName("CollabShareProjectCode")
        meta_col.addWidget(self._code_label)
        path = _short_path(getattr(project, "directory", ""))
        self._detail = QLabel(path)
        self._detail.setObjectName("CollabShareProjectPath")
        self._detail.setWordWrap(True)
        meta_col.addWidget(self._detail)
        layout.addLayout(meta_col, 2)

    def isChecked(self) -> bool:
        return self._check.isChecked()

    def setChecked(self, checked: bool) -> bool:
        return self._check.setChecked(checked)

    @property
    def directory(self) -> str:
        return str(getattr(self.project, "directory", ""))


class CollabShareProjectPicker(QWidget):
    """Lets the user tick which workspaces teammates may see and sync with."""

    selection_changed = pyqtSignal()

    def __init__(
        self,
        ctx: "AppContext",
        parent: Optional[QWidget] = None,
        *,
        autoload: bool = True,
    ) -> None:
        super().__init__(parent)
        self._ctx = ctx
        self._rows: dict[str, _ShareProjectRow] = {}
        self._projects: dict[str, object] = {}
        self._loaded = False

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(10)

        self._root_label = QLabel("")
        self._root_label.setObjectName("CollabShareRootBanner")
        self._root_label.setWordWrap(True)
        root.addWidget(self._root_label)

        tools = QFrame()
        tools.setObjectName("CollabShareToolsBar")
        tools_lay = QHBoxLayout(tools)
        tools_lay.setContentsMargins(10, 8, 10, 8)
        tools_lay.setSpacing(8)
        self._search_edit = QLineEdit()
        self._search_edit.setObjectName("CollabShareSearch")
        self._search_edit.setPlaceholderText(tr("搜索项目名或路径"))
        self._search_edit.textChanged.connect(self._apply_filter)
        tools_lay.addWidget(self._search_edit, 1)

        self._all_btn = QPushButton(tr("全选"))
        self._all_btn.setObjectName("Ghost")
        self._all_btn.clicked.connect(self._select_all_visible)
        tools_lay.addWidget(self._all_btn)

        self._current_btn = QPushButton(tr("只选当前"))
        self._current_btn.setObjectName("Ghost")
        self._current_btn.clicked.connect(self._select_current_project)
        tools_lay.addWidget(self._current_btn)

        self._clear_btn = QPushButton(tr("清空"))
        self._clear_btn.setObjectName("Ghost")
        self._clear_btn.clicked.connect(lambda: self.set_all_checked(False))
        tools_lay.addWidget(self._clear_btn)

        self._add_btn = QPushButton(tr("添加文件夹"))
        self._add_btn.setObjectName("Outline")
        icons.set_button_icon(self._add_btn, "mdi6.folder-plus-outline", size=16)
        self._add_btn.clicked.connect(self._add_workspace_folder)
        tools_lay.addWidget(self._add_btn)
        root.addWidget(tools)

        self._summary_label = QLabel(tr("已选择 0 个项目"))
        self._summary_label.setObjectName("CollabShareSummary")
        root.addWidget(self._summary_label)

        self._scroll = QScrollArea()
        self._scroll.setObjectName("CollabShareProjectScroll")
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self._list_host = QWidget()
        self._list_lay = QVBoxLayout(self._list_host)
        self._list_lay.setContentsMargins(0, 0, 0, 0)
        self._list_lay.setSpacing(8)
        self._scroll.setWidget(self._list_host)
        root.addWidget(self._scroll, 1)

        self._empty_label = QLabel(tr("暂无可用项目。请先打开或新建一个拍照工作区。"))
        self._empty_label.setObjectName("EmptyState")
        self._empty_label.setWordWrap(True)
        self._empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_label.hide()
        root.addWidget(self._empty_label)

        if autoload:
            self.reload()

    def reload(self) -> None:
        self._loaded = True
        while self._list_lay.count():
            item = self._list_lay.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._rows.clear()
        self._projects.clear()

        current = getattr(self._ctx, "current_project_dir", None)
        candidates = list_local_share_candidates(
            extra_directories=[current] if current else None
        )
        settings = getattr(self._ctx, "settings", None)
        qs = getattr(settings, "_qs", settings)
        selected = default_shared_dirs(qs, current_directory=current)

        if not candidates:
            self._empty_label.show()
            self._scroll.hide()
            self._refresh_summary()
            self._refresh_root_label()
            return

        self._empty_label.hide()
        self._scroll.show()

        for project in candidates:
            directory = str(getattr(project, "directory", ""))
            row = _ShareProjectRow(
                project,
                checked=directory in selected,
            )
            row.toggled.connect(lambda _=False: self._on_selection_changed())
            self._list_lay.addWidget(row)
            self._rows[directory] = row
            self._projects[directory] = project

        self._list_lay.addStretch(1)
        self._resize_scroll()
        self._apply_filter()
        self._refresh_summary()
        self._refresh_root_label()

    def _common_parent(self, directories: list[str] | None = None) -> str:
        dirs = directories if directories is not None else list(self._rows)
        if not dirs:
            return ""
        try:
            parts = [Path(d).parts for d in dirs]
            common: list[str] = []
            for segs in zip(*parts):
                if len(set(segs)) == 1:
                    common.append(segs[0])
                else:
                    break
            if common:
                return str(Path(*common))
        except (OSError, ValueError, IndexError):
            pass
        if len(dirs) == 1:
            return str(Path(dirs[0]).parent)
        return ""

    def _refresh_root_label(self) -> None:
        dirs = list(self._rows)
        if not dirs:
            self._root_label.setText(
                tr("列表来自「最近打开的项目」。可先添加文件夹，或去总览/项目树打开工作区。")
            )
            return
        parent = self._common_parent(dirs)
        if parent:
            self._root_label.setText(
                tr("工作区目录：{dir} · 最近打开 {count} 个项目").format(
                    dir=parent, count=len(dirs)
                )
            )
        else:
            self._root_label.setText(
                tr("各项目路径独立保存 · 最近打开 {count} 个工作区").format(
                    count=len(dirs)
                )
            )

    def _resize_scroll(self) -> None:
        visible = sum(1 for row in self._rows.values() if row.isVisible())
        count = max(visible, len(self._rows))
        row_h = 68
        height = min(280, max(140, count * row_h + 16))
        self._scroll.setMinimumHeight(height)
        self._scroll.setMaximumHeight(height)

    def selected_directories(self) -> set[str]:
        return {
            directory
            for directory, row in self._rows.items()
            if row.isChecked()
        }

    def apply_selection(self) -> set[str]:
        """Persist ticks and push to the collab service if running."""
        selected = self.selected_directories()
        settings = getattr(self._ctx, "settings", None)
        qs = getattr(settings, "_qs", settings)
        save_shared_dirs(qs, selected)
        svc = getattr(self._ctx, "collab_service", None)
        if svc is not None and hasattr(svc, "set_shared_project_dirs"):
            svc.set_shared_project_dirs(selected)
        return selected

    def set_all_checked(self, checked: bool) -> None:
        for row in self._rows.values():
            row.setChecked(checked)
        self._refresh_summary()

    def _select_all_visible(self) -> None:
        any_visible = False
        for row in self._rows.values():
            if row.isHidden():
                continue
            any_visible = True
            row.setChecked(True)
        if not any_visible:
            self._summary_label.setText(tr("没有可选项目"))
            return
        self._refresh_summary()
        self.selection_changed.emit()

    def _add_workspace_folder(self) -> None:
        from app.services.project_service import (
            resolve_user_projects_json,
            record_recent_workspace,
        )
        from app.services.project_tree_service import is_workspace
        from app.utils.ui import get_existing_directory

        start = self._common_parent() or getattr(self._ctx, "current_project_dir", "") or ""
        chosen = get_existing_directory(self, tr("选择拍照工作区文件夹"), start)
        if not chosen:
            return
        if not is_workspace(chosen):
            self._summary_label.setText(
                tr("该文件夹还不是工作区（需先在此目录创建/打开项目）。")
            )
            return
        record_recent_workspace(resolve_user_projects_json(), chosen)
        self.reload()
        self.selection_changed.emit()

    def _on_selection_changed(self) -> None:
        self._refresh_summary()
        self.selection_changed.emit()

    def _refresh_summary(self) -> None:
        total = len(self._rows)
        selected = len(self.selected_directories())
        visible = sum(1 for row in self._rows.values() if row.isVisible())
        if self._search_edit.text().strip():
            self._summary_label.setText(
                tr("已选择 {selected} 个 · 显示 {visible}/{total}").format(
                    selected=selected, visible=visible, total=total
                )
            )
        else:
            self._summary_label.setText(
                tr("已选择 {selected} 个 · 共 {total} 个可共享").format(
                    selected=selected, total=total
                )
            )

    def _apply_filter(self) -> None:
        query = self._search_edit.text().strip().casefold()
        for directory, row in self._rows.items():
            project = self._projects.get(directory)
            haystack = " ".join((
                getattr(project, "name", ""),
                getattr(project, "directory", directory),
                getattr(project, "project_id", ""),
            )).casefold()
            row.setVisible(not query or query in haystack)
        self._resize_scroll()
        if self._rows and not any(row.isVisible() for row in self._rows.values()):
            self._summary_label.setText(tr("没有匹配的项目"))
        else:
            self._refresh_summary()

    def _select_current_project(self) -> None:
        current = getattr(self._ctx, "current_project_dir", None)
        if not current:
            return
        try:
            current_key = str(Path(str(current)).resolve())
        except OSError:
            current_key = str(current)
        if current_key not in self._rows:
            return
        for directory, row in self._rows.items():
            row.setChecked(directory == current_key)
        self._refresh_summary()
        self.selection_changed.emit()

    # Back-compat for tests that touch _checks
    @property
    def _checks(self) -> dict[str, QCheckBox]:
        return {directory: row._check for directory, row in self._rows.items()}
