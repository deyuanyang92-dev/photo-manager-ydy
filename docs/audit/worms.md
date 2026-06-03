# WoRMS 模块审计报告

生成日期：2026-06-03  
参考来源：`prototype-photo-gui/app.js`（web 函数）+ `app/views/worms_view.py` + `app/services/worms_service.py`

---

## 覆盖表

| Web 函数 | 行号 | 状态 | Qt 等价位置 | 备注 |
|---|---|---|---|---|
| `wormsSearch(query, like)` | 11363 | ✓ | `WormsService.search()` | 完整 |
| `wormsGetClassification(aphiaId)` | 11369 | ✓ | `WormsService.classification()` | 完整 |
| `wormsGetSynonyms(aphiaId)` | 11375 | ✓ | `WormsService.synonyms()` | 完整 |
| `wormsGetChildren(aphiaId, offset)` | 11381 | ✓ | `WormsService.children()` | 分页参数完整 |
| `fetchWormsFamilySpecies(familyName)` | 11387 | ✓ | `WormsService.family_genera()` | 本次新增 |
| `fetchWormsGenusSpecies(genusName)` | 11417 | ✓ | `WormsService.genus_species()` | 本次新增 |
| `wormsFillToSpecimen(record, sp)` | 11447 | ✓ | `WormsView._on_fill_to_specimen()` | 字段映射已修正（本次） |
| `flattenClassification(tree)` | 11459 | ✓ | `WormsService.flatten_classification()` | 完整 |
| `loadWormsTaxonomyCandidates()` | 804 | ✓ | `WormsService.load_taxonomy_candidates()` | 本次新增 |
| `fetchWormsJobs()` | 11602 | ✓ | `WormsView._refresh_jobs()` | 自动轮询 QTimer(1.5 s)已加（本次） |
| `startTaxonomyWormsJob(allFiltered)` | 11701 | ◐ | `WormsView._on_create_job()` | 仅支持输入 ID；缺"全部筛选"模式 |
| `updateWormsJob(job, action)` | 11735 | ✓ | `WormsService.update_job_status()` + `retry_failed_job()` | 全四动作（本次加 retry-failed） |
| `resolveTaxonMapping(row, aphiaId, noMatch)` | 11742 | ✓ | `WormsService.resolve_mapping()` | 本次新增 |
| `openWormsMatchModal(row)` | 11767 | ✓ | `WormsMatchDialog` | 本次新增 |
| `searchWormsForTaxonRow()` | 11777 | ✓ | `WormsMatchDialog._on_search()` | 本次新增（内嵌对话框） |
| `selectWormsMatchCandidate(candidate)` | 11791 | ✓ | `WormsMatchDialog._on_candidate_clicked()` | 本次新增 |
| `saveWormsMatchCandidate()` | 11806 | ✓ | `WormsMatchDialog._on_save()` | 本次新增 |
| `renderWormsPage()` | 12378 | ✓ | `WormsView._setup_ui()` | 布局完整 |
| `renderWormsResultItem(rec)` | 12452 | ✓ | `_ResultItemWidget` | 完整 |
| `renderWormsDetail(rec)` | 12475 | ✓ | `_DetailPanel._render()` | 完整；WoRMS 外链为文本（Qt 限制） |
| `renderWormsOverviewTab(rec)` | 12546 | ✓ | `_build_overview_tab()` | 完整 |
| `renderWormsChildrenTab(rec)` | 12584 | ✓ | `_build_children_tab()` + "加载更多" | 本次新增分页按钮 |
| `renderWormsSynonymsTab(rec)` | 12614 | ✓ | `_build_synonyms_tab()` | 完整 |
| `doWormsSearch()` | 12631 | ✓ | `WormsView._on_search()` | 完整 |
| `selectWormsTaxon(rec)` | 12654 | ✓ | `WormsView._on_result_clicked()` | 完整 |
| `renderWormsPopupOverlay()` | 12685 | ✓ | `WormsQuickFillDialog` (worms_view.py) + MetadataPanel「WoRMS 查」按钮 | 本次实现 |
| `doWormsPopupSearch()` | 12743 | ✓ | `WormsQuickFillDialog._on_search()` + `_QuickSearchWorker` | 本次实现 |

---

## 已补缺项（本次）

1. **`WormsService.family_genera()`** — 按科名查属列表，带独立文件缓存（30 天）  
2. **`WormsService.genus_species()`** — 按属名查种列表，带独立文件缓存（30 天）  
3. **`WormsService.load_taxonomy_candidates()`** — 从 worms_taxonomy.json 读取已匹配/已更名记录  
4. **`WormsService.resolve_mapping()`** — 将 review/not_found 行写入 worms_taxonomy.json  
5. **`WormsMatchDialog`** — `worms_view.py` 内嵌对话框，对应 renderWormsMatchModal：原始种名、搜索框、候选列表、分类链预览、保存/取消  
6. **子分类"加载更多"** — `_DetailPanel` 现在保存 `_children_offset`；"加载更多"按钮触发 `_load_more_children()`  
7. **`wormsFillToSpecimen` 字段映射** — 将 `class`→`taxonGroup`、`order`→`order`、`family`→`family`、Species 级→`scientificName`、`taxonomyConfirmed=False` 写入标本  

---

## 本次新补缺项（第二次）

8. **`WormsQuickFillDialog`** — `worms_view.py` 新增，对应 `renderWormsPopupOverlay` + `doWormsPopupSearch`：搜索框预填、结果列表（每行有「填充」按钮）、关闭按钮；Latin-only 填充，Chinese 不覆盖
9. **MetadataPanel「WoRMS 查」按钮** — `metadata_panel.py` 分类区底部新增按钮，点击弹出 `WormsQuickFillDialog`，回调通过 `ctx.worms_fill_specimen` 写入
10. **批量任务 1.5 s 自动轮询** — `WormsView._refresh_jobs()` 启动 `QTimer` single-shot 1500ms，仅在有 running 任务时激活；`_poll_timer` 属性跟踪，`on_activate` 时初始化
11. **retry-failed job action** — `WormsService.retry_failed_job()` 新增：按 worms_taxonomy.json 过滤 error 状态 record_ids、归零 cursor/counts、重置 status="running"；`WormsView` 加「重试失败」按钮

---

## 仍然缺失（诚实说明）

| 缺口 | 影响 | 原因 |
|---|---|---|
| 批量任务"全部筛选"模式 | `startTaxonomyWormsJob(allFiltered=true)` 的 query payload | worms_view.py 没有 taxonomy 表的 query state；需 taxonomy_view 联动 |
