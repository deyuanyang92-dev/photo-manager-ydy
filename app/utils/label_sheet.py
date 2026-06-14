"""label_sheet.py — shared 拼版 full-page sheet painter (Qt).

One geometry source for everything that shows or prints an A4/A5 sheet:

  - the labels-view inline thumbnail and the 排版预览 dialog,
  - the 排版设计 designer canvas (which also needs per-cell pixel rects for
    drag hit-testing),
  - and — indirectly — the printer, because every pixel position here comes
    from the same :func:`label_core.calculate_grid` / :func:`slot_origin_mm`
    math that :func:`label_core.plan_label_pages` feeds to ``paint_jobs``.

Cells are placed at true mm positions (margin/gap/scale rendered to scale),
unlike the old equal-cell-division approximation that lived in
``labels_view._paint_sheet``.
"""

from __future__ import annotations

from typing import Optional

from PyQt6.QtGui import QColor, QPainter, QPen

from app.utils.label_core import (
    calculate_grid,
    effective_page_mm,
    slot_origin_mm,
)
from app.utils.label_render import render_label_onto


def draw_crop_marks(painter: QPainter, x: int, y: int, w: int, h: int,
                    arm: int = 4, gap: int = 2) -> None:
    """Draw printer crop/cut marks at the four corners of a label box."""
    painter.save()
    painter.setPen(QPen(QColor("#111827"), 1))
    for cx, cy, sx, sy in (
        (x, y, -1, -1), (x + w, y, 1, -1),
        (x, y + h, -1, 1), (x + w, y + h, 1, 1),
    ):
        painter.drawLine(cx + sx * gap, cy, cx + sx * (gap + arm), cy)
        painter.drawLine(cx, cy + sy * gap, cx, cy + sy * (gap + arm))
    painter.restore()


def compute_sheet_geometry(
    dims: dict,
    paper_type: str,
    paper: Optional[dict],
    grid_opts: Optional[dict],
    avail_w: int,
    avail_h: int,
) -> dict:
    """Fit the (orientation-aware) page into an *avail_w*×*avail_h* canvas.

    Keeps the historical thumbnail insets (42 px horizontal / 48 px vertical
    breathing room, page centred, 16 px top) so the existing preview framing
    is unchanged. Returns page rect in px, the px-per-mm scale and the
    resolved grid.
    """
    opts = grid_opts or {}
    page_w_mm, page_h_mm = effective_page_mm(paper, paper_type, opts)
    usable_w = max(40.0, avail_w - 42)
    usable_h = max(40.0, avail_h - 48)
    aspect = page_w_mm / max(1.0, page_h_mm)
    page_w_px = min(usable_w, usable_h * aspect)
    page_h_px = page_w_px / aspect
    grid = calculate_grid(
        float(dims.get("w", 50)), float(dims.get("h", 30)),
        page_w_mm, page_h_mm, opts=opts)
    return {
        "page_x": (avail_w - page_w_px) / 2,
        "page_y": 16.0,
        "page_w_px": page_w_px,
        "page_h_px": page_h_px,
        "px_per_mm": page_w_px / max(1.0, page_w_mm),
        "page_w_mm": page_w_mm,
        "page_h_mm": page_h_mm,
        "grid": grid,
    }


def paint_sheet_page(
    painter: QPainter,
    job: dict,
    grid_opts: Optional[dict],
    page_index: int,
    geom: dict,
    *,
    cut_marks: bool = False,
    wysiwyg_limit: int = 48,
    demo_data: Optional[dict] = None,
) -> dict:
    """Paint one A4/A5 sheet page of *job* using *geom* from
    :func:`compute_sheet_geometry`.

    Semantics carried over from the old ``labels_view._paint_sheet`` grid
    branch: every slot is drawn — real slots show the labels actually printed
    on this page, surplus slots show a cycled "ghost" repeat (排版示意 only),
    a blank job falls back to *demo_data*; ≤ *wysiwyg_limit* slots per page
    render real label content, more fall back to coloured placeholder blocks.

    排版设计 additions: cells sit at true mm positions (margins, axis gaps and
    shrink scale visible to scale) and ``startSlot`` slots on the first page
    are drawn as skipped (残张续打 — already-used stickers).

    Returns ``{"total_pages", "per_page", "page_count", "wysiwyg", "start",
    "cells"}`` where ``cells`` is one px-rect dict per slot
    (``{"slot","x","y","w","h","real","skipped"}``) for designer hit-testing.
    """
    opts = grid_opts or {}
    items = job.get("items") or []
    dims = job.get("dims") or {"w": 50, "h": 30}
    tmpl = job.get("template") or {}
    is_circle = (tmpl.get("shape") or "rect").lower() == "circle"

    grid = geom["grid"]
    cols, per_page = grid["cols"], grid["perPage"]
    scale = grid["scale"]
    ppm = geom["px_per_mm"]
    page_x, page_y = geom["page_x"], geom["page_y"]
    start = max(0, int(opts.get("startSlot") or 0)) % per_page

    total_pages = max(1, -(-(len(items) + start) // per_page)) if items else 1
    page = max(0, min(int(page_index), total_pages - 1))
    slot_lo = start if page == 0 else 0          # first usable slot this page
    idx0 = max(0, page * per_page - start)       # first item index this page
    page_count = max(0, min(len(items) - idx0, per_page - slot_lo))

    cell_w = max(2.0, grid["labelW"] * scale * ppm)
    cell_h = max(2.0, grid["labelH"] * scale * ppm)
    wysiwyg = per_page <= wysiwyg_limit

    painter.setPen(QPen(QColor("#7b8794"), 1))
    painter.drawRect(int(page_x), int(page_y),
                     int(geom["page_w_px"]), int(geom["page_h_px"]))

    cells = []
    for slot in range(per_page):
        sx_mm, sy_mm = slot_origin_mm(grid, slot)
        x = int(page_x + sx_mm * ppm)
        y = int(page_y + sy_mm * ppm)
        rw, rh = int(cell_w), int(cell_h)
        skipped = slot < slot_lo
        item_idx = page * per_page + slot - start
        real = (not skipped) and 0 <= item_idx < len(items)
        cells.append({"slot": slot, "x": x, "y": y, "w": rw, "h": rh,
                      "real": real, "skipped": skipped})

        if skipped:
            # 残张续打：该格已被用掉，画浅灰+斜线示意，不画内容。
            painter.fillRect(x, y, rw, rh, QColor("#f1f5f9"))
            painter.setPen(QPen(QColor("#cbd5e1"), 1))
            painter.drawRect(x, y, rw, rh)
            painter.drawLine(x, y, x + rw, y + rh)
            painter.setPen(QPen(QColor("#7b8794"), 1))
            continue

        if wysiwyg:
            if page_count > 0:
                src = items[idx0 + ((slot - slot_lo) % page_count)]
                data = (src.get("data")
                        if isinstance(src, dict) and "data" in src else src)
            else:
                data = demo_data
            if job.get("_previewDemoWhenBlank") and not data:
                data = demo_data
            painter.save()
            painter.fillRect(x, y, rw, rh, QColor("#ffffff"))
            painter.setClipRect(x, y, rw, rh)
            render_label_onto(
                painter, tmpl, dims, data or {},
                px_per_mm=ppm * scale, x_off=float(x), y_off=float(y),
                placeholder=True, fill_bg=False,
            )
            painter.restore()
            painter.setPen(QPen(QColor("#7b8794"), 1))
        else:
            painter.fillRect(x, y, rw, rh,
                             QColor("#e8f4f2") if real else QColor("#f8fafc"))
        if is_circle:
            painter.drawEllipse(x, y, rw, rh)
        else:
            painter.drawRect(x, y, rw, rh)
        if cut_marks:
            draw_crop_marks(painter, x, y, rw, rh)

    return {
        "total_pages": total_pages,
        "per_page": per_page,
        "page_count": page_count,
        "wysiwyg": wysiwyg,
        "start": start,
        "cells": cells,
    }
