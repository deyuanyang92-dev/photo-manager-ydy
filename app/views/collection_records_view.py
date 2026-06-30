"""collection_records_view.py — 采集记录：野外采集记录簿（站位登记 + 自动填充源）.

提前为项目录入每个采样站位/采集事件的完整元数据（经纬度 / 底质 / 潮水 / 采集人 /
采集·拍摄时间 / 拍摄地点 …），后续在工作台拍照时按 (地区+样地+站位+采集时间) 四键
自动填充。本页是这些记录的 CRUD 入口；自动填充消费方在 workbench_view。

**按采区分两套国标表样**（GB/T 12763.6-2007）：
  - 潮间带 H.39（样方号 / 气温 / 标本瓶数 …）
  - 潮下带 H.30（航次 / 船号 / 放绳长度 / 网型 / 拖网距离 …）
顶栏 `潮间带 / 潮下带 / 全部` 分段切换：网格列、导出模板随之；编辑器按记录自身采区
呈现对应字段。物理一表 + zone 列（见 collection_record_service）。

数据键 = (province, site, station, collection_date)，对齐 UID 地点段
（app/utils/naming.py:42-60）。持久化经 app/services/collection_record_service.py。

注：本功能超出 web oracle（其 code_labels.stations 仅 {码:标签}，不带元数据），
是 Qt 版新增的"野外采集记录簿"能力。
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QApplication,
    QAbstractItemView,
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QMenu,
    QPushButton,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from app.config.theme import local_font_css
from app.services import collection_record_service as crs
from app.views.base_view import BaseView
from app.widgets._form_row import form_row

if TYPE_CHECKING:
    from app.app_context import AppContext


# ── Form field spec ───────────────────────────────────────────────────────────
# (key, 中文标签, help_text)。按采区分两套，对齐 H.39 / H.30。
_KEY_FIELDS = ("province", "site", "station", "collection_date")

_EDITOR_SECTIONS: dict[str, list[tuple[str, list[tuple[str, str, str]]]]] = {
    "intertidal": [
        ("站位标识", [
            ("province",        "地区",   "地区代码，如 ZJ＝浙江。与样地/站位/采集时间共同唯一确定一条记录。"),
            ("site",            "样地",   "样地代码，如 SMW＝三门湾。"),
            ("station",         "站位",   "采集站位 / 站号，如 B2。"),
            ("quadrate_no",     "样方号", "每站多样方，如 B2-Q3。潮间带定量 H.39 必填。"),
            ("station_label",   "站位说明", "站位中文说明，如 北滩二区。"),
            ("collection_date", "采集日期", "YYYYMMDD，如 20260518。四键之一。"),
            ("collection_time", "采集时刻", "选填，如 14:30。"),
        ]),
        ("位置", [
            ("lon",        "经度",     "十进制度，留空存 NULL。"),
            ("lat",        "纬度",     "十进制度，留空存 NULL。"),
            ("geo_area",   "采集地理区", "如 浙江·三门湾。可由经纬度反解。"),
        ]),
        ("环境（潮间带）", [
            ("tidal_zone",       "潮区",      "潮间带分带：高潮区 / 中潮区 / 低潮区。"),
            ("habitat",          "底质",      "泥滩 / 泥沙滩 / 沙滩 / 砾石滩 / 岩礁 …（H.39 底质）。"),
            ("depth",            "水深(m)",   "潮间带通常留空。"),
            ("air_temp",         "气温(℃)",   "H.39 三温之一：气温/水温/底温。"),
            ("water_temp",       "表层水温(℃)", "选填。"),
            ("bottom_temp",      "底层水温(℃)", "选填，底栖动物所处水层。"),
            ("salinity",         "盐度",      "选填。"),
            ("dissolved_oxygen", "溶解氧(DO)", "选填，现场水质参数。"),
            ("ph",               "pH",       "选填，现场水质参数。"),
            ("weather",          "气象",      "晴 / 阴 / 雨 …（H.39 气象）。"),
            ("tide",             "潮水",      "潮位 / 潮时 / 大小潮，如 低潮 14:30。"),
        ]),
        ("采样（潮间带）", [
            ("sample_type",   "采集性质",    "定量 / 半定量 / 定性。定量需取样面积+次数。"),
            ("method",        "采样方法",    "定量框 / 手拣定性 等。"),
            ("sampler_model", "采泥器型号",  "型号 / 类型（潮间带少用，可留空）。"),
            ("sampler_spec",  "采样器规格",  "尺寸 / 取样口，如 25×25cm 框。"),
            ("sample_area",   "取样面积(m²)", "每站总取样面积，标准化 个体数·m⁻² 的关键。"),
            ("replicates",    "取样次数",    "每站框数，如 泥滩 8 框。"),
            ("sample_thickness", "样品厚度(cm)", "H.39 样品厚度。"),
            ("sieve_mesh",    "网筛孔径(mm)", "分选筛网孔径，大型底栖常 1.0。"),
            ("sample_no",     "样品编号",    "现场样品袋编号，如 B2-2026-007（DwC recordNumber）。"),
        ]),
        ("样品分装", [
            ("quant_bottles", "定量瓶数", "定量标本瓶数（现场分装，H.39）。"),
            ("qual_bottles",  "定性瓶数", "定性标本瓶数（现场分装，H.39）。"),
        ]),
        ("人员", [
            ("collector",    "采集人", "自动填充到工作台标本的采集人。"),
            ("recorder",     "记录人", "野外记录表填写人（责任链）。"),
            ("checker",      "核对人", "记录核对 / 复核人（责任链）。"),
            ("photographer", "拍摄人", "自动填充到工作台标本的拍摄人。"),
            ("identifier",   "鉴定人", "自动填充到工作台标本的鉴定人。"),
        ]),
        ("拍摄", [
            ("photo_date",     "拍摄日期", "YYYYMMDD，选填。"),
            ("photo_location", "拍摄地点", "如 实验室。"),
        ]),
        ("其它", [("remark", "备注", "")]),
    ],
    "subtidal": [
        ("站位标识", [
            ("province",        "地区",   "地区代码，如 ZJ＝浙江。"),
            ("site",            "样地",   "样地代码，如 SMW＝三门湾。"),
            ("station",         "站位",   "采集站位 / 站号，如 H1。"),
            ("station_label",   "站位说明", "站位中文说明。"),
            ("collection_date", "采集日期", "YYYYMMDD，如 20260518。四键之一。"),
            ("sample_no",       "样品编号", "现场样品袋编号（DwC recordNumber）。"),
        ]),
        ("位置", [
            ("lon",        "经度",     "十进制度，留空存 NULL。"),
            ("lat",        "纬度",     "十进制度，留空存 NULL。"),
            ("geo_area",   "采集地理区", "如 浙江·三门湾。"),
            ("water_body", "海区",     "DwC waterBody，如 东海 / 东海·三门湾。"),
        ]),
        ("平台（船基）", [
            ("cruise",   "航次",       "如 2026春季三门湾航次（H.30）。"),
            ("vessel",   "船号 / 船名", "调查船，如 科学三号 / 浙渔科×号（H.30）。"),
            ("wire_out", "放绳长度(m)", "船基水深订正（H.30）。"),
        ]),
        ("环境（潮下带）", [
            ("depth",       "水深(m)",    "潮下带站位水深。"),
            ("habitat",     "底质",       "泥 / 沙 / 砾石 / 岩 …（H.30 底质）。"),
            ("bottom_temp", "底层水温(℃)", "底栖动物所处水层。"),
            ("salinity",    "盐度(底层)",  "底层盐度（H.30 底盐）。"),
            ("weather",     "气象",       "晴 / 阴 / 雨 …。"),
        ]),
        ("采泥", [
            ("sample_type",      "采集性质",     "定量 / 半定量 / 定性。"),
            ("method",           "采样方法",     "采泥器 / 拖网 / 手拣定性。"),
            ("sampler_model",    "采泥器型号",   "如 大洋50型 / Van Veen / 箱式。"),
            ("sampler_area",     "采泥器面积(m²)", "定量换算关键，如 0.1m² 抓斗。"),
            ("replicates",       "采泥次数",     "每站采泥次数，如 4 次。"),
            ("sample_thickness", "样品厚度(cm)", "采泥厚度（H.30）。"),
            ("grab_sample_total", "采泥样品总数", "本站采泥样品总数。"),
            ("collection_time",  "采泥时刻",     "如 14:30。"),
        ]),
        ("拖网", [
            ("net_type",           "网型",       "阿氏网 / 双刃拖网 / 桁拖网 …（H.30）。"),
            ("net_width",          "网宽(m)",    "拖网网宽。"),
            ("trawl_distance",     "拖网距离(m)", "拖网距离。"),
            ("trawl_start",        "拖网起始",   "起网时刻。"),
            ("trawl_end",          "拖网结束",   "收网时刻。"),
            ("trawl_sample_total", "拖网样品总数", "本站拖网样品总数。"),
        ]),
        ("人员", [
            ("collector",    "采集人", "自动填充到工作台标本的采集人。"),
            ("recorder",     "记录人", "野外记录表填写人（责任链）。"),
            ("checker",      "核对人", "记录核对 / 复核人（责任链）。"),
            ("photographer", "拍摄人", "自动填充到工作台标本的拍摄人。"),
            ("identifier",   "鉴定人", "自动填充到工作台标本的鉴定人。"),
        ]),
        ("拍摄", [
            ("photo_date",     "拍摄日期", "YYYYMMDD，选填。"),
            ("photo_location", "拍摄地点", "如 实验室。"),
        ]),
        ("其它", [("remark", "备注", "")]),
    ],
}

# 批量表格（快速录入）列序——按采区。
_GRID_COLS_BY_ZONE: dict[str, list[tuple[str, str]]] = {
    "intertidal": [
        ("station", "站位"), ("collection_date", "采集日期"),
        ("quadrate_no", "样方号"), ("tidal_zone", "潮区"),
        ("habitat", "底质"), ("sample_area", "取样面积(m²)"),
        ("sample_type", "采集性质"), ("collector", "采集人"),
    ],
    "subtidal": [
        ("station", "站位"), ("collection_date", "采集日期"),
        ("depth", "水深(m)"), ("habitat", "底质"),
        ("sampler_area", "采泥器面积(m²)"), ("net_type", "网型"),
        ("sample_type", "采集性质"), ("collector", "采集人"),
    ],
}

# 向后兼容别名：默认（潮间带）网格列。旧测试/外部引用 _GRID_COLS 仍可用。
_GRID_COLS: list[tuple[str, str]] = _GRID_COLS_BY_ZONE["intertidal"]

# 逐条精修列表列（带采区标签）。
_TABLE_COLS: list[tuple[str, str]] = [
    ("zone", "采区"),
    ("station", "站位"),
    ("collection_date", "采集时间"),
    ("station_label", "说明"),
    ("sample_type", "性质"),
    ("collector", "采集人"),
]

_ZONE_LABELS = {"intertidal": "潮间带", "subtidal": "潮下带"}


def _zone_tag(z: Optional[str]) -> str:
    return _ZONE_LABELS.get((z or "").strip(), "—")


def _theme():
    """Return a token getter bound to the live theme (graceful fallback)."""
    try:
        from app.config.theme import TOKENS
        return TOKENS.get
    except Exception:  # pragma: no cover - theme always present in app
        return lambda k, d=None: d


class CollectionRecordsView(BaseView):
    """采集记录簿 — list + editor for per-station field collection records."""

    view_id = "collection_records"
    nav_title = "采集记录"
    nav_icon = "🗂️"

    def __init__(self, ctx: "AppContext") -> None:
        self._fields: dict[str, QLineEdit] = {}
        self._current_id: Optional[int] = None
        self._zone_filter: str = "intertidal"     # 顶栏分段：潮间带/潮下带/全部
        self._editor_zone: str = "intertidal"     # 当前编辑记录的采区
        self._editing_full: dict = {}             # 正在编辑的全字段（防 NULL 覆盖）
        super().__init__(ctx)

    # ── UI ────────────────────────────────────────────────────────────────────

    def _setup_ui(self) -> None:
        self._apply_style()

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(10)

        root.addWidget(self._build_zone_bar())

        # 两个标签页：新增「批量表格」+ 保留的「逐条精修」（原列表+侧边表单）。
        self._tabs = QTabWidget()
        self._tabs.addTab(self._build_grid_pane(), "批量表格")
        refine = QWidget()
        rl = QHBoxLayout(refine)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(14)
        rl.addWidget(self._build_list_pane(), 3)
        rl.addWidget(self._build_editor_pane(), 2)
        self._tabs.addTab(refine, "逐条精修")
        root.addWidget(self._tabs)

    def _build_zone_bar(self) -> QWidget:
        """顶栏采区分段：潮间带 / 潮下带 / 全部。控制网格列 + 新行采区 + 导出模板。"""
        bar = QWidget()
        h = QHBoxLayout(bar)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(8)
        title = QLabel("采区")
        title.setObjectName("SectionTitle")
        h.addWidget(title)
        self._zone_group = QButtonGroup(self)
        self._zone_group.setExclusive(True)
        for z, lbl in (("intertidal", "潮间带"), ("subtidal", "潮下带"), ("all", "全部")):
            btn = QPushButton(lbl)
            btn.setCheckable(True)
            btn.setProperty("zone", z)
            if z == self._zone_filter:
                btn.setChecked(True)
            btn.clicked.connect(lambda _checked=False, zz=z: self._on_zone_filter(zz))
            self._zone_group.addButton(btn)
            h.addWidget(btn)
        h.addStretch()
        self._zone_hint = QLabel("")
        self._zone_hint.setObjectName("SectionTitle")
        h.addWidget(self._zone_hint)
        return bar

    def _apply_style(self) -> None:
        g = _theme()
        bg, panel, border = g("bg", "#0a1e24"), g("panel_2", "#0e2329"), g("border", "#21424a")
        text, muted, accent = g("text", "#c8dcd6"), g("muted", "#7fa49b"), g("accent", "#4fd1b8")
        accent_fg = g("accent_fg", "#ffffff")
        hdr_bg = g("accent", "#2c5f8a")
        _ff = local_font_css()
        self.setStyleSheet(
            f"#{self.view_id}{{{_ff}background:{bg};}}"
            f"QLabel{{color:{text};background:transparent;}}"
            f"QLabel#SectionTitle{{color:{muted};font-weight:600;font-size:12px;}}"
            f"QLabel#PaneTitle{{color:{text};font-weight:600;font-size:15px;}}"
            f"QLineEdit{{background:{panel};color:{text};border:1px solid {border};"
            f"border-radius:5px;padding:4px 8px;font-size:13px;}}"
            f"QPushButton{{background:{panel};color:{text};border:1px solid {border};"
            f"border-radius:5px;padding:5px 12px;font-size:13px;}}"
            f"QPushButton:hover{{background:{border};}}"
            f"QPushButton:checked{{background:{accent};color:{accent_fg};border:1px solid {accent};}}"
            f"QPushButton#Primary{{background:{accent};color:{accent_fg};border:1px solid {accent};}}"
            f"QTableWidget{{background:{bg};color:{text};gridline-color:{border};"
            f"border:1px solid {border};border-radius:6px;"
            f"selection-background-color:{accent};selection-color:{accent_fg};}}"
            f"QHeaderView::section{{background:{hdr_bg};color:{accent_fg};font-weight:600;"
            f"padding:5px 8px;border:none;border-right:1px solid {border};}}"
            f"QScrollArea{{background:transparent;border:none;}}"
            f"QFrame#Sep{{background:{border};max-height:1px;border:none;}}"
        )

    # ── Grid pane (批量表格) ────────────────────────────────────────────────────
    def _grid_cols(self) -> list[tuple[str, str]]:
        """当前采区的网格列；「全部」用潮间带列。"""
        return _GRID_COLS_BY_ZONE.get(
            self._zone_filter, _GRID_COLS_BY_ZONE["intertidal"]
        )

    def _build_grid_pane(self) -> QWidget:
        pane = QWidget()
        v = QVBoxLayout(pane)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(8)

        bar = QHBoxLayout()
        title = QLabel("批量表格")
        title.setObjectName("PaneTitle")
        bar.addWidget(title)
        self._grid_ps_lbl = QLabel("")
        self._grid_ps_lbl.setObjectName("SectionTitle")
        bar.addWidget(self._grid_ps_lbl)
        bar.addStretch()
        btn_add = QPushButton("＋ 加一行")
        btn_add.clicked.connect(lambda: self._grid_add_row(inherit=True))
        bar.addWidget(btn_add)
        btn_fill = QPushButton("↓ 向下填充")
        btn_fill.setToolTip("把当前格的值填到本列下方所有行")
        btn_fill.clicked.connect(self._grid_fill_down)
        bar.addWidget(btn_fill)
        btn_save = QPushButton("保存表格")
        btn_save.setObjectName("Primary")
        btn_save.clicked.connect(self._grid_save)
        bar.addWidget(btn_save)
        self._btn_export = QPushButton("⬇ 导出模板")
        self._btn_export.setToolTip("导出当前采区 Excel 模板（含已有记录），离线填好后再导入")
        self._btn_export.clicked.connect(self._grid_export_template)
        bar.addWidget(self._btn_export)
        btn_import = QPushButton("⬆ 导入Excel")
        btn_import.setToolTip("按模板（固定列）导入；配合「导出模板」往返")
        btn_import.clicked.connect(self._grid_import)
        bar.addWidget(btn_import)
        btn_import2 = QPushButton("⬆ 导入(自定义)")
        btn_import2.setToolTip("任意 Excel/CSV/TXT：自定义列映射 + 任意经纬度格式 + 坐标系纠偏，"
                               "用于采集计划批量录入断面/站位")
        btn_import2.clicked.connect(self._grid_import_mapped)
        bar.addWidget(btn_import2)
        v.addLayout(bar)

        self._grid_status_lbl = QLabel("")
        self._grid_status_lbl.setObjectName("SectionTitle")
        v.addWidget(self._grid_status_lbl)

        cols = self._grid_cols()
        self._grid = QTableWidget(0, len(cols))
        self._grid.setHorizontalHeaderLabels([lbl for _k, lbl in cols])
        self._grid.verticalHeader().setVisible(True)
        self._grid.setEditTriggers(
            QAbstractItemView.EditTrigger.DoubleClicked
            | QAbstractItemView.EditTrigger.EditKeyPressed
            | QAbstractItemView.EditTrigger.AnyKeyPressed
        )
        self._grid.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        v.addWidget(self._grid, 1)
        return pane

    def _apply_zone_to_grid(self) -> None:
        """切换采区后重建网格列序并重载。"""
        cols = self._grid_cols()
        self._grid.setColumnCount(len(cols))
        self._grid.setHorizontalHeaderLabels([lbl for _k, lbl in cols])
        if hasattr(self, "_btn_export"):
            zhlbl = _ZONE_LABELS.get(self._zone_filter, "全部(潮间带+潮下带)")
            self._btn_export.setToolTip(f"导出 {zhlbl} Excel 模板（含已有记录），离线填好后再导入")
        if hasattr(self, "_zone_hint"):
            self._zone_hint.setText(
                f"新行采区：{_ZONE_LABELS.get(self._new_row_zone())}"
            )
        self._grid_load()

    def _new_row_zone(self) -> str:
        """新建行落入的采区：「全部」时默认潮间带（用户主用）。"""
        return self._zone_filter if self._zone_filter in _ZONE_LABELS else "intertidal"

    def _build_list_pane(self) -> QWidget:
        pane = QWidget()
        v = QVBoxLayout(pane)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(8)

        bar = QHBoxLayout()
        title = QLabel("采集记录")
        title.setObjectName("PaneTitle")
        bar.addWidget(title)
        bar.addStretch()
        btn_new = QPushButton("＋ 新建")
        btn_new.clicked.connect(self._new_record)
        bar.addWidget(btn_new)
        self._count_lbl = QLabel("")
        self._count_lbl.setStyleSheet("font-size:12px;")
        bar.addWidget(self._count_lbl)
        v.addLayout(bar)

        self._table = QTableWidget(0, len(_TABLE_COLS))
        self._table.setHorizontalHeaderLabels([lbl for _k, lbl in _TABLE_COLS])
        self._table.verticalHeader().setVisible(False)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.itemSelectionChanged.connect(self._on_row_selected)
        self._table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._table.customContextMenuRequested.connect(self._show_table_context_menu)
        self._table.horizontalHeader().setStretchLastSection(True)
        v.addWidget(self._table, 1)
        return pane

    def _build_editor_pane(self) -> QWidget:
        outer = QWidget()
        ov = QVBoxLayout(outer)
        ov.setContentsMargins(0, 0, 0, 0)
        ov.setSpacing(8)

        title = QLabel("记录详情")
        title.setObjectName("PaneTitle")
        ov.addWidget(title)

        # 编辑器内采区选择（决定该记录分类 + 显示字段集）
        zrow = QHBoxLayout()
        zrow.setContentsMargins(0, 0, 0, 0)
        zlbl = QLabel("采区")
        zlbl.setObjectName("SectionTitle")
        zlbl.setFixedWidth(40)
        zrow.addWidget(zlbl)
        self._editor_zone_group = QButtonGroup(self)
        self._editor_zone_group.setExclusive(True)
        for z, lbl in (("intertidal", "潮间带"), ("subtidal", "潮下带")):
            btn = QPushButton(lbl)
            btn.setCheckable(True)
            if z == self._editor_zone:
                btn.setChecked(True)
            btn.clicked.connect(lambda _c=False, zz=z: self._set_editor_zone(zz))
            self._editor_zone_group.addButton(btn)
            zrow.addWidget(btn)
        zrow.addStretch()
        ov.addLayout(zrow)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        inner = QWidget()
        self._editor_form = QVBoxLayout(inner)
        self._editor_form.setContentsMargins(2, 2, 8, 2)
        self._editor_form.setSpacing(6)
        scroll.setWidget(inner)
        ov.addWidget(scroll, 1)
        self._rebuild_editor(self._editor_zone)

        # Action bar
        actions = QHBoxLayout()
        self._btn_delete = QPushButton("删除")
        self._btn_delete.clicked.connect(self._delete_record)
        actions.addWidget(self._btn_delete)
        actions.addStretch()
        btn_save = QPushButton("保存")
        btn_save.setObjectName("Primary")
        btn_save.clicked.connect(self._save_record)
        actions.addWidget(btn_save)
        ov.addLayout(actions)
        return outer

    def _clear_editor_form(self) -> None:
        layout = self._editor_form
        while layout.count():
            it = layout.takeAt(0)
            w = it.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()
        self._fields = {}

    def _rebuild_editor(self, zone: str) -> None:
        """按 *zone* 重建表单字段，从 _editing_full 回填（跨采区切换不丢值）。"""
        self._editor_zone = zone
        self._clear_editor_form()
        sections = _EDITOR_SECTIONS.get(zone, _EDITOR_SECTIONS["intertidal"])
        for section_title, rows in sections:
            sec = QLabel(section_title)
            sec.setObjectName("SectionTitle")
            self._editor_form.addSpacing(4)
            self._editor_form.addWidget(sec)
            for key, label, help_text in rows:
                edit = QLineEdit()
                val = self._editing_full.get(key)
                if val not in (None, ""):
                    edit.setText(str(val))
                self._fields[key] = edit
                required = key in _KEY_FIELDS
                self._editor_form.addWidget(
                    form_row(label, edit, required=required, help_text=help_text or None)
                )
        self._editor_form.addStretch()

    def _set_editor_zone(self, zone: str) -> None:
        """编辑器采区切换：先吸收当前输入到 _editing_full，再重建。"""
        self._collect_editor_into_full()
        self._editing_full["zone"] = zone
        self._rebuild_editor(zone)

    def _collect_editor_into_full(self) -> None:
        for key, edit in self._fields.items():
            self._editing_full[key] = edit.text().strip()

    # ── BaseView ────────────────────────────────────────────────────────────────

    def on_activate(self) -> None:
        self._apply_style()
        self._apply_zone_to_grid()
        self._reload()
        self._consume_pending_filter()

    def _on_zone_filter(self, z: str) -> None:
        self._zone_filter = z
        self._apply_zone_to_grid()

    def _consume_pending_filter(self) -> None:
        """采集地图点击点 → ctx.pending_record_filter，跳来此页时选中匹配行。"""
        flt = getattr(self.ctx, "pending_record_filter", None)
        if not isinstance(flt, dict):
            return
        try:
            self.ctx.pending_record_filter = None
        except Exception:
            pass
        prov, site, station = flt.get("province"), flt.get("site"), flt.get("station")
        db = self.ctx.get_db()
        if db is None:
            return
        records = crs.list_records(db)
        target = None
        for rec in records:
            if rec.get("province") != prov:
                continue
            if site is not None and rec.get("site") != site:
                continue
            if station is not None and rec.get("station") != station:
                continue
            target = rec.get("id")
            break
        if target is not None:
            self._select_row_by_id(target)

    # ── Data ──────────────────────────────────────────────────────────────────

    def _reload(self) -> None:
        self._table.blockSignals(True)
        self._table.setUpdatesEnabled(False)
        try:
            self._table.setRowCount(0)
            db = self.ctx.get_db()
            records = crs.list_records(db) if db is not None else []
            for rec in records:
                row = self._table.rowCount()
                self._table.insertRow(row)
                for col, (key, _lbl) in enumerate(_TABLE_COLS):
                    val = rec.get(key)
                    if key == "zone":
                        val = _zone_tag(rec.get("zone"))
                    item = QTableWidgetItem("" if val is None else str(val))
                    if col == 0:
                        item.setData(Qt.ItemDataRole.UserRole, rec.get("id"))
                    self._table.setItem(row, col, item)
        finally:
            self._table.setUpdatesEnabled(True)
            self._table.blockSignals(False)
        self._count_lbl.setText(f"{len(records)} 条")

    def _on_row_selected(self) -> None:
        items = self._table.selectedItems()
        if not items:
            return
        row = items[0].row()
        rid = self._table.item(row, 0).data(Qt.ItemDataRole.UserRole)
        db = self.ctx.get_db()
        if db is None or rid is None:
            return
        rec = next((r for r in crs.list_records(db) if r.get("id") == rid), None)
        if rec is None:
            return
        self._current_id = rid
        self._editing_full = dict(rec)
        zone = (rec.get("zone") or "").strip() or "intertidal"
        # 同步采区选择按钮
        for btn in self._editor_zone_group.buttons():
            btn.setChecked(btn.property("zone") == zone)
        self._rebuild_editor(zone)

    def _record_for_row(self, row: int) -> Optional[dict]:
        if row < 0 or row >= self._table.rowCount():
            return None
        item = self._table.item(row, 0)
        rid = item.data(Qt.ItemDataRole.UserRole) if item is not None else None
        db = self.ctx.get_db()
        if db is None or rid is None:
            return None
        return next((r for r in crs.list_records(db) if r.get("id") == rid), None)

    def _show_table_context_menu(self, pos) -> None:
        row = self._table.indexAt(pos).row()
        rec = self._record_for_row(row)

        menu = QMenu(self._table)
        if rec is None:
            new_action = menu.addAction("新建采集记录")
            new_action.triggered.connect(self._new_record)
            menu.exec(self._table.viewport().mapToGlobal(pos))
            return

        self._table.selectRow(row)
        self._current_id = rec.get("id")

        edit_action = menu.addAction("编辑详情")
        edit_action.triggered.connect(lambda: self._edit_record_row(row))

        copy_key_action = menu.addAction("复制四键")
        copy_key_action.triggered.connect(lambda _=False, r=rec: self._copy_record_key(r))

        copy_summary_action = menu.addAction("复制记录摘要")
        copy_summary_action.triggered.connect(lambda _=False, r=rec: self._copy_record_summary(r))

        menu.addSeparator()
        new_action = menu.addAction("新建采集记录")
        new_action.triggered.connect(self._new_record)

        delete_action = menu.addAction("删除记录")
        delete_action.triggered.connect(self._delete_record)

        menu.addSeparator()
        properties_action = menu.addAction("属性")
        properties_action.triggered.connect(
            lambda _=False, r=rec: self._show_record_properties(r)
        )

        menu.exec(self._table.viewport().mapToGlobal(pos))

    def _edit_record_row(self, row: int) -> None:
        if row < 0:
            return
        self._table.selectRow(row)
        self._on_row_selected()
        self._tabs.setCurrentIndex(1)

    def _copy_record_key(self, rec: dict) -> None:
        parts = [
            rec.get("province") or "",
            rec.get("site") or "",
            rec.get("station") or "",
            rec.get("collection_date") or "",
        ]
        QApplication.clipboard().setText("\t".join(str(p) for p in parts))

    def _copy_record_summary(self, rec: dict) -> None:
        keys = [
            ("采区", _zone_tag(rec.get("zone"))),
            ("地区", rec.get("province")),
            ("样地", rec.get("site")),
            ("站位", rec.get("station")),
            ("采集日期", rec.get("collection_date")),
            ("采集人", rec.get("collector")),
            ("底质", rec.get("habitat")),
            ("经纬度", self._record_lonlat(rec)),
        ]
        text = "\n".join(f"{label}: {value or '—'}" for label, value in keys)
        QApplication.clipboard().setText(text)

    @staticmethod
    def _record_lonlat(rec: dict) -> str:
        lon, lat = rec.get("lon"), rec.get("lat")
        if lon in (None, "") or lat in (None, ""):
            return ""
        return f"{lon}, {lat}"

    def _show_record_properties(self, rec: dict) -> None:
        lines = [
            f"采区：{_zone_tag(rec.get('zone'))}",
            f"地区：{rec.get('province') or '—'}",
            f"样地：{rec.get('site') or '—'}",
            f"站位：{rec.get('station') or '—'}",
            f"采集日期：{rec.get('collection_date') or '—'}",
            f"采集人：{rec.get('collector') or '—'}",
            f"经纬度：{self._record_lonlat(rec) or '—'}",
            f"记录 ID：{rec.get('id') or '—'}",
        ]
        QMessageBox.information(self, "采集记录属性", "\n".join(lines))

    def _new_record(self) -> None:
        self._current_id = None
        self._table.clearSelection()
        zone = self._new_row_zone()
        self._editing_full = {"zone": zone}
        for btn in self._editor_zone_group.buttons():
            btn.setChecked(btn.property("zone") == zone)
        self._rebuild_editor(zone)
        if self._fields:
            self._fields.get("province") or self._fields.get("station")  # noop guard
        if "province" in self._fields:
            self._fields["province"].setFocus()

    def _save_record(self) -> None:
        db = self.ctx.get_db()
        if db is None:
            QMessageBox.information(self, "采集记录", "当前没有打开的项目，无法保存。")
            return
        self._collect_editor_into_full()
        data = dict(self._editing_full)
        data["zone"] = self._editor_zone
        missing = [lbl for k, lbl in (("province", "地区"), ("site", "样地"),
                                       ("station", "站位"), ("collection_date", "采集时间"))
                   if not data.get(k)]
        if missing:
            QMessageBox.warning(self, "采集记录", "请填写必填项：" + "、".join(missing))
            return
        if self._current_id is not None:
            data["id"] = self._current_id
        rid = crs.upsert_record(db, data)
        self._current_id = rid
        self._reload()
        self._apply_zone_to_grid()
        self._select_row_by_id(rid)

    def _delete_record(self) -> None:
        if self._current_id is None:
            self._new_record()
            return
        db = self.ctx.get_db()
        if db is None:
            return
        crs.delete_record(db, self._current_id)
        self._current_id = None
        self._reload()
        self._apply_zone_to_grid()
        self._new_record()

    def _select_row_by_id(self, rid: int) -> None:
        for row in range(self._table.rowCount()):
            if self._table.item(row, 0).data(Qt.ItemDataRole.UserRole) == rid:
                self._table.selectRow(row)
                return

    # ── Grid (批量表格) data ─────────────────────────────────────────────────────
    def _effective_ps(self) -> tuple[str, str]:
        """Inherited (province, site) for the current workspace, or empties."""
        project_dir = getattr(self.ctx, "current_project_dir", None)
        if not project_dir:
            return ("", "")
        try:
            from app.services import project_settings_service as pss
            pf = pss.effective_new_specimen_prefill(
                project_dir, root=self.ctx.current_project_root
            )
            return (pf.get("province", ""), pf.get("site", ""))
        except Exception:
            return ("", "")

    def _grid_load(self) -> None:
        """Fill the batch grid from the project's records + one blank trailing row."""
        prov, site = self._effective_ps()
        if prov or site:
            self._grid_ps_lbl.setText(f"地区 {prov or '—'} · 样地 {site or '—'}（自动套用）")
        else:
            self._grid_ps_lbl.setText("（未设地区/样地：可在项目设置或上层目录填写）")

        self._grid.blockSignals(True)
        self._grid.setUpdatesEnabled(False)
        try:
            self._grid.setRowCount(0)
            db = self.ctx.get_db()
            records = crs.list_records(db) if db is not None else []
            # zone 分段过滤行：「全部」显所有；潮间带含 zone=NULL 老记录（迁移
            # 默认归潮间带），潮下带只显显式 subtidal。列序由 _apply_zone_to_grid 切。
            zf = getattr(self, "_zone_filter", "intertidal")
            if zf == "subtidal":
                records = [r for r in records if (r.get("zone") or "") == "subtidal"]
            elif zf == "intertidal":
                records = [r for r in records
                           if (r.get("zone") or "") in ("", "intertidal")]
            for rec in records:
                self._grid_append_row(rec)
            self._grid_append_row(None)  # trailing blank row for quick add
        finally:
            self._grid.setUpdatesEnabled(True)
            self._grid.blockSignals(False)

    def _grid_append_row(self, rec: Optional[dict]) -> None:
        row = self._grid.rowCount()
        self._grid.insertRow(row)
        for col, (key, _lbl) in enumerate(self._grid_cols()):
            val = (rec or {}).get(key)
            item = QTableWidgetItem("" if val in (None, "") else str(val))
            if col == 0:
                # Stash the originating record (id/province/site/zone + 全字段) so
                # re-saving preserves identity and any non-grid fields.
                item.setData(Qt.ItemDataRole.UserRole, rec or None)
            self._grid.setItem(row, col, item)

    def _grid_add_row(self, *, inherit: bool = True) -> None:
        """Append a blank row, inheriting 采集日期/采集人 from the last row +
        stamping the current zone filter."""
        carry: dict = {"zone": self._new_row_zone()}
        if inherit and self._grid.rowCount() > 0:
            last = self._grid.rowCount() - 1
            for col, (key, _lbl) in enumerate(self._grid_cols()):
                if key in ("collection_date", "collector"):
                    it = self._grid.item(last, col)
                    if it and it.text().strip():
                        carry[key] = it.text().strip()
        self._grid_append_row(carry or None)

    def _grid_fill_down(self) -> None:
        cur = self._grid.currentItem()
        if cur is None:
            return
        col = cur.column()
        text = cur.text()
        for row in range(cur.row() + 1, self._grid.rowCount()):
            it = self._grid.item(row, col)
            if it is None:
                it = QTableWidgetItem("")
                self._grid.setItem(row, col, it)
            it.setText(text)

    def _grid_save(self) -> None:
        """Upsert every non-blank grid row. 地区/样地 come from inheritance;
        zone comes from the row origin or the current filter for new rows."""
        db = self.ctx.get_db()
        if db is None:
            self._grid_status_lbl.setText("当前没有打开的项目，无法保存。")
            return
        eff_prov, eff_site = self._effective_ps()
        default_zone = self._new_row_zone()

        saved = 0
        skipped_no_ps = 0
        for row in range(self._grid.rowCount()):
            values = {}
            for col, (key, _lbl) in enumerate(self._grid_cols()):
                it = self._grid.item(row, col)
                values[key] = it.text().strip() if it else ""
            if not values.get("station") or not values.get("collection_date"):
                continue
            station_item = self._grid.item(row, 0)
            orig = station_item.data(Qt.ItemDataRole.UserRole) if station_item else None
            data = dict(orig) if isinstance(orig, dict) else {}
            data["province"] = (data.get("province") or eff_prov)
            data["site"] = (data.get("site") or eff_site)
            data.update(values)
            data.setdefault("zone", default_zone)   # 新行无 zone → 当前采区
            if not data.get("province") or not data.get("site"):
                skipped_no_ps += 1
                continue
            crs.upsert_record(db, data)
            saved += 1

        self._reload()
        self._grid_load()
        msg = f"已保存 {saved} 条。"
        if skipped_no_ps:
            msg += f"  {skipped_no_ps} 行缺地区/样地未保存（请先在项目设置或上层目录填写）。"
        self._grid_status_lbl.setText(msg)

    # ── Excel 模板导出 / 导入 ─────────────────────────────────────────────────────
    def _grid_export_template(self) -> None:
        from app.utils import ui
        db = self.ctx.get_db()
        if db is None:
            self._grid_status_lbl.setText("当前没有打开的项目，无法导出。")
            return
        zone = self._zone_filter
        suffix = "" if zone == "all" else f"-{_ZONE_LABELS[zone]}"
        default_name = f"采集记录模板{suffix}.xlsx"
        path = ui.get_save_file_name(
            self, "导出采集记录模板", default_name, "Excel 文件 (*.xlsx)"
        )
        if not path:
            return
        if not path.lower().endswith(".xlsx"):
            path += ".xlsx"
        prov, site = self._effective_ps()
        try:
            from app.services import collection_record_io as crio
            n = crio.export_template(db, path, zone=zone, province=prov, site=site)
            self._grid_status_lbl.setText(f"已导出 {_ZONE_LABELS.get(zone,'全部')}模板：{n} 条已有记录 + 空行 → {path}")
        except Exception as exc:  # noqa: BLE001
            self._grid_status_lbl.setText(f"导出失败：{exc}")

    def _grid_import(self) -> None:
        from app.utils import ui
        db = self.ctx.get_db()
        if db is None:
            self._grid_status_lbl.setText("当前没有打开的项目，无法导入。")
            return
        path = ui.get_open_file_name(
            self, "导入采集记录", "", "表格 (*.xlsx *.xlsm *.csv)"
        )
        if not path:
            return
        from app.services import collection_record_io as crio
        rep = crio.import_file(db, path)
        self._reload()
        self._grid_load()
        if not rep.ok:
            self._grid_status_lbl.setText("导入失败：" + "；".join(rep.errors[:3]))
            return
        self._snapshot_current()
        msg = f"已导入 {rep.imported} 条。"
        if rep.skipped:
            msg += f"  跳过 {rep.skipped} 行（缺地区/样地/站位/采集日期）。"
        if rep.errors:
            msg += f"  {len(rep.errors)} 行出错。"
        self._grid_status_lbl.setText(msg)

    def _grid_import_mapped(self) -> None:
        db = self.ctx.get_db()
        if db is None:
            self._grid_status_lbl.setText("当前没有打开的项目，无法导入。请先建/选项目。")
            return
        from app.widgets.coord_import_dialog import CoordImportDialog
        dlg = CoordImportDialog(db, parent=self)
        if dlg.exec():
            self._snapshot_current()
            self._reload()
            self._grid_load()
            self._grid_status_lbl.setText("导入完成（自定义映射）。")

    def _snapshot_current(self) -> None:
        cur = getattr(self.ctx, "current_project_dir", None)
        if cur:
            from app.services.backup_service import snapshot_project
            snapshot_project(cur)
