"""Frozen-exe regression: uvicorn 的默认日志格式化器在无控制台的 PyInstaller
--windowed exe 里会崩。

真实现场（v0.57 / v0.59 win64 包，用户日志 app.log）：

    File "uvicorn/logging.py", line 42, in __init__
    AttributeError: 'NoneType' object has no attribute 'isatty'
      → ValueError: Unable to configure formatter 'default'
    File "app/services/collab_net.py", line 109, in run

原因：`--windowed` 打包后 `sys.stdout is None`，而 uvicorn 的 DefaultFormatter
在 __init__ 里做 `sys.stdout.isatty()`。开发时跑 `python main.py` 有控制台，
所以永远复现不出来。

这里直接把 sys.stdout / sys.stderr 置 None 来模拟冻结 exe 的运行环境。
"""
from __future__ import annotations

import sys

import pytest

from app.services.collab_net import build_uvicorn_config


@pytest.fixture()
def no_std_streams(monkeypatch):
    """模拟 PyInstaller --windowed：进程没有 stdout/stderr。"""
    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)
    yield


def test_build_uvicorn_config_survives_missing_stdout(no_std_streams):
    """无 stdout 时构建 uvicorn.Config 不得抛异常（红线：协作服务器能起来）。"""
    app = object()  # uvicorn.Config 不在 __init__ 里校验 app 类型

    config = build_uvicorn_config(app, port=5050)

    assert config.port == 5050
    assert config.host == "0.0.0.0"


def test_build_uvicorn_config_disables_uvicorn_log_config(no_std_streams):
    """uvicorn 不得自己 dictConfig —— 日志统一交给 app 自己的 logging 配置。"""
    config = build_uvicorn_config(object(), port=5051)

    assert config.log_config is None
