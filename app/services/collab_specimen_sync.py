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


# Claude Code 修改 2026-07-15 — 团队共享工作区多主同步(阶段 1d): collab_rev 是
# 本地单调序号, 单独处理, 不进 SPEC_SYNC_COLS(那是跨机器同步的列白名单)。
def _next_rev(db) -> int:
    """该工作区下一个本地 rev = 当前最大 rev + 1(每写一行 bump 一次, 保证单调)。"""
    row = db.execute("SELECT COALESCE(MAX(collab_rev), 0) FROM specimens").fetchone()
    return int((row[0] if row else 0) or 0) + 1


def get_local_specimens(
    project_dir: str,
    uid: Optional[str] = None,
    since_rev: Optional[int] = None,
) -> list[dict]:
    """Read specimen records from a project DB (used by the FastAPI endpoint).

    Claude Code 修改 2026-07-15 — 返回值多带一个 ``collab_rev``(本地序号), 供对方
    追踪增量游标; ``since_rev`` 给定时只回 ``collab_rev > since_rev`` 的行(增量拉取)。
    ``collab_rev`` 是**本地**元数据, 对方不会把它写进自己的行(合并时各自分配)。
    """
    if not project_dir:
        return []
    try:
        from app.db.db_manager import open_project_db_private
        db = open_project_db_private(project_dir)
        # 读取列 = 同步白名单 + 本地 collab_rev(附加返回, 不参与远端合并)
        read_cols = (*SPEC_SYNC_COLS, "collab_rev")
        cols = ", ".join(read_cols)
        try:
            where = []
            params: list = []
            if uid:
                where.append("uid=?")
                params.append(uid)
            if since_rev is not None:
                where.append("collab_rev > ?")
                params.append(int(since_rev))
            sql = f"SELECT {cols} FROM specimens"
            if where:
                sql += " WHERE " + " AND ".join(where)
            rows = db.execute(sql, tuple(params)).fetchall()
            return [dict(zip(read_cols, row)) for row in rows]
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
                    # Claude Code 修改 2026-07-15 — 新行分配一个本地 rev(bump), 让它
                    # 进增量; payload 里若带了对方的 collab_rev 一律忽略(用本地的)。
                    insert_payload = dict(payload)
                    insert_payload["collab_rev"] = _next_rev(db)
                    insert_cols = [*cols, "collab_rev"]
                    placeholders = ", ".join(f":{c}" for c in insert_cols)
                    col_str = ", ".join(insert_cols)
                    db.execute(
                        f"INSERT INTO specimens ({col_str}) VALUES ({placeholders})",
                        insert_payload,
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

                # Claude Code 修改 2026-07-15 — 真的改了一行 -> bump 本地 rev, 让它进
                # 增量(对方下次 since_rev 拉取能看到)。未被 LWW 放行的行不到这里,
                # rev 保持不变(见上面 continue 分支), 所以"没真改的行不进增量"。
                set_cols = [*update_cols, "collab_rev"]
                set_clause = ", ".join(f"{c}=:{c}" for c in set_cols)
                update_payload = {c: payload[c] for c in update_cols}
                update_payload["collab_rev"] = _next_rev(db)
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


# ── 多工作区同步核心(阶段 1 a/c) ──────────────────────────────────────────────
# Claude Code 2026-07-15 — 团队共享工作区多主同步 spec 阶段 1: 把"只同步当前打开
# 的 1 个工作区"解耦成"遍历一组共享工作区目录"。底层 read/write helper 已按
# project_dir 参数化, 这里只加"遍历共享集合 + 稳定 workspace_id + 按 id 追踪增量
# 游标"。跨库读一律走 helper 里的私有连接+finally close(守 Windows 锁红线),
# 单个坏库(盘没挂/损坏/锁)跳过、绝不整批炸。


def workspace_sync_id(workspace_dir: str) -> str:
    """稳定 workspace 标识: 优先取工作区自己的 workspace_meta.workspace_id;
    legacy 库(无 meta 表)回退到解析后的绝对路径 —— 保证"两个都叫断面1"也不混淆。
    纯读, 不建库/不迁移(守跨库只读红线)。
    """
    try:
        from app.services.project_catalog_service import read_workspace_meta
        meta = read_workspace_meta(workspace_dir)
        wid = str((meta or {}).get("workspace_id") or "").strip()
        if wid:
            return wid
    except Exception as exc:  # noqa: BLE001
        logger.debug("collab: workspace_sync_id meta read failed: %s", exc)
    try:
        from pathlib import Path
        return str(Path(workspace_dir).resolve())
    except OSError:
        return str(workspace_dir)


def iter_shared_workspace_specimens(
    shared_dirs: "list[str]",
    since_rev_by_id: "Optional[dict]" = None,
) -> "list[dict]":
    """遍历一组共享工作区目录, 逐个读出(增量)标本索引。

    返回每个可读工作区一个 dict::

        {"workspace_id": <稳定 id>, "dir": <目录>,
         "specimens": [<该库自 since_rev 后的标本行, 各带 collab_rev>],
         "max_rev": <该库当前最大 rev, 作为对方下次的游标>}

    ``since_rev_by_id`` = {workspace_id: 上次拉到的 max_rev}; 缺则全量。
    单个工作区读失败(盘没挂/损坏/锁)静默跳过, 不影响其余(跨库读容忍红线)。
    """
    cursors = since_rev_by_id or {}
    out: list[dict] = []
    for ws_dir in shared_dirs or []:
        try:
            wid = workspace_sync_id(ws_dir)
            since = cursors.get(wid)
            specimens = get_local_specimens(ws_dir, since_rev=since)
            # get_local_specimens 对不存在的库返回 [] 而不抛; 用是否真有库区分
            # "空库" vs "库不可读": 探一下 project.db 是否在。
            from pathlib import Path
            if not (Path(ws_dir) / "_data" / "project.db").is_file():
                continue  # 目录不是工作区 / 盘没挂 -> 跳过, 不当成"空共享工作区"
            max_rev = 0
            for s in specimens:
                r = int(s.get("collab_rev") or 0)
                if r > max_rev:
                    max_rev = r
            # since 拉的是增量, max_rev 需反映"整库"最大, 不只是增量里的 ->
            # 增量为空时用 since 兜底(游标不倒退)。
            if since is not None and max_rev < int(since):
                max_rev = int(since)
            out.append({
                "workspace_id": wid,
                "dir": ws_dir,
                "specimens": specimens,
                "max_rev": max_rev,
            })
        except Exception as exc:  # noqa: BLE001 — 一个坏库不拖垮整批
            logger.debug("collab: iter_shared skip %s: %s", ws_dir, exc)
            continue
    return out


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
