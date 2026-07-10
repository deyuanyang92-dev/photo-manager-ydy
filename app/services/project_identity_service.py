"""Stable per-project identity used by collaboration file sync."""
from __future__ import annotations

import base64
import json
import re
import uuid
from datetime import datetime, timezone

from app.services.project_settings_service import (
    load_setting_if_present,
    save_setting,
)

PROJECT_IDENTITY_KEY = "project_identity"
PROJECT_SYNC_CODE_PREFIX = "SPP-PROJECT:"
_PROJECT_ID_RE = re.compile(r"^[0-9a-fA-F]{32}$")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def ensure_project_identity(db, *, project_name: str = "") -> str:
    """Return this project's stable ID, creating one if missing.

    The ID lives inside the project database, so moving the project to another
    drive keeps the identity. Independently-created projects get different IDs
    even when their folder names are identical.
    """
    data = load_setting_if_present(db, PROJECT_IDENTITY_KEY) or {}
    project_id = str(data.get("projectId") or data.get("project_id") or "").strip()
    if project_id:
        return project_id

    project_id = uuid.uuid4().hex
    save_setting(
        db,
        PROJECT_IDENTITY_KEY,
        {
            "projectId": project_id,
            "projectName": str(project_name or ""),
            "createdAt": _now_iso(),
        },
    )
    return project_id


def read_project_identity(db) -> str:
    """Return the project ID if present, otherwise an empty string."""
    data = load_setting_if_present(db, PROJECT_IDENTITY_KEY) or {}
    return str(data.get("projectId") or data.get("project_id") or "").strip()


def project_sync_code(project_id: str, *, project_name: str = "") -> str:
    """Return a copy/paste code that identifies one logical project.

    The code contains no path information. Two computers can keep the project
    in different folders; sharing this code only declares that the two folders
    are the same logical shooting project for media sync.
    """
    project_id = _normalise_project_id(project_id)
    payload = {
        "v": 1,
        "projectId": project_id,
        "projectName": str(project_name or ""),
    }
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return PROJECT_SYNC_CODE_PREFIX + base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def parse_project_sync_code(raw: str) -> dict[str, str]:
    """Parse a project sync code or raw 32-char project ID."""
    text = str(raw or "").strip()
    if not text:
        raise ValueError("empty project sync code")
    if _PROJECT_ID_RE.fullmatch(text):
        return {"projectId": text.lower(), "projectName": ""}
    if not text.startswith(PROJECT_SYNC_CODE_PREFIX):
        raise ValueError("invalid project sync code")
    encoded = text[len(PROJECT_SYNC_CODE_PREFIX):].strip()
    if not encoded:
        raise ValueError("invalid project sync code")
    padded = encoded + "=" * (-len(encoded) % 4)
    try:
        data = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8"))
    except Exception as exc:
        raise ValueError("invalid project sync code") from exc
    if not isinstance(data, dict) or data.get("v") != 1:
        raise ValueError("unsupported project sync code")
    project_id = _normalise_project_id(str(data.get("projectId") or ""))
    return {
        "projectId": project_id,
        "projectName": str(data.get("projectName") or ""),
    }


def set_project_identity(
    db,
    project_id: str,
    *,
    project_name: str = "",
    previous_project_id: str = "",
) -> str:
    """Persist an explicit project identity after user confirmation."""
    project_id = _normalise_project_id(project_id)
    data = {
        "projectId": project_id,
        "projectName": str(project_name or ""),
        "updatedAt": _now_iso(),
    }
    if previous_project_id and previous_project_id != project_id:
        data["previousProjectId"] = previous_project_id
    save_setting(db, PROJECT_IDENTITY_KEY, data)
    return project_id


def reset_project_identity(
    db,
    *,
    project_name: str = "",
    previous_project_id: str = "",
) -> str:
    """Assign a fresh project identity, breaking any previous project-code link."""
    project_id = uuid.uuid4().hex
    data = {
        "projectId": project_id,
        "projectName": str(project_name or ""),
        "updatedAt": _now_iso(),
        "resetAt": _now_iso(),
    }
    if previous_project_id and previous_project_id != project_id:
        data["previousProjectId"] = previous_project_id
    save_setting(db, PROJECT_IDENTITY_KEY, data)
    return project_id


def _normalise_project_id(project_id: str) -> str:
    value = str(project_id or "").strip().lower()
    if not _PROJECT_ID_RE.fullmatch(value):
        raise ValueError("project ID must be a 32-character hex string")
    return value
