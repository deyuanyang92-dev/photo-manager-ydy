# 拍摄/整理 功能逐场景核对（活文档）

逐场景核对桌面版拍摄/整理流程是否忠实 Web 原型（`prototype-photo-gui/app.js` = 行为标准），
边核对边在源码加中文注释，发现的逻辑问题逐条修复。每条结论以证据为先（`file:line` / oracle 行号）。

状态图例：✅ 对 / ⚠️ 疑点 / ❌ 确认 bug（已修标 ✔）。

## 场景清单（脊柱）

**拍摄段**
1. 新建标本唯一编号 — `_on_new_specimen` / `_on_naming_save` / `naming.py`
2. 激活 / 去激活（全局互斥）— `activation_service.activate`
3. 实时监控新拍 JPG — `QFileSystemWatcher` + `monitor_service`（firstSeenAt）
4. JPG 归属（自动 + 手动）— `_on_assign_jpg` / `explicit_unassigns`
5. 阶段状态标记（每编号 4 点点 + 批次条）— ✅已实现
6. 分组（按角度）— `grouping_panel` / `grouping_service`
7. 合成 TIFF（Helicon）— `_on_compose_requested`
8. 无号合成 — `_on_free_compose`

**整理/归档段**
9. 整理 + 归档（4 前置 / TIFF 永不删）— `archive_group`
10. 撤销合成 — `_on_undo_compose`
11. 还原归档 — `_on_restore_archive`
12. 补处理 / 补充归档 — `_on_retroactive_scan` / `supplementary_service`
13. 批次条 + 监控统计 — `monitor_panel`

---

## 场景1：新建标本唯一编号 ✔（已核对 + 已修）

### 现状流程
- `+ 新增标本唯一编号` → `_on_new_specimen`（`workbench_view.py:850`）：建空草稿，只继承
  地区/样地 + 人员（项目设置），其余命名段清空；**不写库**。
- 实时预览编号 = `derive_uid`（`naming.py:42`）：`[地区,样地,站位,物种编号,保存方式,日期段]`
  滤空 join `-`，缺站位自动降级。
- `保存` → `_on_naming_save`（`:967`）：协作 409 认领 → upsert specimens（命名段）。

### 裁决
- ✅ UID 推导 / 缺站位降级 / 中文名不自动填 / 协作认领 / 幂等 upsert / 新草稿干净 —— 忠实 oracle。
- ❌→✔ **保存丢 metadata**（疑点1+2 同源）：`_schedule_rail_save` 只在 `_current_uid` 已设时触发
  （`:2217`），新草稿 `_current_uid=None` → metadata autosave 整段跳过；`_on_naming_save` 又
  不 flush 右栏 → 新号「先填 metadata 再保存」→ 采集人/经纬度/地理区/分类**静默丢失**。
  **修**：`_on_naming_save` upsert 后 set `_current_uid` 再调 `_on_save_metadata(uid)`，保存=存全部。
- ✅（非bug，疑点3）**保存后不自动激活** = oracle `autoActivateOnNewSpecimen:false`（`app.js:582`）。
  另发现 **key 不一致 bug**：复选框存 `workbench/auto_activate_new`，`settings.py` 属性却读
  `..._on_new_specimen` → 设置永远读不到勾选 + `_on_naming_save` 无人执行。
  **修**：对齐 key（`settings.py`）+ `_on_naming_save` 读开关，开则自动激活（默认关）。
- ➕（新需求）**坐标继承「两者都要」**：优先级 **项目默认 < 站位采集记录 < 用户手动/已存**。
  新项目设置键 `capture_defaults`（默认经纬度/地理区，drawer「命名规则」tab 可填）→ 新号兜底；
  选定有记录的站位 → 采集记录覆盖项目默认（不碰手动）。
  **机制**：`metadata_panel._auto_fields` 跟踪「自动填」字段；`apply_autofill(override_auto=)`
  只覆盖空或自动字段；`_apply_collection_autofill` 把自动字段当空看以触发覆盖。

### 改动文件
- `app/views/workbench_view.py`：`_on_naming_save`（flush + 自动激活）、`_on_new_specimen`
  （灌项目默认坐标）、`_apply_collection_autofill`（override 语义）。
- `app/widgets/metadata_panel.py`：`_auto_fields` + `apply_autofill(override_auto)` + `auto_fields()`。
- `app/config/settings.py`：`auto_activate_on_new_specimen` 对齐 key + 字符串解析。
- `app/services/project_settings_service.py`：`DEFAULT_CAPTURE_DEFAULTS` + prefill 返回坐标。
- `app/widgets/project_settings_drawer.py`：默认经纬度/地理区输入 + 存/读。

### 测试
- `tests/test_workbench_view.py`：`TestSaveButtonPersistsMetadata`、`TestAutoActivateOnSave`。
- `tests/test_collection_autofill.py`：`TestMetadataAutofillPrecedence`、`TestProjectDefaultCoordsPrefill`。

---

## 场景2：激活 / 去激活 ✔（已核对 + 已修）

### 裁决
- ✅ 互斥（激活时其余去激活）、`is_active=1`+`activated_at`、记激活事件（决定归属时间窗）、
  去激活不记事件 —— 忠实 oracle（`activation_service.py:121,168`）。
- ❌→✔ **激活不自动置「拍摄中」**：oracle 激活新号即 `status=shooting`（`app.js:3531-3534,3556`），
  Qt 仅写 is_active → 激活后点点/批次条空白。**修**：`_on_sidebar_activate` 激活后若无阶段
  （None/created）→ `_set_phase(uid,"shooting")`；已有更高阶段保留。
- ❌→✔ **切换激活号无提醒**：oracle 切号弹 toast「旧号此前照片仍归旧号」（`app.js:3517-3520`），
  Qt 静默切换。**修**：用 `activate()` 返回的 `previous_uid`，切号时 `_status_message` 提醒。

### 改动 / 测试
- `app/views/workbench_view.py`：`_on_sidebar_activate`（auto-shooting + 切号提醒）。
- `tests/test_workbench_view.py`：`TestActivateBehaviour`（4 例）。

## 场景3：实时监控新拍 JPG ✔（已核对 + 已修）

### 裁决
- ✅ 归属 4 级优先（黑名单/分组/手动/激活时间窗）+ firstSeenAt（非 mtime，只存一次永不覆盖）
  + 实时监听 + 兜底定时器 —— 忠实 oracle（`monitor_service.py:101-150`,`monitor-service.js:101-116`）。
- ❌→✔ **incoming/results 子目录写死**：设置页能改（`project/incoming_subdir`）、有遗留别名
  `新拍JPG`，但 `_setup_fs_watcher`（写死）+ `_refresh_monitor`（scan 用默认）都不认 → 改了
  目录名或用 `新拍JPG` 时监控失效。**修**：`_resolve_capture_subdirs()`（设置值 + 新拍JPG
  存在性兜底）；watcher 监听解析后目录；scan 传解析后 subdir。
- 待办（场景7/9 对齐）：合成/整理仍写死 incoming-jpg/results（`_on_compose_requested:1570` 等）。

### 改动 / 测试
- `app/config/settings.py`：`incoming_subdir`/`results_subdir` 属性。
- `app/views/workbench_view.py`：`_resolve_capture_subdirs` + watcher + scan。
- `tests/test_workbench_view.py`：`TestConfigurableIncomingDir`（4 例：解析/兜底/监听/扫描）。

## 场景4：JPG 手动归属 / 取消归属 ✔（已核对 + 已修）

### 裁决
- ✅ 归属到激活标本 → manual-assign 事件(P2)；指定归属/加入分组 → 进该号分组(P1)。
- ❌→✔ **「取消归属」做反**：oracle 撤销归属=加入 P0 黑名单打败一切来源
  (`server.js:4281-4294`)；Qt 现状只从分组(P1)删 → 拍摄期自动归属(P3)的照片不在分组
  → 点了无效，取消不掉；真正写黑名单的 `_on_unassign_jpg` 是死代码（信号 `deactivate_requested`
  从未 emit）。**修**：`_on_ctx_unassign` 写 `add_explicit_unassign`(P0) + 踢出合成组
  （后者按用户选定，偏离 oracle 的"只写黑名单"，避免废片仍被合成）。
- ❌→✔ **取消后归不回**：oracle 重新归属/加入分组时从黑名单移除(`server.js:4216-4219`)；
  Qt 不移除 → P0 永久卡住。**修**：`_on_ctx_add_to_group`/`_on_ctx_assign_uid`/
  `_on_assign_jpg` 都调 `remove_explicit_unassign` 解除黑名单。

### 改动 / 测试
- `app/widgets/monitor_panel.py`：`_on_ctx_unassign`(P0+踢组)、`_on_ctx_add_to_group`/
  `_on_ctx_assign_uid`(解除黑名单 + refresh)。
- `app/views/workbench_view.py`：`_on_assign_jpg`(解除黑名单)。
- `tests/test_monitor_panel.py`：`TestContextMenuUnassign` 新增 4 例。

## 场景6/7：分组 + 合成后自动整理 ✔（部分）

### 裁决
- ✅ 手动分组（建组/加照片/改角度标签/删组/合成）可用。
- ⚠️ **`groupingAutoWatch` 死设置**：设置页有「JPG 入库后自动分组处理」勾+模式，但全软件
  无人读 → 开=没开（oracle `app.js:3702,6175` 会自动加组+按模式处理）。**未动**（用户把
  "自动"重新定义为下方「合成后自动整理」）；保留为待办（要么按 oracle 接，要么删）。
- ➕ **合成后自动整理归档（新开关，默认关）**：用户需求——合成永远手动（软件无法判断哪些
  JPG 该合成）；开关开时，手动合成出 TIFF 后**自动**把源 JPG 打包压缩(JXL+ZIP)+命名+移
  results（= 自动跑 `_on_organise_requested`）。**绝不自动删 TIFF**。
  - `settings.py` `auto_organize_after_compose` 属性 + `settings_view` 复选框。
  - `workbench_view._maybe_auto_organize`，合成成功后调用。
  - 测试 `TestAutoOrganizeAfterCompose`（开/关 2 例）。

## TIFF 手动删除 ✔（用户推翻「TIFF 永不删」红线）

- 旧：TIFF 卡右键无删除项（写死 `monitor_panel.py if kind=="jpg"`）+ `_delete_paths` 选中 TIFF
  即警告中止 → 删不掉。
- 新：TIFF 卡加「删除此文件」；`_delete_paths` 对 TIFF 单独弹确认框（无损母片不可恢复）后删。
  **仅手动 + 确认**；归档/整理等自动流程仍绝不删 TIFF（`test_archive_service.test_tiff_never_deleted`
  保留）。CLAUDE.md 红线 #1 已改措辞。测试 `TestTiffDelete`（菜单/确认删/取消保留 3 例）。

## 场景8：无号合成 ✔（已核对 + 已修写死）

### 裁决
- ✅ 选中 JPG → Helicon 堆叠 → 输出到 incoming + 自动命名（自由合成-N）—— 忠实 oracle
  `freeComposeSelected`（`app.js:7982-8010`，toIncoming）。
- ❌→✔ **incoming/results 写死（写入/移动侧）**：之前只修了监控"看"的那半；workbench 里
  添加照片/无号合成/有号合成/整理移动 共 ~8 处仍写死 `incoming-jpg`/`results`。最伤的是整理
  移动用 `"incoming-jpg" in path` 判断 → 项目用 `新拍JPG` 时 TIFF/ZIP **移不到 results**。
  **修**：全部改用 `_resolve_capture_subdirs()`；移动判断改 `inc in normpath(p).split(sep)`
  （路径组件匹配，认 incoming-jpg / 新拍JPG / 自定义）。

### 改动
- `app/views/workbench_view.py`：`_on_add_jpg_files` / `_on_free_compose` / `_on_compose_requested`
  / `_on_organise_requested`（含 `_in_incoming` 辅助 + 同名 ZIP 检查 + archive_zip 路径）。

## 场景9：整理 + 归档（红线区）✅ 核对通过，无需改

- cjxl `--distance 0 -e`（无损 bit-exact）+ delete_jpg 默认 False + 4 道闸（cjxl 可用 / ZIP>32B /
  清单完整 / djxl 真能解回）+ TIFF 从不作删除对象 —— 严谨忠实 oracle archive.js。我加的"自动
  整理"复用同一套闸，安全。

## 场景10：撤销合成 ✔（已改：删TIFF + JPG解关联）

### 核对 + 用户裁决
- 拍照区核心数据模型 = **中间 JPG ↔ 对应 TIFF 的关联**。用户裁决：撤销合成 = **删除该 TIFF**
  （不可恢复，带确认框）+ **关联 JPG 解组放回自由池**（TIFF 没了关联失去意义，可重新分组/重拍）。
- 旧实现：把 TIFF 挪到 `_retired-tiff/`（退役不删）+ JPG 留组。**改为**：确认后 `os.unlink` 删
  TIFF + 移除整组（JPG 回监控自由池）。取消则全保留。
- `workbench_view._on_undo_compose` 重写；测试 `TestUndoComposeDeletesTiff`（确认删+解组 / 取消保留）。
- 注：`_retired-tiff` 仍由合成预览的「取消」(_retire_tiff) 使用；该目录未列入 RESERVED_DIR_NAMES
  （会被项目树当节点显示）= 待办小漏。

## 场景11：还原归档 ✅ 核对通过，无需改

- 从归档 ZIP 用 djxl 解码还原原始 JPG 到用户选的文件夹；只读 ZIP + 只写新 JPG（additive，删任何
  东西都不删）；有 JXL 但无 djxl → 中止不留半成品；清单丢失降级；bit-exact。`restore_archive` +
  `RestoreWorker` 离线程。忠实安全。

## 场景12：补处理 / 存量整理 ✔（已修写死目录）

- ✅ 存量整理 = 扫 results/ 下按编号命名的 TIF 批量整理；补充归档 = 无激活标本时从 TIFF 文件名
  解析标本身份归档 JPG+TIFF 包（`supplementary_service` + `SuppCompressionWorker`）。逻辑忠实 oracle。
- ❌→✔ **incoming/results 写死（延续场景8 清理）**：`_on_retroactive_scan` 没把配置子目录传给
  `scan_project_retroactive`；`_run_supplementary` / `_on_supp_finished` / `_RetroactiveScanDialog`
  写死 `results`。**修**：全部走 `_resolve_capture_subdirs()` / 传 `results_subdir`。

## 场景13：批次条 + 监控统计 — 待核对
