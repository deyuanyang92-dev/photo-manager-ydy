"""activation_service.py — Specimen activation state management + event log.

Mirrors server.js:3844-3914 (/api/specimen-log/activate and /assign) and
monitor-service.js:50-79 (buildAttribution activations/assignments).

State is persisted in two places:
  1. DB tasks table: is_active / activated_at (fast SQL query, mirrors §3.2)
  2. <projectDir>/_data/state.json events[] array: append-only event log
     (portable, used by attribution time-window algorithm §3.5 P3)

Oracle references:
  server.js:3844-3888   POST /api/specimen-log/activate
  server.js:3890-3914   POST /api/specimen-log/assign
  server.js:3151-3158   specimenLogRead / specimenLogAppend (state.json events)
  monitor-service.js:50-79  buildAttribution → activations list
"""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from app.services.monitor_service import AttributionCtx


# ── File paths ─────────────────────────────────────────────────────────────────

def _state_json_path(project_dir: str) -> str:
    """Return the path to <projectDir>/_data/state.json.

    Oracle: server.js:3141 projectStatePath.
    """
    return str(Path(project_dir) / "_data" / "state.json")


def _read_state(project_dir: str) -> dict:
    """Read _data/state.json; return {} on missing/corrupt."""
    p = _state_json_path(project_dir)
    try:
        if os.path.isfile(p):
            with open(p, encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return {}


def _write_state(project_dir: str, state: dict) -> None:
    """Write _data/state.json atomically (via tmp-rename).

    Oracle: server.js:3146-3149 projectStateWrite + atomicWriteJson.
    """
    p = _state_json_path(project_dir)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    os.replace(tmp, p)


def _append_event(project_dir: str, entry: dict) -> None:
    """Append one event dict to state.json[events].

    Oracle: server.js:3154-3158 specimenLogAppend.
    Append-only: existing events are never modified.
    """
    state = _read_state(project_dir)
    events: list = list(state.get("events") or [])
    events.append(entry)
    state["events"] = events
    _write_state(project_dir, state)


# ── DB helpers ─────────────────────────────────────────────────────────────────

def _ensure_tasks_table(db: sqlite3.Connection) -> None:
    """Create tasks table if absent (subset of workbench_view test schema)."""
    db.execute("""
        CREATE TABLE IF NOT EXISTS tasks (
            uid TEXT PRIMARY KEY,
            is_active INTEGER DEFAULT 0,
            activated_at TEXT,
            last_organized_at TEXT,
            next_result_sequence_hint INTEGER,
            raw_json TEXT
        )
    """)
    db.commit()


def _set_all_inactive(db: sqlite3.Connection, except_uid: Optional[str] = None) -> Optional[str]:
    """Set all tasks rows to is_active=0 except *except_uid*.

    Returns the UID of the previously active specimen (if any), or None.

    Oracle: server.js:3864-3869 — global mutual exclusion: only one active at a time.
    """
    _ensure_tasks_table(db)
    # Find old active uid
    row = db.execute(
        "SELECT uid FROM tasks WHERE is_active = 1 LIMIT 1"
    ).fetchone()
    previous_uid: Optional[str] = row[0] if row else None

    if except_uid is not None:
        db.execute(
            "UPDATE tasks SET is_active = 0 WHERE uid != ?", (except_uid,)
        )
    else:
        db.execute("UPDATE tasks SET is_active = 0")
    db.commit()
    return previous_uid


# ── Public API ─────────────────────────────────────────────────────────────────

def activate(project_dir: str, db: sqlite3.Connection, uid: str) -> dict:
    """Activate *uid* as the current specimen.

    - Global mutual exclusion: all other specimens are deactivated first.
    - Writes tasks.is_active=1 + activated_at in the DB.
    - Appends a ``{source: "activation"}`` event to state.json.

    Oracle: server.js:3844-3888.

    Returns
    -------
    dict with keys: ok, previous_uid, new_uid, timestamp
    """
    if not uid:
        raise ValueError("uid 不能为空")

    now = _iso_now()
    _ensure_tasks_table(db)

    # Deactivate all others (mutual exclusion)
    previous_uid = _set_all_inactive(db, except_uid=uid)
    if previous_uid == uid:
        previous_uid = None  # was already active

    # Upsert the target row with is_active=1
    db.execute(
        """
        INSERT INTO tasks (uid, is_active, activated_at)
        VALUES (?, 1, ?)
        ON CONFLICT(uid) DO UPDATE SET
            is_active = 1,
            activated_at = excluded.activated_at
        """,
        (uid, now),
    )
    db.commit()

    # Append activation event (Oracle: server.js:3876-3881)
    _append_event(project_dir, {
        "specimenUniqueId": uid,
        "eventAt": now,
        "source": "activation",
    })

    return {"ok": True, "previous_uid": previous_uid, "new_uid": uid, "timestamp": now}


def deactivate(project_dir: str, db: sqlite3.Connection, uid: str) -> dict:
    """Deactivate *uid*.

    Sets tasks.is_active=0; does NOT write a log event
    (Oracle: server.js:3857-3861 — deactivate only clears isActive, no log).

    Returns
    -------
    dict with keys: ok, previous_uid, new_uid, timestamp
    """
    if not uid:
        raise ValueError("uid 不能为空")

    now = _iso_now()
    _ensure_tasks_table(db)
    db.execute(
        "UPDATE tasks SET is_active = 0 WHERE uid = ?", (uid,)
    )
    db.commit()

    return {"ok": True, "previous_uid": uid, "new_uid": None, "timestamp": now}


def manual_assign(project_dir: str, uid: str, jpg_paths: list[str]) -> dict:
    """Record a manual assignment: *jpg_paths* → *uid*.

    Appends a ``{source: "manual-assign"}`` event to state.json.
    These events feed the P2 manual-assign priority in attribution.

    Oracle: server.js:3891-3913.

    Returns
    -------
    dict with keys: ok, assigned, count
    """
    if not uid:
        raise ValueError("uid 不能为空")
    if not jpg_paths:
        return {"ok": True, "assigned": True, "count": 0}

    now = _iso_now()
    resolved_paths = [str(Path(p).resolve()) for p in jpg_paths]

    _append_event(project_dir, {
        "specimenUniqueId": uid,
        "eventAt": now,
        "source": "manual-assign",
        "jpgPaths": resolved_paths,
    })

    return {"ok": True, "assigned": True, "count": len(resolved_paths)}


def read_activations(project_dir: str) -> AttributionCtx:
    """Build an AttributionCtx from the project's event log.

    Reads state.json events and builds:
      - activations: sorted ascending by eventAt, source=="activation"
      - assign_to_uid: path → uid from manual-assign events (last-write wins)
      - explicit_unassigns, path_to_uid: left empty (those come from grouping_service)

    Oracle: monitor-service.js:50-79 buildAttribution (activations + assignToUid).

    Note: explicit_unassigns and path_to_uid are NOT populated here — they
    come from grouping_service.get_explicit_unassigns() and the grouping DB table
    respectively.  The caller (workbench_view._refresh_monitor) merges both.
    """
    state = _read_state(project_dir)
    events: list = state.get("events") or []

    # Build manual-assign map (last assignment for a path wins)
    assign_to_uid: dict[str, str] = {}
    for e in events:
        if e.get("source") != "manual-assign":
            continue
        uid = e.get("specimenUniqueId", "")
        for p in e.get("jpgPaths") or []:
            assign_to_uid[str(Path(p).resolve())] = uid

    # Build sorted activation list
    activations = sorted(
        [e for e in events if e.get("source") == "activation"],
        key=lambda e: e.get("eventAt", ""),
    )

    return AttributionCtx(
        activations=activations,
        assign_to_uid=assign_to_uid,
    )


def get_active_uid(db: sqlite3.Connection) -> Optional[str]:
    """Return the currently active specimen UID, or None.

    Queries the tasks table; does not touch state.json.
    """
    try:
        _ensure_tasks_table(db)
        row = db.execute(
            "SELECT uid FROM tasks WHERE is_active = 1 LIMIT 1"
        ).fetchone()
        return row[0] if row else None
    except Exception:
        return None


def set_collab_status(db: sqlite3.Connection, uid: str, status: str) -> None:
    """Persist the collab phase status for *uid* into tasks.raw_json.

    Mirrors the oracle's server-side task persistence (server.js:3117,
    specimen_tasks.json) — the in-memory TaskStore does not survive a
    restart, the project DB does.  Merges into existing raw_json; never
    touches is_active / activated_at.
    """
    _ensure_tasks_table(db)
    row = db.execute("SELECT raw_json FROM tasks WHERE uid = ?", (uid,)).fetchone()
    raw: dict = {}
    if row and row[0]:
        try:
            parsed = json.loads(row[0])
            if isinstance(parsed, dict):
                raw = parsed
        except (json.JSONDecodeError, TypeError):
            raw = {}
    raw["status"] = status
    raw["updatedAt"] = _iso_now()
    db.execute(
        """
        INSERT INTO tasks (uid, raw_json) VALUES (?, ?)
        ON CONFLICT(uid) DO UPDATE SET raw_json = excluded.raw_json
        """,
        (uid, json.dumps(raw, ensure_ascii=False)),
    )
    db.commit()


def get_collab_status(db: sqlite3.Connection, uid: str) -> Optional[str]:
    """Read the persisted collab phase status from tasks.raw_json, or None."""
    try:
        row = db.execute(
            "SELECT raw_json FROM tasks WHERE uid = ?", (uid,)
        ).fetchone()
        if not row or not row[0]:
            return None
        parsed = json.loads(row[0])
        return parsed.get("status") if isinstance(parsed, dict) else None
    except Exception:
        return None


# ── Internal helpers ───────────────────────────────────────────────────────────

def _iso_now() -> str:
    return datetime.now(tz=timezone.utc).isoformat()
