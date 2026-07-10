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
    connection_role: str
    connection_title: str
    connection_detail: str
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


def _peer_label(peer: Any) -> str:
    """Human-readable peer label for UI and activity logs."""
    for attr in ("operator_name", "session_name", "hostname", "ip"):
        val = str(getattr(peer, attr, "") or "").strip()
        if val:
            return val
    return "未知用户"


def peer_display_name(peer: Any) -> str:
    """Alias for UI modules that import a explicit helper name."""
    return _peer_label(peer)


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
            connection_role="idle",
            connection_title="尚未启动协作",
            connection_detail="在上方方式一填写永久码，点「保存并启动协作」。",
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
            connection_role="idle",
            connection_title="协作尚未启动",
            connection_detail="填写永久码和名字后，点「保存并启动协作」。",
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
            connection_role="idle",
            connection_title="尚未设置团队永久码",
            connection_detail="在方式一生成或填写永久码，然后保存并启动。",
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
            connection_role="waiting",
            connection_title="等待队友（还未连接成功）",
            connection_detail=(
                f"永久码「{group}」已生效。队友填同一码并保存后，会出现在在线设备列表。"
            ),
            setup_enabled=True,
            bind_project_enabled=bind_enabled,
        )

    if not same_group:
        others = "、".join(_peer_label(peer) for peer in peers_list[:3])
        return CollabStatusSnapshot(
            state="different_group",
            status_badge=badge,
            scope_label=scope,
            next_step_label="下一步：统一团队永久码",
            next_step_detail=(
                "已发现设备，但团队永久码不同，不会同步。请让队友填写同一个码。"
            ),
            connection_role="warn",
            connection_title="发现设备，但永久码不一致（未连通）",
            connection_detail=(
                f"你的永久码是「{group}」，但在线设备（{others}）使用了不同的码。"
                f"请让队友改成与你完全相同的永久码后再保存。"
            ),
            setup_enabled=True,
            bind_project_enabled=bind_enabled,
        )

    same_names = "、".join(_peer_label(peer) for peer in same_group[:3])
    extra = f"等 {len(same_group)} 台" if len(same_group) > 3 else ""
    if not same_project:
        return CollabStatusSnapshot(
            state="tasks_only",
            status_badge=badge,
            scope_label=scope,
            next_step_label="下一步：使用项目码",
            next_step_detail=(
                "团队已连通。打开项目码共享，选择本机项目后互相粘贴项目码，"
                "照片才会在选定的项目里互传。"
            ),
            connection_role="success",
            connection_title=f"连接成功：{len(same_group)} 台队友在线",
            connection_detail=(
                f"已发现队友 {same_names}{extra}（团队永久码一致）。"
                f"任务已可协作；若要同步照片，请继续用方式二做项目码配对。"
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
        connection_role="success",
        connection_title=f"连接成功：{len(same_group)} 台队友，照片可同步",
        connection_detail=(
            f"队友 {same_names}{extra} 已与当前项目配对。"
            f"回到照片工作区即可同步编号与照片。"
        ),
        setup_enabled=True,
        bind_project_enabled=bind_enabled,
    )
