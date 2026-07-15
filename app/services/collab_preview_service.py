"""collab_preview_service.py — 服务端按分辨率生成图像预览(协作流式加载 阶段 1).

Claude Code 2026-07-15 — 协作分辨率可选流式加载 spec 阶段 1:
serving peer 收到"给我这张图 @ maxDim=1600"时, 现生成一张有界的预览 JPEG 字节传回,
而不是把整份原图 / 母 TIF(几百MB)传过去。这是"浏览对方工作区照片不卡"的核心块 ——
先流预览、想要原图/TIF 再按需取。

复用 image_thumbnail 的解码(支持 JPG 和 TIF), 只读, 绝不改原文件; TIF 母片不碰,
预览是另生成的 JPEG。纯函数, offscreen 可测, 不碰网络(端点在阶段 2)。
"""
from __future__ import annotations

import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

# 预览质量/尺寸的合理边界(防滥用: 请求 maxDim 过大就是要原图, 走 download 端点)
_MIN_DIM = 16
_MAX_DIM = 4096
_MIN_Q = 20
_MAX_Q = 95


def build_file_preview(
    file_path: str,
    max_dim: int = 1600,
    quality: int = 85,
) -> Optional[bytes]:
    """把 *file_path* 解码并缩到长边 ≤ max_dim, 编码成 JPEG 字节返回。

    · 支持 JPG / TIF(复用 image_thumbnail._decode_image)。
    · ZIP / 非图像 / 文件不存在 / 解码失败 -> None。
    · 绝不放大: 原图比 max_dim 小就保持原尺寸(_decode_image 的 max_size 语义)。
    · 只读: 不写、不改原文件。
    """
    if not file_path:
        return None
    try:
        if not os.path.isfile(file_path):
            return None
    except OSError:
        return None
    # 非图像扩展名(zip 等)直接不生成预览
    ext = os.path.splitext(file_path)[1].lower()
    if ext not in (".jpg", ".jpeg", ".tif", ".tiff", ".png", ".bmp"):
        return None

    md = max(_MIN_DIM, min(_MAX_DIM, int(max_dim)))
    q = max(_MIN_Q, min(_MAX_Q, int(quality)))

    # Claude Code 修改 2026-07-15 — 绝不放大: _decode_image 对小图 + 大 max_size 会
    # 放大到 fit。先用 QImageReader 只读文件头拿原生尺寸(便宜, 大 TIF 也不用解码),
    # 把 md 夹到原生长边, 保证预览不超过原图。读头失败(某些 TIF 变体)则按 md 原样
    # (真实预览请求 md 一般远小于原图, 不会触发放大)。
    try:
        from PyQt6.QtGui import QImageReader
        reader = QImageReader(file_path)
        native = reader.size()
        native_long = max(native.width(), native.height())
        if native_long > 0:
            md = min(md, native_long)
    except Exception:  # noqa: BLE001
        pass

    try:
        from app.utils.image_thumbnail import _decode_image
        # use_cache=False: 预览尺寸是调用方指定的任意值, 不该污染本地缩略图缓存
        img = _decode_image(file_path, max_size=md, use_cache=False)
    except Exception as exc:  # noqa: BLE001
        logger.debug("collab preview: decode failed %s: %s", file_path, exc)
        return None
    if img is None or img.isNull():
        return None

    # 保险: _decode_image 的 max_size 已按长边缩放, 但个别解码路径可能不缩,
    # 这里再兜一层, 确保长边不超 md(绝不放大)。
    try:
        from app.utils.image_thumbnail import scale_preview_image
        if max(img.width(), img.height()) > md:
            img = scale_preview_image(img, md)
    except Exception:  # noqa: BLE001
        pass

    try:
        from PyQt6.QtCore import QBuffer, QByteArray, QIODevice
        buf = QBuffer()
        buf.open(QIODevice.OpenModeFlag.WriteOnly)
        ok = img.save(buf, "JPEG", q)
        buf.close()
        if not ok:
            return None
        data: QByteArray = buf.data()
        return bytes(data)
    except Exception as exc:  # noqa: BLE001
        logger.debug("collab preview: encode failed %s: %s", file_path, exc)
        return None
