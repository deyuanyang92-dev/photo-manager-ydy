"""tests/test_survey_overview_panel.py — 右栏概览面板口径标签.

photo_count 双口径是刻意的(概览=全部成片含未归编号; 数据汇总=筛选范围内
uid 归组成片), 同一张 KPI 卡两种口径共用 → 标题必须随模式切换说清数的是什么。
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def _make_panel(qtbot, monkeypatch):
    from app.widgets import survey_overview_panel as sop

    monkeypatch.setattr(
        sop, "aggregate_survey_overview",
        lambda ws, labels=None: {"photo_count": 5, "specimen_count": 2},
    )
    panel = sop.SurveyOverviewPanel()
    qtbot.addWidget(panel)
    monkeypatch.setattr(
        panel._species_panel, "set_workspaces", lambda *a, **k: None
    )
    return panel


class TestPhotoCardScopeTitle:
    def test_overview_mode_titles_all_results(self, qtbot, monkeypatch, tmp_path):
        panel = _make_panel(qtbot, monkeypatch)
        panel.set_workspaces([str(tmp_path)])
        assert panel._card_photos._title.text() == "全部成片"

    def test_filtered_mode_titles_uid_results(self, qtbot, monkeypatch, tmp_path):
        panel = _make_panel(qtbot, monkeypatch)
        panel.set_filtered_stats(
            {"photo_count": 3}, workspace_dirs=[str(tmp_path)]
        )
        assert panel._card_photos._title.text() == "编号成片"
