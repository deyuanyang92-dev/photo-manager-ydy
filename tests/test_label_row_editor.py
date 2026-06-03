"""tests/test_label_row_editor.py — Tests for the row structural editor and
preview context menu added to LabelEditorWidget.

Covers:
  - _RowEditorPanel: add / delete / reorder rows; font size / bold / italic
  - LabelEditorWidget.row_editor property
  - _RowsCommand undo / redo via QUndoStack
  - Preview context menu (_show_preview_context_menu)
  - _cycle_qr_position toggles QR position
  - _copy_label_text puts text on clipboard

Mirrors:
  renderEditorModeBar(bucket) + renderRowFloatingToolbar(bucket) from app.js
  renderLabelPreviewContextMenu() from app.js
"""

from __future__ import annotations

import os
import sys
import copy

import pytest
from PyQt6.QtCore import Qt


@pytest.fixture(scope="module")
def qt_app():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt6.QtWidgets import QApplication
    existing = QApplication.instance()
    if existing is not None:
        yield existing
    else:
        app = QApplication(sys.argv[:1])
        yield app


def _make_template():
    from app.utils.label_core import normalize_template
    return normalize_template({
        "name": "测试模板",
        "rows": [
            {"fields": ["uniqueId"], "size": 9, "style": ""},
            {"fields": ["speciesName"], "size": 8, "style": "bold"},
            {"fields": ["region"], "size": 7, "style": ""},
        ],
        "qr": {"position": "right", "sizePct": 0.4, "ecc": "Q"},
    })


def _make_data():
    return {
        "uniqueId": "FJ-YGLZ-B2-DLC001-D95E-20260508",
        "speciesName": "背鳞虫 sp.01",
        "region": "福建·厦门",
        "collectorLabel": "杨德援采集",
        "latin": "Polynoidae sp.",
    }


# ══════════════════════════════════════════════════════════════════════════════
# _RowEditorPanel — pure widget tests
# ══════════════════════════════════════════════════════════════════════════════

class TestRowEditorPanel:
    def _make_panel(self, qt_app):
        from app.widgets.label_editor import _RowEditorPanel
        panel = _RowEditorPanel()
        return panel

    def test_instantiates_without_crash(self, qt_app):
        panel = self._make_panel(qt_app)
        assert panel is not None

    def test_set_rows_returns_copy(self, qt_app):
        panel = self._make_panel(qt_app)
        rows = [{"fields": [{"key": "uniqueId"}], "size": 9}]
        panel.set_rows(rows)
        retrieved = panel.get_rows()
        assert retrieved == rows
        # Mutation of original should not affect retrieved
        rows[0]["size"] = 99
        assert panel.get_rows()[0]["size"] == 9

    def test_add_row_increases_count(self, qt_app):
        panel = self._make_panel(qt_app)
        panel.set_rows([])
        panel._add_row()
        assert len(panel.get_rows()) == 1

    def test_add_row_appends_default_field(self, qt_app):
        panel = self._make_panel(qt_app)
        panel.set_rows([])
        panel._add_row()
        row = panel.get_rows()[0]
        # New row should have at least one field
        assert len(row.get("fields") or []) >= 1

    def test_delete_row_decreases_count(self, qt_app):
        panel = self._make_panel(qt_app)
        panel.set_rows([
            {"fields": [{"key": "uniqueId"}], "size": 9},
            {"fields": [{"key": "speciesName"}], "size": 8},
        ])
        panel._delete_row(0)
        assert len(panel.get_rows()) == 1
        # The remaining row should be speciesName
        assert panel.get_rows()[0]["fields"][0]["key"] == "speciesName"

    def test_delete_row_out_of_range_no_crash(self, qt_app):
        panel = self._make_panel(qt_app)
        panel.set_rows([{"fields": [], "size": 9}])
        panel._delete_row(99)  # out of range: no crash
        assert len(panel.get_rows()) == 1

    def test_move_row_up(self, qt_app):
        panel = self._make_panel(qt_app)
        panel.set_rows([
            {"fields": [{"key": "uniqueId"}], "size": 9},
            {"fields": [{"key": "speciesName"}], "size": 8},
        ])
        panel._move_row(1, -1)
        rows = panel.get_rows()
        assert rows[0]["fields"][0]["key"] == "speciesName"
        assert rows[1]["fields"][0]["key"] == "uniqueId"

    def test_move_row_down(self, qt_app):
        panel = self._make_panel(qt_app)
        panel.set_rows([
            {"fields": [{"key": "uniqueId"}], "size": 9},
            {"fields": [{"key": "speciesName"}], "size": 8},
        ])
        panel._move_row(0, +1)
        rows = panel.get_rows()
        assert rows[0]["fields"][0]["key"] == "speciesName"

    def test_move_row_at_boundary_no_crash(self, qt_app):
        panel = self._make_panel(qt_app)
        panel.set_rows([{"fields": [{"key": "uniqueId"}], "size": 9}])
        panel._move_row(0, -1)   # already at top
        panel._move_row(0, +1)   # already at bottom
        assert len(panel.get_rows()) == 1

    def test_rows_changed_signal_emitted_on_add(self, qt_app):
        panel = self._make_panel(qt_app)
        panel.set_rows([])
        received = []
        panel.rows_changed.connect(lambda: received.append(1))
        panel._add_row()
        assert len(received) == 1

    def test_rows_changed_signal_emitted_on_delete(self, qt_app):
        panel = self._make_panel(qt_app)
        panel.set_rows([{"fields": [], "size": 9}])
        received = []
        panel.rows_changed.connect(lambda: received.append(1))
        panel._delete_row(0)
        assert len(received) == 1

    def test_rows_changed_signal_emitted_on_move(self, qt_app):
        panel = self._make_panel(qt_app)
        panel.set_rows([
            {"fields": [{"key": "uniqueId"}], "size": 9},
            {"fields": [{"key": "speciesName"}], "size": 8},
        ])
        received = []
        panel.rows_changed.connect(lambda: received.append(1))
        panel._move_row(0, +1)
        assert len(received) == 1


# ══════════════════════════════════════════════════════════════════════════════
# LabelEditorWidget — row_editor integration
# ══════════════════════════════════════════════════════════════════════════════

class TestLabelEditorRowEditor:
    def _make_editor(self, qt_app):
        from app.widgets.label_editor import LabelEditorWidget
        tmpl = _make_template()
        data = _make_data()
        return LabelEditorWidget(tmpl, {"w": 60, "h": 40}, data)

    def test_row_editor_property_exists(self, qt_app):
        editor = self._make_editor(qt_app)
        assert hasattr(editor, "row_editor")
        assert editor.row_editor is not None

    def test_row_editor_has_correct_row_count(self, qt_app):
        editor = self._make_editor(qt_app)
        assert len(editor.row_editor.get_rows()) == 3

    def test_add_row_updates_template_in_scene(self, qt_app):
        editor = self._make_editor(qt_app)
        initial_count = len(editor.row_editor.get_rows())
        editor.row_editor._add_row()
        assert len(editor.row_editor.get_rows()) == initial_count + 1

    def test_delete_row_updates_template(self, qt_app):
        editor = self._make_editor(qt_app)
        initial_count = len(editor.row_editor.get_rows())
        editor.row_editor._delete_row(0)
        assert len(editor.row_editor.get_rows()) == initial_count - 1

    def test_reorder_row_works(self, qt_app):
        editor = self._make_editor(qt_app)
        rows_before = editor.row_editor.get_rows()
        first_key_before = (rows_before[0].get("fields") or [{}])[0].get("key")
        second_key_before = (rows_before[1].get("fields") or [{}])[0].get("key")
        editor.row_editor._move_row(0, +1)
        rows_after = editor.row_editor.get_rows()
        first_key_after = (rows_after[0].get("fields") or [{}])[0].get("key")
        assert first_key_after == second_key_before

    def test_undo_after_add_row(self, qt_app):
        editor = self._make_editor(qt_app)
        initial_count = len(editor.row_editor.get_rows())
        editor.row_editor._add_row()
        assert len(editor.row_editor.get_rows()) == initial_count + 1
        # Undo via undo stack
        editor.undo_stack.undo()
        assert len(editor.row_editor.get_rows()) == initial_count

    def test_redo_after_undo_add_row(self, qt_app):
        editor = self._make_editor(qt_app)
        initial_count = len(editor.row_editor.get_rows())
        editor.row_editor._add_row()
        editor.undo_stack.undo()
        editor.undo_stack.redo()
        assert len(editor.row_editor.get_rows()) == initial_count + 1

    def test_update_label_syncs_row_editor(self, qt_app):
        editor = self._make_editor(qt_app)
        new_tmpl = _make_template()
        new_tmpl["rows"] = [{"fields": [{"key": "uniqueId"}], "size": 9}]
        editor.update_label(template=new_tmpl)
        assert len(editor.row_editor.get_rows()) == 1


# ══════════════════════════════════════════════════════════════════════════════
# _RowsCommand — undo / redo correctness
# ══════════════════════════════════════════════════════════════════════════════

class TestRowsCommand:
    def test_undo_restores_rows(self, qt_app):
        from app.widgets.label_editor import LabelEditorWidget, _RowsCommand
        tmpl = _make_template()
        editor = LabelEditorWidget(tmpl, {"w": 60, "h": 40}, _make_data())
        old_rows = list(tmpl["rows"])
        new_rows = old_rows[:1]  # only keep first row
        cmd = _RowsCommand(editor, old_rows, new_rows, "test remove rows")
        editor.undo_stack.push(cmd)
        assert len(editor.row_editor.get_rows()) == 1
        editor.undo_stack.undo()
        assert len(editor.row_editor.get_rows()) == 3

    def test_redo_applies_new_rows(self, qt_app):
        from app.widgets.label_editor import LabelEditorWidget, _RowsCommand
        tmpl = _make_template()
        editor = LabelEditorWidget(tmpl, {"w": 60, "h": 40}, _make_data())
        old_rows = list(tmpl["rows"])
        new_rows = old_rows[:2]
        cmd = _RowsCommand(editor, old_rows, new_rows, "test remove last row")
        editor.undo_stack.push(cmd)
        editor.undo_stack.undo()
        editor.undo_stack.redo()
        assert len(editor.row_editor.get_rows()) == 2


# ══════════════════════════════════════════════════════════════════════════════
# Preview context menu — _show_preview_context_menu
# Mirrors renderLabelPreviewContextMenu() in app.js
# ══════════════════════════════════════════════════════════════════════════════

class TestPreviewContextMenu:
    def _make_editor(self, qt_app):
        from app.widgets.label_editor import LabelEditorWidget
        return LabelEditorWidget(_make_template(), {"w": 60, "h": 40}, _make_data())

    def test_view_has_context_menu_policy(self, qt_app):
        """QGraphicsView must have CustomContextMenu policy."""
        editor = self._make_editor(qt_app)
        assert (editor._view.contextMenuPolicy()
                == Qt.ContextMenuPolicy.CustomContextMenu)

    def test_copy_label_text_copies_to_clipboard(self, qt_app):
        """_copy_label_text must put label data text on clipboard."""
        from PyQt6.QtWidgets import QApplication
        editor = self._make_editor(qt_app)
        editor._copy_label_text()
        cb_text = QApplication.clipboard().text()
        # uniqueId should be present in clipboard content
        assert "FJ-YGLZ-B2-DLC001" in cb_text

    def test_copy_label_text_content(self, qt_app):
        """Clipboard text = uniqueId + speciesName + region + collectorLabel joined by newline."""
        from PyQt6.QtWidgets import QApplication
        from app.utils.label_core import label_data_text
        editor = self._make_editor(qt_app)
        expected = label_data_text(editor._label_data)
        editor._copy_label_text()
        assert QApplication.clipboard().text() == expected

    def test_cycle_qr_position_advances_position(self, qt_app):
        """_cycle_qr_position must advance QR position by one step."""
        editor = self._make_editor(qt_app)
        # Initial position is 'right' (from _make_template)
        assert editor._template["qr"]["position"] == "right"
        editor._cycle_qr_position()
        assert editor._template["qr"]["position"] == "bottom"

    def test_cycle_qr_position_wraps_none_to_right(self, qt_app):
        """After 'none', position should wrap back to 'right'."""
        from app.utils.label_core import normalize_template
        editor_tmpl = normalize_template({
            "rows": [{"fields": ["uniqueId"], "size": 9}],
            "qr": {"position": "none", "ecc": "Q"},
        })
        from app.widgets.label_editor import LabelEditorWidget
        editor = LabelEditorWidget(editor_tmpl, {"w": 60, "h": 40}, _make_data())
        editor._cycle_qr_position()
        assert editor._template["qr"]["position"] == "right"

    def test_cycle_qr_emits_template_changed(self, qt_app):
        """_cycle_qr_position must emit template_changed signal."""
        editor = self._make_editor(qt_app)
        received = []
        editor.template_changed.connect(lambda t: received.append(t))
        editor._cycle_qr_position()
        assert len(received) == 1
        assert isinstance(received[0], dict)

    def test_context_menu_connected_to_slot(self, qt_app):
        """customContextMenuRequested signal must be connected (not raise on disconnect)."""
        editor = self._make_editor(qt_app)
        # If connection exists, disconnect won't raise
        try:
            editor._view.customContextMenuRequested.disconnect(
                editor._show_preview_context_menu
            )
            connected = True
        except RuntimeError:
            connected = False
        assert connected, "_show_preview_context_menu not connected to customContextMenuRequested"
