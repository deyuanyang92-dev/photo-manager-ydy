"""test_project_scan_disk_cache.py — 项目扫描磁盘缓存: 命中/未命中/失效/TTL/关闭.

Claude Code 2026-07-15 — 大规模性能阶段 1b.
"""
from __future__ import annotations

import time

import pytest

from app.utils import project_scan_disk_cache as dc


@pytest.fixture(autouse=True)
def _isolate_cache_root(tmp_path):
    dc.set_cache_root_for_tests(str(tmp_path / "scan_cache"))
    yield
    dc.set_cache_root_for_tests(None)


def _project(tmp_path, name="proj"):
    p = tmp_path / name
    (p / "断面一").mkdir(parents=True)
    (p / "断面二").mkdir(parents=True)
    return str(p)


def test_miss_when_nothing_stored(tmp_path):
    assert dc.get(_project(tmp_path), 6, ttl_seconds=300) is None


def test_put_then_hit(tmp_path):
    d = _project(tmp_path)
    value = {"path": d, "children": [{"name": "断面一"}, {"name": "断面二"}]}
    dc.put(d, 6, value)
    got = dc.get(d, 6, ttl_seconds=300)
    assert got == value


def test_ttl_zero_never_hits(tmp_path):
    d = _project(tmp_path)
    dc.put(d, 6, {"x": 1})
    assert dc.get(d, 6, ttl_seconds=0) is None


def test_expired_entry_is_a_miss(tmp_path, monkeypatch):
    d = _project(tmp_path)
    dc.put(d, 6, {"x": 1})
    # 把存盘时间往回拨到很久以前
    real_time = time.time
    monkeypatch.setattr(time, "time", lambda: real_time() + 10_000)
    assert dc.get(d, 6, ttl_seconds=300) is None


def test_dir_change_invalidates(tmp_path):
    d = _project(tmp_path)
    dc.put(d, 6, {"x": 1})
    assert dc.get(d, 6, ttl_seconds=300) == {"x": 1}
    # 新增一个可见子目录 -> 指纹变 -> 之前的缓存必须失效
    from pathlib import Path
    (Path(d) / "断面三").mkdir()
    assert dc.get(d, 6, ttl_seconds=300) is None


def test_different_depth_is_separate_entry(tmp_path):
    d = _project(tmp_path)
    dc.put(d, 6, {"depth": 6})
    dc.put(d, 2, {"depth": 2})
    assert dc.get(d, 6, ttl_seconds=300) == {"depth": 6}
    assert dc.get(d, 2, ttl_seconds=300) == {"depth": 2}


def test_survives_a_fresh_module_state(tmp_path):
    """落盘缓存必须跨"重新读取"存活(模拟关软件重开: 只要根目录不变, 磁盘上还在)。"""
    d = _project(tmp_path)
    dc.put(d, 6, {"persisted": True})
    # 不清任何内存态 —— 直接再 get, 走的是磁盘读取
    assert dc.get(d, 6, ttl_seconds=300) == {"persisted": True}


def test_clear_all(tmp_path):
    d = _project(tmp_path)
    dc.put(d, 6, {"x": 1})
    dc.clear()
    assert dc.get(d, 6, ttl_seconds=300) is None


def test_clear_one_dir(tmp_path):
    d1 = _project(tmp_path, "p1")
    d2 = _project(tmp_path, "p2")
    dc.put(d1, 6, {"a": 1})
    dc.put(d2, 6, {"b": 2})
    dc.clear(d1)
    assert dc.get(d1, 6, ttl_seconds=300) is None
    assert dc.get(d2, 6, ttl_seconds=300) == {"b": 2}
