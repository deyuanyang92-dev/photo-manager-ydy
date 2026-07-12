"""项目树的增删改移 —— 基本操作（用户 R-009，2026-07-13）。

用户："这种属于基本操作，我都不应该提，你都应该加入。"

红线（TIFF 是无价母图，手滑不能丢）：
  * 删除**默认送回收站**，不是直接抹除；只有显式 permanent=True 才真删；
  * 删除前必须能算清内容（多少标本 / 多少 TIFF / 多少字节），供 UI 做二次确认；
  * 移动 = 整个文件夹搬走（``_data/project.db`` 跟着走，零迁移），
    并把 ``user_projects.json`` 里的路径同步改掉（否则「最近使用」指向不存在的路径）；
  * 重命名同理：磁盘改名 + 记录同步。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.services import project_node_ops as ops


# ── helpers ──────────────────────────────────────────────────────────────────

def _mk_workspace(parent: Path, name: str, *, tiffs: int = 0, jpgs: int = 0) -> Path:
    ws = parent / name
    (ws / "_data").mkdir(parents=True, exist_ok=True)
    (ws / "_data" / "project.db").write_bytes(b"sqlite-ish")
    results = ws / "results"
    results.mkdir(exist_ok=True)
    for i in range(tiffs):
        (results / f"UID-{i}.tif").write_bytes(b"x" * 1024)
    incoming = ws / "incoming-jpg"
    incoming.mkdir(exist_ok=True)
    for i in range(jpgs):
        (incoming / f"P{i}.JPG").write_bytes(b"y" * 512)
    return ws


def _seed_projects(json_path: Path, entries: list[dict]) -> None:
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(
        json.dumps({"version": 1, "projects": entries}, ensure_ascii=False),
        encoding="utf-8",
    )


def _projects(json_path: Path) -> list[dict]:
    return json.loads(json_path.read_text(encoding="utf-8"))["projects"]


@pytest.fixture
def recent_json(tmp_path, monkeypatch):
    p = tmp_path / "user_projects.json"
    monkeypatch.setattr(
        "app.services.project_service.default_user_projects_json_path", lambda: str(p)
    )
    _seed_projects(p, [])
    return p


# ── 内容盘点（删除确认要用） ───────────────────────────────────────────────────

def test_describe_contents_counts_tiffs_and_bytes(tmp_path):
    ws = _mk_workspace(tmp_path, "断面A", tiffs=3, jpgs=2)

    info = ops.describe_contents(str(ws))

    assert info["tiff_count"] == 3
    assert info["jpg_count"] == 2
    assert info["workspace_count"] == 1
    assert info["total_bytes"] >= 3 * 1024 + 2 * 512


def test_describe_contents_walks_nested_projects(tmp_path):
    proj = tmp_path / "北方多样性调查"
    sub = proj / "江苏盐城-2026"
    sub.mkdir(parents=True)
    _mk_workspace(sub, "断面A", tiffs=2)
    _mk_workspace(sub, "断面B", tiffs=1)

    info = ops.describe_contents(str(proj))

    assert info["workspace_count"] == 2
    assert info["tiff_count"] == 3


def test_has_masters_flags_tiff_presence(tmp_path):
    empty = tmp_path / "空目录"
    empty.mkdir()
    withtiff = _mk_workspace(tmp_path, "有母图", tiffs=1)

    assert ops.describe_contents(str(empty))["tiff_count"] == 0
    assert ops.describe_contents(str(withtiff))["tiff_count"] == 1


# ── 删除：默认回收站 ─────────────────────────────────────────────────────────

def test_delete_defaults_to_trash_not_permanent(tmp_path, monkeypatch, recent_json):
    ws = _mk_workspace(tmp_path, "断面A", tiffs=1)
    _seed_projects(recent_json, [{"name": "断面A", "directory": str(ws)}])

    trashed: list[str] = []
    monkeypatch.setattr(ops, "_send_to_trash", lambda p: trashed.append(str(p)))

    ops.delete_node(str(ws))

    assert trashed == [str(ws)], "删除必须默认走回收站"
    # 回收站是外部动作(这里被 patch 掉了) —— 关键是没有 rmtree 掉
    assert ws.exists(), "默认删除不得直接抹除磁盘内容"
    assert _projects(recent_json) == [], "记录里的条目要清掉"


def test_delete_permanent_actually_removes(tmp_path, recent_json):
    ws = _mk_workspace(tmp_path, "断面A", tiffs=1)
    _seed_projects(recent_json, [{"name": "断面A", "directory": str(ws)}])

    ops.delete_node(str(ws), permanent=True)

    assert not ws.exists()
    assert _projects(recent_json) == []


def test_delete_refuses_outside_paths(tmp_path):
    with pytest.raises(ValueError):
        ops.delete_node("")
    with pytest.raises(FileNotFoundError):
        ops.delete_node(str(tmp_path / "不存在"))


# ── 重命名 ──────────────────────────────────────────────────────────────────

def test_rename_moves_folder_and_updates_records(tmp_path, recent_json):
    ws = _mk_workspace(tmp_path, "断面A", tiffs=1)
    _seed_projects(recent_json, [{"name": "断面A", "directory": str(ws)}])

    new_path = ops.rename_node(str(ws), "断面A-重测")

    assert not ws.exists()
    assert Path(new_path).is_dir()
    assert (Path(new_path) / "_data" / "project.db").exists(), "数据库跟着走"
    entries = _projects(recent_json)
    assert entries[0]["directory"] == str(new_path), "记录路径必须同步"
    assert entries[0]["name"] == "断面A-重测"


def test_rename_rejects_bad_names(tmp_path, recent_json):
    ws = _mk_workspace(tmp_path, "断面A")
    for bad in ("", "  ", "a/b", "a\\b", ".."):
        with pytest.raises(ValueError):
            ops.rename_node(str(ws), bad)


def test_rename_rejects_existing_sibling(tmp_path, recent_json):
    _mk_workspace(tmp_path, "断面A")
    ws = _mk_workspace(tmp_path, "断面B")
    with pytest.raises(FileExistsError):
        ops.rename_node(str(ws), "断面A")


# ── 移动：散工作区归入项目 ───────────────────────────────────────────────────

def test_move_into_project_moves_folder_and_db(tmp_path, recent_json):
    proj = tmp_path / "北方多样性调查"
    proj.mkdir()
    ws = _mk_workspace(tmp_path, "FJ-SHUTDOWN", tiffs=2)
    _seed_projects(recent_json, [{"name": "FJ-SHUTDOWN", "directory": str(ws)}])

    dest = ops.move_node(str(ws), str(proj))

    assert not ws.exists()
    assert Path(dest) == proj / "FJ-SHUTDOWN"
    assert (Path(dest) / "_data" / "project.db").exists(), "库跟着文件夹走，零迁移"
    assert len(list((Path(dest) / "results").glob("*.tif"))) == 2, "TIFF 母图一张不能少"
    assert _projects(recent_json)[0]["directory"] == str(dest), "记录路径必须同步"


def test_move_refuses_into_own_subtree(tmp_path, recent_json):
    proj = tmp_path / "项目"
    sub = proj / "子目录"
    sub.mkdir(parents=True)
    with pytest.raises(ValueError):
        ops.move_node(str(proj), str(sub))  # 把父目录搬进自己的子目录 = 自吞


def test_move_refuses_when_target_name_taken(tmp_path, recent_json):
    proj = tmp_path / "项目"
    (proj / "断面A").mkdir(parents=True)
    ws = _mk_workspace(tmp_path, "断面A")
    with pytest.raises(FileExistsError):
        ops.move_node(str(ws), str(proj))


def test_preview_move_reports_inherited_and_kept_fields(tmp_path, recent_json):
    """移动前预览：空字段会继承新父项目，已填字段保留 —— 不静默覆盖。"""
    proj = tmp_path / "项目"
    proj.mkdir()
    ws = _mk_workspace(tmp_path, "FJ-SHUTDOWN", tiffs=1)

    preview = ops.preview_move(
        str(ws), str(proj),
        parent_meta={"region": "江苏·盐城", "leader": "杨德元", "device": "E-M1"},
        node_meta={"device": "GFX100"},  # 已填过 → 保留
    )

    assert preview["target_path"] == str(proj / "FJ-SHUTDOWN")
    assert preview["contents"]["tiff_count"] == 1
    assert preview["inherit"] == {"region": "江苏·盐城", "leader": "杨德元"}
    assert preview["keep"] == {"device": "GFX100"}


# ── 非空提醒：有图的项目不许静默删 ────────────────────────────────────────────

def test_confirm_level_escalates_with_content(tmp_path):
    """删除确认的强度由内容决定 —— 有图片数据必须明确拦一道。

    用户 2026-07-13: "如果是有数据，比如图片的项目，应该提醒用户，有图片数据，
    非空项目，是否确定"。

    级别：
      * "simple"  —— 空目录，普通确认即可；
      * "confirm" —— 有 JPG / 有标本记录，弹明确的「非空」确认；
      * "typed"   —— 有 TIFF 母图（无价、不可再生）→ 必须手打目录名才能删。
    """
    empty = tmp_path / "空目录"
    empty.mkdir()
    assert ops.confirm_level(str(empty)) == "simple"

    with_jpg = _mk_workspace(tmp_path, "只有原片", jpgs=5)
    assert ops.confirm_level(str(with_jpg)) == "confirm"

    with_tiff = _mk_workspace(tmp_path, "有母图", tiffs=1, jpgs=3)
    assert ops.confirm_level(str(with_tiff)) == "typed"


def test_summarize_for_confirm_is_human_readable(tmp_path):
    """给确认框用的一句话，必须说清「有多少图、多少母图、占多少空间」。"""
    ws = _mk_workspace(tmp_path, "断面A", tiffs=2, jpgs=4)

    text = ops.summarize_for_confirm(str(ws))

    assert "2" in text and "TIFF" in text.upper() or "母图" in text
    assert "4" in text
    assert "断面A" in text
