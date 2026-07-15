"""tests/test_collab_preview_endpoint.py — 预览端点端到端(协作流式 spec 阶段 2).

Claude Code 2026-07-15 — GET /api/collab/files/preview 走完整链路: 项目码门控 ->
file_path_fn 解析 -> build_file_preview 生成有界 JPEG -> 返回 image/jpeg 字节。
用 httpx ASGITransport(loopback 过 LAN 门), 同既有 test_collab_api 的模式。
"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PyQt6")

from PyQt6.QtGui import QColor, QImage


def _big_jpg(path, w=2000, h=1500):
    img = QImage(w, h, QImage.Format.Format_RGB32)
    img.fill(QColor("#2266aa"))
    assert img.save(str(path), "JPEG", 92)
    return str(path)


def _app(tmp_path):
    from app.services.collab_service import TaskStore, _build_fastapi_app
    img = _big_jpg(tmp_path / "photo.jpg")

    def _file_path_fn(rel: str) -> Path:
        # 简化: rel 就是文件名, 落在 tmp_path 下
        return tmp_path / rel

    return _build_fastapi_app(
        TaskStore(),
        lambda: {"groupCode": "G1", "projectId": "P1", "serverTime": 0},
        file_path_fn=_file_path_fn,
    ), os.path.basename(img)


def _get(app, path, params):
    import httpx

    async def go():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            return await c.get(path, params=params)

    return asyncio.run(go())


def test_preview_endpoint_returns_bounded_jpeg(tmp_path):
    app, name = _app(tmp_path)
    resp = _get(app, "/api/collab/files/preview", {
        "path": name, "groupCode": "G1", "projectId": "P1", "maxDim": 400,
    })
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/jpeg"
    data = resp.content
    assert data[:3] == b"\xff\xd8\xff"  # JPEG magic
    img = QImage()
    assert img.loadFromData(data)
    assert max(img.width(), img.height()) <= 400


def test_preview_endpoint_rejects_wrong_project(tmp_path):
    app, name = _app(tmp_path)
    resp = _get(app, "/api/collab/files/preview", {
        "path": name, "groupCode": "G1", "projectId": "WRONG", "maxDim": 400,
    })
    # 项目码不匹配 -> 拒绝(隔离红线, 同 download 端点)
    assert resp.status_code in (403, 400)


def test_preview_endpoint_missing_file_404(tmp_path):
    app, _name = _app(tmp_path)
    resp = _get(app, "/api/collab/files/preview", {
        "path": "nope.jpg", "groupCode": "G1", "projectId": "P1", "maxDim": 400,
    })
    assert resp.status_code == 404
