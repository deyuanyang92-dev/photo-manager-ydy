# 坐标工具功能规格 (coords-functional)

> Oracle 行号引用自 `prototype-photo-gui/app.js` (18 575 行版本，2026-06-03 快照)。

---

## 1. 输入框 + 格式徽章 (coord-input-wrap / coord-format-badge)

| # | 行为 | Oracle 行号 |
|---|------|------------|
| 1.1 | 单行文本输入，placeholder 提示 DD / DMS 示例 | app.js:12879–12882 |
| 1.2 | 每次 `input` 事件调 `coordUpdateParse()` + `coordUpdateBadgeAndCards()` 原地更新 | app.js:12883–12887 |
| 1.3 | 输入为空 → 徽章隐藏 | app.js:12901 |
| 1.4 | 解析成功 → 绿色徽章 `✓ <格式名> — lat xx.xxxxxx, lon xxx.xxxxxx` | app.js:12904–12905 |
| 1.5 | 解析失败 → 红色徽章 `无法识别坐标格式` | app.js:12907–12909 |
| 1.6 | 徽章原地更新（不重建 DOM），避免每次按键全量 render | app.js:13290–13303 |

**支持格式（CoordParser.parseDetailed）：**

| 格式 | 示例 | coord-utils.js 函数 |
|------|------|---------------------|
| DD | `29.11492 N 121.76421 E` / `29.11492, 121.76421` | `_parseDD` |
| DMS | `29°06'53.7"N 121°45'51.2"E` / `N24°29'21.1" E118°11'03.6"` | `_parseDMS` |
| DDM | `29°06.895'N 121°45.854'E` | `_parseDDM` |
| ISO 6709 | `+29.11492+121.76421/` | `_parseISO6709` |
| 中文方向 | `北纬 29.11492 东经 121.76421` | 预处理替换后走 DD |

---

## 2. 坐标系标签页 + 三卡片 (coord-cs-tabs / coord-cs-cards)

| # | 行为 | Oracle 行号 |
|---|------|------------|
| 2.1 | 三标签：十进制 / 度分秒 / 度分，默认 `dd` | app.js:12916–12933 |
| 2.2 | 三张卡片：WGS-84（国际通用） / GCJ-02（国测局） / BD09（百度） | app.js:12937–12959 |
| 2.3 | 每张卡显示所选格式的中文方向格式化值（`toDDzh`/`toDMSzh`/`toDDMzh`） | app.js:12967–12972 |
| 2.4 | 每张卡有「复制」按钮，成功后 1.5 s 内切换为 `✓` | app.js:12974–12983 |
| 2.5 | 无解析结果时隐藏整个 CS 区域 | app.js:13307–13311 |
| 2.6 | 原地更新（不重建 DOM） | app.js:13305–13382 |

坐标系转换：
- WGS-84 → GCJ-02: `CoordParser.wgs84ToGcj02(lon, lat)` — 仅在中国大陆边界框内偏移 | coord-utils.js:327–338
- WGS-84 → BD09: `CoordParser.wgs84ToBd09(lon, lat)` — GCJ-02 再 BD09 | coord-utils.js:367–370
- GCJ-02 → WGS-84: 5 次迭代反解，误差 < 1 m | coord-utils.js:340–349

---

## 3. 地名搜索 (coord-place-wrap / coord-place-results)

| # | 行为 | Oracle 行号 |
|---|------|------------|
| 3.1 | 文本输入 + 「搜索地名」按钮，回车触发 | app.js:12991–12996 |
| 3.2 | 搜索中显示 `搜索中...` 占位 | app.js:13001–13003 |
| 3.3 | 结果列表：地名 + 坐标预览 (lat.toFixed(5), lon.toFixed(5)) + 「填入」按钮 | app.js:13007–13021 |
| 3.4 | 点「填入」→ 主输入框填入 `lat.toFixed(6) + ", " + lon.toFixed(6)` 并重解析 | app.js:13013–13016 |
| 3.5 | Web 实现用高德 AMap.PlaceSearch；Python 降级用 Nominatim geocoder (无需 API key) | app.js:13399–13419 |

---

## 4. 结构化 DMS 输入 (coord-struct-toggle / coord-struct-input)

| # | 行为 | Oracle 行号 |
|---|------|------------|
| 4.1 | 默认折叠，标题「▶ 结构化输入 (DMS)」 | app.js:13025–13032 |
| 4.2 | 展开后显示纬度 / 经度行，各有度/分/秒输入框 + 方向选择（N/S、E/W） | app.js:13035–13086 |
| 4.3 | 任一字段变化 → `fromDMSFields` 算出 DD → 更新主输入框为 DMS 格式 | app.js:13060–13065 |
| 4.4 | 展开时若已有解析结果则预填分量 | app.js:13037–13039 |

---

## 5. 批量转换 (coord-batch-section)

| # | 行为 | Oracle 行号 |
|---|------|------------|
| 5.1 | 默认折叠 `▶ 批量转换` | app.js:13093–13098 |
| 5.2 | 展开后显示：多行文本框 + 「解析」按钮 + 示例数据下拉 | app.js:13101–13180 |
| 5.3 | 示例数据 6 组：mixed / dd / dms / cn / prefix / ddm | app.js:13126–13173 |
| 5.4 | 点「解析」→ 逐行 `parseDetailed()`，解析结果填 rows；失败行 `error:"无法识别"` | app.js:13568–13578 |
| 5.5 | 有结果后显示输出格式标签 (DD / DMS / DDM) | app.js:13184–13215 |
| 5.6 | 输出坐标系标签 (WGS-84 / GCJ-02 / BD09) | app.js:13202–13215 |
| 5.7 | 表格列：# / 输入 / 北纬 / 东经；错误行 `✗ 无法识别` | app.js:13218–13248 |
| 5.8 | 单列值 `batchFormatCoord(val, fmt)` — DD=6 位小数 / DMS=度分秒无方向 / DDM=度分无方向 | app.js:13596–13611 |
| 5.9 | 「复制 CSV」→ 7 列 CSV（`序号,原始,格式,纬度,经度,转换结果,错误`）含 BOM，1.5 s 后恢复 | app.js:13613–13629 |
| 5.10 | 「下载 .csv」→ blob 触发文件名 `batch-coords-<ts>.csv` | app.js:13631–13642 |

**CSV 完整列格式（7 列）：**
```
序号, 原始, 格式, 纬度(6dp), 经度(6dp), 转换结果(full zh string), 错误
```
`转换结果` 是 `batchConvertRow(row, outputFormat, outputCs)` 的完整中文格式化字符串。

---

## 6. 交互地图模态 (coord-map-overlay / coord-map-modal)

| # | 行为 | Oracle 行号 |
|---|------|------------|
| 6.1 | 点「📍」按钮 → 打开模态覆盖层 | app.js:12888–12895 |
| 6.2 | 模态含：地名搜索框 + 搜索按钮 + 关闭按钮 + 地图容器 + 坐标显示区 + 确认按钮 | app.js:13478–13553 |
| 6.3 | 高德地图嵌入（GCJ-02 坐标系）；拖拽/点击 marker → 实时显示 WGS-84 + GCJ-02 + DMS + DDM | app.js:13455–13476 |
| 6.4 | 坐标显示格式：`WGS-84: lat, lon  (GCJ-02: lat, lon)` | app.js:13473 |
| 6.5 | 格式行：`DMS: ... | DDM: ...` | app.js:13475 |
| 6.6 | 「确认选点」→ 填入主输入框并关闭；GCJ-02 → WGS-84 转换 | app.js:13526–13545 |
| 6.7 | ESC 键关闭 | app.js:13556–13563 |
| 6.8 | 地图内搜索框同样调 AMap.PlaceSearch | app.js:13486–13500 |
| 6.9 | Python 实现：QWebEngineView 内嵌高德地图 HTML；onMarkerMoved JS→Python 桥 | coords_view.py:1447–1468 |
| 6.10 | QWebEngineView 不可用时降级为 QLabel 占位 | coords_view.py:1481–1489 |

---

## 7. 反向地理编码 (metaReverseGeocode)

> **注**：反向地理编码 (`/api/geocode/reverse`) 属于工作台标本卡片（`metaReverseGeocode`），
> 不在坐标工具页面本身。坐标工具页不含反向地理编码功能。
> server.js:2201–2231；app.js:13655–13677。

---

## 8. 黄金向量

以下测试向量来自 JS 实现的手动运算或直接执行输出：

| 格式 | 输入 | 期望 lat | 期望 lon |
|------|------|---------|---------|
| DD | `29.11492 N 121.76421 E` | 29.11492 | 121.76421 |
| DMS | `29°06'53.7"N 121°45'51.2"E` | 29.114917 | 121.764222 |
| DDM | `29°06.895'N 121°45.854'E` | 29.114917 | 121.764233 |
| ISO6709 | `+29.11492+121.76421/` | 29.11492 | 121.76421 |
| 中文 | `北纬 29.11492 东经 121.76421` | 29.11492 | 121.76421 |
| 方位前置 | `N24°29'21.1" E118°11'03.6"` | 24.489194 | 118.184333 |

WGS-84 → GCJ-02 黄金值（lon=121.76421, lat=29.11492）：
- `gcj.lon ≈ 121.767192` (±0.0001)
- `gcj.lat ≈ 29.112135` (±0.0001)

---

## 9. Python / JS 对齐说明

| JS | Python | 状态 |
|----|--------|------|
| `CoordParser.parseDetailed()` | `coord_utils.parse_detailed()` | ✓ 已对齐 |
| `CoordParser.toDDzh/toDMSzh/toDDMzh()` | `to_dd_zh/to_dms_zh/to_ddm_zh()` | ✓ 已对齐 |
| `CoordParser.wgs84ToGcj02()` | `wgs84_to_gcj02()` | ✓ 已对齐 |
| `CoordParser.wgs84ToBd09()` | `wgs84_to_bd09()` | ✓ 已对齐 |
| `CoordParser.gcj02ToWgs84()` 5 次迭代 | `gcj02_to_wgs84()` 5 次迭代 | ✓ 已对齐 |
| `batchToCsv()` 7 列 + BOM | `_batch_to_csv()` | ⚠ 旧版仅 4 列 → 已修正 |
| `coordUpdateMapDisplay` 显示 GCJ + DMS + DDM | `_Bridge.onMarkerMoved` | ⚠ 旧版仅 WGS → 已修正 |
| `coordMapEscHandler` ESC 关闭 | `keyPressEvent` ESC 关闭 | ✓ 已实现 |
