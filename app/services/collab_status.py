"""Shared presentation state for collaboration UI.

User-facing vocabulary (keep consistent everywhere):
  团队永久码 — shared saved code everyone enters to join the same team
  项目配对   — separate opt-in binding for photo/TIF/ZIP sync
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
            scope_label="团队永久码：—",
            next_step_label="下一步：选择协作方式",
            next_step_detail=(
                "要多人自动连接，先保存团队永久码；只绑定同一个采集项目，"
                "可直接打开项目码共享，选择本机项目并粘贴队友项目码。"
            ),
            setup_enabled=True,
            bind_project_enabled=False,
        )

    running = _is_running(service)
    group = _service_value(service, "group_code")
    project_id = _service_value(service, "project_id")
    bind_enabled = bool(project_id)

    if not running:
        group_label = group or "未配对"
        return CollabStatusSnapshot(
            state="not_started",
            status_badge="⚪ 协作未启动",
            scope_label=f"团队永久码：{group_label} · 协作未启动",
            next_step_label="下一步：选择协作方式",
            next_step_detail=(
                "团队永久码用于几台电脑自动连接，保存后永久有效；"
                "项目码共享是单独功能，可直接选择项目并让队友粘贴连接。"
            ),
            setup_enabled=True,
            bind_project_enabled=bind_enabled,
        )

    if not group:
        return CollabStatusSnapshot(
            state="missing_group",
            status_badge="⚪ 未配对团队",
            scope_label="团队永久码：未设置",
            next_step_label="下一步：选择协作方式",
            next_step_detail=(
                "要自动找队友，设置同一个团队永久码；只共享某个项目，"
                "打开项目码共享，选择本机项目后生成或粘贴项目码。"
            ),
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
    scope = f"团队永久码：{group} · 任务可跨项目，照片需做项目配对"

    if not peers_list:
        return CollabStatusSnapshot(
            state="no_peers",
            status_badge=badge,
            scope_label=scope,
            next_step_label="下一步：等待队友上线",
            next_step_detail=(
                "让队友在协作中心填写同一个团队永久码；"
                "局域网找不到时可复制连接码手动连接。"
            ),
            setup_enabled=True,
            bind_project_enabled=bind_enabled,
        )

    if not same_group:
        return CollabStatusSnapshot(
            state="different_group",
            status_badge=badge,
            scope_label=scope,
            next_step_label="下一步：统一团队永久码",
            next_step_detail=(
                "已发现设备，但团队永久码不同，不会同步。请让队友填写同一个码。"
            ),
            setup_enabled=True,
            bind_project_enabled=bind_enabled,
        )

    if not same_project:
        return CollabStatusSnapshot(
            state="tasks_only",
            status_badge=badge,
            scope_label=scope,
            next_step_label="下一步：选择共享项目",
            next_step_detail=(
                "团队已连通。选择队友项目或互相粘贴项目码，"
                "照片才会在选定的项目里互传。"
            ),
            setup_enabled=True,
            bind_project_enabled=bind_enabled,
        )

    return CollabStatusSnapshot(
        state="media_ready",
        status_badge=badge,
        scope_label=scope,
        next_step_label="照片同步已就绪",
        next_step_detail=(
            f"已有 {len(same_project)} 台设备与当前项目配对。"
            "回到照片工作区，使用「同步当前编号」或「同步项目」。"
        ),
        setup_enabled=True,
        bind_project_enabled=bind_enabled,
    )
