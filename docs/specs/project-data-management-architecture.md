# 项目化数据管理架构设计

本文记录下一版数据管理架构。目标不是把用户的文件夹强行改成软件想要的样子，而是让软件能管理真实的调查项目：一个调查根项目下面有多个断面/区域/航次子目录，每个子目录可独立拍照，也能被汇总、移动、重命名、整理和出报告。

## 1. 调研依据

本设计基于当前代码和测试，不是从零空想：

- `app/services/project_tree_service.py`：项目树是只读扫描；任意深度子目录都可成为工作区；`_data`、`incoming-jpg`、`results` 等内部目录不展示为业务节点。
- `app/views/project_tree_view.py`：进入任意树节点时，会通过 `enter_workspace()` 建立工作区，并把根目录写入 `ctx.current_project_root`。
- `app/services/project_settings_service.py` 与 `tests/test_project_settings_effective.py`：项目设置已经支持沿目录树继承，且不会为了读取设置而创建数据库。
- `app/db/schema.sql`：当前每个工作区有自己的 `_data/project.db`，核心表为 `specimens`、`tasks`、`grouping`、`seen_files`、`collection_records`、`project_settings`。
- `app/services/collection_record_service.py`：采集记录当前按 `(province, site, station, collection_date)` 唯一定位，并为工作台自动填充经纬度、人员、采集地等字段。
- `app/services/coord_import_service.py` 与 `collection_record_io.py`：已经支持把站位表、采集记录从 Excel/CSV/TXT 导入，坐标可统一到 WGS84。
- `app/services/monitor_service.py`：JPG 归属依赖 `firstSeenAt`，不能用 mtime 替代。
- `app/services/grouping_service.py`：当前照片分组以 `grouping.jpg_paths` JSON 记录路径，这是后续需要加深的数据点。
- `app/views/summary_view.py` 与 `app/services/export_service.py`：当前汇总和导出主要从 `specimens` 与 `grouping` 拼出 Excel/CSV/Darwin Core。
- `CLAUDE.md` 与 `docs/adr/0001-pyqt6-over-electron.md`：已接受的方向是 PyQt6、项目内 SQLite、路径安全、只读导入、JPG 归属以 `firstSeenAt` 为准。

## 2. 业务模型

以“厦门海域项目”为例：

```text
厦门海域项目
  ├── 01_同安湾断面
  │   ├── 站位 A01/A02/A03
  │   ├── 采集记录
  │   ├── 拍照 JPG
  │   └── 合成成果 TIFF/ZIP/JXL
  ├── 02_五缘湾断面
  ├── 03_鼓浪屿断面
  ├── 04_翔安断面
  └── 05_海沧湾断面
```

业务上要区分这些概念：

- **调查项目**：一次完整调查，例如“厦门海域项目”。负责项目级元数据、人员、统一报告、跨断面汇总。
- **工作区**：一个可进入拍照的目录，通常是一个断面，也可以是某个更细的区域或航次。
- **断面/区域/航次节点**：项目树上的业务子目录。它可以只是分类容器，也可以是工作区。
- **站位**：空间点，例如 A01。站位有坐标、说明、坐标来源、坐标精度。
- **采集事件**：某个日期/时间在某站位发生的一次采集。生境、潮水、天气、采集人等属于采集事件。
- **标本**：拍照和后续分类的对象。标本引用采集事件，必要时保存一份经纬度快照以保证导出历史稳定。
- **原始照片**：相机进来的 JPG，是核心资产。它应该有独立记录，而不是只靠文件夹扫描临时推断。
- **照片分组**：若干原始照片构成一个角度/批次，用于合成。
- **成果文件**：TIFF、ZIP、JXL、报告图等从照片或数据生成的资产。
- **报告/Excel**：输出物，不是主数据库。用户可以导入 Excel，但软件内部应以 SQLite 为事实源。

## 3. 推荐目录布局

目录布局应兼容现有实现，即每个工作区仍可以自带 `_data/project.db`。新增的根项目数据库只做项目级登记和汇总缓存，不替代子工作区数据库。

```text
厦门海域项目/
  _data/
    project.db                   # 根项目数据库：项目元数据、工作区登记、报告索引
    imports/
      stations_20260610.xlsx     # 原始导入留痕，可选
      collection_log_20260610.xlsx
    exports/
      厦门海域项目_标本汇总.xlsx
      厦门海域项目_采集事件汇总.xlsx
      厦门海域项目_照片资产清单.xlsx
      厦门海域项目_质控报告.html

  01_同安湾断面/
    _data/
      project.db                 # 工作区数据库：本站位/标本/照片/成果事实
    incoming-jpg/
    results/
    reports/

  02_五缘湾断面/
    _data/project.db
    incoming-jpg/
    results/

  03_鼓浪屿断面/
  04_翔安断面/
  05_海沧湾断面/
```

原则：

- 根项目可以有 `_data/project.db`，但通常不直接拍照。
- 每个断面工作区自包含：复制或移动一个断面文件夹时，照片、成果和本断面数据库一起走。
- 根数据库保存“有哪些断面工作区、它们现在在哪里、最后一次索引结果是什么”。
- 路径以相对路径为主，绝对路径只作为运行时解析结果，不作为长期身份。

## 4. 身份与路径规则

当前系统大量使用路径和 UID。下一版应补稳定 ID：

| 概念 | 稳定 ID | 说明 |
| --- | --- | --- |
| 调查项目 | `project_id` | 根项目唯一 ID，存根库 |
| 工作区/断面 | `workspace_id` | 存在每个工作区自己的 DB 中，也登记到根库 |
| 站位 | `station_id` | 站位稳定身份，站位代码可改 |
| 采集事件 | `event_id` | 一次采集的稳定身份 |
| 标本 | `uid` + 内部 row id | UID 继续兼容现有命名规则 |
| 原始照片 | `photo_id` | 不能只用文件名或路径 |
| 照片分组 | `group_id` | 一组 JPG 的稳定身份 |
| 成果文件 | `asset_id` | TIFF/ZIP/JXL/报告图等 |

路径规则：

- 数据库长期保存 `relative_path`，例如 `incoming-jpg/DSC00123.JPG`。
- 跨项目登记保存 `workspace_relative_path`，例如 `01_同安湾断面`。
- 绝对路径只在打开项目时由根目录拼出。
- 子目录被重命名或移动时，软件通过 `workspace_id` 识别它不是新断面。

## 5. 根项目数据库

根项目数据库仍叫 `_data/project.db`，但它的职责是项目级 catalog。

建议表：

```sql
CREATE TABLE survey_project (
  project_id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  code TEXT,
  root_relative_path TEXT DEFAULT '.',
  location TEXT,
  date_range TEXT,
  created_at TEXT,
  updated_at TEXT,
  raw_json TEXT
);

CREATE TABLE workspaces (
  workspace_id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL,
  parent_workspace_id TEXT,
  role TEXT,                 -- transect / area / voyage / station_group / workspace
  name TEXT NOT NULL,
  relative_path TEXT NOT NULL,
  display_order INTEGER,
  active INTEGER DEFAULT 1,
  last_seen_at TEXT,
  last_indexed_at TEXT,
  raw_json TEXT
);

CREATE TABLE workspace_index_cache (
  workspace_id TEXT PRIMARY KEY,
  specimen_count INTEGER DEFAULT 0,
  station_count INTEGER DEFAULT 0,
  event_count INTEGER DEFAULT 0,
  photo_count INTEGER DEFAULT 0,
  unassigned_photo_count INTEGER DEFAULT 0,
  result_count INTEGER DEFAULT 0,
  missing_coord_count INTEGER DEFAULT 0,
  taxonomy_incomplete_count INTEGER DEFAULT 0,
  updated_at TEXT
);

CREATE TABLE report_runs (
  report_id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL,
  report_type TEXT NOT NULL,     -- specimen_summary / collection_summary / photo_inventory / qc / map
  scope_json TEXT NOT NULL,
  output_path TEXT,
  generated_at TEXT,
  status TEXT,
  raw_json TEXT
);
```

根项目数据库可以继续使用现有 `project_settings`。项目级人员、项目编号、地图样式、导出字段方案都应存在根库，断面库通过继承读取。

## 6. 工作区数据库

工作区数据库是一个断面/区域内的事实源。现有表不删除，先加表和外键，后续逐步迁移。

### 6.1 工作区元信息

```sql
CREATE TABLE workspace_meta (
  workspace_id TEXT PRIMARY KEY,
  project_id TEXT,
  role TEXT,
  display_name TEXT,
  root_project_hint TEXT,
  created_at TEXT,
  updated_at TEXT,
  raw_json TEXT
);
```

### 6.2 站位

```sql
CREATE TABLE stations (
  station_id TEXT PRIMARY KEY,
  workspace_id TEXT,
  province TEXT,
  site TEXT,
  station TEXT,
  station_label TEXT,
  lon_wgs84 REAL,
  lat_wgs84 REAL,
  original_lon TEXT,
  original_lat TEXT,
  original_crs TEXT,          -- WGS84 / GCJ02 / BD09 / unknown
  accuracy_m REAL,
  coordinate_source TEXT,     -- manual / excel / geocode / gps / map_pick
  active INTEGER DEFAULT 1,
  updated_at TEXT,
  raw_json TEXT,
  UNIQUE(workspace_id, province, site, station)
);
```

设计要点：

- 经纬度首先属于站位或采集事件，不应只属于标本。
- 导入 GCJ02/BD09 坐标后，统一保存 WGS84，同时保留原始坐标和坐标系。
- 站位代码可修改，但 `station_id` 不变。

### 6.3 采集事件

```sql
CREATE TABLE collection_events (
  event_id TEXT PRIMARY KEY,
  workspace_id TEXT,
  station_id TEXT,
  event_code TEXT,
  collection_date TEXT,
  collection_time TEXT,
  habitat TEXT,
  tide TEXT,
  salinity TEXT,
  water_temp TEXT,
  weather TEXT,
  collector TEXT,
  photographer TEXT,
  identifier TEXT,
  method TEXT,
  remark TEXT,
  raw_json TEXT,
  UNIQUE(workspace_id, station_id, collection_date, collection_time)
);
```

现有 `collection_records` 可视为 `stations + collection_events` 的合并旧表。迁移时：

- 用 `(province, site, station)` 生成或匹配 `stations`。
- 用 `(station_id, collection_date, collection_time)` 生成 `collection_events`。
- 原始字段完整放入 `raw_json`，不丢 Excel 中的额外列。

### 6.4 标本

保留现有 `specimens`，新增：

```sql
ALTER TABLE specimens ADD COLUMN collection_event_id TEXT;
ALTER TABLE specimens ADD COLUMN station_id TEXT;
ALTER TABLE specimens ADD COLUMN workspace_id TEXT;
```

规则：

- `uid` 继续按现有规则生成，兼容文件名、标签和旧数据。
- `collection_event_id` 是标本连接野外采集记录的主关系。
- `specimens.lon/lat/province/site/station` 保留为导出快照和兼容字段。
- 如果后期校正站位坐标，软件应提示是否同步更新相关标本快照。

### 6.5 原始照片

这是最优先补的表。

```sql
CREATE TABLE photos (
  photo_id TEXT PRIMARY KEY,
  workspace_id TEXT,
  specimen_uid TEXT,
  collection_event_id TEXT,
  relative_path TEXT NOT NULL,
  original_name TEXT,
  size_bytes INTEGER,
  mtime TEXT,
  first_seen_at TEXT,
  sha256 TEXT,
  exif_datetime TEXT,
  camera_model TEXT,
  status TEXT DEFAULT 'incoming',
  attribution_source TEXT,    -- grouping / manual / activation / imported / none
  missing_on_disk INTEGER DEFAULT 0,
  raw_json TEXT,
  UNIQUE(workspace_id, relative_path)
);
```

为什么必须有 `photos`：

- 当前 `seen_files` 只按文件名保存 firstSeenAt，不能完整表达照片状态。
- 当前 `grouping.jpg_paths` 把路径 JSON 塞在分组表中，路径移动后弱。
- 报告需要回答“未归属照片、已分组照片、已合成照片、已归档照片、磁盘丢失照片”。
- 后续整理子目录时，需要通过 `photo_id` 和相对路径重新定位。

`seen_files` 可在迁移期保留，之后由 `photos.first_seen_at` 接管。

### 6.6 照片分组

```sql
CREATE TABLE photo_groups (
  group_id TEXT PRIMARY KEY,
  workspace_id TEXT,
  specimen_uid TEXT NOT NULL,
  group_index INTEGER,
  angle_label TEXT,
  status TEXT,
  source TEXT,
  created_at TEXT,
  updated_at TEXT,
  raw_json TEXT
);

CREATE TABLE photo_group_items (
  group_id TEXT NOT NULL,
  photo_id TEXT NOT NULL,
  sort_order INTEGER DEFAULT 0,
  PRIMARY KEY(group_id, photo_id)
);
```

迁移期可继续写 `grouping`，但新代码应以 `photo_groups/photo_group_items` 为事实源，再生成兼容视图给旧 UI。

### 6.7 成果文件

```sql
CREATE TABLE result_assets (
  asset_id TEXT PRIMARY KEY,
  workspace_id TEXT,
  specimen_uid TEXT,
  group_id TEXT,
  kind TEXT,                 -- tiff / zip / jxl / report_image / pdf
  relative_path TEXT NOT NULL,
  size_bytes INTEGER,
  sha256 TEXT,
  result_sequence INTEGER,
  compose_engine TEXT,
  compose_params_json TEXT,
  archive_status TEXT,
  created_at TEXT,
  missing_on_disk INTEGER DEFAULT 0,
  raw_json TEXT,
  UNIQUE(workspace_id, relative_path)
);
```

这张表让“成果数、成果状态、归档状态、缺失成果”不再通过扫描文件名临时推断。

### 6.8 审计事件

```sql
CREATE TABLE audit_events (
  audit_id TEXT PRIMARY KEY,
  workspace_id TEXT,
  entity_type TEXT,          -- station / event / specimen / photo / group / asset / path
  entity_id TEXT,
  action TEXT,
  before_json TEXT,
  after_json TEXT,
  actor TEXT,
  created_at TEXT
);
```

必须审计：

- 站位坐标修改。
- 标本 UID 重命名。
- 照片重新归属。
- 子目录移动/重命名。
- JPG 归档或删除。
- Excel 批量导入覆盖已有记录。

## 7. Excel 导入和导出

Excel 是入口和出口，不是长期事实源。

### 7.1 站位规划表

用于项目开始前批量建立站位：

| 字段 | 说明 |
| --- | --- |
| 项目编号 | 如 XMSEA2026 |
| 断面编号 | 如 T01 |
| 断面名称 | 如 同安湾断面 |
| 站位编号 | 如 A01 |
| 站位说明 | 中文说明 |
| 经度 | 原始经度 |
| 纬度 | 原始纬度 |
| 坐标系 | WGS84/GCJ02/BD09 |
| 坐标来源 | GPS/地图/文献/人工 |
| 备注 | 任意补充 |

导入后写入 `stations`。如果同一 `workspace_id + station` 已存在，进入“更新预览”，不能无提示覆盖。

### 7.2 采集记录表

用于野外记录：

| 字段 | 说明 |
| --- | --- |
| 断面编号 | 匹配工作区 |
| 站位编号 | 匹配站位 |
| 采集日期 | 必填 |
| 采集时间 | 可选，但建议填 |
| 生境 | 泥滩/岩礁/养殖区等 |
| 潮水 | 高潮/低潮/涨潮/退潮 |
| 天气 | 可选 |
| 采集人 | 可继承默认 |
| 拍摄人 | 可继承默认 |
| 鉴定人 | 可继承默认 |
| 方法 | 拖网/手采/潜水等 |
| 备注 | 任意补充 |

导入后写入 `collection_events`。工作台创建标本时，通过 `event_id` 自动填充。

### 7.3 标本汇总表

从 `v_specimen_export` 导出，包含：

- 项目/断面/站位/采集事件。
- 标本 UID、物种编号、保存方式、日期段。
- 分类字段。
- 经纬度快照。
- 采集人/拍摄人/鉴定人。
- 成果数、成果状态、照片数。
- 质控状态。

### 7.4 照片资产清单

从 `photos + photo_groups + result_assets` 导出，包含：

- 原始 JPG 路径、首次发现时间、EXIF 时间、大小、hash。
- 归属标本。
- 分组状态。
- 是否已合成。
- 是否已归档。
- 文件是否丢失。

### 7.5 质控报告

建议生成 HTML 和 Excel 双格式，报告内容：

- 缺经纬度站位。
- 坐标超范围或坐标系未声明。
- 有照片但未归属标本。
- 有标本但无照片。
- 有分组但无成果。
- 有成果文件但数据库无记录。
- 分类信息不完整。
- 同一 UID 冲突。
- 子目录存在但未登记为工作区。

## 8. 项目树规整

用户可能会改文件夹结构，例如：

```text
厦门海域项目/
  同安湾/
  五缘湾/
```

整理成：

```text
厦门海域项目/
  01_同安湾断面/
  02_五缘湾断面/
```

软件应提供“规整/重命名/认领”流程，而不是让用户手工改完后数据断裂。

### 8.1 扫描认领

扫描根目录时：

1. 跳过 `_data`、`incoming-jpg`、`results` 等内部目录。
2. 发现含 `_data/project.db` 的目录，读取其 `workspace_id`。
3. 如果根库已有该 `workspace_id`，更新 `relative_path`。
4. 如果根库没有该 `workspace_id`，提示“发现未登记工作区，是否认领”。
5. 如果同一 `workspace_id` 出现两次，进入冲突处理。

### 8.2 安全移动/重命名

移动一个断面目录时：

1. 先检查目标路径不在另一个工作区内部目录中。
2. 移动文件夹。
3. 更新根库 `workspaces.relative_path`。
4. 工作区内部照片和成果路径保持相对路径，不需要批量改绝对路径。
5. 写入 `audit_events`。

### 8.3 合并/拆分断面

合并断面比重命名危险，不能自动静默执行。需要：

- 显示两个工作区的站位、采集事件、标本、照片、成果数量。
- 检查 UID、站位代码、照片路径是否冲突。
- 用户确认冲突解决方案。
- 写入审计记录。

拆分断面也一样，需要明确哪些 `station_id/event_id/specimen_uid/photo_id/asset_id` 移入新工作区。

## 9. 推荐查询视图

为了让 UI、Excel、报告不再各自拼业务规则，建议提供统一视图：

```sql
CREATE VIEW v_station_map_points AS
SELECT
  s.station_id,
  s.workspace_id,
  s.province,
  s.site,
  s.station,
  s.station_label,
  s.lon_wgs84 AS lon,
  s.lat_wgs84 AS lat,
  COUNT(e.event_id) AS event_count
FROM stations s
LEFT JOIN collection_events e ON e.station_id = s.station_id
GROUP BY s.station_id;
```

```sql
CREATE VIEW v_specimen_export AS
SELECT
  sp.uid,
  sp.workspace_id,
  sp.collection_event_id,
  sp.id,
  sp.scientific_name,
  sp.scientific_name_cn,
  sp.family,
  sp.genus,
  sp.storage,
  sp.collection_date,
  sp.photo_date,
  sp.lon,
  sp.lat,
  sp.collector,
  sp.photographer,
  sp.identifier,
  COUNT(DISTINCT p.photo_id) AS photo_count,
  COUNT(DISTINCT ra.asset_id) AS result_count
FROM specimens sp
LEFT JOIN photos p ON p.specimen_uid = sp.uid
LEFT JOIN result_assets ra ON ra.specimen_uid = sp.uid
GROUP BY sp.uid;
```

```sql
CREATE VIEW v_photo_status AS
SELECT
  p.photo_id,
  p.relative_path,
  p.specimen_uid,
  p.first_seen_at,
  p.status,
  COUNT(gi.group_id) AS group_count,
  p.missing_on_disk
FROM photos p
LEFT JOIN photo_group_items gi ON gi.photo_id = p.photo_id
GROUP BY p.photo_id;
```

## 10. 迁移路线

### M1：项目根 catalog

- 给根项目建立 `_data/project.db`。
- 加 `survey_project`、`workspaces`、`workspace_index_cache`。
- 给每个工作区加 `workspace_meta.workspace_id`。
- 项目树扫描时读取和登记 `workspace_id`。

收益：断面移动/重命名后不丢身份；根项目能汇总五个断面。

### M2：照片事实表

- 加 `photos`。
- `monitor_service.scan_project()` 扫描后 upsert `photos`。
- `seen_files` 迁移到 `photos.first_seen_at`。
- UI 仍可暂时读旧结构，但新增报告可以读 `photos`。

收益：未归属照片、已归属照片、缺失文件、归档状态都有统一来源。

### M3：照片分组和成果资产

- 加 `photo_groups`、`photo_group_items`、`result_assets`。
- 新分组写新表，同时生成旧 `grouping` 兼容行。
- 整理/合成写 `result_assets`。

收益：分组不再依赖路径 JSON；成果统计不再靠扫描文件名。

### M4：采集记录拆分为站位和事件

- 从 `collection_records` 回填 `stations` 和 `collection_events`。
- `specimens` 增加 `station_id`、`collection_event_id`。
- 工作台自动填充改为先选 `collection_event_id`，四键只作为查找入口。

收益：经纬度校正、同站位多日期采集、Excel 导入覆盖都更稳定。

### M5：项目级报告生成

- 根库扫描所有工作区，更新 `workspace_index_cache`。
- 生成项目级 Excel/HTML/PDF：
  - 标本汇总
  - 采集事件汇总
  - 站位地图数据
  - 照片资产清单
  - 质控报告

收益：用户不需要手工整理多个断面 Excel。

## 11. 实现优先级

第一优先级：`photos` 表。照片是软件核心资产，现在却没有独立事实表。

第二优先级：`workspace_id` 与根项目 `workspaces`。没有稳定工作区身份，子目录规整会依赖路径，风险高。

第三优先级：`stations + collection_events`。这会把经纬度和采集信息从“标本附属字段”提升为真正的野外数据模型。

第四优先级：项目级报告。等照片、工作区、采集事件稳定后，报告会自然变简单。

## 12. 不应做的事

- 不要让 Excel 成为主数据源。Excel 只能导入、导出和留痕。
- 不要用绝对路径作为长期身份。
- 不要只把新字段塞进 `raw_json`，然后让 UI 到处解析 JSON。
- 不要把根项目和断面工作区强行合成一个巨大数据库，否则移动单个断面和离线使用会变复杂。
- 不要删除旧表。先加新表、新视图、兼容写入，再逐步迁移 UI。

## 13. 对“厦门海域项目”的落地样例

创建项目：

```text
厦门海域项目/
```

软件写入根库：

```text
_data/project.db
  survey_project: 厦门海域项目
  workspaces: 空，等待创建断面
```

创建五个断面：

```text
01_同安湾断面/
02_五缘湾断面/
03_鼓浪屿断面/
04_翔安断面/
05_海沧湾断面/
```

每个断面第一次进入工作区时：

```text
01_同安湾断面/
  _data/project.db
  incoming-jpg/
  results/
```

导入站位表：

```text
stations:
  T01-A01  同安湾断面  118.xxx 24.xxx WGS84
  T01-A02  同安湾断面  118.xxx 24.xxx WGS84
```

野外导入采集记录：

```text
collection_events:
  event_id=...
  station=T01-A01
  collection_date=2026-06-10
  habitat=泥滩
  tide=低潮
  collector=...
```

拍照时：

```text
specimens:
  uid=FJ-XM-T01A01-DLC001-RD75E-20260610
  collection_event_id=...

photos:
  photo_id=...
  relative_path=incoming-jpg/DSC00123.JPG
  specimen_uid=FJ-XM-T01A01-DLC001-RD75E-20260610
  first_seen_at=...
```

合成后：

```text
photo_groups:
  group_id=...
  specimen_uid=...

photo_group_items:
  group_id=...
  photo_id=...

result_assets:
  kind=tiff
  relative_path=results/FJ-XM-T01A01-DLC001-1-RD75E-20260610.tif
```

项目结束时，根项目生成：

```text
_data/exports/
  厦门海域项目_标本汇总.xlsx
  厦门海域项目_采集事件汇总.xlsx
  厦门海域项目_照片资产清单.xlsx
  厦门海域项目_质控报告.html
```

这样用户可以按断面拍照、按项目汇总、按站位出图，也可以后期整理断面目录而不丢数据身份。
