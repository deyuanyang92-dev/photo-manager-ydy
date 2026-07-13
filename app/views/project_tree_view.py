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

from PyQt6.QtCore import (
    Q_ARG,
    QItemSelectionModel,
    QMetaObject,
    QSize,
    QThread,
    QTimer,
    Qt,
    pyqtSignal,
)
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


def _resolved_eq(a: str, b: str) -> bool:
    """两个路径指向同一个目录吗（归一化后比较）。"""
    if not a or not b:
        return False
    try:
        return Path(a).expanduser().resolve() == Path(b).expanduser().resolve()
    except (OSError, ValueError):
        return str(a) == str(b)
_KIND_ROLE = Qt.ItemDataRole.UserRole.value + 1

# 退休但可能仍在跑的预热线程(v0.56): 保持 Python/Qt 对象存活直到线程真正结束,
# 宁可对象多活一会儿也不 destroy-while-running(那会直接 abort 整个进程)。
_RETIRED_WARMUP_WORKERS: list = []


def _reap_retired_warmup_workers(*, wait_ms: int = 0) -> None:
    """收割已结束的退休预热线程; 仍在跑的先 cancel 再(可选)等待, 等不到就留着."""
    still: list = []
    for w in _RETIRED_WARMUP_WORKERS:
        try:
            if w.isRunning():
                w.cancel()
                if wait_ms and w.wait(wait_ms):
                    continue  # 已停, 丢弃引用
                still.append(w)
        except Exception:
            pass  # 判定失败的对象直接丢弃, 不再追踪
    _RETIRED_WARMUP_WORKERS[:] = still


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
        # ── 缩略图解码 worker(性能修复): 详情预览大图 + 影像预览小图都改成
        # 「主线程只查缓存, 未命中丢给 worker 线程解码, QImage 回主线程转 QPixmap」。
        # QThread 懒建(第一次真的需要解码时才起), 不 parent 到 self —— 见
        # uid_grouped_grid.py:517 的同款处置(避免 Qt 父子销毁与 quit/wait 抢跑)。
        self._thumb_thread: Optional[QThread] = None
        self._thumb_worker = None
        self._thumb_req_counter: int = 0
        # req_id -> (kind, target, path, extra)；kind ∈ {"preview", "media"}
        self._thumb_pending: dict[int, tuple] = {}
        self._thumb_pending_paths: set[str] = set()
        self._preview_req: int = 0
        self._preview_pixmap = None  # 最近一次成功解码的原始 QPixmap(主线程构造)
        self._media_gen: int = 0  # 每次清空影像预览自增 → 迟到结果作废
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
        # §7 旧: 360px 硬上限 → 长路径被截断成一串没头没尾的字符。现在标题栏放的是
        #   「N 个项目 · 数据位置」这类短文本, 放宽上限即可完整显示。
        self._root_lbl.setMaximumWidth(640)
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

        self._btn_new_project = QPushButton("＋ 项目")
        self._btn_new_project.setObjectName("Primary")
        self._btn_new_project.setFixedHeight(34)
        self._btn_new_project.setToolTip(
            "新建一个项目文件夹；项目下面的断面 / 采样点用“＋ 下级目录”添加。"
        )
        self._btn_new_project.setCursor(Qt.CursorShape.PointingHandCursor)
        icons.set_button_icon(self._btn_new_project, "mdi6.folder-plus-outline",
                              color="#ffffff", size=15)
        self._btn_new_project.clicked.connect(self._new_region)
        bar.addWidget(self._btn_new_project)

        # 用户场景（2026-07-13）：更新后项目索引可能丢失，找回入口必须直接可见，
        # 不能只藏在「⋯」菜单，也不能要求用户重新创建磁盘上已经存在的项目。
        self._btn_import_project = QPushButton("导入已有项目")
        self._btn_import_project.setObjectName("Outline")
        self._btn_import_project.setFixedHeight(34)
        self._btn_import_project.setToolTip("选择磁盘上已经存在的项目文件夹并加入项目树")
        self._btn_import_project.clicked.connect(self._add_workspace_manual)
        bar.addWidget(self._btn_import_project)

        self._btn_scan_projects = QPushButton("扫描项目位置")
        self._btn_scan_projects.setObjectName("Outline")
        self._btn_scan_projects.setFixedHeight(34)
        self._btn_scan_projects.setToolTip("指定长期保存项目的磁盘或目录，扫描并找回全部项目")
        self._btn_scan_projects.clicked.connect(self._scan_disk)
        bar.addWidget(self._btn_scan_projects)

        self._btn_library_dir = QPushButton("项目库目录")
        self._btn_library_dir.setObjectName("Outline")
        self._btn_library_dir.setFixedHeight(34)
        self._btn_library_dir.setToolTip("设置以后新建项目默认保存到哪个上级目录")
        self._btn_library_dir.clicked.connect(self._choose_project_library_directory)
        bar.addWidget(self._btn_library_dir)

        # 「+ 新建子目录」提到工具栏(用户 2026-07-12): 采样点不再在「新建项目」时一次问完,
        # 改为建完项目后在树里自由加(任意层, 空壳; 进入时才初始化为工作区) —— 这个入口是
        # 新流程的主动作, 不能只藏在右键菜单和「⋯」里。
        self._btn_new_subfolder = QPushButton("＋ 下级目录")
        self._btn_new_subfolder.setObjectName("Outline")
        self._btn_new_subfolder.setFixedHeight(34)
        self._btn_new_subfolder.setToolTip(
            "在选中的文件夹下增加一层（如断面 / 采样点）；不选则建在项目根下。\n"
            "进入这个目录拍照时，它会自动成为工作区。"
        )
        self._btn_new_subfolder.setCursor(Qt.CursorShape.PointingHandCursor)
        icons.set_button_icon(self._btn_new_subfolder, "mdi6.folder-plus-outline",
                              color=icons.TONE_MUTED, size=15)
        self._btn_new_subfolder.clicked.connect(self._new_subfolder)
        bar.addWidget(self._btn_new_subfolder)

        # 「项目设置」提到工具栏: 项目根是容器(进不去工作台), 它的设置本来无 UI 可填 ——
        # 这是「新建项目」对话框砍到 2 个字段的前提(spec §3.4)。
        self._btn_node_settings = QPushButton("项目设置")
        self._btn_node_settings.setObjectName("Outline")
        self._btn_node_settings.setFixedHeight(34)
        self._btn_node_settings.setToolTip(
            "为选中的项目/目录填采集人、地区代码、默认坐标、拍摄场地等；\n"
            "下面所有采样点自动继承，拍照时右栏直接带出来，不用每次重填。"
        )
        self._btn_node_settings.setCursor(Qt.CursorShape.PointingHandCursor)
        icons.set_button_icon(self._btn_node_settings, "mdi6.cog-outline",
                              color=icons.TONE_MUTED, size=15)
        self._btn_node_settings.clicked.connect(self._open_node_settings)
        bar.addWidget(self._btn_node_settings)

        self._btn_refresh = QPushButton("刷新")
        self._btn_refresh.setObjectName("Outline")
        self._btn_refresh.setFixedHeight(34)
        self._btn_refresh.setToolTip("重新扫描当前列表")
        self._btn_refresh.setCursor(Qt.CursorShape.PointingHandCursor)
        icons.set_button_icon(self._btn_refresh, "mdi6.refresh", color=icons.TONE_MUTED, size=15)
        # self._btn_refresh.clicked.connect(self._reload_project_tree)  # §7 旧: 直连重载,
        #     但 scan_tree 默认 use_cache=True → 缓存 TTL 内点「刷新」拿到的是陈旧树。
        self._btn_refresh.clicked.connect(self._on_refresh_clicked)
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
        # §7 旧文案: "新建项目…" —— 现在这个入口一次建好「项目 + 若干采样点」,
        # 叫「新建项目」更贴用户的说法(Fable 5, 2026-07-12)
        self._act_new_region = self._more_menu.addAction("新建项目文件夹…")
        self._act_new_region.triggered.connect(self._new_region)
        self._act_scan = self._more_menu.addAction("扫描磁盘…")
        self._act_scan.triggered.connect(self._scan_disk)
        self._act_add_ws = self._more_menu.addAction("添加已有文件夹…")
        self._act_add_ws.triggered.connect(self._add_workspace_manual)
        self._act_clear_library_dir = self._more_menu.addAction("取消默认项目库目录")
        self._act_clear_library_dir.triggered.connect(self._clear_project_library_directory)
        self._act_refresh_index = self._more_menu.addAction("刷新汇总索引…")
        self._act_refresh_index.setToolTip(
            "更新调查根库中的 workspace_index_cache（加速后续跨断面汇总）"
        )
        self._act_refresh_index.triggered.connect(self._refresh_index_cache_manual)
        self._more_menu.addSeparator()
        self._act_newsub = self._more_menu.addAction("新建下级目录…")
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
        # 用户场景（2026-07-13，参考 Geneious）：这里是全局资料库树，
        # 不是当前 B2 的目录浏览器。项目作为顶层，下面保留任意层文件夹结构。
        tree_title = QLabel("项目库")
        tree_title.setObjectName("Section")
        tree_head.addWidget(tree_title)
        self._tree_count_lbl = QLabel("0 个节点")
        self._tree_count_lbl.setObjectName("MutedSmall")
        tree_head.addWidget(self._tree_count_lbl)
        tree_head.addSpacing(4)
        self._tree_metrics_inline = QLabel("0 项目 · 0 拍摄目录 · 0 待导入")
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
        # §7 旧: 一整排筛选 chip「全部 / 拍摄目录 / 文件夹 / 待导入」+「全选拍摄目录」按钮
        #   常驻左栏, 占掉一整行。用户 2026-07-13 截图逐个指着说「死按键, 很多一点用都没有」。
        #
        # 【用户为什么要「导入」—— 别再当成垃圾删掉】(用户 2026-07-13 亲口说明)
        #   "我设计导入, 是为了防止这个软件自动识别, 导致一些工作区无法被识别,
        #    我可以手动导入项目或工作区。"
        #   → 「待导入」是用户的**安全网**: 自动扫描认不出来的旧工作区、别人拷贝过来的
        #     目录、盘符变了的项目, 必须有一条手动捞回来的路。这个能力**永久保留**,
        #     不是可有可无的实现细节。见「认领此文件夹」(_adopt_selected_candidate)。
        #
        # 现在的处理: 能力全留, 但不再常驻占地方 ——
        #   · 筛选/全选这些控件改为隐藏(逻辑与右键动作仍在用);
        #   · 真的扫到未登记的旧目录时, 树里会显式列出「待导入」节点, 右键即可认领;
        #   · 需要主动找回来时: 工具栏「更多 → 扫描磁盘…」/「认领此文件夹…」。
        #   项目树本体就是个目录树 —— 像资源管理器: 搜索框 + 树。
        for key, label in (
            ("all", "全部"),
            ("workspace", "拍摄目录"),
            ("folder", "文件夹"),
            ("candidate", "待导入"),
        ):
            chip = QPushButton(label)
            chip.setObjectName("FilterChip")
            chip.setCheckable(True)
            chip.clicked.connect(lambda _checked=False, k=key: self._set_kind_filter(k))
            chip.hide()
            self._kind_filter_buttons[key] = chip
        self._btn_select_all_ws = QPushButton("全选拍摄目录")
        self._btn_select_all_ws.setObjectName("Outline")
        self._btn_select_all_ws.clicked.connect(self._select_all_visible_workspaces)
        self._btn_select_all_ws.hide()
        self._kind_filter_buttons["all"].setChecked(True)
        select_hint = QLabel(
            "项目可逐层展开 · 双击最末级进入拍照 · 右键新建/改名/移动"
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
        self._tree.setColumnCount(2)
        self._tree.setHeaderLabels(["名称", "子目录"])
        self._tree.setHeaderHidden(True)
        self._tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self._tree.header().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self._tree.setAlternatingRowColors(False)
        self._tree.setAnimated(True)
        self._tree.setIndentation(22)
        self._tree.setIconSize(QSize(14, 14))
        self._tree.setUniformRowHeights(True)
        # 用户场景（2026-07-13）：项目树必须直观看出「项目 → 子目录」层级。
        # 顶层展开箭头不能隐藏，否则 b 即使已有子目录也看起来像一行死条目。
        self._tree.setRootIsDecorated(True)
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
            "新建项目：一次建好项目目录和它下面的采样点，采样点自动继承项目设置。"
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
        self._btn_enter = QPushButton("设为当前拍摄目录")
        self._btn_enter.setObjectName("Primary")
        self._btn_enter.setToolTip("选中任意文件夹作为照片保存位置；需要的数据结构会自动建立")
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
        self._btn_enter_sticky = QPushButton("设为当前拍摄目录")
        self._btn_enter_sticky.setObjectName("Primary")
        self._btn_enter_sticky.setToolTip("选中任意文件夹作为照片保存位置")
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
            "请先在左侧选择至少一个拍摄目录。"
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
        # 「全部项目 / 按根目录」决定整棵树的数据范围，不能藏进 ⋯ 菜单；
        # 用户一旦停在按根模式，就会误以为其他项目被删除了。
        if hasattr(self, "_mode_row_host"):
            self._mode_row_host.setVisible(True)
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
                "多选拍摄目录 → 右侧概览 · 中间编号照片"
                if v2
                else "① 选择一个或多个拍摄目录  ② 右侧看概览  ③ 中间显示编号与照片"
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
            # from app.db.db_manager import open_project_db  # §7 旧: 缓存被浏览节点的连接 → 锁泄漏
            from app.db.db_manager import open_project_db_private
            # conn = open_project_db(ws, create=False)  # §7 旧
            conn = open_project_db_private(ws, create=False)
        except Exception:
            return None
        try:
            if uid:
                row = conn.execute(
                    "SELECT * FROM specimens WHERE uid = ? LIMIT 1", (uid,)
                ).fetchone()
                if row:
                    return dict(row)
            return None
        finally:
            conn.close()

    def _enter_preview_mode(self, path: str) -> None:
        self._preview_mode = True
        self._show_preview_image(path)
        self._grid_stack.setCurrentIndex(1)

    def _exit_preview_mode(self) -> None:
        self._preview_mode = False
        self._grid_stack.setCurrentIndex(0)

    # def _show_preview_image(self, path: str) -> None:   # §7 旧: 主线程全尺寸解码
    #     from app.utils.image_thumbnail import decode_image_thumbnail
    #
    #     pm = decode_image_thumbnail(path, max_size=ptl.PREVIEW_DECODE_MAX)
    #     #  ↑ 1600px 解码走 GUI 线程; TIFF 母版没有 720 的 master 缓存可用,
    #     #    冷路径要真解 TIFF, 最坏还会 fork ImageMagick(timeout=12s) → 界面卡死。
    #     self._preview_image.setScaledContents(False)
    #     if pm is not None and not pm.isNull():
    #         QApplication.processEvents()   # ← 在 paint/布局调用栈里重入事件循环
    #         w = max(ptl.PREVIEW_MIN_WIDTH, self._preview_image.width())
    #         h = max(ptl.PREVIEW_MIN_HEIGHT, self._preview_image.height())
    #         scaled = pm.scaled(
    #             w,
    #             h,
    #             Qt.AspectRatioMode.KeepAspectRatio,
    #             Qt.TransformationMode.SmoothTransformation,
    #         )
    #         self._preview_image.setPixmap(scaled)
    #         self._preview_image.setText("")
    #     else:
    #         self._preview_image.clear()
    #         self._preview_image.setText("无法预览此文件")
    #     self._preview_title.setText(Path(path).name)

    def _show_preview_image(self, path: str) -> None:
        """新: 两段式。主线程只做「查缓存」(绝不解码), 未命中丢给 worker 线程。

        行为等价: 命中缓存 → 与旧代码同一帧、同样的 scaled 结果; 未命中 → 先显示
        「载入中…」, 解码完成后由主线程槽 (_on_thumb_decoded) 填图; 解码失败仍然是
        「无法预览此文件」。QApplication.processEvents() 被删除 —— 它唯一的作用是等
        布局刷新拿到最新的 label 宽度, 现在用一次 singleShot(0) 的重新适配替代, 不再
        在 paint 栈里重入事件循环。
        """
        from app.utils.image_thumbnail import try_cached_image_data

        self._preview_image.setScaledContents(False)
        self._preview_title.setText(Path(path).name)
        self._preview_req += 1
        req = self._preview_req
        self._preview_pixmap = None

        image = None
        try:
            image = try_cached_image_data(path, ptl.PREVIEW_DECODE_MAX)
        except Exception:  # pragma: no cover - 防御性
            image = None
        if image is not None and not image.isNull():
            self._apply_preview_image(req, image)
            return

        self._preview_image.clear()
        self._preview_image.setText("载入中…")
        self._request_thumb_decode(
            "preview", path, ptl.PREVIEW_DECODE_MAX, target=None, extra=req,
        )

    def _apply_preview_image(self, req: int, image) -> None:
        """主线程: QImage → QPixmap → 按当前 label 尺寸缩放上屏。"""
        from app.utils.image_thumbnail import make_pixmap

        if req != self._preview_req:
            return  # 用户已经切到别的图, 丢弃过期结果
        pm = make_pixmap(image)  # QPixmap 只能在主线程构造
        if pm is None or pm.isNull():
            self._preview_pixmap = None
            self._preview_image.clear()
            self._preview_image.setText("无法预览此文件")
            return
        self._preview_pixmap = pm
        self._rescale_preview_pixmap()
        # 旧代码用 processEvents() 等布局, 这里改成下一轮事件循环再按最终宽度适配一次
        # (主线程 singleShot, 不是 worker 线程里的 QTimer)。
        QTimer.singleShot(0, lambda r=req: self._rescale_preview_pixmap(r))

    def _rescale_preview_pixmap(self, req: Optional[int] = None) -> None:
        if req is not None and req != self._preview_req:
            return
        pm = self._preview_pixmap
        if pm is None or pm.isNull():
            return
        try:
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
        except RuntimeError:  # pragma: no cover - label 已销毁
            pass

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
        self._sync_library_dir_button()
        self._sync_view_mode_from_settings()
        self._sync_layout_mode_from_settings()
        self._restore_tree_split_state()
        self._restore_grid_inner_split_state()
        self._restore_summary_body_split_state()
        mode = ptl.normalize_content_mode(self.ctx.settings.project_tree_content_mode)
        self.ctx.settings.project_tree_content_mode = mode
        # 用户场景（2026-07-13）：软件更新后项目索引意外为空，但用户之前指定的
        # 项目磁盘仍然存在。进入项目树时应从固定扫描位置自动恢复，而不是显示空白。
        self._recover_empty_catalog_from_saved_roots()
        self._reload_project_tree()
        # self._reload_card_grid()  # §7 旧: 无条件重建卡片网格 —— 树模式(默认)下卡片
        #     根本不可见, 却照样对每个项目跑一次 get_project_summary(sqlite + results/
        #     + freeform/ + incoming-jpg/ 全量 iterdir + per-file stat), 与 _reload_project_tree
        #     串成「进页两次全盘扫描」。
        # 新: 只有卡片布局才加载卡片数据; 切到 cards 时 _set_layout_mode 已有重载路径
        #     (见 _set_layout_mode), 认领后的刷新亦仍显式调用, 行为等价。
        if getattr(self.ctx.settings, "project_tree_layout_mode", "tree") == "cards":
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

    def focus_project(self, root_path: str) -> None:
        """切到「按根目录」模式、以 *root_path* 为根、并选中该项目节点。

        GUI 实测(2026-07-12)抓到的 bug: 新建项目后落到项目树, 但树若停在「全部项目」
        模式, 选中的仍是**上一个项目**(用户 QSettings 里的旧记录) —— 接着点
        「新建子目录」, 目录就静默建到了**别的项目**下面(实测报
        `Permission denied: '/mnt/n'`, 因为那是另一台机器的旧项目路径)。
        建完项目必须把树焦点钉到新项目上。
        """
        if not root_path or not Path(root_path).is_dir():
            return
        target = str(Path(root_path).resolve())
        # §7 旧实现(切成 rooted 单项目模式 —— 建完新项目, 其余项目从树里全消失,
        #   用户 2026-07-12 截图报障):
        # self.ctx.settings.project_tree_root = root_path
        # self.ctx.settings.project_tree_view_mode = "rooted"
        # self._root = str(Path(root_path).resolve())
        # pts.clear_project_tree_cache(self._root)
        # self._sync_view_mode_buttons()
        # self._reload_project_tree()
        # top = self._tree.topLevelItem(0)
        # if top is not None:
        #     self._tree.setCurrentItem(top)
        #     ...
        # 新: 不动视图模式 —— 树里所有项目照旧并排, 只把焦点钉到新项目那个节点上。
        # (焦点必须钉住: 否则接着点「新建子目录」会静默建到**上一个**选中的项目下面,
        #  实测报过 Permission denied: '/mnt/n' —— 那是另一台机器的旧项目路径。)
        pts.clear_project_tree_cache(target)
        self._reload_project_tree()
        item = None
        for i in range(self._tree.topLevelItemCount()):
            item = self._find_item_by_path(self._tree.topLevelItem(i), target)
            if item is not None:
                break
        # 找不到节点时也不能偷偷切成「单项目」模式。登记或刷新失败只意味着本次
        # 无法选中新项目；切模式会让原有项目全部从眼前消失，后果更严重。
        if item is None:
            return
        if item is not None:
            self._tree.setCurrentItem(item)
            item.setSelected(True)
            self._tree.expandItem(item)
            self._tree.scrollToItem(item)

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
    def _on_refresh_clicked(self) -> None:
        """「刷新」按钮: 必须绕过 scan_tree 的 TTL 缓存, 否则点了等于没点。

        建目录 / 改根目录等路径本来就调用了 clear_project_tree_cache, 只有这个
        按钮没有 —— 显式清一次, 再走原来的重载。
        """
        try:
            pts.clear_project_tree_cache(self._root)
        except Exception:  # pragma: no cover - 防御性
            pass
        self._reload_project_tree()

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
        # §7 旧: self._act_newsub.setEnabled(False)
        #   —— 「全部项目」模式下把「＋ 下级目录」禁掉。而用户平时**就在这个模式**,
        #   于是那个按钮一直是死的(用户 2026-07-13:「很多死按键，一点用都没有」)。
        #   现在树在任何模式下都是真树, 选中哪个节点就能在它下面建 —— 不该禁。
        self._act_newsub.setEnabled(True)
        nodes = self._load_known_projects_nodes()
        if not nodes:
            self._root_lbl.setText("还没有项目 —— 点右上角「＋ 项目」开始")
            self._detail_name.setText("选择或创建项目")
            self._detail_path.setText("")
            self._detail_kind.setText("未选择")
            self._info_block.hide()
            self._child_block.hide()
            self._empty_state.setText(
                "还没有项目。\n\n"
                "点右上角「＋ 项目」新建一个 —— 项目就是个文件夹，\n"
                "下面可以再建任意层子目录，最里层双击进去就能拍照。\n\n"
                "已经有旧数据？「更多 → 扫描磁盘…」把它们找回来。"
            )
            self._empty_state.show()
            return
        # §7 旧: setText(f"（全部已建项目 · {len(nodes)}）") / 或一条被截断的绝对路径。
        #   现在给的是有用的信息: 项目数 + 数据所在磁盘/目录。完整路径在右栏详情里。
        self._root_lbl.setText(self._projects_scope_text(nodes))
        self._tree_count_lbl.setText(f"{len(nodes)} 个项目")
        self._update_tree_metrics(nodes)
        self._empty_state.hide()
        for node in nodes:
            project_item = self._build_item(node)
            self._tree.addTopLevelItem(project_item)
            # Geneious 式资料库树：进入页面即可看到每个项目的第一层结构；
            # 更深层仍由用户按箭头逐层展开，避免一次铺满整棵树。
            if project_item.childCount() > 0:
                project_item.setExpanded(True)
        self._filter_tree(self._search.text())
        # §7 旧: if len(nodes) >= 1: self._select_all_visible_workspaces()
        #   —— 一进页面就替用户**全选**所有拍摄目录(为了让右侧汇总立刻有数)。后果:
        #   多选状态下「设为当前拍摄目录」是故意禁用的 → 用户什么都还没点, 那个显眼的
        #   大绿按钮就已经是死的(实测: 2 个工作区 → enabled=False, 文字「多选时不进入拍照」)。
        #   用户 2026-07-13 报障:「我进入某个文件目录, 选择也进入不了」。
        #   现在只选中第一个节点(单选) —— 按钮是活的; 想看多断面汇总请 Ctrl/Shift 主动多选,
        #   或点「全选拍摄目录」。
        self._select_first_item()

    def _projects_scope_text(self, nodes: list) -> str:
        """标题栏那行 —— 说清「有几个项目、数据在哪个盘」。

        §7 旧行为: 直接贴一条绝对路径, 宽度限死 360px → 长路径被拦腰截断,
        既看不全也没信息量(用户 2026-07-13 截图指着它说看不到磁盘位置)。
        完整路径在右栏详情面板里本来就有, 这里给的是**概览**。
        """
        parents: list[str] = []
        for node in nodes:
            path = node.get("path") or ""
            if not path:
                continue
            try:
                parent = str(Path(path).expanduser().resolve().parent)
            except (OSError, ValueError):
                continue
            if parent not in parents:
                parents.append(parent)
        if len(parents) == 1:
            return f"{len(nodes)} 个项目 · {parents[0]}"
        if parents:
            return f"{len(nodes)} 个项目 · {len(parents)} 个位置"
        return f"{len(nodes)} 个项目"

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
                f"{regions} 文件夹 · {workspaces} 拍摄目录 · {candidates} 待导入"
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
            # §7 旧: "children": [] —— 假树。全部项目模式下每个项目都展不开, 于是
            #   「想看全部就没层级, 想看层级就只剩一个」。用户实测 bug(2026-07-12 截图):
            #   点「＋项目」后 focus_project 把树切成 rooted 单项目模式, 其余项目全消失。
            #   现在全部项目模式也扫真实子树 —— 一棵树管到底(项目 → 任意层子目录 → 拍摄目录)。
            children: list = []
            if exists and not candidate:
                try:
                    scanned = pts.scan_tree(directory)
                    children = list(scanned.get("children") or [])
                except (OSError, ValueError):
                    children = []
            nodes.append({
                "name": name or Path(directory).name or "(未命名)",
                "path": directory,
                "has_data": pts.is_workspace(directory) if exists else False,
                "is_candidate": candidate,
                "unavailable": not exists,
                "children": children,
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
        # Keep the internal workspace distinction in _KIND_ROLE only. The user
        # sees one ordinary folder tree; entering a folder initializes it when
        # needed, so the tree must not append implementation labels to names.
        if node.get("unavailable"):
            label = f"{node['name']}  ·  不可用"
            glyph = "mdi6.folder-alert-outline"
            tone = icons.TONE_WARN
            kind = "unavailable"
        elif node["has_data"]:
            label = str(node["name"])
            glyph = "mdi6.folder-open-outline"
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
        child_count = len(node.get("children") or [])
        item = QTreeWidgetItem([label, str(child_count) if child_count else ""])
        item.setTextAlignment(1, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
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
        """当前操作的目标节点 —— **主选中项**优先。

        §7 旧: 直接返回 items[0](选中列表的第一个)。多选时这会和界面说的不一致:
        按钮写着「进入「断面B」」(主选中项), 点下去却进了 items[0]=断面A —— 比死按键还糟。
        现在优先用 currentItem()(键盘/鼠标最后落点, 也是 Qt 的 anchor), 它不在选中集里
        时才退回 items[0]。
        """
        items = self._tree.selectedItems()
        current = self._tree.currentItem()
        if current is not None and (not items or current in items):
            path = current.data(0, _PATH_ROLE)
            if path:
                return path
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
        # 用户场景（2026-07-13）：右键菜单只放当前节点真正能执行的操作；
        # 汇总/筛选/站位导入和封面能力保留，但收进子菜单，避免十几项平铺成“死按键墙”。
        try:
            path_exists = Path(path).expanduser().exists()
        except OSError:
            path_exists = False
        kind = item.data(0, _KIND_ROLE) if item else None

        if path_exists:
            if kind not in {"region", "unavailable"} and not pts.is_region(str(path)):
                enter_action = menu.addAction("设为当前拍摄目录")
                enter_action.triggered.connect(self._enter_selected)

            new_child_action = menu.addAction("新建下级目录…")
            new_child_action.triggered.connect(self._new_subfolder)

            settings_action = menu.addAction("项目设置…")
            settings_action.triggered.connect(self._open_node_settings)

            menu.addSeparator()
            rename_action = menu.addAction("重命名…")
            rename_action.triggered.connect(self._rename_node)
            move_action = menu.addAction("移动到项目…")
            move_action.triggered.connect(self._move_node)
            delete_action = menu.addAction("删除…")
            delete_action.triggered.connect(self._delete_node)

            menu.addSeparator()
            open_action = menu.addAction("打开文件夹")
            open_action.triggered.connect(lambda _=False, p=path: self._open_directory(p))

            if kind == "candidate":
                adopt_action = menu.addAction("认领此文件夹…")
                adopt_action.triggered.connect(self._adopt_selected_candidate)

            data_menu = menu.addMenu("数据工具")
            summary_action = data_menu.addAction("汇总导出…")
            summary_action.triggered.connect(self._open_summary_export)
            filter_action = data_menu.addAction("数据筛选…")
            filter_action.triggered.connect(self._open_data_filter)
            station_action = data_menu.addAction("导入站位总表…")
            station_action.triggered.connect(self._open_station_import)

            cover_menu = menu.addMenu("封面")
            cover_action = cover_menu.addAction("设置封面…")
            cover_action.triggered.connect(
                lambda _=False, p=path: self._set_cover_for_directory(str(p))
            )
            clear_cover_action = cover_menu.addAction("恢复自动封面")
            clear_cover_action.triggered.connect(
                lambda _=False, p=path: self._clear_cover_for_directory(str(p))
            )
        else:
            unavailable = menu.addAction("磁盘未连接或目录已移动")
            unavailable.setEnabled(False)
            relocate_action = menu.addAction("指到新位置…")
            relocate_action.triggered.connect(self._relocate_selected_path)

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
        from app.utils.file_manager import local_path, open_directory_detailed

        directory = Path(local_path(path))
        if not directory.exists():
            # 用户场景（2026-07-13）：项目树里的“打开文件夹”不能成为无响应的死按钮；
            # 原路径已经失效时，明确显示原路径和本机解释后的路径，方便重新定位项目。
            ui.warn(
                self,
                "打开文件夹",
                f"目录不存在或磁盘未连接。\n\n记录路径：{path}\n本机路径：{directory}",
            )
            return
        result = open_directory_detailed(path)
        if not result.opened:
            ui.warn(
                self,
                "打开文件夹",
                f"无法打开文件夹。\n\n本机路径：{result.local_path}\n系统错误：{result.error or '未知错误'}",
            )

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
        kind = "拍摄目录" if workspace else "文件夹"
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
        self._set_enter_action_style("Primary", "设为当前拍摄目录", "mdi6.camera-outline")
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
                f"已选 {n} 个拍摄目录 — 右侧概览 · 中间编号照片"
            )
        elif n == 1:
            self._scope_status_lbl.setText(
                f"已选 1 个 · {labeled[0][1]}（Ctrl+点击可多选汇总）"
            )
        else:
            self._scope_status_lbl.setText(
                "未选择；点「全选拍摄目录」或 Ctrl+点击多选"
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
                # §7 旧: self._btn_enter.setEnabled(False)  + 文字「多选时不进入拍照」
                #   —— 多选是为了看汇总, 不该把「进入」变成一块砖。现在按钮仍然可用,
                #   作用于**主选中项**(anchor), 并在文字里说清进的是哪一个。
                self._btn_enter.setEnabled(True)
                self._btn_summary.setEnabled(True)
                self._btn_station_species.setEnabled(True)
                self._btn_station_import.setEnabled(False)
                self._btn_open_dir.setEnabled(False)
                self._btn_copy_path.setEnabled(False)
                self._detail_kind.setText("多选汇总")
                self._detail_name.setText(f"{n_ws} 个拍摄目录")
                self._detail_path.setText(names)
                self._info_type.setText("多选汇总")
                self._info_status.setText("右侧看调查概览统计，中间看编号与照片")
                self._info_children.setText(f"{n_ws} 个拍摄目录")
                self._info_block.show()
                self._child_block.hide()
                self._clear_child_preview()
                self._clear_media_preview()
                self._clear_stats()
                # 按钮作用于主选中项(current item) —— 说清进的是哪一个。
                anchor = self._tree.currentItem()
                anchor_name = anchor.text(0) if anchor is not None else ""
                if not anchor_name and labeled:
                    anchor_name = labeled[0][1]
                self._set_enter_action_style(
                    "Primary", f"进入「{anchor_name}」拍照" if anchor_name else "设为当前拍摄目录",
                    "mdi6.camera-outline", color=icons.TONE_ON_ACCENT,
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
            self._set_enter_action_style("Primary", "设为当前拍摄目录", "mdi6.camera-outline")
            self._empty_state.setText("选择左侧文件夹后，可设为拍摄目录、汇总导出或导入站位表。")
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
            kind = "拍摄目录"
            state = "已初始化，可拍照"
            self._set_enter_action_style(
                "Primary", "设为当前拍摄目录", "mdi6.camera-outline",
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
            # 用词统一(用户 2026-07-12): 项目 = 容器; 采样点 = 进去拍照的地方。
            # §7 旧: kind = "调查区域"; state = "区域节点，通常在下级断面拍照"
            kind = "项目"
            state = "项目节点，照片放在下面的采样点里"
            self._btn_adopt.hide()
            self._set_enter_action_style(
                "SoftAction", "进入此层拍照", "mdi6.folder-open-outline",
                color=icons.TONE_ACCENT,
            )
        else:
            kind = "文件夹"
            state = "设为拍摄目录时自动准备" if exists else "路径不可访问或磁盘未连接"
            self._btn_adopt.hide()
            self._set_enter_action_style(
                "Primary", "设为当前拍摄目录", "mdi6.camera-plus-outline",
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
        """派发后台查询(worker 线程), 结果回 _apply_summary_result。

        # §7 旧: 主线程同步跑 query_summary_scope(N 库全表扫 + results glob +
        # 每标本 EXIF + 子库索引 sync), 多断面大表假死数秒且零反馈。
        # 旧同步实现 = 下方 _apply_summary_result 的主体, 查询部分移入
        # SummaryQueryWorker(模式同 TiffPreviewWarmupWorker)。
        # token 防过期: 旧查询结果回来时若已有新请求, 直接丢弃。
        """
        from app.workers.summary_query_worker import SummaryQueryWorker

        token = getattr(self, "_summary_query_token", 0) + 1
        self._summary_query_token = token
        self._stop_summary_query_worker(wait_ms=200)

        # busy 反馈复用现有状态标签(不新增控件): 点击立即可见"查询中"
        try:
            self._summary_stats_lbl.setText(f"查询中… ({len(dirs)} 个拍摄目录)")
            self._grid_count_lbl.setText("查询中…")
        except Exception:
            pass

        worker = SummaryQueryWorker(
            dirs, self._summary_conditions, label_list, parent=self
        )
        worker.finished_result.connect(
            lambda result, cols, t=token: self._on_summary_query_done(
                t, dirs, label_list, labels_map, result, cols
            )
        )
        worker.failed.connect(
            lambda msg, t=token: self._on_summary_query_failed(t, msg)
        )
        self._summary_query_worker = worker
        worker.start()

    def _stop_summary_query_worker(self, wait_ms: int = 200) -> None:
        worker = getattr(self, "_summary_query_worker", None)
        if worker is None:
            return
        try:
            if worker.isRunning():
                worker.cancel()
                if not worker.wait(wait_ms):
                    # 停不下来就脱父进退休名单(同 warmup worker 的处置),
                    # 避免视图销毁连带销毁运行中线程 → 原生 abort。
                    self._retire_summary_query_worker(worker)
        except Exception:
            pass
        self._summary_query_worker = None

    def _retire_summary_query_worker(self, worker) -> None:
        for sig in ("finished_result", "failed"):
            try:
                getattr(worker, sig).disconnect()
            except Exception:
                pass
        try:
            worker.setParent(None)
        except Exception:
            pass
        _RETIRED_WARMUP_WORKERS.append(worker)

    def _on_summary_query_failed(self, token: int, msg: str) -> None:
        if token != getattr(self, "_summary_query_token", 0):
            return
        # 与 _run_scope_refresh 的同步守护同款处置(坏库/锁库):
        # 收起中栏避免半截界面 + 状态栏提示, 不炸 slot。
        self._hide_grid_panel()
        win = self.window()
        bar = getattr(win, "statusBar", None)
        if callable(bar):
            try:
                bar().showMessage(f"汇总读取失败: {msg}", 5000)
            except Exception:
                pass

    def _on_summary_query_done(
        self,
        token: int,
        dirs: list[str],
        label_list: list[str],
        labels_map: dict[str, str],
        result,
        all_columns,
    ) -> None:
        if token != getattr(self, "_summary_query_token", 0):
            return  # 过期结果: 期间已发起新查询
        self._apply_summary_result(dirs, label_list, labels_map, result, all_columns)

    def _apply_summary_result(
        self,
        dirs: list[str],
        label_list: list[str],
        labels_map: dict[str, str],
        result,
        all_columns,
    ) -> None:
        from app.services import cross_workspace_query_service as cwq

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
        # "照片"→"成片": 此处数的是筛选范围内 uid 归组的母版 TIF 成片
        # (右栏概览的「全部成片」含未归编号, 口径不同是刻意的, 标签必须区分)。
        self._summary_stats_lbl.setText(
            f"{n_spec} 编号 · {n_photo} 成片 · RNA {n_rna}{cond_note}"
        )
        self._grid_count_lbl.setText(f"{n_spec} 编号 · {n_photo} 张成片")
        # self._summary_all_columns = cwq.summary_all_columns(dirs)  # §7 旧: 主线程逐库 PRAGMA; 已随查询进 worker
        self._summary_all_columns = all_columns
        self._summary_visible_columns = cwq.resolve_summary_visible_columns(
            self._summary_all_columns,
            _read_summary_visible_column_keys(self.ctx.settings),
        )
        self._rebuild_specimen_table_structure()
        self._refresh_specimen_table()
        self._apply_summary_groups_to_grid(self._groups_for_summary_display())
        panel = getattr(self, "_overview_panel", None)
        if panel is not None:
            scope = f"数据汇总 · {len(dirs)} 个拍摄目录"
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
                    # §7 旧: worker.wait(200) 后直接覆盖引用 —— 200ms 内没停的
                    # 线程成孤儿, 视图销毁时 "QThread: Destroyed while thread is
                    # still running" 直接 abort。新: 停不下来就脱签名、脱父、进
                    # 模块级退休名单继续追踪, 由 _reap_retired_warmup_workers 收割。
                    if not worker.wait(200):
                        self._retire_warmup_worker(worker)
            except Exception:
                pass
        _reap_retired_warmup_workers(wait_ms=0)

        w = TiffPreviewWarmupWorker(paths, parent=self)
        self._tif_preview_warmup_worker = w
        w.finished_result.connect(self._on_tiff_preview_warmup_done)
        w.start()

    def _retire_warmup_worker(self, worker) -> None:
        try:
            worker.finished_result.disconnect(self._on_tiff_preview_warmup_done)
        except Exception:
            pass
        try:
            # 脱离父子关系: 视图销毁不再连带销毁运行中的 QThread(那是 abort 源)。
            worker.setParent(None)
        except Exception:
            pass
        _RETIRED_WARMUP_WORKERS.append(worker)

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
        if worker is not None:
            try:
                if worker.isRunning():
                    worker.cancel()
                    if not worker.wait(500):
                        self._retire_warmup_worker(worker)
            except Exception:
                pass
            self._tif_preview_warmup_worker = None
        _reap_retired_warmup_workers(wait_ms=1500)

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

    @staticmethod
    def _configure_specimen_table_interaction_for(table) -> None:
        """编号表的**行排序**配置 —— 拖行号(垂直表头), 不拖单元格。

        场景(审计 2026-07-12 实锤, 用户裁定 PROJECT_MEMORY:242「可拖动行排序」):
          旧配置 InternalMove + MoveAction 把 QTableWidget 的原生模型当成可移动行的
          模型用 —— 但 QTableWidget **不发 rowsMoved**(_on_specimen_table_rows_moved
          是死代码), 真正执行的是 QTableModel::dropMimeData 的**单元格覆盖**语义:
              A / B / C, 把 C 拖到第 1 行 -> A / C / C(B 被覆盖) -> 删源行 -> A / C
          用户拖一次, **B 行的数据凭空消失**。要的是排序, 拿到的是删行。
        理由(Fable 5, 2026-07-12): 行排序改走 verticalHeader().setSectionsMovable(True)
          —— Qt 只调整**视觉行序**, 一个单元格都不动, 天然无损; sectionMoved 信号
          真实可用, 顺序据此重算。单元格拖放彻底关掉(NoDragDrop)。
        §7 旧:
            table.setDragEnabled(True); table.setAcceptDrops(True)
            table.setDropIndicatorShown(True); table.setDragDropOverwriteMode(False)
            table.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
            table.setDefaultDropAction(Qt.DropAction.MoveAction)
            model.rowsMoved.connect(self._on_specimen_table_rows_moved)
        """
        table.setDragEnabled(False)
        table.setAcceptDrops(False)
        table.setDropIndicatorShown(False)
        table.setDragDropMode(QAbstractItemView.DragDropMode.NoDragDrop)
        vhdr = table.verticalHeader()
        vhdr.setSectionsMovable(True)      # 拖行号 = 真排序(不动数据)
        vhdr.setVisible(True)              # 看得见行号才拖得动
        vhdr.setToolTip("拖动行号可调整编号顺序")

    def _configure_specimen_table_interaction(self) -> None:
        """编号表：拖行号排序、表头拖动调列序、选中联动成片。"""
        table = getattr(self, "_specimen_table", None)
        if table is None or getattr(self, "_specimen_table_interaction_ready", False):
            return
        self._configure_specimen_table_interaction_for(table)
        table.verticalHeader().sectionMoved.connect(self._on_specimen_rows_reordered)
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

    def _on_specimen_rows_reordered(self, _logical: int, _old_v: int, _new_v: int) -> None:
        """拖行号之后: 按**视觉顺序**重算编号顺序 -> 同步下方照片网格。

        (Fable 5, 2026-07-12) 旧的 _on_specimen_table_rows_moved 挂在 QTableWidget
        永不发射的 rowsMoved 上 = 死代码, 见 _configure_specimen_table_interaction_for。
        """
        table = getattr(self, "_specimen_table", None)
        if table is None:
            return
        vhdr = table.verticalHeader()
        order: list[str] = []
        for visual in range(table.rowCount()):
            logical = vhdr.logicalIndex(visual)
            uid = self._row_uid_from_table_item(logical)
            if uid:
                order.append(uid)
        if order:
            self._summary_row_uid_order = order
        self._sync_summary_grid_from_table()

    # §7 旧(死代码, QTableWidget 从不发 rowsMoved):
    # def _on_specimen_table_rows_moved(self, _parent, _start, _end, _destination, _row):
    #     order = self._table_row_uids()
    #     if order:
    #         self._summary_row_uid_order = order
    #     self._sync_summary_grid_from_table()

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
            ui.info(self, "显示列", "请先选择拍摄目录并加载数据汇总。")
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
        # ⚠ 未接线 (v0.56 标注): v0.55 的内容模式切换设计已放弃, 中栏固定为
        # 数据汇总 (_apply_content_mode 直达 _show_data_summary_scope), 本方法
        # 现无任何调用方。按 §7 保留不删; 若恢复多内容模式, 从 _apply_content_mode 分发。
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
        # ⚠ 未接线 (v0.56 标注): v0.55 的内容模式切换设计已放弃, 中栏固定为
        # 数据汇总 (_apply_content_mode 直达 _show_data_summary_scope), 本方法
        # 现无任何调用方。按 §7 保留不删; 若恢复多内容模式, 从 _apply_content_mode 分发。
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
        # ⚠ 未接线 (v0.56 标注): v0.55 的内容模式切换设计已放弃, 中栏固定为
        # 数据汇总 (_apply_content_mode 直达 _show_data_summary_scope), 本方法
        # 现无任何调用方。按 §7 保留不删; 若恢复多内容模式, 从 _apply_content_mode 分发。
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
        # ⚠ 未接线 (v0.56 标注): v0.55 的内容模式切换设计已放弃, 中栏固定为
        # 数据汇总 (_apply_content_mode 直达 _show_data_summary_scope), 本方法
        # 现无任何调用方。按 §7 保留不删; 若恢复多内容模式, 从 _apply_content_mode 分发。
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
        # 进行中的汇总查询一并取消: 回来时 on_activate 会重新派发, 过期
        # 结果本就会被 token 丢弃, 让它跑完只是白烧 IO。
        self._stop_summary_query_worker(wait_ms=200)
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

    def _stop_thumb_worker(self, wait_ms: int = 2000) -> None:
        """join 缩略图解码线程(退出/销毁时). 在途请求先作废。"""
        self._thumb_pending.clear()
        thread = self._thumb_thread
        if thread is None:
            return
        self._thumb_thread = None
        self._thumb_worker = None
        try:
            thread.quit()
            thread.wait(wait_ms)
        except Exception:  # pragma: no cover - 防御性
            pass

    def stop_background_work(self) -> None:
        """App 退出时 join worker 线程."""
        self._stop_tiff_preview_warmup_worker()
        self._stop_summary_query_worker(wait_ms=2000)
        self._stop_thumb_worker(wait_ms=2000)
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

        # 卡片(以及卡片里的 QLabel)马上要被销毁: 让所有在途的 media 解码结果作废,
        # 免得迟到的图往已销毁的 label 上贴 / 贴到新节点的卡片上。
        self._media_gen += 1
        for rid, info in list(self._thumb_pending.items()):
            if info and info[0] == "media":
                self._thumb_pending.pop(rid, None)
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
            glyph, tone, badge = "mdi6.folder-open-outline", icons.TONE_ACCENT, "拍摄目录"
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
        # pm = None                                                  # §7 旧: 建卡循环里逐张同步解码
        # try:                                                       #   (TIFF 还可能 fork ImageMagick),
        #     from app.utils.image_thumbnail import decode_image_thumbnail   #   6 张 = 6 次全量解码堵住 GUI。
        #     pm = decode_image_thumbnail(str(path), max_size=150)
        # except Exception:
        #     pm = None
        # if pm is not None and not pm.isNull():
        #     thumb.setPixmap(
        #         pm.scaled(
        #             thumb.size(),
        #             Qt.AspectRatioMode.KeepAspectRatio,
        #             Qt.TransformationMode.SmoothTransformation,
        #         )
        #     )
        # else:
        #     thumb.setText(path.suffix.upper().lstrip(".") or "FILE")
        # 新: 主线程只查缓存(try_cached_image_data 永不全量解码); 未命中先放占位文字,
        #     解码丢给 worker 线程, 结果经 QImage 回主线程再 make_pixmap。
        self._fill_media_thumb(thumb, path)
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

    def _fill_media_thumb(self, thumb: QLabel, path: Path) -> None:
        """影像预览缩略图: 缓存命中即同步上屏, 未命中异步解码(占位不变形)."""
        from app.utils.image_thumbnail import try_cached_image_data

        image = None
        try:
            image = try_cached_image_data(str(path), 150)
        except Exception:
            image = None
        if image is not None and not image.isNull():
            self._apply_media_thumb(thumb, image, path)
            return
        thumb.setText(path.suffix.upper().lstrip(".") or "FILE")
        self._request_thumb_decode(
            "media", str(path), 150, target=thumb, extra=self._media_gen,
        )

    def _apply_media_thumb(self, thumb: QLabel, image, path: Path) -> None:
        from app.utils.image_thumbnail import make_pixmap

        pm = make_pixmap(image)  # QPixmap 只能在主线程构造
        try:
            if pm is not None and not pm.isNull():
                thumb.setPixmap(
                    pm.scaled(
                        thumb.size(),
                        Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation,
                    )
                )
                thumb.setText("")
            else:
                thumb.setText(path.suffix.upper().lstrip(".") or "FILE")
        except RuntimeError:  # pragma: no cover - 卡片已被销毁
            pass

    # ── 缩略图解码 worker (GUI 线程零解码) ─────────────────────────────────────
    def _ensure_thumb_worker(self) -> bool:
        """懒起一条常驻解码线程; 只在真的需要解码时才创建。"""
        if self._thumb_thread is not None:
            return True
        try:
            from app.workers.thumbnail_worker import GridThumbnailWorker

            thread = QThread()  # 不 parent 到 self: 避免父子销毁与 quit/wait 抢跑
            worker = GridThumbnailWorker()
            worker.moveToThread(thread)
            worker.decoded.connect(self._on_thumb_decoded)  # 自动 → QueuedConnection
            thread.start()
        except Exception:  # pragma: no cover - 无 Qt 线程时降级为「不解码」
            return False
        self._thumb_thread = thread
        self._thumb_worker = worker

        thread_ref = thread

        def _cleanup(*_a: object) -> None:
            try:
                thread_ref.quit()
                thread_ref.wait(2000)
            except Exception:
                pass

        self._thumb_cleanup_fn = _cleanup  # 强引用, 别让 GC 收走
        try:
            self.destroyed.connect(_cleanup)
        except Exception:  # pragma: no cover
            pass
        return True

    def _request_thumb_decode(
        self,
        kind: str,
        path: str,
        max_size: int,
        *,
        target=None,
        extra=None,
    ) -> None:
        if not path or not self._ensure_thumb_worker():
            return
        self._thumb_req_counter += 1
        req_id = self._thumb_req_counter
        self._thumb_pending[req_id] = (kind, target, path, extra)
        QMetaObject.invokeMethod(
            self._thumb_worker,
            "decode",
            Qt.ConnectionType.QueuedConnection,
            Q_ARG(int, req_id),
            Q_ARG(str, path),
            Q_ARG(int, max_size),
        )

    def _on_thumb_decoded(self, req_id: object, image: object) -> None:
        """worker 回调 —— 队列连接, 跑在主线程。只有这里能碰 QPixmap/QWidget。"""
        try:
            key = int(req_id)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return
        info = self._thumb_pending.pop(key, None)
        if info is None:
            return  # 过期(视图已重建/已切走)
        kind, target, path, extra = info
        if kind == "preview":
            self._apply_preview_image(int(extra or 0), image)
            return
        if kind == "media":
            if extra != self._media_gen:
                return  # 已经切到别的节点, 丢弃
            if target is None:
                return
            self._apply_media_thumb(target, image, Path(path))

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
        saved_roots = list(getattr(self.ctx.settings, "project_scan_roots", []))
        start = saved_roots[-1] if saved_roots else ""
        path = ui.get_existing_directory(self, "选择要扫描的磁盘或目录", start)
        if not path:
            return
        root = str(Path(path).resolve())
        progress = QProgressDialog("正在识别项目和拍照目录…", "取消", 0, 0, self)
        progress.setWindowTitle("扫描项目位置")
        progress.setWindowModality(Qt.WindowModality.ApplicationModal)
        progress.setMinimumDuration(0)
        progress.show()
        QApplication.processEvents()
        try:
            project_count, workspace_count, added = self._register_projects_from_scan(
                root, max_depth=6
            )
        except OSError:
            project_count = workspace_count = added = 0
        progress.close()
        # 用户场景：这个位置是长期项目仓库，保存下来后即使升级丢索引也能自动找回。
        self.ctx.settings.project_scan_roots = [*saved_roots, root]
        ui.info(
            self,
            "扫描完成",
            f"已保存扫描位置：{root}\n\n"
            f"识别到 {project_count} 个项目、{workspace_count} 个旧拍照目录；"
            f"新增 {added} 个到项目树。",
        )
        pts.clear_project_tree_cache(root)
        self.ctx.settings.project_tree_view_mode = "all"
        self._root = None
        self.ctx.settings.project_tree_root = None
        self._sync_view_mode_buttons()
        self._reload_project_tree()

    def _sync_library_dir_button(self) -> None:
        configured = str(getattr(self.ctx.settings, "project_library_dir", "") or "")
        if not hasattr(self, "_btn_library_dir"):
            return
        self._btn_library_dir.setText("项目库目录 ✓" if configured else "项目库目录")
        self._btn_library_dir.setToolTip(
            f"新建项目默认保存在：{configured}\n点击可修改"
            if configured else
            "设置以后新建项目默认保存到哪个上级目录"
        )
        if hasattr(self, "_act_clear_library_dir"):
            self._act_clear_library_dir.setEnabled(bool(configured))

    def _choose_project_library_directory(self) -> None:
        """Choose an optional default project home and scan it immediately."""
        from app.services.project_service import default_project_parent_directory

        current = str(getattr(self.ctx.settings, "project_library_dir", "") or "")
        start = current or default_project_parent_directory()
        chosen = ui.get_existing_directory(self, "选择项目库目录", start)
        if not chosen:
            return
        chosen = str(Path(chosen).resolve())
        # 用户场景（2026-07-13）：统一保存目录同时也是恢复扫描位置；设置后立即
        # 找回其中已有项目，但不影响从其他磁盘手动导入项目。
        self.ctx.settings.project_library_dir = chosen
        roots = list(getattr(self.ctx.settings, "project_scan_roots", []))
        self.ctx.settings.project_scan_roots = [*roots, chosen]
        try:
            project_count, workspace_count, added = self._register_projects_from_scan(
                chosen, max_depth=6
            )
        except (OSError, ValueError):
            project_count = workspace_count = added = 0
        self._sync_library_dir_button()
        pts.clear_project_tree_cache()
        self._set_view_mode("all")
        ui.info(
            self,
            "项目库目录",
            f"以后新建项目默认保存在：\n{chosen}\n\n"
            f"已识别 {project_count} 个项目、{workspace_count} 个旧拍照目录；"
            f"新增 {added} 个。",
        )

    def _clear_project_library_directory(self) -> None:
        """Clear only the creation default; imported projects remain registered."""
        self.ctx.settings.project_library_dir = ""
        self._sync_library_dir_button()
        ui.info(self, "项目库目录", "已取消默认保存目录；现有项目和导入记录不会删除。")

    def _register_projects_from_scan(self, root: str, *, max_depth: int = 6) -> tuple[int, int, int]:
        """Scan one configured location and append recognisable projects."""
        from app.services.project_service import (
            default_user_projects_json_path,
            list_projects,
            record_recent_workspace,
            register_project_root,
        )

        jp = default_user_projects_json_path()
        before = len(list_projects(jp))
        project_roots = pts.discover_project_roots(root, max_depth=max_depth)
        root_paths = [Path(row["path"]).resolve() for row in project_roots]
        for row in project_roots:
            register_project_root(row["path"], name=row["name"], user_projects_json_path=jp)

        candidates = pts.discover_workspace_candidates(root, max_depth=max_depth)
        standalone: list[dict] = []
        for candidate in candidates:
            candidate_path = Path(candidate["path"]).resolve()
            if any(candidate_path == project_root or project_root in candidate_path.parents
                   for project_root in root_paths):
                continue
            standalone.append(candidate)
            record_recent_workspace(jp, candidate["path"])

        added = len(list_projects(jp)) - before
        return len(project_roots), len(standalone), added

    def _recover_empty_catalog_from_saved_roots(self) -> None:
        """Rebuild an empty catalogue from persistent user-selected locations."""
        from app.services.project_service import default_user_projects_json_path, list_projects

        if list_projects(default_user_projects_json_path()):
            return
        for root in getattr(self.ctx.settings, "project_scan_roots", []):
            try:
                if Path(root).is_dir():
                    self._register_projects_from_scan(root, max_depth=6)
            except (OSError, ValueError):
                continue

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
                "可改用「添加已有文件夹」登记。",
            )
            return
        cur = getattr(self.ctx, "current_project_dir", None)
        if cur and str(cur) == str(old_path):
            self.ctx.current_project_dir = new_path
        pts.clear_project_tree_cache()
        self._reload_project_tree()
        ui.info(self, "已更新", f"项目路径已更新为：\n{new_path}")

    def _add_workspace_manual(self) -> None:
        """手动选择一个已有项目根或旧拍照工作区并登记。

        与 ``_scan_disk`` 互补: 扫描是"指定盘/目录深扫找全部"; 这是"我就要这一个"。
        选目录 → record_recent_workspace 去重登记 → 刷新 flat list。
        """
        path = ui.get_existing_directory(self, "选择已有项目文件夹")
        if not path:
            return
        from app.services.project_service import (
            default_user_projects_json_path,
            record_recent_workspace,
            register_project_root,
        )
        try:
            if pts.is_region(path):
                register_project_root(
                    path,
                    name=Path(path).name,
                    user_projects_json_path=default_user_projects_json_path(),
                )
            else:
                record_recent_workspace(default_user_projects_json_path(), path)
        except Exception as exc:
            ui.warn(self, "导入失败", f"登记已有项目失败:\n{exc}")
            return
        pts.clear_project_tree_cache(self._root or path)
        self._reload_project_tree()
        ui.info(self, "导入完成", f"已加入项目树:\n{path}")

    def _new_region(self) -> None:
        """新建项目: 只建**一个空项目目录**(容器), 断面/采样点之后在树里自由加。

        场景(用户 2026-07-12): "只建立一个项目目录, 后续点击这个目录, 也可以建立子目录"
          "我创建了江苏盐城2026, 可以再创建 2 个实际的工作区, 即 2 个断面进行拍照;
           江苏盐城只是汇总这两个断面 —— 目录中 2 个子目录。然后我可以在目录中自由创建子目录。"

        §7 旧流程(2026-07-12 上午, 同日被否): 一个对话框问完 项目名/位置/年份/采集人/地区代码
          + 采样点多行列表, 一次建好「项目 + N 个采样点」, 建完直接进第一个采样点开拍。
          旧对话框之所以塞这么满, 是因为项目根是容器(非工作区) → 设置抽屉打不开 → 项目级
          默认值**只有那一次机会**可填。现在项目树补了「项目设置」入口(_open_node_settings),
          字段有了事后填的地方, 对话框才砍得掉。
          恢复旧行为: 反注释下面 create_survey_project(sites=...) 那段 + 采样点登记 +
          「建完直接进第一个点」分支, 并反注释 new_survey_project_dialog 里的字段。
        (Fable 5, 2026-07-12)
        """
        from app.widgets.new_survey_project_dialog import NewSurveyProjectDialog

        # 「＋项目」创建的是全局顶层项目，不能继承当前选中的 B2/断面或
        # 当前拍摄工作区。默认位置只从全局项目目录推导。
        from app.services.project_service import default_project_parent_directory

        # 用户场景：优先使用可选的统一项目库目录；未设置/磁盘断开时才从旧项目推导。
        configured = str(getattr(self.ctx.settings, "project_library_dir", "") or "")
        default_parent = (
            configured if configured and Path(configured).is_dir()
            else default_project_parent_directory()
        )
        dlg = NewSurveyProjectDialog(self, default_parent_dir=default_parent)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        vals = dlg.values()

        try:
            from app.services.project_scaffold_service import create_survey_project

            # §7 旧调用(恢复时反注释): 一次建好项目 + N 个采样点, 并把每个点登记进
            #    user_projects.json 供顶栏「最近项目」直接切。
            # from app.services.project_service import (
            #     default_user_projects_json_path, load_user_projects,
            #     save_project_descriptor,
            # )
            # res = create_survey_project(
            #     vals["parent_dir"], name=vals["name"], sites=vals["sites"],
            #     meta=vals["meta"], collector=vals["collector"],
            #     province=vals["province"],
            # )
            # for site_dir in res["sites"]:
            #     save_project_descriptor(
            #         default_user_projects_json_path(),
            #         {
            #             "name": Path(site_dir).name, "directory": site_dir,
            #             "location": vals["meta"].get("location", ""),
            #             "year": vals["meta"].get("year", ""),
            #             "collector": vals["collector"],
            #         },
            #         existing_projects=load_user_projects(),
            #     )
            res = create_survey_project(
                vals["parent_dir"], name=vals["name"], sites=[]
            )
        except (ValueError, FileExistsError, FileNotFoundError) as exc:
            ui.warn(self, "新建项目", str(exc))
            return
        except Exception as exc:  # pragma: no cover - defensive
            ui.warn(self, "新建项目", f"创建失败：{exc}")
            return

        # 空项目根不是照片工作区，也必须追加登记到项目列表；否则「全部项目」模式
        # 刷新后找不到它，旧实现便退回单项目模式，造成其他项目看似全部消失。
        try:
            from app.services.project_service import register_project_root

            register_project_root(res["root"], name=vals["name"])
        except Exception as exc:  # noqa: BLE001
            ui.warn(
                self,
                "新建项目",
                f"项目已创建，但添加到项目列表失败：\n{exc}",
            )

        # 新建项目属于跨项目操作。无论此前浏览的是哪个根，都回到全部项目，
        # 否则新项目已登记却仍被旧根过滤，看起来就像“创建了但没有显示”。
        self._set_view_mode("all")
        self.focus_project(res["root"])

        # §7 旧提示(含 N 个采样点), 恢复时反注释:
        # n = len(res["sites"])
        # ui.info(self, "新建项目", f"项目「{vals['name']}」已建好，含 {n} 个采样点。...")
        ui.info(
            self,
            "新建项目",
            f"项目「{vals['name']}」已建好（空项目）。\n\n"
            "· 点「新建子目录」添加断面 / 采样点，双击进去就能拍\n"
            "· 点「项目设置」填采集人、地区代码、默认坐标、拍摄场地——\n"
            "  下面所有采样点自动继承，拍照时右栏直接带出来，不用每次重填\n"
            "· 照片只会落在采样点里，不会堆在项目根",
        )

    def prompt_new_child_under_root(self, root_path: Optional[str] = None) -> None:
        """Public top-bar entry: create under the explicitly targeted project."""
        # 用户场景：全局项目树没有专属 root；新增下级目录的归属由调用动作明确传入。
        self._new_subfolder(parent_override=root_path or self._root)

    def _new_subfolder(self, parent_override: Optional[str] = None) -> None:
        parent = parent_override or self._selected_path() or self._root
        # 安全闸(GUI 实测 2026-07-12): 选中的节点可能属于**别的项目**(「全部项目」模式下
        # 树里列着所有已知项目; 或选中项是上一个根的残留) —— 那样会把子目录静默建到别人
        # 家里去。当前有根时, 只允许建在根的子树内, 否则退回根本身。
        if self._root and parent:
            try:
                Path(parent).resolve().relative_to(Path(self._root).resolve())
            except ValueError:
                parent = self._root
        if not parent:
            ui.info(self, "项目树", "请先选择根目录或一个文件夹。")
            return
        name, ok = QInputDialog.getText(
            self,
            "新建下级目录",
            "目录名称（如 断面A、采样点1）：",
        )
        name = (name or "").strip()
        if not ok or not name:
            return
        if any(c in name for c in ("/", "\\", "..")):
            ui.warn(self, "项目树", "名称不合法（不能含 / \\ ..）。")
            return
        new_path = Path(parent) / name
        try:
            new_path.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            ui.warn(self, "项目树", f"无法创建：{exc}")
            return
        # 用户场景（2026-07-13）：在项目 b 下新建子目录后，必须立即在 b 下显示，
        # 自动展开并选中，用户随后可双击或设为拍摄目录。局部缓存键可能因
        # Windows/WSL 路径写法不同而漏清，因此这里清整棵树缓存保证界面与磁盘一致。
        pts.clear_project_tree_cache()
        self._reload_project_tree()
        self._select_tree_path(str(new_path))

    # ── 基本操作: 重命名 / 移动 / 删除 (用户 R-009, 2026-07-13) ─────────────────
    #
    # 用户: "有些属于基本操作…用户输入错了，都不能改正的" —— 项目树此前只能新建,
    # 改名/挪窝/删除全都做不到。危险动作的事实与口径统一走 app/services/project_node_ops:
    #   * 删除默认送系统回收站(可找回); 有 TIFF 母图(无价、不可再生)必须手打目录名;
    #   * 移动 = 整个文件夹搬走(_data/project.db 跟着走, 零迁移), 动手前先预览;
    #   * 改名/移动之后, user_projects.json 里的路径同步改写(否则「最近使用」指空)。

    def _ask_yes(self, title: str, text: str) -> bool:
        """Yes/No 确认。危险动作(删除/移动)统一走这里。"""
        from PyQt6.QtWidgets import QMessageBox

        resp = ui.question(self, title, text)
        return resp == QMessageBox.StandardButton.Yes

    def _rename_node(self) -> None:
        from app.services import project_node_ops as ops

        path = self._selected_path()
        if not path:
            ui.info(self, "重命名", "请先选中一个项目或目录。")
            return
        old_name = Path(path).name
        name, ok = QInputDialog.getText(self, "重命名", "新名称：", text=old_name)
        if not ok or not str(name).strip() or str(name).strip() == old_name:
            return
        try:
            new_path = ops.rename_node(path, str(name))
        except (ValueError, FileExistsError, FileNotFoundError, OSError) as exc:
            ui.warn(self, "重命名失败", str(exc))
            return
        pts.clear_project_tree_cache(self._root or str(Path(new_path).parent))
        if self._root and _resolved_eq(self._root, path):
            self._root = new_path
            self.ctx.settings.project_tree_root = new_path
        self._reload_project_tree()
        self._reload_card_grid()
        self._select_tree_path(new_path)

    def _move_node(self) -> None:
        from app.services import project_node_ops as ops

        path = self._selected_path()
        if not path:
            ui.info(self, "移动", "请先选中要移动的目录。")
            return
        start = str(Path(path).parent)
        target = ui.get_existing_directory(self, "移动到哪个项目 / 目录下", start)
        if not target:
            return
        try:
            preview = ops.preview_move(path, target)
        except OSError as exc:
            ui.warn(self, "移动失败", str(exc))
            return

        contents = preview["contents"]
        lines = [
            f"来源：{preview['source_path']}",
            f"目标：{preview['target_path']}",
            "",
            f"随目录一起搬走：{contents['workspace_count']} 个拍摄目录 · "
            f"{contents['tiff_count']} 张 TIFF 母图 · {contents['jpg_count']} 张 JPG",
            "",
            "文件夹整体移动，数据库跟着走（零迁移）；已填过的资料一律保留，"
            "只有空字段才继承新上级项目。",
        ]
        if not self._ask_yes("移动到项目", "\n".join(lines)):
            return
        try:
            dest = ops.move_node(path, target)
        except (ValueError, FileExistsError, FileNotFoundError, OSError) as exc:
            ui.warn(self, "移动失败", str(exc))
            return
        pts.clear_project_tree_cache(self._root or target)
        self._reload_project_tree()
        self._reload_card_grid()
        self._select_tree_path(dest)

    def _delete_node(self) -> None:
        from app.services import project_node_ops as ops

        path = self._selected_path()
        if not path:
            ui.info(self, "删除", "请先选中要删除的项目或目录。")
            return
        name = Path(path).name
        level = ops.confirm_level(path)
        summary = ops.summarize_for_confirm(path)

        if level == "typed":
            # 有 TIFF 母图 —— 不可再生。必须手打目录名, 挡住手滑。
            typed, ok = QInputDialog.getText(
                self,
                "删除项目（含母图）",
                f"{summary}\n\n"
                f"删除将把整个目录移入系统回收站（可找回）。\n"
                f"确认请输入目录名「{name}」：",
            )
            if not ok or str(typed).strip() != name:
                if ok:
                    ui.info(self, "删除", "名称不匹配，已取消。")
                return
        elif level == "confirm":
            if not self._ask_yes(
                "删除项目",
                f"{summary}\n\n删除将把整个目录移入系统回收站（可找回）。仍要删除吗？",
            ):
                return
        else:
            if not self._ask_yes(
                "删除",
                f"{summary}\n\n删除将把整个目录移入系统回收站（可找回）。仍要删除吗？",
            ):
                return

        try:
            ops.delete_node(path)  # 默认回收站, 不是真删
        except RuntimeError as exc:  # 回收站不可用 —— 绝不静默改成永久删除
            ui.warn(self, "删除失败", str(exc))
            return
        except (ValueError, FileNotFoundError, OSError) as exc:
            ui.warn(self, "删除失败", str(exc))
            return

        parent = str(Path(path).parent)
        if self._root and _resolved_eq(self._root, path):
            self._root = None
            self.ctx.settings.project_tree_root = None
            self.ctx.settings.project_tree_view_mode = "all"
            self._sync_view_mode_buttons()
        pts.clear_project_tree_cache(self._root or parent)
        self._reload_project_tree()
        self._reload_card_grid()
        ui.info(self, "删除", f"「{name}」已移入回收站。")

    def _open_node_settings(self) -> None:
        """选中节点 →「项目设置…」: 在该节点(通常是项目根)自己的库上开设置抽屉。

        需求场景(用户 2026-07-12): "每个项目、子项目或工作区, 可以设计一些采集人、采集时间、
        坐标、经纬度、拍摄场地等信息吗, 方便主界面右侧可以自动读取, 减少每次拍照都要填写?"

        继承机制早就有(project_settings_service.get_effective 沿目录树向上找, 近的祖先赢),
        缺的是**入口**: 项目根是容器(非工作区), 进不去工作台 → 抽屉打不开 → 项目级默认值
        无处可填。这正是旧「新建项目」对话框塞满 6 个字段的原因; 补上这个入口, 那些字段才
        有了事后填的地方。详见 docs/specs/2026-07-12-slim-new-project-and-settings-inheritance.md
        """
        path = self._selected_path() or self._root
        if not path:
            ui.info(self, "项目树", "请先选择一个项目或文件夹。")
            return
        from app.widgets.project_settings_dialog import open_project_settings_dialog
        open_project_settings_dialog(self, self.ctx, path)

    def _enter_selected(self) -> None:
        path = self._selected_path()
        if not path:
            return
        # 项目≠采样点: 一个还有子文件夹、又不是工作区的节点, 多半是**项目**
        # (设置继承的锚点), 不是拍照的地方。不禁止, 但要确认 —— 免得项目根
        # 稀里糊涂变成拍照工作区、照片堆在项目根上。(用词统一: 用户 2026-07-12)
        items = self._tree.selectedItems()
        item = items[0] if items else None
        if item is not None and item.childCount() > 0 and not pts.is_workspace(path):
            resp = ui.question(
                self,
                "进入工作区",
                f"「{Path(path).name}」下面还有子文件夹，看起来是**项目**（不是采样点）。"
                "照片通常放在下面的采样点里。仍要把这一层当成采样点进入吗？",
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
                    "或先回到照片工作台停止正在运行的合成/整理任务。",
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
