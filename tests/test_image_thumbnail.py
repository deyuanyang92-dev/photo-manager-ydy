from __future__ import annotations

import os
import struct
import sys

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtGui import QColor, QImage, QPixmap
from PyQt6.QtWidgets import QApplication


@pytest.fixture(scope="module", autouse=True)
def qapp():
    return QApplication.instance() or QApplication([])


def _write_image(path, color="#336699") -> None:
    img = QImage(24, 16, QImage.Format.Format_RGB32)
    img.fill(QColor(color))
    assert img.save(str(path))


def test_decode_image_thumbnail_uses_stat_cache(monkeypatch, tmp_path) -> None:
    from app.utils import image_thumbnail

    path = tmp_path / "thumb.jpg"
    _write_image(path)
    image_thumbnail.clear_thumbnail_cache()

    first = image_thumbnail.decode_image_thumbnail(str(path), max_size=32)
    assert first is not None and not first.isNull()

    def fail_backend(*_args, **_kwargs):
        raise AssertionError("backend should not be called on cache hit")

    monkeypatch.setattr(image_thumbnail, "_decode_with_qt", fail_backend)
    monkeypatch.setattr(image_thumbnail, "_decode_with_pillow", fail_backend)
    monkeypatch.setattr(image_thumbnail, "_decode_with_tifffile", fail_backend)
    monkeypatch.setattr(image_thumbnail, "_decode_with_imagemagick", fail_backend)

    cached = image_thumbnail.decode_image_thumbnail(str(path), max_size=32)
    assert cached is not None and not cached.isNull()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows path localization")
def test_decode_image_thumbnail_localizes_wsl_paths(tmp_path) -> None:
    from app.utils import image_thumbnail
    from app.utils.path_utils import windows_to_wsl

    path = tmp_path / "thumb.jpg"
    _write_image(path)
    wsl_path = windows_to_wsl(str(path))
    assert wsl_path

    decoded = image_thumbnail.decode_image_thumbnail(wsl_path, max_size=32, use_cache=False)

    assert decoded is not None
    assert not decoded.isNull()


def test_clear_thumbnail_cache_forces_decode(monkeypatch, tmp_path) -> None:
    from app.utils import image_thumbnail

    path = tmp_path / "thumb.jpg"
    _write_image(path)
    image_thumbnail.clear_thumbnail_cache()
    assert image_thumbnail.decode_image_thumbnail(str(path), max_size=32) is not None

    image_thumbnail.clear_thumbnail_cache(str(path))
    calls = {"qt": 0}

    def miss_qt(*_args, **_kwargs):
        calls["qt"] += 1
        return None

    monkeypatch.setattr(image_thumbnail, "_decode_with_qt", miss_qt)
    monkeypatch.setattr(image_thumbnail, "_decode_with_pillow", lambda *_a, **_k: None)
    monkeypatch.setattr(image_thumbnail, "_decode_with_tifffile", lambda *_a, **_k: None)
    monkeypatch.setattr(image_thumbnail, "_decode_with_imagemagick", lambda *_a, **_k: None)

    assert image_thumbnail.decode_image_thumbnail(str(path), max_size=32) is None
    assert calls["qt"] == 1


def test_malformed_lzw_tiff_fallback_decodes_strips(tmp_path) -> None:
    imagecodecs = pytest.importorskip("imagecodecs")
    from app.utils import image_thumbnail

    width, height, samples = 2, 2, 3
    original = [
        [10, 20, 30, 11, 22, 33],
        [40, 50, 60, 42, 53, 64],
    ]
    predicted = bytes(
        original[0][:3]
        + [original[0][3] - original[0][0], original[0][4] - original[0][1], original[0][5] - original[0][2]]
        + original[1][:3]
        + [original[1][3] - original[1][0], original[1][4] - original[1][1], original[1][5] - original[1][2]]
    )
    strip = imagecodecs.lzw_encode(predicted)
    strip_offset = 8
    ifd_offset = strip_offset + len(strip)
    bits_offset = ifd_offset + 2 + 10 * 12 + 4

    def entry(tag, typ, count, value):
        return struct.pack("<HHII", tag, typ, count, value)

    entries = [
        entry(256, 4, 1, width),
        entry(257, 4, 1, height),
        entry(258, 3, 3, bits_offset),
        entry(259, 3, 1, 5),
        entry(262, 3, 1, 2),
        entry(273, 4, 1, strip_offset),
        entry(277, 3, 1, samples),
        entry(278, 4, 1, height),
        entry(279, 4, 1, len(strip)),
        entry(317, 3, 1, 2),
    ]
    data = (
        b"II*\x00"
        + struct.pack("<I", ifd_offset)
        + strip
        + struct.pack("<H", len(entries))
        + b"".join(entries)
        + struct.pack("<I", 0)
        + struct.pack("<HHH", 8, 8, 8)
    )
    path = tmp_path / "legacy-lzw.tif"
    path.write_bytes(data)

    pixmap = image_thumbnail._decode_malformed_lzw_tiff(str(path), 32)

    assert pixmap is not None
    assert not pixmap.isNull()

    decoded = image_thumbnail.decode_image_thumbnail(str(path), max_size=32, use_cache=False)
    assert decoded is not None
    assert not decoded.isNull()


# ---------------------------------------------------------------------------
# Spec §5 refactor (2026-07-08): thread-safe QImage path + main-thread pixmap bridge
# ---------------------------------------------------------------------------


def test_decode_image_data_returns_qimage(tmp_path) -> None:
    from app.utils import image_thumbnail

    path = tmp_path / "di.jpg"
    _write_image(path)
    image_thumbnail.clear_thumbnail_cache()

    img = image_thumbnail.decode_image_data(str(path), max_size=32, use_cache=False)
    assert img is not None
    assert isinstance(img, QImage), "decode_image_data must return a QImage (thread-safe)"
    assert not img.isNull()
    from app.utils.thumbnail_disk_cache import normalize_thumb_cache_size

    bucket = normalize_thumb_cache_size(32)
    assert img.width() <= bucket and img.height() <= bucket


def test_decode_image_data_uses_qimage_cache(monkeypatch, tmp_path) -> None:
    from app.utils import image_thumbnail

    path = tmp_path / "dc.jpg"
    _write_image(path)
    image_thumbnail.clear_thumbnail_cache()

    first = image_thumbnail.decode_image_data(str(path), max_size=32)
    assert first is not None and not first.isNull()
    assert isinstance(first, QImage)

    def fail(*_a, **_k):
        raise AssertionError("backend should not run on cache hit")

    monkeypatch.setattr(image_thumbnail, "_decode_with_qt", fail)
    monkeypatch.setattr(image_thumbnail, "_decode_with_pillow", fail)
    monkeypatch.setattr(image_thumbnail, "_decode_with_tifffile", fail)
    monkeypatch.setattr(image_thumbnail, "_decode_with_imagemagick", fail)

    cached = image_thumbnail.decode_image_data(str(path), max_size=32)
    assert cached is not None and not cached.isNull()
    assert isinstance(cached, QImage)


def test_decode_image_data_none_for_missing_path(tmp_path) -> None:
    from app.utils import image_thumbnail

    image_thumbnail.clear_thumbnail_cache()
    assert image_thumbnail.decode_image_data(str(tmp_path / "absent.jpg"), max_size=32) is None


def test_make_pixmap_converts_qimage(tmp_path) -> None:
    from app.utils import image_thumbnail

    path = tmp_path / "mp.jpg"
    _write_image(path)
    image_thumbnail.clear_thumbnail_cache()
    img = image_thumbnail.decode_image_data(str(path), max_size=32, use_cache=False)
    assert img is not None
    pm = image_thumbnail.make_pixmap(img)
    assert pm is not None
    assert isinstance(pm, QPixmap)
    assert not pm.isNull()


def test_make_pixmap_none_for_none() -> None:
    from app.utils import image_thumbnail

    assert image_thumbnail.make_pixmap(None) is None


def test_decode_image_thumbnail_back_compat_returns_pixmap(tmp_path) -> None:
    """Legacy callers (project_tree_view, monitor_panel) keep getting QPixmap."""
    from app.utils import image_thumbnail

    path = tmp_path / "bc.jpg"
    _write_image(path)
    image_thumbnail.clear_thumbnail_cache()
    pm = image_thumbnail.decode_image_thumbnail(str(path), max_size=32, use_cache=False)
    assert pm is not None
    assert isinstance(pm, QPixmap)
    assert not pm.isNull()


def test_decode_image_data_cache_returns_independent_handle(tmp_path) -> None:
    """Repeated lookups return distinct QImage handles with identical pixels.

    The cache stores a deep copy; returned handles may be mutated without
    poisoning future reads (critical for worker/main-thread concurrency).
    """
    from app.utils import image_thumbnail

    path = tmp_path / "ic.jpg"
    _write_image(path, "#336699")
    image_thumbnail.clear_thumbnail_cache()

    first = image_thumbnail.decode_image_data(str(path), max_size=32)
    assert first is not None
    cx, cy = first.width() // 2, first.height() // 2
    original_color = first.pixelColor(cx, cy)

    again = image_thumbnail.decode_image_data(str(path), max_size=32)
    assert again is not None and not again.isNull()
    assert again is not first  # distinct handles (cache returns a copy)
    assert again.pixelColor(cx, cy) == original_color  # same pixels


def test_cache_stored_qimage_is_independent_of_worker_mutation(tmp_path) -> None:
    """Mutating a returned handle must not corrupt the cache entry."""
    from app.utils import image_thumbnail

    path = tmp_path / "ic2.jpg"
    _write_image(path, "#55aaaa")
    image_thumbnail.clear_thumbnail_cache()

    first = image_thumbnail.decode_image_data(str(path), max_size=32)
    assert first is not None
    cx, cy = first.width() // 2, first.height() // 2
    original_color = first.pixelColor(cx, cy)

    # Mutate our handle — copy-on-write detaches it from the cache buffer.
    first.fill(0)

    again = image_thumbnail.decode_image_data(str(path), max_size=32)
    assert again is not None
    assert again.pixelColor(cx, cy) == original_color, (
        "cache entry was corrupted by mutating a previously-returned handle"
    )
