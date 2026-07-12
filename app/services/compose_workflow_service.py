"""Compose workflow rules for the workbench.

This module owns the selected-JPG and implicit-compose business rules that used
to live directly in ``WorkbenchView``.  The view still owns dialogs and worker
startup; this module owns target resolution, JPG ownership writes, and implicit
group creation.
"""
from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable, Optional

from app.services.activation_service import manual_assign
from app.services.grouping_service import (
    ADHOC_GROUPING_UID,
    Group,
    SpecimenGrouping,
    load_grouping,
    remove_explicit_unassign,
    remove_jpg_from_all_groups,
    save_grouping,
)


@dataclass(frozen=True)
class SelectedComposeTarget:
    """Resolved owner and naming mode for a selected-JPG compose request."""

    uid: str
    output_name: Optional[str] = None
    assign_to_uid: bool = False


@dataclass(frozen=True)
class ImplicitGroupBuildResult:
    """Result of creating a pending group from explicit or attributed JPGs."""

    group_index: Optional[int]
    grouping: Optional[SpecimenGrouping] = None
    jpg_paths: tuple[str, ...] = ()
    reason: str = ""


@dataclass(frozen=True)
class ComposedGroupPersistResult:
    """Persisted result of a successful Helicon composition."""

    uid: str
    group_index: int
    grouping: SpecimenGrouping
    tiff_path: str
    result_sequence: Optional[int]


@dataclass(frozen=True)
class ExternalTiffCandidate:
    """A newly observed TIFF that may be auto-archived with explicit JPGs."""

    reason: str
    current_tiff_paths: tuple[str, ...] = ()
    new_tiff_paths: tuple[str, ...] = ()
    target_tiff: Optional[str] = None

    @property
    def should_seed_known_tiffs(self) -> bool:
        return self.reason == "disabled"

    @property
    def needs_jpg_source(self) -> bool:
        return self.reason == "needs-jpg-source"


@dataclass(frozen=True)
class ExternalTiffJpgSource:
    """Resolved JPG source for archiving an external TIFF."""

    reason: str
    jpg_paths: tuple[str, ...] = ()
    source: str = ""

    @property
    def ready(self) -> bool:
        return self.reason == "ready"


PromptTarget = Callable[[int, str], Optional[SelectedComposeTarget]]


def free_compose_output_name(incoming_dir: str, user_name: Optional[str]) -> str:
    """Return a unique output TIFF name for free-compose."""
    incoming = Path(incoming_dir)
    if user_name:
        safe = re.sub(r'[\\/:*?"<>|]', "_", user_name.strip())
        if safe and not safe.lower().endswith(".tif"):
            safe += ".tif"
        if safe and not (incoming / safe).exists():
            return safe

    n = 1
    while True:
        candidate = f"自由合成-{n}.tif"
        if not (incoming / candidate).exists():
            return candidate
        n += 1


def _path_key(path: str) -> str:
    try:
        return str(Path(path).resolve())
    except Exception:
        return str(path)


def _clean_path_tuple(paths: Iterable[str] | None) -> tuple[str, ...]:
    return tuple(str(path) for path in list(paths or []) if str(path or "").strip())


def pending_tiff_paths(scan_result) -> set[str]:
    """Return unresolved TIFFs from a monitor scan result."""
    return {
        _path_key(getattr(entry, "path", ""))
        for entry in getattr(scan_result, "tiff_files", [])
        if not bool(getattr(entry, "has_zip", False))
        and str(getattr(entry, "path", "") or "").strip()
    }


def detect_external_tiff_candidate(
    *,
    enabled: bool,
    current_tiff_paths: Iterable[str],
    known_tiff_paths: Iterable[str],
    busy: bool,
) -> ExternalTiffCandidate:
    """Detect whether auto-archive should try one newly observed TIFF.

    The function intentionally stops before choosing JPGs. Source selection can
    be expensive because active-UID JPGs need grouping/database checks, so the
    caller should only fetch those paths when this result needs a JPG source.
    """
    current = tuple(
        sorted(
            {
                _path_key(path)
                for path in current_tiff_paths
                if str(path or "").strip()
            }
        )
    )
    known = {
        _path_key(path)
        for path in known_tiff_paths
        if str(path or "").strip()
    }
    if not enabled:
        return ExternalTiffCandidate("disabled", current_tiff_paths=current)

    new_paths = tuple(path for path in current if path not in known)
    if not new_paths:
        return ExternalTiffCandidate(
            "no-new-tiff",
            current_tiff_paths=current,
            new_tiff_paths=new_paths,
        )
    if busy:
        return ExternalTiffCandidate(
            "busy",
            current_tiff_paths=current,
            new_tiff_paths=new_paths,
        )
    return ExternalTiffCandidate(
        "needs-jpg-source",
        current_tiff_paths=current,
        new_tiff_paths=new_paths,
        target_tiff=new_paths[0],
    )


def resolve_external_tiff_jpg_source(
    *,
    active_uid: Optional[str],
    active_uid_jpg_paths: Iterable[str] | None = None,
    selected_jpg_paths: Iterable[str] | None = None,
) -> ExternalTiffJpgSource:
    """Choose explicit JPGs for an external TIFF auto-archive request.

    Active UID provides the default ownership context only when present. Without
    an active UID, manually selected JPGs are the only valid source.
    """
    active = str(active_uid or "").strip()
    if active:
        jpgs = _clean_path_tuple(active_uid_jpg_paths)
        if not jpgs:
            return ExternalTiffJpgSource("no-active-jpgs", source="active_uid")
        return ExternalTiffJpgSource("ready", jpg_paths=jpgs, source="active_uid")

    selected = _clean_path_tuple(selected_jpg_paths)
    if not selected:
        return ExternalTiffJpgSource("no-selected-jpgs", source="selection")
    return ExternalTiffJpgSource("ready", jpg_paths=selected, source="selection")


def unoccupied_jpg_paths(
    db: sqlite3.Connection,
    uid: str,
    jpg_paths: list[str],
) -> list[str]:
    """Return candidate JPGs that are not already used by any group for *uid*."""
    if not db or not uid:
        return []
    grouping = load_grouping(db, uid)
    occupied = {
        _path_key(path)
        for group in grouping.groups
        for path in list(getattr(group, "jpg_paths", []) or [])
    }
    return [
        path for path in list(jpg_paths or [])
        if _path_key(path) not in occupied
    ]


def create_implicit_group(
    db: sqlite3.Connection,
    uid: str,
    jpg_paths: list[str],
    *,
    output_name: Optional[str] = None,
) -> ImplicitGroupBuildResult:
    """Create a pending group from explicit JPG paths.

    The caller decides whether ``jpg_paths`` came from manual selection or from
    active-UID attribution.  This function only applies the shared invariant:
    already occupied JPGs are skipped, and Helicon compose groups require at
    least two remaining JPGs.
    """
    if not db or not uid:
        return ImplicitGroupBuildResult(None, reason="missing-db-or-uid")

    unoccupied = unoccupied_jpg_paths(db, uid, list(jpg_paths or []))
    if len(unoccupied) < 2:
        return ImplicitGroupBuildResult(
            None,
            jpg_paths=tuple(unoccupied),
            reason="not-enough-unoccupied-jpgs",
        )

    grouping = load_grouping(db, uid)
    next_idx = max((g.group_index for g in grouping.groups), default=-1) + 1
    grouping.groups.append(
        Group(
            group_index=next_idx,
            jpg_paths=unoccupied,
            status="pending",
            output_name=(output_name or None),
        )
    )
    save_grouping(db, uid, grouping.groups, clean_phantoms=False)
    return ImplicitGroupBuildResult(
        next_idx,
        grouping=grouping,
        jpg_paths=tuple(unoccupied),
    )


def resolve_compose_output_name(
    db: Optional[sqlite3.Connection],
    uid: str,
    group: Group,
    results_dir: str,
    incoming_dir: str,
    *,
    allow_auto_seq: bool = True,
) -> tuple[str, int]:
    """Resolve one group's output TIFF name and result sequence.

    Priority is intentionally the same as the old ``WorkbenchView`` method:
    explicit group output name, then real-UID sequence naming, then ad-hoc group
    sequence names such as ``1.tif`` and ``2.tif``.
    """
    is_adhoc = (uid == ADHOC_GROUPING_UID) or db is None
    if is_adhoc:
        seq = int(getattr(group, "group_index", 0)) + 1
        output_name = getattr(group, "output_name", None)
        if output_name:
            return Path(output_name).stem + ".tif", seq
        # 场景: 未归属(ad-hoc)分组、用户也没填输出名。用户有两条裁定:
        #   甲(PROJECT_MEMORY:76): 不得静默命名成 1.tif/2.tif, 要用户给名字或选编号
        #   乙(2026-06-21):       [合成+整理] 一条龙**零弹框**, 输出名=组序.tif
        # 用户 2026-07-12 拍板: 两条共存 —— **人点的单组合成要问**;
        #   **自动化/批量(一条龙)保持零弹框**, 照用 组序.tif。
        # 所以命名策略由调用方声明: allow_auto_seq=False -> 返回空名(视图去问用户);
        #   默认 True -> 保持旧行为(批量/无人值守路径不受影响)。(Fable 5, 2026-07-12)
        if not allow_auto_seq:
            return "", seq
        return f"{seq}.tif", seq

    from app.services.organize_service import organize_preview

    preview = organize_preview(
        db,
        uid,
        results_dir=results_dir,
        incoming_dir=incoming_dir,
    )
    output_name = getattr(group, "output_name", None)
    if output_name:
        return Path(output_name).stem + ".tif", preview.next_seq
    return preview.suggested_tiff_name, preview.next_seq


def persist_composed_group(
    db: Optional[sqlite3.Connection],
    uid: str,
    grouping: SpecimenGrouping,
    group_index: int,
    *,
    tiff_path: str,
    result_sequence: Optional[int],
    jpg_paths: Optional[list[str]] = None,
) -> ComposedGroupPersistResult:
    """Persist a successful compose result to grouping state.

    This is the shared write path for interactive and headless compose. The
    caller owns UI refresh and dialogs; this function owns the grouping mutation
    and sequence-hint advancement.
    """
    target_uid = str(uid or "").strip()
    if not target_uid:
        raise ValueError("uid is required")
    output = str(tiff_path or "").strip()
    if not output or not Path(output).is_file():
        raise FileNotFoundError(f"TIFF 不存在: {output}")

    write_grouping = grouping
    group = next(
        (
            g for g in list(getattr(write_grouping, "groups", []) or [])
            if getattr(g, "group_index", None) == group_index
        ),
        None,
    )
    if db is not None:
        fresh_grouping = load_grouping(db, target_uid)
        fresh_group = next(
            (
                g for g in list(getattr(fresh_grouping, "groups", []) or [])
                if getattr(g, "group_index", None) == group_index
            ),
            None,
        )
        if fresh_group is not None:
            write_grouping = fresh_grouping
            group = fresh_group
    if group is None:
        raise ValueError(f"找不到组{group_index + 1}")

    old_jpg_paths = list(getattr(group, "jpg_paths", []) or [])
    old_tiff_path = getattr(group, "composed_tiff_path", None)
    old_status = getattr(group, "status", None)
    old_updated_at = getattr(group, "updated_at", None)
    old_result_sequence = getattr(group, "result_sequence", None)

    try:
        if jpg_paths is not None:
            group.jpg_paths = list(jpg_paths)
        group.composed_tiff_path = output
        group.status = "composed"
        group.updated_at = datetime.now(tz=timezone.utc).isoformat()
        group.result_sequence = result_sequence

        if db is not None:
            save_grouping(db, target_uid, write_grouping.groups, clean_phantoms=False)
            if result_sequence is not None:
                try:
                    from app.services.organize_service import _bump_seq_hint

                    _bump_seq_hint(db, target_uid, int(result_sequence))
                except Exception:
                    import logging
                    logging.getLogger(__name__).warning(
                        "序号 hint 更新失败 (uid=%s, seq=%s)", target_uid, result_sequence,
                        exc_info=True,
                    )
    except Exception:
        group.jpg_paths = old_jpg_paths
        group.composed_tiff_path = old_tiff_path
        group.status = old_status
        group.updated_at = old_updated_at
        group.result_sequence = old_result_sequence
        raise

    return ComposedGroupPersistResult(
        uid=target_uid,
        group_index=group_index,
        grouping=write_grouping,
        tiff_path=output,
        result_sequence=result_sequence,
    )


def assign_selected_jpgs_to_uid(
    project_dir: str,
    db: sqlite3.Connection,
    uid: str,
    jpg_paths: list[str],
) -> int:
    """Write explicit ownership for selected JPGs before composing them."""
    if not project_dir or not db or not uid or not jpg_paths:
        return 0

    selected = list(jpg_paths)
    for path in selected:
        remove_jpg_from_all_groups(db, path)
    result = manual_assign(project_dir, uid, selected)
    for path in selected:
        remove_explicit_unassign(db, path)
    return int(result.get("count") or 0)


def resolve_implicit_compose_target(
    selected_jpgs: list[str],
    active_uid: Optional[str],
    *,
    auto_archive_enabled: bool,
    selected_owner_uids: list[str],
    prompt_target: PromptTarget,
) -> Optional[SelectedComposeTarget]:
    """Resolve the main compose button target without touching UI widgets.

    Decision table:
    - selected JPGs + active UID: active UID wins and attribution is written.
    - selected JPGs + no active UID: ask the UI for target UID or free stem.
    - no selected JPGs + active UID + auto archive: use active UID attribution.
    - no selected JPGs otherwise: no target; caller may show guidance text.
    """
    selected = list(selected_jpgs or [])
    active = str(active_uid or "").strip()
    if selected:
        if active:
            return SelectedComposeTarget(uid=active, assign_to_uid=True)
        owners = [str(uid or "").strip() for uid in list(selected_owner_uids or [])]
        owners = [uid for uid in owners if uid]
        default_uid = owners[0] if len(owners) == 1 else ""
        return prompt_target(len(selected), default_uid)

    if active and auto_archive_enabled:
        return SelectedComposeTarget(uid=active, assign_to_uid=False)
    return None
