"""Remote collaboration relay client.

This module is intentionally separate from ``collab_service``.  The existing
collaboration service is LAN P2P; remote collaboration must go through a relay
or account-backed service and must not reuse local IP pairing codes.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RemoteInvite:
    code: str
    expires_at: str = ""
    permission: str = "read_only"
    scope: str = ""


@dataclass(frozen=True)
class RemoteJoinResult:
    status: str
    session_id: str = ""
    peer_name: str = ""
    message: str = ""


class RemoteCollabService:
    """Small HTTP client for an account/relay based remote collaboration service."""

    def __init__(
        self,
        relay_url: str,
        account_id: str,
        device_token: str,
        *,
        timeout: float = 8.0,
    ) -> None:
        self.relay_url = (relay_url or "").rstrip("/")
        self.account_id = (account_id or "").strip()
        self.device_token = (device_token or "").strip()
        self.timeout = timeout

    def is_configured(self) -> bool:
        return bool(self.relay_url and self.account_id and self.device_token)

    def is_available(self) -> bool:
        return self.is_configured()

    def _url(self, path: str) -> str:
        return f"{self.relay_url}/{path.lstrip('/')}"

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.device_token}",
            "X-Remote-Account": self.account_id,
        }

    def create_invite(
        self,
        *,
        project_name: str = "",
        permission: str = "read_only",
        ttl_seconds: int = 600,
    ) -> RemoteInvite:
        if not self.is_configured():
            raise RuntimeError("远程中继服务未配置")
        import httpx

        resp = httpx.post(
            self._url("/api/remote-collab/invites"),
            headers=self._headers(),
            json={
                "projectName": project_name,
                "permission": permission,
                "ttlSeconds": int(ttl_seconds),
            },
            timeout=self.timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        code = str(data.get("inviteCode") or data.get("code") or "").strip()
        if not code:
            raise RuntimeError("远程中继没有返回邀请码")
        return RemoteInvite(
            code=code,
            expires_at=str(data.get("expiresAt") or ""),
            permission=str(data.get("permission") or permission),
            scope=str(data.get("scope") or project_name or ""),
        )

    def join_invite(self, code: str, *, project_name: str = "") -> RemoteJoinResult:
        if not self.is_configured():
            raise RuntimeError("远程中继服务未配置")
        invite_code = (code or "").strip()
        if not invite_code:
            raise ValueError("远程邀请码为空")
        import httpx

        resp = httpx.post(
            self._url("/api/remote-collab/invites/join"),
            headers=self._headers(),
            json={
                "inviteCode": invite_code,
                "projectName": project_name,
            },
            timeout=self.timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        return RemoteJoinResult(
            status=str(data.get("status") or "requested"),
            session_id=str(data.get("sessionId") or ""),
            peer_name=str(data.get("peerName") or ""),
            message=str(data.get("message") or ""),
        )
