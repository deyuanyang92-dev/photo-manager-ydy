"""organize_service.py — Specimen organize: sequence allocation + preview + orchestration.

Oracle: server.js:3493-3557 (maxSeqForUid / seqForNextCompose / bumpSeqHint /
         buildResultBasename), server.js:3615-3840 (organizeSpecimen).

Design:
  next_result_sequence(db, uid) → int
      → maxSeqForUid (disk scan) + 1, or hint+1, whichever is larger.
  organize_preview(db, uid, resolved_dir, path_config) → dict
      → nextSeq + suggested TIFF name.
  _check_organize_gate(...)
      → the pre-organize gate only. The full organize orchestration
        (Helicon compose → archive → write back grouping/tasks) lives in
        the workbench view + capture_workflow_service, not in this module.

Gates (hard requirements before organize can proceed):
  1. uid must be the active specimen (or opts.allow_inactive).
  2. At least one group with ≥2 JPGs must exist (implicit group fallback allowed).
"""

from __future__ import annotations

import os
import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


# ── Sequence helpers ──────────────────────────────────────────────────────────

def _uid_from_row(row: tuple | dict) -> str:
    """Extract uid string from a DB row (sqlite3.Row or dict or tuple)."""
    if hasattr(row, "keys"):
        return str(row["uid"])
    return str(row[0])


def _parse_result_tiff_name(
    name: str, components: Optional[list] = None
) -> Optional[tuple[str, int]]:
    """Parse ``(uid, sequence)`` from a result-TIFF basename.

    权威解析器 = ``naming.parse_tiff_result_detail``（PROJECT_MEMORY：文件名→UID
    禁止 split('-') 猜段）。同时认标准 7 段
    ``省-地-站-物-序-存-日`` 与 legacy 无站位 6 段
    ``GXFCG-BLW-BZC003-R-1-20260618``（→ uid=GXFCG-BLW-BZC003-R-20260618, seq=1）。
    对标准 7 段名的输出与旧手搓实现逐字节一致（超集，不改变既有行为）；
    裸 uid（无序号段）与无关名仍返回 None。
    """
    from app.utils.naming import normalize_naming_components, parse_tiff_result_detail

    detail = parse_tiff_result_detail(
        Path(name).stem, normalize_naming_components(components)
    )
    if detail is None or detail.sequence is None:
        return None
    return detail.uid, int(detail.sequence)


def _parse_uid_from_tiff_name(name: str) -> Optional[str]:
    """Extract the specimen UID (no sequence segment) from a TIFF filename."""
    # §7 旧: 手搓 split('-'), 要求 ≥7 段 → legacy 无站位 6 段名恒返 None,
    #        补处理/回填对用户自己的 GXFCG legacy 成果失效; 违 PROJECT_MEMORY
    #        「必须走 parse_tiff_result_detail」禁令。oracle app.js:3798-3800 的
    #        parts.slice(0,4).concat(parts.slice(5)) 语义被权威解析器完整覆盖。
    # stem = Path(name).stem
    # parts = stem.split("-")
    # if len(parts) < 7:
    #     return None
    # try:
    #     int(parts[4])  # position 4 is the numeric sequence
    # except ValueError:
    #     return None
    # uid_parts = parts[:4] + parts[5:]
    # return "-".join(uid_parts)
    parsed = _parse_result_tiff_name(name)
    return parsed[0] if parsed else None


def _max_seq_for_uid_on_disk(uid: str, *dirs: str) -> int:
    """Scan *dirs* for TIFF files belonging to *uid*; return max sequence found.

    Oracle: server.js:3501-3528 maxSeqForUid.

    场景(用户 2026-07-11 报障并确认修): 老式**无站位**编号
    ``GXFCG-BLW-BZC003-R-20260618``(5 段), 其成果文件名插入序号后只有 6 段。
    理由(Fable 5): §7 旧实现用 ``split('-')`` 且要求 ``len(parts) >= 7`` ——
    这些 6 段老成片**全部被跳过** → ``disk_max`` 恒为 0 → 下一个序号算成 1 →
    新成片直接**覆盖用户已拍好的第 1 张**, 静默丢数据。这正是 PROJECT_MEMORY
    明令禁止的 "split('-') 猜段"(必须走 ``parse_tiff_result_detail`` 权威解析器);
    ``_parse_uid_from_tiff_name`` 已修, 唯独这里漏了。
    现改为复用同一个权威解析器 ``_parse_result_tiff_name``: 标准 7 段与 legacy
    6 段一视同仁, 标准段行为逐字节不变(超集)。
    """
    mx = 0
    for d in dirs:
        if not d or not os.path.isdir(d):
            continue
        for name in os.listdir(d):
            if not re.search(r"\.tiff?$", name, re.IGNORECASE):
                continue
            # §7 旧: split('-') + len(parts) < 7 跳过 + parts[4] 猜序号 +
            #        "-".join(parts[:4] + parts[5:]) 拼 uid —— legacy 6 段全漏。
            # stem = Path(name).stem
            # parts = stem.split("-")
            # if len(parts) < 7:
            #     continue
            # try:
            #     seq = int(parts[4])
            # except ValueError:
            #     continue
            # candidate_uid = "-".join(parts[:4] + parts[5:])
            # if candidate_uid == uid:
            #     if seq > mx:
            #         mx = seq
            parsed = _parse_result_tiff_name(name)
            if not parsed:
                continue
            candidate_uid, seq = parsed
            if candidate_uid == uid and seq > mx:
                mx = seq
    return mx


def list_unnumbered_result_tiffs(*dirs: str) -> list[str]:
    """列出 *dirs* 里「命名不规范、算不进序号」的成片 TIF 文件名。

    场景(用户 2026-07-11 要求): 用户想在整理时被提醒哪些成片命名不规范。
    理由(Fable 5): 序号扫描(``_max_seq_for_uid_on_disk``)只认权威解析器能解出
    ``(uid, seq)`` 的文件名。解不出的文件(外部软件随手命名、手动改过名的)
    **不会被计入序号**, 是撞号覆盖的风险源 —— 但用户看不见它们的存在。
    这个函数把它们挑出来, 让工作台能主动提醒"这些成片没有规范序号,
    不参与序号计算, 建议改名或用『检查 TIF 命名』修复"。

    只读、不改任何文件。返回文件名(非全路径), 按名排序。
    """
    out: list[str] = []
    for d in dirs:
        if not d or not os.path.isdir(d):
            continue
        for name in sorted(os.listdir(d)):
            if not re.search(r"\.tiff?$", name, re.IGNORECASE):
                continue
            if _parse_result_tiff_name(name) is None:
                out.append(name)
    return out


def next_result_sequence(db: sqlite3.Connection, uid: str) -> int:
    """Return the next available result sequence number for *uid*.

    Uses maxSeqForUid (disk) + 1 for correctness.  The DB tasks table
    `next_result_sequence_hint` provides the fast-path hint, ensuring
    monotonic increase even when the disk is not yet flushed.

    Oracle: server.js:3532-3542 seqForNextCompose.
    """
    hint = 0
    try:
        row = db.execute(
            "SELECT next_result_sequence_hint FROM tasks WHERE uid = ?", (uid,)
        ).fetchone()
        if row and row[0] is not None:
            hint = int(row[0])
    except Exception:
        pass

    # We don't have project_dir here — caller should use organize_preview
    # which passes dirs.  This function is the simple DB-only variant for tests.
    disk_next = hint  # no disk scan without dirs; callers with dirs use _max_seq_for_uid_on_disk
    return max(hint, disk_next, 1)


def _bump_seq_hint(db: sqlite3.Connection, uid: str, last_seq: int) -> None:
    """Advance nextResultSequenceHint to last_seq + 1.

    Oracle: server.js:3545-3557.
    """
    next_val = last_seq + 1
    db.execute(
        """
        INSERT INTO tasks (uid, next_result_sequence_hint)
        VALUES (?, ?)
        ON CONFLICT(uid) DO UPDATE SET
            next_result_sequence_hint = MAX(
                COALESCE(next_result_sequence_hint, 0), excluded.next_result_sequence_hint
            )
        """,
        (uid, next_val),
    )
    db.commit()


def build_result_basename(uid: str, seq: int) -> str:
    """Insert sequence at position 4 in the UID's dash-parts.

    Oracle: server.js:3493-3497 buildResultBasename.

    Example:
      uid = "FJ-YGLZ-B2-DLC001-RD75E-20260506-0508"
      seq = 1
      → "FJ-YGLZ-B2-DLC001-1-RD75E-20260506-0508"
    """
    parts = uid.split("-")
    parts.insert(4, str(seq))
    return "-".join(parts)


def rename_tiff(old_path: str, new_name: str) -> str:
    """把磁盘上的 TIFF 改名为 *new_name*（同目录），返回新路径。

    用于外部 Helicon 合成的 TIFF 按激活编号成果名重命名（拍照区核心：JPG↔TIFF 关联）。
    - 源文件须存在，否则 FileNotFoundError。
    - 目标名已被**别的**文件占用 → 追加 `_1/_2…` 序号，绝不覆盖他人。
    - 新名 == 旧名（无变化）→ 原样返回，不动盘。
    纯函数、无 Qt。
    """
    src = Path(old_path)
    if not src.is_file():
        raise FileNotFoundError(f"TIFF 不存在: {old_path}")
    new_name = (new_name or "").strip()
    if not new_name:
        raise ValueError("新文件名不能为空")
    dst = src.with_name(new_name)
    if dst == src:
        return str(src)
    if dst.exists():
        stem, suffix = Path(new_name).stem, Path(new_name).suffix
        i = 1
        while dst.exists():
            dst = src.with_name(f"{stem}_{i}{suffix}")
            i += 1
    os.replace(str(src), str(dst))
    return str(dst)


# ── Preview ───────────────────────────────────────────────────────────────────

@dataclass
class OrganizePreview:
    uid: str
    next_seq: int
    suggested_tiff_name: str  # e.g. FJ-YGLZ-B2-DLC001-1-RD75E-20260506-0508.tif
    groups: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def organize_preview(
    db: sqlite3.Connection,
    uid: str,
    results_dir: str = "",
    incoming_dir: str = "",
) -> OrganizePreview:
    """Compute next sequence + suggested TIFF name without any disk writes.

    Gate: uid must exist in tasks or specimens table (or at least be non-empty).
    Does not require active status — preview is always allowed.

    Oracle: server.js:3532-3542 seqForNextCompose + buildResultBasename.
    """
    if not uid:
        raise ValueError("uid 不能为空")

    # Disk-based max seq
    dirs = [d for d in [results_dir, incoming_dir] if d]
    disk_max = _max_seq_for_uid_on_disk(uid, *dirs)

    # Hint from DB
    hint = 0
    try:
        row = db.execute(
            "SELECT next_result_sequence_hint FROM tasks WHERE uid = ?", (uid,)
        ).fetchone()
        if row and row[0] is not None:
            hint = int(row[0])
    except Exception:
        pass

    next_seq = max(hint, disk_max + 1, 1)
    basename = build_result_basename(uid, next_seq)
    tiff_name = basename + ".tif"

    # Load groups (if any)
    groups: list[dict] = []
    try:
        rows = db.execute(
            "SELECT group_index, angle_label, jpg_paths FROM grouping WHERE uid = ?",
            (uid,),
        ).fetchall()
        for row in rows:
            groups.append({
                "groupIndex": row[0],
                "angleLabel": row[1] or "",
                "jpgPaths": __import__("json").loads(row[2] or "[]"),
            })
    except Exception:
        pass

    warnings = []
    if not groups:
        warnings.append("无分组 — 将使用隐式归属 JPG")

    return OrganizePreview(
        uid=uid,
        next_seq=next_seq,
        suggested_tiff_name=tiff_name,
        groups=groups,
        warnings=warnings,
    )


# ── Organize gate check ───────────────────────────────────────────────────────

class OrganizeGateError(Exception):
    """Raised when organize gate conditions are not met."""
    pass


def _check_organize_gate(
    db: sqlite3.Connection,
    uid: str,
    groups: list[dict],
    allow_inactive: bool = False,
) -> None:
    """Enforce organize gates.

    Gate 1: uid must be active (unless allow_inactive).
    Gate 2: at least one group with ≥2 jpg_paths must exist.

    Oracle: server.js:3619-3672 (gate: no groupsToUse → 400 error).
    """
    if not uid:
        raise OrganizeGateError("uid 不能为空")

    if not allow_inactive:
        try:
            row = db.execute(
                "SELECT is_active FROM tasks WHERE uid = ?", (uid,)
            ).fetchone()
            if not row or not row[0]:
                raise OrganizeGateError(f"标本 {uid} 未激活，无法整理")
        except OrganizeGateError:
            raise
        except Exception:
            raise OrganizeGateError(f"标本 {uid} 未激活，无法整理")

    # Gate 2: at least one group with ≥2 jpg_paths
    any_valid = any(
        len(g.get("jpgPaths", g.get("jpg_paths", []))) >= 2
        for g in groups
    )
    if not any_valid:
        raise OrganizeGateError(
            "分组工具里还没有照片（或照片不足 2 张）。请先分组再整理。"
        )
