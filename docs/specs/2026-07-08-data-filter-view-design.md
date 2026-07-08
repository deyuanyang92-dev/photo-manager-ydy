# 数据筛选视图 (DataFilterView) 设计 spec

日期: 2026-07-08
来源: brainstorming skill(用户:预览字段筛选 + 只读/编辑权限)

## 1. 需求

1. **独立 nav 页「数据筛选」** — 跨断面查询/筛选 specimen。
2. **多选工作区(断面)** → 字段筛选 → **编号列表 + 照片 + 统计**。
   - 例:取了 RNA 的编号 / 某拍摄人拍了多少 / 某地区多少标本。
3. **字段动态,不硬编码**(用户诉求"不要硬编号,方便升级")。
4. **默认只读预览**;**编辑需管理权限**:密码默认 `123`(可改)+ 填修改人姓名。
5. **解锁模型**:会话登录(A,git 风格)— 进编辑模式输一次,本会话内随便编辑,所有改动记该姓名。
6. **保护范围**:只「数据筛选」页(A,workbench 不动)。

## 2. 字段策略(方案 3:PRAGMA + 注册表)

- **可筛字段 = `PRAGMA table_info(specimens)` ∪ `FIELD_META` ∪ `DERIVED`**
- `FIELD_META`:只存**增强元数据**(中文科标签),**不存字段列表**。
- `DERIVED`:派生维度,如 `storage_is_rna`(oracle `app.js:300` 规则:`storage` 以 `R` 开头 = 已取 RNA)。
- **升级路径**:DB 加列 → `PRAGMA` 自动出现可筛(零码改);要中文标签/派生语义 → 注册表加一条。

## 3. RNA 解码(oracle 依据)

`prototype-photo-gui/app.js:300`:
```
{ code: "R95E", detail: "已取 RNA,组织保存于 RNAlater;剩余标本以 95% 酒精保存", transcriptome: true }
```
→ `storage_is_rna` = `str(storage).upper().startswith("R")`。

## 4. 模块(全新增,各可独立测)

```
app/config/specimen_fields.py        # 注册表,Qt-free
app/services/specimen_filter_service.py  # 跨断面只读查询,Qt-free
app/services/edit_lock_service.py    # 密码 config + 会话解锁,Qt-free
app/views/data_filter_view.py        # UI 组合上述 + 复用 thumbnail_worker
```

`AppContext` 加属性: `edit_unlocked: bool`(默认 False), `edit_actor: str`(默认 "")。

### 4.1 specimen_fields.py
```python
FIELD_META = {                      # 只增强元数据,不存列表
    "storage":         {"label": "保存方式"},
    "photographer":    {"label": "拍摄人"},
    "collector":       {"label": "采集人"},
    "identifier":      {"label": "鉴定人"},
    "province":        {"label": "省"},
    "site":            {"label": "地区"},
    "station":         {"label": "站位"},
    "scientific_name": {"label": "学名"},
    "scientific_name_cn": {"label": "中名"},
    "family":          {"label": "科"},
    "genus":           {"label": "属"},
    "geo_area":        {"label": "海区"},
    "collection_date": {"label": "采集日期"},
    "photo_date":      {"label": "拍摄日期"},
    # ... 其余列无 meta 也能筛(PRAGMA 提供),仅无中文标签
}
DERIVED = {
    "storage_is_rna": {"label": "已取RNA", "from": "storage",
                       "match": lambda v: str(v or "").upper().startswith("R")},
}
def filterable_fields(db_path) -> list[dict]:
    # PRAGMA table_info(specimens) 列名 ∪ FIELD_META ∪ DERIVED,带 label
def field_label(key) -> str   # FIELD_META/DERIVED label,否则 key
def is_derived(key) -> bool
def eval_derived(key, specimen_row) -> bool
```

### 4.2 specimen_filter_service.py
```python
def query_specimens(workspaces: list[str], conditions: list[dict]) -> list[dict]:
    # conditions: [{"field": str, "op": str, "value": str}]
    #   op ∈ {"eq","contains","is_empty","not_empty","is_rna"}
    # 每个 workspace 读 _data/project.db(specimens 表),内存合并 + 过滤
    # 派生维度(is_rna 等)post-filter;普通列 SQL WHERE 或内存过滤
    # 只 SELECT,不写。db 缺失/损坏/锁定跳过(容错,同 taxon_inventory)。
def field_choices(workspaces: list[str], field: str) -> list[str]:
    # 某字段所有非空 distinct 值(筛选下拉候选),派生维度返 ["是","否"]
```
返回行: `{**specimen_columns, "_workspace": dir, "_workspace_label": name}`。

### 4.3 edit_lock_service.py
```python
def load_config() -> dict            # data/app_config.json,缺省 {"edit_password": sha256("123")}
def save_config(cfg)
def verify_password(plain) -> bool   # sha256 比
def set_password(plain)              # 改密
def is_unlocked(ctx) -> bool         # ctx.edit_unlocked
def current_actor(ctx) -> str        # ctx.edit_actor
def unlock(ctx, actor, plain) -> bool  # 校验+置 ctx.edit_unlocked=True, ctx.edit_actor=actor
def lock(ctx)                        # 置 False, ""
def require_unlock(ctx) -> bool      # 编辑前调;False = 调用方应弹密码框
```
密码 hash: `hashlib.sha256(plain.encode()).hexdigest()`。明文不落盘。

### 4.4 DataFilterView
- 顶部:**数据源多选**(QListWidget CheckBoxMode,列 `discover_workspaces` + `user_projects.json`)
- **筛选条**:动态字段下拉(`filterable_fields`)+ 操作符下拉 + 值(下拉 `field_choices` 或文本)+ `[+条件]` / `[清除]`
- **状态行**:`只读预览` | `[🔒 解锁编辑]`(解锁后变 `[🔒 已解锁:姓名][锁定]`)
- **统计**:总数 + 取 RNA 数 + 按拍摄人/地区分组计数
- **结果表**:QTableWidget(编号/地区/拍摄人/storage/学名/…),双击→编辑(需解锁)
- **照片**:选中行 → 右侧/下方缩略(复用 `GridThumbnailWorker`)
- 编辑(解锁后):双击单元格改字段 → 写回该 specimen 所属 workspace db → `activity_audit.log_event(actor=ctx.edit_actor, action="specimen.edit", ...)`
- 注册 `ALL_VIEWS`(`view_id="data_filter"`, `nav_title="数据筛选"`)

## 5. 数据流

```
UI(多选工作区 + conditions)
  → specimen_filter_service.query(workspaces, conditions)
  → 各 workspace _data/project.db SELECT specimens
  → 内存合并 + AND 过滤(派生 post-filter)
  → list[dict] → 统计区 + 结果表
照片 → GridThumbnailWorker(decode QImage)→ make_pixmap → 表/缩略
解锁 → edit_lock_service.unlock(ctx, actor, plain)
     → ctx.edit_unlocked=True, edit_actor=actor
编辑 → require_unlock(ctx)? → 写 db + activity_audit.log_event
```

## 6. 红线

- 筛选服务**纯 SELECT,不写 db**(查询路径永不 INSERT/UPDATE)。
- **无 `species`/`species_cn` 列**(用 `scientific_name`/`scientific_name_cn`)。
- `raw_json` 兜底**不动**(查询不碰)。
- **§7 注释保留旧码**:改既有文件用 `#` 留旧实现;本 spec 以新增模块为主,改动 `AppContext`/`ALL_VIEWS` 处保留旧码注释。
- **UI 冻结**:新增 nav 页 + 新模块,不改现有页布局/视觉(workbench 不动)。
- 密码明文不落盘(sha256)。

## 7. 测试(TDD,单文件跑,禁全量)

- `test_specimen_fields.py`: `filterable_fields` 返回 PRAGMA 列 ∪ META ∪ DERIVED;`storage_is_rna` 匹配 `R95E`/`R`/不匹配 `T95E`/`D`;`field_label` 中文。
- `test_specimen_filter_service.py`: 多 workspace 跨断面查询;条件 AND;`op=is_rna` 过滤 R*;空值/`is_empty`;`field_choices` distinct;db 缺失跳过;**只读不写**(查询后 db mtime/内容不变)。
- `test_edit_lock_service.py`: 默认密码 `123` verify True;错密 False;`set_password` 后旧密失效;`unlock` 置 `ctx.edit_unlocked`+`actor`;`lock` 复位;`is_unlocked`/`require_unlock`。
- `test_data_filter_view.py`: 数据源多选;筛选条动态字段;结果表填;解锁流程(错密不解锁);只读态双击不编辑。

## 8. 实现顺序(依赖)

1. `specimen_fields.py`(无依赖)
2. `specimen_filter_service.py`(依赖 fields)
3. `edit_lock_service.py` + `AppContext` 属性(无依赖)
4. `DataFilterView`(依赖 1-3)+ 注册 nav
5. 跑测试 → commit → 启动 app
