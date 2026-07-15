"""test_monitor_scan_scandir.py — scandir 化后输出与旧 listdir 版逐字段一致.

Claude Code 2026-07-15 — 大规模性能优化阶段 1a: monitor_service 的目录枚举从
os.listdir + 每文件 2 次 os.stat(isfile + build_entry) 改成 os.scandir(一次目录
读取带回缓存 stat, 每文件 0 次额外 syscall)。这是纯提速改动, FileEntry 列表必须
逐字段不变 —— 本文件把"不变"钉成红线。
"""
from __future__ import annotations

import os

from app.services.monitor_service import (
    _list_pending_jpg_entries,
    _list_tiff_entries,
)


def _write(path: str, data: bytes = b"\xff\xd8\xffX") -> None:
    with open(path, "wb") as f:
        f.write(data)


def _entry_tuple(e):
    return (
        e.name, e.path, e.kind, e.size, e.mtime, e.detail,
        getattr(e, "basename", None), getattr(e, "has_zip", None),
    )


# ── 参考实现: 改动前的 os.listdir 版本(冻结在测试里做对拍基准) ──────────────

def _ref_list_jpg(jpg_dir, archived_set, detail_prefix, include_hidden=False):
    import re
    from app.services.monitor_service import _build_file_entry
    result = []
    if not os.path.isdir(jpg_dir):
        return result
    for name in os.listdir(jpg_dir):
        if not include_hidden and name.startswith("."):
            continue
        if not re.search(r"\.jpe?g$", name, re.IGNORECASE):
            continue
        if name in archived_set:
            continue
        full = os.path.join(jpg_dir, name)
        try:
            if os.path.isfile(full):
                result.append(_build_file_entry(full, name, "jpg", detail_prefix + " · 未关联原片"))
        except OSError:
            pass
    return result


def _ref_list_tiff(tiff_dir, processed_set, detail_prefix,
                   skip_processed=True, skip_if_zip=True, include_hidden=False):
    import re
    from pathlib import Path
    from app.services.monitor_service import _build_file_entry
    result = []
    if not os.path.isdir(tiff_dir):
        return result
    for name in os.listdir(tiff_dir):
        if not include_hidden and name.startswith("."):
            continue
        if not re.search(r"\.tiff?$", name, re.IGNORECASE):
            continue
        base = Path(name).stem
        if skip_processed and base in processed_set:
            continue
        if skip_if_zip:
            if os.path.isfile(os.path.join(tiff_dir, base + ".zip")):
                continue
        full = os.path.join(tiff_dir, name)
        try:
            if os.path.isfile(full):
                e = _build_file_entry(full, name, "tiff", detail_prefix + " · TIFF")
                e.basename = base
                e.has_zip = os.path.isfile(os.path.join(tiff_dir, base + ".zip"))
                result.append(e)
        except OSError:
            pass
    return result


def _sorted(entries):
    return sorted(entries, key=lambda e: e.path)


def test_jpg_scandir_matches_listdir_output(tmp_path):
    d = tmp_path / "incoming-jpg"
    d.mkdir()
    for n in ["a.jpg", "b.JPEG", "c.jpeg", "d.png", ".hidden.jpg", "archived.jpg"]:
        _write(str(d / n))
    (d / "subdir").mkdir()  # 目录不该被当文件

    archived = {"archived.jpg"}
    got = _sorted(_list_pending_jpg_entries(str(d), archived, "前缀"))
    ref = _sorted(_ref_list_jpg(str(d), archived, "前缀"))

    assert [_entry_tuple(e) for e in got] == [_entry_tuple(e) for e in ref]
    names = {e.name for e in got}
    assert names == {"a.jpg", "b.JPEG", "c.jpeg"}  # png/隐藏/已归档 全排除


def test_tiff_scandir_matches_listdir_output(tmp_path):
    d = tmp_path / "results"
    d.mkdir()
    for n in ["x.tif", "y.tiff", "z.TIF", "done.tif", "withzip.tif", "notatiff.jpg"]:
        _write(str(d / n))
    _write(str(d / "withzip.zip"))  # withzip.tif 应因同名 zip 被跳过

    processed = {"done"}
    got = _sorted(_list_tiff_entries(str(d), processed, "前缀"))
    ref = _sorted(_ref_list_tiff(str(d), processed, "前缀"))

    assert [_entry_tuple(e) for e in got] == [_entry_tuple(e) for e in ref]
    names = {e.name for e in got}
    assert names == {"x.tif", "y.tiff", "z.TIF"}  # done(已处理)/withzip(有zip)/jpg 全排除


def test_missing_dir_returns_empty(tmp_path):
    missing = str(tmp_path / "nope")
    assert _list_pending_jpg_entries(missing, set(), "p") == []
    assert _list_tiff_entries(missing, set(), "p") == []


def test_include_hidden_flag(tmp_path):
    d = tmp_path / "incoming-jpg"
    d.mkdir()
    _write(str(d / ".secret.jpg"))
    _write(str(d / "visible.jpg"))
    got = _sorted(_list_pending_jpg_entries(str(d), set(), "p", include_hidden=True))
    ref = _sorted(_ref_list_jpg(str(d), set(), "p", include_hidden=True))
    assert [_entry_tuple(e) for e in got] == [_entry_tuple(e) for e in ref]
    assert {e.name for e in got} == {".secret.jpg", "visible.jpg"}
