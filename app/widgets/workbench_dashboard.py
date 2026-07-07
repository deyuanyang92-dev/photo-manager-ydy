"""workbench_dashboard.py — 工作台顶部工作流仪表条。

从 app/views/workbench_view.py 拆出；workbench_view 仍以原名
`_WorkflowDashboard` re-export，保持旧测试和导入兼容。
"""
from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout

from app.config import icons


class _WorkflowDashboard(QFrame):
    """Compact workbench state strip inspired by project/pipeline desktops."""

    open_project_requested = pyqtSignal()
    add_photos_requested = pyqtSignal()
    new_specimen_requested = pyqtSignal()
    compose_requested = pyqtSignal()
    organise_requested = pyqtSignal()
    grouping_requested = pyqtSignal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("WorkflowDashboard")
        root = QHBoxLayout(self)
        root.setContentsMargins(12, 10, 12, 10)
        root.setSpacing(12)

        summary = QVBoxLayout()
        summary.setContentsMargins(0, 0, 0, 0)
        summary.setSpacing(2)
        eyebrow = QLabel("工作流")
        eyebrow.setObjectName("WorkflowDashEyebrow")
        self._next_title = QLabel("打开项目")
        self._next_title.setObjectName("WorkflowDashTitle")
        self._next_detail = QLabel("选择一个工作区后开始处理照片")
        self._next_detail.setObjectName("WorkflowDashDetail")
        self._next_detail.setWordWrap(True)
        summary.addWidget(eyebrow)
        summary.addWidget(self._next_title)
        summary.addWidget(self._next_detail)
        root.addLayout(summary, stretch=2)

        self._project_value = self._add_metric(root, "项目", "未打开")
        self._uid_value = self._add_metric(root, "编号", "无")
        self._pending_value = self._add_metric(root, "待处理", "JPG 0 / TIFF 0")
        self._archive_value = self._add_metric(root, "归档", "0")

        actions = QHBoxLayout()
        actions.setContentsMargins(0, 0, 0, 0)
        actions.setSpacing(6)
        root.addLayout(actions)

        self._open_btn = self._button("打开", "mdi6.folder-open-outline", "Outline")
        self._open_btn.clicked.connect(self.open_project_requested.emit)
        actions.addWidget(self._open_btn)

        self._add_btn = self._button("添加照片", "mdi6.image-plus-outline", "Outline")
        self._add_btn.clicked.connect(self.add_photos_requested.emit)
        actions.addWidget(self._add_btn)

        self._new_btn = self._button("新编号", "mdi6.plus", "Outline")
        self._new_btn.clicked.connect(self.new_specimen_requested.emit)
        actions.addWidget(self._new_btn)

        self._compose_btn = self._button("合成", "mdi6.layers-triple-outline", "Primary")
        self._compose_btn.clicked.connect(self.compose_requested.emit)
        actions.addWidget(self._compose_btn)

        self._organise_btn = self._button("整理", "mdi6.folder-zip-outline", "Outline")
        self._organise_btn.clicked.connect(self.organise_requested.emit)
        actions.addWidget(self._organise_btn)

        self._group_btn = self._button("分组", "mdi6.view-grid-plus-outline", "Ghost")
        self._group_btn.clicked.connect(self.grouping_requested.emit)
        actions.addWidget(self._group_btn)

        self.update_state(
            project_dir=None,
            active_uid=None,
            current_uid=None,
            scan_result=None,
        )

    def _add_metric(self, root: QHBoxLayout, label: str, value: str) -> QLabel:
        wrap = QFrame()
        wrap.setObjectName("WorkflowDashMetric")
        lay = QVBoxLayout(wrap)
        lay.setContentsMargins(10, 0, 10, 0)
        lay.setSpacing(2)
        label_w = QLabel(label)
        label_w.setObjectName("WorkflowDashMetricLabel")
        value_w = QLabel(value)
        value_w.setObjectName("WorkflowDashMetricValue")
        value_w.setMinimumWidth(74)
        value_w.setWordWrap(False)
        lay.addWidget(label_w)
        lay.addWidget(value_w)
        root.addWidget(wrap)
        return value_w

    def _button(self, text: str, glyph: str, object_name: str) -> QPushButton:
        btn = QPushButton(text)
        btn.setObjectName(object_name)
        btn.setFixedHeight(28)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        icons.set_button_icon(
            btn,
            glyph,
            color=icons.TONE_ON_ACCENT if object_name == "Primary" else icons.TONE_MUTED,
            size=14,
        )
        return btn

    def update_state(
        self,
        *,
        project_dir: str | None,
        active_uid: str | None,
        current_uid: str | None,
        scan_result,
    ) -> None:
        project_name = Path(project_dir).name if project_dir else ""
        jpg_count = len(getattr(scan_result, "jpg_files", []) or [])
        tiff_count = len(getattr(scan_result, "tiff_files", []) or [])
        pending_count = int(
            getattr(scan_result, "pending_count", jpg_count + tiff_count) or 0
        )
        archived_count = int(getattr(scan_result, "archived_jpg_count", 0) or 0)
        processed_tiff_count = int(getattr(scan_result, "processed_tiff_count", 0) or 0)

        self._project_value.setText(project_name or "未打开")
        if active_uid:
            self._uid_value.setText(f"激活 {self._short_uid(active_uid)}")
        elif current_uid:
            self._uid_value.setText(f"选中 {self._short_uid(current_uid)}")
        else:
            self._uid_value.setText("无")
        self._pending_value.setText(f"JPG {jpg_count} / TIFF {tiff_count}")
        self._archive_value.setText(f"JPG {archived_count} / TIFF {processed_tiff_count}")

        has_project = bool(project_dir)
        if not has_project:
            title = "打开项目"
            detail = "选择一个工作区后开始处理照片"
        elif pending_count <= 0:
            title = "等待新照片"
            detail = "可导入 JPG/TIF，或继续完善编号和标签"
        elif tiff_count and jpg_count:
            title = "整理 TIFF 与原片"
            detail = "选择一个 TIFF 和对应 JPG 后整理归档"
        elif tiff_count:
            title = "整理外部 TIFF"
            detail = "选择 TIFF，再选择对应 JPG 作为归档原片"
        else:
            title = "选择照片后合成"
            detail = "手选 JPG 可直接合成；激活编号只是默认归属"
        self._next_title.setText(title)
        self._next_detail.setText(detail)

        for btn in (self._add_btn, self._new_btn, self._group_btn):
            btn.setEnabled(has_project)
        self._compose_btn.setEnabled(has_project and jpg_count > 0)
        self._organise_btn.setEnabled(has_project and pending_count > 0)
        self._open_btn.setEnabled(True)

    @staticmethod
    def _short_uid(uid: str) -> str:
        text = str(uid or "").strip()
        if len(text) <= 22:
            return text or "无"
        return f"{text[:10]}...{text[-8:]}"
