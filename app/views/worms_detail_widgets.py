"""Result and detail widgets for the WoRMS view."""
from __future__ import annotations

from typing import Any, Optional

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QScrollArea, QFrame, QSizePolicy, QAbstractItemView,
    QStackedWidget, QTextBrowser,
)

from app.config.theme import local_font_css
from app.services.worms_service import WormsService
from app.views import worms_view_support as _wv
from app.views.worms_workers import _LoadMoreWorker

# ── Result item widget (mirrors worms-result-item) ────────────────────────────

class _ResultItemWidget(QWidget):
    """One row in the worms-result-list.  Wraps a QWidget with click signal."""

    clicked = pyqtSignal(dict)

    def __init__(self, rec: dict, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        _wv._refresh_palette()
        self._rec = rec

        self.setObjectName("WResultItem")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        _ff = local_font_css()
        self.setStyleSheet(
            "QWidget#WResultItem {"
            f"  {_ff} background: transparent; border: 1px solid transparent;"
            f"  border-radius: 6px;"
            "}"
            "QWidget#WResultItem:hover {"
            f"  background: {_wv._C_ACCENT_08};"
            f"  border: 1px solid {_wv._C_BORDER};"
            "}"
        )

        root = QVBoxLayout(self)
        root.setContentsMargins(10, 8, 10, 8)
        root.setSpacing(3)

        # Top row: sciname | authority | badges
        top = QHBoxLayout()
        top.setSpacing(6)

        sciname = rec.get("scientificname") or "?"
        sci_lbl = _wv._label(sciname, font=_wv._MONO, bold=True)
        top.addWidget(sci_lbl)

        if rec.get("authority"):
            auth_lbl = _wv._label(rec["authority"], color=_wv._C_MUTED, size=11)
            auth_lbl.setStyleSheet(
                auth_lbl.styleSheet() + " font-style:italic;"
            )
            top.addWidget(auth_lbl)

        top.addStretch()

        # Badges
        rank = rec.get("rank") or ""
        status = (rec.get("status") or "").lower()
        if rank:
            top.addWidget(_wv._badge(rank, "rank"))
        if status:
            badge_kind = "accepted" if status == "accepted" else "unaccepted"
            top.addWidget(_wv._badge(status, badge_kind))

        root.addLayout(top)

        # Breadcrumb: class > order > family
        breadcrumb_parts = [
            rec.get("class"), rec.get("order"), rec.get("family")
        ]
        breadcrumb = " > ".join(p for p in breadcrumb_parts if p)
        if breadcrumb:
            root.addWidget(_wv._label(breadcrumb, color=_wv._C_DIM, size=11))

        # Valid name hint (only when not accepted)
        if status != "accepted" and rec.get("valid_name"):
            vn = _wv._label(f"→ accepted: {rec['valid_name']}", color=_wv._C_WARN, size=11)
            root.addWidget(vn)

    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        self.clicked.emit(self._rec)
        super().mousePressEvent(event)


# ── Chain node widget (worms-chain-node) ──────────────────────────────────────

def _chain_node_widget(node: dict, is_current: bool = False) -> QWidget:
    """One row in the worms-classification-chain."""
    w = QWidget()
    w.setObjectName("WChainNode")
    border_color = _wv._C_ACCENT if is_current else _wv._C_BORDER_20
    bg = _wv._C_ACCENT_06 if is_current else "transparent"
    w.setStyleSheet(
        f"QWidget#WChainNode {{ border-left:2px solid {border_color};"
        f"  padding-left:10px; background:{bg}; border-radius:0px; }}"
    )

    row = QHBoxLayout(w)
    row.setContentsMargins(10, 3, 6, 3)
    row.setSpacing(8)

    rank_lbl = _wv._label(node.get("rank", ""), color=_wv._C_MUTED, size=11)
    rank_lbl.setFixedWidth(80)
    row.addWidget(rank_lbl)

    name_lbl = _wv._label(node.get("scientificname", ""), font=_wv._MONO, size=12)
    row.addWidget(name_lbl)

    row.addStretch()

    aphia_id = node.get("AphiaID", 0)
    if aphia_id:
        id_lbl = _wv._label(f"#{aphia_id}", color=_wv._C_DIM, size=11)
        row.addWidget(id_lbl)

    return w


# ── Tab content builders ───────────────────────────────────────────────────────

def _build_overview_tab(rec: dict) -> QWidget:
    """worms-overview-tab: key-value field list."""
    c = QWidget()
    lay = QVBoxLayout(c)
    lay.setContentsMargins(2, 6, 2, 6)
    lay.setSpacing(2)

    fields = [
        ("AphiaID",  str(rec.get("AphiaID", ""))),
        ("学名",      rec.get("scientificname", "")),
        ("命名人",    rec.get("authority", "")),
        ("等级",      rec.get("rank", "")),
        ("状态",      rec.get("status", "")),
        ("界",        rec.get("kingdom", "")),
        ("门",        rec.get("phylum", "")),
        ("纲",        rec.get("class", "")),
        ("目",        rec.get("order", "")),
        ("科",        rec.get("family", "")),
        ("属",        rec.get("genus", "")),
        ("URL",       rec.get("url", "")),
        ("LSID",      rec.get("lsid", "")),
    ]
    for field_name, val in fields:
        if not val:
            continue
        row = QHBoxLayout()
        row.setContentsMargins(0, 3, 0, 3)
        lbl = _wv._label(field_name, color=_wv._C_MUTED, size=12)
        lbl.setFixedWidth(66)
        lbl.setAlignment(Qt.AlignmentFlag.AlignTop)
        row.addWidget(lbl)
        val_lbl = _wv._label(val, size=12)
        val_lbl.setWordWrap(True)
        row.addWidget(val_lbl, stretch=1)
        lay.addLayout(row)

    # Habitat flags
    habitat = []
    if rec.get("isMarine"):      habitat.append("海洋")
    if rec.get("isFreshwater"):  habitat.append("淡水")
    if rec.get("isBrackish"):    habitat.append("半咸水")
    if rec.get("isTerrestrial"): habitat.append("陆地")
    if habitat:
        row = QHBoxLayout()
        row.setContentsMargins(0, 3, 0, 3)
        lbl = _wv._label("生境", color=_wv._C_MUTED, size=12)
        lbl.setFixedWidth(66)
        row.addWidget(lbl)
        row.addWidget(_wv._label(" / ".join(habitat), size=12))
        lay.addLayout(row)

    lay.addStretch()
    return c


def _build_children_tab(
    children: list[dict],
    loading: bool,
    *,
    has_more: bool = False,
    on_child_click: Optional[Any] = None,
    on_load_more: Optional[Any] = None,
) -> QWidget:
    """worms-children-tab: list of child taxa.

    Parameters
    ----------
    children:
        List of child AphiaRecord dicts.
    loading:
        True while the initial fetch is in progress.
    has_more:
        True when a "加载更多" button should be shown (≥50 children returned).
        Oracle: renderWormsChildrenTab app.js ~12598.
    on_child_click:
        Optional callback(rec: dict) invoked when a child row is clicked.
    on_load_more:
        Optional callback() invoked when the "加载更多" button is clicked.
    """
    c = QWidget()
    lay = QVBoxLayout(c)
    lay.setContentsMargins(2, 6, 2, 6)
    lay.setSpacing(3)

    if loading:
        lay.addWidget(_wv._label("加载子分类…", color=_wv._C_MUTED, size=12))
    elif not children:
        lay.addWidget(_wv._label("无子分类", color=_wv._C_MUTED, size=12))
    else:
        for child in children:
            row_w = QWidget()
            row_w.setObjectName("WChildItem")
            row_w.setCursor(Qt.CursorShape.PointingHandCursor)
            row_w.setStyleSheet(
                f"QWidget#WChildItem:hover {{ background: {_wv._C_ACCENT_06}; "
                f"border-radius: 4px; }}"
            )
            row_lay = QHBoxLayout(row_w)
            row_lay.setContentsMargins(4, 3, 4, 3)
            row_lay.setSpacing(6)
            row_lay.addWidget(_wv._label(child.get("scientificname", ""), font=_wv._MONO, size=12))
            if child.get("rank"):
                row_lay.addWidget(_wv._badge(child["rank"], "rank"))
            row_lay.addStretch()
            lay.addWidget(row_w)
            if callable(on_child_click):
                _child = child  # capture loop var
                row_w.mousePressEvent = lambda _e, rec=_child: on_child_click(rec)  # type: ignore[method-assign]

        # "加载更多" button — oracle: app.js renderWormsChildrenTab ~12598
        if has_more and callable(on_load_more):
            more_btn = QPushButton("加载更多…")
            more_btn.setObjectName("WMoreBtn")
            more_btn.setStyleSheet(
                f"QPushButton#WMoreBtn {{ background:none; color:{_wv._C_ACCENT};"
                f"  border:1px solid {_wv._C_ACCENT_30}; border-radius:6px;"
                f"  padding:5px 12px; font-size:12px; }}"
                f"QPushButton#WMoreBtn:hover {{ background:{_wv._C_ACCENT_08}; }}"
            )
            more_btn.clicked.connect(on_load_more)
            lay.addWidget(more_btn)

    lay.addStretch()
    return c


def _build_synonyms_tab(synonyms: list[dict], loading: bool) -> QWidget:
    """worms-synonyms-tab: list of synonym records."""
    c = QWidget()
    lay = QVBoxLayout(c)
    lay.setContentsMargins(2, 6, 2, 6)
    lay.setSpacing(3)

    if loading:
        lay.addWidget(_wv._label("加载同义词…", color=_wv._C_MUTED, size=12))
    elif not synonyms:
        lay.addWidget(_wv._label("无同义词记录", color=_wv._C_MUTED, size=12))
    else:
        for syn in synonyms:
            row_w = QWidget()
            row_lay = QHBoxLayout(row_w)
            row_lay.setContentsMargins(4, 3, 4, 3)
            row_lay.setSpacing(6)
            row_lay.addWidget(_wv._label(syn.get("scientificname", ""), font=_wv._MONO, size=12))
            status = (syn.get("status") or "").lower()
            if status:
                row_lay.addWidget(_wv._badge(status, "accepted" if status == "accepted" else "unaccepted"))
            if syn.get("authority"):
                row_lay.addWidget(_wv._label(syn["authority"], color=_wv._C_MUTED, size=11))
            row_lay.addStretch()
            lay.addWidget(row_w)
    lay.addStretch()
    return c


# ── Detail panel ───────────────────────────────────────────────────────────────

class _DetailPanel(QWidget):
    """worms-detail-panel: right side of worms-body.

    Shows empty placeholder until a taxon is selected, then shows the
    full detail view with classification chain + tabs (overview / children
    / synonyms) + worms-fill-btn.

    Children pagination:  _children_offset / _children_has_more mirror
    app.js state.worms.childrenOffset / childrenHasMore.
    Oracle: renderWormsChildrenTab ~12598.
    """

    TAB_OVERVIEW  = "overview"
    TAB_CHILDREN  = "children"
    TAB_SYNONYMS  = "synonyms"
    TAB_LABELS    = {"overview": "概览", "children": "子分类", "synonyms": "同义词"}

    # Emitted when user clicks "填充到当前标本".
    fill_requested = pyqtSignal(dict)   # the selected WoRMS record
    # Emitted when a child taxon row is clicked (to navigate).
    child_selected = pyqtSignal(dict)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        _wv._refresh_palette()
        self.setObjectName("WDetailPanel")
        _ff = local_font_css()
        self.setStyleSheet(
            f"QWidget#WDetailPanel {{ {_ff} background:{_wv._C_PANEL};"
            f"  border:1px solid {_wv._C_BORDER}; border-radius:8px; }}"
        )

        self._current_tab: str = self.TAB_OVERVIEW
        self._rec:      Optional[dict] = None
        self._chain:    list[dict] = []
        self._synonyms: list[dict] = []
        self._children: list[dict] = []
        self._loading:  bool = False

        # Children pagination state (oracle: app.js childrenOffset/childrenHasMore)
        self._children_offset: int = 1
        self._children_has_more: bool = False
        self._service: Optional[WormsService] = None
        self._load_more_thread: Optional[QThread] = None
        self._load_more_worker: Optional["_LoadMoreWorker"] = None

        self._root = QVBoxLayout(self)
        self._root.setContentsMargins(16, 16, 16, 16)
        self._root.setSpacing(10)

        self._render_worms_detail_panel()

    # ── Public API ─────────────────────────────────────────────────────

    def set_service(self, service: WormsService) -> None:
        """Provide a WormsService instance for load-more pagination."""
        self._service = service

    def show_empty(self) -> None:
        self._rec = None
        self._chain = []
        self._synonyms = []
        self._children = []
        self._loading = False
        self._current_tab = self.TAB_OVERVIEW
        self._children_offset = 1
        self._children_has_more = False
        self._render_worms_detail_panel()

    def show_loading(self, rec: dict) -> None:
        self._rec = rec
        self._chain = []
        self._synonyms = []
        self._children = []
        self._loading = True
        self._current_tab = self.TAB_OVERVIEW
        self._children_offset = 1
        self._children_has_more = False
        self._render_worms_detail_panel()

    def show_detail(self, rec: dict, chain: list[dict],
                    synonyms: list[dict], children: list[dict]) -> None:
        self._rec      = rec
        self._chain    = chain
        self._synonyms = synonyms
        self._children = children
        self._loading  = False
        # Detect has_more: ≥50 means there may be more (oracle: app.js ~12674)
        self._children_has_more = len(children) >= 50
        self._children_offset = 1
        self._render_worms_detail_panel()

    def set_tab(self, tab: str) -> None:
        self._current_tab = tab
        self._render_worms_detail_panel()

    def update_fill_label(self, specimen_label: str) -> None:
        """Refresh the fill button text after active specimen changes."""
        # Re-render to pick up new label; only meaningful when detail is shown.
        if self._rec is not None:
            self._render_worms_detail_panel()

    # ── Children pagination ─────────────────────────────────────────

    def _on_load_more_children(self) -> None:
        """Fetch the next page of children.

        Oracle: renderWormsChildrenTab "加载更多" button handler app.js ~12600.
        Increments childrenOffset and fires _LoadMoreWorker on a background thread.
        """
        if self._rec is None or self._service is None:
            return
        if self._load_more_thread and self._load_more_thread.isRunning():
            return

        aphia_id = self._rec.get("AphiaID")
        if not aphia_id:
            return

        self._children_offset += 1
        worker = _LoadMoreWorker(self._service, int(aphia_id), self._children_offset)
        thread = QThread(self)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(self._on_load_more_done)
        worker.error.connect(lambda _: None)   # silently ignore errors (oracle behaviour)
        worker.finished.connect(thread.quit)
        worker.error.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        self._load_more_worker = worker
        self._load_more_thread = thread
        thread.start()

    def _on_load_more_done(self, more: list[dict]) -> None:
        """Append fetched children and re-render the children tab."""
        if more:
            self._children = self._children + more
            self._children_has_more = len(more) >= 50
        else:
            self._children_has_more = False
        self._render_worms_detail_panel()

    # ── Rendering ──────────────────────────────────────────────────────

    def _clear_worms_detail_layout(self) -> None:
        while self._root.count():
            item = self._root.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
            elif item.layout():
                pass  # nested layout — just remove reference

    def _render_worms_detail_panel(self) -> None:
        self._clear_worms_detail_layout()

        if self._rec is None:
            # worms-detail-empty
            empty = _wv._label(
                "搜索物种名并点击结果查看分类详情",
                color=_wv._C_MUTED, size=13
            )
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            empty.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
            self._root.addWidget(empty)
            return

        rec = self._rec

        # ── worms-detail-header ────────────────────────────────────────
        header_w = QWidget()
        header_lay = QHBoxLayout(header_w)
        header_lay.setContentsMargins(0, 0, 0, 4)
        header_lay.setSpacing(8)

        name_lbl = _wv._label(
            rec.get("scientificname") or "?",
            size=15, bold=True, font=_wv._MONO
        )
        name_lbl.setWordWrap(True)
        header_lay.addWidget(name_lbl, stretch=1)

        if rec.get("authority"):
            header_lay.addWidget(_wv._label(rec["authority"], color=_wv._C_MUTED, size=11))
        if rec.get("rank"):
            header_lay.addWidget(_wv._badge(rec["rank"], "rank"))
        status = (rec.get("status") or "").lower()
        if status:
            header_lay.addWidget(_wv._badge(status, "accepted" if status == "accepted" else "unaccepted"))

        self._root.addWidget(header_w)

        # Valid name hint
        if status != "accepted" and rec.get("valid_name"):
            self._root.addWidget(
                _wv._label(f"→ accepted: {rec['valid_name']} (AphiaID: {rec.get('valid_AphiaID', '?')})",
                       color=_wv._C_WARN, size=11)
            )

        # WoRMS external link (text-only label)
        aphia = rec.get("AphiaID")
        if aphia:
            link_lbl = _wv._label(
                f"WoRMS: marinespecies.org/aphia.php?id={aphia}",
                color=_wv._C_ACCENT, size=11
            )
            self._root.addWidget(link_lbl)

        self._root.addWidget(_wv._divider())

        # ── worms-classification-chain ─────────────────────────────────
        if self._loading:
            self._root.addWidget(_wv._label("加载分类链…", color=_wv._C_MUTED, size=12))
        elif self._chain:
            chain_container = QWidget()
            chain_container.setObjectName("WChainContainer")
            chain_container.setStyleSheet(
                f"QWidget#WChainContainer {{ background:{_wv._C_INPUT};"
                f" border-radius:6px; }}"
            )
            chain_lay = QVBoxLayout(chain_container)
            chain_lay.setContentsMargins(0, 6, 0, 6)
            chain_lay.setSpacing(0)

            current_aphia = rec.get("AphiaID")
            for node in self._chain:
                is_current = (node.get("AphiaID") == current_aphia)
                chain_lay.addWidget(_chain_node_widget(node, is_current))

            self._root.addWidget(chain_container)

        # ── worms-detail-tabs ──────────────────────────────────────────
        tab_bar = QWidget()
        tab_bar.setObjectName("WTabBar")
        tab_bar.setStyleSheet(
            f"QWidget#WTabBar {{ border-bottom:1px solid {_wv._C_BORDER_10}; }}"
        )
        tab_row = QHBoxLayout(tab_bar)
        tab_row.setContentsMargins(0, 4, 0, 0)
        tab_row.setSpacing(0)

        for tab_id, tab_label in self.TAB_LABELS.items():
            btn = QPushButton(tab_label)
            btn.setObjectName("WTabBtn")
            is_sel = (self._current_tab == tab_id)
            accent_border = _wv._C_ACCENT if is_sel else "transparent"
            text_color    = _wv._C_ACCENT if is_sel else _wv._C_MUTED
            btn.setStyleSheet(
                f"QPushButton#WTabBtn {{ background:none; border:none;"
                f" border-bottom:2px solid {accent_border}; margin-bottom:-1px;"
                f" color:{text_color}; padding:7px 14px; font-size:12px;"
                f" font-weight:{'600' if is_sel else '500'}; }}"
                f"QPushButton#WTabBtn:hover {{ color:{_wv._C_TEXT}; }}"
            )
            _tab = tab_id  # capture loop var
            btn.clicked.connect(lambda _, t=_tab: self.set_tab(t))
            tab_row.addWidget(btn)

        tab_row.addStretch()
        self._root.addWidget(tab_bar)

        # ── worms-tab-content ──────────────────────────────────────────
        if self._current_tab == self.TAB_OVERVIEW:
            content = _build_overview_tab(rec)
        elif self._current_tab == self.TAB_CHILDREN:
            content = _build_children_tab(
                self._children,
                self._loading,
                has_more=self._children_has_more,
                on_child_click=self.child_selected.emit,
                on_load_more=self._on_load_more_children,
            )
        else:
            content = _build_synonyms_tab(self._synonyms, self._loading)

        # Scroll area for tab content
        tab_scroll = QScrollArea()
        tab_scroll.setWidgetResizable(True)
        tab_scroll.setFrameShape(QFrame.Shape.NoFrame)
        tab_scroll.setStyleSheet("QScrollArea { background: transparent; }")
        tab_scroll.setWidget(content)
        self._root.addWidget(tab_scroll, stretch=1)

        # ── worms-fill-btn (web parity: 填充到当前标本) ────────────────
        self._root.addWidget(_wv._divider())

        fill_btn = QPushButton("填充到当前标本")
        fill_btn.setObjectName("WFillBtn")
        fill_btn.setToolTip("将 WoRMS 分类信息（纲/目/科/属/学名）写入工作区当前标本")
        fill_btn.setStyleSheet(
            f"QPushButton#WFillBtn {{ background:{_wv._C_ACCENT_12};"
            f"  color:{_wv._C_ACCENT}; border:1px solid {_wv._C_ACCENT_30};"
            f"  border-radius:6px; padding:7px 14px; font-size:12px; font-weight:600; }}"
            f"QPushButton#WFillBtn:hover {{ background:{_wv._C_ACCENT_20};"
            f"  border-color:{_wv._C_ACCENT}; }}"
            f"QPushButton#WFillBtn:pressed {{ background:{_wv._C_ACCENT_28}; }}"
        )
        _rec = rec  # capture
        fill_btn.clicked.connect(lambda: self.fill_requested.emit(_rec))
        self._root.addWidget(fill_btn)


