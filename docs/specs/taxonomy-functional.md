# 内置分类库 — 完整功能 SPEC

> Oracle: web `app.js` + `server.js:353-730,837-982` + `docs/modules/taxonomy.md`
> Implementation target: `app/views/taxonomy_view.py` + `app/services/taxonomy_service.py`

## 1. 数据层（taxonomy_service.py）

### 1.1 种子库只读（server.js:837,884）
- `taxonomy_seed.json` 永不写入；任何 learn/update/delete 只写 `user_taxonomy.json`。
- `reload()` 强制重新从磁盘加载（用于外部修改后刷新）。

### 1.2 learn（server.js:837 `POST /api/taxonomy/learn`）
- 入参：`class / order / family / species`（4 级拉丁，全部必填）。
- 可选：`classCn / orderCn / familyCn / speciesCn / genus / genusCn`（中文字段，提供则存，不自动填）。
- 行为：4 元组完整 → 按 `class|order|family|species` key 查重。
  - 已存在 → `useCount += 1`，`lastUsedAt = now`，可选字段只在原值为空时更新。
  - 不存在 → 新建条目，`recordId = "user:<16hex>"`，`useCount = 1`，`addedAt = lastUsedAt = now`。
- 4 元组不完整 → 静默返回 `{}`，不写磁盘。
- 返回：upserted record dict。

### 1.3 update（server.js:892 `POST /api/taxonomy/update`）
- 只允许操作 `recordId.startswith("user:")`；种子库不可改。
- 写入前先把旧值追加到 `entry["history"][]`（最多保留 10 条）：
  ```json
  { "at": "<ISO8601>", "before": { "class":…, "classCn":…, "order":…,
    "orderCn":…, "family":…, "familyCn":…, "genus":…, "genusCn":…,
    "species":…, "speciesCn":… } }
  ```
- 写新值（所有 10 个可编辑字段），`lastModifiedAt = now`，保存到磁盘。
- 未找到 recordId → 返回 `None`。

### 1.4 delete（server.js:966 `POST /api/taxonomy/delete`）
- 只删 user 记录（recordId 以 "user:" 开头）。
- 返回 `True` / `False`（是否找到并删除）。

### 1.5 all_records / pagination
- `source_filter`: `None`=全部（user 在前），`"user"`=仅用户，`"seed"`=仅种子。
- 零基分页：`page=0, page_size=50`。
- 返回 `(page_records: list[dict], total_count: int)`。

### 1.6 search / candidates
- `sp_key` ∈ `{taxonGroup, order, family, scientificName}`。
- 空 query → 返回全部候选（受 ancestor 约束）。
- NFKC 归一化 + lowercase，匹配 Latin value 或 cn 字段，按首次命中位置升序排。
- ancestor 约束：知道 class→过滤 order；知道 order→过滤 family；知道 family→过滤 species。
- 结果最多 `max_results`（默认 30）。

---

## 2. 表格页面（taxonomy_view.py）

### 2.1 标题栏（app.js:renderTaxonomyPage ~12060）
- 页面标题「内置分类库」+ 统计「共 N 条」。
- 视图切换：**原始分类 / WoRMS 分类 / 对照视图**（segmented tabs）。
- 图表 toggle（当前为 stub，信息框提示）。

### 2.2 列控制（原始视图专有，app.js ~12100）
- **类群** chips：目 / 科 / 属 / 种（每个可独立开关）。
- **语言** chips：中文 / 拉丁名（每个可独立开关）。
- 纲（taxonGroup）列始终显示（不受类群 chip 控制）。

### 2.3 过滤栏（app.js ~12140）
- 列选择下拉（全部列 / 纲中 / 纲拉 / 目中 / 目拉 / 科中 / 科拉 / 属中 / 属拉 / 种中 / 种拉）。
- 搜索框 + 搜索按钮 + 清除按钮。
- 过滤激活时显示「已筛选 N 条」标签。
- 搜索为客户端过滤（桌面 GUI，无网络延迟，全量加载后本地筛）。

### 2.4 操作栏（app.js ~12160）
- **+ 新增条目**（仅原始视图）→ 弹出新增对话框。
- 已选 N 条 / 已选择全部筛选结果（N 条）。
- **全选筛选结果** / **取消选择**。
- **WoRMS 更新所选**（当前为 stub，提示前往 WoRMS 页）。
- **WoRMS 更新筛选结果**（stub）。
- **导出 Excel** / **导出 CSV**（导出当前视图全部记录，非仅当前页）。
- **导入 Excel/CSV**（弹出文件选择器，解析后批量 learn）。

### 2.5 表格（app.js ~12200）
- 列：☑ | # | 动态数据列 | 来源 | 操作。
- 数据列按 2.2 开关动态调整。
- ☑ 列：checkbox，全选/取消全选。
- # 列：显示全局行号（当前页偏移 + 行内索引 + 1）。
- 来源列：用户记录显示「用户」（绿）；种子记录显示「种子」（灰）。
- 操作列：每行内嵌「编辑」按钮；用户记录额外「删除」按钮。
- 用户记录行底色略微高亮（浅青色背景）。
- 双击行 → 若为用户记录则弹编辑对话框；种子记录弹只读提示。

### 2.6 编辑/新增对话框（app.js openTaxonomyTableModal）
- 字段：class / order / family / species（必填）+ classCn / orderCn / familyCn / speciesCn / genus / genusCn（可选）。
- 必填项未填时阻止提交并聚焦该字段。
- **编辑时**：若记录含 `history[]` 则显示「查看历史」按钮。
- 「查看历史」→ 弹历史列表对话框，每条显示 `at` 时间 + 变更前的 10 字段值；「回滚」按钮把该快照写回表单（不立即保存）。

### 2.7 删除确认
- 弹 QMessageBox 确认，显示物种名（种拉丁 + 纲）。
- 只对用户记录（种子不可删）。

### 2.8 导入（server.js:777 `POST /api/taxonomy/import`）
- 支持 `.xlsx` / `.xls` / `.csv`。
- 首行为表头，大小写不敏感，支持中英文列名：
  `class/纲 · order/目 · family/科 · species/种 · classCn/纲中文 · orderCn/目中文 ·
   familyCn/科中文 · speciesCn/种中文 · genus/属 · genusCn/属中文`。
- 逐行调 `learn()`；4 元组不完整则跳过（skipped 计数）。
- 导入完成后弹「成功 N 条，跳过 M 条」并刷新表格。

### 2.9 导出（server.js:410 `exportTaxonomyRows`）
- 导出当前视图全部记录（非分页，page_size=999999）。
- CSV：UTF-8 BOM，逗号分隔，首行表头（当前可见列标签 + 来源）。
- XLSX：openpyxl，sheet 名「分类库」，首行表头，每行数据。
- 导出完成后弹确认框（成功 N 条 + 保存路径）。

### 2.10 分页
- 默认每页 50 条。
- 上一页 / 下一页 / 跳到第 N 页（QSpinBox）。
- 页脚显示「第 P / T 页（共 K 条）」+ 「种子库 S 条 | 用户 U 条」。

### 2.11 自动学习（auto-learn）
- 在 `TaxonomyInputPanel`（taxonomy_input.py）中：用户在工作台录入标本分类，输入框 blur/编辑完成时若 4 元组完整则调 `svc.learn()`。
- 中文字段 (`*Cn`) 永不被自动填充。
- 见 `app/widgets/taxonomy_input.py _on_editing_finished` / `_commit_candidate`。

---

## 3. 硬规则（永不例外）

| 规则 | Oracle |
|------|--------|
| seed 只读，永不写入 | server.js:884 `atomicWriteJson(TAXONOMY_USER_PATH, ...)` |
| 中文字段不自动填充 | taxonomy.md 最后一条 |
| update 保存 history（≤10条） | server.js:934-943 |
| 导入 4 元组不完整 → 跳过 | server.js:802-810 |
| 删除只对 user: 记录 | server.js:966-980 |
