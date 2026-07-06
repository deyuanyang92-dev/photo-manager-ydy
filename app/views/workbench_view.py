"""workbench_view.py — Daily-use workbench main view.

Integrates the five sub-widgets into a QSplitter three-column layout:

  Left   | Centre (top: monitor, bottom: grouping) | Right
  ────────────────────────────────────────────────────────
  Specimen │  Monitor panel (incoming-jpg / results)  │ Naming
  Sidebar  │  ──────────────────────────────────────  │ + Metadata
           │  Grouping panel (draft + composed)        │

The view wires up all inter-widget signals and drives the service layer:
  - on_activate(): scans the project via monitor_service and loads the
    last-active specimen.
  - Selecting a specimen: loads its grouping + metadata.
  - Activate/deactivate: activation_service (mutual exclusion + event log).
  - Compose: helicon_service (QProcess + QProgressDialog; graceful no-Helicon).
  - Organise: organize_service gate + archive_service.archive_group.

Oracle: docs/modules/workbench.md, monitor.md; web app.js workspace render.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import QEvent, Qt, QFileSystemWatcher, QTimer, QSize, pyqtSignal
from PyQt6.QtGui import QColor, QIcon, QKeySequence, QPainter, QPixmap, QShortcut
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QProgressDialog,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from app.workers.helicon_worker import HeliconWorker

from app.config import icons
from app.config.theme import TOKENS
from app.services.compose_workflow_service import (
    SelectedComposeTarget as _SelectedComposeTarget,
    detect_external_tiff_candidate,
    pending_tiff_paths,
    persist_composed_group,
    resolve_external_tiff_jpg_source,
)
from app.services.organize_workflow_service import (
    compose_batch_queue,
    inspect_organize_group,
    organize_batch_targets,
    plan_archive_worker,
    plan_organize_gate_check,
    prepare_existing_tiff_group,
)
from app.utils import ui
from app.utils.image_thumbnail import decode_image_thumbnail
from app.utils.path_utils import equivalent_paths
from app.views.base_view import BaseView
from app.widgets.grouping_panel import GroupingPanel
from app.widgets.helicon_params_panel import HeliconParamsPanel
from app.widgets.metadata_panel import MetadataPanel
from app.widgets.monitor_panel import MonitorPanel
from app.widgets.naming_panel import NamingPanel
from app.widgets.results_column import ResultsColumn
from app.widgets.taxon_card_panel import TaxonCardPanel
from app.widgets.specimen_sidebar import SpecimenSidebar


class _WorkflowNoticePanel(QFrame):
    """In-window task progress panel for long-running workbench operations."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._hidden_by_user = False
        self._collapsed = False
        self._task_key = ""
        self._state = "info"
        self.setObjectName("WorkflowNoticePanel")
        self.setMinimumWidth(360)
        self.setMaximumWidth(460)
        self.setFixedWidth(440)
        self.hide()
        self._auto_hide_timer = QTimer(self)
        self._auto_hide_timer.setSingleShot(True)
        self._auto_hide_timer.timeout.connect(self._auto_hide_finished_notice)
        self._launcher = QPushButton("任务", parent) if parent is not None else None
        if self._launcher is not None:
            self._launcher.setObjectName("WorkflowTaskLauncher")
            self._launcher.setFixedHeight(26)
            self._launcher.clicked.connect(self._restore_from_launcher)
            self._launcher.hide()

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 10, 12, 10)
        root.setSpacing(8)

        cap = QHBoxLayout()
        cap.setContentsMargins(0, 0, 0, 0)
        cap.setSpacing(8)
        heading = QLabel("任务进度")
        heading.setObjectName("WorkflowPanelHeading")
        cap.addWidget(heading)
        cap.addStretch()
        self._collapse_btn = QPushButton("收起")
        self._collapse_btn.setObjectName("Tiny")
        self._collapse_btn.setFixedHeight(22)
        self._collapse_btn.clicked.connect(self._toggle_collapsed)
        cap.addWidget(self._collapse_btn)
        self._hide_btn = QPushButton("隐藏")
        self._hide_btn.setObjectName("Tiny")
        self._hide_btn.setFixedHeight(22)
        self._hide_btn.clicked.connect(self._hide_current_task)
        cap.addWidget(self._hide_btn)
        root.addLayout(cap)

        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(10)
        self._stage = QLabel("进行中")
        self._stage.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._stage.setFixedWidth(58)
        self._stage.setObjectName("WorkflowDialogStage")
        head.addWidget(self._stage)

        title_box = QVBoxLayout()
        title_box.setContentsMargins(0, 0, 0, 0)
        title_box.setSpacing(2)
        self._title = QLabel("")
        self._title.setObjectName("WorkflowDialogTitle")
        self._detail = QLabel("")
        self._detail.setObjectName("WorkflowDialogDetail")
        self._detail.setWordWrap(True)
        title_box.addWidget(self._title)
        title_box.addWidget(self._detail)
        head.addLayout(title_box, 1)
        root.addLayout(head)

    def set_notice(
        self,
        title: str,
        detail: str = "",
        *,
        state: str = "busy",
        force_show: bool = False,
        task_key: str | None = None,
    ) -> None:
        key = str(task_key or "")
        new_task = False
        if key and key != self._task_key:
            self._task_key = key
            self._hidden_by_user = False
            self._collapsed = False
            new_task = True
        if force_show:
            self._hidden_by_user = False
            self._collapsed = False
        state = state if state in {"busy", "success", "error", "info"} else "info"
        self._state = state
        bg, fg, border, stage = {
            "busy": ("#f7fbff", "#0b5cad", "#90cdf4", "进行中"),
            "success": ("#f6fef9", TOKENS["success"], "#86efac", "完成"),
            "error": ("#fffafa", TOKENS["danger"], "#fca5a5", "失败"),
            "info": ("#f8fafc", TOKENS["accent"], TOKENS["border_medium"], "提示"),
        }[state]
        self.setStyleSheet(
            "QFrame#WorkflowNoticePanel {"
            f" background:{bg}; border:1px solid {border}; border-radius:8px;"
            "}"
            "QLabel#WorkflowPanelHeading {"
            f" color:{TOKENS['muted']}; font-size:11px; font-weight:700;"
            "}"
            "QLabel#WorkflowDialogStage {"
            f" background:{fg}; color:white; border-radius:4px;"
            " padding:3px 6px; font-size:11px; font-weight:700;"
            "}"
            "QLabel#WorkflowDialogTitle {"
            f" color:{TOKENS['text']}; font-size:13px; font-weight:700;"
            "}"
            "QLabel#WorkflowDialogDetail {"
            f" color:{TOKENS['muted']}; font-size:12px;"
            "}"
        )
        self._stage.setText(stage)
        self._title.setText(str(title or "").strip())
        self._detail.setText(str(detail or "").strip())
        self._apply_collapsed()
        self._sync_launcher_style(fg, stage)
        if self._hidden_by_user:
            self._show_launcher()
        elif force_show or new_task or not self.isVisible():
            self._show_near_parent()
        else:
            self.reposition()
        if state in {"success", "info"}:
            self._auto_hide_timer.start(6500)
        else:
            self._auto_hide_timer.stop()

    def notice_text(self) -> tuple[str, str, str]:
        return self._stage.text(), self._title.text(), self._detail.text()

    def _apply_collapsed(self) -> None:
        has_detail = bool(self._detail.text().strip())
        self._detail.setVisible(has_detail and not self._collapsed)
        self._collapse_btn.setEnabled(has_detail)
        self._collapse_btn.setText("展开" if self._collapsed else "收起")
        self.adjustSize()

    def _toggle_collapsed(self) -> None:
        self._collapsed = not self._collapsed
        self._apply_collapsed()

    def _hide_current_task(self) -> None:
        self._hidden_by_user = True
        self.hide()
        self._show_launcher()

    def _show_near_parent(self) -> None:
        self._hide_launcher()
        self.reposition()
        self.show()
        self.raise_()

    def reposition(self) -> None:
        parent = self.parentWidget()
        if parent is None:
            return
        try:
            self.adjustSize()
            width = min(max(self.width(), 360), min(440, max(260, parent.width() - 48)))
            self.setFixedWidth(width)
            self.adjustSize()
            x = max(12, parent.width() - self.width() - 24)
            y = max(12, parent.height() - self.height() - 28)
            self.move(x, y)
            if self._launcher is not None and self._launcher.isVisible():
                self._place_launcher()
        except Exception:
            pass

    def _sync_launcher_style(self, color: str, stage: str) -> None:
        if self._launcher is None:
            return
        self._launcher.setText(f"任务 · {stage}")
        self._launcher.setStyleSheet(
            "QPushButton#WorkflowTaskLauncher {"
            f" background:{color}; color:white; border:0; border-radius:13px;"
            " padding:3px 12px; font-size:12px; font-weight:700;"
            "}"
        )

    def _show_launcher(self) -> None:
        if self._launcher is None:
            return
        self._place_launcher()
        self._launcher.show()
        self._launcher.raise_()

    def _hide_launcher(self) -> None:
        if self._launcher is not None:
            self._launcher.hide()

    def _place_launcher(self) -> None:
        if self._launcher is None:
            return
        parent = self.parentWidget()
        if parent is None:
            return
        self._launcher.adjustSize()
        x = max(12, parent.width() - self._launcher.width() - 24)
        y = max(12, parent.height() - self._launcher.height() - 28)
        self._launcher.move(x, y)

    def _restore_from_launcher(self) -> None:
        self._hidden_by_user = False
        self._show_near_parent()

    def _auto_hide_finished_notice(self) -> None:
        if self._state not in {"success", "info"}:
            return
        self.hide()
        self._hide_launcher()
        self._hidden_by_user = False

    def closeEvent(self, event) -> None:  # noqa: N802
        self._hide_current_task()
        event.ignore()


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


class _ComposeOrganiseProgressDialog(QWidget):
    """Compose-and-organise progress card with a Geneious-style background entry."""

    cancel_requested = pyqtSignal(str)

    def __init__(self, host: QWidget, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent or host)
        self._host = host
        self._hidden_by_user = False
        self._compact = False
        self._task_key = ""
        self._last_title = ""
        self._overall_stage = "提示"
        self._primary_action_mode = "close"
        self._launcher = QPushButton("合成+整理 · 提示", host)
        self._launcher.setObjectName("ComposeOrganiseLauncher")
        self._launcher.setFixedHeight(30)
        self._launcher.setCursor(Qt.CursorShape.PointingHandCursor)
        self._launcher.clicked.connect(self._restore_from_launcher)
        self._launcher.hide()
        self.setObjectName("ComposeOrganiseProgressDialog")
        self._auto_hide_timer = QTimer(self)
        self._auto_hide_timer.setSingleShot(True)
        self._auto_hide_timer.timeout.connect(self._auto_hide_finished_notice)
        self.setMinimumWidth(500)
        self.resize(540, 250)

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(0)

        card = QFrame()
        card.setObjectName("ComposeOrganiseCard")
        root.addWidget(card)

        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(18, 16, 18, 16)
        card_layout.setSpacing(14)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(10)
        mark = QLabel("合")
        mark.setAlignment(Qt.AlignmentFlag.AlignCenter)
        mark.setFixedSize(34, 34)
        mark.setObjectName("ComposeOrganiseMark")
        header.addWidget(mark)

        title_box = QVBoxLayout()
        title_box.setContentsMargins(0, 0, 0, 0)
        title_box.setSpacing(2)
        title = QLabel("合成+整理")
        title.setObjectName("ComposeOrganiseTitle")
        self._subtitle = QLabel("等待开始")
        self._subtitle.setObjectName("ComposeOrganiseSubtitle")
        title_box.addWidget(title)
        title_box.addWidget(self._subtitle)
        header.addLayout(title_box, 1)

        self._overall_badge = QLabel("提示")
        self._overall_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._overall_badge.setObjectName("ComposeOrganiseOverallBadge")
        header.addWidget(self._overall_badge)

        self._compact_btn = QToolButton()
        self._compact_btn.setObjectName("ComposeOrganiseToolButton")
        self._compact_btn.setText("-")
        self._compact_btn.setToolTip("转入后台")
        self._compact_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._compact_btn.setFixedSize(34, 34)
        icons.set_button_icon(
            self._compact_btn, "mdi6.window-minimize", TOKENS["muted"], size=14
        )
        self._compact_btn.clicked.connect(self._minimize_to_background)
        header.addWidget(self._compact_btn)

        self._hide_btn = QToolButton()
        self._hide_btn.setObjectName("ComposeOrganiseToolButton")
        self._hide_btn.setText("x")
        self._hide_btn.setToolTip("隐藏窗口")
        self._hide_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._hide_btn.setFixedSize(34, 34)
        icons.set_button_icon(
            self._hide_btn, "mdi6.close", TOKENS["muted"], size=14
        )
        self._hide_btn.clicked.connect(self._handle_hide_clicked)
        header.addWidget(self._hide_btn)
        card_layout.addLayout(header)

        self._stage_panel = QFrame()
        self._stage_panel.setObjectName("ComposeOrganiseStagePanel")
        stage_layout = QVBoxLayout(self._stage_panel)
        stage_layout.setContentsMargins(12, 10, 12, 10)
        stage_layout.setSpacing(8)
        self._compose_row, self._compose_state, self._compose_hint = self._stage_row(
            "1", "合成 TIFF"
        )
        self._organise_row, self._organise_state, self._organise_hint = self._stage_row(
            "2", "整理归档"
        )
        self._stage_hint_by_label = {
            self._compose_state: (
                "等待任务调度",
                "正在生成高景深 TIFF",
                "TIFF 已生成",
                "TIFF 未生成",
                "未开始合成",
            ),
            self._organise_state: (
                "等待 TIFF 完成",
                "正在打包 JPG 并登记 ZIP",
                "ZIP 已归档",
                "整理未完成",
                "未开始整理",
            ),
        }
        stage_layout.addWidget(self._compose_row)
        stage_layout.addWidget(self._organise_row)
        card_layout.addWidget(self._stage_panel)

        self._detail = QLabel("")
        self._detail.setObjectName("ComposeOrganiseDetail")
        self._detail.setWordWrap(True)
        self._detail.setMinimumHeight(40)
        card_layout.addWidget(self._detail)

        action_row = QHBoxLayout()
        action_row.setContentsMargins(0, 0, 0, 0)
        action_row.setSpacing(8)
        self._cancel_action = QPushButton("取消任务")
        self._cancel_action.setObjectName("ComposeOrganiseCancelButton")
        self._cancel_action.setProperty("role", "danger")
        self._cancel_action.setCursor(Qt.CursorShape.PointingHandCursor)
        self._cancel_action.clicked.connect(self._request_cancel)
        action_row.addWidget(self._cancel_action)
        self._ok_action = QPushButton("确定")
        self._ok_action.setObjectName("ComposeOrganiseOkButton")
        self._ok_action.setCursor(Qt.CursorShape.PointingHandCursor)
        self._ok_action.clicked.connect(self._dismiss_finished_notice)
        self._ok_action.hide()
        action_row.addWidget(self._ok_action)
        action_row.addStretch()
        self._compact_action = QPushButton("收起详情")
        self._compact_action.setObjectName("ComposeOrganiseActionButton")
        self._compact_action.setCursor(Qt.CursorShape.PointingHandCursor)
        self._compact_action.clicked.connect(self._toggle_compact)
        action_row.addWidget(self._compact_action)
        self._hide_action = QPushButton("隐藏窗口")
        self._hide_action.setObjectName("ComposeOrganiseActionButton")
        self._hide_action.setCursor(Qt.CursorShape.PointingHandCursor)
        self._hide_action.clicked.connect(self._handle_hide_clicked)
        action_row.addWidget(self._hide_action)
        card_layout.addLayout(action_row)

        self.setStyleSheet(
            f"QWidget#ComposeOrganiseProgressDialog {{ background:transparent; }}"
            "QFrame#ComposeOrganiseCard {"
            f" background:{TOKENS['panel']};"
            f" border:1px solid {TOKENS['border_medium']};"
            " border-radius:12px;"
            "}"
            "QLabel#ComposeOrganiseMark {"
            f" background:{TOKENS['accent']}; color:white;"
            " border-radius:8px; font-size:15px; font-weight:800;"
            "}"
            "QLabel#ComposeOrganiseTitle {"
            f" color:{TOKENS['text']}; font-size:16px; font-weight:800;"
            "}"
            "QLabel#ComposeOrganiseSubtitle {"
            f" color:{TOKENS['muted']}; font-size:12px;"
            "}"
            "QLabel#ComposeOrganiseOverallBadge {"
            " background:transparent; border:0; padding:2px 0;"
            " font-size:12px; font-weight:800;"
            "}"
            "QToolButton#ComposeOrganiseToolButton {"
            f" background:{TOKENS['panel_2']};"
            f" border:1px solid {TOKENS['border']};"
            " border-radius:8px; min-width:28px; min-height:28px;"
            f" color:{TOKENS['muted']}; font-size:14px; font-weight:800;"
            "}"
            "QToolButton#ComposeOrganiseToolButton:hover {"
            f" background:{TOKENS['panel_inset']};"
            f" border-color:{TOKENS['border_medium']};"
            "}"
            "QPushButton#ComposeOrganiseActionButton {"
            f" background:{TOKENS['panel_2']};"
            f" color:{TOKENS['text']};"
            f" border:1px solid {TOKENS['border']};"
            " border-radius:8px; padding:6px 12px;"
            " font-size:12px; font-weight:700;"
            "}"
            "QPushButton#ComposeOrganiseActionButton:hover {"
            f" background:{TOKENS['panel_inset']};"
            f" border-color:{TOKENS['border_medium']};"
            "}"
            "QPushButton#ComposeOrganiseCancelButton[role=\"danger\"] {"
            " background:#fff7ed;"
            f" color:{TOKENS['danger']};"
            " border:1px solid #fed7aa;"
            " border-radius:8px; padding:6px 12px;"
            " font-size:12px; font-weight:800;"
            "}"
            "QPushButton#ComposeOrganiseCancelButton[role=\"danger\"]:hover {"
            " background:#ffedd5; border-color:#fdba74;"
            "}"
            "QPushButton#ComposeOrganiseCancelButton[role=\"neutral\"] {"
            f" background:{TOKENS['panel_2']};"
            f" color:{TOKENS['text']};"
            f" border:1px solid {TOKENS['border']};"
            " border-radius:8px; padding:6px 12px;"
            " font-size:12px; font-weight:800;"
            "}"
            "QPushButton#ComposeOrganiseCancelButton[role=\"neutral\"]:hover {"
            f" background:{TOKENS['panel_inset']};"
            f" border-color:{TOKENS['border_medium']};"
            "}"
            "QPushButton#ComposeOrganiseCancelButton:disabled {"
            f" color:{TOKENS['muted']}; background:{TOKENS['panel_2']};"
            f" border-color:{TOKENS['border']};"
            "}"
            "QPushButton#ComposeOrganiseOkButton {"
            f" background:{TOKENS['accent']}; color:white;"
            " border:0; border-radius:8px; padding:6px 16px;"
            " font-size:12px; font-weight:800;"
            "}"
            "QPushButton#ComposeOrganiseOkButton:hover {"
            f" background:{TOKENS['accent_hover']};"
            "}"
            "QFrame#ComposeOrganiseStagePanel {"
            f" background:{TOKENS['panel_2']};"
            f" border:1px solid {TOKENS['border']};"
            " border-radius:10px;"
            "}"
            "QFrame#ComposeOrganiseStageRow {"
            " background:transparent; border:0;"
            "}"
            "QLabel#ComposeOrganiseStepName {"
            f" color:{TOKENS['text']}; font-size:13px; font-weight:700;"
            "}"
            "QLabel#ComposeOrganiseStepHint {"
            f" color:{TOKENS['muted']}; font-size:11px;"
            "}"
            "QLabel#ComposeOrganiseDetail {"
            f" color:{TOKENS['muted']};"
            f" background:{TOKENS['panel_inset']};"
            f" border:1px solid {TOKENS['border']};"
            " border-radius:8px; padding:8px 10px; font-size:12px;"
            "}"
        )

        self._set_stage(self._compose_state, "等待")
        self._set_stage(self._organise_state, "等待")
        self._set_overall_badge("提示")

    def _stage_row(self, number: str, label: str) -> tuple[QFrame, QLabel, QLabel]:
        row_frame = QFrame()
        row_frame.setObjectName("ComposeOrganiseStageRow")
        row = QHBoxLayout(row_frame)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(10)
        index = QLabel(number)
        index.setAlignment(Qt.AlignmentFlag.AlignCenter)
        index.setFixedSize(26, 26)
        index.setObjectName("ComposeOrganiseStepIndex")
        index.setStyleSheet(
            f"background:{TOKENS['panel_inset']}; color:{TOKENS['muted']};"
            f" border:1px solid {TOKENS['border_medium']}; border-radius:12px;"
            " font-size:12px; font-weight:700;"
        )
        text_box = QVBoxLayout()
        text_box.setContentsMargins(0, 0, 0, 0)
        text_box.setSpacing(1)
        name = QLabel(label)
        name.setObjectName("ComposeOrganiseStepName")
        hint = QLabel("等待任务调度" if number == "1" else "等待 TIFF 完成")
        hint.setObjectName("ComposeOrganiseStepHint")
        text_box.addWidget(name)
        text_box.addWidget(hint)
        state = QLabel("等待")
        state.setAlignment(Qt.AlignmentFlag.AlignCenter)
        state.setFixedWidth(68)
        row.addWidget(index)
        row.addLayout(text_box, 1)
        row.addWidget(state)
        return row_frame, state, hint

    def set_notice(
        self,
        title: str,
        detail: str = "",
        *,
        state: str = "busy",
        force_show: bool = False,
        task_key: str | None = None,
    ) -> None:
        key = str(task_key or "")
        if key and key != self._task_key:
            self._task_key = key
            self._hidden_by_user = False
            self._set_stage(self._compose_state, "等待")
            self._set_stage(self._organise_state, "等待")
        if force_show:
            self._hidden_by_user = False

        text = str(detail or "").strip()
        state = state if state in {"busy", "success", "error", "info"} else "info"
        title_text = str(title or "")
        self._last_title = title_text
        self._overall_stage = {
            "busy": "进行中",
            "success": "完成",
            "error": "失败",
            "info": "提示",
        }[state]
        self._set_overall_badge(self._overall_stage)
        cancelling = "正在取消" in title_text
        if state == "busy":
            self._sync_action_buttons("cancelling" if cancelling else "cancel")
        else:
            self._sync_action_buttons("close")
        finish_state = state in {"success", "error", "info"}
        self._hide_btn.setToolTip("关闭窗口" if finish_state else "隐藏窗口")
        self._hide_action.setText("关闭窗口" if finish_state else "隐藏窗口")
        self._subtitle.setText(title_text.replace("合成+整理：", "") or self._overall_stage)
        if state == "success":
            self._set_stage(self._compose_state, "完成")
            self._set_stage(self._organise_state, "完成")
        elif state == "error":
            if "合成阶段" in text or "没有生成 TIFF" in text:
                self._set_stage(self._compose_state, "失败")
                self._set_stage(self._organise_state, "未启动")
            else:
                self._set_stage(self._compose_state, "完成")
                self._set_stage(self._organise_state, "失败")
        elif "正在整理" in title_text:
            self._set_stage(self._compose_state, "完成")
            self._set_stage(self._organise_state, "进行中")
        elif "正在合成" in title_text:
            self._set_stage(self._compose_state, "进行中")
            self._set_stage(self._organise_state, "等待")
        self._detail.setText(text)
        if finish_state:
            self._auto_hide_timer.start(8000)
        else:
            self._auto_hide_timer.stop()
        if not self._hidden_by_user:
            self._show_near_parent()

    def notice_text(self) -> tuple[str, str, str]:
        return (
            self._overall_stage,
            self._last_title,
            self._detail.text(),
        )

    def stage_texts(self) -> tuple[str, str]:
        return self._compose_state.text(), self._organise_state.text()

    def _set_stage(self, label: QLabel, state: str) -> None:
        colors = {
            "等待": (TOKENS["muted"], "#f4f6f8", TOKENS["border_medium"]),
            "未启动": (TOKENS["muted"], "#f4f6f8", TOKENS["border_medium"]),
            "进行中": (TOKENS["accent"], "#e8f7f4", "#8bd3c7"),
            "完成": (TOKENS["success"], "#ecfdf3", "#86efac"),
            "失败": (TOKENS["danger"], "#fff1f0", "#fca5a5"),
        }
        fg, bg, border = colors.get(state, colors["等待"])
        label.setText(state)
        label.setStyleSheet(
            f"background:{bg}; color:{fg}; border:1px solid {border};"
            " border-radius:10px; padding:4px 8px; font-size:12px; font-weight:800;"
        )
        hint = getattr(self, "_stage_hint_by_label", {}).get(label)
        if hint is not None:
            waiting, busy, done, failed, not_started = hint
            label_hint = {
                "等待": waiting,
                "进行中": busy,
                "完成": done,
                "失败": failed,
                "未启动": not_started,
            }.get(state, waiting)
            if label is self._compose_state:
                self._compose_hint.setText(label_hint)
            elif label is self._organise_state:
                self._organise_hint.setText(label_hint)

    def _set_overall_badge(self, stage: str) -> None:
        colors = {
            "进行中": (TOKENS["accent"], "#e8f7f4", "#8bd3c7"),
            "完成": (TOKENS["success"], "#ecfdf3", "#86efac"),
            "失败": (TOKENS["danger"], "#fff1f0", "#fca5a5"),
            "提示": (TOKENS["muted"], "#f4f6f8", TOKENS["border_medium"]),
        }
        fg, bg, border = colors.get(stage, colors["提示"])
        self._overall_badge.setText(f"状态：{stage}")
        self._overall_badge.setStyleSheet(
            f"background:transparent; color:{fg}; border:0;"
            " padding:2px 0; font-size:12px; font-weight:800;"
        )
        self._sync_launcher_style(fg, stage)

    def _sync_action_buttons(self, mode: str) -> None:
        mode = mode if mode in {"cancel", "cancelling", "close"} else "close"
        self._primary_action_mode = mode
        if mode == "cancel":
            self._cancel_action.setText("取消任务")
            self._cancel_action.setEnabled(True)
            self._cancel_action.setProperty("role", "danger")
            self._cancel_action.show()
            self._ok_action.hide()
        elif mode == "cancelling":
            self._cancel_action.setText("正在取消...")
            self._cancel_action.setEnabled(False)
            self._cancel_action.setProperty("role", "neutral")
            self._cancel_action.show()
            self._ok_action.hide()
        else:
            self._cancel_action.hide()
            self._ok_action.show()
            self._ok_action.setEnabled(True)
        self._cancel_action.style().unpolish(self._cancel_action)
        self._cancel_action.style().polish(self._cancel_action)
        self._cancel_action.update()

    def _toggle_compact(self) -> None:
        self._compact = not self._compact
        self._detail.setVisible(not self._compact)
        self._compact_action.setText("展开详情" if self._compact else "收起详情")
        self.adjustSize()

    def _minimize_to_background(self) -> None:
        self._hide_for_current_task()

    def show(self) -> None:  # noqa: N802
        self._ensure_on_top()

    def hide(self) -> None:  # noqa: N802
        super().hide()

    def _ensure_on_top(self) -> None:
        self._reposition_panel()
        super().show()
        self.raise_()
        if not self._is_running():
            if self._ok_action.isVisible():
                self._ok_action.setFocus(Qt.FocusReason.OtherFocusReason)
            else:
                self._cancel_action.setFocus(Qt.FocusReason.OtherFocusReason)

    def _reposition_panel(self) -> None:
        host = self._host
        if host is None:
            return
        self.adjustSize()
        x = max(12, (host.width() - self.width()) // 2)
        y = max(12, (host.height() - self.height()) // 2)
        self.move(x, y)

    def _show_near_parent(self) -> None:
        if self._hidden_by_user:
            self._show_launcher()
            return
        host = self._host
        if host is not None:
            for attr in ("_settings_scrim", "_collab_scrim"):
                scrim = getattr(host, attr, None)
                if scrim is not None and scrim.isVisible():
                    scrim.hide()
        self.adjustSize()
        self._hide_launcher()
        self._ensure_on_top()

    def _hide_for_current_task(self) -> None:
        self._hidden_by_user = True
        self.hide()
        self._show_launcher()

    def _dismiss_finished_notice(self) -> None:
        self._auto_hide_timer.stop()
        self._hidden_by_user = False
        self.hide()
        self._hide_launcher()

    def _auto_hide_finished_notice(self) -> None:
        if not self._is_running():
            self._dismiss_finished_notice()

    def _is_running(self) -> bool:
        return self._primary_action_mode in {"cancel", "cancelling"}

    def _handle_hide_clicked(self) -> None:
        if self._is_running():
            self._hide_for_current_task()
        else:
            self._dismiss_finished_notice()

    def closeEvent(self, event) -> None:  # noqa: N802
        if self._is_running():
            self._hide_for_current_task()
            event.ignore()
        else:
            self._dismiss_finished_notice()
            event.accept()

    def reject(self) -> None:
        self._handle_hide_clicked()

    def _request_cancel(self) -> None:
        self._cancel_action.setEnabled(False)
        self._cancel_action.setText("正在取消...")
        self._cancel_action.setProperty("role", "neutral")
        self._cancel_action.style().unpolish(self._cancel_action)
        self._cancel_action.style().polish(self._cancel_action)
        self._detail.setText("正在取消当前任务；已写入但未完成的 ZIP 会被清理，JPG 不会删除。")
        self.cancel_requested.emit(str(self._task_key or ""))

    def _sync_launcher_style(self, color: str, stage: str) -> None:
        label = "任务 (1 进行中)" if stage == "进行中" else f"任务 · {stage}"
        self._launcher.setText(label)
        self._launcher.setToolTip("点击查看后台任务")
        self._launcher.setStyleSheet(
            "QPushButton#ComposeOrganiseLauncher {"
            f" background:{color}; color:white; border:0; border-radius:15px;"
            " padding:4px 14px; font-size:12px; font-weight:800;"
            "}"
        )

    def _show_launcher(self) -> None:
        self._place_launcher()
        self._launcher.show()
        self._launcher.raise_()

    def _hide_launcher(self) -> None:
        self._launcher.hide()

    def _place_launcher(self) -> None:
        host = self._host
        if host is None:
            return
        self._launcher.adjustSize()
        x = max(12, host.width() - self._launcher.width() - 24)
        y = max(12, host.height() - self._launcher.height() - 28)
        self._launcher.move(x, y)

    def _restore_from_launcher(self) -> None:
        self._hidden_by_user = False
        self._hide_launcher()
        self._show_near_parent()

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.key() in (
            Qt.Key.Key_Escape,
            Qt.Key.Key_Return,
            Qt.Key.Key_Enter,
        ) and not self._is_running():
            self._dismiss_finished_notice()
            event.accept()
            return
        super().keyPressEvent(event)


class _BatchResultDialog(QDialog):
    """Batch retroactive archive result detail dialog.

    Shows a per-file table with status, size, and error column.
    Replaces the plain QMessageBox.information summary after batch archiving.
    """

    def __init__(self, results: list, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("批量归档结果")
        self.resize(700, 400)
        layout = QVBoxLayout(self)

        ok_count = sum(1 for r in results if r.ok)
        fail_count = len(results) - ok_count
        self._summary = QLabel(f"✓ {ok_count} 成功  ✗ {fail_count} 失败")
        layout.addWidget(self._summary)

        self._table = QTableWidget(len(results), 4)
        self._table.setHorizontalHeaderLabels(["文件名", "状态", "大小", "错误"])
        self._table.horizontalHeader().setStretchLastSection(True)
        for i, r in enumerate(results):
            self._table.setItem(i, 0, QTableWidgetItem(r.name))
            status_item = QTableWidgetItem("✓" if r.ok else "✗")
            status_item.setForeground(QColor("green" if r.ok else "red"))
            self._table.setItem(i, 1, status_item)
            size_str = f"{r.size_bytes // 1024} KB" if r.size_bytes else "-"
            self._table.setItem(i, 2, QTableWidgetItem(size_str))
            self._table.setItem(i, 3, QTableWidgetItem(r.error or ""))
        layout.addWidget(self._table)

        btn = QPushButton("关闭")
        btn.clicked.connect(self.accept)
        layout.addWidget(btn)


class _RnaQueueDialog(QDialog):
    """RNAlater sheet queue preview with explicit print/clear actions."""

    def __init__(self, uids: list[str], job: dict, grid_opts: dict, parent=None) -> None:
        super().__init__(parent)
        self.action: str = ""
        self.setWindowTitle("RNAlater 合版队列")
        self.resize(880, 620)

        root = QHBoxLayout(self)
        left = QVBoxLayout()
        root.addLayout(left, 0)

        title = QLabel(f"待打印 RNAlater 标签：{len(uids)}")
        title.setStyleSheet("font-size:15px;font-weight:700;")
        left.addWidget(title)

        self._list = QListWidget()
        self._list.setMinimumWidth(300)
        for uid in uids:
            self._list.addItem(uid)
        left.addWidget(self._list, 1)

        hint = QLabel("确认排版后打印；清空只会移出待打印队列，不删除标本记录。")
        hint.setWordWrap(True)
        hint.setObjectName("MutedSmall")
        left.addWidget(hint)

        btn_row = QHBoxLayout()
        print_btn = QPushButton("打印合版")
        print_btn.setObjectName("Primary")
        clear_btn = QPushButton("清空队列")
        clear_btn.setObjectName("Outline")
        close_btn = QPushButton("关闭")
        print_btn.clicked.connect(lambda: self._finish("print"))
        clear_btn.clicked.connect(lambda: self._finish("clear"))
        close_btn.clicked.connect(self.reject)
        btn_row.addWidget(print_btn)
        btn_row.addWidget(clear_btn)
        btn_row.addWidget(close_btn)
        left.addLayout(btn_row)

        preview_box = QVBoxLayout()
        root.addLayout(preview_box, 1)
        pv_title = QLabel("合版预览")
        pv_title.setStyleSheet("font-size:13px;font-weight:700;")
        preview_box.addWidget(pv_title)
        self._preview = QLabel()
        self._preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._preview.setMinimumSize(500, 560)
        self._preview.setStyleSheet("background:#ffffff;border:1px solid #c8d0d8;")
        preview_box.addWidget(self._preview, 1)

        self._render_preview(job, grid_opts)

    def _finish(self, action: str) -> None:
        self.action = action
        self.accept()

    def _render_preview(self, job: dict, grid_opts: dict) -> None:
        from app.utils.label_sheet import compute_sheet_geometry, paint_sheet_page

        w, h = 520, 560
        pm = QPixmap(w, h)
        pm.fill(QColor("#f3f6f8"))
        painter = QPainter(pm)
        try:
            paper_type = job.get("paperType") or "a4"
            geom = compute_sheet_geometry(
                job.get("dims") or {},
                paper_type,
                job.get("paper"),
                grid_opts,
                w,
                h,
            )
            info = paint_sheet_page(
                painter,
                job,
                grid_opts,
                0,
                geom,
                cut_marks=bool(grid_opts.get("cutMarks", True)),
            )
            painter.setPen(QColor("#475569"))
            painter.drawText(
                14,
                h - 14,
                f"{paper_type.upper()} · 每页 {info.get('per_page', 0)} 个 · 共 {info.get('total_pages', 1)} 页",
            )
        finally:
            painter.end()
        self._preview.setPixmap(pm)


def _free_compose_output_name(incoming_dir: str, user_name: Optional[str]) -> str:
    """Return a unique output TIFF name for free-compose.

    If user_name is given, sanitize and use it.
    Otherwise auto-generate "自由合成-N.tif" incrementing N until no conflict.
    Oracle: app.js freeComposeSelected(), auto-naming "自由合成-N".
    """
    from app.services.compose_workflow_service import free_compose_output_name
    return free_compose_output_name(incoming_dir, user_name)


class _ScaledImagePreview(QLabel):
    """Scaled image label for the compose result preview."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._source_pixmap: Optional[QPixmap] = None
        self._scroll_area: Optional[QScrollArea] = None
        self._fit_to_window = True
        self._zoom_percent = 100
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMinimumSize(1, 1)
        self.setWordWrap(True)
        self.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

    def set_scroll_area(self, scroll_area: QScrollArea) -> None:
        self._scroll_area = scroll_area
        scroll_area.viewport().installEventFilter(self)

    def set_preview_pixmap(self, pixmap: Optional[QPixmap]) -> None:
        if pixmap is None or pixmap.isNull():
            self._source_pixmap = None
            self.setPixmap(QPixmap())
            return
        self._source_pixmap = QPixmap(pixmap)
        self._fit_to_window = True
        self._refresh_scaled_pixmap()

    def set_placeholder(self, text: str) -> None:
        self._source_pixmap = None
        self.setPixmap(QPixmap())
        self.setText(text)

    def source_pixmap(self) -> Optional[QPixmap]:
        if self._source_pixmap is None:
            return None
        return QPixmap(self._source_pixmap)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        if self._fit_to_window:
            self._refresh_scaled_pixmap()

    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        if (
            self._scroll_area is not None
            and obj is self._scroll_area.viewport()
            and event.type() == QEvent.Type.Resize
            and self._fit_to_window
        ):
            self._refresh_scaled_pixmap()
        if (
            event.type() == QEvent.Type.Wheel
            and event.modifiers() & Qt.KeyboardModifier.ControlModifier
        ):
            self.zoom_by_wheel_delta(event.angleDelta().y())
            event.accept()
            return True
        return super().eventFilter(obj, event)

    def wheelEvent(self, event) -> None:  # noqa: N802
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.zoom_by_wheel_delta(event.angleDelta().y())
            event.accept()
            return
        super().wheelEvent(event)

    def zoom_by_wheel_delta(self, delta: int) -> None:
        if delta == 0:
            return
        steps = max(1, abs(delta) // 120)
        direction = 1 if delta > 0 else -1
        self.set_zoom_percent(self._zoom_percent + direction * steps * 10)

    def set_zoom_percent(self, percent: int) -> None:
        if self._source_pixmap is None or self._source_pixmap.isNull():
            return
        self._fit_to_window = False
        self._zoom_percent = max(25, min(400, int(percent)))
        self._refresh_scaled_pixmap()

    def fit_to_window(self) -> None:
        self._fit_to_window = True
        self._refresh_scaled_pixmap()

    def actual_size(self) -> None:
        self.set_zoom_percent(100)

    def _refresh_scaled_pixmap(self) -> None:
        if self._source_pixmap is None or self._source_pixmap.isNull():
            return
        source_w = max(1, self._source_pixmap.width())
        source_h = max(1, self._source_pixmap.height())
        if self._fit_to_window:
            if self._scroll_area is not None:
                area = self._scroll_area.viewport().size()
                target_w = max(1, area.width() - 24)
                target_h = max(1, area.height() - 24)
            else:
                target_w = max(1, self.width() - 24)
                target_h = max(1, self.height() - 24)
            scaled = self._source_pixmap.scaled(
                target_w,
                target_h,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            label_w = max(target_w, scaled.width())
            label_h = max(target_h, scaled.height())
        else:
            target_w = max(1, int(source_w * self._zoom_percent / 100))
            target_h = max(1, int(source_h * self._zoom_percent / 100))
            scaled = self._source_pixmap.scaled(
                target_w,
                target_h,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            label_w = scaled.width()
            label_h = scaled.height()
        self.setText("")
        self.setFixedSize(max(1, label_w), max(1, label_h))
        self.setPixmap(scaled)


class _ComposeWorkbenchDialog(QDialog):
    """Post-compose preview workspace.

    Mirrors the web compose page at a desktop scale: left source JPG checklist,
    center TIFF preview/status, right Helicon params, footer save/cancel/recompose.
    """

    ACTION_SAVE = "save"
    ACTION_CANCEL = "cancel"
    ACTION_RECOMPOSE = "recompose"

    def __init__(
        self,
        jpg_paths: list[str],
        tiff_path: str,
        params: dict,
        *,
        angle_label: str = "",
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._jpg_paths = list(jpg_paths)
        self._tiff_path = tiff_path
        self._action = self.ACTION_CANCEL
        self._checks: list[tuple[QCheckBox, str]] = []
        self._params_panel = HeliconParamsPanel()
        self._params_panel.set_params(params)
        self._shortcuts: list[QShortcut] = []
        self.setWindowTitle("合成工作台")
        self.setMinimumSize(920, 560)
        self._build_ui(angle_label)
        self._install_shortcuts()

    def _build_ui(self, angle_label: str) -> None:
        t = TOKENS
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 14)
        root.setSpacing(12)

        head = QHBoxLayout()
        title = QLabel("合成工作台")
        title.setStyleSheet(f"font-size: 17px; font-weight: 700; color: {t['text']};")
        head.addWidget(title)
        if angle_label:
            badge = QLabel(angle_label)
            badge.setStyleSheet(
                f"color:{t['accent']}; border:1px solid {t['accent_glow']};"
                " border-radius:5px; padding:2px 8px; font-size:12px;"
            )
            head.addWidget(badge)
        fname = QLabel(Path(self._tiff_path).name)
        fname.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        fname.setStyleSheet(f"color:{t['muted']}; font-size:12px;")
        head.addWidget(fname, 1)
        root.addLayout(head)

        body = QSplitter(Qt.Orientation.Horizontal)
        body.setChildrenCollapsible(False)
        body.setHandleWidth(12)

        sources = QFrame()
        sources.setObjectName("Panel")
        src_lay = QVBoxLayout(sources)
        src_lay.setContentsMargins(12, 12, 12, 12)
        src_lay.setSpacing(8)
        src_lay.addWidget(QLabel("源图"))
        src_list = QListWidget()
        src_list.setAlternatingRowColors(True)
        for path in self._jpg_paths:
            item = QListWidgetItem(src_list)
            cb = QCheckBox(Path(path).name)
            cb.setChecked(True)
            cb.setToolTip(path)
            thumb = self._load_source_preview_pixmap(path)
            if thumb is not None and not thumb.isNull():
                cb.setIcon(QIcon(thumb))
                cb.setIconSize(QSize(46, 46))
            self._checks.append((cb, path))
            src_list.setItemWidget(item, cb)
            item.setSizeHint(cb.sizeHint())
        src_lay.addWidget(src_list, 1)
        body.addWidget(sources)

        preview = QFrame()
        preview.setObjectName("Panel")
        pv_lay = QVBoxLayout(preview)
        pv_lay.setContentsMargins(16, 16, 16, 16)
        pv_lay.setSpacing(10)
        pv_title = QLabel("TIFF 预览")
        pv_title.setStyleSheet(f"font-size: 13px; font-weight: 700; color:{t['text']};")
        pv_lay.addWidget(pv_title)
        self._tiff_preview = _ScaledImagePreview()
        self._tiff_preview.setObjectName("ComposeTiffPreview")
        self._tiff_preview.setStyleSheet(
            f"color:{t['muted']}; background:{t['panel_inset']};"
            f" border:1px dashed {t['border_medium']}; border-radius:8px;"
            " padding:8px; font-size:12px;"
        )
        self._tiff_preview.setToolTip(self._tiff_path)
        self._preview_scroll = QScrollArea()
        self._preview_scroll.setWidgetResizable(False)
        self._preview_scroll.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._preview_scroll.setMinimumSize(360, 300)
        self._preview_scroll.setStyleSheet(
            f"QScrollArea {{ background:{t['panel_inset']};"
            f" border:1px dashed {t['border_medium']}; border-radius:8px; }}"
        )
        self._tiff_preview.set_scroll_area(self._preview_scroll)
        self._preview_scroll.setWidget(self._tiff_preview)
        meta = QLabel()
        meta.setObjectName("Muted")
        if os.path.isfile(self._tiff_path):
            size_mb = os.path.getsize(self._tiff_path) / (1024 * 1024)
            pixmap = self._load_tiff_preview_pixmap()
            if pixmap is not None and not pixmap.isNull():
                self._tiff_preview.set_preview_pixmap(pixmap)
                meta.setText(
                    f"已生成 TIFF · {Path(self._tiff_path).name} · "
                    f"{size_mb:.1f} MB"
                )
            else:
                self._tiff_preview.set_placeholder(
                    f"无法预览 TIFF\n{self._tiff_path}\n\n大小：{size_mb:.1f} MB"
                )
                meta.setText("TIFF 已生成，但当前环境无法解码预览图。")
        else:
            self._tiff_preview.set_placeholder(f"未找到 TIFF\n{self._tiff_path}")
            meta.setText("输出文件不存在。")
        meta.setWordWrap(True)
        meta.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        pv_lay.addWidget(self._preview_scroll, 1)
        pv_lay.addWidget(meta)
        hint = QLabel("调整右侧参数后可重合成预览；保存后写入当前分组结果。")
        hint.setWordWrap(True)
        hint.setStyleSheet(f"color:{t['muted_dim']}; font-size:11px;")
        pv_lay.addWidget(hint)
        body.addWidget(preview)

        body.addWidget(self._params_panel)
        body.setSizes([240, 460, 220])
        root.addWidget(body, 1)

        foot = QHBoxLayout()
        foot.addStretch()
        cancel = QPushButton("取消（退回 TIFF）")
        cancel.setObjectName("Outline")
        cancel.clicked.connect(self._reject_compose_preview)
        foot.addWidget(cancel)
        recompose = QPushButton("重合成预览")
        recompose.setObjectName("Outline")
        recompose.clicked.connect(self._accept_recompose_preview)
        foot.addWidget(recompose)
        save = QPushButton("保存到结果")
        save.setObjectName("Primary")
        save.clicked.connect(self._accept_save_result)
        foot.addWidget(save)
        root.addLayout(foot)

    def _install_shortcuts(self) -> None:
        def add(seq, callback) -> None:
            shortcut = QShortcut(QKeySequence(seq), self)
            shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            shortcut.activated.connect(callback)
            self._shortcuts.append(shortcut)

        add("Ctrl++", lambda: self._tiff_preview.set_zoom_percent(self._tiff_preview._zoom_percent + 10))
        add("Ctrl+=", lambda: self._tiff_preview.set_zoom_percent(self._tiff_preview._zoom_percent + 10))
        add("Ctrl+-", lambda: self._tiff_preview.set_zoom_percent(self._tiff_preview._zoom_percent - 10))
        add("Ctrl+0", self._tiff_preview.fit_to_window)
        add("Ctrl+1", self._tiff_preview.actual_size)

    def _load_tiff_preview_pixmap(self) -> Optional[QPixmap]:
        try:
            return decode_image_thumbnail(self._tiff_path, max_size=2400, use_cache=False)
        except Exception:
            return None

    def _load_source_preview_pixmap(self, path: str) -> Optional[QPixmap]:
        try:
            return decode_image_thumbnail(path, max_size=96, use_cache=True)
        except Exception:
            return None

    def selected_jpgs(self) -> list[str]:
        return [path for cb, path in self._checks if cb.isChecked()]

    def params(self) -> dict:
        return self._params_panel.get_params()

    def action(self) -> str:
        return self._action

    def _accept_save_result(self) -> None:
        self._action = self.ACTION_SAVE
        self.accept()

    def _reject_compose_preview(self) -> None:
        self._action = self.ACTION_CANCEL
        self.reject()

    def _accept_recompose_preview(self) -> None:
        self._action = self.ACTION_RECOMPOSE
        self.accept()


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
    """Dimmed backdrop behind the settings drawer; click anywhere to dismiss."""

    def __init__(self, on_click, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("DrawerScrim")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._on_click = on_click
        self.hide()

    def mousePressEvent(self, event) -> None:  # noqa: N802 (Qt override)
        self._on_click()


class WorkbenchView(BaseView):
    """Daily-use workbench — specimen list | monitor + grouping | naming + metadata.

    view_id   = "workbench"
    nav_title = "工作台"
    nav_icon  = "🔬"
    """

    view_id = "workbench"
    nav_title = "照片工作区"
    nav_icon = "🔬"

    # ── Build UI ──────────────────────────────────────────────────────────────

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        panel = getattr(self, "_workflow_notice_panel", None)
        if panel is not None:
            try:
                panel.reposition()
            except Exception:
                pass
        dlg = getattr(self, "_compose_organise_progress_dialog", None)
        if dlg is not None and dlg.isVisible():
            try:
                dlg._place_launcher()
                dlg._reposition_panel()
            except Exception:
                pass

    def _setup_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # NOTE: brand / project-switcher / global-action chrome now lives in
        # MainWindow's TopBar + ContextBar.  This view renders only the
        # three-column workbench content with generous whitespace.

        # ── Body container (header + dir-strip + splitter) ─────────────────
        body = QWidget()
        body_lay = QVBoxLayout(body)
        body_lay.setContentsMargins(24, 18, 24, 18)
        body_lay.setSpacing(14)
        root.addWidget(body, stretch=1)

        # ── Workspace header: title + project tag + helicon status ─────────
        body_lay.addLayout(self._build_header())

        # ── Directory info strip ───────────────────────────────────────────
        self._dir_strip = self._build_dir_strip()
        body_lay.addWidget(self._dir_strip)

        # ── Outer horizontal splitter: left | centre+right ─────────────────
        outer = QSplitter(Qt.Orientation.Horizontal)
        outer.setObjectName("WorkbenchSplitter")
        outer.setChildrenCollapsible(False)
        outer.setHandleWidth(14)

        # ── Left: specimen sidebar ─────────────────────────────────────────
        self._sidebar = SpecimenSidebar(self.ctx)
        self._sidebar.setMinimumWidth(250)
        self._sidebar.setMaximumWidth(330)
        self._sidebar.specimen_selected.connect(self._on_specimen_selected)
        self._sidebar.show_all_requested.connect(self._on_show_all_results)
        self._sidebar.activate_requested.connect(self._on_sidebar_activate)
        self._sidebar.deactivate_requested.connect(self._on_sidebar_deactivate)
        self._sidebar.activate_no_selection.connect(
            lambda: self._status_message("请先在左侧选中要激活的编号。")
        )
        self._sidebar.new_specimen_requested.connect(self._on_new_specimen)
        self._sidebar.collab_manager_requested.connect(self._on_open_collab_panel)
        self._sidebar.sync_selected_requested.connect(self._on_sync_selected_uid)
        self._sidebar.sync_project_requested.connect(self._on_sync_project_files)
        self._sidebar.sync_selected_overwrite_requested.connect(
            lambda: self._on_sync_selected_uid(mode="overwrite")
        )
        self._sidebar.sync_project_overwrite_requested.connect(
            lambda: self._on_sync_project_files(mode="overwrite")
        )
        self._sidebar.print_labels_requested.connect(self._on_print_labels)
        self._sidebar.delete_specimen_requested.connect(self._confirm_delete_specimen)
        self._sidebar.print_rna_queue_requested.connect(self._on_print_rna_queue)
        self._sidebar.phase_mark_requested.connect(self._on_phase_mark)
        outer.addWidget(self._sidebar)

        # Wire collab service signals → sidebar strip refresh + collab card refresh
        svc = getattr(self.ctx, "collab_service", None)
        if svc is not None:
            svc.peers_changed.connect(
                lambda: self._sidebar.update_collab_status(svc)
            )
            svc.tasks_changed.connect(
                lambda: self._sidebar.update_collab_status(svc)
            )
            svc.server_ready.connect(
                lambda _port: self._sidebar.update_collab_status(svc)
            )
            # Refresh collab card when tasks change (if a specimen is selected)
            svc.tasks_changed.connect(self._refresh_collab_card)
            # Peer-synced status changes flow back into the phase pills.
            svc.tasks_changed.connect(self._refresh_batch_header)
        self._sidebar.update_collab_status(svc)

        # ── Centre ①: vertical splitter (monitor top, grouping bottom) ───────
        centre = QSplitter(Qt.Orientation.Vertical)
        centre.setChildrenCollapsible(False)
        centre.setHandleWidth(14)

        self._monitor = MonitorPanel(self.ctx)
        self._monitor.refresh_requested.connect(self._refresh_monitor)
        self._monitor.assign_requested.connect(self._on_assign_jpg)
        self._monitor.unassign_requested.connect(self._on_unassign_jpg)
        self._monitor.add_jpg_requested.connect(self._on_add_jpg_files)
        self._monitor.external_jpgs_dropped.connect(self._on_external_jpgs_dropped)
        self._monitor.clear_pending_requested.connect(self._on_clear_pending_queue)
        self._monitor.grouping_requested.connect(self._on_open_grouping)
        self._monitor.legacy_organize_requested.connect(
            self._on_legacy_photo_batch_organize
        )
        self._monitor.compose_implicit_requested.connect(self._on_compose_implicit)
        self._monitor.organise_selected_requested.connect(self._on_organise_selected)
        self._monitor.compose_implicit_organise_requested.connect(
            lambda: self._on_compose_implicit(organise=True)
        )
        self._monitor.auto_compress_toggled.connect(self._on_auto_compress_toggled)
        self._monitor.compose_preview_toggled.connect(self._on_compose_preview_toggled)
        self._sync_auto_archive_toggle()
        self._sync_compose_preview_toggle()
        self._monitor.settings_requested.connect(self._on_open_settings)
        self._monitor.phase_clicked.connect(self._on_phase_clicked)
        centre.addWidget(self._monitor)

        # 分组工具 lives in an on-demand popup, NOT permanently in the main
        # column.  The web oracle keeps this panel collapsed by default and opens
        # it from a 监控区 "分组工具" toggle (app.js:568 / 8595-8627); a non-modal
        # dialog is the desktop equivalent and keeps the work column clean
        # (监控区↑ / 结果区↓).  The panel instance + every signal wire are
        # unchanged — it is just re-homed into the dialog.
        self._grouping = GroupingPanel(self.ctx)
        self._grouping.compose_requested.connect(self._on_compose_requested)
        self._grouping.organise_requested.connect(self._on_organise_requested)
        self._grouping.undo_compose_requested.connect(self._on_undo_compose)
        self._grouping.grouping_changed.connect(self._on_grouping_changed)
        self._grouping.add_selection_to_group_requested.connect(self._on_add_selection_to_group)
        self._grouping.free_compose_requested.connect(self._on_free_compose)
        self._grouping.retroactive_requested.connect(self._on_retroactive_scan)
        self._grouping.auto_group_organize_requested.connect(
            self._on_auto_group_organize
        )
        self._grouping.tiff_naming_check_requested.connect(
            self._on_tiff_naming_check
        )
        self._grouping.tiff_naming_check_path_requested.connect(
            self._on_tiff_naming_check_path
        )
        self._grouping.helicon_params_requested.connect(self._open_grouping_helicon_params)
        self._grouping.import_tiff_requested.connect(self._persist_imported_group_tiff)  # #cursor
        self._grouping.archive_zip_registered.connect(self._on_archive_zip_registered)
        self._grouping.supp_process_requested.connect(self._on_supplementary_process)
        self._grouping.supp_files_dropped.connect(self._on_supplementary_dropped)
        # 批量[合成]/[合成+整理]/[整理] — workbench 驱动顺序队列(合成异步,需串行)。
        self._batch = None  # {"uid","queue":[group_index...],"organise":bool}
        self._last_scan_result = None
        self._monitor_scan_worker = None
        self._monitor_scan_request_id = 0
        self._monitor_scan_pending = False
        self._photo_import_worker = None
        self._auto_known_tiffs: set[str] = set()
        self._auto_tiff_busy = False
        self._grouping.compose_all_requested.connect(
            lambda uid: self._start_compose_batch(uid, organise=False))
        self._grouping.compose_and_organise_all_requested.connect(
            lambda uid: self._start_compose_batch(uid, organise=True))
        self._grouping.organise_all_requested.connect(self._organise_all_batch)
        # 参数对象依然为合成流程提供 get_params()，但 UI 只从“更多”弹出。
        self._helicon_params = HeliconParamsPanel()
        self._seed_helicon_defaults()
        self._grouping_dialog = self._build_grouping_dialog(self._grouping)

        # 成果内容 (composed TIFFs + archive ZIPs) — stacked BELOW the monitor in
        # the main column, mirroring the web oracle's 监控区(top) / 结果区(bottom)
        # workspace (app.js:4995, renderFinalResults app.js:9017).  Keeping the
        # results visible in the work column (not hidden behind a tab) preserves
        # at-a-glance compose/compress state.
        self._results = ResultsColumn()
        self._results.restore_requested.connect(self._on_restore_archive)
        self._results.specimen_requested.connect(self._on_specimen_selected)
        self._results.show_all_requested.connect(self._on_show_all_results)
        self._results.current_requested.connect(self._on_show_current_results)
        self._results.link_result_requested.connect(self._on_link_result_to_right_uid)
        self._results.tiff_naming_check_requested.connect(
            self._on_tiff_naming_check_path
        )
        self._results.tiff_delete_requested.connect(self._on_delete_result_tiff_path)
        centre.addWidget(self._results)

        centre.setSizes([440, 360])
        centre.setMinimumWidth(520)
        outer.addWidget(centre)

        # ── Right rail: 编号与元数据 column.  Vertical stacking of the results in
        #    the centre column frees the horizontal budget the old tab hack was
        #    invented to reclaim, so the naming panel keeps a width floor (never
        #    clips the UID / copy buttons) as a plain column — no tabs.
        right = QWidget()
        right_lay = QVBoxLayout(right)
        right_lay.setContentsMargins(0, 0, 0, 0)
        right_lay.setSpacing(12)

        # Right-rail command strip.  Keep the global save action visible without
        # turning it into a banner that competes with the form itself.
        rail_toolbar = QHBoxLayout()
        rail_toolbar.setContentsMargins(0, 0, 0, 0)
        rail_toolbar.setSpacing(8)

        rail_toolbar.addStretch(1)

        # 栏顶「收起命名 / 展开命名」整体折叠按钮（web rightPanelCollapsed）
        self._rail_collapse_btn = QPushButton("收起")
        self._rail_collapse_btn.setObjectName("Ghost")
        self._rail_collapse_btn.setFixedHeight(30)
        self._rail_collapse_btn.setToolTip("收起 / 展开整条命名栏")
        self._rail_collapse_btn.clicked.connect(self._toggle_right_rail)
        rail_toolbar.addWidget(self._rail_collapse_btn)
        right_lay.addLayout(rail_toolbar)

        # 卡1 照片编号
        self._naming = NamingPanel(self.ctx)
        self._naming.save_requested.connect(self._on_naming_save)
        self._naming.add_requested.connect(self._on_naming_add)
        self._naming.update_requested.connect(self._on_naming_update_results)
        self._naming.delete_requested.connect(self._confirm_delete_specimen)
        self._naming.uid_generated.connect(self._on_naming_uid_generated)
        self._naming.uid_corrected.connect(self._on_uid_corrected)
        self._naming.storage_applied.connect(self._on_naming_storage_applied)
        self._naming.open_project_settings.connect(self._on_open_settings)
        self._naming.keys_committed.connect(self._apply_collection_autofill)
        right_lay.addWidget(self._naming)           # natural height, no compress

        # Right-rail autosave debounce (web scheduleRightPanelPersist, 500ms).
        # 卡2/卡3 have no save button — edits persist live; reload=False keeps
        # the focused input's cursor.
        self._rail_save_timer = QTimer(self)
        self._rail_save_timer.setSingleShot(True)
        self._rail_save_timer.setInterval(500)
        self._rail_save_timer.timeout.connect(self._flush_rail_save)
        # 卡1 non-key fields (日期/拍照备注) autosave like web input-persist.
        # KEY segments (地区/样地/站位/物种/保存方式) still go through the 保存
        # button / storage-correction path — autosaving them would change the UID.
        self._naming._collection_date.textEdited.connect(lambda *_: self._schedule_rail_save())
        self._naming._photo_date.textEdited.connect(lambda *_: self._schedule_rail_save())
        self._naming._photo_notes.textChanged.connect(lambda: self._schedule_rail_save())

        # 卡2 分类标签（独立卡，对齐 web renderTaxonNotesCard）
        self._taxon_card = TaxonCardPanel(self.ctx)
        self._taxon_card.save_requested.connect(
            lambda: self._on_save_metadata(self._current_uid) if self._current_uid else None
        )
        self._taxon_card.taxon_changed.connect(lambda *_: self._schedule_rail_save())
        self._taxon_card.taxon_changed.connect(lambda *_: self._sync_uid_display_summary())
        self._taxon_card.open_edit_requested.connect(self._on_open_taxon_edit)
        right_lay.addWidget(self._taxon_card)

        # 卡3 元数据（已瘦身，无分类；编辑即存）
        self._metadata = MetadataPanel(self.ctx)
        self._metadata.save_requested.connect(self._on_save_metadata)
        self._metadata.metadata_changed.connect(lambda *_: self._schedule_rail_save())
        self._metadata.metadata_changed.connect(lambda *_: self._sync_uid_display_summary())
        right_lay.addWidget(self._metadata)

        # 卡4 协作状态（默认折叠）
        from app.widgets.collab_specimen_card import CollabSpecimenCard
        self._collab_card = CollabSpecimenCard(self.ctx)
        right_lay.addWidget(self._collab_card)
        right_lay.addStretch(1)

        right_scroll = QScrollArea()
        right_scroll.setObjectName("ColumnScroll")
        right_scroll.setWidget(right)
        right_scroll.setWidgetResizable(True)
        right_scroll.setFrameShape(QFrame.Shape.NoFrame)
        right_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        right_scroll.setMinimumWidth(320)   # web bindPanelResize 下限
        right_scroll.setMaximumWidth(500)   # web bindPanelResize 上限
        self._right_scroll = right_scroll
        self._right_rail_widget = right
        self._right_rail_collapsed = False
        outer.addWidget(right_scroll)

        # 3-zone proportions: sidebar : centre stage (monitor/grouping/results)
        # : naming rail.
        outer.setSizes([280, 760, 380])

        # The three columns' min widths sum to ~1166 px (250+520+320 + handles).
        # On narrower windows (≤1166, e.g. 1024 remote desktops / WSLg HiDPI),
        # childrenCollapsible=False means the splitter can't shrink below that,
        # so the rightmost rail (保存方式) was clipped off the window edge.
        # Hosting the splitter in a horizontal scroll area makes the overflow
        # scrollable instead of clipped.  On wide windows widgetResizable lets
        # the splitter fill the viewport, no scrollbar shows, layout identical.
        outer_scroll = QScrollArea()
        outer_scroll.setObjectName("WorkbenchScroll")
        outer_scroll.setWidget(outer)
        outer_scroll.setWidgetResizable(True)
        outer_scroll.setFrameShape(QFrame.Shape.NoFrame)
        outer_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        outer_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        body_lay.addWidget(outer_scroll, stretch=1)

        # ── Project settings drawer (overlay, hidden by default) ────────────
        from app.widgets.project_settings_drawer import ProjectSettingsDrawer
        self._settings_scrim = _DrawerScrim(self._close_settings, parent=self)
        self._settings_drawer = ProjectSettingsDrawer(self.ctx, parent=self)
        self._settings_drawer.setFixedWidth(380)
        self._settings_drawer.closed.connect(self._settings_scrim.hide)
        self._settings_drawer.personnel_changed.connect(
            self._on_project_personnel_changed
        )
        self._settings_drawer.storages_changed.connect(
            self._naming.refresh_storage_methods
        )
        self._settings_drawer.naming_rules_changed.connect(
            self._naming.refresh_naming_rules
        )

        # ── Collab panel drawer (overlay, hidden by default) ───────────────
        from app.widgets.collab_panel import CollabPanel
        self._collab_scrim = _DrawerScrim(self._close_collab_panel, parent=self)
        self._collab_panel = CollabPanel(self.ctx, parent=self)
        self._collab_panel.closed.connect(self._collab_scrim.hide)

        # ── No-project banner ───────────────────────────────────────────────
        self._no_project_banner = QLabel(
            "未选择工作区 — 请先在「项目树」进入一个断面，或在「最近工作区」打开"
        )
        self._no_project_banner.setObjectName("Muted")
        self._no_project_banner.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._no_project_banner.hide()
        body_lay.addWidget(self._no_project_banner)

        # Pending grouping-save debounce timer (500 ms)
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(500)
        self._save_timer.timeout.connect(self._flush_grouping_save)

        # ── File-system real-time monitoring (replaces 2 s poll) ─────────
        # Primary: QFileSystemWatcher pushes OS-level directory change events.
        # Debounce: 300 ms window merges rapid bursts (camera burst / batch copy).
        # Fallback: occasional full rescan catches missed events on WSL2 / SMB.
        # QFileSystemWatcher handles normal realtime updates; keeping this wide
        # avoids constant Windows-drive scans while the user is editing fields.
        self._fs_watcher = QFileSystemWatcher(self)
        self._fs_watcher.directoryChanged.connect(self._on_fs_changed)

        self._debounce_timer = QTimer(self)
        self._debounce_timer.setSingleShot(True)
        self._debounce_timer.setInterval(300)
        self._debounce_timer.timeout.connect(self._refresh_monitor)

        self._fallback_timer = QTimer(self)
        self._fallback_timer.setInterval(120000)
        self._fallback_timer.timeout.connect(self._refresh_monitor)

        # Track current UID for grouping edits
        self._current_uid: Optional[str] = None
        self._pending_grouping = None  # SpecimenGrouping awaiting save
        self.ctx.worms_fill_specimen = self.worms_fill_specimen
        self._refresh_workflow_dashboard()

        # Pre-create overlay last so it stacks above body / drawer scrims.
        self._compose_organise_progress_dialog = _ComposeOrganiseProgressDialog(self)
        self._compose_organise_progress_dialog.cancel_requested.connect(
            self._cancel_workflow_task
        )

    # ── Header chrome builders ─────────────────────────────────────────────────

    def _build_header(self) -> QHBoxLayout:
        """Workspace title + project tag + Helicon status tag.

        Slim content-level header.  Global chrome (brand / project switcher /
        quick actions) lives in MainWindow's TopBar + ContextBar.
        """
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(10)
        title = QLabel("拍照工作台")
        title.setObjectName("WorkspaceTitle")
        row.addWidget(title)
        self._project_tag = QLabel("—")
        self._project_tag.setObjectName("TagSea")
        row.addWidget(self._project_tag)
        self._helicon_tag = QLabel("Helicon 未检测")
        self._helicon_tag.setObjectName("TagWarn")
        row.addWidget(self._helicon_tag)
        row.addStretch()
        settings_btn = QPushButton("设置")
        settings_btn.setObjectName("Ghost")
        settings_btn.setFixedHeight(26)
        settings_btn.clicked.connect(self._on_open_settings)
        row.addWidget(settings_btn)
        return row

    def _build_dir_strip(self) -> QFrame:
        """Working-directory / camera-JPG / results path strip."""
        strip = QFrame()
        strip.setObjectName("DirStrip")
        lay = QHBoxLayout(strip)
        lay.setContentsMargins(10, 6, 10, 6)
        lay.setSpacing(12)

        def add_directory_path_indicator(label: str) -> QLabel:
            l = QLabel(label)
            l.setObjectName("DirLabel")
            lay.addWidget(l)
            path = QLabel("—")
            path.setObjectName("DirPath")
            lay.addWidget(path)
            return path

        self._dir_root = add_directory_path_indicator("工作目录")
        self._dir_incoming = add_directory_path_indicator("相机 JPG")
        self._dir_results = add_directory_path_indicator("成果")
        lay.addStretch()
        return strip

    def _refresh_header(self) -> None:
        """Update header tags + dir-strip + monitor batch from current state."""
        project_dir = self.ctx.current_project_dir
        name = Path(project_dir).name if project_dir else "（未选）"
        self._project_tag.setText(name)

        # Keep MainWindow's context bar (project + active badge) in sync.
        win = self.window()
        if hasattr(win, "refresh_context_bar"):
            try:
                win.refresh_context_bar()
            except Exception:
                pass

        # Helicon status tag
        installed = False
        try:
            from app.services.helicon_service import detect_helicon
            installed = bool(detect_helicon())
        except Exception:
            installed = False
        if installed:
            self._helicon_tag.setText("Helicon OK")
            self._helicon_tag.setObjectName("TagOk")
        else:
            self._helicon_tag.setText("Helicon 未检测")
            self._helicon_tag.setObjectName("TagWarn")
        self._helicon_tag.style().unpolish(self._helicon_tag)
        self._helicon_tag.style().polish(self._helicon_tag)

        # Dir strip
        if project_dir:
            self._dir_strip.show()
            self._dir_root.setText(project_dir)
            self._dir_incoming.setText("incoming-jpg/")
            self._dir_results.setText("results/")
        else:
            self._dir_strip.hide()
        self._refresh_workflow_dashboard()

    # ── BaseView contract ─────────────────────────────────────────────────────

    def on_activate(self) -> None:
        """Called each time the user navigates to the workbench page."""
        if not self.ctx.has_project:
            self._show_no_project()
            return

        self._no_project_banner.hide()
        self._refresh_header()
        self._sidebar.refresh()
        self._sync_rna_queue_count()
        self._sync_auto_archive_toggle()
        self._sync_compose_preview_toggle()

        # Re-select the previously active specimen if possible
        active_uid = self._get_active_uid()
        if active_uid:
            self._sidebar.select_uid(active_uid)
            self._load_specimen(active_uid)
        else:
            # 无激活标本(空项目/新建):清命名卡,防上一项目最后加载的
            # province/site 等字段残留显示(用户会误以为"被默认了")。
            self._naming.load_specimen({})
            self._current_uid = None
            try:
                self._apply_draft_project_defaults()
            except Exception:
                pass
        self._refresh_batch_header()

        # Start filesystem watcher + fallback poll
        self._setup_fs_watcher()
        # Let the page paint before scanning directories.  The scan can touch a
        # large incoming/results folder, so doing it through the debounce timer
        # keeps returning to the workbench responsive and avoids a duplicate
        # immediate scan.
        self._debounce_timer.start(50)  # first refresh almost immediately
        if not self._fallback_timer.isActive():
            self._fallback_timer.start()

    def _sync_auto_archive_toggle(self) -> None:
        """Keep the toolbar auto-archive button in sync with persisted settings."""
        try:
            self._monitor.set_auto_archive_enabled(
                bool(getattr(self.ctx.settings, "auto_organize_after_compose", False))
            )
        except Exception:
            pass

    def _sync_compose_preview_toggle(self) -> None:
        """Keep the toolbar preview checkbox in sync with silent-compose setting."""
        try:
            self._monitor.set_compose_preview_enabled(
                not bool(getattr(self.ctx.settings, "silent_compose", False))
            )
        except Exception:
            pass

    def on_deactivate(self) -> None:
        """Called when navigating away; stop watchers and timers."""
        self._debounce_timer.stop()
        self._fallback_timer.stop()
        self._fs_watcher.removePaths(self._fs_watcher.directories())
        self._monitor_scan_pending = False
        self._monitor_scan_request_id += 1

    def stop_background_work(self) -> None:
        """Cancel an in-flight Helicon compose so its subprocess + QThread
        cannot outlive app exit (orphaned helicon-focus*.exe holds /mnt
        handles → must-reboot lock leak)."""
        workers = list(getattr(self, "_helicon_workers", set()) or [])
        legacy_worker = getattr(self, "_helicon_worker", None)
        if legacy_worker is not None and legacy_worker not in workers:
            workers.append(legacy_worker)
        for w in workers:
            if w is not None and w.isRunning():
                try:
                    w.cancel()
                except Exception:  # noqa: BLE001
                    pass
                w.wait(3000)
        for worker in list(getattr(self, "_archive_workers", set()) or []):
            if worker is not None and worker.isRunning():
                worker.wait(3000)
        importer = getattr(self, "_photo_import_worker", None)
        if importer is not None and importer.isRunning():
            importer.wait(3000)
        sync_worker = getattr(self, "_collab_file_sync_worker", None)
        if sync_worker is not None and sync_worker.isRunning():
            sync_worker.wait(3000)

    # ── Filesystem watcher helpers ──────────────────────────────────────────

    def _resolve_capture_subdirs(self) -> tuple[str, str]:
        """解析当前项目的 incoming / results 子目录名（监听+扫描共用）。

        incoming 目录名不写死：优先用设置页配置（`project/incoming_subdir`，默认
        incoming-jpg）；若配置的目录不存在但遗留的「新拍JPG」存在，则用「新拍JPG」
        （复用 project_service.LEGACY_INCOMING_JPG_DIR）。results 同理（默认 results）。
        """
        s = getattr(self.ctx, "settings", None)
        inc = getattr(s, "incoming_subdir", None)
        res = getattr(s, "results_subdir", None)
        inc = inc if isinstance(inc, str) and inc else "incoming-jpg"
        res = res if isinstance(res, str) and res else "results"
        project_dir = getattr(self.ctx, "current_project_dir", None)
        if project_dir:
            from app.services.project_service import LEGACY_INCOMING_JPG_DIR
            if not os.path.isdir(os.path.join(project_dir, inc)) and \
               os.path.isdir(os.path.join(project_dir, LEGACY_INCOMING_JPG_DIR)):
                inc = LEGACY_INCOMING_JPG_DIR
        return inc, res

    def _setup_fs_watcher(self) -> None:
        """Watch the resolved incoming + results dirs for OS-level change events."""
        self._fs_watcher.removePaths(self._fs_watcher.directories())
        project_dir = self.ctx.current_project_dir
        if not project_dir:
            return
        inc, res = self._resolve_capture_subdirs()
        for sub in (inc, res):
            d = os.path.join(project_dir, sub)
            os.makedirs(d, exist_ok=True)
            self._fs_watcher.addPath(d)

    def _on_fs_changed(self, _path: str) -> None:
        """Debounced handler: merge rapid file events into one refresh."""
        if not self._debounce_timer.isActive():
            self._debounce_timer.start(300)

    # ── Specimen selection ────────────────────────────────────────────────────

    def _on_specimen_selected(self, uid: str) -> None:
        self._current_uid = uid
        self._load_specimen(uid)
        self._refresh_batch_header()

    def _on_show_all_results(self) -> None:
        """Show organized results for every specimen in the current project."""
        db = self.ctx.get_db()
        project_dir = self.ctx.current_project_dir
        if not db or not project_dir:
            self._results.clear()
            return
        try:
            project_dirs = equivalent_paths(project_dir)
            owner_filter = ",".join("?" * len(project_dirs)) or "?"
            rows = db.execute(
                f"""
                SELECT uid
                FROM specimens
                WHERE owner_project_dir IN ({owner_filter})
                ORDER BY uid
                """,
                project_dirs or [project_dir],
            ).fetchall()
            uids = [
                str(row["uid"] if hasattr(row, "keys") else row[0])
                for row in rows
            ]
        except Exception:
            uids = []

        groups: list[dict] = []
        try:
            from app.services.grouping_service import load_grouping
            from app.services.specimen_rename_service import (
                repair_grouping_result_files_for_uid,
            )

            for uid in uids:
                try:
                    repair_grouping_result_files_for_uid(db, uid)
                    grouping = load_grouping(db, uid)
                except Exception:
                    continue
                tiffs, zips = self._result_infos_from_grouping(grouping)
                if tiffs or zips:
                    groups.append({"uid": uid, "tiffs": tiffs, "zips": zips})
        except Exception:
            groups = []

        self._results.load_many(groups)
        result_count = sum(
            len({item.get("seq") or item.get("path") or item.get("name")
                 for item in g.get("tiffs", []) + g.get("zips", [])})
            for g in groups
        )
        self._status_message(
            f"已展示全部成果：{len(groups)} 个编号，{result_count} 项。"
        )

    def _on_show_current_results(self) -> None:
        """Return the results panel to the currently selected specimen."""
        uid = self._current_uid or self._sidebar.current_uid()
        if not uid:
            self._results.clear()
            self._status_message("请先选择一个编号。")
            return
        try:
            from app.services.grouping_service import load_grouping
            grouping = load_grouping(self.ctx.get_db(), uid)
        except Exception:
            grouping = None
        self._refresh_results_column(uid, grouping)

    def _on_edit_specimen_requested(self, uid: str) -> None:
        """Compatibility entry used by the sidebar edit action."""
        if not uid:
            return
        self._on_specimen_selected(uid)

    def _confirm_delete_specimen(self, uid: str) -> None:
        """Confirm before deleting a specimen record from the workbench DB."""
        uid = str(uid or "").strip()
        if not uid:
            return
        ret = QMessageBox.question(
            self,
            "删除标本编号",
            f"确定删除这个标本编号吗？\n\n{uid}\n\n"
            "只删除工作台中的编号记录和关联状态，不删除磁盘上的照片或成果文件。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if ret == QMessageBox.StandardButton.Yes:
            self._on_delete_specimen(uid)

    def _on_delete_specimen(self, uid: str) -> None:
        """Delete a specimen and local DB references owned by the workbench."""
        uid = str(uid or "").strip()
        if not uid:
            return
        db = self.ctx.get_db()
        if not db:
            return
        try:
            from app.services.capture_workflow_service import delete_specimen

            delete_specimen(db, uid)
            if self._current_uid == uid:
                self._current_uid = None
                for widget in (self._naming, self._metadata, self._taxon_card, self._grouping):
                    clear = getattr(widget, "clear", None)
                    if callable(clear):
                        clear()
            self._sidebar.refresh()
            self._refresh_batch_header()
        except Exception as exc:
            QMessageBox.warning(self, "删除失败", str(exc))

    def _refresh_batch_header(self) -> None:
        """Sync the monitor's batch-ident bar with the active specimen."""
        db = self.ctx.get_db()
        active_uid = self._get_active_uid()
        activated_at = None
        phase = None
        if db and active_uid:
            try:
                row = db.execute(
                    "SELECT activated_at FROM tasks WHERE uid = ?", (active_uid,)
                ).fetchone()
                if row:
                    activated_at = row[0]
            except Exception:
                pass
            phase = self._collab_phase_for(active_uid)
        batch_uid = active_uid or self._current_uid
        self._monitor.set_batch(batch_uid, active_uid, activated_at)
        if active_uid:
            self._monitor.set_phase(phase)
        self._refresh_workflow_dashboard()

    def _refresh_workflow_dashboard(self) -> None:
        panel = getattr(self, "_workflow_dashboard", None)
        if panel is None:
            return
        try:
            panel.update_state(
                project_dir=getattr(self.ctx, "current_project_dir", None),
                active_uid=self._get_active_uid(),
                current_uid=getattr(self, "_current_uid", None),
                scan_result=getattr(self, "_last_scan_result", None),
            )
        except Exception:
            pass

    def _collab_phase_for(self, uid: str) -> Optional[str]:
        """Return confirmed collab phase from memory first, then project DB."""
        svc = getattr(self.ctx, "collab_service", None)
        try:
            from app.services.activation_service import resolve_phase
            return resolve_phase(svc, self.ctx.get_db(), uid)
        except Exception:
            return None

    def _set_phase(self, uid: str, status: str) -> bool:
        """Mark *uid* to phase *status* — manual human marking, any uid, any jump.

        Writes the project DB and (when collab is running) syncs peers with
        ``force=True`` so out-of-order / backward marks are honoured, mirroring
        the oracle's free assignment (app.js:3303).  Does NOT require *uid* to
        be the active specimen, so the sidebar phase dots can mark any 编号.
        Returns True on success.
        """
        if not uid:
            return False
        allowed = set(getattr(self._monitor, "_phase_pills", {}).keys())
        if status not in allowed:
            self._status_message(f"未知阶段：{status}")
            return False

        db = self.ctx.get_db()
        seed_status = self._collab_phase_for(uid)
        svc = getattr(self.ctx, "collab_service", None)
        if svc is not None:
            ok, msg = svc.update_task_status(
                uid, status, seed_status=seed_status, force=True
            )
            if not ok:
                self._status_message(f"阶段未变更：{msg}")
                return False

        if db is not None:
            try:
                from app.services.activation_service import set_collab_status
                set_collab_status(db, uid, status)
            except Exception as exc:
                self._status_message(f"阶段保存失败：{exc}")
                return False

        self._status_message("阶段已更新")
        self._refresh_batch_header()
        try:
            self._sidebar.update_collab_status(svc)
            self._sidebar.refresh_phases()
            self._refresh_collab_card()
        except Exception:
            pass
        return True

    def _on_phase_clicked(self, status: str) -> None:
        """Batch-bar phase pill: marks the *active* specimen's phase."""
        uid = self._get_active_uid()
        if not uid:
            self._monitor.set_phase(None)
            self._status_message("请先激活一个编号，再标记拍摄阶段")
            return
        if self._set_phase(uid, status):
            self._monitor.set_phase(status)
        else:
            self._refresh_batch_header()

    def _on_phase_mark(self, uid: str, status: str) -> None:
        """Sidebar phase-dot click: mark any 编号's phase (no activation needed)."""
        self._set_phase(uid, status)

    def _on_new_specimen(self) -> None:
        """Start a fresh blank UID draft in the naming/metadata panels.

        The draft is pre-filled with the project's inherited 地区/样地 + 人员
        defaults (resolved up the folder tree) so the user never re-types them
        per specimen — see project_settings_service.effective_new_specimen_prefill.

        采集/拍摄日期沿用上一号(sticky): 同一断面连拍时日期通常不变, 新编号继承当前
        命名卡上的日期而非留空, 减手输; 用户仍可改。无上一号(首次新增)则留空。 (2026-06-14)
        """
        # Capture the currently-shown collection context BEFORE clearing the
        # draft. 新增编号通常是同一工作区/同一站位连续拍摄：地点、站位、
        # 日期、保存方式、人员和坐标应沿用；物种编号/备注/分类仍按新标本清空。
        prev_naming_context = {
            "province": self._naming._province.text().strip(),
            "site": self._naming._site.text().strip(),
            "station": self._naming._station.text().strip(),
            "storage": self._naming._storage.text().strip(),
            "collectionDate": self._naming._collection_date.text().strip(),
            "photoDate": self._naming._photo_date.text().strip(),
        }
        try:
            # Personnel are project defaults for every new specimen, not sticky
            # values from the previous specimen. Location context may carry over
            # and remains auto-marked so a station record can replace it.
            prev_meta_context = {
                key: str(value or "").strip()
                for key, value in self._metadata.current_values().items()
                if key in ("lon", "lat", "geo_area")
            }
        except Exception:
            prev_meta_context = {}

        self._current_uid = None
        prefill = self._effective_prefill()
        self._naming.load_specimen({
            "province": prev_naming_context["province"] or prefill.get("province", ""),
            "site": prev_naming_context["site"] or prefill.get("site", ""),
            "station": prev_naming_context["station"],
            "storage": prev_naming_context["storage"],
            "collectionDate": prev_naming_context["collectionDate"],
            "photoDate": prev_naming_context["photoDate"],
        })
        try:
            self._metadata.clear()
            self._taxon_card.clear()
            # 项目级预填（自动，非手动）：人员三项 + 默认坐标/地理区。clear() 先
            # 清空，故都填进空字段并标记为「自动」。选定具体站位后，采集记录会以
            # override_auto 覆盖这里的项目默认坐标（见 _apply_collection_autofill）。
            self._apply_draft_project_defaults()
            self._metadata.apply_autofill({
                k: v
                for k, v in prev_meta_context.items()
                if v
            }, override_auto=True)
        except Exception:
            pass
        self._refresh_workflow_dashboard()

    def _apply_draft_project_defaults(self, *, override_auto: bool = False) -> None:
        """把项目默认人员/坐标预填进拍摄界面右栏（仅空字段或自动字段）。

        新建标本、进入工作台无激活号、项目设置里改人员时都会走这里。
        用户仍可在右侧手改（不同站位/临时换人）；手填值不会被覆盖。
        """
        prefill = self._effective_prefill()
        self._metadata.apply_autofill({
            k: prefill[k]
            for k in ("collector", "photographer", "identifier",
                      "lon", "lat", "geo_area")
            if prefill.get(k)
        }, override_auto=override_auto)
        self._sync_uid_display_summary()

    def _on_project_personnel_changed(self, personnel: dict) -> None:
        """Apply project personnel defaults to empty/auto fields on the right rail.

        Works for new drafts and for older specimens whose personnel columns were
        never filled — never overwrites values already saved on the specimen.
        """
        values = {
            key: str(personnel.get(key) or "").strip()
            for key in ("collector", "photographer", "identifier")
        }
        if not any(values.values()):
            return
        before = self._metadata.current_values()
        self._metadata.apply_autofill(values, override_auto=True)
        after = self._metadata.current_values()
        if self._current_uid and before != after:
            self._schedule_rail_save()
        self._sync_uid_display_summary()

    def _effective_prefill(self) -> dict:
        """Inherited new-specimen defaults for the current project, or empties."""
        empty = {"province": "", "site": "", "stations": {},
                 "collector": "", "photographer": "", "identifier": "",
                 "lon": "", "lat": "", "geo_area": ""}
        project_dir = getattr(self.ctx, "current_project_dir", None)
        if not project_dir:
            return empty
        try:
            from app.services import project_settings_service as pss
            return pss.effective_new_specimen_prefill(
                project_dir, root=self.ctx.current_project_root
            )
        except Exception:
            return empty

    def _on_print_labels(self, uid: str) -> None:
        """Print this specimen's labels.

        Fast path (一键直接打印): route sample and RNAlater labels to the project
        configured printers. R-prefix specimens may send tissue labels directly
        or queue them for A4/A5 sheet printing.

        Fallback: open the label studio pre-selected with *uid*, so the user can
        still tune fields / 留白 / 纸张 when there is no default printer or the
        template needs adjusting.
        """
        if not uid:
            return
        if self._quick_print_labels(uid):
            return
        self._status_message("未能开始打印，请检查项目设置中的打印机、模板和纸张。")

    def _quick_print_labels(self, uid: str) -> bool:
        """Send *uid*'s labels to printer per ``quick_print_mode`` setting.

        ``quick_print_mode``:
          - ``"direct"`` — send straight to configured printer (no dialog)
          - ``"dialog"`` — show PrintJobDialog for printer selection
          - ``"studio"`` — return False, caller falls back to label studio

        Returns ``False`` when the caller should open the studio instead.
        """
        try:
            from PyQt6.QtPrintSupport import QPrinterInfo
            from app.services import label_service
            from app.services import project_settings_service as pss
            import app.utils.label_print as label_print

            project_dir = getattr(self.ctx, "current_project_dir", None)
            project_root = getattr(self.ctx, "current_project_root", None)
            if not isinstance(project_root, str):
                project_root = None
            db = self.ctx.get_db()
            if db is None:
                return False
            print_settings = (
                pss.effective_print_settings(
                    project_dir, root=project_root
                )
                if project_dir else dict(pss.DEFAULT_PRINT_SETTINGS)
            )
            local_print_settings = pss.load_setting_if_present(db, "print_settings")
            if local_print_settings is not None:
                print_settings = pss.merge_print_settings(print_settings, local_print_settings)
                if (
                    "quick_print_mode" not in local_print_settings
                    and "quick_print" in local_print_settings
                ):
                    print_settings["quick_print_mode"] = (
                        "direct" if bool(local_print_settings["quick_print"]) else "studio"
                    )

            # read quick_print_mode with backward compat for old quick_print bool.
            # DEFAULT_PRINT_SETTINGS now carries quick_print_mode="direct", so a
            # legacy project with only quick_print=False needs an explicit remap.
            quick_mode = str(print_settings.get("quick_print_mode") or "")
            if not quick_mode:
                quick_mode = "direct" if bool(print_settings.get("quick_print", True)) else "studio"
            elif quick_mode == "direct" and not bool(print_settings.get("quick_print", True)):
                quick_mode = "studio"
            if quick_mode == "studio":
                return False

            from app.utils import windows_print
            if windows_print.is_available():
                default_printer = windows_print.windows_default_printer_name()
                available = set(windows_print.windows_printer_names())
            else:
                default_printer = QPrinterInfo.defaultPrinterName()
                available = {
                    p.printerName()
                    for p in QPrinterInfo.availablePrinters()
                    if p.printerName()
                }
            sample_printer = self._resolve_quick_printer(
                str(print_settings.get("sample_printer") or ""),
                default_printer,
                available,
            )
            tissue_printer = self._resolve_quick_printer(
                str(print_settings.get("tissue_printer") or ""),
                default_printer,
                available,
            )
            if quick_mode == "direct" and not sample_printer:
                return False

            sample_paper_type = str(print_settings.get("sample_paper_type") or "")
            tissue_paper_type = str(print_settings.get("tissue_paper_type") or "")
            paper_types = {
                k: v for k, v in {
                    "sample": sample_paper_type,
                    "tissue": tissue_paper_type,
                }.items() if v in {"label", "a4", "a5"}
            } or None
            template_keys = {
                k: v for k, v in {
                    "sample": str(print_settings.get("sample_template_key") or ""),
                    "tissue": str(print_settings.get("tissue_template_key") or ""),
                }.items() if v
            } or None

            specimens = label_service.load_specimen_dicts(db)
            jobs = label_service.LabelService.quick_print_jobs_for_specimen(
                specimens, uid, paper_types=paper_types, template_keys=template_keys
            )
            if not print_settings.get("include_tissue", True):
                jobs = [j for j in jobs if j.get("bucket") != "tissue"]
            if not jobs:
                return False

            direct_jobs: list[dict] = []
            queued_tissue = 0
            for job in jobs:
                if job.get("bucket") == "tissue":
                    if quick_mode == "direct" and not tissue_printer:
                        return False
                    effective_tissue_paper = tissue_paper_type or label_service.persisted_paper_type("tissue")
                    if self._should_queue_tissue_label(
                        print_settings,
                        sample_printer=sample_printer,
                        tissue_printer=tissue_printer,
                        tissue_paper_type=effective_tissue_paper,
                    ):
                        from app.services import rna_label_queue_service as rna_queue
                        queued_tissue += rna_queue.enqueue(db, [uid])
                        self._sync_rna_queue_count()
                        continue
                direct_jobs.append(job)

            if not direct_jobs:
                if queued_tissue:
                    from app.services import rna_label_queue_service as rna_queue
                    self._status_message(f"RNAlater 标签已加入合版队列：当前 {rna_queue.pending_count(db)} 张")
                return True

            printed = 0
            printers_used: list[str] = []
            printed_details: list[str] = []
            try:
                from app.services.activity_audit_service import default_actor, record_print_jobs
                actor = default_actor(self.ctx)
            except Exception:
                record_print_jobs = None
                actor = ""

            if quick_mode == "dialog":
                # ── Dialog path: user picks printer, all jobs printed together ──
                if windows_print.is_available():
                    ok, printer_name = windows_print.print_jobs_with_windows_dialog(
                        direct_jobs, document_name=f"标本标签 {uid}"
                    )
                    if not ok:
                        # User cancelled the system dialog; this is handled and
                        # must not navigate to the template-design workspace.
                        return True
                    if record_print_jobs is not None:
                        try:
                            record_print_jobs(
                                db, direct_jobs, actor=actor,
                                printer_name=printer_name or "Windows 打印机",
                            )
                        except Exception:
                            pass
                    printed = sum(len(j.get("labels") or []) for j in direct_jobs)
                    printer_display = printer_name or "Windows 打印机"
                    printers_used = [printer_display] if printed else []
                    printed_details = [f"{len(direct_jobs)} 个作业 → {printer_display}"]
                else:
                    from app.widgets.print_dialog import PrintJobDialog
                    dlg = PrintJobDialog(direct_jobs, self)
                    if dlg.exec() != QDialog.DialogCode.Accepted:
                        if queued_tissue:
                            from app.services import rna_label_queue_service as rna_queue
                            self._status_message(f"RNAlater 标签已加入合版队列：当前 {rna_queue.pending_count(db)} 张")
                        return False

                    printer_name = dlg.selected_printer()
                    printer = label_print.build_printer(direct_jobs[0])
                    if printer_name:
                        printer.setPrinterName(printer_name)

                    if not label_print.paint_jobs(printer, direct_jobs):
                        return False
                    if record_print_jobs is not None:
                        try:
                            record_print_jobs(
                                db, direct_jobs, actor=actor,
                                printer_name=printer_name or printer.printerName() or "default",
                            )
                        except Exception:
                            pass
                    printed = sum(len(j.get("labels") or []) for j in direct_jobs)
                    printer_display = printer_name or printer.printerName() or "默认打印机"
                    printers_used = [printer_display] if printed else []
                    printed_details = [f"{len(direct_jobs)} 个作业 → {printer_display}"]
            else:
                # ── Direct path: each job to its configured printer ──
                for job in direct_jobs:
                    target = tissue_printer if job.get("bucket") == "tissue" else sample_printer
                    if not target:
                        return False
                    if windows_print.is_available():
                        ok, used_printer = windows_print.print_jobs_with_windows_dialog(
                            [job], document_name=f"标本标签 {uid}",
                            printer_name=target, show_dialog=False,
                        )
                        if not ok:
                            return False
                        target = used_printer or target
                    else:
                        printer = label_print.build_printer(job)
                        printer.setPrinterName(target)
                        if not label_print.paint_jobs(printer, [job]):
                            return False
                    if record_print_jobs is not None:
                        try:
                            record_print_jobs(db, [job], actor=actor, printer_name=target)
                        except Exception:
                            pass
                    printed += len(job.get("labels") or [])
                    if target not in printers_used:
                        printers_used.append(target)
                    printed_details.append(self._quick_print_job_label(job, target))

            msg_parts = []
            if printed:
                msg_parts.append(
                    f"已发送 {printed} 张标签到 {'、'.join(printers_used)}"
                    f"（{'; '.join(printed_details)}）"
                )
            if queued_tissue:
                from app.services import rna_label_queue_service as rna_queue
                msg_parts.append(f"RNAlater 标签已加入合版队列：当前 {rna_queue.pending_count(db)} 张")
            if msg_parts:
                self._status_message("；".join(msg_parts))
            return True
        except Exception:
            return False

    def _sync_rna_queue_count(self) -> None:
        db = None
        try:
            db = self.ctx.get_db()
        except Exception:
            db = None
        count = 0
        if db is not None:
            try:
                from app.services import rna_label_queue_service as rna_queue
                count = rna_queue.pending_count(db)
            except Exception:
                count = 0
        try:
            self._sidebar.set_rna_queue_count(count)
        except Exception:
            pass

    @staticmethod
    def _quick_print_job_label(job: dict, printer_name: str) -> str:
        bucket = "RNAlater" if job.get("bucket") == "tissue" else "样品瓶"
        tmpl = job.get("template") or {}
        template_name = str(tmpl.get("name") or "未命名模板")
        paper = str(job.get("paperType") or "label").upper()
        return f"{bucket}: {template_name} / {paper} / {printer_name}"

    @staticmethod
    def _resolve_quick_printer(configured: str, default_printer: str, available: set[str]) -> str:
        configured = (configured or "").strip()
        if configured:
            return configured if configured in available else ""
        return default_printer or ""

    @staticmethod
    def _should_queue_tissue_label(
        settings: dict,
        *,
        sample_printer: str,
        tissue_printer: str,
        tissue_paper_type: str,
    ) -> bool:
        strategy = str(settings.get("tissue_strategy") or "auto")
        if strategy == "queue":
            return True
        if strategy == "direct":
            return False
        return (
            bool(sample_printer)
            and sample_printer == tissue_printer
            and tissue_paper_type in {"a4", "a5"}
        )

    def _on_print_rna_queue(self) -> None:
        """Open the RNAlater sheet queue preview, then print or clear."""
        try:
            from PyQt6.QtPrintSupport import QPrinterInfo
            from app.services import label_service
            from app.services import project_settings_service as pss
            from app.services import rna_label_queue_service as rna_queue
            from app.services.label_print_executor import LabelPrintExecutor
            from app.utils.label_core import unique_id
            import app.utils.label_print as label_print
            from app.utils.label_sheet import draw_crop_marks

            db = self.ctx.get_db()
            project_dir = getattr(self.ctx, "current_project_dir", None)
            project_root = getattr(self.ctx, "current_project_root", None)
            if not isinstance(project_root, str):
                project_root = None
            if db is None or not project_dir:
                self._status_message("请先打开一个项目工作区。")
                return

            uids = rna_queue.pending_uids(db)
            if not uids:
                self._status_message("没有待打印的 RNAlater 合版标签。")
                self._sync_rna_queue_count()
                return

            settings = pss.effective_print_settings(
                project_dir, root=project_root
            )
            local_print_settings = pss.load_setting_if_present(db, "print_settings")
            if local_print_settings is not None:
                settings = pss.merge_print_settings(settings, local_print_settings)

            default_printer = QPrinterInfo.defaultPrinterName()
            available = {
                p.printerName()
                for p in QPrinterInfo.availablePrinters()
                if p.printerName()
            }
            tissue_printer = self._resolve_quick_printer(
                str(settings.get("tissue_printer") or ""),
                default_printer,
                available,
            )
            if not tissue_printer:
                QMessageBox.information(self, "打印 RNA 合版", "未找到 RNAlater 标签打印机。")
                return

            specimens = label_service.load_specimen_dicts(db)
            wanted = set(uids)
            indices = [i for i, sp in enumerate(specimens) if unique_id(sp) in wanted]
            if not indices:
                self._status_message("队列里的 RNAlater 标签没有对应标本记录。")
                return

            lib = label_service.LabelTemplateLibrary("tissue")
            paper_type = (
                str(settings.get("tissue_paper_type") or "")
                or label_service.persisted_paper_type("tissue")
            )
            if paper_type not in {"a4", "a5"}:
                paper_type = str((settings.get("tissue_sheet") or {}).get("paper") or "a4")
            if paper_type not in {"a4", "a5"}:
                paper_type = "a4"
            grid_opts = label_service.persisted_imposition("tissue")
            if "cutMarks" not in grid_opts:
                grid_opts = dict(grid_opts)
                grid_opts["cutMarks"] = True

            job = label_service.LabelService.build_print_job(
                specimens,
                label_service.resolve_template(lib),
                "tissue",
                selected_indices=indices,
                dims=label_service.resolve_dims(lib, lib.selected_custom_dims()),
                copies=1,
                paper_type=paper_type,
                paper=label_service.PAPER_SIZES.get(paper_type),
            )
            if not (job.get("items") or []):
                self._status_message("队列中没有可生成的 RNAlater 标签。")
                return
            job["gridOpts"] = grid_opts

            ordered_uids = [unique_id(specimens[i]) for i in indices]
            dlg = _RnaQueueDialog(ordered_uids, job, grid_opts, self)
            if dlg.exec() != QDialog.DialogCode.Accepted:
                return
            if dlg.action == "clear":
                changed = rna_queue.clear_pending(db)
                self._sync_rna_queue_count()
                self._status_message(f"已清空 RNAlater 待打印队列：{changed} 张")
                return
            if dlg.action != "print":
                return

            result = LabelPrintExecutor(
                ctx=self.ctx,
                parent=self,
                build_printer_fn=label_print.build_printer,
                paint_jobs_fn=label_print.paint_jobs,
            ).print_direct(
                [job],
                printer_name=tissue_printer,
                document_name="RNAlater 合版标签",
                grid_opts=grid_opts,
                cut_marks=bool(grid_opts.get("cutMarks")),
                draw_crop_marks=draw_crop_marks,
            )
            if not result.printed:
                QMessageBox.warning(self, "打印 RNA 合版", "打印任务发送失败。")
                return
            rna_queue.mark_printed(db, ordered_uids)
            self._sync_rna_queue_count()
            used_printer = result.printer_name or tissue_printer
            self._status_message(
                f"RNAlater 合版已发送到 {used_printer} · {len(ordered_uids)} 张"
            )
        except Exception as exc:
            QMessageBox.warning(self, "打印 RNA 合版", str(exc))

    def _status_message(self, text: str, msec: int = 4000) -> None:
        ui.show_status(self, text, msec)

    def _workflow_notice(
        self,
        title: str,
        detail: str = "",
        *,
        state: str = "busy",
        force_show: bool = False,
        task_key: str | None = None,
    ) -> None:
        try:
            try:
                self._monitor.clear_workflow_notice()
            except Exception:
                pass
            if str(title or "").startswith("合成+整理"):
                dlg = getattr(self, "_compose_organise_progress_dialog", None)
                if dlg is None:
                    dlg = _ComposeOrganiseProgressDialog(self)
                    dlg.cancel_requested.connect(self._cancel_workflow_task)
                    self._compose_organise_progress_dialog = dlg
                dlg.set_notice(
                    title,
                    detail,
                    state=state,
                    force_show=force_show,
                    task_key=task_key,
                )
                return
            dlg = getattr(self, "_workflow_notice_panel", None)
            if dlg is None:
                dlg = _WorkflowNoticePanel(self)
                self._workflow_notice_panel = dlg
            dlg.set_notice(
                title,
                detail,
                state=state,
                force_show=force_show,
                task_key=task_key,
            )
        except Exception:
            pass

    def _workflow_notice_text(self) -> tuple[str, str, str]:
        dlg = getattr(self, "_compose_organise_progress_dialog", None)
        if dlg is not None:
            try:
                return dlg.notice_text()
            except Exception:
                pass
        dlg = getattr(self, "_workflow_notice_panel", None)
        if dlg is None:
            return ("", "", "")
        try:
            return dlg.notice_text()
        except Exception:
            return ("", "", "")

    def _cancel_workflow_task(self, task_key: str) -> None:
        key = str(task_key or "")
        cancelled = False
        archive_map = getattr(self, "_archive_worker_by_task_key", {})
        worker = archive_map.get(key) if isinstance(archive_map, dict) else None
        if worker is not None:
            try:
                worker.cancel()
                cancelled = True
            except Exception:
                pass
        helicon_map = getattr(self, "_helicon_worker_by_task_key", {})
        hworker = helicon_map.get(key) if isinstance(helicon_map, dict) else None
        if hworker is not None:
            try:
                hworker.cancel()
                cancelled = True
            except Exception:
                pass
        if cancelled:
            self._workflow_notice(
                "合成+整理：正在取消",
                "正在停止当前后台任务；不会删除 JPG，未完成 ZIP 会被清理。",
                state="busy",
                task_key=key,
            )
            self._status_message("正在取消当前任务…")
        else:
            self._workflow_notice(
                "合成+整理无法取消",
                "当前阶段没有可取消的后台任务，可能已经完成或正在收尾登记。",
                state="info",
                task_key=key,
            )

    def _on_naming_uid_generated(self, uid: str) -> None:
        """Treat the current/active specimen UID as the row being edited.

        The naming card emits the specimen voucher UID while the result sequence
        can still change from 1 to 2, 3...  That must not be interpreted as a
        duplicate specimen when the left sidebar already shows the same UID as
        the current active/editing target.
        """
        text = str(uid or "").strip()
        if not text:
            return
        current_uid = self._current_uid
        active_uid = self._get_active_uid()
        if current_uid:
            if text != current_uid:
                return
        elif text != active_uid:
            return
        db = self.ctx.get_db()
        if not db:
            return
        try:
            row = db.execute("SELECT 1 FROM specimens WHERE uid = ?", (text,)).fetchone()
        except Exception:
            row = None
        if row:
            self._naming.acknowledge_existing_uid(text)

    def _on_uid_corrected(self, old_uid: str, new_uid: str) -> None:
        """Handle UID change after storage correction in NamingPanel.

        Updates _current_uid, refreshes the sidebar, and reloads migrated results.
        """
        if self._current_uid == old_uid:
            self._current_uid = new_uid
        self._sidebar.refresh()
        if new_uid:
            self._sidebar.select_uid(new_uid)
        db = self.ctx.get_db()
        if db and new_uid:
            try:
                from app.services.specimen_rename_service import (
                    repair_grouping_result_files_for_uid,
                )
                from app.services.grouping_service import load_grouping

                repair_grouping_result_files_for_uid(db, new_uid)
                grouping = load_grouping(db, new_uid)
                self._grouping.load_grouping(new_uid, grouping)
                self._refresh_results_column(new_uid, grouping)
            except Exception:
                self._refresh_results_column(new_uid, None)
        self._naming.refresh_legacy_photo_notes()
        self._try_bind_adhoc_to_existing_specimen(new_uid or self._naming.current_uid())
        if self._sync_grouping_outputs_from_naming():
            self._status_message("保存方式已写入编号，待整理输出名已同步更新。")
        else:
            self._status_message("保存方式已写入编号，成果文件名已同步更新。")

    def _claim_current_grouping_for_saved_uid(self, uid: str) -> bool:
        """Attach the currently-open temporary grouping to a newly saved UID.

        Users often create groups before they have saved the voucher number.
        In that flow the grouping panel is backed by the ad-hoc placeholder;
        once the right rail is saved, the visible groups must move with the
        new specimen so the sidebar progress and later organise actions target
        the real UID.
        """
        text = str(uid or "").strip()
        if not text:
            return False
        db = self.ctx.get_db()
        if not db:
            return False

        grouping = getattr(self._grouping, "_grouping", None)
        if grouping is None:
            return False

        panel_uid = getattr(self._grouping, "_uid", None)
        groups = list(getattr(grouping, "groups", []) or [])

        from app.services.capture_workflow_service import (
            persist_grouping_claim,
            prepare_grouping_claim,
        )
        from app.services.grouping_service import SpecimenGrouping

        plan = prepare_grouping_claim(db, text, panel_uid, groups)
        if not plan.should_persist:
            return False

        grouping.groups = plan.groups
        if plan.claimed:
            self._grouping.load_grouping(text, SpecimenGrouping(uid=text, groups=plan.groups))
        self._sync_grouping_outputs_from_naming()
        persist_grouping_claim(db, plan)
        return plan.claimed

    def _on_naming_save(self) -> None:
        """Persist the naming panel's current UID into the specimens table.

        Mirrors the web 「💾 保存」 button: upsert a specimen row keyed by the
        live-preview UID with the seven naming segments.  Chinese fields are
        never auto-filled (hard rule).
        """
        db = self.ctx.get_db()
        project_dir = self.ctx.current_project_dir
        if not db or not project_dir:
            self._status_message("请先打开一个项目工作区。")
            return
        uid = self._naming.current_uid()
        if not uid:
            self._status_message("编号尚未填写完整。")
            return
        dlg_parent = self._grouping_ui_parent()
        missing_required = [
            field for field in self._naming.missing_required_fields()
            if field not in ("采集日期", "拍照日期", "拍摄日期")
        ]
        if missing_required:
            self._status_message("编号信息未填写完整：" + "、".join(missing_required))
            QMessageBox.warning(
                dlg_parent,
                "编号信息未填写完整",
                "请先补全必填字段：" + "、".join(missing_required),
            )
            self._naming._check_compliance(uid)
            return
        persisted_uid = self._naming.persisted_uid()
        old_uid = (
            persisted_uid
            if persisted_uid and persisted_uid != uid
            else None
        )
        old_raw: dict = {}
        if old_uid:
            try:
                row = db.execute("SELECT raw_json FROM specimens WHERE uid=?", (old_uid,)).fetchone()
                old_raw = json.loads(row["raw_json"] or "{}") if row else {}
                if not isinstance(old_raw, dict):
                    old_raw = {}
            except Exception:
                old_raw = {}

        # 采集日期软必填：它是编号核心字段、会写入 UID 日期段。空着强提醒，但允许继续
        # （兼容采集日期确实未知的标本——编号自动少一段）。默认「返回填写」。
        if not self._naming._collection_date.text().strip():
            reply = QMessageBox.question(
                dlg_parent, "采集日期未填",
                "采集日期是编号核心字段，会写入唯一编号。\n\n"
                "未知可留空继续（编号自动少日期段），或返回填写。",
                QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            if reply != QMessageBox.StandardButton.Save:
                return

        # ── UID uniqueness (mirrors web server.js HTTP 409) ─────────────────
        # A specimen UID is a museum-style catalogue number.
        # (a) Cross-workspace: scan every OTHER known workspace's project.db;
        #     if this UID is already owned there → refuse (global unique).
        # (b) Same-workspace cover: editing a loaded specimen (_current_uid) and
        #     changing its fields to another UID that already exists here →
        #     refuse (would overwrite a different specimen via upsert).
        # Re-saving the very same UID (update self, incl. _current_uid is None
        # and the row already exists) is allowed — PRIMARY KEY upsert handles it.
        from app.services import specimen_catalog_service as _catalog
        try:
            _current_resolved = str(Path(project_dir).resolve())
        except OSError:
            _current_resolved = project_dir
        _other_hits = []
        if persisted_uid != uid:
            for h in _catalog.find_uid(
                uid,
                current_project_dir=project_dir,
                current_project_root=getattr(self.ctx, "current_project_root", None),
            ):
                try:
                    hit_resolved = str(Path(h.project_dir).resolve())
                except OSError:
                    hit_resolved = h.project_dir
                if hit_resolved != _current_resolved:
                    _other_hits.append(h)
        _local_owner = db.execute(
            "SELECT owner_project_dir FROM specimens WHERE uid=?", (uid,)
        ).fetchone()
        _local_cover = (
            _local_owner is not None
            and persisted_uid
            and persisted_uid != uid
        )
        if _other_hits:
            self._status_message(f"编号已被占用：{uid}")
            QMessageBox.warning(
                dlg_parent, "编号已被占用",
                "⚠ " + _catalog.format_uid_conflict(uid, _other_hits),
            )
            self._naming.show_dup_warn(True)
            return
        if _local_cover:
            self._status_message(f"编号已存在于本项目：{uid}")
            QMessageBox.warning(
                dlg_parent, "编号已被占用",
                f"⚠ 编号 {uid} 已存在于本项目（另一个标本），不能覆盖。请修改字段后再保存。",
            )
            self._naming.show_dup_warn(True)
            return

        # ── Collaboration UID claim ───────────────────────────────────────
        # When collaboration is active (running + a group code), claim a NEW
        # UID across the LAN so no teammate can reuse it.  Re-saving a UID that
        # is already a local specimen is an update, not a new claim.
        svc = getattr(self.ctx, "collab_service", None)
        if svc is not None and svc.is_running() and svc.group_code:
            is_local = db.execute(
                "SELECT 1 FROM specimens WHERE uid=?", (uid,)
            ).fetchone() is not None
            if not is_local:
                ok, msg = svc.create_task(uid, assignee=self._collab_operator())
                if not ok:
                    self._status_message(f"协作编号占用：{uid}")
                    QMessageBox.warning(
                        dlg_parent, "编号已被占用",
                        f"编号 {uid} 已被占用：{msg}\n请改用其他编号后再保存。",
                    )
                    self._naming._apply_sequence_suggestion()
                    return

        n = self._naming
        from app.utils.naming import coalesce_specimen_dates
        collection_date, photo_date = coalesce_specimen_dates(
            n._collection_date.text().strip(),
            n._photo_date.text().strip(),
        )
        try:
            db.execute(
                """
                INSERT INTO specimens (uid, id, province, site, station,
                                       storage, collection_date, photo_date,
                                       photo_notes, owner_project_dir)
                VALUES (?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(uid) DO UPDATE SET
                    id=excluded.id, province=excluded.province,
                    site=excluded.site, station=excluded.station,
                    storage=excluded.storage,
                    collection_date=excluded.collection_date,
                    photo_date=excluded.photo_date,
                    photo_notes=excluded.photo_notes,
                    owner_project_dir=excluded.owner_project_dir
                """,
                (
                    uid,
                    n._species_id.text().strip(),
                    n._province.text().strip(),
                    n._site.text().strip(),
                    n._station.text().strip(),
                    n._storage.text().strip(),
                    collection_date,
                    photo_date,
                    n._photo_notes.toPlainText().strip(),
                    project_dir,
                ),
            )
            if collection_date and not n._collection_date.text().strip():
                n._collection_date.setText(collection_date)
            if photo_date and not n._photo_date.text().strip():
                n._photo_date.setText(photo_date)
            # 命名行已建/更新 → 标记为当前标本，再 flush 右栏三卡。
            # 关键修复（场景1 疑点1+2）：新草稿 _current_uid 原为 None，metadata
            # autosave 整段被 _schedule_rail_save 跳过；保存只写命名段 → 用户在
            # metadata 卡填的 采集人/经纬度/地理区/分类 会静默丢失。这里先设
            # _current_uid（行已存在），再调 _on_save_metadata 把右栏一并入库，
            # 使「保存」= 存全部。
            self._current_uid = uid
            self._on_save_metadata(uid, reload=False, commit=False)
            if old_uid:
                self._finalize_uid_rename(old_uid, uid, old_raw)
            claimed_grouping = self._claim_current_grouping_for_saved_uid(uid)
            db.commit()
            self._naming.acknowledge_existing_uid(uid)
            self._sidebar.refresh()
            self._sidebar.select_uid(uid)
            # 「新建编号后自动激活」开关（默认关，复刻 oracle
            # autoActivateOnNewSpecimen app.js:9396-9397）：开则保存即把此号设为
            # 当前激活标本，省去手动点激活；关则不动激活（守 oracle 默认）。
            if bool(getattr(self.ctx.settings, "auto_activate_on_new_specimen", False)):
                self._on_sidebar_activate(uid)
            elif claimed_grouping:
                self._status_message(f"已添加到左侧并绑定当前分组：{uid}")
            else:
                self._status_message(f"已保存编号：{uid}")
        except Exception as exc:
            self._status_message(f"保存失败：{exc}")
            QMessageBox.warning(dlg_parent, "保存失败", str(exc))

    def _on_naming_add(self) -> None:
        """Add the visible voucher as a new/current specimen, without renaming old."""
        self._naming.mark_current_values_as_new()
        self._current_uid = None
        self._on_naming_save()

    def _on_naming_update_results(self) -> None:
        """Save current naming fields, then rename registered result TIFF/ZIP files."""
        uid = self._naming.current_uid()
        if not uid:
            self._status_message("编号尚未填写完整，无法更新成果文件名。")
            return

        self._on_naming_save()
        persisted_uid = self._naming.persisted_uid()
        if persisted_uid != uid:
            self._status_message("编号尚未成功保存，未更新成果文件名。")
            return

        try:
            changed = self._sync_result_files_to_current_naming(persisted_uid)
        except Exception as exc:
            self._status_message(f"成果文件名更新失败：{exc}")
            QMessageBox.warning(self._grouping_ui_parent(), "成果文件名更新失败", str(exc))
            return

        if changed:
            self._status_message(f"已更新 {changed} 个成果文件名。")
        else:
            self._status_message("成果文件名已是当前编号，无需更新。")

    def _current_naming_result_values(self) -> dict[str, str]:
        from app.utils.naming import coalesce_specimen_dates, specimen_date_seg

        collection_date, photo_date = coalesce_specimen_dates(
            self._naming._collection_date.text().strip(),
            self._naming._photo_date.text().strip(),
        )
        values = {
            "province": self._naming._province.text().strip(),
            "site": self._naming._site.text().strip(),
            "station": self._naming._station.text().strip(),
            "species_id": self._naming._species_id.text().strip(),
            "storage": self._naming._storage.text().strip(),
            "date_seg": specimen_date_seg(collection_date, photo_date),
            "collection_date": collection_date,
            "photo_date": photo_date,
        }
        try:
            values.update(self._naming.naming_component_values())
        except Exception:
            pass
        return values

    def _suggest_current_result_filename(
        self,
        path: Path,
        *,
        components: list[str],
        values: dict[str, str],
        seq: int,
    ) -> tuple[str, str]:
        """Return (filename, parsed_uid) for *path* under current naming fields."""
        from app.utils.naming import recognize_tiff_filename, suggest_tiff_filename_preserve_legacy

        rec = recognize_tiff_filename(path.stem, components)
        parsed_uid = rec.uid if rec else ""
        effective_seq = rec.sequence if rec else seq
        if rec:
            stem = suggest_tiff_filename_preserve_legacy(
                path.stem,
                components,
                values,
                seq=effective_seq,
            )
        else:
            stem = self._naming.suggested_result_stem(seq=effective_seq)
        return stem + path.suffix, parsed_uid

    def _sync_result_files_to_current_naming(self, uid: str) -> int:
        """Rename registered result files for *uid* to match the right-rail naming."""
        db = self.ctx.get_db()
        if not db:
            return 0

        grouping = self._get_grouping_for_uid(uid)
        if grouping is None or not getattr(grouping, "groups", None):
            return 0

        components = self._load_naming_components()
        values = self._current_naming_result_values()
        plans: list[tuple[Path, Path, str, str]] = []
        planned_sources: set[Path] = set()
        group_updates: dict[int, dict[str, str]] = {}

        def add_result_file_rename_plan(
            src: Path, dst: Path, old_uid: str = "", new_uid: str = ""
        ) -> None:
            if src == dst:
                return
            if not src.is_file():
                return
            plans.append((src, dst, old_uid, new_uid))
            planned_sources.add(src)

        for group in grouping.groups:
            try:
                fallback_seq = int(
                    getattr(group, "result_sequence", None)
                    or getattr(group, "group_index", 0) + 1
                )
            except (TypeError, ValueError):
                fallback_seq = int(getattr(group, "group_index", 0) or 0) + 1

            updates: dict[str, str] = {}
            tiff_path_text = getattr(group, "composed_tiff_path", None) or ""
            tiff_path = Path(tiff_path_text) if tiff_path_text else None
            new_tiff_path = None
            old_uid_for_group = ""

            if tiff_path and tiff_path.is_file():
                new_name, old_uid_for_group = self._suggest_current_result_filename(
                    tiff_path,
                    components=components,
                    values=values,
                    seq=fallback_seq,
                )
                new_tiff_path = tiff_path.with_name(new_name)
                add_result_file_rename_plan(tiff_path, new_tiff_path, old_uid_for_group, uid)
                updates["composed_tiff_path"] = str(new_tiff_path)
                updates["output_name"] = new_tiff_path.stem

            zip_paths: list[Path] = []
            archive_zip = getattr(group, "archive_zip", None) or ""
            if archive_zip:
                zip_paths.append(Path(archive_zip))
            if tiff_path:
                sibling_zip = tiff_path.with_suffix(".zip")
                if sibling_zip.is_file() and sibling_zip not in zip_paths:
                    zip_paths.append(sibling_zip)

            for zip_path in zip_paths:
                if not zip_path.is_file():
                    continue
                if new_tiff_path is not None and tiff_path and zip_path.stem == tiff_path.stem:
                    new_zip_path = new_tiff_path.with_suffix(".zip")
                else:
                    new_zip_name, zip_old_uid = self._suggest_current_result_filename(
                        zip_path,
                        components=components,
                        values=values,
                        seq=fallback_seq,
                    )
                    old_uid_for_group = old_uid_for_group or zip_old_uid
                    new_zip_path = zip_path.with_name(new_zip_name)
                add_result_file_rename_plan(zip_path, new_zip_path, old_uid_for_group, uid)
                updates["archive_zip"] = str(new_zip_path)

            if updates:
                group_updates[int(getattr(group, "group_index", 0) or 0)] = updates

        seen_targets: set[Path] = set()
        for src, dst, _old_uid, _new_uid in plans:
            if dst.exists() and dst != src and dst not in planned_sources:
                raise ValueError(f"目标文件已存在，无法更新：{dst}")
            if dst in seen_targets:
                raise ValueError(f"多个成果会写到同一个目标文件，已中止：{dst}")
            seen_targets.add(dst)

        from app.services.specimen_rename_service import _rewrite_zip_manifest

        changed = 0
        for src, dst, old_uid, new_uid in plans:
            if src == dst:
                continue
            os.replace(str(src), str(dst))
            changed += 1
            if dst.suffix.lower() == ".zip" and old_uid and new_uid and old_uid != new_uid:
                _rewrite_zip_manifest(str(dst), old_uid, new_uid)

        if group_updates:
            for group in grouping.groups:
                updates = group_updates.get(int(getattr(group, "group_index", 0) or 0))
                if not updates:
                    continue
                if "composed_tiff_path" in updates:
                    group.composed_tiff_path = updates["composed_tiff_path"]
                if "archive_zip" in updates:
                    group.archive_zip = updates["archive_zip"]
                if "output_name" in updates:
                    group.output_name = updates["output_name"]
            self._save_grouping_for_uid(uid, grouping.groups)
            grouping = self._get_grouping_for_uid(uid)
            self._refresh_results_column(uid, grouping)

        return changed

    def _finalize_uid_rename(self, old_uid: str, new_uid: str, old_raw: Optional[dict] = None) -> None:
        """Move DB references after right-rail editing changes the generated UID."""
        if not old_uid or not new_uid or old_uid == new_uid:
            return
        db = self.ctx.get_db()
        if not db:
            return
        raw = dict(old_raw or {})
        n = self._naming
        raw.update({
            "uid": new_uid,
            "id": n._species_id.text().strip(),
            "province": n._province.text().strip(),
            "site": n._site.text().strip(),
            "station": n._station.text().strip(),
            "storage": n._storage.text().strip(),
            "collectionDate": n._collection_date.text().strip(),
            "photoDate": n._photo_date.text().strip(),
        })
        try:
            raw.update(n.naming_extra_field_values())
        except Exception:
            pass
        prev = raw.get("previousUniqueIds") or []
        if not isinstance(prev, list):
            prev = []
        if old_uid not in prev:
            prev.append(old_uid)
        raw["previousUniqueIds"] = prev
        try:
            from app.services.specimen_rename_service import migrate_uid_references
            with db:
                migrate_uid_references(db, old_uid, new_uid)
                try:
                    db.execute(
                        "UPDATE photo_assignments SET specimen_uid=? WHERE specimen_uid=?",
                        (new_uid, old_uid),
                    )
                except Exception:
                    pass
                db.execute("DELETE FROM specimens WHERE uid=?", (old_uid,))
                db.execute(
                    "UPDATE specimens SET raw_json=? WHERE uid=?",
                    (json.dumps(raw, ensure_ascii=False), new_uid),
                )
        except Exception as exc:
            QMessageBox.warning(self, "编号迁移失败", str(exc))

    def _seed_helicon_defaults(self) -> None:
        """Seed the compose params panel from saved Helicon defaults (QSettings).

        Keeps the per-compose panel in sync with the 「保存为默认」 stored by the
        top-bar Helicon 配置 dialog. Failures are non-fatal (panel keeps its own
        hardcoded defaults).
        """
        try:
            from app.views.settings_view import (
                _K_HELICON_METHOD, _K_HELICON_RADIUS, _K_HELICON_SMOOTHING,
            )
            qs = self.ctx.settings._qs
            self._helicon_params.set_params({
                "method": int(qs.value(_K_HELICON_METHOD, 1)),
                "radius": float(qs.value(_K_HELICON_RADIUS, 8.0)),
                "smoothing": int(qs.value(_K_HELICON_SMOOTHING, 4)),
            })
        except Exception:
            pass

    def _build_grouping_dialog(self, panel: QWidget) -> QDialog:
        """Host the GroupingPanel in a non-modal popup.

        Non-modal so the user can still drag/select files in the monitor while
        the grouping tool is open (the monitor → group flow relies on it).
        """
        dlg = QDialog(self)
        dlg.setObjectName("GroupingDialog")
        dlg.setWindowTitle("分组工具")
        dlg.setModal(False)
        lay = QVBoxLayout(dlg)
        # Pad so the inner WorkbenchSection card's drop-shadow is not clipped —
        # the card floats on the soft-gray dialog bg (QDialog#GroupingDialog).
        lay.setContentsMargins(14, 14, 14, 14)
        # Collapse header row is redundant inside a dedicated popup.
        header = getattr(panel, "_group_header_widget", None)
        if header is not None:
            header.setVisible(False)
        divider = getattr(panel, "_header_divider", None)
        if divider is not None:
            divider.setVisible(False)
        lay.addWidget(panel)
        dlg.resize(760, 560)
        return dlg

    def _grouping_ui_parent(self) -> QWidget:
        """Dialog parent while the grouping popup is open — keeps pickers on top."""
        g = getattr(self, "_grouping_dialog", None)
        if g is not None and g.isVisible():
            return g
        return self

    def _open_grouping_helicon_params(self) -> None:
        """从分组工具的“更多”按需弹出 Helicon 参数。"""
        dlg = getattr(self, "_grouping_helicon_params_dialog", None)
        if dlg is None:
            dlg = QDialog(self._grouping_dialog)
            dlg.setWindowTitle("Helicon 合成参数")
            layout = QVBoxLayout(dlg)
            layout.setContentsMargins(16, 16, 16, 16)
            layout.addWidget(self._helicon_params)
            dlg.resize(720, 330)
            self._grouping_helicon_params_dialog = dlg
        dlg.show()
        dlg.raise_()
        dlg.activateWindow()

    def _load_temporary_grouping_task(self, label: str = "未命名（临时分组）") -> None:
        """Bind the grouping editor to a fresh unassigned task."""
        from app.services.grouping_service import ADHOC_GROUPING_UID, SpecimenGrouping

        self._grouping.load_grouping(
            ADHOC_GROUPING_UID,
            SpecimenGrouping(uid=ADHOC_GROUPING_UID, groups=[]),
        )
        self._grouping._uid_label.setText(label)
        self._grouping._target_label.setText("临时")

    def _on_open_grouping(self) -> None:
        """Open (or re-focus) the grouping/compose popup — web 分组工具 toggle.

        有激活编号时归属激活编号；没有激活编号时进入未归属临时任务。
        左侧选中/右侧正在编辑的未激活编号不应隐式成为拍照分组目标。
        """
        from app.services.grouping_service import (
            ADHOC_GROUPING_UID,
            SpecimenGrouping,
            load_grouping,
        )
        uid = self._get_active_uid()
        if not uid:
            if getattr(self._grouping, "_uid", None) != ADHOC_GROUPING_UID:
                self._load_temporary_grouping_task("未归属（可后续归入编号）")
            else:
                self._grouping._uid_label.setText("未归属（可后续归入编号）")
                self._grouping._target_label.setText("临时")
            self._status_message("当前没有激活编号：新组将暂存为未归属分组。", 5000)
            uid = ADHOC_GROUPING_UID
        if getattr(self._grouping, "_uid", None) != uid:
            db = self.ctx.get_db()
            try:
                if db:
                    grouping = load_grouping(db, uid)
                else:
                    grouping = SpecimenGrouping(uid=uid, groups=[])
                self._grouping.load_grouping(uid, grouping)
            except Exception:
                pass
        self._recognize_first_grouping_tiff(getattr(self._grouping, "_grouping", None))
        dlg = self._grouping_dialog
        dlg.show()
        dlg.raise_()
        dlg.activateWindow()

    def _on_legacy_photo_batch_organize(self) -> None:
        """Expose old JPG/TIF folder organising as a first-class monitor action."""
        self._on_open_grouping()
        self._on_auto_group_organize()

    def _reset_to_unassigned_task(self) -> None:
        """Clear specimen-bound UI after deactivation and start a fresh temp task."""
        self._current_uid = None
        self._pending_grouping = None
        try:
            self._save_timer.stop()
            self._rail_save_timer.stop()
        except Exception:
            pass

        self._naming.load_specimen({})
        try:
            self._metadata.clear()
            self._taxon_card.clear()
            self._apply_draft_project_defaults()
        except Exception:
            pass

        try:
            self._results.clear()
        except Exception:
            pass

        # Deactivation removes specimen ownership. Keep the grouping tool usable
        # as an unassigned task so new groups can later be saved into a UID.
        self._load_temporary_grouping_task("未归属（新任务）")
        self._refresh_batch_header()

    def _toggle_right_rail(self) -> None:
        """Collapse/expand the whole naming rail (web rightPanelCollapsed)."""
        self._right_rail_collapsed = not self._right_rail_collapsed
        c = self._right_rail_collapsed
        for w in (self._naming, self._taxon_card, self._metadata):
            w.setVisible(not c)
        if c:
            self._right_scroll.setMinimumWidth(48)
            self._right_scroll.setMaximumWidth(48)
        else:
            self._right_scroll.setMinimumWidth(280)
            self._right_scroll.setMaximumWidth(480)
        self._rail_collapse_btn.setText("展开" if c else "收起")

    def _on_open_taxon_edit(self) -> None:
        """Open the 「一次编辑五级」 taxon modal and write the result back."""
        from app.widgets.taxon_edit_dialog import TaxonEditDialog
        dlg = TaxonEditDialog(self._taxon_card.field_values(), parent=self)
        if dlg.exec():
            self._taxon_card.apply_values(dlg.result_values())
            if self._current_uid:
                self._on_save_metadata(self._current_uid)

    def _load_specimen(self, uid: str) -> None:
        """Load grouping + naming + metadata + results for *uid*."""
        self._current_uid = uid
        db = self.ctx.get_db()
        if not db:
            return

        # Load grouping for result display. The editable grouping target follows
        # only the active specimen; inactive sidebar selection must not become
        # the owner of a subsequent "新组".
        grouping = None
        try:
            from app.services.specimen_rename_service import (
                repair_grouping_result_files_for_uid,
            )
            from app.services.grouping_service import ADHOC_GROUPING_UID, load_grouping

            repair_grouping_result_files_for_uid(db, uid)
            grouping = load_grouping(db, uid)
            active_uid = self._get_active_uid()
            if active_uid == uid:
                self._grouping.load_grouping(uid, grouping)
            elif active_uid:
                if getattr(self._grouping, "_uid", None) != active_uid:
                    self._grouping.load_grouping(active_uid, load_grouping(db, active_uid))
            elif getattr(self._grouping, "_uid", None) != ADHOC_GROUPING_UID:
                self._load_temporary_grouping_task("未归属（可后续归入编号）")
        except Exception:
            if self._get_active_uid() == uid:
                self._grouping.clear()

        # Load specimen record for naming + metadata panels
        try:
            row = db.execute(
                "SELECT * FROM specimens WHERE uid = ?", (uid,)
            ).fetchone()
            if row:
                from app.models.specimen import Specimen
                sp = Specimen.from_row(row)
                sp_dict = dict(sp.raw)
                sp_dict.update({
                    "province": sp.province,
                    "site": sp.site,
                    "station": sp.station,
                    "id": sp.id,
                    "storage": sp.storage,
                    "collection_date": sp.collection_date,
                    "collectionDate": sp.collection_date,
                    "photo_date": sp.photo_date,
                    "photoDate": sp.photo_date,
                    "photo_notes": sp.photo_notes,
                    "photoNotes": sp.photo_notes,
                    "collector": sp.collector,
                    "photographer": sp.photographer,
                    "identifier": sp.identifier,
                    "geo_area": sp.geo_area,
                    "scientific_name": sp.scientific_name,
                    "scientificName": sp.scientific_name,
                    "scientific_name_cn": sp.scientific_name_cn,
                    "scientificNameCn": sp.scientific_name_cn,
                    "taxon_group": sp.taxon_group,
                    "taxonGroup": sp.taxon_group,
                    "order_name": sp.order_name,
                    "order": sp.order_name,
                    "family": sp.family,
                    "genus": sp.genus,
                    "notes": sp.notes,
                })
                sp_dict["uid"] = sp.uid
                sp_dict.setdefault("photo_notes", sp.photo_notes)
                self._naming.load_specimen(sp_dict)
                self._metadata.load_specimen(sp)
                self._taxon_card.load_specimen(sp)
                self._sync_uid_display_summary()
                self._collab_card.load_specimen(uid)
            else:
                self._load_naming_from_uid(uid)
        except Exception:
            pass
        self._recognize_first_grouping_tiff(grouping)

        # Populate ② 成果内容 column from grouping data
        self._refresh_results_column(uid, grouping)

        # Backfill empty capture fields from a matching 采集记录. load_specimen sets
        # the four location keys via direct setText (no keys_committed signal), so
        # _apply_collection_autofill would otherwise never run on the load path →
        # 已填的 采集人/拍摄人/坐标 在右栏不显示。非破坏：只填空字段。
        # 容错：采集记录表缺失/查询异常不能拖垮标本加载本身。
        try:
            self._apply_collection_autofill()
        except Exception:
            pass
        # 项目 personnel 默认值回填空字段。_on_project_personnel_changed 只灌新草稿
        # （_current_uid 为 None），已存标本的空 采集人/拍摄人 不会被它覆盖 → 建号时
        # personnel 还没设、之后才设的标本加载后永远空。空不是事实，加载时补默认值。
        # 非破坏：apply_autofill 只填空字段，已存值不动。采集记录优先级更高（已先跑）。
        try:
            self._backfill_personnel_defaults()
        except Exception:
            pass

    def _load_naming_from_uid(self, uid: str) -> bool:
        """Populate the naming card from a UID when no specimen row exists yet."""
        from app.utils.naming import parse_uid

        parsed = parse_uid(uid)
        if not parsed:
            return False
        date_seg = str(parsed.get("dateSegment") or "")
        collection_date = date_seg
        photo_date = date_seg
        if re.fullmatch(r"\d{8}-\d{4}", date_seg):
            collection_date, tail = date_seg.split("-", 1)
            photo_date = collection_date[:4] + tail
        elif re.fullmatch(r"\d{8}-\d{8}", date_seg):
            collection_date, photo_date = date_seg.split("-", 1)
        self._naming.load_specimen({
            "uid": uid,
            "province": parsed.get("province") or "",
            "site": parsed.get("site") or "",
            "station": parsed.get("station") or "",
            "id": parsed.get("speciesId") or "",
            "storage": parsed.get("storage") or "",
            "collectionDate": collection_date,
            "photoDate": photo_date,
            "nextResultSequenceHint": parsed.get("resultSequence") or 1,
        })
        try:
            self._metadata.clear()
            self._taxon_card.clear()
        except Exception:
            pass
        return True

    def _sync_uid_display_summary(self) -> None:
        """Mirror existing metadata into the UID card's non-identity summary."""
        values = {}
        try:
            values.update(self._metadata.current_values())
        except Exception:
            pass
        try:
            values.update(self._taxon_card.field_values())
        except Exception:
            pass
        try:
            values["photo_notes"] = self._naming._photo_notes.toPlainText().strip()
            self._naming.set_display_metadata(values)
        except Exception:
            pass

    # ── Collection-record auto-fill ─────────────────────────────────────────────

    def _apply_collection_autofill(self) -> None:
        """Fill empty capture fields from a matching 采集记录 (野外采集记录簿).

        Triggered when the four location keys (地区/样地/站位/采集日期) are
        finished editing or picked from the record menu. Non-destructive: only
        empty fields are filled (collection_record_service.autofill_values).
        Fields the capture cards lack (生境/潮水/…) stay in the record only,
        unless the project naming rules expose a matching dynamic naming field.
        """
        db = self.ctx.get_db()
        if not db:
            return
        province, site, station, col_date = self._naming.current_keys()
        if not (province and site and station and col_date):
            return
        from app.services import collection_record_service as crs
        rec = crs.lookup_record(db, province, site, station, col_date)
        if not rec:
            return
        dynamic_changed = False
        try:
            dynamic_changed = bool(self._naming.apply_dynamic_autofill(rec))
        except Exception:
            pass
        # 优先级 项目默认 < 站位记录 < 手动/已存：把「自动填」字段当作空看待，让站位
        # 采集记录能覆盖项目默认坐标；受保护字段（用户手填/加载已存的非空值）保留真实
        # 值 → autofill_values 视为已填、不再返回，从而不被覆盖。
        auto = self._metadata.auto_fields()
        current = {
            k: ("" if (k in auto or not v.strip()) else v)
            for k, v in self._metadata.current_values().items()
        }
        current["photo_date"] = self._naming._photo_date.text()
        vals = crs.autofill_values(rec, current)
        if not vals:
            if dynamic_changed and self._current_uid:
                self._schedule_rail_save()
            return
        if "photo_date" in vals and not self._naming._photo_date.text().strip():
            self._naming._photo_date.setText(str(vals["photo_date"]))
        # override_auto=True：覆盖项目默认（自动）坐标，但 apply_autofill 内部仍
        # 跳过手动字段。
        self._metadata.apply_autofill(vals, override_auto=True)
        # Persist for an already-saved specimen; a brand-new draft persists when
        # the user hits 保存 (fields are read straight off the panels then).
        if self._current_uid:
            self._schedule_rail_save()

    def _backfill_personnel_defaults(self) -> None:
        """把项目 personnel 默认值回填到已加载标本的【空】采集人/拍摄人/鉴定人。

        与 _on_project_personnel_changed 互补：后者只灌新草稿（_current_uid 为
        None 时），不碰已存标本。但已存标本的空 personnel 字段（建号时项目默认值
        还没设、之后才设的情况）应能从项目默认值补上。非破坏：apply_autofill 只填
        空字段，已存值（含采集记录已回填的）保留。
        """
        prefill = self._effective_prefill()
        values = {
            k: str(prefill.get(k) or "").strip()
            for k in ("collector", "photographer", "identifier")
        }
        if not any(values.values()):
            return
        before = self._metadata.current_values()
        self._metadata.apply_autofill(values, override_auto=True)
        after = self._metadata.current_values()
        if self._current_uid and before != after:
            self._schedule_rail_save()
        self._sync_uid_display_summary()

    # ── Monitor ───────────────────────────────────────────────────────────────

    def _refresh_monitor(self) -> None:
        """Re-scan the project directory and repopulate the monitor panel."""
        project_dir = self.ctx.current_project_dir
        if not project_dir:
            self._monitor.clear()
            self._last_scan_result = None
            self._refresh_workflow_dashboard()
            return

        # Headless tests use mock/in-memory DBs, so keep their existing
        # synchronous semantics. Real GUI sessions use the worker below.
        if os.environ.get("QT_QPA_PLATFORM") == "offscreen":
            self._refresh_monitor_sync()
            return

        worker = self._monitor_scan_worker
        if worker is not None and worker.isRunning():
            # A refresh requested while a scan is in flight means the in-flight
            # result is older than the current file/DB state (for example after
            # archive completion).  Bump the request id now so that stale result
            # cannot briefly repopulate consumed JPGs before the queued scan runs.
            self._monitor_scan_request_id += 1
            self._monitor_scan_pending = True
            return

        inc, res = self._resolve_capture_subdirs()
        self._monitor_scan_request_id += 1
        request_id = self._monitor_scan_request_id

        from app.workers.monitor_scan_worker import MonitorScanWorker

        worker = MonitorScanWorker(request_id, project_dir, inc, res, parent=self)
        worker.finished_scan.connect(self._on_monitor_scan_finished)
        worker.failed.connect(self._on_monitor_scan_failed)
        worker.finished.connect(worker.deleteLater)
        self._monitor_scan_worker = worker
        worker.start()

    def _refresh_monitor_sync(self) -> None:
        """Synchronous scan path used by tests and explicit headless runs."""
        project_dir = self.ctx.current_project_dir
        if not project_dir:
            self._monitor.clear()
            self._last_scan_result = None
            self._refresh_workflow_dashboard()
            return

        db = self.ctx.get_db()
        if not db:
            self._monitor.clear()
            self._last_scan_result = None
            self._refresh_workflow_dashboard()
            return

        try:
            from app.services.monitor_service import (
                build_attribution_context,
                scan_project,
            )

            attr = build_attribution_context(project_dir, db)
            inc, res = self._resolve_capture_subdirs()
            result = scan_project(
                project_dir, db, attr=attr,
                incoming_subdir=inc, results_subdir=res,
            )
            self._apply_monitor_scan_result(result)
        except FileNotFoundError:
            self._monitor.clear()
            self._last_scan_result = None
            self._refresh_workflow_dashboard()
        except Exception:
            self._monitor.clear()
            self._last_scan_result = None
            self._refresh_workflow_dashboard()

    def _on_monitor_scan_finished(self, request_id: int, result) -> None:
        worker = self._monitor_scan_worker
        if worker is not None and getattr(worker, "request_id", None) == request_id:
            self._monitor_scan_worker = None

        if request_id == self._monitor_scan_request_id:
            project_dir = getattr(result, "project_dir", None)
            current_project = self.ctx.current_project_dir
            try:
                same_project = (
                    bool(project_dir)
                    and bool(current_project)
                    and Path(project_dir).resolve() == Path(current_project).resolve()
                )
            except Exception:
                same_project = project_dir == current_project
            if not project_dir or same_project:
                self._apply_monitor_scan_result(result)

        self._run_pending_monitor_scan()

    def _on_monitor_scan_failed(self, request_id: int, _error) -> None:
        worker = self._monitor_scan_worker
        if worker is not None and getattr(worker, "request_id", None) == request_id:
            self._monitor_scan_worker = None
        if request_id == self._monitor_scan_request_id and self._last_scan_result is None:
            self._monitor.clear()
            self._refresh_workflow_dashboard()
        self._run_pending_monitor_scan()

    def _run_pending_monitor_scan(self) -> None:
        if not self._monitor_scan_pending:
            return
        self._monitor_scan_pending = False
        QTimer.singleShot(0, self._refresh_monitor)

    def _apply_monitor_scan_result(self, result) -> None:
        self._last_scan_result = result
        self._monitor.load_scan(self._monitor_display_scan_result(result))
        self._refresh_workflow_dashboard()
        self._maybe_auto_process_new_tiff(result)

    def _monitor_display_scan_result(self, result):
        """Keep monitor ownership display tied to the current activation state.

        The scan service may recover historical attribution from grouping rows,
        manual assignment events, or old activation windows.  When no specimen is
        active, showing that historical UID on JPG cards looks like the current
        sidebar selection owns those photos and also lets selected JPGs infer a
        compose target from stale state.  Preserve the raw scan for business
        logic, but clear the presentation field for the current monitor view.
        """
        if self._get_active_uid():
            return result
        import copy
        display_result = copy.copy(result)
        display_result.jpg_files = [
            copy.copy(entry)
            for entry in getattr(result, "jpg_files", []) or []
        ]
        for entry in display_result.jpg_files:
            entry.attributed_specimen_id = None
        return display_result

    def _pending_tiff_paths(self, scan_result) -> set[str]:
        return pending_tiff_paths(scan_result)

    def _on_auto_compress_toggled(self, on: bool) -> None:
        try:
            self.ctx.settings.auto_organize_after_compose = bool(on)
        except Exception:
            pass
        if on:
            self._auto_known_tiffs = self._pending_tiff_paths(self._last_scan_result) if self._last_scan_result else set()
            self._status_message("自动归档已开启：可按激活编号自动合成，并处理之后出现的外部 TIF")
        else:
            self._status_message("自动归档已关闭")

    def _on_compose_preview_toggled(self, on: bool) -> None:
        try:
            self.ctx.settings.silent_compose = not bool(on)
            self.ctx.settings.flush_to_disk()
        except Exception:
            pass
        self._status_message("合成预览已开启" if on else "合成预览已关闭：将直接合成")

    def _maybe_auto_process_new_tiff(self, scan_result) -> None:
        candidate = detect_external_tiff_candidate(
            enabled=self._auto_archive_enabled(),
            current_tiff_paths=self._pending_tiff_paths(scan_result),
            known_tiff_paths=self._auto_known_tiffs,
            busy=self._auto_tiff_busy,
        )
        if candidate.should_seed_known_tiffs:
            self._auto_known_tiffs = set(candidate.current_tiff_paths)
            return
        if not candidate.needs_jpg_source:
            return

        uid = self._get_active_uid()
        if uid:
            source = resolve_external_tiff_jpg_source(
                active_uid=uid,
                active_uid_jpg_paths=self._unoccupied_jpg_paths(
                    uid,
                    self._get_attributed_jpg_paths(uid),
                ),
            )
        else:
            try:
                selected_jpg_paths = self._monitor.selected_jpg_paths()
            except Exception:
                selected_jpg_paths = []
            source = resolve_external_tiff_jpg_source(
                active_uid=None,
                selected_jpg_paths=selected_jpg_paths,
            )
        if source.reason == "no-selected-jpgs":
            self._status_message("发现外部 TIF；未激活时请选中对应 JPG 后再自动整理")
            return
        if source.reason == "no-active-jpgs":
            self._status_message("发现外部 TIF；当前激活编号没有可整理 JPG")
            return

        target_tiff = candidate.target_tiff
        if not source.ready or not target_tiff:
            return
        jpg_paths = list(source.jpg_paths)

        def _auto_archive_done(ok: bool) -> None:
            self._auto_tiff_busy = False
            if ok:
                self._auto_known_tiffs.add(target_tiff)
                self._status_message("外部 TIF 自动整理完成")
            else:
                self._status_message("外部 TIF 自动整理失败，已保留待处理")

        self._auto_tiff_busy = True
        try:
            started = self._organise_jpgs_with_tiff(
                jpg_paths,
                target_tiff,
                silent=True,
                on_complete=_auto_archive_done,
            )
        except Exception as exc:
            self._auto_tiff_busy = False
            self._status_message(f"外部 TIF 自动整理失败：{exc}")
            return
        if started:
            self._status_message("发现外部 TIF，正在自动整理")
        else:
            self._auto_tiff_busy = False

    def _missing_meta_fields(self, uid: str) -> list:
        """返回该编号缺失的关键字段标签：保存方式 / 采集日期 / 拍摄日期。

        拍摄当时可能还不知道怎么处理标本（保存方式/日期没填），切到下一个号时用来
        提醒回填，免得遗漏。左侧点该号即可随时编辑补填。
        """
        db = self.ctx.get_db()
        if not db or not uid:
            return []
        try:
            row = db.execute(
                "SELECT storage, collection_date, photo_date FROM specimens WHERE uid = ?",
                (uid,),
            ).fetchone()
        except Exception:
            return []
        if not row:
            return []
        missing = []
        if not (row[0] and str(row[0]).strip()):
            missing.append("保存方式")
        if not (row[1] and str(row[1]).strip()):
            missing.append("采集日期")
        if not (row[2] and str(row[2]).strip()):
            missing.append("拍摄日期")
        return missing

    def _on_sidebar_activate(self, uid: str) -> None:
        """Activate *uid* via activation_service and refresh the sidebar + monitor.

        Oracle: server.js:3844-3888 POST /api/specimen-log/activate.
        """
        project_dir = self.ctx.current_project_dir
        db = self.ctx.get_db()
        if not uid:
            return
        if not project_dir or not db:
            self._status_message("请先打开一个项目工作区。")
            return
        try:
            from app.services.activation_service import activate as svc_activate
            result = svc_activate(project_dir, db, uid)
            prev_uid = result.get("previous_uid") if isinstance(result, dict) else None
            self._sidebar.refresh()
            self._refresh_monitor()
            # Select and load the newly activated specimen
            self._sidebar.select_uid(uid)
            self._load_specimen(uid)
            # 激活即置「拍摄中」：仅当此号尚无阶段（None/空/created）时默认 shooting，
            # 已有更高阶段（已拍完/整理中/完成）保留不动 —— 对齐 oracle
            # activateSpecimen 的 status: existing!=="created" ? existing : "shooting"
            # (app.js:3531-3534) + collabUpdateTaskStatus(uid, status||shooting) (:3556)。
            phase = self._collab_phase_for(uid)
            if phase in (None, "", "created"):
                self._set_phase(uid, "shooting")
            else:
                self._refresh_batch_header()
            self._status_message(f"已激活：{uid}", 4000)
            # 切换激活号提醒：旧号在其激活期间到达的照片仍归旧号，不会改归新号
            # (oracle app.js:3517-3520)。用状态栏提示（非阻塞 toast 等价）。
            if prev_uid and prev_uid != uid:
                segs = prev_uid.split("-")
                short = segs[3] if len(segs) > 3 else prev_uid
                self._status_message(
                    f"已切到新号。提醒：旧号「{short}」此前拍的照片仍归旧号"
                    "（不推荐频繁切换）", 6000,
                )
                # 资料未填完提醒：离开旧号时若它缺 保存方式/采集日期/拍摄日期 → 弹提醒，
                # 免得拍完忘了回填（拍摄当时可能还没决定怎么处理标本）。
                missing = self._missing_meta_fields(prev_uid)
                if missing:
                    QMessageBox.information(
                        self, "上一个编号资料未填完",
                        f"编号「{short}」还缺：{'、'.join(missing)}。\n"
                        "已激活下一个；左侧点该编号可随时回填编辑。",
                    )
        except Exception as exc:
            QMessageBox.warning(self, "激活失败", str(exc))

    def _on_sidebar_deactivate(self, uid: str) -> None:
        """Deactivate *uid* via activation_service and refresh.

        Oracle: server.js:3857-3861 (active=false path).
        """
        project_dir = self.ctx.current_project_dir
        db = self.ctx.get_db()
        if not project_dir or not db or not uid:
            return
        try:
            from app.services.activation_service import deactivate as svc_deactivate
            svc_deactivate(project_dir, db, uid)
            if self._current_uid == uid or self._get_active_uid() is None:
                self._reset_to_unassigned_task()
            self._sidebar.refresh()
            self._refresh_monitor()
            self._refresh_batch_header()
            self._status_message(f"已取消激活：{uid}", 4000)
        except Exception as exc:
            QMessageBox.warning(self, "去激活失败", str(exc))

    def _on_assign_jpg(self, path: str) -> None:
        """Manual attribution: assign *path* to the currently active specimen.

        Writes a manual-assign event so the attribution P2 table picks it up.
        If no specimen is active, show an informational message.

        Oracle: server.js:3891-3913 POST /api/specimen-log/assign.
        """
        project_dir = self.ctx.current_project_dir
        db = self.ctx.get_db()
        if not project_dir or not db or not path:
            return

        try:
            from app.services.capture_workflow_service import assign_jpg_to_active_specimen

            result = assign_jpg_to_active_specimen(project_dir, db, path)
        except Exception as exc:
            QMessageBox.warning(self, "手动归属失败", str(exc))
            return

        if not result.active_uid:
            QMessageBox.information(
                self,
                "手动归属",
                "请先激活一个标本，再手动归属 JPG。",
            )
            return

        self._refresh_monitor()

    def _on_unassign_jpg(self, path: str) -> None:
        """Explicit unassign: adds path to the P0 blacklist."""
        db = self.ctx.get_db()
        if not db or not path:
            return
        try:
            from app.services.capture_workflow_service import unassign_jpg
            unassign_jpg(db, path)
            self._refresh_monitor()
        except Exception:
            pass

    def _on_add_selection_to_group(self, group_index: int) -> None:
        """Resolve monitor selection → add JPGs (+ optional TIFF) to a group."""
        jpg_paths = self._monitor.selected_jpg_paths()
        tiff_paths = self._monitor.selected_tiff_paths()
        if not jpg_paths and not tiff_paths:
            self._status_message("请先在上方监控区选中 JPG 或 TIFF。")
            return
        if len(tiff_paths) > 1:
            self._status_message("一次只能关联 1 个 TIFF。")
            return
        tiff_path = tiff_paths[0] if tiff_paths else None
        self._grouping.drop_external_files(group_index, jpg_paths, tiff_path)
        from app.services.grouping_service import ADHOC_GROUPING_UID
        uid = getattr(self._grouping, "_uid", None)
        project_dir = self.ctx.current_project_dir
        if uid and uid != ADHOC_GROUPING_UID and project_dir and jpg_paths:
            try:
                from app.services.activation_service import manual_assign
                manual_assign(project_dir, uid, jpg_paths)
            except Exception:
                pass
        self._monitor._on_select_none()

    def _on_add_to_group(self, group_index: int, jpg_paths: list[str]) -> None:
        """Add selected monitor JPGs to the specified grouping group."""
        self._grouping.add_jpgs_to_group(group_index, jpg_paths)
        # Also mark those paths as manually assigned to the current uid
        uid = self._current_uid
        project_dir = self.ctx.current_project_dir
        if uid and project_dir and jpg_paths:
            try:
                from app.services.activation_service import manual_assign
                manual_assign(project_dir, uid, jpg_paths)
            except Exception:
                pass

    def _on_add_jpg_files(self) -> None:
        """Open file picker for JPG/TIFF → import into capture workspace.

        Oracle: app.js importJpgFiles() app.js:7944–7975.
        """
        project_dir = self.ctx.current_project_dir
        if not project_dir:
            self._status_message("请先打开一个项目。")
            return

        from PyQt6.QtWidgets import QFileDialog
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            "选择 JPG / TIFF 照片",
            filter=(
                "JPG 与 TIFF (*.jpg *.jpeg *.JPG *.JPEG *.tif *.tiff *.TIF *.TIFF);;"
                "JPG 照片 (*.jpg *.jpeg *.JPG *.JPEG);;"
                "TIFF 成片 (*.tif *.tiff *.TIF *.TIFF)"
            ),
        )
        if not paths:
            return

        self._start_import_media_paths(paths, source="添加照片")

    def _on_external_jpgs_dropped(self, paths: list[str]) -> None:
        """Import JPG/TIFF dropped from Explorer/Finder/file managers."""
        self._start_import_media_paths(paths, source="拖入照片")

    def _import_jpg_paths(self, paths: list[str], *, source: str) -> list[str]:
        """Compatibility wrapper for older JPG-only callers."""
        return self._import_media_paths(paths, source=source)

    def _import_media_paths(self, paths: list[str], *, source: str) -> list[str]:
        project_dir = self.ctx.current_project_dir
        if not project_dir:
            self._status_message("请先打开一个项目。")
            return []

        inc, _res = self._resolve_capture_subdirs()
        incoming_dir = os.path.join(project_dir, inc)
        from app.services.photo_import_service import import_media_to_project
        result = import_media_to_project(list(paths), incoming_dir)
        imported = self._handle_media_import_result(
            result,
            source=source,
            incoming_label=inc,
            project_dir=project_dir,
        )
        self._refresh_monitor()
        return imported

    def _start_import_media_paths(self, paths: list[str], *, source: str) -> None:
        project_dir = self.ctx.current_project_dir
        if not project_dir:
            self._status_message("请先打开一个项目。")
            return
        if not paths:
            return
        worker = getattr(self, "_photo_import_worker", None)
        if worker is not None and worker.isRunning():
            self._status_message("照片仍在导入，请稍后再添加。")
            return

        inc, _res = self._resolve_capture_subdirs()
        incoming_dir = os.path.join(project_dir, inc)
        from app.workers.photo_import_worker import PhotoImportWorker

        worker = PhotoImportWorker(list(paths), incoming_dir, parent=self)
        self._photo_import_worker = worker
        task_key = f"photo-import:{id(worker)}"

        def set_photo_import_busy_state(on: bool) -> None:
            try:
                self._monitor.set_import_busy(on)
            except Exception:
                pass

        def _cleanup() -> None:
            set_photo_import_busy_state(False)
            if self._photo_import_worker is worker:
                self._photo_import_worker = None
            worker.deleteLater()

        def _import_started(count: int) -> None:
            self._workflow_notice(
                f"{source}：正在导入",
                f"正在复制 {count} 个文件到 {inc}；导入在后台运行，可继续拍摄或整理。",
                state="busy",
                force_show=True,
                task_key=task_key,
            )
            self._status_message(f"正在导入 {count} 个文件到 {inc}，可继续操作其他区域。")

        worker.started_import.connect(_import_started)
        worker.completed.connect(
            lambda result: self._on_photo_import_finished(
                result,
                source=source,
                incoming_label=inc,
                project_dir=project_dir,
                task_key=task_key,
            )
        )
        worker.failed.connect(
            lambda message: self._on_photo_import_failed(
                source, message, task_key=task_key
            )
        )
        worker.finished.connect(_cleanup)
        set_photo_import_busy_state(True)
        worker.start()

    def _on_photo_import_finished(
        self,
        result,
        *,
        source: str,
        incoming_label: str,
        project_dir: str,
        task_key: str = "",
    ) -> None:
        imported = self._handle_media_import_result(
            result,
            source=source,
            incoming_label=incoming_label,
            project_dir=project_dir,
        )
        duplicate_count = len(getattr(result, "skipped_duplicate_paths", []) or [])
        error_count = len(getattr(result, "errors", []) or [])
        jpg_count = len(getattr(result, "imported_jpg_paths", []) or [])
        tiff_count = len(getattr(result, "imported_tiff_paths", []) or [])
        if imported:
            parts = []
            if jpg_count:
                parts.append(f"{jpg_count} 张 JPG")
            if tiff_count:
                parts.append(f"{tiff_count} 个 TIFF")
            detail = f"已导入 {'，'.join(parts)} 到 {incoming_label}。"
            if duplicate_count:
                detail += f" 已跳过 {duplicate_count} 个重复文件。"
            if error_count:
                detail += f" 有 {error_count} 个文件失败，请查看弹窗。"
            self._workflow_notice(
                f"{source}{'部分完成' if error_count else '完成'}",
                detail,
                state="error" if error_count else "success",
                task_key=task_key,
            )
        elif duplicate_count:
            self._workflow_notice(
                f"{source}完成",
                f"未新增文件；已跳过 {duplicate_count} 个重复文件。",
                state="info",
                task_key=task_key,
            )
        else:
            self._workflow_notice(
                f"{source}未导入",
                "未识别到 JPG/JPEG 或 TIFF 文件。",
                state="info",
                task_key=task_key,
            )
        self._refresh_monitor()

    def _on_photo_import_failed(
        self,
        source: str,
        message: str,
        *,
        task_key: str = "",
    ) -> None:
        self._workflow_notice(
            f"{source}失败",
            message or "导入过程出现错误。",
            state="error",
            task_key=task_key,
        )
        QMessageBox.warning(self, f"{source}失败", message or "导入过程出现错误。")
        self._status_message(f"{source}失败。")

    def _handle_media_import_result(
        self,
        result,
        *,
        source: str,
        incoming_label: str,
        project_dir: str,
    ) -> list[str]:
        try:
            from app.services.photo_import_service import record_imported_media
            record_imported_media(
                project_dir,
                list(getattr(result, "imported_records", []) or []),
            )
        except Exception:
            pass

        if result.errors:
            QMessageBox.warning(self, f"{source}部分失败", "\n".join(result.errors[:5]))
        duplicate_count = len(getattr(result, "skipped_duplicate_paths", []) or [])
        if result.imported_paths:
            parts = []
            if result.imported_jpg_paths:
                parts.append(f"{len(result.imported_jpg_paths)} 张 JPG 到 {incoming_label}")
            if result.imported_tiff_paths:
                parts.append(f"{len(result.imported_tiff_paths)} 个 TIFF 到 {incoming_label}")
            msg = "已导入 " + "，".join(parts)
            if duplicate_count:
                msg += f"；已跳过 {duplicate_count} 个重复文件"
            self._status_message(msg + "。")
        elif duplicate_count:
            self._status_message(f"已跳过 {duplicate_count} 个重复文件，队列未新增。")
        elif result.skipped_paths:
            self._status_message("未识别到 JPG/JPEG 或 TIFF 文件。")
        return result.imported_paths

    def _on_clear_pending_queue(self, paths: list[str]) -> None:
        project_dir = self.ctx.current_project_dir
        if not project_dir:
            self._status_message("请先打开一个项目。")
            return
        try:
            from app.services.photo_import_service import clear_pending_imports
            result = clear_pending_imports(project_dir, list(paths))
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "清空队列失败", str(exc))
            return

        try:
            self._monitor._on_select_none()
        except Exception:
            pass
        self._refresh_monitor()

        if result.errors:
            QMessageBox.warning(self, "清空队列部分失败", "\n".join(result.errors[:5]))

        if result.changed_count:
            parts = []
            if result.returned_paths:
                parts.append(f"退回 {len(result.returned_paths)} 个")
            if result.stashed_paths:
                parts.append(f"安全移出 {len(result.stashed_paths)} 个")
            self._status_message("队列已清空：" + "，".join(parts) + "。")
        elif result.skipped_paths:
            self._status_message("队列未变化：没有可清空的文件。")

    def _on_free_compose(self) -> None:
        """Free compose: selected monitor JPGs → Helicon → incoming-jpg/.

        Stub — full implementation in Task 7.
        Oracle: app.js freeComposeSelected() app.js:7982–8010.
        """
        project_dir = self.ctx.current_project_dir
        if not project_dir:
            self._status_message("请先打开一个项目。")
            return

        jpg_paths = self._monitor.selected_jpg_paths()
        if not jpg_paths:
            self._status_message("请先在监控区选中要合成的 JPG。")
            return

        from app.services.helicon_service import detect_helicon
        exe = detect_helicon()
        if not exe:
            QMessageBox.warning(self, "未检测到 Helicon Focus",
                                "请确认 Helicon Focus 已安装并配置路径。")
            return

        from PyQt6.QtWidgets import QInputDialog
        user_name, ok = QInputDialog.getText(
            self, "无号合成", "输出文件名（留空自动命名）：", text=""
        )
        if not ok:
            return

        inc, _res = self._resolve_capture_subdirs()
        incoming_dir = os.path.join(project_dir, inc)
        os.makedirs(incoming_dir, exist_ok=True)
        output_name = _free_compose_output_name(incoming_dir, user_name.strip() or None)
        output_path = os.path.join(incoming_dir, output_name)
        # Honor 输出格式 (tif/jpg) — swap extension so -save: matches the encoder.
        output_path = self._with_output_ext(output_path, self._helicon_output_opts()["format"])
        output_name = os.path.basename(output_path)

        params = self._helicon_params.get_params()

        def _handle_free_compose_finished(tiff_path):
            if os.path.isfile(output_path):
                QMessageBox.information(self, "无号合成完成",
                                        f"TIFF 已保存到 {inc}/：\n{output_name}")
                self._refresh_monitor()
            else:
                QMessageBox.warning(self, "无号合成失败", "Helicon 执行后未生成输出文件。")

        def _handle_free_compose_failed(msg: str):
            if msg != "用户取消":
                QMessageBox.warning(self, "无号合成失败", msg)

        self._run_helicon_stack(
            jpg_paths,
            output_path,
            params,
            _handle_free_compose_finished,
            _handle_free_compose_failed,
        )

    def _on_retroactive_scan(self) -> None:
        """Launch retroactive organize modal.

        Oracle: app.js retroactiveScan() + renderRetroactiveModal().
        """
        project_dir = self.ctx.current_project_dir
        db = self.ctx.get_db()
        if not project_dir or not db:
            self._status_message("请先打开一个项目。")
            return

        _inc0, _res0 = self._resolve_capture_subdirs()
        pre = _RetroactiveScanDialog(project_dir, parent=self, results_subdir=_res0)
        if pre.exec() != QDialog.DialogCode.Accepted:
            return
        selected_subdir = pre.selected_subdir()

        try:
            from app.services.retroactive_service import scan_project_retroactive
            # 存量整理也用项目配置的 incoming/results 子目录（与监控/合成一致）。
            inc, res = self._resolve_capture_subdirs()
            result = scan_project_retroactive(
                project_dir, db, subdir=selected_subdir,
                incoming_subdir=inc, results_subdir=res,
            )
        except Exception as exc:
            QMessageBox.warning(self, "扫描失败", str(exc))
            return

        total_groups = sum(len(sp["groups"]) for sp in result.get("specimens", []))
        if not total_groups and not result.get("unnamedTiffs"):
            QMessageBox.information(
                self, "存量整理",
                "没找到可整理的 TIF 成片（需 results/ 里有按编号命名的 TIF）。"
            )
            return

        from app.widgets.retroactive_modal import RetroactiveModal
        dlg = RetroactiveModal(self.ctx, result, parent=self)
        if dlg.exec() == RetroactiveModal.DialogCode.Accepted:
            panel_uid = getattr(self._grouping, "_uid", None)
            if panel_uid:
                try:
                    from app.services.grouping_service import load_grouping
                    db = self.ctx.get_db()
                    if db is not None:
                        self._grouping.load_grouping(
                            panel_uid, load_grouping(db, panel_uid)
                        )
                except Exception:
                    pass
            self._refresh_monitor()

    def _open_project_via_dialog(self, parent: QWidget) -> Optional[str]:
        """Open an existing workspace folder and activate it in the workbench."""
        from app.views.project_dialog import ProjectDialog
        from app.views.overview_view import _load_projects
        from app.services.project_service import (
            default_user_projects_json_path,
            save_project_descriptor,
        )

        dlg = ProjectDialog(
            mode="open",
            existing_projects=_load_projects(),
            parent=parent,
        )
        if dlg.exec() != ProjectDialog.DialogCode.Accepted:
            return None
        proj = dlg.result_project()
        directory = str(proj.get("directory", "") or "").strip() if proj else ""
        if not directory:
            return None
        try:
            save_project_descriptor(
                default_user_projects_json_path(),
                proj,
                existing_projects=_load_projects(),
            )
            from app.services.project_service import enter_workspace
            enter_workspace(self.ctx, directory)
            self.on_activate()
        except Exception as exc:
            ui.warn(parent, "打开项目失败", str(exc))
            return None
        return directory

    def _on_dashboard_open_project(self) -> None:
        self._open_project_via_dialog(self)

    def _pick_auto_group_source_folder(self, parent: QWidget) -> str:
        """Prompt folder vs project, then return the directory to scan."""
        chooser = _AutoGroupSourceDialog(parent)
        if chooser.exec() != QDialog.DialogCode.Accepted:
            return ""

        start_dir = self.ctx.current_project_dir or str(Path.home())
        if chooser.selected_source_mode() == _AutoGroupSourceDialog.MODE_FOLDER:
            folder = ui.get_existing_directory(
                parent,
                "选择需要自动分组整理的 JPG / TIF 目录",
                start=start_dir,
            )
            if not folder:
                return ""
            try:
                from app.services.project_service import enter_photo_folder
                enter_photo_folder(self.ctx, folder)
            except Exception as exc:
                ui.warn(
                    parent,
                    "打开照片文件夹失败",
                    f"无法在照片目录中保存管理数据：{exc}",
                )
                return ""
            return folder

        project_dir = self._open_project_via_dialog(parent)
        if not project_dir:
            return ""
        inc, _ = self._resolve_capture_subdirs()
        start = Path(project_dir) / inc
        if not start.is_dir():
            start = Path(project_dir)
        return ui.get_existing_directory(
            parent,
            "选择项目内要自动分组整理的目录",
            start=str(start),
        )

    def _on_auto_group_organize(self) -> None:
        """Scan → inline preview; second click opens archive dialog."""
        parent = self._grouping_ui_parent()

        if self._grouping.has_auto_group_preview():
            from app.widgets.retroactive_modal import RetroactiveModal
            result = self._grouping.auto_group_preview_result()
            if not result:
                return
            dlg = RetroactiveModal(self.ctx, result, parent=parent)
            if dlg.exec() == RetroactiveModal.DialogCode.Accepted:
                self._grouping.clear_auto_group_staging()
                panel_uid = getattr(self._grouping, "_uid", None)
                if panel_uid:
                    try:
                        from app.services.grouping_service import load_grouping
                        db = self.ctx.get_db()
                        if db is not None:
                            self._grouping.load_grouping(
                                panel_uid, load_grouping(db, panel_uid)
                            )
                    except Exception:
                        pass
                self._refresh_monitor()
            return

        fallback_uid = getattr(self._grouping, "_uid", None)
        from app.services.grouping_service import ADHOC_GROUPING_UID
        if fallback_uid == ADHOC_GROUPING_UID:
            fallback_uid = self._current_uid or self._get_active_uid()

        staged = self._grouping.staged_auto_group_paths()
        try:
            if staged:
                from app.services.retroactive_service import scan_paths_auto_groups
                result = scan_paths_auto_groups(staged, fallback_uid=fallback_uid)
            else:
                folder = self._pick_auto_group_source_folder(parent)
                if not folder:
                    return
                from app.services.retroactive_service import scan_folder_auto_groups
                result = scan_folder_auto_groups(
                    folder,
                    fallback_uid=fallback_uid,
                )
        except Exception as exc:
            ui.warn(parent, "自动分组扫描失败", str(exc))
            return

        total_groups = sum(
            len(sp.get("groups", [])) for sp in result.get("specimens", [])
        )
        if not total_groups and not result.get("unnamedTiffs"):
            ui.info(
                parent,
                "自动分组整理",
                "没有可配对的 JPG…TIF。\n\n"
                "请拖入原片+成片，或选择直接包含这些文件的文件夹"
                "（按修改时间排序，每个 TIF 前一批 JPG 为一组）。",
            )
            return

        # Drag-dropped legacy photos have no folder picker.  When they all come
        # from one directory, adopt that directory as a standalone photo folder
        # so confirmed grouping is persisted beside the photos as well.
        if not self.ctx.current_project_dir and result.get("scanFolder"):
            try:
                from app.services.project_service import enter_photo_folder
                enter_photo_folder(self.ctx, result["scanFolder"])
            except Exception as exc:
                ui.warn(
                    parent,
                    "保存分组数据失败",
                    f"无法在照片目录中创建 _data/project.db：{exc}",
                )
                return

        self._grouping.show_auto_group_preview(result)
        self._status_message(
            f"已识别 {total_groups} 组，请在下方核对预览。"
            "确认无误后再点「执行整理归档」。"
        )

    def _offer_tiff_naming_check(self, folder: str | None) -> None:
        """Ask user to run the read-only TIF naming audit on *folder*."""
        if not folder or not os.path.isdir(folder):
            return
        if ui.question(
            self,
            "检查 TIF 命名",
            "整理已完成。是否立即检查 TIF 命名并提取标本编号？",
        ):
            self._run_tiff_naming_check(folder)

    def _current_grouping_tiff_paths_for_check(self) -> tuple[list[str], bool]:
        """Return visible grouping TIFFs for the naming audit.

        Checked groups narrow the audit. With no checks, all visible linked
        TIFFs are audited; if there are none, the caller falls back to a folder
        picker for legacy bulk checks.
        """
        grouping = getattr(self._grouping, "_grouping", None)
        if grouping is None:
            return [], False
        try:
            selected = set(self._grouping.selected_group_indexes())
        except Exception:
            selected = set()
        paths: list[str] = []
        for group in list(getattr(grouping, "groups", []) or []):
            if selected and group.group_index not in selected:
                continue
            tiff_path = str(getattr(group, "composed_tiff_path", "") or "").strip()
            if tiff_path:
                paths.append(tiff_path)
        return paths, bool(selected)

    def _run_tiff_naming_check(
        self,
        folder: str | None = None,
        *,
        paths: list[str] | None = None,
    ) -> None:
        """Scan a folder or explicit TIF paths and show the naming audit."""
        project_dir = self.ctx.current_project_dir
        if not project_dir and not paths:
            self._status_message("请先打开一个项目。")
            return

        explicit_paths: list[str] = [
            str(p).strip() for p in (paths or []) if str(p).strip()
        ]
        selected_only = False
        if not folder and not explicit_paths:
            explicit_paths, selected_only = self._current_grouping_tiff_paths_for_check()
            if selected_only and not explicit_paths:
                ui.warn(self, "TIF 命名检查", "勾选的分组没有已关联 TIF。")
                return

        if not folder and not explicit_paths:
            start_dir = project_dir or str(Path.home())
            folder = ui.get_existing_directory(
                self,
                "选择需要检查 TIF 命名的目录",
                start=start_dir,
            )
        if not folder and not explicit_paths:
            return

        current_uid = getattr(self._grouping, "_uid", None)
        try:
            from app.services.grouping_service import ADHOC_GROUPING_UID
            if current_uid == ADHOC_GROUPING_UID:
                current_uid = None
            from app.services.project_settings_service import (
                DEFAULT_NAMING_RULES,
                load_setting,
            )
            from app.services.tiff_naming_service import (
                inspect_tiff_names,
                inspect_tiff_paths,
            )
            from app.utils.naming import component_values_from_specimen

            db = self.ctx.get_db()
            rules = (
                load_setting(db, "naming_rules", DEFAULT_NAMING_RULES)
                if db
                else DEFAULT_NAMING_RULES
            )
            components = rules.get("components", DEFAULT_NAMING_RULES["components"])

            specimen_values = None
            if current_uid and db:
                row = db.execute(
                    "SELECT * FROM specimens WHERE uid = ?", (current_uid,)
                ).fetchone()
                if row:
                    from app.models.specimen import Specimen

                    sp = Specimen.from_row(row)
                    sp_dict = dict(sp.raw)
                    sp_dict.update({
                        "province": sp.province,
                        "site": sp.site,
                        "station": sp.station,
                        "id": sp.id,
                        "storage": sp.storage,
                        "collection_date": sp.collection_date,
                        "collectionDate": sp.collection_date,
                        "photo_date": sp.photo_date,
                        "photoDate": sp.photo_date,
                        "collector": sp.collector,
                        "photographer": sp.photographer,
                        "identifier": sp.identifier,
                        "geo_area": sp.geo_area,
                        "taxon_group": sp.taxon_group,
                        "scientific_name": sp.scientific_name,
                        "scientific_name_cn": sp.scientific_name_cn,
                        "notes": sp.notes,
                        "photo_notes": sp.photo_notes,
                    })
                    specimen_values = component_values_from_specimen(sp_dict)

            if explicit_paths:
                audit = inspect_tiff_paths(
                    explicit_paths,
                    current_uid=current_uid,
                    naming_components=components,
                    specimen_values=specimen_values,
                )
            else:
                audit = inspect_tiff_names(
                    folder,
                    current_uid=current_uid,
                    naming_components=components,
                    specimen_values=specimen_values,
                )
        except Exception as exc:
            ui.warn(self, "TIF 命名检查失败", str(exc))
            return

        if audit.total == 0:
            message = (
                "所选文件不是 TIF/TIFF，或文件不存在。"
                if explicit_paths
                else "所选目录中没有 TIF/TIFF 文件。"
            )
            ui.info(self, "TIF 命名检查", message)
            return

        from app.widgets.tiff_naming_audit_dialog import TiffNamingAuditDialog
        TiffNamingAuditDialog(audit, parent=self).exec()

    def _on_tiff_naming_check(self) -> None:
        """Run the independent, read-only TIFF filename audit."""
        self._run_tiff_naming_check()

    def _on_tiff_naming_check_path(self, path: str) -> None:
        """Run the read-only naming audit for one TIFF file."""
        self._run_tiff_naming_check(paths=[path])

    def _group_for_tiff_path(self, path: str) -> tuple[str, int] | None:
        """Return ``(uid, group_index)`` for a registered TIFF path."""
        if not path:
            return None
        try:
            from app.services.grouping_service import result_path_key as _path_key
        except Exception:
            def _path_key(value):
                return str(value or "").casefold()

        target_key = _path_key(path)
        if not target_key:
            return None

        grouping = getattr(self._grouping, "_grouping", None)
        uid = getattr(self._grouping, "_uid", None)
        for group in list(getattr(grouping, "groups", []) or []):
            try:
                if _path_key(getattr(group, "composed_tiff_path", None)) == target_key:
                    return str(uid or ""), int(group.group_index)
            except Exception:
                continue

        db = self.ctx.get_db()
        if db is None:
            return None
        try:
            rows = db.execute(
                """
                SELECT uid, group_index, composed_tiff_path
                FROM grouping
                WHERE composed_tiff_path IS NOT NULL
                  AND composed_tiff_path != ''
                """
            ).fetchall()
        except Exception:
            return None
        for row in rows:
            row_uid = row["uid"] if hasattr(row, "keys") else row[0]
            group_index = row["group_index"] if hasattr(row, "keys") else row[1]
            tiff_path = row["composed_tiff_path"] if hasattr(row, "keys") else row[2]
            try:
                if _path_key(tiff_path) == target_key:
                    return str(row_uid or ""), int(group_index)
            except Exception:
                continue
        return None

    def _on_delete_result_tiff_path(self, path: str) -> None:
        """Delete a TIFF from the results context menu after confirmation."""
        path = str(path or "").strip()
        if not path:
            return
        registered = self._group_for_tiff_path(path)
        if registered is not None:
            uid, group_index = registered
            self._on_undo_compose(uid, group_index)
            return

        if not os.path.isfile(path):
            ui.warn(self, "删除 TIF", f"文件不存在：\n{path}")
            return
        reply = QMessageBox.question(
            self,
            "删除 TIF",
            "这张 TIF 尚未关联到任何分组。\n\n"
            "确认永久删除这个文件？\n"
            f"{path}",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        try:
            os.unlink(path)
        except OSError as exc:
            ui.warn(self, "删除 TIF", f"删除失败：{exc}")
            return
        self._refresh_monitor()
        if getattr(self._results, "_display_mode", "single") == "many":
            self._on_show_all_results()
        else:
            self._on_show_current_results()
        self._status_message("TIF 已删除。", 4000)

    # ── Grouping ──────────────────────────────────────────────────────────────

    def _get_grouping_for_uid(self, uid: str):
        """Load grouping from DB when a project is open, else from panel memory."""
        from app.services.capture_workflow_service import grouping_for_clean_uid

        panel = self._grouping
        if getattr(panel, "_uid", None) == uid and panel._grouping is not None:
            return panel._grouping
        return grouping_for_clean_uid(self.ctx.get_db(), uid)

    def _save_grouping_for_uid(self, uid: str, groups: list) -> None:
        """Persist grouping to DB when available; always refresh the open panel."""
        from app.services.capture_workflow_service import persist_grouping
        from app.services.grouping_service import SpecimenGrouping

        db = self.ctx.get_db()
        persist_grouping(db, uid, groups, clean_phantoms=False)
        panel = self._grouping
        if getattr(panel, "_uid", None) == uid:
            panel.load_grouping(uid, SpecimenGrouping(uid=uid, groups=groups))

    def _ensure_compose_incoming_dir(self, group) -> tuple[str, str]:
        """Return (incoming_dir, results_dir) for Helicon output.

        With a project: use configured incoming/results subdirs.
        Without: write beside the group's JPGs (or a temp fallback).
        """
        import tempfile

        project_dir = self.ctx.current_project_dir
        if project_dir:
            inc, res = self._resolve_capture_subdirs()
            incoming = os.path.join(project_dir, inc)
            results = os.path.join(project_dir, res)
            os.makedirs(incoming, exist_ok=True)
            return incoming, results
        if group and group.jpg_paths:
            incoming = os.path.dirname(os.path.abspath(group.jpg_paths[0]))
            os.makedirs(incoming, exist_ok=True)
            return incoming, incoming
        incoming = os.path.join(tempfile.gettempdir(), "specimen-photo-workbench")
        os.makedirs(incoming, exist_ok=True)
        return incoming, incoming

    def _on_compose_requested(
        self,
        uid: str,
        group_index: int,
        *,
        on_composed=None,
    ) -> None:
        """Compose the JPGs in the specified group via Helicon Focus CLI.

        Steps:
          1. Detect Helicon .exe (graceful failure if not found).
          2. Build output TIFF path using organize_service sequence.
          3. Call helicon_service.stack_single_subprocess with progress dialog.
          4. Update grouping DB with composedTiffPath + status="composed".

        Oracle: workbench.md, helicon.md; helicon_service.stack_single_subprocess.

        NOTE: QProcess real invocation requires a true machine with Helicon.
        """
        db = self.ctx.get_db()
        if not uid:
            return

        def _notify_composed(success: bool) -> None:
            if callable(on_composed):
                on_composed(success)

        try:
            from app.services.helicon_service import detect_helicon

            grouping = self._get_grouping_for_uid(uid)
            group = next(
                (g for g in grouping.groups if g.group_index == group_index), None
            )
            if group is None:
                QMessageBox.warning(self, "合成", f"找不到组{group_index + 1}")
                _notify_composed(False)
                return

            from app.services.helicon_service import resolve_existing_image_path
            resolved_jpgs: list[str] = []
            missing_jpgs: list[str] = []
            for p in group.jpg_paths:
                r = resolve_existing_image_path(p)
                if r:
                    resolved_jpgs.append(r)
                else:
                    missing_jpgs.append(p)
            if missing_jpgs:
                sample = "\n".join(missing_jpgs[:5])
                extra = f"\n…等 {len(missing_jpgs)} 个" if len(missing_jpgs) > 5 else ""
                QMessageBox.warning(
                    self,
                    "合成",
                    f"以下 JPG 在磁盘上找不到：\n{sample}{extra}\n\n"
                    "请确认照片仍在 incoming-jpg 目录；WSL 下请用 /mnt/n/... 打开项目。",
                )
                _notify_composed(False)
                return
            group.jpg_paths = resolved_jpgs

            if len(group.jpg_paths) < 2:
                # ── Implicit-batch fallback  #cursor ─────────────────────────
                # Mirrors web composeImplicitActiveBatch() app.js:5660–5706.
                # If group is empty/insufficient, offer to use all JPGs
                # currently attributed to this specimen in the monitor scan.
                attributed_paths = self._get_attributed_jpg_paths(uid)
                if len(attributed_paths) >= 2:
                    reply = QMessageBox.question(
                        self,
                        "该组 JPG 不足",
                        f"该分组 JPG 不足 2 张，但检测到 {len(attributed_paths)} 张"
                        f" 已归属到此标本的 JPG。\n\n"
                        "是否用这些照片作为隐式批次执行合成？",
                        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                        QMessageBox.StandardButton.No,
                    )
                    if reply != QMessageBox.StandardButton.Yes:
                        _notify_composed(False)
                        return
                    group.jpg_paths = attributed_paths
                else:
                    QMessageBox.warning(
                        self, "合成", "该组 JPG 不足 2 张，无法合成。"
                    )
                    _notify_composed(False)
                    return

            # ── Pre-compose preview dialog  #cursor renderComposePreviewModal ─
            # Mirrors web renderComposePreviewModal() app.js:6597.
            # Shows JPG list so user can confirm / deselect before Helicon runs.
            selected_jpgs = self._show_compose_preview(group.jpg_paths)
            if selected_jpgs is None:
                return  # User cancelled
            if len(selected_jpgs) < 2:
                QMessageBox.warning(self, "合成", "选中的 JPG 不足 2 张，无法合成。")
                _notify_composed(False)
                return
            group.jpg_paths = selected_jpgs

            # Check Helicon availability first
            exe = detect_helicon()
            if not exe:
                QMessageBox.warning(
                    self,
                    "未检测到 Helicon Focus",
                    "未在常见安装目录找到 Helicon Focus，请确认已安装并设置 "
                    "HELICON_FOCUS_PATH 环境变量指向可执行文件。",
                )
                _notify_composed(False)
                return

            # Determine output path — TIFF lands in incoming first;
            # organize step moves it to results/ (oracle app.js:4336,8867).
            incoming_dir, results_dir = self._ensure_compose_incoming_dir(group)

            # 输出名统一走 _resolve_compose_output_name:覆盖值 > 编号-序号 > 组序.tif。
            # _seq:真编号=preview.next_seq;临时分组(ad-hoc)=组序。
            output_name, _seq = self._resolve_compose_output_name(
                db, uid, group, results_dir, incoming_dir)
            output_path = os.path.join(incoming_dir, output_name)  # incoming, not results
            # Honor 输出格式 (tif/jpg); default tif keeps the lossless archival master.
            output_path = self._with_output_ext(output_path, self._helicon_output_opts()["format"])
            output_name = os.path.basename(output_path)

            params = self._helicon_params.get_params()
            task_key = f"compose:{uid}:{group.group_index}:{output_name}"
            self._workflow_notice(
                "合成：正在生成 TIFF",
                f"正在合成 {len(group.jpg_paths)} 张 JPG → {output_name}。完成后会进入预览确认。",
                state="busy",
                force_show=True,
                task_key=task_key,
            )

            def _run_interactive_compose_with_preview(jpg_paths, out_path, cur_params):
                def _open_compose_review_after_helicon_success(tiff_path):
                    if not os.path.isfile(out_path):
                        self._workflow_notice(
                            "合成失败",
                            "Helicon 执行后未生成输出文件。",
                            state="error",
                            task_key=task_key,
                        )
                        QMessageBox.warning(self, "合成失败", "Helicon 执行后未生成输出文件。")
                        _notify_composed(False)
                        return
                    dlg = _ComposeWorkbenchDialog(
                        jpg_paths,
                        out_path,
                        cur_params,
                        angle_label=group.angle_label,
                        parent=self,
                    )
                    dlg.exec()
                    action = dlg.action()
                    new_params = dlg.params()
                    self._helicon_params.set_params(new_params)

                    if action == _ComposeWorkbenchDialog.ACTION_RECOMPOSE:
                        selected = dlg.selected_jpgs()
                        if len(selected) < 2:
                            QMessageBox.warning(self, "合成", "选中的 JPG 不足 2 张，无法重合成。")
                            return
                        self._retire_tiff(out_path)
                        group.jpg_paths = selected
                        _run_interactive_compose_with_preview(selected, out_path, new_params)
                        return

                    if action == _ComposeWorkbenchDialog.ACTION_CANCEL:
                        self._retire_tiff(out_path)
                        return

                    # Save to result: persist grouping only after preview approval.
                    persisted = persist_composed_group(
                        db,
                        uid,
                        grouping,
                        group.group_index,
                        tiff_path=out_path,
                        result_sequence=_seq,
                    )
                    self._grouping.load_grouping(uid, persisted.grouping)
                    self._refresh_results_column(uid, persisted.grouping)
                    self._on_helicon_finished(uid)
                    self._workflow_notice(
                        "合成完成",
                        f"TIFF 已生成：{output_name}。JPG 尚未整理；需要归档时请继续点击整理。",
                        state="success",
                        task_key=task_key,
                    )
                    if callable(on_composed):
                        on_composed(True)
                        return
                    QMessageBox.information(self, "合成完成", f"TIFF 已生成：{output_name}")
                    # 自动归档打开时，合成完成后自动把源 JPG 打包 ZIP+命名+移
                    # results（省掉手动点[整理]）。开关默认关。
                    self._maybe_auto_organize(uid, group.group_index)

                def _report_interactive_compose_failure(msg: str):
                    if msg != "用户取消":
                        self._workflow_notice(
                            "合成失败",
                            msg,
                            state="error",
                            task_key=task_key,
                        )
                    if msg != "用户取消":
                        QMessageBox.warning(self, "合成失败", msg)
                    _notify_composed(False)

                self._run_helicon_stack(
                    jpg_paths,
                    out_path,
                    cur_params,
                    _open_compose_review_after_helicon_success,
                    _report_interactive_compose_failure,
                    workflow_task_key=task_key,
                )

            _run_interactive_compose_with_preview(group.jpg_paths, output_path, params)

        except RuntimeError as exc:
            QMessageBox.warning(self, "合成失败", str(exc))
            _notify_composed(False)
        except Exception as exc:
            QMessageBox.warning(self, "合成失败", f"意外错误：{exc}")
            _notify_composed(False)

    def _helicon_output_opts(self) -> dict:
        """Read Helicon output options from settings (mirrors oracle 输出选项).

        Returns ``{format, tiff_compression, quality}``. Default output is TIFF
        (per the software's design: 输出 TIF 或 JPG, 默认 TIF). For TIF the chosen
        TIFF compression is returned and quality is None; for JPG, quality is
        returned and tiff_compression is None — so only the relevant CLI flag
        (-tif: / -j:) gets emitted, exactly like the oracle.
        """
        from app.views.settings_view import (
            _K_HELICON_OUTPUT_FORMAT,
            _K_HELICON_QUALITY,
            _K_HELICON_TIFF_COMPRESSION,
        )
        qs = self.ctx.settings._qs
        fmt = "jpg" if str(qs.value(_K_HELICON_OUTPUT_FORMAT, "tif")).lower() == "jpg" else "tif"
        if fmt == "jpg":
            return {
                "format": "jpg",
                "tiff_compression": None,
                "quality": int(qs.value(_K_HELICON_QUALITY, 95)),
            }
        return {
            "format": "tif",
            "tiff_compression": str(qs.value(_K_HELICON_TIFF_COMPRESSION, "u")) or "u",
            "quality": None,
        }

    @staticmethod
    def _with_output_ext(path: str, fmt: str) -> str:
        """Swap *path*'s extension to match output format (tif/jpg).

        Helicon infers the encoder from the -save: extension, so it MUST agree
        with the -tif:/-j: flag (oracle app.js:7283-7291).
        """
        base, _ = os.path.splitext(path)
        return base + (".jpg" if fmt == "jpg" else ".tif")

    def _run_helicon_stack(
        self,
        jpg_paths: list[str],
        output_path: str,
        params: dict,
        on_finished,
        on_failed,
        *,
        show_progress_dialog: bool = True,
        workflow_task_key: str = "",
    ) -> None:
        """Launch HeliconWorker (non-blocking), optionally with Helicon progress UI.

        *on_finished(tiff_path: Path)* is called on success.
        *on_failed(msg: str)* is called on error or cancel.
        """
        from app.services.helicon_service import build_helicon_cmd

        # Wire the output options (输出格式 / TIFF 压缩 / JPEG 质量) into the CLI —
        # oracle app.js:7290-7291 (-tif: for tif, -j: for jpg). Previously these
        # settings were saved but never applied (output was always uncompressed tif).
        opts = self._helicon_output_opts()
        try:
            cmd = build_helicon_cmd(
                jpg_paths=jpg_paths,
                output_file=output_path,
                method=str(params["method"]),
                radius=str(params["radius"]),
                smoothing=str(params["smoothing"]),
                tiff_compression=opts["tiff_compression"],
                quality=opts["quality"],
                input_list_dir=os.path.dirname(os.path.abspath(output_path)),
            )
        except FileNotFoundError as exc:
            on_failed(str(exc))
            return
        except RuntimeError as exc:
            on_failed(str(exc))
            return

        progress = None
        if show_progress_dialog:
            progress = QProgressDialog(
                f"正在合成 {len(jpg_paths)} 张 JPG…",
                "取消",
                0,
                0,
                self,
            )
            progress.setWindowModality(Qt.WindowModality.NonModal)
            progress.setWindowTitle("Helicon 合成")
            progress.setMinimumDuration(0)

        worker = HeliconWorker(cmd=cmd, output_path=output_path, parent=self)
        if not hasattr(self, "_helicon_workers"):
            self._helicon_workers = set()
        if not hasattr(self, "_helicon_progress_dialogs"):
            self._helicon_progress_dialogs = set()
        if not hasattr(self, "_helicon_worker_by_task_key"):
            self._helicon_worker_by_task_key = {}
        self._helicon_workers.add(worker)
        task_key = str(workflow_task_key or "")
        if task_key:
            self._helicon_worker_by_task_key[task_key] = worker
        if progress is not None:
            self._helicon_progress_dialogs.add(progress)
        self._helicon_worker = worker  # legacy single-worker reference
        callback_sent = {"value": False}

        def _release_helicon_worker() -> None:
            if progress is not None:
                progress.close()
            self._helicon_workers.discard(worker)
            if progress is not None:
                self._helicon_progress_dialogs.discard(progress)
            if getattr(self, "_helicon_worker", None) is worker:
                self._helicon_worker = None
            for key, mapped in list(getattr(self, "_helicon_worker_by_task_key", {}).items()):
                if mapped is worker:
                    self._helicon_worker_by_task_key.pop(key, None)
            if progress is not None and getattr(self, "_helicon_progress", None) is progress:
                self._helicon_progress = None
            worker.deleteLater()

        def _handle_helicon_worker_finished(tiff_path):
            _release_helicon_worker()
            if callback_sent["value"]:
                return
            callback_sent["value"] = True
            on_finished(tiff_path)

        def _handle_helicon_worker_failed(msg: str):
            _release_helicon_worker()
            if callback_sent["value"]:
                return
            callback_sent["value"] = True
            on_failed(msg)

        if progress is not None:
            def _cancel_running_helicon_worker():
                worker.cancel()
                progress.setLabelText("正在取消 Helicon 合成…")
                if not callback_sent["value"]:
                    callback_sent["value"] = True
                    on_failed("用户取消")

        worker.finished.connect(_handle_helicon_worker_finished)
        worker.failed.connect(_handle_helicon_worker_failed)
        if progress is not None:
            progress.canceled.connect(_cancel_running_helicon_worker)

        if progress is not None:
            progress.show()
            self._helicon_progress = progress  # legacy single-progress reference
        worker.start()

    # ── 批量[合成]/[合成+整理] 顺序队列 ────────────────────────────────────────
    # 合成与整理都在后台 worker 完成。批量绝不能紧循环 emit——会同时启动
    # 多个 worker 互相覆盖,且整理会在合成完成前读到空 composed。
    # 故由 workbench 串行驱动:合成完成(异步回调)→ 后台整理该组 → 下一组。
    # 批量时走 `_compose_group_headless`(无预览/结果确认框),满足"一键直合"。

    def _resolve_compose_output_name(self, db, uid, group, results_dir, incoming_dir):
        """统一的「输出 TIF 名」解析(合成单组/批量共用)。返回 (name.tif, seq)。

        优先级:
          ① 用户在该组「输出 TIF」框手填的覆盖值(去后缀+.tif)
          ② 有真编号 → organize_preview 建议成果名(编号-序号.tif)
          ③ 无编号(临时分组 ad-hoc) → 组序.tif(组0→1.tif, 组1→2.tif)
        seq:真编号取 preview.next_seq;ad-hoc 取 group_index+1。
        """
        from app.services.compose_workflow_service import resolve_compose_output_name
        return resolve_compose_output_name(db, uid, group, results_dir, incoming_dir)

    def _start_compose_batch(self, uid: str, organise: bool) -> None:
        """启动批量合成队列。organise=True 时每组合成完后立即整理该组。"""
        if not uid:
            return
        grouping = self._get_grouping_for_uid(uid)
        selected = set()
        try:
            selected = set(self._grouping.selected_group_indexes())
        except Exception:
            selected = set()
        queue = list(compose_batch_queue(grouping, selected))
        if not queue:
            # 无待合成组:合成+整理 → 退而整理已合成组;纯合成 → 状态栏提示。
            if organise:
                self._organise_all_batch(
                    uid,
                    silent_batch=True,
                    workflow_label="批量合成+整理",
                )
            else:
                self._batch_status("无待合成组。")
                self._workflow_notice(
                    "批量合成无需处理",
                    "没有待合成的分组。",
                    state="info",
                    force_show=True,
                    task_key=f"batch-compose-empty:{uid}",
                )
            return
        # 只有确实存在待合成组时才需要 Helicon。已有 TIF 的组点
        # [合成+整理]应直接进入归档，不能被合成工具检测提前拦截。
        from app.services.helicon_service import detect_helicon
        if not detect_helicon():
            QMessageBox.warning(
                self, "未检测到 Helicon Focus",
                "未找到 Helicon Focus，无法批量合成。请安装并设置 "
                "HELICON_FOCUS_PATH 环境变量。",
            )
            self._workflow_notice(
                "批量合成失败",
                "未找到 Helicon Focus，无法批量合成。",
                state="error",
                force_show=True,
                task_key=f"batch-compose-no-helicon:{uid}",
            )
            return
        label = "批量合成+整理" if organise else "批量合成"
        task_key = f"batch-compose:{uid}:{'organise' if organise else 'compose'}"
        self._batch = {
            "uid": uid,
            "queue": queue,
            "organise": organise,
            "total": len(queue),
            "done": 0,
            "label": label,
            "task_key": task_key,
        }
        self._workflow_notice(
            f"{label}：准备开始",
            f"共 {len(queue)} 组，将按顺序在后台处理。",
            state="busy",
            force_show=True,
            task_key=task_key,
        )
        self._compose_next_in_batch()

    def _compose_next_in_batch(self) -> None:
        """取队首组合成;队列空 → 清状态 + 状态栏提示完成。"""
        b = self._batch
        if not b:
            return
        if not b["queue"]:
            label = str(b.get("label") or "批量合成")
            total = int(b.get("total", 0))
            task_key = str(b.get("task_key") or "")
            self._batch = None
            self._batch_status("批量合成完成。")
            self._workflow_notice(
                f"{label}完成",
                f"已处理 {total} 组。",
                state="success",
                task_key=task_key,
            )
            return
        uid = b["uid"]
        idx = b["queue"].pop(0)
        done = int(b.get("done", 0))
        total = int(b.get("total", done + 1))
        label = str(b.get("label") or "批量合成")
        task_key = str(b.get("task_key") or "")
        self._batch_status(f"批量合成 {done + 1}/{total}：组 {idx}")
        self._workflow_notice(
            f"{label}：正在合成",
            f"第 {done + 1}/{total} 组：组 {idx}。",
            state="busy",
            task_key=task_key,
        )
        self._compose_group_headless(
            uid,
            idx,
            lambda ok: self._batch_group_done(ok, uid, idx),
            background=True,
            show_progress_dialog=not bool(b.get("organise")),
            workflow_task_key=task_key,
        )

    def _batch_group_done(self, success: bool, uid: str, group_index: int) -> None:
        """单组合成回调：需要整理时，等待后台归档结束再处理下一组。"""
        if self._batch is None:
            return
        self._batch["done"] = int(self._batch.get("done", 0)) + 1
        if success and self._batch.get("organise"):
            # 一条龙:批量整理走静默模式——跳过激活拦截/TIF改名框/同名确认/成功提示,
            # 但 JPG删除四闸 + TIFF永不自动删 红线照常(都在 archive_group 内,未碰)。
            started = self._on_organise_requested(
                uid,
                group_index,
                silent_batch=True,
                workflow_task_key=str(self._batch.get("task_key") or ""),
                workflow_label=str(self._batch.get("label") or "批量合成+整理"),
                on_complete=lambda _ok: self._compose_next_in_batch(),
            )
            if not started:
                self._compose_next_in_batch()
            return
        elif not success:
            done = int(self._batch.get("done", 0))
            total = int(self._batch.get("total", done))
            label = str(self._batch.get("label") or "批量合成")
            task_key = str(self._batch.get("task_key") or "")
            self._batch_status(f"组 {group_index} 合成失败，继续下一组（{done}/{total}）")
            self._workflow_notice(
                f"{label}：合成失败，继续下一组",
                f"组 {group_index} 合成失败；已完成 {done}/{total}。",
                state="error",
                task_key=task_key,
            )
        self._compose_next_in_batch()

    def _batch_status(self, msg: str) -> None:
        """非阻塞反馈(状态栏);批量回调里绝不用模态框——会卡死且打断链路。"""
        try:
            self.window().statusBar().showMessage(msg, 4000)
        except Exception:
            pass

    def _compose_group_headless(
        self,
        uid: str,
        group_index: int,
        on_done,
        *,
        background: bool = False,
        show_progress_dialog: bool = True,
        workflow_task_key: str = "",
    ) -> None:
        """批量用:无确认框合成单组。完成调 on_done(success: bool)。

        复刻 `_on_compose_requested` 成功路径的保存块,但剥掉预览框/结果框
        (满足"一键直合不弹框")。组 JPG < 2 直接 on_done(False) 跳过。
        产出名:每组 output_name 覆盖 > 否则 organize_preview 的建议成果名。
        """
        db = self.ctx.get_db()
        if not uid:
            on_done(False)
            return
        try:
            grouping = self._get_grouping_for_uid(uid)
            group = next(
                (g for g in grouping.groups if g.group_index == group_index), None
            )
            if group is None or len(group.jpg_paths) < 2:
                on_done(False)  # 批量不弹隐式兜底问句,JPG 不足直接跳过
                return

            incoming_dir, results_dir = self._ensure_compose_incoming_dir(group)

            output_name, _seq = self._resolve_compose_output_name(
                db, uid, group, results_dir, incoming_dir)
            output_path = os.path.join(incoming_dir, output_name)
            output_path = self._with_output_ext(
                output_path, self._helicon_output_opts()["format"]
            )
            params = self._helicon_params.get_params()

            def _save_headless_compose_result(tiff_path):
                try:
                    persisted = persist_composed_group(
                        db,
                        uid,
                        grouping,
                        group.group_index,
                        tiff_path=output_path,
                        result_sequence=_seq,
                    )
                except Exception:
                    on_done(False)
                    return
                panel_uid = getattr(self._grouping, "_uid", None)
                if not background or self._current_uid == uid or panel_uid == uid:
                    self._grouping.load_grouping(uid, persisted.grouping)
                    self._refresh_results_column(uid, persisted.grouping)
                self._on_helicon_finished(uid, select_uid=not background)
                on_done(True)

            def _mark_headless_compose_failed(msg: str):
                on_done(False)

            self._run_helicon_stack(
                group.jpg_paths,
                output_path,
                params,
                _save_headless_compose_result,
                _mark_headless_compose_failed,
                show_progress_dialog=show_progress_dialog,
                workflow_task_key=workflow_task_key,
            )
        except Exception:
            on_done(False)

    def _organise_all_batch(
        self,
        uid: str,
        silent_batch: bool = False,
        workflow_label: str | None = None,
    ) -> None:
        """[整理] 批量串行归档已合成未归档组。"""
        if not uid:
            return
        label = workflow_label or "批量整理"
        grouping = self._get_grouping_for_uid(uid)
        selected = set()
        try:
            selected = set(self._grouping.selected_group_indexes())
        except Exception:
            selected = set()
        targets = organize_batch_targets(grouping, selected)
        if not targets:
            self._batch_status("无可整理组。")
            self._workflow_notice(
                f"{label}无需处理",
                "没有可整理的已合成分组。",
                state="info",
                force_show=True,
                task_key=f"organise-batch-empty:{uid}:{label}",
            )
            return
        if getattr(self, "_organise_batch", None) is not None:
            self._batch_status("整理队列正在运行，请稍候。")
            self._workflow_notice(
                f"{label}已在运行",
                "整理队列正在运行，请等待当前任务完成。",
                state="info",
                force_show=True,
                task_key=f"organise-batch-running:{uid}:{label}",
            )
            return
        task_key = f"organise-batch:{uid}:{label}"
        self._organise_batch = {
            "uid": uid,
            "queue": list(targets),
            "silent_batch": silent_batch,
            "total": len(targets),
            "done": 0,
            "label": label,
            "task_key": task_key,
        }
        self._workflow_notice(
            f"{label}：准备开始",
            f"共 {len(targets)} 组，将按顺序后台整理。",
            state="busy",
            force_show=True,
            task_key=task_key,
        )
        self._organise_next_in_batch()

    def _organise_next_in_batch(self) -> None:
        """Run the next archive job after the previous worker has finished."""
        b = getattr(self, "_organise_batch", None)
        if not b:
            return
        if not b["queue"]:
            label = str(b.get("label") or "批量整理")
            total = int(b.get("total", 0))
            task_key = str(b.get("task_key") or "")
            self._organise_batch = None
            self._batch_status("批量整理完成。")
            self._workflow_notice(
                f"{label}完成",
                f"已整理 {total} 组。",
                state="success",
                task_key=task_key,
            )
            return
        uid = b["uid"]
        idx = b["queue"].pop(0)
        done = int(b.get("done", 0))
        total = int(b.get("total", done + 1))
        label = str(b.get("label") or "批量整理")
        task_key = str(b.get("task_key") or "")
        self._batch_status(f"批量整理 {done + 1}/{total}：组 {idx}")
        self._workflow_notice(
            f"{label}：正在整理",
            f"第 {done + 1}/{total} 组：组 {idx}。",
            state="busy",
            task_key=task_key,
        )
        callback_sent = {"value": False}

        def _done(_ok: bool) -> None:
            if callback_sent["value"]:
                return
            callback_sent["value"] = True
            current = getattr(self, "_organise_batch", None)
            if current is not None:
                current["done"] = int(current.get("done", 0)) + 1
            self._organise_next_in_batch()

        started = self._on_organise_requested(
            uid,
            idx,
            silent_batch=bool(b.get("silent_batch")),
            workflow_task_key=task_key,
            workflow_label=label,
            on_complete=_done,
        )
        if not started:
            _done(False)

    def _on_organise_selected(self) -> None:
        """整理监控区选中的 JPG + TIFF，不重新合成。

        有激活编号：TIFF 自动按激活编号的下一个成果名重命名，ZIP 同名。
        无激活编号：保留 TIFF 文件名，ZIP 直接用 TIFF stem 命名。
        """
        try:
            jpg_paths = self._monitor.selected_jpg_paths()
            tiff_paths = self._monitor.selected_tiff_paths()
        except Exception:
            jpg_paths, tiff_paths = [], []
        if len(tiff_paths) != 1 or not jpg_paths:
            QMessageBox.information(
                self,
                "整理",
                "请选中至少 1 张 JPG 原片和 1 个 TIFF 成片。",
            )
            return
        self._organise_jpgs_with_tiff(jpg_paths, tiff_paths[0], silent=True)

    def _organise_jpgs_with_tiff(
        self,
        jpg_paths: list[str],
        tiff_path: str,
        *,
        silent: bool,
        on_complete=None,
    ) -> bool:
        """把已有 JPG + TIFF 登记成已合成组，然后走统一整理/归档入口。"""
        db = self.ctx.get_db()
        project_dir = self.ctx.current_project_dir
        if not db or not project_dir:
            self._status_message("请先打开项目")
            return False

        active_uid = self._get_active_uid()
        incoming_subdir = "incoming-jpg"
        results_subdir = "results"
        if active_uid:
            try:
                incoming_subdir, results_subdir = self._resolve_capture_subdirs()
            except Exception as exc:
                if silent:
                    self._status_message(f"TIFF 按编号命名失败：{exc}")
                else:
                    QMessageBox.warning(self, "整理", f"TIFF 按编号命名失败：{exc}")
                return False

        try:
            prepared = prepare_existing_tiff_group(
                db,
                active_uid=active_uid,
                jpg_paths=jpg_paths,
                tiff_path=tiff_path,
                project_dir=project_dir,
                incoming_subdir=incoming_subdir,
                results_subdir=results_subdir,
            )
        except Exception as exc:
            prefix = "TIFF 按编号命名失败" if active_uid else "整理准备失败"
            if silent:
                self._status_message(f"{prefix}：{exc}")
            else:
                QMessageBox.warning(self, "整理", f"{prefix}：{exc}")
            return False

        self._grouping.load_grouping(prepared.uid, prepared.grouping)
        return bool(self._on_organise_requested(
            prepared.uid,
            prepared.group_index,
            silent_batch=silent,
            allow_single_jpg=True,
            workflow_label="整理",
            on_complete=on_complete,
        ))

    def _maybe_auto_organize(self, uid: str, group_index: int) -> None:
        """合成成功后的自动整理钩子。

        「自动归档」打开时，直接复用手动整理入口 `_on_organise_requested`：
        把这组源 JPG 打包 ZIP、命名并移到 results。
        """
        if self._auto_archive_enabled():
            self._on_organise_requested(uid, group_index)

    def _load_naming_components(self) -> list[str]:
        from app.services.project_settings_service import (
            DEFAULT_NAMING_RULES,
            load_setting,
        )
        from app.utils.naming import normalize_naming_components

        db = self.ctx.get_db()
        rules = (
            load_setting(db, "naming_rules", DEFAULT_NAMING_RULES)
            if db
            else DEFAULT_NAMING_RULES
        )
        return normalize_naming_components(rules.get("components"))

    def _try_bind_adhoc_to_existing_specimen(self, uid: str) -> bool:
        """Link ad-hoc grouping to an existing voucher (no false duplicate warn)."""
        from app.services.grouping_service import ADHOC_GROUPING_UID, SpecimenGrouping

        panel_uid = getattr(self._grouping, "_uid", None)
        if panel_uid not in (None, ADHOC_GROUPING_UID):
            return False
        text = str(uid or "").strip()
        if not text:
            return False
        db = self.ctx.get_db()
        if not db:
            return False
        row = db.execute(
            "SELECT uid FROM specimens WHERE uid = ?", (text,)
        ).fetchone()
        if not row:
            return False
        self._current_uid = text
        self._naming.acknowledge_existing_uid(text)
        panel_grouping = getattr(self._grouping, "_grouping", None)
        if panel_grouping is not None:
            self._grouping.load_grouping(
                text,
                SpecimenGrouping(uid=text, groups=panel_grouping.groups),
            )
        return True

    def _recognize_first_grouping_tiff(self, grouping) -> None:
        """Use the first linked TIFF in the open grouping to seed right-rail fields."""
        if grouping is None or not getattr(grouping, "groups", None):
            return
        if self._naming.has_result_identity():
            return
        for group in grouping.groups:
            tiff_path = getattr(group, "composed_tiff_path", None)
            if tiff_path:
                self._apply_tiff_filename_recognition(tiff_path)
                return

    def _apply_tiff_filename_recognition(
        self,
        tiff_path: str,
        *,
        overwrite: bool = False,
    ) -> None:
        """Parse legacy/standard TIFF names → fill right-rail fields (non-destructive)."""
        from app.services.grouping_service import ADHOC_GROUPING_UID
        from app.utils.naming import recognize_tiff_filename

        components = self._load_naming_components()
        rec = recognize_tiff_filename(Path(tiff_path).stem, components)
        if rec is None:
            return

        if overwrite:
            self._current_uid = None

        self._naming.apply_recognized_fields(
            rec.field_values,
            collection_date=rec.collection_date,
            photo_date=rec.photo_date,
            sequence=rec.sequence,
            inline_labels=rec.inline_labels,
            source_filename=Path(tiff_path).name,
            overwrite=overwrite,
        )

        panel_uid = getattr(self._grouping, "_uid", None)
        preview_uid = self._naming.current_uid()
        target_uid = preview_uid or rec.uid
        if panel_uid in (None, ADHOC_GROUPING_UID) and target_uid:
            if not self._try_bind_adhoc_to_existing_specimen(target_uid):
                db = self.ctx.get_db()
                if db:
                    row = db.execute(
                        "SELECT uid FROM specimens WHERE uid = ?", (target_uid,)
                    ).fetchone()
                    if row:
                        self._load_specimen(target_uid)
                    else:
                        self._current_uid = None
                panel_grouping = getattr(self._grouping, "_grouping", None)
                if panel_grouping is not None:
                    from app.services.grouping_service import SpecimenGrouping
                    self._grouping.load_grouping(
                        target_uid,
                        SpecimenGrouping(uid=target_uid, groups=panel_grouping.groups),
                    )

        self._sync_grouping_outputs_from_naming()
        self._status_message(
            f"已从 TIF 识别编号（{target_uid or rec.uid}，成果 #{rec.sequence}）。"
            "未对应字段已写入拍照备注；保存方式等可稍后补全。"
        )

    def _on_naming_storage_applied(self, old_code: str, new_code: str) -> None:
        """保存方式选定后：刷新拍照备注 + 自动更新分组里的成果输出名（带提示）。"""
        if not (new_code or "").strip():
            return
        self._naming.refresh_legacy_photo_notes()
        updated = self._sync_grouping_outputs_from_naming()
        self._try_bind_adhoc_to_existing_specimen(self._naming.current_uid())
        if updated:
            self._status_message(
                f"已加入保存方式 {new_code.strip()}，成果文件名已同步更新。"
                "请在分组工具「输出」栏核对。"
            )
        elif old_code != new_code:
            self._status_message(
                f"已选择保存方式 {new_code.strip()}，编号预览已更新。"
            )

    def _sync_grouping_outputs_from_naming(self) -> bool:
        """Push standard result stems into auto-legacy group output fields."""
        grouping = getattr(self._grouping, "_grouping", None)
        if grouping is None or not grouping.groups:
            return False
        if not self._naming._storage.text().strip():
            return False
        if not self._naming.has_result_identity():
            return False
        changed = False
        for g in grouping.groups:
            if (
                getattr(g, "status", None) == "organized"
                or getattr(g, "archive_zip", None)
            ):
                continue
            current = getattr(g, "output_name", None) or ""
            if not self._naming.group_output_looks_auto_legacy(current):
                continue
            seq = (
                self._naming._seq.value()
                if len(grouping.groups) == 1
                else g.group_index + 1
            )
            new_stem = self._naming.suggested_result_stem(seq=seq)
            if not new_stem or new_stem == current:
                continue
            g.output_name = new_stem
            changed = True
        if changed:
            self._grouping._rebuild()
            self._grouping.grouping_changed.emit()
        return changed

    def _maybe_rename_tiff_before_organize(self, db, uid, grouping, group, project_dir):
        """整理前的 TIFF 命名网关。返回 None=无需改名 / True=已改名 / False=用户取消。

        合成 TIFF 名不符成果规范时（导入的外部 Helicon TIFF），弹确认框按本组编号的下个
        成果名建议改名（可改），确认则磁盘改名 + 更新 group.composed_tiff_path + 持久化。
        """
        from app.utils.naming import (
            recognize_tiff_filename,
            suggest_tiff_filename_preserve_legacy,
            tiff_stem_needs_rename_for_organize,
            coalesce_specimen_dates,
            specimen_date_seg,
        )
        from app.services.organize_service import organize_preview, rename_tiff
        from app.services.grouping_service import save_grouping
        from app.widgets.tiff_rename_dialog import TiffRenameDialog

        tiff_path = group.composed_tiff_path
        current = Path(tiff_path).name
        components = self._load_naming_components()
        stem = Path(tiff_path).stem

        self._apply_tiff_filename_recognition(tiff_path)

        panel_uid = self._naming.current_uid()
        panel_storage = self._naming._storage.text().strip()
        if not tiff_stem_needs_rename_for_organize(
            stem,
            components,
            panel_uid=panel_uid,
            panel_storage=panel_storage,
        ):
            return None

        rec = recognize_tiff_filename(stem, components)
        if rec:
            col, photo = coalesce_specimen_dates(
                self._naming._collection_date.text().strip(),
                self._naming._photo_date.text().strip(),
            )
            date_seg = specimen_date_seg(col or None, photo or None)
            values = {
                "province": self._naming._province.text().strip(),
                "site": self._naming._site.text().strip(),
                "station": self._naming._station.text().strip(),
                "species_id": self._naming._species_id.text().strip(),
                "storage": panel_storage,
                "date_seg": date_seg,
                "collection_date": col,
                "photo_date": photo,
            }
            suggested = suggest_tiff_filename_preserve_legacy(
                stem, components, values, seq=rec.sequence,
            ) + ".tif"
        else:
            inc, res = self._resolve_capture_subdirs()
            try:
                preview = organize_preview(
                    db, uid,
                    os.path.join(project_dir, res),
                    os.path.join(project_dir, inc),
                )
                suggested = preview.suggested_tiff_name
            except Exception:
                suggested = current

        dlg = TiffRenameDialog(current, suggested, parent=self._grouping_ui_parent())
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return False  # 取消 → 中止整理
        new_name = dlg.new_name()
        if not new_name:
            return False
        try:
            new_path = rename_tiff(tiff_path, new_name)
        except Exception as exc:
            ui.warn(self._grouping_ui_parent(), "整理", f"TIFF 改名失败：{exc}")
            return False
        if new_path != tiff_path:
            group.composed_tiff_path = new_path
            try:
                save_grouping(db, uid, grouping.groups, clean_phantoms=False)
            except Exception:
                pass
        return True

    def _organize_tif_only_group(
        self,
        uid: str,
        grouping,
        group,
        *,
        db,
        project_dir: str,
        has_project: bool,
        silent_batch: bool,
        workflow_task_key: str = "",
        workflow_label: str = "整理",
        on_complete=None,
    ) -> bool:
        """Register an existing TIFF to the current specimen without JPG ZIP."""
        dlg_parent = self._grouping_ui_parent()
        if (
            not getattr(group, "composed_tiff_path", "")
            or not os.path.isfile(group.composed_tiff_path)
        ):
            if not silent_batch:
                ui.warn(dlg_parent, "整理", "找不到该组 TIFF 文件。")
            self._workflow_notice(
                f"{workflow_label}失败",
                "找不到该组 TIFF 文件。",
                state="error",
                task_key=workflow_task_key,
            )
            if on_complete is not None:
                on_complete(False)
            return False

        from app.services.capture_workflow_service import register_tif_only_group

        _inc, res = self._resolve_capture_subdirs() if has_project else ("", "results")
        project_root = getattr(self.ctx, "current_project_root", None)
        if not isinstance(project_root, str):
            project_root = None
        result = register_tif_only_group(
            db,
            uid,
            grouping,
            group,
            project_dir=project_dir if has_project else "",
            results_subdir=res,
            project_root=project_root,
        )
        if result.metadata.error:
            self._status_message(f"TIFF 元数据写入失败：{result.metadata.error}")
        elif (
            result.metadata.skipped
            and result.metadata.skipped not in {"disabled", "unchanged"}
        ):
            self._status_message(f"TIFF 元数据未写入：{result.metadata.skipped}")

        self._refresh_monitor()
        if not silent_batch or self._current_uid == uid:
            self._grouping.load_grouping(uid, grouping)
            self._refresh_results_column(uid, grouping)
        self._on_organize_finished(uid, select_uid=not silent_batch)

        if not silent_batch:
            ui.info(
                dlg_parent,
                "整理完成",
                f"TIFF 已登记到当前编号：\n{uid}\n\n"
                f"文件：{Path(result.tiff_path).name}\n"
                "未生成 ZIP：该组没有 JPG 原片。",
            )
        else:
            self._batch_status(f"TIF 已整理：{Path(result.tiff_path).name}")
        self._workflow_notice(
            f"{workflow_label}完成",
            f"TIFF 已登记：{Path(result.tiff_path).name}。该组没有 JPG 原片，未生成 ZIP。",
            state="success",
            task_key=workflow_task_key,
        )
        if on_complete is not None:
            on_complete(True)
        return True

    def _on_organise_requested(
        self,
        uid: str,
        group_index: int,
        silent_batch: bool = False,
        allow_single_jpg: bool = False,
        workflow_task_key: str = "",
        workflow_label: str | None = None,
        on_complete=None,
    ) -> bool:
        """Organise (archive) the composed group.

        silent_batch=True(批量[合成+整理]一条龙):跳过激活拦截框、TIF改名框、
        同名ZIP确认框、成功提示框,失败静默返回——给"一键直跑"用。红线不变:
        JPG删除四闸 + TIFF永不自动删 都在 archive_group 内,silent 不绕。


        Gate checks (via organize_service._check_organize_gate):
          - uid must be active (or user explicitly bypasses)
          - group must have ≥2 JPGs
          - TIFF must already be composed

        delete_jpg is READ from settings; defaults to True after verified archival.
        Calls archive_service.archive_group (JPG→ZIP + optional delete).

        Oracle: server.js:3615-3840 organizeSpecimen; archive.js:67-190.
        """
        dlg_parent = self._grouping_ui_parent()
        db = self.ctx.get_db()
        project_dir = self.ctx.current_project_dir
        has_project = bool(db and project_dir)
        notice_label = workflow_label or ("合成+整理" if silent_batch else "整理")
        notice_task_key = workflow_task_key or f"organise:{uid}:{group_index}:{notice_label}"

        def _organise_not_started(message: str) -> bool:
            text = str(message or "整理条件未通过").strip()
            self._last_organise_failure_reason = text
            self._workflow_notice(
                f"{notice_label}失败",
                text,
                state="error",
                task_key=notice_task_key,
            )
            if silent_batch:
                self._status_message(f"整理未启动：{text}")
            return False

        self._last_organise_failure_reason = ""
        if not uid:
            if not silent_batch:
                ui.warn(dlg_parent, "整理", "请先打开分组后再整理。")
            return _organise_not_started("没有打开分组或编号")

        try:
            from app.services.organize_service import _check_organize_gate, OrganizeGateError

            self._save_timer.stop()
            self._flush_grouping_save()

            grouping = self._get_grouping_for_uid(uid)
            group_state = inspect_organize_group(grouping, group_index)
            group = group_state.group
            if group_state.reason == "missing-group":
                if not silent_batch:
                    ui.warn(
                        dlg_parent,
                        "整理",
                        f"找不到组{group_index + 1}。请关闭分组工具后重新打开再试。",
                )
                return _organise_not_started(f"找不到组{group_index + 1}")

            if group_state.already_organized:
                if not silent_batch:
                    self._status_message("该组已整理，有 ZIP 归档，无需再次整理。")
                self._workflow_notice(
                    f"{notice_label}无需处理",
                    "该组已整理，有 ZIP 归档，无需再次整理。",
                    state="info",
                    task_key=notice_task_key,
                )
                if on_complete is not None:
                    on_complete(True)
                return True

            if group_state.reason == "missing-tiff" or group is None:
                if not silent_batch:
                    ui.warn(
                        dlg_parent, "整理", "该组尚未合成，请先合成 TIFF 再整理。"
                    )
                return _organise_not_started("该组尚未合成 TIFF")

            # 整理前：若合成 TIFF 名不符成果命名规范（多见于导入的外部 Helicon TIFF），
            # 弹「TIFF 命名需确认」框，按本组编号成果名建议改名（守 S5：默认本组号、可改）。
            # 取消则中止整理；in-app 合成的 TIFF 本就规范，此路不触发。
            # silent_batch:批量时输出名是我们自己定的(编号-序号 / 组序.tif),不弹改名。
            if has_project and not silent_batch and self._maybe_rename_tiff_before_organize(
                db, uid, grouping, group, project_dir
            ) is False:
                return False

            gate_plan = plan_organize_gate_check(
                grouping,
                group,
                has_project=has_project,
                allow_single_jpg=allow_single_jpg,
                silent_batch=silent_batch,
            )
            if gate_plan.required:
                try:
                    _check_organize_gate(
                        db,
                        uid,
                        list(gate_plan.groups_as_dicts),
                        allow_inactive=gate_plan.allow_inactive,
                    )
                except OrganizeGateError as e:
                    if gate_plan.silent_skip_on_error:
                        return _organise_not_started(
                            str(e) or "JPG 不足或整理条件未通过"
                        )
                    if gate_plan.prompt_on_error:
                        reply = ui.question(
                            dlg_parent,
                            "整理确认",
                            f"{e}\n\n是否跳过激活检查继续整理？",
                        )
                        if reply != QMessageBox.StandardButton.Yes:
                            return False
                    else:
                        return _organise_not_started(str(e) or "整理条件未通过")

            if group_state.is_tif_only:
                self._workflow_notice(
                    f"{notice_label}：正在登记 TIFF",
                    f"正在登记 TIFF：{Path(group.composed_tiff_path).name}",
                    state="busy",
                    force_show=not bool(workflow_task_key),
                    task_key=notice_task_key,
                )
                return self._organize_tif_only_group(
                    uid,
                    grouping,
                    group,
                    db=db,
                    project_dir=project_dir,
                    has_project=has_project,
                    silent_batch=silent_batch,
                    workflow_task_key=notice_task_key,
                    workflow_label=notice_label,
                    on_complete=on_complete,
                )

            # Default workflow: verified ZIP replaces loose JPGs. Organise does
            # not auto-delete TIFF; explicit delete/undo is a separate action.
            delete_jpg: bool = True
            try:
                delete_jpg = bool(
                    getattr(self.ctx.settings, "delete_jpg_after_archive", True)
                )
            except Exception:
                pass

            if group.jpg_paths:
                from app.services.organize_workflow_service import resolve_group_jpg_paths

                resolved_jpgs, missing_jpgs = resolve_group_jpg_paths(group.jpg_paths)
                if missing_jpgs:
                    sample = "\n".join(missing_jpgs[:5])
                    extra = f"\n…等 {len(missing_jpgs)} 个" if len(missing_jpgs) > 5 else ""
                    message = (
                        f"以下 JPG 在磁盘上找不到：\n{sample}{extra}\n\n"
                        "请确认照片仍在 incoming-jpg 目录；WSL 下请用 /mnt/n/... 打开项目。"
                    )
                    if silent_batch:
                        return _organise_not_started(message)
                    ui.warn(dlg_parent, "整理失败", message)
                    return False
                group.jpg_paths = resolved_jpgs

            # 有项目：归档直接进入当前项目 results/。
            # 无项目：就地整理，ZIP 放在 TIFF 同目录，文件名等于 TIFF 基础名。
            res = "results"
            if has_project:
                _inc, res = self._resolve_capture_subdirs()
            archive_plan = plan_archive_worker(
                project_dir=project_dir if has_project else "",
                results_subdir=res,
                tiff_path=group.composed_tiff_path,
                delete_jpg_after_archive=delete_jpg,
            )
            archive_output_dir = archive_plan.archive_output_dir
            os.makedirs(archive_output_dir, exist_ok=True)

            # ── Collision guard: if ZIP already exists at the target path,  #cursor
            #    warn and let user choose overwrite / skip. ──────────────
            existing_zip = archive_plan.existing_zip
            if archive_plan.existing_zip_exists:
                if silent_batch:
                    return _organise_not_started(
                        f"同名 ZIP 已存在：{Path(existing_zip).name}，为避免覆盖已跳过"
                    )
                reply_col = ui.question(
                    dlg_parent,
                    "归档文件已存在",
                    f"同名归档 ZIP 已存在：\n{Path(existing_zip).name}\n\n"
                    "是否覆盖并重新归档？",
                )
                if reply_col != QMessageBox.StandardButton.Yes:
                    return False

            # Archive in a worker thread.  Large photo groups can still take
            # time; doing this in the GUI thread made the whole
            # window appear hung and gave the user no indication that work was
            # still in progress.
            from app.workers.supp_compression_worker import SuppCompressionWorker

            # Progress is now shown in the unified background-task window.  Do
            # not open a second per-action dialog for archive jobs.
            progress = None

            # Two-phase archive: ZIP first, DB finalize, then delete JPGs.
            request_delete_jpg = archive_plan.delete_jpg
            worker = SuppCompressionWorker(
                jpg_paths=group.jpg_paths,
                tiff_path=group.composed_tiff_path,
                project_dir=project_dir or archive_output_dir,
                delete_jpg=False,
                method=getattr(self.ctx.settings, "jxl_effort_method", "standard"),
                concurrency=getattr(self.ctx.settings, "jxl_concurrency", 4),
                output_dir=archive_output_dir,
                parent=self,
            )
            if not hasattr(self, "_archive_workers"):
                self._archive_workers = set()
            if not hasattr(self, "_archive_worker_by_task_key"):
                self._archive_worker_by_task_key = {}
            self._archive_workers.add(worker)
            self._archive_worker_by_task_key[notice_task_key] = worker
            self._workflow_notice(
                f"{notice_label}：正在整理",
                f"正在把 {len(group.jpg_paths)} 张 JPG 写入 ZIP：{Path(group.composed_tiff_path).name}",
                state="busy",
                force_show=not bool(workflow_task_key),
                task_key=notice_task_key,
            )
            if silent_batch:
                self._status_message(
                    f"正在整理：{Path(group.composed_tiff_path).name}，打包 {len(group.jpg_paths)} 张 JPG…"
                )

            def _release_worker() -> None:
                if progress is not None:
                    progress.close()
                self._archive_workers.discard(worker)
                for key, mapped in list(getattr(self, "_archive_worker_by_task_key", {}).items()):
                    if mapped is worker:
                        self._archive_worker_by_task_key.pop(key, None)
                worker.deleteLater()

            def _archive_progress(current: int, total: int, filename: str) -> None:
                verifying = str(filename or "") == "__verify_zip__"
                detail = (
                    "正在校验 ZIP 内容；校验通过后才会删除待处理区散落 JPG。"
                    if verifying
                    else f"正在打包第 {current}/{total} 张 JPG：{filename}"
                )
                if progress is not None:
                    progress.setMaximum(total)
                    progress.setValue(max(0, current - 1))
                    progress.setLabelText(
                        "正在校验 ZIP\n请稍候"
                        if verifying
                        else f"正在打包第 {current}/{total} 张\n{filename}"
                    )
                self._workflow_notice(
                    f"{notice_label}：正在整理",
                    detail,
                    state="busy",
                    task_key=notice_task_key,
                )

            def _archive_failed(message: str) -> None:
                _release_worker()
                self._last_organise_failure_reason = message or "归档过程出现错误。"
                self._workflow_notice(
                    f"{notice_label}失败",
                    self._last_organise_failure_reason,
                    state="error",
                    task_key=notice_task_key,
                )
                self._batch_status(f"整理失败：{message}")
                if not silent_batch:
                    ui.warn(
                        dlg_parent, "整理失败", message or "归档过程出现错误。"
                    )
                if on_complete is not None:
                    on_complete(False)

            def _archive_cancelled(message: str) -> None:
                _release_worker()
                reason = message or "用户取消"
                self._last_organise_failure_reason = reason
                self._workflow_notice(
                    f"{notice_label}已取消",
                    "整理归档已取消。JPG 没有删除，未完成 ZIP 已清理；已生成的 TIFF 仍保留在待处理区。",
                    state="info",
                    task_key=notice_task_key,
                )
                self._status_message("整理归档已取消，JPG 已保留。")
                if on_complete is not None:
                    on_complete(False)

            def _archive_finished(result) -> None:
                if not result.ok:
                    _archive_failed("归档过程出现错误。")
                    return
                _release_worker()
                from app.services.capture_workflow_service import finalize_archived_group

                project_root = getattr(self.ctx, "current_project_root", None)
                if not isinstance(project_root, str):
                    project_root = None
                try:
                    organized = finalize_archived_group(
                        db,
                        uid,
                        grouping,
                        group,
                        result,
                        archive_output_dir=archive_output_dir,
                        project_dir=project_dir if has_project else "",
                        project_root=project_root,
                    )
                except Exception as exc:  # noqa: BLE001
                    message = (
                        f"归档 ZIP 已生成，但成果登记失败：{exc}\n\n"
                        "JPG 原片仍保留在待处理区，可修正问题后重新整理。"
                    )
                    self._last_organise_failure_reason = message
                    self._workflow_notice(
                        f"{notice_label}失败",
                        message,
                        state="error",
                        task_key=notice_task_key,
                    )
                    self._batch_status(f"整理失败：{message}")
                    if not silent_batch:
                        ui.warn(dlg_parent, "整理失败", message)
                    if on_complete is not None:
                        on_complete(False)
                    return

                if request_delete_jpg and group.jpg_paths:
                    from app.services.archive_service import commit_jpg_deletion_after_archive

                    result = commit_jpg_deletion_after_archive(
                        result,
                        list(group.jpg_paths),
                    )
                if organized.metadata.error:
                    self._status_message(f"TIFF 元数据写入失败：{organized.metadata.error}")
                elif (
                    organized.metadata.skipped
                    and organized.metadata.skipped not in {"disabled", "unchanged"}
                ):
                    self._status_message(f"TIFF 元数据未写入：{organized.metadata.skipped}")

                msg = (
                    f"归档完成：{Path(group.archive_zip).name}\n"
                    "ZIP 内为原始 JPG，可直接解压使用。\n"
                )
                if result.delete_jpg:
                    msg += "JPG 原片已删除。"
                    finish_detail = (
                        f"已生成 {Path(group.archive_zip).name}；"
                        f"{result.file_count} 张 JPG 已写入 ZIP 并从待处理区删除。"
                    )
                elif result.requested_delete_jpg and not result.delete_jpg:
                    msg += f"JPG 保留（{result.deletion_skipped_reason}）。"
                    finish_detail = (
                        f"已生成 {Path(group.archive_zip).name}；JPG 已写入 ZIP，"
                        f"但删除前校验未通过，文件保留：{result.deletion_skipped_reason}"
                    )
                else:
                    finish_detail = (
                        f"已生成 {Path(group.archive_zip).name}；JPG 已写入 ZIP，"
                        "按当前设置保留在磁盘，但不再作为待处理照片显示。"
                    )
                self._workflow_notice(
                    f"{notice_label}完成",
                    finish_detail,
                    state="success",
                    task_key=notice_task_key,
                )
                if on_complete is not None:
                    on_complete(True)
                if not silent_batch:
                    ui.info(dlg_parent, "整理完成", msg)
                else:
                    self._batch_status(
                        f"整理完成：{Path(group.archive_zip).name}（{result.file_count} 张 JPG）"
                    )

                def _deferred_refresh() -> None:
                    self._refresh_monitor()
                    panel_uid = getattr(self._grouping, "_uid", None)
                    if not silent_batch or self._current_uid == uid or panel_uid == uid:
                        self._grouping.load_grouping(uid, grouping)
                        self._refresh_results_column(uid, grouping)
                    self._on_organize_finished(uid, select_uid=not silent_batch)
                    dlg = getattr(self, "_compose_organise_progress_dialog", None)
                    if dlg is not None and dlg.isVisible():
                        dlg._ensure_on_top()

                QTimer.singleShot(0, _deferred_refresh)

            worker.progress.connect(_archive_progress)
            worker.finished.connect(_archive_finished)
            worker.cancelled.connect(_archive_cancelled)
            worker.failed.connect(_archive_failed)
            if progress is not None:
                progress.show()
                ui.center_on(progress, dlg_parent)
                progress.raise_()
            worker.start()
            return True

        except FileNotFoundError as exc:
            if not silent_batch:
                ui.warn(dlg_parent, "整理失败", f"文件不存在：{exc}")
            return _organise_not_started(f"文件不存在：{exc}")
        except Exception as exc:
            if not silent_batch:
                ui.warn(dlg_parent, "整理失败", f"意外错误：{exc}")
            return _organise_not_started(f"意外错误：{exc}")

    # ── 补处理 (supplementary archival) ────────────────────────────────────────
    #   Archive a selected JPG + TIFF bundle WITHOUT requiring an active specimen.
    #   The specimen identity is read from the TIFF filename (oracle
    #   processSelectedMonitorFiles / startSmartCompression, app.js:4407/4282).

    def _on_supplementary_process(self) -> None:
        """补处理 button clicked → consume the monitor selection."""
        from app.utils import ui
        paths = self._monitor.selected_all_paths()
        if not paths:
            ui.info(self, "补处理", "请先在监控区选择 JPG 原片与 TIFF 成片")
            return
        self._run_supplementary(paths)

    def _on_supplementary_dropped(self, paths: list) -> None:
        """Files dropped onto the 补处理 button → archive them directly."""
        if paths:
            self._run_supplementary(list(paths))

    def _supp_autoname_tiff_by_active(self, db, project_dir, paths: list) -> list:
        """补处理前的兜底：外部名 TIF + 有激活编号 → 自动按激活编号成果名改名。

        只在「TIF 文件名反查不到标本」且「有激活编号」时改名；TIF 名本就规范则原样。
        返回（可能已把 TIF 路径替换为新名后的）路径列表。
        """
        try:
            from app.services.supplementary_service import resolve_specimen_for_tiff
            from app.services.organize_service import organize_preview, rename_tiff
        except Exception:
            return paths
        tiffs = [p for p in paths if str(p).lower().endswith((".tif", ".tiff"))]
        if len(tiffs) != 1:
            return paths
        tiff = tiffs[0]
        try:
            if resolve_specimen_for_tiff(db, Path(tiff).name) is not None:
                return paths  # 名能反查 → 不动
        except Exception:
            return paths
        active = self._get_active_uid()
        if not active:
            return paths  # 无激活编号 → 维持原状(会在 validate 报命名不规范)
        try:
            inc, res = self._resolve_capture_subdirs()
            preview = organize_preview(
                db, active,
                os.path.join(project_dir, res),
                os.path.join(project_dir, inc),
            )
            new_path = rename_tiff(tiff, preview.suggested_tiff_name)
        except Exception:
            return paths
        return [new_path if p == tiff else p for p in paths]

    def _run_supplementary(self, paths: list) -> None:
        from app.services.supplementary_service import (
            validate_supp_group,
            SuppGroupError,
        )
        from app.workers.supp_compression_worker import SuppCompressionWorker
        from app.utils import ui

        db = self.ctx.get_db()
        project_dir = self.ctx.current_project_dir
        has_project = bool(db and project_dir)

        if has_project:
            # 激活编号兜底命名：补处理本来只从 TIF 文件名反查标本；若 TIF 是外部名(反查
            # 不到)但当前有激活编号 → 自动按激活编号的成果名给 TIF 改名，再走补处理。
            # 落地"激活 → 自动命名"（用户设计），免得外部 Helicon 的 TIF 因名不规范被卡。
            paths = self._supp_autoname_tiff_by_active(db, project_dir, list(paths))

            # Validate selection → resolve specimen from TIFF name.
            try:
                grp = validate_supp_group(db, paths)
            except SuppGroupError as exc:
                ui.warn(self, "补处理", str(exc))
                return
        else:
            from app.services.supplementary_service import SuppGroup
            jpgs = [
                str(p) for p in paths
                if str(p).lower().endswith((".jpg", ".jpeg"))
            ]
            tiffs = [
                str(p) for p in paths
                if str(p).lower().endswith((".tif", ".tiff"))
            ]
            if len(jpgs) < 1 or len(tiffs) != 1 or len(jpgs) + len(tiffs) != len(paths):
                ui.warn(self, "补处理", "请选择至少 1 张 JPG 原片和 1 张 TIFF 成片后再整理")
                return
            grp = SuppGroup(jpg_paths=jpgs, tiff_path=tiffs[0], uid="", specimen=None)

        # Collision guard: project → results/；no project → TIFF 同目录。
        if has_project:
            _inc, res = self._resolve_capture_subdirs()
            results_dir = Path(project_dir) / res
        else:
            results_dir = Path(grp.tiff_path).parent
        tiff_stem = Path(grp.tiff_path).stem
        existing_zip = results_dir / f"{tiff_stem}.zip"
        existing_tiff = results_dir / Path(grp.tiff_path).name
        if existing_zip.is_file() or (
            existing_tiff.is_file()
            and str(existing_tiff) != str(Path(grp.tiff_path))
        ):
            reply = ui.question(
                self,
                "归档文件已存在",
                f"{results_dir} 下已存在同名成果：\n{tiff_stem}.*\n\n是否覆盖并重新归档？",
            )
            if reply != QMessageBox.StandardButton.Yes:
                return

        delete_jpg: bool = True
        try:
            delete_jpg = bool(
                getattr(self.ctx.settings, "delete_jpg_after_archive", True)
            )
        except Exception:
            pass

        # Stash for the finished handler (move-to-results + UI refresh).
        self._supp_pending = grp
        self._supp_worker = SuppCompressionWorker(
            grp.jpg_paths,
            grp.tiff_path,
            project_dir or str(results_dir),
            delete_jpg=delete_jpg,
            method=getattr(self.ctx.settings, "jxl_effort_method", "standard"),
            concurrency=getattr(self.ctx.settings, "jxl_concurrency", 4),
            output_dir=str(results_dir),
            parent=self,
        )
        self._supp_task_key = f"supplementary:{tiff_stem}:{id(self._supp_worker)}"
        self._workflow_notice(
            "补处理：准备整理",
            f"正在把 {len(grp.jpg_paths)} 张 JPG 写入 ZIP：{tiff_stem}.zip",
            state="busy",
            force_show=True,
            task_key=self._supp_task_key,
        )
        self._supp_worker.started_archiving.connect(self._on_supp_started)
        self._supp_worker.progress.connect(self._on_supp_progress)
        self._supp_worker.finished.connect(self._on_supp_finished)
        self._supp_worker.failed.connect(self._on_supp_failed)
        self._supp_worker.start()

    def _on_supp_started(self, jpg_count: int, tiff_stem: str) -> None:
        self._workflow_notice(
            "补处理：正在整理",
            f"正在归档 {jpg_count} 张原片 → {tiff_stem}.zip",
            state="busy",
            task_key=str(getattr(self, "_supp_task_key", "") or ""),
        )
        try:
            win = self.window()
            bar = win.statusBar() if hasattr(win, "statusBar") else None
            if bar is not None:
                bar.showMessage(f"正在归档 {jpg_count} 张原片 → {tiff_stem}.zip", 4000)
        except Exception:
            pass

    def _on_supp_progress(self, current: int, total: int, filename: str) -> None:
        self._workflow_notice(
            "补处理：正在整理",
            f"正在打包第 {current}/{total} 张 JPG：{filename}",
            state="busy",
            task_key=str(getattr(self, "_supp_task_key", "") or ""),
        )

    def _on_supp_finished(self, result) -> None:
        """Move TIFF + ZIP into results/ (decision①), then refresh + toast."""
        from app.utils import ui
        grp = getattr(self, "_supp_pending", None)
        task_key = str(getattr(self, "_supp_task_key", "") or "")
        self._supp_pending = None
        self._supp_task_key = ""
        project_dir = self.ctx.current_project_dir
        if not result or not getattr(result, "ok", False) or grp is None:
            self._workflow_notice(
                "补处理失败",
                "归档过程出现错误。",
                state="error",
                task_key=task_key,
            )
            ui.warn(self, "补处理", "归档过程出现错误。")
            return

        res = "results"
        if project_dir:
            _inc, res = self._resolve_capture_subdirs()
        try:
            from app.services.capture_workflow_service import finalize_supplementary_archive

            finalized = finalize_supplementary_archive(
                result,
                grp,
                project_dir=project_dir or "",
                results_subdir=res,
            )
        except Exception as exc:
            self._workflow_notice(
                "补处理失败",
                f"归档已生成，但成果移动失败：{exc}",
                state="error",
                task_key=task_key,
            )
            ui.warn(self, "补处理", f"归档已生成，但成果移动失败：{exc}")
            return

        # Refresh monitor; refresh results column if the archived specimen is loaded.
        self._refresh_monitor()
        try:
            from app.services.grouping_service import load_grouping
            db = self.ctx.get_db()
            if db is not None and grp.uid and getattr(self, "_current_uid", None) == grp.uid:
                self._refresh_results_column(grp.uid, load_grouping(db, grp.uid))
        except Exception:
            pass
        if grp.uid:
            self._on_organize_finished(grp.uid)

        msg = (
            f"归档完成：{Path(finalized.zip_path).name}\n"
            "ZIP 内为原始 JPG，可直接解压使用。\n"
        )
        if result.delete_jpg:
            msg += "JPG 原片已删除。"
            finish_detail = (
                f"已生成 {Path(finalized.zip_path).name}；"
                f"{result.file_count} 张 JPG 已写入 ZIP 并从待处理区删除。"
            )
        elif result.requested_delete_jpg and not result.delete_jpg:
            msg += f"JPG 保留（{result.deletion_skipped_reason}）。"
            finish_detail = (
                f"已生成 {Path(finalized.zip_path).name}；JPG 已写入 ZIP，"
                f"但删除前校验未通过，文件保留：{result.deletion_skipped_reason}"
            )
        else:
            msg += "JPG 原片已保留。"
            finish_detail = (
                f"已生成 {Path(finalized.zip_path).name}；JPG 已写入 ZIP，"
                "按当前设置保留在磁盘，但不再作为待处理照片显示。"
            )
        self._workflow_notice(
            "补处理完成",
            finish_detail,
            state="success",
            task_key=task_key,
        )
        ui.info(self, "补处理完成", msg)

    def _on_supp_failed(self, message: str) -> None:
        from app.utils import ui
        task_key = str(getattr(self, "_supp_task_key", "") or "")
        self._supp_pending = None
        self._supp_task_key = ""
        self._workflow_notice(
            "补处理失败",
            message or "归档失败。",
            state="error",
            task_key=task_key,
        )
        ui.warn(self, "补处理", f"归档失败: {message}")

    # ── 还原归档 JPG ──────────────────────────────────────────────────────────

    def _on_restore_archive(self, zip_path: str) -> None:
        """Recover the original JPGs from a result ZIP into a user-chosen folder.

        Read-only against the archive + additive (writes new JPGs, deletes
        nothing). Heavy extraction/legacy decode work runs off-thread in RestoreWorker.
        """
        from app.utils import ui
        from PyQt6.QtWidgets import QMessageBox
        from app.workers.restore_worker import RestoreWorker

        if not zip_path or not Path(zip_path).is_file():
            ui.warn(self, "还原原片", "归档文件不存在。")
            return

        out = ui.get_existing_directory(self, "选择还原 JPG 的输出文件夹")
        if not out:
            return

        overwrite = False
        try:
            if any(True for _ in os.scandir(out)):  # 目录非空
                reply = ui.question(
                    self, "目标文件夹非空",
                    "目标文件夹已有文件。同名 JPG 是否覆盖？\n（选「否」则跳过已存在的文件）",
                )
                overwrite = (reply == QMessageBox.StandardButton.Yes)
        except Exception:
            pass

        count = 0
        try:
            import zipfile
            with zipfile.ZipFile(zip_path) as zf:
                count = sum(
                    1 for n in zf.namelist()
                    if Path(n).suffix.lower() in {".jpg", ".jpeg", ".jxl"}
                )
        except Exception:
            pass

        self._restore_worker = RestoreWorker(
            zip_path, out, overwrite=overwrite, file_count=count, parent=self
        )
        self._restore_worker.started.connect(self._on_restore_started)
        self._restore_worker.finished.connect(self._on_restore_finished)
        self._restore_worker.failed.connect(self._on_restore_failed)
        self._restore_worker.start()

    def _on_restore_started(self, count: int) -> None:
        try:
            bar = self.window().statusBar()
            if bar is not None:
                n = f"{count} 张" if count else "原片"
                bar.showMessage(f"正在还原 {n} JPG …", 4000)
        except Exception:
            pass

    def _on_restore_finished(self, result) -> None:
        from app.utils import ui
        if result is None:
            ui.critical(self, "还原原片", "还原过程出现错误。")
            return
        if not getattr(result, "ok", False):
            reason = getattr(result, "reason", "") or "；".join(result.failures[:3])
            ui.critical(self, "还原失败", reason or "还原失败，未输出文件。")
            return

        msg = f"已还原 {result.count} 张 JPG →\n{result.output_dir}"
        if result.skipped:
            msg += f"\n已跳过 {len(result.skipped)} 个已存在文件。"
        if result.failures:
            msg += f"\n{len(result.failures)} 个失败：" + "；".join(result.failures[:3])
        ui.info(self, "还原完成", msg)

    def _on_restore_failed(self, message: str) -> None:
        from app.utils import ui
        ui.critical(self, "还原原片", f"还原失败: {message}")

    def _persist_imported_group_tiff(self, uid: str, group_index: int) -> None:
        """Persist the imported TIFF association from grouping panel to DB.

        Called after grouping_panel._import_existing_tiff_into_group successfully updated the
        in-memory grouping.  Flushes the updated grouping to DB and refreshes
        the results column.

        Oracle: app.js groupingImportTiff() app.js:6057.
        """
        db = self.ctx.get_db()
        if not db or not uid:
            return
        try:
            self._save_timer.stop()
        except Exception:
            pass
        try:
            from app.services.grouping_service import save_grouping
            grouping = getattr(self._grouping, "_grouping", None)
            if grouping:
                save_grouping(db, uid, grouping.groups, clean_phantoms=False)
                self._refresh_results_column(uid, grouping)
                self._refresh_monitor()
        except Exception as exc:
            from app.db.db_manager import is_database_locked
            if is_database_locked(exc):
                QMessageBox.warning(
                    self,
                    "导入 TIFF",
                    "数据库正忙，可能是后台扫描或另一个软件窗口正在写入。\n"
                    "请关闭重复打开的窗口，或稍后重试。\n\n"
                    f"详情：{exc}",
                )
            else:
                QMessageBox.warning(self, "导入 TIFF", f"保存失败：{exc}")
            return
        group = next(
            (
                g
                for g in (grouping.groups if grouping else [])
                if g.group_index == group_index
            ),
            None,
        )
        if group and group.composed_tiff_path:
            self._apply_tiff_filename_recognition(
                group.composed_tiff_path,
                overwrite=True,
            )

    def _on_archive_zip_registered(self, uid: str, group_index: int) -> None:
        """Persist an existing ZIP association selected in the grouping panel."""
        db = self.ctx.get_db()
        if not db or not uid:
            return
        try:
            self._save_timer.stop()
        except Exception:
            pass
        try:
            from app.services.grouping_service import save_grouping
            grouping = getattr(self._grouping, "_grouping", None)
            if grouping:
                save_grouping(db, uid, grouping.groups, clean_phantoms=False)
                self._refresh_results_column(uid, grouping)
                self._refresh_monitor()
        except Exception as exc:
            from app.db.db_manager import is_database_locked
            if is_database_locked(exc):
                QMessageBox.warning(
                    self,
                    "注册 ZIP",
                    "数据库正忙，可能是后台扫描或另一个软件窗口正在写入。\n"
                    "请关闭重复打开的窗口，或稍后重试。\n\n"
                    f"详情：{exc}",
                )
            else:
                QMessageBox.warning(self, "注册 ZIP", f"保存失败：{exc}")

    def _on_link_result_to_right_uid(self, tiff_path: str, zip_path: str) -> None:
        """Move/register an existing result pair under the voucher shown at right."""
        db = self.ctx.get_db()
        if not db:
            self._status_message("请先打开项目")
            return

        target_uid = (
            self._naming.current_uid()
            or self._current_uid
            or self._get_active_uid()
            or ""
        ).strip()
        if not target_uid:
            QMessageBox.warning(
                self,
                "关联成果",
                "右侧编号尚未填写完整，无法关联成果。",
            )
            return

        try:
            from app.services.capture_workflow_service import link_result_pair_to_clean_uid

            linked = link_result_pair_to_clean_uid(db, target_uid, tiff_path, zip_path)
            self._grouping.load_grouping(target_uid, linked.grouping)
            self._refresh_results_column(target_uid, linked.grouping)
            self._refresh_monitor()
            if linked.removed_from:
                self._status_message(f"成果已改挂到右侧编号：{target_uid}")
            else:
                self._status_message(f"成果已关联到右侧编号：{target_uid}")
        except FileNotFoundError as exc:
            QMessageBox.warning(self, "关联成果", str(exc))
        except Exception as exc:
            QMessageBox.warning(self, "关联成果", f"关联失败：{exc}")

    def _on_undo_compose(self, uid: str, group_index: int) -> None:
        """Undo the latest group step.

        Organized group: undo organise first by restoring JPGs from ZIP and
        returning the group to composed/pending-organise state.

        Composed group: 删除这张合成 TIFF + 把关联 JPG 解组放回自由池。
        用户选定语义（拍照区核心 = 中间 JPG ↔ 对应 TIFF 的关联）：TIFF 一旦删除，
        关联失去意义 → 这组 JPG 退出分组、回到监控自由池（未分组，可重新分组/重拍）。
        因删 TIFF 不可恢复 → 删前弹确认框（默认否）。取消则全保留、原样不动。
        """
        db = self.ctx.get_db()
        if not db:
            return
        from app.services.grouping_service import load_grouping, save_grouping
        grouping = load_grouping(db, uid)
        target = next(
            (g for g in grouping.groups
             if g.group_index == group_index and g.composed_tiff_path),
            None,
        )
        if target is None:
            return

        if getattr(target, "archive_zip", None):
            self._on_undo_organise(uid, grouping, target)
            return

        reply = QMessageBox.question(
            self, "删除 TIF / 撤销合成",
            "将删除这张合成 TIFF（不可恢复），并把关联的 JPG 放回自由池"
            "（可重新分组/合成）。确认？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        # ① 删除 TIFF（用户主权；非自动流程，手动确认后删）。
        try:
            if target.composed_tiff_path and os.path.isfile(target.composed_tiff_path):
                os.unlink(target.composed_tiff_path)
        except OSError as exc:
            QMessageBox.warning(self, "撤销合成", f"TIFF 删除失败：{exc}")
            return

        # ② JPG 解关联：移除整组 → 这些 JPG 回到自由池（未分组）。
        grouping.groups = [g for g in grouping.groups if g.group_index != group_index]
        try:
            save_grouping(db, uid, grouping.groups, clean_phantoms=False)
            self._grouping.load_grouping(uid, grouping)
            self._refresh_monitor()
            self._refresh_results_column(uid, grouping)
        except Exception:
            pass

    def _on_undo_organise(self, uid: str, grouping, target) -> None:
        """Undo organise: restore JPGs from ZIP, keep TIFF, clear archive state."""
        db = self.ctx.get_db()
        if not db:
            return
        zip_path = str(getattr(target, "archive_zip", "") or "")
        jpg_paths = [str(p) for p in list(getattr(target, "jpg_paths", []) or [])]
        if not zip_path or not os.path.isfile(zip_path):
            QMessageBox.warning(self, "撤销整理", "找不到该组 ZIP，无法恢复 JPG。")
            return
        if not jpg_paths:
            QMessageBox.warning(self, "撤销整理", "该组没有记录原 JPG 路径，无法自动恢复。")
            return

        reply = QMessageBox.question(
            self,
            "撤销整理",
            "将从 ZIP 还原原始 JPG 到原位置，并把该组退回“已合成、待整理”。\n\n"
            "TIFF 不会删除；项目内 ZIP 会移到 _retired-zip 作为备份。确认？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        from app.services.archive_service import restore_archive_to_original_paths
        from app.services.grouping_service import _utc_now_iso, save_grouping

        result = restore_archive_to_original_paths(
            zip_path,
            jpg_paths,
            overwrite=False,
        )
        if not getattr(result, "ok", False):
            reason = getattr(result, "reason", "") or "；".join(result.failures[:3])
            QMessageBox.warning(self, "撤销整理失败", reason or "部分 JPG 未能恢复。")
            return

        retired_zip = self._retire_zip(zip_path)
        target.archive_zip = None
        target.status = "composed"
        target.updated_at = _utc_now_iso()
        try:
            save_grouping(db, uid, grouping.groups, clean_phantoms=False)
            self._grouping.load_grouping(uid, grouping)
            self._refresh_monitor()
            self._refresh_results_column(uid, grouping)
            restored_count = getattr(result, "count", 0)
            skipped_count = len(getattr(result, "skipped", []) or [])
            detail = f"已恢复 {restored_count} 张 JPG"
            if skipped_count:
                detail += f"，{skipped_count} 张原位置已有文件已跳过"
            if retired_zip:
                detail += f"；ZIP 已移到 {Path(retired_zip).parent.name}/"
            else:
                detail += "；ZIP 已取消登记，磁盘文件保留原处"
            self._status_message(f"撤销整理完成：{detail}")
        except Exception as exc:
            QMessageBox.warning(self, "撤销整理", f"状态更新失败：{exc}")

    def _retire_tiff(self, tiff_path: str) -> None:
        """Move a TIFF to the project's _retired-tiff/ directory."""
        try:
            import shutil
            src = Path(tiff_path)
            if not src.is_file():
                return
            project_dir = self.ctx.current_project_dir
            if not project_dir:
                return
            retired_dir = Path(project_dir) / "_retired-tiff"
            retired_dir.mkdir(exist_ok=True)
            dest = retired_dir / src.name
            # Avoid overwriting — add a numeric suffix if needed
            if dest.exists():
                stem, suffix = src.stem, src.suffix
                i = 1
                while dest.exists():
                    dest = retired_dir / f"{stem}_{i}{suffix}"
                    i += 1
            shutil.move(str(src), str(dest))
        except Exception:
            pass

    def _retire_zip(self, zip_path: str) -> str:
        """Move a project-managed ZIP to _retired-zip; leave external ZIPs alone."""
        try:
            src = Path(zip_path)
            if not src.is_file():
                return ""
            project_dir = getattr(self.ctx, "current_project_dir", None)
            if not project_dir:
                return ""
            project_root = Path(project_dir).resolve()
            try:
                src.resolve().relative_to(project_root)
            except ValueError:
                return ""
            retired_dir = project_root / "_retired-zip"
            retired_dir.mkdir(exist_ok=True)
            dest = retired_dir / src.name
            if dest.exists():
                stem, suffix = src.stem, src.suffix
                i = 1
                while dest.exists():
                    dest = retired_dir / f"{stem}_{i}{suffix}"
                    i += 1
            shutil.move(str(src), str(dest))
            return str(dest)
        except Exception:
            return ""

    def _on_grouping_changed(self) -> None:
        """Debounce-save grouping to DB after edits."""
        self._pending_grouping = None  # will re-read from grouping panel
        self._save_timer.start()

    def _flush_grouping_save(self) -> None:
        """Persist current in-memory grouping to the DB."""
        # The GroupingPanel holds the authoritative in-memory state via its
        # _grouping attribute; reach in safely.
        uid = getattr(self._grouping, "_uid", None)
        grouping = getattr(self._grouping, "_grouping", None)
        db = self.ctx.get_db()
        try:
            from app.services.capture_workflow_service import flush_visible_grouping
            flush_visible_grouping(db, uid, grouping)
        except Exception:
            pass

    # ── Metadata save ─────────────────────────────────────────────────────────

    def _schedule_rail_save(self) -> None:
        """Debounce a right-rail autosave (卡2/卡3 live edits)."""
        if self._current_uid:
            self._rail_save_timer.start()

    def _flush_rail_save(self) -> None:
        if self._current_uid:
            self._on_save_metadata(self._current_uid, reload=False)

    def _on_save_metadata(self, uid: str, reload: bool = True, *, commit: bool = True) -> None:
        """Persist right-rail edits to the DB specimens table.

        Mirrors the web whole-`sp` persist (scheduleRightPanelPersist): one save
        gathers every right-rail field across the three cards —
        卡1 命名(日期/保存方式/拍照备注), 卡2 分类(拉丁/中名/备注), 卡3 元数据
        (采集人/拍摄人/鉴定人/经纬度/地理区).  ``reload=False`` for autosave so the
        focused input does not lose its cursor mid-edit.
        """
        db = self.ctx.get_db()
        if not db:
            return
        if reload:
            self._flush_grouping_save()
        panel = self._metadata
        naming = self._naming
        fields: dict[str, str] = {
            # 卡3 元数据
            "collector":       panel._collector.text(),
            "photographer":    panel._photographer.text(),
            "identifier":      panel._identifier.text(),
            "geo_area":        panel._geo_area.text(),
            # 卡1 命名（日期 / 保存方式 / 拍照备注）
            "collection_date": naming._collection_date.text(),
            "photo_date":      naming._photo_date.text(),
            "storage":         naming._storage.text(),
            "photo_notes":     naming._photo_notes.toPlainText(),
        }
        # 卡2 分类字段（拉丁 + 中名 + 备注）来自独立的「分类标签」卡片
        fields.update(self._taxon_card.field_values())
        lon_str = panel._lon.text().strip()
        lat_str = panel._lat.text().strip()

        set_clauses = ", ".join(f"{k} = ?" for k in fields)
        values = list(fields.values())

        try:
            lon_val: Optional[float] = float(lon_str) if lon_str else None
        except ValueError:
            lon_val = None
        try:
            lat_val: Optional[float] = float(lat_str) if lat_str else None
        except ValueError:
            lat_val = None

        try:
            db.execute(
                f"UPDATE specimens SET {set_clauses}, lon = ?, lat = ? WHERE uid = ?",
                values + [lon_val, lat_val, uid],
            )
            try:
                extra = naming.naming_extra_field_values()
            except Exception:
                extra = {}
            if extra:
                row = db.execute(
                    "SELECT raw_json FROM specimens WHERE uid = ?",
                    (uid,),
                ).fetchone()
                try:
                    raw = json.loads(row["raw_json"] or "{}") if row else {}
                    if not isinstance(raw, dict):
                        raw = {}
                except Exception:
                    raw = {}
                raw.update(extra)
                db.execute(
                    "UPDATE specimens SET raw_json = ? WHERE uid = ?",
                    (json.dumps(raw, ensure_ascii=False), uid),
                )
            if commit:
                db.commit()
        except Exception:
            pass

        if reload:
            self._load_specimen(uid)

    # ── WoRMS fill hook ───────────────────────────────────────────────────────

    def worms_fill_specimen(self, rec: dict) -> str:
        """Fill current specimen with WoRMS Latin taxonomy fields.

        Mirrors web ``wormsFillToSpecimen``: Latin class/order/family/genus/
        species are updated, ``taxonomyConfirmed`` is reset in raw_json, and
        Chinese fields are left untouched.
        """
        uid = self._current_uid or self._get_active_uid()
        if not uid:
            raise RuntimeError("需先在工作区选择或激活标本")
        db = self.ctx.get_db()
        if not db:
            raise RuntimeError("请先打开项目工作区")

        row = db.execute("SELECT * FROM specimens WHERE uid = ?", (uid,)).fetchone()
        if row is None:
            raise RuntimeError(f"当前标本不存在: {uid}")

        try:
            raw = json.loads(row["raw_json"]) if row["raw_json"] else {}
            if not isinstance(raw, dict):
                raw = {}
        except Exception:
            raw = {}

        from app.services.worms_service import WormsService
        raw = WormsService.merge_worms_into_record(raw, rec)
        if rec.get("class"):
            raw["taxonGroup"] = rec["class"]
        if rec.get("order"):
            raw["order"] = rec["order"]
        if rec.get("family"):
            raw["family"] = rec["family"]
        if rec.get("genus"):
            raw["genus"] = rec["genus"]
        if rec.get("scientificname"):
            raw["scientificName"] = rec["scientificname"]
        raw["taxonomyConfirmed"] = False

        db.execute(
            """
            UPDATE specimens
            SET taxon_group = ?, order_name = ?, family = ?, genus = ?,
                scientific_name = ?, raw_json = ?
            WHERE uid = ?
            """,
            (
                rec.get("class") or row["taxon_group"],
                rec.get("order") or row["order_name"],
                rec.get("family") or row["family"],
                rec.get("genus") or row["genus"],
                rec.get("scientificname") or row["scientific_name"],
                json.dumps(raw, ensure_ascii=False),
                uid,
            ),
        )
        db.commit()
        self._load_specimen(uid)
        return uid

    # ── Collab photo-index hooks ──────────────────────────────────────────────

    def _on_helicon_finished(self, uid: str, *, select_uid: bool = True) -> None:
        """Broadcast tiff photo-index to collab peers (oracle: collabPostPhotoIndex)."""
        try:
            self._sidebar.refresh()
            if select_uid:
                self._sidebar.select_uid(uid)
        except Exception:
            pass
        svc = getattr(self.ctx, "collab_service", None)
        if svc is not None:
            try:
                svc.post_photo_index(uid, "tiff")
            except Exception:
                pass

    def _on_organize_finished(self, uid: str, *, select_uid: bool = True) -> None:
        """Broadcast zip photo-index to collab peers (oracle: collabPostPhotoIndex)."""
        try:
            self._sidebar.refresh()
            if select_uid:
                self._sidebar.select_uid(uid)
        except Exception:
            pass
        svc = getattr(self.ctx, "collab_service", None)
        if svc is not None:
            try:
                svc.post_photo_index(uid, "zip")
            except Exception:
                pass

    # ── Results column ────────────────────────────────────────────────────────

    def _result_infos_from_grouping(self, grouping) -> tuple[list[dict], list[dict]]:
        """Return display-ready TIFF/ZIP info lists for one specimen grouping."""
        from app.services.capture_workflow_service import result_infos_from_grouping
        return result_infos_from_grouping(grouping)

    def _refresh_results_column(self, uid: str, grouping=None) -> None:
        """Populate the ② 成果内容 column from one specimen's grouping data."""
        composed_tiffs, archive_zips = self._result_infos_from_grouping(grouping)
        self._results.load_uid(uid, composed_tiffs, archive_zips)

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _get_active_uid(self) -> Optional[str]:
        """Return the currently active specimen UID from the tasks table."""
        db = self.ctx.get_db()
        if not db:
            return None
        try:
            from app.services.activation_service import get_active_uid
            return get_active_uid(db)
        except Exception:
            return None

    def _get_attributed_jpg_paths(self, uid: str) -> list[str]:
        """Return paths of all JPGs currently attributed to *uid* in the monitor.

        Used as implicit-batch fallback when a group has < 2 JPGs.
        Mirrors web composeImplicitActiveBatch() app.js:5660–5706.
        """
        project_dir = self.ctx.current_project_dir
        db = self.ctx.get_db()
        if not project_dir or not db:
            return []
        try:
            from app.services.capture_workflow_service import attributed_jpg_paths
            inc, res = self._resolve_capture_subdirs()
            return attributed_jpg_paths(
                project_dir,
                db,
                uid,
                last_scan=self._last_scan_result,
                incoming_subdir=inc,
                results_subdir=res,
            )
        except Exception:
            return []

    def _build_implicit_group(
        self,
        uid: str,
        jpg_paths: Optional[list[str]] = None,
        *,
        output_name: Optional[str] = None,
    ) -> Optional[int]:
        """把明确来源的 JPG 建成一个待合成组。

        jpg_paths 不为空时，只消费这些手选 JPG；未传 jpg_paths 时才回退到
        uid 下已归属且未占用的 JPG。返回新组 group_index；可用 JPG <2 张则
        返回 None。占用 = 已在任何分组里（草稿或已合成）。
        """
        db = self.ctx.get_db()
        if not db or not uid:
            return None
        from app.services.compose_workflow_service import create_implicit_group

        candidates = (
            list(jpg_paths)
            if jpg_paths is not None
            else self._get_attributed_jpg_paths(uid)
        )
        result = create_implicit_group(
            db,
            uid,
            candidates,
            output_name=output_name,
        )
        if result.group_index is None or result.grouping is None:
            return None
        self._grouping.load_grouping(uid, result.grouping)
        return result.group_index

    def _unoccupied_jpg_paths(self, uid: str, jpg_paths: list[str]) -> list[str]:
        """Filter out JPGs already present in any group for this UID."""
        db = self.ctx.get_db()
        if not db or not uid:
            return []
        try:
            from app.services.compose_workflow_service import unoccupied_jpg_paths
            return unoccupied_jpg_paths(db, uid, jpg_paths)
        except Exception:
            return list(jpg_paths)

    def _default_selected_compose_uid(self) -> str:
        candidates = []
        try:
            candidates.append(self._naming.current_uid())
        except Exception:
            pass
        candidates.extend([self._current_uid, self._get_active_uid()])
        for uid in candidates:
            text = str(uid or "").strip()
            if text:
                return text
        return ""

    def _prompt_selected_compose_target(
        self,
        jpg_count: int,
        *,
        organise: bool,
        default_uid: str = "",
    ) -> Optional[_SelectedComposeTarget]:
        """Ask how selected JPGs should be named/owned."""
        from app.services.grouping_service import ADHOC_GROUPING_UID

        default_uid = (default_uid or self._default_selected_compose_uid()).strip()
        dlg = QDialog(self)
        dlg.setWindowTitle("合成输出")
        dlg.setMinimumWidth(520)

        root = QVBoxLayout(dlg)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(10)

        action = "合成+整理" if organise else "合成"
        title = QLabel(f"已选 {jpg_count} 张 JPG，准备{action}")
        title.setObjectName("Section")
        root.addWidget(title)

        uid_radio = QRadioButton("归属到编号，按编号自动命名")
        uid_edit = QLineEdit(default_uid)
        uid_edit.setPlaceholderText("输入或粘贴编号；成果将自动使用下一个序号")
        root.addWidget(uid_radio)
        root.addWidget(uid_edit)

        name_radio = QRadioButton("自由输出名，TIF 与 ZIP 使用同名")
        name_edit = QLineEdit()
        name_edit.setPlaceholderText("例如 SC002-结果1；无需写 .tif/.zip")
        root.addWidget(name_radio)
        root.addWidget(name_edit)

        hint = QLabel("选择编号时不能手填成果名；软件会按该编号已有成果自动顺延 -1、-2、-3。")
        hint.setObjectName("MutedSmall")
        hint.setWordWrap(True)
        root.addWidget(hint)

        if default_uid:
            uid_radio.setChecked(True)
        else:
            name_radio.setChecked(True)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("继续")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        root.addWidget(buttons)

        def _accept() -> None:
            if uid_radio.isChecked():
                uid = uid_edit.text().strip()
                if not uid:
                    QMessageBox.warning(dlg, "合成输出", "请输入要归属的编号。")
                    return
                dlg.done(QDialog.DialogCode.Accepted)
                return
            name = Path(name_edit.text().strip()).stem
            if not name:
                QMessageBox.warning(dlg, "合成输出", "请输入输出名称。")
                return
            dlg.done(QDialog.DialogCode.Accepted)

        buttons.accepted.connect(_accept)
        buttons.rejected.connect(dlg.reject)

        if dlg.exec() != QDialog.DialogCode.Accepted:
            return None
        if uid_radio.isChecked():
            uid = uid_edit.text().strip()
            return _SelectedComposeTarget(
                uid=uid,
                output_name=None,
                assign_to_uid=True,
            )
        return _SelectedComposeTarget(
            uid=ADHOC_GROUPING_UID,
            output_name=Path(name_edit.text().strip()).stem,
            assign_to_uid=False,
        )

    def _assign_selected_jpgs_to_uid(self, uid: str, jpg_paths: list[str]) -> None:
        project_dir = self.ctx.current_project_dir
        db = self.ctx.get_db()
        if not project_dir or not db or not uid or not jpg_paths:
            return
        try:
            from app.services.compose_workflow_service import assign_selected_jpgs_to_uid
            assign_selected_jpgs_to_uid(project_dir, db, uid, jpg_paths)
        except Exception:
            import logging
            logging.getLogger(__name__).warning(
                "JPG 归属写入失败 (uid=%s)", uid, exc_info=True,
            )

    def _resolve_implicit_compose_target(
        self,
        selected_jpgs: list[str],
        active_uid: Optional[str],
        *,
        organise: bool,
    ) -> Optional[_SelectedComposeTarget]:
        """Resolve the main compose button's target using the two real modes.

        Decision table:
        - selected JPGs + active UID: use active UID, auto-name, no prompt.
        - selected JPGs + no active UID: prompt for target UID or free output name.
        - no selected JPGs + active UID + auto archive: use active UID's
          unoccupied attributed JPGs.
        - no selected JPGs otherwise: nothing to compose from the main toolbar.

        Existing JPG attribution only pre-fills the prompt when no UID is active.
        It never overrides an active UID and never silently decides a filename.
        """
        owners = []
        if selected_jpgs and not active_uid:
            try:
                owners = self._monitor.selected_jpg_owner_uids()
            except Exception:
                owners = []

        from app.services.compose_workflow_service import resolve_implicit_compose_target

        target = resolve_implicit_compose_target(
            selected_jpgs,
            active_uid,
            auto_archive_enabled=self._auto_archive_enabled(),
            selected_owner_uids=owners,
            prompt_target=lambda jpg_count, default_uid: (
                self._prompt_selected_compose_target(
                    jpg_count,
                    organise=organise,
                    default_uid=default_uid,
                )
            ),
        )
        if target is None and not selected_jpgs:
            self._status_message("请先选中要合成的 JPG；或激活编号并开启自动归档。")
        return target

    def _auto_archive_enabled(self) -> bool:
        """Return true for the user's explicit incoming auto-archive mode."""
        try:
            if bool(getattr(self.ctx.settings, "auto_organize_after_compose", False)):
                return True
        except Exception:
            pass
        try:
            return bool(self._monitor.auto_compress_enabled())
        except Exception:
            return False

    def _on_compose_implicit(self, organise: bool = False) -> None:
        """主界面[合成]：优先处理手选 JPG；自动归档打开时可处理激活编号 JPG。

        有手选 JPG 时，手选优先。没有手选 JPG 时，只有“激活编号 + 自动归档”
        这个显式快速拍摄模式才会取该编号下未占用 JPG。
        """
        selected = []
        try:
            selected = self._monitor.selected_jpg_paths()
        except Exception:
            selected = []
        active_uid = self._get_active_uid()
        target = self._resolve_implicit_compose_target(
            selected,
            active_uid,
            organise=organise,
        )
        if target is None:
            return
        uid = target.uid
        if selected and target.assign_to_uid:
            self._assign_selected_jpgs_to_uid(uid, selected)
        idx = self._build_implicit_group(
            uid,
            selected or None,
            output_name=target.output_name,
        )
        if idx is None:
            self._status_message("没有可合成的未占用 JPG（至少 2 张）")
            return
        silent = bool(getattr(self.ctx.settings, "silent_compose", False))
        task_key = f"implicit-compose:{uid}:{idx}:{'organise' if organise else 'compose'}"
        if organise:
            jpg_count = len(selected) if selected else len(self._get_attributed_jpg_paths(uid))
            self._workflow_notice(
                "合成+整理：正在合成 TIFF",
                f"已接收 {jpg_count} 张 JPG。合成完成后会自动整理、生成 ZIP，并从待处理队列移出这些 JPG。",
                state="busy",
                force_show=True,
                task_key=task_key,
            )
            self._status_message("合成+整理已转入后台，可继续拍摄或新增编号。")
            self._compose_group_headless(
                uid,
                idx,
                lambda ok: self._implicit_compose_done(ok, uid, idx, organise, task_key),
                background=True,
                show_progress_dialog=False,
                workflow_task_key=task_key,
            )
            return
        if silent:
            jpg_count = len(selected) if selected else len(self._get_attributed_jpg_paths(uid))
            self._workflow_notice(
                "合成：正在生成 TIFF",
                f"已接收 {jpg_count} 张 JPG。合成完成后 TIFF 会留在待处理区，JPG 仍需整理归档。",
                state="busy",
                force_show=True,
                task_key=task_key,
            )
            self._compose_group_headless(
                uid,
                idx,
                lambda ok: self._implicit_compose_done(ok, uid, idx, organise, task_key),
                background=False,
                workflow_task_key=task_key,
            )
            return
        self._on_compose_requested(uid, idx)

    def _implicit_compose_done(
        self,
        success: bool,
        uid: str,
        group_index: int,
        organise: bool,
        task_key: str = "",
    ) -> None:
        if not success:
            if organise:
                self._workflow_notice(
                    "合成+整理失败",
                    "合成阶段没有生成 TIFF，因此整理没有启动。",
                    state="error",
                    task_key=task_key,
                )
            else:
                self._workflow_notice(
                    "合成失败",
                    "合成阶段没有生成 TIFF。",
                    state="error",
                    task_key=task_key,
                )
            self._status_message("合成失败或未生成成果")
            return
        if organise:
            self._workflow_notice(
                "合成+整理：正在整理",
                "TIFF 已生成，正在打包 JPG 原片并登记 ZIP。",
                state="busy",
                task_key=task_key,
            )
            self._status_message("合成完成，正在整理并打包 JPG…")
            def _done(ok: bool) -> None:
                if ok:
                    self._status_message("合成+整理完成")
                else:
                    reason = str(
                        getattr(self, "_last_organise_failure_reason", "") or ""
                    ).strip()
                    detail = reason or "整理或归档没有完成。"
                    self._workflow_notice(
                        "合成+整理失败",
                        detail,
                        state="error",
                        task_key=task_key,
                    )
                    self._status_message("合成完成，但整理失败或未完成")

            started = self._on_organise_requested(
                uid,
                group_index,
                silent_batch=True,
                workflow_task_key=task_key,
                workflow_label="合成+整理",
                on_complete=_done,
            )
            if not started:
                reason = str(getattr(self, "_last_organise_failure_reason", "") or "").strip()
                suffix = f"：{reason}" if reason else ""
                self._workflow_notice(
                    "合成+整理失败",
                    reason or "整理没有启动。",
                    state="error",
                    task_key=task_key,
                )
                self._status_message(f"合成完成，但整理未启动{suffix}")
        else:
            detail = "TIFF 已生成在待处理区；JPG 尚未整理归档。"
            try:
                grouping = self._get_grouping_for_uid(uid)
                group = next(
                    (g for g in grouping.groups if g.group_index == group_index),
                    None,
                )
                tiff_path = getattr(group, "composed_tiff_path", "") if group else ""
                if tiff_path:
                    detail = f"TIFF 已生成：{Path(tiff_path).name}。JPG 尚未整理归档。"
            except Exception:
                pass
            self._workflow_notice(
                "合成完成",
                detail,
                state="success",
                task_key=task_key,
            )
            self._status_message("合成完成，成果已生成在 incoming")

    def _show_compose_preview(self, jpg_paths: list[str]) -> Optional[list[str]]:
        """Pre-compose JPG checklist dialog.

        Mirrors web renderComposePreviewModal() app.js:6597 — simplified Qt
        version: shows all JPG filenames as a checkable list so the user can
        confirm or deselect files before Helicon runs.

        Returns:
            list[str]: selected (checked) JPG paths if user confirms.
            None:      if the user cancelled.
        """
        from PyQt6.QtWidgets import (
            QCheckBox,
            QDialog,
            QDialogButtonBox,
            QScrollArea,
        )

        dlg = QDialog(self)
        dlg.setWindowTitle("合成预览 — 确认原片")
        dlg.setMinimumWidth(460)

        root_lay = QVBoxLayout(dlg)
        root_lay.setContentsMargins(16, 16, 16, 16)
        root_lay.setSpacing(10)

        info = QLabel(
            f"即将合成 {len(jpg_paths)} 张 JPG。\n取消勾选可从本次合成中排除："
        )
        info.setObjectName("Muted")
        info.setWordWrap(True)
        root_lay.addWidget(info)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setMaximumHeight(260)
        inner = QWidget()
        inner_lay = QVBoxLayout(inner)
        inner_lay.setContentsMargins(4, 4, 4, 4)
        inner_lay.setSpacing(4)

        checkboxes: list[tuple[QCheckBox, str]] = []
        for p in jpg_paths:
            cb = QCheckBox(Path(p).name)
            cb.setChecked(True)
            cb.setToolTip(p)
            inner_lay.addWidget(cb)
            checkboxes.append((cb, p))
        inner_lay.addStretch()
        scroll.setWidget(inner)
        root_lay.addWidget(scroll)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        ok_btn = btns.button(QDialogButtonBox.StandardButton.Ok)
        if ok_btn:
            ok_btn.setText("✓ 开始合成")
        cancel_btn = btns.button(QDialogButtonBox.StandardButton.Cancel)
        if cancel_btn:
            cancel_btn.setText("取消")
        btns.accepted.connect(dlg.accept)
        btns.rejected.connect(dlg.reject)
        root_lay.addWidget(btns)

        # Center on parent screen (dual-monitor safe)
        try:
            from app.utils.ui import center_on
            center_on(dlg, self)
        except Exception:
            pass

        if dlg.exec() != QDialog.DialogCode.Accepted:
            return None

        return [path for cb, path in checkboxes if cb.isChecked()]

    # ── Collaboration file sync ──────────────────────────────────────────────

    def _collab_file_sync_peers(self) -> list:
        svc = getattr(self.ctx, "collab_service", None)
        if svc is None or not svc.is_running() or not svc.group_code:
            return []
        local_project_id = str(getattr(svc, "project_id", "") or "")
        if not local_project_id:
            return []
        return [
            peer for peer in svc.peers()
            if getattr(peer, "group_code", "") == svc.group_code
            and str(getattr(peer, "project_id", "") or "") == local_project_id
        ]

    def _on_sync_selected_uid(self, mode: str = "smart") -> None:
        uid = self._sidebar.current_uid() or self._current_uid
        if not uid:
            self._status_message("请先在左侧选择一个编号。")
            return
        self._start_collab_file_sync([uid], title=f"同步编号 {uid}", mode=mode)

    def _on_sync_project_files(self, mode: str = "smart") -> None:
        self._start_collab_file_sync(None, title="同步整个项目", mode=mode)

    def _start_collab_file_sync(self, uids: Optional[list[str]], *, title: str,
                                mode: str = "smart") -> None:
        project_dir = self.ctx.current_project_dir
        if not project_dir:
            self._status_message("请先打开项目。")
            return
        svc = getattr(self.ctx, "collab_service", None)
        if svc is None or not svc.is_running() or not svc.group_code:
            QMessageBox.information(self, "照片同步", "请先启用局域网协作并设置协作组码。")
            return
        project_id = str(getattr(svc, "project_id", "") or "")
        if not project_id:
            QMessageBox.information(
                self,
                "照片同步",
                "当前项目还没有可用于照片同步的项目同步码。请重新打开项目后再同步照片。",
            )
            return
        same_group_peers = [
            peer for peer in svc.peers()
            if getattr(peer, "group_code", "") == svc.group_code
        ]
        peers = self._collab_file_sync_peers()
        if not peers:
            if same_group_peers:
                QMessageBox.information(
                    self,
                    "照片同步",
                    "当前在线设备没有使用同一个项目同步码。\n\n"
                    "任务状态可以跨项目查看；照片/TIF/ZIP 只允许同项目同步码设备同步。\n"
                    "请到协作中心点击“绑定同一项目”，由确认是同一项目的一台电脑分享项目码。",
                )
            else:
                QMessageBox.information(self, "照片同步", "没有同组在线设备，无法同步照片。")
            return
        current_worker = getattr(self, "_collab_file_sync_worker", None)
        if current_worker is not None and current_worker.isRunning():
            self._status_message("照片同步正在进行中。")
            return
        if mode == "overwrite":
            ret = QMessageBox.warning(
                self,
                "强制覆盖本机文件",
                "强制覆盖会用队友设备上的同名文件替换本机文件。\n\n"
                "本机旧文件会先备份到 _data/sync-conflicts/，不会直接删除。\n"
                "确定继续吗？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if ret != QMessageBox.StandardButton.Yes:
                return

        from app.workers.collab_file_sync_worker import CollabFileSyncWorker

        progress = None

        worker = CollabFileSyncWorker(
            project_dir=project_dir,
            peers=peers,
            group_code=svc.group_code,
            project_id=project_id,
            uids=uids,
            mode=mode,
            max_workers=4,
            parent=self,
        )
        self._collab_file_sync_worker = worker
        self._collab_file_sync_progress = progress
        task_key = f"collab-sync:{title}:{id(worker)}"
        self._workflow_notice(
            f"{title}：准备同步",
            f"正在连接 {len(peers)} 台同项目设备；同步在后台运行。",
            state="busy",
            force_show=True,
            task_key=task_key,
        )

        def _on_progress(current: int, total: int, rel: str) -> None:
            if progress is not None:
                progress.setMaximum(max(1, total))
                progress.setValue(min(current, max(1, total)))
                progress.setLabelText(f"正在同步 {current}/{total}\n{rel}")
            self._workflow_notice(
                f"{title}：正在同步",
                f"正在同步 {current}/{total}：{rel}",
                state="busy",
                task_key=task_key,
            )

        def _finish(summary) -> None:
            if progress is not None:
                progress.close()
            self._collab_file_sync_worker = None
            self._collab_file_sync_progress = None
            try:
                self._refresh_monitor()
                self._sidebar.refresh()
                if self._current_uid:
                    self._on_show_current_results()
            except Exception:
                pass
            msg = (
                f"照片同步完成：下载 {summary.downloaded}，跳过 {summary.skipped}，"
                f"冲突 {summary.conflicts}，失败 {summary.failed}"
                f"，同步码不同跳过 {getattr(summary, 'incompatible_peers', 0)}。"
            )
            state = "error" if summary.conflicts or summary.failed else "success"
            self._workflow_notice(
                f"{title}完成",
                msg,
                state=state,
                task_key=task_key,
            )
            self._status_message(msg)
            if summary.conflicts or summary.failed:
                detail = ""
                if summary.conflict_paths:
                    detail += "冲突文件：\n" + "\n".join(summary.conflict_paths[:12])
                    if len(summary.conflict_paths) > 12:
                        detail += f"\n... 还有 {len(summary.conflict_paths) - 12} 个"
                if summary.failed_paths:
                    if detail:
                        detail += "\n\n"
                    detail += "失败文件：\n" + "\n".join(summary.failed_paths[:12])
                    if len(summary.failed_paths) > 12:
                        detail += f"\n... 还有 {len(summary.failed_paths) - 12} 个"
                QMessageBox.warning(self, "照片同步完成但有问题", detail or msg)

        def _fail(message: str) -> None:
            if progress is not None:
                progress.close()
            self._collab_file_sync_worker = None
            self._collab_file_sync_progress = None
            self._workflow_notice(
                f"{title}失败",
                message or "未知错误",
                state="error",
                task_key=task_key,
            )
            QMessageBox.warning(self, "照片同步失败", message or "未知错误")

        worker.progress.connect(_on_progress)
        worker.finished_summary.connect(_finish)
        worker.failed.connect(_fail)
        worker.finished.connect(worker.deleteLater)
        worker.start()
        if progress is not None:
            progress.show()

    def _collab_operator(self) -> str | None:
        """Current operator name (for task assignee), read safely from settings."""
        try:
            raw = self.ctx.settings._qs.value("user/current_user", "")
            name = str(raw).strip()
            return name or None
        except Exception:
            return None

    def _on_open_collab_panel(self) -> None:
        """Show collab panel drawer positioned at the right edge."""
        self._collab_panel.refresh()
        try:
            win_rect = self.rect()
            self._collab_scrim.setGeometry(win_rect)
            self._collab_scrim.show()
            self._collab_scrim.raise_()
            p = self._collab_panel
            p.setGeometry(
                win_rect.right() - p.width(), 0,
                p.width(), win_rect.height()
            )
            p.show()
            p.raise_()
        except Exception:
            self._collab_panel.show()

    def _close_collab_panel(self) -> None:
        """Dismiss the collab panel and its backdrop scrim."""
        self._collab_scrim.hide()
        self._collab_panel._on_close()

    def _refresh_collab_card(self) -> None:
        """Refresh the right-rail collab card when tasks change."""
        if self._current_uid:
            self._collab_card.load_specimen(self._current_uid)

    def _on_open_settings(self) -> None:
        """Show project settings drawer positioned at the right edge."""
        self._settings_drawer.refresh()
        try:
            win_rect = self.rect()
            # Backdrop scrim covers the whole view, drawer sits on top of it.
            self._settings_scrim.setGeometry(win_rect)
            self._settings_scrim.show()
            self._settings_scrim.raise_()
            dw = self._settings_drawer
            dw.setGeometry(
                win_rect.right() - dw.width(), 0,
                dw.width(), win_rect.height()
            )
            dw.show()
            dw.raise_()
        except Exception:
            self._settings_drawer.show()

    def _close_settings(self) -> None:
        """Dismiss the settings drawer and its backdrop scrim."""
        self._settings_scrim.hide()
        self._settings_drawer._on_close()

    def _show_no_project(self) -> None:
        self._sidebar.refresh()  # clears list
        self._last_scan_result = None
        self._monitor.clear()
        self._grouping.clear()
        self._results.clear()
        self._metadata.clear()
        self._taxon_card.clear()
        self._naming.load_specimen({})  # 清命名卡残留字段
        self._current_uid = None
        self._refresh_header()
        self._refresh_workflow_dashboard()
        self._no_project_banner.show()
