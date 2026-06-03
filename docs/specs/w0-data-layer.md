# W0 数据层 SPEC（Opus 出 · Sonnet 据此 TDD 实现 · 独立 Opus 验收）

> 这是 Sonnet 的**唯一输入**。不要自由读 web 源码发挥；只按本 spec + 列出的 Oracle 行实现。
> 发现 spec 矛盾/不可实现 → **停下回报，不许自己改设计**。

## 范围（W0 只做这些）
1. SQLite 项目库管理（开库/建表/缓存）。
2. uid 派生（复刻 web）。
3. 3 张核心表 + darwin_core 视图 + seen_files + _import_manifest。
4. 从现有全局 `data/*.json` **只读导入**到各项目 `_data/project.db`。
5. 导入安全：sha256 证只读 + 一致性闸。

W3/W4 模块表（worms_*/helicon_*/collab_events/user_taxonomy/free_compose_batches）**不在 W0**，各自波次建。

## Oracle（正确答案来源，逐一对照）
- 真实 schema + uid 派生 + 迁移：`/mnt/n/claude/photo-platform-ydy/prototype-photo-gui/db-utils.js`（schema 36-98、upsertSpecimen/uid 104-156、specimenDateSeg 158-165、migrateJsonToDb 235-281）。
- 数据文件结构：`/mnt/n/claude/photo-platform-ydy-v2/DATA-MODEL.md`（specimen 对象 7-48、磁盘文件 54-72）。
- 全局数据真身：`/mnt/n/claude/photo-platform-ydy/prototype-photo-gui/data/`（user_specimens.json / user_projects.json / specimen_tasks.json / grouping_confirmations.json）。

---

## 文件
- `app/db/schema.sql`
- `app/db/db_manager.py`
- `app/utils/naming.py`（W0 只需 uid 派生部分；完整 7 段解析留 W1）
- `app/services/import_service.py`
- `tests/test_db_manager.py` / `test_naming_uid.py` / `test_import_service.py`

---

## 1. schema.sql（在 db-utils.js 基础上**补 raw_json 兜底 + grouping 扩列**）

```sql
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
);
-- 注意：真实模型无 species/species_cn 列；中文名在 scientific_name_cn；俗名在 raw_json。不要新增这两列。

CREATE TABLE IF NOT EXISTS tasks (
  uid TEXT PRIMARY KEY,
  is_active INTEGER DEFAULT 0,
  activated_at TEXT,
  last_organized_at TEXT,
  next_result_sequence_hint INTEGER,
  raw_json TEXT            -- 兜底：协作字段(status/createdBy/assignedTo/role/photoIndexSummary…)全在这里
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

-- darwin_core 视图：逐字复刻 db-utils.js:75-97（occurrenceID/scientificName/family/genus/"order"/
-- decimalLongitude/decimalLatitude/eventDate/recordedBy/identifiedBy/locality(province·site·station)/"verbatimPreservation")
```

## 2. db_manager.py
```python
def open_project_db(project_dir: str) -> sqlite3.Connection
    # resolve 路径；建 _data/；connect(check_same_thread=False)；row_factory=Row
    # PRAGMA journal_mode=WAL; foreign_keys=ON; ensure_schema; 按 resolved dir 缓存
def get_db(project_dir) -> Connection      # 缓存取，未开则 open
def ensure_schema(conn) -> None            # 幂等 executescript(schema.sql)；darwin_core 用 DROP VIEW IF EXISTS 再建
def close_all() -> None                    # 测试/退出
```

## 3. naming.py（W0 子集）
```python
def specimen_date_seg(collection_date: str|None, photo_date: str|None) -> str
    # 逐字复刻 db-utils.js:158-165：
    #   c=collection 去非数字取前8；p=photo 同；
    #   not c -> p；(not p or c==p) -> c；前4位(年)相等 -> c+"-"+p[4:]；否则 c+"-"+p
def derive_uid(sp: dict) -> str
    # [province, site, station, id, storage, specimen_date_seg(...)] 去假值 join("-")
    # 注意 id 字段就是 sp["id"]（如 DLC001）；缺 station 自动降级（filter 掉空）
```

## 4. import_service.py（只读导入，最高安全）
```python
def import_all(global_data_dir: str, projects: list[dict]) -> ImportReport
    # global_data_dir = photo-platform-ydy/prototype-photo-gui/data
    # 1. 进入前：对要读的 *.json 全部 sha256 快照
    # 2. 读 user_specimens.json{specimens:[]} / specimen_tasks.json{projects:{path:{uid:task}}}
    #    / grouping_confirmations.json{projects:{path:{specimens:{uid:{groups:[]}}}}}
    # 3. 按项目分桶：每个 project_dir 开它自己的 _data/project.db
    #    - specimens：按 ownerProjectDir == project_dir 过滤；derive_uid 作 PK；raw_json=整对象
    #    - tasks：该项目桶下 {uid:task}，**uid 用 JSON 里的原始 key 原样作 PK，不经任何 parse/校验**
    #             （防中文样地 key 如「浙江-三门湾-…」被丢）；raw_json=整 task
    #    - grouping：该项目桶 specimens{uid:{groups}}；每 group 写一行；缺的扩列填 NULL；raw_json=整 group
    #    - 全部 per-row INSERT OR REPLACE（非「表非空即跳过」）
    #    - 写 _import_manifest(source_file, sha256, row_count, imported_at)
    # 4. 退出后：再 sha256 源文件，逐一与步骤1比对 → 任一变化 raise IntegrityError
    # 5. 一致性闸：每项目 specimens/tasks/grouping 行数 == 源桶条数；
    #    且抽样 raw_json 反序列化 == 源对象（深度相等）→ 不等 raise IntegrityError
    # 返回 ImportReport(per_project counts, source_sha, ok)
```

---

## 不变量（契约测试，必须各写一条）
- `test_import_does_not_mutate_source`：导入前后源 *.json 的 sha256 不变。
- `test_raw_json_roundtrip`：specimens/tasks/grouping 任取一行，`json.loads(raw_json)` 与源对象深度相等（零字段丢失）。
- `test_chinese_task_key_preserved`：tasks 桶含中文 key（`浙江-三门湾-B2-DLC001-T95E-20260601`）→ 原样入库，不被丢弃。
- `test_legacy_uid_missing_station`：specimen 缺 station → derive_uid 自动降级（少一段），不报错。
- `test_idempotent_per_row`：同源导入两次，行数不翻倍，内容一致。

## 边界 / 失败用例（必须各写测试）
- 源文件不存在 → 跳过该源，不崩。
- 源 JSON 损坏（非法 JSON）→ 报错中止，**不写入半截**。
- 空 specimens / 空 projects → 正常产出空库。
- 同一 uid 在两个 owner_project_dir（理论冲突）→ 各进各自项目库（全局唯一靠 uid，项目隔离靠库文件）。
- lon/lat 为空字符串 → 存 NULL（非 0）。

## TDD 流程（每个能力）
红（写失败测试）→ 跑红确认 ImportError/AssertionError → 实现 → 跑绿 → 契约断言 → commit。
**禁止**：mock 掉 sha256 校验、删边界测试蒙混、import 源用假数据替代真实 data 结构。

## 验收命令
```bash
cd /mnt/n/claude/photo-platform-ydy-v3
pytest tests/ -v        # 全绿
# 真实数据演练（只读副本）：
cp -r /mnt/n/claude/photo-platform-ydy/prototype-photo-gui/data /tmp/v3-import-test && chmod -R a-w /tmp/v3-import-test
# 用 /tmp/v3-import-test 跑 import_all，断言三表==源、sha256 不变
```
