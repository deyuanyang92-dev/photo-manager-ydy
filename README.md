# 标本照片工作台 — 桌面版（v3）

把 `photo-platform-ydy/` 的 Web 原型复现为 Windows / macOS / Linux 桌面 GUI（PyQt6）。
功能 100% 照搬 Web 原型，界面美观+顺手，真实数据只读导入零丢失。

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

## 红线（绝不破）

- **TIFF 绝不自动删**（无损母片）。仅允许用户在确认对话框后**手动**删除；后台/归档/整理流程一律不得删 TIFF。
- **JPG 删除四前置**全满足才删（ZIP 已生成 + 清单完整 + djxl 可恢复校验通过 + 删除开关开），默认不删。
- 导入现有数据**只读**，原文件一字节不改（sha256 校验）。

## 状态

主力模块已落地：工作区 / 项目总览 / 项目文件夹树 / 标签打印（含 A4/A5 拼版 + 矢量设计器）/ WoRMS / 内置分类库 / 坐标工具 / 采集记录 / 采集地图 / 协作（内嵌 FastAPI + mDNS）/ 设置。详见 `CLAUDE.md` 与 `docs/specs/`。
