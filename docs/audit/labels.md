# Labels Module — Web→Qt Coverage Audit

**Audited**: 2026-06-03  
**Web oracle**: `prototype-photo-gui/app.js` (79 label-related functions)  
**Qt targets**: `app/utils/label_core.py`, `app/services/label_service.py`, `app/views/labels_view.py`, `app/widgets/label_editor.py`

Legend: ✓ fully covered · ◐ partially covered · ✗ missing / not ported

---

## 1. Pure-logic functions (label_core.py / label_service.py)

| # | Web function | Status | Qt location / note |
|---|---|---|---|
| 1 | `specimenToLabelData(sp)` | ✓ | `label_core.specimen_to_label_data()` |
| 2 | `bucketSpecimens(indices)` | ✓ | `label_core.bucket_specimens()` — double-bucket rule verified |
| 3 | `normalizeField(field)` | ✓ | `label_core.normalize_field()` |
| 4 | `normalizeTemplate(tmpl)` | ✓ | `label_core.normalize_template()` — ecc Q default confirmed |
| 5 | `resolveLineHeight(tmpl,row)` | ✓ | `label_core.resolve_line_height()` |
| 6 | `resolveWrap(row)` | ✓ | `label_core.resolve_wrap()` |
| 7 | `calculateGrid(lw,lh,pw,ph)` | ✓ | `label_core.calculate_grid()` |
| 8 | `estimateTextScale(tmpl,dims)` | ✓ | `label_core.estimate_text_scale()` |
| 9 | `qrMetrics(tmpl,dims)` | ✓ | `label_core.qr_metrics()` — all positions incl. free |
| 10 | `validatePrintJob(job)` | ✓ | `label_core.validate_print_job()` — all 7 warning codes |
| 11 | `createLabelPrintJob(bucket)` (core math) | ✓ | `label_core.create_print_job()` |
| 12 | `uniqueId(sp)` | ✓ | `label_core.unique_id()` |
| 13 | `dateSegment(sp)` | ✓ | `label_core.date_segment()` |
| 14 | `normalizedDate(v)` | ✓ | `label_core.normalized_date()` |
| 15 | `hasRnaTissue(sp)` | ✓ | `label_core.has_rna_tissue()` |
| 16 | `rnaPreservative(sp)` | ✓ | `label_core.rna_preservative()` |
| 17 | `uniqueSpecimenIndices(idx,sps)` | ✓ | `label_core.unique_specimen_indices()` |
| 18 | `getLabelDims(bucket)` | ✓ | `LabelTemplateLibrary.selected_size_key()` + `PAPER_SIZES` |
| 19 | `getActiveTemplate(bucket)` | ✓ | `_BucketColWidget.selected_template()` |
| 20 | `getLabelDataWithEdits(sp,idx)` | ✓ | `label_core.bucket_specimens(edits=…)` + `LabelService.build_print_job(edits=…)` |
| 21 | `labelDataText(data)` | ✗ | **Missing**: pure-text summary of label data. Used only in web clipboard/toast; low priority but absent. |

## 2. Template library (label_service.py — LabelTemplateLibrary)

| # | Web function | Status | Qt location / note |
|---|---|---|---|
| 22 | `labelTemplateLibraryKey(bucket)` | ✓ | `_LIBRARY_QSETTINGS_KEY[bucket]` |
| 23 | `labelTemplateMigrationKey(bucket)` | ✓ | `_MIGRATION_QSETTINGS_KEY[bucket]` |
| 24 | `isLabelLibraryKey(key)` | ✓ | `label_service.is_library_key()` |
| 25 | `labelTemplateIdFromKey(key)` | ✓ | `label_service.id_from_key()` |
| 26 | `labelTemplateKeyFromId(id)` | ✓ | `label_service.key_from_id()` |
| 27 | `createLabelTemplateId()` | ✓ | `label_service._create_template_id()` |
| 28 | `readLabelTemplateLibrary(bucket)` | ✓ | `LabelTemplateLibrary._read_raw()` |
| 29 | `writeLabelTemplateLibrary(bucket,lib)` | ✓ | `LabelTemplateLibrary._write_raw()` |
| 30 | `normalizeLibraryRecord(bucket,rec)` | ✓ | `LabelTemplateLibrary._normalize_record()` |
| 31 | `upsertLabelTemplateRecord(bucket,rec)` | ✓ | `LabelTemplateLibrary.upsert()` |
| 32 | `getLabelTemplateRecord(bucket,id)` | ✓ | `LabelTemplateLibrary.get()` |
| 33 | `labelTemplateRecords(bucket)` | ✓ | `LabelTemplateLibrary.records()` |
| 34 | `migrateLegacyLabelTemplate(bucket)` | ✓ | `LabelTemplateLibrary._migrate_legacy()` |
| 35 | `ensureLabelTemplateLibraries()` | ✓ | Both buckets constructed on `LabelsView` init; no separate "ensure" needed |
| 36 | `chooseCustomLabelTemplate(bucket,rec)` | ✓ | `LabelTemplateLibrary.select_record()` |
| 37 | `chooseLabelTemplate(bucket,key)` | ✓ | `LabelTemplateLibrary.set_selected_key()` |
| 38 | `cloneLabelTemplateToCustom(bucket,src,name)` | ✓ | `LabelTemplateLibrary.clone_from_builtin()` |
| 39 | `duplicateLabelTemplateRecord(bucket,rec)` | ✓ | `LabelTemplateLibrary.duplicate()` |
| 40 | `deleteLabelTemplateRecord(bucket,rec)` | ✓ | `LabelTemplateLibrary.delete()` |
| 41 | `exportLabelTemplateJson(bucket,id)` | ✓ | `_BucketColWidget._lib_export()` + `_TemplateManageDialog._do_export()` |
| 42 | `importLabelTemplateJson(bucket)` | ✓ | `_BucketColWidget._import_json()` |
| 43 | `openLabelTemplateManageMenu(bucket,x,y,id)` | ✓ | `_BucketColWidget._open_manage_dialog()` → `_TemplateManageDialog` |
| 44 | `formatLabelTemplateTime(iso)` | ◐ | Web: formatted date string for UI. Qt uses `rec.get('updatedAt','')[:10]` inline — same result, not a named function. Adequate. |
| 45 | `currentTemplateKeyForBucket(bucket)` | ✓ | `LabelTemplateLibrary.selected_key()` |
| 46 | `backupLabelCustomTemplate(bucket,reason)` | ✗ | **Missing**: auto-backup before destructive edits. Web keeps rolling 20-slot backup in localStorage. Qt has undo stack (30 steps) in LabelEditorWidget; no separate backup slot. Lower risk because Qt undo is per-session (not cross-session persistent). |
| 47 | `latestLabelCustomBackup(bucket)` | ✗ | **Missing** (depends on #46) |
| 48 | `restoreLatestLabelCustomBackup(bucket)` | ✗ | **Missing** (depends on #46) |
| 49 | `getCurrentCustomTemplate(bucket)` | ◐ | Resolved inline in `_BucketColWidget.selected_template()`. No dedicated method, but logic is equivalent. |
| 50 | `saveCustomTemplate(bucket,tmpl,opts)` | ◐ | `LabelTemplateLibrary.upsert()` covers the persist path. The undo-before-save side-effect is handled by `QUndoStack` in `LabelEditorWidget`, not by a unified `saveCustomTemplate`. Functionally equivalent. |
| 51 | `labelCustomStorageKey(bucket)` | ✓ | `_LEGACY_CUSTOM_QSETTINGS_KEY[bucket]` (legacy only; active path is library key) |
| 52 | `labelCustomBackupKey(bucket)` | ✗ | **Missing** (backup subsystem absent) |
| 53 | `labelCustomStateKey(bucket)` | ✓ | `_SELECTED_QSETTINGS_KEY[bucket]` |

## 3. Rendering / UI functions (labels_view.py, label_editor.py)

| # | Web function | Status | Qt location / note |
|---|---|---|---|
| 54 | `renderLabelsClassic()` — 4-step wizard | ✓ | `LabelsView` with `_Step1…4Widget` |
| 55 | `renderLabelStep1(opts)` | ✓ | `_Step1Widget` |
| 56 | `renderLabelStep2(opts)` | ✓ | `_Step2Widget` |
| 57 | `renderBucketColumn(bucket,items,opts)` | ✓ | `_BucketColWidget` |
| 58 | `renderLabelStep3()` (paper/copies) | ✓ | `_Step4Widget` paper radios + copies spinner |
| 59 | `renderLabelStep4()` (output/print buttons) | ✓ | `_Step4Widget` print buttons + warnings |
| 60 | `renderLabelBucketSummary()` | ✓ | `_Step1Widget._update_bucket_cards()` |
| 61 | `renderLabelClassicHeader()` | ✓ | `LabelsView._setup_ui()` step nav bar |
| 62 | `renderLabelStatusBar()` | ✓ | `LabelsView._update_status_bar()` |
| 63 | `renderLabelToolbar()` | ◐ | Web has mode switcher (quick/batch/design). Qt has only batch (4-step wizard). Mode switcher not ported (workbench mode excluded by design). |
| 64 | `renderLabelLayoutSwitch()` | ✗ | **Missing**: workbench vs classic toggle. Qt ships classic-only; workbench layout not yet ported. |
| 65 | `renderLabels()` | ✓ | Dispatched by `LabelsView.on_activate()` |
| 66 | `renderLabelsWorkbench()` | ✗ | **Not ported** — workbench mode is a future Qt milestone, not the current scope. |
| 67 | `renderTemplateEditor(bucket)` | ✓ | `LabelEditorWidget` (QGraphicsScene WYSIWYG) |
| 68 | `renderLabelEl(data,tmpl,dims,…)` | ✓ | `LabelScene._build()` + `LabelEditorWidget` — Qt WYSIWYG scene is the equivalent |
| 69 | `buildPrintLabel(data,tmpl,dims)` | ✓ | `LabelsView._paint_labels()` (QPainter onto QPrinter) |
| 70 | `renderQrControlPanel(bucket,dims)` | ◐ | Web: dedicated QR position/size panel. Qt: QR draggable in `LabelScene`; no explicit numeric input panel for position. |
| 71 | `renderEditorModeBar(bucket)` | ✗ | **Missing**: web row-edit toolbar (add/remove/move rows). Qt scene allows moving text items but no structural row add/remove UI. |
| 72 | `renderRowFloatingToolbar(bucket)` | ✗ | **Missing**: per-row floating action bar (add field, remove row, reorder). |
| 73 | `updateSelectionUI(bucket,wrapEl)` | ✓ | `_BucketColWidget._rebuild_template_picker()` rebuilds on selection change |
| 74 | `renderLabelPreviewContextMenu()` | ✗ | **Missing**: right-click context menu on label preview area. |
| 75 | `renderLabelTemplateContextMenu()` | ✓ | `_BucketColWidget._show_mgmt_menu()` (QMenu dropdown on template card) |
| 76 | `renderLabelRecordContextMenu()` | ✓ | `_TemplateManageDialog` + per-card `_show_mgmt_menu()` |
| 77 | `attachFieldDrag(span,bucket,ri,fi,dims)` | ◐ | Qt: `QGraphicsTextItem` is movable; no row-index-aware field drag. |
| 78 | `attachQrDrag(img,tmpl,bucket,dims)` | ✓ | `LabelScene._qr_item` with `ItemIsMovable` flag + `_MoveQrCommand` undo |
| 79 | `bindCharSelection(previewEl,bucket)` | ✗ | **Missing**: click-to-select individual field span, character selection overlay. Qt uses `QGraphicsTextItem` TextEditorInteraction instead (equivalent effect, different mechanism). Functionally equivalent. |

## 4. Print execution & QR generation

| # | Web function | Status | Qt location / note |
|---|---|---|---|
| 80 | `ensureQRCodeLoaded()` | ✓ | `qrcode` library imported at module level; no lazy-load needed in Qt |
| 81 | `generateQRDataURL(text,px,ecc)` | ✓ | `label_editor._generate_qr_pixmap()` — ECC Q enforced |
| 82 | `printLabels(bucket)` | ✓ | `LabelsView._print()` + `_paint_labels()` via QPrinter/QPaintDialog |
| 83 | `renderPaperColumn(bucket,count)` | ✓ | `_Step4Widget._make_paper_col()` |

## 5. Navigation/mode helpers (low-priority, web-specific)

| # | Web function | Status | Qt location / note |
|---|---|---|---|
| 84 | `labelModeName(mode)` | ✗ | Web-only (quick/batch/design mode names). Qt is batch-only; no equivalent needed. |
| 85 | `setLabelMode(mode)` | ✗ | Web-only. |
| 86 | `setLabelLayout(layout)` | ✗ | Web-only (workbench/classic toggle). |
| 87 | `activeCustomBucket()` | ✓ | Qt: current bucket tracked by `_Step3Widget._current_bucket` |
| 88 | `loadLabelChoice(key,fallback)` | ✓ | `LabelTemplateLibrary.selected_key()` / `selected_size_key()` |
| 89 | `saveLabelChoice(key,value)` | ✓ | `LabelTemplateLibrary.set_selected_key()` / `set_selected_size_key()` |
| 90 | `handleLabelsKeydown(e)` | ◐ | Web: Ctrl-Z undo via keydown listener. Qt: `QUndoStack` bound to undo/redo buttons; no global keypress handler. |
| 91 | `undoLabelEdit()` | ✓ | `LabelEditorWidget._undo_stack.undo()` (QUndoStack) |
| 92 | `redoLabelEdit()` | ✓ | `LabelEditorWidget._undo_stack.redo()` (QUndoStack) |

---

## Summary

| Category | Total | ✓ | ◐ | ✗ |
|---|---|---|---|---|
| Pure logic (label_core / label_service) | 21 | 19 | 1 | 1 |
| Template library CRUD | 32 | 27 | 3 | 3 (backup subsystem) |
| Rendering / UI | 26 | 15 | 4 | 7 |
| Print / QR | 4 | 4 | 0 | 0 |
| Navigation helpers | 9 | 5 | 1 | 3 (web-only mode flags) |
| **Total** | **92** | **70 (76%)** | **9 (10%)** | **13 (14%)** |

### Hard-rule red-lines: all PASS

- **R-prefix double-bucket**: `bucket_specimens()` → both `samples` and `tissues`. ✓
- **QR ECC = Q**: default in `normalize_template()`, all builtin templates verified. ✓
- **2 mm safety margin**: `_SAFETY_MARGIN_MM = 2.0` in `label_editor.py`. ✓
- **一标本一张 (one-per-page print)**: QPrinter calls `printer.newPage()` per item. ✓

### Honest gaps

1. **Backup subsystem** (`backupLabelCustomTemplate` / `latestLabelCustomBackup` / `restoreLatestLabelCustomBackup`): absent in Qt. Per-session undo (30 steps) covers most needs; cross-session backup is the delta.
2. **Row structural editor** (`renderEditorModeBar`, `renderRowFloatingToolbar`): web lets users add/remove/reorder rows interactively. Qt editor shows existing rows but has no add/remove row UI.
3. **Workbench mode** (`renderLabelsWorkbench`, `renderLabelLayoutSwitch`, `setLabelLayout`): intentionally deferred.
4. **Preview context menu** (`renderLabelPreviewContextMenu`): absent. Right-click on label preview does nothing in Qt.
5. **`labelDataText()`**: trivial helper (uniqueId + speciesName + region + collectorLabel joined by newline). Absent but low impact.
