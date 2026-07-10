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


@pytest.fixture(autouse=True)
def _isolate_user_projects_json(tmp_path, monkeypatch):
    """测试永不写真 ``data/user_projects.json``。

    根因: ``record_recent_workspace`` / ``enter_workspace`` 走
    ``default_user_projects_json_path()`` → 测试建 tmp 工作区并 enter 时, 把
    ``/tmp/pytest-.../FJ-SHUTDOWN`` 这类垃圾登记进 app-local json, 下次启动 app
    就在项目列表里看到测试垃圾。这里把 default 路径 patch 到 per-test tmp,
    测试显式传 path 的用例不受影响。
    """
    try:
        from app.services import project_service as _ps
    except Exception:
        yield
        return
    jp = tmp_path / "user_projects.json"
    jp.write_text('{"version": 1, "projects": []}', encoding="utf-8")
    monkeypatch.setattr(_ps, "default_user_projects_json_path", lambda: str(jp))
    yield


@pytest.fixture(autouse=True)
def _isolate_collab_peer_trust_qs(tmp_path, monkeypatch):
    """Each test gets a fresh collab trust/block list (no cross-test pollution)."""
    from PyQt6.QtCore import QSettings

    from app.services.collab_peer_trust import CollabPeerTrustStore

    path = tmp_path / "collab_peer_trust.ini"
    qs = QSettings(str(path), QSettings.Format.IniFormat)

    def _init(self, qs_arg=None) -> None:
        self._qs = qs if qs_arg is None else qs_arg

    monkeypatch.setattr(CollabPeerTrustStore, "__init__", _init)
    yield
