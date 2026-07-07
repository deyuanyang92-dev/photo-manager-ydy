"""Table, filtering, charting, and selection workflow for taxonomy."""
from __future__ import annotations

from typing import Any, Optional

from PyQt6.QtCore import QPoint, Qt
from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QProgressBar,
    QTableView,
    QVBoxLayout,
)

from app.views import taxonomy_view_support as _tv
from app.views.taxonomy_dialogs import _TaxonFacetPanel


class TaxonomyTableWorkflowMixin:
    # ── View-switch ───────────────────────────────────────────────────────────

    def _on_view_switch(self, view_key: str) -> None:
        self._view = view_key
        self._page = 1
        self._selected_ids.clear()
        self._select_all_filtered = False
        self._model.clear_checked()

        # Sync button checked states (avoid recursive toggling)
        self._btn_original.blockSignals(True)
        self._btn_worms.blockSignals(True)
        self._btn_compare.blockSignals(True)
        self._btn_original.setChecked(view_key == "original")
        self._btn_worms.setChecked(view_key == "worms")
        self._btn_compare.setChecked(view_key == "compare")
        self._btn_original.blockSignals(False)
        self._btn_worms.blockSignals(False)
        self._btn_compare.blockSignals(False)

        # col controls and add/chart only in original view
        in_original = view_key == "original"
        self._col_ctrl_frame.setVisible(in_original)
        self._btn_add.setVisible(in_original)
        self._btn_chart.setVisible(in_original)

        # Action column delegate — only in original view
        action_col = _tv._COL_DATA_START + len(self._model.columns()) + 1
        if in_original:
            self._table.setItemDelegateForColumn(action_col, self._action_delegate)
        else:
            self._table.setItemDelegateForColumn(action_col, None)

        self._load_page()

    # ── Chart toggle ──────────────────────────────────────────────────────────

    def _on_chart_toggle(self) -> None:
        """Toggle chart dialog (mirrors renderTaxonChart in app.js)."""
        self._show_chart = self._btn_chart.isChecked()
        if self._show_chart:
            self._open_chart_dialog()
        elif self._chart_dialog is not None:
            self._chart_dialog.close()

    def _chart_entries(self) -> list[tuple[str, int]]:
        """Return Top-12 order buckets (mirrors web renderTaxonChart)."""
        if self._svc is None:
            return []
        rows, total = self._svc.all_records(page=0, page_size=1_000_000)
        if len(rows) < total:
            rows, _ = self._svc.all_records(page=0, page_size=max(total, 1))
        buckets: dict[str, int] = {}
        for rec in rows:
            if self._filter_text and not self._record_matches_filter(rec):
                continue
            label = str(rec.get("orderCn") or rec.get("order") or "—").strip() or "—"
            buckets[label] = buckets.get(label, 0) + 1
        return sorted(buckets.items(), key=lambda item: (-item[1], item[0]))[:12]

    def _record_matches_filter(self, rec: dict[str, Any]) -> bool:
        needle = self._filter_text.strip().lower()
        if not needle:
            return True
        if self._filter_col:
            return needle in str(rec.get(self._filter_col, "")).lower()
        return any(needle in str(rec.get(col["key"], "")).lower() for col in _tv._ALL_COLS)

    def _open_chart_dialog(self) -> None:
        entries = self._chart_entries()
        if self._chart_dialog is not None:
            self._chart_dialog.close()
        dlg = QDialog(self)
        dlg.setWindowTitle("分类群分布图表")
        dlg.setMinimumWidth(520)
        dlg.setMinimumHeight(360)
        dlg.finished.connect(self._on_chart_dialog_finished)
        layout = QVBoxLayout(dlg)
        layout.setContentsMargins(18, 18, 18, 14)
        layout.setSpacing(12)
        title = QLabel("按目统计")
        title.setStyleSheet(f"font-size: 16px; font-weight: 600; color: {_tv._C_TEXT};")
        layout.addWidget(title)
        subtitle = QLabel("显示当前筛选条件下数量最多的前 12 个目。")
        subtitle.setStyleSheet(f"font-size: 12px; color: {_tv._C_MUTED};")
        layout.addWidget(subtitle)
        body = QFrame()
        body.setStyleSheet(
            f"QFrame {{ background: {_tv._C_PANEL}; border: 1px solid {_tv._C_BORDER}; border-radius: 8px; }}"
        )
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(14, 14, 14, 14)
        body_layout.setSpacing(10)
        if entries:
            max_count = max(count for _, count in entries)
            for label, count in entries:
                row_layout = QHBoxLayout()
                row_layout.setSpacing(10)
                name = QLabel(label)
                name.setMinimumWidth(130)
                name.setStyleSheet(f"color: {_tv._C_TEXT}; font-size: 12px;")
                row_layout.addWidget(name)
                bar = QProgressBar()
                bar.setRange(0, max_count)
                bar.setValue(count)
                bar.setTextVisible(False)
                bar.setFixedHeight(12)
                bar.setStyleSheet(
                    f"QProgressBar {{ background: {_tv._C_INPUT}; border: none; border-radius: 6px; }}"
                    f"QProgressBar::chunk {{ background: {_tv._C_ACCENT}; border-radius: 6px; }}"
                )
                row_layout.addWidget(bar, 1)
                value = QLabel(str(count))
                value.setMinimumWidth(36)
                value.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                value.setStyleSheet(f"color: {_tv._C_MUTED}; font-size: 12px;")
                row_layout.addWidget(value)
                body_layout.addLayout(row_layout)
        else:
            empty = QLabel("暂无可统计记录")
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            empty.setStyleSheet(f"color: {_tv._C_MUTED}; padding: 28px;")
            body_layout.addWidget(empty)
        layout.addWidget(body, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(dlg.close)
        layout.addWidget(buttons)
        self._chart_dialog = dlg
        dlg.show()

    def _on_chart_dialog_finished(self) -> None:
        self._chart_dialog = None
        self._show_chart = False
        if hasattr(self, "_btn_chart"):
            self._btn_chart.blockSignals(True)
            self._btn_chart.setChecked(False)
            self._btn_chart.blockSignals(False)

    # ── Column chip handlers ──────────────────────────────────────────────────

    def _on_level_chip(self, level_key: str, show: bool) -> None:
        self._model.set_vis_level(level_key, show)
        self._update_action_delegate_column()

    def _on_lang_chip(self, lang_key: str, show: bool) -> None:
        self._model.set_vis_lang(lang_key, show)
        self._update_action_delegate_column()

    def _update_action_delegate_column(self) -> None:
        """Re-attach delegate to the correct 操作 column after column visibility change."""
        if self._view != "original":
            return
        action_col = _tv._COL_DATA_START + len(self._model.columns()) + 1
        self._table.setItemDelegateForColumn(action_col, self._action_delegate)
        self._table.resizeColumnToContents(action_col)

    def _table_key_press(self, event: "QKeyEvent") -> None:
        """Handle Ctrl+C on the table: copy selected cells in Excel/CSV format."""
        from PyQt6.QtCore import QItemSelection
        from PyQt6.QtGui import QKeyEvent, QKeySequence
        from PyQt6.QtWidgets import QAbstractItemView

        if event.matches(QKeySequence.StandardKey.Copy):
            indexes = self._table.selectionModel().selectedIndexes()
            if not indexes:
                return
            # Sort by row then column
            indexes = sorted(indexes, key=lambda i: (i.row(), i.column()))
            rows: dict[int, list] = {}
            for idx in indexes:
                rows.setdefault(idx.row(), []).append(idx)
            lines = []
            for row_idxs in rows.values():
                parts = []
                for idx in sorted(row_idxs, key=lambda i: i.column()):
                    val = self._model.data(idx, Qt.ItemDataRole.DisplayRole)
                    parts.append(str(val) if val is not None else "")
                lines.append("\t".join(parts))
            from PyQt6.QtWidgets import QApplication
            QApplication.clipboard().setText("\n".join(lines))
            return
        # Fall through to default handling
        QTableView.keyPressEvent(self._table, event)

    def _adjust_font(self, delta: int) -> None:
        """Increase or decrease table font size. Mirrors Excel-style zoom."""
        self._font_size = max(8, min(20, self._font_size + delta))
        self._font_size_lbl.setText(str(self._font_size))
        f = self._table.font()
        f.setPointSize(self._font_size)
        self._table.setFont(f)
        self._table.horizontalHeader().setFont(f)
        # Row height scales with font (approx 2× font pt size)
        row_h = max(22, int(self._font_size * 2.2))
        self._table.verticalHeader().setDefaultSectionSize(row_h)
        self._table.viewport().update()

    # ── Search / filter ───────────────────────────────────────────────────────

    def _on_search(self) -> None:
        self._filter_text = self._search_input.text().strip()
        self._filter_col = self._col_select.currentData() or ""
        self._page = 1
        self._selected_ids.clear()
        self._select_all_filtered = False
        self._model.clear_checked()
        self._load_page()

    def _on_clear_filter(self) -> None:
        self._search_input.clear()
        self._col_select.setCurrentIndex(0)
        self._filter_text = ""
        self._filter_col = ""
        self._page = 1
        self._selected_ids.clear()
        self._select_all_filtered = False
        self._model.clear_checked()
        self._filter_active_label.hide()
        self._load_page()

    # ── Pagination ────────────────────────────────────────────────────────────

    def _on_prev_page(self) -> None:
        if self._page > 1:
            self._page -= 1
            self._load_page()

    def _on_next_page(self) -> None:
        total_pages = max(1, (self._total + _tv._PAGE_SIZE - 1) // _tv._PAGE_SIZE)
        if self._page < total_pages:
            self._page += 1
            self._load_page()

    def _on_jump_page(self) -> None:
        p = self._page_jump.value()
        total_pages = max(1, (self._total + _tv._PAGE_SIZE - 1) // _tv._PAGE_SIZE)
        p = max(1, min(p, total_pages))
        if p != self._page:
            self._page = p
            self._load_page()

    # ── Data loading ──────────────────────────────────────────────────────────

    def _load_page(self) -> None:
        if self._svc is None:
            return

        self._loading = True
        self._loading_label.show()
        self._table.hide()

        # Fetch all records for client-side filtering and sorting
        source_filter = None
        if self._view == "worms":
            source_filter = "seed"

        _fetch_size = max(self._svc.seed_count() + self._svc.user_count(), _tv._PAGE_SIZE)
        all_recs, _ = self._svc.all_records(source_filter=source_filter, page=0, page_size=_fetch_size)

        # Apply text filter client-side
        if self._filter_text:
            q = self._filter_text.lower()
            col_key = self._filter_col
            if col_key:
                all_recs = [r for r in all_recs if q in str(r.get(col_key, "")).lower()]
            else:
                all_recs = [r for r in all_recs if any(q in str(v).lower() for v in r.values())]

        # Apply per-column facet filters (mirrors web taxon filter predicates)
        for col_key, pred in self._col_filters.items():
            if not pred:
                continue
            mode = pred.get("mode", "all")
            if mode == "all":
                continue
            if mode == "include":
                include_vals = set(pred.get("values") or [])
                all_recs = [r for r in all_recs if str(r.get(col_key, "")) in include_vals]
            elif mode == "exclude":
                excluded = set(pred.get("excluded") or [])
                all_recs = [r for r in all_recs if str(r.get(col_key, "")) not in excluded]
            elif mode == "search":
                sq = (pred.get("search") or "").lower()
                excluded = set(pred.get("excluded") or [])
                all_recs = [
                    r for r in all_recs
                    if (not sq or sq in str(r.get(col_key, "")).lower())
                    and str(r.get(col_key, "")) not in excluded
                ]

        # Apply column sort
        if self._sort_col:
            all_recs = sorted(
                all_recs,
                key=lambda r: str(r.get(self._sort_col, "")).lower(),
                reverse=(self._sort_dir == "desc"),
            )

        self._total = len(all_recs)

        # Update filter active label
        has_active_filter = bool(self._filter_text) or bool(self._col_filters)
        if has_active_filter:
            self._filter_active_label.setText(f"已筛选 {self._total} 条")
            self._filter_active_label.show()
        else:
            self._filter_active_label.hide()

        # Client-side pagination
        page_offset = (self._page - 1) * _tv._PAGE_SIZE
        records = all_recs[page_offset : page_offset + _tv._PAGE_SIZE]

        # Back-fill WoRMS mapping status onto the visible rows so review entries
        # surface in the row context menu (mirrors web per-row mappingStatus).
        records = self._annotate_mappings(records)

        self._model.set_records(records, page_offset=page_offset)

        # Re-attach action delegate after model reset (column count may change)
        if self._view == "original":
            action_col = _tv._COL_DATA_START + len(self._model.columns()) + 1
            self._table.setItemDelegateForColumn(action_col, self._action_delegate)

        self._loading = False
        self._loading_label.hide()
        self._table.show()

        self._update_pager()
        self._update_selection_note()

        seed_n = self._svc.seed_count()
        user_n = self._svc.user_count()
        self._stats_label.setText(f"共 {self._total} 条")
        self._footer_label.setText(f"种子库 {seed_n} 条 | 用户 {user_n} 条")

        # Refresh WoRMS job panel (mirrors renderTaxonJobPanel in web)
        self._refresh_job_panel()

    def _update_pager(self) -> None:
        total_pages = max(1, (self._total + _tv._PAGE_SIZE - 1) // _tv._PAGE_SIZE)
        self._page_info.setText(
            f"第 {self._page} / {total_pages} 页（共 {self._total} 条）"
        )
        self._page_jump.setMaximum(total_pages)
        self._page_jump.setValue(self._page)
        self._btn_prev.setEnabled(self._page > 1)
        self._btn_next.setEnabled(self._page < total_pages)

    def _update_selection_note(self) -> None:
        checked_ids = self._model.checked_ids()
        if self._select_all_filtered:
            note = f"已选择全部筛选结果（{self._total} 条）"
        else:
            note = f"已选 {len(checked_ids)} 条"
        self._selection_note.setText(note)
        self._btn_worms_sel.setEnabled(bool(checked_ids) or self._select_all_filtered)

    # ── Selection ─────────────────────────────────────────────────────────────

    def _on_selection_changed(self) -> None:
        idxs = self._table.selectionModel().selectedRows()
        self._selected_ids = []
        for idx in idxs:
            rec = self._model.record_at(idx.row())
            if rec and rec.get("recordId"):
                self._selected_ids.append(rec["recordId"])
        self._select_all_filtered = False
        self._update_selection_note()

    def _on_select_all_filtered(self) -> None:
        self._select_all_filtered = True
        self._model.set_all_page_checked(True)
        self._update_selection_note()

    def _on_deselect(self) -> None:
        self._selected_ids.clear()
        self._select_all_filtered = False
        self._model.clear_checked()
        self._table.clearSelection()
        self._update_selection_note()

    # ── Row context menu (mirrors openTaxonRowMenu / renderTaxonRowMenu) ──────

    def _on_row_context_menu(self, pos: QPoint) -> None:
        index = self._table.indexAt(pos)
        if not index.isValid():
            return
        rec = self._model.record_at(index.row())
        if rec is None:
            return
        menu = QMenu(self._table)
        menu.setStyleSheet(f"QMenu {{ background: {_tv._C_PANEL}; color: {_tv._C_TEXT}; border: 1px solid {_tv._C_BORDER}; border-radius: 6px; }} QMenu::item {{ padding: 6px 18px; font-size: 12px; }} QMenu::item:selected {{ background: {_tv._C_ACCENT_SOFT}; }} QMenu::separator {{ background: {_tv._C_BORDER}; height: 1px; margin: 4px 0; }}")
        title_action = menu.addAction(rec.get("species") or rec.get("class") or "当前记录")
        title_action.setEnabled(False)
        menu.addSeparator()
        wm = menu.addAction("WoRMS 匹配当前物种")
        wm.triggered.connect(lambda: self._on_worms_match_row(rec))
        mapping_candidates = rec.get("mappingCandidates") or []
        if mapping_candidates:
            ra = menu.addAction(f"审核 WoRMS 候选（{len(mapping_candidates)} 个）")
            ra.triggered.connect(lambda: self._on_review_worms_row(rec))
        checked_ids = self._model.checked_ids() or self._selected_ids
        if len(checked_ids) > 1 and rec.get("recordId") in checked_ids:
            menu.addSeparator()
            ba = menu.addAction(f"WoRMS 更新已选 {len(checked_ids)} 条")
            ba.triggered.connect(lambda: self._on_worms_update(selected_only=True))
        if self._select_all_filtered:
            menu.addSeparator()
            fa = menu.addAction(f"WoRMS 更新全部筛选结果 {self._total} 条")
            fa.triggered.connect(lambda: self._on_worms_update(selected_only=False))
        if rec.get("recordId", "").startswith("user:"):
            menu.addSeparator()
            ea = menu.addAction("编辑"); ea.triggered.connect(lambda: self._edit_record(rec))
            da = menu.addAction("删除"); da.triggered.connect(lambda: self._delete_record(rec))
        menu.exec(self._table.viewport().mapToGlobal(pos))

    # ── Column header facet filter (mirrors openTaxonFacetMenu) ──────────────

    def _on_header_context_menu(self, pos: QPoint) -> None:
        self._open_facet_for_column(self._table.horizontalHeader().logicalIndexAt(pos))

    def _open_facet_for_column(self, logical_col: int) -> None:
        if self._svc is None:
            return
        data_idx = logical_col - _tv._COL_DATA_START
        cols = self._model.columns()
        if data_idx < 0 or data_idx >= len(cols):
            return
        col_def = cols[data_idx]
        all_recs, total = self._svc.all_records(page=0, page_size=1_000_000)
        if len(all_recs) < total:
            all_recs, _ = self._svc.all_records(page=0, page_size=max(total, 1))
        if self._facet_panel is not None:
            self._facet_panel.close()
        panel = _TaxonFacetPanel(col_def["key"], col_def["label"], all_recs, current_predicate=self._col_filters.get(col_def["key"]), parent=self)
        panel.filter_applied.connect(self._on_facet_filter_applied)
        panel.sort_requested.connect(self._on_facet_sort)
        header = self._table.horizontalHeader()
        x = header.sectionViewportPosition(logical_col)
        panel.move(self._table.mapToGlobal(QPoint(x, header.height())))
        panel.show(); panel.raise_(); panel.activateWindow()
        self._facet_panel = panel

    def _on_facet_filter_applied(self, col_key: str, predicate: Optional[dict[str, Any]]) -> None:
        if predicate is None: self._col_filters.pop(col_key, None)
        else: self._col_filters[col_key] = predicate
        self._page = 1; self._selected_ids.clear(); self._select_all_filtered = False
        self._model.clear_checked(); self._facet_panel = None; self._load_page()

    def _on_facet_sort(self, col_key: str, direction: str) -> None:
        self._sort_col = col_key; self._sort_dir = direction; self._page = 1; self._load_page()
