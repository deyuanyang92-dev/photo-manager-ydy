# Windows 安装与使用

## 直接使用打包版

1. 下载 `SpecimenPhotoWorkbench-v0.4-win64.zip`。
2. 解压到一个普通目录，例如 `D:\Apps\SpecimenPhotoWorkbench\`。
3. 双击 `SpecimenPhotoWorkbench.exe` 启动。
4. 首次使用时，在“项目总览”中新建或打开项目目录。

注意：

- 不要直接在 zip 压缩包里双击运行，必须先解压。
- Windows Defender / SmartScreen 可能提示未知发布者；这是未签名程序的正常提示。
- 项目数据保存在你选择的项目文件夹内，尤其是 `<项目目录>\_data\project.db`。
- GitHub 便携包没有内置完整 `data\worms_taxonomy.json` 离线库。需要完整离线
  WoRMS 查询时，从源码仓库复制 `data\worms_taxonomy.json` 和
  `data\worms_cache.json` 到解压目录的 `_internal\data\`。

## 从源码运行

需要 Windows Python 3.11 或更高版本。

```powershell
cd N:\claude\photo-platform-ydy-v3
py -m pip install -r requirements.txt
py main.py
```

WSL 环境也可以用仓库根目录的 `launch_windows.cmd` 启动。

## Windows 打包

在 Windows PowerShell 中运行：

```powershell
cd N:\claude\photo-platform-ydy-v3
powershell -ExecutionPolicy Bypass -File .\scripts\build_windows.ps1
```

输出：

- `dist\SpecimenPhotoWorkbench\SpecimenPhotoWorkbench.exe`
- `dist\SpecimenPhotoWorkbench-v0.4-win64.zip`

## 可选外部工具

程序本身可以启动和录入数据。以下工具只影响对应高级功能：

- Helicon Focus：用于真实景深合成；未安装时相关功能不可用或需手动处理。
- JPEG XL `cjxl/djxl`：用于更高压缩比的内部 JPG 中转；缺失时自动退回普通 JPG ZIP。

## 常用操作

- 新建项目：顶部“项目总览”或“项目树”中新建调查区域。
- 新建子目录：路径条下拉菜单中选择“新建文件夹…”。
- 标本筛选：左侧标本列表可用搜索框，`RNA N` 按钮只显示已取 RNA 的编号。
- 图片预览：成果 TIFF 双击打开预览；鼠标滚轮缩放，拖拽平移，方向键翻页，`Ctrl+0` 适合窗口，`Esc` 关闭。

## 快速上手

1. 启动后先打开“项目总览”。
2. 点“新建工作区”创建一个采集项目，或点“打开文件夹”选择已有项目目录。
3. 进入工作区后，在左侧“标本唯一编号 / voucher number”区域选择编号。
4. 在中间“拍摄队列”处理照片，成果 TIFF 会显示在“成果”区域。
5. 双击成果缩略图打开图片预览；预览窗口支持鼠标滚轮缩放、拖拽平移、方向键翻页、`Ctrl+0` 适合窗口、`Esc` 关闭。
