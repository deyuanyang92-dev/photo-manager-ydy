"""tests/test_label_sheet.py — shared 拼版 sheet painter (app/utils/label_sheet.py).

The sheet painter is consumed by the labels-view thumbnail, the 排版预览
dialog AND the 排版设计 designer canvas; its geometry must come from the same
calculate_grid / slot_origin_mm math the printer uses (true mm, not the old
equal-cell division).
"""

from __future__ import annotations

import pytest

from PyQt6.QtGui import QColor, QPainter, QPixmap

from app.services.label_service import BUILTIN_TEMPLATES
from app.utils.label_core import calculate_grid


def _job(n: int, paper_type: str = "a4", dims=None):
    return {
        "items": [{"idx": i, "data": {"uniqueId": f"U{i}"}} for i in range(n)],
        "template": BUILTIN_TEMPLATES["standard"],
        "dims": dims or {"w": 50, "h": 30},
        "paperType": paper_type,
        "paper": {"w": 210, "h": 297} if paper_type == "a4" else None,
    }


def _paint(job, grid_opts, page=0, w=800, h=1000, **kw):
    from app.utils.label_sheet import compute_sheet_geometry, paint_sheet_page
    geom = compute_sheet_geometry(job["dims"], job["paperType"], job["paper"],
                                  grid_opts, w, h)
    pm = QPixmap(w, h)
    pm.fill(QColor("#ffffff"))
    p = QPainter(pm)
    try:
        info = paint_sheet_page(p, job, grid_opts, page, geom, **kw)
    finally:
        p.end()
    return pm, geom, info


class TestGeometry:
    def test_page_fits_rect_and_aspect(self, qapp):
        from app.utils.label_sheet import compute_sheet_geometry
        g = compute_sheet_geometry({"w": 50, "h": 30}, "a4",
                                   {"w": 210, "h": 297}, {}, 800, 1000)
        assert g["page_w_px"] <= 800 and g["page_h_px"] <= 1000
        assert g["page_h_px"] > g["page_w_px"]                      # portrait
        assert g["page_w_px"] / g["page_h_px"] == pytest.approx(210 / 297)
        assert g["px_per_mm"] == pytest.approx(g["page_w_px"] / 210)
        assert g["page_w_mm"] == 210 and g["page_h_mm"] == 297

    def test_landscape_swaps(self, qapp):
        from app.utils.label_sheet import compute_sheet_geometry
        g = compute_sheet_geometry({"w": 50, "h": 30}, "a4",
                                   {"w": 210, "h": 297},
                                   {"orientation": "landscape"}, 800, 1000)
        assert g["page_w_mm"] == 297 and g["page_h_mm"] == 210
        assert g["page_w_px"] > g["page_h_px"]
        # landscape grid fits 5 columns of 50mm labels
        assert g["grid"]["cols"] == 5


class TestPaintSheetPage:
    def test_cells_at_true_mm_positions(self, qapp):
        _, geom, info = _paint(_job(4), {})
        ppm = geom["px_per_mm"]
        c0 = info["cells"][0]
        assert c0["x"] == pytest.approx(geom["page_x"] + 8 * ppm, abs=1.0)
        assert c0["y"] == pytest.approx(geom["page_y"] + 8 * ppm, abs=1.0)
        assert c0["w"] == pytest.approx(50 * ppm, abs=1.0)
        c1 = info["cells"][1]
        assert c1["x"] == pytest.approx(geom["page_x"] + (8 + 52) * ppm, abs=1.0)

    def test_per_side_margin_moves_cells(self, qapp):
        opts = {"marginLeftMm": 20, "marginTopMm": 4}
        _, geom, info = _paint(_job(4), opts)
        ppm = geom["px_per_mm"]
        c0 = info["cells"][0]
        assert c0["x"] == pytest.approx(geom["page_x"] + 20 * ppm, abs=1.0)
        assert c0["y"] == pytest.approx(geom["page_y"] + 4 * ppm, abs=1.0)

    def test_start_slot_cells_flagged_skipped(self, qapp):
        _, _, info = _paint(_job(4), {"startSlot": 3})
        cells = info["cells"]
        assert all(cells[i]["skipped"] for i in range(3))
        assert not cells[3]["skipped"] and cells[3]["real"]
        assert not any(c["skipped"] for c in cells[3:])

    def test_total_pages_accounts_for_start_slot(self, qapp):
        per = calculate_grid(50, 30, 210, 297)["perPage"]
        _, _, plain = _paint(_job(per), {})
        assert plain["total_pages"] == 1
        _, _, shifted = _paint(_job(per), {"startSlot": 1})
        assert shifted["total_pages"] == 2
        # page 1 holds the spilled last item at slot 0
        _, _, page1 = _paint(_job(per), {"startSlot": 1}, page=1)
        assert page1["cells"][0]["real"]
        assert not page1["cells"][0]["skipped"]

    def test_scale_shrinks_cells(self, qapp):
        opts = {"forceCols": 5, "shrinkToFit": True}
        _, geom, info = _paint(_job(6), opts)
        ppm = geom["px_per_mm"]
        sc = 194 / 258
        assert geom["grid"]["scale"] == pytest.approx(sc)
        assert info["cells"][0]["w"] == pytest.approx(50 * sc * ppm, abs=1.0)

    def test_cut_marks_pixels_differ(self, qapp):
        pm1, _, _ = _paint(_job(4), {})
        pm2, _, _ = _paint(_job(4), {}, cut_marks=True)
        assert pm1.toImage() != pm2.toImage()

    def test_per_page_and_counts(self, qapp):
        _, _, info = _paint(_job(4), {})
        assert info["per_page"] == 24      # 3×8 for 50×30 on A4
        assert info["page_count"] == 4     # real cells on this page
        assert info["wysiwyg"] is True
        assert len(info["cells"]) == 24
