"""project_node_ops.py — 项目树节点的增删改移（用户 R-009，2026-07-13）。

项目树此前只能「新建」，没有删除 / 重命名 / 移动 —— 用户："这种属于基本操作，
我都不应该提，你都应该加入。"

本模块是**纯服务层**（无 Qt），只做磁盘操作 + 记录同步；所有确认/预览交给 UI，
但确认所需的**事实**（有多少图、多少母图、多少空间、该用哪种确认强度）由这里给出。

红线
----
1. **删除默认送系统回收站**（send2trash），不是直接抹除。只有显式 ``permanent=True``
   才真删。TIFF 是无价母图（不可再生），手滑丢了没法补。
2. **有内容必须拦**：``confirm_level()`` 按内容升级确认强度 ——
   空目录 → ``"simple"``；有 JPG/标本 → ``"confirm"``（明确告知「非空」）；
   **有 TIFF 母图 → ``"typed"``**（必须手打目录名才能删）。
3. **移动 = 整个文件夹搬走**，``_data/project.db`` 跟着走 —— 零迁移、零转换。
4. **路径记录必须同步**：重命名/移动之后，``user_projects.json`` 里的 directory 要跟着改，
   否则「最近使用」会指向一个不存在的路径（Windows ``E:\\`` 与 WSL ``/mnt/e`` 两种写法
   都要能匹配上）。
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any, Optional

# 文件名里禁止出现的字符（与 project_tree_view._new_subfolder 的口径一致）
_BAD_NAME_CHARS = set('/\\:*?"<>|')

_TIFF_SUFFIXES = {".tif", ".tiff"}
_JPG_SUFFIXES = {".jpg", ".jpeg"}

# 工作区内部目录（不算作「子目录」，见 project_tree_service.RESERVED_DIR_NAMES）
_DATA_DIRNAME = "_data"
_DB_NAME = "project.db"


# ── 内容盘点 ─────────────────────────────────────────────────────────────────

def describe_contents(path: str) -> dict[str, Any]:
    """走一遍目录，算清里面到底有什么 —— 删除确认的事实依据。

    返回 ``{tiff_count, jpg_count, workspace_count, file_count, total_bytes}``。
    坏权限/坏软链一律跳过，不抛异常（盘点失败不该阻断 UI）。
    """
    root = Path(path)
    tiff = jpg = files = ws = 0
    total = 0
    if not root.exists():
        return {
            "tiff_count": 0, "jpg_count": 0, "workspace_count": 0,
            "file_count": 0, "total_bytes": 0,
        }
    for p in root.rglob("*"):
        try:
            if p.is_dir():
                if p.name == _DATA_DIRNAME and (p / _DB_NAME).exists():
                    ws += 1
                continue
            files += 1
            total += p.stat().st_size
            suffix = p.suffix.lower()
            if suffix in _TIFF_SUFFIXES:
                tiff += 1
            elif suffix in _JPG_SUFFIXES:
                jpg += 1
        except OSError:
            continue
    # 目录自身就是工作区时 rglob 也会扫到它的 _data，上面已计入
    return {
        "tiff_count": tiff,
        "jpg_count": jpg,
        "workspace_count": ws,
        "file_count": files,
        "total_bytes": total,
    }


def confirm_level(path: str) -> str:
    """删除该走哪种确认强度。

    * ``"simple"``  空目录 —— 普通「确定删除吗」即可；
    * ``"confirm"`` 有 JPG / 有文件 —— 必须明确告知「非空项目，含 N 张图片」；
    * ``"typed"``   **有 TIFF 母图** —— 无价、不可再生 ⇒ 必须手打目录名才放行。
    """
    info = describe_contents(path)
    if info["tiff_count"] > 0:
        return "typed"
    if info["jpg_count"] > 0 or info["file_count"] > 0:
        return "confirm"
    return "simple"


def _human_bytes(n: int) -> str:
    step = 1024.0
    value = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < step or unit == "TB":
            return f"{value:.0f} {unit}" if unit in ("B", "KB") else f"{value:.1f} {unit}"
        value /= step
    return f"{value:.1f} TB"


def summarize_for_confirm(path: str) -> str:
    """给确认框用的一句话：这个目录里到底有什么、删了会失去什么。"""
    name = Path(path).name or path
    info = describe_contents(path)
    if info["file_count"] == 0:
        return f"「{name}」是空目录。"
    bits: list[str] = []
    if info["workspace_count"]:
        bits.append(f"{info['workspace_count']} 个拍摄目录")
    if info["tiff_count"]:
        bits.append(f"{info['tiff_count']} 张 TIFF 母图（不可再生）")
    if info["jpg_count"]:
        bits.append(f"{info['jpg_count']} 张 JPG 原片")
    if not bits:
        bits.append(f"{info['file_count']} 个文件")
    return (
        f"「{name}」不是空项目，包含 " + "、".join(bits)
        + f"，共 {_human_bytes(info['total_bytes'])}。"
    )


# ── 记录同步（user_projects.json） ───────────────────────────────────────────

def _resolved(path: str) -> str:
    try:
        return str(Path(path).expanduser().resolve())
    except (OSError, ValueError):
        return str(path)


def _same_path(a: str, b: str) -> bool:
    """Windows ``E:\\x`` 与 WSL ``/mnt/e/x`` 是同一个目录 —— 都归一化后再比。"""
    if not a or not b:
        return False
    return _resolved(a).replace("\\", "/").lower() == _resolved(b).replace("\\", "/").lower()


def _load_records() -> tuple[Optional[Path], dict]:
    from app.services.project_service import default_user_projects_json_path

    try:
        json_path = Path(default_user_projects_json_path())
        data = json.loads(json_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None, {}
    if not isinstance(data, dict) or not isinstance(data.get("projects"), list):
        return json_path, {}
    return json_path, data


def _save_records(json_path: Path, data: dict) -> None:
    try:
        json_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except OSError:
        pass  # 记录同步失败不该让磁盘操作回滚 —— 树刷新时会自愈


def _rewrite_records(old: str, new: Optional[str], *, new_name: Optional[str] = None) -> None:
    """把记录里指向 *old*（及其子路径）的条目改写到 *new*；``new=None`` = 删除该条目。"""
    json_path, data = _load_records()
    if json_path is None or not data:
        return
    old_res = _resolved(old)
    kept: list[dict] = []
    for entry in data.get("projects", []):
        directory = entry.get("directory") or entry.get("dir") or ""
        if not directory:
            kept.append(entry)
            continue
        dir_res = _resolved(directory)
        is_self = _same_path(directory, old)
        is_child = dir_res.startswith(old_res.rstrip("/\\") + "/") or dir_res.startswith(
            old_res.rstrip("/\\") + "\\"
        )
        if not (is_self or is_child):
            kept.append(entry)
            continue
        if new is None:
            continue  # 删除 → 条目一并清掉
        # 重定位：把前缀换成新路径（子目录的相对部分保留）
        suffix = dir_res[len(old_res):]
        moved = str(Path(_resolved(new) + suffix))
        entry = dict(entry)
        entry["directory"] = moved
        if "dir" in entry:
            entry["dir"] = moved
        if is_self and new_name:
            entry["name"] = new_name
        kept.append(entry)
    data["projects"] = kept
    _save_records(json_path, data)


# ── 删除 ─────────────────────────────────────────────────────────────────────

def _send_to_trash(path: str) -> None:
    """送系统回收站。send2trash 缺失时抛 RuntimeError —— **绝不静默改成真删**。"""
    try:
        from send2trash import send2trash
    except ImportError as exc:  # pragma: no cover - 环境缺依赖
        raise RuntimeError(
            "回收站功能不可用（缺 send2trash）。如需彻底删除请显式选择「彻底删除」。"
        ) from exc
    send2trash(str(Path(path)))


def delete_node(path: str, *, permanent: bool = False) -> None:
    """删除一个项目/目录/拍摄目录。

    **默认送回收站**（可找回）。``permanent=True`` 才真抹除 —— 调用方必须已经
    按 :func:`confirm_level` 做过对应强度的确认（有 TIFF 时须手打目录名）。
    """
    if not path or not str(path).strip():
        raise ValueError("路径为空")
    target = Path(path)
    if not target.exists():
        raise FileNotFoundError(f"目录不存在：{path}")
    if not target.is_dir():
        raise ValueError(f"不是目录：{path}")

    if permanent:
        shutil.rmtree(target)
    else:
        _send_to_trash(str(target))
    _rewrite_records(str(target), None)


# ── 重命名 ───────────────────────────────────────────────────────────────────

def _validate_name(name: str) -> str:
    clean = (name or "").strip()
    if not clean:
        raise ValueError("名称不能为空")
    if clean in (".", ".."):
        raise ValueError("名称非法")
    if any(ch in _BAD_NAME_CHARS for ch in clean):
        raise ValueError(r'名称不能包含 / \ : * ? " < > |')
    return clean


def rename_node(path: str, new_name: str) -> str:
    """改名。磁盘改名 + 记录同步；返回新路径。"""
    clean = _validate_name(new_name)
    src = Path(path)
    if not src.is_dir():
        raise FileNotFoundError(f"目录不存在：{path}")
    dest = src.parent / clean
    if dest.exists() and not _same_path(str(dest), str(src)):
        raise FileExistsError(f"同级下已存在「{clean}」")
    src.rename(dest)
    _rewrite_records(str(src), str(dest), new_name=clean)
    return str(dest)


# ── 移动 ─────────────────────────────────────────────────────────────────────

def _is_within(child: str, parent: str) -> bool:
    c, p = _resolved(child), _resolved(parent)
    return c == p or c.startswith(p.rstrip("/\\") + "/") or c.startswith(p.rstrip("/\\") + "\\")


def preview_move(
    path: str,
    target_parent: str,
    *,
    parent_meta: Optional[dict] = None,
    node_meta: Optional[dict] = None,
) -> dict[str, Any]:
    """移动前的预览：搬到哪、带走什么、哪些资料会继承、哪些保留。

    **不静默覆盖**：只有节点自己**没填过**的字段才继承新父项目；填过的一律保留。
    """
    src = Path(path)
    dest = Path(target_parent) / src.name
    parent_meta = parent_meta or {}
    node_meta = node_meta or {}

    inherit = {
        k: v for k, v in parent_meta.items()
        if v not in (None, "") and not str(node_meta.get(k) or "").strip()
    }
    keep = {
        k: v for k, v in node_meta.items()
        if str(v or "").strip()
    }
    return {
        "source_path": str(src),
        "target_path": str(dest),
        "contents": describe_contents(str(src)),
        "inherit": inherit,
        "keep": keep,
    }


def move_node(path: str, target_parent: str) -> str:
    """把一个目录整体搬进 *target_parent*。

    文件夹整体 move —— ``_data/project.db`` 和 results/ 里的 TIFF 母图跟着走，
    **零迁移、零转换**。返回新路径。
    """
    src = Path(path)
    parent = Path(target_parent)
    if not src.is_dir():
        raise FileNotFoundError(f"目录不存在：{path}")
    if not parent.is_dir():
        raise FileNotFoundError(f"目标项目不存在：{target_parent}")
    if _is_within(str(parent), str(src)):
        raise ValueError("不能把一个目录移动到它自己（或它的子目录）里")
    dest = parent / src.name
    if dest.exists():
        raise FileExistsError(f"目标项目下已存在「{src.name}」")

    shutil.move(str(src), str(dest))
    _rewrite_records(str(src), str(dest))
    return str(dest)
