"""test_monitor_panel_thumb_worker.py — 待处理流缩略图必须**离开 GUI 主线程**解码。

回归背景(2026-07-12, P0 卡顿):
  monitor_panel 的 16ms 定时器每跳在**主线程**同步解 4 张图, 且 use_cache=False
  绕过磁盘缩略图缓存。单张 24MP JPEG 缩解码 40–100ms、无 720px 代理的新 TIFF
  0.5–5s ⇒ 相机连拍 / Helicon 合成完毕后主界面直接「未响应」。

本文件锁三条不可回归的性质:
  1. 缩略图在 worker 线程解码 —— 主线程的 decode_image_thumbnail 一次都不许被调用。
  2. 走磁盘缓存(use_cache 不再是 False), 跨启动不重复解码。
  3. 解码线程是全进程共享单例 —— 每个面板各起一条 QThread 会在 Python GC 回收
     面板时「线程仍在运行就被析构」⇒ 进程 abort(实测 test_workbench_view 挂掉)。
"""

from unittest.mock import MagicMock

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QImage

from app.services.monitor_service import FileEntry, ScanResult
from app.widgets import monitor_panel as mp
from app.widgets.monitor_panel import MonitorPanel


@pytest.fixture
def ctx():
    c = MagicMock()
    c.get_db.return_value = None
    return c


@pytest.fixture
def panel(qtbot, ctx):
    w = MonitorPanel(ctx)
    qtbot.addWidget(w)
    w.show()
    return w


@pytest.fixture(autouse=True)
def _clear_thumb_cache():
    mp.clear_file_thumb_cache()
    yield
    mp.clear_file_thumb_cache()


def _real_jpg(tmp_path, name="shot.jpg"):
    """真 JPEG 文件 —— worker 必须真能解出图, 才证明图不是主线程解的。"""
    img = QImage(64, 48, QImage.Format.Format_RGB32)
    img.fill(Qt.GlobalColor.red)
    p = tmp_path / name
    assert img.save(str(p), "JPG"), "测试自身的 JPEG 落盘失败"
    return p


def _scan_of(path):
    e = FileEntry(
        name=path.name,
        path=str(path),
        kind="jpg",
        size=path.stat().st_size,
        mtime="2026-01-01T00:00:00+00:00",
        attributed_specimen_id=None,
    )
    return ScanResult(project_dir=str(path.parent), jpg_files=[e], tiff_files=[])


def test_thumbnail_is_decoded_off_the_gui_thread(panel, qtbot, tmp_path, monkeypatch):
    """缩略图照常出现, 但主线程的同步解码入口一次都没被调用。"""
    jpg = _real_jpg(tmp_path)

    main_thread_decodes = []

    def _spy(path, *a, **kw):  # monitor_panel 里唯一的主线程解码入口
        main_thread_decodes.append(path)
        return None

    monkeypatch.setattr(mp, "decode_image_thumbnail", _spy)

    panel.load_scan(_scan_of(jpg))
    card = panel._cards[0]

    # worker 解完 → 主线程槽转 QPixmap → 贴图
    qtbot.waitUntil(lambda: card._thumb_label.property("hasThumbnail") is True,
                    timeout=5000)

    pixmap = card._thumb_label.pixmap()
    assert pixmap is not None and not pixmap.isNull()
    assert main_thread_decodes == [], (
        f"GUI 主线程仍在解码({main_thread_decodes}) —— P0 卡顿会复发"
    )


def test_initial_thumbnails_are_revealed_as_one_batch(panel, qtbot):
    """One ready card must not appear before the rest of the first batch."""
    first = MagicMock()
    second = MagicMock()
    panel._card_by_key = {"a.jpg": first, "b.jpg": second}

    panel._begin_silent_thumbnail_load(["a.jpg", "b.jpg"])
    panel._mark_silent_thumbnail_ready("a.jpg")
    panel._on_silent_thumbnail_timeout()

    first.load_thumbnail_now.assert_not_called()
    second.load_thumbnail_now.assert_not_called()
    assert not panel._thumb_loading_cover.isHidden()

    panel._mark_silent_thumbnail_ready("b.jpg")
    qtbot.waitUntil(lambda: first.load_thumbnail_now.call_count == 1, timeout=1000)

    second.load_thumbnail_now.assert_called_once_with()
    assert panel._thumb_loading_cover.isHidden()


def test_recycled_card_skip_does_not_hang_the_loading_cover(panel, qtbot):
    """回收卡片被派发循环跳过时, 也必须清出 silent pending —— 否则 pending 永不归零,
    加载遮罩(_thumb_loading_cover)挂死, 实况监控网格被永久遮住(BUG 2026-07-14)。"""
    path = "recycled.jpg"

    # 一张「已被回收」的卡片: parent() is None, 但 _entry.path 仍是本批次里的路径。
    dead = MagicMock()
    dead.parent.return_value = None
    dead._entry.path = path

    panel._card_by_key = {path: dead}
    panel._begin_silent_thumbnail_load([path])
    assert not panel._thumb_loading_cover.isHidden()
    assert panel._silent_thumb_pending == {path}

    # 卡片进解码队列, 派发循环因回收而跳过它。
    panel._queue_thumbnail(dead)
    panel._load_next_thumbnail_batch()

    # pending 必须能归零 → finish 被调度 → 遮罩隐藏(否则永久遮住网格)。
    qtbot.waitUntil(lambda: panel._thumb_loading_cover.isHidden(), timeout=1500)
    assert panel._silent_thumb_pending == set()


def test_file_thumb_pixmap_uses_the_disk_cache(monkeypatch, tmp_path):
    """§7 旧代码写死 use_cache=False, 磁盘缩略图缓存形同虚设。"""
    jpg = _real_jpg(tmp_path, "cached.jpg")
    seen = {}

    def _spy(path, max_size, **kw):
        seen.update(kw)
        return None

    monkeypatch.setattr(mp, "decode_image_thumbnail", _spy)
    mp._file_thumb_pixmap(str(jpg))

    assert seen.get("use_cache", True) is not False, "缩略图解码又绕开磁盘缓存了"


def test_decode_thread_is_a_process_wide_singleton(qtbot, ctx, tmp_path):
    """每面板一条 QThread ⇒ 面板被 GC 时线程仍在跑 ⇒ Qt qFatal ⇒ 进程 abort。"""
    jpg = _real_jpg(tmp_path, "a.jpg")

    p1, p2 = MonitorPanel(ctx), MonitorPanel(ctx)
    qtbot.addWidget(p1)
    qtbot.addWidget(p2)

    p1.load_scan(_scan_of(jpg))
    p2.load_scan(_scan_of(jpg))
    qtbot.waitUntil(lambda: p1._thumb_worker is not None and p2._thumb_worker is not None,
                    timeout=5000)

    assert p1._thumb_worker is p2._thumb_worker, "两个面板起了两条解码线程"
    assert not hasattr(p1, "_thumb_thread"), "面板不得自己持有 QThread(GC 时会 abort)"


def test_stale_decoded_reply_is_dropped(panel):
    """陈旧/别的面板的回包(req_id 不在 _thumb_pending)必须被安静丢弃。"""
    panel._on_thumb_decoded(999_999, None)  # 不得抛异常
    assert panel._thumb_pending == {}


def test_worker_reply_populates_the_memory_cache(panel, qtbot, tmp_path):
    """回包先落 _FILE_THUMB_CACHE, 卡片才贴图 —— 贴图那一下才不会触发解码。"""
    jpg = _real_jpg(tmp_path, "warm.jpg")

    panel.load_scan(_scan_of(jpg))
    qtbot.waitUntil(lambda: mp._file_thumb_cache_get(str(jpg))[0] is True, timeout=5000)

    hit, pm = mp._file_thumb_cache_get(str(jpg))
    assert hit is True
    assert pm is not None and not pm.isNull()
