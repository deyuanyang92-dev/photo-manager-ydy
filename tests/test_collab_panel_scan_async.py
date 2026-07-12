"""Regression: 「搜索队友」不得在主线程同步跑 scan_lan()。

Bug: CollabPanel._on_scan 直接调用 svc.scan_lan()（254 IP × 20 端口，
每个 _probe timeout=3s），主线程被阻塞数秒 —— 界面白屏；随后的
QTimer.singleShot(5000, ...) 只是假装扫描在后台跑。

Fix: 用 _LanScanWorker(QThread) 跑 scan_lan()，扫完 queued signal 回主线程
恢复按钮。本测试断言：
  1) scan_lan() 在非主线程执行；
  2) _on_scan() 立即返回（不阻塞主线程）；
  3) 扫描结束后按钮被恢复（不再依赖 5s 假定时器）。
"""

from __future__ import annotations

import threading
import time

import pytest
from PyQt6.QtCore import QCoreApplication, QThread
from PyQt6.QtWidgets import QApplication

from app.widgets.collab_panel import CollabPanel, _LanScanWorker


class _SlowFakeService:
    """scan_lan() 故意慢，模拟真实全子网扫描。"""

    SCAN_SECONDS = 0.4

    def __init__(self) -> None:
        self.called_on_thread: int | None = None
        self.call_count = 0

    def scan_lan(self, hosts=None, ports=None, timeout: float = 0.3):
        self.call_count += 1
        self.called_on_thread = threading.get_ident()
        time.sleep(self.SCAN_SECONDS)
        return ["peer-a", "peer-b"]


class _Ctx:
    collab_service = None
    current_project_dir = ""
    settings = None


def _pump(seconds: float) -> None:
    """跑 Qt 事件循环，让 queued signal 有机会投递。"""
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        QCoreApplication.processEvents()
        time.sleep(0.01)


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def test_worker_runs_scan_lan_off_the_main_thread(app):
    svc = _SlowFakeService()
    worker = _LanScanWorker(svc)  # type: ignore[arg-type]
    results: list[int] = []
    worker.scan_done.connect(results.append)

    main_ident = threading.get_ident()
    t0 = time.monotonic()
    worker.start()
    started_cost = time.monotonic() - t0
    # start() 立即返回，不等扫描结束
    assert started_cost < _SlowFakeService.SCAN_SECONDS / 2

    assert worker.wait(5000)
    _pump(0.3)

    assert svc.call_count == 1
    assert svc.called_on_thread is not None
    assert svc.called_on_thread != main_ident      # ← 核心断言：没在主线程跑
    assert results == [2]                          # 发现 2 个节点，回主线程


def test_on_scan_returns_immediately_and_restores_button(app):
    panel = CollabPanel(_Ctx())  # type: ignore[arg-type]
    svc = _SlowFakeService()
    panel._svc = svc  # type: ignore[assignment]

    t0 = time.monotonic()
    panel._on_scan()
    cost = time.monotonic() - t0

    # 主线程没有被扫描阻塞
    assert cost < _SlowFakeService.SCAN_SECONDS / 2
    assert panel._peer_scan_btn.isEnabled() is False
    assert panel._scan_worker is not None

    worker = panel._scan_worker
    assert worker.wait(5000)
    _pump(0.5)

    # 扫描结束后按钮被 on_done 恢复（而不是 5s 定时器）
    assert panel._peer_scan_btn.isEnabled() is True
    assert panel._peer_scan_btn.text() == "搜索队友"
    assert panel._scan_worker is None
    assert svc.called_on_thread != threading.get_ident()

    panel.deleteLater()


def test_repeat_click_while_scanning_does_not_spawn_second_worker(app):
    panel = CollabPanel(_Ctx())  # type: ignore[arg-type]
    svc = _SlowFakeService()
    panel._svc = svc  # type: ignore[assignment]

    panel._on_scan()
    first = panel._scan_worker
    panel._on_scan()          # 重复点击
    assert panel._scan_worker is first

    assert isinstance(first, QThread)
    assert first.wait(5000)
    _pump(0.5)
    assert svc.call_count == 1

    panel.deleteLater()
