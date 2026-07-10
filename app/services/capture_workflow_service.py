"""Capture workflow persistence helpers used by the workbench view.

This module keeps specimen, grouping, and JPG-attribution data rules out of
``WorkbenchView``. The view still owns dialogs and visual refreshes; this
module owns the SQLite/event-log mutations that must stay consistent.
"""
from __future__ import annotations

import sqlite3
import os
import shutil
from datetime import datetime, timezone
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from app.services.activation_service import get_active_uid, manual_assign
from app.services.grouping_service import (
    ADHOC_GROUPING_UID,
    Group,
    SpecimenGrouping,
    add_explicit_unassign,
    delete_grouping,
    load_grouping,
    merge_adhoc_groups_for_uid,
    remove_explicit_unassign,
    save_grouping,
    without_blank_draft_groups,
)
from app.utils.path_utils import localize_path


@dataclass(frozen=True)
class DeleteSpecimenResult:
    uid: str
    deleted: bool
    unassigned_photo_rows: int = 0


@dataclass(frozen=True)
class GroupingClaimPlan:
    kind: str
    uid: str
    groups: list[Group] = field(default_factory=list)

    @property
    def should_persist(self) -> bool:
        return self.kind in {"same_uid", "claimed_adhoc"}

    @property
    def claimed(self) -> bool:
        return self.kind == "claimed_adhoc"


@dataclass(frozen=True)
class ManualJpgAssignmentResult:
    active_uid: Optional[str]
    assigned_count: int = 0

    @property
    def assigned(self) -> bool:
        return bool(self.active_uid) and self.assigned_count > 0


@dataclass(frozen=True)
class ArchiveTarget:
    output_dir: str
    existing_zip: str


@dataclass(frozen=True)
class MetadataWriteOutcome:
    written: bool = False
    skipped: str = ""
    error: str = ""


@dataclass(frozen=True)
class OrganizedGroupResult:
    tiff_path: str
    archive_zip: Optional[str] = None
    metadata: MetadataWriteOutcome = field(default_factory=MetadataWriteOutcome)


@dataclass(frozen=True)
class LinkedResultPair:
    uid: str
    grouping: SpecimenGrouping
    removed_from: list[str] = field(default_factory=list)
    tiff_path: str = ""
    zip_path: str = ""


@dataclass(frozen=True)
class SupplementaryArchiveResult:
    zip_path: str
    tiff_path: str
    uid: str = ""


def _clean_uid(value: str | None) -> str:
    return str(value or "").strip()


def _utc_now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def delete_specimen(db: sqlite3.Connection, uid: str) -> DeleteSpecimenResult:
    """Delete one specimen and local references without touching disk files."""
    target = _clean_uid(uid)
    if not target:
        return DeleteSpecimenResult(uid="", deleted=False)

    unassigned_photo_rows = 0
    with db:
        db.execute("DELETE FROM grouping WHERE uid=?", (target,))
        db.execute("DELETE FROM tasks WHERE uid=?", (target,))
        try:
            cur = db.execute(
                "UPDATE photo_assignments SET specimen_uid=NULL WHERE specimen_uid=?",
                (target,),
            )
            unassigned_photo_rows = max(cur.rowcount, 0)
        except sqlite3.Error:
            unassigned_photo_rows = 0
        cur = db.execute("DELETE FROM specimens WHERE uid=?", (target,))
    return DeleteSpecimenResult(
        uid=target,
        deleted=cur.rowcount > 0,
        unassigned_photo_rows=unassigned_photo_rows,
    )


def grouping_for_clean_uid(
    db: Optional[sqlite3.Connection],
    uid: str,
) -> SpecimenGrouping:
    """Load persisted grouping, or return an empty grouping without a DB."""
    target = _clean_uid(uid)
    if db:
        return load_grouping(db, target)
    return SpecimenGrouping(uid=target, groups=[])


def persist_grouping(
    db: Optional[sqlite3.Connection],
    uid: str,
    groups: list[Group],
    *,
    clean_phantoms: bool = False,
) -> None:
    """Persist grouping when a DB is available."""
    target = _clean_uid(uid)
    if db and target:
        save_grouping(db, target, groups, clean_phantoms=clean_phantoms)


def flush_visible_grouping(
    db: Optional[sqlite3.Connection],
    uid: str | None,
    grouping: Optional[SpecimenGrouping],
) -> bool:
    """Persist the grouping object currently visible in the editor."""
    target = _clean_uid(uid)
    if not db or not target or grouping is None:
        return False
    if _clean_uid(getattr(grouping, "uid", target)) != target:
        return False
    save_grouping(db, target, list(grouping.groups or []))
    return True


def prepare_grouping_claim(
    db: sqlite3.Connection,
    target_uid: str,
    panel_uid: str | None,
    groups: list[Group],
) -> GroupingClaimPlan:
    """Plan how visible draft groups should attach to a saved specimen UID.

    The plan is intentionally returned before persisting so the view can update
    output-name fields that depend on the visible naming UI, then call
    :func:`persist_grouping_claim`.
    """
    target = _clean_uid(target_uid)
    if not target:
        return GroupingClaimPlan(kind="none", uid="")

    visible_groups = without_blank_draft_groups(list(groups or []))
    source_uid = _clean_uid(panel_uid)

    if source_uid == target:
        return GroupingClaimPlan(kind="same_uid", uid=target, groups=visible_groups)

    if source_uid != ADHOC_GROUPING_UID or not visible_groups:
        return GroupingClaimPlan(kind="none", uid=target)

    claimed_groups = merge_adhoc_groups_for_uid(db, target, visible_groups)
    if not claimed_groups:
        return GroupingClaimPlan(kind="none", uid=target)
    return GroupingClaimPlan(kind="claimed_adhoc", uid=target, groups=claimed_groups)


def persist_grouping_claim(
    db: sqlite3.Connection,
    plan: GroupingClaimPlan,
) -> None:
    """Persist a prepared grouping claim plan."""
    if not plan.should_persist:
        return
    save_grouping(db, plan.uid, plan.groups, clean_phantoms=False)
    if plan.claimed:
        delete_grouping(db, ADHOC_GROUPING_UID)


def assign_jpg_to_active_specimen(
    project_dir: str,
    db: sqlite3.Connection,
    path: str,
) -> ManualJpgAssignmentResult:
    """Assign one JPG path to the currently active specimen."""
    jpg_path = str(path or "").strip()
    if not project_dir or not jpg_path:
        return ManualJpgAssignmentResult(active_uid=None, assigned_count=0)

    active_uid = get_active_uid(db)
    if not active_uid:
        return ManualJpgAssignmentResult(active_uid=None, assigned_count=0)

    result = manual_assign(project_dir, active_uid, [jpg_path])
    remove_explicit_unassign(db, jpg_path)
    return ManualJpgAssignmentResult(
        active_uid=active_uid,
        assigned_count=int(result.get("count") or 0),
    )


def unassign_jpg(db: sqlite3.Connection, path: str) -> bool:
    """Put a JPG path on the explicit-unassign list."""
    jpg_path = str(path or "").strip()
    if not jpg_path:
        return False
    add_explicit_unassign(db, jpg_path)
    return True


def result_infos_from_grouping(grouping) -> tuple[list[dict], list[dict]]:
    """Return display-ready TIFF and ZIP info lists for a specimen grouping."""
    composed_tiffs: list[dict] = []
    archive_zips: list[dict] = []
    if grouping is None:
        return composed_tiffs, archive_zips

    owner_uid = getattr(grouping, "uid", "")
    for group in list(getattr(grouping, "groups", []) or []):
        seq = getattr(group, "result_sequence", None)
        raw_tiff_path = getattr(group, "composed_tiff_path", None)
        raw_zip_path = getattr(group, "archive_zip", None)
        tiff_path = localize_path(raw_tiff_path) if raw_tiff_path else None
        zip_path = localize_path(raw_zip_path) if raw_zip_path else None
        if not zip_path and tiff_path:
            inferred_zip = Path(tiff_path).with_suffix(".zip")
            if inferred_zip.is_file():
                zip_path = str(inferred_zip)

        if tiff_path:
            composed_tiffs.append({
                "path": tiff_path,
                "name": os.path.basename(tiff_path),
                "seq": seq,
                "owner_uid": owner_uid,
                "group_index": getattr(group, "group_index", None),
                "registered": True,
            })

        if zip_path:
            try:
                zip_size = os.path.getsize(zip_path)
            except OSError:
                zip_size = 0
            archive_zips.append({
                "path": zip_path,
                "name": os.path.basename(zip_path),
                "size": zip_size,
                "seq": seq,
                "owner_uid": owner_uid,
                "group_index": getattr(group, "group_index", None),
                "registered": bool(getattr(group, "archive_zip", None)),
            })

    return composed_tiffs, archive_zips


def plan_archive_target(
    project_dir: str | None,
    results_subdir: str,
    tiff_path: str,
) -> ArchiveTarget:
    """Return where an archive ZIP should land for a composed group."""
    if project_dir:
        output_dir = str(Path(project_dir) / (results_subdir or "results"))
    else:
        output_dir = str(Path(tiff_path).parent)
    existing_zip = str(Path(output_dir) / (Path(tiff_path).stem + ".zip"))
    return ArchiveTarget(output_dir=output_dir, existing_zip=existing_zip)


def _unique_destination(
    directory: Path,
    name: str,
    *,
    source: Path | None = None,
) -> Path:
    dst = directory / name
    if source is not None and dst.exists():
        try:
            if os.path.samefile(source, dst):
                return dst
        except OSError:
            pass
    if not dst.exists():
        return dst
    stem = Path(name).stem
    suffix = Path(name).suffix
    index = 1
    while True:
        candidate = directory / f"{stem}_{index}{suffix}"
        if not candidate.exists():
            return candidate
        index += 1


def move_result_file(
    src: str,
    output_dir: str | Path,
    *,
    preferred_name: str | None = None,
    replace_existing: bool = False,
) -> str:
    """Move one result file into *output_dir*, avoiding accidental overwrite.

    Missing input is returned unchanged so callers can preserve the original
    state and decide how visible the failure should be.
    """
    if not src or not os.path.isfile(src):
        return src

    src_path = Path(src)
    results_path = Path(output_dir)
    results_path.mkdir(parents=True, exist_ok=True)
    dst_name = preferred_name or src_path.name

    try:
        already_in_dir = src_path.resolve().parent == results_path.resolve()
    except OSError:
        already_in_dir = False

    if already_in_dir and src_path.name == dst_name:
        return str(src_path)

    dst = results_path / dst_name
    if replace_existing and dst.exists():
        try:
            if os.path.samefile(src_path, dst):
                return str(dst)
        except OSError:
            pass
        if dst.is_dir():
            raise IsADirectoryError(f"目标路径是目录，不能覆盖: {dst}")
        dst.unlink()
    elif dst.exists():
        dst = _unique_destination(results_path, dst_name, source=src_path)
    if dst == src_path:
        return str(dst)
    shutil.move(str(src_path), str(dst))
    return str(dst)


def _require_existing_file(path: str, label: str) -> str:
    if not path or not os.path.isfile(path):
        raise FileNotFoundError(f"{label} 不存在: {path}")
    return path


def _write_result_metadata(
    db: Optional[sqlite3.Connection],
    uid: str,
    tiff_path: str,
    *,
    project_dir: str = "",
    project_root: Optional[str] = None,
) -> MetadataWriteOutcome:
    if not db or not uid or not tiff_path or not os.path.isfile(tiff_path):
        return MetadataWriteOutcome()
    try:
        from app.services.tiff_metadata_service import write_result_tiff_metadata

        result = write_result_tiff_metadata(
            db,
            uid,
            tiff_path,
            project_dir=project_dir,
            project_root=project_root,
        )
        return MetadataWriteOutcome(
            written=bool(result.get("written")),
            skipped=str(result.get("skipped") or ""),
        )
    except Exception as exc:  # noqa: BLE001 - metadata must not break archival
        return MetadataWriteOutcome(error=str(exc))


def register_tif_only_group(
    db: Optional[sqlite3.Connection],
    uid: str,
    grouping: SpecimenGrouping,
    group: Group,
    *,
    project_dir: str = "",
    results_subdir: str = "results",
    project_root: Optional[str] = None,
) -> OrganizedGroupResult:
    """Register a TIFF-only group and persist its organized state."""
    tiff_src = str(getattr(group, "composed_tiff_path", "") or "")
    if not tiff_src or not os.path.isfile(tiff_src):
        raise FileNotFoundError(f"TIFF 不存在: {tiff_src}")

    moved_tiff = tiff_src
    if project_dir:
        moved_tiff = move_result_file(tiff_src, Path(project_dir) / results_subdir)

    metadata = _write_result_metadata(
        db,
        uid,
        moved_tiff,
        project_dir=project_dir,
        project_root=project_root,
    )

    group.status = "organized"
    group.composed_tiff_path = moved_tiff
    group.archive_zip = None
    group.updated_at = _utc_now_iso()
    persist_grouping(db, uid, list(grouping.groups or []), clean_phantoms=False)
    return OrganizedGroupResult(
        tiff_path=moved_tiff,
        archive_zip=None,
        metadata=metadata,
    )


def finalize_archived_group(
    db: Optional[sqlite3.Connection],
    uid: str,
    grouping: SpecimenGrouping,
    group: Group,
    archive_result,
    *,
    archive_output_dir: str,
    project_dir: str = "",
    project_root: Optional[str] = None,
) -> OrganizedGroupResult:
    """Move archive outputs, write metadata, and persist organized state."""
    tiff_src = _require_existing_file(
        str(getattr(group, "composed_tiff_path", "") or ""),
        "TIFF",
    )
    zip_src = _require_existing_file(
        str(getattr(archive_result, "zip_path", "") or ""),
        "ZIP",
    )

    if project_dir:
        moved_tiff = move_result_file(tiff_src, archive_output_dir)
        preferred_zip = (
            Path(moved_tiff).with_suffix(".zip").name
            if moved_tiff
            else None
        )
        moved_zip = move_result_file(
            zip_src,
            archive_output_dir,
            preferred_name=preferred_zip,
        )
    else:
        moved_tiff = tiff_src
        moved_zip = zip_src

    metadata = _write_result_metadata(
        db,
        uid,
        moved_tiff,
        project_dir=project_dir,
        project_root=project_root,
    )

    group.status = "organized"
    group.composed_tiff_path = moved_tiff
    group.archive_zip = moved_zip
    group.updated_at = _utc_now_iso()
    persist_grouping(db, uid, list(grouping.groups or []), clean_phantoms=False)
    return OrganizedGroupResult(
        tiff_path=moved_tiff,
        archive_zip=moved_zip,
        metadata=metadata,
    )


def _same_path(a: str | None, b: str) -> bool:
    if not a or not b:
        return False
    try:
        return str(Path(a).resolve()) == b
    except OSError:
        return str(a) == b


def resolve_result_pair(tiff_path: str, zip_path: str) -> tuple[str, str]:
    """Resolve a TIFF/ZIP pair, inferring the sibling when one side is omitted."""
    tiff = str(Path(tiff_path).resolve()) if tiff_path else ""
    zipf = str(Path(zip_path).resolve()) if zip_path else ""
    if not zipf and tiff:
        sibling = Path(tiff).with_suffix(".zip")
        if sibling.is_file():
            zipf = str(sibling.resolve())
    if not tiff and zipf:
        sibling = Path(zipf).with_suffix(".tif")
        if sibling.is_file():
            tiff = str(sibling.resolve())
    if not tiff or not Path(tiff).is_file():
        raise FileNotFoundError("找不到对应 TIF 文件。")
    if not zipf or not Path(zipf).is_file():
        raise FileNotFoundError("找不到对应 ZIP 文件。")
    return tiff, zipf


def _resolve_result_pair_lenient(tiff_path: str, zip_path: str) -> tuple[str, str]:
    """纠错路径(解绑/改绑)用的宽松解析:ZIP 可以不存在,TIF 必须能定位。

    合成完但还没「整理」的成果只有 TIF、没有 ZIP;把这种成果绑错了同样要能改。
    """
    tiff = str(Path(tiff_path).resolve()) if tiff_path else ""
    zipf = str(Path(zip_path).resolve()) if zip_path else ""
    if not zipf and tiff:
        sibling = Path(tiff).with_suffix(".zip")
        if sibling.is_file():
            zipf = str(sibling.resolve())
    if not tiff and zipf:
        sibling = Path(zipf).with_suffix(".tif")
        if sibling.is_file():
            tiff = str(sibling.resolve())
    if not tiff and not zipf:
        raise FileNotFoundError("未指定要解绑/改绑的成果文件。")
    return tiff, zipf


def _detach_result_pair(
    db: sqlite3.Connection,
    tiff: str,
    zipf: str,
    *,
    also: str = "",
) -> list[str]:
    """把这对 TIFF/ZIP 从**所有**编号的 grouping 里摘掉,返回被摘掉的编号。

    只改数据库里的「归属」事实,**不碰磁盘上的字节**(TIFF 是无损母版,
    红线:任何流程都不得自动删除它)。``also`` 是一个即便当前没挂该成果、
    也要一并扫一遍的编号(改绑时的目标编号,防止重复挂两份)。
    """
    rows = db.execute("SELECT DISTINCT uid FROM grouping").fetchall()
    affected_uids = {str(row[0]) for row in rows}
    if also:
        affected_uids.add(also)
    removed_from: list[str] = []
    for uid in sorted(affected_uids):
        grouping = load_grouping(db, uid)
        before = len(grouping.groups)
        grouping.groups = [
            g for g in grouping.groups
            if not (
                _same_path(getattr(g, "composed_tiff_path", None), tiff)
                or _same_path(getattr(g, "archive_zip", None), zipf)
            )
        ]
        if len(grouping.groups) != before:
            removed_from.append(uid)
            save_grouping(db, uid, grouping.groups, clean_phantoms=False)
    return removed_from


def unbind_result_pair(
    db: sqlite3.Connection,
    tiff_path: str,
    zip_path: str,
) -> list[str]:
    """解绑:把一对成果 TIFF/ZIP 从它当前挂着的编号上摘下来,不挂到任何编号。

    使用场景(用户 2026-07-10 提出):拍摄/自动归档把 TIF 关联到了**错误的编号**。
    用户先要能「摘下来」,再从容确认它到底属于哪个编号 —— 这一步不应强迫他
    立刻指定新编号,也绝不能因此删除或移动 TIF 母版。

    返回被摘掉的编号列表(幂等:本来就没挂 → 返回 ``[]``)。

    刻意**不**走 ``resolve_result_pair``:那要求 TIF 与 ZIP 都在盘上(关联新成果
    的前置条件)。解绑是纠错动作,ZIP 可能还没归档、或已被用户挪走 —— 只要能
    定位 TIF 就该允许摘下来,否则用户被卡死在错误关联里。
    """
    tiff, zipf = _resolve_result_pair_lenient(tiff_path, zip_path)
    return _detach_result_pair(db, tiff, zipf)


def _attach_result_pair(
    db: sqlite3.Connection,
    target: str,
    tiff: str,
    zipf: str,
) -> SpecimenGrouping:
    """把一对成果挂到 *target* 编号名下(追加一个 group),返回更新后的 grouping。

    有 ZIP ⇒ ``organized``(已归档);只有 TIF ⇒ ``composed``(合成完、待整理)。
    """
    target_grouping = load_grouping(db, target)
    next_idx = max((g.group_index for g in target_grouping.groups), default=-1) + 1
    target_grouping.groups.append(
        Group(
            group_index=next_idx,
            angle_label=f"成果{len(target_grouping.groups) + 1}",
            jpg_paths=[],
            composed_tiff_path=tiff,
            status="organized" if zipf else "composed",
            source="linked-existing-result",
            updated_at=_utc_now_iso(),
            archive_zip=zipf,
            output_name=Path(tiff or zipf).stem,
        )
    )
    save_grouping(db, target, target_grouping.groups, clean_phantoms=False)
    return target_grouping


def rebind_result_pair_to_uid(
    db: sqlite3.Connection,
    target_uid: str,
    tiff_path: str,
    zip_path: str,
) -> LinkedResultPair:
    """改绑:一步把成果从错误编号改挂到 *target_uid*。

    使用场景(用户 2026-07-10):已经确认这张 TIF 真正属于哪个编号,不需要
    「先解绑、再切到那个编号、再关联」三步。语义 = 从所有编号摘掉 + 挂到目标。

    与 ``link_result_pair_to_clean_uid`` 的差别:后者是「把一个**已归档**的
    TIF+ZIP 对登记到编号名下」,强制 ZIP 存在;改绑是纠错,合成完未整理
    (只有 TIF)的成果同样要能改。文件字节永不变动。
    """
    target = _clean_uid(target_uid)
    if not target:
        raise ValueError("目标编号不能为空")
    tiff, zipf = _resolve_result_pair_lenient(tiff_path, zip_path)
    removed_from = _detach_result_pair(db, tiff, zipf, also=target)
    target_grouping = _attach_result_pair(db, target, tiff, zipf)
    return LinkedResultPair(
        uid=target,
        grouping=target_grouping,
        removed_from=removed_from,
        tiff_path=tiff,
        zip_path=zipf,
    )


def list_unbound_result_tiffs(
    db: sqlite3.Connection,
    results_dir: str,
) -> list[str]:
    """列出 ``results_dir`` 里**没有被任何编号关联**的成果 TIF(绝对路径,已排序)。

    使用场景:解绑之后 TIF 仍留在磁盘上,但成果区只渲染 grouping 里的记录 ——
    不列出来的话,那张文件就从界面上消失了,用户再也无法改绑。「全部」模式用
    这个查询多渲染一个「未关联成果」分组,让解绑真正可逆。
    同样能捞出外部软件丢进 results/、从未登记过的 TIF。
    """
    root = Path(results_dir)
    if not root.is_dir():
        return []
    bound: set[str] = set()
    try:
        rows = db.execute("SELECT DISTINCT uid FROM grouping").fetchall()
    except sqlite3.Error:
        return []
    for row in rows:
        for g in load_grouping(db, str(row[0])).groups:
            for attr in ("composed_tiff_path", "archive_zip"):
                val = getattr(g, attr, None)
                if val:
                    try:
                        bound.add(str(Path(val).resolve()))
                    except OSError:
                        bound.add(str(val))

    out: list[str] = []
    for path in sorted(root.rglob("*")):
        if path.suffix.lower() not in (".tif", ".tiff") or not path.is_file():
            continue
        try:
            resolved = str(path.resolve())
        except OSError:
            resolved = str(path)
        if resolved not in bound:
            out.append(resolved)
    return out


def link_result_pair_to_clean_uid(
    db: sqlite3.Connection,
    target_uid: str,
    tiff_path: str,
    zip_path: str,
) -> LinkedResultPair:
    """Register an existing TIFF+ZIP pair under *target_uid*.

    先把这对成果从**任何**已挂的编号上摘掉,再挂到目标编号 —— 所以它天然
    也是「改绑」:一个成果同一时刻只能属于一个编号。见 :func:`rebind_result_pair_to_uid`。
    """
    target = _clean_uid(target_uid)
    if not target:
        raise ValueError("目标编号不能为空")
    tiff, zipf = resolve_result_pair(tiff_path, zip_path)

    removed_from = _detach_result_pair(db, tiff, zipf, also=target)

    target_grouping = load_grouping(db, target)
    next_idx = max((g.group_index for g in target_grouping.groups), default=-1) + 1
    target_grouping.groups.append(
        Group(
            group_index=next_idx,
            angle_label=f"成果{len(target_grouping.groups) + 1}",
            jpg_paths=[],
            composed_tiff_path=tiff,
            status="organized",
            source="linked-existing-result",
            updated_at=_utc_now_iso(),
            archive_zip=zipf,
            output_name=Path(tiff).stem,
        )
    )
    save_grouping(db, target, target_grouping.groups, clean_phantoms=False)
    return LinkedResultPair(
        uid=target,
        grouping=target_grouping,
        removed_from=removed_from,
        tiff_path=tiff,
        zip_path=zipf,
    )


def finalize_supplementary_archive(
    archive_result,
    supp_group,
    *,
    project_dir: str = "",
    results_subdir: str = "results",
) -> SupplementaryArchiveResult:
    """Move supplementary TIFF/ZIP outputs into their final directory."""
    tiff_path = _require_existing_file(
        str(getattr(supp_group, "tiff_path", "") or ""),
        "TIFF",
    )
    zip_path = _require_existing_file(
        str(getattr(archive_result, "zip_path", "") or ""),
        "ZIP",
    )
    if project_dir:
        results_dir = Path(project_dir) / (results_subdir or "results")
    else:
        results_dir = Path(tiff_path).parent
    results_dir.mkdir(parents=True, exist_ok=True)

    final_zip = move_result_file(
        zip_path,
        results_dir,
        replace_existing=True,
    )
    final_tiff = move_result_file(
        tiff_path,
        results_dir,
        replace_existing=True,
    )
    return SupplementaryArchiveResult(
        zip_path=final_zip,
        tiff_path=final_tiff,
        uid=str(getattr(supp_group, "uid", "") or ""),
    )


def attributed_jpg_paths(
    project_dir: str,
    db: sqlite3.Connection,
    uid: str,
    *,
    last_scan=None,
    incoming_subdir: str = "incoming-jpg",
    results_subdir: str = "results",
) -> list[str]:
    """Return JPG paths currently attributed to one specimen UID."""
    target = _clean_uid(uid)
    if not project_dir or not target:
        return []

    try:
        same_project = (
            last_scan is not None
            and Path(last_scan.project_dir).resolve() == Path(project_dir).resolve()
        )
    except Exception:
        same_project = False

    if same_project:
        return [
            f.path for f in list(getattr(last_scan, "jpg_files", []) or [])
            if getattr(f, "attributed_specimen_id", None) == target
            and getattr(f, "path", None)
        ]

    from app.services.monitor_service import build_attribution_context, scan_project

    attr = build_attribution_context(project_dir, db)
    result = scan_project(
        project_dir,
        db,
        attr=attr,
        incoming_subdir=incoming_subdir,
        results_subdir=results_subdir,
    )
    return [
        f.path for f in result.jpg_files
        if f.attributed_specimen_id == target and f.path
    ]
