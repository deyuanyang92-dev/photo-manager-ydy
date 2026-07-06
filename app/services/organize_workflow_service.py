"""Organize workflow decisions for the workbench.

The workbench owns dialogs, progress windows, and worker startup. This module
owns the preflight decisions that must stay consistent across manual, batch,
selected-JPG, and external-TIFF organize flows.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Iterable, Optional

from app.services.capture_workflow_service import ArchiveTarget, plan_archive_target
from app.services.grouping_service import (
    ADHOC_GROUPING_UID,
    Group,
    SpecimenGrouping,
    load_grouping,
    save_grouping,
)


@dataclass(frozen=True)
class OrganizeGroupState:
    """Preflight state for one grouping row."""

    reason: str
    group: Optional[Group] = None
    is_tif_only: bool = False

    @property
    def ready(self) -> bool:
        return self.reason in {"ready-jpg-archive", "ready-tif-only"}

    @property
    def already_organized(self) -> bool:
        return self.reason == "already-organized"


@dataclass(frozen=True)
class OrganizeGateCheckPlan:
    """How the view should call organize_service._check_organize_gate."""

    required: bool
    groups_as_dicts: tuple[dict, ...] = ()
    allow_inactive: bool = False
    prompt_on_error: bool = False
    silent_skip_on_error: bool = False
    skip_gate: bool = False


@dataclass(frozen=True)
class ArchiveWorkerPlan:
    """File-system target and deletion setting for the JPG archive worker."""

    archive_target: ArchiveTarget
    delete_jpg: bool
    existing_zip_exists: bool = False

    @property
    def archive_output_dir(self) -> str:
        return self.archive_target.output_dir

    @property
    def existing_zip(self) -> str:
        return self.archive_target.existing_zip


@dataclass(frozen=True)
class ExistingTiffGroupResult:
    """Persisted composed group created from explicit JPGs and an existing TIFF."""

    uid: str
    group_index: int
    grouping: SpecimenGrouping
    tiff_path: str
    renamed_tiff: bool = False


def resolve_group_jpg_paths(jpg_paths: list[str]) -> tuple[list[str], list[str]]:
    """Resolve JPG paths on disk (WSL/localize), mirroring compose preflight."""
    from app.services.helicon_service import resolve_existing_image_path

    resolved: list[str] = []
    missing: list[str] = []
    for path in list(jpg_paths or []):
        found = resolve_existing_image_path(path)
        if found:
            resolved.append(found)
        else:
            missing.append(path)
    return resolved, missing


def inspect_organize_group(
    grouping: SpecimenGrouping,
    group_index: int,
) -> OrganizeGroupState:
    """Classify one group before organize starts."""
    group = next(
        (g for g in list(getattr(grouping, "groups", []) or [])
         if getattr(g, "group_index", None) == group_index),
        None,
    )
    if group is None:
        return OrganizeGroupState("missing-group")

    if getattr(group, "status", None) == "organized":
        return OrganizeGroupState("already-organized", group=group)

    if not getattr(group, "composed_tiff_path", None):
        return OrganizeGroupState("missing-tiff", group=group)

    is_tif_only = not bool(getattr(group, "jpg_paths", None))
    return OrganizeGroupState(
        "ready-tif-only" if is_tif_only else "ready-jpg-archive",
        group=group,
        is_tif_only=is_tif_only,
    )


def prepare_existing_tiff_group(
    db,
    *,
    active_uid: Optional[str],
    jpg_paths: list[str],
    tiff_path: str,
    project_dir: str,
    incoming_subdir: str = "incoming-jpg",
    results_subdir: str = "results",
) -> ExistingTiffGroupResult:
    """Register selected JPGs + an existing TIFF as a composed group.

    With an active UID, the TIFF is first renamed to that UID's next result
    sequence. Without an active UID, the group is stored under the ad-hoc UID
    and the TIFF filename is left untouched.
    """
    if not db:
        raise ValueError("db is required")
    active = str(active_uid or "").strip()
    uid = active or ADHOC_GROUPING_UID
    final_tiff_path = str(tiff_path or "")
    original_tiff_path = final_tiff_path
    renamed_tiff = False

    if active:
        from app.services.organize_service import organize_preview, rename_tiff

        preview = organize_preview(
            db,
            uid,
            os.path.join(project_dir, results_subdir),
            os.path.join(project_dir, incoming_subdir),
        )
        if os.path.basename(final_tiff_path) != preview.suggested_tiff_name:
            final_tiff_path = rename_tiff(final_tiff_path, preview.suggested_tiff_name)
            renamed_tiff = True

    grouping = load_grouping(db, uid)
    next_idx = max((g.group_index for g in grouping.groups), default=-1) + 1
    new_group = Group(
        group_index=next_idx,
        jpg_paths=list(jpg_paths or []),
        composed_tiff_path=final_tiff_path,
        status="composed",
    )
    grouping.groups.append(new_group)
    try:
        save_grouping(db, uid, grouping.groups, clean_phantoms=False)
    except Exception:
        try:
            grouping.groups.remove(new_group)
        except ValueError:
            pass
        if renamed_tiff:
            _rollback_tiff_rename(final_tiff_path, original_tiff_path)
        raise
    return ExistingTiffGroupResult(
        uid=uid,
        group_index=next_idx,
        grouping=grouping,
        tiff_path=final_tiff_path,
        renamed_tiff=renamed_tiff,
    )


def _rollback_tiff_rename(current_path: str, original_path: str) -> None:
    """Best-effort rollback after rename succeeded but grouping save failed."""
    if not current_path or not original_path or current_path == original_path:
        return
    if not os.path.isfile(current_path) or os.path.exists(original_path):
        return
    try:
        os.replace(current_path, original_path)
    except OSError:
        pass


def _selected_group_set(selected_group_indexes: Iterable[int] | None) -> set[int]:
    selected: set[int] = set()
    for value in list(selected_group_indexes or []):
        try:
            selected.add(int(value))
        except (TypeError, ValueError):
            continue
    return selected


def compose_batch_queue(
    grouping: SpecimenGrouping,
    selected_group_indexes: Iterable[int] | None = None,
) -> tuple[int, ...]:
    """Return groups that still need TIFF composition."""
    selected = _selected_group_set(selected_group_indexes)
    queue: list[int] = []
    for group in list(getattr(grouping, "groups", []) or []):
        group_index = getattr(group, "group_index", None)
        if group_index is None:
            continue
        if getattr(group, "composed_tiff_path", None):
            continue
        if selected and group_index not in selected:
            continue
        queue.append(int(group_index))
    return tuple(queue)


def organize_batch_targets(
    grouping: SpecimenGrouping,
    selected_group_indexes: Iterable[int] | None = None,
) -> tuple[int, ...]:
    """Return composed, not-yet-organized groups that should be archived."""
    selected = _selected_group_set(selected_group_indexes)
    targets: list[int] = []
    for group in list(getattr(grouping, "groups", []) or []):
        group_index = getattr(group, "group_index", None)
        if group_index is None:
            continue
        if not getattr(group, "composed_tiff_path", None):
            continue
        if getattr(group, "status", None) == "organized":
            continue
        if selected and group_index not in selected:
            continue
        targets.append(int(group_index))
    return tuple(targets)


def plan_organize_gate_check(
    grouping: SpecimenGrouping,
    group: Group,
    *,
    has_project: bool,
    allow_single_jpg: bool,
    silent_batch: bool,
) -> OrganizeGateCheckPlan:
    """Plan the legacy organize gate call without showing UI.

    The gate implementation still lives in ``organize_service``. This function
    keeps the view from duplicating the branching rules for tif-only groups,
    selected JPG + external TIFF, and silent batch mode.
    """
    if not has_project:
        return OrganizeGateCheckPlan(required=False)

    is_tif_only = bool(getattr(group, "composed_tiff_path", None)) and not bool(
        getattr(group, "jpg_paths", None)
    )
    if allow_single_jpg or is_tif_only:
        if silent_batch:
            return OrganizeGateCheckPlan(required=False)
        return OrganizeGateCheckPlan(
            required=False,
            skip_gate=True,
        )

    groups_as_dicts = tuple(
        {"jpgPaths": list(getattr(g, "jpg_paths", []) or [])}
        for g in list(getattr(grouping, "groups", []) or [])
    )
    return OrganizeGateCheckPlan(
        required=True,
        groups_as_dicts=groups_as_dicts,
        allow_inactive=bool(silent_batch),
        prompt_on_error=not bool(silent_batch),
        silent_skip_on_error=bool(silent_batch),
    )


def plan_archive_worker(
    *,
    project_dir: str,
    results_subdir: str,
    tiff_path: str,
    delete_jpg_after_archive: bool,
) -> ArchiveWorkerPlan:
    """Plan archive worker output paths without starting the worker."""
    archive_target = plan_archive_target(project_dir, results_subdir, tiff_path)
    return ArchiveWorkerPlan(
        archive_target=archive_target,
        delete_jpg=bool(delete_jpg_after_archive),
        existing_zip_exists=os.path.isfile(archive_target.existing_zip),
    )
