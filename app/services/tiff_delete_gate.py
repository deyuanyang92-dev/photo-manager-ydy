"""tiff_delete_gate.py — TIF 删除的管理员闸门(密码 + 操作人 + 审计留痕)。

用户 2026-07-12 裁定(以此为准, 旧的"TIFF 永不删"记忆作废):
    "TIF 不是不能删, 可以的, 只是需要管理员。为何保护 tif —— 增加管理员,
     如果删除, 要输入密码, 给出删除的操作人。"

所以 TIF 删除 = **可以做, 但必须留下是谁干的**:
    ① 输入管理员密码(复用数据筛选页那套 edit_lock_service: sha256 存
       data/app_config.json, 默认 "123", 明文不落盘 —— 不另造第二套密码)
    ② 必须填**操作人姓名**(不填不许删)
    ③ 删除动作写进项目库的 audit_log 表(谁、什么时候、删了哪个文件、多大),
       文件没了但记录还在, 事后能追责

Qt-free: 这里只做「校验 + 记账」, 弹框在 app/widgets/admin_delete_dialog.py。
(Fable 5, 2026-07-12)
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

ACTION_DELETE_TIFF = "delete_tiff"
ENTITY_FILE = "file"


class TiffDeleteDenied(Exception):
    """密码错 / 没填操作人 —— 不许删。"""


def check_admin(actor: str, password: str, *, config_path: Optional[str] = None) -> str:
    """校验管理员身份, 返回规范化后的操作人姓名; 不通过则抛 TiffDeleteDenied。"""
    from app.services.edit_lock_service import verify_password

    who = str(actor or "").strip()
    if not who:
        raise TiffDeleteDenied("请填写删除操作人姓名。")
    if not verify_password(str(password or ""), config_path):
        raise TiffDeleteDenied("管理员密码错误。")
    return who


def record_tiff_deletion(db: Any, actor: str, path: str, *, reason: str = "") -> None:
    """把这次删除写进 audit_log —— 文件可以没, 记录不能没。

    db 为 None(无项目上下文)时只记日志文件, 不抛异常 —— 删除本身不该因为记账
    失败而失败, 但必须留痕可查。
    """
    info = {"path": str(path or "")}
    try:
        st = os.stat(path)
        info["size"] = int(st.st_size)
        info["mtime"] = float(st.st_mtime)
    except OSError:
        pass
    if reason:
        info["reason"] = reason

    logger.warning("TIF 删除: actor=%s path=%s info=%s", actor, path, info)
    if db is None:
        return
    try:
        from app.services.activity_audit_service import log_event

        log_event(
            db,
            actor=actor,
            action=ACTION_DELETE_TIFF,
            entity_type=ENTITY_FILE,
            entity_id=str(Path(path).name),
            old_value=info,
            new_value={"deleted": True},
        )
    except Exception:  # noqa: BLE001
        logger.exception("TIF 删除审计写入失败: %s", path)


def delete_tiff_with_audit(
    db: Any,
    path: str,
    *,
    actor: str,
    password: str,
    reason: str = "",
    config_path: Optional[str] = None,
) -> None:
    """一步到位: 校验管理员 -> 记账 -> 删文件。

    记账在删除**之前**(拿得到文件大小/时间); 删除失败则抛 OSError, 审计里那条
    记录会留着 —— 宁可多一条"尝试删除"的痕迹, 也不要删了却没记。
    """
    who = check_admin(actor, password, config_path=config_path)
    if not os.path.isfile(path):
        raise FileNotFoundError(f"文件不存在: {path}")
    record_tiff_deletion(db, who, path, reason=reason)
    os.unlink(path)
