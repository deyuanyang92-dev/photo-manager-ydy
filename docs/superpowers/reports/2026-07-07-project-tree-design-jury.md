# 项目树页最优设计简报

> 首席设计师基于 6 软件 Feature Study + 5 位专家评审的裁决。每条结论均挂证据(专家发言+功能来源),可直接驱动 spec v4 改写。

## 0. 评分基线(先看数据指向哪)

| 维度 | 生物学家 | 分类学家 | LR 重度 | UX 设计师 | 架构师 | 均分 |
|---|---|---|---|---|---|---|
| recognize | 7 | 7 | 8 | 8 | 7 | **7.4** |
| import | 6 | 6 | 6 | 7 | 7 | 6.4 |
| preview | 6 | 7 | **5** | 6 | 6.5 | **6.1** ← 最弱 |
| navigation | **5** | 6 | **5** | 6 | 6 | **5.6** ← 次弱 |
| metadata | **5** | 6 | 6 | 6 | 6 | 5.8 |
| overall | 7 | 7 | 6 | 7 | 7 | 6.8 |

骨架(recognize 7.4)被认可;**preview/navigation/metadata 三项是公认短板**,杠杆点在此。

---

## 1. 共识问题(按提及次数排)

### P1. 盘符漂移后项目「断链死路」、无重链出口 — **4/5 提及**

| 专家 | 原话锚点 |
|---|---|
| 分类学家 | "盘符变了项目就'死'了,没有重链出口...我十年前的库早从 /mnt/g 挪到 /mnt/h...spec 一个都没有" |
| LR 重度 | "磁盘断链是死路一条,不是可恢复状态...**这是我十年用 Lightroom 最不能忍的退步**" |
| UX 设计师 | "重新检测失败时不止灰显死等,提供'指到新位置'入口"(top_improvement #2) |
| 架构师 | "WSL 盘符漂移下靠 _same_path 的项目身份会断链却没有 re-bind 入口...**不补就是第一批线上回归 bug**" |

**spec 现状**: §7 只有「盘未连接→卡片灰显+重新检测小按钮」,而重新检测=re-stat 同一路径。代码 `project_tree_view.py:1467-1471` 遇 `ProjectUnavailableError` 也只弹框让用户「接回数据盘」。§5 去重用 `project_service._same_path` 按路径判同一性 — 换盘符会被当新项目重复认领,**旧身份和历史全丢**。

### P2. 认领(adopt)无 dry-run 预扫描、信任靠「文字承诺」 — **4/5 提及**

| 专家 | 原话锚点 |
|---|---|
| 生物学家 | "认领没有 dry-run 预扫描报告——我每次点认领都在赌...面对的是几千张不可复现的原始 TIFF" |
| 分类学家 | "认领旧项目是'文字承诺'不是'写前预演'...对一个要把十年家底交出去的人,'相信对话框文字'是绝对不够的" |
| UX 设计师 | "认领(adopt)的可信度停在文字承诺,没有写盘前可见的 dry-run...这是把痛点②从'不敢点'翻成'放心点'的最高杠杆缺口" |
| 架构师 | top_improvement #2,同时指出 adopt 后的「僵尸工作区」会污染读 project.db 的跨工作区工具(汇总导出/分类名录在空 db 上静默出空 Excel) |

**spec 现状**: §6 只有大白话确认框 + 失败 `_rollback_adopt`。Feature Study 把 Symbiota 的 Pending Data Transfer Report 标为最高投入产出比的一招,但 spec 未吸收。

### P3. 预览停在「看一眼」、缺审片三件套 — **4/5 提及(含解码红线)**

三件套分述:

**(a) 缩略图大小滑块缺失 — 密度被写死**
- LR 重度: "通读全 spec 找不到一个用户可拖动的缩略图大小滑块...Lightroom/Bridge/Photo Mechanic/Capture One 全部把密度滑块做成一等公民"
- UX 设计师: top_problem #3a
- 代码现状: `project_tree_view.py:1267` 缩略图钉死 112x78、塞右栏 3x2 `QGridLayout`(`_make_media_preview_card`),主区是文字树

**(b) press-hold 不适合逐张审、缺 preview-in-place 同级兄弟**
- 生物学家: "Preview 是'空格 press-hold 100%(松手就没)+ Enter 模态大图(挡住列表)'...审一个断面的 200 张成片我得一直按着空格键?"
- LR 重度: "spec round 2 笔记自己提到 press-hold 不适合逐张审成片,却还是保留了它"
- UX 设计师: top_problem #3b
- digiKam Feature Study 把 F3 Preview-in-place(Icon-Area 与 Preview-Area 同级兄弟)列为 standout_idea

**(c) TIFF 全解打不到 §8 <100ms 红线 — 缺内嵌 JPEG 抽取**
- 生物学家: "§8 硬指标'单张解码<100ms'在几十 MB 的无损 TIFF 上几乎不可能——WSL/drvfs 9p 上必爆,spec 全文没提 digiKam 的'TIFF 内嵌 JPEG 抽取'"
- 架构师: top_improvement #3,与缓存统一一并提
- LR 重度: top_improvement #2

### P4. 元数据密度不够、缺表格视图/缩略图叠加层 — **2/5 直接提,与 P3 交叠**

- 分类学家: "元数据密度不够批量核名...导完 200 个旧标本,要一眼扫完它们的学名/采集人/站位有没有丢,不是一个个点...这跟没导一样累"
- UX 设计师: top_problem #3c "缩略图无叠加状态(GPS 图钉/合成色角/格式徽标),所有状态只活在右栏=眼睛在网格与右栏间反复跳"
- digiKam Feature Study 的 Table-View + 缩略图叠加层是直接范本

---

## 2. 共识改进 Top 5(按一致度+预期影响排)

### I1. ★断链重链 Locate/Update Path(4/5 一致,直击痛点①+部署刚需)

**借自**: Lightroom Classic 「Find Missing Folder / Update Folder Location」 + digiKam「每条 Collection 带稳定 UUID + Update Path 重绑按钮」 + Capture One「Locate…」

**改动**: 重新检测失败时给「指到新位置」入口 → 校验新路径下确实是同一项目(含 `_data/project.db` 或同名结构)→ 重写 `user_projects.json` 的 `directory` 字段,保留项目身份/设置继承链/recent 顺序。adopt 时给每条记录写稳定 id(`stat.st_uuid` 或 `sha256(resolved_path+mtime)`)进 `_data/project.db` 的 `project_meta`,后续 `discover_all_projects` 优先按 id 匹配、路径仅 fallback。

**预期影响**(架构师原话): "把'盘换号'这种灾难场景从'重选根+重新认领+历史丢失'降级成一次点击。这是把项目树从'扫描型工具'变'资产型库'的最后一块拼图。"

### I2. ★认领前 dry-run 预扫描报告(4/5 一致,直击痛点②信任)

**借自**: Symbiota「Skeletal vs Full Upload + 临时表 dry-run / Pending Data Transfer Report」

**改动**: 点候选后先异步扫一次目录(复用 `discover_workspace_candidates` + 封面 fallback 取图逻辑,**零写盘**),弹报告:"识别到 incoming-jpg 142 张 / results .tif 8 个 / .project-specimens.json 1 个 / 0 个 _data,认领将新建 `_data/project.db`,**原始文件 0 改动**;legacy sidecar 待首次进入时 migrate",再给 [认领]。

**预期影响**(UX 设计师原话): "痛点②从'文字承诺'升级为'写盘前看见真实计数',科学家对认领外部/旧文件夹的信任度质变;adopt 转化率与首次使用恐惧同时下降。" 顺带把 adopt-but-not-entered 中间态对用户显式化,降低下游工具静默空结果困惑。

### I3. ★预览三件套(4/5 一致,直击痛点③+§8 红线)

**借自**:
- (a) 滑块+防抖: Adobe Bridge「Content 面板缩略图大小滑块 + Grid Lock」
- (b) Preview-in-place: digiKam「F3 — Icon-Area 与 Preview-Area 同级兄弟」(standout_idea)
- (c) TIFF 内嵌 JPEG 抽取: digiKam「Preview shows embedded view if available」

**改动**:
- (a) 中网格底部加滑块(`setIconSize`+`setGridSize` 实时生效,配 `setUniformItemSizes(True)` 防重排抖动)
- (b) 把空格 press-hold 改为 **toggle Preview 态**:Preview 区作中网格同级兄弟(`QSplitter` 可拖分割条),显示当前照片大图+其归属 specimen 元数据,其它缩略图全程可见;空格/Esc/F3/再点回纯网格。补 2-up 并排对比(PM Preview Window 范本,挑片刚需)
- (c) `image_thumbnail.decode_image_data` 先抽 TIFF 内嵌 JPEG(Pillow 操作 `ExifIFD.TagJPEGInterchangeFormat` IFD),抽不到再降级全解;抽到的代理图进 `~/.cache` 同目录做二级缓存

**预期影响**(生物学家): "TIFF 不再卡(内嵌 JPEG 抽取比全解快一个数量级),审片节奏不被模态弹窗打断,密度可适配粗筛/清点两种场景。" 这是 §8 <100ms / 2000 项首屏 <200ms 红线在 TIFF-heavy 调查上**唯一能打住的招**(架构师判)。

### I4. 缓存统一为一层 LRU + 磁盘持久层 + invalidate 联动(架构师 #3,UX 间接支持)

**借自**: digiKam 中央缓存目录 + Symbiota 三档 URL Thumbnail Maintenance 批量补建

**改动**: `QPixmapCache` 作进程内**唯一** LRU,`image_thumbnail.py` 的 `_THUMB_CACHE`(OrderedDict 384 条)与 `~/.cache/.../covers/<sha256>` 改成 QPixmapCache 的 look-through(命中直接回,未命中才落盘/解码);成片重合成时同时清进程缓存与磁盘 covers。废弃独立 OrderedDict。

**预期影响**(架构师 top_problem #1 原话): "用户重合成一张成片后,网格(命中 QPixmapCache 旧 pixmap)与封面(命中 sha256 文件缓存)与工作台(命中 _THUMB_CACHE)三处显示不一致...这是 6 个月后'为什么缩略图不刷新'类 bug 的温床。" 统一后 eliminate 不一致根因。

### I5. 表格视图 + 缩略图叠加层(分类学家 #3 + UX #3c,2/5 直击元数据密度)

**借自**: digiKam「Table-View(缩略图成行+列可任意定制为 DB 字段)」+ digiKam「缩略图叠加层(地理图钉/格式/标题/星级/旋转)」+ Bridge「Metadata 面板多选显示共有字段、可勾选字段组」

**改动**:
- 顶栏视图切换器扩三态(卡片/树-网格/树-表格),表格列直接映射 `project.db specimen` 表(学名/UID/站位/经纬度/采集日期/采集人/合成状态),列可在「字段偏好」勾选(归档员看一组、鉴定员看另一组),行首小缩略图共用同一 `ThumbnailListModel`
- 网格态 Delegate paint() 叠加:GPS 图钉(有经纬度→显)、合成状态色角(已合成绿/待合成橙,对齐 §4.2 业务态角标)、格式徽标(TIF/JPG/JXL)

**预期影响**(分类学家): "导完旧库后能一眼扫完 200 个标本的学名/采集人/站位是否完整...对元数据完整性核验(标本馆的日常)是质变,且复用现有 specimen 表零新表。"

---

## 3. 分歧点(两方观点)

### 分歧 A. Smart Album / 智能节点的优先级 — **强调差异**

```
一方(现场生物学家,top_problem #1):
  "我一年跑 5 个调查、30 个断面,每周真正想做的不是'找一个已知项目',
   而是'列出所有待处理>0 的断面'...spec 把项目树钉死在'文件系统镜像'"

其他 4 位专家:
  未提及;改进预算全部投在三件套(断链/dry-run/预览/缓存)上

  裁决: 列 P2。复用现有 specimen/candidate 数据、不新增 DB 表、不破坏契约,
        是「远期活仪表盘」的入口,但不是上线刚需——生物学家每周省半小时,
        其他角色感知弱
```

### 分歧 B. 缓存架构 — 单层 LRU vs 多档分层 — **真实架构分歧**

```
一方(架构师):
  三套缓存(_THUMB_CACHE / QPixmapCache / sha256 文件缓存)各自为政是
  coherency 温床 → 收敛为单一 QPixmapCache LRU,废弃其它

另一方(Symbiota Feature Study + digiKam + Bridge):
  中央缓存目录 + 三档分辨率(thumbnail/web/large),各档独立索引适配不同场景
  (列表/网格/详情各取所需)

  裁决: 折中。QPixmapCache 作进程内唯一 LRU(解决一致性),
        但保留磁盘 covers 持久层(因封面需跨会话/盘掉线时可用,
        QPixmapCache 进程内无法跨会话)。两层通过统一 invalidate 信号联动
```

### 分歧 C. 卡片视图是否跨根显示全部已录项目(硬契约 vs 待定) — **契约歧义**

```
一方(UX 设计师,top_problem #1):
  "必须写成硬契约:'卡片视图永远显示全部已录项目,与当前展开的库根无关',
   否则痛点①会在另一视图复活"

另一方(spec 现状 + 其他专家默认):
  只在树视图左栏加「全部库根」折叠面板,卡片视图契约未明

  裁决: 采纳 UX 建议,写成硬契约。痛点①的根治必须覆盖两个视图,
        这是能把「标题级 bug 重新放回来」的唯一歧义
```

### 分歧 D. 断链重链的身份锚定方式(实现层) — **技术路径分歧**

```
一方(LR 重度用户):
  按文件夹身份(目录名 + 关键文件指纹,如 incoming-jpg 某张 JPG 的 sha256
  或 _data/project.db 的 mtime)做候选匹配

另一方(架构师):
  首次认领时记 stat.st_uuid 或 sha256(resolved_path+mtime) 写进 project_meta

  本质分歧: 卷 UUID(系统级,跨路径稳定但盘格式化即失)
           vs 文件指纹(应用级,跨重命名稳定但文件改动即失)

  裁决: 双轨。UUID 作快速自动复活(同卷重接),文件指纹作 fallback 匹配
        (跨卷迁移手选时校验)
```

---

## 4. 最终设计建议

### 4.1 三栏布局调整(ASCII 示意)

```
┌─────────────────────────────────────────────────────────────────────────────┐
│ 顶栏: [卡片|树-网格|树-表格]  搜索框  [只显示工作区 ●]  [刷新库]           │
│       非阻塞进度条「正在扫描更多库根…(N/M)」                                  │
├──────────────┬──────────────────────────────────────┬─────────────────────┤
│ 左栏(常驻)  │ 中栏(主区,Content 面板化)        │ 右栏(元数据)       │
│              │                                       │                     │
│ ★全部已录    │  QListView IconMode + Delegate 虚拟化 │ 选中节点 → 统计     │
│   (虚拟根,  │  + setUniformItemSizes(True) 防抖    │   (标本/成片/待处理 │
│    永远在)   │                                       │    + 含子级共 N)    │
│              │  缩略图叠加层: 📌GPS · 🟢合成 · [TIF] │                     │
│ 库根折叠分区 │                                       │ 选中单张 → specimen │
│  ┌ 雷州半岛  │  ┌────────────────────────────────┐  │   字段(学名/UID/   │
│  ├ 厦门湾 ★  │  │ Preview 区(同级兄弟,QSplitter)│  │   站位/经纬度/日期 │
│  └ …         │  │ 点缩略图→大图+元数据;Esc 回   │  │   /采集人/合成状态) │
│              │  │ 纯网格;支持 2-up 并排对比      │  │   EXIF 折叠次要    │
│ 智能节点 P2  │  └────────────────────────────────┘  │                     │
│  · 待处理>0  │                                       │ 多选 → 共有字段     │
│  · 未认领    │  底部: [滑块━━●━━] 缩略图大小        │   (灰显差异或「多种」)│
│              │  状态栏: [3 激活] [🗑清空] N/M 张    │  ⚙字段偏好          │
│ 历史/最近 5  │                                       │                     │
│ [<][>] Back │                                       │                     │
├──────────────┴──────────────────────────────────────┴─────────────────────┤
│ 状态栏: 当前路径 breadcrumb(每段可点回跳)                                  │
└─────────────────────────────────────────────────────────────────────────────┘
```

**左栏四分区常驻(关键)**: 「全部已录」虚拟根(Lightroom All Photographs / digiKam All Images 范本)+ 库根折叠分区 + 智能节点(P2)+ 历史/最近 5。**选任一库根 = 展开该子树,不清空、不替换其它段** — 这是痛点①在两个视图同时根治的硬契约。

### 4.2 三大诉求各采纳哪个功能

```
┌─────────────┬──────────────────────────────────────────────────────────────┐
│ 诉求        │ 采纳(主采 + 辅采)                                          │
├─────────────┼──────────────────────────────────────────────────────────────┤
│ 识别        │ 主采: digiKam 多根 Collections(local/removable/network 三类)│
│ recognize   │      + Lightroom「?」徽标 + Find Missing Folder 重链          │
│             │ 辅采: Bridge Favorites + 颜色标签(库根置顶,多根不爆屏)    │
│             │ 辅采: Specify 7「只显示工作区」boolean 开关(默认开)        │
├─────────────┼──────────────────────────────────────────────────────────────┤
│ 导入/认领   │ 主采: Symbiota Pending Data Transfer Report(零写盘预扫描)  │
│ import      │ 辅采: digiKam Add Collection vs Import 二分用语(不混「导入」)│
│             │ 辅采: Lightroom「Add」原地认领零搬运措辞                    │
│             │ 辅采: digiKam 每条 Collection UUID + Update Path 重绑        │
├─────────────┼──────────────────────────────────────────────────────────────┤
│ 预览        │ 主采: Bridge Content 面板滑块 + Grid Lock                    │
│ preview     │ 主采: digiKam Preview-in-place (F3) 同级兄弟(替代 press-hold)│
│             │ 主采: digiKam Embedded preview extraction(TIFF 内嵌 JPEG)  │
│             │ 辅采: digiKam 缩略图叠加层(GPS/合成色角/格式徽标)          │
│             │ 辅采: Photo Mechanic Preview Window 2-Up 并排对比           │
└─────────────┴──────────────────────────────────────────────────────────────┘
```

### 4.3 落地优先级

```
P0(本期必做,直击红线/部署刚需)
  1. 断链重链 Locate/Update Path + 稳定 id 锚定        ← 架构师判「第一批线上回归」
  2. TIFF 内嵌 JPEG 抽取(decode_image_data)          ← §8 <100ms 红线唯一解药
  3. 缩略图大小滑块 + Grid Lock                       ← 痛点③主区密度直接解
  4. Preview-in-place toggle 替代 press-hold           ← spec round 2 已自认缺陷
  5. Dry-run 预扫描报告(adopt 前)                    ← 痛点②信任,零契约改动

P1(下期,元数据密度 + 一致性)
  6. 表格视图(树-表格态)+ 缩略图叠加层              ← 元数据批量核名
  7. 缓存统一为一层 QPixmapCache + 磁盘 covers 持久层  ← 三套缓存 coherency 温床
     + invalidate 联动(成片重合成同时清)
  8. 卡片视图跨根硬契约写进 spec                       ← 防痛点①在卡片视图复活
  9. Adopt-but-not-entered 中间态卡片标注              ← 防跨工作区工具静默空结果
     + 汇总导出/分类名录在未 migrate 时灰显或提示
 10. ThumbnailWorker 生命周期 guard                    ← re-entrant on_activate
     (tab 走时 in-flight 请求处理:取消 or 排队)

P2(远期,活仪表盘)
 11. Smart Album / 智能节点(待处理>0/未认领/2026 航次)← 跨断面汇总,无新表
 12. Favorites + 颜色标签(库根置顶,调查类型区分)
 13. Back/Forward 历史栈 + 最近 5 工作区下拉
 14. 字段偏好(右栏可勾选 specimen 字段)
 15. 多选共有字段显示 + 设置继承链可视化
```

---

## 5. 一句话:spec v3 离「最优」还差什么

> 当前 spec v3 离「最优」还差**三块拼图** —— **盘符漂移下的断链重链**(部署刚需,架构师判为第一批线上回归)、**adopt 写盘前的 dry-run 预扫描报告**(信任从承诺升为证据)、**TIFF 内嵌 JPEG 抽取 + preview-in-place toggle + 密度滑块**(审片深度 + §8 <100ms 红线的真正解药);这三件套补齐,项目树才能从「文件系统镜像」跨到「资产型库」、从「能用」跨到「8 小时顺用」,而 Smart Album / 表格视图 / 缓存统一是 P1-P2 把它进一步推向「活的工作仪表盘」的增量。

---

### 附:证据索引(专家→改进映射)

| 改进 | 生物学家 | 分类学家 | LR 重度 | UX 设计师 | 架构师 |
|---|---|---|---|---|---|
| I1 断链重链 | — | ✓#2 | ✓#1 | ✓#2 | ✓#1 |
| I2 dry-run | ✓#2 | ✓#1 | — | ✓#1 | ✓#2 |
| I3 预览三件套 | ✓#3 | — | ✓#2,#3 | ✓#3 | ✓#3(解码+缓存) |
| I4 缓存统一 | — | — | — | — | ✓#1,#3 |
| I5 表格+叠加层 | — | ✓#3 | — | ✓#3c | — |
| (Smart Album) | ✓#1 | — | — | — | — |