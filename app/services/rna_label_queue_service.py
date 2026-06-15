"""Persistent RNAlater label queue for sheet/batch printing.

The workbench quick-print path uses this when tissue labels should be batched
onto A4/A5 instead of sent immediately to a small-label printer.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
from typing import Iterable


QUEUE_SETTING_KEY = "rna_label_queue"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_queue(db) -> list[dict]:
    if db is None:
        return []
    try:
        row = db.execute(
            "SELECT value_json FROM project_settings WHERE setting_key=?",
            (QUEUE_SETTING_KEY,),
        ).fetchone()
        if not row:
            return []
        data = json.loads(row[0] or "[]")
        if not isinstance(data, list):
            return []
        out: list[dict] = []
        for item in data:
            if isinstance(item, dict) and item.get("uid"):
                out.append({
                    "uid": str(item.get("uid") or ""),
                    "created_at": str(item.get("created_at") or item.get("createdAt") or ""),
                    "printed": bool(item.get("printed", False)),
                    "printed_at": str(item.get("printed_at") or item.get("printedAt") or ""),
                    "source": str(item.get("source") or "workbench_quick_print"),
                })
        return out
    except Exception:
        return []


def save_queue(db, items: list[dict]) -> None:
    if db is None:
        return
    db.execute(
        "INSERT OR REPLACE INTO project_settings(setting_key, value_json) VALUES (?,?)",
        (QUEUE_SETTING_KEY, json.dumps(items, ensure_ascii=False)),
    )
    db.commit()


def enqueue(db, uids: Iterable[str], *, source: str = "workbench_quick_print") -> int:
    """Add unprinted UIDs if not already pending. Returns number newly queued."""
    items = load_queue(db)
    pending = {x["uid"] for x in items if not x.get("printed")}
    added = 0
    now = _now_iso()
    for uid in uids:
        uid = str(uid or "").strip()
        if not uid or uid in pending:
            continue
        items.append({
            "uid": uid,
            "created_at": now,
            "printed": False,
            "printed_at": "",
            "source": source,
        })
        pending.add(uid)
        added += 1
    if added:
        save_queue(db, items)
    return added


def pending_uids(db) -> list[str]:
    return [x["uid"] for x in load_queue(db) if not x.get("printed")]


def pending_count(db) -> int:
    return len(pending_uids(db))


def mark_printed(db, uids: Iterable[str]) -> int:
    targets = {str(u or "").strip() for u in uids if str(u or "").strip()}
    if not targets:
        return 0
    items = load_queue(db)
    now = _now_iso()
    changed = 0
    for item in items:
        if item.get("uid") in targets and not item.get("printed"):
            item["printed"] = True
            item["printed_at"] = now
            changed += 1
    if changed:
        save_queue(db, items)
    return changed


def clear_pending(db) -> int:
    """Mark all pending labels as printed/cleared. Returns count changed."""
    items = load_queue(db)
    now = _now_iso()
    changed = 0
    for item in items:
        if not item.get("printed"):
            item["printed"] = True
            item["printed_at"] = now
            item["source"] = item.get("source") or "cleared"
            changed += 1
    if changed:
        save_queue(db, items)
    return changed
