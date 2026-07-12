# 标本照片工作台 v0.59

面向标本拍摄、景深合成、原片归档、分类鉴定、采集记录和项目协作的 PyQt6 桌面软件，支持 Windows，源码也可在 macOS / Linux 环境运行。

当前需求与行为以 [`docs/REQUIREMENTS_CURRENT.md`](docs/REQUIREMENTS_CURRENT.md) 和 [`docs/PROJECT_MEMORY.md`](docs/PROJECT_MEMORY.md) 为准；`docs/audit/` 是历史审计快照，不代表当前缺口。

## 主要能力

- 工作台：JPG 多选、分组、批量合成、合成后整理、结果预览和状态恢复。
- 归档：普通 JPG ZIP 为默认格式；校验通过后才允许清理散落 JPG；TIFF 永不自动删除。
- 批量还原：成果区可多选 ZIP；安全任务并行解包，数据库状态顺序提交。
- 项目数据：项目树、汇总、跨工作区筛选、采集记录、地图和结果导出。
- 分类：本地分类库、WoRMS 匹配、批量更新、筛选和导出。
- 标签与协作：标签设计/打印、团队永久码和项目共享码等独立协作流程。
- 显示设置：可调整全局字体缩放并持久保存，弹窗与主界面使用同一字号体系。

## Windows 使用

1. 从 GitHub Releases 下载 `SpecimenPhotoWorkbench-v0.59-win64.zip`。
2. 完整解压到普通目录，不要直接在压缩包内运行。
3. 双击 `SpecimenPhotoWorkbench.exe`，在“项目总览”中新建或打开项目。

完整说明见 [`docs/windows-install.md`](docs/windows-install.md)。发布包由 GitHub Actions 在版本标签推送后自动构建。

## 开发

```bash
pip install -r requirements.txt
python main.py
QT_QPA_PLATFORM=offscreen python -m pytest tests/ -q
```

Windows PowerShell 构建便携包：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\build_windows.ps1
```

## 数据安全红线

- TIFF 母版不由合成、整理、归档或还原流程自动删除。
- JPG 只在 ZIP 存在、完整性校验和逐文件大小/SHA-256 校验通过后删除。
- 同名 ZIP 和结果文件使用临时文件校验后原子替换；失败或取消不破坏旧文件。
- ZIP 还原拒绝绝对路径和目录穿越；覆盖时先校验临时文件，再替换目标。
- 已关联 ZIP 还原成功后直接删除项目内 ZIP，不创建 `_retired-zip` 备份；删除失败时保留数据库登记。
- 导入和查询型功能不修改用户原始数据文件。

## 文档入口

- [当前需求基线](docs/REQUIREMENTS_CURRENT.md)
- [项目长期记忆与不可回归项](docs/PROJECT_MEMORY.md)
- [文档索引](docs/README.md)
- [Windows 安装](docs/windows-install.md)
- [架构与模块边界](docs/architecture/module-map.md)
