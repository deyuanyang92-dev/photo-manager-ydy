"""WoRMS matching and quick-fill dialogs."""
from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import Qt, QThread
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QLineEdit, QScrollArea, QFrame, QCheckBox, QDialog,
)

from app.config.theme import local_font_css
from app.services.worms_service import WormsService
from app.views import worms_view_support as _wv
from app.views.worms_workers import _MatchChainWorker, _MatchSearchWorker, _QuickSearchWorker

class WormsMatchDialog(QDialog):
    """Manual WoRMS match dialog for taxonomy review rows.

    Mirrors ``renderWormsMatchModal()`` + ``searchWormsForTaxonRow()`` +
    ``selectWormsMatchCandidate()`` + ``saveWormsMatchCandidate()`` in
    app.js lines ~11767–11811.

    Usage::

        dlg = WormsMatchDialog(service, row_record, parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            aphia_id, chain = dlg.result_aphia_id, dlg.result_chain

    Emits nothing — callers inspect ``result_aphia_id`` and ``result_chain``
    after ``exec()``.  For "no match / skip", both are None.

    Parameters
    ----------
    service:
        WormsService instance for searches.
    row:
        The taxonomy row dict being matched.  Must contain at least
        ``recordId`` and ``species`` (original name).
    parent:
        Parent widget for centering.
    """

    def __init__(
        self,
        service: WormsService,
        row: dict,
        *,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        _wv._refresh_palette()
        self._svc  = service
        self._row  = row

        self.result_aphia_id: Optional[int] = None
        self.result_chain:    list[dict]    = []

        self._results:        list[dict]  = []
        self._selected_rec:   Optional[dict] = None
        self._chain:          list[dict]  = []
        self._loading:        bool = False
        self._chain_loading:  bool = False
        self._error:          str  = ""

        self._search_thread: Optional[QThread] = None
        self._chain_thread:  Optional[QThread] = None

        self.setWindowTitle("WoRMS 匹配物种")
        self.setMinimumSize(680, 480)
        self.setModal(True)
        _ff = local_font_css()
        self.setStyleSheet(
            f"QDialog {{ {_ff}background:{_wv._C_BG}; color:{_wv._C_TEXT}; }}"
            f"QLabel {{ color:{_wv._C_TEXT}; background:transparent; }}"
        )
        self._build_ui()

        # Auto-search with the original species name
        initial = row.get("species", "")
        if initial:
            self._search_input.setText(initial)
            self._start_worms_candidate_search()

    # ── UI ─────────────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 16, 20, 16)
        root.setSpacing(10)

        root.addWidget(_wv._label("WoRMS 匹配物种", size=15, bold=True))

        original = self._row.get("species", "")
        if original:
            orig_lbl = _wv._label(f"原始种名：{original}", color=_wv._C_MUTED, size=12)
            root.addWidget(orig_lbl)

        # Search bar
        bar = QHBoxLayout()
        bar.setSpacing(8)

        self._search_input = QLineEdit()
        self._search_input.setObjectName("WMSearchInput")
        self._search_input.setPlaceholderText("输入科学名")
        self._search_input.setStyleSheet(
            f"QLineEdit#WMSearchInput {{ background:{_wv._C_INPUT}; border:1px solid {_wv._C_BORDER};"
            f"  border-radius:6px; padding:7px 10px; color:{_wv._C_TEXT}; font-size:13px;"
            f"  font-family:{_wv._MONO}; }}"
            f"QLineEdit#WMSearchInput:focus {{ border-color:{_wv._C_ACCENT}; }}"
        )
        self._search_input.returnPressed.connect(self._start_worms_candidate_search)
        bar.addWidget(self._search_input, stretch=1)

        self._like_cb = QCheckBox("模糊匹配")
        self._like_cb.setStyleSheet(
            f"QCheckBox {{ color:{_wv._C_MUTED}; font-size:12px; spacing:5px; }}"
        )
        bar.addWidget(self._like_cb)

        search_btn = QPushButton("搜索")
        search_btn.setObjectName("WMSearchBtn")
        search_btn.setStyleSheet(
            f"QPushButton#WMSearchBtn {{ background:{_wv._C_ACCENT}; color:{_wv._C_BG};"
            f"  border:none; border-radius:6px; padding:7px 16px; font-size:12px; font-weight:600; }}"
            f"QPushButton#WMSearchBtn:hover {{ background:{_wv._C_ACCENT_H}; }}"
        )
        search_btn.clicked.connect(self._start_worms_candidate_search)
        bar.addWidget(search_btn)
        root.addLayout(bar)

        # Error label
        self._error_lbl = _wv._label("", color=_wv._C_DANGER, size=11)
        self._error_lbl.setVisible(False)
        root.addWidget(self._error_lbl)

        # Body: results list | chain preview
        body = QHBoxLayout()
        body.setSpacing(12)

        # Results list
        results_w = QWidget()
        results_w.setObjectName("WMResultsPanel")
        results_w.setStyleSheet(
            f"QWidget#WMResultsPanel {{ background:{_wv._C_PANEL}; border:1px solid {_wv._C_BORDER};"
            f"  border-radius:6px; }}"
        )
        results_lay = QVBoxLayout(results_w)
        results_lay.setContentsMargins(6, 6, 6, 6)
        results_lay.setSpacing(4)

        self._results_scroll = QScrollArea()
        self._results_scroll.setWidgetResizable(True)
        self._results_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._results_scroll.setStyleSheet("QScrollArea { background: transparent; }")

        self._results_container = QWidget()
        self._results_container.setStyleSheet("background:transparent;")
        self._results_layout = QVBoxLayout(self._results_container)
        self._results_layout.setContentsMargins(0, 0, 0, 0)
        self._results_layout.setSpacing(3)
        self._results_layout.addStretch()

        self._results_scroll.setWidget(self._results_container)
        results_lay.addWidget(self._results_scroll)
        body.addWidget(results_w, stretch=3)

        # Chain preview
        detail_w = QWidget()
        detail_w.setObjectName("WMDetailPanel")
        detail_w.setStyleSheet(
            f"QWidget#WMDetailPanel {{ background:{_wv._C_INPUT}; border:1px solid {_wv._C_BORDER};"
            f"  border-radius:6px; }}"
        )
        detail_lay = QVBoxLayout(detail_w)
        detail_lay.setContentsMargins(10, 10, 10, 10)
        detail_lay.setSpacing(4)

        self._chain_scroll = QScrollArea()
        self._chain_scroll.setWidgetResizable(True)
        self._chain_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._chain_scroll.setStyleSheet("QScrollArea { background: transparent; }")
        self._chain_content = QWidget()
        self._chain_content.setStyleSheet("background:transparent;")
        self._chain_layout = QVBoxLayout(self._chain_content)
        self._chain_layout.setContentsMargins(0, 0, 0, 0)
        self._chain_layout.setSpacing(2)
        self._chain_layout.addWidget(
            _wv._label("选择候选后预览标准分类阶元", color=_wv._C_DIM, size=12)
        )
        self._chain_scroll.setWidget(self._chain_content)
        detail_lay.addWidget(self._chain_scroll)
        body.addWidget(detail_w, stretch=2)

        root.addLayout(body, stretch=1)

        # Action buttons
        actions = QHBoxLayout()
        actions.setSpacing(8)
        actions.addStretch()

        self._save_btn = QPushButton("采用并保存")
        self._save_btn.setObjectName("WMSaveBtn")
        self._save_btn.setEnabled(False)
        self._save_btn.setStyleSheet(
            f"QPushButton#WMSaveBtn {{ background:{_wv._C_ACCENT}; color:{_wv._C_BG};"
            f"  border:none; border-radius:6px; padding:8px 18px; font-size:12px; font-weight:600; }}"
            f"QPushButton#WMSaveBtn:disabled {{ background:{_wv._C_PANEL}; color:{_wv._C_DIM}; }}"
            f"QPushButton#WMSaveBtn:hover:enabled {{ background:{_wv._C_ACCENT_H}; }}"
        )
        self._save_btn.clicked.connect(self._accept_selected_worms_candidate)
        actions.addWidget(self._save_btn)

        cancel_btn = QPushButton("取消")
        cancel_btn.setObjectName("WMCancelBtn")
        cancel_btn.setStyleSheet(
            f"QPushButton#WMCancelBtn {{ background:{_wv._C_PANEL}; color:{_wv._C_MUTED};"
            f"  border:1px solid {_wv._C_BORDER}; border-radius:6px; padding:8px 16px; font-size:12px; }}"
            f"QPushButton#WMCancelBtn:hover {{ color:{_wv._C_TEXT}; }}"
        )
        cancel_btn.clicked.connect(self.reject)
        actions.addWidget(cancel_btn)
        root.addLayout(actions)

    # ── Search (mirrors searchWormsForTaxonRow app.js ~11777) ───────────────

    def _start_worms_candidate_search(self) -> None:
        name = self._search_input.text().strip()
        if not name:
            return
        if self._search_thread and self._search_thread.isRunning():
            return

        self._loading = True
        self._error = ""
        self._results = []
        self._selected_rec = None
        self._chain = []
        self._save_btn.setEnabled(False)
        self._error_lbl.setVisible(False)
        self._update_results_list()
        self._update_chain_view()

        like = self._like_cb.isChecked()
        worker = _MatchSearchWorker(self._svc, name, like)
        thread = QThread(self)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(self._show_worms_candidate_search_results)
        worker.error.connect(self._show_worms_candidate_search_error)
        worker.finished.connect(thread.quit)
        worker.error.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        self._search_thread = thread
        thread.start()

    def _show_worms_candidate_search_results(self, results: list[dict]) -> None:
        self._loading = False
        self._results = results
        self._update_results_list()

    def _show_worms_candidate_search_error(self, msg: str) -> None:
        self._loading = False
        self._error = f"搜索失败：{msg}"
        self._error_lbl.setText(self._error)
        self._error_lbl.setVisible(True)
        self._update_results_list()

    # ── Candidate selection (mirrors selectWormsMatchCandidate ~11791) ──────

    def _load_worms_candidate_classification_chain(self, rec: dict) -> None:
        self._selected_rec = rec
        aphia_id = rec.get("valid_AphiaID") or rec.get("AphiaID")
        if not aphia_id:
            return

        self._chain_loading = True
        self._chain = []
        self._save_btn.setEnabled(False)
        self._update_results_list()   # re-highlight selected
        self._update_chain_view()

        worker = _MatchChainWorker(self._svc, int(aphia_id))
        thread = QThread(self)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(self._show_worms_candidate_classification_chain)
        worker.error.connect(lambda _: self._show_worms_candidate_classification_chain([]))
        worker.finished.connect(thread.quit)
        worker.error.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        self._chain_thread = thread
        thread.start()

    def _show_worms_candidate_classification_chain(self, chain: list[dict]) -> None:
        self._chain = chain
        self._chain_loading = False
        self._save_btn.setEnabled(self._selected_rec is not None)
        self._update_chain_view()

    # ── Save (mirrors saveWormsMatchCandidate ~11806) ───────────────────────

    def _accept_selected_worms_candidate(self) -> None:
        if not self._selected_rec:
            return
        self.result_aphia_id = int(
            self._selected_rec.get("valid_AphiaID") or self._selected_rec.get("AphiaID") or 0
        )
        self.result_chain = self._chain
        self.accept()

    # ── UI helpers ──────────────────────────────────────────────────────────

    def _update_results_list(self) -> None:
        # Clear old widgets
        to_remove = []
        for i in range(self._results_layout.count()):
            item = self._results_layout.itemAt(i)
            if item and item.widget():
                to_remove.append(item.widget())
        for w in to_remove:
            self._results_layout.removeWidget(w)
            w.deleteLater()

        if self._loading:
            self._results_layout.addWidget(
                _wv._label("正在查询 WoRMS…", color=_wv._C_MUTED, size=12)
            )
        elif not self._results:
            msg = "未找到候选，请修改关键词或启用模糊匹配。" if not self._loading else ""
            self._results_layout.addWidget(_wv._label(msg, color=_wv._C_MUTED, size=12))
        else:
            selected_id = (
                (self._selected_rec.get("valid_AphiaID") or self._selected_rec.get("AphiaID"))
                if self._selected_rec
                else None
            )
            for rec in self._results:
                rec_id = rec.get("valid_AphiaID") or rec.get("AphiaID")
                is_sel = rec_id == selected_id

                btn_w = QWidget()
                btn_w.setObjectName("WMCandBtn")
                btn_w.setCursor(Qt.CursorShape.PointingHandCursor)
                bg = _wv._C_ACCENT_12 if is_sel else "transparent"
                border = _wv._C_ACCENT if is_sel else "transparent"
                btn_w.setStyleSheet(
                    f"QWidget#WMCandBtn {{ background:{bg}; border:1px solid {border};"
                    f"  border-radius:6px; }}"
                    f"QWidget#WMCandBtn:hover {{ background:{_wv._C_ACCENT_08}; }}"
                )
                row_lay = QVBoxLayout(btn_w)
                row_lay.setContentsMargins(8, 6, 8, 6)
                row_lay.setSpacing(2)

                name_row = QHBoxLayout()
                name_row.setSpacing(6)
                name = rec.get("valid_name") or rec.get("scientificname") or ""
                name_row.addWidget(_wv._label(name, bold=True, font=_wv._MONO, size=12))
                name_row.addStretch()
                status = (rec.get("status") or "").lower()
                name_row.addWidget(_wv._badge(
                    status,
                    "accepted" if status == "accepted" else "unaccepted"
                ))
                row_lay.addLayout(name_row)

                detail_txt = (
                    (status or "")
                    + f"  ·  AphiaID {rec_id}"
                )
                row_lay.addWidget(_wv._label(detail_txt, color=_wv._C_DIM, size=11))

                bc = " > ".join(
                    p for p in [rec.get("class"), rec.get("order"), rec.get("family")]
                    if p
                )
                if bc:
                    row_lay.addWidget(_wv._label(bc, color=_wv._C_DIM, size=11))

                _rec = rec  # capture
                btn_w.mousePressEvent = lambda _e, r=_rec: self._load_worms_candidate_classification_chain(r)  # type: ignore[method-assign]
                self._results_layout.addWidget(btn_w)

        self._results_layout.addStretch()

    def _update_chain_view(self) -> None:
        # Clear old widgets
        to_remove = []
        for i in range(self._chain_layout.count()):
            item = self._chain_layout.itemAt(i)
            if item and item.widget():
                to_remove.append(item.widget())
        for w in to_remove:
            self._chain_layout.removeWidget(w)
            w.deleteLater()

        if self._chain_loading:
            self._chain_layout.addWidget(
                _wv._label("加载分类链…", color=_wv._C_MUTED, size=12)
            )
        elif self._selected_rec and self._chain:
            self._chain_layout.addWidget(
                _wv._label("采用后保存的 WoRMS 分类链", bold=True, size=12, color=_wv._C_MUTED)
            )
            for node in self._chain:
                row_w = QWidget()
                row_lay = QHBoxLayout(row_w)
                row_lay.setContentsMargins(4, 1, 4, 1)
                row_lay.setSpacing(8)
                rank_lbl = _wv._label(node.get("rank", ""), color=_wv._C_DIM, size=11)
                rank_lbl.setFixedWidth(72)
                row_lay.addWidget(rank_lbl)
                row_lay.addWidget(
                    _wv._label(node.get("scientificname", ""), font=_wv._MONO, size=11)
                )
                row_lay.addStretch()
                self._chain_layout.addWidget(row_w)
        else:
            self._chain_layout.addWidget(
                _wv._label("选择候选后预览标准分类阶元", color=_wv._C_DIM, size=12)
            )
        self._chain_layout.addStretch()




class WormsQuickFillDialog(QDialog):
    """工作台快捷 WoRMS 填充弹窗.

    Mirrors ``renderWormsPopupOverlay()`` / ``doWormsPopupSearch()`` in
    app.js lines ~12685–12760.

    Behaviour:
    - Search bar pre-filled with *initial_query* (typically current
      taxon group or scientific name in the specimen card).
    - Results list: each row shows sciname / rank / status badges /
      breadcrumb + 「填充」button.
    - Clicking 「填充」fills Latin-only fields (class→taxonGroup,
      order, family, genus, scientificname if Species rank) via
      ``fill_callback`` then closes the dialog.
    - Chinese fields (*Cn) are NEVER written.

    Parameters
    ----------
    service:
        WormsService for searches.
    fill_callback:
        Callable(rec: dict) invoked with the chosen WoRMS AphiaRecord.
        The callback is responsible for merging fields into the specimen.
    initial_query:
        Pre-filled search text (may be empty).
    parent:
        Parent widget.
    """

    def __init__(
        self,
        service: WormsService,
        fill_callback,
        *,
        initial_query: str = "",
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        _wv._refresh_palette()
        self._svc           = service
        self._fill_callback = fill_callback
        self._results:      list[dict] = []
        self._loading:      bool = False
        self._error:        str  = ""
        self._search_thread: Optional[QThread] = None

        self.setWindowTitle("从 WoRMS 查找分类")
        self.setMinimumSize(540, 440)
        self.setModal(True)
        _ff = local_font_css()
        self.setStyleSheet(
            f"QDialog {{ {_ff}background:{_wv._C_BG}; color:{_wv._C_TEXT}; }}"
            f"QLabel {{ color:{_wv._C_TEXT}; background:transparent; }}"
        )
        self._build_ui()

        # Pre-fill and auto-search if query provided
        if initial_query:
            self._search_input.setText(initial_query.strip())
            self._start_quick_fill_taxon_search()

    # ── UI ──────────────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 16, 20, 16)
        root.setSpacing(10)

        root.addWidget(_wv._label("从 WoRMS 查找分类", size=15, bold=True))

        # Search bar (oracle: worms-popup-search div ~12695)
        bar = QHBoxLayout()
        bar.setSpacing(8)

        self._search_input = QLineEdit()
        self._search_input.setObjectName("WQSearchInput")
        self._search_input.setPlaceholderText("输入拉丁学名…")
        self._search_input.setStyleSheet(
            f"QLineEdit#WQSearchInput {{ background:{_wv._C_INPUT}; border:1px solid {_wv._C_BORDER};"
            f"  border-radius:6px; padding:7px 10px; color:{_wv._C_TEXT}; font-size:13px;"
            f"  font-family:{_wv._MONO}; }}"
            f"QLineEdit#WQSearchInput:focus {{ border-color:{_wv._C_ACCENT}; }}"
        )
        self._search_input.returnPressed.connect(self._start_quick_fill_taxon_search)
        bar.addWidget(self._search_input, stretch=1)

        search_btn = QPushButton("搜索")
        search_btn.setObjectName("WQSearchBtn")
        search_btn.setStyleSheet(
            f"QPushButton#WQSearchBtn {{ background:{_wv._C_ACCENT}; color:{_wv._C_BG};"
            f"  border:none; border-radius:6px; padding:7px 16px; font-size:12px; font-weight:600; }}"
            f"QPushButton#WQSearchBtn:hover {{ background:{_wv._C_ACCENT_H}; }}"
        )
        search_btn.clicked.connect(self._start_quick_fill_taxon_search)
        bar.addWidget(search_btn)
        root.addLayout(bar)

        # Status label (loading / error)
        self._status_lbl = _wv._label("", color=_wv._C_MUTED, size=11)
        root.addWidget(self._status_lbl)

        # Results scroll area (oracle: worms-popup-results div ~12713)
        self._results_scroll = QScrollArea()
        self._results_scroll.setWidgetResizable(True)
        self._results_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._results_scroll.setStyleSheet("QScrollArea { background: transparent; }")

        self._results_container = QWidget()
        self._results_container.setStyleSheet("background:transparent;")
        self._results_layout = QVBoxLayout(self._results_container)
        self._results_layout.setContentsMargins(0, 0, 0, 0)
        self._results_layout.setSpacing(4)
        self._results_layout.addStretch()

        self._results_scroll.setWidget(self._results_container)
        root.addWidget(self._results_scroll, stretch=1)

        # Close button (oracle: worms-popup-cancel ~12736)
        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        btn_row.addStretch()
        close_btn = QPushButton("关闭")
        close_btn.setObjectName("WQCloseBtn")
        close_btn.setStyleSheet(
            f"QPushButton#WQCloseBtn {{ background:{_wv._C_PANEL}; color:{_wv._C_MUTED};"
            f"  border:1px solid {_wv._C_BORDER}; border-radius:6px; padding:7px 16px; font-size:12px; }}"
            f"QPushButton#WQCloseBtn:hover {{ color:{_wv._C_TEXT}; }}"
        )
        close_btn.clicked.connect(self.reject)
        btn_row.addWidget(close_btn)
        root.addLayout(btn_row)

    # ── Search (oracle: doWormsPopupSearch ~12743) ──────────────────────────

    def _start_quick_fill_taxon_search(self) -> None:
        q = self._search_input.text().strip()
        if not q:
            return
        if self._search_thread and self._search_thread.isRunning():
            return

        self._loading = True
        self._error = ""
        self._results = []
        self._status_lbl.setText("查询中…")
        self._render_results()

        worker = _QuickSearchWorker(self._svc, q)
        thread = QThread(self)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(self._show_quick_fill_taxon_search_results)
        worker.error.connect(self._show_quick_fill_taxon_search_error)
        worker.finished.connect(thread.quit)
        worker.error.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        self._search_thread = thread
        thread.start()

    def _show_quick_fill_taxon_search_results(self, results: list[dict]) -> None:
        self._loading = False
        self._results = results
        self._status_lbl.setText(
            f"找到 {len(results)} 条结果" if results else "无结果"
        )
        self._render_results()

    def _show_quick_fill_taxon_search_error(self, msg: str) -> None:
        self._loading = False
        self._error = msg
        self._status_lbl.setText(f"错误: {msg}")
        self._render_results()

    # ── Result rendering (oracle: worms-popup-result rows ~12714) ──────────

    def _render_results(self) -> None:
        # Clear old result widgets
        to_remove = []
        for i in range(self._results_layout.count()):
            item = self._results_layout.itemAt(i)
            if item and item.widget():
                to_remove.append(item.widget())
        for w in to_remove:
            self._results_layout.removeWidget(w)
            w.deleteLater()

        if self._loading:
            self._results_layout.addWidget(
                _wv._label("查询中…", color=_wv._C_MUTED, size=12)
            )
        elif not self._results:
            pass  # status label already shows the message
        else:
            for rec in self._results:
                row_w = QWidget()
                row_w.setObjectName("WQResultRow")
                row_w.setStyleSheet(
                    f"QWidget#WQResultRow {{ background:{_wv._C_PANEL}; border:1px solid {_wv._C_BORDER};"
                    f"  border-radius:6px; }}"
                    f"QWidget#WQResultRow:hover {{ border-color:{_wv._C_ACCENT}; }}"
                )
                row_lay = QVBoxLayout(row_w)
                row_lay.setContentsMargins(10, 8, 10, 8)
                row_lay.setSpacing(3)

                # Top: sciname + rank/status badges (oracle: ~12716–12718)
                top = QHBoxLayout()
                top.setSpacing(6)
                sciname = rec.get("scientificname") or "?"
                top.addWidget(_wv._label(sciname, bold=True, font=_wv._MONO, size=12))
                rank = rec.get("rank") or ""
                status = (rec.get("status") or "").lower()
                if rank:
                    top.addWidget(_wv._badge(rank, "rank"))
                if status:
                    top.addWidget(_wv._badge(status, "accepted" if status == "accepted" else "unaccepted"))
                top.addStretch()

                # 填充 button (oracle: worms-popup-fill-btn ~12721)
                fill_btn = QPushButton("填充")
                fill_btn.setObjectName("WQFillBtn")
                fill_btn.setFixedWidth(52)
                fill_btn.setStyleSheet(
                    f"QPushButton#WQFillBtn {{ background:{_wv._C_ACCENT_12}; color:{_wv._C_ACCENT};"
                    f"  border:1px solid {_wv._C_ACCENT_30}; border-radius:5px;"
                    f"  padding:3px 8px; font-size:11px; font-weight:600; }}"
                    f"QPushButton#WQFillBtn:hover {{ background:{_wv._C_ACCENT_22};"
                    f"  border-color:{_wv._C_ACCENT}; }}"
                )
                _rec = rec  # capture loop var
                fill_btn.clicked.connect(lambda _checked=False, r=_rec: self._fill_active_specimen_from_quick_result(r))
                top.addWidget(fill_btn)
                row_lay.addLayout(top)

                # Breadcrumb: class > order > family (oracle: ~12719)
                bc = " > ".join(
                    p for p in [rec.get("class"), rec.get("order"), rec.get("family")]
                    if p
                )
                if bc:
                    row_lay.addWidget(_wv._label(bc, color=_wv._C_DIM, size=11))

                self._results_layout.addWidget(row_w)

        self._results_layout.addStretch()

    # ── Fill action (oracle: fillBtn click ~12722–12727) ──────────────────

    def _fill_active_specimen_from_quick_result(self, rec: dict) -> None:
        """Invoke fill_callback with *rec*, then close the dialog.

        Chinese fields (*Cn) are never touched — that constraint is
        enforced by the callback implementation, not here.
        """
        try:
            self._fill_callback(rec)
        except Exception:
            pass
        self.accept()
