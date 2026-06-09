# 自由设计标签框架

目标：在“标签打印”里增加一个明确的“设计标签”子功能，像 PPT 一样在标签画布上自由放置文字、绑定字段、线条、矩形、圆形/椭圆、图片、二维码/条码，并且支持矩形、圆角矩形、圆形等标签外形。

## 当前基础

- 现有 `LabelDesignerDialog` 已经是 WYSIWYG 画布：拖拽、缩放、对齐、分布、撤销、复制粘贴、属性面板。
- 现有渲染入口是 `app.utils.label_render.render_label_onto()`；设计器预览、排版预览、实际打印必须共用这个入口。
- 现有模板 JSON 已支持：
  - `shape`: `rect` / `roundrect` / `circle`
  - `rows`: 传统按行布局
  - `qr`: 二维码位置、内容字段、纠错等级
  - `elements`: 自由图形层，坐标单位为 mm，列表顺序就是层级顺序，最后一个在最上面

## 模板数据模型

一个自由设计模板仍然是普通 label template，不另建打印格式：

```json
{
  "name": "样品瓶自由设计",
  "shape": "circle",
  "bgColor": "#ffffff",
  "cornerRadius": 2,
  "rows": [],
  "qr": {"content": "uniqueId", "position": "free", "sizePct": 0.35, "ecc": "Q"},
  "elements": [
    {"type": "ellipse", "x": 1, "y": 1, "w": 28, "h": 28, "stroke": "#111111", "fill": null},
    {"type": "field", "key": "headerId", "x": 4, "y": 4, "w": 20, "h": 5, "size": 8, "style": "bold"},
    {"type": "text", "text": "RNAlater", "x": 4, "y": 10, "w": 18, "h": 4, "size": 7}
  ]
}
```

## 设计器能力注册表

`app/services/label_design_schema.py` 是新的无 Qt 注册表，Claude Code 后续可以直接按它接 UI：

- `LABEL_SHAPES`: 矩形、圆角矩形、圆形
- `ELEMENT_TOOLS`: 文字、绑定字段、直线、矩形、椭圆/圆、图片、条码
- `FIELD_OPTIONS`: 标签可展示字段
- `QR_CONTENT_KEYS`: 二维码推荐内容字段
- `DESIGN_CAPABILITIES`: 当前能力摘要

原则：UI 菜单、属性面板、测试都应逐步改为从这个注册表读取，避免字段散落在多个文件里。

## 用户流程

1. 用户进入“标签打印”。
2. 在模板库点击“自由设计”或“新建自定义”。
3. 选择标签尺寸和外形：样品瓶常用矩形/圆角矩形，冻存管盖可用圆形。
4. 在画布上添加元素：
   - 固定文字：项目名、保存液、备注。
   - 绑定字段：编号、物种、日期、采集人、地点、经纬度。
   - 图形：边框、色块、圆形底纹、分隔线。
   - 二维码/条码：绑定唯一编号或编号头。
5. 保存为自定义模板。
6. 回到排版预览和打印，仍走同一个 `render_label_onto()`。

## 标签主要信息建议

样品瓶标准标签：

- `headerId`: 例如 FJ-XM-B2-DLC004，肉眼识别最快。
- `storage`: T95E / RNAlater 等保存方式。
- `shortDate`: 采集/拍摄日期段。
- `speciesName` + `latin`: 中文名和拉丁名，空间不足时只保留中文名。
- `family`: 科名，适合较大标签。
- `region` / `geoArea`: 地点。
- `lon` + `lat`: 经纬度，适合详细采集标签，不适合很小标签。
- `collectorLabel`: 采集人。

小管/冻存管标签：

- `headerId`
- `storage`
- `shortDate`
- `rnaPreservative`，只在 RNA 样品上显示
- QR 放 `uniqueId`

## 二维码内容建议

默认推荐 `uniqueId`。它最适合作为数据库/项目内唯一检索键，二维码不需要塞入过多文本。

可选策略：

- `uniqueId`: 默认，最稳。
- `headerId`: 空间极小时使用，但需要系统能从编号头回查。
- `shortDate` / `storage`: 只适合特殊流程，不建议单独作为二维码。
- JSON 内容：暂不作为默认。二维码越长越难扫，小标签上风险高；如果后续需要，可加一个 `qrPayloadMode=json` 扩展。

## Claude Code 后续任务

1. 把 `LabelDesignerDialog` 顶部的元素菜单、字段菜单、形状菜单改为读取 `label_design_schema.py`。
2. 在模板库卡片区把“新建自定义”视觉上改成“自由设计标签”，进入时打开当前设计器。
3. 增加尺寸/外形启动向导：矩形、圆角矩形、圆形；样品瓶、冻存管侧贴、冻存管盖三个预设。
4. 属性面板补齐图形细节：填充色、边框色、线宽、圆角、锁定比例。
5. 二维码支持 `free` 位置时作为可拖拽元素展示，仍保存到 `qr` 或迁移为专门 `barcode`/`qr` element。
6. 给圆形标签加裁切/安全区提示，避免文字贴边。
7. 所有新增能力先加测试：schema、normalize、render、designer dialog 操作。

## 验收标准

- 一个圆形标签模板可以保存、重新打开、预览、排版、打印。
- 自由元素在设计器和排版预览的位置一致。
- 二维码默认内容是 `uniqueId`，能在所有标签尺寸中保持可扫描大小。
- 没有选择样品时，模板预览也能用示例数据展示真实排版。
- 旧模板不带 `elements` 时行为不变。
