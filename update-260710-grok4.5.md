# 标本影像工作台 — 设计评价与优化方案（Grok 4.5）

> 文档日期：2026-07-10  
> 评估者：Cursor Grok 4.5  
> 对象：`photo-platform-ydy-v3`（PyQt6 桌面标本影像工作台）  
> 性质：**评审 + 可执行优化路线**；默认不自动改代码，实施需用户明确授权  
> 对照：可与根目录 `codex-260710-suggestion.md` 交叉阅读（Codex 偏规模/依赖度量；本文偏领域语义、契约与分批落地）

---

## 0. 使用约定

1. 本文中的 P0–P3 均为**候选建议**，不是已开工任务。  
2. 实施前：读 `docs/PROJECT_MEMORY.md`；动核心流程时跑 `scripts/run_core_regression.py` 对应套件。  
3. 禁止：大爆炸目录搬迁、无 shim 改全库 import、改 UID 语义「按标准地理纠正」、自动删 TIFF。  
4. 成功标准：用户行为、文件结果、DB 结果与发布包行为一致；不是「目录更漂亮」。

---

## 1. 总评（一句话）

**领域设计强，工程结构在补课。**  
业务红线与工作流理解到位；架构成熟度落后于功能体量。最优路线是**小步、等价、可验证**的整理，而不是推倒重写。

综合观感约 **7/10**：可稳定使用与迭代；尚未达到「低成本长期扩展」的最优形态。

| 维度 | 评价 | 说明 |
|------|------|------|
| 产品 / 领域 | 高 | 标本拍照、归档、编号、协作有真实行业约束 |
| 架构骨架 | 中高 | DI + lazy view + services/views/widgets 分层正确 |
| 可维护性 | 中 | 大文件、扁平 services、契约偶发漂移 |
| 数据安全 | 高 | TIFF/JPG/导入只读/路径安全认真 |
| 演进能力 | 中→中高 | 已有目录契约与迁移清单，可渐进 |

---

## 2. 设计优点（应保留）

### 2.1 领域红线清晰

以下规则是产品灵魂，任何优化不得削弱：

- TIFF **永不自动删除**；用户手动删除须确认。  
- JPG 删除须四条件（cjxl、ZIP、manifest、djxl 可恢复）；默认不删。  
- 选中 JPG 的「合成 / 合成+整理」**不要求**先激活编号。  
- 导入源 JSON 只读 + sha256；路径走 `SafePathRegistry`。  
- cjxl 仅 `--distance 0 -e <effort>`。

权威：`docs/PROJECT_MEMORY.md`、`docs/specs/photo-grouping-workflow.md`、`app/services/archive_service.py`。

### 2.2 壳层与依赖注入

- `AppContext` 单容器；页面互不 import，handoff 走 `ctx.*`。  
- `LazyViewSpec` + 首次打开再构建页面，控制启动成本。  
- 顶栏 pin + 工具箱分组，符合「主线少、工具多」的桌面习惯。

### 2.3 分层意识正确

| 层 | 职责 |
|----|------|
| `services/` | 业务编排，优先 Qt-free |
| `views/` | 整页 `BaseView` |
| `widgets/` | 多页复用面板 |
| `utils/` | 命名、标签数学、路径、对话框 |
| `workers/` | 长任务 / 线程 |

### 2.4 行为 oracle

以 web 原型为行为真相，不凭空发明流程——对「忠实移植」类项目至关重要。

### 2.5 近期结构债已开始还

- `docs/architecture/directory-boundaries.md`  
- `docs/migration-checklist.md`  
- `app/services/{project,specimen,taxonomy,label,collab}/` 锚点 + shim 试点（如 `edit_lock_service`）  
- F821 未定义名修复；导航栈 placeholder / settings 兜底等契约修复  

---

## 3. 主要问题（按影响）

### 3.1 编号语义易被「纠正错」

用户习惯（必须遵守）：

| 段 | DB 字段 | 用户叫法 | 例 |
|----|---------|----------|-----|
| 1 | `province` | **省/市**（合并一段） | GXFCG、FJ |
| 2 | `site` | **地区 / 样地**（同义） | BLW、YGLZ |
| 3 | `station` | **站位** | B2 |
| 4 | `id` | **物种编号** | BZC003、SC001 |
| — | `storage` | 保存方式 | R（不是物种） |
| — | 文件名数字 | 成片序号 | 1、10（不是新标本） |
| — | `uid` | 完整标本号（无序号） | …-R-20260618 |

**风险：** AI/新人按「标准行政区划」把 GXFCG 当「地区」、把 SC001 当「样地编号」。  
**对策：** 标签唯一真相源 = `naming_field_catalog`；汇总/筛选走 `specimen_fields.field_label()`；禁止页面硬编码「省」「样地编号」。

### 3.2 大模块过重

典型热点（量级会随版本变，以当前仓库为准）：

- `ProjectTreeView`：树 + 汇总 + 筛选 + 成片网格 + 概览，职责过多。  
- `WorkbenchView` 及其 mixin：合成/整理/侧栏/命名，依赖面宽。  
- `collab_service` 及拆分模块：网络 + 同步 + UI 边界仍需纪律。  
- `theme.build_qss`：超长样式生成，改主题成本高。

**风险：** 改一处牵一片；全量 pytest 易因定时器/协作不稳定。

### 3.3 契约与实现偶发脱节

已出现过的类型：

- 启动 placeholder 占 `QStackedWidget` → lazy 契约 `_stack.count()==0` 失败。  
- 工具菜单注册了 `tiff_jpeg_tool`，测试仍假定仅「标签打印/采集地图」。  
- `FakeSettings` 缺属性 → 项目树汇总路径 AttributeError。  
- `restore_state` 延迟与「下一 tick」文档不一致。

**对策：** 壳层契约测试与实现同 PR 更新；FakeSettings 与真 `AppSettings` 对齐或统一 getattr 兜底。

### 3.4 测试与质量门

- 单文件测稳定；整仓易挂（Workbench QTimer、collab）。  
- CI 目前以 pytest 为主，静态检查（F821）尚未锁死。  
- 测试树扁平；`tests/unit/` 骨架已建，历史文件未归并（正确：勿批量搬）。

### 3.5 结构迁移未完成

子域目录多为锚点；多数模块仍扁平。`api-gateway/` 空占位需定期决策（保留文档化 vs 删除）。

---

## 4. 优化原则

1. **行为等价优先**：用户可见流程、文件、DB 不变，再谈结构。  
2. **一次一事**：一 PR 一类风险（契约 / 瘦身 / 子域迁移 / lint）。  
3. **shim 兼容**：移动实现，旧 `app.services.xxx` 路径保留。  
4. **单文件回归**：`pytest tests/<file> -q`，忌默认全仓。  
5. **编号语义冻结**：改标签只动 catalog；改解析只动 `naming.py`。  
6. **UI 冻结**：无用户明示不改布局/视觉/已确认 UX。

---

## 5. 分阶段优化方案

### 阶段 A — 立即收益（P0，1–3 天量级）

| # | 项 | 做法 | 验收 |
|---|-----|------|------|
| A1 | CI 锁 F821 | `.github/workflows/ci.yml` 增加 `ruff check app --select=F821`（或 `scripts/lint_f821.py`） | PR 上未定义名必红 |
| A2 | 编号契约测试 | 保持/扩展 `test_uid_segment_labels_match_project_memory`：省/市、地区/样地、物种编号、uid≠id | 改错标签即失败 |
| A3 | 壳层契约对齐 | placeholder 不占 stack；菜单测试与注册表一致；`restore_state` 用 `waitUntil` | `test_main_window.py` 绿 |
| A4 | FakeSettings 兜底 | 项目树读列用 helper；survey 测试 FakeSettings 补齐属性 | `test_project_tree_*.py` 绿 |
| A5 | 根目录噪音 | 临时输出进 `tmp/`/`artifacts/`；`.gitignore` 已补则维持 | `git status` 干净 |

**不做：** 批量 F401、搬大 view、改归档逻辑。

---

### 阶段 B — 结构渐进（P1，按周）

按 `docs/migration-checklist.md` 继续：

| 批次 | 内容 | 风险 |
|------|------|------|
| B1 | 叶子 service 迁子域 + shim（naming_field_catalog、collab_types、label_design_schema 等） | 低 |
| B2 | project 侧只读聚合（survey_overview、cover_pick、cross_workspace_query） | 中低 |
| B3 | specimen_filter / 数据汇总链路 | 中 |
| B4 | collab 成组迁移（api/file_sync/types 已有则巩固） | 中 |
| B5 | **最后**再动 `project_service` / `collab_service` 门面 | 高 |

每步：单独 commit + 对应 pytest + 失败即回滚。

新测试：落 `tests/unit/{services,views,widgets,utils}/`；旧测试不批量搬。

---

### 阶段 C — 大模块瘦身（P2，按月）

**目标：** 减职责，不改行为。

#### C1 项目树（优先）

建议切面（逻辑边界，非一次拆完）：

1. **范围选择** — 树多选 / 子树工作区列表  
2. **数据汇总** — 筛选条 + 编号表 + 列选择（已部分服务化）  
3. **成片网格** — `UidGroupedGrid` + 标注模式  
4. **调查概览** — 右栏 KPI / 地图 / 分布  

View 只编排；查询继续走 `cross_workspace_query_service` / `survey_overview_service`。

#### C2 工作台

保持 mixin 拆分方向；新增能力进独立 mixin/service，禁止再向 `workbench_view.py` 堆千行。  
合成/整理红线：改前读 `photo-grouping-workflow.md`，跑 `run_core_regression.py workbench compose`。

#### C3 主题 QSS

`build_qss` 按组件块拆文件或函数；`apply_theme` 入口不变；视觉 diff 用 `docs/shots` 抽检。

#### C4 标签设计器

属性面板：值编辑走 live refresh（已有方向）；结构编辑才 rebuild；避免 `deleteLater` 未 flush 导致测试/拖拽中断。

---

### 阶段 D — 质量与文档（P2–P3）

| # | 项 | 说明 |
|---|-----|------|
| D1 | F401/F811 分目录清理 | 先 `config`/`utils`，再 services；忌一次全仓 |
| D2 | 归档/线程/DB/删除人工复审 | 安全红线，禁止自动批量改 |
| D3 | docs 真相源 | `specs/`+`PROJECT_MEMORY`+`adr/` 为当前；`superpowers/` 为历史 |
| D4 | `api-gateway` 决策 | 6 个月无需求 → ADR 后删除；否则保持 README 占位 |
| D5 | `data/` 大库 | 离线大库外置（下载脚本/LFS）；`data/cache` 继续忽略 |

---

### 阶段 E — 明确不做（除非产品变更）

- 把 `province` 拆成真实「省」「市」两列（需迁移与产品确认）。  
- Electron 回迁。  
- 为「目录美观」无 shim 全库改 import。  
- 全量 pytest 作为唯一门禁（应用分文件 + named regression）。  
- 像素级复刻 web QSS。

---

## 6. 分模块详细建议

### 6.1 编号 / 命名

- **唯一解析：** `app/utils/naming.py`（`parse_uid` / `parse_tiff_result_detail` / `uid_group_key` / `uid_display_core`）。  
- **唯一标签：** `naming_field_catalog` → `specimen_fields.field_label`。  
- **默认短标签：** `GXFCG-BLW-BZC003`（省/市-地区/样地-物种）。  
- **禁止：** `split('-')[i]` 猜段；把 `R` 当物种；把 `-10-` 当新标本。

### 6.2 工作台

- 激活编号 = 默认归属上下文，不是所有操作的权限门。  
- 手动选中 = 明确意图。  
- `incoming-jpg/` = 待处理区（可含 TIF）。  
- 外部 Helicon TIF 一等公民。

### 6.3 项目树 / 调查汇总

- 左：选范围；中：数据汇总（表+可选成片）；右：调查概览。  
- 同标本多成片左侧一行（`uid_group_key`）。  
- 跨工作区只读；坏库跳过不抛。

### 6.4 协作

- LAN P2P 与 WAN relay 勿混。  
- 同步离主线程；信任列表门控。  
- UI 改动遵守「布局冻结」：无明示不改视觉。

### 6.5 标签

- 无 `elements` / 无 `grid_opts` 须与旧路径字节级一致（已有 parity 测试）。  
- 打印与预览同一渲染路径。

---

## 7. 建议实施顺序（性价比）

```
周 1：A1 CI F821 + A2/A3/A4 契约巩固
周 2–3：B1–B2 叶子/只读 service 迁移
周 4+：C1 项目树切面（先抽纯函数/服务，再减 view 体积）
并行：D3 文档索引；编号相关任何 PR 必跑 naming 套件
```

每完成一块：更新 `docs/migration-checklist.md` 勾选状态；用户确认后再开下一块。

---

## 8. 验收清单（任意优化 PR）

- [ ] 未改红线行为（或有用户明示 + 新回归测试）  
- [ ] 编号标签与 `PROJECT_MEMORY` 一致  
- [ ] 有 shim 或未破坏旧 import  
- [ ] 跑过触及文件的单测 / named regression  
- [ ] UI 无未授权的布局/文案大改  
- [ ] 未提交 `tmp/`、`artifacts/`、缓存、密钥  

---

## 9. 与 Codex 文档的关系

| 文档 | 侧重 |
|------|------|
| `codex-260710-suggestion.md` | 规模度量、循环依赖、类体积、发布基线策略 |
| `update-260710-grok4.5.md`（本文） | 领域语义、契约、分阶段落地、编号/工作台/项目树细则 |
| `docs/migration-checklist.md` | 文件级迁移步骤与回滚 |
| `docs/architecture/directory-boundaries.md` | 目录职责契约 |

三者冲突时：**用户口头确认 > PROJECT_MEMORY > specs > 本文建议**。

---

## 10. 结论

软件已经是**可用的专业工作台**，不是玩具原型。  
最大风险是「改功能时波及邻近流程」和「编号语义被善意改错」。  

优化应走：**锁契约 → 渐进分域 → 瘦身大模块 → 人工复审红线区**。  
不追求一次完美；追求每次改动可回退、可验证、用户无感行为回归。

---

*文档结束。实施任一章节前请用户明确授权范围。*
