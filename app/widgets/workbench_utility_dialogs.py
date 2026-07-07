"""workbench_utility_dialogs.py — 工作台的小型辅助界面。

从 app/views/workbench_view.py 拆出；workbench_view 仍 re-export 原私有名，
避免破坏测试和旧导入。
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


class _RetroactiveScanDialog(QDialog):
    """Pre-scan dialog: choose results/ subdirectory before retroactive scan.

    Presents a combo populated with subdirectories of project results/.
    '全部' (data=None) scans the whole results/ tree; a named entry restricts
    the scan to results/<subdir>/.
    """

    def __init__(self, project_dir: str, parent=None, results_subdir: str = "results") -> None:
        super().__init__(parent)
        self.setWindowTitle("存量整理 — 选择扫描范围")
        self._project_dir = project_dir
        self._results_subdir = results_subdir or "results"
        self._build_ui()

    def _build_ui(self) -> None:
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 16, 16, 16)
        lay.setSpacing(12)

        lay.addWidget(QLabel("子目录："))
        self._subdir_combo = QComboBox()
        self._subdir_combo.addItem("全部", None)
        results_dir = Path(self._project_dir) / self._results_subdir
        if results_dir.exists():
            for d in sorted(results_dir.iterdir()):
                if d.is_dir():
                    self._subdir_combo.addItem(d.name, d.name)
        lay.addWidget(self._subdir_combo)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        ok_btn = btns.button(QDialogButtonBox.StandardButton.Ok)
        if ok_btn:
            ok_btn.setText("开始扫描")
        cancel_btn = btns.button(QDialogButtonBox.StandardButton.Cancel)
        if cancel_btn:
            cancel_btn.setText("取消")
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        lay.addWidget(btns)

    def selected_subdir(self) -> Optional[str]:
        """Return the selected subdirectory name, or None for 全部."""
        return self._subdir_combo.currentData()


class _AutoGroupSourceDialog(QDialog):
    """Choose scan source when the staging area is empty."""

    MODE_FOLDER = "folder"
    MODE_PROJECT = "project"

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("自动分组整理 — 选择来源")
        self._mode = ""
        self._build_ui()

    def _build_ui(self) -> None:
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 16, 16, 16)
        lay.setSpacing(12)

        hint = QLabel(
            "暂存区没有照片。可以直接整理已有照片文件夹；"
            "只有需要项目化管理时才选择项目。"
        )
        hint.setWordWrap(True)
        lay.addWidget(hint)

        folder_btn = QPushButton("选择照片文件夹（默认）…")
        folder_btn.setObjectName("Outline")
        folder_btn.clicked.connect(self._choose_folder)
        lay.addWidget(folder_btn)

        project_btn = QPushButton("选择或创建项目…")
        project_btn.setObjectName("Outline")
        project_btn.clicked.connect(self._choose_project)
        lay.addWidget(project_btn)

        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        btns.rejected.connect(self.reject)
        lay.addWidget(btns)

    def _choose_folder(self) -> None:
        self._mode = self.MODE_FOLDER
        self.accept()

    def _choose_project(self) -> None:
        self._mode = self.MODE_PROJECT
        self.accept()

    def selected_source_mode(self) -> str:
        return self._mode


class _DrawerScrim(QWidget):
    """Dimmed backdrop behind a workbench drawer; click anywhere to dismiss."""

    def __init__(self, on_click, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("DrawerScrim")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._on_click = on_click
        self.hide()

    def mousePressEvent(self, event) -> None:  # noqa: N802 (Qt override)
        self._on_click()
