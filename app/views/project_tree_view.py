"""project_tree_view.py — 项目树（新增，计划 glittery-riding-oasis 步骤 3）.

把「项目」当成一棵文件夹树来管理：选一个调查根目录（如 雷州半岛多样性/），
软件展示其下任意层子文件夹（断面a/b/c、厦门/漳州…），任一节点都可「进入工作区」拍照。
已含 _data/project.db 的子文件夹按原样认领；空文件夹进入时由工作区按需补建。

不破坏现有「项目总览」页——这是一个独立的新页。地区/样地/人员沿这棵树向上继承
（见 project_settings_service.get_effective），进入节点时把根记到 ctx.current_project_root。
"""
from __future__ import annotations

from pathlib import Path
import sqlite3
from typing import TYPE_CHECKING, Optional

from PyQt6.QtCore import QItemSelectionModel, QSize, Qt, pyqtSignal
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMenu,
    QPushButton,
    QProgressDialog,
    QGridLayout,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QSplitter,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
    QToolButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.config import icons
from app.config import project_tree_layout as ptl
from app.config.theme import local_font_css
from app.services import project_tree_service as pts
from app.utils import ui
from app.utils.tooltip_policy import suppress_popup_tooltip
from app.views.base_view import BaseView

if TYPE_CHECKING:
    from app.app_context import AppContext

_PATH_ROLE = Qt.ItemDataRole.UserRole
_KIND_ROLE = Qt.ItemDataRole.UserRole.value + 1


def _theme():
    try:
        from app.config.theme import TOKENS
        return TOKENS.get
    except Exception:  # pragma: no cover
        return lambda k, d=None: d


def _read_summary_visible_column_keys(settings) -> list[str] | None:
    """Safe read for tests using minimal FakeSettings stubs."""
    if not hasattr(settings, "project_tree_summary_visible_columns"):
        return None
    try:
        raw = settings.project_tree_summary_visible_columns
    except Exception:
        return None
    return list(raw) if raw else None


def _write_summary_visible_column_keys(settings, keys: list[str]) -> None:
    if hasattr(settings, "project_tree_summary_visible_columns"):
        settings.project_tree_summary_visible_columns = list(keys or [])


class ProjectTreeView(BaseView):
    """项目树 — 浏览/新建子文件夹，进入任一节点作为拍照工作区."""

    view_id = "project_tree"
    nav_title = "项目树"
    nav_icon = "🌲"

    enter_workspace_requested = pyqtSignal(str)  # carries the chosen node dir

    def __init__(self, ctx: "AppContext") -> None:
        self._root: Optional[str] = None
        self._kind_filter = "all"
        self._kind_filter_buttons: dict[str, QPushButton] = {}
        self._grid_cols_buttons: dict[int, QPushButton] = {}
        super().__init__(ctx)

    # ── UI ──────────────────────────────────────────────────────────────────
    def _setup_ui(self) -> None:
        self._apply_style()
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 8, 12, 10)
        root.setSpacing(8)

        # Header bar: current root context plus the directory-management actions.
        header = QFrame()
        header.setObjectName("ProjectTreeHeader")
        bar = QHBoxLayout(header)
        bar.setContentsMargins(10, 6, 10, 6)
        bar.setSpacing(8)
        title = QLabel("项目树")
        title.setObjectName("ProjectTreeTitle")
        bar.addWidget(title)
        self._root_lbl = QLabel("（未选根目录）")
        self._root_lbl.setObjectName("ProjectTreeRoot")
        self._root_lbl.setAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        )
        self._root_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._root_lbl.setSizePolicy(
            QSizePolicy.Policy.Maximum,
            QSizePolicy.Policy.Preferred,
        )
        self._root_lbl.setMaximumWidth(360)
        bar.addWidget(self._root_lbl)
        bar.addStretch(1)

        mode_row = QHBoxLayout()
        mode_row.setSpacing(6)
        self._btn_mode_all = QPushButton("全部项目")
        self._btn_mode_all.setObjectName("FilterChip")
        self._btn_mode_all.setCheckable(True)
        self._btn_mode_all.setFixedHeight(32)
        self._btn_mode_all.setToolTip("显示所有已登记项目（不受根目录限制）")
        self._btn_mode_all.clicked.connect(lambda: self._set_view_mode("all"))
        mode_row.addWidget(self._btn_mode_all)
        self._btn_mode_rooted = QPushButton("按根目录")
        self._btn_mode_rooted.setObjectName("FilterChip")
        self._btn_mode_rooted.setCheckable(True)
        self._btn_mode_rooted.setFixedHeight(32)
        self._btn_mode_rooted.setToolTip("只浏览某个调查根目录下的文件夹树")
        self._btn_mode_rooted.clicked.connect(lambda: self._set_view_mode("rooted"))
        mode_row.addWidget(self._btn_mode_rooted)
        self._mode_row_host = QWidget()
        self._mode_row_host.setLayout(mode_row)
        bar.addWidget(self._mode_row_host)

        layout_row = QHBoxLayout()
        layout_row.setSpacing(6)
        self._btn_layout_tree = QPushButton("树视图")
        self._btn_layout_tree.setObjectName("FilterChip")
        self._btn_layout_tree.setCheckable(True)
        self._btn_layout_tree.setChecked(True)
        self._btn_layout_tree.setFixedHeight(32)
        self._btn_layout_tree.setToolTip("左树 + 中网格 + 右详情（Lightroom 式）")
        self._btn_layout_tree.clicked.connect(lambda: self._set_layout_mode("tree"))
        layout_row.addWidget(self._btn_layout_tree)
        self._btn_layout_cards = QPushButton("卡片")
        self._btn_layout_cards.setObjectName("FilterChip")
        self._btn_layout_cards.setCheckable(True)
        self._btn_layout_cards.setFixedHeight(32)
        self._btn_layout_cards.setToolTip("全部已录项目卡片（不受根目录限制）")
        self._btn_layout_cards.clicked.connect(lambda: self._set_layout_mode("cards"))
        layout_row.addWidget(self._btn_layout_cards)
        bar.addLayout(layout_row)

        self._header_search = QLineEdit()
        self._header_search.setObjectName("ProjectTreeSearch")
        self._header_search.setPlaceholderText("搜索项目…")
        self._header_search.setClearButtonEnabled(True)
        self._header_search.setFixedWidth(200)
        self._header_search.setFixedHeight(32)
        self._header_search.setToolTip("树视图与卡片视图共用")
        self._header_search.textChanged.connect(self._on_shared_search_changed)
        self._header_search.returnPressed.connect(self._enter_selected)
        bar.addWidget(self._header_search)

        self._btn_pick = QPushButton("选择根目录…")
        self._btn_pick.setObjectName("Outline")
        self._btn_pick.setToolTip("选择调查根目录，在「按根目录」模式下浏览其子树")
        self._btn_pick.setFixedHeight(34)
        self._btn_pick.setCursor(Qt.CursorShape.PointingHandCursor)
        icons.set_button_icon(self._btn_pick, "mdi6.folder-open-outline",
                              color=icons.TONE_MUTED, size=15)
        self._btn_pick.clicked.connect(self._pick_root)
        bar.addWidget(self._btn_pick)

        self._btn_refresh = QPushButton("刷新")
        self._btn_refresh.setObjectName("Outline")
        self._btn_refresh.setFixedHeight(34)
        self._btn_refresh.setToolTip("重新扫描当前列表")
        self._btn_refresh.setCursor(Qt.CursorShape.PointingHandCursor)
        icons.set_button_icon(self._btn_refresh, "mdi6.refresh", color=icons.TONE_MUTED, size=15)
        self._btn_refresh.clicked.connect(self._reload_project_tree)
        bar.addWidget(self._btn_refresh)

        self._more_btn = QToolButton()
        self._more_btn.setObjectName("Outline")
        self._more_btn.setText("⋯")
        self._more_btn.setToolTip("更多操作")
        self._more_btn.setFixedSize(34, 34)
        self._more_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self._more_menu = QMenu(self._more_btn)
        self._act_view_all = self._more_menu.addAction("浏览：全部项目")
        self._act_view_all.triggered.connect(lambda: self._set_view_mode("all"))
        self._act_view_rooted = self._more_menu.addAction("浏览：按根目录")
        self._act_view_rooted.triggered.connect(lambda: self._set_view_mode("rooted"))
        self._act_pick_root = self._more_menu.addAction("选择根目录…")
        self._act_pick_root.triggered.connect(self._pick_root)
        self._more_menu.addSeparator()
        self._act_new_region = self._more_menu.addAction("新建调查区域…")
        self._act_new_region.triggered.connect(self._new_region)
        self._act_scan = self._more_menu.addAction("扫描磁盘…")
        self._act_scan.triggered.connect(self._scan_disk)
        self._act_add_ws = self._more_menu.addAction("添加工作区…")
        self._act_add_ws.triggered.connect(self._add_workspace_manual)
        self._act_refresh_index = self._more_menu.addAction("刷新汇总索引…")
        self._act_refresh_index.setToolTip(
            "更新调查根库中的 workspace_index_cache（加速后续跨断面汇总）"
        )
        self._act_refresh_index.triggered.connect(self._refresh_index_cache_manual)
        self._more_menu.addSeparator()
        self._act_newsub = self._more_menu.addAction("新建断面/子节点")
        self._act_newsub.triggered.connect(self._new_subfolder)
        self._more_btn.setMenu(self._more_menu)
        bar.addWidget(self._more_btn)
        root.addWidget(header)

        # 首次引导（可关闭，设置持久化）
        self._tip_bar = QFrame()
        self._tip_bar.setObjectName("ProjectTreeTipBar")
        tip_lay = QHBoxLayout(self._tip_bar)
        tip_lay.setContentsMargins(12, 4, 8, 4)
        tip_lay.setSpacing(8)
        tip_text = QLabel(
            "提示：树视图多选看汇总 · 卡片 Ctrl 多选后点「查看汇总」· 右键可设封面 · Esc 清除选择"
        )
        tip_text.setObjectName("MutedSmall")
        tip_text.setWordWrap(True)
        tip_lay.addWidget(tip_text, 1)
        self._btn_tip_dismiss = QPushButton("知道了")
        self._btn_tip_dismiss.setObjectName("Ghost")
        self._btn_tip_dismiss.setFixedHeight(24)
        self._btn_tip_dismiss.clicked.connect(self._dismiss_tip_bar)
        tip_lay.addWidget(self._btn_tip_dismiss)
        root.addWidget(self._tip_bar)

        # Body: 树三栏 | 卡片视图
        self._body_stack = QStackedWidget()

        # ── 树视图页：左树 | 中网格 | 右详情 ──
        tree_page = QWidget()
        tree_page_lay = QVBoxLayout(tree_page)
        tree_page_lay.setContentsMargins(0, 0, 0, 0)
        split = QSplitter(Qt.Orientation.Horizontal)
        split.setObjectName("ProjectTreeSplitter")
        split.setChildrenCollapsible(False)
        split.setHandleWidth(14)
        suppress_popup_tooltip(split)
        split.splitterMoved.connect(self._save_tree_split_state)

        tree_panel = QFrame()
        tree_panel.setObjectName("ProjectTreePanel")
        tl = QVBoxLayout(tree_panel)
        tl.setContentsMargins(10, 8, 10, 10)
        tl.setSpacing(4)
        tree_head = QHBoxLayout()
        tree_head.setSpacing(6)
        tree_title = QLabel("目录结构")
        tree_title.setObjectName("Section")
        tree_head.addWidget(tree_title)
        self._tree_count_lbl = QLabel("0 个节点")
        self._tree_count_lbl.setObjectName("MutedSmall")
        tree_head.addWidget(self._tree_count_lbl)
        tree_head.addSpacing(4)
        self._tree_metrics_inline = QLabel("0 区域 · 0 工作区 · 0 待导入")
        self._tree_metrics_inline.setObjectName("TreeMetricsInline")
        tree_head.addWidget(self._tree_metrics_inline, 1)
        tl.addLayout(tree_head)
        self._metric_regions = None
        self._metric_workspaces = None
        self._metric_candidates = None
        self._search = QLineEdit()
        self._search.setObjectName("ProjectTreeSearch")
        self._search.setPlaceholderText("搜索节点或路径")
        self._search.setClearButtonEnabled(True)
        self._search.textChanged.connect(self._on_tree_panel_search_changed)
        self._search.returnPressed.connect(self._enter_selected)
        tl.addWidget(self._search)
        filter_row = QHBoxLayout()
        filter_row.setSpacing(6)
        for key, label in (
            ("all", "全部"),
            ("workspace", "工作区"),
            ("folder", "区域"),
            ("candidate", "待导入"),
        ):
            chip = QPushButton(label)
            chip.setObjectName("FilterChip")
            chip.setCheckable(True)
            chip.setFixedHeight(26)
            chip.setCursor(Qt.CursorShape.PointingHandCursor)
            if key == "all":
                chip.setToolTip("显示全部类型节点（不会自动全选）")
            chip.clicked.connect(lambda _checked=False, k=key: self._set_kind_filter(k))
            self._kind_filter_buttons[key] = chip
            filter_row.addWidget(chip)
        self._btn_select_all_ws = QPushButton("全选工作区")
        self._btn_select_all_ws.setObjectName("Outline")
        self._btn_select_all_ws.setFixedHeight(26)
        self._btn_select_all_ws.setToolTip(
            "选中当前列表中全部工作区（Ctrl/Shift 也可多选部分项目）"
        )
        self._btn_select_all_ws.clicked.connect(self._select_all_visible_workspaces)
        filter_row.addWidget(self._btn_select_all_ws)
        self._kind_filter_buttons["all"].setChecked(True)
        tl.addLayout(filter_row)
        select_hint = QLabel(
            "① 全选或多选工作区  ② 右侧看调查概览  ③ 中间自动显示编号与照片"
        )
        select_hint.setObjectName("MutedSmall")
        select_hint.setWordWrap(True)
        self._select_hint = select_hint
        tl.addWidget(select_hint)
        self._scope_status_lbl = QLabel("")
        self._scope_status_lbl.setObjectName("MutedSmall")
        self._scope_status_lbl.setWordWrap(True)
        tl.addWidget(self._scope_status_lbl)
        self._tree = QTreeWidget()
        self._tree.setObjectName("ProjectDirectoryTree")
        self._tree.setHeaderHidden(True)
        self._tree.setAlternatingRowColors(False)
        self._tree.setAnimated(True)
        self._tree.setIndentation(22)
        self._tree.setIconSize(QSize(14, 14))
        self._tree.setUniformRowHeights(True)
        self._tree.setRootIsDecorated(False)
        # T5 survey-summary (spec §2): 树改多选,Ctrl/Shift 多选断面做汇总.
        self._tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        # self._tree.itemSelectionChanged.connect(self._update_detail_panel_for_selected_project)  # §7 旧单选槽,保留;多选改由 _on_tree_selection_changed 派发
        self._tree.itemSelectionChanged.connect(self._on_tree_selection_changed)
        # §7 旧: 双击直接 _enter_selected, 多选态下误跳拍照界面(打断多断面预览).
        # self._tree.itemDoubleClicked.connect(lambda *_: self._enter_selected())
        # 新: _on_tree_double_clicked 在 ≥2 选中时保持预览, 不进入.
        self._tree.itemDoubleClicked.connect(self._on_tree_double_clicked)
        self._tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._tree.customContextMenuRequested.connect(self._show_tree_context_menu)
        tl.addWidget(self._tree, 1)
        tree_panel.setMinimumWidth(ptl.SPLIT_MIN_TREE)

        detail = QFrame()
        detail.setObjectName("ProjectTreeDetail")
        dl = QVBoxLayout(detail)
        dl.setContentsMargins(18, 16, 18, 16)
        dl.setSpacing(12)
        detail_head = QHBoxLayout()
        detail_head.setSpacing(8)
        current_lbl = QLabel("当前节点")
        current_lbl.setObjectName("Section")
        detail_head.addWidget(current_lbl)
        detail_head.addStretch()
        self._detail_kind = QLabel("未选择")
        self._detail_kind.setObjectName("NodeBadge")
        detail_head.addWidget(self._detail_kind)
        dl.addLayout(detail_head)
        self._detail_name = QLabel("选择左侧文件夹查看详情")
        self._detail_name.setObjectName("ProjectTreeNodeTitle")
        dl.addWidget(self._detail_name)
        self._detail_path = QLabel("")
        self._detail_path.setObjectName("ProjectTreePath")
        self._detail_path.setWordWrap(True)
        self._detail_path.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        path_row = QHBoxLayout()
        path_row.setSpacing(8)
        path_row.addWidget(self._detail_path, 1)
        self._btn_open_dir = QPushButton()
        self._btn_open_dir.setObjectName("IconAction")
        self._btn_open_dir.setFixedSize(32, 32)
        self._btn_open_dir.setToolTip("打开文件夹")
        self._btn_open_dir.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_open_dir.clicked.connect(lambda: self._open_selected_directory())
        icons.set_button_icon(self._btn_open_dir, "mdi6.folder-open-outline",
                              color=icons.TONE_MUTED, size=15)
        self._btn_open_dir.setEnabled(False)
        path_row.addWidget(self._btn_open_dir)
        self._btn_copy_path = QPushButton()
        self._btn_copy_path.setObjectName("IconAction")
        self._btn_copy_path.setFixedSize(32, 32)
        self._btn_copy_path.setToolTip("复制路径")
        self._btn_copy_path.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_copy_path.clicked.connect(lambda: self._copy_selected_path())
        icons.set_button_icon(self._btn_copy_path, "mdi6.content-copy",
                              color=icons.TONE_MUTED, size=15)
        self._btn_copy_path.setEnabled(False)
        path_row.addWidget(self._btn_copy_path)
        dl.addLayout(path_row)
        self._empty_state = QLabel(
            "还没有选择调查根目录。\n\n"
            "选择根目录：读取已有文件夹树，不改动原文件。\n"
            "新建调查区域：创建一个区域根目录，后续断面会继承区域设置。"
        )
        self._empty_state.setObjectName("EmptyState")
        self._empty_state.setWordWrap(True)
        dl.addWidget(self._empty_state)
        self._stats_row = QHBoxLayout()
        self._stats_row.setSpacing(10)
        dl.addLayout(self._stats_row)
        self._media_block = QFrame()
        self._media_block.setObjectName("MediaPreviewBlock")
        media_l = QVBoxLayout(self._media_block)
        media_l.setContentsMargins(12, 10, 12, 10)
        media_l.setSpacing(8)
        media_head = QHBoxLayout()
        media_head.setSpacing(8)
        media_title = QLabel("最近影像")
        media_title.setObjectName("Section")
        media_head.addWidget(media_title)
        self._media_count_lbl = QLabel("")
        self._media_count_lbl.setObjectName("MutedSmall")
        self._media_count_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        media_head.addWidget(self._media_count_lbl, 1)
        media_l.addLayout(media_head)
        self._media_grid = QGridLayout()
        self._media_grid.setHorizontalSpacing(8)
        self._media_grid.setVerticalSpacing(8)
        media_l.addLayout(self._media_grid)
        self._media_empty_lbl = QLabel("当前节点还没有可预览影像。")
        self._media_empty_lbl.setObjectName("MediaEmpty")
        self._media_empty_lbl.setWordWrap(True)
        media_l.addWidget(self._media_empty_lbl)
        self._media_block.hide()
        dl.addWidget(self._media_block)
        self._info_block = QFrame()
        self._info_block.setObjectName("ProjectInfoBlock")
        info_l = QVBoxLayout(self._info_block)
        info_l.setContentsMargins(12, 10, 12, 10)
        info_l.setSpacing(8)
        self._info_type = self._add_info_row(info_l, "类型")
        self._info_status = self._add_info_row(info_l, "状态")
        self._info_children = self._add_info_row(info_l, "下级")
        self._info_block.hide()
        dl.addWidget(self._info_block)
        self._child_block = QFrame()
        self._child_block.setObjectName("ChildPreviewBlock")
        child_l = QVBoxLayout(self._child_block)
        child_l.setContentsMargins(12, 10, 12, 10)
        child_l.setSpacing(8)
        child_head = QHBoxLayout()
        child_head.setSpacing(8)
        child_title = QLabel("下级节点")
        child_title.setObjectName("Section")
        child_head.addWidget(child_title)
        self._child_count_lbl = QLabel("")
        self._child_count_lbl.setObjectName("MutedSmall")
        self._child_count_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        child_head.addWidget(self._child_count_lbl, 1)
        child_l.addLayout(child_head)
        self._child_list = QVBoxLayout()
        self._child_list.setSpacing(6)
        child_l.addLayout(self._child_list)
        self._child_block.hide()
        dl.addWidget(self._child_block)
        action_lbl = QLabel("操作")
        action_lbl.setObjectName("Section")
        dl.addWidget(action_lbl)
        self._btn_enter = QPushButton("进入工作区拍照")
        self._btn_enter.setObjectName("Primary")
        self._btn_enter.setToolTip("把选中文件夹作为当前拍照工作区")
        self._btn_enter.setFixedHeight(38)
        self._btn_enter.setCursor(Qt.CursorShape.PointingHandCursor)
        icons.set_button_icon(self._btn_enter, "mdi6.camera-outline",
                              color=icons.TONE_ON_ACCENT, size=16)
        self._btn_enter.clicked.connect(self._enter_selected)
        self._btn_enter.setEnabled(False)
        dl.addWidget(self._btn_enter)
        self._btn_adopt = QPushButton("认领此文件夹")
        self._btn_adopt.setObjectName("SoftAction")
        self._btn_adopt.setFixedHeight(36)
        self._btn_adopt.setToolTip("只创建 _data/project.db，不改动原始照片")
        self._btn_adopt.hide()
        self._btn_adopt.clicked.connect(self._adopt_selected_candidate)
        dl.addWidget(self._btn_adopt)
        # 精简模式：右栏顶部固定主操作条（概览页也能看到「进入」）
        self._btn_enter_sticky = QPushButton("进入工作区拍照")
        self._btn_enter_sticky.setObjectName("Primary")
        self._btn_enter_sticky.setToolTip("把选中文件夹作为当前拍照工作区")
        self._btn_enter_sticky.setFixedHeight(38)
        self._btn_enter_sticky.setCursor(Qt.CursorShape.PointingHandCursor)
        icons.set_button_icon(
            self._btn_enter_sticky, "mdi6.camera-outline",
            color=icons.TONE_ON_ACCENT, size=16,
        )
        self._btn_enter_sticky.clicked.connect(self._enter_selected)
        self._btn_enter_sticky.setEnabled(False)
        self._btn_enter_sticky.hide()
        self._btn_adopt_sticky = QPushButton("认领此文件夹")
        self._btn_adopt_sticky.setObjectName("SoftAction")
        self._btn_adopt_sticky.setFixedHeight(36)
        self._btn_adopt_sticky.setToolTip("只创建 _data/project.db，不改动原始照片")
        self._btn_adopt_sticky.hide()
        self._btn_adopt_sticky.clicked.connect(self._adopt_selected_candidate)
        tool_grid = QGridLayout()
        tool_grid.setContentsMargins(0, 0, 0, 0)
        tool_grid.setHorizontalSpacing(10)
        tool_grid.setVerticalSpacing(10)
        self._btn_summary = QPushButton("汇总导出…")
        self._btn_summary.setObjectName("Outline")
        self._btn_summary.setToolTip("从选中文件夹向下汇总标本记录并导出")
        self._btn_summary.setMinimumHeight(34)
        self._btn_summary.setCursor(Qt.CursorShape.PointingHandCursor)
        icons.set_button_icon(self._btn_summary, "mdi6.file-export-outline",
                              color=icons.TONE_MUTED, size=15)
        self._btn_summary.setEnabled(False)
        self._btn_summary.clicked.connect(self._open_summary_export)
        tool_grid.addWidget(self._btn_summary, 0, 0)
        self._btn_station_species = QPushButton("分类名录…")
        self._btn_station_species.setObjectName("Outline")
        self._btn_station_species.setToolTip("查看真正的分类名录，并分开展示样品处理概况")
        self._btn_station_species.setMinimumHeight(34)
        self._btn_station_species.setCursor(Qt.CursorShape.PointingHandCursor)
        icons.set_button_icon(self._btn_station_species, "mdi6.format-list-bulleted",
                              color=icons.TONE_MUTED, size=15)
        self._btn_station_species.setEnabled(False)
        self._btn_station_species.clicked.connect(self._open_station_species_summary)
        tool_grid.addWidget(self._btn_station_species, 0, 1)
        self._btn_data_filter = QPushButton("数据筛选…")
        self._btn_data_filter.setObjectName("Outline")
        self._btn_data_filter.setToolTip("跨断面查询标本字段(只读预览, 可解锁编辑)")
        self._btn_data_filter.setMinimumHeight(34)
        self._btn_data_filter.setCursor(Qt.CursorShape.PointingHandCursor)
        icons.set_button_icon(self._btn_data_filter, "mdi6.filter-variant",
                              color=icons.TONE_MUTED, size=15)
        self._btn_data_filter.clicked.connect(self._open_data_filter)
        tool_grid.addWidget(self._btn_data_filter, 1, 0)
        self._btn_station_import = QPushButton("导入站位总表…")
        self._btn_station_import.setObjectName("Outline")
        self._btn_station_import.setToolTip("把站位坐标和采集信息导入选中文件夹")
        self._btn_station_import.setMinimumHeight(34)
        self._btn_station_import.setCursor(Qt.CursorShape.PointingHandCursor)
        icons.set_button_icon(self._btn_station_import, "mdi6.table-arrow-down",
                              color=icons.TONE_MUTED, size=15)
        self._btn_station_import.setEnabled(False)
        self._btn_station_import.clicked.connect(self._open_station_import)
        tool_grid.addWidget(self._btn_station_import, 1, 1)
        dl.addLayout(tool_grid)
        dl.addStretch()
        detail.setMinimumWidth(ptl.SPLIT_MIN_DETAIL)

        # ── 中栏：虚拟化缩略图网格 ──
        self._grid_panel = QFrame()
        self._grid_panel.setObjectName("ProjectTreeGridPanel")
        grid_panel_lay = QVBoxLayout(self._grid_panel)
        grid_panel_lay.setContentsMargins(6, 6, 6, 6)
        grid_panel_lay.setSpacing(6)
        mode_row = QHBoxLayout()
        mode_row.setSpacing(6)
        center_title = QLabel("数据汇总")
        center_title.setObjectName("Section")
        mode_row.addWidget(center_title)
        center_hint = QLabel("统计数字见右侧「调查概览」")
        center_hint.setObjectName("MutedSmall")
        mode_row.addWidget(center_hint, 1)
        self._grid_center_mode_row = QWidget()
        self._grid_center_mode_row.setLayout(mode_row)
        grid_panel_lay.addWidget(self._grid_center_mode_row)
        self._content_mode_buttons: dict[str, QPushButton] = {}
        grid_head_row = QHBoxLayout()
        self._grid_head = QLabel("内容预览")
        self._grid_head.setObjectName("Section")
        grid_head_row.addWidget(self._grid_head)
        grid_head_row.addStretch()
        self._grid_breadcrumb = QLabel("")
        self._grid_breadcrumb.setObjectName("MutedSmall")
        self._grid_breadcrumb.setWordWrap(True)
        grid_head_row.addWidget(self._grid_breadcrumb, 1)
        self._grid_count_lbl = QLabel("")
        self._grid_count_lbl.setObjectName("MutedSmall")
        grid_head_row.addWidget(self._grid_count_lbl)
        grid_panel_lay.addLayout(grid_head_row)
        self._grid_idle_hint = QLabel(
            "请先在左侧选择至少一个工作区。"
            "统计在右侧「调查概览」，编号与照片将显示在本栏。"
        )
        self._grid_idle_hint.setObjectName("MutedSmall")
        self._grid_idle_hint.setWordWrap(True)
        self._grid_idle_hint.setAlignment(
            Qt.AlignmentFlag.AlignCenter
        )
        self._grid_idle_hint.setMinimumHeight(120)
        grid_panel_lay.addWidget(self._grid_idle_hint, 1)
        self._grid_body = QWidget()
        grid_body_lay = QVBoxLayout(self._grid_body)
        grid_body_lay.setContentsMargins(0, 0, 0, 0)
        grid_body_lay.setSpacing(8)
        self._data_summary_panel = QFrame()
        self._data_summary_panel.setObjectName("ProjectTreeDataSummaryPanel")
        ds_lay = QVBoxLayout(self._data_summary_panel)
        ds_lay.setContentsMargins(0, 0, 0, 0)
        ds_lay.setSpacing(6)
        summary_actions = QHBoxLayout()
        summary_actions.setSpacing(6)
        self._btn_toggle_filter = QPushButton("筛选 ▸")
        self._btn_toggle_filter.setObjectName("Ghost")
        self._btn_toggle_filter.setCheckable(True)
        self._btn_toggle_filter.setChecked(False)
        self._btn_toggle_filter.setFixedHeight(26)
        self._btn_toggle_filter.setToolTip("展开/收起筛选区")
        self._btn_toggle_filter.clicked.connect(self._toggle_summary_filter_panel)
        summary_actions.addWidget(self._btn_toggle_filter)
        from app.widgets.specimen_filter_panel import SpecimenFilterPanel
        self._summary_filter = SpecimenFilterPanel()
        self._summary_filter.filter_changed.connect(self._on_summary_filter_changed)
        self._summary_filter.set_body_expanded(False)
        self._btn_toggle_table = QPushButton("编号列表 ▾")
        self._btn_toggle_table.setObjectName("Ghost")
        self._btn_toggle_table.setCheckable(True)
        self._btn_toggle_table.setChecked(True)
        self._btn_toggle_table.setFixedHeight(26)
        self._btn_toggle_table.setToolTip("显示/隐藏上方编号表")
        self._btn_toggle_table.clicked.connect(self._toggle_specimen_table)
        summary_actions.addWidget(self._btn_toggle_table)
        self._btn_toggle_photos = QPushButton("成片 ▾")
        self._btn_toggle_photos.setObjectName("Ghost")
        self._btn_toggle_photos.setCheckable(True)
        self._btn_toggle_photos.setChecked(
            bool(getattr(self.ctx.settings, "project_tree_show_photos", True))
        )
        self._btn_toggle_photos.setFixedHeight(26)
        self._btn_toggle_photos.setToolTip(
            "显示/隐藏下方成片；中间分割条可拖动调整编号表与成片高度"
        )
        self._btn_toggle_photos.clicked.connect(self._toggle_photo_panel)
        summary_actions.addWidget(self._btn_toggle_photos)
        self._summary_stats_lbl = QLabel("")
        self._summary_stats_lbl.setObjectName("MutedSmall")
        summary_actions.addWidget(self._summary_stats_lbl, 1)
        self._btn_summary_more = QToolButton()
        self._btn_summary_more.setObjectName("Outline")
        self._btn_summary_more.setText("操作 ▾")
        self._btn_summary_more.setFixedHeight(28)
        self._btn_summary_more.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self._summary_more_menu = QMenu(self._btn_summary_more)
        self._btn_summary_more.setMenu(self._summary_more_menu)
        act_export = self._summary_more_menu.addAction("导出 CSV")
        act_export.triggered.connect(self._export_filtered_specimens)
        act_tiff = self._summary_more_menu.addAction("转 JPG…")
        act_tiff.triggered.connect(self._open_tiff_jpeg_export_for_selection)
        act_cols = self._summary_more_menu.addAction("显示列…")
        act_cols.triggered.connect(self._open_summary_column_picker)
        act_help = self._summary_more_menu.addAction("表格操作说明…")
        act_help.triggered.connect(self._show_summary_table_help)
        summary_actions.addWidget(self._btn_summary_more)
        self._btn_export_filtered = act_export  # 测试/兼容
        self._btn_tiff_to_jpg = act_tiff
        self._btn_summary_columns = act_cols
        ds_lay.addLayout(summary_actions)
        ds_lay.addWidget(self._summary_filter)
        from app.services.cross_workspace_query_service import (
            DEFAULT_SUMMARY_VISIBLE_KEYS,
            summary_cell_value,
        )

        self._summary_cell_value = summary_cell_value
        self._summary_all_columns: list[tuple[str, str]] = []
        self._summary_visible_columns: list[tuple[str, str]] = [
            (k, k) for k in DEFAULT_SUMMARY_VISIBLE_KEYS
        ]
        self._specimen_table = QTableWidget(0, len(self._summary_visible_columns))
        self._specimen_table.setObjectName("SpecimenSummaryTable")
        # 不用 setToolTip：拖动行/列时 Qt 会弹出大块浮层遮住表格
        suppress_popup_tooltip(self._specimen_table)
        self._rebuild_specimen_table_structure()
        self._specimen_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self._specimen_table.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection
        )
        self._specimen_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._specimen_table.setMinimumHeight(72)
        self._summary_row_uid_order: list[str] | None = None
        self._specimen_table_interaction_ready = False
        self._summary_table_sort_key: str | None = None
        self._summary_table_sort_asc: bool = True
        self._summary_table_column_filters: dict[str, set[str]] = {}
        self._configure_specimen_table_interaction()
        self._specimen_table.itemSelectionChanged.connect(
            self._on_specimen_table_selection_changed
        )
        self._summary_conditions: list = []
        self._current_summary_result = None
        self._current_merged: list = []
        self._current_ws_dirs: list = []
        from app.widgets.uid_grouped_grid import UidGroupedGrid
        self._photo_block = QWidget()
        self._photo_block.setObjectName("ProjectTreePhotoBlock")
        photo_block_lay = QVBoxLayout(self._photo_block)
        photo_block_lay.setContentsMargins(0, 0, 0, 0)
        photo_block_lay.setSpacing(8)
        self._grid_stack = QStackedWidget()
        self._uid_grid = UidGroupedGrid(self._grid_panel)
        self._uid_grid.bind_settings(self.ctx.settings)
        self._uid_grid.photo_selected.connect(self._on_grid_photo_selected)
        self._grid_stack.addWidget(self._uid_grid)
        self._preview_panel = QFrame()
        self._preview_panel.setObjectName("ProjectTreePreviewPanel")
        preview_lay = QVBoxLayout(self._preview_panel)
        preview_lay.setContentsMargins(8, 8, 8, 8)
        preview_head = QHBoxLayout()
        self._preview_title = QLabel("大图预览")
        self._preview_title.setObjectName("Section")
        preview_head.addWidget(self._preview_title)
        preview_head.addStretch()
        self._btn_preview_close = QPushButton("关闭 Esc")
        self._btn_preview_close.setObjectName("Outline")
        self._btn_preview_close.setFixedHeight(28)
        self._btn_preview_close.clicked.connect(self._exit_preview_mode)
        preview_head.addWidget(self._btn_preview_close)
        preview_lay.addLayout(preview_head)
        self._preview_image = QLabel()
        self._preview_image.setObjectName("PreviewImage")
        self._preview_image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._preview_image.setMinimumHeight(ptl.PREVIEW_MIN_HEIGHT)
        self._preview_image.setScaledContents(False)
        preview_lay.addWidget(self._preview_image, 1)
        self._preview_meta = QLabel("")
        self._preview_meta.setObjectName("MutedSmall")
        self._preview_meta.setWordWrap(True)
        preview_lay.addWidget(self._preview_meta)
        self._grid_stack.addWidget(self._preview_panel)
        self._preview_path: Optional[str] = None
        self._preview_mode = False
        from app.widgets.project_tree_uid_index import ProjectTreeUidIndex
        self._grid_inner_split = QSplitter(Qt.Orientation.Horizontal)
        self._grid_inner_split.setObjectName("ProjectTreeGridInnerSplitter")
        self._grid_inner_split.setChildrenCollapsible(False)
        self._grid_inner_split.setHandleWidth(10)
        suppress_popup_tooltip(self._grid_inner_split)
        self._grid_inner_split.splitterMoved.connect(self._save_grid_inner_split_state)
        self._uid_index = ProjectTreeUidIndex(self._grid_inner_split)
        self._uid_index.uid_clicked.connect(self._on_uid_index_clicked)
        self._uid_index.hide()
        self._grid_inner_split.addWidget(self._uid_index)
        self._grid_inner_split.addWidget(self._grid_stack)
        self._grid_inner_split.setStretchFactor(0, 0)
        self._grid_inner_split.setStretchFactor(1, 1)
        self._grid_inner_split.setSizes(
            ptl.compute_grid_inner_split_sizes(ptl.SPLIT_MIN_GRID)
        )
        self._uid_grid.catalog_changed.connect(self._sync_uid_index)
        self._uid_grid.photo_selected.connect(self._on_grid_photo_uid_sync)
        photo_block_lay.addWidget(self._grid_inner_split, 1)
        thumb_row = QHBoxLayout()
        self._density_lbl_widget = QLabel("密度")
        thumb_row.addWidget(self._density_lbl_widget)
        saved_density = self.ctx.settings.project_tree_grid_density
        slider_min, slider_max = ptl.density_slider_range()
        self._density_slider = QSlider(Qt.Orientation.Horizontal)
        self._density_slider.setObjectName("ThumbSizeSlider")
        self._density_slider.setRange(slider_min, slider_max)
        self._density_slider.setValue(saved_density)
        suppress_popup_tooltip(self._density_slider)
        self._density_slider.valueChanged.connect(self._on_density_slider_changed)
        thumb_row.addWidget(self._density_slider, 1)
        self._density_label = QLabel(ptl.density_label(ptl.columns_for_density_index(saved_density)))
        self._density_label.setObjectName("MutedSmall")
        self._density_label.setMinimumWidth(52)
        self._density_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        thumb_row.addWidget(self._density_label)
        thumb_row.addSpacing(8)
        self._sort_lbl_widget = QLabel("排序")
        thumb_row.addWidget(self._sort_lbl_widget)
        self._grid_sort_cmb = QComboBox()
        self._grid_sort_cmb.setObjectName("GridSortCombo")
        self._grid_sort_cmb.setMinimumWidth(108)
        for key, label in ptl.GRID_SORT_MODES:
            self._grid_sort_cmb.addItem(label, key)
        sort_mode = self.ctx.settings.project_tree_grid_sort
        sort_idx = max(0, self._grid_sort_cmb.findData(sort_mode))
        self._grid_sort_cmb.setCurrentIndex(sort_idx)
        self._grid_sort_cmb.currentIndexChanged.connect(self._on_grid_sort_changed)
        thumb_row.addWidget(self._grid_sort_cmb)
        thumb_row.addSpacing(8)
        self._caption_lbl_widget = QLabel("标注")
        thumb_row.addWidget(self._caption_lbl_widget)
        self._grid_caption_cmb = QComboBox()
        self._grid_caption_cmb.setObjectName("GridCaptionCombo")
        self._grid_caption_cmb.setMinimumWidth(96)
        self._grid_caption_cmb.setToolTip(
            "缩略图下方文字。默认「地区-样地-物种」(如 GXFCG-BLW-BZC003)；"
            "同标本多片时智能模式会加 #序号"
        )
        for key, label in ptl.GRID_CAPTION_MODES:
            self._grid_caption_cmb.addItem(label, key)
        caption_mode = self.ctx.settings.project_tree_grid_caption
        cap_idx = max(0, self._grid_caption_cmb.findData(caption_mode))
        self._grid_caption_cmb.setCurrentIndex(cap_idx)
        self._grid_caption_cmb.currentIndexChanged.connect(self._on_grid_caption_changed)
        thumb_row.addWidget(self._grid_caption_cmb)
        self._uid_grid.set_sort_mode(sort_mode)
        self._uid_grid.set_caption_mode(caption_mode)
        self._uid_grid.set_density_index(saved_density)
        self._thumb_row_host = QWidget()
        self._thumb_row_host.setLayout(thumb_row)
        photo_block_lay.addWidget(self._thumb_row_host)
        self._sync_grid_cols_buttons(saved_density)
        quick_row = QHBoxLayout()
        quick_row.setSpacing(6)
        self._grid_cols_lbl = QLabel("网格")
        quick_row.addWidget(self._grid_cols_lbl)
        self._grid_cols_group = QButtonGroup(self)
        self._grid_cols_group.setExclusive(True)
        for cols in ptl.GRID_QUICK_COLUMN_PRESETS:
            btn = QPushButton(str(cols))
            btn.setObjectName("FilterChip")
            btn.setCheckable(True)
            btn.setFixedHeight(26)
            btn.setToolTip(f"每行 {cols} 张")
            btn.clicked.connect(lambda _checked=False, c=cols: self._on_grid_cols_clicked(c))
            self._grid_cols_group.addButton(btn)
            self._grid_cols_buttons[cols] = btn
            quick_row.addWidget(btn)
        quick_row.addStretch(1)
        # 精简模式：显示菜单收纳排序/标注/密度滑块
        self._btn_display = QToolButton()
        self._btn_display.setObjectName("Outline")
        self._btn_display.setText("显示 ▾")
        self._btn_display.setFixedHeight(26)
        self._btn_display.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self._btn_display.setToolTip("排序、标注与密度")
        self._display_menu = QMenu(self._btn_display)
        self._btn_display.setMenu(self._display_menu)
        self._rebuild_display_menu()
        quick_row.addWidget(self._btn_display)
        self._quick_row_host = QWidget()
        self._quick_row_host.setLayout(quick_row)
        photo_block_lay.addWidget(self._quick_row_host)
        self._summary_table_host = QFrame()
        self._summary_table_host.setObjectName("ProjectTreeSummaryTableHost")
        table_host_lay = QVBoxLayout(self._summary_table_host)
        table_host_lay.setContentsMargins(0, 0, 0, 0)
        table_host_lay.setSpacing(0)
        table_host_lay.addWidget(self._specimen_table, 1)
        self._summary_body_split = QSplitter(Qt.Orientation.Vertical)
        self._summary_body_split.setObjectName("ProjectTreeSummaryBodySplit")
        self._summary_body_split.setChildrenCollapsible(False)
        self._summary_body_split.setHandleWidth(14)
        self._summary_body_split.setOpaqueResize(True)
        suppress_popup_tooltip(self._summary_body_split)
        self._summary_body_split.splitterMoved.connect(self._save_summary_body_split_state)
        self._summary_table_host.setMinimumHeight(ptl.SUMMARY_BODY_TABLE_MIN)
        self._photo_block.setMinimumHeight(ptl.SUMMARY_BODY_PHOTO_MIN)
        self._summary_body_split.addWidget(self._summary_table_host)
        self._summary_body_split.addWidget(self._photo_block)
        self._summary_body_split.setStretchFactor(0, 1)
        self._summary_body_split.setStretchFactor(1, 4)
        self._summary_body_split.setSizes(
            ptl.compute_summary_body_split_sizes(ptl.SUMMARY_BODY_DEFAULT_HEIGHT)
        )
        ds_lay.addWidget(self._summary_body_split, 1)
        self._data_summary_panel.hide()
        grid_body_lay.addWidget(self._data_summary_panel, 1)
        self._apply_photo_panel_visibility(
            bool(getattr(self.ctx.settings, "project_tree_show_photos", True))
        )
        grid_panel_lay.addWidget(self._grid_body, 1)
        self._grid_body.hide()
        self._selection_items: list = []
        self._scope_labeled: list[tuple[str, str]] = []
        self._grid_panel.setMinimumWidth(ptl.SPLIT_MIN_GRID)

        self._right_stack = QStackedWidget()
        self._right_stack.addWidget(detail)
        from app.widgets.survey_overview_panel import SurveyOverviewPanel
        self._overview_panel = SurveyOverviewPanel(self.ctx, parent=self)
        self._survey_panel = self._overview_panel.species_panel()
        self._right_stack.addWidget(self._overview_panel)
        self._right_stack.setMinimumWidth(ptl.SPLIT_MIN_DETAIL)

        # 右栏外壳：精简模式下顶部固定「进入/认领」，下方再切详情/概览
        self._right_shell = QFrame()
        self._right_shell.setObjectName("ProjectTreeRightShell")
        right_shell_lay = QVBoxLayout(self._right_shell)
        right_shell_lay.setContentsMargins(0, 0, 0, 0)
        right_shell_lay.setSpacing(0)
        self._right_action_bar = QFrame()
        self._right_action_bar.setObjectName("ProjectTreeRightActionBar")
        rab = QVBoxLayout(self._right_action_bar)
        rab.setContentsMargins(12, 10, 12, 8)
        rab.setSpacing(6)
        rab.addWidget(self._btn_enter_sticky)
        rab.addWidget(self._btn_adopt_sticky)
        self._right_action_bar.hide()
        right_shell_lay.addWidget(self._right_action_bar)
        right_shell_lay.addWidget(self._right_stack, 1)
        self._right_shell.setMinimumWidth(ptl.SPLIT_MIN_DETAIL)

        split.addWidget(tree_panel)
        split.addWidget(self._grid_panel)
        split.addWidget(self._right_shell)
        st = ptl.split_stretch_factors()
        split.setStretchFactor(0, st[0])
        split.setStretchFactor(1, st[1])
        split.setStretchFactor(2, st[2])
        split.setSizes(ptl.compute_split_sizes())
        self._tree_split = split
        tree_page_lay.addWidget(split, 1)
        self._body_stack.addWidget(tree_page)

        from app.widgets.project_card import ProjectCardGrid
        self._card_grid = ProjectCardGrid(self)
        self._card_grid.enter_requested.connect(self._enter_workspace_from_card)
        self._card_grid.adopt_requested.connect(self._adopt_from_card)
        self._card_grid.summarize_requested.connect(self._summarize_from_cards)
        self._card_grid.set_cover_requested.connect(self._set_cover_for_directory)
        self._card_grid.clear_cover_requested.connect(self._clear_cover_for_directory)
        self._card_grid.open_in_tree_requested.connect(self._select_tree_path)
        self._body_stack.addWidget(self._card_grid)

        root.addWidget(self._body_stack, 1)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._apply_ux_profile()
        self._sync_tip_bar_visibility()

    def _ux_v2(self) -> bool:
        return bool(getattr(self.ctx.settings, "project_tree_ux_v2", True))

    def _rebuild_display_menu(self) -> None:
        menu = getattr(self, "_display_menu", None)
        if menu is None:
            return
        menu.clear()
        sort_menu = menu.addMenu("排序")
        for key, label in ptl.GRID_SORT_MODES:
            act = sort_menu.addAction(label)
            act.setCheckable(True)
            act.setChecked(self._grid_sort_cmb.currentData() == key)
            act.triggered.connect(
                lambda _checked=False, k=key: self._set_grid_sort_from_menu(k)
            )
        cap_menu = menu.addMenu("标注")
        for key, label in ptl.GRID_CAPTION_MODES:
            act = cap_menu.addAction(label)
            act.setCheckable(True)
            act.setChecked(self._grid_caption_cmb.currentData() == key)
            act.triggered.connect(
                lambda _checked=False, k=key: self._set_grid_caption_from_menu(k)
            )
        dens_menu = menu.addMenu("密度")
        for cols in (1, 2, 4, 8, 16, 32):
            act = dens_menu.addAction(f"每行 {cols} 张")
            act.triggered.connect(
                lambda _checked=False, c=cols: self._on_grid_cols_clicked(c)
            )

    def _set_grid_sort_from_menu(self, key: str) -> None:
        idx = self._grid_sort_cmb.findData(key)
        if idx >= 0:
            self._grid_sort_cmb.setCurrentIndex(idx)
        self._rebuild_display_menu()

    def _set_grid_caption_from_menu(self, key: str) -> None:
        idx = self._grid_caption_cmb.findData(key)
        if idx >= 0:
            self._grid_caption_cmb.setCurrentIndex(idx)
        self._rebuild_display_menu()

    def _apply_ux_profile(self) -> None:
        """按 settings.project_tree_ux_v2 切换精简/旧版控件可见性（可随时退回）."""
        v2 = self._ux_v2()
        # 顶栏：精简时隐藏「全部/按根」chip 与「选择根目录」按钮（改走 ⋯ 菜单）
        if hasattr(self, "_mode_row_host"):
            self._mode_row_host.setVisible(not v2)
        if hasattr(self, "_btn_pick"):
            self._btn_pick.setVisible(not v2)
        if hasattr(self, "_act_view_all"):
            self._act_view_all.setVisible(v2)
            self._act_view_rooted.setVisible(v2)
            self._act_pick_root.setVisible(v2)
        # 中栏：精简时收起密度滑块行，只留列数 chip +「显示」菜单
        if hasattr(self, "_thumb_row_host"):
            self._thumb_row_host.setVisible(not v2)
        if hasattr(self, "_btn_display"):
            self._btn_display.setVisible(v2)
            if v2:
                self._rebuild_display_menu()
        # 左栏引导：精简时隐藏长说明，只保留状态行
        if hasattr(self, "_select_hint"):
            self._select_hint.setText(
                "多选工作区 → 右侧概览 · 中间编号照片"
                if v2
                else "① 全选或多选工作区  ② 右侧看调查概览  ③ 中间自动显示编号与照片"
            )
            self._select_hint.setVisible(not v2)
        self._apply_compact_header_metrics(v2)
        # 右栏：精简时顶部固定进入/认领；详情页内同名按钮隐藏，避免重复
        if hasattr(self, "_right_action_bar"):
            self._right_action_bar.setVisible(v2)
        if hasattr(self, "_btn_enter"):
            self._btn_enter.setVisible(not v2)
        if hasattr(self, "_btn_adopt"):
            # 认领按钮仍由详情逻辑 show/hide；精简模式改走 sticky
            if v2:
                self._btn_adopt.hide()
            self._sync_sticky_enter_from_primary()
        self._sync_view_mode_buttons()
        self._sync_tip_bar_visibility()

    def _apply_compact_header_metrics(self, v2: bool) -> None:
        """精简模式压矮顶栏按钮，减少无效留白。"""
        h = 28 if v2 else 32
        for btn in (
            getattr(self, "_btn_mode_all", None),
            getattr(self, "_btn_mode_rooted", None),
            getattr(self, "_btn_layout_tree", None),
            getattr(self, "_btn_layout_cards", None),
        ):
            if btn is not None:
                btn.setFixedHeight(h)
        search = getattr(self, "_header_search", None)
        if search is not None:
            search.setFixedHeight(h)
        for btn in (
            getattr(self, "_btn_pick", None),
            getattr(self, "_btn_refresh", None),
        ):
            if btn is not None:
                btn.setFixedHeight(h + 2)
        more = getattr(self, "_more_btn", None)
        if more is not None:
            more.setFixedSize(h + 2, h + 2)

    def _sync_sticky_enter_from_primary(self) -> None:
        sticky = getattr(self, "_btn_enter_sticky", None)
        primary = getattr(self, "_btn_enter", None)
        if sticky is None or primary is None:
            return
        sticky.setEnabled(primary.isEnabled())
        sticky.setText(primary.text())
        sticky.setObjectName(primary.objectName())
        sticky.setVisible(self._ux_v2())
        sticky.style().unpolish(sticky)
        sticky.style().polish(sticky)
        adopt_s = getattr(self, "_btn_adopt_sticky", None)
        adopt = getattr(self, "_btn_adopt", None)
        if adopt_s is not None and adopt is not None:
            if self._ux_v2():
                # 详情页认领钮隐藏；是否显示 sticky 看主按钮文案（候选=「认领后进入」）
                adopt.hide()
                adopt_wanted = "认领" in (primary.text() or "")
            else:
                adopt_wanted = adopt.isVisible()
            adopt_s.setVisible(self._ux_v2() and adopt_wanted)
            adopt_s.setEnabled(True)
            adopt_s.setText(adopt.text() or "认领此文件夹")

    def keyPressEvent(self, event) -> None:  # noqa: D401 - Qt override
        key = event.key()
        mods = event.modifiers()
        cards_mode = (
            hasattr(self, "_body_stack")
            and self._body_stack.currentIndex() == 1
        )
        if cards_mode and hasattr(self, "_card_grid"):
            if (
                key == Qt.Key.Key_A
                and mods & (
                    Qt.KeyboardModifier.ControlModifier
                    | Qt.KeyboardModifier.MetaModifier
                )
            ):
                self._card_grid.select_all()
                event.accept()
                return
            if key == Qt.Key.Key_Escape:
                self._card_grid.clear_selection()
                event.accept()
                return
            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                dirs = self._card_grid.selected_directories()
                if len(dirs) == 1:
                    self._enter_workspace_from_card(dirs[0])
                    event.accept()
                    return
        if (
            not cards_mode
            and key == Qt.Key.Key_Escape
            and not self._preview_mode
        ):
            self._tree.clearSelection()
            self._on_tree_selection_changed()
            event.accept()
            return
        if key == Qt.Key.Key_Space and self._grid_panel.isVisible():
            if self._preview_mode:
                self._exit_preview_mode()
            elif self._preview_path:
                self._enter_preview_mode(self._preview_path)
            event.accept()
            return
        if key == Qt.Key.Key_Escape and self._preview_mode:
            self._exit_preview_mode()
            event.accept()
            return
        super().keyPressEvent(event)

    def _dismiss_tip_bar(self) -> None:
        self.ctx.settings.project_tree_tip_dismissed = True
        self._sync_tip_bar_visibility()

    def _sync_tip_bar_visibility(self) -> None:
        bar = getattr(self, "_tip_bar", None)
        if bar is None:
            return
        dismissed = bool(getattr(self.ctx.settings, "project_tree_tip_dismissed", False))
        bar.setVisible(self._ux_v2() and not dismissed)

    def _on_shared_search_changed(self, text: str) -> None:
        """顶栏搜索：同步到左栏搜索框 + 卡片过滤."""
        tree_search = getattr(self, "_search", None)
        if tree_search is not None and tree_search.text() != text:
            tree_search.blockSignals(True)
            tree_search.setText(text)
            tree_search.blockSignals(False)
        self._filter_tree(text)
        if hasattr(self, "_card_grid"):
            self._card_grid.set_filter_text(text)

    def _on_tree_panel_search_changed(self, text: str) -> None:
        header = getattr(self, "_header_search", None)
        if header is not None and header.text() != text:
            header.blockSignals(True)
            header.setText(text)
            header.blockSignals(False)
        self._filter_tree(text)
        if hasattr(self, "_card_grid"):
            self._card_grid.set_filter_text(text)

    def _on_density_slider_changed(self, value: int) -> None:
        idx = int(value)
        self.ctx.settings.project_tree_grid_density = idx
        cols = ptl.columns_for_density_index(idx)
        self._density_label.setText(ptl.density_label(cols))
        self._uid_grid.set_density_index(idx)
        self._sync_grid_cols_buttons(idx)

    def _on_grid_cols_clicked(self, cols: int) -> None:
        idx = ptl.density_index_for_columns(cols)
        self._sync_density_slider(idx)
        self._uid_grid.set_density_index(idx)
        self._sync_grid_cols_buttons(idx)

    def _sync_grid_cols_buttons(self, density_index: int) -> None:
        cols = ptl.columns_for_density_index(density_index)
        for c, btn in self._grid_cols_buttons.items():
            btn.blockSignals(True)
            btn.setChecked(c == cols)
            btn.blockSignals(False)

    def _on_grid_sort_changed(self, _index: int = 0) -> None:
        mode = self._grid_sort_cmb.currentData()
        if not mode:
            return
        self.ctx.settings.project_tree_grid_sort = str(mode)
        self._uid_grid.set_sort_mode(str(mode))
        if getattr(self, "_current_summary_result", None) is not None:
            selected = self._selected_specimen_uids()
            if selected:
                groups = self._groups_for_summary_display(uid_filter=selected)
            else:
                groups = self._groups_for_summary_display()
            self._apply_summary_groups_to_grid(groups)

    def _on_grid_caption_changed(self, _index: int = 0) -> None:
        mode = self._grid_caption_cmb.currentData()
        if not mode:
            return
        self.ctx.settings.project_tree_grid_caption = str(mode)
        self._uid_grid.set_caption_mode(str(mode))

    def _restore_tree_split_state(self) -> None:
        split = getattr(self, "_tree_split", None)
        if split is None:
            return
        saved = self.ctx.settings.project_tree_split_state
        if saved and split.restoreState(saved):
            return
        split.setSizes(ptl.compute_split_sizes(max(split.width(), ptl.SPLIT_DEFAULT_TOTAL)))

    def _restore_grid_inner_split_state(self) -> None:
        inner = getattr(self, "_grid_inner_split", None)
        if inner is None:
            return
        saved = self.ctx.settings.project_tree_grid_inner_split_state
        if saved and inner.restoreState(saved):
            return
        inner.setSizes(
            ptl.compute_grid_inner_split_sizes(max(inner.width(), ptl.GRID_INNER_PHOTO_MIN))
        )

    def _save_tree_split_state(self) -> None:
        split = getattr(self, "_tree_split", None)
        if split is None:
            return
        self.ctx.settings.project_tree_split_state = split.saveState()

    def _save_grid_inner_split_state(self) -> None:
        inner = getattr(self, "_grid_inner_split", None)
        if inner is None:
            return
        self.ctx.settings.project_tree_grid_inner_split_state = inner.saveState()

    def _restore_summary_body_split_state(self) -> None:
        split = getattr(self, "_summary_body_split", None)
        if split is None:
            return
        photo = getattr(self, "_photo_block", None)
        if photo is not None and not photo.isVisible():
            return
        saved = self.ctx.settings.project_tree_summary_body_split_state
        if saved and split.restoreState(saved):
            return
        total = max(split.height(), ptl.SUMMARY_BODY_DEFAULT_HEIGHT)
        split.setSizes(ptl.compute_summary_body_split_sizes(total))

    def _save_summary_body_split_state(self) -> None:
        split = getattr(self, "_summary_body_split", None)
        photo = getattr(self, "_photo_block", None)
        if split is None or photo is None or not photo.isVisible():
            return
        sizes = split.sizes()
        if len(sizes) >= 2 and sizes[1] <= 8:
            return
        self.ctx.settings.project_tree_summary_body_split_state = split.saveState()

    def _on_grid_photo_selected(self, path: str, item: object) -> None:
        self._preview_path = path
        self._update_photo_metadata(path, item if isinstance(item, dict) else {})
        if self._preview_mode:
            self._show_preview_image(path)

    def _on_grid_photo_uid_sync(self, path: str, item: object) -> None:
        if not isinstance(item, dict):
            return
        uid = str(item.get("_uid") or item.get("uid") or "")
        if uid and getattr(self, "_uid_index", None) is not None:
            self._uid_index.set_current_uid(uid)

    def _sync_uid_index(self) -> None:
        index = getattr(self, "_uid_index", None)
        grid = getattr(self, "_uid_grid", None)
        if index is None or grid is None:
            return
        catalog = grid.uid_catalog()
        index.set_entries(catalog)
        index.setVisible(bool(catalog))

    def _on_uid_index_clicked(self, uid: str) -> None:
        grid = getattr(self, "_uid_grid", None)
        if grid is None:
            return
        if not grid.scroll_to_uid(uid):
            return
        first = grid.first_photo_for_uid(uid)
        if not first:
            return
        path, item = first
        self._preview_path = path
        self._update_photo_metadata(path, item)
        self._enter_preview_mode(path)
        if getattr(self, "_uid_index", None) is not None:
            self._uid_index.set_current_uid(uid)

    def _update_photo_metadata(self, path: str, item: dict) -> None:
        name = Path(path).name
        uid = str(item.get("uid") or "")
        lines = [f"文件：{name}"]
        if uid:
            lines.append(f"编号：{uid}")
        specimen = self._lookup_specimen_for_photo(path, uid)
        if specimen:
            sci = specimen.get("scientific_name") or specimen.get("scientificName")
            if sci:
                lines.append(f"学名：{sci}")
            station = specimen.get("station")
            if station:
                lines.append(f"站位：{station}")
            collector = specimen.get("collector")
            if collector:
                lines.append(f"采集人：{collector}")
        self._info_status.setText("\n".join(lines))
        self._preview_meta.setText("\n".join(lines))

    def _lookup_specimen_for_photo(self, path: str, uid: str) -> Optional[dict]:
        ws = self._selected_path()
        if not ws:
            return None
        try:
            from app.db.db_manager import open_project_db
            conn = open_project_db(ws, create=False)
        except Exception:
            return None
        if uid:
            row = conn.execute(
                "SELECT * FROM specimens WHERE uid = ? LIMIT 1", (uid,)
            ).fetchone()
            if row:
                return dict(row)
        return None

    def _enter_preview_mode(self, path: str) -> None:
        self._preview_mode = True
        self._show_preview_image(path)
        self._grid_stack.setCurrentIndex(1)

    def _exit_preview_mode(self) -> None:
        self._preview_mode = False
        self._grid_stack.setCurrentIndex(0)

    def _show_preview_image(self, path: str) -> None:
        from app.utils.image_thumbnail import decode_image_thumbnail

        pm = decode_image_thumbnail(path, max_size=ptl.PREVIEW_DECODE_MAX)
        self._preview_image.setScaledContents(False)
        if pm is not None and not pm.isNull():
            QApplication.processEvents()
            w = max(ptl.PREVIEW_MIN_WIDTH, self._preview_image.width())
            h = max(ptl.PREVIEW_MIN_HEIGHT, self._preview_image.height())
            scaled = pm.scaled(
                w,
                h,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            self._preview_image.setPixmap(scaled)
            self._preview_image.setText("")
        else:
            self._preview_image.clear()
            self._preview_image.setText("无法预览此文件")
        self._preview_title.setText(Path(path).name)

    def _fit_grid_thumb_to_panel(self) -> None:
        """Keep current density preset aligned with viewport width."""
        if not getattr(self, "_grid_panel", None) or not self._grid_panel.isVisible():
            return
        QApplication.processEvents()
        idx = self.ctx.settings.project_tree_grid_density
        fit = ptl.thumb_size_for_density(self._uid_grid._viewport_width(), idx)
        if abs(fit - self._uid_grid.thumb_size()) <= 4:
            return
        self._uid_grid.set_density_index(idx)
        self._sync_density_slider(idx)

    def _sync_density_slider(self, index: int) -> None:
        idx = ptl.clamp_density_index(index)
        self._density_slider.blockSignals(True)
        self._density_slider.setValue(idx)
        self._density_slider.blockSignals(False)
        self._density_label.setText(
            ptl.density_label(ptl.columns_for_density_index(idx))
        )
        self.ctx.settings.project_tree_grid_density = idx

    def _prepare_grid_panel(self, path: Optional[str] = None) -> None:
        self._grid_panel.setVisible(True)
        self._show_grid_content()
        self._set_grid_breadcrumb(path)
        self._media_block.hide()
        self._child_block.hide()

    def _present_grid_panel(self, path: Optional[str] = None) -> None:
        self._prepare_grid_panel(path)
        self._fit_grid_thumb_to_panel()

    def _set_grid_breadcrumb(self, path: Optional[str]) -> None:
        if not path:
            self._grid_breadcrumb.setText("")
            return
        parts = Path(path).parts
        self._grid_breadcrumb.setText(" / ".join(parts[-4:]))

    def _add_tree_metric(self, layout: QHBoxLayout, label: str) -> QLabel:
        box = QFrame()
        box.setObjectName("TreeMetric")
        bl = QHBoxLayout(box)
        bl.setContentsMargins(6, 2, 6, 2)
        bl.setSpacing(4)
        value = QLabel("0")
        value.setObjectName("TreeMetricValue")
        value.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        caption = QLabel(label)
        caption.setObjectName("TreeMetricLabel")
        caption.setAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        )
        bl.addStretch(1)
        bl.addWidget(value)
        bl.addWidget(caption)
        bl.addStretch(1)
        layout.addWidget(box, 1)
        return value

    def _add_info_row(self, layout: QVBoxLayout, label: str) -> QLabel:
        row = QHBoxLayout()
        row.setSpacing(10)
        key = QLabel(label)
        key.setObjectName("InfoKey")
        key.setFixedWidth(52)
        value = QLabel("—")
        value.setObjectName("InfoValue")
        value.setWordWrap(True)
        row.addWidget(key)
        row.addWidget(value, 1)
        layout.addLayout(row)
        return value

    def _apply_style(self) -> None:
        g = _theme()
        bg = g("bg", "#f4f6f8")
        panel = g("panel", "#ffffff")
        panel_2 = g("panel_2", "#f8fafc")
        panel_inset = g("panel_inset", "#eef2f6")
        border = g("border", "rgba(15,23,42,0.08)")
        text = g("text", "#17212b")
        text_soft = g("text_soft", "#334155")
        muted = g("muted", "#64748b")
        muted_dim = g("muted_dim", "#94a3b8")
        accent = g("accent", "#0f766e")
        accent_top = g("accent_top", accent)
        accent_bottom = g("accent_bottom", accent)
        accent_hover = g("accent_hover", accent)
        accent_soft = g("accent_soft", "rgba(15,118,110,0.11)")
        accent_softer = g("accent_softer", "rgba(15,118,110,0.06)")
        edge = g("edge_highlight", "rgba(255,255,255,0.65)")
        border_medium = g("border_medium", border)
        border_strong = g("border_strong", border_medium)
        accent_fg = g("accent_fg", g("bg", "#ffffff"))
        success = g("success", "#15803d")
        success_soft = g("success_soft", "rgba(21,128,61,0.11)")
        warn = g("warn", "#b45309")
        warn_soft = g("warn_soft", "rgba(180,83,9,0.11)")
        _ff = local_font_css()
        self.setStyleSheet(
            f"#{self.view_id}{{{_ff}background:{bg};}}"
            f"QFrame#ProjectTreeHeader,QFrame#ProjectTreePanel,QFrame#ProjectTreeDetail,"
            f"QFrame#ProjectTreeRightShell{{"
            f"background:{panel};border:1px solid {border};border-top:1px solid {edge};"
            f"border-radius:12px;}}"
            f"QFrame#ProjectTreeRightActionBar{{background:{panel_2};"
            f"border:0;border-bottom:1px solid {border};border-radius:0;}}"
            f"QFrame#ProjectTreeRightShell > QStackedWidget{{border:0;background:transparent;}}"
            f"QFrame#ProjectTreeRightShell QFrame#ProjectTreeDetail{{"
            f"border:0;border-radius:0;background:transparent;}}"
            f"QFrame#ProjectCardGrid{{background:transparent;border:0;}}"
            f"QFrame#ProjectCard{{background:{panel};border:1px solid {border};"
            f"border-top:1px solid {edge};border-radius:12px;}}"
            f"QFrame#ProjectCard:hover{{border-color:{border_medium};background:{panel};}}"
            f"QFrame#ProjectCard[selected='true']{{border:2px solid {accent};"
            f"background:{accent_softer};}}"
            f"QLabel#ProjectCardCover{{background:{panel_inset};border:1px solid {border};"
            f"border-radius:8px;}}"
            f"QLabel#ProjectCardTitle{{color:{text};font-size:14px;font-weight:800;}}"
            f"QLabel#ProjectCardStats{{color:{muted};font-size:11px;}}"
            f"QLabel#ProjectCardCheck{{color:{accent};font-size:14px;font-weight:800;}}"
            f"QLabel#ProjectCardBadge{{color:{accent_fg};background:{accent};"
            f"border-radius:999px;padding:2px 7px;font-size:10px;font-weight:800;}}"
            f"QFrame#ProjectTreeTipBar{{background:{accent_softer};border:1px solid {accent_soft};"
            f"border-radius:8px;}}"
            f"QPushButton#Ghost{{background:transparent;color:{muted};border:0;"
            f"padding:4px 8px;font-size:11px;font-weight:700;}}"
            f"QPushButton#Ghost:hover{{color:{accent};}}"
            f"QLabel{{color:{text};background:transparent;}}"
            f"QLabel#ProjectTreeTitle{{color:{text};font-weight:800;font-size:16px;}}"
            f"QLabel#ProjectTreeRoot{{color:{muted};font-size:11px;padding-left:4px;}}"
            f"QFrame#ProjectTreeHeader{{background:{panel_2};border:1px solid {border};"
            f"border-radius:8px;max-height:44px;}}"
            f"QLabel#TreeMetricsInline{{color:{muted_dim};font-size:11px;}}"
            f"QLabel#Section{{color:{muted};font-size:11px;font-weight:800;letter-spacing:0.08em;}}"
            f"QLabel#MutedSmall{{color:{muted_dim};font-size:11px;}}"
            f"QLabel#ProjectTreeNodeTitle{{color:{text};font-weight:800;font-size:20px;}}"
            f"QLabel#ProjectTreePath{{color:{muted};background:{panel_2};"
            f"border:1px solid {border};border-radius:7px;padding:7px 10px;"
            f"font-family:monospace;font-size:11px;}}"
            f"QLabel#NodeBadge{{color:{accent};background:{accent_soft};"
            f"border:1px solid {accent_soft};border-radius:999px;padding:4px 11px;"
            f"font-size:11px;font-weight:700;}}"
            f"QFrame#ProjectTreeMetrics{{background:{panel_2};border:1px solid {border};"
            f"border-radius:8px;}}"
            f"QFrame#TreeMetric{{background:transparent;border:0;}}"
            f"QLabel#TreeMetricValue{{color:{text};font-size:15px;font-weight:800;}}"
            f"QLabel#TreeMetricLabel{{color:{muted_dim};font-size:10px;font-weight:700;}}"
            f"QLineEdit#ProjectTreeSearch{{background:{panel};color:{text_soft};"
            f"border:1px solid {border};border-radius:8px;padding:7px 10px;"
            f"font-size:12px;selection-background-color:{accent_soft};}}"
            f"QLineEdit#ProjectTreeSearch:focus{{border-color:{accent};background:{panel};}}"
            f"QLabel#EmptyState{{color:{muted};background:{panel_2};border:1px dashed {border_medium};"
            f"border-radius:8px;padding:22px 24px;font-size:12px;line-height:1.5;}}"
            f"QFrame#ProjectInfoBlock{{background:{panel_2};border:1px solid {border};"
            f"border-radius:8px;}}"
            f"QFrame#MediaPreviewBlock{{background:{panel_2};border:1px solid {border};"
            f"border-radius:8px;}}"
            f"QFrame#MediaPreviewCard{{background:{panel};border:1px solid {border};"
            f"border-radius:8px;}}"
            f"QFrame#MediaPreviewCard:hover{{background:{accent_softer};border-color:{border_medium};}}"
            f"QLabel#MediaThumb{{background:{panel_inset};border:1px solid {border};"
            f"border-radius:6px;color:{muted_dim};font-size:10px;font-weight:800;}}"
            f"QLabel#MediaName{{color:{text_soft};font-size:11px;font-weight:800;}}"
            f"QLabel#MediaMeta{{color:{muted_dim};font-size:10px;font-weight:700;}}"
            f"QLabel#MediaEmpty{{color:{muted};background:{panel};border:1px dashed {border_medium};"
            f"border-radius:7px;padding:12px 14px;font-size:12px;}}"
            f"QLabel#InfoKey{{color:{muted_dim};font-size:11px;font-weight:700;}}"
            f"QLabel#InfoValue{{color:{text_soft};font-size:12px;font-weight:600;}}"
            f"QPushButton{{background:{panel};color:{text_soft};border:1px solid {border_medium};"
            f"border-radius:6px;padding:6px 12px;font-size:12px;font-weight:600;}}"
            f"QPushButton:hover{{background:{accent_softer};border-color:{border_strong};color:{text};}}"
            f"QPushButton#Primary{{background:qlineargradient(x1:0,y1:0,x2:0,y2:1,"
            f"stop:0 {accent_top},stop:1 {accent_bottom});color:{accent_fg};"
            f"border:1px solid {accent_bottom};border-top:1px solid {accent_top};"
            f"font-weight:800;}}"
            f"QPushButton#Primary:hover{{background:{accent_hover};border-color:{accent_hover};}}"
            f"QPushButton#Outline{{background:{panel};color:{text_soft};border:1px solid {border_medium};}}"
            f"QPushButton#Outline:hover{{background:{accent_softer};border-color:{accent};}}"
            f"QPushButton#SoftAction{{background:{accent_softer};color:{accent};"
            f"border:1px solid {accent_soft};font-weight:800;}}"
            f"QPushButton#SoftAction:hover{{background:{accent_soft};border-color:{accent};color:{text};}}"
            f"QPushButton#IconAction{{background:{panel};color:{muted};border:1px solid {border};"
            f"border-radius:6px;padding:0;}}"
            f"QPushButton#IconAction:hover{{background:{accent_softer};border-color:{accent};}}"
            f"QPushButton#FilterChip{{background:{panel};color:{muted};border:1px solid {border};"
            f"border-radius:999px;padding:3px 10px;font-size:11px;font-weight:700;}}"
            f"QPushButton#FilterChip:hover{{background:{accent_softer};border-color:{border_medium};"
            f"color:{text_soft};}}"
            f"QPushButton#FilterChip:checked{{background:{accent_soft};color:{accent};"
            f"border-color:{accent_soft};font-weight:800;}}"
            f"QPushButton#Primary:disabled,QPushButton#SoftAction:disabled,"
            f"QPushButton#Outline:disabled,QPushButton#IconAction:disabled{{"
            f"background:{panel};color:{muted_dim};border:1px solid {border};}}"
            f"QPushButton:disabled{{background:{panel};color:{muted_dim};border:1px solid {border};}}"
            f"QTreeWidget#ProjectDirectoryTree{{background:{panel_2};color:{text};border:1px solid {border};"
            f"border-radius:8px;padding:6px;outline:0;font-size:13px;"
            f"selection-background-color:{accent_soft};selection-color:{text};}}"
            f"QTreeWidget#ProjectDirectoryTree::item{{min-height:22px;padding:2px 6px;"
            f"border-radius:3px;color:{text_soft};}}"
            f"QTreeWidget#ProjectDirectoryTree[compactWsList='true']::item{{"
            f"min-height:20px;padding:1px 4px;border-radius:2px;}}"
            f"QTreeWidget#ProjectDirectoryTree::item:hover{{background:{accent_softer};color:{text};}}"
            f"QTreeWidget#ProjectDirectoryTree::item:selected{{background:{accent_soft};"
            f"color:{text};font-weight:700;}}"
            f"QTreeWidget#ProjectDirectoryTree::branch{{background:transparent;image:none;border-image:none;}}"
            f"QTreeWidget#ProjectDirectoryTree::branch:hover{{background:{accent_softer};}}"
            f"QTreeWidget#ProjectDirectoryTree::branch:selected{{background:transparent;}}"
            f"QTreeWidget#ProjectDirectoryTree::branch:has-children:selected,"
            f"QTreeWidget#ProjectDirectoryTree::branch:open:selected,"
            f"QTreeWidget#ProjectDirectoryTree::branch:closed:selected{{background:transparent;}}"
            f"QFrame#StatCard{{border:1px solid {border};border-left:3px solid {accent};"
            f"border-radius:8px;background:{panel_2};}}"
            f"QFrame#StatCard[role='specimen']{{border-left-color:{accent};}}"
            f"QFrame#StatCard[role='result']{{border-left-color:{success};}}"
            f"QFrame#StatCard[role='pending']{{border-left-color:{warn};}}"
            f"QLabel#StatValue{{font-size:22px;font-weight:800;color:{accent};}}"
            f"QLabel#StatValue[role='result']{{color:{success};}}"
            f"QLabel#StatValue[role='pending']{{color:{warn};}}"
            f"QLabel#StatLabel{{font-size:11px;color:{muted};font-weight:700;}}"
            f"QFrame#ChildPreviewBlock{{background:{panel_2};border:1px solid {border};"
            f"border-radius:8px;}}"
            f"QFrame#ChildNodeRow{{background:{panel};border:1px solid {border};border-radius:5px;}}"
            f"QFrame#ChildNodeRow:hover{{background:{accent_softer};border-color:{border_medium};}}"
            f"QLabel#ChildNodeName{{color:{text_soft};font-size:12px;font-weight:800;}}"
            f"QLabel#ChildNodeMeta{{color:{muted_dim};font-size:11px;font-weight:700;}}"
            f"QLabel#ChildNodeBadge{{color:{muted};background:{panel_inset};"
            f"border:1px solid {border};border-radius:999px;padding:2px 9px;"
            f"font-size:10px;font-weight:800;}}"
            f"QListView#uidUnifiedGrid,QListView#uidSectionList{{background:transparent;"
            f"outline:0;border:0;}}"
            f"QFrame#uidUnifiedHost,QFrame#uidSection{{background:transparent;border:0;}}"
            f"QFrame#uidSectionHeader{{background:{panel_2};border:1px solid {border};"
            f"border-radius:6px;}}"
            f"QScrollArea{{background:transparent;border:0;}}"
            f"QSplitter#ProjectTreeSplitter::handle:horizontal,"
            f"QSplitter#ProjectTreeGridInnerSplitter::handle:horizontal{{"
            f"width:12px;background:{panel_inset};"
            f"border-left:1px solid {border_medium};border-right:1px solid {border_medium};"
            f"margin:4px 0;border-radius:2px;}}"
            f"QSplitter#ProjectTreeSplitter::handle:horizontal:hover,"
            f"QSplitter#ProjectTreeGridInnerSplitter::handle:horizontal:hover{{"
            f"background:{accent_soft};border-left-color:{accent};border-right-color:{accent};}}"
            f"QFrame#ProjectTreeDataSummaryPanel{{background:{panel_2};border:1px solid {border};"
            f"border-radius:10px;padding:2px;}}"
            f"QFrame#SpecimenFilterCondBox{{background:{panel};border:1px solid {border};"
            f"border-radius:8px;}}"
            f"QTableWidget#SpecimenSummaryTable{{background:{panel};alternate-background-color:{panel_2};"
            f"border:1px solid {border};border-radius:8px;gridline-color:{border};"
            f"font-size:11px;selection-background-color:{accent_soft};}}"
            f"QTableWidget#SpecimenSummaryTable::item{{padding:3px 6px;}}"
            f"QHeaderView::section{{background:{panel_2};color:{muted};padding:5px 8px;"
            f"border:0;border-bottom:1px solid {border_medium};font-size:11px;font-weight:700;}}"
            f"QSplitter#ProjectTreeSummaryBodySplit::handle:vertical{{"
            f"height:12px;background:{panel_inset};border-top:1px solid {border_medium};"
            f"border-bottom:1px solid {border_medium};margin:2px 24px;border-radius:4px;}}"
            f"QSplitter#ProjectTreeSummaryBodySplit::handle:vertical:hover{{"
            f"background:{accent_soft};border-top-color:{accent};border-bottom-color:{accent};}}"
        )

    # ── BaseView ────────────────────────────────────────────────────────────
    def on_activate(self) -> None:
        from app.config import preview_profile as pp

        pp.set_preview_master_size(self.ctx.settings.project_tree_preview_master_size)
        if getattr(self, "_uid_grid", None) is not None:
            self._uid_grid.bind_settings(self.ctx.settings)
        self._apply_style()
        self._apply_ux_profile()
        self._sync_view_mode_from_settings()
        self._sync_layout_mode_from_settings()
        self._restore_tree_split_state()
        self._restore_grid_inner_split_state()
        self._restore_summary_body_split_state()
        mode = ptl.normalize_content_mode(self.ctx.settings.project_tree_content_mode)
        self.ctx.settings.project_tree_content_mode = mode
        self._reload_project_tree()
        self._reload_card_grid()

    def _sync_view_mode_from_settings(self) -> None:
        mode = getattr(self.ctx.settings, "project_tree_view_mode", "all")
        if mode == "rooted":
            saved = self.ctx.settings.project_tree_root
            self._root = saved if saved and Path(saved).is_dir() else None
        else:
            self._root = None
        self._sync_view_mode_buttons()

    def _sync_view_mode_buttons(self) -> None:
        mode = getattr(self.ctx.settings, "project_tree_view_mode", "all")
        rooted = mode == "rooted"
        self._btn_mode_all.setChecked(not rooted)
        self._btn_mode_rooted.setChecked(rooted)
        self._btn_pick.setEnabled(rooted)
        if hasattr(self, "_act_pick_root"):
            # 精简模式：菜单里始终可点「选择根目录」（会切到 rooted）
            self._act_pick_root.setEnabled(True)
        self._act_newsub.setEnabled(rooted and bool(self._root))
        if hasattr(self, "_act_view_all"):
            self._act_view_all.setText(
                "浏览：全部项目 ✓" if not rooted else "浏览：全部项目"
            )
            self._act_view_rooted.setText(
                "浏览：按根目录 ✓" if rooted else "浏览：按根目录"
            )

    def _set_view_mode(self, mode: str) -> None:
        if mode not in {"all", "rooted"}:
            return
        self.ctx.settings.project_tree_view_mode = mode
        if mode == "rooted":
            saved = self.ctx.settings.project_tree_root
            self._root = saved if saved and Path(saved).is_dir() else None
            if self._root is None:
                self._pick_root()
                if self._root is None:
                    self.ctx.settings.project_tree_view_mode = "all"
        else:
            self._root = None
        self._sync_view_mode_buttons()
        self._reload_project_tree()

    def _is_rooted_view(self) -> bool:
        return (
            getattr(self.ctx.settings, "project_tree_view_mode", "all") == "rooted"
            and self._root
            and Path(self._root).is_dir()
        )

    def _maybe_refresh_root_index_cache(self) -> None:
        """Best-effort：刷新调查根下已登记工作区的索引缓存（方便后续汇总加速）."""
        if not self._is_rooted_view() or not self._root:
            return
        try:
            from app.services.workspace_index_service import refresh_registered_workspaces

            refresh_registered_workspaces(self._root)
        except Exception:
            pass

    def _refresh_index_cache_manual(self) -> None:
        """⋯ 菜单：手动刷新根库索引缓存."""
        root = self._root if self._is_rooted_view() else None
        if not root:
            # 无根时：对当前选中工作区的父级尝试；否则提示先选根
            ui.info(
                self,
                "刷新汇总索引",
                "请先切换到「按根目录」并选择调查根，再刷新索引。",
            )
            return
        try:
            from app.services.workspace_index_service import refresh_registered_workspaces

            rows = refresh_registered_workspaces(root)
        except Exception as exc:
            ui.warn(self, "刷新汇总索引", str(exc))
            return
        ui.info(
            self,
            "刷新汇总索引",
            f"已更新 {len(rows)} 个工作区的索引缓存。",
        )

    def _sync_layout_mode_from_settings(self) -> None:
        mode = getattr(self.ctx.settings, "project_tree_layout_mode", "tree")
        cards = mode == "cards"
        self._btn_layout_tree.setChecked(not cards)
        self._btn_layout_cards.setChecked(cards)
        if hasattr(self, "_body_stack"):
            self._body_stack.setCurrentIndex(1 if cards else 0)

    def _set_layout_mode(self, mode: str) -> None:
        if mode not in {"tree", "cards"}:
            return
        prev = getattr(self.ctx.settings, "project_tree_layout_mode", "tree")
        # 切换前记下当前选择，两边互通
        bridge: list[str] = []
        if prev == "tree" and mode == "cards":
            bridge = [
                str(it.data(0, _PATH_ROLE))
                for it in self._tree.selectedItems()
                if it.data(0, _PATH_ROLE)
            ]
        elif prev == "cards" and mode == "tree" and hasattr(self, "_card_grid"):
            bridge = list(self._card_grid.selected_directories())
        self.ctx.settings.project_tree_layout_mode = mode
        self._sync_layout_mode_from_settings()
        if mode == "cards":
            self._reload_card_grid()
            if bridge and hasattr(self, "_card_grid"):
                self._card_grid.set_selected_directories(bridge)
        elif mode == "tree" and bridge:
            if getattr(self.ctx.settings, "project_tree_view_mode", "all") != "all":
                self.ctx.settings.project_tree_view_mode = "all"
                self._root = None
                self._sync_view_mode_buttons()
                self._reload_project_tree()
            self._select_tree_paths(bridge)

    def _reload_card_grid(self) -> None:
        if not hasattr(self, "_card_grid"):
            return
        from app.services.project_service import (
            default_user_projects_json_path,
            get_project_summary,
            list_projects,
        )

        jp = default_user_projects_json_path()
        entries = []
        for p in list_projects(jp):
            directory = p.get("directory") or p.get("dir") or ""
            if not directory:
                continue
            row = dict(p)
            try:
                row["is_candidate"] = not pts.is_workspace(directory)
            except OSError:
                row["is_candidate"] = False
            entries.append(row)
        # 卡片硬契约：永远显示全部已录项目 (spec §4.1)
        self._card_grid.set_entries(
            entries,
            stats_loader=lambda d: get_project_summary(d),
        )
        # 保持与顶栏搜索同步
        q = ""
        if hasattr(self, "_header_search"):
            q = self._header_search.text()
        elif hasattr(self, "_search"):
            q = self._search.text()
        if q:
            self._card_grid.set_filter_text(q)

    def _enter_workspace_from_card(self, directory: str) -> None:
        try:
            if pts.is_workspace(directory):
                # §7 旧(v0.55 回归): 只 emit 信号, 生产端无人连接 → 点「进入」无任何反应。
                # self.enter_workspace_requested.emit(directory)
                # 新: 走与树「进入工作区」相同的统一入口(设 ctx、建目录、记最近、跳工作台)。
                self._enter_workspace_path(directory)
            else:
                self._select_tree_path(directory)
        except OSError:
            self._select_tree_path(directory)

    def _adopt_from_card(self, directory: str) -> None:
        self._run_adopt_flow(directory)

    def _summarize_from_cards(self, directories: list) -> None:
        """卡片多选 → 切到树视图并选中对应工作区，触发中栏汇总."""
        paths = [str(p) for p in (directories or []) if p]
        if not paths:
            return
        self._set_layout_mode("tree")
        # 卡片跨根硬契约：汇总时切到「全部项目」才能选中
        need_reload = (
            getattr(self.ctx.settings, "project_tree_view_mode", "all") != "all"
            or self._tree.topLevelItemCount() == 0
        )
        if need_reload:
            self.ctx.settings.project_tree_view_mode = "all"
            self._root = None
            self._sync_view_mode_buttons()
            self._reload_project_tree()
        self._select_tree_paths(paths)

    def _select_tree_paths(self, directories: list[str]) -> None:
        targets = set()
        for d in directories:
            try:
                targets.add(str(Path(d).resolve()))
            except OSError:
                targets.add(str(d))
        found: list = []
        for i in range(self._tree.topLevelItemCount()):
            self._collect_items_by_paths(self._tree.topLevelItem(i), targets, found)
        if not found:
            return
        self._tree.blockSignals(True)
        self._tree.clearSelection()
        for it in found:
            it.setSelected(True)
            it.setExpanded(True)
        # CurrentOnly：设当前项但不清掉其它已选项（ExtendedSelection 默认会 ClearAndSelect）
        self._tree.setCurrentItem(
            found[0], 0, QItemSelectionModel.SelectionFlag.Current,
        )
        self._tree.blockSignals(False)
        self._tree.scrollToItem(found[0])
        self._on_tree_selection_changed()

    def _collect_items_by_paths(
        self, item: Optional[QTreeWidgetItem], targets: set[str], acc: list,
    ) -> None:
        if item is None:
            return
        p = item.data(0, _PATH_ROLE)
        if p:
            try:
                resolved = str(Path(p).resolve())
            except OSError:
                resolved = str(p)
            if resolved in targets:
                acc.append(item)
        for i in range(item.childCount()):
            self._collect_items_by_paths(item.child(i), targets, acc)

    def _set_cover_for_directory(self, directory: str) -> None:
        from app.services.cover_pick_service import set_project_cover_path
        from app.utils import ui as ui_mod

        start = directory or ""
        path = ui_mod.get_open_file_name(
            self,
            "选择项目封面",
            start,
            "图片 (*.jpg *.jpeg *.png *.tif *.tiff);;所有文件 (*.*)",
        )
        if not path:
            return
        try:
            set_project_cover_path(directory, path)
        except Exception as exc:
            ui_mod.warn(self, "设置封面失败", str(exc))
            return
        if hasattr(self, "_card_grid"):
            self._card_grid.refresh_cover(directory)
        ui_mod.info(self, "封面", "已更新项目封面。")

    def _clear_cover_for_directory(self, directory: str) -> None:
        from app.services.cover_pick_service import clear_project_cover_path
        from app.utils import ui as ui_mod

        try:
            clear_project_cover_path(directory)
        except Exception as exc:
            ui_mod.warn(self, "恢复封面失败", str(exc))
            return
        if hasattr(self, "_card_grid"):
            self._card_grid.refresh_cover(directory)
        ui_mod.info(self, "封面", "已恢复为自动封面。")

    def _select_tree_path(self, directory: str) -> None:
        self._set_layout_mode("tree")
        target = str(Path(directory).resolve()) if directory else ""
        for i in range(self._tree.topLevelItemCount()):
            found = self._find_item_by_path(self._tree.topLevelItem(i), target)
            if found:
                self._select_tree_item(found)
                return

    def _find_item_by_path(
        self, item: Optional[QTreeWidgetItem], target: str,
    ) -> Optional[QTreeWidgetItem]:
        if item is None:
            return None
        p = item.data(0, _PATH_ROLE)
        if p and str(Path(p).resolve()) == target:
            return item
        for i in range(item.childCount()):
            hit = self._find_item_by_path(item.child(i), target)
            if hit:
                return hit
        return None

    # ── Data / tree build ─────────────────────────────────────────────────────
    def _reload_project_tree(self) -> None:
        self._tree.clear()
        self._tree_count_lbl.setText("0 个节点")
        self._update_tree_metrics()
        self._btn_enter.setEnabled(False)
        self._sync_sticky_enter_from_primary()
        self._btn_summary.setEnabled(False)
        self._btn_station_species.setEnabled(False)
        self._btn_station_import.setEnabled(False)
        self._btn_open_dir.setEnabled(False)
        self._btn_copy_path.setEnabled(False)
        self._detail_kind.setText("未选择")
        self._info_block.hide()
        self._child_block.hide()
        self._clear_child_preview()
        self._clear_media_preview()
        self._clear_stats()
        if self._is_rooted_view():
            # ── Rooted scan mode (unchanged): one survey root, recursive tree ──
            self._act_newsub.setEnabled(True)
            self._root_lbl.setText(self._root)
            self._empty_state.hide()
            tree = pts.scan_tree(self._root)
            self._tree_count_lbl.setText(f"{self._count_nodes(tree)} 个节点")
            self._update_tree_metrics([tree])
            root_item = self._build_item(tree)
            self._tree.addTopLevelItem(root_item)
            root_item.setExpanded(True)
            self._filter_tree(self._search.text())
            self._select_first_item()
            return

        # ── Flat-list mode: every project already recorded in user_projects.json ──
        # No survey root chosen -> recognize the projects the user already created
        # instead of showing a blank tree. Each project is a top-level node feeding
        # the same _build_item, so selection / stats / enter / summary / station-import
        # all keep working unchanged.
        self._act_newsub.setEnabled(False)
        nodes = self._load_known_projects_nodes()
        if not nodes:
            self._root_lbl.setText("（未选根目录）")
            self._detail_name.setText("选择或创建调查区域")
            self._detail_path.setText("")
            self._detail_kind.setText("未选择")
            self._info_block.hide()
            self._child_block.hide()
            self._empty_state.setText(
                "还没有选择调查根目录，也没有已记录的项目。\n\n"
                "选择根目录：读取已有文件夹树，不改动原文件。\n"
                "新建调查区域：创建一个区域根目录，后续断面会继承区域设置。"
            )
            self._empty_state.show()
            return
        self._root_lbl.setText(f"（全部已建项目 · {len(nodes)}）")
        self._tree_count_lbl.setText(f"{len(nodes)} 个项目")
        self._update_tree_metrics(nodes)
        self._empty_state.hide()
        for node in nodes:
            self._tree.addTopLevelItem(self._build_item(node))
        self._filter_tree(self._search.text())
        if len(nodes) >= 1:
            self._select_all_visible_workspaces()
        else:
            self._select_first_item()

    def _count_nodes(self, node: dict) -> int:
        return 1 + sum(self._count_nodes(child) for child in node.get("children", []))

    def _collect_node_metrics(self, node: dict) -> tuple[int, int, int]:
        regions = 0
        workspaces = 1 if node.get("has_data") else 0
        candidates = 1 if node.get("is_candidate") else 0
        if not node.get("has_data") and not node.get("is_candidate"):
            regions = 1
        for child in node.get("children", []):
            r, w, c = self._collect_node_metrics(child)
            regions += r
            workspaces += w
            candidates += c
        return regions, workspaces, candidates

    def _update_tree_metrics(self, nodes: Optional[list] = None) -> None:
        regions = workspaces = candidates = 0
        for node in nodes or []:
            r, w, c = self._collect_node_metrics(node)
            regions += r
            workspaces += w
            candidates += c
        inline = getattr(self, "_tree_metrics_inline", None)
        if inline is not None:
            inline.setText(
                f"{regions} 区域 · {workspaces} 工作区 · {candidates} 待导入"
            )
            return
        if getattr(self, "_metric_regions", None) is not None:
            self._metric_regions.setText(str(regions))
            self._metric_workspaces.setText(str(workspaces))
            self._metric_candidates.setText(str(candidates))

    def _select_first_item(self) -> None:
        query = self._search.text().strip().lower()
        item = (
            self._first_matching_item(query)
            if self._is_filtering(query)
            else self._first_visible_item()
        )
        if item is None:
            return
        self._tree.setCurrentItem(item)
        item.setSelected(True)
        self._update_detail_panel_for_selected_project()

    def _set_kind_filter(self, kind: str) -> None:
        if kind not in self._kind_filter_buttons:
            return
        self._kind_filter = kind
        for key, button in self._kind_filter_buttons.items():
            button.blockSignals(True)
            button.setChecked(key == kind)
            button.blockSignals(False)
        self._filter_tree(self._search.text())
        # 旧版：点「全部」会顺带全选；精简版只过滤，全选走独立按钮
        if kind == "all" and not self._ux_v2():
            self._select_all_visible_workspaces()

    def _select_all_visible_workspaces(self) -> None:
        """全选所有可见工作区 → 用于跨项目数据汇总."""
        found: list = []
        self._tree.expandAll()
        for i in range(self._tree.topLevelItemCount()):
            self._collect_visible_workspaces(self._tree.topLevelItem(i), found)
        if not found:
            return
        self._kind_filter = "all"
        for key, button in self._kind_filter_buttons.items():
            button.blockSignals(True)
            button.setChecked(key == "all")
            button.blockSignals(False)
        self._tree.blockSignals(True)
        self._tree.clearSelection()
        for it in found:
            it.setSelected(True)
        if found:
            # setCurrentItem 会清掉 ExtendedSelection 多选，只滚动即可
            self._tree.scrollToItem(found[-1])
        self._tree.blockSignals(False)
        self._on_tree_selection_changed()

    def _collect_visible_workspaces(self, item, acc: list) -> None:
        if (not item.isHidden()) and item.data(0, _KIND_ROLE) == "workspace":
            acc.append(item)
        for i in range(item.childCount()):
            self._collect_visible_workspaces(item.child(i), acc)

    def _is_filtering(self, query: str) -> bool:
        return bool(query) or self._kind_filter != "all"

    def _first_visible_item(self) -> Optional[QTreeWidgetItem]:
        for i in range(self._tree.topLevelItemCount()):
            found = self._first_visible_item_from(self._tree.topLevelItem(i))
            if found is not None:
                return found
        return None

    def _first_visible_item_from(self, item: QTreeWidgetItem) -> Optional[QTreeWidgetItem]:
        if not item.isHidden():
            return item
        for i in range(item.childCount()):
            found = self._first_visible_item_from(item.child(i))
            if found is not None:
                return found
        return None

    def _filter_tree(self, text: str) -> None:
        query = (text or "").strip().lower()
        filtering = self._is_filtering(query)
        total = 0
        matches = 0
        for i in range(self._tree.topLevelItemCount()):
            item_total, item_matches = self._filter_item(self._tree.topLevelItem(i), query)
            total += item_total
            matches += item_matches
        if filtering:
            self._tree_count_lbl.setText(f"{matches}/{total} 个匹配")
        elif total:
            suffix = "项目" if self._root is None else "节点"
            self._tree_count_lbl.setText(f"{total} 个{suffix}")
        current = self._tree.currentItem()
        if filtering:
            if current is not None and self._item_matches_active_filter(current, query):
                return
            first = self._first_matching_item(query)
        else:
            if current is not None and not current.isHidden():
                return
            first = self._first_visible_item()
        self._tree.clearSelection()
        if first is not None:
            self._select_tree_item(first)
        else:
            self._show_no_match_state()

        self._sync_tree_list_presentation()

    def _sync_tree_list_presentation(self) -> None:
        """工作区筛选：紧凑行高、隐藏列表内重复图标，减少左栏遮挡。"""
        compact_ws = self._kind_filter == "workspace"
        self._tree.setProperty("compactWsList", compact_ws)
        self._tree.setIndentation(14 if compact_ws else 22)
        self._tree.style().unpolish(self._tree)
        self._tree.style().polish(self._tree)
        for i in range(self._tree.topLevelItemCount()):
            self._apply_tree_item_presentation(self._tree.topLevelItem(i), compact_ws)

    def _apply_tree_item_presentation(
        self, item: Optional[QTreeWidgetItem], compact_ws: bool,
    ) -> None:
        if item is None:
            return
        kind = item.data(0, _KIND_ROLE) or "folder"
        if kind == "workspace":
            if compact_ws:
                item.setIcon(0, QIcon())
            else:
                item.setIcon(
                    0,
                    icons.icon("mdi6.database-outline", color=icons.TONE_ACCENT),
                )
        for i in range(item.childCount()):
            self._apply_tree_item_presentation(item.child(i), compact_ws)

    def _filter_item(self, item: QTreeWidgetItem, query: str) -> tuple[int, int]:
        total = 1
        child_matches = 0
        for i in range(item.childCount()):
            child_total, child_match_count = self._filter_item(item.child(i), query)
            total += child_total
            child_matches += child_match_count
        own_match = self._item_matches_active_filter(item, query)
        visible = own_match or child_matches > 0
        item.setHidden(not visible)
        if self._is_filtering(query) and child_matches:
            item.setExpanded(True)
        return total, child_matches + (1 if own_match else 0)

    def _item_matches_active_filter(self, item: QTreeWidgetItem, query: str) -> bool:
        text_match = not query or self._item_matches_query(item, query)
        return text_match and self._item_matches_kind(item)

    def _item_matches_query(self, item: QTreeWidgetItem, query: str) -> bool:
        haystack = " ".join([
            item.text(0),
            str(item.data(0, _PATH_ROLE) or ""),
        ]).lower()
        return query in haystack

    def _item_matches_kind(self, item: QTreeWidgetItem) -> bool:
        if self._kind_filter == "all":
            return True
        kind = item.data(0, _KIND_ROLE) or "folder"
        return kind == self._kind_filter

    def _first_matching_item(self, query: str) -> Optional[QTreeWidgetItem]:
        for i in range(self._tree.topLevelItemCount()):
            found = self._first_matching_item_from(self._tree.topLevelItem(i), query)
            if found is not None:
                return found
        return None

    def _first_matching_item_from(
        self, item: QTreeWidgetItem, query: str
    ) -> Optional[QTreeWidgetItem]:
        if not item.isHidden() and self._item_matches_active_filter(item, query):
            return item
        for i in range(item.childCount()):
            found = self._first_matching_item_from(item.child(i), query)
            if found is not None:
                return found
        return None

    def _load_known_projects_nodes(self) -> list:
        """Synthesize scan_tree-shaped nodes for every project recorded in
        ``user_projects.json``. Used by ``_reload_project_tree`` in flat-list mode (no survey
        root selected) so already-created projects are recognized without the
        user having to pick a common parent folder.

        Skips demo projects; dedupes by resolved directory; most-recent first
        (the json is append-ordered, so the latest-entered project is last and
        lands on top after reverse()). ``has_data`` mirrors ``pts.is_workspace``
        so the 📷/📁 label and all downstream logic stays correct even when a
        drive is unmounted.
        """
        from app.services.project_service import (
            default_user_projects_json_path,
            list_projects,
        )
        try:
            projects = list_projects(default_user_projects_json_path())
        except Exception:
            return []

        seen: set = set()
        nodes: list = []

        def _add_node(directory: str, name: str, *, candidate: bool = False) -> None:
            if not directory:
                return
            try:
                resolved = str(Path(directory).expanduser().resolve())
            except (OSError, ValueError):
                resolved = directory
            if resolved in seen:
                return
            seen.add(resolved)
            try:
                exists = Path(directory).expanduser().exists()
            except OSError:
                exists = False
            nodes.append({
                "name": name or Path(directory).name or "(未命名)",
                "path": directory,
                "has_data": pts.is_workspace(directory) if exists else False,
                "is_candidate": candidate,
                "unavailable": not exists,
                "children": [],
            })

        scan_roots: set[str] = set()
        for p in projects:
            if p.get("isDemo"):
                continue
            directory = p.get("directory") or p.get("dir") or ""
            try:
                scan_roots.add(str(Path(directory).expanduser().resolve().parent))
            except (OSError, ValueError):
                pass
            _add_node(directory, p.get("name") or Path(directory).name)

        has_recorded_projects = bool(nodes)

        for attr in ("current_project_root", "current_project_dir"):
            value = getattr(self.ctx, attr, None)
            if value:
                try:
                    scan_roots.add(str(Path(value).expanduser().resolve().parent))
                except (OSError, ValueError):
                    pass

        if not has_recorded_projects:
            try:
                # Fallback only when the recent-project list is empty. Scanning
                # a broad /mnt workspace on every 项目树 activation is expensive
                # under WSL/drvfs and makes basic navigation feel frozen.
                scan_roots.add(str(Path.cwd().resolve().parent))
            except OSError:
                pass

        # Keep recent projects first, then append discovered legacy/candidate
        # workspaces from nearby roots. Depth 1 is enough for sibling workspaces;
        # depth 2 catches one container folder such as "ceshi/ceshi3".
        nodes.reverse()  # most-recent recorded first
        for root in sorted(scan_roots if not has_recorded_projects else set()):
            try:
                candidates = pts.discover_workspace_candidates(root, max_depth=2)
            except OSError:
                candidates = []
            for c in candidates:
                _add_node(c["path"], c["name"], candidate=True)
        return nodes

    def _build_item(self, node: dict) -> QTreeWidgetItem:
        # Two-level semantics: a node with its own project.db is a 工作区 (where
        # you actually shoot); everything else is a 区域/文件夹 (an inheritance
        # anchor or just a container) — never call them all "项目".
        if node.get("unavailable"):
            label = f"{node['name']}  ·  不可用"
            glyph = "mdi6.folder-alert-outline"
            tone = icons.TONE_WARN
            kind = "unavailable"
        elif node["has_data"]:
            label = f"{node['name']} · 工作区"
            glyph = "mdi6.database-outline"
            tone = icons.TONE_ACCENT
            kind = "workspace"
        elif node.get("is_candidate"):
            label = f"{node['name']}  ·  可导入"
            glyph = "mdi6.folder-search-outline"
            tone = icons.TONE_WARN
            kind = "candidate"
        else:
            label = str(node["name"])
            glyph = "mdi6.folder-outline"
            tone = icons.TONE_MUTED
            kind = "folder"
        item = QTreeWidgetItem([label])
        show_ws_icon = not (kind == "workspace" and self._kind_filter == "workspace")
        if show_ws_icon:
            item.setIcon(0, icons.icon(glyph, color=tone))
        item.setData(0, _PATH_ROLE, node["path"])
        item.setData(0, _KIND_ROLE, kind)
        item.setToolTip(0, "")
        for child in node["children"]:
            item.addChild(self._build_item(child))
        return item

    def _selected_path(self) -> Optional[str]:
        items = self._tree.selectedItems()
        if not items:
            return None
        return items[0].data(0, _PATH_ROLE)

    def _show_tree_context_menu(self, pos) -> None:
        item = self._tree.itemAt(pos)
        if item is None:
            return
        self._tree.setCurrentItem(item)
        item.setSelected(True)
        path = item.data(0, _PATH_ROLE)
        if not path:
            return

        menu = QMenu(self._tree)

        enter_action = menu.addAction("进入工作区拍照")
        enter_action.triggered.connect(self._enter_selected)

        new_child_action = menu.addAction("新建子文件夹")
        new_child_action.triggered.connect(self._new_subfolder)

        menu.addSeparator()
        summary_action = menu.addAction("汇总导出…")
        summary_action.triggered.connect(self._open_summary_export)

        filter_action = menu.addAction("数据筛选…")
        filter_action.triggered.connect(self._open_data_filter)

        station_action = menu.addAction("导入站位总表…")
        station_action.triggered.connect(self._open_station_import)

        menu.addSeparator()
        open_action = menu.addAction("打开文件夹")
        open_action.triggered.connect(lambda _=False, p=path: self._open_directory(p))

        copy_action = menu.addAction("复制路径")
        copy_action.triggered.connect(
            lambda _=False, p=path: QApplication.clipboard().setText(str(p))
        )

        try:
            path_exists = Path(path).expanduser().exists()
        except OSError:
            path_exists = False
        if not path_exists:
            relocate_action = menu.addAction("指到新位置…")
            relocate_action.triggered.connect(self._relocate_selected_path)

        kind = item.data(0, _KIND_ROLE) if item else None
        if kind == "candidate":
            adopt_action = menu.addAction("认领此文件夹…")
            adopt_action.triggered.connect(self._adopt_selected_candidate)

        menu.addSeparator()
        cover_action = menu.addAction("设置封面…")
        cover_action.triggered.connect(
            lambda _=False, p=path: self._set_cover_for_directory(str(p))
        )
        clear_cover_action = menu.addAction("恢复自动封面")
        clear_cover_action.triggered.connect(
            lambda _=False, p=path: self._clear_cover_for_directory(str(p))
        )

        properties_action = menu.addAction("属性")
        properties_action.triggered.connect(
            lambda _=False, p=path: self._show_path_properties(str(p))
        )

        menu.exec(self._tree.viewport().mapToGlobal(pos))

    def _open_directory(self, path: str) -> None:
        from app.utils.file_manager import local_path, open_directory

        directory = Path(local_path(path))
        if not directory.exists():
            ui.warn(self, "打开文件夹", f"目录不存在或磁盘未连接：\n{path}")
            return
        if not open_directory(str(directory)):
            ui.warn(self, "打开文件夹", f"无法打开文件夹：\n{directory}")

    def _open_selected_directory(self) -> None:
        path = self._selected_path()
        if path:
            self._open_directory(path)

    def _copy_selected_path(self) -> None:
        path = self._selected_path()
        if path:
            QApplication.clipboard().setText(str(path))

    def _show_path_properties(self, path: str) -> None:
        p = Path(path)
        exists = p.exists()
        try:
            child_count = sum(1 for child in p.iterdir() if child.is_dir()) if exists else 0
        except OSError:
            child_count = 0
        try:
            workspace = pts.is_workspace(path)
        except OSError:
            workspace = False
        kind = "工作区" if workspace else "文件夹"
        state = "存在" if exists else "不可访问"
        ui.info(
            self,
            "属性",
            "\n".join([
                f"名称：{p.name or path}",
                f"类型：{kind}",
                f"状态：{state}",
                f"子文件夹：{child_count}",
                f"路径：{path}",
            ]),
        )

    # ── Detail panel ──────────────────────────────────────────────────────────
    def _show_no_match_state(self) -> None:
        self._btn_enter.setEnabled(False)
        self._btn_summary.setEnabled(False)
        self._btn_station_species.setEnabled(False)
        self._btn_station_import.setEnabled(False)
        self._btn_open_dir.setEnabled(False)
        self._btn_copy_path.setEnabled(False)
        self._act_newsub.setEnabled(bool(self._is_rooted_view()))
        self._detail_kind.setText("无匹配")
        self._detail_name.setText("没有匹配节点")
        self._detail_path.setText("")
        self._info_block.hide()
        self._child_block.hide()
        self._clear_child_preview()
        self._clear_media_preview()
        self._clear_stats()
        self._set_enter_action_style("Primary", "进入工作区拍照", "mdi6.camera-outline")
        self._empty_state.setText("没有匹配的节点。请调整搜索词或类型筛选。")
        self._empty_state.show()
        self._sync_sticky_enter_from_primary()

    def _update_scope_status_label(self) -> None:
        if not hasattr(self, "_scope_status_lbl"):
            return
        labeled = getattr(self, "_scope_labeled", None) or []
        n = len(labeled)
        if n >= 2:
            self._scope_status_lbl.setText(
                f"已选 {n} 个工作区 — 右侧概览 · 中间编号照片"
            )
        elif n == 1:
            self._scope_status_lbl.setText(
                f"已选 1 个 · {labeled[0][1]}（Ctrl+点击可多选汇总）"
            )
        else:
            self._scope_status_lbl.setText(
                "未选择；点「全选工作区」或 Ctrl+点击多选"
            )

    def _update_detail_panel_for_selected_project(self) -> None:
        selected_items = self._tree.selectedItems()
        if len(selected_items) >= 2:
            labeled = self._labeled_workspaces_from_items(selected_items)
            n_ws = len(labeled)
            if n_ws >= 2:
                names = "、".join(lbl for _, lbl in labeled[:5])
                if n_ws > 5:
                    names += f" 等{n_ws}个"
                self._empty_state.hide()
                self._btn_enter.setEnabled(False)
                self._btn_summary.setEnabled(True)
                self._btn_station_species.setEnabled(True)
                self._btn_station_import.setEnabled(False)
                self._btn_open_dir.setEnabled(False)
                self._btn_copy_path.setEnabled(False)
                self._detail_kind.setText("多选汇总")
                self._detail_name.setText(f"{n_ws} 个工作区")
                self._detail_path.setText(names)
                self._info_type.setText("多选汇总")
                self._info_status.setText("右侧看调查概览统计，中间看编号与照片")
                self._info_children.setText(f"{n_ws} 个工作区")
                self._info_block.show()
                self._child_block.hide()
                self._clear_child_preview()
                self._clear_media_preview()
                self._clear_stats()
                self._set_enter_action_style(
                    "Outline", "多选时不进入拍照", "mdi6.image-multiple-outline",
                )
                self._update_scope_status_label()
                self._sync_sticky_enter_from_primary()
                return
        path = self._selected_path()
        if not path:
            self._btn_enter.setEnabled(False)
            self._btn_summary.setEnabled(False)
            self._btn_station_species.setEnabled(False)
            self._btn_station_import.setEnabled(False)
            self._btn_open_dir.setEnabled(False)
            self._btn_copy_path.setEnabled(False)
            self._act_newsub.setEnabled(bool(self._is_rooted_view()))
            self._detail_kind.setText("未选择")
            self._info_block.hide()
            self._child_block.hide()
            self._clear_child_preview()
            self._clear_media_preview()
            self._clear_stats()
            self._set_enter_action_style("Primary", "进入工作区拍照", "mdi6.camera-outline")
            self._empty_state.setText("选择左侧文件夹后，可进入工作区、汇总导出或导入站位表。")
            self._empty_state.show()
            self._sync_sticky_enter_from_primary()
            return
        self._empty_state.hide()
        self._act_newsub.setEnabled(True)
        self._btn_enter.setEnabled(True)
        self._btn_summary.setEnabled(True)
        self._btn_station_species.setEnabled(True)
        self._btn_station_import.setEnabled(True)
        self._btn_open_dir.setEnabled(True)
        self._btn_copy_path.setEnabled(True)
        selected_items = self._tree.selectedItems()
        current_item = selected_items[0] if selected_items else self._tree.currentItem()
        child_count = current_item.childCount() if current_item else 0
        p = Path(path)
        try:
            exists = p.exists()
        except OSError:
            exists = False
        try:
            workspace = pts.is_workspace(path)
        except OSError:
            workspace = False
        node_kind = current_item.data(0, _KIND_ROLE) if current_item else None
        if workspace:
            kind = "工作区"
            state = "已初始化，可拍照"
            self._set_enter_action_style(
                "Primary", "进入工作区拍照", "mdi6.camera-outline",
                color=icons.TONE_ON_ACCENT,
            )
        elif not exists:
            kind = "不可用"
            state = "路径不存在或磁盘未连接 — 右键可「指到新位置」"
            self._btn_enter.setEnabled(False)
            self._btn_adopt.hide()
            self._set_enter_action_style(
                "Outline", "路径不可用", "mdi6.folder-alert-outline",
                color=icons.TONE_MUTED,
            )
        elif node_kind == "candidate":
            kind = "待导入"
            state = "可认领：只建 _data，原始照片不改动"
            self._btn_adopt.show()
            self._set_enter_action_style(
                "Outline", "认领后进入", "mdi6.folder-plus-outline",
                color=icons.TONE_ACCENT,
            )
        elif child_count:
            kind = "调查区域"
            state = "区域节点，通常在下级断面拍照"
            self._btn_adopt.hide()
            self._set_enter_action_style(
                "SoftAction", "进入此层拍照", "mdi6.folder-open-outline",
                color=icons.TONE_ACCENT,
            )
        else:
            kind = "文件夹"
            state = "进入时自动初始化工作区" if exists else "路径不可访问或磁盘未连接"
            self._btn_adopt.hide()
            self._set_enter_action_style(
                "Primary", "初始化并进入拍照", "mdi6.camera-plus-outline",
                color=icons.TONE_ON_ACCENT,
            )
        if workspace:
            self._btn_adopt.hide()
        self._detail_kind.setText(kind)
        self._detail_name.setText(p.name or path)
        self._detail_path.setText(path)
        self._info_type.setText(kind)
        self._info_status.setText(state)
        self._info_children.setText(f"{child_count} 个子节点")
        self._info_block.show()
        self._render_child_preview(current_item)
        self._render_stats(path)
        if self._grid_panel.isVisible():
            self._media_block.hide()
        else:
            self._render_media_preview(path)
        self._sync_sticky_enter_from_primary()

    # ── T5 survey-summary: 多选派发 + 三栏切换 (spec §2) ───────────────────────
    def _on_tree_double_clicked(self, *_args) -> None:
        """双击进入工作区; 多选(≥2)态下保持多断面汇总预览, 不进拍照 (spec §2).

        §7 旧实现 (已注释保留在 _setup_ui L194):
            self._tree.itemDoubleClicked.connect(lambda *_: self._enter_selected())
        多选预览时双击会误跳拍照界面. 新实现: ≥2 选中 → 维持预览.
        """
        if len(self._tree.selectedItems()) >= 2:
            return
        self._enter_selected()

    def _all_visible_workspace_items(self) -> list:
        found: list = []
        for i in range(self._tree.topLevelItemCount()):
            self._collect_visible_workspaces(self._tree.topLevelItem(i), found)
        return found

    def _effective_scope_labeled(self) -> list[tuple[str, str]]:
        """当前汇总范围：选中节点下的工作区；未选时在「全部」筛选项下默认全部可见工作区."""
        labeled = getattr(self, "_scope_labeled", None) or []
        if labeled:
            return labeled
        if getattr(self, "_kind_filter", "all") == "all":
            ws_items = self._all_visible_workspace_items()
            if ws_items:
                return self._labeled_workspaces_from_items(ws_items)
        return []

    def _overview_source_items(self) -> list:
        items = getattr(self, "_selection_items", None) or []
        if items:
            return items
        return self._all_visible_workspace_items()

    def _on_content_mode_clicked(self, mode: str) -> None:
        self._set_content_mode(mode)
        if self._effective_scope_labeled():
            self._refresh_survey_overview(self._overview_source_items())
        self._apply_content_mode()

    def _set_content_mode(self, mode: str, *, persist: bool = True) -> None:
        mode = ptl.normalize_content_mode(mode)
        if persist:
            self.ctx.settings.project_tree_content_mode = mode

    def _sync_content_mode_buttons(self, mode: str | None = None) -> None:
        """兼容旧调用；中栏仅 data_summary，无切换按钮."""
        _ = ptl.normalize_content_mode(
            mode or self.ctx.settings.project_tree_content_mode
        )

    def _show_grid_idle(self) -> None:
        self._grid_idle_hint.show()
        self._grid_body.hide()
        if getattr(self, "_data_summary_panel", None) is not None:
            self._data_summary_panel.hide()
        self._set_data_summary_chrome(False)
        self._exit_preview_mode()
        self._preview_path = None
        if getattr(self, "_uid_grid", None) is not None:
            self._uid_grid.set_unified_grid(False)
            self._uid_grid.clear()
        self._current_merged = []
        self._current_ws_dirs = []
        self._grid_head.setText("内容预览")
        self._grid_count_lbl.setText("")
        self._set_grid_breadcrumb(None)

    def _show_grid_content(self) -> None:
        self._grid_idle_hint.hide()
        self._grid_body.show()

    def _apply_content_mode(self) -> None:
        """按当前选中范围刷新中栏（数据汇总）+ 右栏调查概览."""
        items = getattr(self, "_selection_items", None) or []
        labeled = self._effective_scope_labeled()
        panel = getattr(self, "_overview_panel", None)

        if not items and not labeled:
            self._hide_grid_panel()
            return

        if len(items) == 1:
            path = items[0].data(0, _PATH_ROLE)
            kind = items[0].data(0, _KIND_ROLE) or "folder"
            if path and kind == "candidate":
                self._hide_grid_panel()
                self._btn_adopt.show()
                return

        self._btn_adopt.hide()

        if not labeled:
            self._grid_panel.setVisible(True)
            self._show_grid_idle()
            return

        self._show_data_summary_scope(labeled)
        if panel is not None:
            panel.set_overview_sections_collapsed()

    def _toggle_specimen_table(self, expanded: bool) -> None:
        self._specimen_table.setVisible(expanded)
        host = getattr(self, "_summary_table_host", None)
        if host is not None:
            host.setVisible(expanded)
        self._btn_toggle_table.setText("编号列表 ▾" if expanded else "编号列表 ▸")
        split = getattr(self, "_summary_body_split", None)
        photo = getattr(self, "_photo_block", None)
        if split is None or photo is None or not photo.isVisible():
            return
        if expanded:
            self._restore_summary_body_split_state()
            return
        self._save_summary_body_split_state()
        total = max(sum(split.sizes()), split.height(), ptl.SUMMARY_BODY_TABLE_MIN)
        split.setSizes([0, total])

    def _toggle_photo_panel(self, show: bool) -> None:
        self.ctx.settings.project_tree_show_photos = bool(show)
        self._apply_photo_panel_visibility(bool(show))

    def _apply_photo_panel_visibility(self, show: bool) -> None:
        """隐藏成片后编号表占满；显示时恢复可拖分割条比例."""
        photo = getattr(self, "_photo_block", None)
        table = getattr(self, "_specimen_table", None)
        split = getattr(self, "_summary_body_split", None)
        btn = getattr(self, "_btn_toggle_photos", None)
        if photo is not None:
            photo.setVisible(show)
        if btn is not None:
            btn.blockSignals(True)
            btn.setChecked(show)
            btn.setText("成片 ▾" if show else "成片 ▸")
            btn.blockSignals(False)
        if table is not None:
            table.setMinimumHeight(72)
            table.setMaximumHeight(16777215)
        if split is None:
            return
        if show:
            self._restore_summary_body_split_state()
            return
        self._save_summary_body_split_state()
        total = max(sum(split.sizes()), split.height(), ptl.SUMMARY_BODY_DEFAULT_HEIGHT)
        split.setSizes([total, 0])

    def _set_data_summary_chrome(self, active: bool) -> None:
        """数据汇总模式：隐藏重复标题，成片区占满宽度."""
        self._grid_head.setVisible(not active)
        self._grid_breadcrumb.setVisible(not active)
        self._grid_count_lbl.setVisible(not active)
        center_row = getattr(self, "_grid_center_mode_row", None)
        if center_row is not None:
            center_row.setVisible(not active)
        quick_row = getattr(self, "_quick_row_host", None)
        if quick_row is not None:
            quick_row.setVisible(not active)
        if active:
            self._uid_index.hide()
        elif self._current_merged:
            self._uid_index.show()
        self._sync_summary_filter_toggle_label()

    def _toggle_summary_filter_panel(self, expanded: bool) -> None:
        panel = getattr(self, "_summary_filter", None)
        if panel is not None:
            panel.set_body_expanded(bool(expanded))
        self._sync_summary_filter_toggle_label()

    def _sync_summary_filter_toggle_label(self) -> None:
        btn = getattr(self, "_btn_toggle_filter", None)
        panel = getattr(self, "_summary_filter", None)
        if btn is None or panel is None:
            return
        expanded = panel.is_body_expanded()
        btn.setChecked(expanded)
        n = panel.active_condition_count()
        suffix = f" ({n})" if n else ""
        btn.setText(f"筛选 {'▾' if expanded else '▸'}{suffix}")

    def _show_data_summary_scope(self, labeled: list[tuple[str, str]]) -> None:
        """数据汇总：通用筛选 + 表格 + 按编号成片网格."""
        dirs = [p for p, _ in labeled]
        labels_map = {p: lbl for p, lbl in labeled}
        label_list = [labels_map.get(d, Path(d).name) for d in dirs]
        self._set_data_summary_chrome(True)
        self._grid_panel.setVisible(True)
        self._show_grid_content()
        self._data_summary_panel.show()
        self._current_ws_dirs = dirs
        self._summary_filter.set_workspaces(dirs)
        if not self._summary_conditions:
            self._summary_conditions = self._summary_filter.conditions()
        if self._summary_conditions:
            self._summary_filter.set_body_expanded(True)
            self._sync_summary_filter_toggle_label()
        self._run_data_summary_query(dirs, label_list, labels_map)

    def _on_summary_filter_changed(self, conditions: list) -> None:
        self._summary_conditions = list(conditions or [])
        if self._summary_conditions:
            self._summary_filter.set_body_expanded(True)
            self._btn_toggle_filter.setChecked(True)
            self._sync_summary_filter_toggle_label()
        labeled = self._effective_scope_labeled()
        if not labeled:
            return
        dirs = [p for p, _ in labeled]
        labels_map = {p: lbl for p, lbl in labeled}
        label_list = [labels_map.get(d, Path(d).name) for d in dirs]
        self._run_data_summary_query(dirs, label_list, labels_map)

    def _run_data_summary_query(
        self,
        dirs: list[str],
        label_list: list[str],
        labels_map: dict[str, str],
    ) -> None:
        from app.services import cross_workspace_query_service as cwq

        result = cwq.query_summary_scope(
            dirs,
            self._summary_conditions,
            labels=label_list,
        )
        self._current_summary_result = result
        self._current_merged = list(result.groups)
        self._summary_row_uid_order = None
        self._summary_table_sort_key = None
        self._summary_table_column_filters = {}
        stats = result.stats
        n_spec = stats.get("specimen_count", 0)
        n_photo = stats.get("photo_count", 0)
        n_rna = stats.get("rna_count", 0)
        cond_note = f" · {len(self._summary_conditions)} 个条件" if self._summary_conditions else " · 全部"
        self._summary_stats_lbl.setText(
            f"{n_spec} 编号 · {n_photo} 照片 · RNA {n_rna}{cond_note}"
        )
        self._grid_count_lbl.setText(f"{n_spec} 编号 · {n_photo} 张")
        self._summary_all_columns = cwq.summary_all_columns(dirs)
        self._summary_visible_columns = cwq.resolve_summary_visible_columns(
            self._summary_all_columns,
            _read_summary_visible_column_keys(self.ctx.settings),
        )
        self._rebuild_specimen_table_structure()
        self._refresh_specimen_table()
        self._apply_summary_groups_to_grid(self._groups_for_summary_display())
        panel = getattr(self, "_overview_panel", None)
        if panel is not None:
            scope = f"数据汇总 · {len(dirs)} 个工作区"
            if self._summary_conditions:
                scope += f" · {len(self._summary_conditions)} 项筛选"
            panel.set_filtered_stats(
                stats,
                workspace_dirs=dirs,
                labels=labels_map,
                scope_label=scope,
            )
        stack = getattr(self, "_right_stack", None)
        if stack is not None:
            stack.setCurrentIndex(1)
        self._schedule_tiff_preview_warmup(result)

    def _schedule_tiff_preview_warmup(self, result) -> None:
        """数据汇总查询后后台预热 TIFF 高清预览 JPG，不阻塞表格/网格。"""
        from app.services.tiff_preview_warmup_service import collect_tif_paths_from_summary
        from app.workers.tiff_preview_warmup_worker import TiffPreviewWarmupWorker

        paths = collect_tif_paths_from_summary(
            getattr(result, "specimens", None) or [],
            getattr(result, "groups", None) or [],
        )
        if not paths:
            return

        worker = getattr(self, "_tif_preview_warmup_worker", None)
        if worker is not None:
            try:
                if worker.isRunning():
                    worker.cancel()
                    worker.wait(200)
            except Exception:
                pass

        w = TiffPreviewWarmupWorker(paths, parent=self)
        self._tif_preview_warmup_worker = w
        w.finished_result.connect(self._on_tiff_preview_warmup_done)
        w.start()

    def _on_tiff_preview_warmup_done(self, warmup_result) -> None:
        created = int(getattr(warmup_result, "created", 0) or 0)
        if created <= 0:
            return
        try:
            groups = self._groups_for_summary_display()
            self._apply_summary_groups_to_grid(groups)
        except Exception:
            pass

    def _stop_tiff_preview_warmup_worker(self) -> None:
        worker = getattr(self, "_tif_preview_warmup_worker", None)
        if worker is None:
            return
        try:
            if worker.isRunning():
                worker.cancel()
                worker.wait(500)
        except Exception:
            pass
        self._tif_preview_warmup_worker = None

    def _rebuild_specimen_table_structure(self) -> None:
        cols = getattr(self, "_summary_visible_columns", [])
        table = getattr(self, "_specimen_table", None)
        if table is None:
            return
        table.setColumnCount(len(cols))
        labels = [label for _key, label in cols]
        table.setHorizontalHeaderLabels(self._specimen_header_labels(labels))
        self._configure_specimen_table_scroll()
        hdr = table.horizontalHeader()
        hdr.setSectionsMovable(True)
        hdr.setFirstSectionMovable(True)

    def _specimen_header_labels(self, base_labels: list[str]) -> list[str]:
        cols = getattr(self, "_summary_visible_columns", [])
        filters = getattr(self, "_summary_table_column_filters", {}) or {}
        sort_key = getattr(self, "_summary_table_sort_key", None)
        sort_asc = bool(getattr(self, "_summary_table_sort_asc", True))
        out: list[str] = []
        for idx, label in enumerate(base_labels):
            text = label
            key = cols[idx][0] if idx < len(cols) else ""
            if key and filters.get(key):
                text = f"▾ {text}"
            if key and key == sort_key:
                text = f"{text} {'↑' if sort_asc else '↓'}"
            out.append(text)
        return out

    def _base_specimen_rows(self) -> list:
        result = getattr(self, "_current_summary_result", None)
        if result is None:
            return []
        return list(result.specimens or [])

    def _specimen_rows_for_table(self) -> list:
        from app.utils.summary_table_ops import (
            apply_column_filters,
            sort_specimen_rows,
        )

        rows = self._base_specimen_rows()
        cell_value = getattr(self, "_summary_cell_value", None)
        if cell_value is None:
            return rows
        rows = apply_column_filters(
            rows,
            getattr(self, "_summary_table_column_filters", {}) or {},
            cell_value=cell_value,
        )
        sort_key = getattr(self, "_summary_table_sort_key", None)
        if sort_key:
            rows = sort_specimen_rows(
                rows,
                sort_key,
                ascending=bool(getattr(self, "_summary_table_sort_asc", True)),
                cell_value=cell_value,
            )
            return rows
        return self._order_specimen_rows(rows)

    def _refresh_specimen_table(self) -> None:
        self._populate_specimen_table(self._specimen_rows_for_table())

    def _column_key_at(self, logical_index: int) -> str:
        cols = getattr(self, "_summary_visible_columns", [])
        if logical_index < 0 or logical_index >= len(cols):
            return ""
        return str(cols[logical_index][0])

    def _on_specimen_header_clicked(self, logical_index: int) -> None:
        key = self._column_key_at(logical_index)
        if not key:
            return
        current = getattr(self, "_summary_table_sort_key", None)
        asc = bool(getattr(self, "_summary_table_sort_asc", True))
        if current != key:
            self._summary_table_sort_key = key
            self._summary_table_sort_asc = True
        elif asc:
            self._summary_table_sort_asc = False
        else:
            self._summary_table_sort_key = None
            self._summary_table_sort_asc = True
        self._summary_row_uid_order = None
        self._rebuild_specimen_table_structure()
        self._refresh_specimen_table()

    def _on_specimen_header_context_menu(self, pos) -> None:
        table = getattr(self, "_specimen_table", None)
        if table is None:
            return
        hdr = table.horizontalHeader()
        logical = hdr.logicalIndexAt(pos)
        key = self._column_key_at(logical)
        if not key:
            return
        cols = getattr(self, "_summary_visible_columns", [])
        label = cols[logical][1] if logical < len(cols) else key
        global_pos = hdr.mapToGlobal(pos)
        menu = QMenu(self)
        act_asc = menu.addAction("升序排序")
        act_asc.triggered.connect(
            lambda: self._set_specimen_header_sort(key, ascending=True)
        )
        act_desc = menu.addAction("降序排序")
        act_desc.triggered.connect(
            lambda: self._set_specimen_header_sort(key, ascending=False)
        )
        act_clear_sort = menu.addAction("清除排序")
        act_clear_sort.triggered.connect(lambda: self._set_specimen_header_sort(None))
        menu.addSeparator()
        act_filter = menu.addAction("筛选此列…")
        act_filter.triggered.connect(
            lambda: self._open_specimen_column_filter(key, label)
        )
        act_clear_filter = menu.addAction("清除此列筛选")
        act_clear_filter.triggered.connect(
            lambda: self._clear_specimen_column_filter(key)
        )
        menu.addSeparator()
        act_clear_all = menu.addAction("清除全部表头筛选")
        act_clear_all.triggered.connect(self._clear_all_specimen_column_filters)
        menu.exec(global_pos)

    def _set_specimen_header_sort(
        self,
        key: str | None,
        *,
        ascending: bool = True,
    ) -> None:
        self._summary_table_sort_key = key
        self._summary_table_sort_asc = ascending
        if key:
            self._summary_row_uid_order = None
        self._rebuild_specimen_table_structure()
        self._refresh_specimen_table()

    def _open_specimen_column_filter(self, key: str, label: str) -> None:
        from app.utils.summary_table_ops import unique_column_values
        from app.widgets.summary_column_filter_dialog import SummaryColumnFilterDialog

        cell_value = getattr(self, "_summary_cell_value", None)
        if cell_value is None:
            return
        values = unique_column_values(
            self._base_specimen_rows(),
            key,
            cell_value=cell_value,
        )
        if not values:
            ui.info(self, "筛选", "此列没有可筛选的值。")
            return
        current = (getattr(self, "_summary_table_column_filters", {}) or {}).get(key)
        dlg = SummaryColumnFilterDialog(
            label,
            values,
            selected=set(current) if current else None,
            parent=self,
        )
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        picked = dlg.selected_values()
        if picked is None:
            return
        filters = dict(getattr(self, "_summary_table_column_filters", {}) or {})
        if picked:
            filters[key] = set(picked)
        else:
            filters.pop(key, None)
        self._summary_table_column_filters = filters
        self._rebuild_specimen_table_structure()
        self._refresh_specimen_table()

    def _clear_specimen_column_filter(self, key: str) -> None:
        filters = dict(getattr(self, "_summary_table_column_filters", {}) or {})
        if key not in filters:
            return
        filters.pop(key, None)
        self._summary_table_column_filters = filters
        self._rebuild_specimen_table_structure()
        self._refresh_specimen_table()

    def _clear_all_specimen_column_filters(self) -> None:
        if not getattr(self, "_summary_table_column_filters", None):
            return
        self._summary_table_column_filters = {}
        self._rebuild_specimen_table_structure()
        self._refresh_specimen_table()

    def _configure_specimen_table_interaction(self) -> None:
        """编号表：行内拖动排序、表头拖动调列序、选中联动成片。"""
        table = getattr(self, "_specimen_table", None)
        if table is None or getattr(self, "_specimen_table_interaction_ready", False):
            return
        table.setDragEnabled(True)
        table.setAcceptDrops(True)
        table.setDropIndicatorShown(True)
        table.setDragDropOverwriteMode(False)
        table.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        table.setDefaultDropAction(Qt.DropAction.MoveAction)
        model = table.model()
        if model is not None:
            model.rowsMoved.connect(self._on_specimen_table_rows_moved)
        hdr = table.horizontalHeader()
        hdr.sectionMoved.connect(self._on_summary_header_section_moved)
        hdr.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        hdr.customContextMenuRequested.connect(self._on_specimen_header_context_menu)
        hdr.sectionClicked.connect(self._on_specimen_header_clicked)
        self._specimen_table_interaction_ready = True

    def _row_uid_from_table_item(self, row: int) -> str:
        table = getattr(self, "_specimen_table", None)
        if table is None or row < 0:
            return ""
        item = table.item(row, 0)
        if item is None:
            return ""
        data = item.data(Qt.ItemDataRole.UserRole) or {}
        return str(data.get("uid") or "")

    def _table_row_uids(self) -> list[str]:
        table = getattr(self, "_specimen_table", None)
        if table is None:
            return []
        out: list[str] = []
        for row in range(table.rowCount()):
            uid = self._row_uid_from_table_item(row)
            if uid:
                out.append(uid)
        return out

    def _selected_specimen_uids(self) -> set[str]:
        table = getattr(self, "_specimen_table", None)
        if table is None:
            return set()
        uids: set[str] = set()
        for idx in table.selectionModel().selectedRows():
            uid = self._row_uid_from_table_item(idx.row())
            if uid:
                uids.add(uid)
        return uids

    def _order_specimen_rows(self, rows: list) -> list:
        order = getattr(self, "_summary_row_uid_order", None)
        if not order:
            return list(rows or [])
        by_uid = {str(r.get("uid") or ""): r for r in (rows or [])}
        out: list = []
        seen: set[str] = set()
        for uid in order:
            row = by_uid.get(uid)
            if row is not None:
                out.append(row)
                seen.add(uid)
        for row in rows or []:
            uid = str(row.get("uid") or "")
            if uid and uid not in seen:
                out.append(row)
        return out

    def _groups_for_summary_display(self, uid_filter: set[str] | None = None) -> list:
        merged = list(getattr(self, "_current_merged", None) or [])
        if not merged:
            return []
        by_uid = {
            str(g.get("uid") or ""): g
            for g in merged
            if str(g.get("uid") or "")
        }
        order = self._table_row_uids()
        if not order:
            custom = getattr(self, "_summary_row_uid_order", None)
            order = list(custom or [str(g.get("uid") or "") for g in merged])
        out: list = []
        seen: set[str] = set()
        for uid in order:
            if uid_filter is not None and uid not in uid_filter:
                continue
            group = by_uid.get(uid)
            if group is not None:
                out.append(group)
                seen.add(uid)
        if uid_filter is not None:
            return out
        for group in merged:
            uid = str(group.get("uid") or "")
            if uid and uid not in seen:
                out.append(group)
        return out

    def _refresh_summary_grid_count_label(
        self,
        groups: list,
        *,
        selected_uid_count: int | None = None,
    ) -> None:
        n_photo = sum(len(g.get("items") or []) for g in groups)
        if selected_uid_count is not None and selected_uid_count > 0:
            self._grid_count_lbl.setText(
                f"已选 {selected_uid_count} 编号 · {n_photo} 张"
            )
            return
        result = getattr(self, "_current_summary_result", None)
        if result is not None:
            n_spec = result.stats.get("specimen_count", len(groups))
            self._grid_count_lbl.setText(f"{n_spec} 编号 · {n_photo} 张")
            return
        self._grid_count_lbl.setText(f"{len(groups)} 编号 · {n_photo} 张")

    def _sync_summary_grid_from_table(self) -> None:
        selected = self._selected_specimen_uids()
        if selected:
            groups = self._groups_for_summary_display(uid_filter=selected)
            self._apply_summary_groups_to_grid(groups)
            self._refresh_summary_grid_count_label(
                groups,
                selected_uid_count=len(selected),
            )
            for uid in self._table_row_uids():
                if uid in selected:
                    self._uid_grid.scroll_to_uid(uid)
                    break
            return
        groups = self._groups_for_summary_display()
        self._apply_summary_groups_to_grid(groups)
        self._refresh_summary_grid_count_label(groups)

    def _persist_summary_column_order_from_header(self) -> None:
        table = getattr(self, "_specimen_table", None)
        cols = list(getattr(self, "_summary_visible_columns", []) or [])
        if table is None or not cols:
            return
        hdr = table.horizontalHeader()
        new_cols: list[tuple[str, str]] = []
        for visual in range(hdr.count()):
            logical = hdr.logicalIndex(visual)
            if 0 <= logical < len(cols):
                new_cols.append(cols[logical])
        if not new_cols:
            return
        self._summary_visible_columns = new_cols
        _write_summary_visible_column_keys(
            self.ctx.settings,
            [key for key, _label in new_cols],
        )

    def _on_specimen_table_rows_moved(
        self,
        _parent,
        _start: int,
        _end: int,
        _destination,
        _row: int,
    ) -> None:
        order = self._table_row_uids()
        if order:
            self._summary_row_uid_order = order
        self._sync_summary_grid_from_table()

    def _on_summary_header_section_moved(
        self,
        _logical: int,
        _old_visual: int,
        _new_visual: int,
    ) -> None:
        self._persist_summary_column_order_from_header()

    def _configure_specimen_table_scroll(self) -> None:
        """编号表：列宽可拖、底部横向滚动条拖动浏览."""
        table = getattr(self, "_specimen_table", None)
        if table is None:
            return
        hdr = table.horizontalHeader()
        hdr.setStretchLastSection(False)
        hdr.setMinimumSectionSize(48)
        for col in range(table.columnCount()):
            hdr.setSectionResizeMode(col, QHeaderView.ResizeMode.Interactive)
        width_hints = {
            "uid": 240,
            "photo_absolute_path": 320,
            "_workspace_label": 88,
            "province": 64,
            "site": 72,
            "geo_area": 64,
            "station": 56,
            "storage": 64,
            "collector": 72,
            "identifier": 72,
            "photographer": 72,
            "lon": 72,
            "lat": 72,
            "collection_date": 88,
            "photo_date": 88,
            "scientific_name": 140,
            "scientific_name_cn": 88,
            "family_cn": 56,
            "genus_cn": 56,
            "taxon_group_cn": 64,
            "notes": 120,
        }
        cols = getattr(self, "_summary_visible_columns", ())
        for col, (key, _label) in enumerate(cols):
            if col < table.columnCount():
                table.setColumnWidth(col, width_hints.get(key, 72))
        table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        table.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        table.setWordWrap(False)

    def _fit_specimen_table_columns(self) -> None:
        """按内容加宽列，超出视口时出现底部横向滚动条."""
        table = getattr(self, "_specimen_table", None)
        if table is None or table.rowCount() == 0:
            return
        min_hints = {
            "uid": 200,
            "photo_absolute_path": 200,
            "_workspace_label": 72,
            "province": 56,
            "site": 56,
            "geo_area": 48,
            "station": 48,
            "storage": 56,
            "collector": 56,
            "identifier": 56,
            "photographer": 56,
            "lon": 56,
            "lat": 56,
            "collection_date": 72,
            "photo_date": 72,
            "scientific_name": 100,
            "scientific_name_cn": 56,
            "family_cn": 48,
            "genus_cn": 48,
            "taxon_group_cn": 48,
            "notes": 80,
        }
        cols = getattr(self, "_summary_visible_columns", ())
        table.resizeColumnsToContents()
        for col, (key, _label) in enumerate(cols):
            if col < table.columnCount():
                table.setColumnWidth(col, max(min_hints.get(key, 48), table.columnWidth(col)))

    def _populate_specimen_table(self, rows: list) -> None:
        cols = getattr(self, "_summary_visible_columns", ())
        cell_value = getattr(self, "_summary_cell_value", None)
        ordered_rows = self._order_specimen_rows(rows)
        self._specimen_table.setRowCount(0)
        for r in ordered_rows:
            idx = self._specimen_table.rowCount()
            self._specimen_table.insertRow(idx)
            for col, (key, _label) in enumerate(cols):
                text = cell_value(r, key) if cell_value else str(r.get(key) or "")
                item = QTableWidgetItem(text)
                item.setData(Qt.ItemDataRole.UserRole, r)
                # 单元格不设 ToolTip，避免拖动行时浮层遮挡
                self._specimen_table.setItem(idx, col, item)
        self._fit_specimen_table_columns()

    def _show_summary_table_help(self) -> None:
        ui.info(
            self,
            "编号表操作",
            "单击表头：排序（升序→降序→恢复）\n"
            "右键表头：筛选此列\n"
            "拖动行：调整编号顺序\n"
            "拖动表头：调整列顺序\n"
            "选中行：下方成片只显示对应编号\n"
            "表格下分割条：上下拖动调高度",
        )

    def _open_summary_column_picker(self) -> None:
        from app.services import cross_workspace_query_service as cwq
        from app.utils import ui
        from app.widgets.summary_column_picker_dialog import SummaryColumnPickerDialog

        all_cols = getattr(self, "_summary_all_columns", [])
        if not all_cols:
            ui.info(self, "显示列", "请先选择工作区并加载数据汇总。")
            return
        dlg = SummaryColumnPickerDialog(
            all_cols,
            [key for key, _label in self._summary_visible_columns],
            parent=self,
        )
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        keys = dlg.selected_keys()
        if not keys:
            ui.warn(self, "显示列", "请至少选择一列。")
            return
        _write_summary_visible_column_keys(self.ctx.settings, keys)
        self._summary_visible_columns = cwq.resolve_summary_visible_columns(all_cols, keys)
        self._rebuild_specimen_table_structure()
        result = getattr(self, "_current_summary_result", None)
        if result is not None:
            self._refresh_specimen_table()

    def _on_specimen_table_selection_changed(self) -> None:
        self._sync_summary_grid_from_table()

    def _apply_summary_groups_to_grid(self, groups: list) -> None:
        self._uid_grid.set_unified_grid(False)
        self._present_grid_panel(None)
        QApplication.processEvents()
        density = self.ctx.settings.project_tree_grid_density
        fit = ptl.thumb_size_for_density(self._uid_grid._viewport_width(), density)
        self._uid_grid.set_density_index(density)
        self._uid_grid.set_groups(groups, thumb_size=fit)
        self._sync_density_slider(density)

    def _open_tiff_jpeg_export_for_selection(self) -> None:
        """选中编号 → 打开 TIFF 转 JPG 工具，预填母版路径，默认高清存档。"""
        from app.services.tiff_preview_warmup_service import collect_tif_paths_from_summary

        selected = self._selected_specimen_uids()
        if not selected:
            ui.info(self, "转 JPG", "请先在编号表中选中要转换的编号行。")
            return
        result = getattr(self, "_current_summary_result", None)
        if result is None or not result.specimens:
            ui.info(self, "转 JPG", "当前没有可转换的汇总数据。")
            return
        rows = [
            row for row in result.specimens
            if str(row.get("uid") or "") in selected
        ]
        paths = collect_tif_paths_from_summary(rows, result.groups)
        if not paths:
            ui.info(self, "转 JPG", "选中编号没有可转换的 TIF 文件。")
            return

        self.ctx.pending_tiff_jpeg_sources = paths
        self.ctx.pending_tiff_jpeg_preset_id = "archive"
        main_win = self.window()
        if hasattr(main_win, "navigate_to"):
            main_win.navigate_to("tiff_jpeg_tool")
        else:
            ui.warn(self, "转 JPG", "无法打开 TIFF 转 JPG 页面。")

    def _export_filtered_specimens(self) -> None:
        from app.services import cross_workspace_query_service as cwq
        from app.utils import ui

        result = getattr(self, "_current_summary_result", None)
        if result is None or not result.specimens:
            ui.info(self, "导出", "当前没有可导出的筛选结果。")
            return
        path = ui.get_save_file_name(
            self,
            "导出筛选结果 CSV",
            "filtered_specimens.csv",
            "CSV (*.csv)",
        )
        if not path:
            return
        try:
            all_cols = getattr(self, "_summary_all_columns", None)
            if not all_cols:
                all_cols = cwq.summary_all_columns(result.workspaces)
            out = cwq.export_filtered_specimens_csv(
                result.specimens,
                path,
                columns=all_cols,
            )
            ui.info(
                self,
                "导出",
                f"已导出 {len(result.specimens)} 条 · {len(all_cols)} 列到：\n{out}",
            )
        except OSError as exc:
            ui.warn(self, "导出失败", str(exc))

    def _show_unified_multi_photos(self, dirs: list[str]) -> None:
        """多断面成片：单网格平铺全部照片."""
        self._grid_head.setText("成片预览")
        merged = self._collect_merged_groups(dirs)
        total = sum(len(g.get("items") or []) for g in merged)
        self._grid_count_lbl.setText(f"{len(dirs)} 个断面 · {total} 张")
        self._uid_grid.set_unified_grid(True)
        self._present_grid_panel(None)
        QApplication.processEvents()
        density = self.ctx.settings.project_tree_grid_density
        fit = ptl.thumb_size_for_density(self._uid_grid._viewport_width(), density)
        self._uid_grid.set_density_index(density)
        self._uid_grid.set_groups(merged, thumb_size=fit)
        self._sync_density_slider(density)

    def _on_tree_selection_changed(self) -> None:
        """selectionChanged: 更新范围 + 右栏 KPI；中栏按内容模式按需加载.

        v0.56 治理: 轻量 UI 更新立即做; 跨工作区聚合(右栏概览 + 中栏数据汇总)
        合并到下一次事件循环再跑——选中高亮先绘制, 连续点选/Shift 框选只聚合一次,
        且聚合全程有异常守护(坏库/锁库不再把异常抛出 Qt slot 留下半截界面)。
        """
        items = self._tree.selectedItems()
        self._selection_items = list(items)
        self._scope_labeled = self._labeled_workspaces_from_items(items)
        self._run_scope_refresh()

    def _run_scope_refresh(self) -> None:
        """选中范围刷新(右栏概览 + 中栏数据汇总 + 状态/详情), 带异常守护.

        执行顺序与 v0.55 逐字相同(detail 面板分支依赖 _grid_panel 可见性,
        必须在 _apply_content_mode 之后跑)。守护: 坏库/锁库不把异常抛出
        Qt slot 留下半截界面。注: 曾尝试 0ms 定时器合并突发选中, 在多视图
        并存场景触发销毁竞态 segfault, 撤回为同步。
        """
        try:
            effective = self._effective_scope_labeled()
            if effective:
                self._refresh_survey_overview(self._overview_source_items())
            elif getattr(self, "_right_stack", None) is not None:
                self._right_stack.setCurrentIndex(0)
            self._sync_content_mode_buttons()
            self._apply_content_mode()
            self._update_scope_status_label()
            self._update_detail_panel_for_selected_project()
        except (sqlite3.Error, OSError) as exc:
            # 某个子工作区库损坏/被锁: 收起中栏避免半截界面, 状态栏提示, 不炸 slot。
            self._hide_grid_panel()
            win = self.window()
            bar = getattr(win, "statusBar", None)
            if callable(bar):
                try:
                    bar().showMessage(f"汇总读取失败: {exc}", 5000)
                except Exception:
                    pass

    def _hide_grid_panel(self) -> None:
        if getattr(self, "_grid_panel", None) is not None:
            self._grid_panel.setVisible(False)
        self._exit_preview_mode()
        self._preview_path = None
        if getattr(self, "_uid_grid", None) is not None:
            self._uid_grid.set_unified_grid(False)
            self._uid_grid.clear()
        if getattr(self, "_grid_idle_hint", None) is not None:
            self._grid_idle_hint.hide()
        if getattr(self, "_grid_body", None) is not None:
            self._grid_body.hide()
        if getattr(self, "_data_summary_panel", None) is not None:
            self._data_summary_panel.hide()
        self._current_merged = []
        self._current_ws_dirs = []
        self._current_summary_result = None
        self._set_grid_breadcrumb(None)

    def _show_single_workspace_grid(self, path: str) -> None:
        from app.services.project_service import get_project_results

        self._grid_head.setText("成片预览")
        self._btn_adopt.hide()
        res = get_project_results(path)
        groups = list(res.get("groups") or [])
        ungrouped = list(res.get("ungrouped") or [])
        if ungrouped:
            groups.append({"uid": "", "items": ungrouped})
        total = sum(len(g.get("items") or []) for g in groups)
        self._grid_count_lbl.setText(f"{total} 张")
        self._uid_grid.set_unified_grid(True)
        self._prepare_grid_panel(path)
        QApplication.processEvents()
        density = self.ctx.settings.project_tree_grid_density
        fit = ptl.thumb_size_for_density(self._uid_grid._viewport_width(), density)
        self._uid_grid.set_density_index(density)
        self._uid_grid.set_groups(groups, thumb_size=fit)
        self._sync_density_slider(density)

    def _show_folder_children_grid(self, item: QTreeWidgetItem) -> None:
        self._grid_head.setText("下级代表图")
        items: list[dict] = []
        for idx in range(item.childCount()):
            child = item.child(idx)
            child_path = child.data(0, _PATH_ROLE)
            if not child_path:
                continue
            cover = self._pick_cover_path(str(child_path))
            label = child.text(0).split("  ·  ", 1)[0]
            if cover:
                items.append({"path": cover, "name": label, "seq": None})
        self._grid_count_lbl.setText(f"{len(items)} 个")
        if not items:
            self._hide_grid_panel()
            return
        path = item.data(0, _PATH_ROLE)
        self._uid_grid.set_unified_grid(True)
        self._prepare_grid_panel(str(path) if path else None)
        QApplication.processEvents()
        density = self.ctx.settings.project_tree_grid_density
        fit = ptl.thumb_size_for_density(self._uid_grid._viewport_width(), density)
        self._uid_grid.set_density_index(density)
        self._uid_grid.set_groups(
            [{"uid": "下级文件夹", "items": items}],
            thumb_size=fit,
        )
        self._sync_density_slider(density)

    def _pick_cover_path(self, directory: str) -> Optional[str]:
        media = self._collect_media_preview(directory, limit=1)
        return str(media[0]) if media else None

    def _collect_labeled_workspaces(self, item, acc: list[tuple[str, str]]) -> None:
        path = item.data(0, _PATH_ROLE)
        kind = item.data(0, _KIND_ROLE)
        label = item.text(0).split("  ·  ", 1)[0]
        if path and kind == "workspace" and pts.is_workspace(str(path)):
            acc.append((str(path), label))
            return
        for i in range(item.childCount()):
            self._collect_labeled_workspaces(item.child(i), acc)

    def _labeled_workspaces_from_items(self, items: list) -> list[tuple[str, str]]:
        acc: list[tuple[str, str]] = []
        seen: set[str] = set()
        for it in items:
            found: list[tuple[str, str]] = []
            self._collect_labeled_workspaces(it, found)
            for path, label in found:
                if path not in seen:
                    seen.add(path)
                    acc.append((path, label))
        return acc

    def _refresh_survey_overview(self, items: list) -> None:
        """右栏调查概览: KPI + 分布 + 物种名录."""
        labeled = self._labeled_workspaces_from_items(items)
        stack = getattr(self, "_right_stack", None)
        panel = getattr(self, "_overview_panel", None)
        if stack is None or panel is None:
            return
        if not labeled:
            stack.setCurrentIndex(0)
            return
        dirs = [p for p, _ in labeled]
        labels = {p: lbl for p, lbl in labeled}
        scope_label = None
        if items:
            node_label = items[0].text(0).split("  ·  ", 1)[0]
            kind = items[0].data(0, _KIND_ROLE) or "folder"
            if (
                len(labeled) > 1
                and not self._is_rooted_view()
                and all((it.data(0, _KIND_ROLE) == "workspace") for it in items)
            ):
                scope_label = f"全部 {len(labeled)} 个项目"
            elif len(labeled) > 1 and kind != "workspace":
                scope_label = f"{node_label} — 汇总 {len(labeled)} 个断面"
            elif kind != "workspace":
                scope_label = f"{node_label}（{labeled[0][1]}）"
        panel.set_workspaces(dirs, labels=labels, scope_label=scope_label)
        stack.setCurrentIndex(1)

    @staticmethod
    def _uid_prefix(uid: str) -> str:
        """Specimen uniqueId without result sequence (for data-filter grid match)."""
        from app.utils.naming import extract_unique_id, normalize_uid, uid_group_key

        text = normalize_uid(str(uid or ""))
        if not text:
            return ""
        stripped = normalize_uid(extract_unique_id(text))
        return stripped or uid_group_key(text)

    def _collect_merged_groups(self, dirs: list[str]) -> list:
        """对每个选中断面调 ``project_service.get_project_results`` 合并 groups.

        复用 (spec §3):groups 直接拼接;ungrouped 合成单个「未分组」section
        (incoming 散片进未分组,spec §6 红线).任一断面读取失败静默跳过.
        """
        from app.services import project_service as _ps
        merged: list[dict] = []
        ungrouped_all: list[dict] = []
        for d in dirs:
            try:
                res = _ps.get_project_results(d)
            except Exception:
                continue
            for g in (res.get("groups") or []):
                merged.append({
                    "uid": str(g.get("uid") or ""),
                    "items": list(g.get("items") or []),
                })
            ungrouped_all.extend(res.get("ungrouped") or [])
        if ungrouped_all:
            merged.append({"uid": "", "items": ungrouped_all})
        return merged

    # ── T5 生命周期: UidGroupedGrid worker 线程防泄漏 (memory: workbench-timer-leak-hang) ──
    def on_deactivate(self) -> None:
        """切走页面:清空合并网格(保留 worker 线程供下次进入)."""
        self._stop_tiff_preview_warmup_worker()
        self._save_tree_split_state()
        self._save_grid_inner_split_state()
        self._save_summary_body_split_state()
        try:
            grid = getattr(self, "_uid_grid", None)
            if grid is not None:
                grid.clear()
            cards = getattr(self, "_card_grid", None)
            if cards is not None:
                cards.teardown()
        except Exception:  # pragma: no cover - 防御性
            pass

    def stop_background_work(self) -> None:
        """App 退出时 join worker 线程."""
        self._stop_tiff_preview_warmup_worker()
        try:
            grid = getattr(self, "_uid_grid", None)
            if grid is not None:
                grid.teardown()
            cards = getattr(self, "_card_grid", None)
            if cards is not None:
                cards.teardown()
        except Exception:  # pragma: no cover - 防御性
            pass

    def closeEvent(self, event) -> None:  # noqa: D401 - Qt override
        self.stop_background_work()
        super().closeEvent(event)

    def _set_enter_action_style(
        self,
        object_name: str,
        text: str,
        icon_name: str,
        *,
        color: Optional[str] = None,
    ) -> None:
        self._btn_enter.setObjectName(object_name)
        self._btn_enter.setText(text)
        icons.set_button_icon(
            self._btn_enter,
            icon_name,
            color=color or icons.TONE_ON_ACCENT,
            size=16,
        )
        self._btn_enter.style().unpolish(self._btn_enter)
        self._btn_enter.style().polish(self._btn_enter)
        sticky = getattr(self, "_btn_enter_sticky", None)
        if sticky is not None:
            sticky.setObjectName(object_name)
            sticky.setText(text)
            icons.set_button_icon(
                sticky,
                icon_name,
                color=color or icons.TONE_ON_ACCENT,
                size=16,
            )
            sticky.style().unpolish(sticky)
            sticky.style().polish(sticky)
            sticky.setVisible(self._ux_v2())
            sticky.setEnabled(self._btn_enter.isEnabled())
        adopt_s = getattr(self, "_btn_adopt_sticky", None)
        adopt = getattr(self, "_btn_adopt", None)
        if adopt_s is not None and adopt is not None:
            adopt_s.setVisible(self._ux_v2() and adopt.isVisible())
            adopt_s.setEnabled(adopt.isEnabled())
            adopt_s.setText(adopt.text())

    def _clear_stats(self) -> None:
        from app.utils.ui import clear_layout_widgets

        clear_layout_widgets(self._stats_row)

    def _clear_child_preview(self) -> None:
        from app.utils.ui import clear_layout_widgets

        clear_layout_widgets(self._child_list)

    def _clear_media_preview(self) -> None:
        from app.utils.ui import clear_layout_widgets

        clear_layout_widgets(self._media_grid)
        self._media_empty_lbl.show()
        self._media_count_lbl.setText("")
        self._media_block.hide()

    def _render_child_preview(self, item: Optional[QTreeWidgetItem]) -> None:
        self._clear_child_preview()
        if item is None or item.childCount() <= 0:
            self._child_block.hide()
            return
        count = item.childCount()
        self._child_count_lbl.setText(f"{count} 个")
        limit = min(count, 5)
        for idx in range(limit):
            child = item.child(idx)
            self._child_list.addWidget(self._make_child_preview_row(child))
        if count > limit:
            more = QLabel(f"另有 {count - limit} 个下级节点")
            more.setObjectName("ChildNodeMeta")
            more.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._child_list.addWidget(more)
        self._child_block.show()

    def _make_child_preview_row(self, item: QTreeWidgetItem) -> QWidget:
        row = QFrame()
        row.setObjectName("ChildNodeRow")
        row.setCursor(Qt.CursorShape.PointingHandCursor)
        row.setToolTip("")
        row.mousePressEvent = lambda event, target=item: self._select_preview_item(target, event)
        rl = QHBoxLayout(row)
        rl.setContentsMargins(8, 4, 8, 4)
        rl.setSpacing(8)
        icon_lbl = QLabel()
        icon_lbl.setFixedSize(18, 18)
        kind = item.data(0, _KIND_ROLE) or "folder"
        if kind == "workspace":
            glyph, tone, badge = "mdi6.database-outline", icons.TONE_ACCENT, "工作区"
        elif kind == "candidate":
            glyph, tone, badge = "mdi6.folder-search-outline", icons.TONE_WARN, "待导入"
        else:
            glyph, tone, badge = "mdi6.folder-outline", icons.TONE_MUTED, "文件夹"
        icon_lbl.setPixmap(icons.icon(glyph, color=tone).pixmap(16, 16))
        rl.addWidget(icon_lbl)
        text_col = QVBoxLayout()
        text_col.setSpacing(0)
        raw_name = item.text(0).split("  ·  ", 1)[0]
        name = QLabel(raw_name)
        name.setObjectName("ChildNodeName")
        meta_path = item.data(0, _PATH_ROLE) or ""
        meta = QLabel(str(Path(meta_path).name or meta_path))
        meta.setObjectName("ChildNodeMeta")
        text_col.addWidget(name)
        text_col.addWidget(meta)
        rl.addLayout(text_col, 1)
        badge_lbl = QLabel(badge)
        badge_lbl.setObjectName("ChildNodeBadge")
        rl.addWidget(badge_lbl)
        return row

    def _select_preview_item(self, item: QTreeWidgetItem, event) -> None:
        self._select_tree_item(item)
        event.accept()

    def _select_tree_item(self, item: QTreeWidgetItem) -> None:
        parent = item.parent()
        while parent is not None:
            parent.setExpanded(True)
            parent = parent.parent()
        self._tree.clearSelection()
        item.setHidden(False)
        item.setSelected(True)
        self._tree.setCurrentItem(item)
        self._tree.scrollToItem(item)

    def _render_stats(self, path: str) -> None:
        self._clear_stats()
        try:
            from app.services.project_service import get_project_summary
            s = get_project_summary(path)
            cards = [("specimen", str(s["specimenCount"]), "标本"),
                     ("result", str(s["resultCount"]), "成片"),
                     ("pending", str(s["pendingJpgCount"]), "待处理")]
        except Exception:
            cards = [("specimen", "—", "标本"), ("result", "—", "成片"),
                     ("pending", "—", "待处理")]
        for role, value, label in cards:
            card = QFrame()
            card.setObjectName("StatCard")
            card.setProperty("role", role)
            card.setMinimumHeight(78)
            cl = QVBoxLayout(card)
            cl.setContentsMargins(12, 9, 12, 9)
            cl.setSpacing(2)
            v = QLabel(value)
            v.setObjectName("StatValue")
            v.setProperty("role", role)
            v.setAlignment(Qt.AlignmentFlag.AlignCenter)
            t = QLabel(label)
            t.setObjectName("StatLabel")
            t.setAlignment(Qt.AlignmentFlag.AlignCenter)
            cl.addWidget(v)
            cl.addWidget(t)
            self._stats_row.addWidget(card, 1)

    def _render_media_preview(self, path: str) -> None:
        self._clear_media_preview()
        media = self._collect_media_preview(path)
        self._media_block.show()
        self._media_count_lbl.setText(f"{len(media)} 个" if media else "0 个")
        if not media:
            self._media_empty_lbl.setText("当前节点还没有 JPG / TIFF / PNG 影像预览。")
            self._media_empty_lbl.show()
            return
        self._media_empty_lbl.hide()
        for idx, item in enumerate(media[:6]):
            self._media_grid.addWidget(
                self._make_media_preview_card(item),
                idx // 3,
                idx % 3,
            )

    def _collect_media_preview(self, path: str, limit: int | None = None) -> list[Path]:
        if limit is None:
            perf = bool(getattr(self.ctx.settings, "performance_mode", False))
            limit = 3 if perf else 6
        root = Path(path)
        if not root.exists():
            return []
        image_exts = {".jpg", ".jpeg", ".png", ".tif", ".tiff"}
        found: list[Path] = []
        queue: list[tuple[Path, int]] = [(root, 0)]
        seen: set[str] = set()
        inspected = 0
        max_depth = 4
        max_inspected = 700
        while queue and inspected < max_inspected and len(found) < limit * 3:
            current, depth = queue.pop(0)
            try:
                resolved = str(current.resolve())
            except OSError:
                resolved = str(current)
            if resolved in seen:
                continue
            seen.add(resolved)
            try:
                entries = sorted(
                    current.iterdir(),
                    key=lambda p: p.stat().st_mtime if p.exists() else 0,
                    reverse=True,
                )
            except OSError:
                continue
            for entry in entries:
                inspected += 1
                if inspected >= max_inspected:
                    break
                if entry.name.startswith("."):
                    continue
                if entry.is_file() and entry.suffix.lower() in image_exts:
                    found.append(entry)
                    if len(found) >= limit * 3:
                        break
                elif entry.is_dir() and depth < max_depth:
                    if entry.name in pts.RESERVED_DIR_NAMES and entry.name not in {
                        "incoming-jpg",
                        "新拍JPG",
                        "results",
                    }:
                        continue
                    queue.append((entry, depth + 1))
        found.sort(key=lambda p: p.stat().st_mtime if p.exists() else 0, reverse=True)
        return found[:limit]

    def _make_media_preview_card(self, path: Path) -> QWidget:
        card = QFrame()
        card.setObjectName("MediaPreviewCard")
        card.setToolTip("")
        card.setCursor(Qt.CursorShape.PointingHandCursor)
        card.mousePressEvent = lambda event, p=path: self._open_directory(str(p.parent))
        lay = QVBoxLayout(card)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(6)
        thumb = QLabel()
        thumb.setObjectName("MediaThumb")
        thumb.setFixedSize(112, 78)
        thumb.setAlignment(Qt.AlignmentFlag.AlignCenter)
        pm = None
        try:
            from app.utils.image_thumbnail import decode_image_thumbnail
            pm = decode_image_thumbnail(str(path), max_size=150)
        except Exception:
            pm = None
        if pm is not None and not pm.isNull():
            thumb.setPixmap(
                pm.scaled(
                    thumb.size(),
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            )
        else:
            thumb.setText(path.suffix.upper().lstrip(".") or "FILE")
        lay.addWidget(thumb)
        name = QLabel()
        name.setObjectName("MediaName")
        name.setFixedWidth(112)
        name.setToolTip("")
        name.setText(name.fontMetrics().elidedText(
            path.name,
            Qt.TextElideMode.ElideMiddle,
            112,
        ))
        lay.addWidget(name)
        meta = QLabel(self._media_file_meta(path))
        meta.setObjectName("MediaMeta")
        lay.addWidget(meta)
        return card

    def _media_file_meta(self, path: Path) -> str:
        try:
            size = path.stat().st_size
        except OSError:
            return path.suffix.upper().lstrip(".")
        if size >= 1024 * 1024:
            size_text = f"{size / (1024 * 1024):.1f} MB"
        elif size >= 1024:
            size_text = f"{size / 1024:.0f} KB"
        else:
            size_text = f"{size} B"
        return f"{path.suffix.upper().lstrip('.')} · {size_text}"

    # ── Cross-workspace tools (append-only launchers) ──────────────────────────
    def _collect_workspaces_from_item(self, item, acc: list[str]) -> None:
        path = item.data(0, _PATH_ROLE)
        kind = item.data(0, _KIND_ROLE)
        if path and kind == "workspace" and pts.is_workspace(str(path)):
            acc.append(str(path))
            return
        for i in range(item.childCount()):
            self._collect_workspaces_from_item(item.child(i), acc)

    def _selected_filter_workspace_dirs(self) -> list[str]:
        items = self._tree.selectedItems()
        dirs: list[str] = []
        seen: set[str] = set()
        for it in items:
            found: list[str] = []
            self._collect_workspaces_from_item(it, found)
            for d in found:
                if d not in seen:
                    seen.add(d)
                    dirs.append(d)
        return dirs

    def _open_data_filter(self) -> None:
        """Open cross-workspace specimen filter (modal, preselects tree workspaces)."""
        from app.widgets.data_filter_dialog import open_data_filter_dialog

        preselect = self._selected_filter_workspace_dirs()
        open_data_filter_dialog(self.ctx, preselect_dirs=preselect or None, parent=self)

    def _open_summary_export(self) -> None:
        """Open the cross-workspace summary export, rooted at the selected node."""
        path = self._selected_path()
        if not path:
            ui.info(self, "汇总导出", "请先选择一个文件夹。")
            return
        from app.widgets.summary_export_dialog import SummaryExportDialog
        dlg = SummaryExportDialog(ctx=self.ctx, initial_root=path, parent=self)
        dlg.exec()

    def _open_station_species_summary(self) -> None:
        """Open station-level taxa and pending mixed-sample summary."""
        path = self._selected_path()
        if not path:
            ui.info(self, "分类名录", "请先选择一个文件夹。")
            return
        from app.widgets.station_species_summary_dialog import (
            StationSpeciesSummaryDialog,
        )
        dlg = StationSpeciesSummaryDialog(ctx=self.ctx, initial_root=path, parent=self)
        dlg.exec()

    def _open_station_import(self) -> None:
        """Open the project station total-table import, rooted at the selected node."""
        path = self._selected_path()
        if not path:
            ui.info(self, "导入站位总表", "请先选择一个文件夹。")
            return
        from app.widgets.project_station_import_dialog import (
            ProjectStationImportDialog,
        )
        dlg = ProjectStationImportDialog(root_dir=path, parent=self)
        dlg.exec()

    def _adopt_selected_candidate(self) -> None:
        path = self._selected_path()
        if path:
            self._run_adopt_flow(path)

    def _run_adopt_flow(self, directory: str) -> None:
        from app.services.project_adopt_service import adopt_project, prescan_project

        try:
            report = prescan_project(directory)
        except OSError as exc:
            ui.warn(self, "认领", f"无法扫描目录：\n{exc}")
            return
        lines = "\n".join(report.summary_lines())
        if not ui.question(
            self,
            "认领工作区",
            f"目录：{directory}\n\n{lines}\n\n确认认领？",
        ):
            return
        try:
            result = adopt_project(self.ctx, directory, root=self._root)
        except (FileNotFoundError, ValueError) as exc:
            ui.warn(self, "认领失败", str(exc))
            return
        except OSError as exc:
            ui.warn(self, "认领失败", str(exc))
            return
        if result == "already":
            ui.info(self, "认领", "该文件夹已是工作区。")
        else:
            ui.info(self, "认领", "已创建 _data/project.db 并登记到项目列表。")
        pts.clear_project_tree_cache(self._root)
        self._reload_project_tree()
        self._reload_card_grid()

    def _pick_root(self) -> None:
        start = self._root or (self.ctx.current_project_dir or "")
        path = ui.get_existing_directory(self, "选择项目根目录", start)
        if not path:
            return
        self._root = str(Path(path).resolve())
        self.ctx.settings.project_tree_root = self._root
        self.ctx.settings.project_tree_view_mode = "rooted"
        pts.clear_project_tree_cache(self._root)
        self._sync_view_mode_buttons()
        self._reload_project_tree()

    def _scan_disk(self) -> None:
        """扫描指定磁盘/目录, 把发现的工作区(含 legacy 候选)登记到项目列表.

        用户核心需求: 旧项目目录不在 user_projects.json 时, 指定盘/目录深扫,
        所有 is_workspace_candidate 的文件夹经 record_recent_workspace 去重登记 →
        立刻出现在 flat list + 项目总览. 同步扫 + 模态进度框(大目录可能慢,
        后续可改后台线程).
        """
        start = self._root or ""
        path = ui.get_existing_directory(self, "选择要扫描的磁盘或目录", start)
        if not path:
            return
        root = str(Path(path).resolve())
        progress = QProgressDialog("正在扫描工作区…", "取消", 0, 0, self)
        progress.setWindowTitle("扫描磁盘")
        progress.setWindowModality(Qt.WindowModality.ApplicationModal)
        progress.setMinimumDuration(0)
        progress.show()
        QApplication.processEvents()
        try:
            candidates = pts.discover_workspace_candidates(root, max_depth=6)
        except OSError:
            candidates = []
        progress.close()
        if not candidates:
            ui.info(self, "扫描完成", f"未在该目录发现工作区:\n{root}")
            return
        from app.services.project_service import (
            default_user_projects_json_path,
            list_projects,
            record_recent_workspace,
        )
        jp = default_user_projects_json_path()
        before = len(list_projects(jp))
        for c in candidates:
            try:
                record_recent_workspace(jp, c["path"], root=root)
            except Exception:
                continue
        added = len(list_projects(jp)) - before
        ui.info(
            self,
            "扫描完成",
            f"在「{Path(root).name}」下发现 {len(candidates)} 个工作区,\n"
            f"新增 {added} 个到项目列表(已登记的自动去重)。",
        )
        pts.clear_project_tree_cache(root)
        self.ctx.settings.project_tree_view_mode = "all"
        self._root = None
        self.ctx.settings.project_tree_root = None
        self._sync_view_mode_buttons()
        self._reload_project_tree()

    def _show_all_projects(self) -> None:
        """兼容旧入口：切到「全部项目」视图。"""
        self._set_view_mode("all")

    def _relocate_selected_path(self) -> None:
        old_path = self._selected_path()
        if not old_path:
            return
        start = str(Path(old_path).expanduser().parent) if old_path else ""
        new_path = ui.get_existing_directory(
            self,
            "指到新位置",
            start,
        )
        if not new_path:
            return
        from app.services.project_service import (
            default_user_projects_json_path,
            relocate_project_directory,
        )
        jp = default_user_projects_json_path()
        try:
            ok = relocate_project_directory(jp, old_path, new_path)
        except FileNotFoundError as exc:
            ui.warn(self, "指到新位置", str(exc))
            return
        except Exception as exc:  # noqa: BLE001
            ui.warn(self, "指到新位置", f"更新失败：{exc}")
            return
        if not ok:
            ui.info(
                self,
                "指到新位置",
                "新路径已选，但项目列表里没有找到旧路径记录。\n"
                "可改用「添加工作区」登记。",
            )
            return
        cur = getattr(self.ctx, "current_project_dir", None)
        if cur and str(cur) == str(old_path):
            self.ctx.current_project_dir = new_path
        pts.clear_project_tree_cache()
        self._reload_project_tree()
        ui.info(self, "已更新", f"项目路径已更新为：\n{new_path}")

    def _add_workspace_manual(self) -> None:
        """手动选单个目录登记为工作区/子节点(不扫整盘, 用于扫描识别不到的目录)。

        与 ``_scan_disk`` 互补: 扫描是"指定盘/目录深扫找全部"; 这是"我就要这一个"。
        选目录 → record_recent_workspace 去重登记 → 刷新 flat list。
        """
        start = self._root or ""
        path = ui.get_existing_directory(self, "选择要添加的工作区或子目录", start)
        if not path:
            return
        from app.services.project_service import (
            default_user_projects_json_path,
            record_recent_workspace,
        )
        try:
            record_recent_workspace(
                default_user_projects_json_path(), path, root=self._root
            )
        except Exception as exc:
            ui.warn(self, "添加失败", f"登记工作区失败:\n{exc}")
            return
        pts.clear_project_tree_cache(self._root or path)
        self._reload_project_tree()
        ui.info(self, "已添加", f"已登记到项目列表:\n{path}")

    def _new_region(self) -> None:
        """Scaffold a 调查区域 root: create the folder, seed region-level
        settings (地区/负责人) as the inheritance anchor, then make it the tree
        root so 断面 created under it auto-inherit (set once, never re-type)."""
        from app.views.project_dialog import ProjectDialog
        dlg = ProjectDialog(mode="new", existing_projects=[], parent=self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        proj = dlg.result_project()
        if not proj:
            return
        directory = proj.get("directory") or proj.get("dir") or ""
        if not directory:
            return
        try:
            from app.services.project_service import (
                default_user_projects_json_path,
                load_user_projects,
                save_project_descriptor,
                seed_region_settings,
            )
            seed_region_settings(
                directory,
                collector=proj.get("collector", ""),
                meta={
                    "name": proj.get("name", ""),
                    "location": proj.get("location", ""),
                    "year": proj.get("year", ""),
                    "date_range": proj.get("dateRange", ""),
                    "project_code": proj.get("projectCode", ""),
                },
            )
            save_project_descriptor(
                default_user_projects_json_path(),
                proj,
                existing_projects=load_user_projects(),
            )
        except Exception as exc:  # pragma: no cover - defensive
            ui.warn(self, "新建调查区域", f"创建失败：{exc}")
            return
        self._root = str(Path(directory).resolve())
        pts.clear_project_tree_cache(self._root)
        self.ctx.settings.project_tree_root = self._root
        self._reload_project_tree()
        ui.info(
            self,
            "新建调查区域",
            "区域已建。地区/负责人已设在区域层，下面新建的断面会自动继承——"
            "在断面里设省/市、地区/样地可覆盖。",
        )

    def _new_subfolder(self) -> None:
        parent = self._selected_path() or self._root
        if not parent:
            ui.info(self, "项目树", "请先选择根目录或一个文件夹。")
            return
        name, ok = QInputDialog.getText(self, "新建子文件夹", "文件夹名称（如 断面a）：")
        name = (name or "").strip()
        if not ok or not name:
            return
        if any(c in name for c in ("/", "\\", "..")):
            ui.warn(self, "项目树", "名称不合法（不能含 / \\ ..）。")
            return
        try:
            (Path(parent) / name).mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            ui.warn(self, "项目树", f"无法创建：{exc}")
            return
        pts.clear_project_tree_cache(self._root or parent)
        self._reload_project_tree()

    def _enter_selected(self) -> None:
        path = self._selected_path()
        if not path:
            return
        # 区域≠工作区: a node with subfolders that isn't yet a workspace is most
        # likely a 调查区域 (inheritance anchor), not where you shoot. Don't
        # forbid — just confirm, so a region doesn't accidentally become a
        # photo workspace.
        items = self._tree.selectedItems()
        item = items[0] if items else None
        if item is not None and item.childCount() > 0 and not pts.is_workspace(path):
            resp = ui.question(
                self,
                "进入工作区",
                f"「{Path(path).name}」下面还有子文件夹，看起来是调查区域。"
                "通常在下层断面里拍照。仍要把这一层当作工作区进入吗？",
            )
            from PyQt6.QtWidgets import QMessageBox
            if resp != QMessageBox.StandardButton.Yes:
                return
        # §7 旧: 统一入口逻辑原本内联在此(enter_workspace + 错误弹窗 + 导航),
        # v0.56 抽成 _enter_workspace_path 供卡片「进入」复用(修卡片空操作回归)。
        self._enter_workspace_path(path)

    def _enter_workspace_path(self, path: str) -> None:
        """Single unified entry path: ensures dirs, sets dir + root (bounding the
        settings-inheritance walk to this survey's tree), and records the node
        into the recent list so it also shows up in 项目总览."""
        from app.services.project_service import (
            default_user_projects_json_path,
            enter_workspace,
        )
        from app.db.db_manager import is_database_locked
        from app.services.project_paths import ProjectUnavailableError
        try:
            enter_workspace(
                self.ctx,
                path,
                root=self._root,
                projects_json_path=default_user_projects_json_path(),
            )
        except ProjectUnavailableError:
            ui.warn(self, "盘未连接",
                    f"该目录所在磁盘未挂载或路径不可用：\n{path}\n\n"
                    "请接回数据盘后再进入。数据仍在盘上，没有丢失。")
            return
        except sqlite3.Error as exc:
            if is_database_locked(exc):
                ui.warn(
                    self,
                    "项目数据库正忙",
                    "当前项目数据库正在被其它操作占用。\n\n"
                    "请稍等几秒后重试；如果一直出现，请关闭其它正在打开该项目的程序窗口，"
                    "或先回到照片工作区停止正在运行的合成/整理任务。",
                )
                return
            ui.warn(
                self,
                "打开项目失败",
                f"项目数据库无法打开：\n{exc}\n\n"
                "请先关闭其它正在使用该项目的窗口后重试。",
            )
            return
        pts.clear_project_tree_cache(self._root or path)
        self.enter_workspace_requested.emit(path)
        main_win = self.window()
        if hasattr(main_win, "refresh_context_bar"):
            main_win.refresh_context_bar()
        if hasattr(main_win, "navigate_to"):
            main_win.navigate_to("workbench")
