"""test_specimen_sidebar_perf.py — 编号侧栏的两处卡顿根因回归测试。

1. 搜索防抖:每敲一个键都 clear() + 重建整棵列表(N=800 时几百 ms~数秒冻结)。
   现在用户键入只重启一个 200ms 的一次性定时器 —— 停止输入后**只重建一次**。
   程序化 setText() 仍立即生效(不发 textEdited), 老调用方/测试语义不变。

2. 行高不再逐行 heightForWidth():旧实现对每一行强制一次完整 layout 计算。
   新实现是纯几何(QFontMetrics 折行 + 同结构行只测一次的 chrome), 必须与旧值
   **逐 px 相同** —— 否则长编号会被卡片下沿裁掉(2026-07-10 用户报障的回归)。
"""
import sqlite3
from unittest.mock import MagicMock

import pytest
from PyQt6.QtWidgets import QApplication

from app.db import db_manager
from app.widgets.specimen_sidebar import SpecimenSidebar

_APP = QApplication.instance() or QApplication([])

_PROJ = "/tmp/proj-sidebar-perf-test"


@pytest.fixture
def db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    db_manager.ensure_schema(conn)
    return conn


@pytest.fixture
def ctx(db):
    c = MagicMock()
    c.get_db.return_value = db
    c.current_project_dir = _PROJ
    c.collab_service = None
    return c


def _add(db, uid, name="", storage=""):
    db.execute(
        "INSERT INTO specimens (uid, scientific_name, storage, owner_project_dir)"
        " VALUES (?, ?, ?, ?)",
        (uid, name, storage, _PROJ),
    )
    db.commit()


# ── (2) 行高:新几何算法必须等于旧的 heightForWidth 结果 ──────────────────────

_UIDS = [
    "U-1",
    "GXFCG-BLW-BZC003-R-20260618",
    "ZJ-TMW-B2-001-RT95E-20260618",
    "FJ-YGLZ-B2-DLC001-RT95E-20260618-0813",
    "FJ-YGLZ-B2-DLC001-RT95E-20260618-0813-EXTRA-LONG-TAIL-SEG",
]


def test_row_content_height_matches_legacy_height_for_width(ctx):
    """纯几何行高 == 旧的逐行 heightForWidth(防裁字回归)。"""
    sb = SpecimenSidebar(ctx)
    mismatches = []
    for width in (160, 180, 200, 220, 240, 260, 300, 400):
        for uid in _UIDS:
            for active in (False, True):
                for is_rna in (False, True):
                    for name in ("", "Marphysa sp."):
                        progress = {"total": 2, "grouped": 1} if is_rna else {}
                        row = sb._build_row_widget(
                            uid, name, "", "RT95E", None,
                            active=active, is_rna=is_rna, progress=progress,
                        )
                        row.ensurePolished()
                        legacy = row.heightForWidth(width)
                        if legacy <= 0:
                            legacy = row.sizeHint().height()
                        new = sb._row_content_height(row, width, active=active)
                        if new != legacy:
                            mismatches.append(
                                (width, uid, active, is_rna, bool(name), legacy, new)
                            )
    assert not mismatches, f"行高与旧算法不一致(可能裁字): {mismatches[:6]}"


def test_row_content_height_is_cached_after_first_measure(ctx):
    """同 (文本, 宽度, active, 结构) 只测一次 —— 重复 refresh / resize 不再付 layout。"""
    sb = SpecimenSidebar(ctx)
    row = sb._build_row_widget(
        "FJ-YGLZ-B2-DLC001-RT95E-20260618-0813", "", "", "",
        None, active=False, is_rna=False, progress={},
    )
    row.ensurePolished()
    first = sb._row_content_height(row, 220, active=False)
    calls = []
    orig = row.heightForWidth
    row.heightForWidth = lambda w: (calls.append(w), orig(w))[1]  # type: ignore
    again = sb._row_content_height(row, 220, active=False)
    assert again == first
    assert calls == [], "第二次取行高不得再触发 heightForWidth(整棵行 layout)"


# ── (1) 搜索防抖 ─────────────────────────────────────────────────────────────


def test_typing_does_not_rebuild_list_per_keystroke(ctx, db, qtbot):
    for i in range(6):
        _add(db, f"UID-{i:03d}")
    sb = SpecimenSidebar(ctx)
    qtbot.addWidget(sb)
    sb.refresh()
    assert sb._list.count() == 6

    rebuilds: list[str] = []
    orig = sb._apply_filter
    sb._apply_filter = lambda t: (rebuilds.append(t), orig(t))[1]  # type: ignore

    sb.show()
    sb._search.setFocus()
    qtbot.keyClicks(sb._search, "UID-003")

    # 敲了 7 个键 —— 一次都不许重建, 只允许把防抖定时器往后推。
    assert rebuilds == [], f"每键重建了 {len(rebuilds)} 次"
    assert sb._search_timer.isActive()
    assert sb._list.count() == 6, "防抖期间列表保持原样"

    qtbot.waitUntil(lambda: not sb._search_timer.isActive(), timeout=3000)
    assert rebuilds == ["UID-003"], "停止输入后只重建一次"
    assert sb._list.count() == 1
    sb.close()


def test_programmatic_set_text_filters_immediately(ctx, db):
    """程序化 setText() 不走防抖(不发 textEdited)—— 老调用方/测试的同步语义不变。"""
    _add(db, "AAA-1")
    _add(db, "BBB-2")
    sb = SpecimenSidebar(ctx)
    sb.refresh()

    sb._search.setText("BBB")

    assert sb._list.count() == 1
    assert not sb._search_timer.isActive()


def test_search_timer_is_created_once_and_reused(ctx, db):
    """定时器只在 __init__ 建一次;refresh()/on_activate 不得再新建(主线程持有)。"""
    _add(db, "AAA-1")
    sb = SpecimenSidebar(ctx)
    t = sb._search_timer
    sb.refresh()
    sb.refresh()
    assert sb._search_timer is t
    assert t.isSingleShot()
    assert t.parent() is sb
