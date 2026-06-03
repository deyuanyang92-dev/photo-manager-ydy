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
| `fetchWormsJobs()` | 11602 | ◐ | `WormsView._refresh_jobs()` | 有刷新；**缺自动轮询（1.5 s）** |
| `startTaxonomyWormsJob(allFiltered)` | 11701 | ◐ | `WormsView._on_create_job()` | 仅支持输入 ID；缺"全部筛选"模式 |
| `updateWormsJob(job, action)` | 11735 | ✓ | `WormsService.update_job_status()` | 完整 |
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
| `renderWormsPopupOverlay()` | 12685 | — | 未实现 | 工作台快捷填充弹窗；需要 workbench_view.py 集成，超出本次范围 |
| `doWormsPopupSearch()` | 12743 | — | 未实现 | 同上 |

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

## 仍然缺失（诚实说明）

| 缺口 | 影响 | 原因 |
|---|---|---|
| `renderWormsPopupOverlay` / `doWormsPopupSearch` | 工作台右侧快捷填充弹窗 | 需改动 workbench_view.py，超出本次约束"只改 worms 相关" |
| 批量任务"全部筛选"模式 | `startTaxonomyWormsJob(allFiltered=true)` 的 query payload | worms_view.py 没有 taxonomy 表的 query state |
| 批量任务自动轮询（1.5 s） | `fetchWormsJobs` 里的 `setTimeout(1500)` | Qt 里需 QTimer；已留 TODO 注释在代码里 |
| `retry-failed` job action | server.js 中 job/:id/:action 有四个动作（pause/cancel/resume/retry-failed）；Qt 只做了 update_job_status | 非核心，可按需补 |
