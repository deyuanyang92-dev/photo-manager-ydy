"""tests/test_tiff_preview_warmup_service.py — 数据汇总后台 TIFF 预览预热."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtGui import QColor, QImage
from PyQt6.QtWidgets import QApplication


@pytest.fixture(scope="module", autouse=True)
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def disk_cache_root(tmp_path, monkeypatch):
    from app.utils import thumbnail_disk_cache as tdc

    root = tmp_path / "thumb-cache"
    tdc.set_cache_root_for_tests(root)
    yield root
    tdc.set_cache_root_for_tests(None)


def _write_tiff(path: Path, color: str = "#336699") -> None:
    from PIL import Image

    Image.new("RGB", (64, 48), color).save(str(path), format="TIFF")


def test_default_preview_export_settings_is_archive_hd() -> None:
    from app.services import tiff_preview_warmup_service as tws

    cfg = tws.default_preview_export_settings()
    assert cfg.quality == 95
    assert cfg.subsampling == 0
    assert cfg.max_long_edge is None


def test_collect_tif_paths_dedupes_specimens_and_groups(tmp_path) -> None:
    from app.services import tiff_preview_warmup_service as tws

    tif_a = tmp_path / "a.tif"
    tif_b = tmp_path / "b.tif"
    _write_tiff(tif_a)
    _write_tiff(tif_b)
    abs_a = str(tif_a.resolve())
    abs_b = str(tif_b.resolve())

    specimens = [
        {
            "photo_absolute_path": abs_a,
            "result_tif_paths": f"{abs_a};{abs_b}",
        },
    ]
    groups = [
        {
            "uid": "U1",
            "items": [{"path": abs_a, "seq": 1}],
        },
    ]
    paths = tws.collect_tif_paths_from_summary(specimens, groups)
    assert paths == [abs_a, abs_b]


def test_warmup_creates_hd_preview_jpeg(disk_cache_root, tmp_path) -> None:
    from app.config import preview_profile as pp
    from app.services import tiff_preview_warmup_service as tws
    from app.utils import image_thumbnail as it

    pp.set_preview_master_size(720)
    path = tmp_path / "warmup.tif"
    _write_tiff(path)
    it.clear_thumbnail_cache()

    assert not tws.preview_cache_exists(str(path))
    result = tws.warmup_tif_paths([str(path)])
    assert result.total == 1
    assert result.created == 1
    assert result.failed == 0
    assert tws.preview_cache_exists(str(path))

    second = tws.warmup_tif_paths([str(path)])
    assert second.skipped == 1
    assert second.created == 0


def test_disk_thumbnail_uses_hd_quality(disk_cache_root, tmp_path, monkeypatch) -> None:
    from app.config import preview_profile as pp
    from app.utils import thumbnail_disk_cache as tdc

    assert pp.current_preview_jpeg_quality() == 95

    saved_quality: list[int] = []

    def _capture_save(self, filename, fmt, quality):  # noqa: ANN001
        saved_quality.append(int(quality))
        return QImage(8, 8, QImage.Format.Format_RGB32).fill(QColor("#aabbcc")) or True

    monkeypatch.setattr(QImage, "save", _capture_save, raising=False)

    img = QImage(32, 24, QImage.Format.Format_RGB32)
    img.fill(QColor("#112233"))
    jpg = tmp_path / "probe.jpg"
    _write_tiff(jpg.with_suffix(".tif"))
    tdc.write_disk_thumbnail(str(jpg.with_suffix(".tif")), 96, img)
    assert saved_quality == [95]
