"""taxonomy_input.py — 4-level linked taxonomy autocomplete widget.

Implements a set of QLineEdit inputs (taxonGroup / order / family /
scientificName) with a shared popup overlay that provides richer
autocomplete than QCompleter:

  - Dual columns: Latin name + Chinese name side-by-side
  - Parent path sub-row (e.g. "Polychaeta · Phyllodocida" under a family)
  - Source badge in four colours: user / seed / worms / cross
  - NFKC-normalised Chinese + Latin substring match
  - Keyboard navigation (↑/↓/Enter/Esc/Tab)
  - Draggable popup (drag handle at top)

Hard rules (mirrors taxonomy.md and app.js):
  - NO QCompleter — cannot handle Chinese label search + dual columns.
  - Selecting a parent level fills ONLY that level and its ancestors,
    never touches child fields.
  - Chinese fields (*Cn) are NEVER auto-filled; only the Latin field
    receives the selection value.  The *Cn counterpart is set only when
    the record's source is "user" and the record carries a cn value AND
    the caller explicitly opts in (currently: never auto — deferred to
    the view layer that knows the full specimen state).

Usage
-----
::

    svc = TaxonomyService(seed_path, user_path)
    panel = TaxonomyInputPanel(svc)
    panel.value_committed.connect(my_handler)   # dict of changed fields

    # Programmatically set context (already-known fields):
    panel.set_context({"taxonGroup": "Polychaeta", "order": "Phyllodocida"})
"""
from __future__ import annotations

import unicodedata
from typing import Any, Callable, Optional

from PyQt6.QtCore import (
    QPoint,
    QRect,
    Qt,
    QTimer,
    pyqtSignal,
)
from PyQt6.QtGui import (
    QColor,
    QFont,
    QKeyEvent,
    QPainter,
    QPalette,
)
from PyQt6.QtWidgets import (
    QAbstractItemDelegate,
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListView,
    QSizePolicy,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QVBoxLayout,
    QWidget,
)
from PyQt6.QtCore import QAbstractListModel, QModelIndex, QVariant

from app.services.taxonomy_service import (
    TaxonCandidate,
    TaxonomyService,
    VALID_SP_KEYS,
    _nfkc,
)


# ── Constants ─────────────────────────────────────────────────────────────────

# Display order for the 4 input fields
_FIELD_ORDER: list[tuple[str, str]] = [
    ("taxonGroup",     "纲 / 门"),
    ("order",          "目"),
    ("family",         "科"),
    ("scientificName", "种"),
]

# Badge colours: source → (bg, fg) in hex
_SOURCE_COLORS: dict[str, tuple[str, str]] = {
    "user":  ("#3b82f6", "#ffffff"),   # blue
    "seed":  ("#22c55e", "#ffffff"),   # green
    "worms": ("#a855f7", "#ffffff"),   # purple
    "cross": ("#f59e0b", "#1f2937"),   # amber
}

_SOURCE_LABELS: dict[str, str] = {
    "user":  "用户",
    "seed":  "权威",
    "worms": "WoRMS",
    "cross": "跨",
}

_ROW_HEIGHT = 52          # px per popup row
_POPUP_WIDTH = 340        # px


# ── List model ────────────────────────────────────────────────────────────────

class _CandidateModel(QAbstractListModel):
    """Simple list model backed by TaxonCandidate objects."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._items: list[TaxonCandidate] = []
        self._query: str = ""

    def set_items(self, items: list[TaxonCandidate], query: str = "") -> None:
        self.beginResetModel()
        self._items = list(items)
        self._query = query
        self.endResetModel()

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return len(self._items)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if not index.isValid() or index.row() >= len(self._items):
            return None
        item = self._items[index.row()]
        if role == Qt.ItemDataRole.DisplayRole:
            return item.value
        if role == Qt.ItemDataRole.UserRole:
            return item          # full TaxonCandidate
        if role == Qt.ItemDataRole.UserRole + 1:
            return self._query   # current query for highlight
        return None

    def candidate_at(self, row: int) -> Optional[TaxonCandidate]:
        if 0 <= row < len(self._items):
            return self._items[row]
        return None

    def count(self) -> int:
        return len(self._items)


# ── Delegate ──────────────────────────────────────────────────────────────────

def _highlight_spans(text: str, query: str) -> list[tuple[int, int, bool]]:
    """Return list of (start, end, is_highlight) spans for painting."""
    if not query:
        return [(0, len(text), False)]
    q = _nfkc(query)
    t = _nfkc(text)
    pos = t.find(q)
    if pos < 0:
        return [(0, len(text), False)]
    spans = []
    if pos > 0:
        spans.append((0, pos, False))
    spans.append((pos, pos + len(q), True))
    if pos + len(q) < len(text):
        spans.append((pos + len(q), len(text), False))
    return spans


class TaxonItemDelegate(QStyledItemDelegate):
    """Paints each candidate row with Latin + CN columns, path row, source badge."""

    def __init__(self, sp_key_fn: Callable[[], str], parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._get_sp_key = sp_key_fn

    def sizeHint(self, option: QStyleOptionViewItem, index: QModelIndex) -> Any:
        from PyQt6.QtCore import QSize
        return QSize(_POPUP_WIDTH, _ROW_HEIGHT)

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex) -> None:
        painter.save()

        cand: Optional[TaxonCandidate] = index.data(Qt.ItemDataRole.UserRole)
        query: str = index.data(Qt.ItemDataRole.UserRole + 1) or ""

        rect = option.rect

        # Background
        is_selected = bool(option.state & option.state.State_Selected)  # type: ignore[attr-defined]
        if is_selected:
            painter.fillRect(rect, QColor("#1e3a5f"))
        else:
            bg = QColor("#1a2535") if index.row() % 2 == 0 else QColor("#1f2d40")
            painter.fillRect(rect, bg)

        if cand is None:
            painter.restore()
            return

        # ── Top row ───────────────────────────────────────────────────

        top_y = rect.top() + 6
        x = rect.left() + 8

        # Latin name with highlight
        latin_font = QFont()
        latin_font.setPointSize(10)
        painter.setFont(latin_font)

        # Draw Latin spans
        for start, end, hi in _highlight_spans(cand.value, query):
            seg = cand.value[start:end]
            if hi:
                painter.setPen(QColor("#fbbf24"))
            else:
                painter.setPen(QColor("#e2e8f0"))
            fm = painter.fontMetrics()
            painter.drawText(x, top_y + fm.ascent(), seg)
            x += fm.horizontalAdvance(seg)

        # Chinese name
        if cand.cn:
            x += 8
            cn_font = QFont()
            cn_font.setPointSize(9)
            painter.setFont(cn_font)
            fm = painter.fontMetrics()
            for start, end, hi in _highlight_spans(cand.cn, query):
                seg = cand.cn[start:end]
                if hi:
                    painter.setPen(QColor("#fbbf24"))
                else:
                    painter.setPen(QColor("#94a3b8"))
                painter.drawText(x, top_y + fm.ascent(), seg)
                x += fm.horizontalAdvance(seg)

        # Source badge (right-aligned)
        badge_text = _SOURCE_LABELS.get(cand.source, cand.source)
        bg_hex, fg_hex = _SOURCE_COLORS.get(cand.source, ("#475569", "#ffffff"))
        badge_font = QFont()
        badge_font.setPointSize(8)
        painter.setFont(badge_font)
        fm = painter.fontMetrics()
        bw = fm.horizontalAdvance(badge_text) + 8
        bh = fm.height() + 2
        bx = rect.right() - bw - 6
        by = top_y - 1
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(bg_hex))
        painter.drawRoundedRect(bx, by, bw, bh, 3, 3)
        painter.setPen(QColor(fg_hex))
        painter.drawText(bx + 4, by + fm.ascent() + 1, badge_text)

        # ── Path sub-row ───────────────────────────────────────────────

        sp_key = self._get_sp_key()
        path_str = _format_path(sp_key, cand.full) if cand.full else ""
        if path_str:
            path_font = QFont()
            path_font.setPointSize(8)
            painter.setFont(path_font)
            painter.setPen(QColor("#64748b"))
            fm = painter.fontMetrics()
            path_y = rect.top() + 30 + fm.ascent()
            painter.drawText(rect.left() + 8, path_y, path_str)

        painter.restore()


def _format_path(sp_key: str, e: dict[str, Any]) -> str:
    """Build the parent path string shown below the candidate name.

    Mirrors ``formatTaxonPath(spKey, e)`` in app.js.
    """
    parts: list[str] = []
    if sp_key == "scientificName":
        if e.get("family"):
            parts.append(e["family"] + (" " + e["familyCn"] if e.get("familyCn") else ""))
        if e.get("order"):
            parts.append(e["order"] + (" " + e["orderCn"] if e.get("orderCn") else ""))
        if e.get("class"):
            parts.append(e["class"])
    elif sp_key == "family":
        if e.get("order"):
            parts.append(e["order"] + (" " + e["orderCn"] if e.get("orderCn") else ""))
        if e.get("class"):
            parts.append(e["class"])
    elif sp_key == "order":
        if e.get("class"):
            parts.append(e["class"])
    return " · ".join(parts)


# ── Popup frame ───────────────────────────────────────────────────────────────

class TaxonPopup(QFrame):
    """Borderless floating popup that shows autocomplete candidates.

    Emits ``item_selected(TaxonCandidate)`` when the user picks an item.
    Emits ``dismissed()`` when Escape is pressed or focus leaves.

    The popup is a child-less top-level window (``Qt.WindowType.Popup``)
    that stays alive for the lifetime of TaxonomyInputPanel; it is shown
    and hidden, never re-created.
    """

    item_selected = pyqtSignal(object)   # TaxonCandidate
    dismissed = pyqtSignal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(
            parent,
            Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setObjectName("TaxonPopup")
        self.setFrameShape(QFrame.Shape.StyledPanel)

        self._sp_key: str = "scientificName"
        self._model = _CandidateModel(self)
        self._delegate = TaxonItemDelegate(lambda: self._sp_key, self)
        self._drag_origin: Optional[QPoint] = None
        self._drag_frame_origin: Optional[QPoint] = None

        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Drag handle
        self._drag_bar = QLabel("⠿ 分类补全")
        self._drag_bar.setObjectName("TaxonDragHandle")
        self._drag_bar.setFixedHeight(22)
        self._drag_bar.setCursor(Qt.CursorShape.SizeAllCursor)
        self._drag_bar.setStyleSheet(
            "background:#0f1926; color:#64748b; font-size:11px;"
            " padding-left:8px; border-bottom:1px solid #334155;"
        )
        layout.addWidget(self._drag_bar)

        # Cross-fallback note (hidden by default)
        self._note_label = QLabel("本科/目下无匹配；跨分类列出，选中后自动改上级。")
        self._note_label.setObjectName("TaxonCrossNote")
        self._note_label.setStyleSheet(
            "background:#1e293b; color:#f59e0b; font-size:10px; padding:4px 8px;"
        )
        self._note_label.setWordWrap(True)
        self._note_label.hide()
        layout.addWidget(self._note_label)

        # Empty-state label
        self._empty_label = QLabel("暂无候选")
        self._empty_label.setObjectName("TaxonEmpty")
        self._empty_label.setStyleSheet(
            "background:#1a2535; color:#64748b; font-size:11px;"
            " padding:10px 8px;"
        )
        self._empty_label.hide()
        layout.addWidget(self._empty_label)

        # List view
        self._list = QListView(self)
        self._list.setObjectName("TaxonList")
        self._list.setModel(self._model)
        self._list.setItemDelegate(self._delegate)
        self._list.setSelectionMode(QListView.SelectionMode.SingleSelection)
        self._list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._list.setStyleSheet(
            "QListView { background:#1a2535; border:none; outline:none; }"
            "QListView::item:selected { background:#1e3a5f; }"
            "QScrollBar:vertical { background:#1a2535; width:6px; }"
            "QScrollBar::handle:vertical { background:#334155; border-radius:3px; }"
        )
        self._list.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        layout.addWidget(self._list)

        self.setStyleSheet(
            "TaxonPopup { background:#1a2535; border:1px solid #334155;"
            " border-radius:4px; }"
        )
        self.setMinimumWidth(_POPUP_WIDTH)
        self.setMaximumWidth(_POPUP_WIDTH + 60)

        # Drag mouse events on the bar
        self._drag_bar.mousePressEvent = self._handle_drag_press      # type: ignore[method-assign]
        self._drag_bar.mouseMoveEvent = self._handle_drag_move        # type: ignore[method-assign]
        self._drag_bar.mouseReleaseEvent = self._handle_drag_release  # type: ignore[method-assign]

        # Click-to-select in list
        self._list.clicked.connect(self._on_list_clicked)

    # ── Public ────────────────────────────────────────────────────────

    def populate(
        self,
        sp_key: str,
        items: list[TaxonCandidate],
        query: str,
        cross_fallback: bool = False,
    ) -> None:
        """Fill the popup with new candidates and show/resize."""
        self._sp_key = sp_key
        self._model.set_items(items, query)
        self._note_label.setVisible(cross_fallback)

        if not items:
            self._empty_label.setText(
                f"未在库中。回车使用 {query.strip()} 并入库" if query.strip()
                else "暂无候选"
            )
            self._empty_label.show()
            self._list.hide()
            self._resize_to(min_rows=0)
        else:
            self._empty_label.hide()
            self._list.show()
            self._resize_to(min_rows=len(items))

        # Select first row
        if items:
            self._list.setCurrentIndex(self._model.index(0, 0))

    def navigate(self, delta: int) -> None:
        """Move keyboard selection up (delta=-1) or down (+1)."""
        count = self._model.count()
        if count == 0:
            return
        current = self._list.currentIndex()
        row = current.row() if current.isValid() else -1
        new_row = max(0, min(count - 1, row + delta))
        idx = self._model.index(new_row, 0)
        self._list.setCurrentIndex(idx)
        self._list.scrollTo(idx)

    def accept_current(self) -> bool:
        """Emit item_selected for the highlighted row.  Returns True if done."""
        idx = self._list.currentIndex()
        if not idx.isValid():
            return False
        cand = self._model.candidate_at(idx.row())
        if cand is None:
            return False
        self.item_selected.emit(cand)
        return True

    def show_below(self, ref_widget: QWidget) -> None:
        """Position the popup just below *ref_widget* (unless already dragged)."""
        gp = ref_widget.mapToGlobal(QPoint(0, ref_widget.height() + 2))
        self.move(gp)
        self.show()
        self.raise_()

    # ── Internal ──────────────────────────────────────────────────────

    def _resize_to(self, min_rows: int) -> None:
        visible_rows = min(min_rows, 8)
        note_h = self._note_label.sizeHint().height() if self._note_label.isVisible() else 0
        empty_h = self._empty_label.sizeHint().height() if self._empty_label.isVisible() else 0
        list_h = visible_rows * _ROW_HEIGHT
        total = 22 + note_h + empty_h + list_h + 4
        self.setFixedHeight(max(total, 44))

    def _on_list_clicked(self, index: QModelIndex) -> None:
        cand = self._model.candidate_at(index.row())
        if cand:
            self.item_selected.emit(cand)

    def _handle_drag_press(self, ev: Any) -> None:
        if ev.button() == Qt.MouseButton.LeftButton:
            self._drag_origin = ev.globalPosition().toPoint()
            self._drag_frame_origin = self.pos()

    def _handle_drag_move(self, ev: Any) -> None:
        if self._drag_origin and self._drag_frame_origin:
            delta = ev.globalPosition().toPoint() - self._drag_origin
            self.move(self._drag_frame_origin + delta)

    def _handle_drag_release(self, ev: Any) -> None:  # noqa: ARG002
        self._drag_origin = None
        self._drag_frame_origin = None

    def keyPressEvent(self, event: QKeyEvent) -> None:
        key = event.key()
        if key == Qt.Key.Key_Escape:
            self.hide()
            self.dismissed.emit()
        else:
            super().keyPressEvent(event)


# ── Input field ───────────────────────────────────────────────────────────────

class TaxonLineEdit(QLineEdit):
    """QLineEdit that forwards arrow keys and Enter to the popup."""

    popup_navigate = pyqtSignal(int)    # +1 / -1
    popup_accept = pyqtSignal()
    popup_dismiss = pyqtSignal()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        key = event.key()
        if key == Qt.Key.Key_Down:
            self.popup_navigate.emit(+1)
            return
        if key == Qt.Key.Key_Up:
            self.popup_navigate.emit(-1)
            return
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.popup_accept.emit()
            return
        if key == Qt.Key.Key_Escape:
            self.popup_dismiss.emit()
            return
        super().keyPressEvent(event)


# ── Main panel ────────────────────────────────────────────────────────────────

class TaxonomyInputPanel(QWidget):
    """4-level linked taxonomy input panel with a shared autocomplete popup.

    Signals
    -------
    value_committed(dict):
        Emitted after the user selects a candidate or commits a typed value.
        The dict contains only the fields that were updated, e.g.
        ``{"taxonGroup": "Polychaeta", "order": "Phyllodocida"}``.
        Chinese fields (*Cn) are NEVER included here — the view layer
        must manage those from its own specimen state.
    """

    value_committed = pyqtSignal(dict)

    def __init__(
        self,
        taxonomy_service: TaxonomyService,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._svc = taxonomy_service
        # Current specimen context for ancestor constraints
        self._context: dict[str, str] = {}
        # Active input field (sp_key → QLineEdit)
        self._inputs: dict[str, TaxonLineEdit] = {}
        # Shared popup
        self._popup = TaxonPopup()
        self._active_sp_key: Optional[str] = None
        # Debounce timer
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(80)
        self._search_timer.timeout.connect(self._do_search)

        self._build_ui()
        self._popup.item_selected.connect(self._on_item_selected)
        self._popup.dismissed.connect(self._popup.hide)

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        for sp_key, label_text in _FIELD_ORDER:
            row = QHBoxLayout()
            row.setSpacing(6)

            lbl = QLabel(label_text)
            lbl.setFixedWidth(60)
            lbl.setObjectName("TaxonLabel")
            lbl.setStyleSheet("color:#94a3b8; font-size:11px;")
            row.addWidget(lbl)

            inp = TaxonLineEdit()
            inp.setObjectName(f"TaxonInput_{sp_key}")
            inp.setPlaceholderText(f"输入{label_text}…")
            inp.setClearButtonEnabled(True)
            row.addWidget(inp)

            # Connect signals
            inp.textChanged.connect(
                lambda text, k=sp_key: self._on_text_changed(k, text)
            )
            inp.editingFinished.connect(
                lambda k=sp_key: self._on_editing_finished(k)
            )
            inp.popup_navigate.connect(self._popup.navigate)
            inp.popup_accept.connect(self._on_accept_popup)
            inp.popup_dismiss.connect(self._popup.hide)

            layout.addLayout(row)
            self._inputs[sp_key] = inp

    # ── Public API ─────────────────────────────────────────────────────

    def set_context(self, context: dict[str, str]) -> None:
        """Set the current specimen's known taxonomy fields.

        These are used for ancestor constraint filtering in the popup.
        Calling this does NOT update the visible text in the inputs —
        call ``set_values`` for that.

        Parameters
        ----------
        context:
            Dict with any of: ``taxonGroup``, ``order``, ``family``,
            ``scientificName``, ``genus``.
        """
        self._context = dict(context)

    def set_values(self, values: dict[str, str]) -> None:
        """Programmatically set the text in one or more input fields.

        Does NOT emit ``value_committed``.
        Does NOT close or update the popup.

        Parameters
        ----------
        values:
            Dict mapping sp_key → Latin text, e.g.
            ``{"taxonGroup": "Polychaeta", "order": "Phyllodocida"}``.
        """
        for sp_key, text in values.items():
            inp = self._inputs.get(sp_key)
            if inp is not None:
                # Temporarily block signals so textChanged doesn't trigger search
                inp.blockSignals(True)
                inp.setText(text)
                inp.blockSignals(False)

    def clear_all(self) -> None:
        """Clear all input fields and context."""
        self._context = {}
        for inp in self._inputs.values():
            inp.blockSignals(True)
            inp.clear()
            inp.blockSignals(False)
        self._popup.hide()

    # ── Internal search flow ───────────────────────────────────────────

    def _on_text_changed(self, sp_key: str, text: str) -> None:
        """Fired on every keystroke in any input."""
        self._active_sp_key = sp_key
        self._search_timer.stop()
        self._search_timer.start()

    def _do_search(self) -> None:
        """Debounced: build candidates and show popup."""
        sp_key = self._active_sp_key
        if sp_key is None:
            return
        inp = self._inputs.get(sp_key)
        if inp is None:
            return
        query = inp.text()

        # Primary search with ancestor constraints
        cands = self._svc.search(sp_key, query, context=self._context)
        cross_fallback = False

        # Cross-level fallback: if no results and there's a query, try unconstrained
        if not cands and query.strip():
            # Only if family/genus not already known (mirrors app.js logic)
            known_family = bool(self._context.get("family")) and sp_key in ("scientificName", "genus")
            known_genus = bool(self._context.get("genus")) and sp_key == "scientificName"
            if not known_family and not known_genus:
                cands_all = self._svc.search(sp_key, query, context={})
                cands = [
                    TaxonCandidate(
                        value=c.value,
                        cn=c.cn,
                        source="user" if c.source == "user" else "cross",
                        full=c.full,
                    )
                    for c in cands_all
                ]
                cross_fallback = bool(cands)

        self._popup.populate(sp_key, cands, query, cross_fallback=cross_fallback)
        if cands or query.strip():
            self._popup.show_below(inp)
        else:
            self._popup.hide()

    def _on_editing_finished(self, sp_key: str) -> None:
        """Called on Enter or focus-leave.  Commit the typed value.

        Mirrors ``commitTypedTaxon(spKey, sp, typedValue)`` in app.js:
          1. Try exact match within constrained (ancestor-filtered) candidates.
          2. If not found, try unconstrained cross-level search (Chinese or Latin).
          3. On any exact hit: call ``_commit_candidate`` so ancestor inputs are
             back-filled (critical: this is the path that was previously missing).
          4. If no match at all: leave typed text as-is (raw entry, no ancestry).
        """
        inp = self._inputs.get(sp_key)
        if inp is None:
            return
        text = inp.text().strip()
        if not text:
            return
        q_lower = _nfkc(text).lower()

        # Step 1: constrained search
        cands = self._svc.search(sp_key, text, context=self._context, max_results=30)
        exact = next(
            (c for c in cands
             if _nfkc(c.value).lower() == q_lower
             or (c.cn and _nfkc(c.cn).lower() == q_lower)),
            None,
        )

        # Step 2: unconstrained cross-level fallback (mirrors exactTaxonCandidate)
        if exact is None:
            cands_all = self._svc.search(sp_key, text, context={}, max_results=30)
            seen_keys: set[str] = {
                (c.value + "|" + (c.cn or "")) for c in cands
            }
            for c in cands_all:
                key = c.value + "|" + (c.cn or "")
                if key in seen_keys:
                    continue
                if (
                    _nfkc(c.value).lower() == q_lower
                    or (c.cn and _nfkc(c.cn).lower() == q_lower)
                ):
                    # Mark as cross-level source so parent knows
                    exact = TaxonCandidate(
                        value=c.value,
                        cn=c.cn,
                        source="cross",
                        full=c.full,
                    )
                    break

        # Step 3: commit — this fills ancestor inputs (not just the typed field)
        if exact is not None:
            self._commit_candidate(sp_key, exact)

    def _on_accept_popup(self) -> None:
        """Enter key pressed while popup is visible."""
        if self._popup.isVisible():
            if not self._popup.accept_current():
                # No item selected — treat as commit of typed text
                if self._active_sp_key:
                    self._on_editing_finished(self._active_sp_key)

    def _on_item_selected(self, cand: TaxonCandidate) -> None:
        self._popup.hide()
        sp_key = self._active_sp_key
        if sp_key is None:
            return
        self._commit_candidate(sp_key, cand)

    def _commit_candidate(self, sp_key: str, cand: TaxonCandidate) -> None:
        """Fill ancestors of sp_key from the candidate record.

        Hard rule: only fills the selected level and its ancestors —
        NEVER touches child-level fields.

        Chinese fields (*Cn) are NEVER auto-filled here.
        """
        full = cand.full or {}
        changed: dict[str, str] = {}

        # Level ordering: taxonGroup < order < family < scientificName
        level_keys = [sp for sp, *_ in _FIELD_ORDER]
        sp_idx = level_keys.index(sp_key)

        # Map sp_key → seed record key for look-up
        _sp_to_seed = {
            "taxonGroup":     "class",
            "order":          "order",
            "family":         "family",
            "scientificName": "species",
        }

        # Fill this level and all ancestors (levels 0 .. sp_idx inclusive)
        for i, lvl in enumerate(level_keys[: sp_idx + 1]):
            seed_k = _sp_to_seed[lvl]
            val = full.get(seed_k, "")
            if lvl == sp_key:
                # Always fill with the actual candidate value
                val = cand.value
            if val:
                inp = self._inputs.get(lvl)
                if inp is not None:
                    inp.blockSignals(True)
                    inp.setText(val)
                    inp.blockSignals(False)
                changed[lvl] = val
                # Update context so subsequent searches are constrained
                self._context[lvl] = val

        if changed:
            self.value_committed.emit(changed)
