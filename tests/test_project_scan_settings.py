"""test_project_scan_settings.py — 项目树扫描/缓存三个可调设置.

Claude Code 2026-07-15 — 大规模性能阶段 1c: 缓存有效期 / 扫描深度 / 自动扫描开关
三个 AppSettings 参数的 get/set/clamp/默认值。
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from app.config.settings import AppSettings


@pytest.fixture
def settings(tmp_path, monkeypatch):
    # 每个测试用独立的 QSettings 存储, 互不污染
    from PyQt6.QtCore import QSettings
    QSettings.setPath(
        QSettings.Format.IniFormat,
        QSettings.Scope.UserScope,
        str(tmp_path),
    )
    s = AppSettings()
    # 清掉可能残留的键
    for k in ("project/scan_cache_ttl_seconds", "project/scan_max_depth",
              "project/tree_auto_scan_enabled"):
        s._qs.remove(k)
    return s


def test_ttl_default_is_300(settings):
    assert settings.project_scan_cache_ttl_seconds == 300


def test_ttl_roundtrip_and_clamp(settings):
    settings.project_scan_cache_ttl_seconds = 600
    assert settings.project_scan_cache_ttl_seconds == 600
    settings.project_scan_cache_ttl_seconds = -5      # 下限 0
    assert settings.project_scan_cache_ttl_seconds == 0
    settings.project_scan_cache_ttl_seconds = 999999  # 上限 86400
    assert settings.project_scan_cache_ttl_seconds == 86400


def test_ttl_zero_means_always_rescan(settings):
    settings.project_scan_cache_ttl_seconds = 0
    assert settings.project_scan_cache_ttl_seconds == 0  # 合法值, 不被夹成默认


def test_ttl_corrupt_value_falls_back_to_default(settings):
    settings._qs.setValue("project/scan_cache_ttl_seconds", "not-a-number")
    assert settings.project_scan_cache_ttl_seconds == 300


def test_max_depth_default_is_6(settings):
    assert settings.project_scan_max_depth == 6


def test_max_depth_roundtrip_and_clamp(settings):
    settings.project_scan_max_depth = 3
    assert settings.project_scan_max_depth == 3
    settings.project_scan_max_depth = 0    # 下限 1
    assert settings.project_scan_max_depth == 1
    settings.project_scan_max_depth = 999  # 上限 12
    assert settings.project_scan_max_depth == 12


def test_auto_scan_default_is_true(settings):
    assert settings.project_tree_auto_scan_enabled is True


def test_auto_scan_roundtrip(settings):
    settings.project_tree_auto_scan_enabled = False
    assert settings.project_tree_auto_scan_enabled is False
    settings.project_tree_auto_scan_enabled = True
    assert settings.project_tree_auto_scan_enabled is True
