# ADR 0001 — 桌面 GUI 采用 PyQt6，不用 Electron

状态：已接受（2026-06-03，用户拍板）

## 背景
Web 原型（app.js 18.5K 行 / server.js 107 端点 / 14 模块）已验证。需做 Win/Mac/Linux 桌面版。
ChatGPT 提 Electron（复用 web+Node），Sonnet 提 PyQt6（全重写）。3 个 Opus 评审对比真实代码。

## 决策
- **PyQt6**：原生打印好、单语言、与已确认的协作架构(FastAPI+mDNS)原生契合。放弃 Electron「复用已验证代码」的优势，以数据可移植 + TDD + web 行为当 oracle 对冲回归风险。
- 数据真相源 = 项目内 SQLite `_data/project.db`；从现有 `data/*.json` **只读导入**(sha256 证只读)。
- 界面：美观 + 顺手 + 零重学，**不追像素级复刻**（QSS 逐像素复刻 web 的阴影/动画/毛玻璃成本数月，不划算）。
- Windows 优先（Helicon 在 Windows），Mac/Linux 随后。
- 坐标交互地图用 QtWebEngine（接受 +150MB，隔离单视图）。
- model 分工：Opus 出结构化 spec → Sonnet TDD 实现 → 独立 Opus 验收。

## 必须按真实代码 1:1 复刻（之前 AI 版写错过）
1. Helicon CLI 参数 → `helicon.js:127-194`（`-silent/-save:/-mp:/-rp:/-sp:`）。
2. cjxl 参数 → `cjxl in out --distance 0 -e <effort>`（无损 bit-exact），禁 `--quality/--modular/-j`（compress.js:32-39）。
3. 删 JPG 前 → `verify_manifest_complete` + `verify_jxl_recoverable`（djxl 真实回解，archive.js:28-61）；djxl 缺失禁删。
4. JPG 归属 → firstSeenAt（非 mtime），4 级优先级（monitor-service.js:101-116）+ `seen_files` 持久化。
5. 路径安全 → 有状态 `SafePathRegistry`（server.js:83-102），判定用 relative_to 查 `..`。

## 数据 schema 要点
- uid 派生复刻 `db-utils.js:121-122`（`[province,site,station,id,storage,dateSeg].filter(Boolean).join("-")`）。
- specimens.`species` 实为**中文名**（"多鳞虫"），拉丁种名在 `scientificName`；全表加 `raw_json` 兜底。
- 补齐表：helicon_jobs / helicon_presets / worms_jobs / worms_taxonomy / user_taxonomy(seed/user 分离) / free_compose_batches / collab_events / seen_files；grouping 补 status/source/result_sequence/archive_zip/retired_tiff_paths；tasks 保留协作列；保留 explicitUnassigns。
- 导入幂等：per-row INSERT OR REPLACE + `_import_manifest`(源 sha)，非「表非空即跳过」。
