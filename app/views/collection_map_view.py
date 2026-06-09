"""collection_map_view.py — 采集地图：站位经纬度分级可视化 + 出版底图出图.

两栏布局：
  左栏「项目」：全部项目 + 各已知项目（跨项目聚合，默认全部）；下方「站位标识」样式面板。
  右栏：工具条（粒度 站位/断面/地区 · 底图 · 校准 · 导出）+ 地图。

数据 = 采集记录簿（collection_records）站位经纬度，按 站位/断面/地区 三级聚合
（crs.map_points / map_points_across）。

底图两模式（QStackedWidget）：
  交互(OSM) = TileMapWidget（v1，点击点弹信息卡 + 跳转采集记录）；
  出版底图 = PublicationMapWidget（官方审图号图按控制点校准精确落点 / Nature·R 生成底图），
  导出论文级 PNG/PDF/SVG/EPS。底图由 basemap_registry 枚举。
"""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Optional

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPushButton,
    QScrollArea,
    QSlider,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from app.config.icons import TONE_ACCENT, TONE_MUTED, icon, set_button_icon
from app.config.theme import local_font_css
from app.services import basemap_registry as br
from app.services import collection_record_service as crs
from app.services import project_settings_service as pss
from app.services.project_service import list_projects
from app.utils import ui
from app.views.base_view import BaseView
from app.widgets.marker_style_panel import MarkerStylePanel
from app.widgets.tile_map_widget import TileMapWidget
# NOTE: PublicationMapWidget pulls in matplotlib (~1.8 s import). It is imported
# lazily at construction (see _build_*) so app startup — which eagerly imports
# this module via the view registry — does not pay the matplotlib cost unless
# the user actually opens the 采集地图 tab.

if TYPE_CHECKING:
    from app.app_context import AppContext


_LEVELS: list[tuple[str, str]] = [
    ("station", "站位"),
    ("site", "断面"),
    ("province", "地区"),
]

_STYLE_KEY = "map_marker_style"


def _theme():
    try:
        from app.config.theme import TOKENS
        return TOKENS.get
    except Exception:  # pragma: no cover
        return lambda k, d=None: d


def _user_projects_json() -> Path:
    """data/user_projects.json（同 overview_view 的解析）。"""
    return Path(__file__).resolve().parents[2] / "data" / "user_projects.json"


class CollectionMapView(BaseView):
    """采集地图 — 两栏：项目 | 地图。"""

    view_id = "collection_map"
    nav_title = "采集地图"
    nav_icon = "🗺️"

    def __init__(self, ctx: "AppContext") -> None:
        self._level: str = "station"
        self._level_btns: dict[str, QPushButton] = {}
        self._points: list[dict] = []
        self._active_basemap: Optional[dict] = None
        self._project_filter: Optional[str] = None   # None = 全部项目
        self._style: dict = {}
        super().__init__(ctx)

    # ── UI ──────────────────────────────────────────────────────────────────

    def _setup_ui(self) -> None:
        self._apply_style()
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self._page_scroll = QScrollArea()
        self._page_scroll.setObjectName("MapPageScroll")
        self._page_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._page_scroll.setWidgetResizable(True)
        self._page_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._page_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        content = QWidget()
        content.setObjectName("MapPageContent")
        content.setMinimumWidth(1010)
        lay = QHBoxLayout(content)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(12)
        lay.addWidget(self._build_left_pane(), 0)
        lay.addWidget(self._build_right_pane(), 1)

        self._page_scroll.setWidget(content)
        root.addWidget(self._page_scroll, 1)

    def _build_left_pane(self) -> QWidget:
        pane = QWidget()
        pane.setObjectName("LeftPane")
        v = QVBoxLayout(pane)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(14)
        v.addWidget(self._build_project_card(), 0)
        v.addWidget(self._build_style_card(), 0)
        v.addStretch(1)

        # 左栏整体可滚动：窗口偏矮时「项目」卡 + 「站位标识」卡仍能完整触达，
        # 地图右栏保持满高（不随整页滚动）。站位样式表单本身另有卡内滚动。
        scroll = QScrollArea()
        scroll.setObjectName("LeftScroll")
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll.setFixedWidth(272)
        scroll.setWidget(pane)
        return scroll

    def _card(self, title: str, icon_name: str = "") -> tuple[QFrame, QVBoxLayout, QHBoxLayout]:
        """统一卡片外壳：圆角 + 软阴影 + 图标标题 + 分隔线。返回 (卡片, 内容布局, 标题行)。"""
        from app.config.effects import apply_card_shadow
        from app.widgets._collapse import set_layout_children_visible
        card = QFrame()
        card.setObjectName("Card")
        outer = QVBoxLayout(card)
        outer.setContentsMargins(14, 12, 14, 12)
        outer.setSpacing(9)
        head_row = QHBoxLayout()
        head_row.setSpacing(7)
        # 收起/展开 ▾/▸ 切换（隐藏分隔线及以下全部内容，只留标题行）
        chevron = QPushButton()
        chevron.setObjectName("CollapseBtn")
        chevron.setFixedSize(18, 18)
        chevron.setCursor(Qt.CursorShape.PointingHandCursor)
        head_row.addWidget(chevron)
        if icon_name:
            ic = QLabel()
            ic.setPixmap(icon(icon_name, color=TONE_ACCENT).pixmap(16, 16))
            ic.setFixedWidth(18)
            head_row.addWidget(ic)
        head = QLabel(title)
        head.setObjectName("CardTitle")
        head_row.addWidget(head)
        head_row.addStretch(1)
        outer.addLayout(head_row)
        div = QFrame()
        div.setObjectName("CardDiv")
        div.setFixedHeight(1)
        outer.addWidget(div)
        apply_card_shadow(card, blur=16, y=3, alpha=28)

        def toggle():
            collapsed = not getattr(card, "_collapsed", False)
            card._collapsed = collapsed
            set_layout_children_visible(outer, 1, not collapsed)  # 1 = 分隔线起
            set_button_icon(chevron, "mdi6.chevron-right" if collapsed else "mdi6.chevron-down",
                            color=TONE_MUTED, size=15)
        set_button_icon(chevron, "mdi6.chevron-down", color=TONE_MUTED, size=15)
        chevron.clicked.connect(toggle)
        return card, outer, head_row

    def _build_project_card(self) -> QFrame:
        card, lay, head_row = self._card("项目", "mdi6.folder-multiple-outline")
        # 标题行右侧「+」新建项目入口
        self._add_proj_btn = QPushButton()
        self._add_proj_btn.setObjectName("AddProjBtn")
        self._add_proj_btn.setFixedSize(26, 26)
        self._add_proj_btn.setToolTip("新建 / 打开项目")
        self._add_proj_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        set_button_icon(self._add_proj_btn, "mdi6.plus", color=TONE_ACCENT, size=16)
        self._add_proj_btn.clicked.connect(self._on_add_menu)
        head_row.addWidget(self._add_proj_btn)
        self._proj_list = QListWidget()
        self._proj_list.setObjectName("ProjList")
        self._proj_list.setFrameShape(QFrame.Shape.NoFrame)
        self._proj_list.setSpacing(2)
        self._proj_list.setMinimumHeight(142)
        self._proj_list.setMaximumHeight(210)
        self._proj_list.itemSelectionChanged.connect(self._on_project_changed)
        self._proj_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._proj_list.customContextMenuRequested.connect(self._on_proj_context_menu)
        lay.addWidget(self._proj_list)
        return card

    def _build_style_card(self) -> QFrame:
        card, lay, _hr = self._card("站位标识", "mdi6.map-marker-outline")
        # 实时预览色块
        prev_row = QHBoxLayout()
        prev_row.setContentsMargins(0, 0, 0, 0)
        prev_row.setSpacing(8)
        prev_row.addWidget(QLabel("预览"))
        self._marker_preview = QLabel()
        self._marker_preview.setFixedSize(54, 30)
        self._marker_preview.setObjectName("MarkerPreview")
        prev_row.addWidget(self._marker_preview)
        prev_row.addStretch()
        lay.addLayout(prev_row)

        # 样式表单卡内滚动：滚动条贴在「站位标识」卡右侧，用户能直接看到可下滑。
        self._style_panel = MarkerStylePanel()
        self._style_panel.style_changed.connect(self._on_style_changed)
        style_scroll = QScrollArea()
        style_scroll.setObjectName("StyleScroll")
        style_scroll.setFrameShape(QFrame.Shape.NoFrame)
        style_scroll.setWidgetResizable(True)
        style_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        style_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        style_scroll.setMinimumHeight(260)
        style_scroll.setMaximumHeight(360)
        style_scroll.setWidget(self._style_panel)
        lay.addWidget(style_scroll)
        return card

    # ── 项目行（名称 + 站位数徽章）──────────────────────────────────────────────

    def _make_project_item(self, name: str, count: Optional[int], directory):
        item = QListWidgetItem()
        item.setData(Qt.ItemDataRole.UserRole, directory)
        row = QFrame()
        row.setObjectName("ProjRow")
        h = QHBoxLayout(row)
        h.setContentsMargins(8, 5, 10, 5)
        h.setSpacing(9)

        bar = QFrame()              # 选中态左侧 accent 指示条
        bar.setObjectName("ProjBar")
        bar.setFixedWidth(3)
        h.addWidget(bar)

        avatar = QLabel(name[:1] or "·")   # 首字母圆标
        avatar.setObjectName("ProjAvatar")
        avatar.setFixedSize(28, 28)
        avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        h.addWidget(avatar)

        col = QVBoxLayout()
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(1)
        nm = QLabel(name)
        nm.setObjectName("ProjName")
        sub = QLabel(f"{count} 站位" if count is not None else "—")
        sub.setObjectName("ProjSub")
        col.addWidget(nm)
        col.addWidget(sub)
        h.addLayout(col, 1)

        # 选中态需手动 repolish 的子控件（含行框本身，驱动底色/指示条/圆标变色）
        row._sel_labels = [row, bar, avatar, nm, sub]
        item.setSizeHint(row.sizeHint())
        return item, row

    def _restyle_proj_selection(self) -> None:
        """setItemWidget 子控件不吃 ::item:selected → 用 [sel] 属性手动同步选中态。"""
        for i in range(self._proj_list.count()):
            item = self._proj_list.item(i)
            w = self._proj_list.itemWidget(item)
            if w is None:
                continue
            flag = "1" if item.isSelected() else "0"
            for lbl in getattr(w, "_sel_labels", []):
                lbl.setProperty("sel", flag)
                lbl.style().unpolish(lbl)
                lbl.style().polish(lbl)

    def _build_right_pane(self) -> QWidget:
        pane = QWidget()
        pane.setObjectName("RightPane")
        pane.setMinimumWidth(690)
        v = QVBoxLayout(pane)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(10)

        # 工具条 1：粒度 + 计数
        header = QFrame()
        header.setObjectName("MapHeader")
        bar = QHBoxLayout(header)
        bar.setContentsMargins(0, 0, 0, 0)
        bar.setSpacing(8)
        title = QLabel("采集地图")
        title.setObjectName("PaneTitle")
        bar.addWidget(title)
        bar.addSpacing(10)
        seg = QFrame()                       # 分段控件容器（站位/断面/地区）
        seg.setObjectName("SegGroup")
        seg_l = QHBoxLayout(seg)
        seg_l.setContentsMargins(3, 3, 3, 3)
        seg_l.setSpacing(3)
        grp = QButtonGroup(self)
        grp.setExclusive(True)
        for lvl, label in _LEVELS:
            btn = QPushButton(label)
            btn.setObjectName("LevelBtn")
            btn.setCheckable(True)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setChecked(lvl == self._level)
            btn.clicked.connect(lambda _=False, l=lvl: self._set_level(l))
            grp.addButton(btn)
            self._level_btns[lvl] = btn
            seg_l.addWidget(btn)
        bar.addWidget(seg)
        bar.addStretch()
        self._count_lbl = QLabel("")
        self._count_lbl.setObjectName("CountLbl")
        bar.addWidget(self._count_lbl)
        v.addWidget(header)

        # 工具条 2：底图 + 校准 + 导出
        tools = QFrame()
        tools.setObjectName("MapToolStrip")
        bar2 = QHBoxLayout(tools)
        bar2.setContentsMargins(10, 8, 10, 8)
        bar2.setSpacing(8)
        tool_label = QLabel("底图")
        tool_label.setObjectName("ToolLabel")
        bar2.addWidget(tool_label)
        self._basemap_combo = QComboBox()
        # Cap width — without this the combo grows to the longest item name
        # ("世界 · Winkel Tripel（NatGeo 标准）"), making the box oversized; the
        # closed field elides, the popup still shows full names.
        self._basemap_combo.setMinimumWidth(170)
        self._basemap_combo.setMaximumWidth(230)
        self._basemap_combo.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        self._basemap_combo.currentIndexChanged.connect(self._on_basemap_changed)
        bar2.addWidget(self._basemap_combo)
        self._calibrate_btn = QPushButton("校准")
        self._calibrate_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        set_button_icon(self._calibrate_btn, "mdi6.crosshairs-gps", color=TONE_MUTED, size=15)
        self._calibrate_btn.clicked.connect(self._on_calibrate)
        self._calibrate_btn.setEnabled(False)
        bar2.addWidget(self._calibrate_btn)
        self._import_btn = QPushButton("导入经纬度")
        self._import_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        set_button_icon(self._import_btn, "mdi6.import", color=TONE_MUTED, size=15)
        self._import_btn.clicked.connect(self._on_import_coords)
        bar2.addWidget(self._import_btn)
        bar2.addStretch()
        self._export_btn = QPushButton("导出图")
        self._export_btn.setObjectName("Primary")
        self._export_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        set_button_icon(self._export_btn, "mdi6.tray-arrow-down",
                        color=_theme()("accent_fg", "#ffffff"), size=15)
        self._export_btn.clicked.connect(self._on_export)
        bar2.addWidget(self._export_btn)
        v.addWidget(tools)

        # 地图堆叠
        self._stack = QStackedWidget()
        self._stack.setObjectName("MapStack")
        self._tile_map = TileMapWidget()
        self._tile_map.interactive_marker = False
        self._tile_map.point_clicked.connect(self._on_point_clicked)
        self._tile_map.zoom_changed.connect(
            lambda _z: self._sync_zoom_slider())
        from app.widgets.publication_map_widget import PublicationMapWidget
        self._pub_map = PublicationMapWidget()
        self._pub_map.zoom_changed.connect(
            lambda _z: self._sync_zoom_slider())
        self._stack.addWidget(self._tile_map)
        self._stack.addWidget(self._pub_map)
        self._stack.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._stack.customContextMenuRequested.connect(self._on_map_context_menu)
        v.addWidget(self._stack, 1)

        self._info_card = self._build_info_card()
        self._info_card.hide()
        self._zoom_panel = self._build_zoom_controls()
        self._tile_map.installEventFilter(self)
        self._pub_map.installEventFilter(self)
        self._populate_basemaps()
        return pane

    def _build_info_card(self) -> QWidget:
        card = QFrame(self._tile_map)
        card.setObjectName("InfoCard")
        v = QVBoxLayout(card)
        v.setContentsMargins(12, 10, 12, 10)
        v.setSpacing(4)
        self._info_title = QLabel("")
        self._info_title.setObjectName("InfoTitle")
        self._info_coord = QLabel("")
        self._info_coord.setObjectName("InfoCoord")
        self._info_count = QLabel("")
        self._info_count.setObjectName("InfoCount")
        v.addWidget(self._info_title)
        v.addWidget(self._info_coord)
        v.addWidget(self._info_count)
        row = QHBoxLayout()
        row.addStretch()
        self._btn_goto = QPushButton("查看记录")
        self._btn_goto.setObjectName("Primary")
        self._btn_goto.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_goto.clicked.connect(self._goto_records)
        btn_close = QPushButton("×")
        btn_close.setObjectName("CloseBtn")
        btn_close.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_close.clicked.connect(card.hide)
        row.addWidget(self._btn_goto)
        row.addWidget(btn_close)
        v.addLayout(row)
        card.adjustSize()
        return card

    def _build_zoom_controls(self) -> QWidget:
        """地图右下角浮动缩放控件：+/- 按钮 + 滑杆 + 适应全部。"""
        panel = QFrame(self._tile_map)
        panel.setObjectName("ZoomPanel")
        v = QVBoxLayout(panel)
        v.setContentsMargins(6, 8, 6, 8)
        v.setSpacing(4)

        self._btn_zoom_in = QPushButton()
        self._btn_zoom_in.setObjectName("ZoomBtn")
        self._btn_zoom_in.setFixedSize(30, 30)
        self._btn_zoom_in.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_zoom_in.setToolTip("放大 (+)")
        set_button_icon(self._btn_zoom_in, "mdi6.plus", color=TONE_MUTED, size=16)
        self._btn_zoom_in.clicked.connect(self._on_zoom_in)
        v.addWidget(self._btn_zoom_in, 0, Qt.AlignmentFlag.AlignHCenter)

        self._zoom_slider = QSlider(Qt.Orientation.Vertical)
        self._zoom_slider.setObjectName("ZoomSlider")
        self._zoom_slider.setFixedHeight(120)
        self._zoom_slider.setMinimum(2)
        self._zoom_slider.setMaximum(18)
        self._zoom_slider.setValue(12)
        self._zoom_slider.setInvertedAppearance(True)
        self._zoom_slider.setPageStep(1)
        self._zoom_slider.setToolTip("缩放级别")
        self._zoom_slider.valueChanged.connect(self._on_slider_zoom)
        v.addWidget(self._zoom_slider, 0, Qt.AlignmentFlag.AlignHCenter)

        self._btn_zoom_out = QPushButton()
        self._btn_zoom_out.setObjectName("ZoomBtn")
        self._btn_zoom_out.setFixedSize(30, 30)
        self._btn_zoom_out.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_zoom_out.setToolTip("缩小 (-)")
        set_button_icon(self._btn_zoom_out, "mdi6.minus", color=TONE_MUTED, size=16)
        self._btn_zoom_out.clicked.connect(self._on_zoom_out)
        v.addWidget(self._btn_zoom_out, 0, Qt.AlignmentFlag.AlignHCenter)

        sep = QFrame()
        sep.setObjectName("ZoomSep")
        sep.setFixedHeight(1)
        v.addWidget(sep)

        self._btn_zoom_fit = QPushButton()
        self._btn_zoom_fit.setObjectName("ZoomBtn")
        self._btn_zoom_fit.setFixedSize(30, 30)
        self._btn_zoom_fit.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_zoom_fit.setToolTip("适应全部点位")
        set_button_icon(self._btn_zoom_fit, "mdi6.fit-to-screen-outline", color=TONE_MUTED, size=16)
        self._btn_zoom_fit.clicked.connect(self._on_zoom_fit)
        v.addWidget(self._btn_zoom_fit, 0, Qt.AlignmentFlag.AlignHCenter)

        # 浮动阴影
        shadow = QGraphicsDropShadowEffect(panel)
        shadow.setBlurRadius(18)
        shadow.setOffset(0, 2)
        shadow.setColor(QColor(0, 0, 0, 120))
        panel.setGraphicsEffect(shadow)

        panel.adjustSize()
        return panel

    def _position_zoom_panel(self) -> None:
        """将缩放控件固定在当前活动地图右下角。"""
        p = self._zoom_panel
        host = self._pub_map if self._is_publication_mode() else self._tile_map
        p.move(
            host.width() - p.width() - 10,
            host.height() - p.height() - (10 if self._is_publication_mode() else 28),
        )

    def _on_zoom_in(self) -> None:
        if self._is_publication_mode():
            self._pub_map.zoom_in()
        else:
            self._tile_map.zoom_in()
        self._sync_zoom_slider()

    def _on_zoom_out(self) -> None:
        if self._is_publication_mode():
            self._pub_map.zoom_out()
        else:
            self._tile_map.zoom_out()
        self._sync_zoom_slider()

    def _on_zoom_fit(self) -> None:
        if self._is_publication_mode():
            self._pub_map.zoom_to_fit()
        else:
            self._tile_map.zoom_to_fit()
        self._sync_zoom_slider()

    def _on_slider_zoom(self, value: int) -> None:
        """滑杆驱动的缩放。"""
        if self._is_publication_mode():
            self._pub_map.set_zoom_factor(value / 10.0)
        else:
            self._tile_map.set_zoom(value)

    def _sync_zoom_slider(self) -> None:
        """从当前地图同步滑杆位置（避免信号反馈环）。"""
        self._zoom_slider.blockSignals(True)
        if self._is_publication_mode():
            self._zoom_slider.setValue(round(self._pub_map.current_zoom() * 10))
        else:
            self._zoom_slider.setValue(self._tile_map.current_zoom())
        self._zoom_slider.blockSignals(False)

    def _apply_style(self) -> None:
        g = _theme()
        bg, panel, border = g("bg", "#0a1e24"), g("panel_2", "#0e2329"), g("border", "#21424a")
        text, muted, accent = g("text", "#c8dcd6"), g("muted", "#7fa49b"), g("accent", "#4fd1b8")
        accent_fg = g("accent_fg", "#ffffff")
        surface = g("modal_surface", panel)
        accent_soft = g("accent_soft", "rgba(79,209,184,0.14)")
        accent_softer = g("accent_softer", accent_soft)
        inset = g("panel_inset", panel)
        hover = g("nav_segment_hover_bg", accent_soft)
        input_bg = g("input_bg", panel)
        input_border = g("input_border", border)
        scroll_handle = g("scrollbar_handle", muted)
        scroll_hover = g("scrollbar_handle_hover", accent)
        _ff = local_font_css()
        self.setStyleSheet(
            f"#{self.view_id}{{background:{bg};{_ff}}}"
            f"QScrollArea#MapPageScroll,QScrollArea#LeftScroll,QScrollArea#StyleScroll{{background:transparent;border:none;}}"
            f"QScrollArea#StyleScroll>QWidget>QWidget{{background:transparent;}}"
            f"QWidget#MapPageContent,QWidget#RightPane{{background:transparent;}}"
            f"QWidget#MarkerStylePanel{{background:transparent;}}"
            f"QWidget#LeftPane{{background:transparent;}}"
            f"QFrame#Card{{background:{panel};border:1px solid {border};border-radius:8px;}}"
            f"QFrame#CardDiv{{background:{border};border:none;}}"
            f"QLabel{{color:{text};background:transparent;}}"
            f"QLabel#CardTitle{{color:{text};font-weight:600;font-size:14px;}}"
            f"QLabel#StyleSection{{color:{muted};font-weight:600;font-size:11px;"
            f"letter-spacing:1px;margin-top:2px;}}"
            f"QPushButton#CollapseBtn{{background:transparent;border:none;border-radius:9px;}}"
            f"QPushButton#CollapseBtn:hover{{background:{hover};}}"
            f"QFrame#MapHeader{{background:transparent;border:none;}}"
            f"QFrame#MapToolStrip{{background:{accent_softer};border:1px solid {border};border-radius:8px;}}"
            f"QLabel#ToolLabel{{color:{muted};font-size:12px;font-weight:600;}}"
            f"QLabel#PaneTitle{{color:{text};font-weight:600;font-size:15px;}}"
            f"QLabel#SectionTitle{{color:{muted};font-weight:600;font-size:12px;}}"
            f"QLabel#CountLbl{{color:{muted};font-size:12px;}}"
            f"QLabel#MarkerPreview{{background:{inset};border:1px solid {border};border-radius:6px;}}"
            # 新建项目「+」圆形 ghost 按钮
            f"QPushButton#AddProjBtn{{background:{accent_soft};border:none;border-radius:13px;}}"
            f"QPushButton#AddProjBtn:hover{{background:{hover};}}"
            # 项目列表：行=自定义 QFrame#ProjRow，选中态全走 [sel="1"] 动态属性，
            #   由 _restyle_proj_selection 设置 + repolish（setItemWidget 子控件不吃 ::item:selected）。
            #   左侧 ProjBar 指示条 + accent_soft 行底 + 首字母圆标反色。
            f"QListWidget#ProjList{{background:transparent;border:none;}}"
            f"QListWidget#ProjList::item{{border:none;margin:2px 0;padding:0;}}"
            f"QListWidget#ProjList::item:selected{{background:transparent;}}"
            f"QFrame#ProjRow{{background:transparent;border-radius:9px;}}"
            f"QFrame#ProjRow:hover{{background:{hover};}}"
            f'QFrame#ProjRow[sel="1"]{{background:{accent_soft};}}'
            f"QFrame#ProjBar{{background:transparent;border-radius:1px;}}"
            f'QFrame#ProjBar[sel="1"]{{background:{accent};}}'
            f"QLabel#ProjAvatar{{background:{accent_soft};color:{accent};border-radius:14px;"
            f"font-size:13px;font-weight:700;}}"
            f'QLabel#ProjAvatar[sel="1"]{{background:{accent};color:{accent_fg};}}'
            f"QLabel#ProjName{{color:{text};font-size:13px;font-weight:600;background:transparent;}}"
            f'QLabel#ProjName[sel="1"]{{color:{accent};}}'
            f"QLabel#ProjSub{{color:{muted};font-size:11px;background:transparent;}}"
            f'QLabel#ProjSub[sel="1"]{{color:{accent};}}'
            f"QComboBox,QSpinBox,QDoubleSpinBox{{background:{input_bg};color:{text};"
            f"border:1px solid {input_border};border-radius:6px;padding:4px 8px;}}"
            f"QComboBox:hover,QSpinBox:hover,QDoubleSpinBox:hover{{border-color:{border};background:{surface};}}"
            f"QComboBox:focus,QSpinBox:focus,QDoubleSpinBox:focus{{border-color:{accent};}}"
            f"QCheckBox{{color:{text};spacing:8px;}}"
            # 粒度分段控件（站位/断面/地区）
            f"QFrame#SegGroup{{background:{inset};border:1px solid {border};border-radius:9px;}}"
            f"QPushButton#LevelBtn{{background:transparent;color:{text};border:none;"
            f"border-radius:6px;padding:5px 14px;font-size:13px;}}"
            f"QPushButton#LevelBtn:hover{{background:{hover};}}"
            f"QPushButton#LevelBtn:checked{{background:{accent};color:{accent_fg};font-weight:600;}}"
            f"QPushButton{{background:{panel};color:{text};border:1px solid {border};"
            f"border-radius:5px;padding:5px 12px;font-size:13px;}}"
            f"QPushButton:hover{{background:{hover};}}"
            f"QPushButton#Primary{{background:{accent};color:{accent_fg};border:1px solid {accent};}}"
            f"QPushButton#Primary:hover{{background:{g('accent_hover', accent)};}}"
            f"QStackedWidget#MapStack{{background:{surface};border:1px solid {border};border-radius:8px;}}"
            f"QFrame#InfoCard{{background:{surface};border:1px solid {border};border-radius:8px;}}"
            f"QLabel#InfoTitle{{color:{text};font-weight:600;font-size:14px;}}"
            f"QPushButton#CloseBtn{{background:transparent;color:{muted};border:none;font-size:16px;}}"
            # 缩放控件浮层 — 毛玻璃风格
            f"QFrame#ZoomPanel{{background:rgba(12,30,36,0.82);border:1px solid rgba(255,255,255,0.08);border-radius:12px;}}"
            f"QPushButton#ZoomBtn{{background:rgba(255,255,255,0.04);color:{muted};border:none;border-radius:8px;}}"
            f"QPushButton#ZoomBtn:hover{{background:rgba(255,255,255,0.12);color:{text};}}"
            f"QPushButton#ZoomBtn:pressed{{background:rgba(255,255,255,0.18);}}"
            f"QSlider#ZoomSlider::groove:vertical{{width:4px;background:rgba(255,255,255,0.10);border-radius:2px;margin:2px 11px;}}"
            f"QSlider#ZoomSlider::sub-page:vertical{{background:rgba(79,209,184,0.5);border-radius:2px;width:4px;}}"
            f"QSlider#ZoomSlider::add-page:vertical{{background:transparent;}}"
            f"QSlider#ZoomSlider::handle:vertical{{height:16px;width:16px;margin:-6px 0;border-radius:8px;background:{accent};border:2px solid rgba(255,255,255,0.25);}}"
            f"QSlider#ZoomSlider::handle:vertical:hover{{background:{g('accent_hover', accent)};border:2px solid rgba(255,255,255,0.4);}}"
            f"QSlider#ZoomSlider::handle:vertical:pressed{{background:{g('accent_pressed', accent)};}}"
            f"QFrame#ZoomSep{{background:rgba(255,255,255,0.08);max-width:20px;}}"
            f"QScrollBar:vertical{{background:transparent;width:10px;margin:3px 1px 3px 1px;}}"
            f"QScrollBar::handle:vertical{{background:{scroll_handle};border-radius:4px;min-height:34px;}}"
            f"QScrollBar::handle:vertical:hover{{background:{scroll_hover};}}"
            f"QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical{{height:0;border:none;background:transparent;}}"
            f"QScrollBar::add-page:vertical,QScrollBar::sub-page:vertical{{background:transparent;}}"
            f"QScrollBar:horizontal{{background:transparent;height:12px;margin:1px 8px 2px 8px;}}"
            f"QScrollBar::handle:horizontal{{background:{scroll_handle};border-radius:5px;min-width:48px;}}"
            f"QScrollBar::handle:horizontal:hover{{background:{scroll_hover};}}"
            f"QScrollBar::add-line:horizontal,QScrollBar::sub-line:horizontal{{width:0;border:none;background:transparent;}}"
            f"QScrollBar::add-page:horizontal,QScrollBar::sub-page:horizontal{{background:transparent;}}"
        )

    # ── BaseView ────────────────────────────────────────────────────────────

    def on_activate(self) -> None:
        self._apply_style()
        self._populate_projects()
        self._load_marker_style()
        self._reload()
        self._sync_zoom_slider()
        self._position_zoom_panel()
        self._zoom_panel.raise_()

    def eventFilter(self, obj, event) -> bool:
        """监听地图 resize，重新定位缩放浮层。"""
        if obj in (self._tile_map, self._pub_map) and event.type() == event.Type.Resize:
            self._position_zoom_panel()
        return super().eventFilter(obj, event)

    # ── 项目列表（跨项目）──────────────────────────────────────────────────────

    def _known_projects(self) -> list[tuple[str, str]]:
        """返回 [(name, directory)]：注册表 + 当前项目，去重。"""
        out: list[tuple[str, str]] = []
        seen: set[str] = set()
        try:
            for p in list_projects(str(_user_projects_json())):
                d = p.get("directory")
                if d and d not in seen:
                    seen.add(d)
                    out.append((p.get("name") or Path(d).name, d))
        except Exception:
            pass
        cur = getattr(self.ctx, "current_project_dir", None)
        if cur and cur not in seen:
            out.append((Path(cur).name, cur))
        return out

    def _station_count(self, directory) -> Optional[int]:
        """该项目（或全部）的站位数；用于项目行徽章。出错则 None。"""
        try:
            if directory:
                db = self.ctx.get_db(directory)
                return len(crs.map_points(db, "station")) if db is not None else None
            dbs = [self.ctx.get_db(d) for _n, d in self._known_projects()]
            dbs = [d for d in dbs if d is not None]
            return len(crs.map_points_across(dbs, "station")) if dbs else 0
        except Exception:
            return None

    def _populate_projects(self) -> None:
        self._proj_list.blockSignals(True)
        self._proj_list.clear()
        entries = [("全部项目", None)] + self._known_projects()
        target_row = 0
        for i, (name, d) in enumerate(entries):
            item, w = self._make_project_item(name, self._station_count(d), d)
            self._proj_list.addItem(item)
            self._proj_list.setItemWidget(item, w)
            if d == self._project_filter:
                target_row = i
        self._proj_list.setCurrentRow(target_row)
        self._proj_list.blockSignals(False)
        self._restyle_proj_selection()

    def _on_project_changed(self) -> None:
        items = self._proj_list.selectedItems()
        if not items:
            return
        self._project_filter = items[0].data(Qt.ItemDataRole.UserRole)
        self._info_card.hide()
        self._restyle_proj_selection()
        self._reload()

    def _on_add_menu(self) -> None:
        """卡片「+」→ 弹菜单：新建项目 / 打开已有项目（以前用过的工作区）。"""
        menu = QMenu(self)
        menu.addAction(icon("mdi6.folder-plus-outline"), "新建项目…").triggered.connect(
            self._on_add_project)
        menu.addAction(icon("mdi6.folder-open-outline"), "打开已有项目…").triggered.connect(
            self._on_open_project)
        menu.exec(self._add_proj_btn.mapToGlobal(self._add_proj_btn.rect().bottomLeft()))

    def _on_add_project(self) -> None:
        """新建项目；建完刷新列表并选中，停留在采集地图（不跳工作区）。"""
        from app.views.project_dialog import ProjectDialog
        from app.views.overview_view import _load_projects

        existing = _load_projects()
        dlg = ProjectDialog(mode="new", existing_projects=existing, parent=self, light=True)
        if dlg.exec() != ProjectDialog.DialogCode.Accepted:
            return
        self._register_and_select(dlg.result_project(), "新建项目失败")

    def _on_open_project(self) -> None:
        """打开磁盘上已有工作区（以前用过、未在列表里的项目目录）。"""
        from app.views.project_dialog import ProjectDialog

        dlg = ProjectDialog(mode="open", parent=self)
        if dlg.exec() != ProjectDialog.DialogCode.Accepted:
            return
        self._register_and_select(dlg.result_project(), "打开项目失败")

    def _register_and_select(self, proj, err_title: str) -> None:
        """把项目写入注册表（去重）、选中为过滤器并激活当前项目、刷新地图。"""
        if not proj:
            return
        from app.views.overview_view import _load_projects, _save_projects
        try:
            all_projects = _load_projects()
            existing_dirs = {p.get("directory") or p.get("dir") for p in all_projects}
            d = proj.get("directory")
            if d not in existing_dirs:
                all_projects.append(proj)
                _save_projects(all_projects)
            self._project_filter = d
            self.ctx.current_project_dir = d
            self._populate_projects()
            self._reload()
        except Exception as exc:
            from app.utils.ui import warn
            warn(self, err_title, str(exc))

    def _pick_target_project(self) -> Optional[str]:
        """全部项目模式下选导入目标：已知项目 + 「新建项目…」。返回 directory 或 None。"""
        from PyQt6.QtWidgets import QInputDialog

        projects = self._known_projects()           # [(name, directory)]
        _NEW = "➕ 新建项目…"
        names = [n for n, _d in projects] + [_NEW]
        choice, ok = QInputDialog.getItem(
            self, "导入到哪个项目", "目标项目", names, 0, False
        )
        if not ok:
            return None
        if choice == _NEW:
            self._on_add_project()                  # 建完已设 _project_filter
            return self._project_filter
        for name, d in projects:
            if name == choice:
                return d
        return None

    def _on_map_context_menu(self, pos) -> None:
        """地图区右键菜单 → 导入经纬度。"""
        menu = QMenu(self)
        act = menu.addAction(icon("mdi6.import"), "导入经纬度…")
        act.triggered.connect(self._on_import_coords)
        menu.exec(self._stack.mapToGlobal(pos))

    # ── 项目列表右键菜单 ───────────────────────────────────────────────────────

    def _proj_name_for(self, directory) -> str:
        for name, d in self._known_projects():
            if d == directory:
                return name
        return Path(directory).name

    def _on_proj_context_menu(self, pos) -> None:
        """项目列表右键菜单。"""
        item = self._proj_list.itemAt(pos)
        if item is None:
            return
        directory = item.data(Qt.ItemDataRole.UserRole)
        is_all = directory is None

        menu = QMenu(self)

        # ── 通用：导入经纬度
        act_import = menu.addAction(icon("mdi6.import"), "导入经纬度…")

        if not is_all:
            menu.addSeparator()
            act_workspace = menu.addAction(icon("mdi6.folder-star-outline"), "打开工作区")
            act_open_dir = menu.addAction(icon("mdi6.folder-open-outline"), "在文件管理器中打开")
            act_copy_path = menu.addAction(icon("mdi6.content-copy"), "复制项目路径")
            menu.addSeparator()
            act_rename = menu.addAction(icon("mdi6.rename-box-outline"), "重命名…")
            act_remove = menu.addAction(icon("mdi6.close-circle-outline"), "从列表移除")

        chosen = menu.exec(self._proj_list.viewport().mapToGlobal(pos))
        if chosen is None:
            return

        if chosen == act_import:
            if is_all:
                self._project_filter = None
            else:
                self._project_filter = directory
            self._on_import_coords()
        elif not is_all and chosen == act_workspace:
            self._do_open_workspace(directory)
        elif not is_all and chosen == act_open_dir:
            self._do_open_in_file_manager(directory)
        elif not is_all and chosen == act_copy_path:
            self._do_copy_path(directory)
        elif not is_all and chosen == act_rename:
            self._do_rename(directory)
        elif not is_all and chosen == act_remove:
            self._do_remove(directory)

    def _do_open_workspace(self, directory) -> None:
        """切换到该项目的工作区（跳转工作台）。"""
        self.ctx.current_project_dir = directory
        self._project_filter = directory
        self._populate_projects()
        win = self.window()
        nav = getattr(win, "navigate_to", None)
        if callable(nav):
            nav("workbench")

    def _do_open_in_file_manager(self, directory) -> None:
        """在系统文件管理器中打开项目目录。"""
        from PyQt6.QtGui import QDesktopServices
        from PyQt6.QtCore import QUrl
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(directory)))

    def _do_copy_path(self, directory) -> None:
        """复制项目路径到剪贴板。"""
        from PyQt6.QtWidgets import QApplication
        QApplication.clipboard().setText(str(directory))
        from app.utils.ui import info
        info(self, "已复制", f"项目路径已复制到剪贴板：\n{directory}")

    def _do_rename(self, directory) -> None:
        """重命名项目显示名称（仅改注册表，不改文件夹名）。"""
        from PyQt6.QtWidgets import QInputDialog
        old_name = self._proj_name_for(directory)
        new_name, ok = QInputDialog.getText(self, "重命名项目", "新名称", text=old_name)
        if not ok or not new_name.strip() or new_name.strip() == old_name:
            return
        from app.views.overview_view import _load_projects, _save_projects
        try:
            all_projects = _load_projects()
            for p in all_projects:
                if (p.get("directory") or p.get("dir")) == directory:
                    p["name"] = new_name.strip()
                    break
            _save_projects(all_projects)
            self._populate_projects()
        except Exception as exc:
            ui.warn(self, "重命名失败", str(exc))

    def _do_remove(self, directory) -> None:
        """从项目列表中移除（不删除磁盘文件）。"""
        from PyQt6.QtWidgets import QMessageBox
        name = self._proj_name_for(directory)
        if ui.question(self, "移除项目",
                       f"确定将「{name}」从列表中移除？\n（不会删除项目文件）") \
                != QMessageBox.StandardButton.Yes:
            return
        from app.views.overview_view import _load_projects, _save_projects
        try:
            all_projects = _load_projects()
            all_projects = [p for p in all_projects
                            if (p.get("directory") or p.get("dir")) != directory]
            _save_projects(all_projects)
            if self._project_filter == directory:
                self._project_filter = None
            self._populate_projects()
            self._reload()
        except Exception as exc:
            ui.warn(self, "移除失败", str(exc))

    def _on_import_coords(self) -> None:
        """右栏「导入经纬度」→ 选目标项目 → 复用 CoordImportDialog → 导入后刷新地图。

        兼容两场景：①录入某项目/采集地的经纬度；②建规划项目导入站位看分布。
        列自动识别 + 任意坐标格式→WGS84 全在 CoordImportDialog/coord_import_service。
        """
        target_dir = self._project_filter or self._pick_target_project()
        if not target_dir:
            return
        db = self.ctx.get_db(target_dir)
        if db is None:
            ui.warn(self, "无法导入", "目标项目数据库不可用。")
            return
        from app.widgets.coord_import_dialog import CoordImportDialog

        dlg = CoordImportDialog(db, parent=self)
        if dlg.exec():
            self._project_filter = target_dir
            self._populate_projects()
            self._reload()

    def _dbs_for_filter(self) -> list:
        """当前项目过滤对应的 DB 连接列表。全部 = 所有已知项目。"""
        if self._project_filter:
            db = self.ctx.get_db(self._project_filter)
            return [db] if db is not None else []
        dbs = []
        for _name, d in self._known_projects():
            db = self.ctx.get_db(d)
            if db is not None:
                dbs.append(db)
        if not dbs:                       # 无注册项目 → 退回当前项目
            db = self.ctx.get_db()
            if db is not None:
                dbs = [db]
        return dbs

    # ── 数据 ────────────────────────────────────────────────────────────────

    def _set_level(self, level: str) -> None:
        if level not in {l for l, _ in _LEVELS}:
            return
        self._level = level
        btn = self._level_btns.get(level)
        if btn is not None and not btn.isChecked():
            btn.setChecked(True)
        self._info_card.hide()
        self._reload()

    def _reload(self) -> None:
        dbs = self._dbs_for_filter()
        if self._project_filter and dbs:
            self._points = crs.map_points(dbs[0], self._level)
        elif dbs:
            self._points = crs.map_points_across(dbs, self._level)
        else:
            self._points = []
        self._tile_map.set_points(self._points)
        if self._is_publication_mode():
            self._pub_map.set_points(self._points)
            self._pub_map.render()
        label = dict(_LEVELS)[self._level]
        scope = "全部项目" if not self._project_filter else "本项目"
        self._count_lbl.setText(f"{scope} · {len(self._points)} 个{label}点")

    # ── 标识样式 ──────────────────────────────────────────────────────────────

    def _load_marker_style(self) -> None:
        db = self.ctx.get_db()
        if db is not None:
            self._style = pss.load_setting(db, _STYLE_KEY, {})
        self._style_panel.set_style(self._style)
        self._apply_marker_style(self._style)

    def _on_style_changed(self, style: dict) -> None:
        self._style = style
        db = self.ctx.get_db()
        if db is not None:
            pss.save_setting(db, _STYLE_KEY, style)
        self._apply_marker_style(style)

    def _apply_marker_style(self, style: dict) -> None:
        self._tile_map.set_point_style(style)
        self._pub_map.set_style(style)
        self._update_marker_preview(style)
        if self._is_publication_mode():
            self._pub_map.render()

    def _update_marker_preview(self, style: dict) -> None:
        """把当前样式画成一个小预览记号。"""
        from PyQt6.QtGui import QColor, QPainter, QPen, QPixmap
        prev = getattr(self, "_marker_preview", None)
        if prev is None:
            return
        w, h = prev.width(), prev.height()
        pm = QPixmap(w, h)
        pm.fill(QColor(0, 0, 0, 0))
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        try:
            fill = QColor(style.get("fill", "#29b9ab"))
            edge = QColor(style.get("edge", "#ffffff"))
        except Exception:
            fill, edge = QColor("#29b9ab"), QColor("#ffffff")
        r = max(6, min(12, int(8 * (style.get("size", 80) / 80.0) ** 0.5)))
        p.setBrush(fill)
        p.setPen(QPen(edge, 1.5))
        p.drawEllipse(w // 2 - r, h // 2 - r, 2 * r, 2 * r)
        p.end()
        prev.setPixmap(pm)

    # ── 底图切换 ───────────────────────────────────────────────────────────────

    def _user_map_dir(self):
        d = getattr(getattr(self.ctx, "settings", None), "map_dir", None)
        return Path(d) if isinstance(d, (str, Path)) else None

    def _populate_basemaps(self) -> None:
        self._basemap_combo.blockSignals(True)
        self._basemap_combo.clear()
        for entry in br.list_basemaps(user_dir=self._user_map_dir()):
            self._basemap_combo.addItem(entry["name"], entry)
        self._basemap_combo.blockSignals(False)
        if self._basemap_combo.count():
            self._activate_basemap(self._basemap_combo.itemData(0))

    def _on_basemap_changed(self, index: int) -> None:
        entry = self._basemap_combo.itemData(index)
        if entry is not None:
            self._activate_basemap(entry)

    def _is_publication_mode(self) -> bool:
        return bool(self._active_basemap) and self._active_basemap.get("kind") != "osm"

    def _activate_basemap(self, entry: dict) -> None:
        self._active_basemap = entry
        kind = entry.get("kind")
        if kind == "osm":
            self._stack.setCurrentWidget(self._tile_map)
            self._calibrate_btn.setEnabled(False)
            self._zoom_panel.setParent(self._tile_map)
            self._zoom_panel.show()
            self._zoom_panel.raise_()
            self._position_zoom_panel()
            self._zoom_slider.blockSignals(True)
            self._zoom_slider.setRange(2, 18)
            self._zoom_slider.setValue(self._tile_map.current_zoom())
            self._zoom_slider.blockSignals(False)
        else:
            calib = None
            calibrated = False
            if kind == "image":
                calib = br.load_calibration(Path(entry.get("source", "")))
                calibrated = calib is not None
            self._pub_map.set_basemap(entry, calibration=calib)
            self._pub_map.set_style(self._style)
            self._stack.setCurrentWidget(self._pub_map)
            self._info_card.hide()
            # generated 底图免校准；image 未校准才需校准
            self._calibrate_btn.setEnabled(kind == "image" and not calibrated)
            self._zoom_panel.setParent(self._pub_map)
            self._zoom_panel.show()
            self._zoom_panel.raise_()
            self._position_zoom_panel()
            self._zoom_slider.blockSignals(True)
            self._zoom_slider.setRange(5, 200)
            self._zoom_slider.setValue(10)
            self._zoom_slider.blockSignals(False)
        self._reload()

    def _on_calibrate(self) -> None:
        entry = self._active_basemap or {}
        if entry.get("kind") != "image":
            return
        from app.widgets.calibration_dialog import CalibrationDialog
        dlg = CalibrationDialog(entry["source"], parent=self)
        if dlg.exec():
            calib = br.load_calibration(Path(entry["source"]))
            self._pub_map.set_basemap(entry, calibration=calib)
            self._calibrate_btn.setEnabled(calib is None)
            self._reload()

    # ── 导出 ───────────────────────────────────────────────────────────────────

    def _on_export(self) -> None:
        name = (self._active_basemap or {}).get("name", "map")
        path = ui.get_save_file_name(
            self, "导出采集地图", f"{name}.pdf",
            "PDF (*.pdf);;PNG (*.png);;SVG (*.svg);;EPS (*.eps)",
        )
        if path:
            self._do_export(path)

    def _do_export(self, path: str) -> None:
        if self._is_publication_mode():
            self._pub_map.set_points(self._points)
            self._pub_map.export(path)
        else:
            out = path if path.lower().endswith(".png") else path + ".png"
            self._tile_map.grab().save(out, "PNG")

    # ── 交互 ───────────────────────────────────────────────────────────────────

    def _on_point_clicked(self, idx: int) -> None:
        if idx < 0 or idx >= len(self._points):
            return
        p = self._points[idx]
        self.ctx.pending_record_filter = {
            "province": p.get("province"), "site": p.get("site"), "station": p.get("station"),
        }
        self._info_title.setText(str(p.get("label") or "—"))
        lon, lat = p.get("lon"), p.get("lat")
        self._info_coord.setText(
            f"经度 {lon:.5f}  纬度 {lat:.5f}" if lon is not None and lat is not None else ""
        )
        self._info_count.setText(f"采集记录 {p.get('count', 0)} 条")
        self._info_card.adjustSize()
        self._info_card.move(self._tile_map.width() - self._info_card.width() - 14, 14)
        self._info_card.show()
        self._info_card.raise_()

    def _goto_records(self) -> None:
        win = self.window()
        nav = getattr(win, "navigate_to", None)
        if callable(nav):
            nav("collection_records")
