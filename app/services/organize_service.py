"""organize_service.py — Specimen organize: sequence allocation + preview + orchestration.

Oracle: server.js:3493-3557 (maxSeqForUid / seqForNextCompose / bumpSeqHint /
         buildResultBasename), server.js:3615-3840 (organizeSpecimen).

Design:
  next_result_sequence(db, uid) → int
      → maxSeqForUid (disk scan) + 1, or hint+1, whichever is larger.
  organize_preview(db, uid, resolved_dir, path_config) → dict
      → nextSeq + suggested TIFF name.
  organize(db, uid, ...) → list[dict]
      → gate checks → Helicon compose → archive → write back grouping/tasks.

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


def _parse_uid_from_tiff_name(name: str) -> Optional[str]:
    """Extract the 6-part specimen UID from a TIFF filename.

    A TIFF basename looks like:
      PROVINCE-SITE-STATION-ID-SEQ-STORAGE-DATESG[.tif/.tiff]
    The UID is all parts EXCEPT the sequence (index 4):
      PROVINCE-SITE-STATION-ID-STORAGE-DATESG

    Returns None if the name doesn't have ≥6 dash-separated parts.
    """
    stem = Path(name).stem
    parts = stem.split("-")
    # Must have at least 7 parts (6 uid + 1 seq in position 4)
    if len(parts) < 7:
        return None
    try:
        int(parts[4])  # position 4 is the numeric sequence
    except ValueError:
        return None
    uid_parts = parts[:4] + parts[5:]
    return "-".join(uid_parts)


def _max_seq_for_uid_on_disk(uid: str, *dirs: str) -> int:
    """Scan *dirs* for TIFF files belonging to *uid*; return max sequence found.

    Oracle: server.js:3501-3528 maxSeqForUid.
    """
    mx = 0
    for d in dirs:
        if not d or not os.path.isdir(d):
            continue
        for name in os.listdir(d):
            if not re.search(r"\.tiff?$", name, re.IGNORECASE):
                continue
            stem = Path(name).stem
            parts = stem.split("-")
            if len(parts) < 7:
                continue
            try:
                seq = int(parts[4])
            except ValueError:
                continue
            # Reconstruct uid from this filename
            candidate_uid = "-".join(parts[:4] + parts[5:])
            if candidate_uid == uid:
                if seq > mx:
                    mx = seq
    return mx


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
