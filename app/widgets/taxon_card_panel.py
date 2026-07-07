"""taxon_card_panel.py — 分类标签 card (right-rail card 2).

Faithful port of the web oracle's ``renderTaxonNotesCard`` (app.js:9933):
a standalone card showing the 5-level taxonomy as a three-column grid
``级别 | 拉丁名 | 中名`` with inline Latin autocomplete,上下级 chain
validation, a 来源 (candidate source) selector, a ☰ field-visibility menu,
a 编辑 button (opens the taxon edit dialog), and a ▾/▸ collapse toggle.

Owns these specimen columns (latin + Chinese):
    taxon_group / taxon_group_cn
    order_name  / order_cn
    family      / family_cn
    genus       / genus_cn
    scientific_name / scientific_name_cn

The autocomplete reuses the proven machinery in ``taxonomy_input`` and
``TaxonomyService`` (search + ancestor backfill + cross-level fallback).
Genus has no seed autocomplete level (the service exposes 4 sp_keys), so it
is a plain field — matching the data model.

Signals
-------
taxon_changed(field, value)
    Emitted on every committed field edit (db column name + value).
save_requested()
    Emitted from the card-header 💾 (delegates to the workbench save path).
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from app.services.taxonomy_service import TaxonCandidate, TaxonomyService
from app.widgets._collapse import set_layout_children_visible
from app.widgets.taxonomy_input import TaxonLineEdit, TaxonPopup

if TYPE_CHECKING:
    from app.app_context import AppContext
    from app.models.specimen import Specimen


# Each level: (label, latin_db, cn_db, sp_key|None, seed_latin_key, seed_cn_key)
# sp_key None → no autocomplete (genus); seed keys used for ancestor backfill.
_LEVELS: list[tuple[str, str, str, Optional[str], str, str]] = [
    ("类群", "taxon_group",     "taxon_group_cn",     "taxonGroup",     "class",   "classCn"),
    ("目",   "order_name",      "order_cn",           "order",          "order",   "orderCn"),
    ("科",   "family",          "family_cn",          "family",         "family",  "familyCn"),
    ("属",   "genus",           "genus_cn",           None,             "genus",   "genusCn"),
    ("物种", "scientific_name", "scientific_name_cn", "scientificName", "species", "speciesCn"),
]

# sp_key → (label, latin_db, cn_db, seed_latin_key, seed_cn_key)
_BY_SP = {lv[3]: lv for lv in _LEVELS if lv[3]}
# db column → validate_taxonomy_chain sp key (for warning lookup)
_DB_TO_SPKEY = {
    "taxon_group": "taxonGroup", "order_name": "order", "family": "family",
    "genus": "genus", "scientific_name": "scientificName",
}


class TaxonCardPanel(QWidget):
    """分类标签 card — 5-level latin+cn taxonomy editor with autocomplete."""

    taxon_changed = pyqtSignal(str, str)   # (db_field, value)
    save_requested = pyqtSignal()
    open_edit_requested = pyqtSignal()

    def __init__(self, ctx: "AppContext", parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self._latin: dict[str, TaxonLineEdit | QLineEdit] = {}   # db_field → edit
        self._cn: dict[str, QLineEdit] = {}                      # db_field → edit
        self._show_cn = True
        self._active_db: Optional[str] = None
        self._collapsed = False
        self._extra_identifications_original: list[dict[str, str]] = []
        self._extra_identifications_text_at_load = ""

        # Taxonomy service (graceful: None if seed files missing)
        self._svc: Optional[TaxonomyService] = None
        try:
            _root = Path(__file__).parent.parent.parent
            self._svc = TaxonomyService(
                _root / "data" / "taxonomy_seed.json",
                _root / "data" / "user_taxonomy.json",
            )
        except Exception:
            self._svc = None

        self._popup = TaxonPopup()
        self._popup.item_selected.connect(self._on_item_selected)
        self._popup.dismissed.connect(self._popup.hide)
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(80)
        self._search_timer.timeout.connect(self._search_taxon_suggestions_for_active_field)

        self._setup_ui()

    # ── UI ──────────────────────────────────────────────────────────────────
    def _setup_ui(self) -> None:
        card = QFrame(self)
        card.setObjectName("PanelCard")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(card)
        try:
            from app.config.effects import apply_card_shadow
            apply_card_shadow(card)
        except Exception:
            pass

        self._root = QVBoxLayout(card)
        self._root.setContentsMargins(20, 16, 20, 16)
        self._root.setSpacing(12)

        # Header: title + actions (编辑 / 来源 / ☰ / ▾)
        hdr = QHBoxLayout()
        hdr.setContentsMargins(0, 0, 0, 0)
        title = QLabel("分类标签")
        title.setObjectName("CardTitle")
        hdr.addWidget(title)

        # WoRMS quick-fill (moved from metadata panel — fills Latin only)
        self._worms_btn = QPushButton("WoRMS 查")
        self._worms_btn.setObjectName("WormsFill")
        self._worms_btn.setFixedHeight(26)
        self._worms_btn.setToolTip("从 WoRMS 快捷查找物种，填充拉丁分类信息（不覆盖中文）")
        self._worms_btn.clicked.connect(self._on_worms_quick_fill)
        hdr.addWidget(self._worms_btn)
        hdr.addStretch()

        edit_btn = QPushButton("编辑")
        edit_btn.setObjectName("Outline")
        edit_btn.setFixedHeight(26)
        edit_btn.setToolTip("一次编辑五级拉丁名和中名")
        edit_btn.clicked.connect(self.open_edit_requested.emit)
        hdr.addWidget(edit_btn)

        self._source_combo = QComboBox()
        self._source_combo.setObjectName("SourceCombo")
        self._source_combo.setFixedHeight(26)
        self._source_combo.addItem("原始库", "original")
        self._source_combo.addItem("WoRMS库", "worms")
        self._source_combo.setToolTip("候选来源")
        hdr.addWidget(self._source_combo)

        self._fields_btn = QPushButton("☰")
        self._fields_btn.setObjectName("Ghost")
        self._fields_btn.setFixedSize(28, 26)
        self._fields_btn.setToolTip("字段显示控制")
        self._fields_btn.clicked.connect(self._open_fields_menu)
        hdr.addWidget(self._fields_btn)

        self._collapse_btn = QPushButton("▾")
        self._collapse_btn.setObjectName("Ghost")
        self._collapse_btn.setFixedSize(28, 26)
        self._collapse_btn.setToolTip("收起")
        self._collapse_btn.clicked.connect(lambda: self.set_collapsed(not self._collapsed))
        hdr.addWidget(self._collapse_btn)
        self._root.addLayout(hdr)

        # Validation warning area (hidden until mismatches)
        self._warn = QLabel("")
        self._warn.setObjectName("TaxonWarn")
        self._warn.setWordWrap(True)
        self._warn.setStyleSheet(
            "QLabel#TaxonWarn{background:#3a2a14;color:#e8aa60;"
            "border:1px solid #6b4f22;border-radius:6px;padding:6px 8px;font-size:11px;}"
        )
        self._warn.hide()
        self._root.addWidget(self._warn)

        # 3-column grid: 级别 | 拉丁名 | 中名
        self._grid = QGridLayout()
        self._grid.setHorizontalSpacing(8)
        self._grid.setVerticalSpacing(6)
        self._grid.setColumnStretch(1, 3)
        self._grid.setColumnStretch(2, 2)

        col_lvl = QLabel("级别"); col_lvl.setObjectName("MutedSmall")
        self._col_latin = QLabel("拉丁名"); self._col_latin.setObjectName("MutedSmall")
        self._col_cn = QLabel("中名"); self._col_cn.setObjectName("MutedSmall")
        self._grid.addWidget(col_lvl, 0, 0)
        self._grid.addWidget(self._col_latin, 0, 1)
        self._grid.addWidget(self._col_cn, 0, 2)

        self._row_widgets: dict[str, list[QWidget]] = {}
        for i, (label, latin_db, cn_db, sp_key, _sk, _ck) in enumerate(_LEVELS, start=1):
            lbl = QLabel(label)
            lbl.setObjectName("TaxonLabel")
            lbl.setStyleSheet("color:#94a3b8;font-size:11px;")

            if sp_key is not None and self._svc is not None:
                latin = TaxonLineEdit()
                latin.popup_navigate.connect(self._popup.navigate)
                latin.popup_accept.connect(self._on_accept_popup)
                latin.popup_dismiss.connect(self._popup.hide)
                latin.textChanged.connect(lambda t, d=latin_db: self._on_latin_text(d, t))
                latin.editingFinished.connect(lambda d=latin_db: self._on_editing_finished(d))
            else:
                latin = QLineEdit()
                latin.textEdited.connect(lambda t, d=latin_db: self._emit_change(d, t))
            latin.setPlaceholderText(label)
            latin.setFixedHeight(28)

            cn = QLineEdit()
            cn.setPlaceholderText("中名")
            cn.setFixedHeight(28)
            cn.textEdited.connect(lambda t, d=cn_db: self._emit_change(d, t))

            self._grid.addWidget(lbl, i, 0)
            self._grid.addWidget(latin, i, 1)
            self._grid.addWidget(cn, i, 2)
            self._latin[latin_db] = latin
            self._cn[cn_db] = cn
            self._row_widgets[latin_db] = [lbl, latin, cn]

        self._root.addLayout(self._grid)

        extra_hdr = QLabel("附加鉴定 / 混合样")
        extra_hdr.setObjectName("CardTitle")
        self._root.addWidget(extra_hdr)
        self._extra_identifications = QTextEdit()
        self._extra_identifications.setObjectName("TaxonExtraIdentifications")
        self._extra_identifications.setFixedHeight(76)
        self._extra_identifications.setPlaceholderText(
            "一行一个分类：拉丁名 | 中文名 | 科 | 属 | 备注\n"
            "例：Limnodrilus hoffmeisteri | 霍氏水丝蚓 | Naididae | Limnodrilus | 5 个"
        )
        self._extra_identifications.textChanged.connect(
            lambda: self.taxon_changed.emit("additional_identifications", "")
        )
        self._root.addWidget(self._extra_identifications)

        # 备注标签 — web renderTaxonNotesCard tail (app.js:10184).  The notes
        # field lives in 卡2 (not 卡3) to mirror the web right rail.
        notes_hdr = QLabel("备注标签")
        notes_hdr.setObjectName("CardTitle")
        self._root.addWidget(notes_hdr)
        self._notes = QTextEdit()
        self._notes.setObjectName("TaxonNotes")
        self._notes.setFixedHeight(60)
        self._notes.setPlaceholderText(
            "记录野外/实验室备注，例：固定时间、组织取样、形态特征…"
        )
        self._notes.textChanged.connect(
            lambda: self.taxon_changed.emit("notes", self._notes.toPlainText().strip())
        )
        self._root.addWidget(self._notes)

    # ── Public API ──────────────────────────────────────────────────────────
    def set_collapsed(self, collapsed: bool) -> None:
        self._collapsed = collapsed
        # hide everything in root after the header layout (index 0)
        set_layout_children_visible(self._root, 1, not collapsed)
        self._collapse_btn.setText("▸" if collapsed else "▾")
        self._collapse_btn.setToolTip("展开" if collapsed else "收起")
        if collapsed:
            self._popup.hide()

    def is_collapsed(self) -> bool:
        return self._collapsed

    def field_values(self) -> dict[str, str]:
        """Return all owned taxon columns (latin + cn) as db-field → text."""
        out: dict[str, str] = {}
        for db, edit in self._latin.items():
            out[db] = edit.text().strip()
        for db, edit in self._cn.items():
            out[db] = edit.text().strip()
        out["notes"] = self._notes.toPlainText().strip()
        return out

    def additional_identifications(self) -> list[dict[str, str]]:
        """Return extra mixed-sample identifications from the free-form editor."""
        text = self._extra_identifications.toPlainText().strip()
        if text == self._extra_identifications_text_at_load:
            return [dict(row) for row in self._extra_identifications_original]
        rows: list[dict[str, str]] = []
        for line in text.splitlines():
            row = self._parse_extra_identification_line(line)
            if self._has_taxon_identity(row):
                rows.append(row)
        return rows

    def load_specimen(self, sp: "Specimen") -> None:
        def _set_taxon_line_edit_without_signals(edit, val):
            edit.blockSignals(True)
            edit.setText(str(val) if val else "")
            edit.blockSignals(False)
        _set_taxon_line_edit_without_signals(self._latin["taxon_group"], sp.taxon_group)
        _set_taxon_line_edit_without_signals(self._latin["order_name"], sp.order_name)
        _set_taxon_line_edit_without_signals(self._latin["family"], sp.family)
        _set_taxon_line_edit_without_signals(self._latin["genus"], sp.genus)
        _set_taxon_line_edit_without_signals(self._latin["scientific_name"], sp.scientific_name)
        _set_taxon_line_edit_without_signals(self._cn["taxon_group_cn"], sp.taxon_group_cn)
        _set_taxon_line_edit_without_signals(self._cn["order_cn"], sp.order_cn)
        _set_taxon_line_edit_without_signals(self._cn["family_cn"], sp.family_cn)
        _set_taxon_line_edit_without_signals(self._cn["genus_cn"], sp.genus_cn)
        _set_taxon_line_edit_without_signals(self._cn["scientific_name_cn"], sp.scientific_name_cn)
        self._notes.blockSignals(True)
        self._notes.setPlainText(sp.notes or "")
        self._notes.blockSignals(False)
        self._set_additional_identifications(self._stored_additional_identifications(sp.raw))
        self._refresh_validation()

    def apply_values(self, values: dict[str, str]) -> None:
        """Set latin/cn fields from a db-field → text dict (from edit modal)."""
        for db, val in values.items():
            edit = self._latin.get(db) or self._cn.get(db)
            if edit is not None:
                edit.blockSignals(True)
                edit.setText(val or "")
                edit.blockSignals(False)
                self.taxon_changed.emit(db, (val or "").strip())
        self._refresh_validation()

    def clear(self) -> None:
        for edit in list(self._latin.values()) + list(self._cn.values()):
            edit.blockSignals(True)
            edit.clear()
            edit.blockSignals(False)
        self._notes.blockSignals(True)
        self._notes.clear()
        self._notes.blockSignals(False)
        self._set_additional_identifications([])
        self._warn.hide()
        self._popup.hide()

    # ── Autocomplete flow (mirrors TaxonomyInputPanel) ────────────────────────
    def _set_additional_identifications(self, rows: list[dict]) -> None:
        normalised = [
            row for row in (self._normalise_extra_identification(item) for item in rows)
            if self._has_taxon_identity(row)
        ]
        text = "\n".join(self._format_extra_identification(row) for row in normalised)
        self._extra_identifications.blockSignals(True)
        self._extra_identifications.setPlainText(text)
        self._extra_identifications.blockSignals(False)
        self._extra_identifications_original = [dict(row) for row in normalised]
        self._extra_identifications_text_at_load = text

    @staticmethod
    def _first_text(data: dict, *keys: str) -> str:
        for key in keys:
            value = data.get(key)
            if value not in (None, ""):
                return str(value).strip()
        return ""

    @classmethod
    def _normalise_extra_identification(cls, item) -> dict[str, str]:
        if isinstance(item, str):
            return cls._parse_extra_identification_line(item)
        if not isinstance(item, dict):
            return {}
        row = {
            "scientific_name": cls._first_text(
                item, "scientific_name", "scientificName", "scientificname", "species"
            ),
            "scientific_name_cn": cls._first_text(
                item, "scientific_name_cn", "scientificNameCn", "speciesCn", "name_cn"
            ),
            "taxon_group": cls._first_text(
                item, "taxon_group", "taxonGroup", "class", "class_name"
            ),
            "order_name": cls._first_text(item, "order_name", "order", "orderName"),
            "family": cls._first_text(item, "family", "family_name"),
            "genus": cls._first_text(item, "genus", "genus_name"),
            "notes": cls._first_text(item, "notes", "note", "remark", "remarks"),
        }
        return {key: value for key, value in row.items() if value}

    @classmethod
    def _parse_extra_identification_line(cls, line: str) -> dict[str, str]:
        text = line.strip()
        if not text:
            return {}
        parts = [part.strip() for part in re.split(r"\t+|\s*\|\s*", text) if part.strip()]
        if not parts:
            return {}
        if len(parts) == 1:
            key = "scientific_name_cn" if re.search(r"[\u4e00-\u9fff]", parts[0]) else "scientific_name"
            return {key: parts[0]}
        fields = ("scientific_name", "scientific_name_cn", "family", "genus", "notes")
        return {field: parts[i] for i, field in enumerate(fields) if i < len(parts) and parts[i]}

    @staticmethod
    def _has_taxon_identity(row: dict) -> bool:
        return any(
            str(row.get(key) or "").strip()
            for key in (
                "scientific_name",
                "scientific_name_cn",
                "taxon_group",
                "order_name",
                "family",
                "genus",
            )
        )

    @staticmethod
    def _format_extra_identification(row: dict[str, str]) -> str:
        parts = [
            row.get("scientific_name", ""),
            row.get("scientific_name_cn", ""),
            row.get("family", ""),
            row.get("genus", ""),
            row.get("notes", ""),
        ]
        while parts and not parts[-1]:
            parts.pop()
        return " | ".join(parts)

    @classmethod
    def _stored_additional_identifications(cls, raw: dict) -> list[dict[str, str]]:
        if not isinstance(raw, dict):
            return []
        for key in ("additional_identifications", "additionalIdentifications"):
            value = raw.get(key)
            if isinstance(value, list):
                return [
                    row for row in (cls._normalise_extra_identification(item) for item in value)
                    if cls._has_taxon_identity(row)
                ]
        return []

    def _current_context(self) -> dict[str, str]:
        ctx: dict[str, str] = {}
        for sp_key, lv in _BY_SP.items():
            ctx[sp_key] = self._latin[lv[1]].text().strip()
        return ctx

    def _on_latin_text(self, db_field: str, _text: str) -> None:
        self._active_db = db_field
        self._emit_change(db_field, _text)
        self._search_timer.stop()
        self._search_timer.start()

    def _search_taxon_suggestions_for_active_field(self) -> None:
        if self._svc is None or self._active_db is None:
            return
        lv = next((l for l in _LEVELS if l[1] == self._active_db), None)
        if lv is None or lv[3] is None:
            return
        sp_key = lv[3]
        inp = self._latin[self._active_db]
        query = inp.text()
        ctx = self._current_context()
        cands = self._svc.search(sp_key, query, context=ctx)
        cross = False
        if not cands and query.strip():
            cands_all = self._svc.search(sp_key, query, context={})
            cands = [TaxonCandidate(c.value, c.cn,
                                    "user" if c.source == "user" else "cross", c.full)
                     for c in cands_all]
            cross = bool(cands)
        self._popup.populate(sp_key, cands, query, cross_fallback=cross)
        if cands or query.strip():
            self._popup.show_below(inp)
        else:
            self._popup.hide()

    def _on_accept_popup(self) -> None:
        if self._popup.isVisible():
            if not self._popup.accept_current() and self._active_db:
                self._on_editing_finished(self._active_db)

    def _on_item_selected(self, cand: TaxonCandidate) -> None:
        self._popup.hide()
        if self._active_db:
            self._commit_candidate(self._active_db, cand)

    def _on_editing_finished(self, db_field: str) -> None:
        if self._svc is None:
            return
        lv = next((l for l in _LEVELS if l[1] == db_field), None)
        if lv is None or lv[3] is None:
            self._refresh_validation()
            return
        sp_key = lv[3]
        text = self._latin[db_field].text().strip()
        if not text:
            self._refresh_validation()
            return
        from app.services.taxonomy_service import _normalize_taxon_match_text
        q = _normalize_taxon_match_text(text).lower()
        cands = self._svc.search(sp_key, text, context=self._current_context())
        exact = next((c for c in cands if _normalize_taxon_match_text(c.value).lower() == q
                      or (c.cn and _normalize_taxon_match_text(c.cn).lower() == q)), None)
        if exact is None:
            for c in self._svc.search(sp_key, text, context={}):
                if (
                    _normalize_taxon_match_text(c.value).lower() == q
                    or (c.cn and _normalize_taxon_match_text(c.cn).lower() == q)
                ):
                    exact = TaxonCandidate(c.value, c.cn, "cross", c.full)
                    break
        if exact is not None:
            self._commit_candidate(db_field, exact)
        else:
            self._refresh_validation()

    def _commit_candidate(self, db_field: str, cand: TaxonCandidate) -> None:
        """Fill this level + ancestors (latin AND cn) from the candidate."""
        full = cand.full or {}
        order = [lv for lv in _LEVELS if lv[3]]  # autocompletable levels in order
        idx = next((i for i, lv in enumerate(order) if lv[1] == db_field), None)
        if idx is None:
            return
        for lv in order[: idx + 1]:
            _label, latin_db, cn_db, _sp, seed_k, seed_cn = lv
            latin_val = cand.value if latin_db == db_field else (full.get(seed_k, "") or "")
            cn_val = full.get(seed_cn, "") or ""
            if latin_val:
                e = self._latin[latin_db]
                e.blockSignals(True); e.setText(latin_val); e.blockSignals(False)
                self.taxon_changed.emit(latin_db, latin_val)
            # cn auto-fill only when empty (don't clobber user-typed Chinese)
            if cn_val and not self._cn[cn_db].text().strip():
                e = self._cn[cn_db]
                e.blockSignals(True); e.setText(cn_val); e.blockSignals(False)
                self.taxon_changed.emit(cn_db, cn_val)
        self._refresh_validation()

    def _emit_change(self, db_field: str, value: str) -> None:
        self.taxon_changed.emit(db_field, value.strip())
        if db_field in _DB_TO_SPKEY:
            self._refresh_validation()

    # ── Chain validation (上下级 warning) ─────────────────────────────────────
    def _refresh_validation(self) -> None:
        if self._svc is None:
            return
        sp_fields = {
            "taxonGroup":     self._latin["taxon_group"].text().strip(),
            "order":          self._latin["order_name"].text().strip(),
            "family":         self._latin["family"].text().strip(),
            "genus":          self._latin["genus"].text().strip(),
            "scientificName": self._latin["scientific_name"].text().strip(),
        }
        try:
            res = self._svc.validate_taxonomy_chain(sp_fields)
        except Exception:
            self._warn.hide()
            return
        if res.get("ok", True):
            self._warn.hide()
            return
        lines = ["⚠ 上下级不匹配（按权威库校对）"]
        for m in res["mismatches"]:
            exp = m["expected"] + (" " + m["expectedCn"] if m.get("expectedCn") else "")
            lines.append(
                f"「{m['label']}」应为 {exp}（当前 {m['current']}；"
                f"触发：{m['triggeredByLevel']} {m['triggeredBy']}）"
            )
        self._warn.setText("\n".join(lines))
        self._warn.show()

    # ── ☰ field-visibility menu ───────────────────────────────────────────────
    def _open_fields_menu(self) -> None:
        menu = QMenu(self)
        sec = menu.addAction("字段")
        sec.setEnabled(False)
        for label, latin_db, _cn, _sp, _sk, _ck in _LEVELS:
            act = menu.addAction(label)
            act.setCheckable(True)
            act.setChecked(self._row_widgets[latin_db][0].isVisible())
            act.toggled.connect(lambda on, d=latin_db: self._set_row_visible(d, on))
        menu.addSeparator()
        lang = menu.addAction("语言")
        lang.setEnabled(False)
        cn_act = menu.addAction("中名")
        cn_act.setCheckable(True)
        cn_act.setChecked(self._show_cn)
        cn_act.toggled.connect(self._set_cn_visible)
        menu.exec(self._fields_btn.mapToGlobal(self._fields_btn.rect().bottomLeft()))

    def _set_row_visible(self, latin_db: str, visible: bool) -> None:
        for w in self._row_widgets[latin_db]:
            w.setVisible(visible)

    def _set_cn_visible(self, visible: bool) -> None:
        self._show_cn = visible
        self._col_cn.setVisible(visible)
        for cn in self._cn.values():
            cn.setVisible(visible)

    # ── WoRMS quick-fill (moved from metadata_panel) ──────────────────────────
    def _on_worms_quick_fill(self) -> None:
        from pathlib import Path as _Path
        from app.services.worms_service import WormsService
        from app.views.worms_view import WormsQuickFillDialog
        try:
            project_dir = getattr(self.ctx, "current_project_dir", None)
            # Only use the project _data dir when the project ROOT is actually
            # present — mkdir on a gone volume would fabricate a ghost tree at
            # the mountpoint (the unmounted-drive data-loss bug).
            _data = (_Path(project_dir) / "_data") \
                if (project_dir and _Path(project_dir).is_dir()) \
                else (_Path.home() / ".photo_workbench" / "data")
            _data.mkdir(parents=True, exist_ok=True)
            svc = WormsService(
                cache_path=str(_data / "worms_cache.json"),
                jobs_path=str(_data / "worms_jobs.json"),
            )
        except Exception:
            return
        initial = (self._latin["scientific_name"].text().strip()
                   or self._latin["taxon_group"].text().strip())

        def apply_worms_record_to_taxon_fields(rec: dict) -> None:
            fill_fn = getattr(self.ctx, "worms_fill_specimen", None)
            if callable(fill_fn):
                try:
                    fill_fn(rec)
                except Exception:
                    pass
            mapping = {"class": "taxon_group", "order": "order_name",
                       "family": "family", "genus": "genus"}
            for wk, db in mapping.items():
                val = rec.get(wk, "")
                if val:
                    e = self._latin[db]
                    e.blockSignals(True); e.setText(val); e.blockSignals(False)
                    self.taxon_changed.emit(db, val)
            if rec.get("rank") == "Species" and rec.get("scientificname"):
                e = self._latin["scientific_name"]
                e.blockSignals(True); e.setText(rec["scientificname"]); e.blockSignals(False)
                self.taxon_changed.emit("scientific_name", rec["scientificname"])
            self._refresh_validation()

        dlg = WormsQuickFillDialog(
            svc, apply_worms_record_to_taxon_fields,
            initial_query=initial, parent=self,
        )
        dlg.exec()
