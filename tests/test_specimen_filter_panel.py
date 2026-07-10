"""Tests for the project-tree specimen filter panel."""
from __future__ import annotations

import pytest

pytest.importorskip("PyQt6")

from app.widgets.specimen_filter_panel import SpecimenFilterPanel


def test_filter_panel_in_operator_accumulates_dropdown_values(qtbot, monkeypatch):
    monkeypatch.setattr(
        "app.widgets.specimen_filter_panel.filter_svc.field_choices",
        lambda _workspaces, field: ["张三", "李四", "王五"] if field == "photographer" else [],
    )
    panel = SpecimenFilterPanel()
    qtbot.addWidget(panel)
    panel.set_workspaces(["/tmp/ws"])
    panel._add_condition_row(field="photographer", op="in")

    row = panel._cond_rows[0]
    value = row["value"]
    panel._on_value_activated(row, 0)
    panel._on_value_activated(row, 2)

    assert value.currentText() == "张三|王五"
    assert panel.conditions() == [
        {"field": "photographer", "op": "in", "value": "张三|王五"}
    ]


def test_filter_panel_date_quick_uses_between(qtbot):
    panel = SpecimenFilterPanel()
    qtbot.addWidget(panel)
    panel._add_quick_condition("collection_date")

    row = panel._cond_rows[0]
    assert row["op"].currentData() == "between"
    assert "202501-202601" in row["value"].placeholderText()


def test_filter_panel_keeps_empty_state_to_one_toolbar_row(qtbot):
    panel = SpecimenFilterPanel()
    qtbot.addWidget(panel)

    assert panel._cond_frame.isHidden()
    assert panel._btn_clear.isHidden()

    panel._add_condition_row(open_popup=False)
    assert panel._cond_frame.isHidden() is False
    assert panel._btn_clear.isHidden() is False

    panel._clear_conditions()
    assert panel._cond_frame.isHidden()
    assert panel._btn_clear.isHidden()
