"""tests/test_survey_overview_panel.py — 右栏概览面板: 口径标签 + 异步聚合.

两件事:

1. photo_count 双口径是刻意的(概览=全部成片含未归编号; 数据汇总=筛选范围内
   uid 归组成片), 同一张 KPI 卡两种口径共用 → 标题必须随模式切换说清数的是什么。
2. ``set_workspaces`` 的聚合必须跑在 **工作线程**: ``aggregate_survey_overview``
   逐库全表 SELECT + 物种名录扫描 + results/ iterdir+stat, 多断面选中时数十秒;
   过去它同步跑在 GUI 线程 → Windows 判「未响应」。这里锁死:
   - set_workspaces 返回时聚合尚未完成(KPI 还是占位符);
   - 聚合体不在主线程执行;
   - 结果经 signal 异步回填;
   - 快速切换选中时旧代次结果被丢弃(不许把旧断面的数字盖到新选中上)。
"""
from __future__ import annotations

import os
import threading
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_LOADING = "…"


def _fake_aggregate(record: dict, *, delay: float = 0.0):
    """构造 aggregate_survey_overview 替身: 记录调用线程/入参, 可选阻塞."""

    def fake(workspaces, labels=None):
        record.setdefault("threads", []).append(threading.current_thread())
        record.setdefault("calls", []).append(list(workspaces))
        if delay:
            time.sleep(delay)
        n = len(workspaces)
        # photo_count 用工作区数编码, 便于分辨「哪一次请求的结果落到了控件上」
        return {
            "photo_count": 100 + n,
            "specimen_count": n,
            "workspace_count": n,
        }

    return fake


def _make_panel(qtbot, monkeypatch, record, *, delay: float = 0.0):
    from app.widgets import survey_overview_panel as sop

    monkeypatch.setattr(
        sop, "aggregate_survey_overview", _fake_aggregate(record, delay=delay)
    )
    panel = sop.SurveyOverviewPanel()
    qtbot.addWidget(panel)
    # 物种名录面板走它自己的同步 API(本次不动), 测试里屏蔽掉真实扫库。
    monkeypatch.setattr(panel._species_panel, "set_workspaces", lambda *a, **k: None)
    return panel


class TestPhotoCardScopeTitle:
    def test_overview_mode_titles_all_results(self, qtbot, monkeypatch, tmp_path):
        record: dict = {}
        panel = _make_panel(qtbot, monkeypatch, record)
        panel.set_workspaces([str(tmp_path)])
        # 标题是模式标记, 不依赖聚合结果 → 立即生效
        assert panel._card_photos._title.text() == "全部成片"
        qtbot.waitUntil(lambda: panel._card_photos._value.text() == "101", timeout=5000)
        panel.teardown()

    def test_filtered_mode_titles_uid_results(self, qtbot, monkeypatch, tmp_path):
        record: dict = {}
        panel = _make_panel(qtbot, monkeypatch, record)
        panel.set_filtered_stats({"photo_count": 3}, workspace_dirs=[str(tmp_path)])
        assert panel._card_photos._title.text() == "编号成片"
        assert panel._card_photos._value.text() == "3"
        panel.teardown()


class TestAsyncAggregation:
    def test_set_workspaces_returns_before_aggregate_lands(
        self, qtbot, monkeypatch, tmp_path
    ):
        """set_workspaces 返回时: 聚合结果还没落到控件上, 只有占位符."""
        record: dict = {}
        panel = _make_panel(qtbot, monkeypatch, record, delay=0.4)

        panel.set_workspaces([str(tmp_path)])

        # 同步实现下这里已经是 "101" —— 红。异步实现下是加载占位符。
        assert panel._card_photos._value.text() == _LOADING
        assert panel._card_specimens._value.text() == _LOADING

        qtbot.waitUntil(lambda: panel._card_photos._value.text() == "101", timeout=8000)
        assert panel._card_specimens._value.text() == "1"
        panel.teardown()

    def test_aggregate_runs_off_the_gui_thread(self, qtbot, monkeypatch, tmp_path):
        record: dict = {}
        panel = _make_panel(qtbot, monkeypatch, record, delay=0.1)
        main_thread = threading.current_thread()

        panel.set_workspaces([str(tmp_path)])
        qtbot.waitUntil(lambda: panel._card_photos._value.text() == "101", timeout=8000)

        assert record["threads"], "aggregate_survey_overview 没被调用"
        assert all(t is not main_thread for t in record["threads"]), (
            "聚合仍在 GUI 线程执行"
        )
        panel.teardown()

    def test_workspace_count_card_is_seeded_immediately(
        self, qtbot, monkeypatch, tmp_path
    ):
        """断面数不需要聚合就能知道 → 加载态立刻显示, 不是占位符."""
        record: dict = {}
        a = tmp_path / "a"
        b = tmp_path / "b"
        a.mkdir()
        b.mkdir()
        panel = _make_panel(qtbot, monkeypatch, record, delay=0.3)

        panel.set_workspaces([str(a), str(b)])
        assert panel._card_workspaces._value.text() == "2"

        qtbot.waitUntil(lambda: panel._card_photos._value.text() == "102", timeout=8000)
        panel.teardown()


class TestStaleResultsDiscarded:
    def test_rapid_reselect_discards_old_worker_result(
        self, qtbot, monkeypatch, tmp_path
    ):
        """快速切换断面: 慢的旧请求回来时不许覆盖新选中的结果."""
        from app.widgets import survey_overview_panel as sop

        record: dict = {}
        started = threading.Event()

        def fake(workspaces, labels=None):
            record.setdefault("calls", []).append(list(workspaces))
            n = len(workspaces)
            if n == 3:  # 旧请求 → 慢
                started.set()
                time.sleep(0.6)
            return {"photo_count": 100 + n, "specimen_count": n, "workspace_count": n}

        monkeypatch.setattr(sop, "aggregate_survey_overview", fake)
        panel = sop.SurveyOverviewPanel()
        qtbot.addWidget(panel)
        monkeypatch.setattr(
            panel._species_panel, "set_workspaces", lambda *a, **k: None
        )

        dirs = []
        for name in ("a", "b", "c"):
            d = tmp_path / name
            d.mkdir()
            dirs.append(str(d))

        panel.set_workspaces(dirs)  # 3 个 → 慢
        assert started.wait(3.0), "旧 worker 没跑起来"
        panel.set_workspaces(dirs[:1])  # 1 个 → 快

        qtbot.waitUntil(lambda: panel._card_photos._value.text() == "101", timeout=8000)
        # 等旧的那次聚合肯定已经返回, 确认它没把 "103" 盖上来
        qtbot.wait(900)
        assert panel._card_photos._value.text() == "101"
        assert panel._card_specimens._value.text() == "1"
        assert panel._card_workspaces._value.text() == "1"
        panel.teardown()

    def test_filtered_stats_wins_over_inflight_overview(
        self, qtbot, monkeypatch, tmp_path
    ):
        """概览 worker 在飞 → 中栏数据汇总先落地; 迟到的概览结果不许覆盖筛选 KPI."""
        record: dict = {}
        panel = _make_panel(qtbot, monkeypatch, record, delay=0.5)

        panel.set_workspaces([str(tmp_path)])
        panel.set_filtered_stats(
            {"photo_count": 7, "specimen_count": 7, "workspace_count": 1},
            workspace_dirs=[str(tmp_path)],
        )
        assert panel._card_photos._value.text() == "7"

        qtbot.wait(1000)  # 旧概览 worker 此时已 emit
        assert panel._card_photos._value.text() == "7"
        assert panel._card_photos._title.text() == "编号成片"
        panel.teardown()


class TestWorkerLifecycle:
    def test_teardown_while_running_does_not_leak_worker(
        self, qtbot, monkeypatch, tmp_path
    ):
        from app.widgets import survey_overview_panel as sop

        record: dict = {}
        panel = _make_panel(qtbot, monkeypatch, record, delay=0.3)
        panel.set_workspaces([str(tmp_path)])
        assert panel._worker is not None

        panel.teardown()
        assert panel._worker is None

        # 仍在跑的线程必须有强引用兜着(不能被 GC → QThread destroyed while running)
        qtbot.waitUntil(
            lambda: not any(w.isRunning() for w in sop._LIVE_OVERVIEW_WORKERS),
            timeout=8000,
        )
        sop._reap_overview_workers()
        assert sop._LIVE_OVERVIEW_WORKERS == []

    def test_failed_aggregate_does_not_crash_panel(self, qtbot, monkeypatch, tmp_path):
        from app.widgets import survey_overview_panel as sop

        def boom(workspaces, labels=None):
            raise RuntimeError("db locked")

        monkeypatch.setattr(sop, "aggregate_survey_overview", boom)
        panel = sop.SurveyOverviewPanel()
        qtbot.addWidget(panel)
        monkeypatch.setattr(
            panel._species_panel, "set_workspaces", lambda *a, **k: None
        )

        panel.set_workspaces([str(tmp_path)])
        qtbot.waitUntil(
            lambda: panel._card_photos._value.text() != _LOADING, timeout=8000
        )
        assert panel._card_photos._value.text() == "—"
        panel.teardown()
