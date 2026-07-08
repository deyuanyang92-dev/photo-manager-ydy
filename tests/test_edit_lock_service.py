"""test_edit_lock_service.py — 编辑锁:密码 config + 会话解锁(spec 2026-07-08 §4.3)."""
from __future__ import annotations

from app.services import edit_lock_service as el


class _Ctx:
    def __init__(self) -> None:
        self.edit_unlocked = False
        self.edit_actor = ""


def test_default_password_is_123(tmp_path) -> None:
    cfg = tmp_path / "app_config.json"
    assert el.verify_password("123", str(cfg)) is True
    assert el.verify_password("wrong", str(cfg)) is False


def test_password_not_stored_plaintext(tmp_path) -> None:
    cfg = tmp_path / "app_config.json"
    el.set_password("123", str(cfg))  # 写盘
    import json
    data = json.loads(cfg.read_text(encoding="utf-8"))
    assert data["edit_password"] != "123", "明文不可落盘"
    assert len(data["edit_password"]) == 64, "sha256 hex"


def test_set_password_invalidates_old(tmp_path) -> None:
    cfg = tmp_path / "app_config.json"
    el.set_password("newpass", str(cfg))
    assert el.verify_password("newpass", str(cfg)) is True
    assert el.verify_password("123", str(cfg)) is False


def test_unlock_sets_state_and_actor(tmp_path) -> None:
    cfg = tmp_path / "app_config.json"
    ctx = _Ctx()
    ok = el.unlock(ctx, "张三", "123", str(cfg))
    assert ok is True
    assert el.is_unlocked(ctx) is True
    assert el.current_actor(ctx) == "张三"


def test_unlock_wrong_password_keeps_locked(tmp_path) -> None:
    cfg = tmp_path / "app_config.json"
    ctx = _Ctx()
    ok = el.unlock(ctx, "x", "wrong", str(cfg))
    assert ok is False
    assert el.is_unlocked(ctx) is False
    assert el.current_actor(ctx) == ""


def test_lock_resets(tmp_path) -> None:
    cfg = tmp_path / "app_config.json"
    ctx = _Ctx()
    el.unlock(ctx, "张三", "123", str(cfg))
    el.lock(ctx)
    assert el.is_unlocked(ctx) is False
    assert el.current_actor(ctx) == ""


def test_require_unlock_reflects_state() -> None:
    ctx = _Ctx()
    assert el.require_unlock(ctx) is False
    ctx.edit_unlocked = True
    assert el.require_unlock(ctx) is True
