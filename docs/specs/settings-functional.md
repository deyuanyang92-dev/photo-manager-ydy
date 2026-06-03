# settings-functional.md — SettingsView 完整功能规格

> Oracle 行号指向 photo-platform-ydy web 原型（同级目录）。

---

## 1. 总体布局

**实现文件：** `app/views/settings_view.py`
**测试文件：** `tests/test_settings_view.py`

SettingsView 是一个 `QTabWidget`，包含 5 个选项卡：

| 顺序 | 标题 | 内容简述 |
|------|------|---------|
| 0 | 项目 | 当前项目目录 + 子目录名 + 最近项目列表 |
| 1 | Helicon | Helicon exe 路径探测/配置 + 合成参数预设 CRUD |
| 2 | 归档 | JXL effort + 删除 JPG 开关 |
| 3 | 操作人 | 当前操作人姓名 |
| 4 | 关于 | 版本 / 运行环境 / 配置文件路径 |

---

## 2. Helicon 选项卡

### 2.1 路径探测与配置（1:1 镜像 web `renderConfigPage()`）

**Web Oracle：** `app.js:12765–12854`，`server.js:2431–2475`

DOM 结构对应关系：

| Web class | Qt widget |
|-----------|-----------|
| `.config-page > .config-header h2` | `QLabel("配置")` |
| `.config-page > .config-header p.config-subtitle` | `QLabel("Helicon Focus CLI 路径与探测设置。")` |
| `.config-section.helicon-config-section` | `QFrame#HeliconConfigSection` |
| `.config-section-title` | `QLabel("Helicon Focus")` |
| `.config-row` 自动探测结果 | `_ConfigRow` + `self._detected_path_label` + `self._detect_status_badge` |
| `.config-row` 当前生效路径 | `_ConfigRow` + `self._effective_path_label` |
| `.config-row--input` 自定义路径 | `QLineEdit#ConfigPathInput` (`self._helicon_exe_edit`) |
| `.config-btn-row [检测]` | `self._test_btn` → `_on_test_click()` |
| `.config-btn-row [保存]` | `self._save_btn` → `_on_save_click()` |
| `.config-btn-row [清除自定义]` | `self._clear_btn` → `_on_clear_click()` |
| `.config-btn-row [重新探测]` | `self._refresh_btn` → `_detect_helicon()` |
| `.config-hint` 探测优先级列表 | `QFrame` + 5 × `QLabel` |

**探测优先级（helicon.js:68–124）：**
1. QSettings 中存储的自定义路径
2. `HELICON_FOCUS_PATH` 环境变量
3. `HELICON_FOCUS_DIR` 环境变量
4. Windows 注册表（Python 实现：通过 `helicon_service.detect_helicon()` 调用）
5. 已知安装目录（`I:\Helicon Focus 8` 等）

**按钮行为：**
- **检测**：调 `_save_helicon()` 保存当前输入的路径，然后调 `_detect_helicon()`（等价于 web testBtn：先保存再探测）
- **保存**：调 `_save_helicon()` + `_refresh_helicon_display()`
- **清除自定义**：清空输入框，调 `_save_helicon()` + `_detect_helicon()`
- **重新探测**：调 `reset_helicon_cache()` + `detect_helicon()` 三级探测

**状态徽章：**
- 可用：`"✓ 可用"`，颜色 `_C_SUCCESS = "#36c98f"`
- 未检测到：`"未检测到"`，颜色 `_C_WARN = "#f1bd57"`

---

### 2.2 合成参数预设 CRUD（新功能，server.js `/api/helicon/presets`）

**Web Oracle（后端 API）：** `server.js:2431–2475`
**Web Oracle（数据结构）：** `helicon.js:391–396`（`heliconPresetsRead/Write`）

Web 原型后端支持命名预设 CRUD，但前端配置页面没有暴露此 UI。GUI 版本需要实现此 UI。

#### 预设数据结构（来自 `helicon.js:HELICON_PRESETS_PATH`）

```json
{
  "version": 1,
  "presets": [
    {
      "name": "标准景深叠加",
      "params": {
        "method": 2,
        "radius": 4,
        "smoothing": 4,
        "quality": 95
      },
      "updatedAt": "2026-06-03T00:00:00.000Z"
    }
  ]
}
```

字段说明：
- `method`: 1=A（加权平均），2=B（景深图），3=C（金字塔）；对应 CLI `-mp:`
- `radius`: 1–16，对应 CLI `-rp:`
- `smoothing`: 0–8，对应 CLI `-sp:`
- `quality`: 70–100，对应 CLI `-j:`（仅 JPEG 输出时有效）

#### GUI 预设 CRUD 实现方案

持久化：**QSettings 键 `helicon/presets_json`**（JSON 字符串存储预设列表），与 web `helicon_presets.json` 文件等价。

UI 布局（在 Helicon tab 的 `合成参数预设` QGroupBox 内）：

```
┌─────────────────────────────────────────────────────┐
│ 合成参数预设                                          │
│                                                     │
│  [预设列表 QListWidget — 显示名称]                     │
│                                                     │
│  合成方式  [A — 加权平均 (1) ▼]                        │
│  半径      [====4====]  (1–16)                       │
│  平滑度    [====4====]  (0–8)                        │
│  JPEG质量  [===95===]   (70–100)                     │
│                                                     │
│  预设名称  [________________]                        │
│  [保存为预设]  [应用选中预设]  [删除选中预设]           │
└─────────────────────────────────────────────────────┘
```

操作语义：
- **保存为预设**：读取名称输入框 + 当前 method/radius/smoothing/quality → 存入 QSettings；若名称已存在则覆盖（upsert，对应 `server.js:2449-2452`）
- **应用选中预设**：从列表选中项填充 method/radius/smoothing/quality spinboxes + _save_helicon()
- **删除选中预设**：从预设列表移除选中项，清空名称框，_save_preset_list()

**QSettings 存储方式：**
- `helicon/presets_json`：JSON 字符串，`[{"name": "...", "params": {...}, "updatedAt": "..."}, ...]`
- 单套「当前参数」继续用 `helicon/method`、`helicon/radius`、`helicon/smoothing`、`helicon/quality` 四个单独键（保持向后兼容）

---

## 3. 归档选项卡

**Web Oracle：** `server.js` compress 配置 + `CLAUDE.md` 删除 JPG 前置条件

### 3.1 JXL effort

| 索引 | 标签 | 含义 |
|------|------|------|
| 0 | `standard — cjxl -e 7（推荐）` | EFFORT_MAP standard=7 |
| 1 | `maximum  — cjxl -e 9（慢，文件更小）` | EFFORT_MAP maximum=9 |

**QSettings key：** `archive/jxl_effort`（整数 0 或 1）

### 3.2 删除 JPG（红线：默认 False）

**硬规则：** `_delete_jpg_chk.setChecked(False)` 是铁律，测试必须断言。

四项前置条件（`CLAUDE.md` + web NOTES.md 均记载）：
1. `cjxl` 可用（JPEG XL 无损压缩工具已安装）
2. ZIP 已生成且大小 > 32 字节
3. 清单完整（文件数 + 名称 + 大小全部核验通过）
4. JXL 可恢复（`djxl` 能重解码每一帧，输出大小 > 0）

**QSettings key：** `archive/delete_jpg`（字符串 `"true"` / `"false"`）

---

## 4. 项目选项卡

**Web Oracle：** `server.js` project-settings drawer（页面内嵌抽屉，无对应 app.js 单一函数）

| 控件 | QSettings key | 默认值 |
|------|--------------|--------|
| 当前项目目录（只读 + 浏览按钮） | `ctx.current_project_dir`（AppContext） | —（无项目） |
| 原片子目录名 | `project/incoming_subdir` | `incoming-jpg` |
| 成果子目录名 | `project/results_subdir` | `results` |
| 最近项目列表 | `project/recent_dirs`（`\n` 分隔字符串，最多 10 条） | —（空） |

最近项目操作：
- `_add_to_recent(path)`：去重 + 移前 + 截断 `_RECENT_MAX=10`
- `_open_recent()`：设置 `ctx.current_project_dir` + 更新显示
- `_clear_recent()`：清空 QSettings key + 清空列表

---

## 5. 操作人选项卡

**QSettings key：** `user/current_user`（字符串，最大 80 字符）

用途：taxonomy 修改的 `modifiedBy` 字段 + 标本创建的 `createdBy` + 协作模式设备注册姓名。

---

## 6. 关于选项卡

| 字段 | 值来源 |
|------|--------|
| 版本 | `APP_VERSION = "0.1.0-dev"`（常量）|
| 运行环境 | `platform.system() + platform.release() + platform.python_version()` |
| 配置文件路径 | `QSettings("SpecimenPhotoWorkbench", "标本照片工作台").fileName()` |

---

## 7. QSettings 键一览

| 键 | 类型 | 默认 |
|----|------|------|
| `helicon/exe_path` | str | `""` |
| `helicon/method` | int | `0` |
| `helicon/radius` | int | `4` |
| `helicon/smoothing` | int | `4` |
| `helicon/quality` | int | `95` |
| `helicon/presets_json` | str (JSON) | `"[]"` |
| `archive/jxl_effort` | int | `0` |
| `archive/delete_jpg` | str | `"false"` |
| `project/incoming_subdir` | str | `"incoming-jpg"` |
| `project/results_subdir` | str | `"results"` |
| `project/recent_dirs` | str (`\n` 分隔) | `""` |
| `project/last_dir` | str | `None`（AppSettings 管理） |
| `user/current_user` | str | `""` |

---

## 8. 新增导出常量（供测试 import）

在 `settings_view.py` 顶部的常量块需要新增：

```python
_K_HELICON_PRESETS_JSON = "helicon/presets_json"
```

---

## 9. 测试断言清单（`tests/test_settings_view.py`）

### 现有测试（已全绿，不得破坏）
- 5 tabs，顺序：`["项目", "Helicon", "归档", "操作人", "关于"]`
- delete_jpg 默认 False（6 个断言）
- 所有 round-trip（current_user / helicon_exe / jxl_effort / radius / smoothing / quality / incoming / results）
- recent_projects（增/去重/移前/清空）
- about tab 可访问
- key string 断言

### 新增测试（预设 CRUD）
- `TestPresetCRUD::test_save_preset_stores_in_settings`
- `TestPresetCRUD::test_save_preset_upserts_existing_name`
- `TestPresetCRUD::test_apply_preset_fills_spinboxes`
- `TestPresetCRUD::test_delete_preset_removes_from_list`
- `TestPresetCRUD::test_preset_list_survives_reload`
- `TestPresetCRUD::test_empty_preset_name_not_saved`
- `TestSettingsKeys::test_helicon_presets_json_key`
