"""tests/conftest.py — 全测试套件共享夹具。

默认禁用真实地理编码网络请求。Nominatim/高德在本环境被墙（见
memory osm-blocked-needs-proxy）；真实请求会让 GeocodeWorker 的 QThread 卡住
~6s（socket 超时），常常 outlive 启动它的测试 → 该 QThread 在仍运行时被销毁
→ Qt 抛 "QThread: Destroyed while thread is still running" 触发 SIGABRT，或在
pytest-qt 的 _process_events 里碰到悬挂对象触发 SIGSEGV。整套表现为偶发崩溃。

对策：autouse 把 geocode_service 的单一网络出口 ``_http_get_json`` 默认替换成立即
抛错（worker 秒结束、不再 outlive 测试）。想测"成功路径"的用例自行在用例内
``patch`` 覆盖即可（局部 patch 嵌套在本 autouse 之内、会赢）。
"""
from __future__ import annotations

from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def _no_real_geocode_network():
    try:
        from app.services import geocode_service
    except Exception:
        yield
        return

    def _blocked(*_args, **_kwargs):
        raise OSError("geocode network disabled in tests (conftest)")

    with patch.object(geocode_service, "_http_get_json", side_effect=_blocked):
        yield
