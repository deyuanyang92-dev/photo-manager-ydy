-- W0 schema — SQLite project database
-- Mirrors db-utils.js ensureSchema + W0 spec extensions (raw_json兜底 + grouping扩列)

CREATE TABLE IF NOT EXISTS specimens (
  uid TEXT PRIMARY KEY,
  id TEXT, province TEXT, site TEXT, station TEXT,
  storage TEXT, collection_date TEXT, photo_date TEXT,
  scientific_name TEXT, scientific_name_cn TEXT,
  taxon_group TEXT, taxon_group_cn TEXT,
  order_name TEXT, order_cn TEXT,
  family TEXT, family_cn TEXT, genus TEXT, genus_cn TEXT,
  lon REAL, lat REAL, geo_area TEXT,
  collector TEXT, photographer TEXT, identifier TEXT,
  notes TEXT, photo_notes TEXT, angle TEXT,
  metadata INTEGER DEFAULT 0,
  pinned INTEGER DEFAULT 0,
  owner_project_dir TEXT,
  raw_json TEXT            -- 完整原始 specimen 对象（兜底，零字段丢失）
  -- 注意：无 species/species_cn 列；中文名在 scientific_name_cn；俗名在 raw_json
);

CREATE TABLE IF NOT EXISTS tasks (
  uid TEXT PRIMARY KEY,
  is_active INTEGER DEFAULT 0,
  activated_at TEXT,
  last_organized_at TEXT,
  next_result_sequence_hint INTEGER,
  raw_json TEXT            -- 兜底：协作字段全在这里
);

CREATE TABLE IF NOT EXISTS grouping (
  uid TEXT, group_index INTEGER,
  angle_label TEXT, jpg_paths TEXT, composed_tiff_path TEXT,
  status TEXT, source TEXT, created_at TEXT, updated_at TEXT,
  result_sequence INTEGER, archive_zip TEXT, retired_tiff_paths TEXT,
  raw_json TEXT,           -- 兜底
  PRIMARY KEY (uid, group_index)
);

CREATE TABLE IF NOT EXISTS seen_files (
  name TEXT PRIMARY KEY,   -- 按文件名（非路径），对齐 monitor-service.js firstSeenAt
  first_seen_at TEXT
);

CREATE TABLE IF NOT EXISTS _import_manifest (
  source_file TEXT PRIMARY KEY,  -- 如 user_specimens.json
  sha256 TEXT, row_count INTEGER, imported_at TEXT
);

CREATE TABLE IF NOT EXISTS project_settings (
  setting_key TEXT PRIMARY KEY,
  value_json  TEXT NOT NULL DEFAULT '{}'
);

-- 根项目 catalog：用于管理一个调查项目下的子项目/断面工作区。
-- 同一份 schema 同时适用于根项目库和工作区库；根项目库主要使用
-- survey_project/workspaces/workspace_index_cache/report_runs，工作区库主要
-- 使用 workspace_meta/specimens/grouping/collection_records 等事实表。
CREATE TABLE IF NOT EXISTS survey_project (
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

CREATE TABLE IF NOT EXISTS workspaces (
  workspace_id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL,
  parent_workspace_id TEXT,
  role TEXT,
  name TEXT NOT NULL,
  relative_path TEXT NOT NULL,
  display_order INTEGER,
  active INTEGER DEFAULT 1,
  last_seen_at TEXT,
  last_indexed_at TEXT,
  raw_json TEXT,
  UNIQUE(project_id, relative_path)
);

CREATE TABLE IF NOT EXISTS workspace_index_cache (
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

CREATE TABLE IF NOT EXISTS report_runs (
  report_id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL,
  report_type TEXT NOT NULL,
  scope_json TEXT NOT NULL,
  output_path TEXT,
  generated_at TEXT,
  status TEXT,
  raw_json TEXT
);

CREATE TABLE IF NOT EXISTS workspace_meta (
  workspace_id TEXT PRIMARY KEY,
  project_id TEXT,
  role TEXT,
  display_name TEXT,
  root_project_hint TEXT,
  created_at TEXT,
  updated_at TEXT,
  raw_json TEXT
);

CREATE TABLE IF NOT EXISTS devices (
  device_id TEXT PRIMARY KEY,
  device_name TEXT,
  owner TEXT,
  public_key TEXT,
  trust_status TEXT DEFAULT 'trusted',
  created_at TEXT,
  last_seen_at TEXT,
  raw_json TEXT
);

CREATE TABLE IF NOT EXISTS uid_sequences (
  sequence_id TEXT PRIMARY KEY,
  project_code TEXT DEFAULT '',
  prefix TEXT NOT NULL,
  scope TEXT DEFAULT 'workspace',
  next_number INTEGER NOT NULL DEFAULT 1,
  padding INTEGER NOT NULL DEFAULT 3,
  updated_at TEXT,
  raw_json TEXT,
  UNIQUE(project_code, prefix, scope)
);

CREATE TABLE IF NOT EXISTS uid_reservations (
  reservation_id TEXT PRIMARY KEY,
  sequence_id TEXT NOT NULL,
  device_id TEXT,
  start_number INTEGER NOT NULL,
  end_number INTEGER NOT NULL,
  next_unused_number INTEGER NOT NULL,
  status TEXT DEFAULT 'active',
  created_at TEXT,
  updated_at TEXT,
  raw_json TEXT
);

CREATE TABLE IF NOT EXISTS photos (
  photo_id TEXT PRIMARY KEY,
  workspace_id TEXT,
  content_hash TEXT,
  perceptual_hash TEXT,
  original_filename TEXT,
  first_seen_at TEXT,
  capture_datetime TEXT,
  photo_kind TEXT DEFAULT 'original',
  lifecycle_status TEXT DEFAULT 'incoming',
  current_assignment_id TEXT,
  primary_file_id TEXT,
  metadata_id TEXT,
  notes TEXT,
  created_at TEXT,
  updated_at TEXT,
  raw_json TEXT
);

CREATE TABLE IF NOT EXISTS photo_files (
  file_id TEXT PRIMARY KEY,
  photo_id TEXT NOT NULL,
  relative_path TEXT NOT NULL,
  storage_role TEXT DEFAULT 'incoming',
  size_bytes INTEGER,
  mtime TEXT,
  sha256 TEXT,
  width INTEGER,
  height INTEGER,
  exists_on_disk INTEGER DEFAULT 1,
  first_seen_at TEXT,
  last_seen_at TEXT,
  raw_json TEXT,
  UNIQUE(relative_path)
);

CREATE TABLE IF NOT EXISTS photo_metadata (
  metadata_id TEXT PRIMARY KEY,
  photo_id TEXT NOT NULL,
  camera_make TEXT,
  camera_model TEXT,
  lens_model TEXT,
  exposure_time TEXT,
  f_number TEXT,
  iso TEXT,
  focal_length TEXT,
  exif_datetime TEXT,
  gps_lat REAL,
  gps_lon REAL,
  image_width INTEGER,
  image_height INTEGER,
  raw_exif_json TEXT,
  created_at TEXT,
  updated_at TEXT
);

CREATE TABLE IF NOT EXISTS photo_assignments (
  assignment_id TEXT PRIMARY KEY,
  photo_id TEXT NOT NULL,
  specimen_uid TEXT,
  collection_event_id TEXT,
  assigned_by TEXT,
  assigned_at TEXT,
  assignment_source TEXT,
  confidence REAL,
  is_current INTEGER DEFAULT 1,
  revoked_at TEXT,
  revoke_reason TEXT,
  raw_json TEXT
);

CREATE TABLE IF NOT EXISTS assets (
  asset_id TEXT PRIMARY KEY,
  workspace_id TEXT,
  specimen_uid TEXT,
  asset_type TEXT,
  relative_path TEXT NOT NULL,
  content_hash TEXT,
  lifecycle_status TEXT DEFAULT 'present',
  created_at TEXT,
  created_by TEXT,
  software_name TEXT,
  software_version TEXT,
  processing_run_id TEXT,
  raw_json TEXT,
  UNIQUE(workspace_id, relative_path)
);

CREATE TABLE IF NOT EXISTS asset_derivations (
  asset_id TEXT NOT NULL,
  source_photo_id TEXT,
  source_file_id TEXT,
  role TEXT DEFAULT 'source',
  raw_json TEXT
);

CREATE TABLE IF NOT EXISTS processing_runs (
  run_id TEXT PRIMARY KEY,
  workspace_id TEXT,
  run_type TEXT,
  started_at TEXT,
  finished_at TEXT,
  operator TEXT,
  tool_name TEXT,
  tool_version TEXT,
  parameters_json TEXT,
  status TEXT,
  raw_json TEXT
);

CREATE TABLE IF NOT EXISTS audit_log (
  audit_id TEXT PRIMARY KEY,
  workspace_id TEXT,
  actor TEXT,
  action TEXT,
  entity_type TEXT,
  entity_id TEXT,
  old_value_json TEXT,
  new_value_json TEXT,
  created_at TEXT
);

CREATE TABLE IF NOT EXISTS label_print_events (
  event_id TEXT PRIMARY KEY,
  workspace_id TEXT,
  actor TEXT,
  bucket TEXT,
  template_key TEXT,
  printer_name TEXT,
  specimen_uids_json TEXT NOT NULL DEFAULT '[]',
  copies INTEGER DEFAULT 1,
  label_count INTEGER DEFAULT 0,
  status TEXT DEFAULT 'printed',
  created_at TEXT,
  raw_json TEXT
);

CREATE TABLE IF NOT EXISTS qc_findings (
  finding_id TEXT PRIMARY KEY,
  workspace_id TEXT,
  severity TEXT,
  finding_type TEXT,
  entity_type TEXT,
  entity_id TEXT,
  message TEXT,
  status TEXT DEFAULT 'open',
  created_at TEXT,
  resolved_at TEXT,
  raw_json TEXT
);

-- 采集记录簿（野外采集记录 / field collection log）
-- 每条记录由 (province, site, station, collection_date) 唯一确定，对齐 UID 地点段。
-- 拍照时按 4 键 lookup → 自动填充工作台能用上的字段子集；其余字段（生境/潮水等）
-- 只存于此，导出时按 4 键 join。raw_json 兜底，零字段丢失 + 扩展字段。
CREATE TABLE IF NOT EXISTS collection_records (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  province        TEXT,
  site            TEXT,
  station         TEXT,
  collection_date TEXT,
  station_label   TEXT,            -- 站位中文说明
  zone            TEXT,            -- 采区：'intertidal'(潮间带 H.39) / 'subtidal'(潮下带 H.30) / NULL(历史未分)
  lon             REAL,
  lat             REAL,
  geo_area        TEXT,            -- 采集地理区
  water_body      TEXT,            -- 海区 / 水体（DwC waterBody，如 东海·三门湾）
  cruise          TEXT,            -- 航次（潮下带船基，如 2026春季三门湾航次）
  vessel          TEXT,            -- 船号 / 船名（潮下带，如 科学三号）
  habitat         TEXT,            -- 生境 / 底质（泥滩/沙滩/岩相…）
  tidal_zone      TEXT,            -- 潮区（高潮区/中潮区/低潮区）— 潮间带分带
  depth           TEXT,            -- 水深 m（潮下带）
  tide            TEXT,            -- 潮位 / 潮时 / 大小潮
  salinity        TEXT,            -- 盐度（选填）
  water_temp      TEXT,            -- 水温·表层（选填）
  bottom_temp     TEXT,            -- 底层水温（选填）
  dissolved_oxygen TEXT,           -- 溶解氧 DO（选填）
  ph              TEXT,            -- pH（选填）
  weather         TEXT,            -- 天气（选填）
  sample_type     TEXT,            -- 采集性质：定量/半定量/定性（DwC 定量↔sampleSize 主轴）
  sampler_model   TEXT,            -- 采泥器型号（大洋50型/Van Veen/箱式…）—型号，区别于规格
  sampler_spec    TEXT,            -- 采样器规格 / 尺寸（25×25cm框 / 0.1m²采泥器…）
  sample_area     TEXT,            -- 取样面积 m²（标准化 个体数·m⁻² 的关键）
  replicates      TEXT,            -- 取样次数 / 重复数
  sieve_mesh      TEXT,            -- 网筛孔径 mm（大型底栖常 1.0）
  sample_no       TEXT,            -- 样品编号（现场样品袋编号，DwC recordNumber）
  -- 潮间带专属（H.39 潮间带生物野外采集记录表）
  quadrate_no     TEXT,            -- 样方号（每站多样方，如 B2-Q3）
  air_temp        TEXT,            -- 气温 ℃（H.39 三温并列：气温/水温/底温）
  quant_bottles   TEXT,            -- 定量标本瓶数（现场分装）
  qual_bottles    TEXT,            -- 定性标本瓶数（现场分装）
  -- 两带通用
  sample_thickness TEXT,           -- 样品厚度 cm（H.30 采泥厚度 / H.39 样品厚度）
  -- 潮下带专属（H.30 大型底栖生物海上采样记录表）
  wire_out        TEXT,            -- 放绳长度 m（船基水深订正）
  sampler_area    TEXT,            -- 采泥器面积 m²（定量换算关键；sampler_spec 保留为自由文字规格）
  net_type        TEXT,            -- 网型（拖网，如 阿氏网/双刃拖网）
  net_width       TEXT,            -- 网宽 m（拖网）
  trawl_distance  TEXT,            -- 拖网距离 m
  trawl_start     TEXT,            -- 拖网起始时刻
  trawl_end       TEXT,            -- 拖网结束时刻
  grab_sample_total TEXT,          -- 采泥样品总数
  trawl_sample_total TEXT,         -- 拖网样品总数
  collector       TEXT,
  recorder        TEXT,            -- 记录人（填表人，责任链）
  checker         TEXT,            -- 核对人（复核人，责任链）
  photographer    TEXT,
  identifier      TEXT,
  collection_time TEXT,            -- 采集时刻（选填）
  photo_date      TEXT,
  photo_location  TEXT,            -- 拍摄地点
  method          TEXT,            -- 采样方法（定量框/采泥器/拖网/手拣定性）
  remark          TEXT,
  raw_json        TEXT             -- 兜底：零字段丢失 + 扩展字段
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_collection_records_unique_event
ON collection_records(province, site, COALESCE(station, ''), collection_date);
