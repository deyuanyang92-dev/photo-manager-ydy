"""label_step1_select.py — Step 1「选择标本」section of the Label Print page.

Mirrors web ``renderLabelStep1({classic:true})`` (app.js:14448-14560):
quick-select buttons (全选 / 仅 RNA / 仅样品 / 清空) over a checkbox grid of the
current project's specimens, each row showing uniqueId + species name and a 🧬
badge for R-prefix (RNAlater) specimens.

The web Step1 also carries a project dropdown to switch projects; the Qt app is
single-project (one project per AppContext), so the project is shown as a static
label instead — selection lives entirely in this widget.

Signals
-------
selection_changed()  — the checked-for-print set changed.
"""
from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from app.utils.label_core import has_rna_tissue, unique_id


# ── Theme colours — resolved from the LIVE active theme ───────────────────────
# These are PANEL CHROME only (section bg / list-item bg / captions / titles).
# The actual printed-label rendering is done elsewhere (label_render) and stays
# white-paper/black-text regardless of theme.
_C_BG = "#08161b"
_C_PANEL_2 = "#0c2027"
_C_INPUT_BG = "#0c2027"
_C_TEXT = "#eef3ef"
_C_TEXT_SOFT = "#cfe0db"
_C_MUTED = "#87a2a1"
_C_MUTED_DIM = "#5f7d7a"
_C_ACCENT = "#29b9ab"
_C_SEL_BG = "#0f2f38"
_C_BORDER = "rgba(145,182,181,0.10)"
_C_BORDER_HI = "rgba(145,182,181,0.25)"


def _refresh_palette() -> None:
    """Rebind the module `_C_*` chrome colours to the current theme tokens."""
    global _C_BG, _C_PANEL_2, _C_INPUT_BG, _C_TEXT, _C_TEXT_SOFT, _C_MUTED
    global _C_MUTED_DIM, _C_ACCENT, _C_SEL_BG, _C_BORDER, _C_BORDER_HI
    from app.config.theme import TOKENS
    g = TOKENS.get
    _C_BG = g("bg", _C_BG)
    _C_PANEL_2 = g("panel_2", _C_PANEL_2)
    _C_INPUT_BG = g("input_bg", _C_INPUT_BG)
    _C_TEXT = g("text", _C_TEXT)
    _C_TEXT_SOFT = g("text", _C_TEXT_SOFT)
    _C_MUTED = g("muted", _C_MUTED)
    _C_MUTED_DIM = g("muted_dim", _C_MUTED_DIM)
    _C_ACCENT = g("accent", _C_ACCENT)
    _C_SEL_BG = g("panel_2", _C_SEL_BG)
    _C_BORDER = g("border", _C_BORDER)
    _C_BORDER_HI = g("border", _C_BORDER_HI)


def _css_outline_btn() -> str:
    return f"""
QPushButton#QuickBtn {{
    background: transparent; border: 1px solid {_C_BORDER_HI};
    border-radius: 4px; color: {_C_TEXT_SOFT}; padding: 4px 12px; font-size: 12px;
}}
QPushButton#QuickBtn:hover {{ border-color: {_C_ACCENT}; color: {_C_ACCENT}; }}
"""


def _css_item() -> str:
    return f"""
QFrame#SpecItem {{
    background: {_C_INPUT_BG}; border: 1px solid {_C_BORDER};
    border-radius: 5px;
}}
QFrame#SpecItem[selected="true"] {{
    border: 1.5px solid {_C_ACCENT}; background: {_C_SEL_BG};
}}
"""


_GRID_COLS = 1


class LabelStep1Select(QWidget):
    """Step 1 — specimen selection grid with quick-select buttons."""

    selection_changed = pyqtSignal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        _refresh_palette()
        self.setStyleSheet(
            f"background:{_C_BG}; color:{_C_TEXT};" + _css_outline_btn() + _css_item()
        )
        self._specimens: list[dict] = []
        self._checked: set[int] = set()
        self._items: list[dict] = []  # [{"idx","sp","rna","check","frame"}]
        self._filter_text = ""
        self._setup_ui()

    # ── UI ─────────────────────────────────────────────────────────────────

    def _setup_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(10)

        title = QLabel("选择标本")
        title.setStyleSheet(f"color:{_C_ACCENT}; font-size:15px; font-weight:bold;")
        root.addWidget(title)

        # Project label (single-project app — no switcher)
        self._project_lbl = QLabel("项目: —")
        self._project_lbl.setStyleSheet(f"color:{_C_MUTED}; font-size:12px;")
        root.addWidget(self._project_lbl)

        # Search / filter row
        search_row = QHBoxLayout()
        search_row.setSpacing(6)
        self._search = QLineEdit()
        self._search.setPlaceholderText("输入编号、物种、地点、采集人筛选")
        self._search.setClearButtonEnabled(True)
        self._search.setStyleSheet(
            f"background:{_C_INPUT_BG}; border:1px solid {_C_BORDER_HI}; "
            f"border-radius:5px; color:{_C_TEXT}; padding:6px 8px; font-size:12px;"
        )
        self._search.textChanged.connect(self._on_filter_text)
        self._search.returnPressed.connect(self.select_filtered)
        search_row.addWidget(self._search, stretch=1)
        self._btn_select_filtered = QPushButton("选择匹配")
        self._btn_select_filtered.setObjectName("QuickBtn")
        self._btn_select_filtered.clicked.connect(self.select_filtered)
        search_row.addWidget(self._btn_select_filtered)
        root.addLayout(search_row)

        # Quick-select buttons
        qrow = QHBoxLayout()
        qrow.setSpacing(6)
        self._btn_all = QPushButton("全选")
        self._btn_rna = QPushButton("仅 RNA")
        self._btn_sample_only = QPushButton("仅样品")
        self._btn_clear = QPushButton("清空")
        self._btn_clear_filter = QPushButton("清除筛选")
        self._btn_rna.setToolTip("只勾选 R 前缀（RNA 组织保存于 RNAlater）的标本")
        self._btn_sample_only.setToolTip("只勾选非 R 前缀的标本")
        for b, fn in (
            (self._btn_all, self.select_all),
            (self._btn_rna, self.select_rna_only),
            (self._btn_sample_only, self.select_sample_only),
            (self._btn_clear, self.clear_selection),
            (self._btn_clear_filter, self.clear_filter),
        ):
            b.setObjectName("QuickBtn")
            b.clicked.connect(lambda _=False, f=fn: f())
            qrow.addWidget(b)
        qrow.addStretch()
        root.addLayout(qrow)

        # Specimen grid (scrollable)
        self._grid_container = QWidget()
        self._grid = QGridLayout(self._grid_container)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setHorizontalSpacing(8)
        self._grid.setVerticalSpacing(8)
        self._grid.setAlignment(Qt.AlignmentFlag.AlignTop)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(self._grid_container)
        scroll.setMinimumHeight(180)
        root.addWidget(scroll, stretch=1)

        self._empty = QLabel("未加载标本。请在配置/项目中选择一个项目。")
        self._empty.setStyleSheet(f"color:{_C_MUTED_DIM}; font-size:12px;")
        self._empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(self._empty)
        self._empty.hide()

    # ── Public API ───────────────────────────────────────────────────────────

    def set_project_name(self, name: str) -> None:
        self._project_lbl.setText(f"项目: {name or '—'}")

    def set_specimens(self, specimens: list[dict]) -> None:
        self._specimens = list(specimens or [])
        self._checked = set(range(len(self._specimens)))  # default: all included
        self._rebuild_grid()
        self._empty.setVisible(not self._specimens)
        self.selection_changed.emit()

    def selected_indices(self) -> list[int]:
        return sorted(i for i in self._checked if 0 <= i < len(self._specimens))

    def select_all(self) -> None:
        self._checked = set(range(len(self._specimens)))
        self._sync_checks()
        self.selection_changed.emit()

    def select_rna_only(self) -> None:
        self._checked = {i for i, sp in enumerate(self._specimens) if has_rna_tissue(sp)}
        self._sync_checks()
        self.selection_changed.emit()

    def select_sample_only(self) -> None:
        self._checked = {i for i, sp in enumerate(self._specimens) if not has_rna_tissue(sp)}
        self._sync_checks()
        self.selection_changed.emit()

    def clear_selection(self) -> None:
        self._checked = set()
        self._sync_checks()
        self.selection_changed.emit()

    def clear_filter(self) -> None:
        self._search.clear()

    def select_filtered(self) -> None:
        visible = self._visible_indices()
        self._checked = set(visible)
        self._sync_checks()
        self.selection_changed.emit()

    def select_only_uid(self, uid: str) -> bool:
        """Check exactly one specimen by its uniqueId (workspace jump)."""
        target = -1
        for i, sp in enumerate(self._specimens):
            if unique_id(sp) == uid:
                target = i
                break
        if target < 0:
            return False
        self._checked = {target}
        self._sync_checks()
        self.selection_changed.emit()
        return True

    # ── Grid construction ─────────────────────────────────────────────────────

    def _rebuild_grid(self) -> None:
        while self._grid.count():
            it = self._grid.takeAt(0)
            w = it.widget()
            if w is not None:
                w.setParent(None)
        self._items.clear()

        visible = self._visible_indices()
        for slot, idx in enumerate(visible):
            sp = self._specimens[idx]
            frame = self._make_item(idx, sp)
            self._grid.addWidget(frame, slot // _GRID_COLS, slot % _GRID_COLS)
        # keep last column from stretching items oddly
        self._grid.setColumnStretch(_GRID_COLS, 1)
        if self._specimens and not visible:
            self._empty.setText("没有匹配的标本编号。")
            self._empty.show()
        else:
            self._empty.setText("未加载标本。请在配置/项目中选择一个项目。")
            self._empty.setVisible(not self._specimens)

    def _make_item(self, idx: int, sp: dict) -> QFrame:
        rna = has_rna_tissue(sp)
        frame = QFrame()
        frame.setObjectName("SpecItem")
        frame.setProperty("selected", idx in self._checked)
        row = QHBoxLayout(frame)
        row.setContentsMargins(8, 5, 8, 5)
        row.setSpacing(8)

        chk = QCheckBox()
        chk.setChecked(idx in self._checked)
        chk.stateChanged.connect(lambda _s, i=idx: self._on_check(i, chk.isChecked()))
        row.addWidget(chk)

        uid = unique_id(sp) or f"#{idx}"
        cn = sp.get("species") or sp.get("scientificNameCn") or ""
        lbl = QLabel(uid + (f"   {cn}" if cn else ""))
        lbl.setStyleSheet(f"color:{_C_TEXT_SOFT}; font-size:12px;")
        row.addWidget(lbl, stretch=1)

        if rna:
            badge = QLabel("🧬")
            badge.setToolTip(
                "R 前缀：已取 RNA 并保存于 RNAlater；将额外打印 RNAlater 组织管标签"
            )
            row.addWidget(badge)

        frame.mousePressEvent = lambda _e, i=idx: self._toggle_item(i)  # type: ignore[assignment]
        self._items.append(
            {"idx": idx, "sp": sp, "rna": rna, "check": chk, "frame": frame}
        )
        return frame

    # ── State sync ───────────────────────────────────────────────────────────

    def _on_check(self, idx: int, checked: bool) -> None:
        if checked:
            self._checked.add(idx)
        else:
            self._checked.discard(idx)
        self._repaint_item(idx)
        self.selection_changed.emit()

    def _sync_checks(self) -> None:
        for it in self._items:
            chk = it["check"]
            blocked = chk.blockSignals(True)
            chk.setChecked(it["idx"] in self._checked)
            chk.blockSignals(blocked)
            self._repaint_item(it["idx"])

    def _repaint_item(self, idx: int) -> None:
        for it in self._items:
            if it["idx"] == idx:
                f = it["frame"]
                f.setProperty("selected", idx in self._checked)
                f.style().unpolish(f)
                f.style().polish(f)
                break

    def _toggle_item(self, idx: int) -> None:
        if idx in self._checked:
            self._checked.discard(idx)
        else:
            self._checked.add(idx)
        self._sync_checks()
        self.selection_changed.emit()

    def _on_filter_text(self, text: str) -> None:
        self._filter_text = (text or "").strip().lower()
        self._rebuild_grid()

    def _visible_indices(self) -> list[int]:
        return [
            i for i, sp in enumerate(self._specimens)
            if self._matches_filter(sp, i)
        ]

    def _matches_filter(self, sp: dict, idx: int) -> bool:
        q = self._filter_text
        if not q:
            return True
        values = [
            unique_id(sp),
            sp.get("id"),
            sp.get("storage"),
            sp.get("species"),
            sp.get("latin"),
            sp.get("family"),
            sp.get("province"),
            sp.get("site"),
            sp.get("station"),
            sp.get("collector"),
            sp.get("photographer"),
            sp.get("geoArea"),
            sp.get("region"),
            str(idx + 1),
        ]
        haystack = " ".join(str(v or "") for v in values).lower()
        return all(part in haystack for part in q.split())
