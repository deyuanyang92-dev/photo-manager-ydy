"""collab_diagnostics.py — Self-diagnostics for the collaboration subsystem.

Pure functions split out of collab_service.py.  ``CollabService.run_diagnostics``
gathers live state and delegates here so health-check rules stay in one module.
"""
from __future__ import annotations

from typing import Optional

from app.services.collab_types import Diagnostic, PeerInfo, _missing_deps

CLOCK_SKEW_THRESHOLD_MS = 5_000


def build_diagnostics(
    *,
    group_code: str,
    discovery_error: str,
    peers: list[PeerInfo],
    port: Optional[int],
) -> list[Diagnostic]:
    """Run all synchronous health checks and return findings."""
    diags: list[Diagnostic] = []
    diags += diag_deps()
    diags += diag_config(group_code)
    diags += diag_mdns(discovery_error)
    diags += diag_group_mismatch(group_code, peers)
    diags += diag_clock_skew(peers)
    diags += diag_reachability(peers, port)
    if not diags:
        diags = [Diagnostic("ok", "ok", "协作正常", "未发现配置问题。")]
    return diags


def overall_health(diags: list[Diagnostic]) -> str:
    """Roll up to a traffic-light colour: red > yellow > green."""
    if any(d.level == "error" for d in diags):
        return "red"
    if any(d.level == "warn" for d in diags):
        return "yellow"
    return "green"


def diag_deps() -> list[Diagnostic]:
    missing = _missing_deps()
    if missing:
        return [Diagnostic(
            "deps_missing", "error", "缺少协作组件",
            f"未安装:{', '.join(missing)}。协作功能无法运行。",
            f"运行 pip install {' '.join(missing)}")]
    return []


def diag_config(group_code: str) -> list[Diagnostic]:
    if not group_code:
        return [Diagnostic(
            "config_no_group", "warn", "未配对团队",
            "未填写团队永久码,不会与任何设备同步标本编号。",
            "在协作中心点击「加入团队」,或在「设置 → 协作」填写相同团队永久码。")]
    return []


def diag_group_mismatch(group_code: str, peers: list[PeerInfo]) -> list[Diagnostic]:
    if not group_code:
        return []
    others = sorted({
        p.group_code for p in peers
        if p.group_code and p.group_code != group_code
    })
    if others:
        return [Diagnostic(
            "group_mismatch", "warn", "发现团队永久码不同的设备",
            f"同网段设备的团队永久码为:{', '.join(others)};你的团队永久码是 {group_code}。"
            "团队永久码不同的设备不会互相同步,可能各自占用了相同编号。",
            "若你们应在同一团队,请核对并统一团队永久码。",
            action="adopt_group")]
    return []


def diag_clock_skew(peers: list[PeerInfo]) -> list[Diagnostic]:
    bad = [p for p in peers
           if p.clock_skew_ms is not None
           and abs(p.clock_skew_ms) > CLOCK_SKEW_THRESHOLD_MS]
    if bad:
        worst = max(abs(p.clock_skew_ms) for p in bad)  # type: ignore[arg-type]
        return [Diagnostic(
            "clock_skew", "warn", "设备时间不一致",
            f"与队友的系统时间相差约 {round(worst / 1000)} 秒。"
            "同步按修改时间先后合并,时间不准会导致较新的修改被覆盖。",
            "请校准各设备的系统时间(建议开启「自动设置时间」)。")]
    return []


def diag_mdns(discovery_error: str) -> list[Diagnostic]:
    if discovery_error:
        return [Diagnostic(
            "mdns_unavailable", "warn", "局域网自动发现不可用",
            f"无法启动自动发现({discovery_error})。",
            "改用「搜索局域网」或「配对码」连接队友。")]
    return []


def diag_reachability(peers: list[PeerInfo], port: Optional[int]) -> list[Diagnostic]:
    blocked = [p for p in peers
               if p.reachable is True and p.reachback_ok is False]
    if blocked:
        listen_port = port or 5050
        return [Diagnostic(
            "firewall_blocked", "error", "队友连不到你",
            f"你能看到队友,但他们无法连回你(端口 {listen_port})。"
            "很可能是本机防火墙挡住了入站连接。",
            f"放行端口 {listen_port} 的入站连接。",
            action="open_firewall")]
    return []
