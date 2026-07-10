# 标本影像管理软件模块地图

本文描述软件的业务边界和依赖方向。它不是按页面或文件数量机械分组，
而是按“谁拥有数据、谁负责规则、谁只是展示或适配”划分。

当前建议划分为 **9 个业务模块 + 3 个基础模块，共 12 个一级模块**。
每个模块仍遵守现有分层：`views` 负责整页，`widgets` 负责组件，
`services` 负责业务规则，`db` 负责持久化，`workers` 负责后台任务。

## 一、业务模块

### M1 项目与工作区

- 职责：调查根项目、子项目、断面工作区、目录认领、项目设置继承、项目树。
- 页面：最近使用、项目树。
- 核心服务：`project_service`、`project_tree_service`、`project_catalog_service`、
  `project_identity_service`、`project_settings_service`、`workspace_index_service`。
- 主要数据：`survey_project`、`workspaces`、`workspace_meta`、
  `workspace_index_cache`、`project_settings`。
- 边界：路径是位置，`project_id` / `workspace_id` 才是长期身份；项目树不复制
  标本编号解析逻辑。

### M2 调查与采集

- 职责：站位、坐标、采集事件、采集记录、地图和底图。
- 页面：采集记录、采集地图、坐标工具。
- 核心服务：`collection_record_service`、`collection_record_io`、
  `project_station_import_service`、`coord_import_service`、`geo_*`、
  `geocode_service`、`basemap_registry`。
- 主要数据：`collection_records`，以及标本记录中的采集事件引用/快照。
- 边界：站位、采集事件和标本是三个概念；地图只展示空间事实，不拥有标本。

### M3 标本与编号

- 职责：标本 CRUD、UID 生成与解析、编号序列、元数据、编辑锁、重命名。
- 页面：照片工作区右栏、数据筛选中的标本编辑能力。
- 核心服务：`capture_workflow_service`、`specimen_catalog_service`、
  `specimen_filter_service`、`specimen_rename_service`、`uid_sequence_service`、
  `activation_service`、`naming_field_catalog`。
- 主要数据：`specimens`、`uid_sequences`、`uid_reservations`。
- 权威规则：`app/utils/naming.py`。
- 边界：标本 `uniqueId` 不含成片序号；物种编号不是保存方式；各页面禁止自行
  按 `-` 拆分猜测编号含义。

### M4 照片采集工作台

- 职责：目录监控、JPG 发现与归属、标本激活、照片分组、拍摄阶段和操作编排。
- 页面：照片工作区。
- 核心组件：`WorkbenchView` 及 `workbench_*_workflow`、`MonitorPanel`、
  `GroupingPanel`、`NamingPanel`、`SpecimenSidebar`。
- 核心服务：`monitor_service`、`media_discovery_service`、`photo_import_service`、
  `grouping_service`。
- 主要数据：`tasks`、`grouping`、`seen_files`、`photos`、`photo_files`、
  `photo_assignments`。
- 边界：工作台只编排流程；文件安全规则由影像成果模块负责。选中 JPG 的合成和
  合成+整理不依赖当前激活编号。

### M5 影像处理与成果资产

- 职责：Helicon 合成、TIFF 登记、整理归档、成果恢复、TIFF 转 JPG、预览缓存。
- 页面：照片工作区成果区、TIFF 转 JPG 工具。
- 核心服务：`helicon_service`、`compose_workflow_service`、
  `organize_workflow_service`、`archive_service`、`photo_asset_service`、
  `specimen_result_tif_service`、`tiff_*_service`、`supplementary_service`。
- 主要数据：`specimen_result_tif_index`、`assets`、`asset_derivations`、
  `processing_runs`。
- 边界：TIFF 母版只读且永不自动删除；内部预览 JPG 与用户主动导出的 JPG 是
  两条独立流程；删除 JPG 必须在归档完整性验证之后。

### M6 数据查询、汇总与报告

- 职责：跨工作区查询、字段筛选、项目 KPI、编号表、成片关联、CSV/Excel/报告。
- 页面：项目树的数据汇总、项目汇总、调查概览。
- 核心服务：`cross_workspace_query_service`、`project_summary_service`、
  `survey_overview_service`、`global_results_service`、`export_service`。
- 主要数据：读取各工作区事实表；输出记录进入 `report_runs`，质量问题进入
  `qc_findings`。
- 边界：这是读模型和输出模块，不修改母版 TIFF；成片路径来自数据库索引，
  不能要求用户手填路径。

### M7 分类学与物种名录

- 职责：内置分类库、WoRMS 查询/验证、分类学习、物种名录聚合。
- 页面：内置分类库、WoRMS 分类库、项目树物种名录。
- 核心服务：`taxonomy_service`、`worms_service`、`taxon_inventory_service`。
- 边界：`名录` 是分类学物种清单，不是处理进度表；未鉴定材料进入处理汇总，
  不伪装成物种名录行。

### M8 标签设计与打印

- 职责：标签模板、自由设计器、纸张拼版、打印批次、NIIMBOT/RNA 标签队列。
- 页面：标签打印；工作台中的 RNA 快捷打印。
- 核心服务：`label_service`、`label_design_schema`、`label_print_batch`、
  `label_print_executor`、`niimbot_print_service`、`rna_label_queue_service`。
- 主要数据：模板配置、`label_print_events`。
- 边界：标签渲染数学位于 Qt-free 的 `utils/label_*`；打印设备适配不进入模板规则。

### M9 协作与同步

- 职责：局域网发现、任务状态、项目配对、标本/文件同步、重试、对等端信任，
  以及可选的远程协作适配。
- 页面：协作页、工作台协作抽屉、设置中的协作配置。
- 核心服务：`collab_*`、`remote_collab_service`。
- 主要数据：`devices`、协作任务、共享登记和同步状态。
- 边界：团队永久码与项目共享码是两个流程；LAN P2P 与远程中继是两个适配器；
  协作模块同步项目/标本/影像事实，但不重新定义这些事实。

## 二、基础模块

### M10 应用外壳与配置

- 职责：进程启动、主窗口、导航注册、`AppContext`、主题、图标、国际化、设置页。
- 主要位置：`main.py`、`app/main_window.py`、`app/app_context.py`、
  `app/config/`、`app/views/registry.py`、`settings_*`。
- 边界：外壳负责装配和导航，不承载项目、标本或文件业务规则。

### M11 数据持久化与运行基础设施

- 职责：SQLite schema/迁移、模型、公共工具、缓存、后台 worker、资源限制。
- 主要位置：`app/db/`、`app/models/`、`app/utils/`、`app/workers/`。
- 边界：worker 只执行由 service 规划的任务；数据库迁移向前兼容旧项目库。

### M12 维护、更新与交付

- 职责：备份、审计、导入导出入口、截图、软件更新、版本、Windows 构建与发布。
- 核心位置：`backup_service`、`activity_audit_service`、`update_service`、
  `screenshot_service`、`app/config/version.py`、`scripts/build_windows.ps1`、
  `.github/workflows/`。
- 边界：构建包不携带用户项目清单、缓存、密钥或本机路径；更新包必须校验版本和签名。

## 三、依赖方向

```text
应用外壳 -> 各业务页面 -> 业务服务 -> 数据库/文件适配
                         -> 后台任务（执行服务给出的计划）

协作适配 -> 项目/标本/影像的公开服务接口
汇总报告 -> 项目/标本/影像/分类的只读查询接口
```

禁止反向依赖：service 不导入 QWidget；业务模块不导入具体页面；页面之间不互相
import；worker 不自行决定删除或归档规则。

## 四、当前整理优先级

1. 先迁移 Qt-free 的叶子 service，旧路径保留兼容入口。
2. 再把项目、标本、分类、标签、协作 service 逐个归入子域。
3. 工作台只拆已经形成独立 workflow/panel 的代码，不一次性移动整个大页面。
4. 项目树先抽查询和展示组件，不重写编号、名录或 TIFF 路径规则。
5. 协作网络测试与普通 Qt 全量测试隔离，避免 zeroconf/uvicorn 原生线程污染。

每次只迁移一个模块，先跑对应测试，再跑核心回归；版本发布前运行隔离后的完整测试矩阵。
