"""Checkbox list: pick which local projects to advertise on the LAN team."""
from __future__ import annotations

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

from app.services.collab_share_registry import (
    default_shared_dirs,
    list_local_share_candidates,
    load_shared_dirs,
    save_shared_dirs,
)

if TYPE_CHECKING:
    from app.app_context import AppContext


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
        self._checks: dict[str, QCheckBox] = {}
        self._projects: dict[str, object] = {}
        self._loaded = False

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(6)

        hint = QLabel(
            "可选：设置团队永久码后，勾选这些项目让队友在局域网看到；"
            "项目码共享不要求先勾选。"
        )
        hint.setObjectName("MutedSmall")
        hint.setWordWrap(True)
        root.addWidget(hint)

        tools = QHBoxLayout()
        tools.setSpacing(6)
        self._search_edit = QLineEdit()
        self._search_edit.setPlaceholderText("搜索项目名或路径")
        self._search_edit.textChanged.connect(self._apply_filter)
        tools.addWidget(self._search_edit, 1)

        self._current_btn = QPushButton("当前项目")
        self._current_btn.setObjectName("Ghost")
        self._current_btn.clicked.connect(self._select_current_project)
        tools.addWidget(self._current_btn)

        self._clear_btn = QPushButton("清空")
        self._clear_btn.setObjectName("Ghost")
        self._clear_btn.clicked.connect(lambda: self.set_all_checked(False))
        tools.addWidget(self._clear_btn)
        root.addLayout(tools)

        self._summary_label = QLabel("已选择 0 个项目")
        self._summary_label.setObjectName("MutedSmall")
        root.addWidget(self._summary_label)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setMinimumHeight(74)
        scroll.setMaximumHeight(118)
        self._list_host = QWidget()
        self._list_lay = QVBoxLayout(self._list_host)
        self._list_lay.setContentsMargins(0, 0, 0, 0)
        self._list_lay.setSpacing(4)
        scroll.setWidget(self._list_host)
        root.addWidget(scroll)

        self._empty_label = QLabel("暂无可用项目。请先打开或新建一个拍照工作区。")
        self._empty_label.setObjectName("MutedSmall")
        self._empty_label.setWordWrap(True)
        self._empty_label.hide()
        root.addWidget(self._empty_label)

        self._preview_label = QLabel("点击项目可查看本机绝对路径和项目码摘要。")
        self._preview_label.setObjectName("MutedSmall")
        self._preview_label.setWordWrap(True)
        root.addWidget(self._preview_label)

        if autoload:
            self.reload()

    def reload(self) -> None:
        self._loaded = True
        while self._list_lay.count():
            item = self._list_lay.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._checks.clear()
        self._projects.clear()

        current = getattr(self._ctx, "current_project_dir", None)
        candidates = list_local_share_candidates(extra_directories=[current] if current else None)
        settings = getattr(self._ctx, "settings", None)
        qs = getattr(settings, "_qs", settings)
        selected = default_shared_dirs(qs, current_directory=current)

        if not candidates:
            self._empty_label.show()
            self._refresh_summary()
            self._preview_label.setText("暂无可用项目。请先打开或新建一个拍照工作区。")
            return
        self._empty_label.hide()

        for project in candidates:
            box = QCheckBox(project.name)
            box.setToolTip(project.directory)
            box.setChecked(project.directory in selected)
            box.toggled.connect(lambda _=False: self._on_selection_changed())
            box.clicked.connect(lambda _checked=False, p=project: self._show_preview(p))
            self._list_lay.addWidget(box)
            self._checks[project.directory] = box
            self._projects[project.directory] = project
        self._list_lay.addStretch()
        self._apply_filter()
        self._refresh_summary()
        self._show_default_preview()

    def selected_directories(self) -> set[str]:
        return {
            directory
            for directory, box in self._checks.items()
            if box.isChecked()
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
        for box in self._checks.values():
            box.setChecked(checked)
        self._refresh_summary()

    def _on_selection_changed(self) -> None:
        self._refresh_summary()
        self.selection_changed.emit()

    def _refresh_summary(self) -> None:
        total = len(self._checks)
        selected = len(self.selected_directories())
        visible = sum(1 for box in self._checks.values() if not box.isHidden())
        if self._search_edit.text().strip():
            self._summary_label.setText(f"已选择 {selected} 个项目 · 当前显示 {visible}/{total}")
        else:
            self._summary_label.setText(f"已选择 {selected} 个项目 · 共 {total} 个可共享项目")

    def _apply_filter(self) -> None:
        query = self._search_edit.text().strip().casefold()
        first_visible = None
        for directory, box in self._checks.items():
            project = self._projects.get(directory)
            haystack = " ".join((
                getattr(project, "name", ""),
                getattr(project, "directory", directory),
                getattr(project, "project_id", ""),
            )).casefold()
            visible = not query or query in haystack
            box.setVisible(visible)
            if visible and first_visible is None:
                first_visible = project
        if first_visible is not None:
            self._show_preview(first_visible)
        elif self._checks:
            self._preview_label.setText("没有匹配的项目。")
        self._refresh_summary()

    def _select_current_project(self) -> None:
        current = getattr(self._ctx, "current_project_dir", None)
        if not current:
            return
        try:
            from pathlib import Path
            current_key = str(Path(str(current)).resolve())
        except OSError:
            current_key = str(current)
        if current_key not in self._checks:
            return
        for directory, box in self._checks.items():
            box.setChecked(directory == current_key)
        self._show_preview(self._projects[current_key])
        self._refresh_summary()
        self.selection_changed.emit()

    def _show_default_preview(self) -> None:
        selected = self.selected_directories()
        first_dir = next(iter(selected), None) or next(iter(self._checks), None)
        project = self._projects.get(first_dir) if first_dir else None
        if project is not None:
            self._show_preview(project)
        elif self._checks:
            self._preview_label.setText("点击项目可查看本机绝对路径和项目码摘要。")

    def _show_preview(self, project) -> None:
        pid = getattr(project, "project_id", "")
        short = f"{pid[:8]}…" if pid else "未生成"
        self._preview_label.setText(
            f"{getattr(project, 'name', '项目')} · 项目码 {short}\n"
            f"{getattr(project, 'directory', '')}"
        )
