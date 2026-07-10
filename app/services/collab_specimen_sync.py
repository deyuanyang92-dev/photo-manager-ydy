"""collab_specimen_sync.py — Specimen metadata replication for LAN collaboration.

Pure DB read/write helpers split out of collab_service.py so LWW merge rules
and column whitelists live in one focused module.  ``CollabService`` delegates
here and re-exports ``SPEC_SYNC_COLS`` for backward-compatible tests.
"""
from __future__ import annotations

import logging
from typing import Optional

from app.services.collab_types import _now_iso

logger = logging.getLogger(__name__)

# Columns synced between peers — must match schema.sql specimens columns
# exactly (snake_case).  Excludes local-only flags (metadata / pinned /
# owner_project_dir).
SPEC_SYNC_COLS = (
    "uid", "id", "province", "site", "station", "storage",
    "collection_date", "photo_date",
    "scientific_name", "scientific_name_cn",
    "taxon_group", "taxon_group_cn", "order_name", "order_cn",
    "family", "family_cn", "genus", "genus_cn",
    "lon", "lat", "geo_area",
    "collector", "photographer", "identifier",
    "notes", "photo_notes", "angle",
    "collab_updated_at", "raw_json",
)


def get_local_specimens(project_dir: str, uid: Optional[str] = None) -> list[dict]:
    """Read specimen records from a project DB (used by the FastAPI endpoint)."""
    if not project_dir:
        return []
    try:
        from app.db.db_manager import open_project_db_private
        db = open_project_db_private(project_dir)
        cols = ", ".join(SPEC_SYNC_COLS)
        try:
            if uid:
                rows = db.execute(
                    f"SELECT {cols} FROM specimens WHERE uid=?", (uid,)
                ).fetchall()
            else:
                rows = db.execute(
                    f"SELECT {cols} FROM specimens"
                ).fetchall()
            return [dict(zip(SPEC_SYNC_COLS, row)) for row in rows]
        finally:
            db.close()
    except Exception as exc:
        logger.debug("collab: get_local_specimens error: %s", exc)
        return []


def write_specimens_to_local_db(project_dir: str, specimens: list[dict]) -> int:
    """Merge incoming specimen records into a project DB (LWW).

    Per-record rule using ``collab_updated_at`` (ISO-8601, string-orderable):
      - local row missing               → INSERT (columns present in payload)
      - remote stamp >  local stamp     → UPDATE only payload columns
      - remote stamp <= local stamp     → skip  (local is same/newer)
      - remote unstamped                → write only if local row missing

    Existing rows are never replaced wholesale — partial payloads cannot wipe
    columns that were not included in the peer push.
    """
    if not project_dir or not specimens:
        return 0
    try:
        from app.db.db_manager import open_project_db_private
        db = open_project_db_private(project_dir)
        written = 0
        try:
            for spec in specimens:
                uid = spec.get("uid")
                if not uid:
                    continue
                cols = [c for c in SPEC_SYNC_COLS if c in spec]
                if "uid" not in cols:
                    continue
                row = db.execute(
                    "SELECT collab_updated_at FROM specimens WHERE uid=?",
                    (uid,),
                ).fetchone()
                remote_ts = str(spec.get("collab_updated_at") or "")
                payload = {c: spec.get(c) for c in cols}

                if row is None:
                    placeholders = ", ".join(f":{c}" for c in cols)
                    col_str = ", ".join(cols)
                    db.execute(
                        f"INSERT INTO specimens ({col_str}) VALUES ({placeholders})",
                        payload,
                    )
                    written += 1
                    continue

                local_ts = str(row[0] or "")
                if not remote_ts:
                    continue          # unstamped remote never overwrites
                if local_ts and remote_ts <= local_ts:
                    continue          # local copy is same or newer

                update_cols = [c for c in cols if c != "uid"]
                if not update_cols:
                    continue
                set_clause = ", ".join(f"{c}=:{c}" for c in update_cols)
                update_payload = {c: payload[c] for c in update_cols}
                update_payload["uid"] = uid
                db.execute(
                    f"UPDATE specimens SET {set_clause} WHERE uid=:uid",
                    update_payload,
                )
                written += 1
            db.commit()
        finally:
            db.close()
        return written
    except Exception as exc:
        logger.debug("collab: write_specimens error: %s", exc)
        return 0


def stamp_specimen(project_dir: str, uid: str) -> None:
    """Write a fresh LWW timestamp onto a local record before pushing."""
    if not project_dir:
        return
    try:
        from app.db.db_manager import open_project_db_private
        db = open_project_db_private(project_dir)
        try:
            db.execute(
                "UPDATE specimens SET collab_updated_at=? WHERE uid=?",
                (_now_iso(), uid),
            )
            db.commit()
        finally:
            db.close()
    except Exception as exc:
        logger.debug("collab: stamp_specimen error: %s", exc)
