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
  lon             REAL,
  lat             REAL,
  geo_area        TEXT,            -- 采集地理区
  habitat         TEXT,            -- 生境 / 底质（泥滩/沙滩/岩相…）
  tide            TEXT,            -- 潮位 / 潮时
  salinity        TEXT,            -- 盐度（选填）
  water_temp      TEXT,            -- 水温（选填）
  weather         TEXT,            -- 天气（选填）
  collector       TEXT,
  photographer    TEXT,
  identifier      TEXT,
  collection_time TEXT,            -- 采集时刻（选填）
  photo_date      TEXT,
  photo_location  TEXT,            -- 拍摄地点
  method          TEXT,            -- 采集方法
  remark          TEXT,
  raw_json        TEXT,            -- 兜底：零字段丢失 + 扩展字段
  UNIQUE(province, site, station, collection_date)
);
