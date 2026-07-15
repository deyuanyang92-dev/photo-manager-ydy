"""tests/test_collab_preview_service.py — 服务端按分辨率生成预览(阶段 1).

Claude Code 2026-07-15 — 协作分辨率可选流式加载 spec 阶段 1 的承重原语:
给一个图像文件 + 请求的 maxDim/quality, 生成一张有界的预览 JPEG 字节(小), 而不是把
整份原图/母 TIF 传过去。这是"浏览对方工作区照片不卡"的核心块 —— 先流预览、想要原图
再按需取。纯函数, offscreen 可测, 不碰网络。

红线: 只读解码, 绝不改原文件; TIF 母片不动, 预览是另生成的 JPEG。
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PyQt6")

from PyQt6.QtGui import QColor, QImage

from app.services.collab_preview_service import build_file_preview


def _write_big_jpg(path, w=2000, h=1500):
    img = QImage(w, h, QImage.Format.Format_RGB32)
    img.fill(QColor("#2266aa"))
    assert img.save(str(path), "JPEG", 92)
    return str(path)


def _decode(data: bytes) -> QImage:
    img = QImage()
    assert img.loadFromData(data)
    return img


def test_preview_is_bounded_to_requested_maxdim(tmp_path):
    src = _write_big_jpg(tmp_path / "big.jpg")
    data = build_file_preview(src, max_dim=400, quality=85)
    assert data, "应返回预览 JPEG 字节"
    img = _decode(data)
    assert max(img.width(), img.height()) <= 400, "预览长边不得超过 maxDim"


def test_larger_maxdim_yields_larger_preview(tmp_path):
    src = _write_big_jpg(tmp_path / "big.jpg")
    small = build_file_preview(src, max_dim=200, quality=85)
    large = build_file_preview(src, max_dim=1200, quality=85)
    assert max(_decode(small).width(), _decode(small).height()) <= 200
    assert max(_decode(large).width(), _decode(large).height()) <= 1200
    # 更大的分辨率 -> 更大的字节体积(粗判, 同质量)
    assert len(large) > len(small)


def test_preview_never_upscales_beyond_original(tmp_path):
    src = _write_big_jpg(tmp_path / "small.jpg", w=300, h=200)
    data = build_file_preview(src, max_dim=4000, quality=85)
    img = _decode(data)
    # 原图才 300x200, maxDim=4000 不该放大到 4000
    assert img.width() <= 300 and img.height() <= 200


def test_quality_affects_size(tmp_path):
    src = _write_big_jpg(tmp_path / "big.jpg")
    lo = build_file_preview(src, max_dim=800, quality=40)
    hi = build_file_preview(src, max_dim=800, quality=95)
    assert len(hi) > len(lo), "高质量预览体积更大"


def test_original_file_is_never_modified(tmp_path):
    src = _write_big_jpg(tmp_path / "big.jpg")
    before = os.stat(src)
    build_file_preview(src, max_dim=400, quality=85)
    after = os.stat(src)
    assert (before.st_size, before.st_mtime_ns) == (after.st_size, after.st_mtime_ns), \
        "生成预览绝不能改原文件(只读红线)"


def test_missing_or_nonimage_returns_none(tmp_path):
    assert build_file_preview(str(tmp_path / "nope.jpg"), max_dim=400) is None
    # ZIP / 非图像 -> None(不是图像, 没有预览)
    zp = tmp_path / "a.zip"
    zp.write_bytes(b"PK\x03\x04not-a-real-zip")
    assert build_file_preview(str(zp), max_dim=400) is None


def test_output_is_jpeg(tmp_path):
    src = _write_big_jpg(tmp_path / "big.jpg")
    data = build_file_preview(src, max_dim=400, quality=85)
    # JPEG 魔数 FF D8 FF
    assert data[:3] == b"\xff\xd8\xff"
