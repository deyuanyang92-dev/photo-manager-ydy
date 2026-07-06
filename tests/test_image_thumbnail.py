from __future__ import annotations

import os
import struct
import sys

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtGui import QColor, QImage
from PyQt6.QtWidgets import QApplication


@pytest.fixture(scope="module", autouse=True)
def qapp():
    return QApplication.instance() or QApplication([])


def _write_image(path) -> None:
    img = QImage(24, 16, QImage.Format.Format_RGB32)
    img.fill(QColor("#336699"))
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
