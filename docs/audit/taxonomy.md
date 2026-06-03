# Taxonomy Module Audit — Web vs. Qt

**Audited:** 2026-06-03
**Scope:** `app.js` taxonomy functions (50 functions) vs.
  `app/services/taxonomy_service.py`, `app/widgets/taxonomy_input.py`,
  `app/views/taxonomy_view.py`

## Legend
- ✓ — equivalent implemented
- ◐ — partially implemented / stubbed
- ✗ — missing
- N/A — intentionally not ported (web-only concern)

---

## Service layer (`taxonomy_service.py`)

| Web function | Line (app.js) | Qt location | Status | Notes |
|---|---|---|---|---|
| `loadTaxonomy` | 769 | `TaxonomyService._read_seed/_read_user` | ✓ | lazy-load on first call |
| `loadWormsTaxonomyCandidates` | 804 | `WoRmsService` (separate module) | ✓ | WoRMS scope, not taxonomy |
| `loadTaxonomyPending` / `saveTaxonomyPending` | 760–767 | N/A | N/A | web-only offline HTTP queue |
| `queueTaxonomyPost` / `flushTaxonomyPending` | 992–1010 | N/A | N/A | web-only offline queue |
| `postTaxonomyLearn` | 984 | `TaxonomyService.learn` | ✓ | direct disk write, no HTTP |
| `taxonFieldDef` | 825 | `_LEVEL_MAP`, `_LEVELS` | ✓ | |
| `findSeedByLevel(level, value)` | 831 | **MISSING** | ✗ | used by `validateTaxonomyChain`; finds first seed entry matching a level value |
| `validateTaxonomyChain(sp)` | 845 | **MISSING** | ✗ | checks species/genus/family/order self-consistency vs. seed; returns `{ok, mismatches[]}` |
| `applyTaxonomyAuthority(sp, validation)` | 897 | **MISSING** | ✗ | overwrites sp upper fields from best seed match |
| `taxonEntryCn(entry, key, cnKey)` | 911 | **MISSING** | ✗ | returns CN from entry or looks up seed; used in `commitTaxonValue` |
| `commitTaxonValue(sp, opts)` | 917 | `TaxonomyService.learn` + `TaxonInputPanel._commit_candidate` | ◐ | `learn` persists; ancestor fill on select is in widget; but `commitTypedTaxon` typed-blur path lacks exact-match ancestry fill |
| `taxonomyCandidates(spKey, sp)` | 1012 | `TaxonomyService._candidates_for` | ✓ | |
| `matchTaxon(query, cands)` | 1073 | `TaxonomyService._match` | ✓ | |
| `exactTaxonCandidate(spKey, sp, value)` | 1091 | partial in `_on_editing_finished` | ◐ | widget does exact-match search but doesn't fall back to unconstrained then apply ancestry |
| `findUserEntryForCurrent(sp)` | 1388 | **MISSING** | ✗ | finds user record matching current specimen's 4-tuple |
| `applyTaxonDraftToSpecimen(sp, draft)` | 1438 | **MISSING** | ✗ | copies 8 draft fields back to specimen object |
| `TaxonomyService.update` | server.js:892 | `TaxonomyService.update` | ✓ | history snapshot, 10-entry cap |
| `TaxonomyService.delete` | server.js:966 | `TaxonomyService.delete` | ✓ | user records only |
| `TaxonomyService.all_records` | server.js:354 | `TaxonomyService.all_records` | ✓ | paginated, source_filter |

**Added in this audit:** `find_seed_by_level`, `validate_taxonomy_chain`,
`apply_taxonomy_authority`, `taxon_entry_cn`, `find_user_entry_for_current`,
`apply_draft_to_specimen` — all added to `taxonomy_service.py`.

---

## Widget layer (`taxonomy_input.py`)

| Web function | Line | Qt location | Status | Notes |
|---|---|---|---|---|
| `ensureTaxonPopupEl` | 1142 | `TaxonPopup.__init__` | ✓ | popup created once |
| `showTaxonPopup` | 1179 | `TaxonPopup.show_below` + `_do_search` | ✓ | |
| `hideTaxonPopup` | 1215 | `TaxonPopup.hide()` | ✓ | |
| `renderTaxonPopup` | 1221 | `TaxonItemDelegate.paint` + `TaxonPopup.populate` | ✓ | drag handle ✓, CN dual column ✓, source badge ✓ |
| `formatTaxonPath` | 1337 | `_format_path` | ✓ | |
| `selectTaxonItem` | 1352 | `TaxonPopup.accept_current` | ✓ | |
| `commitTypedTaxon(spKey, sp, typedValue)` | 1370 | `_on_editing_finished` | ◐ | missing: after exact-match hit, fill ancestors in inputs; currently does not propagate ancestor fills upward |
| `openTaxonEditModal` | 1403 | `_RecordDialog` (taxonomy_view) | ◐ | modal exists in library page; workbench inline edit path not wired to panel |
| `closeTaxonEditModal` | 1433 | `_RecordDialog.reject` | ✓ | |
| `renderTaxonEditModal` | 1515 | `_RecordDialog` | ✓ | form dialog with history button |
| `submitTaxonEditModal` | 1457 | `TaxonomyView._edit_record` | ◐ | library page only; no specimen patch-back path |
| `deleteTaxonModalEntry` | 1493 | `TaxonomyView._delete_record` | ✓ | |

**Widget gap added in this audit:**
`_on_editing_finished` now calls `_commit_candidate` on exact-match (ancestors filled).
Previously it found an exact match but did not call `_commit_candidate`, so ancestor
inputs were never back-filled on blur.

---

## View layer (`taxonomy_view.py`)

| Web function | Line | Qt location | Status | Notes |
|---|---|---|---|---|
| `getVisibleTaxonColumns` | 11505 | `_TaxonTableModel._rebuild_columns` | ✓ | chips control level+lang |
| `renderTaxonChart` | 11523 | `_chart_entries` + `_open_chart_dialog` | ✓ | QPainter bar chart; Top-12 orders; respects filter |
| `taxonQueryPayload` / `fetchTaxonomyTable` | 11569 | `TaxonomyView._load_page` | ✓ | client-side filter + facet + sort |
| `clearTaxonRowSelection` | 11621 | `_on_deselect` | ✓ | |
| `cloneTaxonPredicate` | 11626 | N/A | N/A | server-side predicate cloning; Qt does client filter |
| `fetchTaxonFacetValues` | 11633 | `_TaxonFacetPanel._unique_values` | ✓ | value+count list from all_records |
| `openTaxonFacetMenu` | 11656 | `_on_header_context_menu` + `_open_facet_for_column` | ✓ | right-click header → `_TaxonFacetPanel` |
| `taxonFacetValueChecked` / `toggleTaxonFacetValue` | 11673 | `_TaxonFacetPanel._value_checked` + `_on_item_changed` | ✓ | include/exclude/search/all modes |
| `renderTaxonFacetMenu` | 11897 | `_TaxonFacetPanel` | ✓ | sort buttons + search + checkbox list + actions |
| `startTaxonomyWormsJob` | 11701 | `_on_worms_update` + `_worms_update_record_ids` | ✓ | creates WormsService job, navigates to WoRMS view |
| `taxonExport` | 11718 | `_on_export` | ✓ | xlsx + csv |
| `resolveTaxonMapping` | 11742 | `_on_resolve_mapping` | ✓ | calls `WormsService.resolve_mapping` |
| `openTaxonRowMenu` / `renderTaxonRowMenu` | 11755 | `_on_row_context_menu` | ✓ | QMenu with WoRMS match, review, bulk update, edit/delete |
| `searchWormsForTaxonRow` | 11777 | `_WormsSearchWorker` + `_WormsMatchDialog._do_search` | ✓ | background QThread |
| `renderTaxonJobPanel` | 11979 | `_refresh_job_panel` + job panel frame in `_setup_ui` | ✓ | progress label + bar + pause/resume/retry buttons |
| `renderTaxonReviewModal` | 12012 | `_TaxonReviewDialog` | ✓ | candidates list + 采用 + 标记未找到 |
| `openTaxonomyTableModal` | 12036 | `_RecordDialog` | ✓ | add/edit dialog |
| `renderTaxonomyPage` | 12058 | `TaxonomyView` | ✓ | full page with header/toolbar/table/pager |
| `selectWormsTaxon` | 12654 | `_WormsMatchDialog._on_save` → `_on_resolve_mapping` | ✓ | save WoRMS candidate via resolve_mapping |

---

## What was implemented in this (second) audit session (2026-06-04)

### Added to `taxonomy_view.py`
1. `_TaxonFacetPanel` — per-column facet filter popup (mirrors renderTaxonFacetMenu + support functions)
2. `_WormsSearchWorker` — background QThread for WoRMS search + classification chain
3. `_WormsMatchDialog` — full WoRMS match dialog (mirrors renderWormsMatchModal)
4. `_TaxonReviewDialog` — review auto-found WoRMS candidates (mirrors renderTaxonReviewModal)
5. `_on_row_context_menu` — right-click row context menu (mirrors openTaxonRowMenu/renderTaxonRowMenu)
6. `_on_header_context_menu` + `_open_facet_for_column` — column header → facet panel
7. `_on_facet_filter_applied` + `_on_facet_sort` — facet state management
8. `_on_worms_update` (full) + `_worms_update_record_ids` + `_navigate_to_worms` — WoRMS job creation
9. `_on_worms_match_row` + `_on_review_worms_row` — per-row WoRMS dialogs
10. `_on_resolve_mapping` — apply WoRMS decision (mirrors resolveTaxonMapping)
11. `_refresh_job_panel` — update job progress panel (mirrors renderTaxonJobPanel)
12. `_chart_entries` + `_open_chart_dialog` + `_on_chart_dialog_finished` — real bar chart implementation
13. `_load_page` enhanced — applies `_col_filters`, `_sort_col`/`_sort_dir`, calls `_refresh_job_panel`
14. Job panel frame added to `_setup_ui` layout

### Previously added to `taxonomy_service.py` (audit session 1)
`find_seed_by_level`, `validate_taxonomy_chain`, `apply_taxonomy_authority`,
`taxon_entry_cn`, `find_user_entry_for_current`, `apply_draft_to_specimen`

### Previously fixed in `taxonomy_input.py` (audit session 1)
`_on_editing_finished` — exact-match now calls `_commit_candidate` (ancestor fill)

---

## Honest remaining gaps

| Gap | Status |
|---|---|
| Workbench inline edit modal (workbench → taxonomy panel link) | ◐ — taxonomy panel emits `value_committed`; workbench_view must wire it; out of taxonomy scope |
