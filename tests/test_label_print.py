"""tests/test_label_print.py — Qt print adapter (build_printer / paint_jobs).

These cover the shared print core used by both the label studio's print buttons
and the workbench one-click direct print:

  - build_printer(): paper-size / page-id setup per job.
  - paint_jobs(): multi-job painting onto a single QPrinter, with exactly one
    page-break seam BETWEEN jobs (so "样品瓶 + 组织管" prints in one dialog),
    and per-item page geometry identical to labels_view._paint_labels.

Page counts are verified by rendering to a temp PDF and counting pages with
pypdf — i.e. the real QPrinter paint path, not a mock.
"""

from __future__ import annotations

import os
import tempfile

import pytest

pytest.importorskip("pypdf")
import pypdf

from PyQt6.QtPrintSupport import QPrinter
from PyQt6.QtGui import QPageSize

from app.utils.label_print import build_printer, paint_jobs
from app.services.label_service import BUILTIN_TEMPLATES, PAPER_SIZES


def _job(paper_type: str, n: int, *, dims=None, blanks=()):
    items = [
        {"idx": i, "data": ({} if i in blanks else {"uniqueId": f"U{i}"})}
        for i in range(n)
    ]
    return {
        "items": items,
        "template": BUILTIN_TEMPLATES["standard"],
        "dims": dims or {"w": 50, "h": 30},
        "paperType": paper_type,
        "paper": PAPER_SIZES.get(paper_type) if paper_type in ("a4", "a5") else None,
    }


def _pdf_page_count(jobs, **kw) -> int:
    fd, path = tempfile.mkstemp(suffix=".pdf")
    os.close(fd)
    try:
        printer = build_printer(jobs[0])
        printer.setOutputFormat(QPrinter.OutputFormat.PdfFormat)
        printer.setOutputFileName(path)
        ok = paint_jobs(printer, jobs, **kw)
        if not ok:
            return 0
        return len(pypdf.PdfReader(path).pages)
    finally:
        if os.path.exists(path):
            os.remove(path)


class TestBuildPrinter:
    def test_label_paper_custom_size(self, qapp):
        printer = build_printer(_job("label", 1, dims={"w": 50, "h": 30}))
        sz = printer.pageLayout().pageSize().size(QPageSize.Unit.Millimeter)
        assert round(sz.width()) == 50 and round(sz.height()) == 30

    def test_a4_paper_uses_a4_id(self, qapp):
        printer = build_printer(_job("a4", 1))
        assert printer.pageLayout().pageSize().id() == QPageSize.PageSizeId.A4

    def test_a5_paper_uses_a5_id(self, qapp):
        printer = build_printer(_job("a5", 1))
        assert printer.pageLayout().pageSize().id() == QPageSize.PageSizeId.A5


class TestPaintJobs:
    def test_single_label_job_one_page_per_item(self, qapp):
        assert _pdf_page_count([_job("label", 3)]) == 3

    def test_two_label_jobs_one_seam(self, qapp):
        # job0: 2 pages, seam newPage, job1: 1 page → 3 total.
        assert _pdf_page_count([_job("label", 2), _job("label", 1)]) == 3

    def test_blank_item_still_consumes_label_page(self, qapp):
        assert _pdf_page_count([_job("label", 3, blanks=(1,))]) == 3

    def test_a4_grid_overflow_paginates(self, qapp):
        from app.utils.label_core import calculate_grid
        per_page = calculate_grid(50, 30, 210, 297)["perPage"]
        assert _pdf_page_count([_job("a4", per_page + 1)]) == 2

    def test_empty_jobs_filtered_out(self, qapp):
        empty = {"items": [], "template": BUILTIN_TEMPLATES["standard"],
                 "dims": {"w": 50, "h": 30}, "paperType": "label", "paper": None}
        assert _pdf_page_count([empty]) == 0

    def test_mixed_label_then_grid(self, qapp):
        # label job (2 pages) + a4 grid job (1 page) + seam → 3 pages.
        assert _pdf_page_count([_job("label", 2), _job("a4", 2)]) == 3


class TestImpositionExtensions:
    """排版设计: orientation / per-job gridOpts / shrink scale at print time."""

    def test_build_printer_one_arg_unchanged(self, qapp):
        from PyQt6.QtGui import QPageLayout
        printer = build_printer(_job("a4", 1))
        assert printer.pageLayout().orientation() == QPageLayout.Orientation.Portrait

    def test_build_printer_landscape_sets_orientation(self, qapp):
        from PyQt6.QtGui import QPageLayout
        printer = build_printer(_job("a4", 1), {"orientation": "landscape"})
        assert printer.pageLayout().orientation() == QPageLayout.Orientation.Landscape

    def test_build_printer_landscape_from_job_grid_opts(self, qapp):
        from PyQt6.QtGui import QPageLayout
        job = _job("a4", 1)
        job["gridOpts"] = {"orientation": "landscape"}
        printer = build_printer(job)
        assert printer.pageLayout().orientation() == QPageLayout.Orientation.Landscape

    def test_paint_jobs_prefers_job_grid_opts(self, qapp):
        # 1×1 forced grid via job["gridOpts"] → one label per page.
        job = _job("a4", 3)
        job["gridOpts"] = {"forceCols": 1, "forceRows": 1}
        assert _pdf_page_count([job]) == 3

    def test_paint_jobs_start_slot_paginates(self, qapp):
        from app.utils.label_core import calculate_grid
        per_page = calculate_grid(50, 30, 210, 297)["perPage"]
        job = _job("a4", per_page)        # exactly one page without offset
        job["gridOpts"] = {"startSlot": 1}
        assert _pdf_page_count([job]) == 2

    def test_paint_jobs_scale_shrinks_crop_marks(self, qapp):
        # recording fake: crop marks must receive scaled label w/h
        received = []

        def fake_marks(painter, x, y, w, h, arm=0, gap=0):
            received.append((w, h))

        job = _job("a4", 2)
        job["gridOpts"] = {"forceCols": 5, "shrinkToFit": True}
        fd, path = tempfile.mkstemp(suffix=".pdf")
        os.close(fd)
        try:
            printer = build_printer(job)
            printer.setOutputFormat(QPrinter.OutputFormat.PdfFormat)
            printer.setOutputFileName(path)
            paint_jobs(printer, [job], cut_marks=True, draw_crop_marks=fake_marks)
        finally:
            os.remove(path)
        mm_to_dot = printer.resolution() / 25.4
        sc = 194 / 258
        assert received
        assert all(w == int(50 * sc * mm_to_dot) and h == int(30 * sc * mm_to_dot)
                   for w, h in received)
