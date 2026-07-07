"""Dialogs and popups used by the taxonomy view."""
from __future__ import annotations

from typing import Any, Optional

from PyQt6.QtCore import QPoint, Qt, pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from app.views import taxonomy_view_support as _tv
from app.views.taxonomy_workers import _WormsSearchWorker

if False:  # TYPE_CHECKING
    from app.services.worms_service import WormsService


class _RecordDialog(QDialog):
    """Form dialog for adding or editing a user taxonomy record.

    Mirrors openTaxonomyTableModal() in app.js.
    Shows a '查看历史' button when the record has a history[] list.
    """

    def __init__(
        self,
        parent: Optional[QWidget] = None,
        record: Optional[dict[str, Any]] = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("编辑分类条目" if record else "新增分类条目")
        self.setMinimumWidth(420)
        self._record = record or {}
        self._inputs: dict[str, QLineEdit] = {}
        self._btn_history: QPushButton = QPushButton("查看历史")
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(10)
        layout.setContentsMargins(16, 16, 16, 12)

        form = QFormLayout()
        form.setSpacing(8)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        for key, label_text, required in _tv._DIALOG_FIELDS:
            inp = QLineEdit()
            inp.setText(self._record.get(key, ""))
            if required:
                inp.setPlaceholderText("必填")
            form.addRow(label_text, inp)
            self._inputs[key] = inp

        layout.addLayout(form)

        # History button — visible only when record has history entries
        history = self._record.get("history", [])
        self._btn_history.setObjectName("Outline")
        self._btn_history.setVisible(bool(history))
        self._btn_history.clicked.connect(self._show_history)
        layout.addWidget(self._btn_history)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._validate_and_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _validate_and_accept(self) -> None:
        for key, label_text, required in _tv._DIALOG_FIELDS:
            if required and not self._inputs[key].text().strip():
                QMessageBox.warning(self, "必填项", f"「{label_text}」不能为空")
                self._inputs[key].setFocus()
                return
        self.accept()

    def _show_history(self) -> None:
        history = self._record.get("history", [])
        if not history:
            return
        dlg = _HistoryDialog(self, history=history)
        restored = dlg.exec_and_get()
        if restored is not None:
            # Apply restored snapshot to the form inputs
            for k, v in restored.items():
                inp = self._inputs.get(k)
                if inp is not None:
                    inp.setText(str(v))

    def get_record(self) -> dict[str, Any]:
        return {k: inp.text().strip() for k, inp in self._inputs.items()}


class _HistoryDialog(QDialog):
    """Shows history entries for a user record and allows 1-level rollback.

    Each entry in history[] has: { "at": ISO8601, "before": {10 fields} }
    Selecting an entry and clicking "回滚到此版本" fills the parent form
    with the snapshot values (the parent dialog still needs OK to persist).
    """

    def __init__(
        self,
        parent: Optional[QWidget] = None,
        history: Optional[list[dict[str, Any]]] = None,
    ) -> None:
        super().__init__(parent)
        _tv._refresh_palette()
        self.setWindowTitle("编辑历史")
        self.setMinimumWidth(560)
        self.setMinimumHeight(320)
        self._history = list(reversed(history or []))  # newest first
        self._selected_before: Optional[dict[str, Any]] = None
        self._build_ui()

    def _build_ui(self) -> None:
        from PyQt6.QtWidgets import QListWidget, QListWidgetItem

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 12)
        layout.setSpacing(10)

        info = QLabel("选择历史版本后点击「回滚」，将把该快照填入编辑框（需再点确定保存）。")
        info.setWordWrap(True)
        info.setStyleSheet(f"color:{_tv._C_MUTED}; font-size:11px;")
        layout.addWidget(info)

        self._list = QListWidget()
        self._list.setAlternatingRowColors(True)
        for entry in self._history:
            at = entry.get("at", "")
            before = entry.get("before", {})
            species = before.get("species", "")
            family  = before.get("family", "")
            label   = f"{at[:19].replace('T', ' ')}  →  {species} ({family})"
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, before)
            self._list.addItem(item)
        layout.addWidget(self._list, 1)

        buttons = QDialogButtonBox()
        btn_rollback = buttons.addButton("回滚到此版本", QDialogButtonBox.ButtonRole.AcceptRole)
        btn_cancel   = buttons.addButton("取消",         QDialogButtonBox.ButtonRole.RejectRole)
        btn_rollback.clicked.connect(self._on_rollback)
        btn_cancel.clicked.connect(self.reject)
        layout.addWidget(buttons)

    def _on_rollback(self) -> None:
        item = self._list.currentItem()
        if item is None:
            QMessageBox.information(self, "提示", "请先选择一条历史记录")
            return
        self._selected_before = item.data(Qt.ItemDataRole.UserRole)
        self.accept()

    def exec_and_get(self) -> Optional[dict[str, Any]]:
        """Run dialog modally; return the snapshot dict if user confirmed, else None."""
        if self.exec() == QDialog.DialogCode.Accepted:
            return self._selected_before
        return None


# ── Facet filter panel (mirrors renderTaxonFacetMenu in app.js) ──────────────

class _TaxonFacetPanel(QFrame):
    """Per-column facet filter popup (mirrors renderTaxonFacetMenu in app.js)."""

    filter_applied = pyqtSignal(str, object)
    sort_requested = pyqtSignal(str, str)

    def __init__(self, column_key: str, column_label: str, all_records: list[dict[str, Any]], current_predicate: Optional[dict[str, Any]] = None, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent, Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool)
        _tv._refresh_palette()
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setObjectName("TaxonFacetPanel")
        self._col_key = column_key
        self._col_label = column_label
        self._all_records = all_records
        self._draft: Optional[dict[str, Any]] = dict(current_predicate) if current_predicate else {"mode": "all"}
        self._search_text = ""
        self._build_ui()
        self._fill_values()
        self.setStyleSheet(f"QFrame#TaxonFacetPanel {{ background: {_tv._C_PANEL}; border: 1px solid {_tv._C_BORDER}; border-radius: 8px; }}")
        self.setMinimumWidth(280)

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 10)
        layout.setSpacing(8)
        layout.addWidget(self._make_label(f"{self._col_label} 筛选", "13px", "600", _tv._C_TEXT))
        sort_row = QHBoxLayout()
        for txt, d in [("升序", "asc"), ("降序", "desc")]:
            b = QPushButton(txt); b.setObjectName("Outline"); b.setFixedHeight(24)
            b.clicked.connect(lambda _=False, dd=d: self.sort_requested.emit(self._col_key, dd))
            sort_row.addWidget(b)
        sort_row.addStretch(); layout.addLayout(sort_row)
        self._search_inp = QLineEdit(); self._search_inp.setPlaceholderText("在此列搜索值..."); self._search_inp.setFixedHeight(28)
        self._search_inp.textChanged.connect(self._on_search_changed); layout.addWidget(self._search_inp)
        sel_row = QHBoxLayout(); sel_row.setSpacing(6)
        for txt, fn in [("全选", self._on_select_all), ("全不选", self._on_select_none)]:
            b = QPushButton(txt); b.setObjectName("Outline"); b.setFixedHeight(22); b.clicked.connect(fn); sel_row.addWidget(b)
        self._btn_found = QPushButton("选搜索结果"); self._btn_found.setObjectName("Outline"); self._btn_found.setFixedHeight(22)
        self._btn_found.setEnabled(False); self._btn_found.clicked.connect(self._on_select_found); sel_row.addWidget(self._btn_found); sel_row.addStretch(); layout.addLayout(sel_row)
        self._meta_label = QLabel(""); self._meta_label.setStyleSheet(f"color: {_tv._C_MUTED}; font-size: 11px; background: transparent;"); layout.addWidget(self._meta_label)
        self._values_list = QListWidget(); self._values_list.setMaximumHeight(200)
        self._values_list.setStyleSheet(f"QListWidget {{ background: {_tv._C_INPUT}; border: 1px solid {_tv._C_BORDER}; border-radius: 4px; }} QListWidget::item {{ color: {_tv._C_TEXT}; padding: 3px 6px; }}")
        layout.addWidget(self._values_list, 1)
        act_row = QHBoxLayout(); act_row.setSpacing(6)
        btn_ok = QPushButton("确定"); btn_ok.setObjectName("Primary"); btn_ok.setFixedHeight(28); btn_ok.clicked.connect(self._apply_current_column_filter); act_row.addWidget(btn_ok)
        btn_cancel = QPushButton("取消"); btn_cancel.setObjectName("Outline"); btn_cancel.setFixedHeight(28); btn_cancel.clicked.connect(self.close); act_row.addWidget(btn_cancel)
        btn_clear = QPushButton("清除筛选"); btn_clear.setObjectName("Outline"); btn_clear.setFixedHeight(28); btn_clear.clicked.connect(self._clear_current_column_filter); act_row.addWidget(btn_clear)
        layout.addLayout(act_row)

    @staticmethod
    def _make_label(text: str, size: str, weight: str, color: str) -> QLabel:
        lbl = QLabel(text); lbl.setStyleSheet(f"font-size: {size}; font-weight: {weight}; color: {color}; background: transparent;"); return lbl

    def _unique_values(self) -> list[tuple[str, int]]:
        counts: dict[str, int] = {}
        q = self._search_text.strip().lower()
        for rec in self._all_records:
            v = str(rec.get(self._col_key, "") or "")
            if q and q not in v.lower(): continue
            counts[v] = counts.get(v, 0) + 1
        return sorted(counts.items(), key=lambda x: (-x[1], x[0]))

    def _fill_values(self) -> None:
        items = self._unique_values()
        self._values_list.clear()
        total_unique = len({str(r.get(self._col_key, "")) for r in self._all_records})
        self._meta_label.setText(f"匹配 {len(items)} / {total_unique} 个值")
        try: self._values_list.itemChanged.disconnect()
        except (RuntimeError, TypeError): pass
        for value, count in items:
            item = QListWidgetItem(f"{value or '(空白)'}  ({count})")
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
            item.setCheckState(Qt.CheckState.Checked if self._value_checked(value) else Qt.CheckState.Unchecked)
            item.setData(Qt.ItemDataRole.UserRole, value)
            self._values_list.addItem(item)
        self._values_list.itemChanged.connect(self._on_item_changed)

    def _value_checked(self, value: str) -> bool:
        """Mirrors taxonFacetValueChecked(menu, value) in app.js."""
        draft = self._draft or {"mode": "all"}
        mode = draft.get("mode", "all")
        if mode == "include": return value in (draft.get("values") or [])
        if mode == "exclude": return value not in (draft.get("excluded") or [])
        if mode == "search":
            q = draft.get("search", "")
            return ((not q) or (q.lower() in value.lower())) and value not in (draft.get("excluded") or [])
        return True

    def _on_search_changed(self, text: str) -> None:
        self._search_text = text; self._btn_found.setEnabled(bool(text.strip())); self._fill_values()

    def _on_item_changed(self, item: QListWidgetItem) -> None:
        """Mirrors toggleTaxonFacetValue in app.js."""
        value: str = item.data(Qt.ItemDataRole.UserRole)
        checked = item.checkState() == Qt.CheckState.Checked
        draft = self._draft or {"mode": "all"}; mode = draft.get("mode", "all")
        if mode == "all":
            if not checked: self._draft = {"mode": "exclude", "excluded": [value]}
            return
        if mode == "include":
            vals: list = list(draft.get("values") or [])
            if checked and value not in vals: vals.append(value)
            elif not checked: vals = [v for v in vals if v != value]
            self._draft = {"mode": "include", "values": vals}; return
        excl: list = list(draft.get("excluded") or [])
        if not checked and value not in excl: excl.append(value)
        elif checked: excl = [v for v in excl if v != value]
        self._draft = {**draft, "excluded": excl}

    def _on_select_all(self) -> None: self._draft = {"mode": "all"}; self._fill_values()
    def _on_select_none(self) -> None: self._draft = {"mode": "include", "values": []}; self._fill_values()

    def _on_select_found(self) -> None:
        q = self._search_text.strip()
        if q: self._draft = {"mode": "search", "search": q, "excluded": []}; self._fill_values()

    def _apply_current_column_filter(self) -> None:
        draft = self._draft or {"mode": "all"}
        self.filter_applied.emit(self._col_key, None if draft.get("mode") == "all" else dict(draft))
        self.close()

    def _clear_current_column_filter(self) -> None: self.filter_applied.emit(self._col_key, None); self.close()

    def show_below(self, ref_widget: QWidget) -> None:
        self.move(ref_widget.mapToGlobal(QPoint(0, ref_widget.height() + 2))); self.show(); self.raise_(); self.activateWindow()




# ── WoRMS match dialog (mirrors renderWormsMatchModal in app.js) ──────────────

class _WormsMatchDialog(QDialog):
    """Search WoRMS and select the correct candidate (mirrors renderWormsMatchModal)."""

    def __init__(self, row: dict[str, Any], worms_svc: "WormsService", parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        _tv._refresh_palette()
        self._row = row; self._svc = worms_svc; self._selected: Optional[dict[str, Any]] = None
        self._chain: list[dict[str, Any]] = []; self._worker: Optional[_WormsSearchWorker] = None; self._result: Optional[dict[str, Any]] = None
        self.setWindowTitle("WoRMS 匹配物种"); self.setMinimumWidth(680); self.setMinimumHeight(420)
        self._build_ui(); self._start_worms_match_search()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self); layout.setContentsMargins(18, 18, 18, 14); layout.setSpacing(10)
        t = QLabel("WoRMS 匹配物种"); t.setStyleSheet(f"font-size: 16px; font-weight: 600; color: {_tv._C_TEXT};"); layout.addWidget(t)
        o = QLabel(f"原始种名：{self._row.get('species', '')}"); o.setStyleSheet(f"color: {_tv._C_MUTED}; font-size: 12px;"); layout.addWidget(o)
        sr = QHBoxLayout(); sr.setSpacing(8)
        self._search_input = QLineEdit(); self._search_input.setPlaceholderText("输入科学名"); self._search_input.setText(self._row.get("species", "")); self._search_input.returnPressed.connect(self._start_worms_match_search); sr.addWidget(self._search_input, 1)
        self._fuzzy_check = QCheckBox("模糊匹配"); self._fuzzy_check.setStyleSheet(f"color: {_tv._C_MUTED};"); sr.addWidget(self._fuzzy_check)
        bs = QPushButton("搜索"); bs.setObjectName("Outline"); bs.setFixedWidth(60); bs.clicked.connect(self._start_worms_match_search); sr.addWidget(bs); layout.addLayout(sr)
        self._error_label = QLabel(""); self._error_label.setStyleSheet(f"color: {_tv._C_DANGER}; font-size: 11px;"); self._error_label.hide(); layout.addWidget(self._error_label)
        self._loading_label = QLabel("正在查询 WoRMS..."); self._loading_label.setStyleSheet(f"color: {_tv._C_ACCENT}; font-size: 12px;"); self._loading_label.hide(); layout.addWidget(self._loading_label)
        body = QSplitter(Qt.Orientation.Horizontal); body.setHandleWidth(6)
        self._results_list = QListWidget(); self._results_list.setStyleSheet(f"QListWidget {{ background: {_tv._C_PANEL}; border: 1px solid {_tv._C_BORDER}; border-radius: 4px; }} QListWidget::item {{ color: {_tv._C_TEXT}; padding: 6px 8px; }} QListWidget::item:selected {{ background: {_tv._C_ACCENT}; }}")
        self._results_list.currentItemChanged.connect(self._on_result_selected); body.addWidget(self._results_list)
        df = QFrame(); df.setStyleSheet(f"QFrame {{ background: {_tv._C_PANEL}; border: 1px solid {_tv._C_BORDER}; border-radius: 4px; }}")
        dl = QVBoxLayout(df); dl.setContentsMargins(10, 10, 10, 10); dl.setSpacing(4)
        dt = QLabel("采用后保存的 WoRMS 分类链"); dt.setStyleSheet(f"font-size: 11px; font-weight: 600; color: {_tv._C_MUTED}; background: transparent;"); dl.addWidget(dt)
        self._chain_label = QLabel("选择候选后预览标准分类阶元"); self._chain_label.setStyleSheet(f"color: {_tv._C_DIM}; font-size: 11px; background: transparent;"); self._chain_label.setWordWrap(True); self._chain_label.setAlignment(Qt.AlignmentFlag.AlignTop); dl.addWidget(self._chain_label)
        self._chain_loading_label = QLabel("加载分类链..."); self._chain_loading_label.setStyleSheet(f"color: {_tv._C_ACCENT}; font-size: 11px; background: transparent;"); self._chain_loading_label.hide(); dl.addWidget(self._chain_loading_label); dl.addStretch()
        body.addWidget(df); body.setSizes([340, 300]); layout.addWidget(body, 1)
        ar = QHBoxLayout(); ar.setSpacing(8)
        ms = self._row.get("mappingStatus", ""); bl = "重新匹配并保存" if ms and ms != "unprocessed" else "采用并保存"
        self._btn_save = QPushButton(bl); self._btn_save.setObjectName("Primary"); self._btn_save.setEnabled(False); self._btn_save.clicked.connect(self._accept_selected_worms_match); ar.addWidget(self._btn_save)
        bn = QPushButton("标记未找到"); bn.setObjectName("Outline"); bn.clicked.connect(self._accept_no_worms_match); ar.addWidget(bn)
        bc = QPushButton("取消"); bc.setObjectName("Outline"); bc.clicked.connect(self.reject); ar.addWidget(bc); ar.addStretch(); layout.addLayout(ar)

    def _start_worms_match_search(self) -> None:
        query = self._search_input.text().strip()
        if not query: return
        # Cooperative supersede — never terminate() (it can corrupt Qt state);
        # the old worker finishes its network call but drops its result.
        if self._worker and self._worker.isRunning(): self._worker.requestInterruption()
        self._selected = None; self._chain = []; self._error_label.hide(); self._loading_label.show(); self._results_list.clear(); self._chain_label.setText("选择候选后预览标准分类阶元"); self._btn_save.setEnabled(False)
        self._worker = _WormsSearchWorker(self._svc, query, like=self._fuzzy_check.isChecked(), parent=self)
        self._worker.results_ready.connect(self._on_results_ready); self._worker.error_occurred.connect(self._on_search_error); self._worker.start()

    def _on_results_ready(self, hits: list[dict[str, Any]]) -> None:
        self._loading_label.hide(); self._results_list.clear()
        if not hits:
            item = QListWidgetItem("未找到候选，请修改关键词或启用模糊匹配。"); item.setFlags(Qt.ItemFlag.NoItemFlags); item.setForeground(QColor(_tv._C_DIM)); self._results_list.addItem(item); return
        for rec in hits:
            name = rec.get("valid_name") or rec.get("scientificname") or ""; aphia = rec.get("valid_AphiaID") or rec.get("AphiaID", ""); status = rec.get("status", "")
            chain_str = " > ".join(p for p in [rec.get("class"), rec.get("order"), rec.get("family"), rec.get("genus")] if p)
            item = QListWidgetItem(f"{name}\n{status} · AphiaID {aphia}\n{chain_str}"); item.setData(Qt.ItemDataRole.UserRole, rec); self._results_list.addItem(item)

    def _on_search_error(self, msg: str) -> None:
        self._loading_label.hide(); self._error_label.setText(f"搜索失败：{msg}"); self._error_label.show()

    def _on_result_selected(self, current: Optional[QListWidgetItem], _: Optional[QListWidgetItem]) -> None:
        if current is None: return
        rec = current.data(Qt.ItemDataRole.UserRole)
        if rec is None: return
        self._selected = rec; aphia_id = rec.get("valid_AphiaID") or rec.get("AphiaID")
        if not aphia_id: return
        self._chain_label.hide(); self._chain_loading_label.show(); self._btn_save.setEnabled(False)
        # Stale-response guard: rapid re-selection can leave several chain
        # workers in flight; only the LATEST requested aphia_id may update UI.
        self._chain_expected_aphia = int(aphia_id)
        w = _WormsSearchWorker(self._svc, "", aphia_id=int(aphia_id), parent=self)
        w.chain_ready.connect(lambda chain, a=int(aphia_id): self._on_chain_ready(chain, a))
        w.error_occurred.connect(lambda msg, a=int(aphia_id): self._on_chain_error(msg, a))
        w.start()

    def _is_stale_chain_response(self, aphia_id: Optional[int]) -> bool:
        return (
            aphia_id is not None
            and aphia_id != getattr(self, "_chain_expected_aphia", None)
        )

    def _on_chain_ready(self, chain: list[dict[str, Any]], aphia_id: Optional[int] = None) -> None:
        if self._is_stale_chain_response(aphia_id): return
        self._chain = chain; self._chain_loading_label.hide(); self._chain_label.show()
        self._chain_label.setText("\n".join(f"{n.get('rank','')}  {n.get('scientificname','')}" for n in chain) or "（无分类链数据）")
        self._btn_save.setEnabled(True)

    def _on_chain_error(self, msg: str, aphia_id: Optional[int] = None) -> None:
        if self._is_stale_chain_response(aphia_id): return
        self._chain_loading_label.hide(); self._chain_label.show(); self._chain_label.setText(f"分类链加载失败：{msg}"); self._btn_save.setEnabled(bool(self._selected))

    def _accept_selected_worms_match(self) -> None:
        if self._selected is None: return
        aphia = self._selected.get("valid_AphiaID") or self._selected.get("AphiaID")
        self._result = {"aphia_id": int(aphia) if aphia else None, "worms_record": self._selected, "chain": self._chain}; self.accept()

    def _accept_no_worms_match(self) -> None: self._result = {"no_match": True}; self.accept()

    def get_result(self) -> Optional[dict[str, Any]]: return self._result


# ── WoRMS review dialog (mirrors renderTaxonReviewModal in app.js) ─────────────

class _TaxonReviewDialog(QDialog):
    """Review auto-found WoRMS candidates (mirrors renderTaxonReviewModal in app.js)."""

    def __init__(self, row: dict[str, Any], parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        _tv._refresh_palette()
        self._row = row; self._result: Optional[dict[str, Any]] = None
        self.setWindowTitle("审核 WoRMS 匹配"); self.setMinimumWidth(440); self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self); layout.setContentsMargins(18, 18, 18, 14); layout.setSpacing(10)
        t = QLabel("审核 WoRMS 匹配"); t.setStyleSheet(f"font-size: 16px; font-weight: 600; color: {_tv._C_TEXT};"); layout.addWidget(t)
        o = QLabel(f"原始种名：{self._row.get('species', '')}"); o.setStyleSheet(f"color: {_tv._C_MUTED}; font-size: 12px;"); layout.addWidget(o)
        candidates = self._row.get("mappingCandidates") or []
        if not candidates:
            e = QLabel("没有自动候选，可标记为未找到后重新运行更新。"); e.setStyleSheet(f"color: {_tv._C_DIM}; font-size: 12px;"); e.setWordWrap(True); layout.addWidget(e)
        else:
            for cand in candidates:
                rf = QFrame(); rf.setStyleSheet(f"QFrame {{ background: {_tv._C_PANEL}; border: 1px solid {_tv._C_BORDER}; border-radius: 4px; }}")
                rl = QHBoxLayout(rf); rl.setContentsMargins(10, 8, 10, 8); rl.setSpacing(8)
                aphia = cand.get("valid_AphiaID") or cand.get("AphiaID") or ""; name = cand.get("valid_name") or cand.get("scientificname") or ""
                info = QLabel(f"{name} · AphiaID {aphia}"); info.setStyleSheet(f"color: {_tv._C_TEXT}; font-size: 12px; background: transparent;"); rl.addWidget(info, 1)
                bu = QPushButton("采用"); bu.setObjectName("Primary"); bu.setFixedWidth(56)
                bu.clicked.connect(lambda _=False, c=cand: self._accept_review_candidate(c)); rl.addWidget(bu); layout.addWidget(rf)
        ar = QHBoxLayout(); ar.setSpacing(8)
        bn = QPushButton("标记未找到"); bn.setObjectName("Outline"); bn.clicked.connect(self._accept_no_review_match); ar.addWidget(bn)
        bc = QPushButton("关闭"); bc.setObjectName("Outline"); bc.clicked.connect(self.reject); ar.addWidget(bc); ar.addStretch(); layout.addLayout(ar)

    def _accept_review_candidate(self, cand: dict[str, Any]) -> None:
        aphia = cand.get("valid_AphiaID") or cand.get("AphiaID")
        self._result = {"aphia_id": int(aphia) if aphia else None}; self.accept()

    def _accept_no_review_match(self) -> None: self._result = {"no_match": True}; self.accept()

    def get_result(self) -> Optional[dict[str, Any]]: return self._result
