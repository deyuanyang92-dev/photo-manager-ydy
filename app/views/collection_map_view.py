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
from PyQt6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

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
        root = QHBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 12)
        root.setSpacing(12)
        root.addWidget(self._build_left_pane(), 0)
        root.addWidget(self._build_right_pane(), 1)

    def _build_left_pane(self) -> QWidget:
        pane = QWidget()
        pane.setObjectName("LeftPane")
        pane.setFixedWidth(248)
        v = QVBoxLayout(pane)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(12)
        v.addWidget(self._build_project_card())
        v.addWidget(self._build_style_card())
        v.addStretch(1)
        return pane

    def _card(self, title: str) -> tuple[QFrame, QVBoxLayout]:
        """统一卡片外壳：圆角 + 软阴影 + 标题。返回 (卡片, 内容布局)。"""
        from app.config.effects import apply_card_shadow
        card = QFrame()
        card.setObjectName("Card")
        outer = QVBoxLayout(card)
        outer.setContentsMargins(14, 12, 14, 12)
        outer.setSpacing(8)
        head = QLabel(title)
        head.setObjectName("CardTitle")
        outer.addWidget(head)
        apply_card_shadow(card, blur=16, y=3, alpha=28)
        return card, outer

    def _build_project_card(self) -> QFrame:
        card, lay = self._card("项目")
        # 标题行右侧「+」新建项目入口（CardTitle 已由 _card 加在首行；这里追加按钮行）
        head_row = QHBoxLayout()
        head_row.addStretch(1)
        self._add_proj_btn = QPushButton("+")
        self._add_proj_btn.setObjectName("AddProjBtn")
        self._add_proj_btn.setFixedSize(24, 24)
        self._add_proj_btn.setToolTip("新建项目")
        self._add_proj_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._add_proj_btn.clicked.connect(self._on_add_project)
        head_row.addWidget(self._add_proj_btn)
        lay.addLayout(head_row)
        self._proj_list = QListWidget()
        self._proj_list.setObjectName("ProjList")
        self._proj_list.setFrameShape(QFrame.Shape.NoFrame)
        self._proj_list.setSpacing(2)
        self._proj_list.setMaximumHeight(220)
        self._proj_list.itemSelectionChanged.connect(self._on_project_changed)
        lay.addWidget(self._proj_list)
        return card

    def _build_style_card(self) -> QFrame:
        card, lay = self._card("站位标识")
        # 实时预览色块
        prev_row = QHBoxLayout()
        prev_row.addWidget(QLabel("预览"))
        self._marker_preview = QLabel()
        self._marker_preview.setFixedSize(54, 30)
        prev_row.addWidget(self._marker_preview)
        prev_row.addStretch()
        lay.addLayout(prev_row)
        self._style_panel = MarkerStylePanel()
        self._style_panel.style_changed.connect(self._on_style_changed)
        lay.addWidget(self._style_panel)
        return card

    # ── 项目行（名称 + 站位数徽章）──────────────────────────────────────────────

    def _make_project_item(self, name: str, count: Optional[int], directory):
        item = QListWidgetItem()
        item.setData(Qt.ItemDataRole.UserRole, directory)
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(10, 6, 8, 6)
        nm = QLabel(name)
        nm.setObjectName("ProjName")
        h.addWidget(nm, 1)
        w._sel_labels = [nm]   # 选中态需手动 repolish 的子控件
        if count is not None:
            badge = QLabel(str(count))
            badge.setObjectName("ProjBadge")
            badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
            h.addWidget(badge)
            w._sel_labels.append(badge)
        item.setSizeHint(w.sizeHint())
        return item, w

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
        v = QVBoxLayout(pane)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(8)

        # 工具条 1：粒度 + 计数
        bar = QHBoxLayout()
        bar.setSpacing(8)
        title = QLabel("采集地图")
        title.setObjectName("PaneTitle")
        bar.addWidget(title)
        bar.addSpacing(10)
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
            bar.addWidget(btn)
        bar.addStretch()
        self._count_lbl = QLabel("")
        self._count_lbl.setObjectName("CountLbl")
        bar.addWidget(self._count_lbl)
        v.addLayout(bar)

        # 工具条 2：底图 + 校准 + 导出
        bar2 = QHBoxLayout()
        bar2.setSpacing(8)
        bar2.addWidget(QLabel("底图"))
        self._basemap_combo = QComboBox()
        self._basemap_combo.setMinimumWidth(200)
        self._basemap_combo.currentIndexChanged.connect(self._on_basemap_changed)
        bar2.addWidget(self._basemap_combo)
        self._calibrate_btn = QPushButton("校准")
        self._calibrate_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._calibrate_btn.clicked.connect(self._on_calibrate)
        self._calibrate_btn.setEnabled(False)
        bar2.addWidget(self._calibrate_btn)
        self._import_btn = QPushButton("导入经纬度")
        self._import_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._import_btn.clicked.connect(self._on_import_coords)
        bar2.addWidget(self._import_btn)
        bar2.addStretch()
        self._export_btn = QPushButton("导出图")
        self._export_btn.setObjectName("Primary")
        self._export_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._export_btn.clicked.connect(self._on_export)
        bar2.addWidget(self._export_btn)
        v.addLayout(bar2)

        # 地图堆叠
        self._stack = QStackedWidget()
        self._tile_map = TileMapWidget()
        self._tile_map.interactive_marker = False
        self._tile_map.point_clicked.connect(self._on_point_clicked)
        from app.widgets.publication_map_widget import PublicationMapWidget
        self._pub_map = PublicationMapWidget()
        self._stack.addWidget(self._tile_map)
        self._stack.addWidget(self._pub_map)
        v.addWidget(self._stack, 1)

        self._info_card = self._build_info_card()
        self._info_card.hide()
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

    def _apply_style(self) -> None:
        g = _theme()
        bg, panel, border = g("bg", "#0a1e24"), g("panel_2", "#0e2329"), g("border", "#21424a")
        text, muted, accent = g("text", "#c8dcd6"), g("muted", "#7fa49b"), g("accent", "#4fd1b8")
        accent_fg = g("accent_fg", "#ffffff")
        surface = g("modal_surface", panel)
        accent_soft = g("accent_soft", "rgba(79,209,184,0.14)")
        self.setStyleSheet(
            f"#{self.view_id}{{background:{bg};}}"
            f"QWidget#LeftPane{{background:transparent;}}"
            f"QFrame#Card{{background:{panel};border:1px solid {border};border-radius:10px;}}"
            f"QLabel{{color:{text};background:transparent;}}"
            f"QLabel#CardTitle{{color:{text};font-weight:600;font-size:14px;}}"
            f"QLabel#PaneTitle{{color:{text};font-weight:600;font-size:15px;}}"
            f"QLabel#SectionTitle{{color:{muted};font-weight:600;font-size:12px;}}"
            f"QLabel#CountLbl{{color:{muted};font-size:12px;}}"
            # 项目列表：行=自定义 widget。选中底色沿用全局 theme.qss 的浅 teal
            #   (QListWidget::item:selected)，不与之抢；选中文字走动态属性 [sel="1"]
            #   改为 accent 深色加粗（浅底上可读），由 _restyle_proj_selection 设置 + repolish。
            #   setItemWidget 的子控件不吃 ::item:selected 伪态，故必须用属性。
            f"QListWidget#ProjList{{background:transparent;border:none;}}"
            f"QListWidget#ProjList::item{{border-radius:7px;margin:1px 0;}}"
            f"QLabel#ProjName{{color:{text};font-size:13px;background:transparent;}}"
            f'QLabel#ProjName[sel="1"]{{color:{accent};font-weight:700;}}'
            f"QLabel#ProjBadge{{color:{accent};background:{accent_soft};border-radius:9px;"
            f"min-width:18px;padding:1px 7px;font-size:11px;font-weight:600;}}"
            f'QLabel#ProjBadge[sel="1"]{{color:#ffffff;background:{accent};}}'
            f"QComboBox{{background:{panel};color:{text};border:1px solid {border};"
            f"border-radius:5px;padding:4px 8px;}}"
            f"QPushButton#LevelBtn{{background:{panel};color:{text};border:1px solid {border};"
            f"border-radius:5px;padding:5px 14px;font-size:13px;}}"
            f"QPushButton#LevelBtn:hover{{background:{border};}}"
            f"QPushButton#LevelBtn:checked{{background:{accent};color:{accent_fg};"
            f"border:1px solid {accent};font-weight:600;}}"
            f"QPushButton{{background:{panel};color:{text};border:1px solid {border};"
            f"border-radius:5px;padding:5px 12px;font-size:13px;}}"
            f"QPushButton#Primary{{background:{accent};color:{accent_fg};border:1px solid {accent};}}"
            f"QFrame#InfoCard{{background:{surface};border:1px solid {border};border-radius:8px;}}"
            f"QLabel#InfoTitle{{color:{text};font-weight:600;font-size:14px;}}"
            f"QPushButton#CloseBtn{{background:transparent;color:{muted};border:none;font-size:16px;}}"
        )

    # ── BaseView ────────────────────────────────────────────────────────────

    def on_activate(self) -> None:
        self._apply_style()
        self._populate_projects()
        self._load_marker_style()
        self._reload()

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

    def _on_add_project(self) -> None:
        """卡片内「+」→ 新建项目；建完刷新列表并选中，停留在采集地图（不跳工作区）。"""
        from app.views.project_dialog import ProjectDialog
        from app.views.overview_view import _load_projects, _save_projects

        existing = _load_projects()
        dlg = ProjectDialog(mode="new", existing_projects=existing, parent=self, light=True)
        if dlg.exec() != ProjectDialog.DialogCode.Accepted:
            return
        proj = dlg.result_project()
        if not proj:
            return
        try:
            all_projects = _load_projects()
            existing_dirs = {p.get("directory") or p.get("dir") for p in all_projects}
            d = proj.get("directory")
            if d not in existing_dirs:
                all_projects.append(proj)
                _save_projects(all_projects)
            # 选中新项目作为过滤器并激活为当前项目，停留在地图
            self._project_filter = d
            self.ctx.current_project_dir = d
            self._populate_projects()   # 重读注册表、重建列表、按 _project_filter 选中
            self._reload()              # 按选中项目刷新地图
        except Exception as exc:
            from app.utils.ui import warn
            warn(self, "新建项目失败", str(exc))

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
