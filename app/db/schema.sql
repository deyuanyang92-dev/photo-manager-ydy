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
  raw_json        TEXT,            -- 兜底：零字段丢失 + 扩展字段
  UNIQUE(province, site, station, collection_date)
);
