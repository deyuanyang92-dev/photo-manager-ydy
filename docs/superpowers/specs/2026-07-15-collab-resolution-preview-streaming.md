# 协作:分辨率可选的预览流式加载 + 对方工作区浏览 + TIF 同步加闸（2026-07-15）

> 用户问的三件事(现状核实后):
> 1. 访问 B 的 A 工作区 —— 数据能同步看, 但没有"进对方工作区浏览"的界面, 文件还要项目码配对。
> 2. JPG 高清 + 选分辨率加载 —— **完全不支持**(只能整份原图拷贝, 无网络流式、无分辨率选择)。
> 3. 直接同步 TIF —— 支持且 sha256 校验, 但**无大小闸、纯手动、TIF/JPG 不分级**。
>
> 用户裁定: 合理。#2 不只是"可以", 是**唯一能扛几百万照片的正确架构** ——
> 先流缩略图/预览, 想要原图/TIF 再按需取; "浏览就整份拷原图"是反模式。

## 现状(已核实 file:line)

- `collab_file_sync.build_project_manifest`(:264)列出 JPG/TIF/ZIP 完整原文件, `download`
  端点(collab_api.py:265)传**完整原文件字节**、无缩放。文件同步 `_require_group_project`
  (同 project_id 配对), 纯用户点侧栏按钮触发、不在 5s 周期、默认关。
- **没有任何网络图像加载 / 分辨率选择器**。`image_thumbnail` 只解码本地 path。
- 但**解码/缩放机器现成**: `_decode_image(path, max_size)` 解码 JPG/TIF 到指定尺寸,
  `scale_preview_image(img, max_edge)` 缩放, thumbnail_disk_cache 有本地预览缓存。

## 目标架构

```
浏览对方工作区照片 = 先要"缩略图/预览"(小, 快), 不是整份原图。
  A 请求 B 的某张图 @ maxDim=1600 -> B 服务端**生成**一张 1600px 预览 JPEG(小)-> 传给 A。
  A 想要原图/TIF -> 再按需取完整文件(现有 download 端点)。
分辨率可选: 请求带 maxDim(如 缩略/中等/高清/原图) + quality。
TIF 同步: 加大小闸(超阈值默认按需不自动) + "预览 vs 原文件"分级。
```

## 分阶段

**阶段 1(本轮落地)——承重新原语: 服务端按分辨率生成预览(纯服务层, TDD)**
`app/services/collab_preview_service.py::build_file_preview(file_path, max_dim, quality)
-> Optional[bytes]`:
- 复用 `image_thumbnail._decode_image(path, max_size=max_dim)` 解码 JPG/TIF 到 max_dim;
  QImage 编码成 JPEG 字节(QBuffer, 指定 quality)。
- ZIP / 非图像 -> None。max_dim 更大 -> 预览更大; quality 影响体积。
- 纯函数, temp 图像可测(offscreen), 不碰网络。这是"分辨率可选流式"缺的核心块。

**阶段 2(下一轮)——端点 + 客户端网络加载**
- `GET /api/collab/files/preview?relativePath=&maxDim=&quality=` -> 返回生成的预览 JPEG。
  网关沿用文件端点的 `_require_group_project`(隔离红线不动)。
- 客户端 `fetch_peer_preview(peer, rel_path, max_dim)` -> 存进本地预览缓存 -> 正常显示。

**阶段 3(下一轮)——对方工作区浏览 UI + 分辨率选择器**
- collab_view / 一个新面板: 列出对方共享工作区 -> 点进去看编号 -> 缩略图墙(走阶段2预览
  流)。分辨率选择器(缩略/中/高清/原图)。想要原图/TIF 点"下载原文件"。

**阶段 4(下一轮)——TIF 同步加闸分级**
- manifest / sync 加大小阈值: 超阈值 TIF 默认不自动拉(标"按需"), 用户显式点才拉完整。
- "同步整个项目"区分"只同步元数据+预览" vs "连原文件/TIF 一起"。

## 红线

- 文件/预览端点的 `_require_group_project`(项目码隔离)**不动**。
- 预览生成绝不改原文件(只读解码)。
- TIF 是无损母片: 预览是**另生成**的 JPEG, 不碰、不替代原 TIF。
- 大规模: 服务端预览生成可复用serving peer 自己的 thumbnail_disk_cache 加速(阶段2)。

## 非目标(YAGNI)

- 不做视频/非图像流式。
- 不引入中心图床。
- 阶段 1 不碰端点/UI/网络(纯生成原语)。
