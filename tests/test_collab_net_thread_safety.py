"""线程安全回归：异常绝不许逃出 QThread.run()。

现场（app.log:193-199）：uvicorn.Config 在 frozen exe 里抛 ValueError，
异常从 CollabServerThread.run() 逃出 → PyQt 交给 sys.excepthook → main.py 的
hook 在**工作线程**里构造 QWidget 弹窗（Qt 硬性禁止：QWidget 只能在 GUI 主线程
创建）→ 弹窗风暴 + 主线程冻结。

红线：worker 线程只能发 signal（auto-connection 自动 queued 回主线程），
run() 里的任何异常都必须被兜住并转成 server_error / discovery_error。

注意：这里直接调用 run()（而不是 start()），因为 PyQt6 对「Python 重写的 C++
虚函数里逃出的未捕获异常」会在 sys.excepthook 之后调用 qFatal()/abort() —— 真
起线程跑红用例会直接 abort 掉整个 pytest 进程。直接调 run() 同样能证明
「异常不逃出 run()」这一契约。
"""
from __future__ import annotations

import pytest

from app.services import collab_net
from app.services.collab_net import CollabDiscoveryThread, CollabServerThread


# ── CollabServerThread ────────────────────────────────────────────────────────

def _make_server_thread() -> CollabServerThread:
    return CollabServerThread(store=object(), node_info_fn=lambda: {}, preferred_port=5050)


def test_server_run_contains_port_scan_crash(qapp, monkeypatch):
    """_find_free_port 抛非 OSError 时，run() 不得抛，且必须发 server_error。"""
    th = _make_server_thread()
    monkeypatch.setattr(
        CollabServerThread, "_find_free_port",
        lambda self, start: (_ for _ in ()).throw(RuntimeError("boom-port")),
    )

    seen: list[str] = []
    th.server_error.connect(seen.append)

    th.run()   # 必须不抛

    assert seen and "boom-port" in seen[0]


def test_server_run_contains_app_build_crash(qapp, monkeypatch):
    """_build_fastapi_app 抛异常（旧代码在 try 之外）时同样必须被兜住。"""
    th = _make_server_thread()
    monkeypatch.setattr(CollabServerThread, "_find_free_port", lambda self, start: 5050)
    monkeypatch.setattr(
        collab_net, "_build_fastapi_app",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom-app")),
    )

    seen: list[str] = []
    th.server_error.connect(seen.append)

    th.run()

    assert seen and "boom-app" in seen[0]


def test_server_run_contains_uvicorn_config_crash(qapp, monkeypatch):
    """现场根因：uvicorn.Config 构造抛 ValueError（旧代码在 try 之外）。"""
    th = _make_server_thread()
    monkeypatch.setattr(CollabServerThread, "_find_free_port", lambda self, start: 5050)
    monkeypatch.setattr(collab_net, "_build_fastapi_app", lambda *a, **k: object())
    monkeypatch.setattr(
        collab_net, "build_uvicorn_config",
        lambda app, port: (_ for _ in ()).throw(
            ValueError("Unable to configure formatter 'default'")),
    )

    seen: list[str] = []
    th.server_error.connect(seen.append)

    th.run()

    assert seen and "formatter" in seen[0]


# ── CollabDiscoveryThread ─────────────────────────────────────────────────────

def test_discovery_run_contains_local_ip_crash(qapp, monkeypatch):
    """_get_local_ip / Zeroconf() 抛异常（旧代码在 try 之外）也不许逃出 run()。"""
    pytest.importorskip("zeroconf")

    th = CollabDiscoveryThread(hostname="test-node", port=5050)
    monkeypatch.setattr(
        collab_net, "_get_local_ip",
        lambda: (_ for _ in ()).throw(RuntimeError("boom-ip")),
    )

    seen: list[str] = []
    th.discovery_error.connect(seen.append)

    th.run()

    assert seen and "boom-ip" in seen[0]
