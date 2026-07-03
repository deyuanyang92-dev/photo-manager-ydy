#!/usr/bin/env python3
"""End-to-end simulation for LAN collaboration file sync.

Creates two temporary project directories, exposes the peer project through the
same FastAPI file endpoints used by the app, then pulls files into the local
project and verifies smart skip, conflict, and overwrite behavior.
"""
from __future__ import annotations

import json
import os
import shutil
import socket
import sqlite3
import sys
import tempfile
import threading
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
os.environ.setdefault("NO_PROXY", "127.0.0.1,localhost")

from app.db import db_manager
from app.services.collab_file_sync import (
    manifest_payload,
    resolve_project_relative,
    sha256_file,
    sync_from_peer,
)
from app.services.collab_service import TaskStore, _build_fastapi_app


GROUP_CODE = "SIM-GROUP"
UID_A = "GXFCG-BLW-BZC003-R-1-20260618"
UID_B = "GXFCG-BLW-SC001-D79-20260618"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _make_db(project: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    db_manager.ensure_schema(conn)
    conn.executemany(
        "INSERT INTO specimens (uid, owner_project_dir) VALUES (?, ?)",
        [(UID_A, str(project)), (UID_B, str(project))],
    )
    conn.commit()
    return conn


def _write_peer_files(project: Path) -> dict[str, bytes]:
    incoming = project / "incoming-jpg"
    results = project / "results"
    incoming.mkdir(parents=True)
    results.mkdir()

    files = {
        f"incoming-jpg/{UID_A}_view1.jpg": b"jpg-a-view1",
        f"results/{UID_A}-1-20260703.tif": b"tiff-a-original",
        f"results/{UID_A}-1-20260703.zip": b"zip-a-original",
        f"results/{UID_B}-1-20260703.tif": b"tiff-b-project-wide",
    }
    for rel, data in files.items():
        path = project / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    return files


def _start_peer_server(peer_project: Path, db: sqlite3.Connection) -> tuple[object, threading.Thread, str]:
    try:
        import httpx
        import uvicorn
    except ImportError as exc:
        raise RuntimeError("httpx and uvicorn are required") from exc

    port = _free_port()

    def node_info() -> dict:
        return {
            "hostname": "sim-peer",
            "projectName": "peer-project",
            "groupCode": GROUP_CODE,
            "serverTime": time.time(),
            "lanIp": "127.0.0.1",
            "port": port,
        }

    def file_manifest(uids: list[str] | None) -> dict:
        return manifest_payload(str(peer_project), db=db, uids=uids, device_id="sim-peer")

    def file_path(relative_path: str) -> Path:
        return resolve_project_relative(peer_project, relative_path)

    app = _build_fastapi_app(
        TaskStore(),
        node_info,
        file_manifest_fn=file_manifest,
        file_path_fn=file_path,
    )
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    errors: list[str] = []

    def run_server() -> None:
        try:
            server.run()
        except BaseException as exc:  # noqa: BLE001
            errors.append(repr(exc))

    thread = threading.Thread(target=run_server, name="sim-collab-peer", daemon=True)
    thread.start()

    base_url = f"http://127.0.0.1:{port}"
    deadline = time.time() + 10
    last_error = ""
    while time.time() < deadline:
        if errors:
            raise RuntimeError(f"peer server failed: {errors[-1]}")
        if not thread.is_alive():
            raise RuntimeError("peer server thread exited before startup")
        try:
            if httpx.get(f"{base_url}/api/node/health", timeout=0.5, trust_env=False).status_code == 200:
                return server, thread, base_url
        except Exception as exc:  # noqa: BLE001
            last_error = repr(exc)
            time.sleep(0.05)
    server.should_exit = True
    thread.join(timeout=3)
    raise RuntimeError(f"peer server did not start; last health error: {last_error}")


def _assert_file(project: Path, rel: str, expected: bytes) -> None:
    path = project / rel
    assert path.read_bytes() == expected, rel


def main() -> int:
    temp_root = Path(tempfile.mkdtemp(prefix="collab-sync-sim-"))
    peer_db: sqlite3.Connection | None = None
    server = None
    thread: threading.Thread | None = None
    try:
        local_project = temp_root / "computer-a-local"
        peer_project = temp_root / "computer-b-peer"
        local_project.mkdir()
        peer_project.mkdir()

        expected = _write_peer_files(peer_project)
        peer_db = _make_db(peer_project)
        server, thread, base_url = _start_peer_server(peer_project, peer_db)

        selected = sync_from_peer(
            project_dir=str(local_project),
            peer_base_url=base_url,
            group_code=GROUP_CODE,
            uids=[UID_A],
            mode="smart",
            max_workers=4,
        )
        assert selected.downloaded == 3, selected
        assert selected.failed == 0 and selected.conflicts == 0, selected
        for rel, data in expected.items():
            if UID_A in rel:
                _assert_file(local_project, rel, data)

        second = sync_from_peer(
            project_dir=str(local_project),
            peer_base_url=base_url,
            group_code=GROUP_CODE,
            uids=[UID_A],
            mode="smart",
            max_workers=4,
        )
        assert second.downloaded == 0 and second.skipped == 3, second

        conflict_rel = f"results/{UID_A}-1-20260703.tif"
        (local_project / conflict_rel).write_bytes(b"local-conflict")
        conflict = sync_from_peer(
            project_dir=str(local_project),
            peer_base_url=base_url,
            group_code=GROUP_CODE,
            uids=[UID_A],
            mode="smart",
            max_workers=4,
        )
        assert conflict.conflicts == 1 and conflict.downloaded == 0, conflict
        _assert_file(local_project, conflict_rel, b"local-conflict")

        overwrite = sync_from_peer(
            project_dir=str(local_project),
            peer_base_url=base_url,
            group_code=GROUP_CODE,
            uids=[UID_A],
            mode="overwrite",
            max_workers=4,
        )
        assert overwrite.downloaded == 1 and overwrite.skipped == 2, overwrite
        _assert_file(local_project, conflict_rel, expected[conflict_rel])
        backups = list((local_project / "_data" / "sync-conflicts").rglob("*.tif"))
        assert backups and any(path.read_bytes() == b"local-conflict" for path in backups)

        project_wide = sync_from_peer(
            project_dir=str(local_project),
            peer_base_url=base_url,
            group_code=GROUP_CODE,
            mode="smart",
            max_workers=4,
        )
        assert project_wide.downloaded == 1, project_wide
        _assert_file(local_project, f"results/{UID_B}-1-20260703.tif", expected[f"results/{UID_B}-1-20260703.tif"])

        manifest_hash = sha256_file(local_project / conflict_rel)
        assert manifest_hash == sha256_file(peer_project / conflict_rel)

        summary = {
            "tempRoot": str(temp_root),
            "selectedDownloaded": selected.downloaded,
            "repeatSkipped": second.skipped,
            "smartConflicts": conflict.conflicts,
            "overwriteDownloaded": overwrite.downloaded,
            "projectDownloaded": project_wide.downloaded,
            "backupCount": len(backups),
        }
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0
    finally:
        if server is not None:
            server.should_exit = True
        if thread is not None:
            thread.join(timeout=3)
        if peer_db is not None:
            peer_db.close()
        shutil.rmtree(temp_root, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
