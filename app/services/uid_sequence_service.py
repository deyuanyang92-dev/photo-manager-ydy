"""UID sequence reservation for collaborative specimen numbering."""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Optional


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _norm_prefix(prefix: str) -> str:
    return str(prefix or "").strip().upper()


def ensure_device(
    db,
    *,
    device_id: Optional[str] = None,
    device_name: str = "",
    owner: str = "",
) -> dict:
    """Ensure a collaborator device row exists."""
    did = device_id or str(uuid.uuid4())
    ts = _utc_now_iso()
    db.execute(
        """
        INSERT INTO devices (
          device_id, device_name, owner, created_at, last_seen_at
        ) VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(device_id) DO UPDATE SET
          device_name=excluded.device_name,
          owner=excluded.owner,
          last_seen_at=excluded.last_seen_at
        """,
        (did, device_name, owner, ts, ts),
    )
    db.commit()
    return dict(db.execute("SELECT * FROM devices WHERE device_id=?", (did,)).fetchone())


def ensure_sequence(
    db,
    prefix: str,
    *,
    project_code: str = "",
    scope: str = "workspace",
    padding: int = 3,
) -> dict:
    """Ensure a sequence row exists for a prefix/scope."""
    pfx = _norm_prefix(prefix)
    if not pfx:
        raise ValueError("prefix is required")
    sid = str(uuid.uuid4())
    ts = _utc_now_iso()
    db.execute(
        """
        INSERT INTO uid_sequences (
          sequence_id, project_code, prefix, scope, next_number, padding, updated_at
        ) VALUES (?, ?, ?, ?, 1, ?, ?)
        ON CONFLICT(project_code, prefix, scope) DO UPDATE SET
          padding=MAX(uid_sequences.padding, excluded.padding),
          updated_at=excluded.updated_at
        """,
        (sid, project_code or "", pfx, scope or "workspace", max(1, int(padding)), ts),
    )
    db.commit()
    return dict(
        db.execute(
            """
            SELECT * FROM uid_sequences
             WHERE project_code=? AND prefix=? AND scope=?
            """,
            (project_code or "", pfx, scope or "workspace"),
        ).fetchone()
    )


def reserve_block(
    db,
    prefix: str,
    *,
    count: int = 1,
    device_id: Optional[str] = None,
    project_code: str = "",
    scope: str = "workspace",
    padding: int = 3,
    raw: Optional[dict] = None,
) -> dict:
    """Reserve a contiguous block of specimen numbers transactionally."""
    if count <= 0:
        raise ValueError("count must be positive")
    seq = ensure_sequence(
        db, prefix, project_code=project_code, scope=scope, padding=padding
    )
    ts = _utc_now_iso()
    with db:
        row = db.execute(
            "SELECT * FROM uid_sequences WHERE sequence_id=?",
            (seq["sequence_id"],),
        ).fetchone()
        start = int(row["next_number"])
        end = start + count - 1
        db.execute(
            """
            UPDATE uid_sequences
               SET next_number=?, updated_at=?
             WHERE sequence_id=?
            """,
            (end + 1, ts, row["sequence_id"]),
        )
        rid = str(uuid.uuid4())
        db.execute(
            """
            INSERT INTO uid_reservations (
              reservation_id, sequence_id, device_id, start_number, end_number,
              next_unused_number, status, created_at, updated_at, raw_json
            ) VALUES (?, ?, ?, ?, ?, ?, 'active', ?, ?, ?)
            """,
            (
                rid,
                row["sequence_id"],
                device_id,
                start,
                end,
                start,
                ts,
                ts,
                json.dumps(raw or {}, ensure_ascii=False),
            ),
        )
    return dict(db.execute("SELECT * FROM uid_reservations WHERE reservation_id=?", (rid,)).fetchone())


def format_species_id(prefix: str, number: int, padding: int = 3) -> str:
    return f"{_norm_prefix(prefix)}{int(number):0{max(1, int(padding))}d}"


def consume_next(
    db,
    reservation_id: str,
) -> str:
    """Consume and return the next species id from a reservation."""
    with db:
        res = db.execute(
            """
            SELECT r.*, s.prefix, s.padding
              FROM uid_reservations r
              JOIN uid_sequences s ON s.sequence_id = r.sequence_id
             WHERE r.reservation_id=?
            """,
            (reservation_id,),
        ).fetchone()
        if res is None:
            raise ValueError("unknown reservation")
        if res["status"] != "active":
            raise ValueError("reservation is not active")
        n = int(res["next_unused_number"])
        if n > int(res["end_number"]):
            db.execute(
                "UPDATE uid_reservations SET status='exhausted', updated_at=? WHERE reservation_id=?",
                (_utc_now_iso(), reservation_id),
            )
            raise ValueError("reservation exhausted")
        next_n = n + 1
        status = "exhausted" if next_n > int(res["end_number"]) else "active"
        db.execute(
            """
            UPDATE uid_reservations
               SET next_unused_number=?, status=?, updated_at=?
             WHERE reservation_id=?
            """,
            (next_n, status, _utc_now_iso(), reservation_id),
        )
    return format_species_id(res["prefix"], n, res["padding"])


def reserve_and_consume(
    db,
    prefix: str,
    *,
    device_id: Optional[str] = None,
    project_code: str = "",
    scope: str = "workspace",
    padding: int = 3,
) -> str:
    """Reserve one number and return it. Useful for online host mode."""
    res = reserve_block(
        db,
        prefix,
        count=1,
        device_id=device_id,
        project_code=project_code,
        scope=scope,
        padding=padding,
    )
    return consume_next(db, res["reservation_id"])
