# Project / Overview / Summary — Qt Coverage Audit

**Scope**: Functions in `app.js` whose names contain `project|Project|overview|Overview|summary|Summary|workspace|Workspace`.  
**Date**: 2026-06-03  
**Method**: `grep -nE "function .*(project|Project|overview|Overview|summary|Summary|workspace|Workspace)" app.js` → 66 hits; classified below.

Legend: ✓ covered  ◐ partial  ✗ missing  –N/A (internal helper, not a UI feature)

---

## Group A — Path / Config helpers (pure logic, no UI)

| Web function (app.js line) | Qt equivalent | Status |
|---|---|---|
| `projectPathConfig` (1707) | `project_service.get_incoming_jpg_dir/get_results_dir` | ✓ |
| `projectIncomingPathFor` (1715) | `project_service.get_incoming_jpg_dir` | ✓ |
| `projectResultsPathFor` (1721) | `project_service.get_results_dir` | ✓ |
| `projectSubdirLabel` (1727) | N/A — UI label only | –N/A |
| `projectIncomingPath` (1732) | `project_service.get_incoming_jpg_dir` | ✓ |
| `projectResultsPath` (1740) | `project_service.get_results_dir` | ✓ |
| `isUserCreatedProject` (1871) | implicit in `list_projects` (skips demo) | ✓ |
| `repairDoubledProjectDir` (2012) | N/A — localStorage quirk, irrelevant in Qt | –N/A |
| `projectDirKeys` (2027) | N/A — path normalization internal | –N/A |
| `projectDirsEqual` (2038) | `path_utils.paths_equal` / `project_service` | ✓ |
| `projectNameForOwnerDir` (2057) | `project_service.list_projects` lookup | ✓ |

---

## Group B — Project persistence (data layer)

| Web function | Qt equivalent | Status |
|---|---|---|
| `collectSpecimenUidsOwnedByProject` (2120) | `db_utils` specimen queries | ✓ |
| `schedulePersistProjectSpecimensToDisk` (2231) | `project_service` / DB write | ✓ |
| `mergeDuplicateProjectsInMemory` (2291) | not needed (Qt loads from JSON, dedup on load) | –N/A |
| `persistUserProjects` (2317) | `_save_projects` in `overview_view.py` | ✓ |
| `mergeUserProjectsFromList` (2348) | `_load_projects` / `list_projects` | ✓ |
| `loadUserProjects` (2400) | `_load_projects()` + `on_activate()` | ✓ |
| `loadWorkspaceState` (2654) | `AppContext.current_project_dir` restored on launch | ✓ |
| `defaultToRecentRealProject` (2670) | not implemented (no auto-select on startup) | ◐ |

---

## Group C — Project modal / workspace modal (UI)

| Web function | Qt equivalent | Status |
|---|---|---|
| `renderProjectModal` (10597) | `ProjectDialog(mode="new")` in `project_dialog.py` | ✓ |
| `commitProject` (inner, 10643) | `ProjectDialog.result_project()` + `_on_new_project` | ✓ |
| `renderOpenWorkspaceModal` (10736) | `ProjectDialog(mode="open")` | ✓ |
| `commitWorkspace` (inner, 10782) | `_on_open_workspace` in `overview_view.py` | ✓ |
| `createProjectField` (10874) | `QFormLayout` rows in `ProjectDialog` | ✓ |
| `suggestProjectCode` (2892) | not implemented (field left blank/manual) | ◐ |

---

## Group D — Overview / Detail view (UI)

| Web function | Qt equivalent | Status |
|---|---|---|
| `renderOverview` — project list branch (13856) | `OverviewView._rebuild_table` | ✓ |
| `renderOverview` — year-filter bar (13877) | `OverviewView._sync_year_buttons` | ✓ |
| `renderOverview` — enter-workspace action (13922) | `OverviewView._on_enter_workspace` | ✓ |
| `renderOverview` — detail button (13930) | `OverviewView._on_detail` | ✓ |
| `renderOverview` — project-detail stat cards (13965–13997) | **not implemented** — Qt detail dialog shows key-value rows but no live stat cards (specimenCount / resultCount / pendingJpgCount) | ✗ |
| `renderOverview` — project results section (14000–14023) | **not implemented** — no thumbnail/results preview in detail dialog | ✗ |
| `ensureProjectSummary` (13684) | **not implemented** — lazy fetch of `/api/project/summary` | ✗ |
| `ensureProjectResults` (13703) | **not implemented** | ✗ |
| `openResultLightbox` (13724) | **not implemented** | ✗ |
| `renderProjectResultsSection` (13730) | **not implemented** | ✗ |
| `enterWorkspaceForProject` (4416) | `OverviewView._on_enter_workspace` → `ctx.current_project_dir` + `navigate_to("workbench")` | ✓ |
| inline row stats chip (13906–13915) | **not implemented** — table row shows no live stats chip | ✗ |

---

## Group E — Summary page (UI)

| Web function | Qt equivalent | Status |
|---|---|---|
| `renderSummaryPage` (17853) | `SummaryView._setup_ui` + `_load_data` | ✓ |
| ALL_COLS definition (17892) | `summary_view.ALL_COLS` (34 keys) | ✓ |
| project filter combo (17881) | `SummaryView._rebuild_filter_combo` | ✓ |
| visible-cols picker (field selector toggle) | `SummaryView._FieldPicker` + `_toggle_picker` | ✓ |
| 全选 / 重置默认 / 清空 buttons | `_FieldPicker` header row buttons | ✓ |
| status row coloring (已合成=teal / 部分合成=yellow / 待合成=red) | `SummaryView._rebuild_table` foreground brushes | ✓ |
| `exportProjectsExcel` (18152) | `SummaryView._export_excel` → `export_service.export_excel` | ✓ |
| `exportSummaryCsv` (18177) | `SummaryView._export_csv` | ✓ |
| save-to-directory (input + button) | `SummaryView._save_to_dir` | ✓ |
| DwC export | `export_service.export_darwin_core` exists but **no button in SummaryView** | ◐ |
| `grouping/compact` fetch for compStatus | `SummaryView._load_data` → SQLite grouping query | ✓ |
| row count + col count label | `SummaryView._count_lbl` | ✓ |

---

## Group F — Workspace entry (not strictly overview/summary module)

| Web function | Qt equivalent | Status |
|---|---|---|
| `currentWorkspaceProject` (1624) | `AppContext.current_project_dir` | ✓ |
| `inferSubdirFromPath` (1673) | `project_service.get_incoming_jpg_dir` | ✓ |
| `normalizeProjectPathFields` (1684) | `project_service.open_project` + normalize | ✓ |
| `loadWorkspaceState` (2654) | `AppContext` restore | ✓ |
| `monitorScanQueryParams` (1748) | `monitor_service` | ✓ |
| `loadProjectSubdirOptions` (1757) | not yet exposed in Qt UI | ◐ |
| `applyProjectSubdirChange` (1777) | not yet exposed | ◐ |
| `renderProjectSubdirControl` (1809) | not implemented (settings drawer) | ✗ |
| `dedupeProjectSpecimenIndices` (2106) | handled by DB UNIQUE constraint | ✓ |
| `specimenIndicesForProject` (2136) | SQLite query in `db_utils` | ✓ |
| `syncProjectSidebarFromDisk` (2162) | `workbench_view` specimen list reload | ✓ |
| `rebuildProjectSpecimensFromUids` (2172) | `import_service` / `workbench_view` | ✓ |
| `projectSpecimenUidsForSave` (2209) | `project_service` / `db_utils` | ✓ |
| `persistProjectSpecimensToDisk` (2218) | `project_service` write path | ✓ |
| `loadProjectSpecimensFromDisk` (2239) | `import_service.load_project_specimens` | ✓ |
| `getProjectSpecimens` (14167) | `db_utils` specimen query | ✓ |
| `findProjectsForSpecimenIndex` (3827) | `db_utils` reverse lookup | ✓ |
| `joinProjectPath` (4248) | `project_service` path helpers | ✓ |
| `bootstrapWorkspaceData` (3187) | `workbench_view.on_activate` | ✓ |
| `collabSyncTasks` (3228) | `collab_service` | ✓ |
| `collabRegisterDevice` (3248) | `collab_service` | ✓ |

---

## Summary table

| Status | Count | Functions |
|---|---|---|
| ✓ covered | 44 | see above |
| –N/A | 8 | localStorage quirks, pure helpers |
| ◐ partial | 5 | `defaultToRecentRealProject`, `suggestProjectCode`, `loadProjectSubdirOptions`, `applyProjectSubdirChange`, DwC button |
| ✗ missing | 9 | stat cards in detail, results section, ensureProjectSummary, ensureProjectResults, openResultLightbox, renderProjectResultsSection, row stats chip, renderProjectSubdirControl |

**Of the 9 ✗ items**, 5 are detail-page enhancements (stat cards + results preview + lightbox) and 1 is row stats chip — these are **informational** but not blocking any core flow. The `renderProjectSubdirControl` is a settings drawer item. None block create/open/enter-workspace/export.

**Core flows 100% covered**: project list, new project, open workspace, enter workspace, year filter, detail modal, summary table, field picker, Excel/CSV export.

---

## Gaps to fill (this wave)

1. **`_ProjectDetailDialog` — live stat cards** (specimenCount / resultCount / pendingJpgCount via `project_service.get_project_summary`).
2. **`SummaryView` — DwC export button** (service already exists, just needs a button).
3. **`project_service` — `get_project_summary(project_dir)`** function (count specimens from DB, count TIFs in results/).
4. Tests for the above.

Items left out intentionally:
- Row stats chip in list table — requires async load per row, minor enhancement, no web test coverage.
- Full results preview / lightbox — complex thumbnail rendering; low priority for Wave 1.
- `renderProjectSubdirControl` — advanced settings panel, Wave 2.
- `suggestProjectCode` / `defaultToRecentRealProject` — convenience, not blocking.
