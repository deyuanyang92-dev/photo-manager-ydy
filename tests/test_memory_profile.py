"""Tests for low-memory cache profile helpers."""
from __future__ import annotations

import app.config.memory_profile as mp


def test_apply_memory_profile_performance_mode_lowers_limits() -> None:
    mp.apply_memory_profile(performance_mode=True)
    assert mp.THUMB_CACHE_LIMIT == 192
    assert mp.MONITOR_THUMB_CACHE_LIMIT == 256
    assert mp.QPIXMAP_CACHE_KB == 4096
    mp.apply_memory_profile(performance_mode=False)
    assert mp.THUMB_CACHE_LIMIT == 384
    assert mp.MONITOR_THUMB_CACHE_LIMIT == 512
    assert mp.QPIXMAP_CACHE_KB == 8192


def test_clear_file_thumb_cache() -> None:
    from app.widgets.monitor_panel import _FILE_THUMB_CACHE, clear_file_thumb_cache

    _FILE_THUMB_CACHE[("x", 1, 1)] = None
    clear_file_thumb_cache()
    assert not _FILE_THUMB_CACHE


def test_ensure_collab_service_lazy() -> None:
    from app.app_context import AppContext

    ctx = AppContext()
    assert ctx.collab_service is None
    svc = ctx.ensure_collab_service()
    assert svc is not None
    assert ctx.collab_service is svc
    assert ctx.ensure_collab_service() is svc


def test_is_low_memory_machine(monkeypatch) -> None:
    import app.config.memory_profile as mp

    monkeypatch.setattr(mp, "physical_memory_gb", lambda: 2.0)
    assert mp.is_low_memory_machine() is True
    monkeypatch.setattr(mp, "physical_memory_gb", lambda: 8.0)
    assert mp.is_low_memory_machine() is False
