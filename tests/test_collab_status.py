from __future__ import annotations

from types import SimpleNamespace

from app.services.collab_status import build_collab_status


class _Service:
    def __init__(self, *, running: bool, group_code: str = "", project_id: str = ""):
        self._running = running
        self.group_code = group_code
        self.project_id = project_id

    def is_running(self) -> bool:
        return self._running


def test_collab_status_no_service_is_not_started():
    status = build_collab_status(None, [])

    assert status.state == "no_service"
    assert status.status_badge == "⚪ 协作服务未启动"
    assert status.next_step_label == "下一步：选择协作方式"
    assert status.setup_enabled is True


def test_collab_status_service_not_running_does_not_claim_no_peers():
    status = build_collab_status(_Service(running=False, group_code="TEAM-1"), [])

    assert status.state == "not_started"
    assert status.status_badge == "⚪ 协作未启动"
    assert "未发现其他设备" not in status.status_badge
    assert "TEAM-1" in status.scope_label


def test_collab_status_running_without_group_is_missing_group():
    status = build_collab_status(_Service(running=True), [])

    assert status.state == "missing_group"
    assert status.status_badge == "⚪ 未配对团队"
    assert status.next_step_label == "下一步：选择协作方式"


def test_collab_status_running_group_without_peers_is_no_peers():
    status = build_collab_status(_Service(running=True, group_code="TEAM-1"), [])

    assert status.state == "no_peers"
    assert status.status_badge == "⚪ 未发现其他设备"
    assert "TEAM-1" in status.scope_label


def test_collab_status_same_group_different_project_is_tasks_only():
    svc = _Service(running=True, group_code="TEAM-1", project_id="P1")
    peer = SimpleNamespace(group_code="TEAM-1", project_id="P2")

    status = build_collab_status(svc, [peer])

    assert status.state == "tasks_only"
    assert status.status_badge == "🟢 1 台在线"
    assert status.next_step_label == "下一步：选择共享项目"


def test_collab_status_same_group_same_project_is_media_ready():
    svc = _Service(running=True, group_code="TEAM-1", project_id="P1")
    peer = SimpleNamespace(group_code="TEAM-1", project_id="P1")

    status = build_collab_status(svc, [peer])

    assert status.state == "media_ready"
    assert status.status_badge == "🟢 1 台在线"
    assert status.next_step_label == "照片同步已就绪"
