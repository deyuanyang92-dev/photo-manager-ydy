"""State refresh, selection, quick toolbar, clipboard, and preset workflow."""
from __future__ import annotations

import copy

from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QColorDialog, QLineEdit

from app.utils.label_core import normalize_template


class LabelDesignerStateMixin:
    # ── State / undo ────────────────────────────────────────────────────────
    def _save_template_snapshot_for_undo(self) -> None:
        self._undo.append(copy.deepcopy(self._tmpl))
        self._redo.clear()
        self._undo_btn.setEnabled(True)
        self._redo_btn.setEnabled(False)

    def _undo_template_edit(self) -> None:
        if not self._undo:
            return
        self._redo.append(copy.deepcopy(self._tmpl))
        self._tmpl = self._undo.pop()
        self._undo_btn.setEnabled(bool(self._undo))
        self._redo_btn.setEnabled(True)
        self._refresh_designer_state()

    def _redo_template_edit(self) -> None:
        if not self._redo:
            return
        self._undo.append(copy.deepcopy(self._tmpl))
        self._tmpl = self._redo.pop()
        self._redo_btn.setEnabled(bool(self._redo))
        self._undo_btn.setEnabled(True)
        self._refresh_designer_state()

    def _refresh_designer_state(self) -> None:
        self._tmpl = normalize_template(self._tmpl)
        self._refresh_canvas()
        self._refresh_inspectors()

    def _refresh_live(self) -> None:
        """Canvas + layers refresh, but the property panel is left intact.

        Use this for value edits fired from a slider/spinbox the user is
        actively dragging: rebuilding the panel destroys the focused widget
        mid-drag, so the QR size slider stops after one tick and the label-size
        spinbox refuses to step. The edited widget already shows its own value;
        only the canvas (and the layers/float-bar state) needs to catch up.
        """
        self._tmpl = normalize_template(self._tmpl)
        self._refresh_canvas()
        self._refresh_layers()

    def _refresh_canvas(self) -> None:
        """Refresh only the WYSIWYG surface; safe for high-frequency dragging."""
        self._canvas.set_content(self._tmpl, self._dims, self._data)
        kind, row, field = self._sel
        self._canvas.set_selection(kind, row, field)
        self._canvas.set_multi(getattr(self, "_multi", set()))

    def _refresh_inspectors(self) -> None:
        """Rebuild lower-frequency property and layer inspectors."""
        kind, row, field = self._sel
        self._panel._dims = self._dims
        self._panel.show_for(kind, row, field, self._tmpl)
        self._refresh_layers()

    def _refresh_layers(self) -> None:
        """Update the layers panel + sync auxiliary toolbar state (no panel rebuild)."""
        kind, _row, field = self._sel
        layers = getattr(self, "_layers", None)
        if layers is not None:
            layers.set_elements(self._tmpl.get("elements") or [],
                                sel_index=field if kind == "element" else -1)
        self._sync_delete_action()
        self._sync_float_bar()

    def _select(self, kind: str, row: int, field: int) -> None:
        if (kind, row, field) == self._sel and not self._multi \
                and self._fmt_pending is None:
            return
        self._sel = (kind, row, field)
        self._multi = set()            # a plain click resets the group selection
        # selecting a grouped element pulls in its whole group (move together)
        if kind == "element" and field >= 0:
            peers = self._group_peers(field)
            if len(peers) > 1:
                self._multi = set(peers)
        self._canvas.set_selection(kind, row, field)
        self._canvas.set_multi(self._multi)
        self._panel._dims = self._dims
        self._panel.show_for(kind, row, field, self._tmpl)
        self._sync_delete_action()
        self._sync_float_bar()
        # format painter: first element click captures the source style; the
        # next applies it to the target, then disarms the tool.
        if self._fmt_pending is not None and kind == "element" and field >= 0:
            if self._fmt_pending:
                style = self._fmt_pending
                self._fmt_pending = None
                self._fmt_btn.setChecked(False)
                self._apply_edit({"op": "element_apply_style", "index": field,
                                  "style_dict": style})
            else:
                self._fmt_pending = self._capture_style(field)

    # ── Floating quick-format toolbar (Phase 2) ───────────────────────────────
    def _sync_float_bar(self) -> None:
        bar = getattr(self, "_float_bar", None)
        if bar is None:
            return
        kind, _row, field = self._sel
        el = self._element_at(field) if kind == "element" else None
        if el is not None and el.get("type") in ("text", "field") and not self._multi:
            bar.show_for(field, self._canvas.element_screen_rect(field))
        else:
            bar.hide_bar()

    def _float_size_delta(self, d: int) -> None:
        i = self._float_bar.target_index()
        el = self._element_at(i)
        if el is None:
            return
        cur = float(el.get("size") or 9)
        self._apply_edit({"op": "element_size", "index": i, "value": max(4.0, cur + d)})

    def _float_style(self, flag: str) -> None:
        i = self._float_bar.target_index()
        el = self._element_at(i)
        if el is None:
            return
        on = flag not in (el.get("style") or "")
        self._apply_edit({"op": f"element_{flag}", "index": i, "value": on})

    def _float_align(self, a: str) -> None:
        i = self._float_bar.target_index()
        if self._element_at(i) is not None:
            self._apply_edit({"op": "element_align", "index": i, "value": a})

    def _float_color(self) -> None:
        i = self._float_bar.target_index()
        el = self._element_at(i)
        if el is None:
            return
        col = QColorDialog.getColor(QColor(el.get("color") or "#111111"), self, "文字颜色")
        if col.isValid():
            self._apply_edit({"op": "element_color", "index": i, "value": col.name()})

    def _float_z(self, d: int) -> None:
        i = self._float_bar.target_index()
        if self._element_at(i) is not None:
            self._apply_edit({"op": "element_z", "index": i, "value": d})

    # ── Multi-selection (Phase 1) ─────────────────────────────────────────────
    def _toggle_multi(self, index: int) -> None:
        """Ctrl-click: add/remove an element index from the group selection."""
        els = self._elements()
        if not (0 <= index < len(els)):
            return
        self._multi ^= {index}
        if index in self._multi:
            self._sel = ("element", -1, index)
        elif self._multi:
            self._sel = ("element", -1, min(self._multi))
        else:
            self._sel = ("none", -1, -1)
        self._canvas.set_selection(*self._sel)
        self._canvas.set_multi(self._multi)
        self._panel.show_for(*self._sel, self._tmpl)
        self._sync_delete_action()

    def _marquee_select(self, x: float, y: float, w: float, h: float) -> None:
        """Box-select every element whose bounding box intersects the rect."""
        rx2, ry2 = x + w, y + h
        hit = set()
        for i, el in enumerate(self._elements()):
            bx, by, bw, bh = self._el_bbox(el)
            if bx < rx2 and bx + bw > x and by < ry2 and by + bh > y:
                hit.add(i)
        self._multi = hit
        anchor = min(hit) if hit else -1
        self._sel = ("element", -1, anchor) if anchor >= 0 else ("none", -1, -1)
        self._canvas.set_selection(*self._sel)
        self._canvas.set_multi(self._multi)
        self._panel.show_for(*self._sel, self._tmpl)
        self._sync_delete_action()

    # ── In-place text editing (Phase 2) ───────────────────────────────────────
    def _begin_inline_edit(self, index: int) -> None:
        """Open a QLineEdit over a text element to edit its text in place."""
        el = self._element_at(index)
        if el is None or el.get("type") != "text":
            return
        self._commit_inline_edit()   # close any prior editor first
        rect = self._canvas.element_screen_rect(index)
        editor = QLineEdit(self._canvas)
        editor.setText(str(el.get("text") or ""))
        if rect is not None:
            editor.setGeometry(rect.adjusted(-1, -1, 1, 1))
        editor.setStyleSheet(
            "QLineEdit { background:#fffbe6; color:#111; border:1px solid #29b9ab; }")
        editor.returnPressed.connect(self._commit_inline_edit)
        editor.editingFinished.connect(self._commit_inline_edit)
        self._inline_editor = editor
        self._inline_index = index
        editor.show()
        editor.setFocus()
        editor.selectAll()

    def _commit_inline_edit(self) -> None:
        editor = self._inline_editor
        if editor is None:
            return
        self._inline_editor = None     # guard re-entrancy from editingFinished
        idx = self._inline_index
        self._inline_index = -1
        el = self._element_at(idx)
        text = editor.text()
        editor.deleteLater()
        if el is not None and el.get("type") == "text" and text != el.get("text"):
            self._save_template_snapshot_for_undo()
            el["text"] = text
            self._sel = ("element", -1, idx)
            self._refresh_designer_state()

    def _copy_selection(self) -> None:
        """Copy the selected elements (deep) into the designer clipboard."""
        els = self._elements()
        idx = self._selected_element_indices()
        self._clipboard = [copy.deepcopy(els[i]) for i in idx if 0 <= i < len(els)]

    def _paste_clipboard(self) -> None:
        """Paste clipboard elements offset by +2mm; select them as the group."""
        if not self._clipboard:
            return
        self._save_template_snapshot_for_undo()
        els = self._elements()
        new_idx = set()
        for src in self._clipboard:
            el = copy.deepcopy(src)
            if el.get("type") == "line":
                for k in ("x1", "y1", "x2", "y2"):
                    el[k] = round(float(el.get(k) or 0) + 2.0, 2)
            else:
                el["x"] = round(float(el.get("x") or 0) + 2.0, 2)
                el["y"] = round(float(el.get("y") or 0) + 2.0, 2)
            els.append(el)
            new_idx.add(len(els) - 1)
        self._multi = new_idx
        self._sel = ("element", -1, min(new_idx))
        self._refresh_designer_state()

    def _delete_selection(self) -> None:
        """Delete the current field or selected free-form element(s)."""
        kind, row, field = self._sel
        if kind == "field":
            rows = self._tmpl.get("rows") or []
            if 0 <= row < len(rows) and 0 <= field < len(rows[row].get("fields") or []):
                self._apply_edit({"op": "field_del", "row": row, "field": field})
            return
        idx = set(self._selected_element_indices())
        if not idx:
            return
        self._save_template_snapshot_for_undo()
        els = self._elements()
        self._tmpl["elements"] = [el for i, el in enumerate(els) if i not in idx]
        self._multi = set()
        self._sel = ("none", -1, -1)
        self._refresh_designer_state()

    def _sync_delete_action(self) -> None:
        """Make the global delete command explicit about its current target."""
        button = getattr(self, "_delete_btn", None)
        if button is None:
            return
        kind, _row, _field = self._sel
        if kind == "field":
            text, enabled = "删除字段", True
        elif kind == "element":
            count = len(self._selected_element_indices())
            text, enabled = (f"删除元素 ({count})" if count > 1 else "删除元素"), count > 0
        else:
            text, enabled = "删除所选", False
        button.setText(text)
        button.setEnabled(enabled)

    # ── Presets / guides ──────────────────────────────────────────────────────
    def _apply_preset(self, preset: dict) -> None:
        self._save_template_snapshot_for_undo()
        self._tmpl = normalize_template(copy.deepcopy(preset))
        self._sel = ("none", -1, -1)
        self._refresh_designer_state()

    def _toggle_guide_overlay(self, on: bool) -> None:
        self._canvas.set_guide_overlay(on)

    def edited_dims(self) -> dict:
        """The (possibly designer-edited) label dimensions in mm."""
        return dict(self._dims)

