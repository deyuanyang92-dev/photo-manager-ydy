export const meta = {
  name: 'project-tree-design-jury-r2',
  description: 'Round 2: same 5-persona jury re-scores spec v4 (which folded in round-1 P0 findings), each gets their own round-1 verdict back, synth produces v3-vs-v4 delta + convergence verdict',
  phases: [
    { title: 'Re-score v4', detail: '5 personas re-score, mark each r1 problem addressed?' },
    { title: 'Synthesize delta', detail: 'v3 vs v4 mean per dim + remaining gaps + optimal?' },
  ],
}

const REPO = '/mnt/n/claude/photo-platform-ydy-v3'
const VIEW = `${REPO}/app/views/project_tree_view.py`
const SPEC_V4 = `${REPO}/docs/superpowers/specs/2026-07-07-project-tree-redesign-design.md`

// round-1 verdicts (v3 baseline) — each persona's own scores + top problems + one-liner
const R1 = [{"persona": "现场底栖生物学家(每年跑多次断面调查,每断面上万张标本照,最痛是跨断面快速定位项目 + 预览成片质量)", "scores": {"recognize_projects": 7, "import_old": 6, "preview_quality": 6, "navigation": 5, "metadata": 5, "overall": 7}, "top_problems": ["没有'存搜索为节点/智能集合'——跨断面汇总的科研刚需被彻底忽略。我一年跑5个调查、30个断面,每周真正想做的不是'找一个已知项目',而是'列出所有待处理>0的断面'、'2026航次未认领候选'、'厦门湾所有潮间带断面'。spec §4.1 只有一次性'单搜索框+4个kind-chip',关掉就没了;kind-chip 还只是'节点类型'一个 facet,没有'标本数>0/待处理>0/有成片/盘可用'业务态 facet 和计数占比条。这把项目树钉死在'文件系统镜像',而我需要的是 Smart Album 那种'定义一次永久自动刷新'的活节点。", "认领没有 dry-run 预扫描报告——我每次点认领都在赌。spec §6 的信任完全靠'大白话文字承诺+失败 _rollback_adopt'。我面对的是几千张不可复现的原始 TIFF,'文字承诺不动'和'写之前看见:识别到 142 JPG/8 TIFF/0 个 _data,认领将新建 project.db,原始文件 0 改动'是两个数量级的信任差。Symbiota 的 Pending Data Transfer Report 证明:写盘前用真实计数摊给用户看,可信度从'信对话框'升到'看见实际数据'。", "预览还停在'看一眼'级别,不够'逐张审成片'。(a)中网格没有缩略图大小滑块——粗筛想放大、清点想缩小,密度被写死;(b)Preview 是'空格 press-hold 100%(松手就没)+Enter 模态大图(挡住列表)',不是 digiKam Preview-in-place 那种中网格同级兄弟;(c)§8 硬指标'单张解码<100ms'在几十MB的无损 TIFF 上几乎不可能——WSL/drvfs 9p 上必爆,spec 全文没提 digiKam 的'TIFF 内嵌 JPEG 抽取',这条红线大概率打不绿。"], "verdict_one_line": "方向对(虚拟化网格+adopt/enter分离+user_projects.json真相源+端到端回滚)是扎实地基,但还停在'文件系统镜像'级别——补齐 Smart Album(跨断面汇总)、认领 dry-run(信任)、预览三件套(审成片深度),才能变成我这种跨断面高频汇总的人真正敢用、爱用的资产库。"}, {"persona": "博物馆分类学家 / 标本馆数据管理员（管多年历史藏品，最痛=把十年前的旧项目导进来不丢元数据）", "scores": {"recognize_projects": 7, "import_old": 6, "preview_quality": 7, "navigation": 6, "metadata": 6, "overall": 7}, "top_problems": ["认领旧项目是'文字承诺'不是'写前预演'——spec §6 的 adopt 只给大白话确认框（'原始照片一个都不会动'），却不在点确认前让我先看见'扫到 142 张 JPG / 8 个 TIFF / 0 个 _data，将只新建一个 _data 子目录'。对一个要把十年家底交出去的人，'相信对话框文字'是绝对不够的：我要的是写盘前用真实计数证明会发生什么。当前 spec 的信任全靠一句话撑着，这是旧库导入的心理门槛，不是技术门槛。", "盘符变了项目就'死'了，没有重链出口——spec §7 对'盘未连接'只有'卡片灰显 + 重新检测小按钮'，而重新检测是重新 stat 同一路径。我十年前的库早从 /mnt/g 挪到 /mnt/h、换过机器、跨过 WSL 与 Windows，路径几乎必然变。一旦路径失效，这个项目就永远'盘未连接'，没有任何'指到它现在的新位置'入口。Lightroom 的 Find Missing Folder、Capture One 的 Locate、digiKam 的 Update Path 全是解这个的，spec 一个都没有。对旧库这等于眼睁睁看着项目从列表蒸发。更糟的是 §5 的去重用 project_service._same_path 按路径判同一性——同一物理库换个盘符会被当成新项目重复认领，旧身份和历史全丢。", "元数据密度不够批量核名——一个分类学家导完 200 个旧标本，要一眼扫完它们的学名/采集人/站位有没有丢，不是一个个点。spec §4.3 右栏只在选中单张照片时显示学名/UID/站位/经纬度/采集日期/采集人，是'一次一个'的阅读模式；中网格是纯 IconMode 缩略图，缩略图本身不带学名、不带经纬度图钉、不带格式角标。既没有表格视图（一行一标本+多列字段），也没有缩略图叠加层。导完旧库我只能逐张点核——这跟没导一样累。"], "verdict_one_line": "骨架对、认领零侵入的设计是对的，但这是为'正在拍的调查'设计的库浏览器，不是为'要抢救的十年旧库'设计的——旧库导入缺写前预演、移盘后无重链、批量核名无表格视图，这三件套补齐之前，我不敢把家底交给它。"}, {"persona": "Lightroom 重度用户 — 十年 catalog 管理经验,对库浏览/筛选/预览/键盘导航的交互流畅度极挑剔,见不得蠢设计", "scores": {"recognize_projects": 8, "import_old": 6, "preview_quality": 5, "navigation": 5, "metadata": 6, "overall": 6}, "top_problems": ["磁盘断链是死路一条,不是可恢复状态。spec §7 的'盘未连接→卡片灰显+重新检测小按钮'只是对同一路径再 stat 一次。WSL/drvfs 下 /mnt/g 变 /mnt/h、外置盘换盘符、跨机路径不同——这些场景下'重新检测'永远失败,已录项目就从卡片永久消失,只能手改 user_projects.json 或重选根。当前代码 project_tree_view.py:1467-1471 遇到 ProjectUnavailableError 也只是弹框让用户'接回数据盘'。Lightroom/Bridge/Capture One/digiKam 全都有 Find Missing Folder/Locate/Update Path——按文件夹身份重链到新路径,记录与历史全保留。spec 的 Feature Study 自己都把这条标成'痛点①在盘迁移场景下的真正解药',但没写进 spec。这是我十年用 Lightroom 最不能忍的退步。", "没有缩略图大小滑块,密度被写死。当前 project_tree_view.py:1267 把缩略图钉死在 112x78、塞在右栏 3x2 QGridLayout(行 1200-1205),主区是文字树——这正是 spec §1 痛点③的现状。spec §3/§4.3 升级到 QListView IconMode 虚拟化方向对,但通读全 spec 找不到一个用户可拖动的缩略图大小滑块(setIconSize 只在内部提了一次,没有 UI 控件)。Lightroom/Bridge/Photo Mechanic/Capture One 全部把密度滑块做成一等公民——粗筛时一屏 400 张、精挑时 12 张大图,这是库浏览的肌肉记忆。さらに 当前解码是同步上主线程的(project_tree_view.py:1271-1272 decode_image_thumbnail 直接调用),spec §3 虽然规划了 ThumbnailWorker 异步化,但没提 TIFF 内嵌 JPEG 抽取——而本项目的核心素材就是 TIFF,全解 TIFF 当缩略图根本打不到 spec §8 自己定的'单张<100ms'红线。", "预览是'按住空格'或'模态大图',两头不讨好。spec §4.2 的空格是 press-hold 100%——松手就没了。审一个断面的 200 张成片我得一直按着空格键?这是 Photo Mechanic 早期被骂的设计。而 Enter 大图预览是模态/全屏,挡住列表看不到前后照片。Lightroom 的 E 键 Loupe、Bridge 的空格-后单击、digiKam 的 F3 Preview-in-place 全是'切进去就停在那,前后缩略图仍可见'的 toggle 模型——审片节奏不被打断。spec round 2 笔记自己提到 press-hold 不适合逐张审成片,却还是保留了它。"], "verdict_one_line": "架构重构到位(catalog 心智 + 虚拟化网格 + adopt/enter 分离 + 端到端回滚测试),但交互层还停在'差不多'——磁盘断链只能重 stat 不能重链、预览靠按住空格、没有缩略图大小滑块也没有 Smart Album/表格视图,离 Lightroom 库浏览的流畅度还差一个版本。"}, {"persona": "桌面软件 UX 设计师(信息密集型专业工具方向;看重三栏布局、视觉层级、认知负担、可发现性;以 Lightroom/Bridge/Capture One/Photo Mechanic/digiKam 为基准)", "scores": {"recognize_projects": 8, "import_old": 7, "preview_quality": 6, "navigation": 6, "metadata": 6, "overall": 7}, "top_problems": ["卡片视图与'全部库根'的关系是模糊契约——spec 只在树视图左栏加了'全部库根'常驻折叠面板来根治 rooted 盲区(痛点①),却没写明卡片视图是否也跨根显示全部已录项目。若卡片视图随选中根收窄,痛点①会在另一视图复活;这是能把标题级 bug 重新放回来的唯一歧义,必须在 spec 里写成硬契约:'卡片视图永远显示全部已录项目,与当前展开的库根无关'。", "认领(adopt)的可信度停在文字承诺,没有写盘前可见的 dry-run。spec §6 的确认框是一段大白话('原始照片一个都不会动'),对拿着不可重现野外底片的科学家来说,对话框里的承诺远不如'识别到 incoming-jpg 142 张 / results .tif 8 个 / 0 个 _data → 认领将新建 _data/project.db,原始文件 0 改动'这样带真实计数的预扫描报告可信。这是把痛点②从'不敢点'翻成'放心点'的最高杠杆缺口,spec 差这一步。", "中网格结构性升级到位,但缺三个让专业网格能每天用 8 小时的二阶件:(a) 无缩略图大小滑块——Lightroom/Bridge/C1/PM/digiKam 全有,密集清点场景密度不可调=硬伤;(b) 无持久就地预览(preview-in-place)——只有空格 press-hold 100%(松手就没)和 Enter 大图(模态挡住列表),'逐张审一个断面的成片'这一核心动作被打断;(c) 缩略图无叠加状态(GPS 图钉/合成色角/格式徽标),所有状态只活在右栏=眼睛在网格与右栏间反复跳。"], "verdict_one_line": "架构方向是教科书级正解(Lightroom 编目模型 + adopt/enter 二分 + 虚拟化三栏 + 红线工程纪律罕见地严),但停在'能用'差一步到'8小时顺用'——补齐 dry-run 信任预览、盘迁移断链重链、密度滑块+就地预览+缩略图叠加这四个二阶专业件即达行业一流。"}, {"persona": "软件架构师(数据流/性能/状态一致性/可扩展性)", "scores": {"recognize_projects": 7, "import_old": 7, "preview_quality": 6.5, "navigation": 6, "metadata": 6, "overall": 7}, "top_problems": ["状态一致性: 三套缩略图缓存各自为政、无 coherency 契约。spec 要在网格引入 QPixmapCache.setCacheLimit(200*1024) 作全局 LRU,又声明'废弃手搓缓存',但 image_thumbnail.py 现有的 _THUMB_CACHE(OrderedDict 384 条,被标签/工作台/封面 fallback 共用)依然存在,加上 ~/.cache/.../covers/<sha256> 这第三套。三套用三套 key:(path,mtime,size) / QPixmapCache 内部 key / sha256(path)[:16]。我已 grep 全仓确认 QPixmapCache 当前零调用,意味着这是新引入的第二条解码路径。结局可预测:用户重合成一张成片后,网格(命中 QPixmapCache 旧 pixmap)与封面(命中 sha256 文件缓存)与工作台(命中 _THUMB_CACHE)三处显示不一致,且没有任何一条 invalidate 另一条。这是 6 个月后'为什么缩略图不刷新'类 bug 的温床,spec 没给统一缓存层的设计。", "状态一致性 + 边界情况: adopt 造出'僵尸工作区'状态,却没把这条边界传给读 project.db 的下游消费者。adopt 只建 _data/project.db + seed 设置(不经 migrate),项目即进 user_projects.json 并在卡片显示为工作区。但项目树右键菜单的'汇总导出/分类名录/导入站位总表'(project_tree_view.py:882-886)直接读 project.db——adopt 后 project.db 是空的(legacy sidecar 未 migrate)。用户对已认领未进入的号右键汇总导出,会静默导出一张空 Excel,零警告。spec §6 只 gate 了 enter,没 gate 这些 cross-workspace 工具,也没在卡片上标'已认领·待进入'让用户知道这态有别。'识别'与'已 migrate'的边界没传播到所有 project.db 的读者。", "并发/资源生命周期: 长驻 grid ThumbnailWorker(moveToThread+exec())在 re-entrant on_activate 下无'已在运行'守卫。用户在 项目树↔工作台 间频繁切页是常态,而 memory 里 shutdown-lock-leak-must-reboot 记录的正是'退出不取消 view 线程 → 必须重启'。spec §7 只说 on_deactivate 必须 quit()+wait(),却没回答:用户 tab 走时正在 decode 的请求怎么办?tab 回来是复用旧 worker 还是新建?若新建则旧 worker 的 in-flight QImage 信号回主线程时 widget 可能已 deleteLater → 崩或 silent drop;若复用则需 request-coalescing 逻辑 spec 未给。discover worker 的 JSON 签名 race guard 有,grid worker 的同类问题没盖。"], "verdict_one_line": "方向和契约边界都对(v3 真吸收了 round 2 反馈:删了循环依赖的④源、加固了 open_project_db、网格虚拟化引用了仓内已有范式),但作为架构我最不放心三件事:WSL 盘符漂移下靠 _same_path 的项目身份会断链却没有 re-bind 入口、三套缩略图缓存各自为政、adopt 后的'僵尸工作区'状态会污染读 project.db 的跨工作区工具——这三个不补,上线后就是第一批回归 bug。"}]

// compact feature catalog (app + feature names + standout) — apps don't change between rounds
const CATALOG = [{"app": "Adobe Lightroom Classic (Library 模块 / Catalog 库 + Folders 面板 + 网格视图)", "features": ["Catalog = 引用型 SQLite 数据库(不存照片本体)", "「Add」导入模式(原地认领,零搬运)", "Folders 面板:导入即自动收录 + 行内照片计数 + 层级三角", "缺失文件夹「?」徽标 + Find Missing Folder 重链 + 卷在线 LED", "Synchronize Folder(增量对账式重扫)", "网格视图 + 可配置单元格(Compact/Expanded Cells + Grid Extras)", "Library Filter bar(Text/Attribute/Metadata 三档可叠加 + 预设)", "文件夹收藏/颜色标签 + 文件夹搜索框", "Smart Previews(主文件离线仍可浏览/编辑)"], "standout": "Catalog 是一份「引用清单」而不是文件本身——导入 = 在清单里登记一条引用,文件永远原地不动,清单永远记住它(哪怕盘掉了),断了就「Find Missing Folder」重链到新位置。这是 Lightroom 整个库浏览页的底层心智,也是唯一能同时溶解痛点①(rooted 盲区:已录项目不应因选了某根而消失)和痛点②(无认领动作:外部文件夹可被「Add」原地认领)的那一个点子。spec 已经走到一半(adopt_project ≈ Add;user_projects.json 作真相源 ≈ catalog 永不遗忘),但还差最后一块:重新检测失败时的「重链到新路径」入口(WSL/drvfs 下 /mnt/g→/mnt/h 极常见)。把这三件套(原地认领 + 永不遗忘 + 断链重链)补齐,是这个项目树页能从 Lightroom 抄到的最高杠杆一招,因为它把整个页面从「必须先选一个根才能看到东西」的扫描型工具,变成「我登记过的永远在,想加新的随手认领」的资产型库——这正是痛点①②共同想要的语义。"}, {"app": "Adobe Bridge", "features": ["Favorites 面板：拖拽任意文件夹到左栏即「固定」为常用，一键跳回", "Favorites + Folders 双面板并存，不存在「单一根」概念", "中央缓存（Preferences → Cache → 中央位置）+ Build/Export Cache 预生成 100% 预览", "Content 面板是主区（不是侧栏），缩略图大小滑块 + Grid Lock 防重排", "Always Show Items from Subfolders（持久化跨导航的「含子文件夹」开关）", "Filter 面板：多 facet 切片筛选（文件类型 / 评分 / 关键词 / EXIF 光圈快门 ISO / 日期）", "Metadata 面板：选中即自动填、多选显示共有字段、可勾选要显示的字段组", "空格全屏预览 + Loupe 按住 100% + Review Mode（Ctrl+B 轮播）", "Path bar 面包屑 + Back/Forward/Up/Reveal Recents 全局导航历史"], "standout": "Bridge 根本没有「项目 / 根目录 / 导入」这三个概念——它的答案是「Favorites 拖拽固定 + Folders 层级树双面板并存 + 中央缓存让浏览过的文件夹永久秒开」。即「浏览即识别」，没有任何「先认领成工作区才能用」的门槛步骤。这对项目树页是最值得抄的底层思路：当前 spec 的 library_roots（多根扫描）仍是「根」思维的延伸，仍会有「根外项目怎么发现」的二次问题；Bridge 的 Favorites「pin 思维」更彻底——把已 enter 的工作区和用户手动 pin 的外部文件夹都作为一等公民常驻左栏，配合中央缩略图缓存（spec §11 已采纳封面缓存，可扩展到全部缩略图），让「点开任何文件夹都是工作区」而不是「先选根、再扫树、再认领」。把 adopt（认领）这一步降级为「想在里面拍照时才需要的初始化」，而非「能在树里看到它」的前提——这恰好印证 spec §6 adopt-vs-enter 分离的设计是对的：浏览和识别应零门槛，只有「正式拍照」才需要建 _data/project.db。"}, {"app": "Capture One (Pro) — 仅抽取其 Session/Library 浏览器 + Collections 收藏夹与「项目/文件夹库浏览页」相关的功能/交互模式", "features": ["Library Tool 多源常驻面板（System Folders + Favorites + Collections 三段并列）", "Synchronize Folder（右键文件夹 → 同步，文件原地不动）", "Referenced 导入 / Session 原地引用（默认不 Copy）", "Locate… 离线文件夹重指（重链，不重建）", "Browser 三视图（Filmstrip/Grid/List）+ 独立 Viewer 主区 + 双击放大 + Loupe", "Filters 工具（多维元数据筛选 + 每值命中计数）+ Smart Album 存搜索", "Metadata 工具（选中图元数据为单一真相源）+ Filter 计数即聚合统计"], "standout": "Smart Album——把「保存的搜索」做成树里的一等节点，规则驱动、内容自动刷新、永久挂着。它是 Capture One 把「一次性筛选」升级为「常驻导航对象」的关键设计：定义一次「待处理>0 / 2026 新增 / 厦门湾未认领候选」，之后它就像一个真实文件夹一样永远在树里，内容随磁盘自动更新。对项目树页而言，这是把页面从「文件系统的静态镜像」变成「活的工作仪表盘」的最小且最高杠杆的一步——尤其契合跨断面、跨航次汇总的科研场景，且无需新增 DB 表（查询即规则，复用现有 specimen/candidate 数据）。当前 spec 只有一次性搜索框+kind-chip，缺这个「存搜索为节点」的层级。"}, {"app": "Photo Mechanic / Photo Mechanic Plus (Camera Bits)", "features": ["Ingest 对话框:任意盘/文件夹作源 + 增量记忆 + 后台 + 边拷边开 Contact Sheet", "Source Directory Structure 三档 + Secondary Destination 备份", "Contact Sheet = 主区缩略图网格 + 大小滑块 + 按拍摄时间排序", "Preview Window:空格 1-up + 可拖缩略图条 + info 面板 + 2-Up 对比 + 单击任意处 100%", "Browse Tool:反向筛选(从空到有)+ 每项计数 + 蓝色占比条", "Catalog:跨盘索引 + 含离线介质 + Scan to Catalog 边索引边浏览", "Navigator / Organizer 左面板切换(文件系统树 vs catalog 索引)", "Color Class:数字键 1-8 标色 + 底部 filter widget + View All/Tagged/Untagged", "Variables 重命名模板 + Ingest 时套用 + Local/Global 双模板"], "standout": "Browse Tool 的『反向筛选 + 每项计数 + 蓝色占比条』。这是 PM 里最值得项目树抄的一个点子,因为它正面治痛点①(rooted 盲区)且 spec 目前没有。逻辑反转:现在 project_tree_view.py 是『先选/推导一个根 → 看到一棵树』(project_tree_view.py:554-567 rooted 模式),树外的已录项目在 rooted 模式下消失。Browse 把心智模型从『选根再看』翻成『先看全盘按 facet(调查根/年份/省份/站位)的分布计数与占比条 → 点哪个 facet 就把那组照片追加进主网格』——不选根也不会瞎,因为所有已索引内容以计数形式始终可见,蓝色横条直接告诉你照片集中在哪个调查、哪些断面是空的。这比 spec §4.1 现有的『单搜索框 + 类型 chip』在『识别已有项目』这件事上更直观,可作为卡片/树之外的第三种视图(或卡片视图的一个 facet 抽屉)增量叠加,不动现有契约。"}, {"app": "digiKam (KDE 开源照片管理器, 官方文档 docs.digikam.org 9.x)", "features": ["多根 Collections 库模型 — 内置 Local / Removable / Network 三类, 从不强制单一根", "「Add Collection」≠「Import」— 认领已存在的文件夹作库根, 文件原地不动", "每条 Collection 带稳定 UUID + Update Path 重绑按钮", "Albums 左侧树 = 文件夹 1:1 镜像, 中心 Icon-View 网格 + Preview 区是同级兄弟 (F3 切换)", "缩略图叠加层: 地理位置图钉 + 格式/标题/说明/标签/星级 + 旋转/全屏覆盖按钮", "嵌入预览优先 (Embedded preview extraction) — TIFF/PNG/JPEG/RAW 内嵌小图直接拿来当缩略图", "Table-View: 缩略图排成行 + 列可任意定制为任意 DB 元数据字段", "Album 封面 = 任意照片设为 Album Icon (右键 Set as Album Thumbnail / 拖缩略图到 Album)", "左 Sidebar 九个虚拟视图 (Albums/Tags/Labels/Dates/Timeline/Search/Similarity/Map/People) 共享同一个中心 Icon-Area + 状态栏 funnel/trash/active-count 滤镜条"], "standout": "Preview-in-place (F3): 中网格 Icon-Area 和 Preview-Area 是同级兄弟而非模态弹窗。点缩略图的图区→Preview 区显示该大图+元数据; 点 Preview 区 / 按 Esc / 按 F3 / 点工具栏 Thumbnails 按钮→Preview 消失回到纯网格; 其它缩略图全程可见。这是 digiKam/Lightroom/Bridge 共有的单次点击预览交互。比 spec 现状的「空格 press-hold 100%(松手就没)」和「Enter 大图预览(模态/全屏挡住列表)」都更适合「逐张审成片」的科研场景——用户能在保持网格上下文(看到前后照片)的同时细看当前这一张, 审片节奏不被打断。落到项目树: 中网格的 Preview 态不只是放大图, 同时把该照片归属的 specimen 元数据(学名/UID/站位/经纬度/采集日期/采集人/合成状态)铺在 Preview 区侧边或下方, 一个屏内同时完成「看图+读字段」, 直击痛点③(主区是文字/预览弱)。"}, {"app": "iNaturalist / Symbiota / Specify 7 (生物标本与观测库 — 项目/数据集浏览相关功能抽取)", "features": ["Symbiota — 四种并列浏览入口 (Search Collections / Map Search / Image Search / Browse Images)", "Symbiota — 搜索结果 List Display(带缩略图) vs Table Display(纯表格)一键切换", "Symbiota — Skeletal vs Full Upload + 临时表 dry-run (Pending Data Transfer Report)", "Symbiota — snapshot 数据集用 dbpk 字段把外部记录与门户记录永久链接", "Symbiota — 每张影像存三档 URL (Thumbnail / Web / Large) + Thumbnail Maintenance 批量补建工具", "Specify 7 — 树视图 'Show only nodes with associated objects' 过滤开关", "Specify 7 — 缩略图按需生成 (Web Asset Server 的 resolve_file 原图降采样)", "iNaturalist — Explore 观测页 map / grid / list 三视图切换 + 侧栏多维度筛选", "iNaturalist — 批量上传时从照片 EXIF 自动抽取日期/GPS 填字段"], "standout": "Symbiota 的「临时表 dry-run + Pending Data Transfer Report」——写库前先把结果摊给用户看。这是认领动作(痛点②)可信度的最大杠杆:当前 spec 的 adopt 是「大白话确认对话框 + 失败回滚」,信任靠文字承诺;Symbiota 证明更优解是「先异步扫描目录 → 弹报告(识别到 N 张 JPG / M 个 TIFF / 0 个 _data,认领将新建 _data/project.db,原始文件 0 改动)→ 用户看见真实计数再点认领」。它把「相信软件不会动我照片」从承诺升级为「写之前就看见会发生什么」,且与 spec §6「adopt 不走 migrate、_rollback_adopt 端到端清理」完全不冲突——只是在用户点确认前多插一个零写盘的预扫描步。这一个交互能把痛点②从「不敢点认领」直接翻成「放心点认领」,投入产出比最高。"}]

// v3→v4 改了啥(round-1 P0 五条 + 硬契约,已写进 spec v4)
const V4_DIFF = `spec v3→v4 已吸收 round-1 评审团的 P0 共识(每条都在 spec v4 里落了位):
① 断链重链 Locate/Update Path + 稳定 id 双轨(卷UUID自动复活 + 文件指纹跨卷校验) → §13 新章,adopt 写 id,discover 按 id 匹配,右键「指到新位置」手动重链+指纹校验防误并
② adopt 写盘前 dry-run 零写盘预扫描报告(借 Symbiota Pending Data Transfer Report) → §6 prescan_project + 确认框显示真实计数「142 JPG/8 TIFF/0 _data」
③ 预览三件套: (a)缩略图大小滑块+Grid Lock (b)Preview-in-place toggle 替代 press-hold(同级兄弟QSplitter,2-up对比) (c)TIFF 内嵌 JPEG 抽取(decode_image_data 先抽 ExifIFD.TagJPEGInterchangeFormat,§8<100ms红线唯一解药)
④ 卡片视图跨根硬契约(分歧C裁决): 卡片永远显示全部已录项目,与展开库根无关
⑤ 缓存统一记 P1(本期仅记不强制): QPixmapCache唯一LRU + 磁盘covers持久层 + invalidate联动
P1/P2 未做: Smart Album/智能节点、表格视图、adopt僵尸工作区下游gate、grid worker re-entrant guard`

const VERDICT_SCHEMA = {
  type: 'object',
  properties: {
    persona: { type: 'string' },
    new_scores: {
      type: 'object',
      properties: {
        recognize_projects: { type: 'number' },
        import_old: { type: 'number' },
        preview_quality: { type: 'number' },
        navigation: { type: 'number' },
        metadata: { type: 'number' },
        overall: { type: 'number' },
      },
      required: ['recognize_projects', 'import_old', 'preview_quality', 'navigation', 'metadata', 'overall'],
    },
    round1_resolution: {
      type: 'array',
      description: '逐条对照你 round-1 的 top_problems',
      items: {
        type: 'object',
        properties: {
          problem_short: { type: 'string', description: '你 round-1 那条问题的简述' },
          status: { type: 'string', enum: ['addressed', 'partial', 'not_addressed'] },
          v4_evidence: { type: 'string', description: 'spec v4 哪条/哪节解决了它,或为什么没解决' },
        },
        required: ['problem_short', 'status', 'v4_evidence'],
      },
    },
    new_remaining_problems: { type: 'array', items: { type: 'string' }, description: 'v4 之后仍存在的或新暴露的问题(top 3)' },
    is_optimal_now: { type: 'boolean', description: 'v4 是否已达你心目中的「最优」(可放心执行)' },
    need_another_round: { type: 'boolean', description: '是否需要再迭代一轮 spec' },
    verdict_one_line: { type: 'string' },
  },
  required: ['persona', 'new_scores', 'round1_resolution', 'new_remaining_problems', 'is_optimal_now', 'need_another_round', 'verdict_one_line'],
}

phase('Re-score v4')
const r2 = (await parallel(R1.map((r1) => () =>
  agent(
    `你是「${r1.persona}」。这是 round 2 复审 —— 同一个项目树页设计,spec 已从 v3 升到 v4,吸收了你们 round-1 的 P0 共识。\n\n` +
    `## 你 round-1 的评审(请对照,别装失忆):\n` +
    `打分: ${JSON.stringify(r1.scores)}\n` +
    `你当时骂的 top problems:\n${r1.top_problems.map((p, i) => `  ${i + 1}. ${p}`).join('\n')}\n` +
    `你当时的总结: ${r1.verdict_one_line}\n\n` +
    `## 本轮材料\n` +
    `- 当前实现代码: ${VIEW}\n- spec v4 全文: ${SPEC_V4} (重点读 v3→v4 changelog 顶部 + §6 dry-run + §13 断链重链 + §4.2 preview-in-place/滑块 + §3 TIFF内嵌JPEG + §4.1 卡片跨根硬契约)\n` +
    `- v3→v4 具体改了啥:\n${V4_DIFF}\n` +
    `- 可借鉴功能清单(round-1 已给你过,仅备查):\n${JSON.stringify(CATALOG)}\n\n` +
    `## 任务\n` +
    `1. 先读 spec v4(别凭记忆,真去读文件确认那 5 条改动落位了、写得对不对)。\n` +
    `2. 重新打 6 维分(1-10)。对比你 round-1 的分,升了/没动/甚至降了都说得通,只要诚实。\n` +
    `3. 逐条对照你 round-1 的 top_problems: v4 解决了吗?给 addressed/partial/not_addressed + spec v4 证据(引节号)。\n` +
    `4. 列 v4 之后【仍然存在】或【新暴露】的问题(top 3)。\n` +
    `5. 判断: v4 是否已达你心目中「可放心执行的最优」?是否还要再迭代一轮?\n` +
    `6. 一句话总结。\n\n` +
    `诚实、尖锐、具体。你是同一个专家,前后判断要能对得上(分变了要能解释为什么)。`,
    { label: `r2:${r1.persona.slice(0, 5)}`, phase: 'Re-score v4', schema: VERDICT_SCHEMA }
  )
))).filter(Boolean)

phase('Synthesize delta')
const delta = await agent(
  `你是首席设计师,做 round-2 综合裁决。基于 5 位专家的 round-2 复审(对 spec v4),对比 round-1(对 v3)的基线,产出「v3→v4 迭代成效报告」(中文 markdown)。\n\n` +
  `## round-1 基线(v3,5 专家打分)\n${JSON.stringify(R1.map((x) => ({ persona: x.persona, scores: x.scores })), null, 2)}\n\n` +
  `## round-2 复审(v4,5 专家重打分 + round-1 问题解决度 + 新问题)\n${JSON.stringify(r2, null, 2)}\n\n` +
  `## 报告要求\n` +
  `1. **打分 delta 表**: 6 维 × (v3 均分 → v4 均分 → Δ)。重点看 round-1 三块短板 预览6.1/导航5.6/元数据5.8 上去了多少。\n` +
  `2. **round-1 问题解决度**: 汇总 5 专家的 round1_resolution,哪几条全 addressed、哪几条还 partial/not_addressed。\n` +
  `3. **v4 后仍存/新暴露问题**(去重合并 5 专家的 new_remaining_problems,按提及次数排)。\n` +
  `4. **收敛判断**: 多少专家认为 is_optimal_now=true / need_another_round=true。给总体裁决: 「可执行」/「再迭代一轮(列必改项)」/「方向性返工」。\n` +
  `5. **一句话**: v4 离「最优」还差什么(若已到,明说到了)。\n\n` +
  `基于证据,引用具体专家发言。这份报告决定是否还要 round 3 或可以进执行。`,
  { label: 'synthesize-r2', phase: 'Synthesize delta' }
)

return { delta, r2 }
