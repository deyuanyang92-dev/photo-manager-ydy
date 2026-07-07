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

from PyQt6.QtCore import QSize, Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QDialog,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMenu,
    QPushButton,
    QGridLayout,
    QSplitter,
    QStackedWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.config import icons
from app.config.theme import local_font_css
from app.services import project_tree_service as pts
from app.utils import ui
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
        super().__init__(ctx)

    # ── UI ──────────────────────────────────────────────────────────────────
    def _setup_ui(self) -> None:
        self._apply_style()
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(14)

        # Header bar: current root context plus the directory-management actions.
        header = QFrame()
        header.setObjectName("ProjectTreeHeader")
        bar = QHBoxLayout(header)
        bar.setContentsMargins(18, 12, 18, 12)
        bar.setSpacing(10)
        title_col = QVBoxLayout()
        title_col.setSpacing(2)
        title = QLabel("项目树")
        title.setObjectName("ProjectTreeTitle")
        title_col.addWidget(title)
        self._root_lbl = QLabel("（未选根目录）")
        self._root_lbl.setObjectName("ProjectTreeRoot")
        self._root_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        title_col.addWidget(self._root_lbl)
        bar.addLayout(title_col, 1)
        self._btn_newregion = QPushButton("新建调查区域…")
        self._btn_newregion.setObjectName("Primary")
        self._btn_newregion.setToolTip("创建一个新的调查根目录，并在区域层保存负责人等默认信息")
        self._btn_newregion.setFixedHeight(34)
        self._btn_newregion.setCursor(Qt.CursorShape.PointingHandCursor)
        icons.set_button_icon(self._btn_newregion, "mdi6.folder-plus-outline",
                              color=icons.TONE_ON_ACCENT, size=15)
        self._btn_newregion.clicked.connect(self._new_region)
        bar.addWidget(self._btn_newregion)
        self._btn_pick = QPushButton("选择根目录…")
        self._btn_pick.setObjectName("Outline")
        self._btn_pick.setToolTip("选择已有调查目录，显示其中所有可进入的子工作区")
        self._btn_pick.setFixedHeight(34)
        self._btn_pick.setCursor(Qt.CursorShape.PointingHandCursor)
        icons.set_button_icon(self._btn_pick, "mdi6.folder-open-outline",
                              color=icons.TONE_MUTED, size=15)
        self._btn_pick.clicked.connect(self._pick_root)
        bar.addWidget(self._btn_pick)
        self._btn_newsub = QPushButton("新建断面/子节点")
        self._btn_newsub.setObjectName("Outline")
        self._btn_newsub.setToolTip("在当前选中文件夹下新建断面、站位或任意子节点")
        self._btn_newsub.setFixedHeight(34)
        self._btn_newsub.setCursor(Qt.CursorShape.PointingHandCursor)
        icons.set_button_icon(self._btn_newsub, "mdi6.plus",
                              color=icons.TONE_MUTED, size=15)
        self._btn_newsub.clicked.connect(self._new_subfolder)
        self._btn_newsub.setEnabled(False)
        bar.addWidget(self._btn_newsub)
        root.addWidget(header)

        # Body splitter: tree | detail
        split = QSplitter(Qt.Orientation.Horizontal)
        split.setChildrenCollapsible(False)
        split.setHandleWidth(10)

        tree_panel = QFrame()
        tree_panel.setObjectName("ProjectTreePanel")
        tl = QVBoxLayout(tree_panel)
        tl.setContentsMargins(14, 12, 14, 14)
        tl.setSpacing(10)
        tree_head = QHBoxLayout()
        tree_head.setSpacing(8)
        tree_title = QLabel("目录结构")
        tree_title.setObjectName("Section")
        tree_head.addWidget(tree_title)
        self._tree_count_lbl = QLabel("0 个节点")
        self._tree_count_lbl.setObjectName("MutedSmall")
        self._tree_count_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        tree_head.addWidget(self._tree_count_lbl, 1)
        tl.addLayout(tree_head)
        metrics = QFrame()
        metrics.setObjectName("ProjectTreeMetrics")
        ml = QHBoxLayout(metrics)
        ml.setContentsMargins(10, 8, 10, 8)
        ml.setSpacing(8)
        self._metric_regions = self._add_tree_metric(ml, "区域")
        self._metric_workspaces = self._add_tree_metric(ml, "工作区")
        self._metric_candidates = self._add_tree_metric(ml, "待导入")
        tl.addWidget(metrics)
        self._search = QLineEdit()
        self._search.setObjectName("ProjectTreeSearch")
        self._search.setPlaceholderText("搜索节点或路径")
        self._search.setClearButtonEnabled(True)
        self._search.textChanged.connect(self._filter_tree)
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
            chip.clicked.connect(lambda _checked=False, k=key: self._set_kind_filter(k))
            self._kind_filter_buttons[key] = chip
            filter_row.addWidget(chip)
        self._kind_filter_buttons["all"].setChecked(True)
        filter_row.addStretch(1)
        tl.addLayout(filter_row)
        self._tree = QTreeWidget()
        self._tree.setObjectName("ProjectDirectoryTree")
        self._tree.setHeaderHidden(True)
        self._tree.setAlternatingRowColors(False)
        self._tree.setAnimated(True)
        self._tree.setIndentation(22)
        self._tree.setIconSize(QSize(18, 18))
        self._tree.setUniformRowHeights(True)
        self._tree.setRootIsDecorated(False)
        # T5 survey-summary (spec §2): 树改多选,Ctrl/Shift 多选断面做汇总.
        self._tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        # self._tree.itemSelectionChanged.connect(self._update_detail_panel_for_selected_project)  # §7 旧单选槽,保留;多选改由 _on_tree_selection_changed 派发
        self._tree.itemSelectionChanged.connect(self._on_tree_selection_changed)
        self._tree.itemDoubleClicked.connect(lambda *_: self._enter_selected())
        self._tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._tree.customContextMenuRequested.connect(self._show_tree_context_menu)
        tl.addWidget(self._tree, 1)
        tree_panel.setMinimumWidth(360)
        split.addWidget(tree_panel)

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
        tool_row = QHBoxLayout()
        tool_row.setSpacing(10)
        self._btn_summary = QPushButton("汇总导出…")
        self._btn_summary.setObjectName("Outline")
        self._btn_summary.setToolTip("从选中文件夹向下汇总标本记录并导出")
        self._btn_summary.setFixedHeight(34)
        self._btn_summary.setCursor(Qt.CursorShape.PointingHandCursor)
        icons.set_button_icon(self._btn_summary, "mdi6.file-export-outline",
                              color=icons.TONE_MUTED, size=15)
        self._btn_summary.setEnabled(False)
        self._btn_summary.clicked.connect(self._open_summary_export)
        tool_row.addWidget(self._btn_summary)
        self._btn_station_species = QPushButton("分类名录…")
        self._btn_station_species.setObjectName("Outline")
        self._btn_station_species.setToolTip("查看真正的分类名录，并分开展示样品处理概况")
        self._btn_station_species.setFixedHeight(34)
        self._btn_station_species.setCursor(Qt.CursorShape.PointingHandCursor)
        icons.set_button_icon(self._btn_station_species, "mdi6.format-list-bulleted",
                              color=icons.TONE_MUTED, size=15)
        self._btn_station_species.setEnabled(False)
        self._btn_station_species.clicked.connect(self._open_station_species_summary)
        tool_row.addWidget(self._btn_station_species)
        self._btn_station_import = QPushButton("导入站位总表…")
        self._btn_station_import.setObjectName("Outline")
        self._btn_station_import.setToolTip("把站位坐标和采集信息导入选中文件夹")
        self._btn_station_import.setFixedHeight(34)
        self._btn_station_import.setCursor(Qt.CursorShape.PointingHandCursor)
        icons.set_button_icon(self._btn_station_import, "mdi6.table-arrow-down",
                              color=icons.TONE_MUTED, size=15)
        self._btn_station_import.setEnabled(False)
        self._btn_station_import.clicked.connect(self._open_station_import)
        tool_row.addWidget(self._btn_station_import)
        dl.addLayout(tool_row)
        dl.addStretch()
        detail.setMinimumWidth(560)
        # split.addWidget(detail)            # §7 旧:右栏直接挂 detail 单栏;新:detail 成为右栏 stack 的 page0
        # split.setSizes([420, 720])         # §7 旧:两栏尺寸;新:三栏

        # ── T5 中间预览栏 (多选断面 → UidGroupedGrid 合并 groups;单选时隐藏) ──
        self._grid_panel = QFrame()
        self._grid_panel.setObjectName("ProjectTreeGridPanel")
        grid_panel_lay = QVBoxLayout(self._grid_panel)
        grid_panel_lay.setContentsMargins(14, 12, 14, 14)
        grid_panel_lay.setSpacing(8)
        grid_head = QLabel("按编号汇总")
        grid_head.setObjectName("Section")
        grid_panel_lay.addWidget(grid_head)
        from app.widgets.uid_grouped_grid import UidGroupedGrid
        self._uid_grid = UidGroupedGrid(self._grid_panel)
        grid_panel_lay.addWidget(self._uid_grid, 1)
        self._grid_panel.setMinimumWidth(360)
        self._grid_panel.setVisible(False)   # 单选默认隐藏;多选时显示

        # ── T5 右栏 QStackedWidget 三态 (spec §2):page0 单张详情 / page1 编号列表 / page2 物种名录 ──
        from app.widgets.survey_summary_panel import SurveySummaryPanel
        self._right_stack = QStackedWidget()
        self._right_stack.addWidget(detail)                       # page0: 现有 detail (单选,现状行为不变)
        uid_list_page = QLabel("编号列表(占位)")                   # page1: 编号列表占位 (本期简单)
        uid_list_page.setObjectName("EmptyState")
        uid_list_page.setWordWrap(True)
        self._right_stack.addWidget(uid_list_page)
        self._survey_panel = SurveySummaryPanel(ctx=self.ctx, parent=self)  # page2: 物种名录
        self._right_stack.addWidget(self._survey_panel)
        self._right_stack.setCurrentIndex(0)
        self._right_stack.setMinimumWidth(420)

        split.addWidget(self._grid_panel)
        split.addWidget(self._right_stack)
        split.setSizes([360, 420, 560])

        root.addWidget(split, 1)

    def _add_tree_metric(self, layout: QHBoxLayout, label: str) -> QLabel:
        box = QFrame()
        box.setObjectName("TreeMetric")
        bl = QVBoxLayout(box)
        bl.setContentsMargins(8, 4, 8, 4)
        bl.setSpacing(0)
        value = QLabel("0")
        value.setObjectName("TreeMetricValue")
        value.setAlignment(Qt.AlignmentFlag.AlignCenter)
        caption = QLabel(label)
        caption.setObjectName("TreeMetricLabel")
        caption.setAlignment(Qt.AlignmentFlag.AlignCenter)
        bl.addWidget(value)
        bl.addWidget(caption)
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
            f"QFrame#ProjectTreeHeader,QFrame#ProjectTreePanel,QFrame#ProjectTreeDetail{{"
            f"background:{panel};border:1px solid {border};border-top:1px solid {edge};"
            f"border-radius:10px;}}"
            f"QLabel{{color:{text};background:transparent;}}"
            f"QLabel#ProjectTreeTitle{{color:{text};font-weight:800;font-size:19px;}}"
            f"QLabel#ProjectTreeRoot{{color:{muted};font-size:12px;}}"
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
            f"QTreeWidget#ProjectDirectoryTree::item{{min-height:30px;padding:6px 8px;"
            f"border-radius:6px;color:{text_soft};}}"
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
            f"QFrame#ChildNodeRow{{background:{panel};border:1px solid {border};border-radius:7px;}}"
            f"QFrame#ChildNodeRow:hover{{background:{accent_softer};border-color:{border_medium};}}"
            f"QLabel#ChildNodeName{{color:{text_soft};font-size:12px;font-weight:800;}}"
            f"QLabel#ChildNodeMeta{{color:{muted_dim};font-size:11px;font-weight:700;}}"
            f"QLabel#ChildNodeBadge{{color:{muted};background:{panel_inset};"
            f"border:1px solid {border};border-radius:999px;padding:2px 9px;"
            f"font-size:10px;font-weight:800;}}"
            f"QSplitter::handle{{background:{bg};}}"
            f"QSplitter::handle:hover{{background:{panel_inset};}}"
        )

    # ── BaseView ────────────────────────────────────────────────────────────
    def on_activate(self) -> None:
        self._apply_style()
        if self._root is None:
            # Restore an EXPLICIT user-chosen root only. Never guess by walking
            # current_project_dir.parent — that produced wrong roots (a drive's
            # project dump dir is not a survey root) and hid the real projects
            # the user already created. With no root, _reload_project_tree shows every
            # recorded project as a flat list instead.
            saved = self.ctx.settings.project_tree_root
            if saved and Path(saved).is_dir():
                self._root = saved
        self._reload_project_tree()

    # ── Data / tree build ─────────────────────────────────────────────────────
    def _reload_project_tree(self) -> None:
        self._tree.clear()
        self._tree_count_lbl.setText("0 个节点")
        self._update_tree_metrics()
        self._btn_enter.setEnabled(False)
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
        if self._root and Path(self._root).is_dir():
            # ── Rooted scan mode (unchanged): one survey root, recursive tree ──
            self._btn_newsub.setEnabled(True)
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
        self._btn_newsub.setEnabled(False)
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
            button.setChecked(key == kind)
        self._filter_tree(self._search.text())

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
            nodes.append({
                "name": name or Path(directory).name or "(未命名)",
                "path": directory,
                "has_data": pts.is_workspace(directory),
                "is_candidate": candidate,
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
        if node["has_data"]:
            label = f"{node['name']}  ·  工作区"
            glyph = "mdi6.camera-outline"
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
        item.setIcon(0, icons.icon(glyph, color=tone))
        item.setData(0, _PATH_ROLE, node["path"])
        item.setData(0, _KIND_ROLE, kind)
        item.setToolTip(0, str(node["path"]))
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

        station_action = menu.addAction("导入站位总表…")
        station_action.triggered.connect(self._open_station_import)

        menu.addSeparator()
        open_action = menu.addAction("打开文件夹")
        open_action.triggered.connect(lambda _=False, p=path: self._open_directory(p))

        copy_action = menu.addAction("复制路径")
        copy_action.triggered.connect(
            lambda _=False, p=path: QApplication.clipboard().setText(str(p))
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
        self._btn_newsub.setEnabled(bool(self._root))
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

    def _update_detail_panel_for_selected_project(self) -> None:
        path = self._selected_path()
        if not path:
            self._btn_enter.setEnabled(False)
            self._btn_summary.setEnabled(False)
            self._btn_station_species.setEnabled(False)
            self._btn_station_import.setEnabled(False)
            self._btn_open_dir.setEnabled(False)
            self._btn_copy_path.setEnabled(False)
            self._btn_newsub.setEnabled(bool(self._root))
            self._detail_kind.setText("未选择")
            self._info_block.hide()
            self._child_block.hide()
            self._clear_child_preview()
            self._clear_media_preview()
            self._clear_stats()
            self._set_enter_action_style("Primary", "进入工作区拍照", "mdi6.camera-outline")
            self._empty_state.setText("选择左侧文件夹后，可进入工作区、汇总导出或导入站位表。")
            self._empty_state.show()
            return
        self._empty_state.hide()
        self._btn_newsub.setEnabled(True)
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
        if workspace:
            kind = "工作区"
            state = "已初始化，可拍照"
            self._set_enter_action_style(
                "Primary", "进入工作区拍照", "mdi6.camera-outline",
                color=icons.TONE_ON_ACCENT,
            )
        elif child_count:
            kind = "调查区域"
            state = "区域节点，通常在下级断面拍照"
            self._set_enter_action_style(
                "SoftAction", "进入此层拍照", "mdi6.folder-open-outline",
                color=icons.TONE_ACCENT,
            )
        else:
            kind = "文件夹"
            state = "进入时自动初始化工作区" if exists else "路径不可访问或磁盘未连接"
            self._set_enter_action_style(
                "Primary", "初始化并进入拍照", "mdi6.camera-plus-outline",
                color=icons.TONE_ON_ACCENT,
            )
        self._detail_kind.setText(kind)
        self._detail_name.setText(p.name or path)
        self._detail_path.setText(path)
        self._info_type.setText(kind)
        self._info_status.setText(state)
        self._info_children.setText(f"{child_count} 个子节点")
        self._info_block.show()
        self._render_child_preview(current_item)
        self._render_stats(path)
        self._render_media_preview(path)

    # ── T5 survey-summary: 多选派发 + 三栏切换 (spec §2) ───────────────────────
    def _on_tree_selection_changed(self) -> None:
        """selectionChanged 派发器:按选中节点数切右栏 page + 填中间网格.

        - 选 0 / 1 个 → 现有单选/空选路径 (现状行为不变):右栏 page0 单张详情,
          中间网格隐藏.
        - 选 ≥2 个 → 多选汇总路径:右栏 page2 物种名录,
          中间 UidGroupedGrid 显示合并后的按编号 groups.
        """
        items = self._tree.selectedItems()
        if len(items) >= 2:
            self._show_multi_selection_summary(items)
            return
        # 0 或 1 个 → 现状单选路径
        if getattr(self, "_grid_panel", None) is not None:
            self._grid_panel.setVisible(False)
        if getattr(self, "_uid_grid", None) is not None:
            # 清掉多选残留的合并 groups,避免隐藏网格仍占内存 / worker 解码旧路径.
            self._uid_grid.clear()
        if getattr(self, "_right_stack", None) is not None:
            self._right_stack.setCurrentIndex(0)  # page0: 单张详情
        self._update_detail_panel_for_selected_project()

    def _show_multi_selection_summary(self, items: list) -> None:
        """多选 ≥2 节点:右栏切物种名录页,中间填合并 groups 网格 (spec §2/§3)."""
        dirs: list[str] = []
        labels: dict[str, str] = {}
        for it in items:
            p = it.data(0, _PATH_ROLE)
            if not p:
                continue
            dirs.append(str(p))
            labels[str(p)] = it.text(0)  # 节点 label (如 "断面a  ·  工作区")
        if not dirs:
            self._grid_panel.setVisible(False)
            self._right_stack.setCurrentIndex(0)
            return
        # 右栏 → 物种名录页:聚合所选工作区的 specimens (跨断面按学名去重).
        self._survey_panel.set_workspaces(dirs, labels=labels)
        self._right_stack.setCurrentIndex(2)
        # 中间 → 合并各选中断面的 results groups (不同断面 UID 天然不冲突,spec §3).
        merged = self._collect_merged_groups(dirs)
        self._uid_grid.set_groups(merged)
        self._grid_panel.setVisible(True)
        # page0(detail)已隐藏,其内 _media_block / _empty_state 不再可见.
        self._empty_state.hide()

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
        """切走页面:清空合并网格(保留 worker 线程供下次进入).

        真正 quit+wait 在 :meth:`stop_background_work` (MainWindow._teardown 调用,
        对应 app 退出路径) 与 :meth:`closeEvent` 中执行 —— 切页不杀线程,避免
        再次进入后网格无法解码。
        """
        try:
            grid = getattr(self, "_uid_grid", None)
            if grid is not None:
                grid.clear()
        except Exception:  # pragma: no cover - 防御性
            pass

    def stop_background_work(self) -> None:
        """App 退出 (MainWindow._teardown) 时 join worker 线程,防 close→reopen→必须重启."""
        try:
            grid = getattr(self, "_uid_grid", None)
            if grid is not None:
                grid.teardown()
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

    def _clear_stats(self) -> None:
        while self._stats_row.count():
            it = self._stats_row.takeAt(0)
            if it.widget():
                w = it.widget()
                w.hide()
                w.setParent(None)
                w.deleteLater()

    def _clear_child_preview(self) -> None:
        while self._child_list.count():
            it = self._child_list.takeAt(0)
            if it.widget():
                w = it.widget()
                w.hide()
                w.setParent(None)
                w.deleteLater()

    def _clear_media_preview(self) -> None:
        while self._media_grid.count():
            it = self._media_grid.takeAt(0)
            if it.widget():
                w = it.widget()
                w.hide()
                w.setParent(None)
                w.deleteLater()
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
        row.setToolTip("定位到该节点")
        row.mousePressEvent = lambda event, target=item: self._select_preview_item(target, event)
        rl = QHBoxLayout(row)
        rl.setContentsMargins(10, 7, 10, 7)
        rl.setSpacing(8)
        icon_lbl = QLabel()
        icon_lbl.setFixedSize(18, 18)
        kind = item.data(0, _KIND_ROLE) or "folder"
        if kind == "workspace":
            glyph, tone, badge = "mdi6.camera-outline", icons.TONE_ACCENT, "工作区"
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

    def _collect_media_preview(self, path: str, limit: int = 6) -> list[Path]:
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
        card.setToolTip(str(path))
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
        name.setToolTip(path.name)
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

    # ── Actions ────────────────────────────────────────────────────────────────
    def _pick_root(self) -> None:
        start = self._root or (self.ctx.current_project_dir or "")
        path = ui.get_existing_directory(self, "选择项目根目录", start)
        if not path:
            return
        self._root = str(Path(path).resolve())
        pts.clear_project_tree_cache(self._root)
        self.ctx.settings.project_tree_root = self._root
        self._reload_project_tree()

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
            "在断面里设省份/样地可覆盖。",
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
        # Single unified entry path: ensures dirs, sets dir + root (bounding the
        # settings-inheritance walk to this survey's tree), and records the node
        # into the recent list so it also shows up in 项目总览.
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
