"""Activity audit helpers for accountability and label printing records."""
from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from typing import Iterable, Optional


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_workspace_id(db) -> Optional[str]:
    try:
        row = db.execute("SELECT workspace_id FROM workspace_meta LIMIT 1").fetchone()
        return row["workspace_id"] if row else None
    except Exception:
        return None


def log_event(
    db,
    *,
    actor: str = "",
    action: str,
    entity_type: str,
    entity_id: str = "",
    old_value: Optional[dict] = None,
    new_value: Optional[dict] = None,
) -> dict:
    """Append a generic immutable audit event."""
    audit_id = str(uuid.uuid4())
    ts = _utc_now_iso()
    db.execute(
        """
        INSERT INTO audit_log (
          audit_id, workspace_id, actor, action, entity_type, entity_id,
          old_value_json, new_value_json, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            audit_id,
            _read_workspace_id(db),
            actor or "",
            action,
            entity_type,
            entity_id or "",
            json.dumps(old_value or {}, ensure_ascii=False),
            json.dumps(new_value or {}, ensure_ascii=False),
            ts,
        ),
    )
    db.commit()
    return dict(db.execute("SELECT * FROM audit_log WHERE audit_id=?", (audit_id,)).fetchone())


def record_label_print_event(
    db,
    *,
    specimen_uids: Iterable[str],
    actor: str = "",
    bucket: str = "sample",
    template_key: str = "",
    printer_name: str = "",
    copies: int = 1,
    label_count: Optional[int] = None,
    status: str = "printed",
    raw: Optional[dict] = None,
) -> dict:
    """Record a label print attempt and mirror it into the generic audit log."""
    uids = [str(uid) for uid in specimen_uids if str(uid or "").strip()]
    n_copies = max(1, int(copies or 1))
    n_labels = max(0, int(label_count)) if label_count is not None else len(uids) * n_copies
    event_id = str(uuid.uuid4())
    ts = _utc_now_iso()
    db.execute(
        """
        INSERT INTO label_print_events (
          event_id, workspace_id, actor, bucket, template_key, printer_name,
          specimen_uids_json, copies, label_count, status, created_at, raw_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            event_id,
            _read_workspace_id(db),
            actor or "",
            bucket,
            template_key or "",
            printer_name or "",
            json.dumps(uids, ensure_ascii=False),
            n_copies,
            n_labels,
            status or "printed",
            ts,
            json.dumps(raw or {}, ensure_ascii=False),
        ),
    )
    db.commit()
    log_event(
        db,
        actor=actor,
        action="label.print",
        entity_type="label_print_event",
        entity_id=event_id,
        new_value={
            "bucket": bucket,
            "template_key": template_key or "",
            "printer_name": printer_name or "",
            "specimen_uids": uids,
            "copies": n_copies,
            "label_count": n_labels,
            "status": status or "printed",
        },
    )
    return dict(
        db.execute("SELECT * FROM label_print_events WHERE event_id=?", (event_id,)).fetchone()
    )


def default_actor(ctx=None) -> str:
    """Best-effort current operator name for accountability records."""
    for attr in ("current_operator", "operator", "user_name", "username"):
        try:
            val = getattr(ctx, attr, "") if ctx is not None else ""
        except Exception:
            val = ""
        if isinstance(val, str) and val.strip():
            return val.strip()
    for key in ("SPECIMEN_OPERATOR", "USERNAME", "USER"):
        val = os.environ.get(key, "").strip()
        if val:
            return val
    return "未填写"


def specimen_uids_from_print_job(job: dict) -> list[str]:
    """Extract real specimen UIDs from a label print job, skipping blank cells."""
    if not isinstance(job, dict):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for item in job.get("items") or []:
        data = item.get("data") if isinstance(item, dict) else None
        if not isinstance(data, dict) or not data:
            continue
        uid = (
            data.get("uniqueId")
            or data.get("uid")
            or data.get("catalogNumber")
            or data.get("headerId")
            or data.get("id")
            or ""
        )
        uid = str(uid or "").strip()
        if uid and uid not in seen:
            seen.add(uid)
            out.append(uid)
    return out


def record_print_jobs(
    db,
    jobs: Iterable[dict],
    *,
    actor: str = "",
    printer_name: str = "",
    status: str = "printed",
) -> list[dict]:
    """Record all non-empty label print jobs that were actually sent."""
    rows: list[dict] = []
    for job in jobs or []:
        if not isinstance(job, dict):
            continue
        uids = specimen_uids_from_print_job(job)
        labels = [
            item for item in (job.get("items") or [])
            if isinstance(item, dict) and isinstance(item.get("data"), dict) and item.get("data")
        ]
        if not uids or not labels:
            continue
        tmpl = job.get("template") or {}
        template_key = str(
            tmpl.get("key")
            or tmpl.get("id")
            or tmpl.get("code")
            or tmpl.get("name")
            or ""
        )
        rows.append(record_label_print_event(
            db,
            specimen_uids=uids,
            actor=actor,
            bucket=str(job.get("bucket") or "sample"),
            template_key=template_key,
            printer_name=printer_name,
            copies=int(job.get("copies") or 1),
            label_count=len(labels),
            status=status,
            raw={
                "paper_type": job.get("paperType") or "",
                "dims": job.get("dims") or {},
                "template": template_key,
            },
        ))
    return rows
