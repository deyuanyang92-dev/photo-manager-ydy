"""project_scan_disk_cache.py — 项目树扫描结果的磁盘持久缓存.

Claude Code 2026-07-15 — 大规模性能阶段 1b: 用户要求"扫描一次后就不要一直扫描,
加缓存机制"(2026-07-14 grill-me)。project_tree_service 的内存缓存只活一个会话、
TTL 才 2 秒; 这里把 scan_tree 结果落盘 JSON, 关软件重开也认, TTL 由设置控制。

设计仿 thumbnail_disk_cache:
  · 根 = <repo>/data/cache/project_scan/(mkdir on access; 测试可 override)。
  · key = sha256(resolved_dir \\0 max_depth) -> 分片 root/aa/bb/{digest}.json。
  · 失效: 存的时候把「目录指纹(root mtime_ns + size + 可见子目录数)」和「存盘时间」
    一起写进去; 读的时候 (a) 指纹变了 -> miss(目录动过), (b) now-存盘时间 > ttl
    -> miss(过期)。ttl=0 -> 永远 miss(等于关缓存, 每次都重扫)。
  · 纯 json/hashlib/os, 无 Qt, 易测。

指纹语义与 project_tree_service 的内存缓存一致(都只看 root 层): 深层某文件变动不改
root mtime, 所以不会立刻失效 —— 这是用户明确要的"别一直扫", TTL 是陈旧上限, 手动
「扫描项目位置」是强制刷新入口。
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any, Optional

_CACHE_ROOT_OVERRIDE: Optional[Path] = None


def set_cache_root_for_tests(path: Optional[str]) -> None:
    """测试用: 把缓存根指到 tmp, 不碰真 data/cache。传 None 复位。"""
    global _CACHE_ROOT_OVERRIDE
    _CACHE_ROOT_OVERRIDE = Path(path) if path else None


def scan_cache_root() -> Path:
    if _CACHE_ROOT_OVERRIDE is not None:
        root = _CACHE_ROOT_OVERRIDE
    else:
        root = Path(__file__).resolve().parents[2] / "data" / "cache" / "project_scan"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _resolved(dir_path: str) -> str:
    try:
        return str(Path(dir_path).resolve())
    except OSError:
        return str(dir_path)


def _dir_fingerprint(resolved_dir: str) -> list:
    """目录指纹: (root mtime_ns, size, 可见子目录数)。目录动过 -> 指纹变。

    与 project_tree_service._root_fingerprint 同源(那边是元组, 这里落盘用 list 便于
    JSON 往返), 语义一致。RESERVED_DIR_NAMES 在服务层, 这里避免循环导入不引用它,
    用同样的"隐藏目录不算"规则即可(reserved 目录不是隐藏的, 但只要指纹口径稳定、
    自洽就能可靠检测变化 —— 我们比的是同一函数前后两次的输出)。
    """
    p = Path(resolved_dir)
    try:
        st = p.stat()
        root_sig = [st.st_mtime_ns, st.st_size]
    except OSError:
        root_sig = None
    try:
        visible = sum(
            1 for e in os.scandir(p)
            if not e.name.startswith(".") and e.is_dir()
        )
    except OSError:
        visible = -1
    return [root_sig, visible]


def _entry_path(resolved_dir: str, max_depth: int) -> Path:
    digest = hashlib.sha256(
        f"{resolved_dir}\0{int(max_depth)}".encode("utf-8")
    ).hexdigest()
    root = scan_cache_root()
    shard = root / digest[:2] / digest[2:4]
    shard.mkdir(parents=True, exist_ok=True)
    return shard / f"{digest}.json"


def get(dir_path: str, max_depth: int, ttl_seconds: int) -> Optional[Any]:
    """命中且未过期且指纹未变 -> 返回缓存的扫描结果; 否则 None。

    ttl_seconds <= 0 -> 直接 None(等于关缓存)。
    """
    if ttl_seconds is None or ttl_seconds <= 0:
        return None
    resolved = _resolved(dir_path)
    path = _entry_path(resolved, max_depth)
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    saved_at = payload.get("saved_at")
    if not isinstance(saved_at, (int, float)):
        return None
    if time.time() - saved_at > ttl_seconds:
        return None  # 过期
    if payload.get("fingerprint") != _dir_fingerprint(resolved):
        return None  # 目录动过
    return payload.get("value")


def put(dir_path: str, max_depth: int, value: Any) -> None:
    """把扫描结果落盘。失败静默(缓存写不进不该拖垮扫描)。"""
    resolved = _resolved(dir_path)
    payload = {
        "resolved_dir": resolved,
        "max_depth": int(max_depth),
        "saved_at": time.time(),
        "fingerprint": _dir_fingerprint(resolved),
        "value": value,
    }
    path = _entry_path(resolved, max_depth)
    tmp = path.with_suffix(f".{os.getpid()}.tmp")
    try:
        tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, path)  # 原子替换
    except OSError:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass


def clear(dir_path: Optional[str] = None) -> None:
    """清缓存。dir_path=None 清全部(用于测试 / 手动强制刷新)。"""
    root = scan_cache_root()
    if dir_path is None:
        for sub in root.glob("*/*/*.json"):
            try:
                sub.unlink()
            except OSError:
                pass
        return
    # 单目录: 所有 max_depth 都可能有条目, 逐个 depth 算 key 删(depth 范围小)
    resolved = _resolved(dir_path)
    for depth in range(1, 13):
        try:
            _entry_path(resolved, depth).unlink(missing_ok=True)
        except OSError:
            pass
