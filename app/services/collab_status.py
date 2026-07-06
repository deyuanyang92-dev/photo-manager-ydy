"""Shared presentation state for collaboration UI.

The settings page stores collaboration preferences; the collaboration centre
shows the live service state.  This module keeps the user-facing status rules
in one place so the two surfaces do not drift.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable


@dataclass(frozen=True)
class CollabStatusSnapshot:
    state: str
    status_badge: str
    scope_label: str
    next_step_label: str
    next_step_detail: str
    setup_enabled: bool
    bind_project_enabled: bool

    @property
    def plain_status(self) -> str:
        """Status text without the leading traffic-light glyph."""
        return (
            self.status_badge
            .replace("⚪ ", "")
            .replace("🟢 ", "")
            .strip()
        )


def _service_value(service: Any, name: str, default: str = "") -> str:
    try:
        return str(getattr(service, name, "") or default)
    except Exception:
        return default


def _is_running(service: Any) -> bool:
    try:
        return bool(service is not None and service.is_running())
    except Exception:
        return False


def build_collab_status(
    service: Any,
    peers: Iterable[Any] | None = None,
) -> CollabStatusSnapshot:
    peers_list = list(peers or [])
    if service is None:
        return CollabStatusSnapshot(
            state="no_service",
            status_badge="⚪ 协作服务未启动",
            scope_label="协作组：—",
            next_step_label="下一步：启用协作服务",
            next_step_detail="打开一个项目后，先启动或加入协作组。",
            setup_enabled=False,
            bind_project_enabled=False,
        )

    running = _is_running(service)
    group = _service_value(service, "group_code")
    project_id = _service_value(service, "project_id")
    bind_enabled = bool(project_id)

    if not running:
        group_label = group or "未设置"
        return CollabStatusSnapshot(
            state="not_started",
            status_badge="⚪ 协作未启动",
            scope_label=f"协作组：{group_label} · 协作未启动",
            next_step_label="下一步：启动/加入协作组",
            next_step_detail="协作组让几台电脑互相看见，并同步编号任务；同一团队使用同一个组码。",
            setup_enabled=True,
            bind_project_enabled=bind_enabled,
        )

    if not group:
        return CollabStatusSnapshot(
            state="missing_group",
            status_badge="⚪ 未设置协作组码",
            scope_label="协作组：未设置 · 先启动/加入协作组",
            next_step_label="下一步：设置协作组码",
            next_step_detail="协作服务已启动，但还没有协作组码；同一团队需要使用同一个组码。",
            setup_enabled=True,
            bind_project_enabled=bind_enabled,
        )

    same_group = [
        peer for peer in peers_list
        if _service_value(peer, "group_code") == group
    ]
    same_project = [
        peer for peer in same_group
        if project_id and _service_value(peer, "project_id") == project_id
    ]
    badge = f"🟢 {len(peers_list)} 台在线" if peers_list else "⚪ 未发现其他设备"
    scope = f"协作组：{group} · 任务跨项目，照片仅同项目同步码"

    if not peers_list:
        return CollabStatusSnapshot(
            state="no_peers",
            status_badge=badge,
            scope_label=scope,
            next_step_label="下一步：连接队友电脑",
            next_step_detail="让队友加入同一协作组；如果搜索不到，把“本机连接地址”发给对方手动连接。",
            setup_enabled=True,
            bind_project_enabled=bind_enabled,
        )

    if not same_group:
        return CollabStatusSnapshot(
            state="different_group",
            status_badge=badge,
            scope_label=scope,
            next_step_label="下一步：统一协作组码",
            next_step_detail="已发现设备，但组码不同；任务和照片都不会同步。请让队友使用同一协作组码。",
            setup_enabled=True,
            bind_project_enabled=bind_enabled,
        )

    if not same_project:
        return CollabStatusSnapshot(
            state="tasks_only",
            status_badge=badge,
            scope_label=scope,
            next_step_label="任务已可协作；照片还不能同步",
            next_step_detail="如果这些电脑拍的是同一项目，点击“绑定同一项目”，复制/粘贴项目同步码后才允许同步照片。",
            setup_enabled=True,
            bind_project_enabled=bind_enabled,
        )

    return CollabStatusSnapshot(
        state="media_ready",
        status_badge=badge,
        scope_label=scope,
        next_step_label="照片同步已就绪",
        next_step_detail=f"已有 {len(same_project)} 台可同步设备。回到照片工作区，使用“同步当前编号”或“同步项目”拉取照片/TIF/ZIP。",
        setup_enabled=True,
        bind_project_enabled=bind_enabled,
    )
