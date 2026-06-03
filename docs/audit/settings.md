# Settings Module Audit — Web → Qt

Generated: 2026-06-03  
Source: `prototype-photo-gui/app.js` (12 config functions) vs `app/views/settings_view.py`

---

## Coverage Table

| Web 函数 | 状态 | Qt 位置 / 缺口说明 |
|---|---|---|
| `renderConfigPage()` (app.js:12765) | ✓ | `_build_tab_helicon()` — 1:1 镜像 DOM |
| `fetchHeliconConfig()` (app.js:2700) | ✓ | `_load_all()` → `_refresh_helicon_display()` |
| `saveHeliconConfigPath()` (app.js:2721) | ✓ | `_on_save_click()`, `_on_clear_click()`, `_save_helicon()` |
| `fetchHeliconStatus()` (app.js:2687) | ✓ | `_detect_helicon()` → `helicon_service.detect_helicon()` |
| Helicon preset CRUD (`/api/helicon/presets` GET/POST/DELETE, server.js:1911–1928) | ✓ | `_save_current_as_preset()`, `_apply_selected_preset()`, `_delete_selected_preset()`, `_load_presets()` |
| JXL effort / delete-JPG (`renderSettingsSection` 归档部分) | ✓ | `_build_tab_archive()` — delete_jpg default=False 断言已实现 |
| 操作人设置 (app.js:~9418 project drawer / v4Settings) | ✓ | `_build_tab_user()` — currentUser 字段 |
| 子目录名 + 最近项目 (projectPathConfig app.js:1707) | ✓ | `_build_tab_project()` — incoming/results subdirs + recent list CRUD |
| About 页 (版本/环境/配置文件路径) | ✓ | `_build_tab_about()` |
| `renderHeliconConfigModal()` 高级参数 (app.js:7029) | ◐ | **Helicon tab 已有 method/radius/smoothing/jpegQuality；缺 tiffCompression / outputFormat / saveDepthMap / runMode / concurrency（"高级" 折叠块）** |
| `saveV4Settings()` / `loadV4Settings()` (app.js:2593–2614) | ✗ | **autoWatch、groupingAutoWatch、groupingAutoWatchMode、fileViewMode、autoActivateOnNewSpecimen — 五个工作台持久化开关，settings_view 无对应项** |
| `renderGlobalSettings()` (app.js:4463) | ✓ | `_build_tab_ui()` — 界面 tab：fontScale QDoubleSpinBox(0.7–1.5)、四项 icon emoji 输入框、useRealCompression QCheckBox、四个 QKeySequenceEdit 快捷键录制 |
| `renderProjectSettingsDrawer()` 概要/保存方式/人员预设/命名规则/TIFF元数据 (app.js:9418) | ✗ | **项目级面板（非全局配置）；Qt 中归属 WorkbenchView 抽屉，不在 settings_view。属于已知范围外缺口** |
| `renderSettingsSection()` 工作台压缩折叠 (app.js:17716) | ✗ | **autoNaming / autoStart 仅在工作台压缩面板出现，属 WorkbenchView 不属 settings_view。不应迁入此文件** |

---

## 状态说明

- **✓ 已覆盖** — Qt 有完整等价实现并有测试
- **◐ 部分覆盖** — 主路径已有，高级参数块缺失
- **✗ 缺失（settings_view 范围内）** — v4Settings 工作台开关 + 全局设置弹窗
- **✗ 缺失（settings_view 范围外）** — 项目设置抽屉 + 工作台压缩面板（不应迁入此文件）

---

## 需要补充的项（settings_view 范围内）

### 1. Helicon 高级参数 (◐ → ✓)

`_build_tab_helicon()` 已有 method/radius/smoothing/jpegQuality。  
缺失的高级参数（对应 web `renderHeliconConfigModal` 中的 `<details>输出选项` 折叠块）：

| 参数 | web 默认值 | QSettings key |
|---|---|---|
| outputFormat | "tif" | `helicon/output_format` |
| tiffCompression | "u" (无压缩) | `helicon/tiff_compression` |
| saveDepthMap | false | `helicon/save_depth_map` |
| runMode | "silent" | `helicon/run_mode` |
| concurrency | 1 | `helicon/concurrency` |

**实现方案**：在 Helicon tab 末尾加 `QGroupBox("高级输出选项")` 折叠组（默认收起），放 QComboBox×3 + QSpinBox×1 + QCheckBox×1。

### 2. V4 工作台开关 (✗ → ✓)

`saveV4Settings()` / `loadV4Settings()` 存 5 个布尔/枚举：

| 开关 | 默认 | QSettings key |
|---|---|---|
| autoWatch (TIFF 自动激活) | true | `workbench/auto_watch` |
| autoActivateOnNewSpecimen | false | `workbench/auto_activate_new` |
| groupingAutoWatch | false | `workbench/grouping_auto_watch` |
| groupingAutoWatchMode | "compose+organize" | `workbench/grouping_auto_watch_mode` |
| fileViewMode | "jpg-tif" | `workbench/file_view_mode` |

**实现方案**：在项目 tab 或新增「工作台」tab 中暴露这 5 项。

### 3. 全局设置 (✗ → ✓) ← 已完成

`renderGlobalSettings()` 对应 `_build_tab_ui()`（界面 tab），实现：

| 项目 | web 默认 | QSettings key | 实现 |
|---|---|---|---|
| fontScale | 1.0 | `ui/font_scale` | QDoubleSpinBox 0.7–1.5，实时更新百分比标签 |
| icons.gps | "📡" | `ui/icon_gps` | QLineEdit，emoji 输入 |
| icons.map | "📍" | `ui/icon_map` | QLineEdit |
| icons.folder | "📁" | `ui/icon_folder` | QLineEdit |
| icons.search | "🔍" | `ui/icon_search` | QLineEdit |
| useRealCompression | false | `debug/use_real_compression` | QCheckBox |
| shortcuts/monitor_activate | "" | `shortcuts/monitor_activate` | QKeySequenceEdit |
| shortcuts/monitor_deactivate | "" | `shortcuts/monitor_deactivate` | QKeySequenceEdit |
| shortcuts/labels_print | "" | `shortcuts/labels_print` | QKeySequenceEdit |
| shortcuts/labels_next | "" | `shortcuts/labels_next` | QKeySequenceEdit |

---

## 不需要迁入 settings_view 的项（已明确范围外）

| 项 | 原因 |
|---|---|
| `renderProjectSettingsDrawer()` 5 tabs (保存方式/人员预设/命名规则/TIFF元数据) | 项目级数据，属于 WorkbenchView 抽屉，与全局配置是不同层次 |
| `renderSettingsSection()` (autoNaming/autoStart) | 工作台压缩子面板状态，不是持久设置，属 WorkbenchView |

---

## 当前测试覆盖（109 tests，全绿）

| 测试类 | 项目 |
|---|---|
| `TestInstantiation` (5) | 实例化/view_id/nav_title/nav_icon/objectName |
| `TestTabs` (2) | 7 tabs + 标题验证 |
| `TestDeleteJpgDefault` (6) | **红线断言**：fresh/false/true/回退/QSettings key |
| `TestRoundTrip` (8) | currentUser/heliconExe/jxlEffort/radius/smoothing/quality/incoming/results |
| `TestRecentProjects` (4) | add/deduplicate/move-to-front/clear |
| `TestAboutTab` (2) | version nonempty / tab accessible |
| `TestSettingsKeys` (7) | key 字符串常量校验 |
| `TestPresetCRUD` (9) | save/upsert/empty/apply/double-click/delete/persist/multiple/delete-one-of-many |
| `TestHeliconAdvancedParams` (18) | outputFormat/tiffCompression/saveDepthMap/runMode/concurrency round-trip |
| `TestWorkbenchToggles` (19) | autoWatch/autoActivateNew/groupingAutoWatch/groupingMode/fileViewMode round-trip |
| `TestUISettings` (29) | fontScale/icons/useRealCompression/shortcuts round-trip + key constants |

---

## 补缺优先级建议

1. **高**：Helicon 高级参数 (◐) — 影响合成质量，runMode/concurrency 已在 web 工作流中使用
2. **中**：V4 工作台开关 — autoWatch/groupingAutoWatch 控制自动化程度，用户依赖
3. **低**：全局 UI 设置 (fontScale/icons) — 可用性增强，不影响数据完整性
4. **低**：键盘快捷键 — Qt 原生有更好替代方案（QKeySequenceEdit），可后期实现
