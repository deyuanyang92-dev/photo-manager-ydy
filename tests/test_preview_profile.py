"""tests/test_preview_profile.py"""
from __future__ import annotations

from app.config import preview_profile as pp
from app.config.project_tree_layout import PREVIEW_MASTER_PRESETS


def test_preview_master_size_clamps_to_presets():
    pp.reset_preview_master_size()
    assert pp.set_preview_master_size(999) in PREVIEW_MASTER_PRESETS
    assert pp.current_preview_master_size() == 1080


def test_settings_sync_preview_master_size(tmp_path, monkeypatch):
    from app.config.settings import AppSettings

    monkeypatch.setenv("APPDATA", str(tmp_path))
    settings = AppSettings()
    settings.project_tree_preview_master_size = 512
    pp.set_preview_master_size(settings.project_tree_preview_master_size)
    assert pp.current_preview_master_size() == 512
