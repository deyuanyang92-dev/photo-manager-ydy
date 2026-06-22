from pathlib import Path

from PyQt6.QtGui import QImage

from app.services.label_service import BUILTIN_TEMPLATES, PAPER_SIZES
from app.utils.windows_print import render_jobs_to_pages


def _job(paper_type: str, count: int) -> dict:
    return {
        "items": [
            {"data": {"uniqueId": f"TEST-{index + 1:03d}"}}
            for index in range(count)
        ],
        "template": BUILTIN_TEMPLATES["standard"],
        "dims": {"w": 50, "h": 30},
        "paperType": paper_type,
        "paper": PAPER_SIZES.get(paper_type),
    }


def test_label_roll_renders_one_lossless_page_per_label(qapp, tmp_path):
    pages = render_jobs_to_pages([_job("label", 2)], Path(tmp_path))
    assert len(pages) == 2
    assert [(p["width_mm"], p["height_mm"]) for p in pages] == [(50.0, 30.0)] * 2
    assert all(QImage(p["path"]).width() == round(50 * 300 / 25.4) for p in pages)


def test_a4_job_renders_physical_a4_page(qapp, tmp_path):
    pages = render_jobs_to_pages([_job("a4", 2)], Path(tmp_path))
    assert len(pages) == 1
    assert pages[0]["width_mm"] == 210
    assert pages[0]["height_mm"] == 297
    assert Path(pages[0]["path"]).suffix == ".png"
