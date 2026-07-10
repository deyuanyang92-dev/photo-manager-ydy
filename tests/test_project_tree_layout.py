"""tests/test_project_tree_layout.py"""
from __future__ import annotations

from app.config import project_tree_layout as ptl


def test_clamp_thumb_size():
    assert ptl.clamp_thumb_size(999) == ptl.THUMB_SIZE_MAX
    assert ptl.clamp_thumb_size(10) == ptl.THUMB_SIZE_MIN
    assert ptl.clamp_thumb_size(None) == ptl.DEFAULT_THUMB_SIZE


def test_density_slider_max_is_single_column():
    lo, hi = ptl.density_slider_range()
    assert ptl.columns_for_density_index(lo) == 32
    assert ptl.columns_for_density_index(hi) == 1
    assert ptl.density_label(1) == "1 张/行"


def test_quick_grid_buttons_map_to_exact_columns():
    for cols in ptl.GRID_QUICK_COLUMN_PRESETS:
        idx = ptl.density_index_for_columns(cols)
        assert ptl.columns_for_density_index(idx) == cols


def test_thumb_size_for_one_column_fills_viewport():
    vw = 640
    ts = ptl.thumb_size_for_columns(vw, 1)
    many = ptl.thumb_size_for_columns(vw, 8)
    assert ts > many
    assert ts >= 400


def test_auto_fit_uses_default_density():
    ts = ptl.auto_fit_thumb_for_viewport(900)
    expected = ptl.thumb_size_for_density(900, ptl.DEFAULT_GRID_DENSITY_INDEX)
    assert ts == expected


def test_sort_flat_by_uid_and_seq():
    items = [
        {"_uid": "Z-uid", "seq": 2, "name": "b.tif"},
        {"_uid": "A-uid", "seq": 10, "name": "a.tif"},
        {"_uid": "A-uid", "seq": 1, "name": "c.tif"},
        {"_uid": "", "seq": 1, "name": "u.tif"},
    ]
    out = ptl.sort_flat_grid_items(items, "uid_seq")
    assert [x["name"] for x in out] == ["c.tif", "a.tif", "b.tif", "u.tif"]


def test_sort_flat_by_filename():
    items = [{"name": "z.tif", "path": "/z"}, {"name": "a.tif", "path": "/a"}]
    out = ptl.sort_flat_grid_items(items, "filename")
    assert [x["name"] for x in out] == ["a.tif", "z.tif"]


def test_compute_split_sizes_respects_minimums():
    sizes = ptl.compute_split_sizes(1000)
    assert len(sizes) == 3
    mins = ptl.split_minimum_widths()
    assert sizes[0] >= mins[0]
    assert sizes[1] >= mins[1]
    assert sizes[2] >= mins[2]


def test_emphasize_grid_gives_middle_column_space():
    sizes = [300, 400, 300]
    out = ptl.emphasize_grid_split_sizes(sizes)
    assert sum(out) == sum(sizes)
    assert out[1] >= sizes[1]


def test_compute_summary_body_split_sizes():
    sizes = ptl.compute_summary_body_split_sizes(680)
    assert len(sizes) == 2
    assert sizes[0] >= ptl.SUMMARY_BODY_TABLE_MIN
    assert sizes[1] >= ptl.SUMMARY_BODY_PHOTO_MIN
    assert sum(sizes) == 680
