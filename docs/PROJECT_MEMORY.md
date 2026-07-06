# Project Memory

This file records durable project decisions that the user has had to repeat many times. Future Codex / Claude Code sessions must read this before changing core workflow logic.

## Regression discipline for core workflow changes

The user is a beginner and relies on agents to keep the application coherent. Do not fix one visible bug by casually changing a shared workflow and leaving adjacent features unverified.

## Preserved UI fixes and no-regression areas

The user explicitly asked that successful fixes must be recorded and preserved. Do not casually rewrite these areas while fixing another visible issue:

- Windows/WSL path compatibility is required. A project saved as `/mnt/n/...` in WSL and reopened as `N:\...` on Windows must still resolve to the same workspace, including `owner_project_dir` lookups for the specimen sidebar and UID checks.
- The workbench specimen sidebar must show records from the current workspace even when the DB stores the owner path in the other runtime's syntax. Do not return to `owner_project_dir = current_project_dir` only.
- Pending photo cards must show real JPG/TIFF thumbnails when the files are decodable. Do not replace them with generic icon-only cards or a hidden/lazy path that leaves the user seeing no pictures.
- Organized results must show real TIFF thumbnails/previews when the TIFF is decodable. ZIP may remain a file/archive icon, but TIFF result cards should not be icon-only by default.
- The screenshot tool needs a visible, direct top-bar entry. It must not be discoverable only through a nested toolbox menu.
- When touching one of these areas, run the focused tests for that area and report exactly what was tested. Do not claim unrelated workflows were verified.

Do not tell the user a change is globally "fixed", "complete", "10/10", or "done" when only a local slice was changed or tested. Completion reports must distinguish:

- changed scope: exactly what was edited;
- verified scope: exact commands/tests/manual checks that passed;
- unverified scope: related workflows not exercised;
- residual risk: what can still break and why.

Before changing core workflow logic, the agent must:

- Identify the affected workflow area: naming, right rail save, compose/organise, TIFF preview, label printing, sidebar activation, collection autofill, or collaboration.
- State the likely adjacent features that can regress.
- Add or update at least one focused regression test when fixing a bug or changing behavior.
- Prefer a deep Module with one Interface over duplicating conditions across views. Good seams concentrate behavior and tests.
- Preserve user-repeated requirements already recorded in this file.
- Run the focused regression suite for the touched area before reporting completion.

The helper script is:

```bash
python scripts/run_core_regression.py
python scripts/run_core_regression.py naming
python scripts/run_core_regression.py workbench compose
python scripts/run_core_regression.py labels
python scripts/run_core_regression.py collab
python scripts/run_core_regression.py all-core
```

Default `quick` is the minimum smoke gate for small workflow edits. If the touched area is known, run its named suite too. If a test cannot be run, report that explicitly and explain the remaining risk.

## Windows/WSL same-workspace path scenario

User-confirmed scenario: the same specimen-photo workspace may be processed from WSL and later opened from Windows, or the other way around. Example: WSL stores a recent project as `/mnt/n/claude/zhengli`; Windows must treat that as the same workspace as `N:\claude\zhengli`.

Requirements to preserve:

- Persisted project paths, recent-workspace entries, `owner_project_dir`, UID checks, summaries, and file-open actions must tolerate both `/mnt/<drive>/...` and `<Drive>:\...` forms.
- UI entries such as “最近使用 / 磁盘目录” should show and operate on the path form usable by the current runtime, without corrupting the stored project identity.
- When the app runs in WSL but opens a folder on a mounted Windows drive, use the Windows Explorer path (`N:\...`) so the Windows desktop can open it.
- Do not narrow the model back to string equality on one path spelling. Use the shared path helpers and focused regression tests before changing this area.
- Once the user confirms a path-compatibility implementation works for this scenario, future agents must not casually rewrite it while fixing unrelated workflow issues.

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
- After `合成+整理` or `整理` succeeds, the ZIP consumes the source JPGs. The product default is to delete loose JPG files after archive verification passes; users may explicitly turn on keeping loose JPGs. Whether deleted or explicitly kept, consumed JPGs must not continue to appear as pending photos.
- `整理` / archive must not automatically delete TIFF results, but TIFFs are not sacred or undeletable: if a composed/imported TIFF is wrong, the user may explicitly delete it or undo the compose after confirmation.
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

## Workbench incoming/results media boundary

The user has repeatedly clarified the main workbench file lifecycle:

- `incoming-jpg/` is the pending workspace, not a JPG-only directory.
- New-project camera intake puts JPG originals in `incoming-jpg/`.
- TIFFs produced by this software's `合成` are also temporary pending files in `incoming-jpg/` until `整理` runs.
- TIFFs produced by external software must be importable through the main `添加照片` action into `incoming-jpg/`, then selected and processed with `整理`.
- `results/` is the organized output area. `整理` moves the TIFF and generated ZIP there.
- Do not split the main `添加照片` import as "JPG to incoming, TIF to results". That skips the required pending/organize state and makes imported TIFFs disappear from the queue.

## Workbench multi-scenario design intent

Do not interpret the workbench as a single rigid "shoot in one folder, then process that one folder" pipeline. The product must support multiple real photo-working styles, and the user's old habit of shooting JPGs and externally producing TIFFs in the same directory is one compatibility scenario, not the whole design.

Durable intent:

- The workbench should tolerate different entry points: new-project camera intake, manual file import, selecting existing JPGs, importing or discovering external TIFFs, software-created TIFFs, supplementary archival, and later binding of existing results.
- `incoming-jpg/` is a pending workspace abstraction. It may be the software-created intake folder, or it may point at / receive files from a directory that already matches the user's existing habits.
- Active UID is a default ownership and naming context, not the only way into the workflow.
- Manual selection is explicit user intent. If the user selects JPGs, or JPGs plus one TIFF, the software should process that selection according to the relevant mode instead of forcing a single activation-first flow.
- External composition tools such as Helicon used outside this software are first-class. A TIFF does not have to be generated by this app before it can be organized, named, archived, or assigned.
- Avoid hard-coding assumptions like "TIFFs only appear after in-app compose", "incoming means only JPG", or "activation is required before selected files can be handled".
- When adding or changing workflow behavior, preserve these scenarios side by side instead of simplifying the model to one favored path.

中文说明：

- 不要把这个软件理解成单一流程软件。它不是只服务“一个拍摄目录流水线”，而是要兼容多种真实使用方式。
- 用户过去“在同一个目录拍 JPG、外部合成 TIF、再整理”的习惯只是必须兼容的一种场景，不是唯一中心。
- 软件应该同时支持：新项目自动入库、手动添加照片、选中已有 JPG 合成、外部 TIF 入库/发现后整理、软件内合成后整理、补处理、已有成果后续绑定到编号。
- `incoming-jpg/` 的本质是待处理区，不是只能放 JPG 的目录；TIF 可以先在这里等待整理。
- 激活编号只是默认归属和命名上下文，不是所有操作的入口条件。
- 手动选中文件就是明确意图。选中 JPG、或选中 JPG + 1 个 TIF 时，应按对应模式处理，不要强行套“必须先激活编号”的单一路径。
- 外部 Helicon 或其他外部软件生成的 TIF 是正常工作方式，不能假设 TIF 一定由本软件生成。
- 后续改工作台逻辑时，要保留这些场景并行存在，不能为了代码简单把模型收窄成一种固定流程。
