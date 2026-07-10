"""tests/test_thumbnail_disk_cache.py"""
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


def _write_image(path, color="#336699") -> None:
    img = QImage(48, 32, QImage.Format.Format_RGB32)
    img.fill(QColor(color))
    assert img.save(str(path))


def _write_tiff(path, color="#336699") -> None:
    from PIL import Image

    Image.new("RGB", (48, 32), color).save(str(path), format="TIFF")


def test_disk_cache_survives_memory_clear(disk_cache_root, tmp_path) -> None:
    from app.utils import image_thumbnail as it
    from app.utils import thumbnail_disk_cache as tdc

    path = tmp_path / "a.jpg"
    _write_image(path)
    it.clear_thumbnail_cache()

    first = it.decode_image_data(str(path), max_size=96, use_cache=True)
    assert first is not None and not first.isNull()

    with it._THUMB_CACHE_LOCK:
        it._THUMB_CACHE.clear()
    second = it.try_cached_image_data(str(path), max_size=96)
    assert second is not None and not second.isNull()

    bucket = tdc.normalize_thumb_cache_size(96)
    stat = path.stat()
    digest = tdc.cache_entry_hash(
        str(path.resolve()), int(stat.st_mtime_ns), int(stat.st_size), bucket
    )
    jpg_path, _ = tdc._cache_paths(digest)
    assert jpg_path.is_file()


def test_ensure_tiff_preview_jpeg_creates_disk_proxy(disk_cache_root, tmp_path) -> None:
    from app.config.preview_profile import current_preview_master_size
    from app.utils import image_thumbnail as it

    path = tmp_path / "preview.tif"
    _write_tiff(path)
    it.clear_thumbnail_cache()

    jpg_path = it.ensure_tiff_preview_jpeg(str(path), max_size=96)
    assert jpg_path is not None
    assert Path(jpg_path).is_file()
    assert Path(jpg_path).suffix.lower() == ".jpg"

    second = it.tiff_preview_jpeg_path(str(path), max_size=96)
    assert second == jpg_path

    preview = it.tiff_to_preview_image(str(path), max_size=96, use_cache=True)
    assert preview is not None and not preview.isNull()
    assert max(preview.width(), preview.height()) <= 96

    with it._THUMB_CACHE_LOCK:
        it._THUMB_CACHE.clear()
    img = QImage(str(jpg_path))
    assert not img.isNull()
    assert max(img.width(), img.height()) <= current_preview_master_size()


def test_scale_preview_image_downscales() -> None:
    from app.utils.image_thumbnail import scale_preview_image

    img = QImage(200, 100, QImage.Format.Format_RGB32)
    img.fill(QColor("#112233"))
    out = scale_preview_image(img, 50)
    assert not out.isNull()
    assert max(out.width(), out.height()) <= 50
    assert out.width() == 50
    assert out.height() == 25


def test_scale_preview_image_no_upscale() -> None:
    from app.utils.image_thumbnail import scale_preview_image

    img = QImage(40, 30, QImage.Format.Format_RGB32)
    out = scale_preview_image(img, 256)
    assert out.width() == 40 and out.height() == 30


def test_try_cached_image_data_never_decodes(disk_cache_root, tmp_path, monkeypatch) -> None:
    from app.utils import image_thumbnail as it

    path = tmp_path / "b.jpg"
    _write_image(path, "#cc2222")
    it.clear_thumbnail_cache()
    assert it.decode_image_data(str(path), max_size=64, use_cache=True) is not None
    with it._THUMB_CACHE_LOCK:
        it._THUMB_CACHE.clear()

    monkeypatch.setattr(it, "_decode_with_qt", lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("decode")))
    monkeypatch.setattr(it, "_decode_with_pillow", lambda *_a, **_k: None)
    hit = it.try_cached_image_data(str(path), max_size=64)
    assert hit is not None and not hit.isNull()


def test_fail_sentinel_expires_after_ttl(disk_cache_root, tmp_path) -> None:
    """v0.56: .fail 哨兵须带 TTL——瞬时失败(文件被锁/OOM)不该永久毒化缓存键."""
    import time

    from app.utils import thumbnail_disk_cache as tdc

    src = tmp_path / "a.jpg"
    _write_image(src)

    tdc.mark_disk_thumbnail_failed(str(src), 256)
    stat = tdc._file_stat_key(str(src))
    assert stat is not None
    digest = tdc.cache_entry_hash(stat[0], stat[1], stat[2], 256)
    _jpg, fail = tdc._cache_paths(digest)
    assert fail.is_file(), "前置: 哨兵已写入"

    # 哨兵未过期: 仍然拦截
    assert tdc.read_disk_thumbnail(str(src), 256) is None
    assert fail.is_file()

    # 回拨 mtime 使其过期 → 读取路径应清除哨兵放行重试
    old = time.time() - tdc._FAIL_SENTINEL_TTL_S - 5
    os.utime(fail, (old, old))
    assert tdc.read_disk_thumbnail(str(src), 256) is None  # 尚无缩略图, 仍 None
    assert not fail.is_file(), "过期哨兵应被清除, 允许重试"


def test_part_tmp_name_is_pid_unique(disk_cache_root, tmp_path, monkeypatch) -> None:
    """v0.56: .part 临时名须含 pid——多实例(SPECIMEN_WORKBENCH_ALLOW_MULTI)
    同时写同一 digest 时不得互踩产生半截 JPEG."""
    from app.utils import thumbnail_disk_cache as tdc

    src = tmp_path / "b.jpg"
    _write_image(src)

    captured = {}
    real_replace = os.replace

    def cap(a, b):
        captured["tmp"] = str(a)
        real_replace(a, b)

    monkeypatch.setattr(tdc.os, "replace", cap)
    img = QImage(48, 32, QImage.Format.Format_RGB32)
    img.fill(QColor("#123456"))
    tdc.write_disk_thumbnail(str(src), 256, img)

    assert "tmp" in captured, "应有原子替换"
    assert f".{os.getpid()}." in captured["tmp"], "临时名须含 pid"
    assert tdc.read_disk_thumbnail(str(src), 256) is not None
