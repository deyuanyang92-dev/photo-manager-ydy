"""taxonomy_view.py — Taxonomy library management view.

Faithful PyQt6 replica of the web «内置分类库» page.

Web oracle:
  - pages_dom.json "内置分类库" section (all controls/layout keys)
  - styles.css  .taxon-table-* classes (line 5651 ff.)
  - app.js renderTaxonomyPage() (line 12060 ff.)
  - taxonomy_service.py (seed read-only + user CRUD)

view_id   = "taxonomy"
nav_title = "内置分类库"
nav_icon  = "🧬"
"""
from __future__ import annotations

from typing import Any, Optional

from PyQt6.QtWidgets import QDialog, QFileDialog, QMenu, QMessageBox

from app.services.taxonomy_service import TaxonomyService
from app.services.worms_service import WormsService
from app.views import taxonomy_view_support as _tv
from app.views.base_view import BaseView
from app.views.taxonomy_dialogs import (
    _HistoryDialog,
    _RecordDialog,
    _TaxonFacetPanel,
    _TaxonReviewDialog,
    _WormsMatchDialog,
)
from app.views.taxonomy_layout import TaxonomyLayoutMixin
from app.views.taxonomy_lifecycle import TaxonomyLifecycleMixin
from app.views.taxonomy_records_workflow import TaxonomyRecordsWorkflowMixin
from app.views.taxonomy_table_model import (
    _ActionDelegate,
    _ChipButton,
    _TaxonTableModel,
    _ViewTabButton,
)
from app.views.taxonomy_table_workflow import TaxonomyTableWorkflowMixin
from app.views.taxonomy_workers import _WormsJobWorker, _WormsSearchWorker
from app.views.taxonomy_worms_workflow import TaxonomyWormsWorkflowMixin

if False:  # TYPE_CHECKING
    from app.app_context import AppContext

# Compatibility exports for tests and older internal imports that reached into
# this module before the view was split.
_refresh_palette = _tv._refresh_palette
_COL_CHECK = _tv._COL_CHECK
_COL_NUM = _tv._COL_NUM
_COL_DATA_START = _tv._COL_DATA_START
_DIALOG_FIELDS = _tv._DIALOG_FIELDS
_PAGE_SIZE = _tv._PAGE_SIZE
_ALL_COLS = _tv._ALL_COLS
_LEVEL_CHIPS = _tv._LEVEL_CHIPS
_LANG_CHIPS = _tv._LANG_CHIPS
_DEFAULT_SEED_PATH = _tv._DEFAULT_SEED_PATH
_DEFAULT_USER_PATH = _tv._DEFAULT_USER_PATH
_PROJECT_ROOT = _tv._PROJECT_ROOT
_DATA_DIR = _tv._DATA_DIR

_COMPAT_SUPPORT_NAMES = {
    "_C_PANEL", "_C_INPUT", "_C_TEXT", "_C_TEXT_SOFT", "_C_MUTED", "_C_DIM",
    "_C_ACCENT", "_C_ACCENT_HI", "_C_DANGER", "_C_BORDER", "_C_ACCENT_SOFT",
    "_C_DANGER_SOFT",
}


def __getattr__(name: str) -> Any:
    if name in _COMPAT_SUPPORT_NAMES:
        return getattr(_tv, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

# ── Main view ─────────────────────────────────────────────────────────────────

class TaxonomyView(
    TaxonomyLayoutMixin,
    TaxonomyLifecycleMixin,
    TaxonomyTableWorkflowMixin,
    TaxonomyWormsWorkflowMixin,
    TaxonomyRecordsWorkflowMixin,
    BaseView,
):
    """Taxonomy library management view (view_id="taxonomy").

    Faithful replica of the web 内置分类库 page.

    Layout (matches DOM):

        [QScrollArea — controls area, prevents top from squishing table]
          taxon-table-header
            taxon-table-title-row  (h2 + stats + taxon-view-switch + 图表-btn)
            taxon-col-controls     (类群 chips + 语言 chips)  [original only]
          taxon-table-toolbar
            taxon-table-filter-bar (col-select + search-input + 搜索 + 清除)
            taxon-table-actions    (+ 新增 | selection note | 全选 | 取消 |
                                    WoRMS更新所选 | WoRMS更新筛选 | 导出Excel |
                                    导出CSV | 导入Excel/CSV)
        taxon-table-wrap
          QTableView  (☑ # <dynamic cols> 来源 操作)
          [loading overlay label when _loading=True]
        taxon-table-pager
          上一页 | 第N/M页（共K条） | 跳到 [spin] | 下一页  | footer stats
    """

    view_id = "taxonomy"
    nav_title = "内置分类库"
    nav_icon = "🧬"

    def __init__(self, ctx: "AppContext") -> None:
        # View state — mirrors state.taxonTable in app.js
        self._view: str = "original"          # "original" | "worms" | "compare"
        self._show_chart: bool = False
        self._page: int = 1
        self._total: int = 0
        self._selected_ids: list[str] = []
        self._select_all_filtered: bool = False
        self._filter_col: str = ""
        self._filter_text: str = ""
        self._loading: bool = False
        self._svc: Optional[TaxonomyService] = None
        # New state for facet/sort/WoRMS
        self._worms_svc: Optional[WormsService] = None
        self._job_worker: Optional["_WormsJobWorker"] = None
        self._chart_dialog: Optional[QDialog] = None
        self._col_filters: dict[str, Optional[dict[str, Any]]] = {}
        self._sort_col: str = ""
        self._sort_dir: str = "asc"
        self._facet_panel: Optional[_TaxonFacetPanel] = None
        super().__init__(ctx)
