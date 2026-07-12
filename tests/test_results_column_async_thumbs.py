"""成果列缩略图必须在**工作线程**解码(不得钉死 GUI 线程)。

背景(2026-07-12): results_column 声明了 _thumb_thread/_thumb_worker/... 六个字段,
注释宣称「真正的解码在 GridThumbnailWorker 线程上跑」, 但 _ensure_thumb_worker
根本不存在 —— 实际仍是 _load_next_thumbnail_batch → card.load_thumbnail_now()
→ _thumb_provider → decode_image_thumbnail 在 GUI 线程同步解码(冷缓存 TIFF 最坏
落到 ImageMagick 子进程, 十几秒卡死)。本文件把「解码不在主线程」钉成契约。

红线(app/workers/thumbnail_worker.py): worker 只 emit QImage; QPixmap 只能主线程构造。
"""
from __future__ import annotations

import threading

import pytest
from PIL import Image
from PyQt6.QtGui import QImage

from app.widgets.results_column import ResultsColumn


def _mk_tif(tmp_path, name: str = "AAA-1-R-20260618-1.tif"):
    path = tmp_path / name
    Image.new("RGB", (160, 120), "green").save(path)
    return path


def test_cold_decode_never_runs_on_gui_thread(qtbot, tmp_path, monkeypatch):
    """冷缓存: 真正的解码必须发生在非主线程, 且 GUI 侧同步解码器不得被调用。"""
    import app.workers.thumbnail_worker as tw
    import app.widgets.results_column as rc

    main_ident = threading.get_ident()
    decode_threads: list[int] = []
    real_decode = tw.decode_image_data

    def spy_decode(path, max_size=280, **kw):
        decode_threads.append(threading.get_ident())
        return real_decode(path, max_size, **kw)

    monkeypatch.setattr(tw, "decode_image_data", spy_decode)

    gui_decodes: list[str] = []
    monkeypatch.setattr(
        rc, "_decode_thumb",
        lambda path, max_size=280: gui_decodes.append(path) or None,
    )

    tif = _mk_tif(tmp_path)
    col = ResultsColumn()
    qtbot.addWidget(col)
    col.load_uid("AAA-1-R-20260618", [{"path": str(tif), "name": tif.name}], [])

    card = col._cards[0]
    qtbot.waitUntil(lambda: card._icon.property("hasThumbnail") is True, timeout=5000)

    assert decode_threads, "解码请求必须真的投递给 worker"
    assert all(ident != main_ident for ident in decode_threads), (
        f"解码跑在了 GUI 线程: {decode_threads} vs main {main_ident}"
    )
    assert gui_decodes == [], "GUI 线程同步解码器(_decode_thumb)不得被调用"


def test_worker_is_lazy_and_reused(qtbot, tmp_path):
    """线程懒启动: 没有解码需求时不建线程; 建了就复用同一条(不是每张一条)。"""
    col = ResultsColumn()
    qtbot.addWidget(col)
    assert col._thumb_worker is None and col._thumb_thread is None

    worker = col._ensure_thumb_worker()
    assert worker is not None
    assert col._thumb_thread is not None and col._thumb_thread.isRunning()
    assert col._ensure_thumb_worker() is worker, "必须复用同一条长驻线程"

    col.teardown()
    assert col._thumb_worker is None


def test_requests_are_deduplicated_by_path(qtbot, tmp_path):
    """同一路径在飞行中时不得重复投递解码请求。"""
    tif = _mk_tif(tmp_path)
    col = ResultsColumn()
    qtbot.addWidget(col)
    col.load_uid("AAA-1-R-20260618", [{"path": str(tif), "name": tif.name}], [])
    card = col._cards[0]

    assert col._request_thumbnail(card, str(tif)) is True
    pending_after_first = dict(col._pending_thumbs)
    assert str(tif) in col._pending_thumb_paths
    # 第二次(同路径)不得再投递
    assert col._request_thumbnail(card, str(tif)) is True
    assert dict(col._pending_thumbs) == pending_after_first


def test_stale_reply_is_dropped(qtbot):
    """迟到的回包(卡片已随 _clear_rows 销毁)只能被丢弃, 不得贴错卡/崩溃。"""
    col = ResultsColumn()
    qtbot.addWidget(col)
    col._on_thumb_decoded(4242, QImage(4, 4, QImage.Format.Format_RGB32))  # 未知 req_id
    assert col._pending_thumbs == {}


def test_clear_rows_drops_pending_requests(qtbot, tmp_path):
    """_clear_rows 后, 之前的在途请求整体作废(卡片 C++ 对象已 deleteLater)。"""
    tif = _mk_tif(tmp_path)
    col = ResultsColumn()
    qtbot.addWidget(col)
    col.load_uid("AAA-1-R-20260618", [{"path": str(tif), "name": tif.name}], [])
    col._request_thumbnail(col._cards[0], str(tif))
    assert col._pending_thumbs

    col.clear()
    assert col._pending_thumbs == {}
    assert col._pending_thumb_paths == set()
    # 回包晚到 → 不得抛 RuntimeError(wrapped C/C++ object has been deleted)
    col._on_thumb_decoded(1, QImage(4, 4, QImage.Format.Format_RGB32))


def test_failed_decode_is_negative_cached(qtbot):
    """解码失败(None)进负缓存: 不再反复投递, 卡片落到占位图标。"""
    col = ResultsColumn()
    qtbot.addWidget(col)
    col.load_uid("UID", [{"path": "/fake/missing.tif", "name": "missing.tif"}], [])
    card = col._cards[0]

    qtbot.waitUntil(lambda: "/fake/missing.tif" in col._failed_thumb_paths, timeout=5000)
    assert card._icon.property("hasThumbnail") is False
    # 负缓存命中 ⇒ 不再进 pending
    col._request_thumbnail(card, "/fake/missing.tif")
    assert col._pending_thumb_paths == set()


def test_thumbnail_appears_without_user_action(qtbot, tmp_path):
    """真实 TIFF: 无需任何用户操作, 缩略图异步填充(PROJECT_MEMORY 不可回归项)。"""
    tif = _mk_tif(tmp_path, "BBB-2-R-20260618-1.tif")
    col = ResultsColumn()
    qtbot.addWidget(col)
    col.load_uid("BBB-2-R-20260618", [{"path": str(tif), "name": tif.name}], [])
    card = col._cards[0]
    qtbot.waitUntil(lambda: card._icon.property("hasThumbnail") is True, timeout=5000)
    pm = card._icon.pixmap()
    assert pm is not None and not pm.isNull()


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
