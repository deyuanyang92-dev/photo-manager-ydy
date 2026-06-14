"""label_print.py — Qt print adapter shared by the label studio and the
workbench one-click direct print.

Two helpers, both Qt-using (kept out of the pure ``label_core``):

  - :func:`build_printer` — configure a ``QPrinter`` for a job's paper. Mirrors
    the printer setup in ``labels_view._print``.
  - :func:`paint_jobs` — paint one *or more* jobs onto a single ``QPrinter``.
    Multiple jobs are separated by exactly one page break, so a sample-bottle
    job + an RNAlater-tube job print together under one print dialog. Per-item
    page geometry comes from :func:`label_core.plan_label_pages`, so the output
    stays byte-identical to the old single-bucket paint loop.

The actual pixel rendering is delegated to :func:`label_render.render_label_onto`
— the same renderer that drives the on-screen preview, guaranteeing WYSIWYG.
"""

from __future__ import annotations

from typing import Callable, Optional

from PyQt6.QtCore import QMarginsF, QSizeF
from PyQt6.QtGui import QPageLayout, QPageSize, QPainter
from PyQt6.QtPrintSupport import QPrinter

from app.utils.label_core import plan_label_pages
from app.utils.label_render import render_label_onto


def build_printer(job: dict, grid_opts: Optional[dict] = None) -> QPrinter:
    """Return a ``QPrinter`` configured for *job*'s paper.

    A4/A5 → the standard page size with zero margins (the grid carries its own
    margin); custom label paper → an exact W×H mm page with a 2 mm safe margin.
    Mirrors ``labels_view._print`` lines for printer setup.

    *grid_opts* (or ``job["gridOpts"]`` when omitted) may carry
    ``orientation: "landscape"`` — 排版设计 prints the A4/A5 sheet sideways.
    """
    dims = job.get("dims") or {}
    w_mm = float(dims.get("w", 60))
    h_mm = float(dims.get("h", 40))
    paper_type = job.get("paperType") or "label"
    opts = grid_opts if grid_opts is not None else (job.get("gridOpts") or {})

    printer = QPrinter(QPrinter.PrinterMode.HighResolution)
    if paper_type in ("a4", "a5"):
        std = QPageSize.PageSizeId.A4 if paper_type == "a4" else QPageSize.PageSizeId.A5
        printer.setPageSize(QPageSize(std))
        printer.setPageMargins(QMarginsF(0, 0, 0, 0), QPageLayout.Unit.Millimeter)
        if opts.get("orientation") == "landscape":
            printer.setPageOrientation(QPageLayout.Orientation.Landscape)
    else:
        page_size = QPageSize(QSizeF(w_mm, h_mm), QPageSize.Unit.Millimeter, "Custom")
        printer.setPageSize(page_size)
        printer.setPageMargins(QMarginsF(2, 2, 2, 2), QPageLayout.Unit.Millimeter)
    return printer


def paint_jobs(
    printer: QPrinter,
    jobs,
    grid_opts: Optional[dict] = None,
    cut_marks: bool = False,
    draw_crop_marks: Optional[Callable] = None,
) -> bool:
    """Paint *jobs* (each a print-job dict) onto *printer* in one paint session.

    Jobs with no items are dropped. Jobs are painted in order, separated by a
    single ``newPage()`` seam. Within a job, page/slot geometry is taken from
    :func:`plan_label_pages`; blank items advance the page (label paper) or keep
    their slot (grid) but are not drawn — matching ``_paint_labels`` exactly.

    Returns ``False`` when there was nothing to paint (or the painter failed to
    start), ``True`` otherwise.
    """
    jobs = [j for j in jobs if j and (j.get("items"))]
    if not jobs:
        return False

    painter = QPainter()
    if not painter.begin(printer):
        return False

    dpi = printer.resolution()
    mm_to_dot = dpi / 25.4

    first_job = True
    for job in jobs:
        items = job.get("items") or []
        dims = job.get("dims") or {}
        tmpl = job.get("template") or {}
        paper_type = job.get("paperType") or "label"
        paper = job.get("paper")
        w_mm = float(dims.get("w", 60))
        h_mm = float(dims.get("h", 40))
        is_grid = paper_type in ("a4", "a5")

        # per-job 排版设计 opts win over the shared param (一键双打 buckets
        # may carry different impositions); legacy callers pass neither.
        job_opts = job.get("gridOpts") or grid_opts
        placements = plan_label_pages(items, dims, paper_type, paper, job_opts)

        if not first_job:
            printer.newPage()
        first_job = False

        cur_page = 0
        for p in placements:
            if p["page"] > cur_page:
                printer.newPage()
                cur_page = p["page"]
            data = p["data"]
            if not data:
                continue
            sc = p.get("scale", 1.0)
            x_off = int(p["x_mm"] * mm_to_dot)
            y_off = int(p["y_mm"] * mm_to_dot)
            render_label_onto(
                painter, tmpl, dims, data,
                px_per_mm=mm_to_dot * sc, x_off=float(x_off), y_off=float(y_off),
                placeholder=False, fill_bg=True,
            )
            if is_grid and cut_marks and draw_crop_marks is not None:
                draw_crop_marks(
                    painter, x_off, y_off,
                    int(w_mm * sc * mm_to_dot), int(h_mm * sc * mm_to_dot),
                    arm=int(2 * mm_to_dot), gap=int(0.5 * mm_to_dot))

    painter.end()
    return True
