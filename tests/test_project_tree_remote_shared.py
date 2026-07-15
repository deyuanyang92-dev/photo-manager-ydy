"""tests/test_project_tree_remote_shared.py — 队友共享项目进统一树(纯逻辑).

Claude Code 2026-07-15 — 用户定稿: 队友新建/共享的、我本地还没有的项目, 直接出现在
同一棵项目树里(带标识), 点进去物化+同步。不加单独接口。这里测承重的纯函数
build_remote_shared_nodes(去重、标记)。
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PyQt6")

from app.views.project_tree_view import build_remote_shared_nodes


def test_remote_projects_become_nodes():
    team = [
        {"name": "广西项目", "code": "GX-123", "peer_count": 2},
        {"name": "福建项目", "code": "FJ-456", "peer_count": 1},
    ]
    nodes = build_remote_shared_nodes(team, local_names=set())
    names = {n["name"] for n in nodes}
    assert names == {"广西项目", "福建项目"}
    gx = next(n for n in nodes if n["name"] == "广西项目")
    assert gx["is_remote"] is True
    assert gx["shared"] is True
    assert gx["remote_code"] == "GX-123"
    assert gx["peer_count"] == 2
    assert gx["path"] == ""            # 本地还没有


def test_dedup_against_local_projects_by_name():
    team = [
        {"name": "广西项目", "code": "GX-123", "peer_count": 2},
        {"name": "福建项目", "code": "FJ-456", "peer_count": 1},
    ]
    # 我本地已经有"广西项目" -> 不重复出现远程节点
    nodes = build_remote_shared_nodes(team, local_names={"广西项目"})
    assert {n["name"] for n in nodes} == {"福建项目"}


def test_dedup_within_team_list():
    team = [
        {"name": "广西项目", "code": "GX-1", "peer_count": 1},
        {"name": "广西项目", "code": "GX-2", "peer_count": 1},
    ]
    nodes = build_remote_shared_nodes(team, local_names=set())
    assert len(nodes) == 1


def test_empty_and_nameless_are_skipped():
    assert build_remote_shared_nodes([], set()) == []
    assert build_remote_shared_nodes([{"name": "", "code": "X"}], set()) == []
    assert build_remote_shared_nodes(None, None) == []
