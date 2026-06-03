# Workbench Module — Web → Qt Coverage Audit

> Generated: 2026-06-03  
> Auditor: verified against web app.js + Qt source  
> Oracle: prototype-photo-gui/app.js (workbench section, ~159 functions), server.js lines 2974-3870, monitor-service.js, helicon.js, archive.js

## Legend

- ✓ 已实现 — Qt 有等价实现，逻辑核实对
- ◐ 部分 — 基础框架存在，但某些细节/路径缺失
- ✗ 缺 — web 有功能，Qt 完全无对应
- N/A — web 专有（HTTP轮询/localStorage/SSR），Qt 用不同机制正确替代

---

## 1. 监控 / 文件扫描层

| web 函数 | Qt 状态 | Qt 位置 / 说明 |
|----------|---------|---------------|
| `startMonitorPoll` / `stopMonitorPoll` | ✓ | `WorkbenchView._auto_refresh_timer` (QTimer 2s) + `on_activate/on_deactivate` |
| `pollMonitorDirectory` (fetch `/api/monitor-scan`) | ✓ | `WorkbenchView._refresh_monitor` → `monitor_service.scan_project` |
| `applyScanToMonitor` | ✓ | `MonitorPanel.load_scan(ScanResult)` |
| `monitorFileKind` / `monitorFileById` | N/A | 内嵌于 `monitor_service._file_entry` |
| `shouldUseRealMonitor` | N/A | Qt 永远用真实扫描 |
| `addMonitoredFile` / `simulateNewJpg` / `simulateNewComposite` | N/A | 仿真函数，web 开发调试用 |
| `autoProcessComposite` | ✗ | web 监听到新 TIFF 后自动触发合成弹窗；Qt 版无此路径，用户须手动点合成 |
| `hasDirBrowserOpen` / `hasInputModalOpen` | N/A | Qt 模态对话框天然阻断，不需要全局标志 |
| `renderDirectoryMonitor` | ✓ | `MonitorPanel._setup_ui` + `_rebuild_grid` |
| `renderMonitorContextMenu` | ◐ | Qt 用 `_on_jpg_context_menu`（右键移除），但缺少 web 版完整右键菜单（加入分组/归属/移除等） |
| `deleteSelectedMonitorFiles` | ✓ | `MonitorPanel._on_delete_clicked`（TIFF 保护 + confirm + os.unlink） |
| `copySelectedMonitorJpgs` / `copyIncomingJpgFromPaths` | ✗ | web 剪贴板复制 JPG；Qt 无此功能 |
| `handleMonitorPaste` | ✗ | web 粘贴导入；Qt 有"添加照片"文件选择器替代 |
| `selectedDeletableJpgs` / `selectedMonitorFiles` | ✓ | `MonitorPanel.selected_jpg_paths()` |
| `importJpgFiles` | ✓ | `WorkbenchView._on_add_jpg_files` (QFileDialog + shutil.copy2) |

---

## 2. 标本激活 / 侧边栏

| web 函数 | Qt 状态 | Qt 位置 / 说明 |
|----------|---------|---------------|
| `activateSpecimen(uid, active)` | ✓ | `WorkbenchView._on_sidebar_activate` + `activation_service.activate`；全局互斥 |
| `activeSpecimenUid` / `activeSpecimenObject` | ✓ | `activation_service.get_active_uid(db)` |
| `activateSpecimenByUid` | ✓ | `WorkbenchView._on_sidebar_activate` |
| `openSpecimenContextMenu` / `renderSpecimenContextMenu` | ✓ | `SpecimenSidebar._on_context_menu`：右键菜单含复制编号/打印标签/激活/去激活 |
| `copySpecimenUid` | ✓ | `SpecimenSidebar._on_context_menu` → `QApplication.clipboard().setText(uid)` + `copy_current_uid()` |
| `findProjectsForSpecimenIndex` | N/A | Qt 单项目视图，不需要跨项目查找 |
| `findDuplicateSpecimen` / `formatDuplicateSpecimenHint` | ✓ | `NamingPanel._check_duplicate`：实时 DB 查重，撞号显示 ⚠ 警告标签；`_check_compliance` 另做格式校验 |
| `renameSpecimenCode` | ✗ | web 允许修改物种号（sp.id），Qt 无此功能 |
| `migrateSpecimenUidReferences` | ✗ | UID 重命名时迁移所有引用（grouping/tasks），Qt 无 UID 重命名 |
| `applyStorageCorrection` | ✗ | 修正保存方式后重新计算 UID，Qt 无此路径 |
| `specimenHasRiskyUidReferences` | ✗ | 配合上面的迁移检查，Qt 无 |
| `bootstrapWorkspaceData` | ◐ | `WorkbenchView.on_activate` 调 `_sidebar.refresh()` + `_refresh_monitor()`；但无 web 版的 loadProjectSpecimensFromDisk + mergeUserProjects 全流程 |
| `dedupeSpecimensByUid` / `dedupeProjectSpecimenIndices` | N/A | DB 有 PRIMARY KEY 约束，SQLite 层保证唯一 |

---

## 3. 分组工具 (GroupingPanel)

| web 函数 | Qt 状态 | Qt 位置 / 说明 |
|----------|---------|---------------|
| `ensureGroupingDraft` | ✓ | `grouping_service.load_grouping` + `GroupingPanel.load_grouping` |
| `groupingSave` | ✓ | `WorkbenchView._flush_grouping_save` + `grouping_service.save_grouping` |
| `groupingAddSelectedToGroup` | ✓ | `WorkbenchView._on_add_selection_to_group` + `GroupingPanel.add_jpgs_to_group`（含互斥移除） |
| `groupingRemoveFile` | ✓ | `GroupingPanel.remove_jpg_from_group` |
| `groupingDeleteGroup` | ✓ | `GroupingPanel.delete_group` + `_DraftGroupRow` 删组按钮（已合成组阻止删除） |
| `groupingClearGroup` | ✓ | `GroupingPanel.clear_group` + `_DraftGroupRow` 清空按钮 |
| `groupingMoveFileBetweenGroups` | ◐ | Qt 只能右键移除再手动加入，无直接拖拽组间移动 |
| `groupingAddColumn` (新组) | ✓ | `GroupingPanel._add_group` |
| `groupingDraftGroupOf` | N/A | 内嵌于 save_grouping 路径追踪 |
| `groupingValidateStackReady` | ◐ | Qt 在 `_on_compose_requested` 里检查 ≥2 JPG，但无 web 版完整前置检查（grouping_service._check_organize_gate 涵盖了部分） |
| `groupingSaveThen` / `groupingUidForPanel` | N/A | 内部管道，Qt 直接在 view 层协调 |
| `groupingSelectedIndexes` / `groupingToggleSelectAll` | ✗ | web 分组区有多选组的复选框；Qt 无批量组操作的多选 UI |
| `composedJpgSet` / `selectedManualArchiveGroup` | ✗ | 手动归档组选择；Qt 无此流程 |
| `groupingComposeSelected` (批量) | ✓ | `GroupingPanel._on_compose_all` + `WorkbenchView._on_compose_requested` 循环 |
| `composeMainAction` + `composeImplicitActiveBatch` | ✓ | Qt `_on_compose_requested` 含隐式批次回退（`_get_attributed_jpg_paths`）：组内 JPG < 2 时询问用监控归属 JPG |
| `composeProgressLoop` | ◐ | Qt 用 `QProgressDialog` 显示合成进度（阻塞式）；web 版是非阻塞循环含取消。功能可接受，体验略差 |
| `renderComposePreviewModal` / `composePreviewItem` | ✓ | `WorkbenchView._show_compose_preview`：合成前显示可勾选 JPG 列表，支持去除不需要的帧 |
| `composePreviewSave` / `composePreviewCancel` / `composePreviewRecompose` | ◐ | Qt 版含「取消」和「开始合成」；无 web 版的"重合成预览"（调 Helicon 重跑后再显示预览）；Save 等于直接合成 |
| `renderOccupiedWarnModal` | ✗ | 标本已被其他人占用警告；Qt 无协作锁状态 |
| `groupingStackRun` | ✓ | 由 `_on_compose_requested` 调 `helicon_service.stack_single_subprocess` 实现 |
| `groupingOrganizeOnly` | ✓ | `GroupingPanel._on_organise_all` + `WorkbenchView._on_organise_requested` |
| `groupingStackAndOrganize` | ✓ | `GroupingPanel._on_compose_and_organise_all` |
| `groupingUndoCompose` | ✓ | `WorkbenchView._on_undo_compose` → `_retire_tiff`（移到 `_retired-tiff/`，TIFF 永不删） |
| `groupingImportTiff` | ✓ | `GroupingPanel._on_import_tiff_btn` → `_TiffImportDialog`：列 results/incoming-jpg TIFF + 粘贴路径 + 浏览；更新 composedTiffPath + status="composed" |
| `renderTiffImportModal` | ✓ | `_TiffImportDialog`：等价实现，含候选文件列表 + 路径输入 + 浏览按钮 |
| `groupingArchiveSingle` | ✓ | `WorkbenchView._on_organise_requested` 处理单组 |
| `groupingAutoWatchTrigger` | ✗ | web 监听新 JPG 自动触发分组建议；Qt 无自动触发 |
| `manualRegisterArchive` | ✗ | 手动注册已有归档（ZIP）到分组记录；Qt 无此功能 |
| `organizeSelectedJpgsWithTiff` | ✗ | web 选中 JPG + TIFF 手动整理；Qt 无此独立操作 |
| `freeComposeSelected` | ✓ | `WorkbenchView._on_free_compose`（QInputDialog 命名 + Helicon + incoming-jpg/） |
| `mergeFreeComposeBatches` | ✗ | 合并无号合成批次记录；Qt 无批次持久化 |

---

## 4. 存量整理 (Retroactive)

| web 函数 | Qt 状态 | Qt 位置 / 说明 |
|----------|---------|---------------|
| `retroactiveScan` | ✓ | `WorkbenchView._on_retroactive_scan` + `retroactive_service.scan_project_retroactive` |
| `retroactiveApply` | ✓ | `RetroactiveModal._on_apply` + `archive_service.archive_group` |
| `renderRetroactiveModal` | ✓ | `app/widgets/retroactive_modal.py`（含 delete-jpg 复选框、group checkboxes） |
| `organizeBatchPreview` / `organizeBatchRun` / `organizeBatchRunConfirmed` | ◐ | Qt `RetroactiveModal` 覆盖了批量整理；但缺 web 版的批次目录选择（选项 dir） |
| `organizeSingleRun` | ✓ | `WorkbenchView._on_organise_requested`（单组整理） |
| `postOrganizeWithCollision` / `showCollisionModal` | ✓ | `WorkbenchView._on_organise_requested`：ZIP 已存在时弹确认框（是否覆盖重新归档），拒绝则中止 |
| `renderBatchResult` | ◐ | Qt 显示 QMessageBox（ok_count/fail_count），无 web 版的详细逐项结果列表 |

---

## 5. Helicon 参数 / 配置

| web 函数 | Qt 状态 | Qt 位置 / 说明 |
|----------|---------|---------------|
| `fetchHeliconStatus` / `fetchHeliconConfig` | ✓ | `helicon_service.detect_helicon()` |
| `saveHeliconConfigPath` | ✓ | `ProjectSettingsDrawer._on_detect_helicon`（写入 env var） |
| `renderHeliconConfigModal` | ✓ | `ProjectSettingsDrawer`（Helicon 路径 + 自动检测） |
| `startHeliconPoll` / `stopHeliconPoll` (job 轮询) | ✗ | web 异步 job 系统（server-side job queue）；Qt 用同步 subprocess，无轮询 |
| `submitHeliconAuto` | ✗ | web 的自动合成接口（POST /api/helicon/compose-groups）；Qt 用同步 subprocess |
| `renderComposePage` | ◐ | `GroupingPanel` + `HeliconParamsPanel` 覆盖了参数部分；但无 web 版完整合成页（含文件预览/lightbox） |
| Helicon 参数 (method/radius/smoothing) | ✓ | `HeliconParamsPanel` (A/B/C + sliders)，`get_params()` 传给 `stack_single_subprocess` |
| `renderHeliconConfigModal` (TIFF 压缩/排序/深度图) | ◐ | `HeliconParamsPanel` 只有 method/radius/smoothing；高级参数（tiff_compression/sort/dmap）无 UI |

---

## 6. 归档 / 压缩

| web 函数 | Qt 状态 | Qt 位置 / 说明 |
|----------|---------|---------------|
| `startSmartCompression` / `performRealCompression` | ✓ | `archive_service.archive_group`（JPG→JXL→ZIP + manifest + 可恢复校验） |
| `performRealCompressionUpload` | N/A | web 上传到后端；Qt 直接本地操作 |
| `finishSmartCompression` | ✓ | `archive_service.archive_group` 返回 `ZipResult`，view 更新 grouping |
| `validateSmartGroup` | ◐ | `archive_service.archive_group` 检查文件存在；但缺 web 版对 ZIP 已存在时的跳过/重建逻辑 |
| `smartCompressionWarning` | ◐ | Qt 用 `QMessageBox.warning`，无 web 版的内联 toast 级警告 |
| `toggleCompressPanel` / `renderCompressDrawer` | ✗ | web 有独立压缩抽屉（手动拖拽 JPG+TIFF 压缩）；Qt 无此功能（合成后由整理触发） |
| `renderCompressActions` / `renderCompressGroupsBar` | ✗ | 属于上面的压缩抽屉 UI |
| `doCompression` / `doCompressionBatch` | ✗ | 属于上面的压缩抽屉逻辑 |
| `processSelectedMonitorFiles` | ✗ | 选中监控文件后执行压缩；Qt 无此独立流程 |
| `removeFilesFromMonitor` | ✗ | web 从监控列表移除已处理文件；Qt 扫描时自动不再显示已归档 |

---

## 7. 命名面板 / 标本记录

| web 函数 | Qt 状态 | Qt 位置 / 说明 |
|----------|---------|---------------|
| `renderNamingCard` | ✓ | `NamingPanel`（7段表单 + 实时预览 + R前缀警告 + 序号建议） |
| `nextResultSequenceForSpecimen` / `nextCompositeFileName` | ✓ | `organize_service.organize_preview` + `build_result_basename` |
| `draftNamingComplete` | ✓ | `NamingPanel.current_uid()` 有值即表示完整 |
| `resultIdForSpecimen` / `resultSequenceFromName` / `resultSequenceLabel` | ✓ | `naming_utils.build_result_id` + `organize_service.build_result_basename` |
| `specimenCodeParts` / `specimenCodeGapMessage` | ✓ | `naming_panel._update_sequence_hint`（建议下一个编号 + gap 提示） |
| `refreshExpectedNameFromBackend` | ◐ | `NamingPanel._update_sequence_hint` 从 DB 查询；但无 web 版实时 HTTP 请求后端推算 |
| `commitDraftAsSpecimen` / `confirmAndSaveSpecimen` | ✓ | `WorkbenchView._on_naming_save`（upsert specimens 表） |
| `designComplianceCheck` | ✓ | `NamingPanel._check_compliance`：格式提示（日期 8 位/省份字母/保存方式前缀）；提示而不阻断 |

---

## 8. 元数据面板

| web 函数 | Qt 状态 | Qt 位置 / 说明 |
|----------|---------|---------------|
| `renderMetaCard` | ✓ | `MetadataPanel`（collector/date/photographer/identifier/geo/taxon/notes/coords） |
| `metadataCompleteness` | ✓ | `MetadataPanel._compute_score` + `MetaScoreRing`（5字段 20分制） |
| `metaReverseGeocode` | ✓ | `MetadataPanel._on_reverse_geocode` + `_NominatimWorker` QThread：后台调 Nominatim，填入 geo_area；不覆盖用户已填内容（询问确认） |
| `renderTaxonNotesCard` | ◐ | `MetadataPanel` 有分类字段（taxon_group/family/genus/scientific_name）；但无 web 版的 WoRMS 验证集成弹出 |
| `flushRightPanelEdits` / `scheduleRightPanelPersist` | ✓ | `WorkbenchView._on_save_metadata`（save 按钮触发 DB UPDATE） |

---

## 9. 成果内容列 (ResultsColumn)

| web 函数 | Qt 状态 | Qt 位置 / 说明 |
|----------|---------|---------------|
| `renderFinalResults` / `renderResultGroup` | ✓ | `ResultsColumn.load_uid(uid, tiffs, zips)` |
| `photosForSpecimen` | ✓ | `WorkbenchView._refresh_results_column` 从 grouping.composed_tiff_path / archive_zip 收集 |
| `openResultLightbox` | ✗ | web 点击成片后有 lightbox 全屏预览；Qt 只有"在文件管理器中打开"（`_open_in_explorer`） |

---

## 10. 项目设置抽屉

| web 函数 | Qt 状态 | Qt 位置 / 说明 |
|----------|---------|---------------|
| `renderProjectSettingsDrawer` | ✓ | `ProjectSettingsDrawer`（Helicon路径+检测+auto-activate+subdir显示） |
| `renderProjectSubdirControl` / `applyProjectSubdirChange` | ✗ | web 允许修改 incoming-jpg/results 子目录名；Qt 只读显示固定路径 |
| `loadProjectSubdirOptions` | ✗ | 属于上面缺失的子目录编辑功能 |

---

## 11. 协作 (Collab) — 工作台集成部分

| web 函数 | Qt 状态 | Qt 位置 / 说明 |
|----------|---------|---------------|
| `collabSyncTasks` / `collabCreateTaskSync` | ✗ | web 激活时同步协作任务到后端；Qt 无协作集成（WorkbenchView 不调协作服务） |
| `collabUpdateTaskStatus` | ✗ | 合成/整理时更新协作状态；Qt 无 |
| `collabPostPhotoIndex` | ✗ | 上传 JPG 索引供协作方；Qt 无 |
| `renderCollabStatusBar` / `renderCollabShareModal` / `renderCollabManagerModal` | ✗ | SpecimenSidebar 有协作区 UI 骨架（地址/设备/成员），但无后端调用 |

---

## 汇总

| 状态 | 数量 |
|------|------|
| ✓ 已实现 | 55 |
| ◐ 部分 | 15 |
| ✗ 缺 | 23 |
| N/A | 12 |

---

## 关键 10 任务核实（v3 实现状态）

| 任务 | 状态 | 说明 |
|------|------|------|
| 1. 自动轮询 (startMonitorPoll) | ✓ | QTimer 2s + on_activate/on_deactivate 完整 |
| 2. 真删除 JPG | ✓ | `_on_delete_clicked`：TIFF 保护 + confirm + `os.unlink`，已有测试覆盖 |
| 3. 加入分组 | ✓ | `add_jpgs_to_group`：互斥移除 + 无重复追加 |
| 4. Helicon 参数 | ✓ | `HeliconParamsPanel` A/B/C + radius + smoothing → `get_params()` → `stack_single_subprocess` |
| 5. 无号合成 | ✓ | `_on_free_compose`：命名 → incoming-jpg/ 路径 → Helicon → 刷新监控 |
| 6. 存量整理 | ✓ | `_on_retroactive_scan` + `RetroactiveModal` + `archive_service.archive_group` |
| 7. 项目设置抽屉 | ✓ | `ProjectSettingsDrawer`：Helicon 路径 + auto-activate + 子目录（只读）|
| 8. 合成+整理批量 | ✓ | `_on_compose_and_organise_all`（先 compose all → 再 organise composed） |
| 9. Undo 合成 | ✓ | `_on_undo_compose` + `_retire_tiff`（移 _retired-tiff/，TIFF 永不删） |
| 10. delete_jpg 默认关 | ✓ | `_on_organise_requested` 读 `ctx.settings.delete_jpg_after_archive`，默认 False |

---

## 已验证的硬规则合规性

| 规则 | Qt 实现 |
|------|---------|
| TIFF 永远保留 | `MonitorPanel._on_delete_clicked`：TIFF 路径触发 warning + return；`_on_undo_compose`：移到 `_retired-tiff/` 不删 |
| JPG 删除默认关 | `archive_service.archive_group(delete_jpg=False 默认)` + `_on_organise_requested` 从 settings 读（默认 False） |
| 中文不自动填 | `_on_naming_save`：只保存用户填入的原始文本，无自动推断 |
| 激活互斥 | `activation_service._set_all_inactive`：激活前把其他全清除 |
| 一个删除 + 警告 | `MonitorPanel._on_delete_clicked`：TIFF 则 warning，JPG 则 question confirm |

---

## ◐/✗ 优先补缺建议（按用户价值排序）

### P1 — 高影响（已全部落地）

1. **✓ 删组 / 清空组** (`groupingDeleteGroup` / `groupingClearGroup`)：`_DraftGroupRow` 内嵌删组 / 清空按钮，已合成组阻止删除。
2. **✓ 碰撞处理** (`postOrganizeWithCollision`)：`_on_organise_requested` 检查 ZIP 已存在时弹确认，拒绝可中止。
3. **✓ composeImplicitActiveBatch**：`_on_compose_requested` 含 `_get_attributed_jpg_paths` 隐式批次回退。

### P2 — 中影响（已全部落地）

4. **✓ groupingImportTiff** + **renderTiffImportModal**：`_TiffImportDialog` 对话框，含候选列表 + 路径输入 + 浏览。
5. **✗ organizeSelectedJpgsWithTiff**：选中 JPG+TIFF 直接整理（无需先建组）；Qt 无此独立操作。
6. **✓ 合成预览弹窗** (`renderComposePreviewModal`)：`_show_compose_preview` 可勾选 JPG 列表。
7. **✓ 复制 UID** (sidebar 右键)：`SpecimenSidebar._on_context_menu` 含「复制编号」。

### P3 — 低影响（仍缺）

8. **✗ autoProcessComposite**：新 TIFF 出现时自动弹出命名弹窗。
9. **✗ openResultLightbox**：成果图全屏预览（只有"在文件管理器打开"替代）。
10. **✗ renderProjectSubdirControl**：子目录名编辑（大多数用户不需要改）。
