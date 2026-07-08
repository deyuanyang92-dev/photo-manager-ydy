"""edit_lock_service.py — 数据筛选页编辑锁(密码 + 会话解锁).

spec: docs/specs/2026-07-08-data-filter-view-design.md §4.3

- 密码存 ``data/app_config.json`` (sha256, **明文不落盘**), 默认 ``"123"``。
- 会话登录(A): ``unlock(ctx, actor, plain)`` 校验通过后置 ``ctx.edit_unlocked=True``
  + ``ctx.edit_actor=actor``, 本会话内编辑免再输; ``lock(ctx)`` 复位。
- 编辑动作调 ``require_unlock(ctx)``: True=可编辑, False=调用方应弹密码框。

Qt-free, 纯 json/hashlib, 易测(config_path 可注入, 不写真 data/)。
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Optional

DEFAULT_PASSWORD = "123"


def _default_config_path() -> str:
    """app-local ``data/app_config.json``(与 user_projects.json 同目录)。"""
    return str(Path(__file__).resolve().parents[2] / "data" / "app_config.json")


def _hash(plain: str) -> str:
    return hashlib.sha256(plain.encode("utf-8")).hexdigest()


def load_config(config_path: Optional[str] = None) -> dict[str, Any]:
    """读 config; 缺省/损坏 → 返默认密码 hash(默认 ``123``)。"""
    p = Path(config_path) if config_path else Path(_default_config_path())
    if not p.exists():
        return {"edit_password": _hash(DEFAULT_PASSWORD)}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or "edit_password" not in data:
            return {"edit_password": _hash(DEFAULT_PASSWORD)}
        return data
    except (json.JSONDecodeError, OSError):
        return {"edit_password": _hash(DEFAULT_PASSWORD)}


def save_config(cfg: dict[str, Any], config_path: Optional[str] = None) -> None:
    p = Path(config_path) if config_path else Path(_default_config_path())
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")


def verify_password(plain: str, config_path: Optional[str] = None) -> bool:
    return load_config(config_path).get("edit_password") == _hash(plain)


def set_password(plain: str, config_path: Optional[str] = None) -> None:
    """改密码(设置页用)。"""
    cfg = load_config(config_path)
    cfg["edit_password"] = _hash(plain)
    save_config(cfg, config_path)


def is_unlocked(ctx: Any) -> bool:
    return bool(getattr(ctx, "edit_unlocked", False))


def current_actor(ctx: Any) -> str:
    return str(getattr(ctx, "edit_actor", "") or "")


def unlock(ctx: Any, actor: str, plain: str, config_path: Optional[str] = None) -> bool:
    """校验密码 → 置会话解锁状态 + 操作员名。错密 → False, 不改状态。"""
    if not verify_password(plain, config_path):
        return False
    ctx.edit_unlocked = True
    ctx.edit_actor = str(actor or "").strip()
    return True


def lock(ctx: Any) -> None:
    """退出编辑模式: 复位会话状态。"""
    ctx.edit_unlocked = False
    ctx.edit_actor = ""


def require_unlock(ctx: Any) -> bool:
    """编辑前调用: True=已解锁可编辑, False=调用方应弹密码框。"""
    return is_unlocked(ctx)
