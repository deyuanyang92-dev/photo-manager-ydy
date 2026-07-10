"""tests/test_tiff_jpeg_tool_view.py"""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from app.app_context import AppContext
from app.views.tiff_jpeg_tool_view import TiffJpegToolView


@pytest.fixture(scope="module", autouse=True)
def qapp():
    return QApplication.instance() or QApplication([])


def test_tiff_jpeg_tool_view_smoke() -> None:
    ctx = AppContext()
    view = TiffJpegToolView(ctx)
    view.on_activate()
    assert view.view_id == "tiff_jpeg_tool"
    assert view._btn_start is not None


def test_load_tiff_sources_applies_archive_preset(tmp_path) -> None:
    from PIL import Image

    ctx = AppContext()
    view = TiffJpegToolView(ctx)
    tif_a = tmp_path / "a.tif"
    tif_b = tmp_path / "b.tif"
    Image.new("RGB", (32, 24), "#aabbcc").save(str(tif_a), format="TIFF")
    Image.new("RGB", (24, 32), "#ccbbaa").save(str(tif_b), format="TIFF")

    n = view.load_tiff_sources([str(tif_a), str(tif_b)], preset_id="archive")
    assert n == 2
    assert view._current_preset_id() == "archive"
    assert view._quality_slider.value() == 95
    assert view._edge_unlimited.isChecked()
    assert view._subsampling.currentIndex() == 0


def test_on_activate_consumes_pending_sources(tmp_path) -> None:
    from PIL import Image

    ctx = AppContext()
    tif = tmp_path / "handoff.tif"
    Image.new("RGB", (16, 16), "#112233").save(str(tif), format="TIFF")
    ctx.pending_tiff_jpeg_sources = [str(tif)]
    ctx.pending_tiff_jpeg_preset_id = "archive"

    view = TiffJpegToolView(ctx)
    view.on_activate()

    assert getattr(ctx, "pending_tiff_jpeg_sources", "unset") is None
    assert view._sources == [str(tif.resolve())]
