# 调查汇总视图 实现规格 survey-summary-view

> 2026-07-08。项目树页:**多选断面 → 照片按编号汇总预览 + 物种名录表 + Excel 导出**。
> 派生自用户三条需求。**不实现** v5 项目树重设计 plan 的其余 task(库根管理 / adopt / 身份镜像等)——那些另行。

---

## 1. 需求(用户原话 → 行为)

1. **多选断面汇总预览** — 树多选断面A+B → 中间一起显示两断面的照片。
2. **按编号(UID)分组显示** — 照片按标本编号分组(类 workbench 编号列表),每编号一组 + 序号。
3. **物种名录 Excel + 右侧显示** — 汇总所有选中断面的物种名录,右侧面板表格显示 + 导出 Excel/CSV/Darwin Core。

---

## 2. UI(三栏,改 ProjectTreeView)

```
左 树 ExtendedSelection 多选  │  中 网格 [扁平|按编号]  │  右 [单张|编号|物种名录]
                              │                         │
多选断面 → 中=选中各断面 results 照片并集;右侧默认跳「物种名录」
单选一张照片 → 右侧「单张详情」(现状行为不变)
按编号分组:每个 UID 一个 section header(站点+序号,完整 UID 在 tooltip)+ 该编号照片行
物种名录表:学名/中名/科/属/出现断面/数量;列可排序;底部 [导出 Excel][CSV][Darwin Core]
```

**右侧三态用 QStackedWidget**,按当前选择自动切:多选断面→物种名录;单选照片→单张详情;切「编号列表」手动。

---

## 3. 数据层(复用现成)

- **按 UID 分组**:`project_service.get_project_results(dir)` 已按 uid 分组返回 `{groups:[{uid,items}], ungrouped}`(project_service.py:777)。
- **跨断面**:对每个选中断面调一次,合并 `groups`(不同断面 UID 不同,天然不冲突)。
- **导出**:`export_service` 已有 Excel 34 列 / CSV UTF-8 BOM / Darwin Core。
- **新增** 物种名录聚合 service:扫选中工作区 `_data/project.db` 的 `specimens` 表,按 `scientific_name` 去重,统计「出现于哪些断面 + 各断面编号数」。

---

## 4. 物种名录定义 [默认 A,待用户确认]

- **A(默认):学名去重** — 每种一行(学名 / 中文名 / 科 / 属 / 出现断面 / 各断面数量)
- **B:标本逐条** — 每个编号一行(含 UID)

右侧支持切换,默认 A。若用户要 B,改默认即可。

---

## 5. 性能(防卡 — 这是本专项的硬约束)

- **网格虚拟化**:QListView + QAbstractListModel + QStyledItemDelegate,只渲染可见行。500+ 张不卡。
- **缩略图异步**:worker 线程返 QImage(线程安全),主线程 `make_pixmap`。worker 线程绝不构造 QPixmap。
- `image_thumbnail.py` 现状全主线程解码 + `_pil_image_to_pixmap` 用 tempfile PNG roundtrip(:175,慢)→ **必须拆** `decode_image_data(QImage)` / `make_pixmap(QPixmap)`。

---

## 6. 红线

- **不破坏现有 1495 行功能**:过滤 / 详情面板 / 统计 / `_open_summary_export` / `_open_station_species_summary` / `_open_station_import` / `_new_region` / `_new_subfolder` 全保留。
- 无 `species` / `species_cn` 列 → 用 `scientific_name` / `scientific_name_cn`(CLAUDE.md domain gotcha)。
- TIFF 不自动删;导入只读 sha256;cjxl flags 固定;路径安全 `SafePathRegistry`。
- **incoming JPG 无编号** → 按编号视图只含 `results/` TIFF;incoming 散片进「未分组」分组(复用 `get_project_results` 的 `ungrouped`)。

---

## 7. 编码约定(用户 2026-07-08 指令 — 必须遵守)

**改既有代码:旧实现用 `#` 注释保留,新实现写旁边,不删除。** 新增文件/新函数不涉及。默认永久保留直到用户让删。git commit 正常写。

---

## 8. 任务(TDD,每个 task 写失败测试→实现→绿→commit)

| Task | 内容 | 依赖 | 文件 |
|------|------|------|------|
| T1 | 物种名录聚合 service(pure,无 Qt) | — | 新 `app/services/taxon_inventory_service.py` + test |
| T2 | thumbnail worker + `image_thumbnail` 拆 QImage/pixmap | — | 改 `app/utils/image_thumbnail.py` + 新 `app/workers/thumbnail_worker.py` + test |
| T3 | UidGroupedGrid 虚拟化网格 widget | T2 接口 | 新 `app/widgets/uid_grouped_grid.py` + test |
| T4 | SurveySummaryPanel 物种名录表+导出 widget | T1 | 新 `app/widgets/survey_summary_panel.py` + test |
| T5 | ProjectTreeView 三栏接线 + 树改多选 | T1-T4 | 改 `app/views/project_tree_view.py`(旧代码注释保留) + test |
| T6 | 验收:多选两断面 → 网格 + 名录 + 导出全通 | T1-T5 | 集成 test |

---

## 9. 多 agent 编排

T1-T4 **parallel**(worktree 隔离,各建新文件零冲突)→ T5 **主线串行**接线 → T6 验收。
T5 改既有 1495 行文件,必须串行 + 遵守 §7 注释保留约定。

---

## Self-check

- 三需求全覆盖 ✅
- 数据层复用 `get_project_results` / `export_service`,不重造 ✅
- 性能红线(虚拟化 + 异步)显式 ✅
- 不破坏现有功能(§6)✅
- 注释保留约定(§7)✅
- 物种名录定义默认值 + 待确认标注 ✅
