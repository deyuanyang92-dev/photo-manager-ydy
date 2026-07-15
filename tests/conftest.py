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
def _isolate_app_qsettings(tmp_path, monkeypatch):
    """Never let a test workspace become the user's next startup project."""
    from PyQt6.QtCore import QSettings

    from app.config.settings import AppSettings

    settings_path = tmp_path / "app_settings.ini"

    def _init(self) -> None:
        self._qs = QSettings(str(settings_path), QSettings.Format.IniFormat)
        self._migrate_delete_jpg_default()
        self._migrate_archive_mode_default()

    monkeypatch.setattr(AppSettings, "__init__", _init)
    yield


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
def _flush_deferred_deletions():
    """每个测试后冲洗 Qt deferred-deletion 队列 (v0.56 拆雷).

    pytest-qt 只 close+deleteLater 测试里的 widget, 不转动事件循环——重型视图
    (ProjectTreeView 等) 的延迟销毁在队列里越积越多; 第 3 个视图之后, 任何调用
    qtbot.wait() 的测试都会让 3 个死视图在同一批 processEvents 里销毁 → segfault
    (2026-07-10 二分定位: 任意 3 个前序视图测试 + 1 个 wait 即稳定 core dump,
    ≤2 个则安全)。每测试后立即处理, 队列里永远最多 1 个视图的积压。
    """
    yield
    from PyQt6.QtCore import QEvent
    from PyQt6.QtWidgets import QApplication

    app = QApplication.instance()
    if app is not None:
        for _ in range(3):
            app.sendPostedEvents(None, QEvent.Type.DeferredDelete.value)
            app.processEvents()


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


@pytest.fixture(autouse=True)
def _isolate_collab_tasks_persist(tmp_path, monkeypatch):
    """Claude Code 2026-07-15 — 协作任务清单落盘路径隔离到 tmp。

    CollabService 现在把团队任务持久化到 data/collab_tasks.json(重启后不丢)。
    测试若用真路径, 会互相泄漏任务状态并污染真文件。同 user_projects.json 的隔离
    做法: 把路径函数指到本测试的 tmp。
    """
    try:
        import app.services.collab_service as _cs
    except Exception:
        yield
        return
    monkeypatch.setattr(
        _cs, "_default_tasks_persist_path",
        lambda: str(tmp_path / "collab_tasks.json"),
    )
    yield
