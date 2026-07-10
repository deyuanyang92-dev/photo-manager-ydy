# v3 → v4 迭代成效报告(round-2 综合裁决)

## 1. 打分 delta 表(6 维 × v3 → v4 → Δ)

| 维度 | v3 均分 | v4 均分 | Δ | 备注 |
|---|---|---|---|---|
| recognize_projects(项目识别) | 7.4 | 8.2 | **+0.8** | 卡片跨根硬契约 + dry-run 让认领可信 |
| import_old(旧项目导入) | 6.4 | 8.6 | **+2.2** ⭐ | 增幅最大,dry-run + 断链重链正面打掉两痛 |
| **preview_quality(预览质量)** | **6.1** | **8.3** | **+2.2** ⭐ | round-1 头号短板,三件套全中(TIFF 内嵌 JPEG 是 <100ms 红线唯一解药) |
| **navigation(导航)** | **5.6** | **7.6** | **+2.0** ⭐ | round-1 第二短板,Preview-in-place toggle + 卡片跨根 |
| **metadata(元数据)** | **5.8** | **7.2** | **+1.4** | round-1 第三短板,密度提升但批量核名仍缺 |
| overall | 6.8 | 7.9 | **+1.1** | 5 专家中 4 位给到 8 分(架构师 7.5) |

**round-1 三块短板反弹可视化**(0–10 标度):

```
v3 ▓▓▓▓▓▓▓▓▓▓▓▓░░░░░░░░  preview  6.1
v4 ▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓░░░  preview  8.3   +2.2  ▲

v3 ▓▓▓▓▓▓▓▓▓▓▓░░░░░░░░░  nav      5.6
v4 ▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓░░░░░  nav      7.6   +2.0  ▲

v3 ▓▓▓▓▓▓▓▓▓▓▓▓░░░░░░░░  meta     5.8
v4 ▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓░░░░░  meta     7.2   +1.4  ▲
```

**结论:** 三块 round-1 短板全部反弹且幅度大(+1.4 ~ +2.2),预览与导航两项基本"翻身",元数据是 6 维里最小增幅——也是后面 v4 残留问题的主要落点。

---

## 2. round-1 问题解决度(15 条问题汇总)

```
                addressed  partial  not_addressed
计数              8          4          3
占比             53%        27%        20%
                ████████   ████       ███
```

### ✅ 全 addressed(8 条)

| # | 专家 | round-1 问题 | v4 证据要点 |
|---|---|---|---|
| 1 | 底栖生物学家 | 预览三件套(滑块/Preview/TIFF 内嵌 JPEG) | §4.2 滑块 + Grid Lock;§3 抽 ExifIFD.TagJPEGInterchangeFormat;§4.2 空格 toggle Preview-in-place |
| 2 | 底栖生物学家 | 认领无 dry-run 预扫描 | §6 prescan_project→PrescanReport + §8 零写盘红线 |
| 3 | 分类学家 | 认领是文字承诺非写前预演 | 同上(Symbiota Pending Data Transfer Report) |
| 4 | Lightroom 用户 | 磁盘断链是死路一条 | §13 整章(volume_uuid + file_fingerprint 双轨 + 右键重链) |
| 5 | Lightroom 用户 | 缩略图滑块 + TIFF 同步解码 | §4.2 滑块;§3/§8 TIFF 内嵌 JPEG 抽取 |
| 6 | Lightroom 用户 | 预览 press-hold/模态两头不讨好 | §4.2 toggle Preview-in-place(spec 自认 round-2 press-hold 是缺陷并改掉) |
| 7 | UX 设计师 | 卡片↔全部库根关系模糊 | §4.1 ★v4 硬契约 + §8 跨根红线测试 |
| 8 | UX 设计师 | adopt 可信度停在文字承诺 | §6 dry-run 全落地 |

### ⚠️ partial(4 条)

| # | 专家 | round-1 问题 | 为何只是 partial |
|---|---|---|---|
| 1 | 分类学家 | 盘符变项目死,无重链 | §13 从零到有且抄对 Lightroom Locate,但 stable-id **不回填十年老库**,对核心 persona 只兑现一半 |
| 2 | 分类学家 | 元数据密度不够批量核名 | 密度(滑块)+ 单张审片(Preview-in-place)解了,**表格视图被明推 P2**,缩略图叠加层缺 |
| 3 | UX 设计师 | 中网格三件(滑块/preview/叠加徽标) | 2/3 解了,**第三件被静默换成 TIFF 性能件**(虽对但不是 at-a-glance 件),spec 写得像"全解决" |
| 4 | 架构师 | 三套缩略图缓存 coherency | 网格新路径干净,但**跨缓存 invalidate 联动降级 P1 仅记**;且 §3:66 "废弃手搓缓存" vs §10 "_THUMB_CACHE 留着"自相矛盾 |

### ❌ not_addressed(3 条)

| # | 专家 | round-1 问题 | 状态 |
|---|---|---|---|
| 1 | 底栖生物学家 | Smart Album / 存搜索为节点 | spec changelog 明列 P1/P2 未做 |
| 2 | 架构师 | adopt 僵尸工作区下游 gate | changelog 明列"adopt僵尸工作区下游gate"P1 未做;汇总导出/分类名录/导入站位总表对空 db 静默出空表 |
| 3 | 架构师 | grid worker re-entrant lifecycle | changelog 明列"grid worker re-entrant guard"P1 未做,只答了 clean shutdown |

**关键观察:** 3 条 not_addressed 全部被 spec **诚实标注为 P1 deferral**,不是偷懒隐藏;但架构师明确指出其中两条(缓存契约、grid worker)**是 v4 自己实现时绕不过的承重决策,不能 P1**——这是 round-2 残留问题的核心争议点。

---

## 3. v4 后仍存 / 新暴露问题(去重合并,按提及次数排序)

```
提及次数    问题簇
  5/5  ████████████████████  A. file_fingerprint / stable-id 持久化与回填(真 spec bug)
  3/5  ███████████████       B. Preview-in-place 交互契约未钉死
  2/5  ██████████            C. 缩略图叠加状态徽标(round-1 ③c 残留)
  2/5  ██████████            G. adopt 僵尸工作区下游 gate / dry-run 不覆盖 migrate
  1/5  █████                 D. Smart Album(明 Deferred,跨断面汇总刚需)
  1/5  █████                 E. spec 自相矛盾 §3 vs §10(承重决策)
  1/5  █████                 F. grid worker re-entrant lifecycle(承重决策)
  1/5  █████                 H. 表格视图(明 P2)
```

### 🔴 A. file_fingerprint / stable-id 持久化与回填 —— **5/5 专家命中,真 spec bug**

这是 v4 残留问题里唯一被全员点名的项,且各专家从不同角度打到同一个洞:

- **E1(底栖生物学家):** `file_fingerprint` 是 adopt 时刻一次性的 `_data/project.db` sha256,而 project.db 是活库——任何 specimen 写入后当前 sha256 ≠ adopt-time fp。**§13 跨物理盘迁移的核心动机实际打不住**,会让 §13 红线测试假绿。
- **E2(分类学家):** §13.1 只在 `adopt_project` 写 stable id,**对十年前已 enter_workspace 的老库没有任何回填/懒写机制**——重链这个 headline 特性对"十年家底"核心 persona 只兑现一半。
- **E3(Lightroom 用户):** §13.1 只把 id 写进 `_data/project.db`,但 §13.2 "路径失联时扫候选按 id 匹配"**要求 registered 条目本身持有 id**(路径失联时读不到 project.db)。**必须在 `record_recent_workspace` 写 `user_projects.json` 时双写 id**。典型 TDD 假绿生产真红的 spec 缺陷。
- **E4(UX 设计师):** **架构级 correctness hole**——身份锚存到了会失联的盘上。§13.3 step2 "与原记录比对"里的"原记录"就在死盘上。修法是一行:**adopt 时把 stable id 镜像进 `user_projects.json`**。
- **E5(架构师):** 同 E2,legacy id 回填缺口,discover 按 id 匹配对老项目失效。

> **5 票一致:** 这是跨卷迁移 headline 特性的真 spec bug,不是 deferral;会让 §13 红线测试假绿而生产真红。**必须在迭代中钉死**(采用 E3/E4 的"镜像进 user_projects.json" + E1 的 fp lifecycle 重算或 sentinel)。

### 🟠 B. Preview-in-place 交互契约未钉死 —— **3/5 专家命中**

- **E1:** 滑块↔缓存尺寸(QPixmapCache 按 decode max_size 存,滑块拉大→缓存图放大模糊)、Preview-in-place↔虚拟化网格的 reflow/wiring 未指明。
- **E3:** 缺 Loupe 100% pan(审成片看焦刚需);未编号散片右栏空白(应 fallback EXIF 而非留白)。
- **E4:** 三条契约缺失——①preview 态下 ←→ 是推进被预览照片还是只移网格焦点?②2-up 两张怎么选?③归属 specimen 元数据铺侧边还是大图下方?"不定,实现者会各拍各的,大概率退化成又一个挡住列表的大图。"

### 🟡 C. 缩略图叠加状态徽标 —— **2/5 专家命中(UX 设计师 + 分类学家)**

- **E4:** round-1 问题③c 被静默换成 TIFF 性能件,**spec 写得像"全解决"实际只解 2/3**;GPS 图钉/合成色角/格式徽标/星级仍只活在右栏,审片时眼睛在网格与右栏间反复跳。专业网格 8 小时用必备。
- **E2:** 缩略图仍不带学名/经纬度图钉/格式角标(§4.3 只提 breadcrumb/含子文件夹/虚拟滚动)。

### 🟡 G. adopt 僵尸工作区下游 gate / dry-run 不覆盖 migrate —— **2/5 专家**

- **E5:** 汇总导出/分类名录/导入站位总表对 adopt 后未 migrate 的空 `project.db` 静默出空表;卡片未加"已认领·待进入"态标。
- **E2:** dry-run 只预演 adopt,**不预演 migrate**——对旧库真正可怕的不是建 `_data`(已被零写盘证明),而是首次 enter 时 migrate 会 rename/移动 legacy sidecar、可能因校验失败拒绝部分记录。**dry-run 覆盖了低风险动作,漏掉了高风险动作**。

### 🟢 D / E / F / H(各 1 票但权重不同)

- **D. Smart Album** —— E1 的核心刚需,但 spec 已诚实标 P1 deferral。问题在于 E1 是"跨断面高频汇总"型用户,这是其工作仪表盘的命门。
- **E. spec 自相矛盾 §3 vs §10** —— 架构师点名:**承重决策不能 P1**,否则实现者替 spec 拍板。
- **F. grid worker re-entrant lifecycle** —— 架构师点名:**承重决策不能 P1**,否则生出 silent drop 或崩的回归类(撞 shutdown-lock-leak 教训)。
- **H. 表格视图** —— E2 工作流瓶颈,但已合理推 P2。

---

## 4. 收敛判断

### is_optimal_now / need_another_round 投票

| 专家 | is_optimal_now | need_another_round |
|---|---|---|
| 底栖生物学家 | ❌ false | ✅ true |
| 分类学家 | ✅ **true** | ❌ false |
| Lightroom 用户 | ❌ false | ✅ true |
| UX 设计师 | ❌ false | ✅ true |
| 架构师 | ❌ false | ✅ true |
| **合计** | **1/5 true** | **4/5 true** |

### 关键定性:所有"再迭代一轮"都强调"窄""轻量""文字钉死"

```
E1:  "再窄迭代一轮(修 fp lifecycle + 画一个 Smart Album minimal-viable 后续锚点)"
E3:  "再轻量一轮钉死这 3-5 条文字(不动架构)"
E4:  "还差一次外科手术式小迭代,不是全推倒"
E5:  "再窄迭代一轮把这两条钉死、把僵尸 gate+legacy id 回填挂 P1 task"
E2:  "骨架已达可放心执行,这三处得在 TDD 实现期补上,不必再开多方评审"
```

### 总体裁决: **再迭代一轮(窄,外科手术式)**

不是方向性返工,是精度修复。round-1 三痛(预览/导航/信任)的**方向全部正确且机制扎实**,但 v4 在落地"跨卷迁移"这条新 headline 特性时引入了一个 5/5 命中的真 spec bug(file_fingerprint/stable-id lifecycle),并把两个承重决策(缓存契约、grid worker)以"暂记 P1"形式踢给实现者——这违反了 spec 应防的事。

### 必改项(round-3 必须钉死,不动架构)

| 优先级 | 项 | 来源 | 修法 |
|---|---|---|---|
| **P0** | A. file_fingerprint / stable-id 持久化与回填 | 5/5 专家 | adopt 时镜像 stable id 进 `user_projects.json`(E3/E4 一行修法)+ fp lifecycle 改为关闭时重算或 sentinel(E1)+ 首次 discover 命中无 id 老 db 时静默 backfill(E2/E5) |
| **P0** | E. spec 自相矛盾 §3 vs §10 缓存契约 | 架构师 | 钉死:v4 内 grid 用 QPixmapCache;`_THUMB_CACHE` 暂留 labels/workbench/封面 fallback;invalidate 联动归 P1 task#X 并指明触发源 |
| **P0** | F. grid worker re-entrant lifecycle | 架构师 | 在 spec 里指定:每激活新建 vs 复用 + request-coalescing + 信号生命周期管理(撞 shutdown-lock-leak 教训,不能让实现者瞎选) |
| **P1** | B. Preview-in-place 交互契约 | 3/5 专家 | 钉死 ←→ 推进行为 / 2-up 选法 / 元数据铺位 / Loupe 100% pan / 散片右栏 fallback |
| **P1** | G. adopt 僵尸 gate + dry-run 覆盖 migrate | 2/5 专家 | 空 db 读者加 gate;prescan 增加 migrate 预演(legacy sidecar rename/校验失败计数) |
| **P1** | C. 缩略图叠加状态徽标 | UX + 分类学家 | 承认是 round-1 ③c 残留,补回 GPS 图钉/合成色角/格式徽标 |

### 可保留 P2(合理 deferral,不动)

- D. Smart Album(E1 核心刚需,但 spec 已诚实标 P1/P2 未做,且非 v4 引入的退化)
- H. 表格视图(E2 工作流瓶颈,但已合理推 P2)

---

## 5. 一句话裁决

**v4 离"最优"还差一次外科手术式窄迭代:** round-1 三块短板全部反弹(+1.4~+2.2)、三痛里两痛扎实解决、跨卷重链与 dry-run 方向全对——但 file_fingerprint/stable-id 在活库与跨物理盘场景下有一个 5/5 专家命中的真 spec bug(身份锚存到了会失联的盘上),加上缓存契约 §3↔§10 自相矛盾、grid worker re-entrant 生命周期两条承重决策被以"暂记 P1"踢给实现者——**钉死这 3 条 P0 + 3 条 P1(均为文字精度,不动架构),即可放手执行;不必进 round-3 多方评审,但 round-3 spec 修订必须闭环。**