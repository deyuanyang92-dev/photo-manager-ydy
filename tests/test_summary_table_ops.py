"""tests/test_summary_table_ops.py"""
from __future__ import annotations

from app.utils import summary_table_ops as sto


def _cell(row, key):
    return str(row.get(key) or "").strip()


def test_sort_specimen_rows_numeric_iso() -> None:
    rows = [
        {"uid": "a", "iso": "400"},
        {"uid": "b", "iso": "100"},
        {"uid": "c", "iso": "200"},
    ]
    out = sto.sort_specimen_rows(rows, "iso", cell_value=_cell)
    assert [r["uid"] for r in out] == ["b", "c", "a"]


def test_apply_column_filters_keeps_selected_values() -> None:
    rows = [
        {"uid": "a", "station": "B1"},
        {"uid": "b", "station": "B2"},
        {"uid": "c", "station": "B1"},
    ]
    filtered = sto.apply_column_filters(
        rows,
        {"station": {"B1"}},
        cell_value=_cell,
    )
    assert [r["uid"] for r in filtered] == ["a", "c"]


def test_unique_column_values_sorted() -> None:
    rows = [
        {"station": "B2"},
        {"station": "B1"},
        {"station": "B2"},
        {"station": ""},
    ]
    vals = sto.unique_column_values(rows, "station", cell_value=_cell)
    assert vals == ["B1", "B2", ""]
