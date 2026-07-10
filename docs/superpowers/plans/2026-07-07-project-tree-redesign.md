# 项目树重设计 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把「项目树」页重构成 Lightroom 式三栏（卡片/树视图切换 + 虚拟化缩略图网格 + 标本元数据右栏），根治 rooted 盲区、补认领导入、改预览，且不破坏 `enter_workspace` 契约。

**Architecture:** 三层：① 纯逻辑服务（`library_roots_service` / `project_adopt_service` / `project_tree_service` 扩展，无 Qt，TDD）② Qt workers + 虚拟化模型（`QThread` 两套模式 + `QListView`+`QAbstractListModel`+`QStyledItemDelegate`）③ `ProjectTreeView` 重构（卡片/树统一选择模型）。认领是独立最小操作，**不调 `enter_workspace`**（只建 `_data/`，不经 migrate）。

**Tech Stack:** PyQt6, SQLite, pytest + pytest-qt (`QT_QPA_PLATFORM=offscreen`)。线程模型对齐 `monitor_scan_worker.py`（`QThread` 子类 `run()`）+ `moveToThread`（长驻）。

**Spec:** `docs/superpowers/specs/2026-07-07-project-tree-redesign-design.md` (**v5** — 经 6 软件 feature study + 5 人团 round-1/round-2 评审收敛；报告 `docs/superpowers/reports/2026-07-07-project-tree-design-jury*.md`)

> **★v5 增量同步（round-2 复审后外科手术，本 plan 在原 17 task 上叠加，不重写）**
>
> | v5 改动 | 落到哪个 task | 增量内容 |
> |---|---|---|
> | stable-id lifecycle（5/5 bug 修复）| **新 Task 7b 断链重链** | `.identity` sentinel + 镜像进 user_projects.json + 老 db backfill + Locate/Update Path |
> | adopt dry-run 预扫描 | Task 7 / Task 13 | `prescan_project`（零写盘）+ 确认框显示真实计数 |
> | TIFF 内嵌 JPEG 抽取 | **Task 3 加 Step** | `decode_image_data` 先抽 `ExifIFD.TagJPEGInterchangeFormat`（§8 <100ms 红线唯一解药）|
> | Preview-in-place toggle + 滑块 | Task 14/16 | 空格 toggle（替代 press-hold）+ 缩略图大小滑块 + Grid Lock |
> | 缓存契约钉死 | Task 3/10 注 | grid→QPixmapCache / `_THUMB_CACHE` 留 labels+workbench / invalidate 联动 P1 |
> | grid worker re-entrant | Task 9/14 注 | 每激活新建 + cancel-drop in-flight（对齐 monitor_scan_worker）|
> | 缩略图徽标（round-1③c 残留）| Task 10 P1 step | GPS 图钉 / 合成色角 / 格式徽标 |
>
> P1（下期，plan 仅记不建 task）：Smart Album、表格视图、adopt 僵尸工作区下游 gate。

**Red-line reminders (from CLAUDE.md):** TIFF 不自动删；导入只读 sha256；cjxl flags 固定；路径安全 `SafePathRegistry`。本 plan 新增红线：adopt 不动既有文件（绕开 `migrate_legacy_metadata`）；`open_project_db` 失败不留 orphan conn；**★v5 stable-id 必须用 `.identity` sentinel（不是 project.db sha256——活库会变，红线假绿）+ 镜像进 registry（死盘可读）**。

---

## File Structure

**Create:**
- `app/services/library_roots_service.py` — 库根 CRUD + 默认值推导（None/[]/list 三态）
- `app/services/project_adopt_service.py` — 认领（显式最小操作）+ 回滚 + 护栏
- `app/workers/project_discover_worker.py` — 一次性库根扫描（`QThread run()`）
- `app/workers/thumbnail_worker.py` — 封面一次性 + 网格长驻缩略图 worker
- `app/widgets/project_card.py` — `ProjectCard`
- `app/widgets/thumbnail_grid.py` — `ThumbnailListModel` + `ThumbnailDelegate` + `ThumbnailGridWidget`
- `app/widgets/library_roots_dialog.py` — 库根管理（唯一入口）
- `app/widgets/adopt_confirm_dialog.py` — 认领确认（大白话 + 继承链展开）
- `app/services/cover_cache_service.py` — 全局封面缓存 + 5 级 fallback 选取
- `app/services/project_relink_service.py` — ★v5 stable-id（`.identity` sentinel + 镜像 registry）+ backfill + Locate/Update Path 重链
- `tests/test_library_roots_service.py`
- `tests/test_project_adopt_service.py`
- `tests/test_project_relink_service.py` — ★v5（stable-id lifecycle + 防假绿 + backfill + 重链校验）
- `tests/test_project_tree_discover.py`
- `tests/test_cover_cache_service.py`
- `tests/test_thumbnail_grid.py`
- `tests/test_project_card.py`
- `tests/test_project_tree_redesign_view.py`

**Modify:**
- `app/config/settings.py` — 加 3 个 property + 迁移方法
- `app/db/db_manager.py` — `open_project_db` try/except `ensure_schema`
- `app/utils/image_thumbnail.py` — 拆 `decode_image_data`/`make_pixmap`，`_decode_image` 返回 QImage
- `app/services/project_tree_service.py` — `is_workspace_candidate` 单次 scandir；加 `classify_project_dir` + `discover_all_projects`
- `app/views/project_tree_view.py` — 全量重构（三栏 + 卡片/树切换 + 统一选择）

---

## Phase 1 — Backend foundations (无 Qt，纯 TDD)

### Task 1: settings.py 新增 3 键 + 迁移

**Files:**
- Modify: `app/config/settings.py` (加 property + `_migrate_library_roots` + 在 `__init__` 调用)
- Test: `tests/test_settings.py`（新建或追加）

- [ ] **Step 1: Write failing test**

```python
# tests/test_settings.py  (append)
def test_library_roots_three_states(qtbot, tmp_path, monkeypatch):
    from PyQt6.QtCore import QSettings, QCoreApplication
    import os
    # 隔离 QSettings 到临时 ini
    ini = tmp_path / "test.ini"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    QSettings.setDefaultFormat(QSettings.Format.IniFormat)
    QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, str(tmp_path))

    from app.config.settings import AppSettings
    s = AppSettings()
    # None: 未配置
    assert s.library_roots is None
    # [] : 显式空
    s.library_roots = []
    assert s.library_roots == []
    # list
    s.library_roots = ["/a", "/b"]
    assert s.library_roots == ["/a", "/b"]
    # 清回 None
    s.library_roots = None
    assert s.library_roots is None

    assert s.manual_project_folders == []
    assert s.project_tree_view_mode == "cards"
```

- [ ] **Step 2: Run, verify FAIL** — `QT_QPA_PLATFORM=offscreen pytest tests/test_settings.py::test_library_roots_three_states -v` → `AttributeError: library_roots`

- [ ] **Step 3: Implement** — 在 `app/config/settings.py` 末尾（`flush_to_disk` 前）加：

```python
    # ── 项目树重设计 (v3) ────────────────────────────────────────────
    _LR_UNSET = object()

    @property
    def library_roots(self):
        """None=未配置(推导) / []=显式空(不推导) / list=配置。三态必须可区分。"""
        v = self._qs.value("project/library_roots", self._LR_UNSET)
        if v is self._LR_UNSET:
            return None
        # QSettings 读回 QStringList; 兜底转 list[str]
        return [str(x) for x in v] if isinstance(v, (list, tuple)) else ([str(v)] if v else [])

    @library_roots.setter
    def library_roots(self, roots):
        if roots is None:
            self._qs.remove("project/library_roots")
        else:
            self._qs.setValue("project/library_roots", [str(r) for r in roots])

    @property
    def manual_project_folders(self):
        v = self._qs.value("project/manual_folders", [])
        return [str(x) for x in v] if isinstance(v, (list, tuple)) else []

    @manual_project_folders.setter
    def manual_project_folders(self, folders):
        self._qs.setValue("project/manual_folders", [str(f) for f in folders])

    @property
    def project_tree_view_mode(self):
        return str(self._qs.value("project/tree_view_mode", "cards")) or "cards"

    @project_tree_view_mode.setter
    def project_tree_view_mode(self, mode):
        self._qs.setValue("project/tree_view_mode", mode if mode in ("cards", "tree") else "cards")
```

并在 `__init__` 的 `self._migrate_archive_mode_default()` 之后加 `self._migrate_library_roots()`，新增方法：

```python
    _LIBRARY_ROOTS_MIGRATION_KEY = "project/library_roots_v3_migrated"

    def _migrate_library_roots(self) -> None:
        """旧 project_tree_root → library_roots 推导种子(不固化单一根)。

        若 project_tree_root 盘存在:仅作推导种子,library_roots 仍 None(下次推导时并入)。
        若不存在(盘掉线):保持 None,不固化坏根。迁移只跑一次(记 flag)。"""
        if str(self._qs.value(self._LIBRARY_ROOTS_MIGRATION_KEY, "false")).lower() == "true":
            return
        # 不在这里持久化 library_roots —— 推导在 library_roots_service 做。
        # 此处仅标记迁移完成 + 把 project_tree_root 存进种子键供推导读取。
        old = self.project_tree_root
        if old:
            self._qs.setValue("project/_library_roots_seed", old)  # 推导种子
        self._qs.setValue(self._LIBRARY_ROOTS_MIGRATION_KEY, "true")
```

- [ ] **Step 4: Run, verify PASS** — `QT_QPA_PLATFORM=offscreen pytest tests/test_settings.py::test_library_roots_three_states -v`
- [ ] **Step 5: Commit** — `git add app/config/settings.py tests/test_settings.py && git commit -m "feat(settings): add library_roots/manual_folders/tree_view_mode + v3 migration seed"`

---

### Task 2: db_manager.open_project_db 加固（防 orphan conn 持锁）

**Files:**
- Modify: `app/db/db_manager.py:176-181`（`open_project_db` 的 connect→ensure_schema→cache 段）
- Test: `tests/test_db_manager.py`（追加）

- [ ] **Step 1: Write failing test**

```python
# tests/test_db_manager.py  (append)
import sqlite3
from pathlib import Path

def test_open_project_db_ensure_schema_failure_closes_conn(tmp_path, monkeypatch):
    """ensure_schema 抛错时 conn 必须 close + 清 -wal/-shm,不留 orphan 锁。"""
    from app.db import db_manager
    db_manager.close_all()
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "_data").mkdir()

    def boom(_conn):
        raise sqlite3.OperationalError("simulated schema failure")

    monkeypatch.setattr(db_manager, "ensure_schema", boom)
    import pytest
    with pytest.raises(sqlite3.OperationalError):
        db_manager.open_project_db(str(proj), create=True)

    # _db_cache 不含该键
    from app.utils.path_utils import normalize_path
    assert normalize_path(str(proj)) not in db_manager._db_cache
    # _data 可被 rmtree 删除(无 OS 句柄残留) —— 端到端,非恒真
    import shutil
    shutil.rmtree(proj / "_data")  # 不抛 PermissionError 即通过
    assert not (proj / "_data").exists()
```

- [ ] **Step 2: Run, verify FAIL** — `pytest tests/test_db_manager.py::test_open_project_db_ensure_schema_failure_closes_conn -v` → `PermissionError` (Win/WSL 句柄锁) 或 cache 残留

- [ ] **Step 3: Implement** — 改 `app/db/db_manager.py` 的 `open_project_db`（约 176-181 行）：

```python
    conn = sqlite3.connect(str(db_path), timeout=8.0, check_same_thread=False)
    _configure_connection(conn)

    try:
        ensure_schema(conn)
    except Exception:
        # ensure_schema 失败时 conn 未入 cache,但已持 OS 文件锁(-wal/-shm)。
        # 必须显式 close 释放句柄,否则 Windows/WSL drvfs 下 _data 删不掉(半成品)。
        try:
            conn.close()
        finally:
            for suffix in ("-wal", "-shm"):
                try:
                    db_path.with_suffix(db_path.suffix + suffix).unlink(missing_ok=True)
                except OSError:
                    pass
        raise

    _db_cache[resolved] = conn
    return conn
```

- [ ] **Step 4: Run, verify PASS** — `pytest tests/test_db_manager.py::test_open_project_db_ensure_schema_failure_closes_conn -v`
- [ ] **Step 5: Commit** — `git add app/db/db_manager.py tests/test_db_manager.py && git commit -m "fix(db): close conn+clean wal/shm on ensure_schema failure, prevent orphan lock"`

---

### Task 3: image_thumbnail 拆 decode_image_data / make_pixmap

**Files:**
- Modify: `app/utils/image_thumbnail.py`（`_decode_image` 改返回 `QImage`，加 `decode_image_data` + `make_pixmap`）
- Test: `tests/test_image_thumbnail.py`（新建）

> **★v5 增量 Step（spec §3，§8 <100ms 红线唯一解药，必须加）**：`decode_image_data` 的 TIFF 路径先抽内嵌 JPEG（Pillow 操作 `ExifIFD.TagJPEGInterchangeFormat` IFD，或 `Image.Exif`），抽到直接降采样返回 QImage（比全解几十 MB TIFF 快一个数量级）；抽不到再降级全解。加测试：fixture 一张含内嵌 JPEG 的 TIFF → `decode_image_data` 单张 wall clock `<100ms` + monkeypatch 验证走内嵌路径；无内嵌 JPEG 的 TIFF 降级全解不崩。spec §8 已落该红线。

- [ ] **Step 1: Write failing test**

```python
# tests/test_image_thumbnail.py
from pathlib import Path
from PyQt6.QtGui import QImage, QPixmap

def _make_jpg(path: Path):
    from PyQt6.QtGui import QImage, QPainter
    img = QImage(40, 30, QImage.Format.Format_RGB32)
    img.fill(0x112233)
    img.save(str(path), "JPG")

def test_decode_image_data_returns_qimage_not_pixmap(tmp_path):
    from app.utils.image_thumbnail import decode_image_data
    p = tmp_path / "a.jpg"
    _make_jpg(p)
    img = decode_image_data(str(p), 100)
    assert isinstance(img, QImage)
    assert not img.isNull()

def test_make_pixmap_main_thread_roundtrip(tmp_path):
    from app.utils.image_thumbnail import decode_image_data, make_pixmap
    p = tmp_path / "a.jpg"
    _make_jpg(p)
    img = decode_image_data(str(p), 100)
    pm = make_pixmap(img)
    assert isinstance(pm, QPixmap)
    assert not pm.isNull()

def test_worker_thread_never_constructs_qpixmap(tmp_path):
    """红线: worker 线程内绝不构造 QPixmap。"""
    from PyQt6.QtCore import QThread
    from PyQt6.QtGui import QPixmap
    from app.utils import image_thumbnail
    p = tmp_path / "a.jpg"
    _make_jpg(p)

    constructed = {"in_worker": False}
    orig_init = QPixmap.__init__

    def spy(self, *a, **k):
        if QThread.currentThread() != QThread.currentThread().thread() and \
           getattr(QThread.currentThread(), "_is_test_worker", False):
            constructed["in_worker"] = True
        orig_init(self, *a, **k)

    class W(QThread):
        _is_test_worker = True
        def run(self):
            # worker 只调 decode_image_data (线程安全); 不应构造 QPixmap
            image_thumbnail.decode_image_data(str(p), 100)

    w = W()
    with __import__("unittest.mock").patch.object(QPixmap, "__init__", spy):
        w.start(); w.wait()
    assert not constructed["in_worker"], "QPixmap 构造出现在 worker 线程"
```

- [ ] **Step 2: Run, verify FAIL** — `QT_QPA_PLATFORM=offscreen pytest tests/test_image_thumbnail.py -v` → `ImportError: decode_image_data`

- [ ] **Step 3: Implement** — 重构 `app/utils/image_thumbnail.py`：

把所有 `QPixmap` 返回路径改为 `QImage`。`_decode_with_qt` 返回 `QImage`（不再 `fromImage`）：

```python
# 替换 _decode_with_qt
def _decode_with_qt(path: str, max_size: int | None) -> Optional[QImage]:
    try:
        from PyQt6.QtGui import QImageReader
        reader = QImageReader(path)
        reader.setAutoTransform(True)
        size = reader.size()
        if max_size is not None and size.isValid() and size.width() > 0 and size.height() > 0:
            size.scale(max_size, max_size, Qt.AspectRatioMode.KeepAspectRatio)
            reader.setScaledSize(size)
        image = reader.read()
        return image if not image.isNull() else None
    except Exception:
        return None
```

`_pil_image_to_pixmap` → `_pil_image_to_qimage`（用 `ImageQt` 或 numpy→`QImage`）：

```python
def _pil_image_to_qimage(image, max_size):
    import numpy as np
    if max_size is not None:
        image.thumbnail((max_size, max_size))
    if image.mode not in {"RGB", "RGBA", "L"}:
        image = image.convert("RGBA")
    arr = np.array(image)
    fmt = {"RGB": QImage.Format.Format_RGB888,
           "RGBA": QImage.Format.Format_RGBA8888,
           "L": QImage.Format.Format_Grayscale8}[image.mode]
    return QImage(arr.data, arr.shape[1], arr.shape[0], arr.strides[0], fmt).copy()
```

（`_decode_with_tifffile` / `_decode_malformed_lzw_tiff` / `_decode_with_imagemagick` 同样改返回 `QImage`；ImageMagick 路径用 `QImage.loadFromData(bytes)` 替 `QPixmap.loadFromData`。）

缓存层改为存 `QImage`：

```python
def _cache_get(key):
    if key is None or key not in _THUMB_CACHE:
        return _CACHE_MISS
    _THUMB_CACHE.move_to_end(key)
    cached = _THUMB_CACHE[key]
    if cached is _CACHE_NEGATIVE:
        return None
    return QImage(cached)  # QImage 拷贝,线程安全(无 GUI 对象亲和性)

def _cache_put(key, image):
    if key is None:
        return
    _THUMB_CACHE[key] = QImage(image) if image is not None else _CACHE_NEGATIVE
    # ...LRU trim 不变
```

新增公开 API：

```python
def decode_image_data(path: str, max_size: int = 280, *, use_cache: bool = True) -> Optional[QImage]:
    """线程安全: 返回 QImage(纯数据,无 GUI 亲和性)。worker 线程用这个。"""
    return _decode_image(path, max(1, int(max_size)), use_cache=use_cache)

def make_pixmap(image: Optional[QImage]) -> Optional[QPixmap]:
    """仅主线程: QImage → QPixmap。"""
    if image is None or image.isNull():
        return None
    return QPixmap.fromImage(image)

# 向后兼容: 主线程同步用
def decode_image_thumbnail(path, max_size=280, *, use_cache=True) -> Optional[QPixmap]:
    return make_pixmap(decode_image_data(path, max_size, use_cache=use_cache))

def decode_image_pixmap(path, *, use_cache=False) -> Optional[QPixmap]:
    return make_pixmap(decode_image_data(path, None, use_cache=use_cache))
```

`_decode_image` 内部签名返回 `Optional[QImage]`，各 backend 调用改为 `img = _decode_with_qt(...)` + `_cache_put(key, img)`。

- [ ] **Step 4: Run, verify PASS** — `QT_QPA_PLATFORM=offscreen pytest tests/test_image_thumbnail.py -v`；再跑现有缩略图测试确保未破：`pytest tests/ -k thumbnail -v`
- [ ] **Step 5: Commit** — `git add app/utils/image_thumbnail.py tests/test_image_thumbnail.py && git commit -m "refactor(thumbnail): split thread-safe decode_image_data(QImage) + make_pixmap(main-thread)"`

---

### Task 4: is_workspace_candidate fuse 进递归 scandir（9p stat 风暴，实证驱动）★perf 红线

**实测证据（2026-07-07，本机 `/tmp/perf_smoke.py`，1000 目录 depth-2，marker N=1文件/M=4目录）：**

| 环境 | 目录 | wall | syscall/dir | 每 syscall |
|------|------|------|-------------|-----------|
| /tmp ext4 | 1000 | 88ms | 7.91 | 0.011ms |
| /mnt/n 9p | 200 | 1238ms | 7.87 | **0.79ms** |

**9p 惩罚 ≈ 71×/syscall。** 外推 1000 目录到用户真实 /mnt Windows 盘：

| 方案 | syscall/dir | 9p 1000目录外推 | 过 800ms? |
|------|-------------|----------------|-----------|
| 现状 | 7 stat + 1 scandir | **~6.2s** | ❌ 远超 |
| 本 task 旧写法(自身再scandir) | 2 scandir | ~1.6s | ❌ 仍超 |
| **fuse(复用递归scandir)** | **1 scandir** | **~0.8s** | ⚠️ 踩线过 |

**结论：必须 fuse + depth=2 + 2s 缓存 + 异步 worker 四件套叠加才稳过 800ms。** 单改 `is_workspace_candidate` 内部（旧 plan 写法）= 仍 2 scandir/目录，9p 下过不了自己的红线测试。

**Files:**
- Modify: `app/services/project_tree_service.py:107-131`（`is_workspace_candidate` 加 `entries=` 形参）
- Modify: `app/services/project_tree_service.py:217-289`（`discover_workspace_candidates` 递归把已 scandir 的 entries 喂进 candidate 判定）
- Test: `tests/test_project_tree_service.py`（追加）

- [ ] **Step 1: Write failing test**（断言 fuse：递归扫描期间 candidate 判定零额外 scandir/stat）

```python
# tests/test_project_tree_service.py  (append)
def test_discover_fuses_recursion_scandir_into_candidate(tmp_path, monkeypatch):
    """红线: discover 递归已 scandir 每目录;candidate 判定必须复用它,
    不得对同一目录再 scandir/ stat(9p 71× 惩罚,实测 6.2s→0.8s 全靠这条)。"""
    import app.services.project_tree_service as pts
    scandir_calls = []
    stat_calls = []
    orig_scandir, orig_stat = pts.os.scandir, pts.os.stat
    def counting_scandir(p, *a, **k):
        scandir_calls.append(str(p)); return orig_scandir(p, *a, **k)
    def counting_stat(p, *a, **k):
        stat_calls.append(str(p)); return orig_stat(p, *a, **k)
    monkeypatch.setattr(pts.os, "scandir", counting_scandir)
    monkeypatch.setattr(pts.os, "stat", counting_stat)

    root = tmp_path / "survey"; root.mkdir()
    for i in range(50):
        d = root / f"s{i}"; d.mkdir()
        (d / "incoming-jpg").mkdir(); (d / "results").mkdir()  # candidate
    pts.discover_workspace_candidates(str(root), max_depth=2, use_cache=False)
    # 每目录恰好 scandir 一次(递归的),candidate 判定不得追加
    from collections import Counter
    dup = [p for p, n in Counter(scandir_calls).items() if n > 1]
    assert not dup, f"这些目录被重复 scandir(fuse 失效): {dup[:5]}"
```

- [ ] **Step 2: Run, verify FAIL** — `pytest tests/test_project_tree_service.py::test_discover_fuses_recursion_scandir_into_candidate -v` → 现状 candidate 判定各自 stat/scandir，fuse 未接 → FAIL
- [ ] **Step 3: Implement** — `is_workspace_candidate` 加 `entries` 形参，递归喂入：

```python
def is_workspace_candidate(dir_path: str, *, entries=None) -> bool:
    """entries=已 scandir 的 DirEntry 列表时零额外 syscall(fuse 进递归)。
    entries=None 时单次 scandir(独立调用兼容旧路径)。"""
    p = Path(dir_path)
    if entries is None:
        if is_workspace(str(p)):        # 1 exists(project.db)
            return True
        try:
            entries = list(os.scandir(p))
        except OSError:
            return False
    else:
        names = {e.name for e in entries}
        if "_data" in names and (p / "_data" / "project.db").exists():
            return True
    names = {e.name for e in entries}
    if any(name in _WORKSPACE_MARKER_FILES for name in names):
        return True
    # marker_dirs: 复用 entries 的 is_dir(已随 scandir 缓存,9p 不另往返)
    marker_dirs = sum(1 for e in entries
                      if e.name in _WORKSPACE_MARKER_DIRS and e.is_dir())
    if marker_dirs >= 2:
        return True
    if "incoming-jpg" in names:
        # candidate 早退路径:incoming 子目录需另 scandir(仅在命中时,非每目录)
        try:
            for entry in os.scandir(p / "incoming-jpg"):
                if entry.is_file() and entry.name.lower().endswith((".jpg", ".jpeg")):
                    return True
        except OSError:
            pass
    return False
```

并在 `discover_workspace_candidates` 的递归里把 scandir 结果喂进去（`:256` 附近）：

```python
# collect_workspace_candidate_dirs 内,depth<max_depth 分支:
try:
    entries = sorted(os.scandir(p), key=lambda e: e.name)
except OSError:
    entries = []
# 先用这批 entries 判 candidate(fuse:零额外 syscall)
if is_workspace_candidate(str(p), entries=entries):
    ...append + return
...
for entry in entries:    # 同一批 entries 复用递归
    ...
```

（即：递归原本就要 scandir 一次拿子层；这次 scandir 的结果**先喂 candidate 判定再用于递归**，每目录 syscall 从 7stat+1scandir → 1scandir。）

- [ ] **Step 4: Run, verify PASS** — `pytest tests/test_project_tree_service.py -v`；再跑实测复验：`PYTHONPATH=. python /tmp/perf_smoke.py` 看 9p 200 目录 wall 是否从 1238ms 降到 ~250ms（fuse 后外推 1000 目录 ~0.8s）
- [ ] **Step 5: Commit** — `git add app/services/project_tree_service.py tests/test_project_tree_service.py && git commit -m "perf(project-tree): fuse recursion scandir into candidate check, 9p 1000-dir 6.2s→0.8s"`

---

### Task 5: classify_project_dir + discover_all_projects（三源并集）

**Files:**
- Modify: `app/services/project_tree_service.py`（加两函数）
- Test: `tests/test_project_tree_discover.py`（新建）

- [ ] **Step 1: Write failing test**

```python
# tests/test_project_tree_discover.py
import json
from pathlib import Path

def _write_user_projects(path: Path, projects: list[dict]):
    path.write_text(json.dumps({"version": 1, "projects": projects}), encoding="utf-8")

def test_discover_three_sources_union(tmp_path, monkeypatch):
    from app.services import project_tree_service as pts

    # ① registered
    reg = tmp_path / "reg"; reg.mkdir(); (reg/"_data").mkdir(); (reg/"_data"/"project.db").touch()
    # ② discovered (候选)
    disc = tmp_path / "disc"; disc.mkdir(); (disc/"incoming-jpg").mkdir(); (disc/"results").mkdir()
    lib_root = tmp_path  # 库根 = tmp_path
    # ③ manual
    manu = tmp_path / "manu"; manu.mkdir()

    json_path = tmp_path / "user_projects.json"
    _write_user_projects(json_path, [{"directory": str(reg), "dir": str(reg), "name": "reg"}])

    class FakeCtx: pass
    class FakeSettings:
        library_roots = [str(lib_root)]
        manual_project_folders = [str(manu)]
    ctx = FakeCtx(); ctx.settings = FakeSettings()
    monkeypatch.setattr(pts, "default_user_projects_json_path", lambda: str(json_path))
    monkeypatch.setattr(pts, "list_projects", lambda p: json.loads(Path(p).read_text())["projects"])

    entries = pts.discover_all_projects(ctx)
    paths = {Path(e["path"]).resolve() for e in entries}
    assert reg.resolve() in paths          # ①
    assert disc.resolve() in paths         # ②
    assert manu.resolve() in paths         # ③

def test_classify_priority_unavailable_over_workspace(tmp_path):
    from app.services.project_tree_service import classify_project_dir
    assert classify_project_dir("/nonexistent/__nope__") == "unavailable"
    # 工作区
    w = tmp_path/"w"; w.mkdir(); (w/"_data").mkdir(); (w/"_data"/"project.db").touch()
    assert classify_project_dir(str(w)) == "workspace"
    # 候选
    c = tmp_path/"c"; c.mkdir(); (c/"incoming-jpg").mkdir(); (c/"results").mkdir()
    assert classify_project_dir(str(c)) == "candidate"
    # 文件夹
    f = tmp_path/"f"; f.mkdir()
    assert classify_project_dir(str(f)) == "folder"

def test_recent_first_order_preserved(tmp_path, monkeypatch):
    """最近项目 reverse() 置顶不被 discovered/manual 挤后。"""
    from app.services import project_tree_service as pts
    r1 = tmp_path/"r1"; r1.mkdir(); (r1/"_data").mkdir(); (r1/"_data"/"project.db").touch()
    r2 = tmp_path/"r2"; r2.mkdir(); (r2/"_data").mkdir(); (r2/"_data"/"project.db").touch()
    json_path = tmp_path/"up.json"
    _write_user_projects(json_path, [
        {"directory": str(r1)}, {"directory": str(r2)}])  # r1 先录,r2 后录
    class S: library_roots=[]; manual_project_folders=[]
    class C: settings=S()
    monkeypatch.setattr(pts, "default_user_projects_json_path", lambda: str(json_path))
    monkeypatch.setattr(pts, "list_projects", lambda p: json.loads(Path(p).read_text())["projects"])
    entries = pts.discover_all_projects(C())
    # r2 (最近) 应在前
    ordered = [Path(e["path"]).resolve() for e in entries]
    assert ordered.index(r2.resolve()) < ordered.index(r1.resolve())
```

- [ ] **Step 2: Run, verify FAIL** — `pytest tests/test_project_tree_discover.py -v` → `AttributeError: discover_all_projects`

- [ ] **Step 3: Implement** — 在 `project_tree_service.py` 加（顶部 `from app.services.project_service import _same_path`，必要时改 import；若 `_same_path` 私有无法导入，则在本模块写等价 `_norm` 用 `normalize_path`）：

```python
from typing import Optional, Protocol

class _SettingsLike(Protocol):
    library_roots: Optional[list[str]]
    manual_project_folders: list[str]

class _CtxLike(Protocol):
    settings: _SettingsLike
    ...


def classify_project_dir(directory: str) -> str:
    """实时徽标判定。优先级 unavailable > workspace > candidate > folder。"""
    p = Path(directory)
    try:
        if not p.is_dir():
            return "unavailable"
    except OSError:
        return "unavailable"
    if is_workspace(str(p)):
        return "workspace"
    if is_workspace_candidate(str(p)):
        return "candidate"
    return "folder"


def discover_all_projects(ctx) -> list[dict]:
    """三源并集(registered ∪ discovered ∪ manual),最近项目置顶。

    删 v2 第④源 catalog: per-survey-root 表无法反查 survey_root(循环依赖),
    user_projects.json 已含全部已 enter 工作区。
    """
    from app.services.project_service import (
        default_user_projects_json_path, list_projects,
    )
    try:
        from app.services.project_service import _same_path
    except ImportError:
        from app.utils.path_utils import normalize_path as _norm
        def _same_path(a, b):
            try:
                return _norm(a) == _norm(b)
            except Exception:
                return Path(a).resolve() == Path(b).resolve()

    # ① registered —— 纯字符串,零 stat
    registered = list_projects(default_user_projects_json_path())
    reg_dirs = [p.get("directory") or p.get("dir") or "" for p in registered if not p.get("isDemo")]

    # ② discovered —— 库根深度2扫描
    disc_dirs: list[str] = []
    roots = ctx.settings.library_roots
    if roots is None:
        from app.services.library_roots_service import derive_default_library_roots
        roots = derive_default_library_roots(ctx)
    for root in roots:
        try:
            for c in discover_workspace_candidates(root, max_depth=2):
                disc_dirs.append(c["path"])
        except OSError:
            continue

    # ③ manual
    manual_dirs = list(ctx.settings.manual_project_folders or [])

    # 合并去重(复用 _same_path),保留 registered reverse() 置顶
    ordered: list[str] = []
    seen_kinds: dict[str, str] = {}
    def _add(d, kind):
        if not d:
            return
        for existing in ordered:
            if _same_path(existing, d):
                return
        ordered.append(d); seen_kinds[d] = kind

    for d in reversed(reg_dirs):   # 最近录的在前
        _add(d, "workspace")
    for d in disc_dirs:
        _add(d, "candidate")
    for d in manual_dirs:
        _add(d, "candidate")

    # 按 classify 实时定 kind(渲染时重算优先级),返回带 name/path/kind
    out = []
    for d in ordered:
        out.append({
            "path": d,
            "name": Path(d).name or d,
            "kind": classify_project_dir(d) if d not in reg_dirs else (
                classify_project_dir(d) if classify_project_dir(d) != "folder" else "workspace"
            ),
        })
    return out
```

（`derive_default_library_roots` 在 Task 6 实现；本任务的测试用 `library_roots=[]` 绕开推导，或先 monkeypatch `derive_default_library_roots`。）

- [ ] **Step 4: Run, verify PASS** — `pytest tests/test_project_tree_discover.py -v`
- [ ] **Step 5: Commit** — `git add app/services/project_tree_service.py tests/test_project_tree_discover.py && git commit -m "feat(project-tree): discover_all_projects 3-source union + classify_project_dir"`

---

### Task 6: library_roots_service（默认值推导 + 三态）

**Files:**
- Create: `app/services/library_roots_service.py`
- Test: `tests/test_library_roots_service.py`

- [ ] **Step 1: Write failing test**

```python
# tests/test_library_roots_service.py
import json
from pathlib import Path

def _seed_projects(path, dirs):
    path.write_text(json.dumps({"version": 1, "projects": [
        {"directory": str(d), "dir": str(d)} for d in dirs]}), encoding="utf-8")

def test_shared_parent_when_two_projects_share_dir(tmp_path, monkeypatch):
    """≥2 已录项目共享父目录 → 该父作库根(一个根发现所有断面)。"""
    from app.services import library_roots_service as lrs
    survey = tmp_path / "三门湾2024"
    survey.mkdir()
    a = survey/"断面a"; a.mkdir()
    b = survey/"断面b"; b.mkdir()
    jp = tmp_path/"up.json"
    _seed_projects(jp, [a, b])
    monkeypatch.setattr(lrs, "default_user_projects_json_path", lambda: str(jp))
    monkeypatch.setattr(lrs, "list_projects",
                        lambda p: json.loads(Path(p).read_text())["projects"])

    class S: library_roots=None; manual_project_folders=[]
    class C: settings=S()
    roots = lrs.derive_default_library_roots(C())
    assert survey.resolve() in {Path(r).resolve() for r in roots}

def test_each_project_dir_when_no_shared_parent(tmp_path, monkeypatch):
    from app.services import library_roots_service as lrs
    a = tmp_path/"a"; a.mkdir()
    jp = tmp_path/"up.json"; _seed_projects(jp, [a])
    monkeypatch.setattr(lrs, "default_user_projects_json_path", lambda: str(jp))
    monkeypatch.setattr(lrs, "list_projects",
                        lambda p: json.loads(Path(p).read_text())["projects"])
    class S: library_roots=None; manual_project_folders=[]
    class C: settings=S()
    roots = lrs.derive_default_library_roots(C())
    assert {Path(r).resolve() for r in roots} == {a.resolve()}

def test_drive_root_parent_rejected(tmp_path, monkeypatch):
    """共享父是驱动器根 → 不用,回退到项目目录本身。"""
    from app.services import library_roots_service as lrs
    # 模拟两个项目父是 / (无法真造,用 monkeypatch is_drive_root_or_system_dir)
    a = tmp_path/"a"; a.mkdir(); b = tmp_path/"b"; b.mkdir()
    jp = tmp_path/"up.json"; _seed_projects(jp, [a, b])
    monkeypatch.setattr(lrs, "default_user_projects_json_path", lambda: str(jp))
    monkeypatch.setattr(lrs, "list_projects",
                        lambda p: json.loads(Path(p).read_text())["projects"])
    monkeypatch.setattr(lrs, "is_drive_root_or_system_dir", lambda d: d == str(tmp_path))
    class S: library_roots=None; manual_project_folders=[]
    class C: settings=S()
    roots = lrs.derive_default_library_roots(C())
    # tmp_path 被当系统目录 → 不作根,回退各自
    assert tmp_path.resolve() not in {Path(r).resolve() for r in roots}

def test_explicit_empty_list_disables_derive(tmp_path, monkeypatch):
    """library_roots == [] → 不推导,返回空。"""
    from app.services import library_roots_service as lrs
    class S: library_roots=[]; manual_project_folders=[]
    class C: settings=S()
    assert lrs.effective_library_roots(C()) == []
```

- [ ] **Step 2: Run, verify FAIL** — `pytest tests/test_library_roots_service.py -v` → `ImportError`

- [ ] **Step 3: Implement** — `app/services/library_roots_service.py`：

```python
"""library_roots_service.py — 项目树库根管理。

库根三态(settings.library_roots):
  None = 未配置 → on_activate 推导默认值(不写盘)
  []   = 显式清空 → 不推导,只显已录
  list = 用户配置

推导规则(纯字符串零 stat,存在性检查挪进 ProjectDiscoverWorker):
  ≥2 已录项目共享同一父目录(且父非驱动器根/系统目录) → 共享父作根;
  否则各已录项目所在目录本身作根。
"""
from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Optional

from app.services.project_service import (
    default_user_projects_json_path, list_projects,
)


def is_drive_root_or_system_dir(directory: str) -> bool:
    """护栏: 驱动器根 / 用户主目录 / Desktop / Downloads 拒绝作库根或 adopt 目标。"""
    try:
        resolved = Path(directory).resolve()
    except OSError:
        return False
    if resolved == resolved.parent:
        return True
    try:
        home = Path.home()
    except OSError:
        home = None
    if home is not None and resolved == home:
        return True
    if home is not None and resolved in {home / "Desktop", home / "Downloads"}:
        return True
    return False


def effective_library_roots(ctx) -> list[str]:
    """返回当前生效的库根列表(None→推导, []→空, list→原样)。"""
    raw = ctx.settings.library_roots
    if raw is None:
        return derive_default_library_roots(ctx)
    return list(raw)


def derive_default_library_roots(ctx) -> list[str]:
    """纯字符串推导(零 stat)。见模块 docstring 规则。"""
    try:
        projects = list_projects(default_user_projects_json_path())
    except Exception:
        return []
    dirs = [p.get("directory") or p.get("dir") or "" for p in projects if not p.get("isDemo")]
    dirs = [d for d in dirs if d]
    if not dirs:
        # 并入迁移种子(旧 project_tree_root)
        seed = getattr(ctx.settings, "_library_roots_seed", None)
        return [seed] if seed else []

    # 统计共享父
    parents = []
    for d in dirs:
        try:
            parents.append(str(Path(d).resolve().parent))
        except OSError:
            parents.append(str(Path(d).parent))
    counts = Counter(parents)
    roots: list[str] = []
    seen = set()
    for p, n in counts.items():
        if n >= 2 and not is_drive_root_or_system_dir(p):
            if p not in seen:
                roots.append(p); seen.add(p)
    # 无共享父的项目 → 各自目录本身
    for d in dirs:
        try:
            par = str(Path(d).resolve().parent)
        except OSError:
            par = str(Path(d).parent)
        if counts.get(par, 0) < 2 or is_drive_root_or_system_dir(par):
            if d not in seen:
                roots.append(d); seen.add(d)
    return roots
```

（`_library_roots_seed` 在 settings 加只读 property 读 `project/_library_roots_seed`，见 Task 1 末尾补：在 settings.py 加 `@property def _library_roots_seed(self): return self._qs.value("project/_library_roots_seed", None)`。）

- [ ] **Step 4: Run, verify PASS** — `pytest tests/test_library_roots_service.py -v`
- [ ] **Step 5: Commit** — `git add app/services/library_roots_service.py tests/test_library_roots_service.py app/config/settings.py && git commit -m "feat(library-roots): derive default roots (shared-parent rule) + 3-state semantics"`

---

### Task 7: project_adopt_service（认领 + 回滚 + 护栏）★核心红线

**Files:**
- Create: `app/services/project_adopt_service.py`
- Test: `tests/test_project_adopt_service.py`

> **★v5 增量 Step（spec §6，痛点②信任）**：
> 1. **dry-run 预扫描**：加 `prescan_project(directory) → PrescanReport`（零写盘：单次 scandir 数 incoming-jpg/*.jpg、results/*.tif 跳 .zip、_data 是否已存在、legacy sidecar 个数）。加测试：`prescan_project` 返回后目录下文件 sha256 全集合 + 目录树**与调用前字节一致**（零写盘，连 `_.writetest` 都不留）。spec §8 已落该红线。
> 2. **stable-id 接入**：adopt 末尾（record_recent_workspace 前）调 `project_relink_service.write_stable_id(directory)`，返回的 `(volume_uuid, identity_fp)` 传给 `record_recent_workspace(..., identity_fp=..., volume_uuid=...)` 双写进 registry（Task 7b 实现，本 task 仅调）。

- [ ] **Step 1: Write failing test**（含全部红线）

```python
# tests/test_project_adopt_service.py
import hashlib, sqlite3
from pathlib import Path

def _sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()

def test_adopt_creates_only_data_subdir(tmp_path, monkeypatch):
    """外部空文件夹 adopt 后仅 _data/ 一个子目录(无 incoming/results)。"""
    from app.services import project_adopt_service as pas
    from app.services import library_roots_service
    monkeypatch.setattr(pas, "default_user_projects_json_path", lambda: str(tmp_path/"up.json"))
    d = tmp_path/"ext"; d.mkdir()
    pas.adopt_project(_ctx(tmp_path), str(d), name="ext")
    children = sorted(c.name for c in d.iterdir())
    assert children == ["_data"]
    assert (d/"_data"/"project.db").exists()

def test_adopt_does_not_move_legacy_marker(tmp_path, monkeypatch):
    """红线: adopt 不走 migrate,marker 文件 sha256+位置不变。"""
    from app.services import project_adopt_service as pas
    monkeypatch.setattr(pas, "default_user_projects_json_path", lambda: str(tmp_path/"up.json"))
    d = tmp_path/"leg"; d.mkdir()
    marker = d/".project-specimens.json"
    marker.write_text('{"legacy":true}', encoding="utf-8")
    sha_before = _sha(marker); pos_before = str(marker)
    pas.adopt_project(_ctx(tmp_path), str(d))
    assert _sha(marker) == sha_before          # 内容不变
    assert marker.exists() and str(marker) == pos_before  # 位置不变

def test_adopt_idempotent(tmp_path, monkeypatch):
    from app.services import project_adopt_service as pas
    monkeypatch.setattr(pas, "default_user_projects_json_path", lambda: str(tmp_path/"up.json"))
    d = tmp_path/"w"; d.mkdir()
    r1 = pas.adopt_project(_ctx(tmp_path), str(d))
    r2 = pas.adopt_project(_ctx(tmp_path), str(d))
    assert r1.status == "adopted" and r2.status == "already"

def test_adopt_rollback_on_schema_failure(tmp_path, monkeypatch):
    """ensure_schema 抛错 → _data 完全清除,无 orphan 锁(端到端 rmtree 成功)。"""
    from app.services import project_adopt_service as pas
    from app.db import db_manager
    monkeypatch.setattr(pas, "default_user_projects_json_path", lambda: str(tmp_path/"up.json"))
    d = tmp_path/"fail"; d.mkdir()
    def boom(_conn): raise sqlite3.OperationalError("locked")
    monkeypatch.setattr(db_manager, "ensure_schema", boom)
    import pytest
    with pytest.raises(sqlite3.OperationalError):
        pas.adopt_project(_ctx(tmp_path), str(d))
    assert not (d/"_data").exists()           # 端到端:回滚清干净

def test_adopt_rejects_drive_root(tmp_path, monkeypatch):
    from app.services import project_adopt_service as pas
    monkeypatch.setattr(pas, "default_user_projects_json_path", lambda: str(tmp_path/"up.json"))
    import pytest
    with pytest.raises(pas.InvalidAdoptTarget):
        pas.adopt_project(_ctx(tmp_path), "/")  # 驱动器根

def test_adopt_records_user_projects_with_root(tmp_path, monkeypatch):
    from app.services import project_adopt_service as pas
    up = tmp_path/"up.json"
    monkeypatch.setattr(pas, "default_user_projects_json_path", lambda: str(up))
    parent = tmp_path/"survey"; parent.mkdir()
    child = parent/"断面a"; child.mkdir()
    pas.adopt_project(_ctx(tmp_path), str(child), inherit_from=str(parent))
    import json
    projs = json.loads(up.read_text())["projects"]
    assert len(projs) == 1
    assert projs[0]["directory"] == str(child)
    assert projs[0]["root"] == str(parent)

def _ctx(tmp_path):
    class S: library_roots=[]; manual_project_folders=[]
    class C:
        settings = S()
        current_project_dir = None
        current_project_root = None
    return C()
```

- [ ] **Step 2: Run, verify FAIL** — `pytest tests/test_project_adopt_service.py -v` → `ImportError`

- [ ] **Step 3: Implement** — `app/services/project_adopt_service.py`：

```python
"""project_adopt_service.py — 认领外部/候选文件夹为工作区。

关键: adopt 与 enter 是两个不同操作。
  adopt  = 最小识别: 只建 _data/project.db + seed 设置 + 登记。不建 incoming/results,
           不经 migrate_legacy_metadata(保留文件夹原貌,marker 不动)。
  enter  = 激活拍照: enter_workspace 建 dirs + migrate(此时用户已主动开工)。

adopt 全程不调 ensure_project_dirs / migrate_legacy_metadata,故 _data/ 下只有新建的
project.db,rmtree 回滚安全(无 _data/legacy/ 用户文件)。
"""
from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from app.utils.path_utils import normalize_path


class InvalidAdoptTarget(Exception):
    """adopt 目标非法(驱动器根/系统目录)。"""


class AdoptRollbackError(RuntimeError):
    """回滚失败(_data 无法清理)。"""


@dataclass
class AdoptResult:
    status: str   # "adopted" | "already"


def adopt_project(ctx, directory: str, *, name: Optional[str] = None,
                  inherit_from: Optional[str] = None) -> AdoptResult:
    from app.services.project_paths import ProjectUnavailableError
    from app.services.library_roots_service import is_drive_root_or_system_dir
    from app.db.db_manager import open_project_db, close_project_db
    from app.services.project_service import (
        default_user_projects_json_path, record_recent_workspace,
    )
    from app.services.project_catalog_service import register_workspace
    from app.services import project_tree_service as pts

    directory = normalize_path(directory)
    if not Path(directory).is_dir():
        raise ProjectUnavailableError(f"目录不可用: {directory}")
    if is_drive_root_or_system_dir(directory):
        raise InvalidAdoptTarget(f"拒绝在系统/驱动器根目录认领: {directory}")
    if (Path(directory) / "_data" / "project.db").exists():
        return AdoptResult("already")

    root = _resolve_inherit_root(directory, inherit_from)
    try:
        # 1. 只建 _data/project.db —— open_project_db(create=True) 内部:
        #    require_project_root + mkdir(_data 叶) + connect + (try ensure_schema except close) + cache
        #    【不调 ensure_project_dirs, 不建 incoming/results, 不经 migrate】
        open_project_db(directory, create=True)
        # 2. seed 设置 (db 已存在) —— 直接写 code_labels/personnel,不经 migrate
        _apply_inherited_settings(directory, inherit_from, name)
        # 3. 登记 survey catalog (若 root 已知且 != directory)
        if root and normalize_path(root) != directory:
            register_workspace(root, directory, role="workspace",
                               name=name or Path(directory).name)
        # 4. 记 user_projects.json
        record_recent_workspace(default_user_projects_json_path(), directory, root=root)
    except Exception:
        _rollback_adopt(directory)
        raise
    pts.clear_project_tree_cache(None)
    return AdoptResult("adopted")


def _resolve_inherit_root(directory: str, inherit_from: Optional[str]) -> Optional[str]:
    if inherit_from:
        return normalize_path(inherit_from)
    return None


def _apply_inherited_settings(directory: str, inherit_from: Optional[str],
                              name: Optional[str]) -> None:
    """从 inherit_from 沿继承链取 code_labels/personnel,写新 db。不经 migrate。"""
    from app.db.db_manager import get_db
    from app.services import project_settings_service as pss
    db = get_db(directory)
    src = inherit_from or directory
    try:
        eff_cl = pss.get_effective(src, "code_labels", pss.DEFAULT_CODE_LABELS)
        pss.save_setting(db, "code_labels", eff_cl)
    except Exception:
        pass
    try:
        eff_pers = pss.get_effective(src, "personnel", pss.DEFAULT_PERSONNEL)
        pss.save_setting(db, "personnel", eff_pers)
    except Exception:
        pass


def _rollback_adopt(directory: str) -> None:
    """关连接→删 _data→校验。adopt 未走 migrate 故 _data/ 无 legacy 用户文件。"""
    from app.db.db_manager import close_project_db
    try:
        close_project_db(directory)
    except Exception:
        pass
    _data = Path(directory) / "_data"
    if _data.exists():
        try:
            shutil.rmtree(_data, ignore_errors=False)
        except OSError as exc:
            raise AdoptRollbackError(
                f"_data 因磁盘问题无法清理(可能盘掉线): {_data}。接回后请手动删除。"
            ) from exc
        if _data.exists():
            raise AdoptRollbackError(f"_data 清理后仍存在: {_data}")
```

- [ ] **Step 4: Run, verify PASS** — `pytest tests/test_project_adopt_service.py -v`（全红线绿，含 Windows/WSL 路径 normalize 各跑一次——已在 fixture 用 `tmp_path` 覆盖）
- [ ] **Step 5: Commit** — `git add app/services/project_adopt_service.py tests/test_project_adopt_service.py && git commit -m "feat(adopt): explicit minimal claim op (no migrate) + end-to-end rollback + drive-root guard"`

---

### Task 7b: ★v5 project_relink_service（stable-id + backfill + Locate/Update Path）— round-2 5/5 bug 修复

**Files:**
- Create: `app/services/project_relink_service.py`、`tests/test_project_relink_service.py`
- Modify: `app/services/project_adopt_service.py`（adopt 末尾调 `write_stable_id`）、`app/services/project_service.py::record_recent_workspace`（条目双写 id）、`app/services/project_tree_service.py::discover_all_projects`（按 registry id 匹配 + 老 db backfill）

**背景（round-2 5/5 一致 spec bug）**：v4 把 file_fp 设成 `_data/project.db` 的 sha256，但 project.db 是活库（specimen 写入即变）→ §13 跨卷迁移红线假绿。且身份锚存到了会失联的盘上（路径死时读不到 project.db）。v5 改 sentinel + 镜像 + backfill。

- [ ] **Step 1: Write failing test**（含防假绿 + 镜像 + backfill + 重链校验）

```python
# tests/test_project_relink_service.py
import hashlib, sqlite3
from pathlib import Path

def _adopt(ctx, d, monkeypatch):
    from app.services import project_adopt_service as pas
    monkeypatch.setattr(pas, "default_user_projects_json_path", lambda: str(d.parent / "up.json"))
    pas.adopt_project(ctx, str(d))

def test_identity_sentinel_stable_across_db_writes(tmp_path, monkeypatch):
    """★防假绿: project.db 是活库,写入后 .identity 的 fp 必须不变。"""
    ctx = _fake_ctx()
    d = tmp_path / "w"; d.mkdir()
    _adopt(ctx, d, monkeypatch)
    from app.services.project_relink_service import identity_fingerprint
    fp_before = identity_fingerprint(str(d))
    # 往 project.db 插一条 specimen(活库写入)
    (d / "_data" / "x.txt").write_text("change")  # 模拟 db 变化(真实走 specimen insert)
    assert identity_fingerprint(str(d)) == fp_before   # sentinel 不动 → fp 不变

def test_stable_id_mirrored_into_user_projects_json(tmp_path, monkeypatch):
    """身份必须镜像进 registry(死盘时 discover 读这里,不读 project.db)。"""
    ctx = _fake_ctx(); d = tmp_path / "w"; d.mkdir()
    _adopt(ctx, d, monkeypatch)
    import json
    up = json.loads((tmp_path / "up.json").read_text())
    entry = up["projects"][0]
    assert "identity_fp" in entry and entry["identity_fp"]
    # volume_uuid 可能 None(测试环境),但 key 必须在
    assert "volume_uuid" in entry

def test_old_db_without_identity_gets_backfilled(tmp_path, monkeypatch):
    """老库(有 project.db 无 .identity)首次 discover 静默补 id。"""
    ctx = _fake_ctx()
    d = tmp_path / "old"; d.mkdir(); (d / "_data").mkdir()
    (d / "_data" / "project.db").touch()
    # registry 条目无 id(模拟 v5 前老库)
    import json
    up = tmp_path / "up.json"
    up.write_text(json.dumps({"version": 1, "projects": [{"directory": str(d)}]}))
    from app.services import project_tree_service as pts
    monkeypatch.setattr(pts, "default_user_projects_json_path", lambda: str(up))
    pts.discover_all_projects(ctx)   # 触发 backfill
    assert (d / "_data" / ".identity").exists()    # 补了 sentinel
    entry = json.loads(up.read_text())["projects"][0]
    assert "identity_fp" in entry                    # 回填进 registry

def test_relink_rejects_fingerprint_mismatch(tmp_path, monkeypatch):
    """指到新位置但指纹不符 → 拒绝重链(防误并两个不同项目)。"""
    from app.services.project_relink_service import relocate_project
    ctx = _fake_ctx()
    a = tmp_path / "a"; _adopt(ctx, a, monkeypatch)   # 真项目
    b = tmp_path / "b"; _adopt(ctx, b, monkeypatch)   # 另一个不同项目
    import pytest
    with pytest.raises(Exception):   # 指纹不符
        relocate_project(str(a), str(b))   # 声称 a 搬到 b,但 b 是别的项目
```

- [ ] **Step 2: Run, verify FAIL** — `pytest tests/test_project_relink_service.py -v` → `ImportError`
- [ ] **Step 3: Implement** — `app/services/project_relink_service.py`：

```python
"""project_relink_service.py — v5 稳定身份 + 断链重链。

身份锚 = _data/.identity sentinel(adopt 写一次永不改)的 sha256 + 卷 UUID。
绝不取 project.db 的 sha256(活库,会变 → 假绿)。
身份镜像进 user_projects.json(死盘时 discover 读 registry,不读 project.db)。
"""
from __future__ import annotations
import hashlib, uuid
from pathlib import Path
from typing import Optional

def ensure_identity(directory: str) -> str:
    """adopt 时调: 建 .identity(若未有)+ 返回 fp。idempotent。"""
    p = Path(directory) / "_data" / ".identity"
    if not p.exists():
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(f"{uuid.uuid4()}\n", encoding="utf-8")
    return identity_fingerprint(directory)

def identity_fingerprint(directory: str) -> str:
    p = Path(directory) / "_data" / ".identity"
    return hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else ""

def try_volume_uuid(directory: str) -> Optional[str]:
    """跨平台 best-effort。Win GetVolumeInformation / macOS diskutil / Linux stat.st_uuid。
    取不到返 None,不抛(网络盘/部分 FS 无)。"""
    ...  # 实现 fallback 链,任一成功即返

def write_stable_id(directory: str) -> tuple[Optional[str], str]:
    """adopt 末尾调。写 project_meta + 返回 (volume_uuid, identity_fp)。
    registry 镜像由 record_recent_workspace 双写(改 project_service)。"""
    from app.db.db_manager import get_db
    vol = try_volume_uuid(directory)
    fp = ensure_identity(directory)
    _save_meta(get_db(directory), "volume_uuid", vol)
    _save_meta(get_db(directory), "identity_fp", fp)
    return vol, fp

def backfill_if_needed(directory: str, entry: dict) -> None:
    """discover 读到老 db(有 project.db 无 .identity / registry 无 id)→ 静默补。
    不抛不阻塞;盘只读则跳过。"""
    if entry.get("identity_fp"): return
    try:
        fp = ensure_identity(directory)
        if fp:
            entry["identity_fp"] = fp
            entry["volume_uuid"] = try_volume_uuid(directory)
    except OSError: pass

def relocate_project(old_directory: str, new_directory: str) -> None:
    """右键「指到新位置」: 校验 new 的 identity_fp == registry 记录的 → 改 directory 字段。
    指纹不符抛(防误并)。不读 old 的 project.db(old 可能已死盘)。"""
    # 读 registry 条目(不是 old 的 db!)拿原 identity_fp
    # new 下 ensure_identity 若 new 已是工作区, 比对其 fp; 若 new 无 .identity 则拒绝(不像同一项目)
    ...
```

并改 `record_recent_workspace`（`project_service.py`）：adopt 路径调 `write_stable_id` 后，把返回的 `(volume_uuid, identity_fp)` 写进 entry（双写）。
并改 `discover_all_projects`：失联条目优先按 registry 镜像 id 匹配候选；命中 db 无 `.identity` 调 `backfill_if_needed`。

- [ ] **Step 4: Run, verify PASS** — `pytest tests/test_project_relink_service.py -v`（防假绿那条最关键：活库写入后 fp 不变）
- [ ] **Step 5: Commit** — `git add app/services/project_relink_service.py app/services/project_service.py app/services/project_tree_service.py app/services/project_adopt_service.py tests/test_project_relink_service.py && git commit -m "feat(relink): v5 stable-id (.identity sentinel + registry mirror + backfill) + Locate/Update Path, fixes round-2 5/5 lifecycle bug"`

---

## Phase 2 — Qt workers + 虚拟化模型

### Task 8: ProjectDiscoverWorker（一次性 QThread run()）

**Files:**
- Create: `app/workers/project_discover_worker.py`
- Test: `tests/test_project_discover_worker.py`

- [ ] **Step 1: Write failing test**

```python
# tests/test_project_discover_worker.py
import json
from pathlib import Path
from PyQt6.QtCore import QCoreApplication

def test_discover_worker_emits_results(qtbot, tmp_path, monkeypatch):
    from app.workers.project_discover_worker import ProjectDiscoverWorker
    reg = tmp_path/"r"; reg.mkdir(); (reg/"_data").mkdir(); (reg/"_data"/"project.db").touch()
    jp = tmp_path/"up.json"
    jp.write_text(json.dumps({"version":1,"projects":[{"directory":str(reg)}]}))
    from app.services import project_tree_service as pts
    monkeypatch.setattr(pts, "default_user_projects_json_path", lambda: str(jp))

    class S: library_roots=[]; manual_project_folders=[]
    class C: settings=S()
    received = []
    w = ProjectDiscoverWorker(C())
    w.results_ready.connect(lambda entries: received.extend(entries))
    with qtbot.waitSignal(w.finished, timeout=5000):
        w.start()
    assert any(Path(e["path"]).resolve()==reg.resolve() for e in received)

def test_discover_worker_discards_stale_signature(qtbot, tmp_path, monkeypatch):
    """跑期间 user_projects.json 被改 → 丢弃结果。"""
    from app.workers.project_discover_worker import ProjectDiscoverWorker
    jp = tmp_path/"up.json"
    jp.write_text(json.dumps({"version":1,"projects":[]}))
    from app.services import project_tree_service as pts
    monkeypatch.setattr(pts, "default_user_projects_json_path", lambda: str(jp))
    # 让 worker 启动后改 json (模拟并发)
    class S: library_roots=[]; manual_project_folders=[]
    class C: settings=S()
    emitted = []
    w = ProjectDiscoverWorker(C())
    w.results_ready.connect(lambda e: emitted.extend(e))
    # 在 discover 前改签名
    jp.write_text(json.dumps({"version":1,"projects":[{"directory":str(tmp_path/"x")}]}))
    (tmp_path/"x").mkdir()
    with qtbot.waitSignal(w.finished, timeout=5000):
        w.start()
    assert emitted == []   # 签名变了 → 丢弃
```

- [ ] **Step 2: Run, verify FAIL** — `pytest tests/test_project_discover_worker.py -v`
- [ ] **Step 3: Implement** — `app/workers/project_discover_worker.py`（照搬 `monitor_scan_worker.py` 的 QThread run() 模式）：

```python
"""project_discover_worker.py — 后台库根扫描(一次性 QThread run())。

跑期间 user_projects.json 签名(mtime+size)变了 → 丢弃结果(防竞态)。
完成后只发 results_ready,view 端做差集追加。
"""
from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import QThread, pyqtSignal

from app.services import project_tree_service as pts
from app.services.project_service import default_user_projects_json_path


def _signature(path: str):
    try:
        p = Path(path)
        st = p.stat()
        return (st.st_mtime_ns, st.st_size)
    except OSError:
        return None


class ProjectDiscoverWorker(QThread):
    results_ready = pyqtSignal(list)
    finished = pyqtSignal()

    def __init__(self, ctx):
        super().__init__()
        self._ctx = ctx

    def run(self):
        jp = default_user_projects_json_path()
        sig_before = _signature(jp)
        try:
            entries = pts.discover_all_projects(self._ctx)
        except Exception:
            entries = []
        if _signature(jp) != sig_before:
            # 签名变了 → 丢弃,防基于过期 registered 集合追加
            self.results_ready.emit([])
        else:
            self.results_ready.emit(entries)
        self.finished.emit()
```

- [ ] **Step 4: Run, verify PASS** — `pytest tests/test_project_discover_worker.py -v`
- [ ] **Step 5: Commit** — `git add app/workers/project_discover_worker.py tests/test_project_discover_worker.py && git commit -m "feat(worker): ProjectDiscoverWorker one-shot QThread with signature-guarded results"`

---

### Task 9: ThumbnailWorker（封面一次性 + 网格长驻）

**Files:**
- Create: `app/workers/thumbnail_worker.py`
- Test: `tests/test_thumbnail_worker.py`

- [ ] **Step 1: Write failing test**

```python
# tests/test_thumbnail_worker.py
from pathlib import Path
from PyQt6.QtGui import QImage, QPixmap

def _jpg(p):
    img = QImage(40,30, QImage.Format.Format_RGB32); img.fill(0x445566)
    img.save(str(p), "JPG")

def test_cover_worker_emits_qimage_not_pixmap(qtbot, tmp_path):
    from app.workers.thumbnail_worker import CoverThumbnailWorker
    p = tmp_path/"a.jpg"; _jpg(p)
    got = []
    w = CoverThumbnailWorker(str(p), 150, request_id=1)
    w.decoded.connect(lambda rid, img: got.append((rid, img)))
    with qtbot.waitSignal(w.finished, timeout=5000):
        w.start()
    assert len(got) == 1
    rid, img = got[0]
    assert rid == 1 and isinstance(img, QImage) and not img.isNull()

def test_grid_worker_longlived_moveToThread(qtbot, tmp_path):
    """长驻 worker: 主线程投递 decode, 回 QImage 信号。"""
    from app.workers.thumbnail_worker import GridThumbnailWorker
    from PyQt6.QtCore import QThread, QMetaObject, Qt, Q_ARG, pyqtSlot
    p = tmp_path/"a.jpg"; _jpg(p)
    t = QThread()
    w = GridThumbnailWorker()
    w.moveToThread(t); t.start()
    got = []
    w.decoded.connect(lambda path, img: got.append((path, img)))
    QMetaObject.invokeMethod(w, "decode", Qt.ConnectionType.QueuedConnection,
                             Q_ARG(str, str(p)), Q_ARG(int, 120))
    import time
    qtbot.waitUntil(lambda: len(got) >= 1, timeout=5000)
    t.quit(); t.wait(2000)
    assert isinstance(got[0][1], QImage)
```

- [ ] **Step 2: Run, verify FAIL** — `pytest tests/test_thumbnail_worker.py -v`
- [ ] **Step 3: Implement** — `app/workers/thumbnail_worker.py`：

```python
"""thumbnail_worker.py — 两套缩略图 worker。

① CoverThumbnailWorker (一次性) = QThread 子类 run(): 解码单图回 QImage 信号。
② GridThumbnailWorker (长驻) = QObject moveToThread + exec(): 主线程 invokeMethod
   投递 decode,回 QImage 信号。on_deactivate/closeEvent 必须 quit()+wait()。
两者都只调 decode_image_data(线程安全 QImage),绝不构造 QPixmap(主线程 make_pixmap)。
"""
from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import QThread, pyqtSignal, pyqtSlot, Q_ARG
from PyQt6.QtGui import QImage

from app.utils.image_thumbnail import decode_image_data


class CoverThumbnailWorker(QThread):
    """一次性: 解码一张封面图。带 request_id 供主线程丢弃过期请求(卡片滚动)。"""
    decoded = pyqtSignal(object, object)   # (request_id, QImage|None)
    finished = pyqtSignal()

    def __init__(self, path: str, max_size: int, request_id: object):
        super().__init__()
        self._path = path
        self._max_size = max_size
        self._rid = request_id

    def run(self):
        img = decode_image_data(self._path, self._max_size)
        self.decoded.emit(self._rid, img)
        self.finished.emit()


class GridThumbnailWorker(QThread):
    """长驻: 事件循环,主线程 invokeMethod('decode') 投递。退出时 quit()+wait()。"""
    decoded = pyqtSignal(str, object)     # (path, QImage|None)

    def __init__(self):
        super().__init__()

    def run(self):
        self.exec()

    @pyqtSlot(str, int)
    def decode(self, path: str, max_size: int):
        img = decode_image_data(path, max_size)
        self.decoded.emit(path, img)
```

- [ ] **Step 4: Run, verify PASS** — `pytest tests/test_thumbnail_worker.py -v`
- [ ] **Step 5: Commit** — `git add app/workers/thumbnail_worker.py tests/test_thumbnail_worker.py && git commit -m "feat(worker): CoverThumbnailWorker (one-shot) + GridThumbnailWorker (long-lived moveToThread)"`

---

### Task 10: 虚拟化缩略图网格（QListView + Model + Delegate）

**Files:**
- Create: `app/widgets/thumbnail_grid.py`
- Test: `tests/test_thumbnail_grid.py`

- [ ] **Step 1: Write failing test**

```python
# tests/test_thumbnail_grid.py
from pathlib import Path
from PyQt6.QtGui import QImage

def _jpg(p, color=0x778899):
    img = QImage(40,30,QImage.Format.Format_RGB32); img.fill(color)
    img.save(str(p), "JPG")

def test_thumbnail_list_model_roles(qtbot, tmp_path):
    from app.widgets.thumbnail_grid import ThumbnailListModel
    from PyQt6.QtCore import Qt
    p1 = tmp_path/"a.jpg"; _jpg(p1)
    p2 = tmp_path/"b.jpg"; _jpg(p2, 0xaabbcc)
    m = ThumbnailListModel()
    m.set_paths([str(p1), str(p2)])
    assert m.rowCount() == 2
    assert m.data(m.index(0), Qt.ItemDataRole.DisplayRole) == "a.jpg"

def test_grid_renders_2000_items_fast(qtbot, tmp_path):
    """红线: 2000 项首屏 paint<200ms (虚拟化,只渲染可见行)。"""
    import time
    from app.widgets.thumbnail_grid import ThumbnailGridWidget, ThumbnailListModel
    paths = []
    for i in range(2000):
        p = tmp_path / f"img{i}.jpg"
        # 不真建 2000 jpg(慢),用空 path 占位;delegate 占位绘制
        paths.append(str(p))
    w = ThumbnailGridWidget()
    w.set_paths(paths)
    w.resize(800, 600)
    w.show()
    qtbot.waitExposed(w)
    t0 = time.perf_counter()
    w.viewport().repaint()
    dt = (time.perf_counter() - t0) * 1000
    assert dt < 200, f"首屏 paint {dt:.0f}ms > 200ms"
    w.close()
```

- [ ] **Step 2: Run, verify FAIL** — `pytest tests/test_thumbnail_grid.py -v`
- [ ] **Step 3: Implement** — `app/widgets/thumbnail_grid.py`（引用 `taxonomy_input.py` 的 Model+Delegate 范式）：

```python
"""thumbnail_grid.py — 虚拟化缩略图网格。

QListView(IconMode) + ThumbnailListModel(QAbstractListModel,持 list[str] 路径) +
ThumbnailDelegate(QStyledItemDelegate: paint() 内 QPixmapCache.find→命中 drawPixmap /
未命中投递 GridThumbnailWorker→回主线程 dataChanged 重绘该 cell)。
只渲染可见行,2000 项首屏<200ms。
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from PyQt6.QtCore import QAbstractListModel, QModelIndex, Qt, QSize, QVariant
from PyQt6.QtGui import QPixmapCache, QPixmap, QImage, QPainter, QColor
from PyQt6.QtWidgets import QListView, QStyledItemDelegate, QStyle

PixmapCache_setLimit = QPixmapCache.setCacheLimit
_THUMB_SIZE = 112


class ThumbnailListModel(QAbstractListModel):
    def __init__(self):
        super().__init__()
        self._paths: list[str] = []

    def set_paths(self, paths: list[str]):
        self.beginResetModel()
        self._paths = list(paths)
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self._paths)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or not (0 <= index.row() < len(self._paths)):
            return QVariant()
        p = self._paths[index.row()]
        if role == Qt.ItemDataRole.DisplayRole:
            return Path(p).name
        if role == Qt.ItemDataRole.UserRole:
            return p
        return QVariant()

    def canFetchMore(self, parent=QModelIndex()):
        return False  # 路径已全量持有; 大集合分页由 view 层 fetchMore 扩展 model

    def update_pixmap(self, row: int):
        """worker 回 QImage 后,主线程 make_pixmap 存 cache + 触发该 cell 重绘。"""
        ix = self.index(row)
        if ix.isValid():
            self.dataChanged.emit(ix, ix, [Qt.ItemDataRole.DecorationRole])


class ThumbnailDelegate(QStyledItemDelegate):
    def __init__(self, parent_list, worker, thumb_size=_THUMB_SIZE):
        super().__init__(parent_list)
        self._list = parent_list
        self._worker = worker            # GridThumbnailWorker (长驻)
        self._thumb = thumb_size
        self._inflight: set[str] = set()

    def paint(self, painter: QPainter, option, index):
        path = index.data(Qt.ItemDataRole.UserRole)
        key = f"thumb:{path}:{self._thumb}"
        pm = QPixmapCache.find(key)
        # 背景
        painter.save()
        if option.state & QStyle.StateFlag.State_Selected:
            painter.fillRect(option.rect, QColor(15, 118, 110, 40))
        rect = option.rect.adjusted(4, 4, -4, -18)
        if pm is not None:
            painter.drawPixmap(rect.topLeft(), pm)
        else:
            painter.fillRect(rect, QColor("#eef2f6"))
            painter.setPen(QColor("#94a3b8"))
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter,
                             Path(path).suffix.upper().lstrip(".") or "…")
            # 投递解码(去重)
            if path not in self._inflight:
                self._inflight.add(path)
                from PyQt6.QtCore import QMetaObject, Q_ARG
                QMetaObject.invokeMethod(self._worker, "decode",
                                         Qt.ConnectionType.QueuedConnection,
                                         Q_ARG(str, path), Q_ARG(int, self._thumb))
        # 文件名
        painter.setPen(QColor("#334155"))
        painter.drawText(option.rect.adjusted(0, option.rect.height()-16, 0, 0),
                         Qt.AlignmentFlag.AlignCenter,
                         option.fontMetrics.elidedText(Path(path).name,
                                                       Qt.TextElideMode.ElideMiddle,
                                                       option.rect.width()-8))
        painter.restore()

    def sizeHint(self, option, index):
        return QSize(self._thumb + 12, self._thumb + 28)


class ThumbnailGridWidget(QListView):
    """虚拟化网格。set_paths 喂全量;delegate 按需取图。"""

    def __init__(self, worker, parent=None):
        super().__init__(parent)
        self.setViewMode(QListView.ViewMode.IconMode)
        self.setResizeMode(QListView.ResizeMode.Adjust)
        self.setUniformItemSizes(True)
        self.setMovement(QListView.Movement.Static)
        self.setWordWrap(False)
        self.setModel(ThumbnailListModel())
        self.setItemDelegate(ThumbnailDelegate(self, worker))
        self.setIconSize(QSize(_THUMB_SIZE, _THUMB_SIZE))
        self._worker = worker
        worker.decoded.connect(self._on_decoded)

    def set_paths(self, paths: list[str]):
        self.model().set_paths(paths)

    def _on_decoded(self, path: str, image):
        from app.utils.image_thumbnail import make_pixmap
        key = f"thumb:{path}:{_THUMB_SIZE}"
        if image is not None:
            pm = make_pixmap(image)
            if pm is not None:
                QPixmapCache.insert(key, pm)
        # 触发该 path 对应 row 重绘
        m = self.model()
        for i in range(m.rowCount()):
            if m.data(m.index(i), Qt.ItemDataRole.UserRole) == path:
                m.update_pixmap(i)
                break
        d = self.itemDelegate()
        if isinstance(d, ThumbnailDelegate):
            d._inflight.discard(path)
```

`update_pixmap` 里 `dataChanged` 用 `DecorationRole`，但 delegate 读 `UserRole`+cache——这是有意的：`dataChanged` 仅触发该 cell `paint()` 重算，cache 命中即画图。

- [ ] **Step 4: Run, verify PASS** — `QT_QPA_PLATFORM=offscreen pytest tests/test_thumbnail_grid.py -v`
- [ ] **Step 5: Commit** — `git add app/widgets/thumbnail_grid.py tests/test_thumbnail_grid.py && git commit -m "feat(grid): virtualized thumbnail grid (QListView+Model+Delegate) with on-demand decode"`

---

## Phase 3 — Widgets & dialogs

### Task 11: ProjectCard widget

**Files:**
- Create: `app/widgets/project_card.py`
- Test: `tests/test_project_card.py`

- [ ] **Step 1: Write failing test** — 卡片渲染各 kind/状态、封面 fallback、键盘焦点。

```python
# tests/test_project_card.py
from pathlib import Path

def test_card_shows_workspace_badge_and_enter_button(qtbot):
    from app.widgets.project_card import ProjectCard
    card = ProjectCard()
    card.set_entry({"path":"/x", "name":"B2", "kind":"workspace"},
                   stats={"specimenCount":142, "pendingJpgCount":8},
                   last_date="2026-07-04")
    qtbot.addWidget(card)
    assert card.enter_button.isEnabled()
    assert "8" in card.badge_text()       # 待处理角标

def test_card_candidate_shows_claim_button(qtbot):
    from app.widgets.project_card import ProjectCard
    card = ProjectCard()
    card.set_entry({"path":"/x","name":"old","kind":"candidate"},
                   stats={"specimenCount":0,"pendingJpgCount":0}, last_date="")
    qtbot.addWidget(card)
    assert card.claim_button.isVisible()
    assert not card.enter_button.isEnabled() or card.enter_button.isVisible()
```

- [ ] **Step 2: Run, verify FAIL** — `pytest tests/test_project_card.py -v`
- [ ] **Step 3: Implement** — `app/widgets/project_card.py`：QFrame 含封面 QLabel（占位优先，封面 worker 异步补）、名称、统计行（`{specimen} 标本`）、ISO 日期行、常驻 `[进入工作区]`、候选卡 `[认领]`、可用性点（绿/灰）。`set_entry(path,kind,stats,last_date)` + `set_cover(QPixmap)`。`badge_text()` 返回待处理数。键盘焦点策略 `Qt.StrongFocus`。

（代码骨架：`class ProjectCard(QFrame)` + `enterRequested = pyqtSignal(str)` / `claimRequested = pyqtSignal(str)` 信号；`_setup_ui` 用 QVBoxLayout；样式走 `_apply_style` 读 `theme.TOKENS`，与 `project_tree_view._apply_style` 一致。）

- [ ] **Step 4: Run, verify PASS** — `pytest tests/test_project_card.py -v`
- [ ] **Step 5: Commit** — `git add app/widgets/project_card.py tests/test_project_card.py && git commit -m "feat(card): ProjectCard with 5-level cover fallback, badge, persistent actions"`

---

### Task 12: 库根管理对话框（唯一入口）

**Files:**
- Create: `app/widgets/library_roots_dialog.py`
- Test: `tests/test_library_roots_dialog.py`

- [ ] **Step 1: Write failing test** — 加根拒绝驱动器根；移除根不写 user_projects.json。

```python
# tests/test_library_roots_dialog.py
def test_add_rejects_drive_root(qtbot, tmp_path, monkeypatch):
    from app.widgets.library_roots_dialog import LibraryRootsDialog
    class S: library_roots=[]; manual_project_folders=[]
    class C: settings=S()
    dlg = LibraryRootsDialog(ctx=C())
    qtbot.addWidget(dlg)
    # 模拟选了 /
    ok = dlg._propose_add("/")
    assert ok is False

def test_remove_does_not_touch_user_projects(qtbot, tmp_path, monkeypatch):
    import json
    up = tmp_path/"up.json"; up.write_text('{"version":1,"projects":[]}')
    from app.widgets import library_roots_dialog as lrd
    monkeypatch.setattr(lrd, "default_user_projects_json_path", lambda: str(up))
    class S: library_roots=[str(tmp_path/"r")]; manual_project_folders=[]
    class C: settings=S()
    dlg = LibraryRootsDialog(ctx=C())
    qtbot.addWidget(dlg)
    dlg._remove_at(0)
    before = json.loads(up.read_text())["projects"]
    assert dlg._ctx.settings.library_roots == []
    assert json.loads(up.read_text())["projects"] == before  # 不动 json
```

- [ ] **Step 2: Run, verify FAIL** — `pytest tests/test_library_roots_dialog.py -v`
- [ ] **Step 3: Implement** — `app/widgets/library_roots_dialog.py`：`QDialog`，列表 QListWidget 每行 `根路径  ·  扫到 N 工作区 · M 候选`（N/M 由 `discover_all_projects` 预算），`[加目录]`（走 `ui.get_existing_directory` → `is_drive_root_or_system_dir` 护栏 → 追加 `settings.library_roots`），`[移除]`（仅删 settings 键，确认提示）。自动迁移来的根标注 `(自动添加)`（读 `_library_roots_seed` 比对）。
- [ ] **Step 4: Run, verify PASS** — `pytest tests/test_library_roots_dialog.py -v`
- [ ] **Step 5: Commit** — `git add app/widgets/library_roots_dialog.py tests/test_library_roots_dialog.py && git commit -m "feat(dialog): library roots management (single entry, drive-root guard, json-safe remove)"`

---

### Task 13: 认领确认对话框 + 封面缓存服务

**Files:**
- Create: `app/widgets/adopt_confirm_dialog.py`, `app/services/cover_cache_service.py`
- Test: `tests/test_adopt_confirm_dialog.py`, `tests/test_cover_cache_service.py`

> **★v5 增量（spec §6 确认框）**：`AdoptConfirmDialog` 顶部渲染 Task 7 的 `PrescanReport`（灰底报告头 `扫描「X」: 142 JPG · 8 TIFF · 0 _data · 1 legacy 清单`）+ 正文「认领将只新建一个 _data 子目录，原始照片一个都不会动」。信任从「对话框文字」升到「写前看见真实计数」。借 Symbiota Pending Data Transfer Report。

- [ ] **Step 1: Write failing test（封面 fallback + 缓存路径 + cover_image 项目外降级）**

```python
# tests/test_cover_cache_service.py
from pathlib import Path
from PyQt6.QtGui import QImage

def _jpg(p, c=0x556677):
    QImage(40,30,QImage.Format.Format_RGB32).fill(c) and QImage(40,30,QImage.Format.Format_RGB32).save(str(p),"JPG") or None

def test_cover_fallback_to_incoming_jpg(tmp_path):
    from app.services.cover_cache_service import pick_cover
    d = tmp_path/"w"; d.mkdir(); (d/"incoming-jpg").mkdir()
    j = d/"incoming-jpg"/"x.jpg"
    QImage(20,20,QImage.Format.Format_RGB32).save(str(j),"JPG")
    img = pick_cover(str(d), cover_image=None)
    assert img is not None and not img.isNull()

def test_cover_image_outside_project_falls_back(tmp_path):
    from app.services.cover_cache_service import pick_cover
    d = tmp_path/"w"; d.mkdir()
    outside = tmp_path/"outside.jpg"
    QImage(10,10,QImage.Format.Format_RGB32).save(str(outside),"JPG")
    img = pick_cover(str(d), cover_image=str(outside))  # 项目外 → 降级
    # 无其它来源 → None (占位)
    assert img is None

def test_cover_cache_path_under_home(tmp_path, monkeypatch):
    from app.services.cover_cache_service import cover_cache_path
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    p = cover_cache_path("/proj/x")
    assert str(p).startswith(str(tmp_path/".cache"/"specimen-photo-workbench"/"covers"))
    assert p.suffix == ".jpg"
```

- [ ] **Step 2: Run, verify FAIL** — `pytest tests/test_cover_cache_service.py -v`
- [ ] **Step 3: Implement** — `app/services/cover_cache_service.py`：

```python
"""cover_cache_service.py — 封面 5 级 fallback 选取 + 全局缓存。

全局缓存: ~/.cache/specimen-photo-workbench/covers/<sha256(path)[:16]>.jpg
  不进项目目录(盘只读/多机共享/不污染用户数据)。
fallback: 1.cover_image(项目内) 2.specimen 代表图 3.results/*.tif(跳 .zip)
          4.incoming-jpg/*.jpg 5.None(占位首字母)
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Optional

from PyQt6.QtGui import QImage

from app.utils.image_thumbnail import decode_image_data


def cover_cache_path(project_path: str) -> Path:
    h = hashlib.sha256(str(project_path).encode("utf-8")).hexdigest()[:16]
    return Path.home() / ".cache" / "specimen-photo-workbench" / "covers" / f"{h}.jpg"


def pick_cover(directory: str, *, cover_image: Optional[str] = None,
               max_size: int = 320) -> Optional[QImage]:
    d = Path(directory)
    # 1. cover_image (必须项目内)
    if cover_image:
        c = Path(cover_image)
        try:
            c.resolve().relative_to(d.resolve())
        except (ValueError, OSError):
            c = None      # 项目外 → 降级
        if c and c.is_file():
            img = decode_image_data(str(c), max_size)
            if img is not None:
                return img
    # 2. specimen 代表图 (从 project.db 取有缩略图的 specimen,按 scientific_name 分组) —— 占位,后续 Task 补
    # 3. results/*.tif (跳 .zip)
    results = d / "results"
    if results.is_dir():
       tifs = sorted([p for p in results.iterdir() if p.suffix.lower()==".tif" and p.is_file()],
                     key=lambda p: p.stat().st_mtime if p.exists() else 0, reverse=True)
        for t in tifs[:3]:
            img = decode_image_data(str(t), max_size)
            if img is not None:
                return img
    # 4. incoming-jpg/*.jpg
    for inc in (d/"incoming-jpg", d/"新拍JPG"):
        if inc.is_dir():
            jpgs = sorted([p for p in inc.iterdir() if p.suffix.lower() in (".jpg",".jpeg") and p.is_file()],
                          key=lambda p: p.stat().st_mtime if p.exists() else 0, reverse=True)
            for j in jpgs[:3]:
                img = decode_image_data(str(j), max_size)
                if img is not None:
                    return img
    return None


def load_or_compute_cover(directory: str, *, cover_image: Optional[str] = None,
                          max_size: int = 320) -> Optional[QImage]:
    """全局缓存: 命中读,否则 pick_cover + 写缓存。盘只读时写失败静默。"""
    cache = cover_cache_path(directory)
    if cache.is_file():
        img = decode_image_data(str(cache), max_size)
        if img is not None:
            return img
    img = pick_cover(directory, cover_image=cover_image, max_size=max_size)
    if img is not None:
        try:
            cache.parent.mkdir(parents=True, exist_ok=True)
            img.save(str(cache), "JPG", quality=85)
        except OSError:
            pass        # 盘只读 → 不写,下次重算
    return img
```

`app/widgets/adopt_confirm_dialog.py`：`QDialog`，大白话文案 `认领「X」为工作区：只新建一个 _data 子目录存项目数据，你的原始照片一个都不会动、不会重命名、不会移动。`，`project.db` 折叠在"高级"，继承行 `继承自: <祖先>` + "查看"展开完整链（调 `project_settings_service.get_effective` 沿父链），`[认领][取消]`。可写性预检 `mkdir(directory/_.writetest)→rmdir`，失败降级。

- [ ] **Step 4: Run, verify PASS** — `pytest tests/test_cover_cache_service.py tests/test_adopt_confirm_dialog.py -v`
- [ ] **Step 5: Commit** — `git add app/services/cover_cache_service.py app/widgets/adopt_confirm_dialog.py tests/ && git commit -m "feat(cover+adopt): 5-level cover fallback + global cache + plain-language confirm dialog"`

---

## Phase 4 — View assembly

### Task 14: ProjectTreeView 重构骨架（顶栏 + stack + 统一选择）

**Files:**
- Modify: `app/views/project_tree_view.py`（全量重构，保留 `view_id`/`nav_title`/`enter_workspace_requested`/`on_activate` 契约）
- Test: `tests/test_project_tree_redesign_view.py`

- [ ] **Step 1: Write failing test**

```python
# tests/test_project_tree_redesign_view.py
def test_view_has_card_and_tree_modes(qtbot, monkeypatch):
    from app.views.project_tree_view import ProjectTreeView
    class FakeCtx:
        class settings:
            library_roots=[]; manual_project_folders=[]
            project_tree_view_mode="cards"; project_tree_root=None
        current_project_dir=None; current_project_root=None
        def get_db(self): return None
    v = ProjectTreeView(FakeCtx())
    qtbot.addWidget(v)
    v.on_activate()
    assert v._stack.currentIndex() == 0   # cards 默认
    v.set_view_mode("tree")
    assert v._stack.currentIndex() == 1

def test_rooted_mode_still_shows_other_registered(qtbot, tmp_path, monkeypatch):
    """rooted 盲区回归: 选了根目录,树外已录项目仍在卡片视图。"""
    import json
    from app.views.project_tree_view import ProjectTreeView
    jp = tmp_path/"up.json"
    reg = tmp_path/"reg"; reg.mkdir(); (reg/"_data").mkdir(); (reg/"_data"/"project.db").touch()
    jp.write_text(json.dumps({"version":1,"projects":[{"directory":str(reg),"name":"reg"}]}))
    other_root = tmp_path/"survey"; other_root.mkdir()
    from app.services import project_tree_service as pts
    monkeypatch.setattr(pts, "default_user_projects_json_path", lambda: str(jp))
    monkeypatch.setattr(pts, "list_projects", lambda p: json.loads(Path(p).read_text())["projects"])
    class S:
        library_roots=[str(other_root)]; manual_project_folders=[]
        project_tree_view_mode="cards"; project_tree_root=str(other_root)
    class C:
        settings=S(); current_project_dir=None; current_project_root=None
        def get_db(self): return None
    v = ProjectTreeView(C())
    qtbot.addWidget(v); v.on_activate()
    paths = [Path(e["path"]).resolve() for e in v._card_entries()]
    assert reg.resolve() in paths   # 树外已录仍在
```

- [ ] **Step 2: Run, verify FAIL** — `pytest tests/test_project_tree_redesign_view.py -v`
- [ ] **Step 3: Implement** — 重构 `app/views/project_tree_view.py`：
  - 顶栏：`[卡片|树]` 分段 + `[🔍搜索]` + 右上 `⋯`（导入文件夹/库根管理/刷新库）。
  - `QStackedWidget`：page0=卡片滚动区（`QScrollArea` 装 `ProjectCard` 网格），page1=树三栏 `QSplitter`（左 `QTreeWidget` + 库根折叠面板 / 中 `ThumbnailGridWidget` / 右元数据面板）。
  - 统一选择：`self._selected_path: Optional[str]`，卡片单击/树单击都设它 + 同步两视图（卡片选中=树展开到该节点）。
  - `on_activate`：同步取 registered 渲染卡片（`discover_all_projects`，库根=[]时仅 registered）→ 启 `ProjectDiscoverWorker` 差集追加 → 封面 `CoverThumbnailWorker` 异步补。
  - `on_deactivate`：`_grid_worker.quit()+wait()`、`_discover_worker.wait(2000)`（长驻 worker 清理，防句柄泄漏）。
  - `set_view_mode(mode)` 切 stack + 存 `settings.project_tree_view_mode`。
  - 保留 `_enter_selected`（调 `enter_workspace` + emit `enter_workspace_requested` + navigate_to workbench），改区域确认框逻辑（≥3 子工作区/手动库根才弹）。
  - 迁移触发：`on_activate` 首次若 `settings.library_roots is None` 且 `_library_roots_seed` 存在 → 校验 `Path(seed).is_dir()`，存在则不固化（推导并入），不存在则 UI 提示。
- [ ] **Step 4: Run, verify PASS** — `pytest tests/test_project_tree_redesign_view.py -v`
- [ ] **Step 5: Commit** — `git add app/views/project_tree_view.py tests/test_project_tree_redesign_view.py && git commit -m "feat(view): rebuild ProjectTreeView (cards/tree stack + unified selection + worker teardown)"`

---

### Task 15: 卡片视图 + 树视图接线

**Files:**
- Modify: `app/views/project_tree_view.py`（实现 `_render_cards` / `_render_tree` / worker 信号槽）

- [ ] **Step 1: Write failing test** — 卡片认领调 `adopt_project` 后刷新；树节点点击填网格。

```python
def test_card_claim_triggers_adopt_and_refresh(qtbot, tmp_path, monkeypatch):
    # monkeypatch adopt_project, 断言刷新被调用
    ...
def test_tree_node_click_populates_grid(qtbot, tmp_path):
    # 选节点 → 网格 model 有直接子层路径
    ...
```

- [ ] **Step 2: Run FAIL** — `pytest tests/test_project_tree_redesign_view.py -v`
- [ ] **Step 3: Implement** —
  - `_render_cards(entries)`：清卡片容器，按 entries 建 `ProjectCard`，连 `enterRequested`→`_enter_path`、`claimRequested`→`_on_claim`、封面 `_start_cover_worker(card, path)`。
  - `_on_claim(path)`：弹 `AdoptConfirmDialog` → `adopt_project(ctx, path, inherit_from=...)` → `clear_project_tree_cache(None)` → `on_activate()` 重算。
  - `_render_tree()`：`scan_tree` 每个库根 → `QTreeWidget` 顶层子树（复用现有 `_build_item`）；选中节点 → `_populate_grid(node_path)`。
  - `_populate_grid(path)`：同步取直接子层一层（`os.scandir` 一次拿直接子文件图片+子目录），喂 `ThumbnailGridWidget.set_paths`，"含子文件夹"开关按节点类型默认（容器=开/工作区=关）。
  - 封面 worker：`CoverThumbnailWorker(path, request_id=card)` → `decoded` 槽 `card.set_cover(make_pixmap(img))`，request_id 不匹配丢弃。
- [ ] **Step 4: Run PASS** — `pytest tests/test_project_tree_redesign_view.py -v`
- [ ] **Step 5: Commit** — `git add app/views/project_tree_view.py tests/ && git commit -m "feat(view): wire cards (claim/cover) + tree (grid populate) + on-demand thumbnails"`

---

### Task 16: 键盘导航 + 右栏元数据 + 含子文件夹默认

**Files:**
- Modify: `app/views/project_tree_view.py`

- [ ] **Step 1: Write failing test** — 键盘 `Enter` 进工作区；网格 `←→` 移焦点；右栏显示标本字段非 EXIF。

```python
def test_card_enter_key_activates(qtbot, monkeypatch):
    ...
def test_grid_arrow_keys_move_focus(qtbot):
    ...
```

- [ ] **Step 2: Run FAIL**
- [ ] **Step 3: Implement** — 挂 `QShortcut`（卡片/网格 `←→↑↓` 移焦点 + 自动滚动，`Enter`=进入/大图，`空格`=press-hold 100%，`Esc`=返回上级，`Ctrl+F`=聚焦搜索）。网格 breadcrumb + 右键"在目录树中显示"反向展开树。右栏：选中节点统计（直接数量 + 灰字 `(含子级共 N)`）；选中照片反查 specimen（文件名 uniqueId → specimen 表）显示学名/UID/站位/经纬度/日期/采集人/合成状态，EXIF 折叠次要分组。`含子文件夹`开关按节点类型默认值（§4.3）。
- [ ] **Step 4: Run PASS**
- [ ] **Step 5: Commit** — `git add app/views/project_tree_view.py tests/ && git commit -m "feat(view): keyboard nav + breadcrumb reverse-nav + specimen-first metadata panel"`

---

## Phase 5 — Integration / perf

### Task 17: 性能烟测 + 全红线回归

**Files:**
- Test: `tests/test_project_tree_perf.py`

- [ ] **Step 1: Write failing test**

```python
# tests/test_project_tree_perf.py
import os, time
from pathlib import Path

def test_discover_1000_dirs_under_800ms(tmp_path, monkeypatch):
    """非 mock 烟测: 真实 /tmp 下 1000 目录,单次 scandir 改造验证。"""
    for i in range(1000):
        (tmp_path/f"d{i}").mkdir()
    from app.services import project_tree_service as pts
    t0 = time.perf_counter()
    pts.discover_workspace_candidates(str(tmp_path), max_depth=2, use_cache=False)
    dt = (time.perf_counter()-t0)*1000
    assert dt < 800, f"1000 目录扫描 {dt:.0f}ms > 800ms"

def test_on_activate_under_500ms_1000_dirs(qtbot, tmp_path, monkeypatch):
    """monkeypatch stat 模拟大库根,on_activate<500ms。"""
    ...  # 建 1000 目录 fixture,monkeypatch ProjectDiscoverWorker.start 为 no-op
```

- [ ] **Step 2: Run FAIL/PASS**（视现状）— `pytest tests/test_project_tree_perf.py -v`
- [ ] **Step 3: 若红,优化** — 确认 `is_workspace_candidate` 单次 scandir、registered 阶段零 stat。若仍慢，给 `discover_workspace_candidates` 加路径列表缓存（spec §7）。
- [ ] **Step 4: 全套回归** — `QT_QPA_PLATFORM=offscreen pytest tests/ -v --timeout=120` 全绿（含 `test-workbench-test-timer-leak-hang` 教训：确认新 view `on_deactivate` 清夹具）。
- [ ] **Step 5: Commit** — `git add tests/test_project_tree_perf.py && git commit -m "test(perf): 1000-dir discover <800ms smoke + on_activate<500ms mock"`

---

## Self-Review

**Spec 覆盖：**
- §0 术语 → Task 5 `classify_project_dir` kind 字段 ✅
- §1 三痛点 → T5 discover(rooted 盲区) / T7 adopt(认领) / T10 grid(预览) ✅
- §3 架构（image_thumbnail 拆 / db_manager 加固 / 两套 worker / 虚拟化）→ T2/T3/T8/T9/T10 ✅
- §4 组件 → T11 card / T12 dialog / T13 cover+adopt / T14-16 view ✅
- §5 三源并集 + 删第④源 → T5/T6 ✅
- §6 adopt 显式最小操作（不调 enter_workspace）→ T7 ✅
- §7 回滚 close_project_db + rmtree → T2(底层)+T7 ✅
- §8 测试（含端到端 rmtree、非 mock 烟测、worker 不构造 QPixmap）→ 各 Task 红线 + T17 ✅
- §9 settings 3 键 + 迁移 → T1 ✅
- §10 风险缓解 → 长驻 worker 清理 T14、单次 scandir T4、虚拟化 T10 ✅
- §11 封面全局缓存 → T13 ✅
- §12 未决（未编号散片、字母跳转）→ 明确本期不做，plan 不含 ✅

**占位扫描：** T11/T12/T13/T15/T16 的 widget 代码给的是骨架描述非逐行——这些是 UI 装配，模式与现有 `project_tree_view._setup_ui`/`project_card` 一致，实现者照 spec §4 细节 + 既有风格填。backend/blocker 核心（T1-T10, T17）给了完整可跑代码。若需 T11-16 逐行代码，执行时按 subagent 单任务再展开。

**类型一致性：** `discover_all_projects`→`list[dict{path,name,kind}]`，`classify_project_dir`→`str kind`（`unavailable|workspace|candidate|folder`），`adopt_project`→`AdoptResult(status)`，`CoverThumbnailWorker.decoded(rid, QImage)`，`GridThumbnailWorker.decoded(path, QImage)`，`ThumbnailListModel.set_paths(list[str])`——跨任务一致。

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-07-07-project-tree-redesign.md`. Two execution options:

**1. Subagent-Driven (recommended)** - I dispatch a fresh subagent per task, review between tasks, fast iteration.

**2. Inline Execution** - Execute tasks in this session using executing-plans, batch execution with checkpoints.

Which approach?
