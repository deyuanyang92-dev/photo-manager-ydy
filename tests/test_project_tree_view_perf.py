"""tests/test_project_tree_view_perf.py — 项目树视图的「GUI 线程零磁盘/零解码」回归.

守三条已修的卡顿根因（headless, pytest-qt）：
  1. 树模式(默认)下 on_activate 不得重建卡片网格 → 不得对每个项目跑 get_project_summary。
  2. _show_preview_image 不得在主线程全量解码（不得调用 decode_image_thumbnail），
     缓存未命中先出「载入中…」，解码走 worker 线程，QImage 回主线程转 QPixmap。
  3. _make_media_preview_card 同上：建卡循环里不许同步解码。
另加一条源码级断言：QApplication.processEvents() 不得再出现在预览路径里（重入事件循环）。
"""
from __future__ import annotations

import ast
import inspect
import sqlite3
import textwrap
from pathlib import Path

import pytest

pytest.importorskip("PyQt6")

from PyQt6.QtGui import QColor, QPixmap

from app.views import project_tree_view as ptv
from app.views.project_tree_view import ProjectTreeView

from tests.test_project_tree_view import _FakeCtx  # 复用同一个假 ctx/settings


def _make_workspace(p: Path) -> None:
    (p / "_data").mkdir(parents=True, exist_ok=True)
    sqlite3.connect(str(p / "_data" / "project.db")).close()


def _write_image(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pm = QPixmap(64, 48)
    pm.fill(QColor("#0f766e"))
    assert pm.save(str(path))


@pytest.fixture
def ctx():
    return _FakeCtx()


def _explode(*_a, **_kw):  # noqa: D401
    raise AssertionError("GUI 线程不得同步解码（decode_image_thumbnail 被调用）")


@pytest.mark.parametrize(
    "layout_mode,expect_card_reload", [("tree", False), ("cards", True)]
)
def test_card_grid_only_reloads_when_visible(
    qtbot, tmp_path, ctx, monkeypatch, layout_mode, expect_card_reload
):
    """卡片网格每张卡都要跑一次 get_project_summary（全量 iterdir + stat）。

    树模式（默认）下卡片根本不可见 —— on_activate 不得重建它。
    """
    root = tmp_path / "survey"
    _make_workspace(root / "断面a")
    ctx.settings.project_tree_root = str(root)
    ctx.settings.project_tree_view_mode = "rooted"
    ctx.settings.project_tree_layout_mode = layout_mode

    view = ProjectTreeView(ctx)
    qtbot.addWidget(view)
    try:
        calls: list[int] = []
        monkeypatch.setattr(view, "_reload_card_grid", lambda: calls.append(1))
        view.on_activate()
        assert bool(calls) is expect_card_reload
    finally:
        view.stop_background_work()


def test_preview_image_is_async_and_never_decodes_on_gui_thread(
    qtbot, tmp_path, ctx, monkeypatch
):
    from app.utils import image_thumbnail as it

    monkeypatch.setattr(it, "decode_image_thumbnail", _explode)
    monkeypatch.setattr(it, "try_cached_image_data", lambda *a, **k: None)  # 强制未命中

    img = tmp_path / "ws" / "results" / "a.jpg"
    _write_image(img)

    view = ProjectTreeView(ctx)
    qtbot.addWidget(view)
    try:
        view._show_preview_image(str(img))
        # 未命中 → 立刻占位，主线程没有阻塞解码
        assert view._preview_image.text() == "载入中…"
        assert view._preview_pixmap is None
        # worker 线程解码完成后由主线程槽填图
        qtbot.waitUntil(lambda: view._preview_pixmap is not None, timeout=5000)
        assert not view._preview_pixmap.isNull()
        assert view._preview_image.pixmap() is not None
    finally:
        view.stop_background_work()


def test_preview_discards_stale_worker_result(qtbot, tmp_path, ctx):
    """连点两张图：旧请求的迟到结果必须被丢弃（req_id 递增）."""
    view = ProjectTreeView(ctx)
    qtbot.addWidget(view)
    try:
        img_a = tmp_path / "a.jpg"
        img_b = tmp_path / "b.jpg"
        _write_image(img_a)
        _write_image(img_b)
        view._show_preview_image(str(img_a))
        stale = view._preview_req
        view._show_preview_image(str(img_b))
        assert view._preview_req == stale + 1
        # 用过期 req 回填 → 必须什么都不做
        view._apply_preview_image(stale, QPixmap(8, 8).toImage())
        assert view._preview_pixmap is None or view._preview_req != stale
    finally:
        view.stop_background_work()


def test_media_preview_cards_do_not_decode_on_gui_thread(
    qtbot, tmp_path, ctx, monkeypatch
):
    from app.utils import image_thumbnail as it

    monkeypatch.setattr(it, "decode_image_thumbnail", _explode)
    monkeypatch.setattr(it, "try_cached_image_data", lambda *a, **k: None)

    ws = tmp_path / "ws"
    _make_workspace(ws)
    for n in ("a.jpg", "b.jpg"):
        _write_image(ws / "results" / n)

    view = ProjectTreeView(ctx)
    qtbot.addWidget(view)
    try:
        view._render_media_preview(str(ws))  # 建卡循环：不许同步解码
        assert view._media_grid.count() == 2
        assert view._media_count_lbl.text() == "2 个"
        # 清空节点 → 在途结果作废（generation 自增，pending 里不留 media 项）
        view._clear_media_preview()
        assert not any(v[0] == "media" for v in view._thumb_pending.values())
    finally:
        view.stop_background_work()


def _called_names(func) -> set[str]:
    """函数体里真正被调用的名字（注释/文档字符串不算 —— 走 AST）."""
    tree = ast.parse(textwrap.dedent(inspect.getsource(func)))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            fn = node.func
            if isinstance(fn, ast.Attribute):
                names.add(fn.attr)
            elif isinstance(fn, ast.Name):
                names.add(fn.id)
    return names


def test_preview_path_has_no_process_events_reentry():
    """源码级红线：预览/影像卡路径不得重入事件循环、不得主线程全量解码."""
    preview = _called_names(ProjectTreeView._show_preview_image)
    assert "processEvents" not in preview
    assert "decode_image_thumbnail" not in preview
    card = _called_names(ProjectTreeView._make_media_preview_card)
    assert "decode_image_thumbnail" not in card
    assert "processEvents" not in card


def test_refresh_button_bypasses_scan_cache(qtbot, tmp_path, ctx, monkeypatch):
    """「刷新」必须先清 scan_tree 的 TTL 缓存，否则点了等于没点."""
    cleared: list[object] = []
    monkeypatch.setattr(
        ptv.pts, "clear_project_tree_cache", lambda root=None: cleared.append(root)
    )
    root = tmp_path / "survey"
    _make_workspace(root)
    ctx.settings.project_tree_root = str(root)
    ctx.settings.project_tree_view_mode = "rooted"

    view = ProjectTreeView(ctx)
    qtbot.addWidget(view)
    try:
        view.on_activate()
        cleared.clear()
        view._btn_refresh.click()
        assert cleared, "刷新按钮没有清缓存"
    finally:
        view.stop_background_work()
