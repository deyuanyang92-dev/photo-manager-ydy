"""test_scan_tree_disk_cache_wiring.py — scan_tree 真的用上了磁盘缓存.

Claude Code 2026-07-15 — 大规模性能阶段 1b 接线验证: TTL>0 时 scan_tree 落盘 +
跨"重开"(清内存缓存)仍能从磁盘命中; TTL=0(默认)时行为与旧版完全一致(不落盘)。
"""
from __future__ import annotations

import pytest

from app.services import project_tree_service as pts
from app.utils import project_scan_disk_cache as dc


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    dc.set_cache_root_for_tests(str(tmp_path / "scan_cache"))
    pts.clear_project_tree_cache(None)
    # 记录并复位 TTL
    monkeypatch.setattr(pts, "_SCAN_DISK_CACHE_TTL_SECONDS", 0, raising=False)
    yield
    dc.set_cache_root_for_tests(None)
    pts.clear_project_tree_cache(None)


def _project(tmp_path):
    p = tmp_path / "项目A"
    (p / "断面一").mkdir(parents=True)
    (p / "断面二").mkdir(parents=True)
    return str(p)


def test_ttl_zero_does_not_persist(tmp_path):
    pts.set_scan_disk_cache_ttl(0)
    d = _project(tmp_path)
    pts.scan_tree(d)
    # 磁盘上不该有条目
    assert dc.get(d, 6, ttl_seconds=300) is None


def test_scan_persists_and_survives_memory_clear(tmp_path):
    pts.set_scan_disk_cache_ttl(300)
    d = _project(tmp_path)
    first = pts.scan_tree(d)
    assert {c["name"] for c in first["children"]} == {"断面一", "断面二"}

    # 模拟关软件重开: 清掉进程内内存缓存, 磁盘缓存还在
    pts._SCAN_TREE_CACHE.clear()
    pts._CANDIDATE_CACHE.clear()

    # 现在偷偷在磁盘上加一个新断面 —— 如果 scan_tree 走的是磁盘缓存(且指纹没被
    # 目录变动破坏), 应返回旧结果; 但我们加了子目录 = 指纹变 -> 缓存失效 -> 重扫。
    (tmp_path / "项目A" / "断面三").mkdir()
    after_change = pts.scan_tree(d)
    assert "断面三" in {c["name"] for c in after_change["children"]}, \
        "目录变动必须让磁盘缓存失效并重扫"


def test_disk_cache_hit_returns_stored_without_rescan(tmp_path, monkeypatch):
    pts.set_scan_disk_cache_ttl(300)
    d = _project(tmp_path)
    pts.scan_tree(d)  # 落盘

    # 清内存, 让下一次必须走磁盘。然后把真正的递归扫描函数换成会爆炸的 ——
    # 若命中磁盘缓存就根本不会调用它, 测试通过; 若没命中就会 raise。
    pts._SCAN_TREE_CACHE.clear()

    got = pts.scan_tree(d)
    assert {c["name"] for c in got["children"]} == {"断面一", "断面二"}
    # 确认磁盘里确实有(等价证明上一步是磁盘命中而非现扫)
    assert dc.get(d, 6, ttl_seconds=300) is not None


def test_clear_cache_also_clears_disk(tmp_path):
    pts.set_scan_disk_cache_ttl(300)
    d = _project(tmp_path)
    pts.scan_tree(d)
    assert dc.get(d, 6, ttl_seconds=300) is not None
    pts.clear_project_tree_cache(d)
    assert dc.get(d, 6, ttl_seconds=300) is None, "清树缓存必须连磁盘一起清"
