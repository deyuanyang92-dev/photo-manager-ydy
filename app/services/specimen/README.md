# specimen — 标本 / 命名 / 筛选

**职责：** 标本 CRUD 编排、UID 字段 catalog、数据筛选、编辑锁、重命名。

**已迁入：** `edit_lock_service.py`、`naming_field_catalog.py`（扁平层保留兼容入口）

**待迁入：** `specimen_filter_service.py`, …

**权威：** 编号语义见 `docs/PROJECT_MEMORY.md` + `app/utils/naming.py`。
