"""workbench_notice_panel.py — 工作台右下角任务进度面板。

从 app/views/workbench_view.py 原样拆出（行为逐字节一致）；
workbench_view 仍以原名 `_WorkflowNoticePanel` re-export。
"""
from __future__ import annotations

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout

from app.config.theme import TOKENS


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
