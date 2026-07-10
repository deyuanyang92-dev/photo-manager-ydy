"""summary_view.py — 项目汇总：跨项目标本数据汇总表 + 导出。

Oracle:
  - app.js:17841-18173  (renderSummaryPage / exportProjectsExcel / exportSummaryCsv)
  - pages_dom.json      (控件清单)

功能：
  - 顶部控制栏：标题 + 项目筛选下拉 + "⚙ 字段"按钮 + Excel/CSV 导出 + 保存到目录
  - 可折叠字段选择面板（全选 / 重置默认 / 清空）
  - 多列标本汇总表（sticky 首列）：26 默认列，全量 34 列可切换
  - 空状态提示、行数 + 列数计数标签
  - 状态列彩色（已合成=青 / 部分合成=黄 / 待合成=红）
  - taxoOk / RNA 标记列彩色
  - 导出 Excel（通过 export_service.export_excel）
  - 导出 CSV（客户端按当前可见列 + 项目筛选）
  - 控制区套 QScrollArea 防窗口挤压；汇总表独立滚动 stretch=1
"""
from __future__ import annotations

import csv
import io
import os
import sqlite3
from collections import Counter
from datetime import date as _date
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import Qt, QDate, QSortFilterProxyModel, QThread, pyqtSignal
from PyQt6.QtGui import QColor, QBrush, QStandardItem, QStandardItemModel
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDateEdit,
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from app.models.specimen import Specimen
# NOTE: export_service pulls in openpyxl at import. It is imported lazily inside
# the export handlers so app startup (which eagerly imports this view via the
# registry) does not pay the openpyxl cost unless the user actually exports.
from app.config.theme import local_font_css
from app.views.base_view import BaseView
from app.widgets.lunar_calendar_widget import install_lunar_popup

if True:  # TYPE_CHECKING guard
    from typing import TYPE_CHECKING
    if TYPE_CHECKING:
        from app.app_context import AppContext


# ── Storage helpers (mirrors export_service) ──────────────────────────────────

from app.services.project_settings_service import BUILTIN_STORAGES

_STORAGE_LABELS: dict[str, str] = {
    str(entry["code"]): str(entry["detail"]) for entry in BUILTIN_STORAGES
}


def _pres_detail(storage: Optional[str]) -> str:
    if not storage:
        return ""
    return _STORAGE_LABELS.get(storage.upper(), storage)


def _is_rna(storage: Optional[str]) -> bool:
    return bool(storage and str(storage).upper().startswith("R"))


def _meta_score(sp: Specimen) -> str:
    fields = [sp.scientific_name, sp.family, sp.collector,
              sp.lon, sp.lat]
    filled = sum(1 for f in fields if f is not None and str(f).strip() != "")
    return f"{round(filled / len(fields) * 100)}%"


def _taxo_ok(sp: Specimen) -> bool:
    return bool(sp.scientific_name and sp.family)


def _date_seg(sp: Specimen) -> str:
    """Build date segment from collection_date + photo_date."""
    c = sp.collection_date or ""
    p = sp.photo_date or ""
    if c and p and c != p:
        return f"{c.replace('-', '')}_{p.replace('-', '')}"
    return (c or p).replace("-", "")


def _empty_grouping(proj_code: str = "") -> dict:
    return {
        "count": 0,
        "group_count": 0,
        "status": "无成果",
        "projCode": proj_code,
        "jpg_count": 0,
        "tiff_count": 0,
        "zip_count": 0,
        "tiff_names": "",
        "tiff_paths": "",
        "zip_names": "",
        "zip_paths": "",
    }


# ── Search / date-range filter helpers (Qt-free, unit-testable) ───────────────

def _norm_date(value) -> str:
    """Normalise a date string to ISO ``yyyy-mm-dd`` (slashes → dashes).

    None / empty → "".  Slash-separated forms (``2026/06/01``) become
    dash-separated so string comparison against ISO bounds is well-ordered.
    """
    if not value:
        return ""
    return str(value).strip().replace("/", "-")


def _date_in_range(value, lo, hi) -> bool:
    """Inclusive range test on ISO date strings.

    Empty ``value`` is *excluded* (a row with no date never matches an active
    range).  Empty ``lo`` / ``hi`` mean an open end on that side.
    """
    v = _norm_date(value)
    if not v:
        return False
    lo_s = _norm_date(lo)
    hi_s = _norm_date(hi)
    if lo_s and v < lo_s:
        return False
    if hi_s and v > hi_s:
        return False
    return True


def _preset_range(name: str, today) -> tuple:
    """Map a quick-preset name to an inclusive ``(from, to)`` ISO pair.

    ``today`` is the reference :class:`datetime.date` (injected so this stays
    pure / testable).  ``all`` → ``(None, None)`` (no bound).
    """
    from datetime import timedelta
    if name == "today":
        iso = today.isoformat()
        return (iso, iso)
    if name == "7d":
        return ((today - timedelta(days=6)).isoformat(), today.isoformat())
    if name == "30d":
        return ((today - timedelta(days=29)).isoformat(), today.isoformat())
    if name == "year":
        return (today.replace(month=1, day=1).isoformat(),
                today.replace(month=12, day=31).isoformat())
    return (None, None)


# ── All-column definitions (mirrors app.js ALL_COLS) ─────────────────────────

def _build_all_cols() -> list[dict]:
    """Return list of {key, label, getter(sp, grouping_row)} dicts."""
    def _specimen_geo_area_label(sp: Specimen) -> str:
        if sp.geo_area:
            return sp.geo_area
        parts = [sp.province or ""]
        if sp.site:
            parts.append(f"·{sp.site}")
        return "".join(parts)

    return [
        {"key": "projCode",     "label": "项目编号",      "get": lambda s, g: g.get("projCode", "")},
        {"key": "uid",          "label": "标本唯一编号",  "get": lambda s, g: s.uid or ""},
        {"key": "spId",         "label": "物种拼音编号",  "get": lambda s, g: s.id or ""},
        {"key": "nameCn",       "label": "物种中名",      "get": lambda s, g: s.scientific_name_cn or ""},
        {"key": "nameLat",      "label": "物种拉丁名",    "get": lambda s, g: s.scientific_name or ""},
        {"key": "taxonGrpCn",   "label": "类群",          "get": lambda s, g: s.taxon_group_cn or s.taxon_group or ""},
        {"key": "taxonGrp",     "label": "类群拉丁",      "get": lambda s, g: s.taxon_group or ""},
        {"key": "orderCn",      "label": "目",            "get": lambda s, g: s.order_cn or s.order_name or ""},
        {"key": "order",        "label": "目拉丁",        "get": lambda s, g: s.order_name or ""},
        {"key": "familyCn",     "label": "科",            "get": lambda s, g: s.family_cn or s.family or ""},
        {"key": "family",       "label": "科拉丁",        "get": lambda s, g: s.family or ""},
        {"key": "genusCn",      "label": "属",            "get": lambda s, g: s.genus_cn or s.genus or ""},
        {"key": "genus",        "label": "属拉丁",        "get": lambda s, g: s.genus or ""},
        {"key": "province",     "label": "省/市",          "get": lambda s, g: s.province or ""},
        {"key": "site",         "label": "地区/样地",      "get": lambda s, g: s.site or ""},
        {"key": "station",      "label": "站位",          "get": lambda s, g: s.station or ""},
        {"key": "geoArea",      "label": "采集地",        "get": lambda s, g: _specimen_geo_area_label(s)},
        {"key": "lon",          "label": "经度",          "get": lambda s, g: str(s.lon) if s.lon is not None else ""},
        {"key": "lat",          "label": "纬度",          "get": lambda s, g: str(s.lat) if s.lat is not None else ""},
        {"key": "storage",      "label": "保存方式",      "get": lambda s, g: s.storage or ""},
        {"key": "presDetail",   "label": "固定方式全文",  "get": lambda s, g: _pres_detail(s.storage)},
        {"key": "collDate",     "label": "采集日期",      "get": lambda s, g: s.collection_date or ""},
        {"key": "photoDate",    "label": "拍照日期",      "get": lambda s, g: s.photo_date or ""},
        {"key": "dateSeg",      "label": "日期段",        "get": lambda s, g: _date_seg(s)},
        {"key": "collector",    "label": "采集人",        "get": lambda s, g: s.collector or ""},
        {"key": "photographer", "label": "拍摄人",        "get": lambda s, g: s.photographer or ""},
        {"key": "identifier",   "label": "鉴定人",        "get": lambda s, g: s.identifier or ""},
        {"key": "compCount",    "label": "已合成组数",    "get": lambda s, g: str(g.get("count", 0))},
        {"key": "tiffCount",    "label": "TIF数",         "get": lambda s, g: str(g.get("tiff_count", 0))},
        {"key": "jpgCount",     "label": "JPG原图数",     "get": lambda s, g: str(g.get("jpg_count", 0))},
        {"key": "zipCount",     "label": "ZIP数",         "get": lambda s, g: str(g.get("zip_count", 0))},
        {"key": "tiffNames",    "label": "TIF文件",       "get": lambda s, g: g.get("tiff_names", "")},
        {"key": "zipNames",     "label": "ZIP文件",       "get": lambda s, g: g.get("zip_names", "")},
        {"key": "compStatus",   "label": "成果状态",      "get": lambda s, g: g.get("status", "无成果")},
        {"key": "taxoOk",       "label": "分类完整",      "get": lambda s, g: "✓" if _taxo_ok(s) else "✗"},
        {"key": "rna",          "label": "RNA标本",       "get": lambda s, g: "✓" if _is_rna(s.storage) else "✗"},
        {"key": "meta",         "label": "Meta%",         "get": lambda s, g: _meta_score(s)},
        {"key": "notes",        "label": "备注",          "get": lambda s, g: s.notes or ""},
        {"key": "photoNotes",   "label": "拍照备注",      "get": lambda s, g: s.photo_notes or ""},
    ]


ALL_COLS = _build_all_cols()

# Default visible column keys (mirrors SUMMARY_DEFAULT_COLS in app.js)
_DEFAULT_KEYS = [
    "projCode", "uid", "spId", "nameCn", "nameLat",
    "taxonGrpCn", "orderCn", "familyCn", "genusCn",
    "province", "site", "station", "geoArea",
    "lon", "lat", "storage", "collDate", "photoDate",
    "collector", "photographer",
    "compCount", "tiffCount", "jpgCount", "zipCount", "tiffNames",
    "compStatus", "taxoOk", "rna", "meta",
]

_SCOPE_CURRENT = "current"
_SCOPE_ALL = "all"
_SCOPE_SELECTED = "selected"
_GROUPING_KEY_SEP = "\0"
_TABLE_AUTOSIZE_CELL_LIMIT = 6000

# 停不下来的加载线程退休名单(模式同 project_tree/data_filter): 模块级引用
# 防 GC → destroyed-while-running 原生 abort。
_RETIRED_LOAD_WORKERS: list = []


class _SummaryLoadWorker(QThread):
    """「全部/选中项目」汇总加载后台线程 — 遍历所有项目逐库查询不进主线程."""

    finished_payload = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, dirs: list[str], root: str, parent=None) -> None:
        super().__init__(parent)
        self._dirs = list(dirs)
        self._root = root

    def run(self) -> None:
        try:
            from app.services import project_summary_service as pss
            payload = pss.collect_specimen_summary_rows(self._dirs, self._root)
        except Exception as exc:
            self.failed.emit(str(exc))
            return
        self.finished_payload.emit(payload)

# Colour constants — refreshed from the LIVE active theme by _refresh_palette()
# (called at the top of each view's _setup_ui).  Previously hardcoded deep-teal,
# which force-painted the whole page dark regardless of the chosen theme.
_C_BG           = "#0a1e24"
_C_BG2          = "#0e2329"
_C_BORDER       = "#21424a"
_C_TEXT         = "#c8dcd6"
_C_MUTED        = "#7fa49b"
_C_ACCENT       = "#4fd1b8"
_C_WARN         = "#f0c060"
_C_ERR          = "#e07070"
_C_HDR_BG       = "#2c5f8a"
_C_HDR_FG       = "#ffffff"
_C_ALT_ROW      = "#0f2830"
_C_INACTIVE_TXT = "#5a7a72"


def _refresh_palette() -> None:
    """Rebind the module `_C_*` colours to the current theme tokens."""
    global _C_BG, _C_BG2, _C_BORDER, _C_TEXT, _C_MUTED, _C_ACCENT, _C_WARN
    global _C_ERR, _C_HDR_BG, _C_HDR_FG, _C_ALT_ROW, _C_INACTIVE_TXT
    from app.config.theme import TOKENS
    g = TOKENS.get
    _C_BG           = g("bg", _C_BG)
    _C_BG2          = g("panel_2", _C_BG2)
    _C_BORDER       = g("border", _C_BORDER)
    _C_TEXT         = g("text", _C_TEXT)
    _C_MUTED        = g("muted", _C_MUTED)
    _C_ACCENT       = g("accent", _C_ACCENT)
    _C_WARN         = g("warn", _C_WARN)
    _C_ERR          = g("danger", _C_ERR)
    _C_HDR_BG       = g("accent", _C_HDR_BG)
    _C_HDR_FG       = g("accent_fg", "#ffffff")
    _C_ALT_ROW      = g("panel_2", _C_ALT_ROW)
    _C_INACTIVE_TXT = g("muted_dim", _C_INACTIVE_TXT)


# ── Field-picker sub-widget ───────────────────────────────────────────────────

class _FieldPicker(QFrame):
    """Inline field picker panel (collapsible, replaces the ⚙字段 overlay)."""

    def __init__(
        self,
        visible_keys: list[str],
        on_change,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("FieldPicker")
        self.setStyleSheet(
            f"QFrame#FieldPicker {{background:{_C_BG};border:1px solid {_C_BORDER};"
            f"border-radius:8px;}}"
        )

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 10, 12, 10)
        root.setSpacing(6)

        # Header row
        head_row = QHBoxLayout()
        head_row.setSpacing(8)
        title = QLabel("显示字段")
        title.setStyleSheet(f"font-size:13px;color:{_C_MUTED};font-weight:600;")
        head_row.addWidget(title)

        for btn_txt, action_keys in (
            ("全选",    [c["key"] for c in ALL_COLS]),
            ("重置默认", list(_DEFAULT_KEYS)),
            ("清空",    ["uid"]),
        ):
            btn = QPushButton(btn_txt)
            btn.setStyleSheet(
                f"QPushButton{{font-size:11px;padding:3px 8px;"
                f"background:{_C_BG2};color:{_C_TEXT};border:1px solid {_C_BORDER};"
                f"border-radius:4px;}}"
                f"QPushButton:hover{{background:{_C_BORDER};}}"
            )
            keys = action_keys  # capture
            btn.clicked.connect(lambda checked, k=keys: on_change(k))
            head_row.addWidget(btn)
        head_row.addStretch()
        root.addLayout(head_row)

        # Checkbox grid
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setMaximumHeight(120)
        scroll.setStyleSheet("QScrollArea{background:transparent;border:none;}")
        inner = QWidget()
        inner.setStyleSheet("background:transparent;")
        grid = QHBoxLayout(inner)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(0)

        # Two-row wrap: use a flow-style grid via multiple columns
        cols_widget = QWidget()
        cols_widget.setStyleSheet("background:transparent;")
        cols_layout = QHBoxLayout(cols_widget)
        cols_layout.setContentsMargins(0, 0, 0, 0)
        cols_layout.setSpacing(0)

        self._checks: dict[str, QCheckBox] = {}
        wrap = QWidget()
        wrap.setStyleSheet("background:transparent;")
        from PyQt6.QtWidgets import QGridLayout
        wrap_layout = QGridLayout(wrap)
        wrap_layout.setContentsMargins(0, 0, 0, 0)
        wrap_layout.setHorizontalSpacing(16)
        wrap_layout.setVerticalSpacing(4)

        NCOLS = 8
        for idx, col in enumerate(ALL_COLS):
            r, c = divmod(idx, NCOLS)
            cb = QCheckBox(col["label"])
            cb.setChecked(col["key"] in visible_keys)
            cb.setStyleSheet(
                f"QCheckBox{{font-size:12px;color:{_C_TEXT if col['key'] in visible_keys else _C_INACTIVE_TXT};}}"
            )
            key = col["key"]
            cb.stateChanged.connect(
                lambda state, k=key: self._update_visible_columns_from_checkbox(
                    k, state, on_change
                )
            )
            wrap_layout.addWidget(cb, r, c)
            self._checks[col["key"]] = cb

        scroll.setWidget(wrap)
        root.addWidget(scroll)

    def _update_visible_columns_from_checkbox(self, key: str, state: int, on_change) -> None:
        checked = state == Qt.CheckState.Checked.value
        current = [k for k, cb in self._checks.items() if cb.isChecked()]
        if not checked and key in current:
            current.remove(key)
        elif checked and key not in current:
            # Preserve original order
            current = [c["key"] for c in ALL_COLS if c["key"] in current]
        on_change(current)

    def set_visible_keys(self, keys: list[str]) -> None:
        for k, cb in self._checks.items():
            cb.setChecked(k in keys)
            color = _C_TEXT if k in keys else _C_INACTIVE_TXT
            cb.setStyleSheet(f"QCheckBox{{font-size:12px;color:{color};}}")


# ── Main SummaryView ──────────────────────────────────────────────────────────

class SummaryView(BaseView):
    """项目汇总 — cross-project specimen data table + Excel/CSV export.

    Oracle: app.js:17841–18173 (renderSummaryPage / exportProjectsExcel / exportSummaryCsv)
    """

    view_id   = "summary"
    nav_title = "项目汇总"
    nav_icon  = "📋"

    def __init__(self, ctx: "AppContext") -> None:
        self._visible_keys: list[str] = list(_DEFAULT_KEYS)
        self._specimens: list[Specimen] = []
        self._grouping: dict[str, dict] = {}          # row-key/uid → {count, status, projCode}
        self._project_filter: str = ""                # "" = all
        self._search_text: str = ""                   # cross-column substring filter
        self._date_field: str = "不限日期"            # 不限日期 / 采集日期 / 拍照日期
        self._date_from: str = ""                     # ISO lower bound ("" = open)
        self._date_to: str = ""                       # ISO upper bound ("" = open)
        self._projects: list[dict] = []               # list of {id, name, directory, projectCode?}
        self._summary_scope: str = _SCOPE_CURRENT
        self._selected_project_dirs: list[str] = []
        self._loaded_project_dirs: list[str] = []
        self._loaded_root: str = ""
        self._dashboard_snapshot: dict | None = None
        self._picker_open: bool = False
        self._model: Optional[QStandardItemModel] = None
        self._proxy: Optional[QSortFilterProxyModel] = None
        self._save_msg: str = ""
        super().__init__(ctx)

    # ── BaseView ──────────────────────────────────────────────────────────────

    def _setup_ui(self) -> None:
        _refresh_palette()  # bind _C_* to the active theme before building
        _ff = local_font_css()
        self.setStyleSheet(
            f"SummaryView{{{_ff}background:{_C_BG};}}"
            f"QLabel{{color:{_C_TEXT};background:transparent;}}"
            f"QPushButton{{background:{_C_BG2};color:{_C_TEXT};"
            f"border:1px solid {_C_BORDER};border-radius:5px;padding:5px 12px;font-size:13px;}}"
            f"QPushButton:hover{{background:{_C_BORDER};}}"
            f"QPushButton#Primary{{background:{_C_ACCENT};color:{_C_HDR_FG};border:1px solid {_C_ACCENT};}}"
            f"QPushButton#Primary:hover{{background:{_C_ACCENT};}}"
            f"QComboBox{{background:{_C_BG2};color:{_C_TEXT};border:1px solid {_C_BORDER};"
            f"border-radius:5px;padding:4px 8px;font-size:13px;}}"
            f"QComboBox::drop-down{{border:none;}}"
            f"QComboBox QAbstractItemView{{background:{_C_BG};color:{_C_TEXT};"
            f"border:1px solid {_C_BORDER};}}"
            f"QLineEdit{{background:{_C_BG2};color:{_C_TEXT};border:1px solid {_C_BORDER};"
            f"border-radius:5px;padding:4px 8px;font-size:13px;}}"
            f"QTableView{{background:{_C_BG};color:{_C_TEXT};"
            f"gridline-color:{_C_BORDER};border:1px solid {_C_BORDER};border-radius:6px;"
            f"alternate-background-color:{_C_ALT_ROW};selection-background-color:{_C_ACCENT};}}"
            f"QHeaderView::section{{background:{_C_HDR_BG};color:{_C_HDR_FG};"
            f"font-weight:600;padding:5px 8px;border:none;border-right:1px solid {_C_BORDER};}}"
            # Transparent scrollarea background so controls region blends in
            f"QScrollArea{{background:transparent;border:none;}}"
            f"QScrollArea > QWidget > QWidget{{background:transparent;}}"
            f"QFrame#Dashboard{{background:{_C_BG2};border:1px solid {_C_BORDER};border-radius:6px;}}"
            f"QLabel#DashValue{{font-size:18px;font-weight:700;color:{_C_TEXT};}}"
            f"QLabel#DashName{{font-size:11px;color:{_C_MUTED};}}"
            f"QLabel#DashPeople{{font-size:12px;color:{_C_TEXT};}}"
        )

        # ── Outer layout: controls region (fixed-height scroll) + table (stretch) ──
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 16)
        root.setSpacing(0)

        # ── Controls region wrapped in QScrollArea (prevents overlap when narrow) ──
        controls_widget = QWidget()
        controls_widget.setStyleSheet(f"background:{_C_BG};")
        controls_layout = QVBoxLayout(controls_widget)
        controls_layout.setContentsMargins(0, 0, 0, 0)
        controls_layout.setSpacing(10)

        # Top control bar
        bar = QHBoxLayout()
        bar.setSpacing(12)

        title = QLabel("项目汇总")
        title.setStyleSheet(
            f"font-size:16px;color:{_C_TEXT};font-weight:600;"
        )
        bar.addWidget(title)

        # Separator spacing between title and controls
        bar.addSpacing(8)

        self._scope_combo = QComboBox()
        self._scope_combo.setToolTip("选择汇总范围")
        self._scope_combo.addItem("当前项目", _SCOPE_CURRENT)
        self._scope_combo.addItem("全部已知项目", _SCOPE_ALL)
        self._scope_combo.addItem("自选项目", _SCOPE_SELECTED)
        self._scope_combo.currentIndexChanged.connect(self._on_scope_changed)
        bar.addWidget(self._scope_combo)

        self._btn_sources = QPushButton("选择项目…")
        self._btn_sources.setToolTip("勾选最近项目，或添加未在列表中的项目目录")
        self._btn_sources.clicked.connect(self._open_summary_sources)
        bar.addWidget(self._btn_sources)

        # Project filter combo
        self._filter_combo = QComboBox()
        self._filter_combo.setMinimumWidth(160)
        self._filter_combo.currentIndexChanged.connect(self._on_filter_changed)
        bar.addWidget(self._filter_combo)

        # Search box — instant cross-column substring filter
        self._search_input = QLineEdit()
        self._search_input.setPlaceholderText("🔍 检索 编号/物种/采集人…")
        self._search_input.setClearButtonEnabled(True)
        self._search_input.setMinimumWidth(200)
        self._search_input.setToolTip("跨所有列模糊匹配（编号、物种名、采集人等）")
        self._search_input.textChanged.connect(self._on_search_changed)
        bar.addWidget(self._search_input)

        # Date-range filter group: field selector + from/to + quick presets
        self._date_field_combo = QComboBox()
        self._date_field_combo.addItems(["不限日期", "采集日期", "拍照日期"])
        self._date_field_combo.setToolTip("按采集日期或拍照日期筛选")
        self._date_field_combo.currentTextChanged.connect(self._on_date_field_changed)
        bar.addWidget(self._date_field_combo)

        self._date_from_edit = QDateEdit()
        install_lunar_popup(self._date_from_edit)   # 万年历弹窗：农历+节日+节气
        self._date_from_edit.setDisplayFormat("yyyy-MM-dd")
        self._date_from_edit.setMinimumDate(QDate(1900, 1, 1))
        self._date_from_edit.setToolTip("直接选日期即按采集日期筛选；左侧可切换为拍照日期")
        self._date_from_edit.dateChanged.connect(self._on_date_edit_changed)
        bar.addWidget(self._date_from_edit)

        _dash = QLabel("—")
        bar.addWidget(_dash)

        self._date_to_edit = QDateEdit()
        install_lunar_popup(self._date_to_edit)     # 万年历弹窗：农历+节日+节气
        self._date_to_edit.setDisplayFormat("yyyy-MM-dd")
        self._date_to_edit.setMinimumDate(QDate(1900, 1, 1))
        self._date_to_edit.setToolTip("直接选日期即按采集日期筛选；左侧可切换为拍照日期")
        self._date_to_edit.dateChanged.connect(self._on_date_edit_changed)
        bar.addWidget(self._date_to_edit)

        self._preset_combo = QComboBox()
        self._preset_combo.addItems(["快捷", "今天", "近7天", "近30天", "今年", "全部"])
        self._preset_combo.setToolTip("快速套用日期范围")
        self._preset_combo.activated.connect(self._on_preset_selected)
        bar.addWidget(self._preset_combo)

        # Field selector toggle
        self._btn_cols = QPushButton("⚙ 字段")
        self._btn_cols.setCheckable(True)
        self._btn_cols.clicked.connect(self._toggle_picker)
        bar.addWidget(self._btn_cols)

        bar.addSpacing(4)

        # Export buttons
        self._btn_excel = QPushButton("⬇ Excel")
        self._btn_excel.setObjectName("Primary")
        self._btn_excel.setToolTip("下载格式化 Excel（含全部字段）")
        self._btn_excel.clicked.connect(self._export_excel)
        bar.addWidget(self._btn_excel)

        self._btn_csv = QPushButton("⬇ CSV")
        self._btn_csv.setToolTip("导出当前可见字段为 CSV")
        self._btn_csv.clicked.connect(self._export_csv)
        bar.addWidget(self._btn_csv)

        self._btn_dwc = QPushButton("⬇ DwC")
        self._btn_dwc.setToolTip("导出 Darwin Core CSV（需项目数据库）")
        self._btn_dwc.clicked.connect(self._export_dwc)
        bar.addWidget(self._btn_dwc)

        self._btn_cross = QPushButton("🧮 跨项目汇总…")
        self._btn_cross.setToolTip("合并多个工作区导出标本/采集/质控汇总")
        self._btn_cross.clicked.connect(self._open_cross_summary)
        bar.addWidget(self._btn_cross)

        self._btn_result_ledger = QPushButton("🗂 成果台账")
        self._btn_result_ledger.setToolTip("查看所有已知项目的标本编号、TIF、ZIP 与入库关联状态")
        self._btn_result_ledger.clicked.connect(self._open_global_results)
        bar.addWidget(self._btn_result_ledger)

        bar.addSpacing(8)

        # Save to directory
        self._dir_input = QLineEdit()
        self._dir_input.setPlaceholderText("保存目录，如 /mnt/n/research")
        self._dir_input.setMinimumWidth(220)
        bar.addWidget(self._dir_input)

        self._btn_save = QPushButton("💾 保存")
        self._btn_save.setToolTip("保存 Excel 到指定目录")
        self._btn_save.clicked.connect(self._save_to_dir)
        bar.addWidget(self._btn_save)

        self._save_msg_lbl = QLabel("")
        self._save_msg_lbl.setStyleSheet(f"font-size:12px;color:{_C_ACCENT};")
        bar.addWidget(self._save_msg_lbl)

        bar.addStretch()

        self._count_lbl = QLabel("")
        self._count_lbl.setStyleSheet(f"font-size:12px;color:{_C_MUTED};")
        bar.addWidget(self._count_lbl)

        controls_layout.addLayout(bar)

        # ── Field picker panel (initially hidden) ──────────────────────────────
        self._picker = _FieldPicker(
            self._visible_keys,
            on_change=self._on_keys_changed,
            parent=controls_widget,
        )
        self._picker.setVisible(False)
        controls_layout.addWidget(self._picker)

        # Bottom border under controls area
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"background:{_C_BORDER};max-height:1px;border:none;")
        controls_layout.addWidget(sep)

        # Wrap controls in a scroll area so they never overlap the table
        controls_scroll = QScrollArea()
        controls_scroll.setWidget(controls_widget)
        controls_scroll.setWidgetResizable(True)
        controls_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        controls_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        controls_scroll.setFixedHeight(54)   # bar-only height; expands when picker opens
        controls_scroll.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed,
        )
        self._controls_scroll = controls_scroll
        root.addWidget(controls_scroll)

        self._dashboard = self._build_dashboard()
        root.addWidget(self._dashboard)

        root.addSpacing(10)

        # ── Table (takes all remaining space) ─────────────────────────────────
        self._table = QTableView()
        self._table.setAlternatingRowColors(True)
        self._table.setSelectionBehavior(QTableView.SelectionBehavior.SelectItems)
        self._table.keyPressEvent = self._table_key_press
        self._table.setEditTriggers(QTableView.EditTrigger.NoEditTriggers)
        self._table.setSortingEnabled(True)
        self._table.setWordWrap(False)
        self._table.verticalHeader().setVisible(False)
        self._table.verticalHeader().setDefaultSectionSize(24)
        self._table.horizontalHeader().setStretchLastSection(False)
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        from app.utils.tooltip_policy import suppress_popup_tooltip

        suppress_popup_tooltip(self._table)
        root.addWidget(self._table, stretch=1)

    def _build_dashboard(self) -> QFrame:
        frame = QFrame()
        frame.setObjectName("Dashboard")
        lay = QHBoxLayout(frame)
        lay.setContentsMargins(12, 8, 12, 8)
        lay.setSpacing(14)

        self._dash_values: dict[str, QLabel] = {}
        for key, name in (
            ("specimens", "标本"),
            ("alcohol", "酒精固定"),
            ("rna", "RNA"),
            ("photos", "JPG照片"),
            ("tiffs", "TIF成果"),
            ("unassigned", "未分配照片"),
            ("prints", "标签打印"),
        ):
            box = QWidget()
            box_l = QVBoxLayout(box)
            box_l.setContentsMargins(0, 0, 0, 0)
            box_l.setSpacing(1)
            val = QLabel("0")
            val.setObjectName("DashValue")
            lbl = QLabel(name)
            lbl.setObjectName("DashName")
            box_l.addWidget(val)
            box_l.addWidget(lbl)
            lay.addWidget(box)
            self._dash_values[key] = val

        lay.addSpacing(10)
        self._dash_people = QLabel("拍摄人：-    采集人：-    标签打印：-")
        self._dash_people.setObjectName("DashPeople")
        self._dash_people.setWordWrap(True)
        lay.addWidget(self._dash_people, stretch=1)
        return frame

    def _table_key_press(self, event) -> None:
        from PyQt6.QtGui import QKeySequence
        from PyQt6.QtWidgets import QApplication, QTableView
        if event.matches(QKeySequence.StandardKey.Copy):
            indexes = self._table.selectionModel().selectedIndexes()
            if indexes:
                indexes = sorted(indexes, key=lambda i: (i.row(), i.column()))
                rows: dict = {}
                for idx in indexes:
                    rows.setdefault(idx.row(), []).append(idx)
                lines = []
                for row_idxs in rows.values():
                    parts = [str(self._table.model().data(i) or "") for i in sorted(row_idxs, key=lambda x: x.column())]
                    lines.append("\t".join(parts))
                QApplication.clipboard().setText("\n".join(lines))
            return
        QTableView.keyPressEvent(self._table, event)

    def on_activate(self) -> None:
        """Called every time user navigates to this view."""
        self._load_data()

    # ── Data loading ──────────────────────────────────────────────────────────

    def _load_data(self) -> None:
        """Load specimens + grouping data for the selected summary scope."""
        self._specimens = []
        self._grouping = {}
        self._projects = []
        self._loaded_project_dirs = []
        self._loaded_root = ""
        self._dashboard_snapshot = None

        if self._summary_scope in (_SCOPE_ALL, _SCOPE_SELECTED):
            # §7 旧: if self._load_multi_project_data(): ... —— 「全部/选中项目」
            # 在主线程同步遍历所有项目逐库查询, 多野外季项目假死数秒且零反馈。
            # 现改 worker 派发; 失败/无目录时异步回落当前项目路径。
            if self._dispatch_multi_project_load():
                return

        self._load_current_project_data()
        self._rebuild_filter_combo()
        self._rebuild_table()

    def _dispatch_multi_project_load(self) -> bool:
        """多项目汇总入 worker; 返回 False = 无可扫目录(走当前项目回落)."""
        dirs, root = self._summary_dirs_for_scope()
        if not dirs:
            return False
        token = getattr(self, "_load_token", 0) + 1
        self._load_token = token
        self._stop_summary_load_worker(wait_ms=200)

        try:
            self._count_lbl.setText(f"加载中… ({len(dirs)} 个项目)")
        except Exception:
            pass

        worker = _SummaryLoadWorker(dirs, root, parent=self)
        worker.finished_payload.connect(
            lambda payload, t=token, d=list(dirs), r=root:
                self._on_multi_load_done(t, d, r, payload)
        )
        worker.failed.connect(lambda _msg, t=token: self._on_multi_load_failed(t))
        self._load_worker = worker
        worker.start()
        return True

    def _on_multi_load_done(
        self, token: int, dirs: list[str], root: str, payload: dict
    ) -> None:
        if token != getattr(self, "_load_token", 0):
            return
        self._apply_multi_payload(payload, dirs, root)
        self._rebuild_filter_combo()
        self._rebuild_table()

    def _on_multi_load_failed(self, token: int) -> None:
        if token != getattr(self, "_load_token", 0):
            return
        # 与旧同步路径的 return False 同款回落: 当前项目数据
        self._load_current_project_data()
        self._rebuild_filter_combo()
        self._rebuild_table()

    def _stop_summary_load_worker(self, wait_ms: int = 200) -> None:
        worker = getattr(self, "_load_worker", None)
        if worker is None:
            return
        try:
            if worker.isRunning() and not worker.wait(wait_ms):
                for sig in ("finished_payload", "failed"):
                    try:
                        getattr(worker, sig).disconnect()
                    except Exception:
                        pass
                try:
                    worker.setParent(None)
                except Exception:
                    pass
                _RETIRED_LOAD_WORKERS.append(worker)
        except Exception:
            pass
        self._load_worker = None

    def stop_background_work(self) -> None:
        self._stop_summary_load_worker(wait_ms=2000)

    def _load_current_project_data(self) -> None:
        """Load the current open DB. Kept separate for in-memory test DBs."""
        db: Optional[sqlite3.Connection] = self.ctx.get_db()
        if db is None:
            return

        # Load specimens
        try:
            rows = db.execute(
                "SELECT * FROM specimens ORDER BY uid"
            ).fetchall()
            self._specimens = [Specimen.from_row(r) for r in rows]
        except Exception:
            self._specimens = []

        # Load projects (name, projectCode) from projects table if present
        # Maps directory → project code string for projCode column
        dir_to_proj: dict[str, dict] = {}
        try:
            p_rows = db.execute(
                "SELECT directory, name, project_code FROM projects"
            ).fetchall()
            for pr in p_rows:
                d = dict(pr)
                if d.get("directory"):
                    dir_to_proj[d["directory"]] = d
                    self._projects.append(d)
        except Exception:
            pass  # projects table absent — projCode will be empty

        # Load grouping compact: count/status plus JPG/TIF/ZIP facts per uid.
        try:
            from app.services import project_summary_service as pss
            self._grouping = pss.collect_grouping_summary_for_connection(db, "")
        except Exception:
            self._grouping = {}

        # Inject projCode into grouping records from owner_project_dir lookup
        for sp in self._specimens:
            uid = sp.uid or ""
            proj_info = dir_to_proj.get(sp.owner_project_dir or "")
            code = (proj_info or {}).get("project_code") or ""
            if uid not in self._grouping:
                self._grouping[uid] = _empty_grouping(code)
            self._grouping[uid]["projCode"] = code
            row_key = self._grouping_key_for_specimen(sp)
            self._grouping[row_key] = dict(self._grouping[uid])

        current_dir = self._current_project_dir()
        if current_dir:
            self._loaded_project_dirs = [current_dir]
            self._loaded_root = current_dir

    def _load_multi_project_data(self) -> bool:
        """同步多项目加载(兼容保留; 生产路径已改 _dispatch_multi_project_load)."""
        dirs, root = self._summary_dirs_for_scope()
        if not dirs:
            return False
        try:
            from app.services import project_summary_service as pss
            payload = pss.collect_specimen_summary_rows(dirs, root)
        except Exception:
            return False
        self._apply_multi_payload(payload, list(dirs), root)
        return True

    def _apply_multi_payload(
        self, payload: dict, dirs: list[str], root: str
    ) -> None:
        self._specimens = list(payload.get("specimens") or [])
        self._grouping = dict(payload.get("grouping") or {})
        self._projects = list(payload.get("projects") or [])
        self._dashboard_snapshot = payload.get("dashboard") or None
        self._loaded_project_dirs = list(dirs)
        self._loaded_root = root

    def _current_project_dir(self) -> str:
        raw = getattr(self.ctx, "current_project_dir", "") or getattr(
            self.ctx, "current_project_root", ""
        )
        if not isinstance(raw, str) or not raw:
            return ""
        try:
            return str(Path(raw).resolve())
        except OSError:
            return raw

    @staticmethod
    def _resolve_dir(path: str) -> str:
        try:
            return str(Path(path).resolve())
        except OSError:
            return str(path)

    @staticmethod
    def _common_root(dirs: list[str]) -> str:
        resolved = [SummaryView._resolve_dir(d) for d in dirs if d]
        if not resolved:
            return ""
        if len(resolved) == 1:
            return str(Path(resolved[0]).parent)
        try:
            return os.path.commonpath(resolved)
        except Exception:
            return str(Path(resolved[0]).parent)

    def _known_summary_projects(self) -> list[dict]:
        seen: set[str] = set()
        out: list[dict] = []

        def add_unique_summary_project(
            directory: str, name: str = "", project_code: str = ""
        ) -> None:
            if not directory:
                return
            resolved = self._resolve_dir(directory)
            if resolved in seen:
                return
            seen.add(resolved)
            out.append({
                "directory": resolved,
                "name": name or Path(resolved).name or resolved,
                "project_code": project_code,
            })

        current = self._current_project_dir()
        if current:
            add_unique_summary_project(current, "当前项目")

        try:
            from app.services import project_service
            recent = project_service.list_projects(
                project_service.default_user_projects_json_path()
            )
        except Exception:
            recent = []
        for project in recent:
            directory = project.get("directory") or project.get("dir") or ""
            add_unique_summary_project(
                directory,
                str(project.get("name") or ""),
                str(project.get("project_code") or project.get("projectCode") or ""),
            )

        for directory in self._selected_project_dirs:
            add_unique_summary_project(directory)
        return out

    def _summary_dirs_for_scope(self) -> tuple[list[str], str]:
        if self._summary_scope == _SCOPE_ALL:
            dirs = [p["directory"] for p in self._known_summary_projects()]
        elif self._summary_scope == _SCOPE_SELECTED:
            dirs = [self._resolve_dir(d) for d in self._selected_project_dirs if d]
            if not dirs:
                current = self._current_project_dir()
                dirs = [current] if current else []
        else:
            current = self._current_project_dir()
            dirs = [current] if current else []

        deduped: list[str] = []
        seen: set[str] = set()
        for directory in dirs:
            if directory and directory not in seen:
                deduped.append(directory)
                seen.add(directory)
        return deduped, self._common_root(deduped)

    def _grouping_key_for_specimen(self, sp: Specimen) -> str:
        return f"{sp.owner_project_dir or ''}{_GROUPING_KEY_SEP}{sp.uid or ''}"

    def _grouping_for_specimen(self, sp: Specimen) -> dict:
        default = _empty_grouping("")
        return (
            self._grouping.get(self._grouping_key_for_specimen(sp))
            or self._grouping.get(sp.uid or "")
            or default
        )

    def _rebuild_filter_combo(self) -> None:
        """Rebuild the project filter combo from current specimens.

        Prefers project name from the projects table (if loaded);
        falls back to the last path component of owner_project_dir.
        """
        self._filter_combo.blockSignals(True)
        self._filter_combo.clear()
        self._filter_combo.addItem("全部项目", "")

        # Build dir → display-name map (projects table wins, then path fallback)
        dir_to_name: dict[str, str] = {}
        for p in self._projects:
            if p.get("directory"):
                label = p.get("name") or Path(p["directory"]).name or p["directory"]
                dir_to_name[p["directory"]] = label

        # Collect unique project dirs from specimens (preserves encounter order)
        seen: dict[str, str] = {}
        for sp in self._specimens:
            if sp.owner_project_dir and sp.owner_project_dir not in seen:
                name = dir_to_name.get(
                    sp.owner_project_dir,
                    Path(sp.owner_project_dir).name or sp.owner_project_dir,
                )
                seen[sp.owner_project_dir] = name

        for proj_dir, name in seen.items():
            self._filter_combo.addItem(name, proj_dir)

        # Restore selection
        idx = self._filter_combo.findData(self._project_filter)
        if idx >= 0:
            self._filter_combo.setCurrentIndex(idx)
        else:
            self._filter_combo.setCurrentIndex(0)
            self._project_filter = ""

        self._filter_combo.blockSignals(False)

    # ── Table rebuild ─────────────────────────────────────────────────────────

    def _filtered_specimens(self) -> list[Specimen]:
        specs = self._specimens
        if self._project_filter:
            specs = [s for s in specs if s.owner_project_dir == self._project_filter]
        if self._date_field in ("采集日期", "拍照日期"):
            specs = [s for s in specs if self._match_date(s)]
        q = self._search_text.strip().lower()
        if q:
            specs = [s for s in specs if self._match_search(s, q)]
        return list(specs)

    def _match_date(self, sp: Specimen) -> bool:
        value = sp.collection_date if self._date_field == "采集日期" else sp.photo_date
        return _date_in_range(value, self._date_from, self._date_to)

    def _match_search(self, sp: Specimen, q: str) -> bool:
        """Substring match across every column value (case-insensitive)."""
        g = self._grouping_for_specimen(sp)
        for col in ALL_COLS:
            try:
                val = col["get"](sp, g)
            except Exception:
                val = ""
            if val and q in str(val).lower():
                return True
        return False

    def _rebuild_table(self) -> None:
        """Build QStandardItemModel from filtered specimens + visible columns."""
        # Map key → col definition
        key_to_col = {c["key"]: c for c in ALL_COLS}
        vis_cols = [key_to_col[k] for k in self._visible_keys if k in key_to_col]
        if not vis_cols:
            vis_cols = [key_to_col[k] for k in _DEFAULT_KEYS[:5] if k in key_to_col]

        specs = self._filtered_specimens()

        model = QStandardItemModel(len(specs), len(vis_cols))
        model.setHorizontalHeaderLabels([c["label"] for c in vis_cols])

        # Theme colours as QBrush
        teal_brush   = QBrush(QColor(_C_ACCENT))
        yellow_brush = QBrush(QColor(_C_WARN))
        red_brush    = QBrush(QColor(_C_ERR))
        muted_brush  = QBrush(QColor(_C_INACTIVE_TXT))
        text_brush   = QBrush(QColor(_C_TEXT))

        for row_idx, sp in enumerate(specs):
            g = self._grouping_for_specimen(sp)
            for col_idx, col in enumerate(vis_cols):
                try:
                    val = col["get"](sp, g)
                except Exception:
                    val = ""
                item = QStandardItem(str(val) if val is not None else "")
                item.setEditable(False)

                # Status colouring (mirrors web renderSummaryPage)
                if col["key"] == "compStatus":
                    if val == "已合成":
                        item.setForeground(teal_brush)
                    elif val == "部分合成":
                        item.setForeground(yellow_brush)
                    elif val == "待合成":
                        item.setForeground(red_brush)
                    else:
                        item.setForeground(muted_brush)
                elif col["key"] == "taxoOk":
                    item.setForeground(teal_brush if val == "✓" else muted_brush)
                elif col["key"] == "rna":
                    item.setForeground(yellow_brush if val == "✓" else muted_brush)
                else:
                    item.setForeground(text_brush)

                model.setItem(row_idx, col_idx, item)

        proxy = QSortFilterProxyModel()
        proxy.setSourceModel(model)
        proxy.setFilterCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        proxy.setFilterKeyColumn(-1)

        self._model = model
        self._proxy = proxy
        sorting = self._table.isSortingEnabled()
        self._table.setSortingEnabled(False)
        self._table.setModel(proxy)
        cell_count = len(specs) * max(1, len(vis_cols))
        if cell_count <= _TABLE_AUTOSIZE_CELL_LIMIT:
            self._table.resizeColumnsToContents()
        else:
            self._apply_fast_column_widths(vis_cols)
        self._table.setSortingEnabled(sorting)

        n = len(specs)
        self._count_lbl.setText(f"{n} 条 · {len(vis_cols)} 列")
        self._refresh_dashboard(specs)

    def _apply_fast_column_widths(self, columns: list[dict]) -> None:
        widths = {
            "uid": 180,
            "nameLat": 180,
            "nameCn": 120,
            "geoArea": 180,
            "notes": 180,
            "photoNotes": 180,
            "collector": 110,
            "photographer": 110,
            "identifier": 110,
            "tiffCount": 70,
            "jpgCount": 90,
            "zipCount": 70,
            "tiffNames": 340,
            "zipNames": 220,
            "compStatus": 90,
        }
        for idx, col in enumerate(columns):
            self._table.setColumnWidth(idx, widths.get(col["key"], 105))

    def _refresh_dashboard(self, specs: Optional[list[Specimen]] = None) -> None:
        specs = list(specs if specs is not None else self._filtered_specimens())
        totals = {
            "specimens": len(specs),
            "alcohol": 0,
            "rna": 0,
            "photos": 0,
            "tiffs": 0,
            "unassigned": 0,
            "prints": 0,
        }
        photographers: Counter[str] = Counter()
        collectors: Counter[str] = Counter()
        identifiers: Counter[str] = Counter()
        label_printers: Counter[str] = Counter()

        for sp in specs:
            storage = (sp.storage or "").strip()
            if _is_rna(storage):
                totals["rna"] += 1
            elif storage:
                totals["alcohol"] += 1
            photographers[self._person_key(sp.photographer)] += 1
            collectors[self._person_key(sp.collector)] += 1
            identifiers[self._person_key(sp.identifier)] += 1
            g = self._grouping_for_specimen(sp)
            totals["tiffs"] += int(g.get("tiff_count") or 0)

        dashboard = self._dashboard_snapshot
        if dashboard:
            dt = dashboard.get("totals", {})
            totals["photos"] = int(dt.get("photo_count") or 0)
            totals["unassigned"] = int(dt.get("unassigned_photo_count") or 0)
            totals["prints"] = int(dt.get("label_print_count") or 0)
            for row in dashboard.get("by_person", {}).get("label_printer", []):
                label_printers[self._person_key(row.get("name"))] += int(row.get("count") or 0)
        else:
            db = self._safe_db()
            if db is not None:
                try:
                    row = db.execute(
                        """
                        SELECT COUNT(*) AS total,
                               SUM(CASE WHEN current_assignment_id IS NULL THEN 1 ELSE 0 END) AS unassigned
                          FROM photos
                        """
                    ).fetchone()
                    totals["photos"] = int(row["total"] or 0) if row else 0
                    totals["unassigned"] = int(row["unassigned"] or 0) if row else 0
                except Exception:
                    pass

                try:
                    rows = db.execute(
                        "SELECT actor, SUM(label_count) AS labels FROM label_print_events GROUP BY actor"
                    ).fetchall()
                    for row in rows:
                        n = int(row["labels"] or 0)
                        totals["prints"] += n
                        label_printers[self._person_key(row["actor"])] += n
                except Exception:
                    pass

        for key, val in totals.items():
            if key in self._dash_values:
                self._dash_values[key].setText(str(val))
        self._dash_people.setText(
            "    ".join([
                f"拍摄人：{self._format_people(photographers)}",
                f"采集人：{self._format_people(collectors)}",
                f"鉴定人：{self._format_people(identifiers)}",
                f"标签打印：{self._format_people(label_printers)}",
            ])
        )

    @staticmethod
    def _person_key(name: Optional[str]) -> str:
        value = str(name or "").strip()
        return value or "未填写"

    @staticmethod
    def _format_people(counter: Counter[str], limit: int = 3) -> str:
        if not counter:
            return "-"
        return "，".join(f"{name}{count}" for name, count in counter.most_common(limit))

    def _safe_db(self) -> Optional[sqlite3.Connection]:
        try:
            return self.ctx.get_db()
        except Exception:
            return None


    # ── UI event handlers ────────────────────────────────────────────────────

    def _on_filter_changed(self, idx: int) -> None:
        self._project_filter = self._filter_combo.currentData() or ""
        self._rebuild_table()

    def _on_scope_changed(self, idx: int) -> None:
        scope = self._scope_combo.currentData() or _SCOPE_CURRENT
        if scope == self._summary_scope:
            return
        if scope == _SCOPE_SELECTED and not self._selected_project_dirs:
            current = self._current_project_dir()
            self._selected_project_dirs = [current] if current else []
        self._summary_scope = scope
        self._project_filter = ""
        self._load_data()

    def _open_summary_sources(self) -> None:
        from app.widgets.summary_source_dialog import SummarySourceDialog

        dirs, _root = self._summary_dirs_for_scope()
        selected = dirs if self._summary_scope != _SCOPE_CURRENT else (
            self._selected_project_dirs or dirs
        )
        dlg = SummarySourceDialog(
            self._known_summary_projects(),
            selected_dirs=selected,
            parent=self,
        )
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        self._selected_project_dirs = dlg.selected_dirs()
        self._summary_scope = _SCOPE_SELECTED
        self._project_filter = ""
        idx = self._scope_combo.findData(_SCOPE_SELECTED)
        if idx >= 0:
            self._scope_combo.blockSignals(True)
            self._scope_combo.setCurrentIndex(idx)
            self._scope_combo.blockSignals(False)
        self._load_data()

    # ── Search ────────────────────────────────────────────────────────────────

    def _on_search_changed(self, text: str) -> None:
        self._search_text = text or ""
        self._rebuild_table()

    # Alias kept for callers/tests that prefer the imperative name.
    def _apply_search(self, text: str) -> None:
        self._on_search_changed(text)

    # ── Date-range filter ─────────────────────────────────────────────────────

    def _apply_date_filter(self, field: str, frm, to) -> None:
        """Programmatic entry point: set the active date filter and rebuild.

        ``field`` ∈ {不限日期, 采集日期, 拍照日期}; ``frm``/``to`` are ISO
        bounds ("" / None = open). Widgets are synced (signals blocked) so the
        UI mirrors the state.
        """
        self._date_field = field or "不限日期"
        self._date_from = _norm_date(frm)
        self._date_to = _norm_date(to)
        self._sync_date_widgets()
        self._rebuild_table()

    def _sync_date_widgets(self) -> None:
        for w in (self._date_field_combo, self._date_from_edit, self._date_to_edit):
            w.blockSignals(True)
        try:
            idx = self._date_field_combo.findText(self._date_field)
            if idx >= 0:
                self._date_field_combo.setCurrentIndex(idx)
            if self._date_from:
                self._date_from_edit.setDate(QDate.fromString(self._date_from, "yyyy-MM-dd"))
            if self._date_to:
                self._date_to_edit.setDate(QDate.fromString(self._date_to, "yyyy-MM-dd"))
        finally:
            for w in (self._date_field_combo, self._date_from_edit, self._date_to_edit):
                w.blockSignals(False)

    def _data_date_bounds(self, field: str) -> tuple:
        """Min/max ISO date across project-filtered specimens for ``field``."""
        attr = "collection_date" if field == "采集日期" else "photo_date"
        vals = []
        base = self._specimens
        if self._project_filter:
            base = [s for s in base if s.owner_project_dir == self._project_filter]
        for s in base:
            v = _norm_date(getattr(s, attr, "") or "")
            if v:
                vals.append(v)
        if not vals:
            return ("", "")
        return (min(vals), max(vals))

    def _on_date_field_changed(self, field: str) -> None:
        if field not in ("采集日期", "拍照日期"):
            self._date_field = "不限日期"
            self._date_from = ""
            self._date_to = ""
            self._rebuild_table()
            return
        # Selecting a field: default the range to the data's own min/max so the
        # initial view shows everything, then the user narrows it.
        lo, hi = self._data_date_bounds(field)
        self._apply_date_filter(field, lo, hi)

    def _on_date_edit_changed(self, _qdate) -> None:
        if self._date_field not in ("采集日期", "拍照日期"):
            # "不限日期"下直接选日期 → 自动启用按采集日期筛选（点击必须有反馈）。
            # 未动过的另一端用数据自身边界兜底，避免范围倒挂筛出空表。
            chosen_from = self._date_from_edit.date().toString("yyyy-MM-dd")
            chosen_to = self._date_to_edit.date().toString("yyyy-MM-dd")
            lo, hi = self._data_date_bounds("采集日期")
            if self.sender() is self._date_to_edit:
                self._apply_date_filter("采集日期", lo or chosen_from, chosen_to)
            else:
                self._apply_date_filter("采集日期", chosen_from, hi or chosen_to)
            return
        self._date_from = self._date_from_edit.date().toString("yyyy-MM-dd")
        self._date_to = self._date_to_edit.date().toString("yyyy-MM-dd")
        self._rebuild_table()

    def _on_preset_selected(self, idx: int) -> None:
        name_map = {1: "today", 2: "7d", 3: "30d", 4: "year", 5: "all"}
        preset = name_map.get(idx)
        if preset is None:  # "快捷" placeholder
            return
        lo, hi = _preset_range(preset, _date.today())
        if preset == "all":
            # Keep the current field but clear bounds (show all dated rows).
            field = self._date_field if self._date_field in ("采集日期", "拍照日期") else "采集日期"
            self._apply_date_filter(field, "", "")
        else:
            field = self._date_field if self._date_field in ("采集日期", "拍照日期") else "采集日期"
            self._apply_date_filter(field, lo, hi)
        # reset placeholder so the same preset can be re-picked
        self._preset_combo.blockSignals(True)
        self._preset_combo.setCurrentIndex(0)
        self._preset_combo.blockSignals(False)

    def _toggle_picker(self, checked: bool) -> None:
        self._picker_open = checked
        self._picker.setVisible(checked)
        # Expand/contract the controls scroll area height to accommodate the picker
        # Bar-only ≈ 54 px; bar + picker ≈ 200 px (picker has max-height:120 + header)
        self._controls_scroll.setFixedHeight(200 if checked else 54)

    def _on_keys_changed(self, new_keys: list[str]) -> None:
        self._visible_keys = new_keys
        self._picker.set_visible_keys(new_keys)
        self._rebuild_table()

    # ── Export: Excel ─────────────────────────────────────────────────────────

    def _export_excel(self) -> None:
        specs = self._filtered_specimens()
        if not specs:
            QMessageBox.information(self, "导出 Excel", "当前视图没有标本数据。")
            return
        path, _ = QFileDialog.getSaveFileName(
            self,
            "导出 Excel",
            f"标本数据_{_date.today().isoformat()}.xlsx",
            "Excel 文件 (*.xlsx)",
        )
        if not path:
            return
        try:
            from app.services.export_service import export_excel
            from app.utils import ui as _ui
            with _ui.busy_cursor():  # 大表写盘期间给出忙反馈(完整异步导出待后续)
                out = export_excel(specs, path)
            QMessageBox.information(self, "导出成功", f"已保存到：\n{out}")
        except Exception as exc:
            QMessageBox.critical(self, "导出失败", str(exc))

    # ── Export: CSV (current visible cols + filter) ──────────────────────────

    def _export_csv(self) -> None:
        specs = self._filtered_specimens()
        if not specs:
            QMessageBox.information(self, "导出 CSV", "当前视图没有标本数据。")
            return

        path, _ = QFileDialog.getSaveFileName(
            self,
            "导出 CSV",
            f"标本汇总_{_date.today().isoformat()}.csv",
            "CSV 文件 (*.csv)",
        )
        if not path:
            return

        key_to_col = {c["key"]: c for c in ALL_COLS}
        vis_cols = [key_to_col[k] for k in self._visible_keys if k in key_to_col]

        try:
            with open(path, "w", newline="", encoding="utf-8-sig") as f:
                writer = csv.writer(f)
                writer.writerow([c["label"] for c in vis_cols])
                for sp in specs:
                    g = self._grouping_for_specimen(sp)
                    row = []
                    for col in vis_cols:
                        try:
                            val = col["get"](sp, g)
                        except Exception:
                            val = ""
                        row.append(str(val) if val is not None else "")
                    writer.writerow(row)
            QMessageBox.information(self, "导出成功", f"已保存到：\n{path}")
        except Exception as exc:
            QMessageBox.critical(self, "导出失败", str(exc))

    # ── Export: Darwin Core CSV ───────────────────────────────────────────────

    def _export_dwc(self) -> None:
        """Export Darwin Core CSV using the export_service.export_darwin_core function.

        Requires an open project DB with the darwin_core view.
        Oracle: export_service.export_darwin_core (mirrors DwC export in server.js).
        """
        db = self.ctx.get_db()
        if db is None:
            QMessageBox.information(
                self, "导出 DwC",
                "当前没有打开的项目数据库。\n请先进入一个工作区项目，再导出 Darwin Core 数据。",
            )
            return

        path, _ = QFileDialog.getSaveFileName(
            self,
            "导出 Darwin Core CSV",
            f"darwin_core_{_date.today().isoformat()}.csv",
            "CSV 文件 (*.csv)",
        )
        if not path:
            return

        try:
            from app.services.export_service import export_darwin_core
            out = export_darwin_core(db, path)
            QMessageBox.information(self, "导出成功", f"Darwin Core CSV 已保存到：\n{out}")
        except Exception as exc:
            QMessageBox.critical(self, "导出失败", str(exc))

    # ── Cross-workspace summary (append-only launcher) ─────────────────────────

    def _open_cross_summary(self) -> None:
        """Open the cross-workspace summary export dialog (Mode B default)."""
        from app.widgets.summary_export_dialog import SummaryExportDialog
        dlg = SummaryExportDialog(ctx=self.ctx, parent=self)
        dlg.exec()

    def _open_global_results(self) -> None:
        """Open the global TIFF/ZIP ledger dialog."""
        from app.widgets.global_results_dialog import GlobalResultsDialog
        dlg = GlobalResultsDialog(ctx=self.ctx, parent=self)
        dlg.exec()

    # ── Save to directory ─────────────────────────────────────────────────────

    def _save_to_dir(self) -> None:
        dir_path = self._dir_input.text().strip()
        if not dir_path:
            QMessageBox.warning(self, "保存", "请先填写保存目录路径。")
            return

        target = Path(dir_path)
        if not target.exists():
            try:
                target.mkdir(parents=True, exist_ok=True)
            except Exception as exc:
                self._save_msg_lbl.setStyleSheet(f"font-size:12px;color:{_C_ERR};")
                self._save_msg_lbl.setText(f"✗ 无法创建目录：{exc}")
                return

        specs = self._filtered_specimens()
        fname = f"标本数据_{_date.today().isoformat()}.xlsx"
        out_path = str(target / fname)

        self._btn_save.setEnabled(False)
        self._btn_save.setText("保存中…")

        try:
            from app.services.export_service import export_excel
            export_excel(specs, out_path)
            self._save_msg_lbl.setStyleSheet(f"font-size:12px;color:{_C_ACCENT};")
            self._save_msg_lbl.setText(f"✓ 已保存：{out_path}")
        except Exception as exc:
            self._save_msg_lbl.setStyleSheet(f"font-size:12px;color:{_C_ERR};")
            self._save_msg_lbl.setText(f"✗ 失败：{exc}")
        finally:
            self._btn_save.setEnabled(True)
            self._btn_save.setText("💾 保存")
