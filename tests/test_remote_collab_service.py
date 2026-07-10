from __future__ import annotations


def test_remote_collab_service_reports_configuration() -> None:
    from app.services.remote_collab_service import RemoteCollabService

    assert not RemoteCollabService("", "acct", "token").is_configured()
    assert not RemoteCollabService("https://relay.example", "", "token").is_configured()
    assert RemoteCollabService("https://relay.example", "acct", "token").is_configured()


def test_remote_collab_create_invite_posts_to_relay(monkeypatch) -> None:
    from app.services.remote_collab_service import RemoteCollabService

    calls = {}

    class Response:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {
                "inviteCode": "REMOTE-123",
                "expiresAt": "2026-07-08T12:00:00Z",
                "permission": "read_only",
                "scope": "Project A",
            }

    def fake_post(url, *, headers, json, timeout):
        calls["url"] = url
        calls["headers"] = headers
        calls["json"] = json
        calls["timeout"] = timeout
        return Response()

    import httpx
    monkeypatch.setattr(httpx, "post", fake_post)

    svc = RemoteCollabService("https://relay.example/", "acct-1", "secret", timeout=3)
    invite = svc.create_invite(project_name="Project A", ttl_seconds=600)

    assert calls["url"] == "https://relay.example/api/remote-collab/invites"
    assert calls["headers"]["Authorization"] == "Bearer secret"
    assert calls["headers"]["X-Remote-Account"] == "acct-1"
    assert calls["json"]["projectName"] == "Project A"
    assert calls["json"]["ttlSeconds"] == 600
    assert invite.code == "REMOTE-123"
    assert invite.expires_at == "2026-07-08T12:00:00Z"


def test_remote_collab_join_invite_posts_to_relay(monkeypatch) -> None:
    from app.services.remote_collab_service import RemoteCollabService

    calls = {}

    class Response:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {
                "status": "requested",
                "sessionId": "S1",
                "peerName": "Alice",
                "message": "等待对方确认",
            }

    def fake_post(url, *, headers, json, timeout):
        calls["url"] = url
        calls["json"] = json
        return Response()

    import httpx
    monkeypatch.setattr(httpx, "post", fake_post)

    svc = RemoteCollabService("https://relay.example", "acct-1", "secret")
    result = svc.join_invite("REMOTE-123", project_name="Project B")

    assert calls["url"] == "https://relay.example/api/remote-collab/invites/join"
    assert calls["json"]["inviteCode"] == "REMOTE-123"
    assert calls["json"]["projectName"] == "Project B"
    assert result.status == "requested"
    assert result.session_id == "S1"
    assert result.peer_name == "Alice"
