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
| `renderTaxonChart` | 11523 | `_on_chart_toggle` (info stub) | ◐ | chart not implemented; info dialog shown |
| `taxonQueryPayload` / `fetchTaxonomyTable` | 11569 | `TaxonomyView._load_page` | ✓ | |
| `clearTaxonRowSelection` | 11621 | `_on_deselect` | ✓ | |
| `cloneTaxonPredicate` | 11626 | N/A | N/A | server-side predicate cloning; Qt does client filter |
| `fetchTaxonFacetValues` | 11633 | **MISSING** | ✗ | facet dropdowns per-column with value counts |
| `openTaxonFacetMenu` | 11656 | **MISSING** | ✗ | pop-up facet filter menu per column header |
| `taxonFacetValueChecked` / `toggleTaxonFacetValue` | 11673 | **MISSING** | ✗ | facet checkbox state |
| `renderTaxonFacetMenu` | 11897 | **MISSING** | ✗ | facet menu rendering |
| `startTaxonomyWormsJob` | 11701 | `_on_worms_update` (info stub) | ◐ | triggers WoRMS service job; GUI shows placeholder info |
| `taxonExport` | 11718 | `_on_export` | ✓ | xlsx + csv |
| `resolveTaxonMapping` | 11742 | **MISSING** | ✗ | maps WoRMS result back to a taxonomy row |
| `openTaxonRowMenu` / `renderTaxonRowMenu` | 11755 | **MISSING** | ✗ | per-row context menu (WoRMS search, review, etc.) |
| `searchWormsForTaxonRow` | 11777 | **MISSING** | ✗ | fires WoRMS lookup for selected row |
| `renderTaxonJobPanel` | 11979 | **MISSING** | ✗ | shows in-progress WoRMS batch job panel |
| `renderTaxonReviewModal` | 12012 | **MISSING** | ✗ | review modal for WoRMS match results |
| `openTaxonomyTableModal` | 12036 | `_RecordDialog` | ✓ | add/edit dialog |
| `renderTaxonomyPage` | 12058 | `TaxonomyView` | ✓ | full page with header/toolbar/table/pager |
| `selectWormsTaxon` | 12654 | **MISSING** | ✗ | applies selected WoRMS candidate to taxonomy row |

---

## What was implemented in this audit

### Added to `taxonomy_service.py`
1. `find_seed_by_level(level, value)` — find first seed entry matching a level/value pair (mirrors `findSeedByLevel`)
2. `validate_taxonomy_chain(sp_fields)` — check 4-level self-consistency; returns `{ok, mismatches}` (mirrors `validateTaxonomyChain`)
3. `apply_taxonomy_authority(sp_fields, validation)` — overwrite upper fields from best seed match (mirrors `applyTaxonomyAuthority`)
4. `taxon_entry_cn(entry, key, cn_key)` — return CN from entry or look up seed (mirrors `taxonEntryCn`)
5. `find_user_entry_for_current(sp_fields)` — find user record matching specimen's 4-tuple (mirrors `findUserEntryForCurrent`)
6. `apply_draft_to_specimen(sp_fields, draft)` — copy 8 draft fields back to specimen dict (mirrors `applyTaxonDraftToSpecimen`)

### Fixed in `taxonomy_input.py`
7. `_on_editing_finished` — on exact-match hit now calls `_commit_candidate` (ancestor fill), not just `_on_editing_finished` no-op path

---

## Honest remaining gaps (not fixed here)

| Gap | Reason not fixed |
|---|---|
| Facet filter UI (4 functions) | Complex server-side feature; requires dedicated `_TaxonFacetPanel` widget; out of single-session scope |
| WoRMS row context menu / job panel / review modal / `selectWormsTaxon` | WoRMS module scope; stubs acceptable until WoRMS view is wired |
| `renderTaxonChart` — actual chart | Needs data aggregation + charting widget (e.g., pyqtgraph); stub is honest |
| Workbench inline edit modal (workbench → taxonomy panel link) | Cross-module wiring; taxonomy panel emits `value_committed`; consumer wires it |
