# Claude 对《codex-260710-suggestion》的复审响应

> 日期：2026-07-10
> 复审者：Claude Code（Fable 5）
> 复审基线：`main@02fe507`（含本日两批已提交修复,见 §4）
> 证据来源：4 路并行只读审查(DB 正确性/架构深度/服务设计/UX) + 主线程逐条人工复核 file:line + 本日实测

## 1. 总评

**接受 codex 文档的框架与纪律**：不推倒重写、等价优先、一责任一提交、先保护再重构、
第 8 节验收模板、第 10 节禁止清单 —— 与本侧结论一致,无需再议。7/10 的工程评价与
本侧「骨架健康、债务可定位」判断相符。

两点总体修正：

1. **codex §2.2「工作区存在尚未提交的 Claude Code 优化」已过时**：私有连接治理已提交
   `02fe507`,conftest DeferredDelete 治理已提交 `b1f58aa`,均为独立提交、未夹带截图/脚本
   (即 codex 阶段 A 的第 5 条已满足)。
2. **codex 覆盖面广但有 9 项本侧增量发现未在文中**(§5),其中两项建议提级。

## 2. 逐答 codex §9 的七个复审问题

### Q1 私有连接改动是否存在写入/事务/线程语义变化

**结论：一处刻意的语义变化,已用契约测试锁定;无事务/线程语义变化。**

- 写入语义：唯一写路径 `data_filter_view._log_edit_audit` 走 `log_event`,其内部自带
  `db.commit()`(activity_audit_service.py:55),私有连接 + finally close 后行为不变。
- **刻意变化**：`open_project_db(create=False)` 每次 open 无条件跑 `ensure_schema`
  (db_manager.py:179)= executescript + 自动 ALTER + 迁移 + DROP/CREATE VIEW + commit。
  改私有 plain open 后,**只读汇总不再静默迁移被扫子库** —— 这正是 CLAUDE.md 读路径
  规约的本意。兼容性：老库缺列由 `Specimen.from_row` 的 `d.get(k)` 容错(specimen.py:56,
  自动 ALTER 加的列本来也是 NULL,净结果等价);缺表由各调用点 `except sqlite3.Error`
  跳过(旧行为=建空表后查到 0 行,净结果等价)。
- 契约测试：`tests/test_project_summary_service.py::TestPrivateConnectionGovernance`
  ×2(不留缓存连接 / 不对 legacy 子库建表)。
- 线程：私有连接默认 `check_same_thread=True`,全部在 GUI 线程使用,无变化。
- **未验证残留**(codex 阶段 A 第 3 条)：Windows 真机上「查询后目录可移动/删除」的
  实测。WSL 侧无法等价验证文件锁行为,留待 Windows 环境。

### Q2 DeferredDelete 全局 fixture 是否掩盖产品生命周期缺陷

**结论：不掩盖 —— 它修的是 pytest-qt 特有的测试腔缺陷;但它顺带暴露了一个真实的产品问题,恰好是 codex P1-10 缺的证据。**

- fixture(b1f58aa)只在每测试后 `sendPostedEvents(DeferredDelete)+processEvents`。
  生产环境事件循环持续转动,DeferredDelete 从不积压 3 个重型视图 —— 该 segfault
  是 pytest-qt「close+deleteLater 但不转循环」的采样偏差,不是产品路径。
- 但「3 个死视图同批销毁必崩」间接说明重型视图析构时仍挂着活资源。产品侧的对应
  实锤是本侧发现:**`on_deactivate()` 是死代码** —— `MainWindow._activate_index`
  切页只调新页 `on_activate`,全仓库 `on_deactivate()` 调用点 = 0(仅测试直接调),
  workbench/project_tree 写好的清理(停 QTimer/移除 fs watcher/停预热线程)从不执行。
  这是 P1-10「后台任务所有权」应优先处理的具体入口,证据比"责任分散"更硬。
- ⚠ 接通该 seam 是**行为变化**(v0.54 基线):须先由用户拍板「切走工作台后目录监控
  继续还是暂停」,逐 view 决定,不能一刀切。

### Q3 循环依赖的最小打断点

**结论(已实测,`scripts/check_import_cycles.py`,AST + Tarjan SCC,排除
TYPE_CHECKING 块):顶层运行时 import 环 = 0 组;延迟 import 构成的概念环 = 3 组。**
codex 的 4 与 v0.55 审计的 6 都不准(前者含一个 TYPE_CHECKING 假阳性,后者是旧快照)。

| 组 | 成员 | 定性与最小打断点 |
|---|---|---|
| 1 | workbench_view + 3 个 workflow mixin + retroactive_modal(5 模块) | mixin 家族固有形态:mixin 引用宿主类型。**非掩盖,不必打断**;若要消除,mixin 侧仅 TYPE_CHECKING 引用宿主即可 |
| 2 | main_window ↔ settings_view ↔ settings_ui_workflow ↔ helicon_config_dialog(4 模块) | 真·层级混串(widget→view→…→main_window)。最小断点:`settings_ui_workflow -> main_window` 这条边,改为 helicon 状态经 ctx/信号通知,不 import 主窗口 |
| 3 | project_catalog_service ↔ workspace_index_service(2 模块) | 双向各一条延迟边(register 挂钩 refresh / KPI 读 meta)。最小断点:`refresh` 挂钩改由调用方(catalog 的调用者)显式触发,或抽 meta 读取到独立小模块 |

图规模基线:291 模块 / 631 顶层边 / 1041 全边。脚本退出码 1 可挂 CI 冻结新增
(codex P1-06 验收「静态依赖检测降到 0」应修订为:**顶层保持 0,延迟环冻结在 3 不涨,
组 2 是唯一值得主动打断的**)。

### Q4 ProjectTreeView 第一刀怎么只拆 UI

**结论：接受 codex P1-01 的「第一刀只拆 UI builder」,并补上本侧 seam 实测图。**
4456 行单类的 section banner 已给出天然切线:

| 行段 | 内容 | 处置 |
|---|---|---|
| 123–1630, 1938–2462 | 树构建 + 项目卡片 | 第一刀:只抽 `_setup_ui`(825 行)的纯 builder |
| 2462–2633 | 详情面板 | 随 builder 抽 |
| 2633–3765 (~1130 行) | T5 数据汇总中栏 | 第二刀:`SummaryPanel` widget/mixin(照 WorkbenchView 的 4-mixin 模式,taxonomy/settings 同款,仓内已验证) |
| 3765–4082 | worker 生命周期 | 第三刀,与 P1-10 联动 |
| 4082–4456 | 跨库工具启动器 | 第四刀 |

约束:不动选择/查询/导出规则;`project_tree_content_mode`、编号语义(PROJECT_MEMORY)
原样;每刀独立提交 + `test_project_tree_view.py`(43 tests)+ 截图关键结构比对。

### Q5 更新签名启用后的兼容策略

**结论:天然向后兼容,无需迁移垫层。** 机制(update_service.py):公钥为空 ⇒ 不验签;
配置公钥 ⇒ 拒绝无签名 Release。因此:老客户端(无内置公钥)对签名/未签名包都接受,
不受影响;从「内置公钥的第一个版本 vN」起,**所有 ≥vN 的 Release 必须带 `.sig`**,
一旦开始永不回头(否则 vN 用户无法再升级)。旧的未签名历史 Release 无需补签(只有
老客户端会装它们)。发布流程要点:私钥离线 + CI Secret;`SPECIMEN_UPDATE_PUBKEY`
环境覆盖仅用于测试,不进发布文档。风险:密钥丢失 = ≥vN 客户端永久无法自动升级,
密钥备份属 P0-04 验收的一部分。

### Q6 哪些建议已有实现,应改「保持并加强验证」

| codex ID | 现状证据 | 建议改判 |
|---|---|---|
| P1-09 连接所有权 | 三类契约已成文(db_manager docstring)+ 今日 4 处违规修复 + 契约测试×2 | **大部分已解决**;余项=写 ADR(并 P2-32) |
| P1-16 手选 JPG 规则 | PROJECT_MEMORY 全节 + 回归锚点(TestImplicitCompose 等 4 组) | 已实现,保持 |
| P1-17 路径身份 | path_utils 双形态 + PROJECT_MEMORY 专节 + 定向测试 | 已实现,保持 |
| P1-23 缩略图预算 | thumbnail_disk_cache:失效键(path+mtime_ns+size+bucket)+ .fail 哨兵 10min TTL + pid 防多开互踩 + 原子发布 | 大部分已实现;余项=并发预算量化 |
| P1-24 增量失效 | summary `_cache_signature:150` + global_results `_dir_sig:120` 已签名失效 | 半已实现;**真缺口只剩 workspace_index_service:244**(只查行存在,不比内容,唯一无失效信号的缓存) |
| P0-03 依赖锁定 | ec5fa75 已 pin 部分 Windows 依赖;requirements.txt 仍 23 处 `>=` 区间 | 部分已做;补 constraints 锁定文件 |
| P0-04 签名 | bbfd2ae 已写启用步骤;休眠原因=待用户离线生成密钥(用户动作,非代码) | 待用户,非待开发 |
| P0-08 归档故障 | 四前提门控 + contract test(test_archive_service)已在 | 半已实现;补磁盘满/中断注入 |
| P2-06 测试隔离 | run_tests_batched.ps1 + b1f58aa 拆雷 | 已实现,保持 |
| P0-06 迁移原子性 | migrations.py 版本号仅在迁移函数成功后写(:94-95),失败重跑安全,现有迁移幂等 | 框架健康;真缺口=(a) `:98-100` SCHEMA_VERSION 无对应迁移时静默强对齐的 footgun (b) 复杂数据迁移前快照 |

### Q7 哪些指标缺证据

- 宽异常数:codex 721 vs 本侧 699 —— 都是静态 grep,口径不同;要治理先固定口径脚本。
- 循环依赖 4 vs 6 组(见 Q2/Q3),需脚本。
- 「大项目性能退化」(P1-21)全部未实测,本侧同样只有结构推断(主线程重路径证据见 §5.7);
  1k/10k/50k 假数据生成器是先决条件。
- 文档乱码(P2-35):本侧全程 UTF-8 读取未遇替换字符,**未确证**存在问题,建议 codex
  给出具体文件名再立项。

## 3. 分级裁定(codex 建议逐条/分组)

**P0:全部接受**,修正两条:P0-01 的 v0.56 tag 已存在(`git tag` 实测),验收只剩
「Release 资产可重新下载」;P0-06 按 Q6 表缩窄范围。P0 中最高杠杆是 **P0-07 历史库
升级矩阵** —— 本日发现的「读路径静默迁移子库」正是没有升级矩阵时最容易漏的回归类型。

**P1 架构(01–14):接受 12 条,改判 2 条**(P1-09 大部分已解决、P1-03 部分接受:
WorkbenchView 已是 4-mixin + 9 workflow 文件的成熟分解,扇出 40 是 import 计数而非
单体失控,收口前先加生命周期测试即可,优先级低于 P1-01/P1-10)。

**P1 业务(15–20):方向全接受**,多数已有实现(Q6 表);P1-20 修正:「连接必定关闭」
今日已修(data_filter 私有连接);「审计失败本地留痕」仍缺,保留为小项。

**P1 性能(21–25):接受**,P1-25 补本侧实锤(§5.7)。

**P2(测试/可观测/安全/文档):框架接受**,与 UI 相关的 P2-21~30 受本仓 UI 冻结规则
约束 —— 每条须用户在当次对话明确点头,不得以「优化」名义批量做;P2-09 结构快照与
本侧「灰按钮无 tooltip」发现互补。

**P3:同意全部推迟。**

## 4. 已实施(本日,三批独立提交)

1. `02fe507` — 跨工作区读私有连接收口(4 处)+ 隐式迁移堵截 + 契约测试×2。
2. `b1f58aa` — (并行会话)conftest DeferredDelete 拆雷。
3. 本提交 — `user_projects.json` 原子写(temp+os.replace,4 个写点收敛为
   `_atomic_write_user_projects`,§7 旧代码注释保留)+ `metadata_panel` worker 保活
   (`_track_worker`,修连点地理编码的 "Destroyed while thread is still running"
   崩溃风险)+ 新测试×3。对应 codex M11「临时文件原子替换」与 P1-10 的最小切片。

## 5. 本侧增量发现(codex 文档未覆盖,建议并入路线)

1. **on_deactivate 死 seam**(见 Q2)→ 并入 P1-10,置顶,但需用户拍板监控策略。
2. **open_project_db 恒跑 ensure_schema**(见 Q1)→ 主汇总路径已堵;`import_service`
   等「主动接管」路径属合理使用,无需再动。
3. **`organize_service._parse_uid_from_tiff_name` 对 legacy 无站位名(6 段)恒返 None**
   (organize_service.py:40 要求 ≥7 段):`supplementary_service:87` 与
   `retroactive_service:69,275` 因此对 legacy 文件名失效;且违反 PROJECT_MEMORY
   「禁止 split('-') 手搓、必须走 `parse_tiff_result_detail`」禁令。**修复需贯通
   naming components 配置 + oracle(app.js:3808-3824)对照,单独提交** —— 提级为
   P1 业务正确性新条目(编号是本软件命脉)。
4. **photo_count 双口径**:cross_workspace_query:482(`len(g.items)`)vs
   survey_overview:31-41(`res["total"]`)→ 两个汇总面板可能给出对不上的数字;
   并入 P1-02 查询门面时统一。
5. **死 ctx handoff**:`pending_worms_job_id` 写无读(taxonomy_worms_workflow:63);
   `ctx.current_specimen`/`ctx.save_specimen` 读无写(worms_view:604,617,永远 None
   的死分支)→ 并入 P1-12,按其「先证无调用者再动」流程。
6. **i18n 半成品**:en.json 190 key vs 用户可见未包 `tr()` 字符串约 1873 行;设置页
   English 开关承诺未兑现 → 新 P2 条目:补译或暂藏开关,二选一(产品决策)。
7. **主线程重路径实锤**(支撑 P1-25):`data_filter_view._run_query`(N 库全表扫,
   按钮 handler 直跑)、`project_tree_view._run_data_summary_query:2853`(内含每
   标本 EXIF + glob + **子库 DB 写**)、`summary_view.py:788`「全部项目」同步遍历
   + `:1318` Excel 导出无进度。同文件已有 QThread warmup 范例(project_tree:2925),
   模式现成。进度 UI 属可见变化,需用户点头。
8. **错误呈现两轨制**:`ui.exception()`(可复制 traceback)覆盖约一半流程,~40 文件
   仍直接 `QMessageBox.critical(str(exc))` → 并入 P2-13。
9. **specimens 零二级索引**(schema.sql 仅 2 个索引,均非 specimens)→ 并入 P1-22,
   按其「EXPLAIN 证据先行」原则,不盲加。

## 6. 联合执行序(codex 阶段 A–F 微调)

- **阶段 A(确认当前工作)**:已完成 4/5;余项=Windows 真机文件锁实测(需用户环境)。
- **阶段 B(P0 底线)**:同意;把 P0-07 升级矩阵排最前(理由见 §3)。
- **阶段 C(第一次等价重构)**:同意 ProjectTreeView UI builder 第一刀;切线按 Q4 表。
- **阶段 D(依赖治理)**:先落循环依赖测量脚本(Q3),再逐组。
- **插入 D'(小步业务正确性)**:§5.3 legacy 文件名解析(oracle 对照 + 双形名回归
  测试,独立提交)与 §5.4 photo_count 口径。
- **阶段 E/F**:同意;F 中 UI 项逐条过用户。
- **冲突仲裁**:同意 codex §11 第 5 条 —— 以可运行测试、真实数据、真实界面与用户
  确认为准,不以模型自信程度为准。
