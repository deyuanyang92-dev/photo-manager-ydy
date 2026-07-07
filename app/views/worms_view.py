"""WoRMS view compatibility facade.

The implementation is split by interface depth:
- worms_view_support: dynamic palette and tiny style helpers
- worms_workers: QThread worker adapters
- worms_detail_widgets: result rows and detail panel
- worms_dialogs: manual match and quick-fill dialogs
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional, TYPE_CHECKING

from PyQt6.QtCore import Qt, QThread, QTimer
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QLineEdit, QListWidget, QListWidgetItem, QSplitter,
    QGroupBox, QScrollArea, QFrame, QCheckBox,
    QMessageBox, QProgressBar,
)

from app.services.worms_service import WormsService
from app.views import worms_view_support as _wv
from app.views.base_view import BaseView
from app.views.worms_detail_widgets import (
    _DetailPanel,
    _ResultItemWidget,
    _build_children_tab,
    _build_overview_tab,
    _build_synonyms_tab,
    _chain_node_widget,
)
from app.views.worms_dialogs import WormsMatchDialog, WormsQuickFillDialog
from app.views.worms_workers import (
    _DetailWorker,
    _LoadMoreWorker,
    _MatchChainWorker,
    _MatchSearchWorker,
    _QuickSearchWorker,
    _SearchWorker,
)

if TYPE_CHECKING:
    from app.app_context import AppContext

_label = _wv._label
_badge = _wv._badge
_divider = _wv._divider
_refresh_palette = _wv._refresh_palette
_rgb_tuple = _wv._rgb_tuple
_rgba_tint = _wv._rgba_tint
_FALLBACK_DATA_DIR = _wv._FALLBACK_DATA_DIR

_DYNAMIC_SUPPORT_NAMES = {
    '_C_ACCENT',
    '_C_ACCENT_06',
    '_C_ACCENT_08',
    '_C_ACCENT_12',
    '_C_ACCENT_15',
    '_C_ACCENT_20',
    '_C_ACCENT_22',
    '_C_ACCENT_28',
    '_C_ACCENT_30',
    '_C_ACCENT_H',
    '_C_BG',
    '_C_BORDER',
    '_C_BORDER_10',
    '_C_BORDER_20',
    '_C_BORDER_25',
    '_C_DANGER',
    '_C_DANGER_15',
    '_C_DIM',
    '_C_INPUT',
    '_C_MUTED',
    '_C_PANEL',
    '_C_RUNNING',
    '_C_SUCCESS',
    '_C_SUCCESS_15',
    '_C_TEXT',
    '_C_WARN',
    '_C_WARN_25',
    '_MONO',
    '_SANS',
    '_SERIF',
}


def __getattr__(name: str):
    if name in _DYNAMIC_SUPPORT_NAMES:
        return getattr(_wv, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


class WormsView(BaseView):
    """WoRMS 分类库 page.

    Faithfully reproduces the web prototype layout:

        worms-header
            worms-title-row  h2[serif] + marinespecies.org link
            worms-desc

        worms-body  (QSplitter horizontal, left:right ≈ 6:4)
            worms-search-panel (left, scrollable)
                worms-search-bar  [mono input] [like checkbox] [搜索]
                loading / error / empty state
                worms-result-list  (custom _ResultItemWidget per row)

            worms-detail-panel (right, _DetailPanel)
                worms-fill-btn at bottom (web parity)

        Batch jobs (collapsible QGroupBox footer — not in web page but
        kept for parity with worms_service job management).

    Outer QScrollArea prevents content squashing when window is short.
    """

    view_id   = "worms"
    nav_title = "WoRMS 分类库"
    nav_icon  = "🌊"

    def __init__(self, ctx: "AppContext") -> None:
        self._service:       Optional[WormsService] = None
        self._search_thread: Optional[QThread] = None
        self._detail_thread: Optional[QThread] = None
        self._search_worker: Optional[_SearchWorker] = None
        self._detail_worker: Optional[_DetailWorker] = None
        self._results:       list[dict] = []
        self._selected:      Optional[dict] = None
        # Auto-poll timer for running batch jobs (oracle: fetchWormsJobs app.js ~11609)
        self._poll_timer:    Optional[QTimer] = None
        super().__init__(ctx)

    # ── Service ────────────────────────────────────────────────────────

    def _init_service(self) -> WormsService:
        project_dir = getattr(self.ctx, "current_project_dir", None)
        data_dir = (Path(project_dir) / "_data") if project_dir else _wv._FALLBACK_DATA_DIR
        data_dir.mkdir(parents=True, exist_ok=True)
        return WormsService(
            cache_path=str(data_dir / "worms_cache.json"),
            jobs_path=str(data_dir / "worms_jobs.json"),
        )

    # ── UI construction ────────────────────────────────────────────────

    def _setup_ui(self) -> None:
        _wv._refresh_palette()
        self._service = self._init_service()
        # NOTE: _detail_panel is created later in this method; set_service is
        # called again in on_activate() to pick up any project change.

        # Outer layout: full-view, no margins
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # ── Outer scroll area (prevents squash on short windows) ───────
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet("QScrollArea { background: transparent; }")
        outer.addWidget(scroll)

        # Content widget inside the scroll
        content_w = QWidget()
        content_w.setObjectName("WContentWidget")
        content_w.setStyleSheet("QWidget#WContentWidget { background: transparent; }")
        content_w.setMinimumHeight(560)

        content_lay = QVBoxLayout(content_w)
        content_lay.setContentsMargins(0, 0, 0, 0)
        content_lay.setSpacing(0)
        scroll.setWidget(content_w)

        # ── worms-header ───────────────────────────────────────────────
        header_w = QWidget()
        header_w.setObjectName("WHeader")
        header_w.setStyleSheet(
            "QWidget#WHeader { padding: 0; background: transparent; }"
        )
        header_lay = QVBoxLayout(header_w)
        header_lay.setContentsMargins(28, 22, 28, 14)
        header_lay.setSpacing(6)

        # worms-title-row
        title_row = QHBoxLayout()
        title_row.setSpacing(12)
        title_row.setAlignment(Qt.AlignmentFlag.AlignVCenter)

        h2 = _wv._label(
            "WoRMS 海洋物种分类库",
            size=18, bold=True, font=_wv._SERIF, color=_wv._C_TEXT
        )
        title_row.addWidget(h2)

        ext_link = _wv._label("marinespecies.org", color=_wv._C_ACCENT, size=12)
        title_row.addWidget(ext_link)
        title_row.addWidget(_wv._label("查询", color=_wv._C_MUTED, size=12))
        title_row.addStretch()

        # 批量匹配 (Match Taxa) — 复刻 WoRMS 官网批量匹配工具的入口
        self._match_btn = QPushButton("批量匹配 (Match Taxa)")
        self._match_btn.setObjectName("WMatchBtn")
        self._match_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._match_btn.setStyleSheet(
            f"QPushButton#WMatchBtn {{ background:{_wv._C_ACCENT}; color:{_wv._C_BG};"
            f"  border:none; border-radius:6px; padding:7px 16px;"
            f"  font-size:12px; font-weight:600; }}"
            f"QPushButton#WMatchBtn:hover {{ background:{_wv._C_ACCENT_H}; }}"
        )
        self._match_btn.clicked.connect(self._open_batch_taxon_match_dialog)
        title_row.addWidget(self._match_btn)
        header_lay.addLayout(title_row)

        # worms-desc
        desc = _wv._label(
            "查询 World Register of Marine Species，获取标准化分类链并填充到标本记录。",
            color=_wv._C_MUTED, size=12
        )
        header_lay.addWidget(desc)
        content_lay.addWidget(header_w)

        # ── worms-body (HSplitter) ─────────────────────────────────────
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setHandleWidth(18)
        splitter.setChildrenCollapsible(False)
        splitter.setMinimumHeight(400)
        content_lay.addWidget(splitter, stretch=1)

        # Left: search panel
        left_container = QWidget()
        left_container.setObjectName("WSearchContainer")
        left_container.setStyleSheet("background:transparent;")
        left_lay = QVBoxLayout(left_container)
        left_lay.setContentsMargins(28, 4, 14, 16)
        left_lay.setSpacing(10)

        # worms-search-bar
        search_bar = QWidget()
        sb_lay = QHBoxLayout(search_bar)
        sb_lay.setContentsMargins(0, 0, 0, 0)
        sb_lay.setSpacing(8)

        self._search_input = QLineEdit()
        self._search_input.setObjectName("WSearchInput")
        self._search_input.setPlaceholderText("输入拉丁学名搜索…")
        self._search_input.setStyleSheet(
            f"QLineEdit#WSearchInput {{ background:{_wv._C_INPUT}; border:1px solid {_wv._C_BORDER};"
            f"  border-radius:6px; padding:8px 12px; color:{_wv._C_TEXT}; font-size:13px;"
            f"  font-family:{_wv._MONO}; outline:none; }}"
            f"QLineEdit#WSearchInput:focus {{ border-color:{_wv._C_ACCENT}; }}"
        )
        self._search_input.returnPressed.connect(self._start_worms_taxon_search)
        sb_lay.addWidget(self._search_input, stretch=1)

        # like-toggle (worms-like-toggle)
        self._like_cb = QCheckBox("模糊匹配")
        self._like_cb.setChecked(True)
        self._like_cb.setStyleSheet(
            f"QCheckBox {{ color:{_wv._C_MUTED}; font-size:12px; spacing:5px; }}"
            f"QCheckBox::indicator {{ width:14px; height:14px; border-radius:4px;"
            f"  border:1px solid {_wv._C_BORDER_25}; background:{_wv._C_INPUT}; }}"
            f"QCheckBox::indicator:checked {{ background:{_wv._C_ACCENT}; border-color:{_wv._C_ACCENT}; }}"
        )
        sb_lay.addWidget(self._like_cb)

        # 搜索 button
        self._search_btn = QPushButton("搜索")
        self._search_btn.setObjectName("WSearchBtn")
        self._search_btn.setStyleSheet(
            f"QPushButton#WSearchBtn {{ background:{_wv._C_ACCENT}; color:{_wv._C_BG};"
            f"  border:none; border-radius:6px; padding:8px 18px;"
            f"  font-size:13px; font-weight:600; }}"
            f"QPushButton#WSearchBtn:hover {{ background:{_wv._C_ACCENT_H}; }}"
            f"QPushButton#WSearchBtn:disabled {{ background:{_wv._C_PANEL}; color:{_wv._C_DIM}; }}"
        )
        self._search_btn.clicked.connect(self._start_worms_taxon_search)
        sb_lay.addWidget(self._search_btn)

        left_lay.addWidget(search_bar)

        # Status / progress row
        status_row = QHBoxLayout()
        status_row.setContentsMargins(0, 0, 0, 0)
        status_row.setSpacing(8)

        self._status_lbl = _wv._label("", color=_wv._C_MUTED, size=11)
        status_row.addWidget(self._status_lbl)

        self._progress = QProgressBar()
        self._progress.setRange(0, 0)
        self._progress.setFixedHeight(3)
        self._progress.setVisible(False)
        self._progress.setStyleSheet(
            f"QProgressBar {{ background:{_wv._C_PANEL}; border:none; border-radius:2px; }}"
            f"QProgressBar::chunk {{ background:{_wv._C_ACCENT}; border-radius:2px; }}"
        )
        status_row.addWidget(self._progress, stretch=1)
        left_lay.addLayout(status_row)

        # Result area (scrollable)
        self._result_scroll = QScrollArea()
        self._result_scroll.setWidgetResizable(True)
        self._result_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._result_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._result_scroll.setStyleSheet("QScrollArea { background: transparent; }")

        self._result_container = QWidget()
        self._result_container.setObjectName("WResultContainer")
        self._result_container.setStyleSheet("background:transparent;")
        self._result_layout = QVBoxLayout(self._result_container)
        self._result_layout.setContentsMargins(0, 0, 4, 0)
        self._result_layout.setSpacing(3)
        self._result_layout.addStretch()

        # Initial empty state label — give the otherwise-blank result column a
        # centred hint so the empty state reads as intentional, not dead space.
        self._empty_lbl = _wv._label(
            "输入拉丁学名后点击「搜索」\n结果将在此列出", color=_wv._C_MUTED, size=12
        )
        self._empty_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_lbl.setContentsMargins(0, 48, 0, 0)
        self._result_layout.insertWidget(0, self._empty_lbl)

        self._result_scroll.setWidget(self._result_container)
        left_lay.addWidget(self._result_scroll, stretch=1)

        splitter.addWidget(left_container)

        # Right: detail panel
        self._detail_panel = _DetailPanel()
        self._detail_panel.fill_requested.connect(self._fill_active_specimen_from_worms_record)
        self._detail_panel.child_selected.connect(self._load_selected_worms_result_detail)
        self._detail_panel.set_service(self._service)
        right_container = QWidget()
        right_container.setStyleSheet("background:transparent;")
        right_lay = QVBoxLayout(right_container)
        right_lay.setContentsMargins(14, 4, 28, 16)
        right_lay.setSpacing(0)
        right_lay.addWidget(self._detail_panel)
        splitter.addWidget(right_container)

        splitter.setStretchFactor(0, 6)
        splitter.setStretchFactor(1, 4)

        # ── Batch jobs footer (de-emphasised) ──────────────────────────
        jobs_box = self._build_jobs_section()
        content_lay.addWidget(jobs_box)

    def _build_jobs_section(self) -> QGroupBox:
        """Collapsible batch validation jobs panel (worms_service parity)."""
        box = QGroupBox("批量验证任务")
        box.setCheckable(True)
        box.setChecked(False)
        box.setMaximumHeight(180)
        box.setStyleSheet(
            f"QGroupBox {{ color:{_wv._C_MUTED}; font-size:12px; font-weight:600;"
            f"  border:1px solid {_wv._C_BORDER_10}; border-radius:8px;"
            f"  margin:0 28px 16px 28px; padding:18px 14px 10px 14px;"
            f"  background:{_wv._C_INPUT}; }}"
            f"QGroupBox::title {{ subcontrol-origin:margin; subcontrol-position:top left;"
            f"  left:12px; top:2px; padding:0 6px 0 2px; spacing:6px;"
            f"  background:{_wv._C_INPUT}; color:{_wv._C_MUTED}; }}"
            f"QGroupBox::indicator {{ width:13px; height:13px; }}"
        )

        inner = QVBoxLayout(box)
        inner.setContentsMargins(4, 8, 4, 8)
        inner.setSpacing(6)

        ctrl = QHBoxLayout()
        ctrl.setSpacing(8)
        self._job_ids_input = QLineEdit()
        self._job_ids_input.setPlaceholderText("逗号分隔的 record_id（必填）")
        self._job_ids_input.setStyleSheet(
            f"QLineEdit {{ background:{_wv._C_INPUT}; border:1px solid {_wv._C_BORDER};"
            f"  border-radius:6px; padding:6px 10px; color:{_wv._C_TEXT}; font-size:12px; }}"
        )
        ctrl.addWidget(self._job_ids_input, stretch=1)

        create_btn = QPushButton("创建任务")
        create_btn.setFixedWidth(84)
        create_btn.setStyleSheet(
            f"QPushButton {{ background:{_wv._C_PANEL}; color:{_wv._C_TEXT}; border:1px solid {_wv._C_BORDER};"
            f"  border-radius:6px; padding:6px 10px; font-size:12px; }}"
            f"QPushButton:hover {{ border-color:{_wv._C_ACCENT}; color:{_wv._C_ACCENT}; }}"
        )
        create_btn.clicked.connect(self._on_create_job)
        ctrl.addWidget(create_btn)

        import_filter_btn = QPushButton("从分类库筛选导入")
        import_filter_btn.setObjectName("BtnImportFromTaxonFilter")
        import_filter_btn.setFixedWidth(114)
        import_filter_btn.setStyleSheet(
            f"QPushButton {{ background:{_wv._C_PANEL}; color:{_wv._C_ACCENT}; border:1px solid {_wv._C_ACCENT_30};"
            f"  border-radius:6px; padding:6px 10px; font-size:12px; }}"
            f"QPushButton:hover {{ border-color:{_wv._C_ACCENT}; background:{_wv._C_ACCENT_08}; }}"
        )
        import_filter_btn.clicked.connect(self._on_import_from_taxon_filter)
        ctrl.addWidget(import_filter_btn)

        refresh_btn = QPushButton("刷新")
        refresh_btn.setFixedWidth(52)
        refresh_btn.setStyleSheet(
            f"QPushButton {{ background:{_wv._C_PANEL}; color:{_wv._C_MUTED}; border:1px solid {_wv._C_BORDER};"
            f"  border-radius:6px; padding:6px 8px; font-size:12px; }}"
            f"QPushButton:hover {{ color:{_wv._C_TEXT}; }}"
        )
        refresh_btn.clicked.connect(self._refresh_jobs)
        ctrl.addWidget(refresh_btn)

        # Retry-failed button (oracle: app.js ~12006 retry-failed action)
        self._retry_btn = QPushButton("重试失败")
        self._retry_btn.setFixedWidth(72)
        self._retry_btn.setEnabled(False)
        self._retry_btn.setStyleSheet(
            f"QPushButton {{ background:{_wv._C_PANEL}; color:{_wv._C_WARN}; border:1px solid {_wv._C_WARN_25};"
            f"  border-radius:6px; padding:6px 8px; font-size:12px; }}"
            f"QPushButton:hover:enabled {{ border-color:{_wv._C_WARN}; }}"
            f"QPushButton:disabled {{ color:{_wv._C_DIM}; border-color:{_wv._C_BORDER}; }}"
        )
        self._retry_btn.clicked.connect(self._on_retry_failed)
        ctrl.addWidget(self._retry_btn)
        inner.addLayout(ctrl)

        self._jobs_list = QListWidget()
        self._jobs_list.setFixedHeight(84)
        self._jobs_list.setStyleSheet(
            f"QListWidget {{ background:{_wv._C_INPUT}; border:1px solid {_wv._C_BORDER};"
            f"  border-radius:6px; font-size:11px; color:{_wv._C_MUTED}; }}"
            f"QListWidget::item {{ padding:4px 8px; border-radius:4px; }}"
            f"QListWidget::item:hover {{ background:{_wv._C_ACCENT_08}; color:{_wv._C_TEXT}; }}"
        )
        inner.addWidget(self._jobs_list)

        return box

    # ── BaseView contract ──────────────────────────────────────────────

    def on_activate(self) -> None:
        self._service = self._init_service()
        self._detail_panel.set_service(self._service)
        self._refresh_jobs()

    # ── Search ─────────────────────────────────────────────────────────

    def _open_batch_taxon_match_dialog(self) -> None:
        """Open the batch Match-Taxa wizard (shares this view's WoRMS cache)."""
        from app.widgets.worms_match_dialog import WormsMatchDialog
        if not self._service:
            self._service = self._init_service()
        WormsMatchDialog(self._service, parent=self).exec()

    def _start_worms_taxon_search(self) -> None:
        name = self._search_input.text().strip()
        if not name:
            self._set_status("请输入学名")
            return
        if self._search_thread and self._search_thread.isRunning():
            return

        like = self._like_cb.isChecked()
        self._set_worms_search_busy_state(True, f'搜索 "{name}"…')
        self._clear_results()

        worker = _SearchWorker(self._service, name, like)
        thread = QThread(self)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(self._show_worms_taxon_search_results)
        worker.error.connect(self._show_worms_taxon_search_error)
        worker.finished.connect(thread.quit)
        worker.error.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        self._search_worker = worker
        self._search_thread = thread
        thread.start()

    def _show_worms_taxon_search_results(self, results: list[dict]) -> None:
        self._set_worms_search_busy_state(False)
        self._results = results
        self._clear_results()

        if not results:
            name = self._search_input.text().strip()
            msg = f'"{name}" 无匹配结果。试试模糊匹配或缩短搜索词。' if name else "无结果"
            self._empty_lbl.setText(msg)
            self._set_status("0 条结果")
            return

        self._empty_lbl.setText("")
        # Insert result widgets before the trailing stretch
        stretch_idx = self._result_layout.count() - 1
        for rec in results:
            item_w = _ResultItemWidget(rec)
            item_w.clicked.connect(self._load_selected_worms_result_detail)
            self._result_layout.insertWidget(stretch_idx, item_w)
            stretch_idx += 1

        self._set_status(f"找到 {len(results)} 条结果")

    def _show_worms_taxon_search_error(self, msg: str) -> None:
        self._set_worms_search_busy_state(False)
        self._empty_lbl.setText(f"搜索失败: {msg}")
        self._empty_lbl.setStyleSheet(
            f"color:{_wv._C_DANGER}; font-size:12px; background:transparent;"
        )
        self._set_status("搜索出错")

    def _clear_results(self) -> None:
        """Remove all result item widgets (keep empty_lbl and stretch)."""
        to_remove = []
        for i in range(self._result_layout.count()):
            item = self._result_layout.itemAt(i)
            if item and item.widget() and item.widget() is not self._empty_lbl:
                to_remove.append(item.widget())
        for w in to_remove:
            self._result_layout.removeWidget(w)
            w.deleteLater()
        self._empty_lbl.setStyleSheet(
            f"color:{_wv._C_MUTED}; font-size:12px; background:transparent;"
        )
        self._empty_lbl.setText("")

    # ── Result selection ───────────────────────────────────────────────

    def _load_selected_worms_result_detail(self, rec: dict) -> None:
        self._selected = rec
        valid_id = rec.get("valid_AphiaID") or rec.get("AphiaID")
        if not valid_id:
            self._detail_panel.show_empty()
            return

        self._detail_panel.show_loading(rec)

        if self._detail_thread and self._detail_thread.isRunning():
            self._detail_thread.quit()
            self._detail_thread.wait(400)

        worker = _DetailWorker(self._service, int(valid_id))
        thread = QThread(self)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(self._show_selected_worms_result_detail)
        worker.error.connect(self._show_worms_detail_load_error)
        worker.finished.connect(thread.quit)
        worker.error.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        self._detail_worker = worker
        self._detail_thread = thread
        thread.start()

    def _show_selected_worms_result_detail(self, data: dict) -> None:
        if self._selected is None:
            return
        self._detail_panel.show_detail(
            self._selected,
            data.get("chain", []),
            data.get("synonyms", []),
            data.get("children", []),
        )

    def _show_worms_detail_load_error(self, msg: str) -> None:
        self._set_status(f"详情加载失败: {msg}")

    # ── Fill to specimen (worms-fill-btn) ──────────────────────────────

    def _fill_active_specimen_from_worms_record(self, rec: dict) -> None:
        """Apply WoRMS classification fields to the active specimen via ctx.

        Field mapping mirrors wormsFillToSpecimen() in app.js ~11447:
          class  → sp.taxonGroup
          order  → sp.order
          family → sp.family
          scientificname (Species rank) → sp.scientificName
          taxonomyConfirmed → False

        Chinese fields (*Cn) are NEVER overwritten (hard invariant).
        """
        ctx = self.ctx

        # Resolve unaccepted → accepted name
        r = rec
        if rec.get("status") == "unaccepted" and rec.get("valid_name"):
            r = dict(rec)
            r["scientificname"] = rec["valid_name"]

        # Delegate to app context if it supports specimen fill
        fill_fn = getattr(ctx, "worms_fill_specimen", None)
        if callable(fill_fn):
            try:
                fill_fn(r)
                self._set_status(f"已从 WoRMS 填充分类信息: {r.get('scientificname', '')}")
                return
            except Exception as exc:
                self._set_status(f"填充失败: {exc}")
                return

        # Fallback: apply directly to current_specimen if ctx exposes it
        specimen_fn = getattr(ctx, "current_specimen", None)
        sp = specimen_fn() if callable(specimen_fn) else None
        if sp is not None and isinstance(sp, dict):
            if r.get("class"):
                sp["taxonGroup"] = r["class"]
            if r.get("order"):
                sp["order"] = r["order"]
            if r.get("family"):
                sp["family"] = r["family"]
            if r.get("rank") == "Species" and r.get("scientificname"):
                sp["scientificName"] = r["scientificname"]
            sp["taxonomyConfirmed"] = False
            # Persist via ctx if available
            save_fn = getattr(ctx, "save_specimen", None)
            if callable(save_fn):
                try:
                    save_fn(sp)
                except Exception:
                    pass
            self._set_status(f"已从 WoRMS 填充分类信息: {r.get('scientificname', '')}")
            return

        # No active specimen
        self._set_status("（需先在工作区选择标本）")

    # ── Batch jobs ─────────────────────────────────────────────────────

    def _on_create_job(self) -> None:
        raw = self._job_ids_input.text().strip()
        if not raw:
            QMessageBox.information(
                self, "批量任务",
                "请在输入框中填写逗号分隔的 record_id，再创建任务。\n"
                "也可在\"内置分类库\"模块中选择条目后从那里发起。",
            )
            return
        record_ids = [r.strip() for r in raw.split(",") if r.strip()]
        try:
            job = self._service.create_job(record_ids, source="selected")
            self._set_status(f"任务已创建: {job.id[:8]}… ({len(record_ids)} 条)")
            self._refresh_jobs()
        except Exception as exc:
            self._set_status(f"创建失败: {exc}")

    def _on_import_from_taxon_filter(self) -> None:
        """Fill job IDs input with UIDs from TaxonomyView's current filter result."""
        win = self.window()
        taxon_view = None
        if hasattr(win, "_views"):
            taxon_view = win._views.get("taxonomy")

        if taxon_view is None:
            QMessageBox.warning(self, "提示", "请先打开分类库页面")
            return

        uids = taxon_view.get_filtered_uids()
        if not uids:
            QMessageBox.information(self, "提示", "分类库无筛选结果")
            return

        self._job_ids_input.setText(",".join(uids))
        QMessageBox.information(self, "已导入", f"已导入 {len(uids)} 条筛选结果")

    def _on_retry_failed(self) -> None:
        """Retry all error-status items in the most recent job.

        Oracle: updateWormsJob(job, "retry-failed") in app.js ~12006 /
        server.js ~2157: filter record_ids to those with status="error",
        reset cursor to 0 and status to "running".
        """
        if not self._service:
            return
        try:
            jobs = self._service.list_jobs()
            if not jobs:
                return
            # Operate on the most recent job that has errors
            target = next(
                (j for j in jobs if j.get("counts", {}).get("error", 0) > 0),
                jobs[0],  # fallback to newest
            )
            self._service.retry_failed_job(target["id"])
            self._set_status(f"已重试失败项: {(target.get('id') or '?')[:8]}…")
            self._refresh_jobs()
        except Exception as exc:
            self._set_status(f"重试失败: {exc}")

    def _refresh_jobs(self) -> None:
        """Refresh the jobs list and start/stop the 1.5 s auto-poll timer.

        Oracle: fetchWormsJobs() app.js ~11602 — when a job is running,
        poll every 1 500 ms; stop when no running job exists.
        """
        if not self._service:
            return
        jobs = self._service.list_jobs()
        self._jobs_list.clear()

        # Stop any existing poll timer; we'll restart it if needed
        if self._poll_timer is not None:
            self._poll_timer.stop()
            self._poll_timer = None

        # Detect active (running) job and error-containing job for retry button
        has_running = any(j.get("status") == "running" for j in jobs)
        has_failed  = any(
            j.get("counts", {}).get("error", 0) > 0
            for j in jobs
        )
        if hasattr(self, "_retry_btn"):
            self._retry_btn.setEnabled(has_failed)

        if not jobs:
            item = QListWidgetItem("（暂无任务）")
            item.setForeground(QColor(_wv._C_DIM))
            self._jobs_list.addItem(item)
        else:
            for j in jobs[:20]:
                jid     = (j.get("id") or "?")[:8]
                status  = j.get("status", "?")
                cursor  = j.get("cursor", 0)
                total   = len(j.get("record_ids", []))
                ts      = (j.get("created_at") or "")[:10]
                counts  = j.get("counts", {})
                summary = "  ".join(f"{k}:{v}" for k, v in counts.items() if v)
                label   = f"[{ts}]  {jid}…  {status}  {cursor}/{total}"
                if summary:
                    label += f"  ({summary})"
                item = QListWidgetItem(label)
                if status == "completed":
                    item.setForeground(QColor(_wv._C_SUCCESS))
                elif status == "running":
                    item.setForeground(QColor(_wv._C_RUNNING))
                elif status in ("paused", "cancelled"):
                    item.setForeground(QColor(_wv._C_WARN))
                self._jobs_list.addItem(item)

        # Auto-poll: restart 1.5 s single-shot timer when a job is running
        # Oracle: taxonJobPollTimer = setTimeout(..., 1500) in app.js ~11609
        if has_running:
            self._poll_timer = QTimer(self)
            self._poll_timer.setSingleShot(True)
            self._poll_timer.timeout.connect(self._refresh_jobs)
            self._poll_timer.start(1500)

    # ── UI helpers ─────────────────────────────────────────────────────

    def _set_worms_search_busy_state(self, busy: bool, msg: str = "") -> None:
        self._progress.setVisible(busy)
        self._search_btn.setEnabled(not busy)
        if msg:
            self._set_status(msg)

    def _set_status(self, msg: str) -> None:
        self._status_lbl.setText(msg)
