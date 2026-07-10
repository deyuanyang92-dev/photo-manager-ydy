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


def write_specimens_to_local_db(
    project_dir: str,
    specimens: list[dict],
    *,
    trust_remote_clock: bool = True,
    skew_guarded_out: Optional[list] = None,
) -> int:
    """Merge incoming specimen records into a project DB (LWW).

    Per-record rule using ``collab_updated_at`` (ISO-8601, string-orderable):
      - local row missing               → INSERT (columns present in payload)
      - remote stamp >  local stamp     → UPDATE only payload columns
      - remote stamp <= local stamp     → skip  (local is same/newer)
      - remote unstamped                → write only if local row missing

    Existing rows are never replaced wholesale — partial payloads cannot wipe
    columns that were not included in the peer push.

    **时钟偏斜护栏**(与任务状态同步 collab_store.merge_from_peer 的
    ``trust_remote_clock`` 同语义):LWW 靠墙钟 ``collab_updated_at`` 排序,
    一台时钟快的机器会永远"更新"、把别人真正更新的编辑静默盖掉。当调用方
    测得该 peer 时钟偏斜超阈值(``trust_remote_clock=False``)时,**拒绝用远端
    覆盖任何与本地不同的既有列**,保留本地并把冲突记进 ``skew_guarded_out``
    (``{"uid","field","local","remote"}``)供 UI 告警 —— 不静默丢数据。
    默认 ``True`` 保持旧 LWW 行为(向后兼容);INSERT 缺失行不受护栏影响
    (那不是"覆盖")。
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
                remote_ts = str(spec.get("collab_updated_at") or "")
                payload = {c: spec.get(c) for c in cols}
                update_cols = [c for c in cols if c != "uid"]

                # 既有行的全部当前值(护栏比对 + INSERT 判定共用一次读)
                read_cols = ["collab_updated_at", *update_cols]
                row = db.execute(
                    f"SELECT {', '.join(read_cols)} FROM specimens WHERE uid=?",
                    (uid,),
                ).fetchone()

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

                if not update_cols:
                    continue

                # 时钟不可信 → 只放行"值相同"的列;任何与本地不同的既有列
                # 都拒绝覆盖并记为冲突(镜像任务状态的 skew guard)。
                if not trust_remote_clock:
                    local_vals = {c: row[i + 1] for i, c in enumerate(update_cols)}
                    # 只比内容列 —— collab_updated_at / raw_json 是同步元数据,
                    # 它们不同不代表用户数据冲突(时间戳当然会不同)。
                    _META = ("collab_updated_at", "raw_json")
                    diffs = [
                        c for c in update_cols
                        if c not in _META
                        and _norm(payload[c]) != _norm(local_vals[c])
                    ]
                    if diffs and skew_guarded_out is not None:
                        for c in diffs:
                            skew_guarded_out.append({
                                "uid": uid,
                                "field": c,
                                "local": local_vals[c],
                                "remote": payload[c],
                            })
                    # 时钟不可信:内容有差异→保留本地(上面已记冲突);内容相同→
                    # 也不写(不能把本地时间戳更新成不可信的快钟值,否则本地
                    # 会假装比实际更新)。两种情况都跳过。
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


def _norm(value) -> str:
    """列值归一化后比较(None 与空串等价,数字/文本按字符串比)。"""
    if value is None:
        return ""
    return str(value)


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
