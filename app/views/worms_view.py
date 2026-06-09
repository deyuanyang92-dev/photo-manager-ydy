"""worms_view.py — WoRMS (World Register of Marine Species) verification view.

Faithfully mirrors the web prototype's renderWormsPage() structure
(pages_dom.json: "WoRMS 分类库", app.js ~12378).

Layout
------
worms-header
  worms-title-row  [h2 serif] [marinespecies.org link]
  worms-desc

worms-body  (HSplitter: left flex-6 / right flex-4)
  worms-search-panel (left)
    worms-search-bar  [mono input] [like-toggle checkbox] [搜索 btn]
    worms-result-list
      worms-result-item  (sciname / authority / rank+status badges / breadcrumb / valid-name)
    loading / empty / error state

  worms-detail-panel (right)
    worms-detail-empty  (before selection)
    worms-detail  (after selection)
      worms-detail-header  name / authority / rank+status badges / WoRMS link
      worms-classification-chain  nodes (rank / name / #AphiaID)
      worms-detail-tabs  [概览] [子分类] [同义词]
      worms-tab-content
      worms-fill-btn  (填充到当前标本 — web parity)

Batch jobs: collapsible footer panel (de-emphasised; not in web renderWormsPage
but kept for service completeness).

All network I/O runs on QThread; main UI thread is never blocked.

Oracle:
  app.js  renderWormsPage ~12378
  app.js  renderWormsResultItem ~12452
  app.js  renderWormsDetail ~12475
  app.js  renderWormsOverviewTab ~12546
  app.js  renderWormsChildrenTab ~12584
  app.js  renderWormsSynonymsTab ~12614
  app.js  wormsFillToSpecimen ~11447
  styles.css  ~5250–5650 (worms-* classes)
  docs/modules/worms.md
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Optional, TYPE_CHECKING

from PyQt6.QtCore import Qt, QThread, QTimer, pyqtSignal, QObject
from PyQt6.QtGui import QColor, QFont
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QLineEdit, QListWidget, QListWidgetItem, QSplitter,
    QGroupBox, QScrollArea, QFrame, QSizePolicy, QCheckBox,
    QAbstractItemView, QStackedWidget, QTextBrowser, QMessageBox,
    QProgressBar, QDialog,
)

from app.views.base_view import BaseView
from app.services.worms_service import WormsService
from app.config.theme import local_font_css

if TYPE_CHECKING:
    from app.app_context import AppContext

# ── Colour palette — resolved from the LIVE active theme ─────────────────────
# Previously these were hardcoded deep-teal constants, which force-painted the
# whole WoRMS page (search box, 批量验证任务 panel, detail cards) dark
# regardless of the chosen theme → under a light theme they rendered
# dark/broken.  Now they are refreshed from the active theme tokens by
# _refresh_palette(), called at the top of WormsView._setup_ui() and at the
# start of __init__ of every sub-widget / dialog that reads `_C_*` (they may be
# built independently of the main view).  The dark hex below are fallbacks only.
_C_BG       = "#08161b"
_C_PANEL    = "#0f2127"
_C_INPUT    = "#0b2025"   # inset / list / secondary panel bg
_C_BORDER   = "rgba(145, 182, 181, 0.16)"
_C_ACCENT   = "#29b9ab"
_C_ACCENT_H = "#31d4c4"
_C_TEXT     = "#eef3ef"
_C_MUTED    = "#87a2a1"
_C_DIM      = "#5f7d7a"
_C_SUCCESS  = "#36c98f"
_C_DANGER   = "#e66e63"
_C_WARN     = "#f1bd57"
_C_RUNNING  = "#6699ff"  # running-job status colour (no dedicated token → accent)

# Pre-built rgba tint strings (alpha-blended accent/success/danger/warn/border),
# rebuilt from the live tokens by _refresh_palette() so tints track the theme.
_C_ACCENT_06 = "rgba(41,185,171,0.06)"
_C_ACCENT_08 = "rgba(41,185,171,0.08)"
_C_ACCENT_12 = "rgba(41,185,171,0.12)"
_C_ACCENT_15 = "rgba(41,185,171,0.15)"
_C_ACCENT_20 = "rgba(41,185,171,0.20)"
_C_ACCENT_22 = "rgba(41,185,171,0.22)"
_C_ACCENT_28 = "rgba(41,185,171,0.28)"
_C_ACCENT_30 = "rgba(41,185,171,0.30)"
_C_SUCCESS_15 = "rgba(54,201,143,0.15)"
_C_DANGER_15 = "rgba(230,110,99,0.15)"
_C_WARN_25 = "rgba(241,189,87,0.25)"
_C_BORDER_10 = "rgba(145,182,181,0.10)"
_C_BORDER_20 = "rgba(145,182,181,0.20)"
_C_BORDER_25 = "rgba(145,182,181,0.25)"

_MONO      = '"JetBrains Mono", "Cascadia Code", "SF Mono", Consolas, "DejaVu Sans Mono", monospace'
_SERIF     = '"Noto Serif SC", "Source Han Serif SC", "Songti SC", SimSun, Georgia, serif'
_SANS      = '"Noto Sans SC", "Source Han Sans SC", "Microsoft YaHei", "Segoe UI", sans-serif'


def _rgb_tuple(color: str) -> tuple[int, int, int]:
    """Parse a '#rrggbb' or 'rgb(a)(...)' token into an (r, g, b) tuple."""
    s = (color or "").strip()
    if s.startswith("#") and len(s) >= 7:
        try:
            return (int(s[1:3], 16), int(s[3:5], 16), int(s[5:7], 16))
        except ValueError:
            return (41, 185, 171)
    if s.lower().startswith("rgb"):
        try:
            nums = s[s.index("(") + 1: s.index(")")].split(",")
            return (int(float(nums[0])), int(float(nums[1])), int(float(nums[2])))
        except (ValueError, IndexError):
            return (145, 182, 181)
    return (41, 185, 171)


def _tint(color: str, alpha: float) -> str:
    r, g, b = _rgb_tuple(color)
    return f"rgba({r},{g},{b},{alpha})"


def _refresh_palette() -> None:
    """Rebind the module `_C_*` colours to the current theme tokens."""
    global _C_BG, _C_PANEL, _C_INPUT, _C_BORDER, _C_ACCENT, _C_ACCENT_H
    global _C_TEXT, _C_MUTED, _C_DIM, _C_SUCCESS, _C_DANGER, _C_WARN, _C_RUNNING
    global _C_ACCENT_06, _C_ACCENT_08, _C_ACCENT_12, _C_ACCENT_15, _C_ACCENT_20
    global _C_ACCENT_22, _C_ACCENT_28, _C_ACCENT_30
    global _C_SUCCESS_15, _C_DANGER_15, _C_WARN_25
    global _C_BORDER_10, _C_BORDER_20, _C_BORDER_25
    from app.config.theme import TOKENS
    g = TOKENS.get
    _C_BG       = g("bg", _C_BG)
    _C_PANEL    = g("panel", _C_PANEL)
    _C_INPUT    = g("input_bg", _C_INPUT)
    _C_BORDER   = g("border", _C_BORDER)
    _C_ACCENT   = g("accent", _C_ACCENT)
    _C_ACCENT_H = g("accent_hover", _C_ACCENT_H)
    _C_TEXT     = g("text", _C_TEXT)
    _C_MUTED    = g("muted", _C_MUTED)
    _C_DIM      = g("muted_dim", _C_DIM)
    _C_SUCCESS  = g("success", _C_SUCCESS)
    _C_DANGER   = g("danger", _C_DANGER)
    _C_WARN     = g("warn", _C_WARN)
    _C_RUNNING  = g("accent", _C_RUNNING)
    # Alpha tints derived from the live base colours
    _C_ACCENT_06 = _tint(_C_ACCENT, 0.06)
    _C_ACCENT_08 = _tint(_C_ACCENT, 0.08)
    _C_ACCENT_12 = _tint(_C_ACCENT, 0.12)
    _C_ACCENT_15 = _tint(_C_ACCENT, 0.15)
    _C_ACCENT_20 = _tint(_C_ACCENT, 0.20)
    _C_ACCENT_22 = _tint(_C_ACCENT, 0.22)
    _C_ACCENT_28 = _tint(_C_ACCENT, 0.28)
    _C_ACCENT_30 = _tint(_C_ACCENT, 0.30)
    _C_SUCCESS_15 = _tint(_C_SUCCESS, 0.15)
    _C_DANGER_15 = _tint(_C_DANGER, 0.15)
    _C_WARN_25 = _tint(_C_WARN, 0.25)
    _C_BORDER_10 = _tint(_C_BORDER, 0.10)
    _C_BORDER_20 = _tint(_C_BORDER, 0.20)
    _C_BORDER_25 = _tint(_C_BORDER, 0.25)


_FALLBACK_DATA_DIR = Path.home() / ".photo_workbench" / "data"


# ── Workers ────────────────────────────────────────────────────────────────────

class _SearchWorker(QObject):
    """Run WormsService.search() on a background thread."""

    finished = pyqtSignal(list)   # list of AphiaRecord dicts
    error    = pyqtSignal(str)

    def __init__(self, service: WormsService, name: str, like: bool) -> None:
        super().__init__()
        self._svc  = service
        self._name = name
        self._like = like

    def run(self) -> None:
        try:
            self.finished.emit(self._svc.search(self._name, like=self._like))
        except Exception as exc:
            self.error.emit(str(exc))


class _DetailWorker(QObject):
    """Fetch classification + synonyms + children for one AphiaID."""

    finished = pyqtSignal(dict)   # {"chain": [...], "synonyms": [...], "children": [...]}
    error    = pyqtSignal(str)

    def __init__(self, service: WormsService, aphia_id: int) -> None:
        super().__init__()
        self._svc      = service
        self._aphia_id = aphia_id

    def run(self) -> None:
        try:
            raw_chain = self._svc.classification(self._aphia_id)
            chain     = self._svc.flatten_classification(raw_chain)
            synonyms  = self._svc.synonyms(self._aphia_id)
            try:
                kids = self._svc.children(self._aphia_id, offset=1)
            except Exception:
                kids = []
            self.finished.emit({"chain": chain, "synonyms": synonyms, "children": kids})
        except Exception as exc:
            self.error.emit(str(exc))


class _LoadMoreWorker(QObject):
    """Fetch the next page of children for a taxon (load-more pagination).

    Oracle: renderWormsChildrenTab app.js ~12599–12609.
    """

    finished = pyqtSignal(list)   # additional children
    error    = pyqtSignal(str)

    def __init__(self, service: WormsService, aphia_id: int, offset: int) -> None:
        super().__init__()
        self._svc      = service
        self._aphia_id = aphia_id
        self._offset   = offset

    def run(self) -> None:
        try:
            kids = self._svc.children(self._aphia_id, offset=self._offset)
            self.finished.emit(kids if isinstance(kids, list) else [])
        except Exception as exc:
            self.error.emit(str(exc))


# ── Tiny style helpers ─────────────────────────────────────────────────────────

def _label(text: str, *,
           color: str = "",
           size: int = 13,
           bold: bool = False,
           font: str = _SANS,
           wrap: bool = False) -> QLabel:
    lbl = QLabel(text)
    if not color:
        color = _C_TEXT
    weight = "700" if bold else "500"
    lbl.setStyleSheet(
        f"color:{color}; font-size:{size}px; font-weight:{weight}; font-family:{font};"
        " background:transparent;"
    )
    if wrap:
        lbl.setWordWrap(True)
    return lbl


def _badge(text: str, kind: str) -> QLabel:
    """Render a worms-badge pill.  kind = 'rank' | 'accepted' | 'unaccepted'."""
    lbl = QLabel(text)
    if kind == "rank":
        lbl.setStyleSheet(
            f"font-size:10px; padding:1px 6px; border-radius:4px; font-weight:600;"
            f" background:{_C_ACCENT_15}; color:{_C_ACCENT};"
        )
    elif kind == "accepted":
        lbl.setStyleSheet(
            f"font-size:10px; padding:1px 6px; border-radius:4px; font-weight:600;"
            f" background:{_C_SUCCESS_15}; color:{_C_SUCCESS};"
        )
    else:  # unaccepted
        lbl.setStyleSheet(
            f"font-size:10px; padding:1px 6px; border-radius:4px; font-weight:600;"
            f" background:{_C_DANGER_15}; color:{_C_DANGER};"
        )
    return lbl


def _divider() -> QFrame:
    """Thin horizontal rule."""
    div = QFrame()
    div.setFrameShape(QFrame.Shape.HLine)
    div.setStyleSheet(f"background:{_C_BORDER_10}; max-height:1px; border:none;")
    return div


# ── Result item widget (mirrors worms-result-item) ────────────────────────────

class _ResultItemWidget(QWidget):
    """One row in the worms-result-list.  Wraps a QWidget with click signal."""

    clicked = pyqtSignal(dict)

    def __init__(self, rec: dict, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        _refresh_palette()
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
            f"  background: {_C_ACCENT_08};"
            f"  border: 1px solid {_C_BORDER};"
            "}"
        )

        root = QVBoxLayout(self)
        root.setContentsMargins(10, 8, 10, 8)
        root.setSpacing(3)

        # Top row: sciname | authority | badges
        top = QHBoxLayout()
        top.setSpacing(6)

        sciname = rec.get("scientificname") or "?"
        sci_lbl = _label(sciname, font=_MONO, bold=True)
        top.addWidget(sci_lbl)

        if rec.get("authority"):
            auth_lbl = _label(rec["authority"], color=_C_MUTED, size=11)
            auth_lbl.setStyleSheet(
                auth_lbl.styleSheet() + " font-style:italic;"
            )
            top.addWidget(auth_lbl)

        top.addStretch()

        # Badges
        rank = rec.get("rank") or ""
        status = (rec.get("status") or "").lower()
        if rank:
            top.addWidget(_badge(rank, "rank"))
        if status:
            badge_kind = "accepted" if status == "accepted" else "unaccepted"
            top.addWidget(_badge(status, badge_kind))

        root.addLayout(top)

        # Breadcrumb: class > order > family
        breadcrumb_parts = [
            rec.get("class"), rec.get("order"), rec.get("family")
        ]
        breadcrumb = " > ".join(p for p in breadcrumb_parts if p)
        if breadcrumb:
            root.addWidget(_label(breadcrumb, color=_C_DIM, size=11))

        # Valid name hint (only when not accepted)
        if status != "accepted" and rec.get("valid_name"):
            vn = _label(f"→ accepted: {rec['valid_name']}", color=_C_WARN, size=11)
            root.addWidget(vn)

    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        self.clicked.emit(self._rec)
        super().mousePressEvent(event)


# ── Chain node widget (worms-chain-node) ──────────────────────────────────────

def _chain_node_widget(node: dict, is_current: bool = False) -> QWidget:
    """One row in the worms-classification-chain."""
    w = QWidget()
    w.setObjectName("WChainNode")
    border_color = _C_ACCENT if is_current else _C_BORDER_20
    bg = _C_ACCENT_06 if is_current else "transparent"
    w.setStyleSheet(
        f"QWidget#WChainNode {{ border-left:2px solid {border_color};"
        f"  padding-left:10px; background:{bg}; border-radius:0px; }}"
    )

    row = QHBoxLayout(w)
    row.setContentsMargins(10, 3, 6, 3)
    row.setSpacing(8)

    rank_lbl = _label(node.get("rank", ""), color=_C_MUTED, size=11)
    rank_lbl.setFixedWidth(80)
    row.addWidget(rank_lbl)

    name_lbl = _label(node.get("scientificname", ""), font=_MONO, size=12)
    row.addWidget(name_lbl)

    row.addStretch()

    aphia_id = node.get("AphiaID", 0)
    if aphia_id:
        id_lbl = _label(f"#{aphia_id}", color=_C_DIM, size=11)
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
        lbl = _label(field_name, color=_C_MUTED, size=12)
        lbl.setFixedWidth(66)
        lbl.setAlignment(Qt.AlignmentFlag.AlignTop)
        row.addWidget(lbl)
        val_lbl = _label(val, size=12)
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
        lbl = _label("生境", color=_C_MUTED, size=12)
        lbl.setFixedWidth(66)
        row.addWidget(lbl)
        row.addWidget(_label(" / ".join(habitat), size=12))
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
        lay.addWidget(_label("加载子分类…", color=_C_MUTED, size=12))
    elif not children:
        lay.addWidget(_label("无子分类", color=_C_MUTED, size=12))
    else:
        for child in children:
            row_w = QWidget()
            row_w.setObjectName("WChildItem")
            row_w.setCursor(Qt.CursorShape.PointingHandCursor)
            row_w.setStyleSheet(
                f"QWidget#WChildItem:hover {{ background: {_C_ACCENT_06}; "
                f"border-radius: 4px; }}"
            )
            row_lay = QHBoxLayout(row_w)
            row_lay.setContentsMargins(4, 3, 4, 3)
            row_lay.setSpacing(6)
            row_lay.addWidget(_label(child.get("scientificname", ""), font=_MONO, size=12))
            if child.get("rank"):
                row_lay.addWidget(_badge(child["rank"], "rank"))
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
                f"QPushButton#WMoreBtn {{ background:none; color:{_C_ACCENT};"
                f"  border:1px solid {_C_ACCENT_30}; border-radius:6px;"
                f"  padding:5px 12px; font-size:12px; }}"
                f"QPushButton#WMoreBtn:hover {{ background:{_C_ACCENT_08}; }}"
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
        lay.addWidget(_label("加载同义词…", color=_C_MUTED, size=12))
    elif not synonyms:
        lay.addWidget(_label("无同义词记录", color=_C_MUTED, size=12))
    else:
        for syn in synonyms:
            row_w = QWidget()
            row_lay = QHBoxLayout(row_w)
            row_lay.setContentsMargins(4, 3, 4, 3)
            row_lay.setSpacing(6)
            row_lay.addWidget(_label(syn.get("scientificname", ""), font=_MONO, size=12))
            status = (syn.get("status") or "").lower()
            if status:
                row_lay.addWidget(_badge(status, "accepted" if status == "accepted" else "unaccepted"))
            if syn.get("authority"):
                row_lay.addWidget(_label(syn["authority"], color=_C_MUTED, size=11))
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
        _refresh_palette()
        self.setObjectName("WDetailPanel")
        _ff = local_font_css()
        self.setStyleSheet(
            f"QWidget#WDetailPanel {{ {_ff} background:{_C_PANEL};"
            f"  border:1px solid {_C_BORDER}; border-radius:8px; }}"
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

        self._render()

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
        self._render()

    def show_loading(self, rec: dict) -> None:
        self._rec = rec
        self._chain = []
        self._synonyms = []
        self._children = []
        self._loading = True
        self._current_tab = self.TAB_OVERVIEW
        self._children_offset = 1
        self._children_has_more = False
        self._render()

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
        self._render()

    def set_tab(self, tab: str) -> None:
        self._current_tab = tab
        self._render()

    def update_fill_label(self, specimen_label: str) -> None:
        """Refresh the fill button text after active specimen changes."""
        # Re-render to pick up new label; only meaningful when detail is shown.
        if self._rec is not None:
            self._render()

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
        self._render()

    # ── Rendering ──────────────────────────────────────────────────────

    def _clear(self) -> None:
        while self._root.count():
            item = self._root.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
            elif item.layout():
                pass  # nested layout — just remove reference

    def _render(self) -> None:
        self._clear()

        if self._rec is None:
            # worms-detail-empty
            empty = _label(
                "搜索物种名并点击结果查看分类详情",
                color=_C_MUTED, size=13
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

        name_lbl = _label(
            rec.get("scientificname") or "?",
            size=15, bold=True, font=_MONO
        )
        name_lbl.setWordWrap(True)
        header_lay.addWidget(name_lbl, stretch=1)

        if rec.get("authority"):
            header_lay.addWidget(_label(rec["authority"], color=_C_MUTED, size=11))
        if rec.get("rank"):
            header_lay.addWidget(_badge(rec["rank"], "rank"))
        status = (rec.get("status") or "").lower()
        if status:
            header_lay.addWidget(_badge(status, "accepted" if status == "accepted" else "unaccepted"))

        self._root.addWidget(header_w)

        # Valid name hint
        if status != "accepted" and rec.get("valid_name"):
            self._root.addWidget(
                _label(f"→ accepted: {rec['valid_name']} (AphiaID: {rec.get('valid_AphiaID', '?')})",
                       color=_C_WARN, size=11)
            )

        # WoRMS external link (text-only label)
        aphia = rec.get("AphiaID")
        if aphia:
            link_lbl = _label(
                f"WoRMS: marinespecies.org/aphia.php?id={aphia}",
                color=_C_ACCENT, size=11
            )
            self._root.addWidget(link_lbl)

        self._root.addWidget(_divider())

        # ── worms-classification-chain ─────────────────────────────────
        if self._loading:
            self._root.addWidget(_label("加载分类链…", color=_C_MUTED, size=12))
        elif self._chain:
            chain_container = QWidget()
            chain_container.setObjectName("WChainContainer")
            chain_container.setStyleSheet(
                f"QWidget#WChainContainer {{ background:{_C_INPUT};"
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
            f"QWidget#WTabBar {{ border-bottom:1px solid {_C_BORDER_10}; }}"
        )
        tab_row = QHBoxLayout(tab_bar)
        tab_row.setContentsMargins(0, 4, 0, 0)
        tab_row.setSpacing(0)

        for tab_id, tab_label in self.TAB_LABELS.items():
            btn = QPushButton(tab_label)
            btn.setObjectName("WTabBtn")
            is_sel = (self._current_tab == tab_id)
            accent_border = _C_ACCENT if is_sel else "transparent"
            text_color    = _C_ACCENT if is_sel else _C_MUTED
            btn.setStyleSheet(
                f"QPushButton#WTabBtn {{ background:none; border:none;"
                f" border-bottom:2px solid {accent_border}; margin-bottom:-1px;"
                f" color:{text_color}; padding:7px 14px; font-size:12px;"
                f" font-weight:{'600' if is_sel else '500'}; }}"
                f"QPushButton#WTabBtn:hover {{ color:{_C_TEXT}; }}"
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
        self._root.addWidget(_divider())

        fill_btn = QPushButton("填充到当前标本")
        fill_btn.setObjectName("WFillBtn")
        fill_btn.setToolTip("将 WoRMS 分类信息（纲/目/科/属/学名）写入工作区当前标本")
        fill_btn.setStyleSheet(
            f"QPushButton#WFillBtn {{ background:{_C_ACCENT_12};"
            f"  color:{_C_ACCENT}; border:1px solid {_C_ACCENT_30};"
            f"  border-radius:6px; padding:7px 14px; font-size:12px; font-weight:600; }}"
            f"QPushButton#WFillBtn:hover {{ background:{_C_ACCENT_20};"
            f"  border-color:{_C_ACCENT}; }}"
            f"QPushButton#WFillBtn:pressed {{ background:{_C_ACCENT_28}; }}"
        )
        _rec = rec  # capture
        fill_btn.clicked.connect(lambda: self.fill_requested.emit(_rec))
        self._root.addWidget(fill_btn)


# ── Main view ──────────────────────────────────────────────────────────────────

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
        data_dir = (Path(project_dir) / "_data") if project_dir else _FALLBACK_DATA_DIR
        data_dir.mkdir(parents=True, exist_ok=True)
        return WormsService(
            cache_path=str(data_dir / "worms_cache.json"),
            jobs_path=str(data_dir / "worms_jobs.json"),
        )

    # ── UI construction ────────────────────────────────────────────────

    def _setup_ui(self) -> None:
        _refresh_palette()
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

        h2 = _label(
            "WoRMS 海洋物种分类库",
            size=18, bold=True, font=_SERIF, color=_C_TEXT
        )
        title_row.addWidget(h2)

        ext_link = _label("marinespecies.org", color=_C_ACCENT, size=12)
        title_row.addWidget(ext_link)
        title_row.addWidget(_label("查询", color=_C_MUTED, size=12))
        title_row.addStretch()
        header_lay.addLayout(title_row)

        # worms-desc
        desc = _label(
            "查询 World Register of Marine Species，获取标准化分类链并填充到标本记录。",
            color=_C_MUTED, size=12
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
            f"QLineEdit#WSearchInput {{ background:{_C_INPUT}; border:1px solid {_C_BORDER};"
            f"  border-radius:6px; padding:8px 12px; color:{_C_TEXT}; font-size:13px;"
            f"  font-family:{_MONO}; outline:none; }}"
            f"QLineEdit#WSearchInput:focus {{ border-color:{_C_ACCENT}; }}"
        )
        self._search_input.returnPressed.connect(self._on_search)
        sb_lay.addWidget(self._search_input, stretch=1)

        # like-toggle (worms-like-toggle)
        self._like_cb = QCheckBox("模糊匹配")
        self._like_cb.setChecked(True)
        self._like_cb.setStyleSheet(
            f"QCheckBox {{ color:{_C_MUTED}; font-size:12px; spacing:5px; }}"
            f"QCheckBox::indicator {{ width:14px; height:14px; border-radius:4px;"
            f"  border:1px solid {_C_BORDER_25}; background:{_C_INPUT}; }}"
            f"QCheckBox::indicator:checked {{ background:{_C_ACCENT}; border-color:{_C_ACCENT}; }}"
        )
        sb_lay.addWidget(self._like_cb)

        # 搜索 button
        self._search_btn = QPushButton("搜索")
        self._search_btn.setObjectName("WSearchBtn")
        self._search_btn.setStyleSheet(
            f"QPushButton#WSearchBtn {{ background:{_C_ACCENT}; color:{_C_BG};"
            f"  border:none; border-radius:6px; padding:8px 18px;"
            f"  font-size:13px; font-weight:600; }}"
            f"QPushButton#WSearchBtn:hover {{ background:{_C_ACCENT_H}; }}"
            f"QPushButton#WSearchBtn:disabled {{ background:{_C_PANEL}; color:{_C_DIM}; }}"
        )
        self._search_btn.clicked.connect(self._on_search)
        sb_lay.addWidget(self._search_btn)

        left_lay.addWidget(search_bar)

        # Status / progress row
        status_row = QHBoxLayout()
        status_row.setContentsMargins(0, 0, 0, 0)
        status_row.setSpacing(8)

        self._status_lbl = _label("", color=_C_MUTED, size=11)
        status_row.addWidget(self._status_lbl)

        self._progress = QProgressBar()
        self._progress.setRange(0, 0)
        self._progress.setFixedHeight(3)
        self._progress.setVisible(False)
        self._progress.setStyleSheet(
            f"QProgressBar {{ background:{_C_PANEL}; border:none; border-radius:2px; }}"
            f"QProgressBar::chunk {{ background:{_C_ACCENT}; border-radius:2px; }}"
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
        self._empty_lbl = _label(
            "输入拉丁学名后点击「搜索」\n结果将在此列出", color=_C_MUTED, size=12
        )
        self._empty_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_lbl.setContentsMargins(0, 48, 0, 0)
        self._result_layout.insertWidget(0, self._empty_lbl)

        self._result_scroll.setWidget(self._result_container)
        left_lay.addWidget(self._result_scroll, stretch=1)

        splitter.addWidget(left_container)

        # Right: detail panel
        self._detail_panel = _DetailPanel()
        self._detail_panel.fill_requested.connect(self._on_fill_to_specimen)
        self._detail_panel.child_selected.connect(self._on_result_clicked)
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
            f"QGroupBox {{ color:{_C_MUTED}; font-size:12px; font-weight:600;"
            f"  border:1px solid {_C_BORDER_10}; border-radius:8px;"
            f"  margin:0 28px 16px 28px; padding:18px 14px 10px 14px;"
            f"  background:{_C_INPUT}; }}"
            f"QGroupBox::title {{ subcontrol-origin:margin; subcontrol-position:top left;"
            f"  left:12px; top:2px; padding:0 6px 0 2px; spacing:6px;"
            f"  background:{_C_INPUT}; color:{_C_MUTED}; }}"
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
            f"QLineEdit {{ background:{_C_INPUT}; border:1px solid {_C_BORDER};"
            f"  border-radius:6px; padding:6px 10px; color:{_C_TEXT}; font-size:12px; }}"
        )
        ctrl.addWidget(self._job_ids_input, stretch=1)

        create_btn = QPushButton("创建任务")
        create_btn.setFixedWidth(84)
        create_btn.setStyleSheet(
            f"QPushButton {{ background:{_C_PANEL}; color:{_C_TEXT}; border:1px solid {_C_BORDER};"
            f"  border-radius:6px; padding:6px 10px; font-size:12px; }}"
            f"QPushButton:hover {{ border-color:{_C_ACCENT}; color:{_C_ACCENT}; }}"
        )
        create_btn.clicked.connect(self._on_create_job)
        ctrl.addWidget(create_btn)

        import_filter_btn = QPushButton("从分类库筛选导入")
        import_filter_btn.setObjectName("BtnImportFromTaxonFilter")
        import_filter_btn.setFixedWidth(114)
        import_filter_btn.setStyleSheet(
            f"QPushButton {{ background:{_C_PANEL}; color:{_C_ACCENT}; border:1px solid {_C_ACCENT_30};"
            f"  border-radius:6px; padding:6px 10px; font-size:12px; }}"
            f"QPushButton:hover {{ border-color:{_C_ACCENT}; background:{_C_ACCENT_08}; }}"
        )
        import_filter_btn.clicked.connect(self._on_import_from_taxon_filter)
        ctrl.addWidget(import_filter_btn)

        refresh_btn = QPushButton("刷新")
        refresh_btn.setFixedWidth(52)
        refresh_btn.setStyleSheet(
            f"QPushButton {{ background:{_C_PANEL}; color:{_C_MUTED}; border:1px solid {_C_BORDER};"
            f"  border-radius:6px; padding:6px 8px; font-size:12px; }}"
            f"QPushButton:hover {{ color:{_C_TEXT}; }}"
        )
        refresh_btn.clicked.connect(self._refresh_jobs)
        ctrl.addWidget(refresh_btn)

        # Retry-failed button (oracle: app.js ~12006 retry-failed action)
        self._retry_btn = QPushButton("重试失败")
        self._retry_btn.setFixedWidth(72)
        self._retry_btn.setEnabled(False)
        self._retry_btn.setStyleSheet(
            f"QPushButton {{ background:{_C_PANEL}; color:{_C_WARN}; border:1px solid {_C_WARN_25};"
            f"  border-radius:6px; padding:6px 8px; font-size:12px; }}"
            f"QPushButton:hover:enabled {{ border-color:{_C_WARN}; }}"
            f"QPushButton:disabled {{ color:{_C_DIM}; border-color:{_C_BORDER}; }}"
        )
        self._retry_btn.clicked.connect(self._on_retry_failed)
        ctrl.addWidget(self._retry_btn)
        inner.addLayout(ctrl)

        self._jobs_list = QListWidget()
        self._jobs_list.setFixedHeight(84)
        self._jobs_list.setStyleSheet(
            f"QListWidget {{ background:{_C_INPUT}; border:1px solid {_C_BORDER};"
            f"  border-radius:6px; font-size:11px; color:{_C_MUTED}; }}"
            f"QListWidget::item {{ padding:4px 8px; border-radius:4px; }}"
            f"QListWidget::item:hover {{ background:{_C_ACCENT_08}; color:{_C_TEXT}; }}"
        )
        inner.addWidget(self._jobs_list)

        return box

    # ── BaseView contract ──────────────────────────────────────────────

    def on_activate(self) -> None:
        self._service = self._init_service()
        self._detail_panel.set_service(self._service)
        self._refresh_jobs()

    # ── Search ─────────────────────────────────────────────────────────

    def _on_search(self) -> None:
        name = self._search_input.text().strip()
        if not name:
            self._set_status("请输入学名")
            return
        if self._search_thread and self._search_thread.isRunning():
            return

        like = self._like_cb.isChecked()
        self._set_busy(True, f'搜索 "{name}"…')
        self._clear_results()

        worker = _SearchWorker(self._service, name, like)
        thread = QThread(self)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(self._on_search_done)
        worker.error.connect(self._on_search_error)
        worker.finished.connect(thread.quit)
        worker.error.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        self._search_worker = worker
        self._search_thread = thread
        thread.start()

    def _on_search_done(self, results: list[dict]) -> None:
        self._set_busy(False)
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
            item_w.clicked.connect(self._on_result_clicked)
            self._result_layout.insertWidget(stretch_idx, item_w)
            stretch_idx += 1

        self._set_status(f"找到 {len(results)} 条结果")

    def _on_search_error(self, msg: str) -> None:
        self._set_busy(False)
        self._empty_lbl.setText(f"搜索失败: {msg}")
        self._empty_lbl.setStyleSheet(
            f"color:{_C_DANGER}; font-size:12px; background:transparent;"
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
            f"color:{_C_MUTED}; font-size:12px; background:transparent;"
        )
        self._empty_lbl.setText("")

    # ── Result selection ───────────────────────────────────────────────

    def _on_result_clicked(self, rec: dict) -> None:
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
        worker.finished.connect(self._on_detail_done)
        worker.error.connect(self._on_detail_error)
        worker.finished.connect(thread.quit)
        worker.error.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        self._detail_worker = worker
        self._detail_thread = thread
        thread.start()

    def _on_detail_done(self, data: dict) -> None:
        if self._selected is None:
            return
        self._detail_panel.show_detail(
            self._selected,
            data.get("chain", []),
            data.get("synonyms", []),
            data.get("children", []),
        )

    def _on_detail_error(self, msg: str) -> None:
        self._set_status(f"详情加载失败: {msg}")

    # ── Fill to specimen (worms-fill-btn) ──────────────────────────────

    def _on_fill_to_specimen(self, rec: dict) -> None:
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
            item.setForeground(QColor(_C_DIM))
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
                    item.setForeground(QColor(_C_SUCCESS))
                elif status == "running":
                    item.setForeground(QColor(_C_RUNNING))
                elif status in ("paused", "cancelled"):
                    item.setForeground(QColor(_C_WARN))
                self._jobs_list.addItem(item)

        # Auto-poll: restart 1.5 s single-shot timer when a job is running
        # Oracle: taxonJobPollTimer = setTimeout(..., 1500) in app.js ~11609
        if has_running:
            self._poll_timer = QTimer(self)
            self._poll_timer.setSingleShot(True)
            self._poll_timer.timeout.connect(self._refresh_jobs)
            self._poll_timer.start(1500)

    # ── UI helpers ─────────────────────────────────────────────────────

    def _set_busy(self, busy: bool, msg: str = "") -> None:
        self._progress.setVisible(busy)
        self._search_btn.setEnabled(not busy)
        if msg:
            self._set_status(msg)

    def _set_status(self, msg: str) -> None:
        self._status_lbl.setText(msg)


# ── WormsMatchDialog ───────────────────────────────────────────────────────────

class _MatchSearchWorker(QObject):
    """Search WoRMS for a manual-match query from within WormsMatchDialog."""

    finished = pyqtSignal(list)
    error    = pyqtSignal(str)

    def __init__(self, service: WormsService, name: str, like: bool) -> None:
        super().__init__()
        self._svc  = service
        self._name = name
        self._like = like

    def run(self) -> None:
        try:
            self.finished.emit(self._svc.search(self._name, like=self._like))
        except Exception as exc:
            self.error.emit(str(exc))


class _MatchChainWorker(QObject):
    """Fetch classification chain for the selected candidate in WormsMatchDialog."""

    finished = pyqtSignal(list)   # flattened chain
    error    = pyqtSignal(str)

    def __init__(self, service: WormsService, aphia_id: int) -> None:
        super().__init__()
        self._svc      = service
        self._aphia_id = aphia_id

    def run(self) -> None:
        try:
            raw   = self._svc.classification(self._aphia_id)
            chain = self._svc.flatten_classification(raw)
            self.finished.emit(chain)
        except Exception as exc:
            self.error.emit(str(exc))


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
        _refresh_palette()
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
            f"QDialog {{ {_ff}background:{_C_BG}; color:{_C_TEXT}; }}"
            f"QLabel {{ color:{_C_TEXT}; background:transparent; }}"
        )
        self._build_ui()

        # Auto-search with the original species name
        initial = row.get("species", "")
        if initial:
            self._search_input.setText(initial)
            self._on_search()

    # ── UI ─────────────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 16, 20, 16)
        root.setSpacing(10)

        root.addWidget(_label("WoRMS 匹配物种", size=15, bold=True))

        original = self._row.get("species", "")
        if original:
            orig_lbl = _label(f"原始种名：{original}", color=_C_MUTED, size=12)
            root.addWidget(orig_lbl)

        # Search bar
        bar = QHBoxLayout()
        bar.setSpacing(8)

        self._search_input = QLineEdit()
        self._search_input.setObjectName("WMSearchInput")
        self._search_input.setPlaceholderText("输入科学名")
        self._search_input.setStyleSheet(
            f"QLineEdit#WMSearchInput {{ background:{_C_INPUT}; border:1px solid {_C_BORDER};"
            f"  border-radius:6px; padding:7px 10px; color:{_C_TEXT}; font-size:13px;"
            f"  font-family:{_MONO}; }}"
            f"QLineEdit#WMSearchInput:focus {{ border-color:{_C_ACCENT}; }}"
        )
        self._search_input.returnPressed.connect(self._on_search)
        bar.addWidget(self._search_input, stretch=1)

        self._like_cb = QCheckBox("模糊匹配")
        self._like_cb.setStyleSheet(
            f"QCheckBox {{ color:{_C_MUTED}; font-size:12px; spacing:5px; }}"
        )
        bar.addWidget(self._like_cb)

        search_btn = QPushButton("搜索")
        search_btn.setObjectName("WMSearchBtn")
        search_btn.setStyleSheet(
            f"QPushButton#WMSearchBtn {{ background:{_C_ACCENT}; color:{_C_BG};"
            f"  border:none; border-radius:6px; padding:7px 16px; font-size:12px; font-weight:600; }}"
            f"QPushButton#WMSearchBtn:hover {{ background:{_C_ACCENT_H}; }}"
        )
        search_btn.clicked.connect(self._on_search)
        bar.addWidget(search_btn)
        root.addLayout(bar)

        # Error label
        self._error_lbl = _label("", color=_C_DANGER, size=11)
        self._error_lbl.setVisible(False)
        root.addWidget(self._error_lbl)

        # Body: results list | chain preview
        body = QHBoxLayout()
        body.setSpacing(12)

        # Results list
        results_w = QWidget()
        results_w.setObjectName("WMResultsPanel")
        results_w.setStyleSheet(
            f"QWidget#WMResultsPanel {{ background:{_C_PANEL}; border:1px solid {_C_BORDER};"
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
            f"QWidget#WMDetailPanel {{ background:{_C_INPUT}; border:1px solid {_C_BORDER};"
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
            _label("选择候选后预览标准分类阶元", color=_C_DIM, size=12)
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
            f"QPushButton#WMSaveBtn {{ background:{_C_ACCENT}; color:{_C_BG};"
            f"  border:none; border-radius:6px; padding:8px 18px; font-size:12px; font-weight:600; }}"
            f"QPushButton#WMSaveBtn:disabled {{ background:{_C_PANEL}; color:{_C_DIM}; }}"
            f"QPushButton#WMSaveBtn:hover:enabled {{ background:{_C_ACCENT_H}; }}"
        )
        self._save_btn.clicked.connect(self._on_save)
        actions.addWidget(self._save_btn)

        cancel_btn = QPushButton("取消")
        cancel_btn.setObjectName("WMCancelBtn")
        cancel_btn.setStyleSheet(
            f"QPushButton#WMCancelBtn {{ background:{_C_PANEL}; color:{_C_MUTED};"
            f"  border:1px solid {_C_BORDER}; border-radius:6px; padding:8px 16px; font-size:12px; }}"
            f"QPushButton#WMCancelBtn:hover {{ color:{_C_TEXT}; }}"
        )
        cancel_btn.clicked.connect(self.reject)
        actions.addWidget(cancel_btn)
        root.addLayout(actions)

    # ── Search (mirrors searchWormsForTaxonRow app.js ~11777) ───────────────

    def _on_search(self) -> None:
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
        worker.finished.connect(self._on_search_done)
        worker.error.connect(self._on_search_error)
        worker.finished.connect(thread.quit)
        worker.error.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        self._search_thread = thread
        thread.start()

    def _on_search_done(self, results: list[dict]) -> None:
        self._loading = False
        self._results = results
        self._update_results_list()

    def _on_search_error(self, msg: str) -> None:
        self._loading = False
        self._error = f"搜索失败：{msg}"
        self._error_lbl.setText(self._error)
        self._error_lbl.setVisible(True)
        self._update_results_list()

    # ── Candidate selection (mirrors selectWormsMatchCandidate ~11791) ──────

    def _on_candidate_clicked(self, rec: dict) -> None:
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
        worker.finished.connect(self._on_chain_done)
        worker.error.connect(lambda _: self._on_chain_done([]))
        worker.finished.connect(thread.quit)
        worker.error.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        self._chain_thread = thread
        thread.start()

    def _on_chain_done(self, chain: list[dict]) -> None:
        self._chain = chain
        self._chain_loading = False
        self._save_btn.setEnabled(self._selected_rec is not None)
        self._update_chain_view()

    # ── Save (mirrors saveWormsMatchCandidate ~11806) ───────────────────────

    def _on_save(self) -> None:
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
                _label("正在查询 WoRMS…", color=_C_MUTED, size=12)
            )
        elif not self._results:
            msg = "未找到候选，请修改关键词或启用模糊匹配。" if not self._loading else ""
            self._results_layout.addWidget(_label(msg, color=_C_MUTED, size=12))
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
                bg = _C_ACCENT_12 if is_sel else "transparent"
                border = _C_ACCENT if is_sel else "transparent"
                btn_w.setStyleSheet(
                    f"QWidget#WMCandBtn {{ background:{bg}; border:1px solid {border};"
                    f"  border-radius:6px; }}"
                    f"QWidget#WMCandBtn:hover {{ background:{_C_ACCENT_08}; }}"
                )
                row_lay = QVBoxLayout(btn_w)
                row_lay.setContentsMargins(8, 6, 8, 6)
                row_lay.setSpacing(2)

                name_row = QHBoxLayout()
                name_row.setSpacing(6)
                name = rec.get("valid_name") or rec.get("scientificname") or ""
                name_row.addWidget(_label(name, bold=True, font=_MONO, size=12))
                name_row.addStretch()
                status = (rec.get("status") or "").lower()
                name_row.addWidget(_badge(
                    status,
                    "accepted" if status == "accepted" else "unaccepted"
                ))
                row_lay.addLayout(name_row)

                detail_txt = (
                    (status or "")
                    + f"  ·  AphiaID {rec_id}"
                )
                row_lay.addWidget(_label(detail_txt, color=_C_DIM, size=11))

                bc = " > ".join(
                    p for p in [rec.get("class"), rec.get("order"), rec.get("family")]
                    if p
                )
                if bc:
                    row_lay.addWidget(_label(bc, color=_C_DIM, size=11))

                _rec = rec  # capture
                btn_w.mousePressEvent = lambda _e, r=_rec: self._on_candidate_clicked(r)  # type: ignore[method-assign]
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
                _label("加载分类链…", color=_C_MUTED, size=12)
            )
        elif self._selected_rec and self._chain:
            self._chain_layout.addWidget(
                _label("采用后保存的 WoRMS 分类链", bold=True, size=12, color=_C_MUTED)
            )
            for node in self._chain:
                row_w = QWidget()
                row_lay = QHBoxLayout(row_w)
                row_lay.setContentsMargins(4, 1, 4, 1)
                row_lay.setSpacing(8)
                rank_lbl = _label(node.get("rank", ""), color=_C_DIM, size=11)
                rank_lbl.setFixedWidth(72)
                row_lay.addWidget(rank_lbl)
                row_lay.addWidget(
                    _label(node.get("scientificname", ""), font=_MONO, size=11)
                )
                row_lay.addStretch()
                self._chain_layout.addWidget(row_w)
        else:
            self._chain_layout.addWidget(
                _label("选择候选后预览标准分类阶元", color=_C_DIM, size=12)
            )
        self._chain_layout.addStretch()


# ── WormsQuickFillDialog ───────────────────────────────────────────────────────

class _QuickSearchWorker(QObject):
    """Background search worker for WormsQuickFillDialog."""

    finished = pyqtSignal(list)
    error    = pyqtSignal(str)

    def __init__(self, service: WormsService, name: str) -> None:
        super().__init__()
        self._svc  = service
        self._name = name

    def run(self) -> None:
        try:
            # Always use like=True for quick popup (oracle: doWormsPopupSearch ~12753)
            self.finished.emit(self._svc.search(self._name, like=True))
        except Exception as exc:
            self.error.emit(str(exc))


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
        _refresh_palette()
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
            f"QDialog {{ {_ff}background:{_C_BG}; color:{_C_TEXT}; }}"
            f"QLabel {{ color:{_C_TEXT}; background:transparent; }}"
        )
        self._build_ui()

        # Pre-fill and auto-search if query provided
        if initial_query:
            self._search_input.setText(initial_query.strip())
            self._on_search()

    # ── UI ──────────────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 16, 20, 16)
        root.setSpacing(10)

        root.addWidget(_label("从 WoRMS 查找分类", size=15, bold=True))

        # Search bar (oracle: worms-popup-search div ~12695)
        bar = QHBoxLayout()
        bar.setSpacing(8)

        self._search_input = QLineEdit()
        self._search_input.setObjectName("WQSearchInput")
        self._search_input.setPlaceholderText("输入拉丁学名…")
        self._search_input.setStyleSheet(
            f"QLineEdit#WQSearchInput {{ background:{_C_INPUT}; border:1px solid {_C_BORDER};"
            f"  border-radius:6px; padding:7px 10px; color:{_C_TEXT}; font-size:13px;"
            f"  font-family:{_MONO}; }}"
            f"QLineEdit#WQSearchInput:focus {{ border-color:{_C_ACCENT}; }}"
        )
        self._search_input.returnPressed.connect(self._on_search)
        bar.addWidget(self._search_input, stretch=1)

        search_btn = QPushButton("搜索")
        search_btn.setObjectName("WQSearchBtn")
        search_btn.setStyleSheet(
            f"QPushButton#WQSearchBtn {{ background:{_C_ACCENT}; color:{_C_BG};"
            f"  border:none; border-radius:6px; padding:7px 16px; font-size:12px; font-weight:600; }}"
            f"QPushButton#WQSearchBtn:hover {{ background:{_C_ACCENT_H}; }}"
        )
        search_btn.clicked.connect(self._on_search)
        bar.addWidget(search_btn)
        root.addLayout(bar)

        # Status label (loading / error)
        self._status_lbl = _label("", color=_C_MUTED, size=11)
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
            f"QPushButton#WQCloseBtn {{ background:{_C_PANEL}; color:{_C_MUTED};"
            f"  border:1px solid {_C_BORDER}; border-radius:6px; padding:7px 16px; font-size:12px; }}"
            f"QPushButton#WQCloseBtn:hover {{ color:{_C_TEXT}; }}"
        )
        close_btn.clicked.connect(self.reject)
        btn_row.addWidget(close_btn)
        root.addLayout(btn_row)

    # ── Search (oracle: doWormsPopupSearch ~12743) ──────────────────────────

    def _on_search(self) -> None:
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
        worker.finished.connect(self._on_search_done)
        worker.error.connect(self._on_search_error)
        worker.finished.connect(thread.quit)
        worker.error.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        self._search_thread = thread
        thread.start()

    def _on_search_done(self, results: list[dict]) -> None:
        self._loading = False
        self._results = results
        self._status_lbl.setText(
            f"找到 {len(results)} 条结果" if results else "无结果"
        )
        self._render_results()

    def _on_search_error(self, msg: str) -> None:
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
                _label("查询中…", color=_C_MUTED, size=12)
            )
        elif not self._results:
            pass  # status label already shows the message
        else:
            for rec in self._results:
                row_w = QWidget()
                row_w.setObjectName("WQResultRow")
                row_w.setStyleSheet(
                    f"QWidget#WQResultRow {{ background:{_C_PANEL}; border:1px solid {_C_BORDER};"
                    f"  border-radius:6px; }}"
                    f"QWidget#WQResultRow:hover {{ border-color:{_C_ACCENT}; }}"
                )
                row_lay = QVBoxLayout(row_w)
                row_lay.setContentsMargins(10, 8, 10, 8)
                row_lay.setSpacing(3)

                # Top: sciname + rank/status badges (oracle: ~12716–12718)
                top = QHBoxLayout()
                top.setSpacing(6)
                sciname = rec.get("scientificname") or "?"
                top.addWidget(_label(sciname, bold=True, font=_MONO, size=12))
                rank = rec.get("rank") or ""
                status = (rec.get("status") or "").lower()
                if rank:
                    top.addWidget(_badge(rank, "rank"))
                if status:
                    top.addWidget(_badge(status, "accepted" if status == "accepted" else "unaccepted"))
                top.addStretch()

                # 填充 button (oracle: worms-popup-fill-btn ~12721)
                fill_btn = QPushButton("填充")
                fill_btn.setObjectName("WQFillBtn")
                fill_btn.setFixedWidth(52)
                fill_btn.setStyleSheet(
                    f"QPushButton#WQFillBtn {{ background:{_C_ACCENT_12}; color:{_C_ACCENT};"
                    f"  border:1px solid {_C_ACCENT_30}; border-radius:5px;"
                    f"  padding:3px 8px; font-size:11px; font-weight:600; }}"
                    f"QPushButton#WQFillBtn:hover {{ background:{_C_ACCENT_22};"
                    f"  border-color:{_C_ACCENT}; }}"
                )
                _rec = rec  # capture loop var
                fill_btn.clicked.connect(lambda _checked=False, r=_rec: self._do_fill(r))
                top.addWidget(fill_btn)
                row_lay.addLayout(top)

                # Breadcrumb: class > order > family (oracle: ~12719)
                bc = " > ".join(
                    p for p in [rec.get("class"), rec.get("order"), rec.get("family")]
                    if p
                )
                if bc:
                    row_lay.addWidget(_label(bc, color=_C_DIM, size=11))

                self._results_layout.addWidget(row_w)

        self._results_layout.addStretch()

    # ── Fill action (oracle: fillBtn click ~12722–12727) ──────────────────

    def _do_fill(self, rec: dict) -> None:
        """Invoke fill_callback with *rec*, then close the dialog.

        Chinese fields (*Cn) are never touched — that constraint is
        enforced by the callback implementation, not here.
        """
        try:
            self._fill_callback(rec)
        except Exception:
            pass
        self.accept()
