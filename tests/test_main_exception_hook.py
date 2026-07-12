"""sys.excepthook 线程纪律回归测试。

根因：抛异常的工作线程(如 CollabServerThread)上跑 sys.excepthook，直接构造并
exec() 一个主线程父窗口的 QMessageBox → QObject::setParent 跨线程 + 235 条
QBasicTimer::start 警告 + Recursive repaint + 主窗口「未响应」。

红线：非 GUI 线程 **绝不** 构造/exec 任何 QWidget，只允许 emit signal 回主线程。
"""

from __future__ import annotations

import sys

import pytest
from PyQt6.QtCore import QThread

import main as main_mod


@pytest.fixture
def hook_env(qapp, monkeypatch):
    """Pretend we are a real (non-offscreen) GUI run, and record ui.critical calls."""
    from PyQt6.QtWidgets import QWidget

    from app.utils import ui

    calls: list[tuple] = []
    threads: list[QThread] = []

    def _fake_critical(parent, title, text, informative_text="", detailed_text="", **kw):
        calls.append((title, text, detailed_text))
        threads.append(QThread.currentThread())
        return None

    monkeypatch.setattr(ui, "critical", _fake_critical)
    # _hook 里两道 headless 短路都要拆掉，否则测不到线程分支
    monkeypatch.setattr(main_mod, "_HEADLESS_SMOKE", False)
    monkeypatch.setenv("QT_QPA_PLATFORM", "xcb")
    old_hook = sys.excepthook

    # pytest-qt 会把自己的「捕获事件循环内异常」钩子装进 sys.excepthook，而
    # main._hook 第一句就转调 old_hook —— 于是本测试**故意**抛的异常会被它记为
    # 「逃逸异常」并判测试失败。装 hook 之前先把 excepthook 换成 no-op，让
    # old_hook 变成它，测的才是我们自己的线程分支。
    sys.excepthook = lambda *_a: None

    win = QWidget()
    reporter = main_mod._install_exception_hook(win)
    yield win, reporter, calls, threads
    sys.excepthook = old_hook
    win.deleteLater()


def test_is_main_qt_thread_true_on_gui_thread(qapp):
    assert main_mod._is_main_qt_thread() is True


class _RaisingThread(QThread):
    """Reproduces PyQt handing an escaped QThread.run() exception to sys.excepthook
    *on the worker thread* — exactly what collab_net.py:109 does in the wild."""

    def __init__(self):
        super().__init__()
        self.saw_main_thread = None

    def run(self):
        self.saw_main_thread = main_mod._is_main_qt_thread()
        try:
            raise ValueError("boom from worker")
        except ValueError:
            sys.excepthook(*sys.exc_info())


def test_worker_thread_exception_never_builds_widgets(hook_env, qapp):
    win, reporter, calls, threads = hook_env
    assert reporter is not None

    thread = _RaisingThread()
    thread.start()
    assert thread.wait(5000)

    # 工作线程上 _is_main_qt_thread() 必须为 False，且此刻绝不能碰 QWidget
    assert thread.saw_main_thread is False
    assert calls == [], "worker thread must not construct/exec a QMessageBox"

    # signal 已排队；主线程处理事件后才弹窗，且是在主线程弹的
    qapp.processEvents()
    assert len(calls) == 1
    title, text, detail = calls[0]
    assert title == "程序遇到错误"
    assert text == "boom from worker"
    assert "ValueError" in detail
    assert threads[0] is qapp.thread()


def test_main_thread_exception_shows_dialog_directly(hook_env, qapp):
    win, reporter, calls, threads = hook_env

    try:
        raise RuntimeError("boom from gui")
    except RuntimeError:
        sys.excepthook(*sys.exc_info())

    # 主线程：同步弹窗，不需要 processEvents
    assert len(calls) == 1
    assert calls[0][1] == "boom from gui"
    assert threads[0] is qapp.thread()
