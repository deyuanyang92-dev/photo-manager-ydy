"""tests/test_collab_file_ondemand.py — 大文件(TIF)按需同步闸.

Claude Code 2026-07-15 — 协作流式加载 spec 阶段 4: TIF 动辄几百MB, 现在和 JPG 一视
同仁全量拉。加大小闸: 超阈值的文件(主要是 TIF)标 on_demand -> 默认"同步整个项目"
不自动拉它们, 用户显式要才拉。小文件(JPG/缩略)照常自动。红线: 只是"默认不自动",
不禁止 —— 显式 include_on_demand=True 仍能拉完整原文件。
"""
from __future__ import annotations

from app.services.collab_file_sync import (
    FileManifestEntry,
    LARGE_FILE_THRESHOLD_BYTES,
    plan_downloads,
)


def _entry(uid, rel, kind, size, sha="x"):
    return FileManifestEntry(uid=uid, relative_path=rel, kind=kind,
                             size_bytes=size, mtime=0.0, sha256=sha)


def test_large_tif_is_marked_on_demand():
    e = _entry("A", "results/A.tif", "tiff", LARGE_FILE_THRESHOLD_BYTES + 1)
    assert e.to_dict()["onDemand"] is True


def test_small_jpg_is_not_on_demand():
    e = _entry("A", "incoming/A.jpg", "jpg", 500_000)
    assert e.to_dict().get("onDemand") is False


def test_on_demand_roundtrips_through_dict():
    e = _entry("A", "results/A.tif", "tiff", LARGE_FILE_THRESHOLD_BYTES + 1)
    back = FileManifestEntry.from_dict(e.to_dict())
    assert back.is_on_demand is True


def test_plan_skips_on_demand_by_default():
    remote = [
        _entry("A", "incoming/A.jpg", "jpg", 400_000),
        _entry("A", "results/A.tif", "tiff", LARGE_FILE_THRESHOLD_BYTES + 10),
    ]
    planned = plan_downloads(remote, include_on_demand=False)
    rels = {e.relative_path for e in planned}
    assert "incoming/A.jpg" in rels, "小文件默认要同步"
    assert "results/A.tif" not in rels, "大 TIF 默认不自动拉(按需)"


def test_plan_includes_on_demand_when_explicitly_requested():
    remote = [
        _entry("A", "results/A.tif", "tiff", LARGE_FILE_THRESHOLD_BYTES + 10),
    ]
    planned = plan_downloads(remote, include_on_demand=True)
    assert {e.relative_path for e in planned} == {"results/A.tif"}, \
        "用户显式要 -> 大 TIF 也能拉完整原文件"
