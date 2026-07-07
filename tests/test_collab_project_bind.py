"""Tests for team project bind helpers."""
from __future__ import annotations

from unittest.mock import MagicMock

from app.services.collab_project_bind import (
    describe_project_sync_state,
    filter_bind_options,
    list_bind_options,
    same_name_options,
)
from app.services.collab_service import CollabService
from app.services.collab_types import PeerInfo


def _peer(**kwargs) -> PeerInfo:
    return PeerInfo(
        ip=kwargs.get("ip", "192.168.1.2"),
        port=kwargs.get("port", 5050),
        hostname=kwargs.get("hostname", "队友PC"),
        project_name=kwargs.get("project_name", "三门湾"),
        project_id=kwargs.get("project_id", "b" * 32),
        group_code=kwargs.get("group_code", "TEAM-1"),
    )


class TestListBindOptions:
    def test_excludes_already_synced_project(self):
        pid = "a" * 32
        svc = CollabService()
        svc._group_code = "TEAM-1"
        svc._project_id = pid
        svc._project_name = "三门湾"
        peer = _peer(project_id=pid, project_name="三门湾")
        assert list_bind_options(svc, [peer]) == []

    def test_same_name_flagged(self):
        svc = CollabService()
        svc._group_code = "TEAM-1"
        svc._project_id = "a" * 32
        svc._project_name = "三门湾"
        peer = _peer(project_id="b" * 32, project_name="三门湾")
        opts = list_bind_options(svc, [peer])
        assert len(opts) == 1
        assert opts[0].same_name is True

    def test_filter_by_name(self):
        svc = CollabService()
        svc._group_code = "TEAM-1"
        svc._project_id = "a" * 32
        peers = [
            _peer(project_id="b" * 32, project_name="三门湾", hostname="A"),
            _peer(project_id="c" * 32, project_name="舟山调查", hostname="B"),
        ]
        opts = list_bind_options(svc, peers)
        filtered = filter_bind_options(opts, "舟山")
        assert len(filtered) == 1
        assert filtered[0].name == "舟山调查"


class TestDescribeProjectSyncState:
    def test_synced_with_peer(self):
        pid = "a" * 32
        svc = CollabService()
        svc._group_code = "TEAM-1"
        svc._project_id = pid
        svc._project_name = "三门湾"
        peer = _peer(project_id=pid, hostname="张三")
        text = describe_project_sync_state(svc, [peer])
        assert "已与" in text
        assert "张三" in text

    def test_same_name_hint(self):
        svc = CollabService()
        svc._group_code = "TEAM-1"
        svc._project_id = "a" * 32
        svc._project_name = "三门湾"
        peer = _peer(project_id="b" * 32, project_name="三门湾")
        text = describe_project_sync_state(svc, [peer])
        assert "同名" in text
        assert same_name_options(svc, [peer])
