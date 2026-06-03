# Workbench Functional Spec

> Oracle: web prototype `app.js` + `server.js` + `monitor-service.js` + `helicon.js` + `archive.js`.
> Line numbers reference `/mnt/n/claude/photo-platform-ydy/prototype-photo-gui/`.

---

## 1. Page Entry / Activation

**Web oracle:** `enterWorkspaceForProject()` app.js:4416–4458; `on_activate` in v3 workbench_view.py.

| Feature | Web behavior | v3 status |
|---------|-------------|-----------|
| Register project dir | POST `/api/fs/register-dir` on enter | `on_activate` → `monitor_service.scan_project` | ✓ |
| Load helicon params from `_data/helicon_params.json` | Fetch on enter | Not done in `on_activate` | **MISSING** |
| Fetch Helicon status (detect exe) | `fetchHeliconStatus()` on enter | `detect_helicon()` called in `_refresh_header` | ✓ |
| Start monitor poll | `startMonitorPoll()` timer | QTimer-based `_auto_refresh_timer` | **MISSING** (no auto poll) |
| Select last-active specimen | `activeSpecimenUid()` lookup | `_get_active_uid()` | ✓ |
| Load project specimens from disk | `loadProjectSpecimensFromDisk()` | `_sidebar.refresh()` → DB query | ✓ |
| Load grouping confirmations | `loadGroupingFromProjectDir()` | via `load_grouping(db, uid)` | ✓ |

**Hard rule:** Auto-poll monitor directory every ~2 s while workbench is visible. v3 is missing QTimer.

---

## 2. Specimen Sidebar (Left Column)

**Web oracle:** `renderSidebar()` app.js:4769–; `openSpecimenContextMenu` app.js:4464.

| Feature | Web | v3 status |
|---------|-----|-----------|
| New specimen button | "🧬 + 新增标本唯一编号" | "新增标本唯一编号" btn | ✓ |
| List specimens for current project | Shows all; filters by `owner_project_dir` | `refresh()` → DB | ✓ |
| Active badge | Green "已激活" pill | `_ACTIVE_STYLE` badge | ✓ |
| Search/filter | Text filter on UID + name | `_search` QLineEdit → filter | ✓ |
| Click = select + load | `state.specimen = idx; render()` | `specimen_selected` signal | ✓ |
| Activate button | "▶ 激活" button per row | `activate_requested` signal | ✓ |
| Deactivate button | Only on active row | `deactivate_requested` signal | ✓ |
| Context menu: print labels | Right-click → print | **MISSING** (no context menu) |
| Context menu: copy UID | Right-click → copy | **MISSING** |
| Context menu: collab status | Right-click shows collab | **MISSING** (collab out-of-scope for now) |
| Draft specimen (not-yet-pinned) | `state.draftSpecimen` | Draft = `_current_uid = None` | partial |

**Note:** Context menu (print/copy) is nice-to-have; activation/deactivate are the critical paths.

---

## 3. Monitor Panel (Centre-Top)

**Web oracle:** `renderDirectoryMonitor()` app.js; `pollMonitorDirectory()` app.js.

### 3.1 Batch-ident bar
| Feature | Web | v3 status |
|---------|-----|-----------|
| Shows active UID + "激活" badge | Banner at top of monitor | `set_batch(uid, active_uid, activated_at)` | ✓ |
| Activated-at timestamp | "激活于 HH:MM" | activated_at param | ✓ |

### 3.2 Activity stats
| Feature | Web | v3 status |
|---------|-----|-----------|
| 今日新增 count | JPGs first-seen today | `_stat_label` text | partial |
| 未整理 count | JPGs without ZIP | shown as pending_count | partial |

### 3.3 Controls bar
| Feature | Web | v3 status |
|---------|-----|-----------|
| 刷新 button | `pollMonitorDirectory()` | `refresh_requested` signal | ✓ |
| 添加照片 file picker | `importJpgFiles()` → base64 upload | **MISSING** (no file picker button) |
| Auto-refresh timer (2 s) | `setInterval(pollMonitorDirectory, 2000)` | **MISSING** |

### 3.4 File cards
| Feature | Web | v3 status |
|---------|-----|-----------|
| JPG cards with gradient preview | Amber radial gradient | `_JPG_PREVIEW` QSS | ✓ |
| TIFF cards with gradient preview | Green radial gradient | `_TIFF_PREVIEW` QSS | ✓ |
| Attribution pill (uid label) | Color-coded by attribution | `ChipAttributed/Unattributed` | ✓ |
| Multi-select (click, Ctrl+A) | `selectedFiles` Set | `_selected_cards()` | ✓ |
| Select-all / clear buttons | Buttons in header | `_on_select_all/_on_select_none` | ✓ |
| Delete selected JPGs only | Confirms + calls API | `_on_delete_clicked` | ✓ (but needs file deletion wired) |
| TIFF warning on delete | Warning if TIFF in selection | `tiff_paths` check | ✓ |
| Manual assign button per card | "归属" button → assign API | `assign_requested` signal | partial (button present, not wired for "unattributed" case only) |
| Unassign button per card | "撤销归属" | `deactivate_requested` signal on card | partial |
| Drag JPG into group | DnD from monitor → grouping | **MISSING** (no DnD) |

### 3.5 Auto-poll
Web uses `setInterval(pollMonitorDirectory, 2000)` while on workspace page.
v3 needs a QTimer that fires every 2 s and calls `_refresh_monitor()`.

---

## 4. JPG Attribution (4-Priority Algorithm)

**Web oracle:** `monitor-service.js:101–116`; Python: `monitor_service.attribute_jpg`.

| Priority | Source | Implementation |
|----------|--------|---------------|
| P0 | explicit_unassigns blacklist | `explicit_unassigns` set in AttributionCtx | ✓ |
| P1 | grouping pathToUid | `path_to_uid` dict | ✓ |
| P2 | manual-assign event log | `assign_to_uid` dict | ✓ |
| P3 | activation time window (firstSeenAt) | `activations` list | ✓ |

**Critical:** Attribution uses `firstSeenAt` NOT `mtime`. Implemented correctly in `monitor_service.py`.

---

## 5. Grouping Panel (Centre-Bottom)

**Web oracle:** `renderGroupingPanel()` app.js:~5200–5450; `groupingSave()` app.js:5337.

### 5.1 Main action bar
| Feature | Web | v3 status |
|---------|-----|-----------|
| ⚡ 合成 button | `composeMainAction("compose")` | `compose_all_requested` → `_on_compose_requested` | ✓ but missing "auto-collect attributed JPGs" logic |
| 合成+整理 button | `composeMainAction("both")` | `_on_compose_and_organise_all` | ✓ |
| 🗜 整理 button | `groupingOrganizeOnly()` / `organizeSelectedJpgsWithTiff()` | `_on_organise_all` | ✓ |
| ⋯ 更多 menu | Free-compose, retroactive, etc. | More button exists but **not wired** | **MISSING** |

### 5.2 Grouping tool (collapsible)
| Feature | Web | v3 status |
|---------|-----|-----------|
| Collapsible toggle | ▸/▾ toggle | `_on_group_toggle` | ✓ |
| Angle label editor per group | `<input>` editable | `_DraftGroupRow._label_edit` | ✓ |
| JPG list per group (drag-reorder) | Drag within group | `QListWidget` InternalMove | ✓ |
| + 新组 button | `groupingAddColumn()` | `_add_group()` | ✓ |
| Remove JPG from group | Right-click delete | JPG remove not wired | **MISSING** |
| Move JPG between groups | DnD across groups | **MISSING** |
| 保存分组 button | `groupingSave()` → POST `/api/grouping-tool/save` | debounce-save via `_save_timer` + `_flush_grouping_save` | ✓ |
| Add selected monitor JPGs to group | `groupingAddSelectedToGroup()` | **MISSING** ("拖入所选 JPG" button) |
| Select all/none groups for batch | `groupingToggleSelectAll()` | **MISSING** |
| Group selection checkboxes | `g._selected` flag | **MISSING** |
| "合成选中组" action | Compose only checked groups | **MISSING** |

### 5.3 Compose action (Helicon)
**Web oracle:** server.js POST `/api/organize-current-specimen` heliconOnly=true; app.js:5446–5473.

| Feature | Web | v3 status |
|---------|-----|-----------|
| Compose via Helicon CLI | `stack_single_subprocess` | Called in `_on_compose_requested` | ✓ |
| Progress dialog (blocking) | Toast "合成中…" | `QProgressDialog` | ✓ |
| After compose → navigate to compose-preview page | `state.page = "compose"` | **MISSING** (no compose-preview page in v3) |
| Update grouping with composedTiffPath | Write back to DB | `save_grouping(db, uid, groups)` | ✓ |
| Helicon params (method/radius/smoothing) | `state.heliconParams` | **MISSING** (no params UI in workbench) |

### 5.4 Organise action (archive)
**Web oracle:** server.js POST `/api/organize-current-specimen`; archive.js.

| Feature | Web | v3 status |
|---------|-----|-----------|
| Gate: uid must be active | `_check_organize_gate` | ✓ |
| Gate: ≥2 JPGs in group | `_check_organize_gate` | ✓ |
| Gate: TIFF must be composed | `group.composedTiffPath` check | ✓ |
| JPG→JXL→ZIP with manifest | `archive_service.archive_group` | ✓ |
| delete_jpg default=False | Hard rule | ✓ |
| Four safety checks before delete | manifest+jxl_recoverable | ✓ |
| Update grouping with archive_zip | `group.archive_zip = result.zip_path` | ✓ |
| Sequence numbering | `next_result_sequence + build_result_basename` | ✓ |

### 5.5 Undo compose
| Feature | Web | v3 status |
|---------|-----|-----------|
| Move TIFF to _retired-tiff/ | Server undo endpoint | `_retire_tiff()` | ✓ |
| Clear composedTiffPath in grouping | DB update | ✓ |

---

## 6. Naming Panel (Right Column, top)

**Web oracle:** `renderNamingCard()` app.js; naming-validator.js.

| Feature | Web | v3 status |
|---------|-----|-----------|
| 7-field inputs (province/site/station/id/storage/colldate/photodate) | Input fields | `_province` etc. | ✓ |
| Live preview UID | `resultIdForSpecimen()` | `_update_preview` | ✓ |
| Live preview result-ID (with seq) | Includes `state.draftResultSequence` | `current_result_id()` | ✓ |
| R-prefix storage warning | "R 前缀 → 需双标签" | `_rna_warning` label | ✓ |
| 💾 保存 button | Upsert specimen to DB | `save_requested` signal → `_on_naming_save` | ✓ |
| 📌 添加到侧栏 (pin/confirm) | `state.draftSpecimen._pinned = true` | via save | ✓ |
| Auto-fill from active specimen | When selecting from sidebar | `load_specimen(sp.raw)` | ✓ |
| Chinese fields NEVER auto-filled | taxonGroupCn etc. | Hard rule enforced in `_on_naming_save` (only Latin fields) | ✓ |

---

## 7. Metadata Panel (Right Column, bottom)

**Web oracle:** `renderMetaCard()` app.js.

| Feature | Web | v3 status |
|---------|-----|-----------|
| All DwC fields | collector/photographer/identifier/geo/storage/taxonomy | All fields in `MetadataPanel` | ✓ |
| Coordinate inputs (lon/lat) | `_lon/_lat` fields | ✓ |
| 保存元数据 button | Upsert to specimens table | `save_requested` signal → `_on_save_metadata` | ✓ |
| Completeness ring | Visual score indicator | `MetaScoreRing` | ✓ |
| Taxonomy autocomplete | 4-level completer | `TaxonomyInput` widget | ✓ |

---

## 8. Results Column (Centre-Right)

**Web oracle:** Results section of workspace render; grouping composed TIFFs.

| Feature | Web | v3 status |
|---------|-----|-----------|
| List composed TIFFs | Per-group TIFF paths | `ResultsColumn.load_uid(tiffs, zips)` | ✓ |
| List archive ZIPs | Per-group ZIP paths | ✓ |
| Open file in explorer | Button/click | **MISSING** (no "open in folder" action) |
| File size display | ZIP size in bytes | ✓ |

---

## 9. Project Settings Drawer

**Web oracle:** `renderProjectSettingsDrawer()` app.js:9418–; `renderHeliconConfigModal()` app.js:7028.

| Feature | Web | v3 status |
|---------|-----|-----------|
| Open from main toolbar | "⚙" button | Via MainWindow settings action | ✓ |
| 概要 tab: project name/year/location fields | Editable inputs | **MISSING** in workbench |
| 概要 tab: incomingJpgSubdir / resultsSubdir display | Read-only paths | **MISSING** |
| 概要 tab: auto-activate on new specimen toggle | Checkbox | **MISSING** |
| 命名规则 tab: code labels (province/site/station/species) | Key-value editor | **MISSING** |
| Helicon config drawer | Path input + detection status | **MISSING** (no Helicon config UI in v3 workbench) |
| 保存方式 tab | Preservation method codes + details | **MISSING** |

---

## 10. Free Compose (无号合成)

**Web oracle:** `freeComposeSelected()` app.js:7982–8010; POST `/api/helicon/compose-groups`.

| Feature | Web | v3 status |
|---------|-----|-----------|
| Select JPGs in monitor, then "无号合成" | `freeComposeSelected()` | **MISSING** |
| Prompt for output name | `window.prompt` | **MISSING** |
| Output TIF to incoming-jpg/ | `toIncoming: true` | **MISSING** |

---

## 11. Retroactive Organize (存量整理)

**Web oracle:** `retroactiveScan()` app.js:8054; `retroactiveApply()` app.js:8083; POST `/api/organize/retroactive/scan+apply`.

| Feature | Web | v3 status |
|---------|-----|-----------|
| Scan button → preview modal | With spec+group cards, checkboxes | **MISSING** |
| Select/deselect groups | Per-group checkbox | **MISSING** |
| Delete-JPG toggle (default OFF) | `pv.deleteJpg` | **MISSING** |
| Confirm + apply | POST apply endpoint | **MISSING** (no retroactive organize in v3) |

---

## 12. Compose Preview Page

**Web oracle:** `renderComposePage()` app.js:6741; `composePreviewSave/Cancel/Recompose()` app.js:6940–7025.

| Feature | Web | v3 status |
|---------|-----|-----------|
| Full-page compose workspace | Left: source JPG list + center: TIFF preview + right: Helicon params | **MISSING** (no compose-preview view in v3) |
| Zoom/pan TIFF preview | Wheel zoom, drag pan | **MISSING** |
| Filmstrip of source JPGs with checkboxes | Deselect = exclude from recompose | **MISSING** |
| Helicon params slider (method/radius/smoothing) | Adjust + recompose | **MISSING** |
| ↻ 重合成预览 | Undo current TIFF + recompose | **MISSING** |
| ✓ 保存到结果 | Promote TIFF → results/ + return to workbench | **MISSING** |

---

## 13. Auto-poll / Monitor Timer

**Critical gap.** Web uses:
```js
var _monitorPollInterval = setInterval(pollMonitorDirectory, 2000);
```
while on workspace page (app.js `startMonitorPoll()`).

v3 **does not** have an auto-refresh timer in WorkbenchView. Files added by Olympus/Helicon
will not appear without manual refresh.

---

## 14. Phase Status Pills (Collab-lite)

**Web oracle:** Collab task status pills in monitor; app.js phase labels.

| Status | Label | v3 status |
|--------|-------|-----------|
| created | 待拍 | `_phase_pills` dict in MonitorPanel | ✓ |
| shooting | 拍摄中 | ✓ |
| shot_done | 已拍完 | ✓ |
| organizing | 整理中 | ✓ |
| done | 完成 | ✓ |

---

## 15. Delete Incoming JPG

**Web oracle:** `deleteSelectedMonitorFiles()` app.js:8201; POST `/api/delete-incoming-jpg`.

| Feature | Web | v3 status |
|---------|-----|-----------|
| Confirm dialog | "确认删除 N 张 JPG?" | `QMessageBox.question` | ✓ |
| TIFF/ZIP filtered out | Only JPG deletable | TIFF warning check | ✓ |
| Actual file deletion | `os.unlink()` | `_on_delete_clicked` → needs actual `os.unlink` | **NEEDS WIRING** |

---

## Summary: Gap Priority List

### P1 — Critical functional gaps (blocks real use)

1. **Auto-poll timer** (2 s interval while workbench visible) — without this, new files don't appear.
2. **Add JPG to group from monitor selection** — core grouping workflow; "拖入所选 JPG + TIFF 补处理" button must call `groupingAddSelectedToGroup()` logic.
3. **Actual file deletion** in `MonitorPanel._on_delete_clicked` — button exists but `os.unlink` not called.
4. **Helicon params UI** (method/radius/smoothing) — required to pass correct params to `stack_single_subprocess`.
5. **Compose result persists sequence** — `_on_compose_requested` must use `organize_preview` to get next_seq and name the TIFF correctly, then bump the hint after compose.

### P2 — Important UX gaps

6. **Free compose** (无号合成) — select JPGs in monitor, no-UID compose → output to incoming-jpg/.
7. **Retroactive organize modal** — scan+preview+apply workflow.
8. **Project settings drawer** — Helicon path config; auto-activate toggle; code labels.
9. **Open-in-explorer** button on ResultsColumn items.
10. **Remove JPG from group** — right-click or button on `_DraftGroupRow`.

### P3 — Lower priority / out-of-scope for this pass

11. Compose-preview page (full Helicon GUI).
12. Collab task management UI.
13. Context menu on specimen sidebar.
14. Free-compose batch merge.
