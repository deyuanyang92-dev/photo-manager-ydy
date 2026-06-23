# 标本照片工作台 v0.02

把 `photo-platform-ydy/` 的 Web 原型复现为 Windows / macOS / Linux 桌面 GUI（PyQt6）。
功能 100% 照搬 Web 原型，界面美观+顺手，真实数据只读导入零丢失。

## Windows 直接使用

仓库内已提供 Windows 便携版：

- 下载：`releases/SpecimenPhotoWorkbench-v0.02-win64.zip`
- 解压到普通目录，例如 `D:\Apps\SpecimenPhotoWorkbench\`
- 双击 `SpecimenPhotoWorkbench.exe`
- 进入后在“项目总览”中新建或打开项目目录

不要直接在 zip 压缩包里运行 exe，必须先解压。更完整的安装和使用说明见
`docs/windows-install.md`。便携包为了能直接放进 GitHub 仓库，没有内置完整
`data\worms_taxonomy.json` 离线库；需要完整离线 WoRMS 查询时，把源码仓库
`data\worms_taxonomy.json` 和 `data\worms_cache.json` 复制到解压目录的
`_internal\data\`。

- 方案：`/root/.claude/plans/docs-cross-platform-desktop-gui-plan-md-hidden-frog.md`
- 决策记录：`docs/adr/`
- 每模块详细设计：`docs/specs/`（Opus 出，Sonnet 据此 TDD 实现）

## 开发

```bash
pip install -r requirements.txt
python main.py            # 启动空骨架
pytest tests/ -v         # 跑测试
```

Windows 桌面双击启动：双击仓库里的 `launch_windows.cmd`。它会通过 `wsl.exe`
进入当前 WSL 项目目录启动 GUI；如果失败，会保留错误窗口和 `/tmp/specimen-photo-workbench-launch.log`。

## Windows 打包 / 安装

在 Windows PowerShell 中构建发行包：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\build_windows.ps1
```

输出目录为 `dist\SpecimenPhotoWorkbench\`，可分发压缩包为
`dist\SpecimenPhotoWorkbench-v0.02-win64.zip`。安装与使用说明见
`docs/windows-install.md`。

## 红线（绝不破）

- **TIFF 绝不自动删**（无损母片）。仅允许用户在确认对话框后**手动**删除；后台/归档/整理流程一律不得删 TIFF。
- **JPG 删除前必须校验归档**：ZIP 已生成、完整性通过，且 ZIP 内每张 JPG 的名称/大小/SHA-256 与原图一致；删除开关开启时才会删除散落 JPG。
- 导入现有数据**只读**，原文件一字节不改（sha256 校验）。

## 状态

主力模块已落地：工作区 / 项目总览 / 项目文件夹树 / 标签打印（含 A4/A5 拼版 + 矢量设计器）/ WoRMS / 内置分类库 / 坐标工具 / 采集记录 / 采集地图 / 协作（内嵌 FastAPI + mDNS）/ 设置。详见 `CLAUDE.md` 与 `docs/specs/`。
