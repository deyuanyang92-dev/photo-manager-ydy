"""test_main_shutdown.py — 关窗=真退出契约 (Opus 2026-07-16)。

Windows exe 关窗后仍后台驻留的 bug: 内嵌库线程(uvicorn asyncio / zeroconf)非守护,
解释器退出 join 它们卡住进程。修复=_finalize_and_exit 先跑幂等 teardown(释放 DB 锁),
再 os._exit 硬退。这里断言该收尾函数的行为(monkeypatch os._exit, 不真杀进程)。
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import main as main_mod


def test_finalize_and_exit_tears_down_then_hard_exits(monkeypatch):
    calls = {"teardown": 0, "exit": None}

    class FakeWin:
        def _teardown(self):
            calls["teardown"] += 1

    monkeypatch.setattr(main_mod.os, "_exit",
                        lambda code: calls.__setitem__("exit", code))

    main_mod._finalize_and_exit(FakeWin(), 0)

    assert calls["teardown"] == 1      # 硬退前先跑 teardown(释放 DB 锁/落盘设置)
    assert calls["exit"] == 0          # 用 os._exit 硬退, 不 join 非守护库线程


def test_finalize_and_exit_hard_exits_even_if_teardown_raises(monkeypatch):
    exited = {}

    class BadWin:
        def _teardown(self):
            raise RuntimeError("boom")

    monkeypatch.setattr(main_mod.os, "_exit",
                        lambda code: exited.__setitem__("code", code))

    # teardown 抛错也必须硬退, 否则一个坏的 teardown 又把进程吊住
    main_mod._finalize_and_exit(BadWin(), 3)

    assert exited["code"] == 3


def test_finalize_and_exit_coerces_non_int_code(monkeypatch):
    exited = {}

    class FakeWin:
        def _teardown(self):
            pass

    monkeypatch.setattr(main_mod.os, "_exit",
                        lambda code: exited.__setitem__("code", code))

    main_mod._finalize_and_exit(FakeWin(), None)   # app.exec() 理论上返回 int, 但兜底

    assert exited["code"] == 0
