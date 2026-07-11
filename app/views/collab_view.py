"""collab_view.py — Collaboration module view (CollabView).

Displays:
  • Online devices panel   — 🟢 N 台在线 / ⚪ 未发现其他设备
  • Task list              — per-UID status + assignee, coloured by state
  • Conflict banner        — shown when a 409 is detected
  • Manual connection row  — IP + port input + Connect button (mDNS fallback)
  • Debug drawer           — local address, peers with latency, sync log

Contract (BaseView):
  view_id   = "collab"
  nav_title = "协作"
  nav_icon  = "👥"

  on_activate() — refreshes device list and task table from the in-memory store.
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import Qt, QTimer, pyqtSignal, pyqtSlot
from PyQt6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMenu,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.config import icons
from app.services.collab_offline_queue import StatusRetryQueue
from app.services.collab_status import build_collab_status, peer_display_name
from app.views.base_view import BaseView
from app.widgets.collab_aux_dialogs import (
    CollabManualConnectDialog,
    CollabShareScopeDialog,
)

try:
    from app.config.effects import apply_card_shadow as _apply_card_shadow
except ImportError:  # pragma: no cover
    def _apply_card_shadow(widget, **_kw):  # type: ignore[misc]
        return None

if False:  # TYPE_CHECKING
    from app.app_context import AppContext
    from app.services.collab_service import CollabService, PeerInfo, TaskRecord


# ── Colour palette for task status ───────────────────────────────────────────

_STATUS_COLOURS: dict[str, str] = {
    "created":    "#6eb5ff",
    "assigned":   "#a8d8ea",
    "shooting":   "#f6d365",
    "shot_done":  "#b8f0b8",
    "organizing": "#ffd180",
    "done":       "#69f0ae",
    "void":       "#9e9e9e",
    "conflict":   "#ff5252",
}

_STATUS_LABEL: dict[str, str] = {
    "created":    "已创建",
    "assigned":   "已指派",
    "shooting":   "拍摄中",
    "shot_done":  "拍摄完成",
    "organizing": "整理中",
    "done":       "完成",
    "void":       "作废",
    "conflict":   "冲突",
}

_TASK_COL_UID = 0
_TASK_COL_PROJECT = 1
_TASK_COL_STATUS = 2
_TASK_COL_ASSIGNEE = 3
_TASK_COL_UPDATED = 4


def _elevate_card(frame: QFrame) -> None:
    """Soft drop shadow so setup/activity panels read as cards."""
    _apply_card_shadow(frame, blur=20, y=3, alpha=32)


def _collab_form_label(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("CollabFormLabel")
    label.setAlignment(
        Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
    )
    return label


def _style_collab_field(
    widget: QLineEdit,
    *,
    readonly: bool = False,
    placeholder: str = "",
    code: bool = False,
) -> QLineEdit:
    if code:
        obj = "CollabFieldCodeReadOnly" if readonly else "CollabFieldCode"
    else:
        obj = "CollabFieldReadOnly" if readonly else "CollabFieldInput"
    widget.setObjectName(obj)
    widget.setMinimumHeight(32)
    if placeholder:
        widget.setPlaceholderText(placeholder)
    if readonly:
        widget.setReadOnly(True)
    return widget


def _collab_form_block(
    parent: QVBoxLayout | QHBoxLayout,
    label: str,
    field: QWidget,
    *actions: QPushButton,
    action_below: bool = False,
    framed: bool = False,
) -> None:
    """One labeled field block — avoids grid squeeze on narrow windows."""
    block = QFrame() if framed else QWidget()
    if framed:
        block.setObjectName("CollabFormSection")
    block_layout = QVBoxLayout(block)
    if framed:
        block_layout.setContentsMargins(14, 12, 14, 12)
    else:
        block_layout.setContentsMargins(0, 0, 0, 0)
    block_layout.setSpacing(12 if framed else 8)

    lab = QLabel(label)
    lab.setObjectName("CollabFormLabelInline")
    block_layout.addWidget(lab)

    if actions and action_below:
        block_layout.addWidget(field)
        btn_row = QHBoxLayout()
        btn_row.setContentsMargins(0, 8, 0, 0)
        btn_row.setSpacing(12)
        for action in actions:
            action.setMinimumHeight(34)
            action.setMinimumWidth(100)
            btn_row.addWidget(action)
        btn_row.addStretch()
        block_layout.addLayout(btn_row)
    elif actions:
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(10)
        field.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        row.addWidget(field, 1)
        for action in actions:
            action.setMinimumHeight(34)
            action.setMinimumWidth(100)
            row.addWidget(action, 0)
        block_layout.addLayout(row)
    else:
        block_layout.addWidget(field)
    parent.addWidget(block)


def _collab_compact_copy_row(
    parent: QVBoxLayout,
    label: str,
    field: QLineEdit,
    action: QPushButton,
) -> None:
    row = QHBoxLayout()
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(10)
    lab = QLabel(label)
    lab.setObjectName("CollabCopyRowLabel")
    lab.setFixedWidth(52)
    lab.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    field.setMinimumHeight(32)
    action.setObjectName("Outline")
    action.setMinimumHeight(32)
    action.setMinimumWidth(96)
    row.addWidget(lab)
    row.addWidget(field, 1)
    row.addWidget(action)
    parent.addLayout(row)


def _collab_form_row(
    grid: QGridLayout,
    row: int,
    label: str,
    field: QWidget,
    *actions: QPushButton,
) -> None:
    """Legacy grid helper — kept for callers that still use QGridLayout."""
    grid.addWidget(_collab_form_label(label), row, 0, Qt.AlignmentFlag.AlignTop)
    if actions:
        cell = QWidget()
        cell_layout = QHBoxLayout(cell)
        cell_layout.setContentsMargins(0, 0, 0, 0)
        cell_layout.setSpacing(10)
        cell_layout.addWidget(field, 1)
        for action in actions:
            action.setMinimumHeight(32)
            action.setMinimumWidth(88)
            cell_layout.addWidget(action)
        grid.addWidget(cell, row, 1)
    else:
        grid.addWidget(field, row, 1)


def _collab_detail_shell(parent: QWidget | None = None) -> QWidget:
    """Full-width content host inside the method detail pane."""
    shell = QWidget(parent)
    shell.setObjectName("CollabDetailShell")
    shell.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
    return shell


def _collab_step_panel(
    title: str,
    detail: str = "",
    *,
    entry: bool = False,
    state: QLabel | None = None,
) -> tuple[QFrame, QVBoxLayout, QHBoxLayout]:
    """Shared step card — matches CollabMethodTab / CollabStepPanel language."""
    panel = QFrame()
    panel.setObjectName("CollabStepPanel")
    if entry:
        panel.setProperty("role", "entry")
    layout = QVBoxLayout(panel)
    layout.setContentsMargins(14, 12, 14, 12)
    layout.setSpacing(8)

    title_lbl = QLabel(title)
    title_lbl.setObjectName("CollabStepTitle")
    layout.addWidget(title_lbl)

    if state is not None:
        layout.addWidget(state)

    if detail:
        detail_lbl = QLabel(detail)
        detail_lbl.setObjectName("CollabStepDetail")
        detail_lbl.setWordWrap(True)
        layout.addWidget(detail_lbl)

    body = QVBoxLayout()
    body.setSpacing(10)
    layout.addLayout(body)

    action_row = QHBoxLayout()
    action_row.setSpacing(8)
    layout.addLayout(action_row)
    return panel, body, action_row


# ── CollabView ────────────────────────────────────────────────────────────────

class CollabView(BaseView):
    """Collaboration module view.

    Requires ctx.collab_service (CollabService) to be present.  If the service
    is not available the view shows a "服务未启动" placeholder row and becomes
    effectively read-only.
    """

    view_id   = "collab"
    nav_title = "协作"
    nav_icon  = "👥"

    def __init__(self, ctx: "AppContext") -> None:
        # Service is optional — the view degrades gracefully when absent
        self._service: Optional["CollabService"] = getattr(ctx, "collab_service", None)
        self._connected_service: Optional["CollabService"] = None
        self._project_filter = ""
        self._active_method = ""
        super().__init__(ctx)
        self._offline_queue = StatusRetryQueue(ctx.settings._qs)
        self._retry_timer = QTimer(self)
        self._retry_timer.setInterval(30_000)
        self._retry_timer.timeout.connect(self._retry_offline_drafts)
        self._connect_service_signals()

    # ── BaseView contract ─────────────────────────────────────────────────

    def _setup_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 16, 20, 16)
        root.setSpacing(14)

        # ── Header ───────────────────────────────────────────────────────
        header_frame = QFrame()
        header_frame.setObjectName("CollabHeader")
        _elevate_card(header_frame)
        header_layout = QHBoxLayout(header_frame)
        header_layout.setContentsMargins(14, 10, 14, 10)
        header_layout.setSpacing(12)

        title_col = QVBoxLayout()
        title_col.setSpacing(4)
        title = QLabel("协作中心")
        title.setObjectName("CollabTitle")
        title_col.addWidget(title)
        self._scope_label = QLabel("团队永久码：—")
        self._scope_label.setObjectName("CollabScope")
        title_col.addWidget(self._scope_label)
        self._connection_label = QLabel("")
        self._connection_label.setObjectName("CollabScope")
        self._connection_label.hide()
        title_col.addWidget(self._connection_label)
        header_layout.addLayout(title_col, stretch=2)

        stats_frame = QFrame()
        stats_frame.setObjectName("CollabStatsBar")
        stats_layout = QHBoxLayout(stats_frame)
        stats_layout.setContentsMargins(12, 8, 12, 8)
        stats_layout.setSpacing(10)
        self._local_project_badge = QLabel("本机项目：—")
        self._local_project_badge.setObjectName("CollabMetric")
        stats_layout.addWidget(self._local_project_badge)
        self._device_count_badge = QLabel("在线 0")
        self._device_count_badge.setObjectName("CollabMetric")
        stats_layout.addWidget(self._device_count_badge)
        self._project_count_badge = QLabel("项目 0")
        self._project_count_badge.setObjectName("CollabMetric")
        stats_layout.addWidget(self._project_count_badge)
        self._task_count_badge = QLabel("任务 0")
        self._task_count_badge.setObjectName("CollabMetric")
        stats_layout.addWidget(self._task_count_badge)
        self._conflict_count_badge = QLabel("冲突 0")
        self._conflict_count_badge.setObjectName("CollabMetric")
        stats_layout.addWidget(self._conflict_count_badge)
        header_layout.addWidget(stats_frame, stretch=3)

        status_col = QVBoxLayout()
        status_col.setSpacing(8)
        self._status_badge = QLabel("⚪ 协作未启动")
        self._status_badge.setObjectName("CollabStatusBadge")
        self._status_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        status_col.addWidget(self._status_badge)
        self._debug_btn = QPushButton("诊断")
        self._debug_btn.setObjectName("Ghost")
        self._debug_btn.setCheckable(True)
        self._debug_btn.toggled.connect(self._toggle_debug_drawer)
        status_col.addWidget(self._debug_btn, alignment=Qt.AlignmentFlag.AlignHCenter)
        header_layout.addLayout(status_col)
        root.addWidget(header_frame)

        self._next_step_label = QLabel("下一步：—")
        self._next_step_label.setObjectName("CollabGuideTitle")
        self._next_step_detail = QLabel(
            "先点上方「方式一 / 方式二 / 方式三」，下方展开对应填写项与示例；无需弹窗。"
        )
        self._next_step_detail.setObjectName("CollabGuideDetail")
        self._next_step_detail.setWordWrap(True)

        self._setup_btn = QPushButton("设置永久码")
        self._setup_btn.setObjectName("Primary")
        self._setup_btn.setMinimumHeight(32)
        self._setup_btn.clicked.connect(self._on_setup_wizard)

        self._share_btn = QPushButton("复制局域网连接码")
        self._share_btn.setObjectName("Outline")
        self._share_btn.setMinimumHeight(32)
        self._share_btn.clicked.connect(self._on_share_addr)

        self._manual_toggle_btn = QPushButton("手动连接")
        self._manual_toggle_btn.setObjectName("Outline")
        self._manual_toggle_btn.setMinimumHeight(34)
        self._manual_toggle_btn.setMinimumWidth(112)
        icons.set_button_icon(self._manual_toggle_btn, "mdi6.lan-connect", size=16)
        self._manual_toggle_btn.clicked.connect(self._open_manual_connect_dialog)

        self._shared_scope_btn = QPushButton("共享范围")
        self._shared_scope_btn.setObjectName("Outline")
        self._shared_scope_btn.setMinimumHeight(34)
        self._shared_scope_btn.setMinimumWidth(112)
        icons.set_button_icon(self._shared_scope_btn, "mdi6.folder-multiple-outline", size=16)
        self._shared_scope_btn.clicked.connect(self._open_share_scope_dialog)

        self._pick_project_btn = QPushButton("配对项目")
        self._pick_project_btn.setObjectName("Outline")
        self._pick_project_btn.setMinimumHeight(32)
        self._pick_project_btn.clicked.connect(self._on_pick_team_project)

        self._same_name_btn = QPushButton("同名一键配对")
        self._same_name_btn.setObjectName("Outline")
        self._same_name_btn.setMinimumHeight(32)
        self._same_name_btn.clicked.connect(self._on_same_name_bind_quick)
        self._same_name_btn.hide()

        self._project_code_btn = QPushButton("打开项目码共享")
        self._project_code_btn.setObjectName("Primary")
        self._project_code_btn.setMinimumHeight(32)
        self._project_code_btn.clicked.connect(self._on_project_sync_code)
        self._bind_project_btn = self._pick_project_btn

        self._team_scope_state = QLabel("团队永久码：未设置")
        self._team_scope_state.setObjectName("CollabScopeState")
        self._project_scope_state = QLabel("项目码共享：可单独使用；不需要先设置团队永久码。")
        self._project_scope_state.setObjectName("CollabScopeState")
        self._project_scope_state.setWordWrap(True)

        self._remote_invite_btn = QPushButton("生成远程邀请码")
        self._remote_invite_btn.setObjectName("Outline")
        self._remote_invite_btn.setMinimumHeight(32)
        self._remote_invite_btn.clicked.connect(self._on_remote_invite)

        self._remote_join_code_edit = _style_collab_field(
            QLineEdit(),
            placeholder="粘贴队友发来的远程邀请码",
        )

        self._remote_join_btn = QPushButton("请求远程连接")
        self._remote_join_btn.setObjectName("Primary")
        self._remote_join_btn.setMinimumHeight(32)
        self._remote_join_btn.clicked.connect(self._on_remote_join)

        self._remote_status_label = QLabel(
            "远程连接需要账号中继服务。配置后可生成一次性邀请码，连接须由对方确认。"
        )
        self._remote_status_label.setObjectName("CollabScopeState")
        self._remote_status_label.setWordWrap(True)
        self._remote_security_summary_label = QLabel(
            "公网远程：账号验证 · 一次性邀请码 · 默认只读 · 对方确认 · 可撤销"
        )
        self._remote_security_summary_label.setObjectName("CollabSecuritySummary")
        self._remote_security_summary_label.setWordWrap(True)
        self._remote_security_summary_label.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Preferred,
        )

        self._conflict_banner = QLabel()
        self._conflict_banner.setObjectName("ConflictBanner")
        self._conflict_banner.setWordWrap(True)
        self._conflict_banner.hide()
        root.addWidget(self._conflict_banner)

        setup_hub = QFrame()
        setup_hub.setObjectName("CollabSetupHub")
        _elevate_card(setup_hub)
        hub_layout = QVBoxLayout(setup_hub)
        hub_layout.setContentsMargins(14, 10, 14, 10)
        hub_layout.setSpacing(8)

        # 唯一醒目引导横幅(v0.56 深度协调): 未完成时呼吸式脉冲吸引注意,
        # 协作启动后自动隐藏并停脉冲。页面里其它「填码点保存」类文字全部压成
        # 普通说明, 不再各自占一条彩色带 —— 单一注意力焦点。
        guide_frame = QFrame()
        guide_frame.setObjectName("CollabGuide")
        guide_frame.setProperty("pulse", "off")
        self._guide_frame = guide_frame
        guide_layout = QHBoxLayout(guide_frame)
        guide_layout.setContentsMargins(14, 10, 14, 10)
        guide_layout.setSpacing(12)
        self._guide_icon = QLabel("👉")
        self._guide_icon.setObjectName("CollabGuideIcon")
        self._guide_icon.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignHCenter)
        guide_layout.addWidget(self._guide_icon, 0, Qt.AlignmentFlag.AlignTop)
        guide_text_col = QVBoxLayout()
        guide_text_col.setSpacing(3)
        self._next_step_label.setObjectName("CollabGuideTitle")
        guide_text_col.addWidget(self._next_step_label)
        self._next_step_detail.setObjectName("CollabGuideDetail")
        self._next_step_detail.setWordWrap(True)
        guide_text_col.addWidget(self._next_step_detail)
        guide_layout.addLayout(guide_text_col, 1)
        hub_layout.addWidget(guide_frame)

        # 呼吸脉冲定时器: 每 ~900ms 翻转 pulse 属性 → QSS 两态过渡出「呼吸」。
        # 外观级定时器, 挂 self, on_deactivate 停(遵项目 QTimer 泄漏红线)。
        self._guide_pulse_timer = QTimer(self)
        self._guide_pulse_timer.setInterval(900)
        self._guide_pulse_timer.timeout.connect(self._toggle_guide_pulse)
        self._guide_pulse_on = False

        method_row = QHBoxLayout()
        method_row.setContentsMargins(0, 0, 0, 0)
        method_row.setSpacing(10)
        self._method_team_btn = self._make_method_selector_card(
            "team",
            "方式一",
            "团队永久码",
            "同码自动连接",
        )
        self._method_project_btn = self._make_method_selector_card(
            "project",
            "方式二",
            "项目码共享",
            "粘贴项目码绑定",
        )
        self._method_remote_btn = self._make_method_selector_card(
            "remote",
            "方式三",
            "远程连接",
            "邀请码跨网协作",
        )
        for card in (
            self._method_team_btn,
            self._method_project_btn,
            self._method_remote_btn,
        ):
            card.setMinimumWidth(0)
            method_row.addWidget(card, 1)
        hub_layout.addLayout(method_row)

        self._method_active_title = QLabel("填写方式一：团队永久码")
        self._method_active_title.setObjectName("CollabMethodActiveTitle")
        self._method_active_title.hide()

        self._method_detail_frame = QFrame()
        self._method_detail_frame.setObjectName("CollabMethodDetail")
        method_detail_layout = QVBoxLayout(self._method_detail_frame)
        method_detail_layout.setContentsMargins(0, 4, 0, 0)
        method_detail_layout.setSpacing(0)

        self._method_stack = QStackedWidget()
        self._method_stack.setObjectName("CollabMethodStack")
        self._team_method_detail = self._make_team_method_detail()
        self._project_method_detail = self._make_project_method_detail()
        self._remote_method_detail = self._make_remote_method_detail()
        self._project_sync_panel = None
        for panel in (
            self._team_method_detail,
            self._project_method_detail,
            self._remote_method_detail,
        ):
            self._method_stack.addWidget(panel)
        method_detail_layout.addWidget(self._method_stack)
        hub_layout.addWidget(self._method_detail_frame)
        root.addWidget(setup_hub)

        self._connect_panel = None

        # ── Activity hub (always visible) ─────────────────────────────────
        activity_hub = QFrame()
        activity_hub.setObjectName("CollabActivityHub")
        _elevate_card(activity_hub)
        self._activity_hub = activity_hub
        activity_layout = QVBoxLayout(activity_hub)
        activity_layout.setContentsMargins(16, 14, 16, 14)
        activity_layout.setSpacing(12)

        hub_title = QLabel("设备与任务")
        hub_title.setObjectName("CollabHubTitle")
        activity_layout.addWidget(hub_title)

        self._connection_result_banner = QFrame()
        self._connection_result_banner.setObjectName("CollabConnectionResult")
        self._connection_result_banner.setProperty("role", "idle")
        result_lay = QHBoxLayout(self._connection_result_banner)
        result_lay.setContentsMargins(12, 10, 12, 10)
        result_lay.setSpacing(10)
        self._connection_result_icon = QLabel("○")
        self._connection_result_icon.setObjectName("CollabConnectionIcon")
        result_lay.addWidget(self._connection_result_icon, 0, Qt.AlignmentFlag.AlignTop)
        result_text_col = QVBoxLayout()
        result_text_col.setSpacing(4)
        self._connection_result_title = QLabel("连接结果")
        self._connection_result_title.setObjectName("CollabConnectionTitle")
        result_text_col.addWidget(self._connection_result_title)
        self._connection_result_detail = QLabel("填写并保存团队永久码后，这里会显示是否连接成功。")
        self._connection_result_detail.setObjectName("CollabConnectionDetail")
        self._connection_result_detail.setWordWrap(True)
        result_text_col.addWidget(self._connection_result_detail)
        result_lay.addLayout(result_text_col, 1)
        activity_layout.addWidget(self._connection_result_banner)
        self._empty_banner = self._connection_result_banner

        device_panel = QFrame()
        device_panel.setObjectName("CollabActivityPane")
        self._device_panel = device_panel
        device_layout = QVBoxLayout(device_panel)
        device_layout.setContentsMargins(0, 0, 0, 0)
        device_layout.setSpacing(8)
        dev_title = QLabel("在线设备")
        dev_title.setObjectName("Section")
        device_layout.addWidget(dev_title)
        self._device_list = QTableWidget(0, 7)
        self._device_list.setObjectName("CollabDeviceTable")
        self._device_list.setHorizontalHeaderLabels(
            ["操作者", "主机名", "项目", "团队永久码", "照片", "地址", "延迟"]
        )
        self._device_list.horizontalHeader().setStretchLastSection(False)
        self._device_list.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch
        )
        for col in (1, 2, 3, 4, 5, 6):
            self._device_list.horizontalHeader().setSectionResizeMode(
                col, QHeaderView.ResizeMode.ResizeToContents
            )
        self._device_list.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._device_list.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._device_list.setAlternatingRowColors(True)
        self._device_list.verticalHeader().hide()
        self._device_list.setMinimumHeight(140)
        device_layout.addWidget(self._device_list)
        device_panel.hide()
        activity_layout.addWidget(device_panel)

        task_panel = QFrame()
        task_panel.setObjectName("CollabActivityPane")
        self._task_panel = task_panel
        task_layout = QVBoxLayout(task_panel)
        task_layout.setContentsMargins(0, 0, 0, 0)
        task_layout.setSpacing(8)
        task_header = QHBoxLayout()
        task_title = QLabel("任务清单")
        task_title.setObjectName("Section")
        task_header.addWidget(task_title)
        task_header.addStretch()
        filter_label = QLabel("项目")
        filter_label.setObjectName("MutedSmall")
        task_header.addWidget(filter_label)
        self._project_combo = QComboBox()
        self._project_combo.setObjectName("ProjectFilterCombo")
        self._project_combo.setMinimumWidth(150)
        self._project_combo.currentIndexChanged.connect(self._on_project_filter_changed)
        task_header.addWidget(self._project_combo)
        task_layout.addLayout(task_header)

        self._task_table = QTableWidget(0, 5)
        self._task_table.setObjectName("CollabTaskTable")
        self._task_table.setHorizontalHeaderLabels(["UID", "项目", "状态", "负责人", "更新时间"])
        self._task_table.horizontalHeader().setStretchLastSection(False)
        self._task_table.horizontalHeader().setSectionResizeMode(
            _TASK_COL_UID, QHeaderView.ResizeMode.Stretch
        )
        for col in (_TASK_COL_PROJECT, _TASK_COL_STATUS, _TASK_COL_ASSIGNEE, _TASK_COL_UPDATED):
            self._task_table.horizontalHeader().setSectionResizeMode(
                col, QHeaderView.ResizeMode.ResizeToContents
            )
        self._task_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._task_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._task_table.setAlternatingRowColors(True)
        self._task_table.verticalHeader().hide()
        self._task_table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._task_table.customContextMenuRequested.connect(self._on_task_context_menu)
        self._task_table.setMinimumHeight(200)
        task_layout.addWidget(self._task_table)
        task_panel.hide()
        activity_layout.addWidget(task_panel)

        root.addWidget(activity_hub)
        root.addStretch(1)
        self._activity_splitter = activity_hub  # compat for older refresh hooks

        self._activity_empty_panel = QWidget()
        self._activity_empty_panel.hide()

        # ── Debug drawer (collapsed by default) ───────────────────────────
        self._debug_drawer = QFrame()
        self._debug_drawer.setObjectName("DebugDrawer")
        self._debug_drawer.setFrameShape(QFrame.Shape.StyledPanel)
        self._debug_drawer.setFixedHeight(140)
        debug_layout = QVBoxLayout(self._debug_drawer)
        debug_layout.setContentsMargins(8, 6, 8, 6)
        debug_layout.setSpacing(4)

        self._debug_local_addr = QLabel("本机局域网地址：—")
        self._debug_local_addr.setObjectName("Muted")
        debug_layout.addWidget(self._debug_local_addr)

        self._debug_log = QLabel("日志：—")
        self._debug_log.setObjectName("Muted")
        self._debug_log.setWordWrap(True)
        self._debug_log.setAlignment(Qt.AlignmentFlag.AlignTop)

        scroll = QScrollArea()
        scroll.setWidget(self._debug_log)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        debug_layout.addWidget(scroll)

        self._debug_drawer.hide()
        root.addWidget(self._debug_drawer)

        # ── No-service placeholder in task table ──────────────────────────
        if self._service is None:
            self._show_no_service_placeholder()
        else:
            self._refresh_summary([], [])
        self._select_collab_method("team")
        self._refresh_remote_connection_state()

    def _make_method_selector_card(
        self,
        method_id: str,
        badge: str,
        title: str,
        summary: str,
    ) -> QFrame:
        card = QFrame()
        card.setObjectName("CollabMethodTab")
        card.setProperty("methodId", method_id)
        card.setProperty("selected", False)
        card.setCursor(Qt.CursorShape.PointingHandCursor)
        card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        card.setMinimumHeight(72)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(0)
        top_row = QHBoxLayout()
        top_row.setSpacing(10)
        layout.addLayout(top_row)
        badge_lbl = QLabel(badge)
        badge_lbl.setObjectName("CollabMethodTabBadge")
        top_row.addWidget(badge_lbl, 0, Qt.AlignmentFlag.AlignTop)
        title_col = QVBoxLayout()
        title_col.setSpacing(4)
        title_lbl = QLabel(title)
        title_lbl.setObjectName("CollabMethodTabTitle")
        title_col.addWidget(title_lbl)
        summary_lbl = QLabel(summary)
        summary_lbl.setObjectName("CollabMethodTabSummary")
        summary_lbl.setWordWrap(True)
        title_col.addWidget(summary_lbl)
        top_row.addLayout(title_col, 1)

        def _on_press(event, mid=method_id):
            if event.button() == Qt.MouseButton.LeftButton:
                self._select_collab_method(mid)
            QFrame.mousePressEvent(card, event)

        def _on_enter(event):
            if card.property("selected") is not True:
                card.setProperty("hovered", True)
                card.style().unpolish(card)
                card.style().polish(card)
            QFrame.enterEvent(card, event)

        def _on_leave(event):
            if card.property("hovered") is True:
                card.setProperty("hovered", False)
                card.style().unpolish(card)
                card.style().polish(card)
            QFrame.leaveEvent(card, event)

        card.mousePressEvent = _on_press  # type: ignore[method-assign]
        card.enterEvent = _on_enter  # type: ignore[method-assign]
        card.leaveEvent = _on_leave  # type: ignore[method-assign]
        return card

    def _set_method_tab_selected(self, method: str) -> None:
        tab_map = {
            "team": self._method_team_btn,
            "project": self._method_project_btn,
            "remote": self._method_remote_btn,
        }
        for key, card in tab_map.items():
            selected = key == method
            card.setProperty("selected", selected)
            card.setProperty("hovered", False)
            card.style().unpolish(card)
            card.style().polish(card)

    def _select_collab_method(self, method: str) -> None:
        if method not in {"team", "project", "remote"}:
            return
        self._active_method = method
        self._set_method_tab_selected(method)
        titles = {
            "team": "填写方式一：团队永久码",
            "project": "填写方式二：项目码共享",
            "remote": "填写方式三：远程连接",
        }
        if hasattr(self, "_method_active_title"):
            self._method_active_title.setText(titles[method])
        if hasattr(self, "_method_stack"):
            self._method_stack.setCurrentWidget(
                {
                    "team": self._team_method_detail,
                    "project": self._project_method_detail,
                    "remote": self._remote_method_detail,
                }[method]
            )
        if method == "team":
            self._refresh_team_setup_panel()
        elif method == "project":
            panel = self._ensure_project_sync_panel()
            if hasattr(panel, "_refresh_local_code"):
                panel._refresh_local_code()
        elif method == "remote":
            self._refresh_remote_connection_state()

    def _make_team_method_detail(self) -> QFrame:
        panel = QFrame()
        panel.setObjectName("CollabMethodBody")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        shell = _collab_detail_shell(panel)
        shell_layout = QVBoxLayout(shell)
        shell_layout.setContentsMargins(0, 0, 0, 0)
        shell_layout.setSpacing(10)

        example = QLabel("示例：永久码填 ZJ-SMX-B1，名字填小王；队友填相同码即可上线。")
        example.setObjectName("CollabMethodExample")
        example.setWordWrap(True)
        self._team_example_label = example
        shell_layout.addWidget(example)

        self._team_setup_panel = self._make_team_setup_panel()
        shell_layout.addWidget(self._team_setup_panel)

        layout.addWidget(shell, 1)
        return panel

    def _make_project_method_detail(self) -> QFrame:
        panel = QFrame()
        panel.setObjectName("CollabMethodBody")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        shell = _collab_detail_shell(panel)
        shell_layout = QVBoxLayout(shell)
        shell_layout.setContentsMargins(0, 0, 0, 0)
        shell_layout.setSpacing(10)

        example = QLabel(
            "示例：复制项目码发给队友；对方粘贴后点「连接到所选项目」。"
        )
        example.setObjectName("CollabMethodExample")
        example.setWordWrap(True)
        shell_layout.addWidget(example)
        self._project_scope_state.setObjectName("CollabMethodStatus")
        shell_layout.addWidget(self._project_scope_state)

        self._project_detail_host = QWidget()
        self._project_detail_host_layout = QVBoxLayout(self._project_detail_host)
        self._project_detail_host_layout.setContentsMargins(0, 0, 0, 0)
        self._project_detail_host_layout.setSpacing(0)
        shell_layout.addWidget(self._project_detail_host, 1)
        aux_row = QHBoxLayout()
        aux_row.setSpacing(8)
        aux_row.addWidget(self._pick_project_btn)
        aux_row.addWidget(self._same_name_btn)
        aux_row.addStretch()
        shell_layout.addLayout(aux_row)
        layout.addWidget(shell, 1)
        return panel

    def _make_remote_method_detail(self) -> QFrame:
        panel = QFrame()
        panel.setObjectName("CollabMethodBody")
        panel.setProperty("mode", "remote")
        self._remote_connection_panel = panel
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        shell = _collab_detail_shell(panel)
        shell_layout = QVBoxLayout(shell)
        shell_layout.setContentsMargins(0, 0, 0, 0)
        shell_layout.setSpacing(10)

        example = QLabel(
            "示例：生成邀请码发给队友，或粘贴对方邀请码后请求连接。"
        )
        example.setObjectName("CollabMethodExample")
        example.setWordWrap(True)
        shell_layout.addWidget(example)

        self._remote_status_label.setObjectName("CollabMethodStatus")
        shell_layout.addWidget(self._remote_status_label)
        self._remote_security_summary_label.hide()
        shell_layout.addWidget(self._remote_security_summary_label)

        remote_form = QFrame()
        remote_form.setObjectName("CollabMethodForm")
        action_row = QHBoxLayout(remote_form)
        action_row.setContentsMargins(16, 16, 16, 16)
        action_row.setSpacing(10)
        self._remote_invite_btn.setMinimumHeight(32)
        self._remote_join_btn.setMinimumHeight(32)
        action_row.addWidget(self._remote_invite_btn)
        action_row.addWidget(self._remote_join_code_edit, 1)
        action_row.addWidget(self._remote_join_btn)
        shell_layout.addWidget(remote_form)
        layout.addWidget(shell, 1)
        return panel

    def _open_share_scope_dialog(self) -> None:
        dlg = CollabShareScopeDialog(self.ctx, parent=self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        peers = self._service.peers() if self._service else []
        self._refresh_scope_cards(peers)
        self._refresh_devices()

    def _open_manual_connect_dialog(self) -> None:
        dlg = CollabManualConnectDialog(
            on_connect=self._connect_manual_peer,
            parent=self,
        )
        dlg.exec()

    def _connect_manual_peer(self, ip: str, port: int) -> None:
        if self._service:
            self._service.add_manual_peer(ip, port)

    def _make_team_setup_panel(self) -> QFrame:
        panel = QFrame()
        panel.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        # v0.56 深度协调: 此前这里常驻一条「填写永久码和名字后点保存。」灰带,
        # 与顶部醒目引导 + 面板 detail 三重重复。改为默认空、仅在有真实操作反馈
        # (已生成/已复制/错误)时才显示的行内提示 —— 平时不占视觉。
        self._team_setup_status = QLabel("")
        self._team_setup_status.setObjectName("CollabInlineFeedback")
        self._team_setup_status.setWordWrap(True)
        self._team_setup_status.hide()

        step_entry, entry_body, entry_actions = _collab_step_panel(
            "团队永久码",
            "与队友填写相同的永久码即可自动发现；保存后会启动本机协作服务。",
            entry=True,
            state=self._team_setup_status,
        )

        self._team_code_edit = _style_collab_field(
            QLineEdit(),
            placeholder="输入队友给你的码，或随机生成",
            code=True,
        )
        gen_btn = QPushButton("随机生成")
        gen_btn.setObjectName("Outline")
        icons.set_button_icon(gen_btn, "mdi6.dice-multiple-outline", size=16)
        gen_btn.clicked.connect(self._generate_team_code_inline)
        copy_team_btn = QPushButton("复制永久码")
        copy_team_btn.setObjectName("Outline")
        icons.set_button_icon(copy_team_btn, "mdi6.content-copy", size=16)
        copy_team_btn.clicked.connect(self._copy_team_code_inline)
        _collab_form_block(
            entry_body,
            "永久码",
            self._team_code_edit,
            gen_btn,
            copy_team_btn,
            action_below=False,
            framed=False,
        )

        pair_row = QHBoxLayout()
        pair_row.setContentsMargins(0, 0, 0, 0)
        pair_row.setSpacing(16)
        name_col = QVBoxLayout()
        name_col.setContentsMargins(0, 0, 0, 0)
        name_col.setSpacing(0)
        pair_col = QVBoxLayout()
        pair_col.setContentsMargins(0, 0, 0, 0)
        pair_col.setSpacing(0)

        self._team_operator_edit = _style_collab_field(
            QLineEdit(),
            placeholder="例如 小王",
        )
        _collab_form_block(name_col, "我的名字", self._team_operator_edit, framed=False)

        self._team_pairing_input = _style_collab_field(
            QLineEdit(),
            placeholder="可选：粘贴队友发来的连接码",
        )
        _collab_form_block(pair_col, "备用连接码", self._team_pairing_input, framed=False)
        pair_row.addLayout(name_col, 1)
        pair_row.addLayout(pair_col, 1)
        entry_body.addLayout(pair_row)

        self._team_save_btn = QPushButton("保存并启动协作")
        self._team_save_btn.setObjectName("CollabPrimaryAction")
        self._team_save_btn.setMinimumHeight(32)
        self._team_save_btn.setMaximumWidth(220)
        self._team_save_btn.clicked.connect(self._save_team_setup_inline)
        entry_actions.addWidget(self._team_save_btn)
        # v0.56 修复: 「设置/修改永久码」向导按钮此前创建后从未挂进任何布局
        # (孤儿控件) —— 保存入口之外的修改路径因此消失。挂到主 CTA 旁,
        # 降为 Outline 避免双主按钮打架。
        self._setup_btn.setObjectName("Outline")
        entry_actions.addWidget(self._setup_btn)
        entry_actions.addStretch()
        layout.addWidget(step_entry)

        self._team_post_save_frame, share_body, _share_actions = _collab_step_panel(
            "分享给队友",
            "保存后可复制局域网地址或连接码，发给队友粘贴即可。",
        )

        self._team_addr_display = _style_collab_field(
            QLineEdit(),
            readonly=True,
            placeholder="保存并启动后显示",
            code=True,
        )
        copy_addr_btn = QPushButton("复制地址")
        copy_addr_btn.setObjectName("Outline")
        icons.set_button_icon(copy_addr_btn, "mdi6.content-copy", size=16)
        copy_addr_btn.clicked.connect(self._copy_team_addr_inline)
        _collab_form_block(
            share_body,
            "局域网地址",
            self._team_addr_display,
            copy_addr_btn,
            framed=False,
        )

        self._team_pairing_display = _style_collab_field(
            QLineEdit(),
            readonly=True,
            placeholder="保存并启动后生成",
            code=True,
        )
        copy_pairing_btn = QPushButton("复制连接码")
        copy_pairing_btn.setObjectName("Outline")
        icons.set_button_icon(copy_pairing_btn, "mdi6.content-copy", size=16)
        copy_pairing_btn.clicked.connect(self._copy_team_pairing_inline)
        _collab_form_block(
            share_body,
            "连接码",
            self._team_pairing_display,
            copy_pairing_btn,
            framed=False,
        )
        self._team_post_save_frame.hide()
        layout.addWidget(self._team_post_save_frame)

        aux_panel, _, aux_actions = _collab_step_panel(
            "辅助操作",
            "选择要向队友展示的项目范围；发现不了设备时再用手动连接。",
        )
        aux_actions.addWidget(self._shared_scope_btn)
        aux_actions.addWidget(self._manual_toggle_btn)
        aux_actions.addStretch()
        layout.addWidget(aux_panel)
        return panel

    def _flash_team_status(self, text: str) -> None:
        """行内操作反馈: 有内容才显示, 空则隐藏(不占常驻视觉)。"""
        if not hasattr(self, "_team_setup_status"):
            return
        msg = str(text or "").strip()
        self._team_setup_status.setText(msg)
        self._team_setup_status.setVisible(bool(msg))

    def _update_team_post_save_visibility(self) -> None:
        if not hasattr(self, "_team_post_save_frame"):
            return
        svc = self._service or getattr(self.ctx, "collab_service", None)
        running = False
        if svc is not None:
            try:
                running = bool(svc.is_running())
            except Exception:  # noqa: BLE001
                running = False
        has_saved = bool(self._team_code_edit.text().strip()) and running
        self._team_post_save_frame.setVisible(has_saved)
        # §7 旧: self._team_setup_status.setVisible(not has_saved) —— 会强行显示
        # 空反馈标签占位。反馈显隐现由 _flash_team_status 按内容管理, 这里不再碰。
        if hasattr(self, "_team_example_label"):
            self._team_example_label.setVisible(not has_saved)
        if hasattr(self, "_share_btn"):
            self._share_btn.setVisible(not has_saved)
        if hasattr(self, "_team_save_btn"):
            self._team_save_btn.setText(
                "保存修改" if has_saved else "保存并启动协作"
            )
        if hasattr(self, "_team_setup_panel"):
            self._team_setup_panel.updateGeometry()
            self._team_setup_panel.adjustSize()

    def _show_team_setup_panel(self) -> None:
        self._select_collab_method("team")

    def _hide_team_setup_panel(self) -> None:
        return

    def _settings_value(self, key: str, default: str = "") -> str:
        settings = getattr(self.ctx, "settings", None)
        qs = getattr(settings, "_qs", settings)
        try:
            return str(qs.value(key, default, type=str))
        except Exception:  # noqa: BLE001
            return default

    def _set_setting(self, key: str, value: object) -> None:
        settings = getattr(self.ctx, "settings", None)
        qs = getattr(settings, "_qs", settings)
        try:
            qs.setValue(key, value)
        except Exception:  # noqa: BLE001
            pass

    def _refresh_team_setup_panel(self) -> None:
        svc = self._service or getattr(self.ctx, "collab_service", None)
        settings = getattr(self.ctx, "settings", None)
        group = (
            str(getattr(svc, "group_code", "") or "")
            or str(getattr(settings, "team_code", "") or "")
            or self._settings_value("collab/team_code", "")
        )
        if group:
            self._team_code_edit.setText(group)
        operator = (
            str(getattr(settings, "operator_name", "") or "")
            or self._settings_value("user/current_user", "")
        )
        if operator and not self._team_operator_edit.text().strip():
            self._team_operator_edit.setText(operator)
        self._refresh_team_share_fields()
        self._update_team_post_save_visibility()

    def _generate_team_code_inline(self) -> None:
        try:
            from app.widgets.collab_pairing import generate_group_code
            self._team_code_edit.setText(generate_group_code())
            self._flash_team_status("已生成永久码，保存后开始协作。")
        except Exception as exc:  # noqa: BLE001
            self._flash_team_status(f"生成失败：{exc}")

    def _copy_team_code_inline(self) -> None:
        code = self._team_code_edit.text().strip()
        if not code:
            self._team_code_edit.setFocus(Qt.FocusReason.OtherFocusReason)
            self._flash_team_status("请先输入或生成团队永久码。")
            return
        QApplication.clipboard().setText(code)
        self._flash_team_status("团队永久码已复制。")

    def _save_team_setup_inline(self) -> None:
        pending_pair_info = None
        pairing_text = self._team_pairing_input.text().strip()
        if pairing_text:
            try:
                from app.widgets.collab_pairing import decode_pairing
                pending_pair_info = decode_pairing(pairing_text)
            except ValueError:
                self._team_pairing_input.setFocus(Qt.FocusReason.OtherFocusReason)
                self._flash_team_status("备用连接码格式不正确。")
                return
            self._team_code_edit.setText(pending_pair_info.group_code)

        group_code = self._team_code_edit.text().strip()
        if not group_code:
            self._generate_team_code_inline()
            group_code = self._team_code_edit.text().strip()
        if not group_code:
            self._team_code_edit.setFocus(Qt.FocusReason.OtherFocusReason)
            self._flash_team_status("请先输入或生成团队永久码。")
            return

        settings = getattr(self.ctx, "settings", None)
        self._set_setting("collab/enabled", True)
        self._set_setting("collab/team_code", group_code)
        try:
            settings.collab_enabled = True
            settings.team_code = group_code
            settings.flush_to_disk()
        except Exception:  # noqa: BLE001
            pass

        operator = self._team_operator_edit.text().strip()
        if operator:
            self._set_setting("user/current_user", operator)

        svc = self.ctx.ensure_collab_service()
        if svc is None:
            self._flash_team_status("永久码已保存；协作服务未启动。")
            self._refresh_devices()
            return

        self._service = svc
        old_code = ""
        try:
            old_code = str(svc.group_code or "").strip()
        except Exception:  # noqa: BLE001
            old_code = ""
        try:
            svc.set_group_code(group_code)
        except Exception:  # noqa: BLE001
            pass

        project_name = (
            str(getattr(self.ctx, "current_project_dir", "") or "")
            or str(getattr(settings, "last_project_dir", "") or "")
        )
        running = False
        try:
            running = bool(svc.is_running())
        except Exception:  # noqa: BLE001
            running = False
        # v0.56 修复「永久码不能更新」: set_group_code 只改内存, 运行中的服务
        # mDNS 仍以旧码注册/匹配 —— 码变了必须停旧服务, 走下方 start 以新码重启。
        if running and old_code and old_code != group_code:
            try:
                svc.stop()
                running = False
            except Exception as exc:  # noqa: BLE001
                self._flash_team_status(f"重启协作服务失败：{exc}")
                return
        if not running and hasattr(svc, "start"):
            try:
                svc.start(
                    project_name=project_name,
                    group_code=group_code,
                    project_dir=str(getattr(self.ctx, "current_project_dir", "") or project_name),
                )
            except Exception as exc:  # noqa: BLE001
                self._flash_team_status(f"启动协作失败：{exc}")
                return
        elif hasattr(svc, "ensure_running"):
            try:
                svc.ensure_running(self.ctx)
            except Exception:  # noqa: BLE001
                pass

        if pending_pair_info is not None and hasattr(svc, "add_manual_peer"):
            try:
                svc.add_manual_peer(pending_pair_info.ip, pending_pair_info.port)
            except Exception:  # noqa: BLE001
                pass

        self._connect_service_signals()
        self._refresh_team_share_fields()
        self._refresh_devices()
        self._refresh_tasks()
        self._update_team_post_save_visibility()

    def _refresh_team_share_fields(self) -> None:
        if not hasattr(self, "_team_addr_display"):
            return
        svc = self._service or getattr(self.ctx, "collab_service", None)
        addr = ""
        if svc is not None and hasattr(svc, "local_address"):
            try:
                addr = str(svc.local_address() or "")
            except Exception:  # noqa: BLE001
                addr = ""
        self._team_addr_display.setText(addr)
        self._team_pairing_display.setText(self._team_pairing_code(addr))
        self._update_team_post_save_visibility()

    def _team_pairing_code(self, addr: str = "") -> str:
        svc = self._service or getattr(self.ctx, "collab_service", None)
        if not addr and svc is not None and hasattr(svc, "local_address"):
            try:
                addr = str(svc.local_address() or "")
            except Exception:  # noqa: BLE001
                addr = ""
        if not addr:
            return ""
        group_code = (
            str(getattr(svc, "group_code", "") or "")
            or self._team_code_edit.text().strip()
        )
        try:
            from app.widgets.collab_pairing import encode_pairing
            ip, port_text = addr.rsplit(":", 1)
            return encode_pairing(ip, int(port_text), group_code)
        except Exception:  # noqa: BLE001
            return addr

    def _copy_team_addr_inline(self) -> None:
        self._refresh_team_share_fields()
        text = self._team_addr_display.text().strip()
        if not text:
            self._flash_team_status("先保存永久码并启动协作，才能复制本机地址。")
            return
        QApplication.clipboard().setText(text)
        self._flash_team_status("本机局域网地址已复制。")

    def _copy_team_pairing_inline(self) -> None:
        self._refresh_team_share_fields()
        text = self._team_pairing_display.text().strip()
        if not text:
            self._flash_team_status("先保存永久码并启动协作，才能复制连接码。")
            return
        QApplication.clipboard().setText(text)
        self._flash_team_status("局域网连接码已复制。")

    def _make_scope_panel(
        self,
        title: str,
        detail: str,
        state_label: QLabel,
        *buttons: QPushButton,
    ) -> QFrame:
        panel, _body, action_row = _collab_step_panel(
            title,
            detail,
            state=state_label,
        )
        panel.setMinimumHeight(148)
        for button in buttons:
            action_row.addWidget(button)
        action_row.addStretch()
        return panel

    def on_activate(self) -> None:
        """Refresh devices + tasks from the service."""
        self._service = getattr(self.ctx, "collab_service", self._service)
        if self._service is not None:
            self._service.ensure_running(self.ctx)
            self._connect_service_signals()
        self._refresh_devices()
        self._refresh_tasks()
        if self._service:
            self._debug_local_addr.setText(
                f"本机局域网地址：{self._service.local_address()}"
            )
        if not self._retry_timer.isActive():
            self._retry_timer.start()
        # 无服务时 _refresh_devices/_refresh_tasks 提前 return, 引导刷新走不到,
        # 这里补一次: 保证「未启动」态也能显示并开始脉冲。
        self._refresh_next_step(
            self._service.peers() if self._service is not None else []
        )

    def on_deactivate(self) -> None:
        # 停外观级定时器: 页面不可见时不该继续 blink/重绘(项目 QTimer 泄漏红线)。
        if hasattr(self, "_guide_pulse_timer"):
            self._guide_pulse_timer.stop()
        if hasattr(self, "_retry_timer"):
            self._retry_timer.stop()

    # ── 引导脉冲 (呼吸式醒目提示) ──────────────────────────────────────────

    def _set_guide_pulse(self, active: bool) -> None:
        """active=未完成 → 启动呼吸脉冲; active=已完成 → 停脉冲并复位。"""
        timer = getattr(self, "_guide_pulse_timer", None)
        if timer is None:
            return
        if active:
            if not timer.isActive():
                timer.start()
        else:
            timer.stop()
            self._guide_pulse_on = False
            self._apply_guide_pulse_property("off")

    def _toggle_guide_pulse(self) -> None:
        self._guide_pulse_on = not self._guide_pulse_on
        self._apply_guide_pulse_property("on" if self._guide_pulse_on else "off")

    def _apply_guide_pulse_property(self, value: str) -> None:
        frame = getattr(self, "_guide_frame", None)
        if frame is None:
            return
        frame.setProperty("pulse", value)
        frame.style().unpolish(frame)
        frame.style().polish(frame)

    # ── Signal wiring ─────────────────────────────────────────────────────

    def _connect_service_signals(self) -> None:
        if self._service is None:
            return
        if self._connected_service is self._service:
            return
        self._service.peers_changed.connect(self._refresh_devices)
        self._service.tasks_changed.connect(self._refresh_tasks)
        self._service.conflict_detected.connect(self._on_conflict)
        self._service.server_ready.connect(self._on_server_ready)
        if hasattr(self._service, "project_bind_suggested"):
            self._service.project_bind_suggested.connect(self._on_project_bind_suggested)
        if hasattr(self._service, "photo_index_received"):
            self._service.photo_index_received.connect(self._on_photo_index_received)
        self._connected_service = self._service

    @pyqtSlot(str, str, int, str)
    def _on_photo_index_received(self, uid: str, kind: str, count: int, device_id: str) -> None:
        self._refresh_tasks()
        if self._service is not None:
            self._refresh_devices()

    # ── Slots ─────────────────────────────────────────────────────────────

    @pyqtSlot(int)
    def _on_server_ready(self, port: int) -> None:
        if self._service:
            addr = self._service.local_address()
            self._debug_local_addr.setText(f"本机局域网地址：{addr}")
            self._connection_label.setText(f"本机局域网地址：{addr}")

    @pyqtSlot()
    def _refresh_devices(self) -> None:
        if self._service is None:
            return
        peers = self._service.peers()
        if hasattr(self._device_list, "clearSpans"):
            self._device_list.clearSpans()
        self._device_list.setRowCount(len(peers))
        for row, peer in enumerate(peers):
            operator_label = peer_display_name(peer)
            pending_review = self._service.is_peer_pending_review(peer)
            if pending_review:
                operator_label = f"{operator_label}（待确认）"
            self._device_list.setItem(row, 0, _ro_item(operator_label))
            self._device_list.setItem(row, 1, _ro_item(peer.hostname or peer.ip))
            self._device_list.setItem(row, 2, _ro_item(_project_display(peer.project_name)))
            self._device_list.setItem(row, 3, _ro_item(peer.group_code or "—"))
            media_label = self._peer_media_sync_label(peer)
            if pending_review:
                media_label = "待确认"
            media_item = _ro_item(media_label)
            if media_label == "仅任务":
                media_item.setToolTip("同组但项目同步码不同；任务可见，照片不会同步。")
            elif media_label == "可同步":
                media_item.setToolTip("同组且项目同步码相同，可以同步照片/TIF/ZIP。")
            self._device_list.setItem(row, 4, media_item)
            addr_text = f"{peer.ip}:{peer.port}"
            if peer.manual:
                addr_text += " ✎"
            self._device_list.setItem(row, 5, _ro_item(addr_text))
            lat = f"{peer.latency_ms:.0f} ms" if peer.latency_ms is not None else "—"
            self._device_list.setItem(row, 6, _ro_item(lat))

        if not peers:
            self._device_list.setRowCount(1)
            empty = QTableWidgetItem(
                "暂无在线设备。让队友填写同一个团队永久码，或复制局域网连接码手动连接。"
            )
            empty.setFlags(empty.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self._device_list.setItem(0, 0, empty)
            self._device_list.setSpan(0, 0, 1, self._device_list.columnCount())

        self._status_badge.setText(
            build_collab_status(self._service, peers).status_badge
        )
        tasks = self._service.store.list_tasks()
        self._refresh_project_filter(tasks, peers)
        self._refresh_summary(tasks, peers)
        self._refresh_activity_visibility(peers, tasks)

    @pyqtSlot()
    def _refresh_tasks(self) -> None:
        if self._service is None:
            return
        tasks = self._service.store.list_tasks()
        # Sort by updated_at descending
        tasks.sort(key=lambda t: t.updated_at, reverse=True)
        peers = self._service.peers()
        self._refresh_project_filter(tasks, peers)
        visible_tasks = self._filtered_tasks(tasks)
        if hasattr(self._task_table, "clearSpans"):
            self._task_table.clearSpans()
        self._task_table.setRowCount(len(visible_tasks))
        for row, task in enumerate(visible_tasks):
            uid_item = QTableWidgetItem(task.uid)
            uid_item.setFlags(uid_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self._task_table.setItem(row, _TASK_COL_UID, uid_item)

            project_item = _ro_item(_project_display(task.project_name))
            self._task_table.setItem(row, _TASK_COL_PROJECT, project_item)

            status_val = task.status.value if hasattr(task.status, "value") else str(task.status)
            label = _STATUS_LABEL.get(status_val, status_val)
            colour = _STATUS_COLOURS.get(status_val, "#ffffff")
            status_item = QTableWidgetItem(label)
            status_item.setFlags(status_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            status_item.setBackground(_hex_to_qcolor(colour))
            self._task_table.setItem(row, _TASK_COL_STATUS, status_item)

            self._task_table.setItem(row, _TASK_COL_ASSIGNEE, _ro_item(task.assignee or "—"))
            ts = task.updated_at[:19].replace("T", " ") if task.updated_at else "—"
            self._task_table.setItem(row, _TASK_COL_UPDATED, _ro_item(ts))

        if not visible_tasks:
            self._task_table.setRowCount(1)
            empty = QTableWidgetItem(
                "暂无协作任务。进入照片工作区保存新编号后，任务会在这里同步给同组电脑。"
            )
            empty.setFlags(empty.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self._task_table.setItem(0, 0, empty)
            self._task_table.setSpan(0, 0, 1, self._task_table.columnCount())
        self._refresh_summary(tasks, peers)
        self._refresh_activity_visibility(peers, tasks)

    def _refresh_activity_visibility(
        self,
        peers: list["PeerInfo"],
        tasks: list["TaskRecord"],
    ) -> None:
        has_peers = bool(peers)
        has_tasks = bool(tasks)
        if hasattr(self, "_device_panel"):
            self._device_panel.setVisible(has_peers)
        if hasattr(self, "_task_panel"):
            self._task_panel.setVisible(has_tasks)
        if hasattr(self, "_activity_hub"):
            compact = not has_peers and not has_tasks
            if compact:
                self._activity_hub.setSizePolicy(
                    QSizePolicy.Policy.Expanding,
                    QSizePolicy.Policy.Maximum,
                )
                self._activity_hub.setMaximumHeight(280)
            else:
                self._activity_hub.setSizePolicy(
                    QSizePolicy.Policy.Expanding,
                    QSizePolicy.Policy.Expanding,
                )
                self._activity_hub.setMaximumHeight(16777215)

    def _refresh_project_filter(self, tasks: list["TaskRecord"], peers: list["PeerInfo"]) -> None:
        if not hasattr(self, "_project_combo"):
            return
        current = self._project_filter
        projects = {
            _project_display(getattr(task, "project_name", ""))
            for task in tasks
            if _project_display(getattr(task, "project_name", ""))
        }
        projects.update(
            _project_display(getattr(peer, "project_name", ""))
            for peer in peers
            if _project_display(getattr(peer, "project_name", ""))
        )
        local_project = _project_display(self._local_project_name())
        if local_project:
            projects.add(local_project)

        ordered = sorted(projects, key=lambda text: text.casefold())
        self._project_combo.blockSignals(True)
        self._project_combo.clear()
        self._project_combo.addItem("全部项目", "")
        for project in ordered:
            self._project_combo.addItem(project, project)
        idx = self._project_combo.findData(current)
        if idx < 0:
            current = ""
            idx = 0
        self._project_filter = current
        self._project_combo.setCurrentIndex(idx)
        self._project_combo.blockSignals(False)

    def _filtered_tasks(self, tasks: list["TaskRecord"]) -> list["TaskRecord"]:
        if not self._project_filter:
            return tasks
        return [
            task for task in tasks
            if _project_display(getattr(task, "project_name", "")) == self._project_filter
        ]

    def _refresh_summary(self, tasks: list["TaskRecord"], peers: list["PeerInfo"]) -> None:
        if self._service is None:
            self._scope_label.setText("团队永久码：—")
            self._connection_label.setText("本机局域网地址：启动后显示")
            self._local_project_badge.setText("当前 —")
            self._device_count_badge.setText("在线 0")
            self._project_count_badge.setText("项目 0")
            self._task_count_badge.setText("任务 0")
            self._conflict_count_badge.setText("冲突：0")
            self._refresh_next_step([])
            self._refresh_connection_result(
                build_collab_status(None, [])
            )
            return

        status = build_collab_status(self._service, peers)
        local_project = _project_display(self._local_project_name()) or "—"
        projects = {
            _project_display(getattr(task, "project_name", ""))
            for task in tasks
            if _project_display(getattr(task, "project_name", ""))
        }
        if local_project != "—":
            projects.add(local_project)
        projects.update(
            _project_display(getattr(peer, "project_name", ""))
            for peer in peers
            if _project_display(getattr(peer, "project_name", ""))
        )
        conflict_count = sum(
            1 for task in tasks
            if (task.status.value if hasattr(task.status, "value") else str(task.status)) == "conflict"
        )
        self._scope_label.setText(status.scope_label)
        if status.state in {"no_service", "not_started"}:
            self._connection_label.setText("本机局域网地址：启动后显示")
        else:
            self._connection_label.setText(f"本机局域网地址：{self._service.local_address()}")
        self._local_project_badge.setText(f"当前 {local_project}")
        self._device_count_badge.setText(f"在线 {len(peers)}")
        self._project_count_badge.setText(f"项目 {len(projects)}")
        self._task_count_badge.setText(f"任务 {len(tasks)}")
        self._conflict_count_badge.setText(f"冲突 {conflict_count}")
        self._conflict_count_badge.setObjectName(
            "CollabMetricDanger" if conflict_count else "CollabMetric"
        )
        self._conflict_count_badge.style().unpolish(self._conflict_count_badge)
        self._conflict_count_badge.style().polish(self._conflict_count_badge)
        self._refresh_scope_cards(peers)
        self._refresh_next_step(peers)
        self._refresh_connection_result(status)

    def _refresh_connection_result(self, status) -> None:
        if not hasattr(self, "_connection_result_title"):
            return
        icons = {
            "idle": "○",
            "waiting": "⏳",
            "success": "✓",
            "warn": "⚠",
        }
        self._connection_result_icon.setText(
            icons.get(status.connection_role, "○")
        )
        self._connection_result_title.setText(status.connection_title)
        self._connection_result_detail.setText(status.connection_detail)
        role = status.connection_role or "idle"
        banner = self._connection_result_banner
        banner.setProperty("role", role)
        banner.style().unpolish(banner)
        banner.style().polish(banner)

    def _refresh_scope_cards(self, peers: list["PeerInfo"]) -> None:
        svc = self._service
        if svc is None:
            self._team_scope_state.setText("团队永久码：未启动")
            self._project_scope_state.setText("项目码共享：可单独使用；协作服务未启动时只生成/连接项目码。")
            self._same_name_btn.hide()
            return

        code = str(getattr(svc, "group_code", "") or "")
        if code:
            self._team_scope_state.setText(f"团队永久码：{code}")
        else:
            self._team_scope_state.setText("团队永久码：未设置")
            self._project_scope_state.setText(
                "项目码共享：可单独使用；共享项目范围只影响团队永久码模式。"
            )
            self._same_name_btn.hide()
            return

        from app.services.collab_project_bind import (
            describe_project_sync_state,
            same_name_options,
        )
        self._project_scope_state.setText(describe_project_sync_state(svc, peers))
        same = same_name_options(svc, peers)
        if same and getattr(svc, "project_id", ""):
            self._same_name_btn.show()
            self._same_name_btn.setToolTip(
                f"队友也在做「{same[0].name}」，一键绑定为同一项目"
            )
        else:
            self._same_name_btn.hide()

    def _remote_collab_service(self):
        svc = getattr(self.ctx, "remote_collab_service", None)
        if svc is not None:
            return svc
        settings = getattr(self.ctx, "settings", None)
        if settings is None:
            return None
        relay_url = str(getattr(settings, "remote_relay_url", "") or "").strip()
        account_id = str(getattr(settings, "remote_account_id", "") or "").strip()
        device_token = str(getattr(settings, "remote_device_token", "") or "").strip()
        if not (relay_url and account_id and device_token):
            return None
        from app.services.remote_collab_service import RemoteCollabService
        svc = RemoteCollabService(relay_url, account_id, device_token)
        try:
            self.ctx.remote_collab_service = svc
        except Exception:  # noqa: BLE001
            pass
        return svc

    def _remote_connection_available(self) -> bool:
        svc = self._remote_collab_service()
        if svc is None:
            return False
        is_available = getattr(svc, "is_available", None)
        if callable(is_available):
            try:
                return bool(is_available())
            except Exception:  # noqa: BLE001
                return False
        is_configured = getattr(svc, "is_configured", None)
        if callable(is_configured):
            try:
                return bool(is_configured())
            except Exception:  # noqa: BLE001
                return False
        return True

    def _refresh_remote_connection_state(self) -> None:
        if not hasattr(self, "_remote_status_label"):
            return

        available = self._remote_connection_available()
        self._remote_invite_btn.setEnabled(available)
        self._remote_join_code_edit.setEnabled(available)
        self._remote_join_btn.setEnabled(available)
        self._remote_invite_btn.setText("生成远程邀请码")
        self._remote_join_btn.setText("请求远程连接")
        self._remote_join_code_edit.setPlaceholderText("粘贴队友发来的远程邀请码")
        self._remote_security_summary_label.setText(
            "公网远程：账号验证 · 一次性邀请码 · 默认只读 · 对方确认 · 可撤销"
        )
        if not available:
            self._remote_join_code_edit.clear()
            self._remote_security_summary_label.hide()
            self._remote_status_label.setText(
                "远程服务未配置：需要中继地址、账号和设备令牌。"
            )
            return

        self._remote_security_summary_label.show()
        self._remote_status_label.setText(
            "远程服务已配置：可生成一次性邀请码，或粘贴邀请码请求连接。"
        )

    def _refresh_next_step(self, peers: list["PeerInfo"]) -> None:
        if not hasattr(self, "_next_step_label"):
            return
        status = build_collab_status(self._service, peers)
        self._next_step_label.setText(status.next_step_label)
        self._next_step_detail.setText(status.next_step_detail)
        incomplete = status.state in {"no_service", "not_started", "missing_group"}
        if hasattr(self, "_guide_frame"):
            self._guide_frame.setVisible(incomplete)
        # 未完成 → 呼吸脉冲吸引注意; 协作已就绪 → 停闪(引导横幅同时隐藏)。
        self._set_guide_pulse(incomplete)
        self._setup_btn.setEnabled(status.setup_enabled)
        if status.state in {"no_service", "not_started", "missing_group"}:
            self._setup_btn.setText("设置永久码")
            self._setup_btn.setToolTip("输入或生成团队永久码（保存后自动重连）")
        else:
            self._setup_btn.setText("修改永久码")
            self._setup_btn.setToolTip("查看或修改团队永久码、名字和连接方式")
        self._bind_project_btn.setEnabled(status.bind_project_enabled)
        self._pick_project_btn.setEnabled(status.bind_project_enabled)
        show_connect = status.state in {"no_peers", "different_group"} and self._service
        if getattr(self, "_connect_panel", None) is not None:
            self._connect_panel.setVisible(bool(show_connect))
        self._same_name_btn.setEnabled(status.bind_project_enabled)
        self._project_code_btn.setEnabled(True)
        share_enabled = status.state not in {"no_service", "not_started"}
        self._share_btn.setEnabled(share_enabled)
        post_save_visible = (
            hasattr(self, "_team_post_save_frame")
            and self._team_post_save_frame.isVisible()
        )
        self._share_btn.setVisible(share_enabled and not post_save_visible)
        manual_enabled = status.state not in {"no_service", "not_started"}
        self._manual_toggle_btn.setEnabled(manual_enabled)
        self._manual_toggle_btn.setVisible(manual_enabled)
        self._shared_scope_btn.setEnabled(manual_enabled)
        self._shared_scope_btn.setVisible(manual_enabled)
        if getattr(self, "_active_method", "") == "remote":
            self._refresh_remote_connection_state()

    def _peer_media_sync_label(self, peer: "PeerInfo") -> str:
        svc = self._service
        if svc is None:
            return "—"
        if getattr(peer, "group_code", "") != svc.group_code:
            return "不同组"
        local_project_id = str(getattr(svc, "project_id", "") or "")
        peer_project_id = str(getattr(peer, "project_id", "") or "")
        if local_project_id and peer_project_id == local_project_id:
            return "可同步"
        return "仅任务"

    def _local_project_name(self) -> str:
        svc_project = getattr(self._service, "project_name", "") if self._service else ""
        if svc_project:
            return str(svc_project)
        return str(
            getattr(self.ctx, "current_project_dir", "")
            or getattr(getattr(self.ctx, "settings", None), "last_project_dir", "")
            or ""
        )

    @pyqtSlot(str)
    def _on_conflict(self, uid: str) -> None:
        msg = f'⚠ 编号冲突：“{uid}” 已被其他设备占用，请更改编号。'
        self._conflict_banner.setText(msg)
        self._conflict_banner.show()
        # Auto-hide after 8 s
        QTimer.singleShot(8000, self._conflict_banner.hide)

    def _on_project_filter_changed(self) -> None:
        self._project_filter = str(self._project_combo.currentData() or "")
        self._refresh_tasks()

    def _set_manual_error(self, msg: str) -> None:
        self._conflict_banner.setText(f"⚠ {msg}")
        self._conflict_banner.show()
        QTimer.singleShot(4000, self._conflict_banner.hide)

    def _on_share_addr(self) -> None:
        svc = self._service or getattr(self.ctx, "collab_service", None)
        addr = ""
        if svc is not None and hasattr(svc, "local_address"):
            try:
                addr = str(svc.local_address() or "")
            except Exception:  # noqa: BLE001
                addr = ""
        if not addr:
            self._show_team_setup_panel()
            self._flash_team_status("先保存团队永久码并启动协作，才能复制本机地址。")
            return
        QApplication.clipboard().setText(addr)
        self._refresh_team_share_fields()
        if hasattr(self, "_team_setup_status"):
            self._flash_team_status("本机局域网地址已复制。")

    def _on_pick_team_project(self) -> None:
        self._show_project_sync_panel()

    def _on_same_name_bind_quick(self) -> None:
        from app.services.collab_project_bind import same_name_options

        svc = self._service
        if svc is None:
            return
        opts = same_name_options(svc, svc.peers())
        if not opts:
            self._show_project_sync_panel()
            return
        try:
            svc.apply_project_sync_code(opts[0].code)
            svc.pull_all_specimens_from_session()
        except Exception as exc:  # noqa: BLE001
            self._set_manual_error(f"项目配对失败：{exc}")
            return
        self._on_project_bind_applied()
        self._project_scope_state.setText(f"已配对同名项目「{opts[0].name}」。")

    def _on_project_bind_applied(self) -> None:
        self._refresh_devices()
        self._refresh_tasks()

    def _on_project_bind_suggested(self, peer_name: str, project_name: str, sync_code: str) -> None:
        panel = self._ensure_project_sync_panel()
        if hasattr(panel, "_join_code"):
            panel._join_code.setText(sync_code)
        if hasattr(panel, "_set_project_status"):
            panel._set_project_status(
                f"项目状态：发现队友 {peer_name} 也在做「{project_name}」，项目码已填入。确认无误后点击连接。"
            )
        panel.show()

    def _on_project_sync_code(self) -> None:
        self._show_project_sync_panel()

    def _on_project_sync_code_applied(self) -> None:
        self._on_project_bind_applied()

    def _show_project_sync_panel(self) -> None:
        self._select_collab_method("project")

    def _ensure_project_sync_panel(self):
        if self._project_sync_panel is not None:
            return self._project_sync_panel
        panel = _ProjectSyncCodeDialog(self.ctx, self, inline=True)
        panel.setWindowFlags(Qt.WindowType.Widget)
        panel.applied.connect(self._on_project_bind_applied)
        self._project_sync_panel = panel
        self._project_detail_host_layout.addWidget(panel)
        return panel

    def _on_remote_invite(self) -> None:
        svc = self._remote_collab_service()
        if svc is None or not self._remote_connection_available():
            self._refresh_remote_connection_state()
            return
        try:
            invite = svc.create_invite(
                project_name=_project_display(self._local_project_name()),
                permission="read_only",
                ttl_seconds=600,
            )
        except Exception as exc:  # noqa: BLE001
            self._remote_status_label.setText(f"生成远程邀请码失败：{exc}")
            return
        QApplication.clipboard().setText(invite.code)
        self._remote_join_code_edit.setText(invite.code)
        suffix = f"；有效至 {invite.expires_at}" if getattr(invite, "expires_at", "") else ""
        self._remote_status_label.setText(
            f"远程邀请码已复制{suffix}。对方请求连接后仍需本机确认。"
        )

    def _on_remote_join(self) -> None:
        code = self._remote_join_code_edit.text().strip()
        if not code:
            self._remote_join_code_edit.setFocus(Qt.FocusReason.OtherFocusReason)
            self._remote_status_label.setText("请先粘贴队友发来的远程邀请码。")
            return
        svc = self._remote_collab_service()
        if svc is None or not self._remote_connection_available():
            self._refresh_remote_connection_state()
            return
        try:
            result = svc.join_invite(
                code,
                project_name=_project_display(self._local_project_name()),
            )
        except Exception as exc:  # noqa: BLE001
            self._remote_status_label.setText(f"请求远程连接失败：{exc}")
            return
        self._remote_join_code_edit.clear()
        peer = f"（{result.peer_name}）" if getattr(result, "peer_name", "") else ""
        msg = getattr(result, "message", "") or "请求已发送，等待对方本机确认。"
        self._remote_status_label.setText(
            f"{msg}{peer}"
        )

    def _on_setup_wizard(self) -> None:
        self._select_collab_method("team")

    def _on_setup_wizard_done(self, group_code: str, operator: str) -> None:
        self._service = getattr(self.ctx, "collab_service", self._service)
        self._connect_service_signals()
        self._refresh_devices()
        self._refresh_tasks()

    def _on_task_context_menu(self, pos) -> None:
        row = self._task_table.rowAt(pos.y())
        if row < 0:
            return
        uid_item = self._task_table.item(row, 0)
        if uid_item is None:
            return
        uid = uid_item.text()

        status_item = self._task_table.item(row, _TASK_COL_STATUS)
        if status_item is None:
            return
        status_label = status_item.text() if status_item else ""
        is_conflict = status_label == _STATUS_LABEL.get("conflict", "冲突")

        menu = QMenu(self)
        assign_act = menu.addAction("分配给我")
        status_menu = menu.addMenu("更改状态")
        status_actions: dict = {}
        for ns, label in (
            ("shooting", "拍摄中"),
            ("shot_done", "已拍完"),
            ("organizing", "整理中"),
            ("done", "完成"),
        ):
            status_actions[status_menu.addAction(label)] = ns
        void_act = menu.addAction("作废")
        resolve_act = None
        if is_conflict:
            resolve_act = menu.addAction("处理冲突")

        action = menu.exec(self._task_table.viewport().mapToGlobal(pos))
        svc = self._service
        if not svc or action is None:
            return

        if action == assign_act:
            operator = getattr(getattr(self.ctx, "settings", None), "operator_name", "")
            try:
                svc.assign_task(uid, operator)
            except Exception:
                pass
        elif action == void_act:
            try:
                svc.void_task(uid)
            except Exception:
                pass
        elif resolve_act is not None and action == resolve_act:
            try:
                svc.resolve_conflict(uid)
            except Exception:
                pass
        elif action in status_actions:
            self._update_task_status(uid, status_actions[action])
        self.on_activate()

    def _toggle_debug_drawer(self, checked: bool) -> None:
        self._debug_drawer.setVisible(checked)
        if checked and self._service:
            self._debug_local_addr.setText(
                f"本机局域网地址：{self._service.local_address()}"
            )
            peers = self._service.peers()
            lines = [f"  {p.hostname or p.ip}:{p.port}  延迟={p.latency_ms:.0f}ms" if p.latency_ms else
                     f"  {p.hostname or p.ip}:{p.port}" for p in peers]
            body = "\n".join(lines) if lines else "  （无在线节点）"
            self._debug_log.setText(f"在线节点：\n{body}")

    def _retry_offline_drafts(self) -> None:
        if self._service is None:
            return
        self._offline_queue.retry_all(self._service)

    def _update_task_status(self, uid: str, status: str) -> None:
        """Update task status, queuing as offline draft on network failure."""
        if self._service is None:
            return
        try:
            self._service.update_task_status(
                uid, status, force=True, broadcast=True,
            )
        except Exception:
            self._offline_queue.mark_draft(uid, status)

    def _show_no_service_placeholder(self) -> None:
        if hasattr(self._task_table, "clearSpans"):
            self._task_table.clearSpans()
        self._task_table.setRowCount(1)
        item = QTableWidgetItem("CollabService 未初始化 — 服务未启动")
        item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        self._task_table.setItem(0, 0, item)
        self._task_table.setSpan(0, 0, 1, self._task_table.columnCount())
        self._status_badge.setText("⚪ 协作服务未启动")
        self._refresh_summary([], [])
        self._refresh_activity_visibility([], [])


# ── Helpers ───────────────────────────────────────────────────────────────────

def _ro_item(text: str) -> QTableWidgetItem:
    item = QTableWidgetItem(text)
    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
    return item


def _project_display(project: Optional[str]) -> str:
    raw = str(project or "").strip()
    if not raw:
        return ""
    normalised = raw.replace("\\", "/").rstrip("/")
    path = Path(normalised)
    name = path.name
    return name or raw


def _hex_to_qcolor(hex_colour: str):  # type: ignore[return]
    """Convert #rrggbb to QColor (import deferred to avoid top-level Qt import)."""
    from PyQt6.QtGui import QColor
    return QColor(hex_colour)


# ── Project sync code dialog ─────────────────────────────────────────────────

class _ProjectSyncCodeDialog(QDialog):
    """Share or adopt a stable project identity for media sync."""

    applied = pyqtSignal()

    def __init__(
        self,
        ctx: "AppContext",
        parent: Optional[QWidget] = None,
        *,
        inline: bool = False,
    ) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self._inline = inline
        self._svc = getattr(ctx, "collab_service", None)
        if not inline:
            self.setWindowTitle("项目码共享")
            self.setMinimumSize(780, 430)
        else:
            self.setWindowFlags(Qt.WindowType.Widget)

        current_dir = str(getattr(ctx, "current_project_dir", "") or "")
        self._project_options = []
        try:
            from app.services.collab_share_registry import list_local_share_candidates
            extras = [current_dir] if current_dir else None
            self._project_options = list_local_share_candidates(extra_directories=extras)
        except Exception:  # noqa: BLE001
            self._project_options = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(12)
        note = QLabel(
            "项目码不是连接地址，也不包含本机绝对路径。它只声明“这两个文件夹是同一个采集项目”。"
            "团队永久码负责自动连接；项目码负责照片/TIF/ZIP 同步范围。"
        )
        note.setWordWrap(True)
        if inline:
            note.hide()
        layout.addWidget(note)

        flow = QLabel(
            "多人使用：第一台电脑复制项目码发给所有队友；第 2、3、4 台电脑都选择自己的本机项目，"
            "粘贴同一个项目码并连接。"
        )
        flow.setObjectName("MutedSmall")
        flow.setWordWrap(True)
        if inline:
            flow.hide()
        layout.addWidget(flow)

        project_row = QHBoxLayout()
        project_row.setSpacing(8)
        project_row.addWidget(QLabel("本机项目："))
        self._local_project_combo = QComboBox()
        self._local_project_combo.setMinimumWidth(260)
        for project in self._project_options:
            self._local_project_combo.addItem(project.name, project.directory)
        self._local_project_combo.currentIndexChanged.connect(self._refresh_local_code)
        project_row.addWidget(self._local_project_combo, 1)
        browse_btn = QPushButton("选择文件夹…")
        browse_btn.setObjectName("Ghost")
        browse_btn.clicked.connect(self._browse_local_project)
        project_row.addWidget(browse_btn)
        layout.addLayout(project_row)

        self._local_path_label = QLabel("")
        self._local_path_label.setObjectName("MutedSmall")
        self._local_path_label.setWordWrap(True)
        layout.addWidget(self._local_path_label)

        panels = QHBoxLayout()
        panels.setSpacing(12)

        send_panel = QFrame()
        send_panel.setObjectName("CollabStepPanel")
        send_lay = QVBoxLayout(send_panel)
        send_lay.setContentsMargins(12, 10, 12, 10)
        send_lay.setSpacing(8)
        send_title = QLabel("1. 本机项目码")
        send_title.setObjectName("CollabStepTitle")
        send_lay.addWidget(send_title)
        send_hint = QLabel("把这个码发给所有参与同一采集项目的电脑。")
        send_hint.setObjectName("MutedSmall")
        send_hint.setWordWrap(True)
        send_lay.addWidget(send_hint)
        self._local_code = QLineEdit()
        self._local_code.setReadOnly(True)
        self._local_code.setPlaceholderText("选择项目后生成项目码")
        send_lay.addWidget(self._local_code)

        self._copy_local_btn = QPushButton("复制所选项目码")
        self._copy_local_btn.clicked.connect(self._copy_local_code)
        send_lay.addWidget(self._copy_local_btn)
        panels.addWidget(send_panel, 1)

        join_panel = QFrame()
        join_panel.setObjectName("CollabStepPanel")
        join_lay = QVBoxLayout(join_panel)
        join_lay.setContentsMargins(12, 10, 12, 10)
        join_lay.setSpacing(8)
        join_title = QLabel("2. 连接队友项目码")
        join_title.setObjectName("CollabStepTitle")
        join_lay.addWidget(join_title)
        join_hint = QLabel("其他电脑在这里粘贴项目码，把所选本机项目连接到同一个采集项目。")
        join_hint.setObjectName("MutedSmall")
        join_hint.setWordWrap(True)
        join_lay.addWidget(join_hint)
        self._join_code = QLineEdit()
        self._join_code.setPlaceholderText("粘贴队友的项目码")
        join_lay.addWidget(self._join_code)
        self._apply_btn = QPushButton("连接到所选项目")
        self._apply_btn.setObjectName("Primary")
        self._apply_btn.clicked.connect(self._apply_join_code)
        join_lay.addWidget(self._apply_btn)
        panels.addWidget(join_panel, 1)
        layout.addLayout(panels)

        self._project_status_label = QLabel("项目状态：—")
        self._project_status_label.setObjectName("MutedSmall")
        self._project_status_label.setWordWrap(True)
        layout.addWidget(self._project_status_label)

        action_row = QHBoxLayout()
        action_row.addStretch()
        self._close_btn = QPushButton("关闭")
        self._close_btn.clicked.connect(self.close)
        action_row.addWidget(self._close_btn)
        if inline:
            self._close_btn.hide()
        layout.addLayout(action_row)
        self._refresh_local_code()

    def _copy_local_code(self) -> None:
        code = self._local_code.text().strip()
        if code:
            directory = self._selected_project_directory()
            if directory:
                self._share_directory(directory)
            QApplication.clipboard().setText(code)
            self._set_project_status("项目状态：项目码已复制。")

    def _set_project_status(self, text: str) -> None:
        if hasattr(self, "_project_status_label"):
            self._project_status_label.setText(text)

    def _selected_project_directory(self) -> str:
        return str(self._local_project_combo.currentData() or "").strip()

    def _selected_project_name(self) -> str:
        idx = self._local_project_combo.currentIndex()
        if 0 <= idx < len(self._project_options):
            return str(getattr(self._project_options[idx], "name", "") or "")
        directory = self._selected_project_directory()
        return Path(directory).name if directory else ""

    def _share_directory(self, directory: str) -> None:
        if not directory:
            return
        try:
            from app.services.collab_share_registry import (
                load_shared_dirs,
                save_shared_dirs,
            )
            settings = getattr(self.ctx, "settings", None)
            qs = getattr(settings, "_qs", settings)
            selected = load_shared_dirs(qs)
            try:
                selected.add(str(Path(directory).resolve()))
            except OSError:
                selected.add(directory)
            save_shared_dirs(qs, selected)
            svc = self._svc
            if svc is not None and hasattr(svc, "set_shared_project_dirs"):
                svc.set_shared_project_dirs(selected)
        except Exception:  # noqa: BLE001
            pass

    def _add_project_option(self, directory: str) -> bool:
        from app.services.project_tree_service import is_workspace
        from app.services.collab_share_registry import (
            LocalShareProject,
            read_project_id_for_directory,
        )

        raw = str(directory or "").strip()
        if not raw:
            return False
        try:
            resolved = str(Path(raw).resolve())
        except OSError:
            resolved = raw
        if not is_workspace(resolved):
            self._set_project_status("项目状态：请选择已经创建的拍照工作区。")
            return False
        for i in range(self._local_project_combo.count()):
            if str(self._local_project_combo.itemData(i)) == resolved:
                self._local_project_combo.setCurrentIndex(i)
                return True
        project = LocalShareProject(
            directory=resolved,
            name=Path(resolved).name or resolved,
            project_id=read_project_id_for_directory(resolved),
        )
        self._project_options.append(project)
        self._local_project_combo.addItem(project.name, project.directory)
        self._local_project_combo.setCurrentIndex(self._local_project_combo.count() - 1)
        return True

    def _browse_local_project(self) -> None:
        directory = QFileDialog.getExistingDirectory(
            self,
            "选择本机拍照工作区",
            self._selected_project_directory() or str(getattr(self.ctx, "current_project_dir", "") or ""),
        )
        if directory:
            self._add_project_option(directory)

    def _refresh_local_code(self) -> None:
        code = ""
        path_text = "未找到可生成项目码的本机项目。请先打开或新建项目。"
        status_text = "项目状态：未选择本机项目。"
        directory = self._selected_project_directory()
        name = self._selected_project_name()
        if directory:
            path_text = f"本机绝对路径：{directory}"
            try:
                from app.services.project_identity_service import project_sync_code
                from app.services.collab_share_registry import (
                    project_id_for_directory,
                    read_project_id_for_directory,
                )
                existing = read_project_id_for_directory(directory)
                pid = existing or project_id_for_directory(directory)
                code = project_sync_code(pid, project_name=name or Path(directory).name)
                status_text = (
                    f"项目状态：已绑定项目码 {pid[:8]}…"
                    if existing else
                    f"项目状态：已为所选项目生成项目码 {pid[:8]}…"
                )
            except Exception:  # noqa: BLE001
                code = ""
                status_text = "项目状态：无法生成项目码，请确认这是有效拍照工作区。"
        elif self._svc is not None and hasattr(self._svc, "project_sync_code"):
            code = self._svc.project_sync_code()

        self._local_path_label.setText(path_text)
        self._local_code.setText(code)
        self._copy_local_btn.setEnabled(bool(code))
        if hasattr(self, "_apply_btn"):
            self._apply_btn.setEnabled(bool(directory))
        if hasattr(self, "_project_status_label"):
            self._project_status_label.setText(status_text)

    def _apply_join_code(self) -> None:
        directory = self._selected_project_directory()
        if not directory:
            self._set_project_status("项目状态：请先选择本机项目。")
            return
        raw = self._join_code.text().strip()
        try:
            from app.services.project_identity_service import parse_project_sync_code
            parsed = parse_project_sync_code(raw)
        except ValueError:
            self._set_project_status("项目状态：项目码格式不正确。")
            return

        new_project_id = parsed["projectId"]
        try:
            from app.services.collab_share_registry import read_project_id_for_directory
            current_id = read_project_id_for_directory(directory)
        except Exception:  # noqa: BLE001
            current_id = ""
        if new_project_id == current_id:
            self._share_directory(directory)
            self._set_project_status(
                f"项目状态：所选项目已连接到 {new_project_id[:8]}…，可把同一个项目码给更多电脑使用。"
            )
            return

        remote_name = _project_display(parsed.get("projectName", "")) or "队友项目"
        self._set_project_status(
            f"项目状态：正在把本机项目「{Path(directory).name}」连接到“{remote_name}”…"
        )
        try:
            from app.services.collab_share_registry import apply_project_sync_code_to_directory
            applied_id = apply_project_sync_code_to_directory(directory, raw)
            svc = self._svc
            service_dir = str(getattr(svc, "_project_dir", "") or "") if svc is not None else ""
            try:
                same_as_service = bool(service_dir) and Path(service_dir).resolve() == Path(directory).resolve()
            except OSError:
                same_as_service = service_dir == directory
            if same_as_service and svc is not None:
                svc._project_id = applied_id
        except Exception as exc:  # noqa: BLE001
            self._set_project_status(f"项目状态：写入项目码失败：{exc}")
            return
        self._share_directory(directory)
        self._refresh_local_code()
        self._set_project_status(
            f"项目状态：已连接到队友项目码 {applied_id[:8]}…；第 3、4 台电脑可重复粘贴同一个码。"
        )
        self.applied.emit()


# ── Share address dialog ──────────────────────────────────────────────────────

class _CollabShareDialog(QDialog):
    """Dialog showing the local LAN address for sharing with other operators."""

    def __init__(self, ctx: "AppContext", parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("本机局域网连接地址")
        self.setMinimumWidth(340)

        addr = ""
        svc = getattr(ctx, "collab_service", None)
        if svc is not None and hasattr(svc, "local_address"):
            addr = svc.local_address() or ""

        layout = QVBoxLayout(self)
        layout.setSpacing(10)
        note = QLabel(
            "仅限同一局域网或可信 VPN 使用。不要在路由器或防火墙上向互联网开放此端口。"
        )
        note.setWordWrap(True)
        layout.addWidget(note)
        layout.addWidget(QLabel("把这个局域网地址发给队友，用于手动连接本机："))

        self._addr_edit = QLineEdit(addr)
        self._addr_edit.setReadOnly(True)
        layout.addWidget(self._addr_edit)

        copy_btn = QPushButton("复制本机局域网连接地址")
        copy_btn.clicked.connect(lambda: QApplication.clipboard().setText(addr))
        layout.addWidget(copy_btn)

        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(self.close)
        layout.addWidget(close_btn)
