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


# ── Claude Code 修改 2026-07-14 — fail-closed on corrupt config (codex 回归) ──
# 缺省(文件不存在) = 正常首跑, 默认密码 123 照常通行(见 test_default_password_is_123)。
# 但文件已存在却读不出有效密码(结构损坏/JSON 解析失败/IO 错误), 绝不能悄悄回落
# 默认密码放行 —— 必须 fail-closed, 抛 EditLockConfigError 让调用方锁死+提示。

def test_corrupt_json_fails_closed_not_default_password(tmp_path) -> None:
    cfg = tmp_path / "app_config.json"
    cfg.write_text("{not valid json", encoding="utf-8")
    import pytest
    with pytest.raises(el.EditLockConfigError):
        el.verify_password("123", str(cfg))


def test_missing_password_key_fails_closed_not_default_password(tmp_path) -> None:
    cfg = tmp_path / "app_config.json"
    cfg.write_text('{"something_else": true}', encoding="utf-8")
    import pytest
    with pytest.raises(el.EditLockConfigError):
        el.verify_password("123", str(cfg))


def test_unlock_propagates_config_error_and_stays_locked(tmp_path) -> None:
    cfg = tmp_path / "app_config.json"
    cfg.write_text("{not valid json", encoding="utf-8")
    ctx = _Ctx()
    import pytest
    with pytest.raises(el.EditLockConfigError):
        el.unlock(ctx, "张三", "123", str(cfg))
    assert el.is_unlocked(ctx) is False


def test_missing_config_file_still_allows_default_password(tmp_path) -> None:
    """首跑(文件从未建过)不是"损坏", 默认密码必须照常能用 —— 这条不能被
    fail-closed 误伤。"""
    cfg = tmp_path / "app_config.json"
    assert not cfg.exists()
    assert el.verify_password("123", str(cfg)) is True
