# 项目树重设计 — Lightroom 编目式三栏 (v5，并入 round 3 评审 + round-2 复审外科手术)

- **日期**: 2026-07-07 · **修订**: v5 — 外科手术修复 round-2 复审揪出的 5/5 一致 spec bug + 2 个承重决策钉死（不开团，分类学家判这些是 TDD 期精度非评审议题）
- **状态**: 设计收敛，待「执行」令
- **范围**: `app/views/project_tree_view.py` 重构 + `image_thumbnail.py` 拆分 + `db_manager.open_project_db` 加固 + `settings.py` 新键 + 认领服务 + 断链重链（stable-id lifecycle 修复）
- **不动**: `_data/project.db` schema、`enter_workspace` 签名/契约、WoRMS/标签/协作等其它视图

> **round-2 复审成效（v3→v4）**：三块 round-1 短板全反弹（预览 6.1→8.3 / 导航 5.6→7.6 / 元数据 5.8→7.2 / 导入 6.4→8.6 / 总体 6.8→7.9）。round-1 的 15 条问题 8 addressed / 4 partial / 3 not（全诚实标 P1 deferral）。报告 `docs/superpowers/reports/2026-07-07-project-tree-design-jury-r2.md`。

> v4→v5 外科手术修正（round-2 4/5 要再迭代，但全强调「窄/钉文字」，非返工）：
> ① **stable-id lifecycle 5/5 bug 修复**（round-2 全员命中）— v4 把 file_fp 设成 `_data/project.db` 的 sha256，但 project.db 是**活库**（specimen 写入即变）→ §13 跨卷迁移红线假绿。且身份锚存到了会失联的盘上（路径死时读不到 project.db）。v5 改：fp = `_data/.identity` sentinel 文件（adopt 写一次永不改）sha256；**stable id 镜像进 `user_projects.json`**（registry 持有，死盘也能读）；老 db 首次 discover 静默 backfill。见 §13.1-13.3 重写。
> ② **缓存契约 §3 vs §10 自相矛盾钉死**（架构师判承重决策）— v3 写「废弃手搓缓存」却留 `_THUMB_CACHE`。v5 钉死：grid 用 QPixmapCache；`_THUMB_CACHE` 显式留作 labels/workbench/封面 fallback；invalidate 联动归 P1 task#X 并指明触发源（成片重合成）。见 §3/§10 改。
> ③ **grid worker re-entrant 生命周期指定**（架构师判承重决策，撞 shutdown-lock-leak 教训）— v5 钉死：每激活新建 worker + 旧 worker quit-wait + in-flight 请求 cancel/drop + 信号生命周期。见 §7 改。
> ④ **承认缩略图徽标是 round-1 ③c 残留**（UX 设计师 + 分类学家）— v4 把「中网格三件」第三件静默换成 TIFF 性能件，写得像「全解决」。v5 补回 GPS 图钉 / 合成色角 / 格式徽标（P1，不再假装解决）。

> v3→v4 关键修正（round 3 评审团 4/5 共识，每条挂证据见 jury 报告 §1-§2）：
> ① **断链重链 Locate/Update Path**（P0，架构师判「第一批线上回归」）— 盘符漂移（WSL `/mnt/g→/mnt/h`、外置盘换号）下，现状「重新检测 = re-stat 同一路径」永远失败，已录项目永久消失。新增右键「指到新位置」+ 校验同一项目 + 重写 `user_projects.json` 的 `directory`，保留身份/继承/recent 顺序。adopt 时写稳定 id 双轨（卷 UUID 快速自动复活 + 文件指纹跨卷迁移 fallback）进 `_data/project.db`，`discover_all_projects` 优先按 id 匹配、路径仅 fallback。
> ② **adopt 写盘前 dry-run 预扫描报告**（P0，痛点②信任）— 借 Symbiota Pending Data Transfer Report：点候选后先零写盘扫一次目录，弹「识别 N JPG / M TIFF / 0 _data，将只建 _data，原始文件 0 改动」再给 [认领]。信任从「对话框文字」升到「写前看见真实计数」。
> ③ **预览三件套**（P0，痛点③ + §8 `<100ms` 红线）— (a) 缩略图大小滑块 + `setUniformItemSizes(True)` Grid Lock（现钉死 112px 塞右栏 3×2）；(b) Preview-in-place toggle（空格/Esc/F3，Preview 区作中网格同级兄弟 `QSplitter`，替代 round 2 自认缺陷的 press-hold）；(c) **TIFF 内嵌 JPEG 抽取**（`decode_image_data` 先抽 `ExifIFD.TagJPEGInterchangeFormat` 内嵌 JPEG，抽不到再降级全解）—— 这是 §8 单张 `<100ms` 红线在 TIFF-heavy 调查上**唯一能打住的招**，比全解快一个数量级。
> ④ **卡片视图跨根硬契约**（分歧 C 裁决）— 写死「卡片视图永远显示全部已录项目，与当前展开的库根无关」，否则痛点①在卡片视图复活。
> ⑤ 缓存统一（P1，记入 §10 风险表，本期仅记不强制）：`QPixmapCache` 进程内唯一 LRU + 磁盘 covers 持久层 + invalidate 联动，废弃 `_THUMB_CACHE` 独立 OrderedDict。

> v2→v3 关键修正（round 2 代码核实）：
> ① adopt **不再薄封装 enter_workspace**——二者是不同操作。adopt = `open_project_db(create=True)`（只建 `_data/`，不经 `ensure_project_dirs/migrate`）+ seed + register + record；日后"进入"才走 `enter_workspace` 建 dirs + migrate。消除"薄封装+绕开 migrate"矛盾。
> ② **删第④源 catalog**——它是 per-survey-root，无法反查 survey_root（循环依赖）；`user_projects.json` 即真相源，第①源已含全部已 enter 工作区。
> ③ `db_manager.open_project_db` **加 try/except 包 ensure_schema**，失败 `conn.close()` + 清 `-wal/-shm` 再 raise；`_rollback_adopt` 端到端 rmtree；§8 红线测试改端到端（不再恒真）。
> ④ 中网格改 **QListView + QAbstractListModel + QStyledItemDelegate** 虚拟化（替代手搓 QGridLayout+QLabel），引用项目已有 `taxonomy_input.py` 范式。
> ⑤ 卡片/树**统一选择模型 + 单搜索框**；徽标去琥珀改业务角标；封面 5 级 fallback；右栏默认标本元数据；线程模型拆 run()/moveToThread 两套。

## 0. 术语表

| 术语 | 定义 | 判据 |
|------|------|------|
| **库根** (library root) | 扫描起点目录，多根集合 `settings.library_roots` | 用户配置 |
| **工作区** (workspace) | 任意可拍照目录 | `_data/project.db` 存在 |
| **项目** (project) | `enter_workspace` 登记过的工作区（`user_projects.json` 有条目） | 项目 ⊆ workspace |
| **文件夹** (folder) | 树中非工作区节点（区域/容器） | 无 `_data/project.db` |
| **候选** (candidate) | 旧/外部可认领文件夹 | marker 目录≥2 或 incoming-jpg 含 JPG |

`discover_all_projects` 返回 `list[WorkspaceEntry]`，字段含
`kind: 'workspace'|'folder'|'candidate'|'unavailable'`。`classify_project_dir` 实时重算优先级
`unavailable > workspace > candidate > folder`，与源拼接顺序无关。

## 1. 问题陈述

`project_tree_view.py` 已是 rooted/flat 双分支，无根目录时把已录项目作扁平列表展示（44815a3 已修"无根不识别"）。**剩余真问题**：

1. **rooted 模式盲区** — 选了调查根后，不在该树下的其它已录项目在树视图消失。
2. **无认领动作** — `is_workspace_candidate` 只挂徽标，无把候选/外部文件夹实例化为工作区的动作。
3. **预览弱** — 缩略图塞在右栏 6 张小图（手搓 QGridLayout+QLabel），主区是文字。

## 2. 目标 / 非目标

**目标**：回归 Lightroom/Bridge/Capture One 共性（左树 + 中虚拟化缩略图网格 + 右元数据）；
卡片/树统一选择模型；识别零配置；一键认领；不破坏 `enter_workspace` 契约。

**非目标**：云同步；项目级权限；全盘爬虫；重做工作区内部；新增 DB 表；本期不做"未编号散片追踪"（点名 §12 后续）。

## 3. 架构

```
ProjectTreeView (重构) — 卡片/树统一选择模型,单搜索框
  ├─ 顶栏: [卡片|树切换] [🔍搜索]              ← 主操作只这两个
  │         ⋯ 菜单: [+导入文件夹] [库根管理] [刷新库]
  ├─ QStackedWidget (两视图共享选择集/搜索词)
  │   ├─ 卡片视图 = 树的跨调查顶层折叠态
  │   └─ 树视图 = QSplitter(左树 │ 中网格 │ 右元数据)
  └─ 复用 enter_workspace_requested / ctx 契约不变

中网格 (虚拟化,引用 taxonomy_input.py 范式):
  QListView(IconMode) + ThumbnailListModel(QAbstractListModel) +
  ThumbnailDelegate(QStyledItemDelegate: paint() 内 QPixmapCache.find→命中 drawPixmap/
                    未命中投递 ThumbnailWorker→回主线程 dataChanged 重绘该 cell)
  QPixmapCache.setCacheLimit(200*1024) 全局 LRU(★v5 缓存契约钉死,见下)

★v5 缓存契约(修 round-2 架构师点名的 §3 vs §10 自相矛盾):
  - grid(项目树中网格) → QPixmapCache 进程内 LRU(唯一给 grid 用)
  - _THUMB_CACHE(OrderedDict,image_thumbnail.py 现有) → 显式【保留】,给 labels/workbench/封面 fallback 用
    (不「废弃」,v3 写「废弃手搓缓存」却留它是矛盾之源 —— 现明确两套各管各的场景)
  - ~/.cache/.../covers/<sha256> → 磁盘持久层(跨会话/盘掉线可用,QPixmapCache 进程内无法跨会话)
  - invalidate 联动(P1 task#X,必须指明触发源): 成片重合成时同时清 QPixmapCache(进程) +
    covers(磁盘) + 对应 _THUMB_CACHE 条目;否则三处显示不一致(round-2 架构师判「6 个月后缩略图不刷新 bug 温床」)

image_thumbnail.py 拆分:
  decode_image_data(path, max_size) → Optional[QImage]   # 线程安全,无 GUI 对象
  make_pixmap(qimage) → QPixmap                          # 仅主线程
  decode_image_thumbnail(...) → QPixmap                  # 保留,主线程同步用
  # ★v4: decode_image_data 内部 TIFF 路径先抽内嵌 JPEG(ExifIFD.TagJPEGInterchangeFormat
  #        IFD),抽不到再降级全解。比全解几十 MB TIFF 快一个数量级 —— §8 单张<100ms 红线
  #        在 TIFF-heavy 调查上唯一能打住的招(digiKam "Preview shows embedded view")。
  #        抽到的代理图进 ~/.cache 同目录二级缓存(磁盘 covers 持久层,P1 统一)。

db_manager.open_project_db 加固:
  try: ensure_schema(conn) except: conn.close()+清 wal/shm; raise   # 防 orphan conn 持锁

服务:
  project_tree_service: discover_all_projects(ctx) / classify_project_dir(directory)
  library_roots_service: 库根 CRUD (settings 持久化)
  project_adopt_service.adopt_project(): 显式最小操作(不调 enter_workspace)

线程模型 (拆两套,与代码库现状对齐):
  ① ProjectDiscoverWorker / 封面 ThumbnailWorker (一次性) = QThread 子类重写 run()
     finished → quit+deleteLater (照搬 monitor_scan_worker.py)
  ② 中网格按需 ThumbnailWorker (长驻) = QThread + worker QObject moveToThread + exec()
     主线程 QMetaObject.invokeMethod(worker,'decode',QueuedConnection) 投递
     worker 回 QImage 信号 → 主线程 make_pixmap
     on_deactivate/closeEvent 必须 quit()+wait() (见 shutdown-lock-leak 教训)
```

## 4. 组件

### 4.1 顶栏（收敛）

主操作只 `[视图切换][搜索]`，其余收进右上角 `⋯` 菜单。卡片/树**共享单一搜索框 + 统一选择集**
（选中一张卡片 = 选中该调查根节点，树视图自动展开到该节点；搜索词双向同步）。

`⋯` 菜单：`+导入文件夹` / `库根管理` / `刷新库`。

**★v4 硬契约（分歧 C 裁决，防痛点①在卡片视图复活）**：卡片视图**永远显示全部已录项目**，与当前展开/选中的库根无关。选某库根 = 在树视图展开该子树 + 卡片视图滚动定位到该项目，**不清空、不替换**卡片全集。UX 设计师：「必须写成硬契约，否则痛点①会在另一视图复活」—— 这是能把已修的标题级 bug 重新放回来的唯一歧义。

### 4.2 卡片视图（= 树的跨调查顶层折叠态）

```
┌──────────────────┐
│   封面缩略图      │  5 级 fallback (见下)
│   (异步,全局缓存) │
│            [8]   │  ← 业务态角标: 待处理总数>0 时红数字
├──────────────────┤
│ 三门湾-B2   🟢   │  名称 + 可用性点(绿=可用/灰=盘未连接)
│ 142 标本         │  标本数
│ 上次: 2026-07-04 │  ISO 绝对日期
│ [进入工作区]      │  常驻主操作
└──────────────────┘
未实例化卡: 无绿点 + 常驻 [认领] 二级按钮(描边) + 图标变体区分类型
hover 仅增强视觉,不暴露入口
```

**徽标四维度正交**（颜色=可用性 / 图标=类型 / 按钮=动作 / 角标=业务态）：
- 🟢 绿点 = 工作区可用；⚫ 灰（卡片整体灰显）= 盘未连接。**删除琥珀色**。
- "未实例化"用图标变体（可识别旧项目→"识别"徽章；外部文件夹→无徽章）+ 常驻 [认领] 体现。
- 业务态角标：待处理总数>0 挂红数字（如 `[8]`），树节点同步高亮。

**封面 5 级 fallback**（删 v2"最近成片"误导；.zip 不解压当封面）：
1. `project_settings.cover_image`（用户右键"设为封面"）
2. 该断面有缩略图的标本里按 `scientific_name` 分组取代表图（从 `project.db` specimen 表）
3. `results/` 下最近 `.tif`（**明文跳过 `.zip`**，jxl 包不解压）
4. `incoming-jpg/新拍JPG` 最近一张 `.jpg`（空工作区唯一可视化来源）
5. 占位图带项目首字母
用户可勾选"自动用最新成片"保留旧末位行为。封面缓存全局（见 §11）。

**键盘导航（全局，覆盖两视图）**：`←→↑↓` 移焦点 / `Enter` 进入工作区或大图预览 / `空格` **toggle Preview-in-place**（v4：替代 round 2 自认缺陷的 press-hold。空格/F3 切换 Preview 态：Preview 区作中网格同级兄弟 `QSplitter`，显示当前照片大图 + 其归属 specimen 元数据，**其它缩略图全程可见**，审片节奏不被打断；支持 `2-up` 并排对比，借 Photo Mechanic Preview Window）/ `Ctrl+F` 聚焦搜索 / `Esc` 退出 Preview 回纯网格 / `Ctrl+A` 全选当前页。

**★v4 缩略图大小滑块**（痛点③主区密度直解，借 Bridge Content 面板滑块 + Grid Lock）：中网格底部加滑块，`setIconSize`+`setGridSize` 实时生效，配 `setUniformItemSizes(True)` 防拖动时重排抖动。粗筛拉大看清成片、清点拉小一屏多看，密度不再被写死（现状 `project_tree_view.py` 钉死 112×78 塞右栏 3×2 `QGridLayout`）。

### 4.3 树视图三栏

- **左树**：顶部固定"库根"折叠面板，列出所有根（每根=一棵顶层子树），点根=展开其树。
  "全部库根"=所有根都展开（**是状态不是模式**，消除 v2 §12 未决项）。
- **中网格（虚拟化）**：
  - 点节点 → 默认显示该节点直接子层照片 + 每子文件夹一张代表图。
  - **"含子文件夹"默认值按节点类型**：调查根/区域容器（子节点全是工作区）默认**开**（一眼看到全调查代表照片）；单个断面工作区默认**关**（避免被 incoming-jpg 等内部目录污染）。
  - **虚拟滚动 + 滚到底自动 fetchMore**（`canFetchMore/fetchMore`，替代手动"+200"按钮）。小节点（<200 张）直接全显；大节点步长自适应（首次 +200，连续递增 +400/+800），超大附"全部加载"二次确认。单节点无硬上限。
  - 顶部 **breadcrumb** `库根/调查A/断面b`，每段可点回跳。双击子文件夹代表图=切网格到该子节点；右键缩略图"在目录树中显示"=反向展开树并选中；`Backspace/Esc`=返回上级。
  - 分页提示（仅大节点）固定行 `显示 1-200 / 共 1234 张 · [全部加载]`，**显式防"照片丢了"焦虑**。
  - **★v5 缩略图叠加层徽标（P1，承认是 round-1 ③c 残留，v4 误把它静默换成 TIFF 性能件）**：ThumbnailDelegate paint() 在每张缩略图上叠加 ①GPS 图钉（有经纬度→显，无→不显）②合成状态色角（已合成=绿点 / 待合成=橙点，对齐 §4.2 卡片业务态角标）③格式徽标（TIF/JPG/JXL）。理由（UX 设计师+分类学家）：所有状态只活在右栏 = 审片时眼睛在网格与右栏间反复跳；叠加层让网格自身就是信息载体，「一眼扫完一张图有没有 GPS/几颗星/什么格式」不用点开。借 digiKam 缩略图叠加层。
- **右元数据（默认标本字段，非 EXIF）**：选中节点统计（标本/成片/待处理 + 灰字副 `(含子级共 N)`）；
  选中单张照片优先显示其归属 specimen（从文件名反查 uniqueId→specimen 表）：**学名/标本UID/站位/经纬度/采集日期/采集人/合成状态**；
  EXIF 降为**可折叠次要分组（默认折叠）**。

### 4.4 库根管理（唯一入口，从 `⋯` 菜单打开）

每行根目录后显示 `扫到 N 个工作区 · M 个候选`，悬停/点击展开列具体路径。自动迁移来的根标注 `(自动添加,来自旧设置)`。

[加目录]：加更高层目录时**护栏**拦截驱动器根（`/`、`C:\`、单层 `/mnt/g`）、`Desktop`、`Downloads`、用户主目录。
[移除]：确认提示 `将不再扫描此目录，已发现的 N 个项目仍保留`；仅从 `settings.library_roots` 删路径字符串，
**绝不触碰 `user_projects.json`，不删盘上文件**。

**`is_drive_root_or_system_dir` 算法（跨平台）**：
`resolved = Path(directory).resolve()`；`resolved.parent == resolved` → 驱动器根；
`resolved == Path.home() / resolved in (Path.home()/'Desktop', Path.home()/'Downloads')` → 系统目录；任一命中拒绝。

## 5. 识别模型（三源并集，删 v2 第④源）

```python
def discover_all_projects(ctx) -> list[WorkspaceEntry]:
    registered = list_projects(default_user_projects_json_path())   # ① 已录(永远在,含全部已 enter 工作区)
    discovered = []
    for root in library_roots(ctx.settings):                        # ② 库根深度2扫描
        discovered += pts.discover_workspace_candidates(root, max_depth=2)
    manual = ctx.settings.manual_project_folders                     # ③ 手动加外部
    merged = merge_dedup([registered, discovered, manual], key=_same_path)  # 复用 project_service._same_path
    return stable_recent_first(merged, registered_order)            # 保留"最近 reverse() 置顶"
```

**删第④源 catalog 的理由**：`workspaces` 表存在于每个 survey_root 自己的 `_data/project.db`
（`project_catalog_service.py:196,229` 都 `open_project_db(root, create=True)`），即 catalog 是
per-survey-root 的、没有全局 catalog。要遍历 catalog 拿 survey_root 必须先知道 survey_root 列表——
循环依赖。而 `user_projects.json` 的 `root` 字段（`record_recent_workspace:410` 已写）就是 survey_root 集合，
第①源 `list_projects` 已含全部已 enter 工作区，catalog 对发现是冗余。

**库根默认值（破循环依赖 + 零 stat）**：
- `settings.library_roots is None`（未配置）→ 每次 `on_activate` **推导默认值不写盘**。
  推导规则（**纯字符串零 stat**，直接读 `user_projects.json` 的 `directory` 取 `parent`）：
  - 若 **≥2 个已录项目共享同一父目录**（且该父非驱动器根/系统目录，复用 `is_drive_root_or_system_dir`），
    则把**共享父**作库根（保留"一个调查根自动发现所有断面"价值，不拆成多根）；
  - 否则各已录项目**所在目录本身**作库根。
  存在性检查挪进 `ProjectDiscoverWorker`（与 §7 registered 阶段零 stat 一致）。
- `settings.library_roots == []`（显式清空）→ 不再推导，只显已录。
- 非空 → 按列表。库根管理"移除"设 `[]` 才能区分。

**`merge_dedup`** 统一复用 `project_service._same_path`（已处理跨平台大小写/symlink）。

**`discover_workspace_candidates` 修正**：`is_workspace_candidate` 改单次 `os.scandir` 拿 entries 名字集合
再 `in` 判断（现状 `project_tree_service.py:118-131` 对 5 个 marker 名各 `Path.exists()` = drvfs 5 次 9p 往返）。
对 `root==工作区` 的起始节点跳过自身只扫子层。

## 6. 导入 / 认领（显式最小操作，不调 enter_workspace）

> **关键设计**：adopt 与 enter 是**两个不同操作**。
> - **adopt**（认领）= 最小识别：只建 `_data/project.db` 让文件夹被识别为工作区 + seed 设置 + 登记。**不建 incoming-jpg/results（首次拍照/合成时按需建），不经 migrate（保留文件夹原貌）**。
> - **enter**（进入）= 激活拍照：`enter_workspace` 建 dirs + migrate legacy（此时用户已主动开工，清理 sidecar 合理）。
> adopt 后用户点"进入"才走 enter_workspace。

```python
def adopt_project(ctx, directory, *, name=None, inherit_from=None) -> AdoptResult:
    directory = normalize_path(directory)
    if not Path(directory).is_dir(): raise ProjectUnavailableError(directory)
    if is_drive_root_or_system_dir(directory): raise InvalidAdoptTarget(directory)
    if (Path(directory) / "_data" / "project.db").exists():
        return AdoptResult("already")                                  # 幂等
    root = _resolve_inherit_root(directory, inherit_from)
    try:
        # 1. 只建 _data/project.db —— open_project_db(create=True) 内部:
        #    require_project_root + mkdir(_data 叶) + connect + ensure_schema + cache
        #    【不调 ensure_project_dirs, 不建 incoming/results, 不经 migrate】
        open_project_db(directory, create=True)
        # 2. seed 设置 —— db 已存在,直接写 code_labels/personnel (在 enter/migrate 之后也无矛盾)
        _apply_inherited_settings(directory, inherit_from, name)
        # 3. 登记 survey catalog (若 root 已知且 != directory)
        if root and normalize_path(root) != directory:
            register_workspace(root, directory, role="workspace", name=name or Path(directory).name)
        # 4. 记 user_projects.json (发现的真相源)
        record_recent_workspace(default_user_projects_json_path(), directory, root=root)
    except Exception:
        _rollback_adopt(directory)                                     # 见 §7
        raise
    pts.clear_project_tree_cache(None)                                 # 全清
    return AdoptResult("adopted")
```

`_apply_inherited_settings`：从 `inherit_from` 沿继承链查 `project_settings_service.get_effective` 取
code_labels/personnel，写新 db（仅非空值）。**不调 `ensure_project_dirs/seed_region_settings`**（它们触发 migrate）。

**★v4 认领前 dry-run 预扫描（零写盘，借 Symbiota Pending Data Transfer Report）**：
点候选后**先异步扫一次目录**（复用 `discover_workspace_candidates` + 封面 fallback 取图逻辑，全程零写盘），
弹报告让用户**写盘前看见真实计数**，再给 [认领]：

```python
def prescan_project(directory: str) -> PrescanReport:
    """零写盘预扫描: 数 incoming-jpg/*.jpg、results/*.tif(跳 .zip)、_data 是否已存在、
    legacy sidecar(.project-specimens.json 等)个数。复用单次 scandir(见 Task 4 fuse)。
    返回结构化报告供确认对话框渲染,不创建任何文件。"""
    # 单次 scandir 主体 + 必要时单次 scandir(incoming-jpg)
    ...

class PrescanReport:
    jpg_count: int          # incoming-jpg/新拍JPG 下的 .jpg/.jpeg
    tiff_count: int         # results/*.tif(明文跳 .zip,jxl 包不解压)
    has_data: bool          # _data/project.db 已存在? → 走幂等 already 路径
    legacy_sidecars: list[str]  # .project-specimens.json 等 marker(认领不动,首次进入时 migrate)
    estimated_specimens: int # legacy sidecar 解析出的标本数(若可读),仅供展示
```

**认领确认对话框（v4：写前看见真实计数，去技术化）**：
报告头（灰底）：
`扫描「B2」: 142 张 JPG · 8 个 TIFF · 0 个 _data · 1 个 legacy 清单(含 138 条标本记录)`
正文：
`认领将【只新建一个 _data 子目录存项目数据】，你的原始照片（含 incoming-jpg/results 里已有的 142+8 个）
一个都不会动、不会重命名、不会移动。legacy 清单待你首次「进入工作区」时按标准流程 migrate。`
`project.db` 藏进"高级"折叠。继承行可展开：默认 `继承自: <最近有设置的祖先名>`，点"查看"展开完整继承链
`断面b→区域A→项目根` + 将继承的具体字段（省份/样地/采集人）。可写性检查用 `mkdir(directory/_.writetest)→rmdir`，
失败降级为"尽力预检，失败走 enter 标准错误路径"。

> 评审团依据：分类学家「对一个要把十年家底交出去的人，'相信对话框文字'是绝对不够的」；UX 设计师「痛点②从
> '文字承诺'升级为'写盘前看见真实计数'，科学家对认领外部/旧文件夹的信任度质变」。零契约改动，仅多一个零写盘
> 预扫描步，不破坏 adopt 不走 migrate 的红线。

**区域确认框逻辑修正**（`_enter_selected:1441-1450`）：仅当"选中是用户手动设的库根"**或**"路径下有
≥3 个已是工作区的子节点"才弹；选中断面（有子文件夹但非工作区）直接进不拦。

## 7. 数据流 / 错误处理

```
on_activate
  → 同步取 registered (纯字符串零 stat: 读 user_projects.json directory 字段去重)
  → 卡片/树渲染 registered (典型<50ms,一次性同步)
  → ProjectDiscoverWorker (一次性 QThread run()) 后台扫库根候选 → 差集追加(淡入)
    顶部非阻塞 "正在扫描更多库根…" 进度条
  → 封面 ThumbnailWorker (一次性) 异步补(全局缓存,占位优先)

切树视图 → scan_tree(展开的库根) → 点节点 → 长驻 ThumbnailWorker (moveToThread) 按需取直接子层
认领 → adopt_project → clear_project_tree_cache(None) → 重算
```

**`_rollback_adopt`（端到端，处理 orphan conn + 半残 _data）**：
```
def _rollback_adopt(directory):
    db_manager.close_project_db(directory)        # 1. pop _db_cache 里的 conn + close
    #    (open_project_db 的 try/except 已保证 ensure_schema 抛错时 conn 已自关闭,
    #     此处对"成功入 cache 后失败"场景兜底)
    _data = Path(directory) / "_data"
    if _data.exists():
        shutil.rmtree(_data, ignore_errors=False) # 2. 删 _data; adopt 未走 migrate 故无 _data/legacy/ 用户文件
        if _data.exists():                         # 3. 半残(句柄残留)→ 二次校验
            raise AdoptRollbackError(f"_data 无法清理: {_data}")
    # 任一步失败向上抛,UI 不显示成功;盘掉线致 rmtree 抛 OSError 不吞错,报告"接回后手动删除"
```

> **数据保护**：adopt 全程不调 `ensure_project_dirs/migrate_legacy_metadata`，故 `_data/` 下**只有**新建的
> `project.db`（+ 可能的 `-wal/-shm`），**绝不会**含 `_data/legacy/` 里被移动的用户原文件。rmtree 安全。

**`ProjectDiscoverWorker` 竞态**：启动快照 `user_projects.json` 签名(mtime+size)，完成时若变了 → 丢弃重跑；
完成后只补当前全集没有的候选（差集追加），不动已存在卡片选中状态。

| 场景 | 行为 |
|------|------|
| 盘未连接 | 卡片灰显"盘未连接" + 卡片"重新检测"小按钮，不抛 |
| 超大目录扫描 | 异步 + 进度条 + 可取消；超时降级部分结果 |
| adopt 失败 | `_rollback_adopt` 关连接→rmtree→抛错，无半成品 |
| 缩略图解码失败 | 占位图（已有降级） |
| 库根不可访问 | 跳过该根，记日志，其它根继续 |
| 选中区域容器 | 仅明显容器(≥3 子工作区/手动库根)才弹确认 |

## 8. 测试（去伪命题，加端到端 + 非 mock 烟测）

可测硬指标：
1. `on_activate` 在库根含 1000+ 目录 fixture 下 **<500ms**（monkeypatch stat 内存模拟）。
2. **非 mock 烟测**：真实 `/tmp`（或 CI ext4）下建 1000 目录 fixture，`discover_workspace_candidates(root, max_depth=2) <800ms`（验证 `is_workspace_candidate` 单次 scandir 改造，不掩盖 9p 往返累积）。
3. `ThumbnailWorker` 解码单张 **<100ms** 且不在主线程（`QThread.currentThread() != QCoreApplication.instance().thread()` 断言）。
4. worker 运行期间主线程仍处理事件（`QSignalSpy` 监听定时器）。
5. **2000 项 fixture 下中网格首屏 paint <200ms**（虚拟化验证）；切节点旧 `QPixmapCache` 不残留。

**红线测试**（每条 Windows 风格 + WSL/drvfs 路径两种 normalize 各跑一次，防假绿）：
- adopt 幂等（`_data/project.db` 已存在 → 不重建）。
- **adopt 回滚端到端**：monkeypatch `ensure_schema` 抛 `sqlite3.OperationalError(locked)` →
  `_rollback_adopt` 后 `_data` **完全不存在**（无 `.db/.db-wal/.db-shm`），且 `shutil.rmtree(_data)` 不抛 `PermissionError`（验证无 OS 句柄残留）。
- adopt 外部空文件夹后该文件夹下**仅 `_data/` 一个子目录**（无 `incoming-jpg`/`results`）。
- adopt 后根目录 `.project-specimens.json`/`.specimen-log.json` 的 **sha256 与位置均不变**（绕开 migrate）。
- adopt 后 `user_projects.json` 恰好一条该 directory 条目、`root` 字段非空。
- 移除库根**不写** `user_projects.json`，盘上文件不删。
- 设了 `project_tree_root` 后，已录项目仍出现（rooted 盲区回归）。
- 最近项目 `reverse()` 置顶不被 discovered/manual 挤后。
- worker 内**绝不构造 QPixmap**（monkeypatch 断言 `QPixmap.__init__` 在 worker 线程不被调用）。
- `cover_image` 指向项目外路径时不被读取，降级 fallback（相对路径→项目根解析；绝对路径→`relative_to` 校验失败降级）。
- **db_manager.open_project_db ensure_schema 抛错**：conn 已 `close()`，`_db_cache` 不含该键，`-wal/-shm` 已清。
- **★v4 adopt dry-run 零写盘**：`prescan_project(d)` 返回报告后，断言目录下文件 sha256 全集合 + 目录结构树**与 prescan 前字节一致**（零写盘，连 `_.writetest` 都不留）；报告 jpg/tiff 计数与真实 fixture 一致。
- **★v4 TIFF 内嵌 JPEG 抽取**：fixture 一张含内嵌 JPEG 的 TIFF（Pillow 构造或取真实成片）→ `decode_image_data` 单张 wall clock `<100ms`（§8 硬指标），且 monkeypatch `ImageReader.read`/Pillow decode 验证走了内嵌 JPEG 路径而非全解；无内嵌 JPEG 的 TIFF 降级全解不崩。
- **★v5 断链重链（修 round-2 5/5 假绿 bug）**：①adopt 后 `_data/.identity` sentinel 存在 + `_data/project.db` 与 `user_projects.json` 条目**两处**都写 `volume_uuid`/`identity_fp`；②**关键防假绿**：adopt 后往 project.db 插一条 specimen（活库写入）→ `.identity` 的 fp **不变**（sentinel 不动），registry 镜像的 fp 也不变（v4 写法此处会变 → 假绿）；③模拟盘符漂移（改 `user_projects.json` 的 `directory` 到不存在路径）→ `discover_all_projects` 读 **registry 镜像的 id**（不读死盘 project.db）→ 按指纹自动重链到新位置，条目身份/name/继承链不变；④手动「指到新位置」指向指纹不符的目录 → 拒绝重链（防误并）；⑤老 db backfill：给一个 `_data/project.db` 但无 `.identity` 的老库 → 首次 discover 静默补建 `.identity` + 写 id，不阻塞，下次起支持重链。
- **★v4 卡片视图跨根硬契约**：设 ≥2 库根、选中其中 1 个展开 → 卡片视图仍含**所有**已录项目（另一根下的不消失）；切换库根只影响树展开，不清空卡片。

## 9. settings.py 新键定义 + 迁移

**新键（`@property` + dict 序列化）**：
```python
library_roots: Optional[list[str]] = None     # None=未配置(推导) / []=显式空 / list=配置
manual_project_folders: list[str] = []
project_tree_view_mode: str = "cards"          # "cards" | "tree"
```
（`settings.py:71` 现有 `project_tree_root` 保留不删，向后兼容 fallback。）

**迁移规则（统一 "None 永不写盘" 语义）**：
- 首次启动 `library_roots is None` 且 `project_tree_root` 非空 →
  - 若 `Path(project_tree_root).is_dir()`：**不持久化为唯一根**，把 `project_tree_root` 加入"推导默认值种子集合"
    （与 §5 已录项目所在目录并集）；弹一次性提示 `已自动把你的调查根 [X] 设为库根；你之前的项目都在，可在卡片视图看到全部 N 个`。
  - 若不存在（盘掉线）：保持 `None`（下次再推导），UI 提示 `原 project_tree_root 盘未连接，已暂不固化为库根`。
- `self._root` 概念废弃；树视图根节点 = 展开的 `library_roots` 集合。

## 10. 风险（带缓解）

| 风险 | 缓解 |
|------|------|
| 库根扫描慢 (WSL/drvfs 9p 累积) | 默认深度 2；同步阶段零 stat；`is_workspace_candidate` 单次 scandir；非 mock 烟测验证 |
| 缩略图批量卡/OOM | QListView+Model+Delegate 虚拟化；QPixmapCache LRU 200MB；首屏 paint<200ms 测试 |
| 长驻 worker 句柄泄漏 / re-entrant 生命周期 | `on_deactivate/closeEvent` quit()+wait()（shutdown-lock-leak 教训）。**★v5 钉死 re-entrant**（架构师判承重决策，v4 只答 clean shutdown 没答 re-activate）：① 每次进入树视图**新建** GridThumbnailWorker（不复用旧实例，避免旧 worker 信号命中已 deleteLater 的 widget → 崩/silent drop）；② 进入前对旧 worker：`quit()` + `wait(2000)` + 断开信号 + `deleteLater()`；③ **in-flight 请求处理 = cancel-and-drop**（tab 走时正在 decode 的图丢弃，不回传——切回时按视口重新投递，不排队，因为旧视口图已不可见）；④ worker 持有的 path→request 是 idempotent 的（重复投递同一 path 安全，靠 QPixmapCache 去重）。此模式对齐 `monitor_scan_worker.py` 的「每次扫描新建 QThread」现状，不引入长驻跨激活复用。 |
| QPixmap 跨线程崩 | 拆 `decode_image_data`(worker) + `make_pixmap`(主线程) |
| orphan conn 锁 _data | `open_project_db` try/except ensure_schema 失败 close；`_rollback_adopt` 端到端 |
| 多根交互复杂 | 库根折叠面板 + "全部展开"状态（非模式） |
| 封面取图慢 | 占位优先；全局缓存；异步 worker |

## 11. 封面缓存（全局，已决）

`~/.cache/specimen-photo-workbench/covers/<sha256(project_path)[:16]>.jpg`，**不进项目目录**
（盘只读不影响、多机不冲突、不污染用户数据目录）。
注：跨平台路径哈希在 Win/WSL 双环境各存一份（冗余 ~200KB，可接受）。
`cover_image` 读：相对路径→项目根解析；绝对路径→`relative_to` 项目根校验，失败降级 fallback（**不必强制注册 SafePathRegistry**，只读不写）。

## 12. 未决（实现中定 / 后续）

- "重新检测"是否做 30s 低频 `QFileSystemWatcher` 监听父目录自动刷新（不做高频轮询）。
- **未编号散片追踪**（incoming-jpg 下不在任何 specimen 的 jpg 计数 + 第 4 张 StatCard）——需
  `project_summary_service` 增 `unassignedJpgCount` 字段，**本期不做**，标后续。
- 卡片/网格有焦点时按字母键增量跳转定位（类文件管理器首字母）——后续增强。

## 13. 断链重链 Locate/Update Path（v4 新增，P0，部署刚需）

> 评审团 4/5 共识。架构师判「不补就是第一批线上回归 bug」；Lightroom 重度用户「磁盘断链是死路一条…
> 这是我十年用 Lightroom 最不能忍的退步」；分类学家「我十年前的库早从 /mnt/g 挪到 /mnt/h，spec 一个都没有」。
> 现状 `project_tree_view.py` 遇 `ProjectUnavailableError` 只弹框让用户「接回数据盘」；§5 去重用 `_same_path`
> 按路径判同一性 —— 换盘符会被当新项目重复认领，**旧身份/设置继承/recent 顺序全丢**。

### 13.1 稳定 id 双轨锚定（v5 修复 round-2 5/5 命中的 lifecycle bug）

> **round-2 5/5 一致 spec bug（v4 原写法）**：file_fp = `_data/project.db` 的 sha256。但 project.db 是**活库**——
> 任何 specimen 写入后 sha256 即变 → §13 跨卷迁移红线测试假绿、生产真红（生物学家）。
> 且 §13.2「路径失联读 project.db 按 id 匹配」在路径失联时**根本读不到 project.db**（LR 重度用户）——
> 身份锚存到了会失联的盘上，架构级 correctness hole（UX 设计师）。

**v5 修法（三处，钉死）**：

```python
def _write_stable_id(directory: str) -> tuple[str|None, str]:
    """adopt 末尾调用。两条身份:
       ① volume_uuid(系统级,同卷重接稳定)
       ② identity_fp = _data/.identity 文件的 sha256 —— 不是 project.db 的!
          .identity 是 adopt 时一次性写入(uuid4 + 时间戳)、之后【永不修改】的 sentinel。
          project.db 再怎么变,.identity 不动 → fp 跨整个项目生命周期稳定。
    """
    volume_uuid = _try_volume_uuid(directory)
    identity_path = Path(directory) / "_data" / ".identity"
    if not identity_path.exists():          # adopt 时建一次,后续 idempotent
        identity_path.write_text(f"{uuid.uuid4()}\n{time.time()}\n", encoding="utf-8")
    identity_fp = sha256(identity_path.read_bytes()).hexdigest()
    # 写两处:① project.db 的 project_meta(本地) ② user_projects.json(镜像,见下)
    _save_meta(get_db(directory), "volume_uuid", volume_uuid)
    _save_meta(get_db(directory), "identity_fp", identity_fp)
    return volume_uuid, identity_fp
```

- **卷 UUID**（`stat.st_uuid` / Win `GetVolumeInformation` / macOS `diskutil info`）：同物理卷重接（盘符换字母）→ 自动复活，零用户操作。取不到（部分 FS/网络盘）= None，不抛。
- **identity_fp**（`_data/.identity` sentinel sha256）：sentinel adopt 时写一次永不改，故 fp 跨项目全生命周期稳定。**不再用 project.db 的 sha256**（活库，会变）。跨卷迁移手选时校验「确是同一项目」。
- 任一命中即认定同一项目；都不命中才走"新认领"。

**★v5 关键：stable id 镜像进 `user_projects.json`**（修 E3/E4「身份锚在死盘上」hole）：
`record_recent_workspace` 写条目时**双写** `volume_uuid` + `identity_fp` 到该条目（不只是进 project.db）。
理由：路径失联时 `discover_all_projects` 要按 id 匹配，而路径失联 = 读不到 project.db —— **身份必须也在 registry**。
> v4 写法只在 adopt 写 project.db，对十年老库（无 id）也无效 —— 见 13.2 backfill。

### 13.2 discover_all_projects 匹配优先级（v5：读 registry 的 id + 老 db backfill）

```
registered 条目(从 user_projects.json 读,含镜像的 volume_uuid/identity_fp)
  → 路径在线: 直接命中(零 stat 风暴,纯 registry 字符串)
  → 路径失联: 扫库根下所有候选,读候选 _data/.identity 的 fp + 卷 uuid,
              与条目镜像的 id 比对;命中=自动重链(改 directory 字段,保留其余),不命中=标 unavailable
  → 路径仅作 id 全失效时的 fallback

★v5 老 db 回填(backfill): discover 读到一个 _data/project.db 但其 project_meta 无 id
   (v5 之前 enter_workspace 建的老库,十年家底) → 静默补建 _data/.identity + 写 project_meta
   + 回填进 user_projects.json 该条目。一次性,下次起享受重链。不阻塞 discover。
```

### 13.3 右键「指到新位置」重链入口（手动，当自动匹配失败）

卡片/树节点右键 → `指到新位置…`（仅 unavailable 节点亮）：
1. `ui.get_existing_directory` 选新路径
2. 校验新路径下确是同一项目：读新路径 `_data/.identity` 的 fp + 卷 uuid，与 **registry 条目镜像的 id** 比对（不读原死盘的 project.db）
3. 校验通过 → 重写 `user_projects.json` 该条目的 `directory` 字段，**保留 id/设置继承链/recent 顺序/name**
4. 校验失败 → 提示「新位置不像同一个项目（指纹不符）」，拒绝重链，避免误并两个不同项目
5. 不动 `_data/.identity` 与 project.db（身份锚不重写，仅改 registry 路径）

```
  盘符漂移场景            v3 现状              v5 重链
  /mnt/g → /mnt/h         卡片消失,历史丢      自动按卷 UUID 复活(零操作,读 registry id)
  外置盘换盘符            "接回数据盘"死循环    自动或右键指到新位置,身份/设置/历史全保
  跨机路径不同            重复认领成两份        identity_fp 校验,拒绝误并
  十年老库(无 id)         永远不支持重链        ★v5 backfill: 首次 discover 静默补 id,之后同上
```
