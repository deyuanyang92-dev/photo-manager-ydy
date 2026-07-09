"""collab_api.py — FastAPI HTTP endpoints exposed by each collaboration node.

Endpoint map (see collab_service module docstring for the full protocol):
    GET  /api/node/health / /api/node/info / POST /api/node/reachback
    GET  /api/collab/tasks            POST /api/collab/tasks/create|update-status|release
    POST /api/collab/pairing/request|accept
    POST /api/collab/photo-index
    GET  /api/collab/files/manifest|download
    GET/POST /api/collab/specimens[/push]
    GET/POST /api/collab/activity
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Callable, Optional

from app.models.activity_log import ActivityEntry, ActivityLog
from app.services.collab_store import TaskStore
from app.services.collab_types import TaskStatus, get_httpx

logger = logging.getLogger(__name__)


def _is_private_lan_host(host: str) -> bool:
    """LAN trust boundary: loopback or RFC1918 / link-local address (module-level

    so it is importable for tests).  Used to gate every endpoint that returns
    project data (specimens, media manifest, file download, activity).
    groupCode alone is not enough — a 6-char code over cleartext HTTP on
    0.0.0.0 can be guessed/sniffed on a shared network; requiring the caller
    to be inside the LAN closes that.
    """
    host = str(host or "")
    if host in ("127.0.0.1", "::1"):
        return True
    import ipaddress
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        return False
    return addr.is_private


def _build_fastapi_app(store: TaskStore, node_info_fn: Callable[[], dict],
                       activity_log: Optional[ActivityLog] = None,
                       file_manifest_fn: Optional[Callable[[Optional[list[str]]], dict]] = None,
                       file_path_fn: Optional[Callable[[str], Path]] = None,
                       pairing_request_fn: Optional[Callable[[str, str, str], None]] = None,
                       pairing_accept_fn: Optional[Callable[[str, str], None]] = None,
                       specimen_provider_fn: Optional[Callable[[Optional[str]], list]] = None,
                       specimen_writer_fn: Optional[Callable[[list], int]] = None,
                       photo_index_fn: Optional[Callable[[str, str, int, str], None]] = None) -> Any:
    """Build and return the FastAPI app.  Imported lazily to avoid startup cost.

    The fastapi names are bound into module globals (``global`` below) so that
    the nested endpoint functions' ``request: Request`` annotations resolve via
    ``typing.get_type_hints`` (which reads the function's ``__globals__``).
    A purely function-local import leaves them unresolvable and FastAPI then
    mis-reads ``request`` as a query parameter → every POST 422s.
    """
    global FastAPI, HTTPException, Request, JSONResponse, FileResponse
    try:
        from fastapi import FastAPI, HTTPException, Request
        from fastapi.responses import FileResponse, JSONResponse
    except ImportError as exc:
        raise ImportError("fastapi is required for CollabService") from exc

    app = FastAPI(title="Specimen Collab Node", version="1.0.0")

    def _local_group_code() -> str:
        return str(node_info_fn().get("groupCode", "") or "")

    def _local_project_id() -> str:
        return str(node_info_fn().get("projectId", "") or "")

    def _require_group(group_code: str) -> None:
        local_group = _local_group_code()
        if not local_group or str(group_code or "") != local_group:
            raise HTTPException(status_code=403, detail="collaboration group mismatch")

    def _require_project(project_id: str) -> None:
        local_project = _local_project_id()
        if not local_project or str(project_id or "") != local_project:
            raise HTTPException(status_code=403, detail="project identity mismatch")

    def _require_group_project(group_code: str, project_id: str) -> None:
        _require_group(group_code)
        _require_project(project_id)

    def _require_lan(request: "Request") -> None:
        """Reject callers whose source IP is outside the LAN."""
        client_host = request.client.host if request.client else ""
        if not _is_private_lan_host(client_host):
            raise HTTPException(status_code=403, detail="sender not in LAN")

    @app.get("/api/node/health")
    async def health() -> dict:
        return {"ok": True}

    @app.get("/api/node/info")
    async def node_info() -> dict:
        return node_info_fn()

    @app.get("/api/collab/tasks")
    async def list_tasks(groupCode: str = "") -> list:
        _require_group(groupCode)
        return [t.to_dict() for t in store.list_tasks()]

    @app.post("/api/collab/tasks/create")
    async def create_task(request: Request) -> JSONResponse:
        body = await request.json()
        uid = body.get("uid")
        if not uid:
            raise HTTPException(status_code=400, detail="uid required")
        # Group guard: only accept claims from our own collaboration group.
        # Empty local group = not participating → reject everyone.
        _require_group(body.get("groupCode", ""))
        try:
            task = store.create(
                uid=uid,
                assignee=body.get("assignee"),
                device_id=body.get("deviceId"),
                project_name=body.get("projectName"),
            )
        except ValueError as exc:
            # Local 409 — UID exists on this node
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return JSONResponse(content=task.to_dict(), status_code=201)

    @app.post("/api/collab/tasks/update-status")
    async def update_task_status(request: Request) -> dict:
        body = await request.json()
        uid = body.get("uid")
        status_raw = body.get("status")
        if not uid or not status_raw:
            raise HTTPException(status_code=400, detail="uid and status required")
        _require_group(body.get("groupCode", ""))
        try:
            to_status = TaskStatus(status_raw)
            force = bool(body.get("force"))
            task = store.update_status(
                uid, to_status, assignee=body.get("assignee"), force=force,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return task.to_dict()

    @app.post("/api/node/reachback")
    async def reachback(request: Request) -> dict:
        """Test whether the *caller* is reachable from this node's side.

        The caller passes its own {ip, port}; we try to GET its /api/node/health
        and report back.  Lets a peer detect a one-way firewall block (it can
        reach us, but we cannot reach it).
        """
        body = await request.json()
        ip = body.get("ip")
        port = body.get("port")
        if not ip or not port:
            raise HTTPException(status_code=400, detail="ip and port required")
        reachable = False
        try:
            httpx = get_httpx()
            r = httpx.get(f"http://{ip}:{port}/api/node/health", timeout=3.0)
            reachable = r.status_code == 200
        except Exception:  # noqa: BLE001
            reachable = False
        return {"reachable": reachable}

    @app.post("/api/collab/tasks/release")
    async def release_task(request: Request) -> dict:
        """Release (delete) a UID claim so it becomes reclaimable by anyone."""
        body = await request.json()
        uid = body.get("uid")
        if not uid:
            raise HTTPException(status_code=400, detail="uid required")
        _require_group(body.get("groupCode", ""))
        store.delete(uid)
        return {"ok": True, "uid": uid}

    # ── Pairing (zero-config peer connection) ────────────────────────────────

    @app.post("/api/collab/pairing/request")
    async def pairing_request(request: Request) -> dict:
        """Incoming pairing request from another device.

        Body: {fromIp, fromHostname, groupCode}
        The receiving app shows a confirmation dialog; if accepted the devices
        adopt a shared group code and begin syncing.
        """
        body = await request.json()
        from_ip = body.get("fromIp", "")
        from_hostname = body.get("fromHostname", "未知设备")
        their_code = body.get("groupCode", "")
        if pairing_request_fn is not None:
            pairing_request_fn(from_ip, from_hostname, their_code)
        return {"ok": True, "status": "pending"}

    @app.post("/api/collab/pairing/accept")
    async def pairing_accept(request: Request) -> dict:
        """Notification that the remote device has accepted our pairing request."""
        body = await request.json()
        from_ip = body.get("fromIp", "")
        from_hostname = body.get("fromHostname", "未知设备")
        adopted_code = body.get("groupCode", "")
        if adopted_code:
            node_info_fn()  # ensure state is current
        if pairing_accept_fn is not None:
            pairing_accept_fn(from_ip, from_hostname)
        return {"ok": True, "groupCode": adopted_code}

    @app.post("/api/collab/photo-index")
    async def receive_photo_index(request: Request) -> dict:
        """Receive photo-index report from a peer after helicon/archive completion.

        Mirrors web collabPostPhotoIndex — peers call this to inform us that
        their specimen has jpg/tiff/zip files ready.  We acknowledge and let
        the UI subscribe to signals for richer handling.
        """
        body = await request.json()
        _require_group_project(body.get("groupCode", ""), body.get("projectId", ""))
        uid = body.get("uid", "")
        kind = body.get("kind", "")
        count = int(body.get("count", 0))
        device_id = str(body.get("deviceId", "") or "")
        if photo_index_fn is not None and uid and kind:
            photo_index_fn(uid, kind, count, device_id)
        return {"ok": True, "uid": uid, "kind": kind, "count": count}

    @app.get("/api/collab/files/manifest")
    async def file_manifest(request: Request, groupCode: str = "", projectId: str = "", uids: str = "") -> dict:
        """Return project media manifest for selected UIDs or the whole project."""
        _require_lan(request)
        _require_group_project(groupCode, projectId)
        if file_manifest_fn is None:
            return {"files": []}
        uid_list = [u.strip() for u in str(uids or "").split(",") if u.strip()]
        try:
            return file_manifest_fn(uid_list or None)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @app.get("/api/collab/files/download")
    async def download_file(request: Request, path: str = "", groupCode: str = "", projectId: str = "") -> Any:
        """Download one project-relative media file."""
        _require_lan(request)
        _require_group_project(groupCode, projectId)
        if file_path_fn is None:
            raise HTTPException(status_code=404, detail="file sync unavailable")
        try:
            resolved = file_path_fn(path)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        if not resolved.is_file():
            raise HTTPException(status_code=404, detail="file not found")
        return FileResponse(str(resolved), filename=resolved.name)

    @app.post("/api/collab/specimens/push")
    async def receive_specimen_push(request: Request) -> dict:
        """Accept specimen records pushed from a peer; writes to local DB."""
        body = await request.json()
        _require_group_project(body.get("groupCode", ""), body.get("projectId", ""))
        specimens = body.get("specimens", [])
        if not isinstance(specimens, list):
            raise HTTPException(status_code=400, detail="specimens must be a list")
        written = 0
        if specimen_writer_fn is not None and specimens:
            written = specimen_writer_fn(specimens)
        logger.debug("collab: received %d specimen(s) from peer", written)
        return {"ok": True, "written": written}

    @app.get("/api/collab/specimens")
    async def list_specimens(groupCode: str = "", projectId: str = "") -> list:
        """Return local specimen records for peer sync."""
        _require_group_project(groupCode, projectId)
        if specimen_provider_fn is None:
            return []
        return specimen_provider_fn(None)

    @app.get("/api/collab/activity")
    async def get_activity(groupCode: str = "") -> list:
        """Return recent activity entries from this node."""
        _require_group(groupCode)
        if activity_log is None:
            return []
        return activity_log.to_dicts()

    @app.post("/api/collab/activity")
    async def receive_activity(request: Request) -> dict:
        """Receive an activity entry pushed from a peer (best-effort).

        Double guard: LAN IP check (defense-in-depth) + matching groupCode.
        """
        # §7 keep-old: inline LAN check preserved for cross-reference; now
        # shared via _require_lan (same boundary, used by file endpoints too).
        # client_host = request.client.host if request.client else ""
        # import ipaddress
        # try:
        #     addr = ipaddress.ip_address(client_host)
        #     if not addr.is_private and client_host not in ("127.0.0.1", "::1"):
        #         raise HTTPException(status_code=403, detail="sender not in LAN")
        # except ValueError:
        #     raise HTTPException(status_code=403, detail="invalid sender address")
        _require_lan(request)
        if activity_log is None:
            return {"ok": True}
        body = await request.json()
        _require_group(body.get("groupCode", ""))
        entry = ActivityEntry.from_dict(body)
        activity_log.append(entry)
        return {"ok": True}

    return app
