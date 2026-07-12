"""还原原片的崩溃/断电安全性 —— 中途死机不许丢数据, 重跑要能接上。

用户 2026-07-12: "还原原片, 如果中途死机, 数据会丢失吗? 很多特殊情况我可能没考虑到"。

还原的 5 步: ①解包到临时目录 ②逐张写回原位 ③写库 ④删 ZIP ⑤刷界面
  断在① -> 临时目录残留, ZIP 在, 库没动          -> 零损失
  断在② -> 原子替换: 要么整张写好, 要么没写      -> 零损失, 重跑即可
  断在③ -> JPG 已回原位, 但库还标"已归档"        -> **重跑会卡**(本文件修的就是它)
  断在④ -> 库已更新, ZIP 成孤儿躺在盘上           -> 只占空间, 不丢数据
  断在⑤ -> 纯界面                                -> 零损失

断在③ 的老毛病: 重跑时 JPG 已在原位 -> 被当成"已存在, 跳过" -> 写出 0 张 ->
  判定"未能还原" -> 归档登记和 ZIP 永远留着, 组永远退不回。用户卡死。
修法: 目标文件已存在且 **SHA-256 与归档清单一致** = 这张已经还原过了, 算成功
  (restored), 不算跳过 -> 重跑能把剩下的步骤走完。内容不一致才算"跳过"(那是用户
  自己的新文件, 不能乱覆盖)。

(Fable 5, 2026-07-12)
"""
from __future__ import annotations

import hashlib
import json
import os
import zipfile

from app.services.archive_service import restore_archive_to_original_paths


def _make_archive(tmp_path, payloads: dict[str, bytes]):
    """造一个带 manifest(含 size + sha256)的归档 ZIP, 返回 (zip_path, 原路径列表)。"""
    zp = tmp_path / "results" / "r.zip"
    zp.parent.mkdir(parents=True, exist_ok=True)
    files = []
    targets = []
    with zipfile.ZipFile(zp, "w") as zf:
        for name, data in payloads.items():
            zf.writestr(name, data)
            files.append({
                "archiveName": name,
                "originalSize": len(data),
                "originalSha256": hashlib.sha256(data).hexdigest(),
            })
            targets.append(str(tmp_path / "incoming-jpg" / name))
        zf.writestr("manifest.json", json.dumps({"files": files}))
    (tmp_path / "incoming-jpg").mkdir(parents=True, exist_ok=True)
    return str(zp), targets


def test_resume_after_crash_counts_already_restored_files(tmp_path):
    """崩在「写完 JPG、还没写库」那一刻 -> 重跑必须把它算成已还原, 不能算跳过。

    否则: 写出 0 张 -> "未能还原" -> ZIP 删不掉、组退不回, 用户永久卡住。
    """
    a, b = b"\xff\xd8\xffAAA", b"\xff\xd8\xffBBB"
    zip_path, targets = _make_archive(tmp_path, {"a.jpg": a, "b.jpg": b})

    # 模拟崩溃前已经写回去的那一张(内容与归档一致)
    with open(targets[0], "wb") as fh:
        fh.write(a)

    res = restore_archive_to_original_paths(zip_path, targets, overwrite=False)

    assert res.ok, f"重跑必须成功: {res.failures} {res.reason}"
    assert res.count == 2, "已还原过的那张也要计入, 否则上层判定「未能还原」"
    assert not res.skipped, "内容一致 = 已还原, 不是「跳过」"
    assert open(targets[1], "rb").read() == b


def test_existing_different_file_is_skipped_not_overwritten(tmp_path):
    """原位置那张是**用户自己的新文件**(内容不同) -> 不覆盖, 记为跳过。"""
    a = b"\xff\xd8\xffAAA"
    zip_path, targets = _make_archive(tmp_path, {"a.jpg": a})
    with open(targets[0], "wb") as fh:
        fh.write(b"\xff\xd8\xff_USER_EDITED_")

    res = restore_archive_to_original_paths(zip_path, targets, overwrite=False)

    assert res.skipped == [targets[0]]
    assert res.count == 0
    assert open(targets[0], "rb").read() == b"\xff\xd8\xff_USER_EDITED_", "不许动用户的文件"


def test_partial_write_never_left_behind_on_failure(tmp_path):
    """归档里少了一张 -> 那张记失败, 已写好的另一张仍完整; 不留半截文件。"""
    a = b"\xff\xd8\xffAAA"
    zip_path, targets = _make_archive(tmp_path, {"a.jpg": a})
    # 人为多要一个原路径 -> 数量对不上, 走"数量不一致"保护
    targets_plus = targets + [str(tmp_path / "incoming-jpg" / "ghost.jpg")]

    res = restore_archive_to_original_paths(zip_path, targets_plus, overwrite=False)

    assert not res.ok
    assert res.count == 0
    assert not os.path.exists(targets_plus[1]), "不许留下半截/空文件"
