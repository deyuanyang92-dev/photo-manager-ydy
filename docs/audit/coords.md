# 坐标模块审计 (coords audit)

审计时间：2026-06-03  
参照：`prototype-photo-gui/app.js` + `prototype-photo-gui/coord-utils.js`  
实现：`app/utils/coord_utils.py` + `app/views/coords_view.py`

---

## 图例

| 符号 | 含义 |
|------|------|
| ✓    | 完整等价实现，行为对齐 |
| ◐    | 部分实现，有已知差异或降级 |
| ✗    | 缺失 |

---

## 一、`coord-utils.js` 纯函数（14 个 exported 函数）

| web 函数 / 行为 | 状态 | Qt 位置 | 差异/备注 |
|-----------------|------|---------|-----------|
| `parse(input, {isLatitude})` — DD/DMS/DDM/ISO6709 解析 | ✓ | `coord_utils.parse()` L227 | 同参数名换 snake_case |
| `parseDetailed(input)` — 带 format / label / dms-components 的增强解析 | ✓ | `coord_utils.parse_detailed()` L313 | `verbatim_lon` 同样为空串（P3-6 known limitation） |
| `fromDMSFields(d, m, s, dir)` | ✓ | `coord_utils.from_dms_fields()` L399 | 1:1 |
| `fromDDMFields(d, m, dir)` | ✓ | `coord_utils.from_ddm_fields()` L407 | 1:1 |
| `toDD(lat, lon)` — `29.11492°N, 121.76421°E` | ✓ | `coord_utils.to_dd()` L264 | 1:1 |
| `toDMS(lat, lon)` — `29°6'53.7"N 121°45'51.2"E` | ✓ | `coord_utils.to_dms()` L271 | 1:1 |
| `toDDM(lat, lon)` — `29°6.895'N 121°45.854'E` | ✓ | `coord_utils.to_ddm()` L279 | 1:1 |
| `toDDzh(lat, lon)` — `北纬 29.114920  东经 121.764210` | ✓ | `coord_utils.to_dd_zh()` L287 | 1:1 |
| `toDMSzh(lat, lon)` | ✓ | `coord_utils.to_dms_zh()` L294 | 1:1 |
| `toDDMzh(lat, lon)` | ✓ | `coord_utils.to_ddm_zh()` L303 | 1:1 |
| `isValid(lat, lon)` | ✓ | `coord_utils.is_valid()` L417 | 1:1 |
| `inferLatLonOrder(v1, v2)` | ✓ | `coord_utils.infer_lat_lon_order()` L66 | 1:1 |
| `isInMainlandChina(lon, lat)` | ✓ | `coord_utils.is_in_mainland_china()` L424 | 1:1 |
| `wgs84ToGcj02(lon, lat)` | ✓ | `coord_utils.wgs84_to_gcj02()` L445 | 1:1；精度 6dp |
| `gcj02ToWgs84(lon, lat)` — 5-iteration approx | ✓ | `coord_utils.gcj02_to_wgs84()` L466 | P3-5 known limitation；7dp；同 JS |
| `bd09ToGcj02(bdLon, bdLat)` | ✓ | `coord_utils.bd09_to_gcj02()` L484 | 1:1 |
| `gcj02ToBd09(lon, lat)` | ✓ | `coord_utils.gcj02_to_bd09()` L496 | 1:1 |
| `wgs84ToBd09(lon, lat)` | ✓ | `coord_utils.wgs84_to_bd09()` L506 | 1:1 |
| `bd09ToWgs84(lon, lat)` | ✓ | `coord_utils.bd09_to_wgs84()` L512 | 1:1 |

**小结：coord-utils.js 全部 19 个公开函数在 Python 中均有 1:1 等价实现。**

---

## 二、`app.js` UI 函数（coords 相关，共 19 个）

| web 函数 | 状态 | Qt 位置 | 差异/备注 |
|----------|------|---------|-----------|
| `ensureCoordParser()` — 懒加载 coord-utils.js | ✓ | 不需要（Python 直接 import） | 等价：`from app.utils.coord_utils import …` |
| `ensureAMap()` — 懒加载高德 JS SDK | ✓ | `_MAP_HTML` 内嵌 `<script src=…>` + `_ensure_web_view()` L1469 | 降级：QWebEngineView 不可用时显示占位 label；行为等价 |
| `renderCoordPage()` — 整体布局渲染 | ✓ | `CoordsView._setup_ui()` L347；`_build_header()` / `_build_panel()` / `_build_batch_section()` | PyQt 布局替代 DOM；功能 1:1 |
| `coordUpdateParse()` — 实时解析输入 | ✓ | `_on_input_changed()` L769 + `parse_detailed()` | 1:1；blockSignals 防环 |
| `coordUpdateBadgeAndCards()` — 徽章+卡片 DOM 热更新 | ✓ | `_update_badge()` L784 + `_update_cs_section()` L831 + `_rebuild_cs_cards()` L838 | PyQt 不需要 DOM 热更新技巧；逻辑完全等价 |
| `coordSyncFromStruct()` — DMS 字段 → 主输入 | ✓ | `_on_struct_changed()` L1101 | 1:1；blockSignals 防重入 |
| `coordDoPlaceSearch(q)` — 高德 PlaceSearch | ◐ | `_on_place_search()` L942 | **降级**：地图未打开时走 Nominatim（免 key）而非 AMap PlaceSearch；打开地图时调 `doPlaceSearch(q)` 转发给 JS |
| `coordInitMap(lat, lon)` — 初始化高德地图实例 | ✓ | `_MAP_HTML` JS `initMap(lat,lon)` + `_ensure_web_view()` L1469 | WebEngine 内嵌；功能 1:1 |
| `coordSetMapMarker(gcjLon, gcjLat)` — 放置可拖拽标记 | ✓ | `_MAP_HTML` JS `setMarker(gcjLon, gcjLat)` L189 | 内嵌于 WebEngine；等价 |
| `coordUpdateMapDisplay(gcjLon, gcjLat)` — 更新底部坐标显示 | ✓ | `_Bridge.onMarkerMoved()` L1495（Qt 侧）+ `updateDisplay()` JS L197 | JS 端算 WGS-84；Qt bridge 接收并更新 QLabel；等价 |
| `renderCoordMapModal(ctRef)` — 地图模态层 DOM | ✓ | `_show_map_modal()` L1317 | PyQt 覆盖层；头部搜索+关闭+确认选点全部实现 |
| `coordMapEscHandler(e)` — Esc 关闭地图模态 | ✗ | **未实现** | web 版监听 document keydown；Qt 版无 Esc 关闭（见下方补缺） |
| `batchParseCoords()` — 多行解析 | ✓ | `_on_batch_parse()` L1136 | 1:1 |
| `batchConvertRow(row, fmt, cs)` — 每行转换（中文格式化） | ✓ | `_batch_convert_row_full()` L1243 | 1:1 |
| `batchFormatCoord(val, fmt)` — 单值 DD/DMS/DDM 格式化 | ✓ | `_format_val()` L1191 | 1:1 |
| `batchToCsv()` — 生成 CSV 字符串（BOM + 7列） | ✓ | `_batch_to_csv()` L1261 | 1:1；含 BOM |
| `batchDownloadCsv()` — 触发下载 | ✓ | `_on_download_csv()` L1301 | `QFileDialog.getSaveFileName` 等价 |
| `nominatimToZh(d)` — Nominatim 响应格式化为中文 | ✗ | **未实现** | web 版在 `metaReverseGeocode` 中使用；Qt 版目前只取 `display_name[:60]`（见下方补缺） |
| `metaReverseGeocode(sp)` — 反向地理编码填写标本区域名 | ✗ | **未实现** | 这是工作台视图功能，不在 CoordsView 内；属于工作台/标本元数据模块缺口 |

---

## 三、差异摘要

### 已确认差异（行为降级，属设计决策）

1. **地名搜索降级**（`coordDoPlaceSearch` → `_on_place_search`）  
   - 地图未打开时使用 Nominatim 而非 AMap PlaceSearch；结果格式（`display_name[:60]`）比 AMap 更粗糙。  
   - 理由：Nominatim 无需 API key，适合离线/受限环境；功能语义等价。

2. **`gcj02ToWgs84` 精度**（P3-5 known limitation）  
   - 5 次迭代，误差 < 1 m。两端实现相同，已在注释中说明。

### 真实缺口（✗）

| 缺口 | 影响 | 优先级 |
|------|------|--------|
| **Esc 关闭地图模态**（`coordMapEscHandler`） | 用户体验：只能点"关闭"按钮 | 低（易补） |
| **`nominatimToZh`** — 省市区街道拼装 | 地名搜索结果仅 `display_name[:60]`，不按中文行政层级格式化 | 低（影响美观） |
| **`metaReverseGeocode`** — 反向地理编码写入标本 geoArea | 工作台中经纬度输入后无自动反查地名 | 中（属工作台模块缺口，不在 coords_view） |

---

## 四、补缺内容（本次 commit 完成）

1. **Esc 关闭地图模态** → `CoordsView` 在 `_show_map_modal()` 中注册 `keyPressEvent` 覆盖，捕获 Escape 关闭。  
2. **`nominatim_to_zh(d)`** → 在 `coord_utils.py` 中新增纯函数（省市县街道地点拼装，镜像 JS）；`_on_place_search` 改用此函数格式化结果。

### 仍缺（本次不改）

- `metaReverseGeocode`：属工作台标本元数据流，不在 coords 模块。追踪：待 workbench_view 实现时补充。

---

## 五、测试覆盖

| 测试文件 | 类 | 用例数 |
|----------|----|--------|
| `tests/test_coord_utils.py` | ParseDD / ParseDMS / ParseDDM / ParseISO6709 / ParseEdgeCases / ParseDetailed / Formatting / WGS84GCJ02 / BD09 / FieldConstructors / IsValid | 40 |
| `tests/test_coords_view.py` | Construction / Badge / CsCards / StructuredDms / Batch | 35 |
| `tests/test_coord_utils.py` *(新增)* | TestNominatimToZh | 5 |
| `tests/test_coords_view.py` *(新增)* | TestEscCloseMap | 2 |

总计（本次 commit 后）：**109 → 116 用例，全部通过**
