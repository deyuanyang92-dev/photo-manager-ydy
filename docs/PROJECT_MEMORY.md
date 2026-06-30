# Project Memory

This file records durable project decisions that the user has had to repeat many times. Future Codex / Claude Code sessions must read this before changing core workflow logic.

## Workbench selected-JPG compose and organise

The user has repeatedly clarified this rule: selecting JPG files in the monitor is already an explicit manual operation. The software must not block that workflow with "please activate a specimen number first".

Hard requirements:

- `合成` and `合成+整理` on selected JPGs do not require an active UID.
- Active UID is only the default owner for newly shot photos or for workflows with no manual selection. It is not a permission gate.
- If an active UID exists, selected JPGs compose/organise under that active UID and auto-name by that UID's next result sequence. No output-name prompt is needed.
- If no JPGs are selected, the main toolbar `合成` / `合成+整理` must not silently take the active UID's loose JPGs. That requires a separate, explicit auto mode; activation alone is not permission to choose photos.
- If no active UID exists, selected JPGs still compose/organise, but the user must choose one of two paths:
  - assign/move to a target UID, then auto-name by that UID's next sequence;
  - use a free output stem, and use the same stem for both TIF and ZIP.
- When the user confirms "assign/move to a target UID", always write that JPG attribution, even if the target UID was prefilled from an existing hint.
- Existing JPG attribution, left-sidebar selection, and draft naming-preview values are only hints when no UID is active. They must not silently decide the output name.
- Do not silently name selected JPG outputs as `1.tif`, `2.tif`, etc. without active UID. The user must provide a meaningful output stem unless they choose a target UID.
- If a UID already has results, sequence must advance: existing `UID-1-YYYYMMDD.tif` means the next result is `UID-2-YYYYMMDD.tif`.
- `加入分组`, `新组`, and import-to-pending workflows must still work without active UID; use explicit selected JPG ownership or the unassigned task as appropriate.
- After `合成+整理` succeeds, source JPG files may remain on disk according to archive safety settings, but they must no longer appear as pending photos because the ZIP has consumed them.
- `自动归档` is the explicit fast incoming workflow switch. With an active UID and no selected JPG, toolbar `合成` may use that UID's unoccupied attributed JPGs. Without an active UID, it must not guess JPGs.
- External TIF auto organize is part of `自动归档`: when a TIF produced outside the compose button appears, archive it with explicit JPG sources. With an active UID, use that UID's unoccupied JPGs; without an active UID, use manually selected JPGs if present.
- Completion text must follow the real worker completion. Do not mark `合成+整理` or external TIF auto organize complete when the archive worker has only been started.

Primary spec: `docs/specs/photo-grouping-workflow.md`.

Detailed audit: `docs/audit/compose-organize-logic-2026-06-29-10x.md`.

Regression anchors:

- `tests/test_workbench_view.py::TestImplicitCompose`
- `tests/test_workbench_view.py::TestAdhocGrouping`
- `tests/test_monitor_panel.py::TestSelectionAddToGroup`
- `tests/test_monitor_panel.py::TestSelectionAccessors`

Do not reintroduce "请先激活编号" for selected-JPG `合成` or selected-JPG `合成+整理`.
