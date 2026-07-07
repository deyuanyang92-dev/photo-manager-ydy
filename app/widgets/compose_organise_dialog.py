"""compose_organise_dialog.py — 合成+整理进度卡片（可转后台）。

从 app/views/workbench_view.py 原样拆出（行为逐字节一致）；
workbench_view 仍以原名 `_ComposeOrganiseProgressDialog` re-export。
"""
from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from app.config import icons
from app.config.theme import TOKENS


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
        # Pre-created overlay widgets must start hidden; otherwise Qt shows the
        # idle "waiting" card together with the workbench parent.
        super().hide()

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
