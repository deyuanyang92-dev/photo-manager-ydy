# Project Memory

This file records durable project decisions that the user has had to repeat many times. Future Codex / Claude Code sessions must read this before changing core workflow logic.

## Regression discipline for core workflow changes

The user is a beginner and relies on agents to keep the application coherent. Do not fix one visible bug by casually changing a shared workflow and leaving adjacent features unverified.

## Preserved UI fixes and no-regression areas

The user explicitly asked that successful fixes must be recorded and preserved. Do not casually rewrite these areas while fixing another visible issue:

- Windows/WSL path compatibility is required. A project saved as `/mnt/n/...` in WSL and reopened as `N:\...` on Windows must still resolve to the same workspace, including `owner_project_dir` lookups for the specimen sidebar and UID checks.
- The workbench specimen sidebar must show records from the current workspace even when the DB stores the owner path in the other runtime's syntax. Do not return to `owner_project_dir = current_project_dir` only.
- Pending photo cards must show real JPG/TIFF thumbnails when the files are decodable. Do not replace them with generic icon-only cards or a hidden/lazy path that leaves the user seeing no pictures.
- Organized results must show real TIFF thumbnails/previews when the TIFF is decodable. ZIP may remain a file/archive icon, but TIFF result cards should not be icon-only by default.
- The screenshot tool needs a visible, direct top-bar entry. It must not be discoverable only through a nested toolbox menu.
- When touching one of these areas, run the focused tests for that area and report exactly what was tested. Do not claim unrelated workflows were verified.

Do not tell the user a change is globally "fixed", "complete", "10/10", or "done" when only a local slice was changed or tested. Completion reports must distinguish:

- changed scope: exactly what was edited;
- verified scope: exact commands/tests/manual checks that passed;
- unverified scope: related workflows not exercised;
- residual risk: what can still break and why.

Before changing core workflow logic, the agent must:

- Identify the affected workflow area: naming, right rail save, compose/organise, TIFF preview, label printing, sidebar activation, collection autofill, or collaboration.
- State the likely adjacent features that can regress.
- Add or update at least one focused regression test when fixing a bug or changing behavior.
- Prefer a deep Module with one Interface over duplicating conditions across views. Good seams concentrate behavior and tests.
- Preserve user-repeated requirements already recorded in this file.
- Run the focused regression suite for the touched area before reporting completion.

The helper script is:

```bash
python scripts/run_core_regression.py
python scripts/run_core_regression.py naming
python scripts/run_core_regression.py workbench compose
python scripts/run_core_regression.py labels
python scripts/run_core_regression.py collab
python scripts/run_core_regression.py all-core
```

Default `quick` is the minimum smoke gate for small workflow edits. If the touched area is known, run its named suite too. If a test cannot be run, report that explicitly and explain the remaining risk.

## Windows/WSL same-workspace path scenario

User-confirmed scenario: the same specimen-photo workspace may be processed from WSL and later opened from Windows, or the other way around. Example: WSL stores a recent project as `/mnt/n/claude/zhengli`; Windows must treat that as the same workspace as `N:\claude\zhengli`.

Requirements to preserve:

- Persisted project paths, recent-workspace entries, `owner_project_dir`, UID checks, summaries, and file-open actions must tolerate both `/mnt/<drive>/...` and `<Drive>:\...` forms.
- UI entries such as “最近使用 / 磁盘目录” should show and operate on the path form usable by the current runtime, without corrupting the stored project identity.
- When the app runs in WSL but opens a folder on a mounted Windows drive, use the Windows Explorer path (`N:\...`) so the Windows desktop can open it.
- Do not narrow the model back to string equality on one path spelling. Use the shared path helpers and focused regression tests before changing this area.
- Once the user confirms a path-compatibility implementation works for this scenario, future agents must not casually rewrite it while fixing unrelated workflow issues.

## Workbench selected-JPG compose and organise

The user has repeatedly clarified this rule: selecting JPG files in the monitor is already an explicit manual operation. The software must not block that workflow with "please activate a specimen number first".

Hard requirements:

- `合成` and `合成+整理` on selected JPGs do not require an active UID.
- Active UID is only the default owner for newly shot photos or for workflows with no manual selection. It is not a permission gate.
- If an active UID exists, selected JPGs compose/organise under that active UID and auto-name by that UID's next result sequence. No output-name prompt is needed.
- If no JPGs are selected, the main toolbar `合成` / `合成+整理` must not silently take the active UID's loose JPGs. That requires a separate, explicit auto mode; activation alone is not permission to choose photos.
- If no active UID exists, selected JPGs still compose/organise, but the user must choose one of two paths:
  - assign/move to a target UID, then auto-name by that UID's next sequence;
  - use a free output stem, and use the same stem for both TIF and ZIP.
- When the user confirms "assign/move to a target UID", always write that JPG attribution, even if the target UID was prefilled from an existing hint.
- Existing JPG attribution, left-sidebar selection, and draft naming-preview values are only hints when no UID is active. They must not silently decide the output name.
- Do not silently name selected JPG outputs as `1.tif`, `2.tif`, etc. without active UID. The user must provide a meaningful output stem unless they choose a target UID.
- If a UID already has results, sequence must advance: existing `UID-1-YYYYMMDD.tif` means the next result is `UID-2-YYYYMMDD.tif`.
- `加入分组`, `新组`, and import-to-pending workflows must still work without active UID; use explicit selected JPG ownership or the unassigned task as appropriate.
- After `合成+整理` or `整理` succeeds, the ZIP consumes the source JPGs. The product default is to delete loose JPG files after archive verification passes; users may explicitly turn on keeping loose JPGs. Whether deleted or explicitly kept, consumed JPGs must not continue to appear as pending photos.
- `整理` / archive must not automatically delete TIFF results, but TIFFs are not sacred or undeletable: if a composed/imported TIFF is wrong, the user may explicitly delete it or undo the compose after confirmation.
- `自动归档` is the explicit fast incoming workflow switch. With an active UID and no selected JPG, toolbar `合成` may use that UID's unoccupied attributed JPGs. Without an active UID, it must not guess JPGs.
- External TIF auto organize is part of `自动归档`: when a TIF produced outside the compose button appears, archive it with explicit JPG sources. With an active UID, use that UID's unoccupied JPGs; without an active UID, use manually selected JPGs if present.
- Completion text must follow the real worker completion. Do not mark `合成+整理` or external TIF auto organize complete when the archive worker has only been started.

Primary spec: `docs/specs/photo-grouping-workflow.md`.

Detailed audit: `docs/audit/compose-organize-logic-2026-06-29-10x.md`.

Regression anchors:

- `tests/test_workbench_view.py::TestImplicitCompose`
- `tests/test_workbench_view.py::TestAdhocGrouping`
- `tests/test_monitor_panel.py::TestSelectionAddToGroup`
- `tests/test_monitor_panel.py::TestSelectionAccessors`

Do not reintroduce "请先激活编号" for selected-JPG `合成` or selected-JPG `合成+整理`.

## Workbench incoming/results media boundary

The user has repeatedly clarified the main workbench file lifecycle:

- `incoming-jpg/` is the pending workspace, not a JPG-only directory.
- New-project camera intake puts JPG originals in `incoming-jpg/`.
- TIFFs produced by this software's `合成` are also temporary pending files in `incoming-jpg/` until `整理` runs.
- TIFFs produced by external software must be importable through the main `添加照片` action into `incoming-jpg/`, then selected and processed with `整理`.
- `results/` is the organized output area. `整理` moves the TIFF and generated ZIP there.
- Do not split the main `添加照片` import as "JPG to incoming, TIF to results". That skips the required pending/organize state and makes imported TIFFs disappear from the queue.

## Workbench multi-scenario design intent

Do not interpret the workbench as a single rigid "shoot in one folder, then process that one folder" pipeline. The product must support multiple real photo-working styles, and the user's old habit of shooting JPGs and externally producing TIFFs in the same directory is one compatibility scenario, not the whole design.

Durable intent:

- The workbench should tolerate different entry points: new-project camera intake, manual file import, selecting existing JPGs, importing or discovering external TIFFs, software-created TIFFs, supplementary archival, and later binding of existing results.
- `incoming-jpg/` is a pending workspace abstraction. It may be the software-created intake folder, or it may point at / receive files from a directory that already matches the user's existing habits.
- Active UID is a default ownership and naming context, not the only way into the workflow.
- Manual selection is explicit user intent. If the user selects JPGs, or JPGs plus one TIFF, the software should process that selection according to the relevant mode instead of forcing a single activation-first flow.
- External composition tools such as Helicon used outside this software are first-class. A TIFF does not have to be generated by this app before it can be organized, named, archived, or assigned.
- Avoid hard-coding assumptions like "TIFFs only appear after in-app compose", "incoming means only JPG", or "activation is required before selected files can be handled".
- When adding or changing workflow behavior, preserve these scenarios side by side instead of simplifying the model to one favored path.

中文说明：

- 不要把这个软件理解成单一流程软件。它不是只服务“一个拍摄目录流水线”，而是要兼容多种真实使用方式。
- 用户过去“在同一个目录拍 JPG、外部合成 TIF、再整理”的习惯只是必须兼容的一种场景，不是唯一中心。
- 软件应该同时支持：新项目自动入库、手动添加照片、选中已有 JPG 合成、外部 TIF 入库/发现后整理、软件内合成后整理、补处理、已有成果后续绑定到编号。
- `incoming-jpg/` 的本质是待处理区，不是只能放 JPG 的目录；TIF 可以先在这里等待整理。
- 激活编号只是默认归属和命名上下文，不是所有操作的入口条件。
- 手动选中文件就是明确意图。选中 JPG、或选中 JPG + 1 个 TIF 时，应按对应模式处理，不要强行套“必须先激活编号”的单一路径。
- 外部 Helicon 或其他外部软件生成的 TIF 是正常工作方式，不能假设 TIF 一定由本软件生成。
- 后续改工作台逻辑时，要保留这些场景并行存在，不能为了代码简单把模型收窄成一种固定流程。

## Specimen UID / 编号语义（2026-07-09）

用户多次纠正：成片预览、项目树左侧编号列表、物种名录必须共用同一套编号理解。**权威实现是 `app/utils/naming.py`**；成片扫描用 `parse_tiff_result_detail()`；禁止在项目树/网格里另写一套「按 `-` 分段猜含义」的正则。

### 三层概念（不要混用）

| 层级 | 含义 | 例子 |
|------|------|------|
| **标本 uniqueId** | 一条标本一条，**不含成片序号** | `GXFCG-BLW-BZC003-R-20260618` |
| **成果 resultId** | 同一标本的第 N 张成片，文件名中间多一段 **数字序号** | `GXFCG-BLW-BZC003-R-**10**-20260618.tif` |
| **物种编号 speciesId** | 字段 `id` / `species_id`，物种名录聚合键 | `BZC003`（**不是** `R`，`R` 是保存方式） |

标准 7 段成果文件名：

`省/市-地区/样地-站位-物种编号-序号-保存方式-日期段`

Legacy（无站位）例：

`GXFCG-BLW-BZC003-R-1-20260618.tif` → uniqueId=`GXFCG-BLW-BZC003-R-20260618`，seq=`1`

**同一标本的多张成片（seq 1、2、10…）只能占项目树左侧一行**，不能每张图一行。

### 字段含义（用户项目实例）

- `GXFCG` = **省/市**（province，用户习惯把省、市混在这一段）
- `BLW` = **地区/样地**（site，两个叫法都指同一段）
- `B2` = **站位**（station）
- `BZC003` = **物种编号**（speciesId）— 与 `specimens.id`、物种名录一致
- `R` = 保存方式（storage，RNA）— **不是**物种号
- `1` / `10` = 成片序号（resultSequence）— **不是**保存方式，**不是**新标本
- `20260618` = 日期段（dateSegment）

### 用户口语 ↔ DB 字段（改 UI 前必读）

用户故意把日常叫法和 DB 列名「混用」在同一段里，**不是写错**：

| 用户怎么说 | DB 列 / 工作台键 | 例 | 不是什么 |
|------------|------------------|-----|----------|
| 省/市 | `province` | GXFCG、FJ | 不是第 2 段 BLW |
| 地区、样地（同一段） | `site` | BLW、YGLZ | 不是 GXFCG；两个中文名都指 `site` |
| 站位 | `station` | B2 | 可选；缺则 UID 少一段 |
| 物种编号 | `id` / `species_id` | BZC003、SC001 | 不是 R；不是样地编号 |
| 保存方式 | `storage` | R、T95E | 不是物种号 |
| 成片序号 | 文件名中间纯数字 | 1、10、11 | 不是 storage；不拆成新标本 |
| 完整标本号 | `uid` | …-R-20260618 | 不含序号 |
| 默认短标签 | `uid_display_core()` | GXFCG-BLW-BZC003 | 省/市-地区/样地-物种 |

**改标签时**：只改 `naming_field_catalog.py` 的中文 label；`specimen_fields.field_label()` 会跟随。不要在各页面硬写「省」「地区」「样地编号」。

**改分组/列表时**：必须先 `parse_tiff_result_detail()` / `uid_group_key()`，禁止 `split('-')[2]` 猜段含义。

### 显示缩写（ deliberately 不同用途）

所有显示函数在 **`app/utils/naming.py`**，网格/项目树必须调用它们，不要复制 `parts[2]-parts[3]` 逻辑：

| 函数 | 显示 | 用途 |
|------|------|------|
| `uid_display_core()` | `GXFCG-BLW-BZC003` | **默认短标签**：省/市-地区/样地-物种（项目树左侧编号、网格标注） |
| `uid_display_core_storage()` | `GXFCG-BLW-BZC003-R` | 含保存方式 |
| `uid_display_station_species()` | `B2-DLC001` 或 `BZC003-R` | 工作台卡片式「站位-物种」/ legacy 无站位时「物种-保存」 |
| `uid_group_key()` | 规范化 full uniqueId | **合并/分组键**（同标本多片） |
| full UID 字符串 | `GXFCG-BLW-BZC003-R-20260618` | DB `specimens.uid`、侧边栏完整编号、tooltip |

`grouping_service.uid_core_key()`（前三段按 `-` 切）与 `uid_display_core()` **语义相近但实现不同**；有站位时前者可能是 `FJ-YGLZ-B2` 而非 `FJ-YGLZ-DLC001`。新代码优先 `uid_display_core()`。

### 模块职责（保持一致）

- **`naming.py`** — 解析、拼 UID、显示缩写（唯一真相源）
- **`project_service.get_project_results()`** — 扫 `results/` TIFF，用项目 `naming_rules` + `parse_tiff_result_detail` 得 `(uniqueId, seq)` 再分组
- **`taxon_inventory_service` / 物种名录** — 按 DB `scientific_name` 聚合；物种编号来自 `specimens.id`，不另解析文件名
- **`uid_grouped_grid` + 项目树** — 按 `uid_group_key` 合并；左侧列表默认 `uid_display_core`；缩略图标注可在「标注」下拉切换
- **项目树中栏按需模式（2026-07-09）** — 选中树节点**只更新范围 + 右栏 KPI**；中栏 **「概览」** 或 **「数据汇总」**。数据汇总 = 通用筛选（`specimen_fields` 动态字段 + 日期区间）→ 编号表格 + 关联成片网格 + 筛选后 KPI；引擎 `cross_workspace_query_service.query_summary_scope`。设置键 `project_tree_content_mode`（旧 `photos/species/summary` 自动迁移到 `data_summary`）。
- **工作台 sidebar** — 显示**完整 UID**（合理，编辑上下文需要全串）
- **`tiff_naming_service`** — 文件名审计，与 `get_project_results` 同一解析器

### Agent 常犯错误（禁止再犯）

1. 把文件名里的 `-1-`、`-10-` 当成 storage 或新标本 → 导致「同一编号拆成很多行」
2. 把 `R` 当成物种号的一部分去合并/显示（物种是 `BZC003`）
3. 用 `uid_abbreviation` 的 `parts[2]-parts[3]` 代替语义解析（legacy 文件名会碰巧对、标准 7 段会错）
4. 在网格层「按显示缩写硬合并」而不先修 `get_project_results` 的 uniqueId
5. 改项目树 UI 时不读 `PROJECT_MEMORY` 和工作台/物种名录已有行为

### 回归锚点

```bash
pytest tests/test_project_service.py::TestGetProjectResults -q
pytest tests/test_project_service.py::TestGetProjectResults::test_legacy_gxfcg_result_files_group_by_specimen_uid -q
pytest tests/test_uid_grouped_grid.py -q -k "catalog or caption or merge"
python scripts/run_core_regression.py naming
```

相关 spec：`docs/specs/photo-grouping-workflow.md`（合成/整理）；命名 oracle：`app/utils/naming.py` 文件头注释。

## 项目树 · 数据汇总（2026-07-10，用户多次强调，禁止再混淆）

> **状态：待用户核对** — 本节由 Agent 根据对话整理，用户明确表示尚未逐项核实。  
> 作参考用，**不是固定规格**；若与实际操作或用户口头要求不符，以用户最新说明为准。核实后可删本提示或改为「已确认」。

用户是新手；下列需求已在对话中反复澄清。**改数据汇总、TIF 路径、成片网格、TIFF→JPG 前必须先读本节。**

### 一、数据汇总中栏要做什么

**入口**：项目树 → 中栏「数据汇总」。引擎 `cross_workspace_query_service.query_summary_scope`。

| 区域 | 要求 |
|------|------|
| 筛选区 | 动态字段 + 日期区间，固定在上方 |
| 编号表 | 可拖动行排序；表头可拖动调列序并持久化；支持多选行 |
| 分割条 | 在编号表正下方，可上下拖，调表格 vs 成片高度 |
| 成片网格 | 编号表选中行 → 下方只显示对应编号照片；`items[].path` 永远是母版 TIF |
| 工具栏 | 导出 CSV、显示列…、**转 JPG…**（见下文） |

设置键：`project_tree_summary_visible_columns`、`project_tree_summary_body_split_state`、`project_tree_show_photos`。

### 二、母版 TIF 路径 — 程序自己的事（红线）

用户**强烈反对**让手抄/粘贴 TIF 路径来排查。路径必须程序自动解析并持久化。

硬要求：

- 每个编号对应母版 `.tif` 的**绝对路径**必须写入 DB 索引表 `specimen_result_tif_index`（`specimen_result_tif_service.sync_workspace_result_tifs`）。
- 汇总查询前对每个工作区 sync；汇总字段 `photo_absolute_path`（主 TIF）、`result_tif_paths`（全部 TIF，分号分隔）。
- 成片网格、选片、导出 TIF 的底层 `path` **永远是母版 TIF**，不是 JPG。
- 找不到路径是程序 bug，不是让用户「把路径发我」。

相关：`app/services/specimen_result_tif_service.py`、`cross_workspace_query_service.enrich_specimens_with_photo_info`。

### 三、EXIF / 相机参数

汇总表中的 ISO、光圈等元数据必须读**原始母版 TIF 的 IFD**，不是内嵌 JPEG strip，也不是先读缩略图。实现：`app/utils/tiff_exif_read.py`（TIF 优先，exiftool 仅兜底）。

### 四、TIF → JPG：两套东西，绝不能混为一谈（用户最生气的一次误解）

用户说的「TIF 转 JPG」= **导出 JPG 文件给用户日常使用**（TIF 太大，有人要 JPG 副本）。  
**不是**程序内部为了网格不卡的预览缓存。

| | 预览 JPG（内部） | 导出 JPG（用户要的功能） |
|---|---|---|
| 目的 | 网格/对话框看图不卡 | 生成用户可拿走的 `.jpg` 文件 |
| 服务 | `image_thumbnail.ensure_tiff_master_jpeg` + `data/cache/thumbnails/` | `tiff_jpeg_export_service` + **TIFF 转 JPG** 工具页 |
| 是否弹 UI | 否，后台静默即可 | **用户主动点「转 JPG…」时才打开工具页** |
| 默认品质 | 磁盘缓存 quality 95（高清） | 预设 **「高清存档」** archive：Q95、subsampling=0、不限制最长边 |
| 能否当作品 | 否，程序自用 | 是，用户发图/日常用 |

**禁止**：把后台预览预热说成「已帮用户转 JPG」；禁止在每次打开数据汇总时自动弹出 TIFF 转 JPG 工具页。

### 五、导出 JPG 的正确交互（已实现方向）

1. 用户在数据汇总**编号表选中一行或多行**（批量）。
2. 点工具栏 **「转 JPG…」**。
3. 跳转到 `tiff_jpeg_tool`（工具箱 · TIFF 转 JPG），**预填**选中编号的所有母版 TIF 绝对路径。
4. 默认预设 **高清存档**；用户确认输出目录、覆盖策略后点「开始转换」。
5. 源 TIF **只读**，不修改、不自动删除（全局红线）。

ctx 交接：`ctx.pending_tiff_jpeg_sources`、`ctx.pending_tiff_jpeg_preset_id`（默认 `archive`）。

**未选中编号时不要静默转**；应提示先在表里选中。若用户以后要「整批筛选结果一键转」，需单独加按钮，不能替代「选中特定 TIF」的能力。

成片网格右键单张「导出 JPG」是快捷单文件路径，与汇总批量入口互补。

### 六、Agent 在本主题上常犯的错误（禁止再犯）

1. 把预览缓存 JPG 当成用户要的「TIF 转 JPG 导出」→ 用户会认为 Agent「变笨了」。
2. 加载数据汇总就自动弹 TIFF 转 JPG 工具 → 打断操作，用户不要。
3. 让用户手动提供 TIF 路径 → 用户认为是最基本能力，应由 `specimen_result_tif_index` + sync 解决。
4. 读内嵌 JPEG / 缩略图 EXIF 代替母版 TIF → 汇总表相机参数不准。
5. 把网格 `path` 改成 JPG 路径 → 破坏选片/导出母版逻辑。
6. 只改预览、不改「选中编号 → 导出工具预填」→ 用户无法在汇总里批量选想转的 TIF。

### 七、回归锚点

```bash
pytest tests/test_specimen_result_tif_service.py -q
pytest tests/test_cross_workspace_query_service.py -q
pytest tests/test_tiff_preview_warmup_service.py -q
pytest tests/test_tiff_jpeg_tool_view.py -q
pytest tests/test_tiff_exif_read.py -q
```

相关实现：`app/views/project_tree_view.py`（数据汇总 UI、转 JPG 按钮）、`app/views/tiff_jpeg_tool_view.py`（`load_tiff_sources` / `on_activate` 消费 pending）、`app/services/tiff_jpeg_export_service.py`（真正写 JPG 文件）。

