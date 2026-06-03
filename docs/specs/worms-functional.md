# WoRMS 分类库 — 功能规格（Web Oracle）

> 本文档以 web 原型代码为 Oracle，逐功能描述 WoRMS 分类库页面的完整行为与实现位置。
> PyQt6 GUI 实现（worms_view.py / worms_service.py）必须与此规格 1:1 对齐。

---

## 1. 页面入口 / 导航

| 属性 | 值 |
|------|-----|
| 页面 ID | `worms` |
| 导航标题 | `WoRMS 分类库` |
| 导航图标 | 🌊 |
| web Oracle | `app.js:18323`（`state.page === "worms" ? renderWormsPage()`） |
| PyQt6 | `WormsView.view_id = "worms"`, `nav_title = "WoRMS 分类库"` |

---

## 2. 页面整体布局

```
worms-header
  worms-title-row   [h2 serif 标题]  [marinespecies.org 外链]
  worms-desc        (说明文字)

worms-body  (水平分割，左:右 ≈ 6:4)
  worms-search-panel (左)
    worms-search-bar
      [mono input 拉丁学名]  [like-toggle checkbox 模糊匹配]  [搜索 btn]
    状态行 / 进度条
    worms-result-list  (每条 worms-result-item)

  worms-detail-panel (右)
    worms-detail-empty  (未选中时占位)
    worms-detail        (选中后显示)
      worms-detail-header  (名/作者/rank/status badges + WoRMS link)
      worms-classification-chain  (分类链节点)
      worms-detail-tabs  [概览] [子分类] [同义词]
      worms-tab-content
      worms-fill-btn  (填充到当前标本)

批量验证任务 footer  (collapsible QGroupBox)
```

**web Oracle:** `app.js:12378` `renderWormsPage()`  
**PyQt6:** `WormsView._setup_ui()` + `_DetailPanel._render()`

---

## 3. 功能清单（逐条）

### 3.1 搜索栏 — 模糊/精确搜索

**行为：**
- 输入拉丁学名（Monospace 字体），按回车或点击「搜索」触发查询。
- checkbox「模糊匹配」默认勾选；取消勾选→精确匹配（`like=false`）。
- 搜索中：进度条显示（QProgressBar range 0,0），按钮禁用。
- 搜索完成：显示结果数量（"找到 N 条结果"）。
- 搜索失败：错误文字展示，按钮重新启用。

**API：** `GET /api/worms/search?q=<name>&like=<true|false>`  
**web Oracle:** `app.js:12401–12422` (search bar UI), `12631–12651` (`doWormsSearch`), `11363–11368` (`wormsSearch`)  
**server.js:** `1798–1810`  
**PyQt6:** `WormsView._on_search()`, `_SearchWorker`, `_on_search_done()`, `_on_search_error()`

**缓存：** TTL 7天，key `search:<name>:like|exact`

---

### 3.2 结果列表项（worms-result-item）

每条结果显示：
- **学名**（`scientificname`，mono bold）
- **命名人**（`authority`，italic）
- **rank badge**（如 Species，teal pill）
- **status badge**（accepted=绿色，unaccepted=红色）
- **面包屑**：`class > order > family`（仅非空字段）
- **有效名提示**（unaccepted 时 → `valid_name`，黄色文字）
- 点击整行→触发详情加载

**web Oracle:** `app.js:12452–12473` `renderWormsResultItem(rec)`  
**PyQt6:** `_ResultItemWidget` (`worms_view.py:182–252`)

---

### 3.3 详情面板加载（选中结果后）

选中结果时：
1. 立即显示 loading 状态（`_DetailPanel.show_loading(rec)`）
2. 后台 `_DetailWorker` 并发拉取：
   - 分类链 `GET /api/worms/classification/:aphiaId`
   - 同义词 `GET /api/worms/synonyms/:aphiaId`
   - 子分类 `GET /api/worms/children/:aphiaId?offset=1`
3. 完成后 `show_detail(rec, chain, synonyms, children)`
4. 若 unaccepted，使用 `valid_AphiaID` 而非 `AphiaID` 查分类链

**web Oracle:** `app.js:12654–12681` `selectWormsTaxon(rec)`  
**server.js:** `1812–1860`  
**PyQt6:** `WormsView._on_result_clicked()`, `_DetailWorker`, `_on_detail_done()`

---

### 3.4 详情面板头部（worms-detail-header）

显示：
- 学名（15px bold mono）
- 命名人（italic，muted）
- rank badge + status badge
- valid_name 提示（unaccepted 时，含 AphiaID）
- WoRMS 外链文字（`WoRMS: marinespecies.org/aphia.php?id=<id>`）

**web Oracle:** `app.js:12479–12491` `renderWormsDetail(rec)` 内头部段落  
**PyQt6:** `_DetailPanel._render()` 头部段落（`worms_view.py:503–543`）

---

### 3.5 分类链（worms-classification-chain）

- 每个节点一行：rank（固定宽 80px）/ scientificname（mono）/ `#AphiaID`（右对齐）
- 当前物种节点高亮（accent 左边框 + accent 文字色）
- 节点来自 `WormsService.flatten_classification(raw_chain)` 展平嵌套树

**分类链展平算法：**
- 递归遍历 `node.child`，输出 `[{rank, scientificname, AphiaID}, ...]`
- Kingdom → ... → Species，由高到低

**web Oracle:** `app.js:12494–12509` (chain 渲染), `11459–11468` (`flattenClassification`)  
**PyQt6:** `_chain_node_widget()` (`worms_view.py:256–285`), `WormsService.flatten_classification()` (`worms_service.py:381–406`)  
**server Oracle（展平）:** 隐含于 `saveAcceptedMapping` 调用链

---

### 3.6 详情 Tabs（概览 / 子分类 / 同义词）

三个 tab 按钮，点击切换，当前 tab 显示 accent 下划线。

**概览 Tab（worms-overview-tab）：**
- 字段列表：AphiaID / 学名 / 命名人 / 等级 / 状态 / 界 / 门 / 纲 / 目 / 科 / 属 / URL / LSID
- 生境 flags：`isMarine` 海洋 / `isFreshwater` 淡水 / `isBrackish` 半咸水 / `isTerrestrial` 陆地
- 空值字段跳过

**web Oracle:** `app.js:12546–12582` `renderWormsOverviewTab(rec)`  
**PyQt6:** `_build_overview_tab(rec)` (`worms_view.py:290–342`)

**子分类 Tab（worms-children-tab）：**
- 列表每行：scientificname（mono）+ rank badge
- 无结果 → "无子分类"
- web 有"加载更多"按钮（children≥50 时），PyQt6 实现加载第1页（offset=1）

**web Oracle:** `app.js:12584–12612` `renderWormsChildrenTab(rec)`  
**PyQt6:** `_build_children_tab(children, loading)` (`worms_view.py:345–368`)

**同义词 Tab（worms-synonyms-tab）：**
- 列表每行：scientificname（mono）+ status badge + authority（italic）
- 无结果 → "无同义词记录"

**web Oracle:** `app.js:12614–12629` `renderWormsSynonymsTab(rec)`  
**PyQt6:** `_build_synonyms_tab(synonyms, loading)` (`worms_view.py:371–397`)

---

### 3.7 填充到当前标本（worms-fill-btn）

**行为（web）：**
```js
// app.js:11447–11457
function wormsFillToSpecimen(record, sp) {
  if (record.status === "unaccepted" && record.valid_name)
    r.scientificname = record.valid_name;   // 用有效名替换
  if (r.class)  sp.taxonGroup = r.class;   // 纲 → taxonGroup
  if (r.order)  sp.order = r.order;        // 目
  if (r.family) sp.family = r.family;      // 科
  if (r.rank === "Species" && r.scientificname)
    sp.scientificName = r.scientificname;  // 种名（仅种级）
  sp.taxonomyConfirmed = false;
  commitTaxonValue(sp);
  toast("已从 WoRMS 填充分类信息");
}
```

**规则：**
1. unaccepted → 使用 `valid_name` 作为学名来源
2. 只填拉丁字段（`taxonGroup / order / family / scientificName`）
3. **中文字段（`*Cn`）绝对不覆盖**
4. 设置 `taxonomyConfirmed = false`（待用户再确认）
5. 需当前有激活标本，否则提示"需先在工作区选择标本"

**web Oracle:** `app.js:11447–11457` `wormsFillToSpecimen()`, `12532–12541`（按钮逻辑）  
**PyQt6:** `WormsView._on_fill_to_specimen(rec)` (`worms_view.py:1052–1073`)

**注意：** PyQt6 实现通过 `ctx.worms_fill_specimen(r)` hook 委托给工作台视图。
`AppContext` 尚未实现此 hook（活跃标本路由待 workbench 打通后补）。
当前：hook 不存在时显示状态文字"（需先在工作区选择标本）"。

---

### 3.8 批量验证任务（Batch Jobs）

**数据结构（WormsJob）：**
```
{
  id: UUID,
  status: "running" | "paused" | "cancelled" | "completed",
  created_at: ISO-8601,
  updated_at: ISO-8601,
  created_by: str,
  record_ids: [str, ...],  # taxonomy recordId 列表
  cursor: int,              # 已处理索引（断点续跑）
  counts: { matched, renamed, review, not_found, error, stale },
  source: "selected" | "filtered",
  completed_at: ISO-8601 | null,
}
```

**操作：**

| 操作 | web 端点 | PyQt6 |
|------|----------|-------|
| 创建任务 | `POST /api/worms/jobs` body `{recordIds, modifiedBy}` | `WormsService.create_job(record_ids)` |
| 列表查询 | `GET /api/worms/jobs` | `WormsService.list_jobs()` |
| 单条查询 | `GET /api/worms/jobs/:id` | `WormsService.get_job(id)` |
| 更新状态 | `POST /api/worms/jobs/:id/:action` (pause/cancel/resume) | `WormsService.update_job_status(id, status)` |
| 刷新 UI  | 轮询 1500ms（运行中）| 手动点击「刷新」按钮 |

**web Oracle:** `app.js:11602–11619` `fetchWormsJobs()`, `11701–11716` `startTaxonomyWormsJob()`, `11735–11739` `updateWormsJob()`  
**server.js:** 批量任务端点（约 line 1590–1680）  
**PyQt6:** `WormsView._build_jobs_section()`, `_on_create_job()`, `_refresh_jobs()` (`worms_view.py:866–1123`)  
**WormsService:** `create_job()`, `list_jobs()`, `get_job()`, `update_job_status()` (`worms_service.py:487–584`)

**缓存命中显示：** 任务列表中显示 `[日期] id… status cursor/total (matched:N renamed:N)`

---

### 3.9 缓存机制

| 操作 | 缓存 key | TTL |
|------|----------|-----|
| 搜索 | `search:<name>:like\|exact` | 7 天 |
| 分类链 | `classification:<aphiaId>` | 30 天 |
| 同义词 | `synonyms:<aphiaId>` | 14 天 |
| 子分类 | `children:<aphiaId>:<offset>` | 14 天 |
| 单条记录 | `<aphiaId>` | 14 天 |

- 缓存上限 10MB；超出时删最旧 25%。
- 限速 600ms/次（`threading.Lock` 保护）。
- 原子写入（tmp → rename）。
- 缓存文件可删重建，不影响正确性。

**web Oracle:** `server.js:1726–1753`  
**PyQt6:** `WormsService._fetch()`, `_evict_if_needed()`, `_rate_wait()` (`worms_service.py:204–288`)

---

### 3.10 中文字段保护（红线）

`WormsService.merge_worms_into_record()` 只写以下 `worms_*` 前缀字段：

```
worms_aphia_id, worms_valid_aphia_id, worms_scientific_name,
worms_valid_name, worms_authority, worms_rank, worms_status,
worms_class, worms_order, worms_family, worms_genus,
worms_chain, worms_verified_at
```

任何以 `Cn` 结尾的字段（`scientificNameCn`, `familyCn`, `orderCn`, `classCn` 等）**永远不被 WoRMS 写入**。

**web Oracle:** `docs/modules/worms.md` §坑与约束  
**PyQt6:** `WormsService.merge_worms_into_record()` (`worms_service.py:411–476`)

---

## 4. 网络线程模型

所有网络 I/O 在 `QThread` 上执行，主线程不阻塞：

| Worker | 触发 | 信号 |
|--------|------|------|
| `_SearchWorker` | 点击「搜索」| `finished(list)` / `error(str)` |
| `_DetailWorker` | 点击结果行 | `finished(dict)` / `error(str)` |

线程在 `finished` / `error` 信号后自动 quit + deleteLater。  
`_DetailWorker` 若前序 detail 线程未完成，先 `quit()` + `wait(400ms)` 再启动。

**PyQt6 Oracle:** `worms_view.py:88–129` (workers), `936–1045` (thread lifecycle)

---

## 5. 已验证功能状态

| 功能 | 状态 | 备注 |
|------|------|------|
| 模糊搜索 | ✓ | QThread，缓存命中 |
| 精确搜索 | ✓ | like=false |
| 结果列表（accepted/unaccepted 标记）| ✓ | |
| 分类链（flatten + 高亮当前节点）| ✓ | |
| 概览 Tab（字段+生境）| ✓ | |
| 子分类 Tab | ✓ | 仅第1页，无"加载更多" |
| 同义词 Tab | ✓ | |
| 填充到当前标本 | ✓（hook） | ctx.worms_fill_specimen 待 workbench 打通 |
| 批量验证任务创建/列表/状态 | ✓ | |
| 批量任务 cursor 续跑 | ✓（Service层） | UI 无进度轮询（需手动刷新） |
| 缓存命中（无网络调用）| ✓ | |
| 缓存 TTL 过期→重拉 | ✓ | |
| 缓存 10MB 驱逐 | ✓ | |
| 限速 600ms | ✓ | threading.Lock |
| 中文字段不覆盖 | ✓ | 红线已测试 |
| merge_worms_into_record | ✓ | worms_* 前缀字段 |
| 子分类"加载更多"| ✗ | web 有，PyQt6 未实现（children offset>1 pagination） |
| UI 自动轮询 running jobs | ✗ | web 1500ms 轮询，PyQt6 仅手动刷新 |

---

## 6. 数据文件

| 文件 | 路径 | 格式 |
|------|------|------|
| 缓存 | `<project_dir>/_data/worms_cache.json` 或 `~/.photo_workbench/data/worms_cache.json` | `{_meta, records: {key: {data, fetched_at}}}` |
| 批量任务 | `<project_dir>/_data/worms_jobs.json` | `{jobs: [WormsJob, ...]}` |
| 分类映射（web-only）| `data/worms_taxonomy.json` | `{taxa, mappings}` — 仅 web 批量任务结果，PyQt6 不读写 |

---

## 7. 测试覆盖（tests/test_worms_service.py）

| 测试类 | 覆盖 |
|--------|------|
| `TestCacheHit` | search/record/classification 缓存命中；过期→重拉 |
| `TestChineseFieldProtection` | Cn 字段不被覆盖；worms_* 字段正确写入 |
| `TestJobLifecycle` | create/list/get/update；空 ids 报错 |
| `TestCacheEviction` | clear_expired 移除过期 / 跳过新鲜 |
| `TestFlattenClassification` | 展平链；None/空/{} 边界 |
| `TestInputValidation` | search/record/classification/synonyms 参数校验 |
| `TestNetworkFetch` | live fetch 写缓存；404→None；204→空列表 |
| `TestCacheStats` | entry_count / file_size_bytes |
| `TestWormsViewOffscreen` | WormsView 构造；on_activate 无异常 |
