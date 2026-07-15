"""一步建好整个项目 —— 项目(调查区域) + 若干采样点工作区。

用户 2026-07-12 报障:
    "我开展一个大项目, 比如江苏盐城2026, 在这个区域设置了 2 个点: 日出海湾、月亮湾。
     这个软件在创建工作区时, 无法自动创建项目目录, 然后内部还有 2 个子目录, 我可以
     切换进去。"

现状(实测):
    · 「新建调查区域」调 seed_region_settings -> ensure_project_dirs(create_root=True)
      -> **区域根目录自己被塞进 incoming-jpg/ results/ _data/**, 变成一个拍照工作区,
      照片会堆在项目根上。
    · 「新建子文件夹」只 mkdir 一个空壳, is_workspace()=False, 不能直接进去拍。
    · 没有"一次建好项目 + N 个点"的入口, 要手动点 4~5 次。

要的结构:
    江苏盐城2026/            <- 项目容器(区域): 只放共享设置, **不放照片**
      _data/project.db      <- 设置锚点(地区/人员), 子点继承
      _data/region.json     <- 区域标记: 告诉项目树"这不是拍照工作区"
      日出海湾/              <- 真工作区: incoming-jpg/ results/ _data/project.db
      月亮湾/                <- 真工作区
(Fable 5, 2026-07-12)
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.services import project_tree_service as pts
from app.services.project_scaffold_service import (
    append_survey_structure,
    create_survey_project,
)


def test_creates_region_container_and_site_workspaces(tmp_path):
    res = create_survey_project(
        str(tmp_path),
        name="江苏盐城2026",
        sites=["日出海湾", "月亮湾"],
        meta={"location": "江苏盐城", "year": "2026"},
        collector="张三",
    )

    root = Path(res["root"])
    assert root.name == "江苏盐城2026" and root.is_dir()

    # 区域是**容器**, 不是拍照工作区: 根目录不许出现 incoming-jpg / results
    assert not (root / "incoming-jpg").exists(), "项目根目录不该堆照片"
    assert not (root / "results").exists(), "项目根目录不该有成果区"
    assert (root / "_data" / "project.db").is_file(), "设置锚点(供子点继承)要有"
    assert (root / "_data" / "region.json").is_file(), "区域标记要有"
    assert pts.is_region(str(root)) is True
    assert pts.is_workspace(str(root)) is False, "区域不能被当成工作区进去拍照"

    # 两个点都是**真工作区**, 建好即可进入拍照
    for site in ("日出海湾", "月亮湾"):
        ws = root / site
        assert ws.is_dir()
        assert (ws / "incoming-jpg").is_dir()
        assert (ws / "results").is_dir()
        assert (ws / "_data" / "project.db").is_file()
        assert pts.is_workspace(str(ws)) is True, f"{site} 建完就该能直接进去拍"

    assert [Path(p).name for p in res["sites"]] == ["日出海湾", "月亮湾"]


def test_region_settings_are_inherited_by_sites(tmp_path):
    """在项目层填一次「地区 / 采集人」, 每个点自动继承 —— 不用每个点重填。"""
    from app.services import project_settings_service as pss

    res = create_survey_project(
        str(tmp_path),
        name="江苏盐城2026",
        sites=["日出海湾"],
        meta={"location": "江苏盐城", "year": "2026"},
        collector="张三",
        province="JSYC",
    )
    site = res["sites"][0]

    eff = pss.get_effective(site, "code_labels", {}, root=res["root"])
    assert eff.get("province") == "JSYC", "点应继承项目层的地区代码"


def test_sites_are_registered_so_tree_can_switch_between_them(tmp_path):
    """两个点都登记进项目目录 —— 项目树里能看到、能来回切。"""
    from app.services import project_catalog_service as pcs

    res = create_survey_project(
        str(tmp_path), name="江苏盐城2026", sites=["日出海湾", "月亮湾"]
    )
    listed = pcs.list_registered_workspaces(res["root"])
    assert sorted(w["name"] for w in listed) == ["日出海湾", "月亮湾"]
    # 相对路径存的是相对项目根 -> 整个项目文件夹搬走也不断
    assert sorted(w["relative_path"] for w in listed) == ["日出海湾", "月亮湾"]


def test_duplicate_and_bad_names_are_rejected(tmp_path):
    with pytest.raises(ValueError):
        create_survey_project(str(tmp_path), name="", sites=["A"])
    with pytest.raises(ValueError):
        create_survey_project(str(tmp_path), name="X", sites=["../逃逸"])
    with pytest.raises(ValueError):
        create_survey_project(str(tmp_path), name="X", sites=["A", "A"])


def test_existing_project_is_not_clobbered(tmp_path):
    """同名项目已存在 -> 不许覆盖(项目里可能已经有照片)。"""
    (tmp_path / "江苏盐城2026" / "日出海湾" / "results").mkdir(parents=True)
    (tmp_path / "江苏盐城2026" / "日出海湾" / "results" / "old.tif").write_bytes(b"II*\x00")

    with pytest.raises(FileExistsError):
        create_survey_project(str(tmp_path), name="江苏盐城2026", sites=["日出海湾"])

    assert (tmp_path / "江苏盐城2026" / "日出海湾" / "results" / "old.tif").is_file()


def test_empty_project_creates_container_only(tmp_path):
    """sites=[] → 只建项目根容器；照片目录一个都不许有（红线）。

    需求(用户 2026-07-12): "只建立一个项目目录, 后续点击这个目录, 也可以建立子目录"
    —— 新建项目不再一次问完采样点, 断面/采样点之后在项目树里用「+ 新建子目录」自由加。
    见 docs/specs/2026-07-12-slim-new-project-and-settings-inheritance.md
    """
    res = create_survey_project(str(tmp_path), name="江苏盐城2026", sites=[])
    root = Path(res["root"])

    assert root.is_dir()
    assert (root / "_data" / "region.json").is_file()   # 「这是容器」标记
    assert (root / "_data" / "project.db").is_file()    # 设置锚点
    assert res["sites"] == []
    # 红线: 项目根是容器, 不是拍照工作区 —— 照片不得堆在项目根
    assert not (root / "incoming-jpg").exists()
    assert not (root / "results").exists()


def test_creates_nested_hierarchy_and_only_marks_selected_leaves_as_workspaces(tmp_path):
    structure = [
        {
            "name": "北海区域",
            "type": "区域",
            "is_workspace": False,
            "children": [
                {"name": "断面A", "type": "断面", "is_workspace": True, "children": []},
                {"name": "断面B", "type": "断面", "is_workspace": True, "children": []},
            ],
        },
        {
            "name": "钦州区域",
            "type": "区域",
            "is_workspace": False,
            "children": [
                {"name": "断面C", "type": "断面", "is_workspace": True, "children": []},
            ],
        },
    ]

    res = create_survey_project(
        str(tmp_path), name="广西调查2026", structure=structure
    )
    root = Path(res["root"])

    assert (root / "北海区域").is_dir()
    assert not pts.is_workspace(str(root / "北海区域"))
    assert pts.is_workspace(str(root / "北海区域" / "断面A"))
    assert pts.is_workspace(str(root / "北海区域" / "断面B"))
    assert pts.is_workspace(str(root / "钦州区域" / "断面C"))
    assert len(res["workspaces"]) == 3
    saved = json.loads((root / "_data" / "hierarchy.json").read_text(encoding="utf-8"))
    assert saved["nodes"] == structure


def test_workspace_node_cannot_contain_project_children(tmp_path):
    with pytest.raises(ValueError, match="工作区必须是末级节点"):
        create_survey_project(
            str(tmp_path),
            name="错误项目",
            structure=[{
                "name": "断面A",
                "type": "断面",
                "is_workspace": True,
                "children": [{
                    "name": "子层级",
                    "type": "站点",
                    "is_workspace": True,
                    "children": [],
                }],
            }],
        )


def test_nested_duplicate_names_are_checked_per_parent(tmp_path):
    with pytest.raises(ValueError, match="名称重复"):
        create_survey_project(
            str(tmp_path),
            name="重复项目",
            structure=[
                {"name": "断面A", "type": "断面", "is_workspace": True, "children": []},
                {"name": "断面A", "type": "断面", "is_workspace": True, "children": []},
            ],
        )


def test_append_hierarchy_below_existing_project_subdirectory(tmp_path):
    created = create_survey_project(
        str(tmp_path),
        name="广西调查2026",
        structure=[{
            "name": "北海区域",
            "type": "区域",
            "is_workspace": False,
            "children": [],
        }],
    )
    root = Path(created["root"])
    target = root / "北海区域"

    result = append_survey_structure(
        str(target),
        project_root=str(root),
        structure=[{
            "name": "断面A",
            "type": "断面",
            "is_workspace": True,
            "children": [],
        }],
    )

    workspace = target / "断面A"
    assert result["target"] == str(target)
    assert result["workspaces"] == [str(workspace)]
    assert pts.is_workspace(str(workspace))
    saved = json.loads((root / "_data" / "hierarchy.json").read_text(encoding="utf-8"))
    assert saved["appends"][-1]["parent"] == "北海区域"
    assert saved["appends"][-1]["nodes"][0]["name"] == "断面A"


def test_append_conflict_is_checked_before_any_folder_is_created(tmp_path):
    created = create_survey_project(str(tmp_path), name="项目A", structure=[])
    root = Path(created["root"])
    (root / "已存在").mkdir()

    with pytest.raises(FileExistsError, match="已存在同名目录"):
        append_survey_structure(
            str(root),
            structure=[
                {"name": "新区域", "type": "区域", "is_workspace": False, "children": []},
                {"name": "已存在", "type": "区域", "is_workspace": False, "children": []},
            ],
        )

    assert not (root / "新区域").exists()


def test_create_conflict_is_checked_before_any_folder_is_created(tmp_path):
    """create_survey_project 也要整树预检撞名 —— 否则撞名会留下半成品项目。

    与 append_survey_structure 的 test_append_conflict_is_checked_before_any_folder_is_created
    对应。这里用一个能越过「项目根非空」守卫、却在 create_nodes 途中才撞上的冲突：
    顶层节点名 "_data" 会与 create_survey_project 自己先建的 root/_data 撞名。旧代码里
    create_nodes 会先把 "新区域" 建好、登记好, 再撞 "_data" 崩掉 —— 半成品留盘且无法重试。
    """
    with pytest.raises(FileExistsError, match="已存在同名目录"):
        create_survey_project(
            str(tmp_path),
            name="项目A",
            structure=[
                {"name": "新区域", "type": "区域", "is_workspace": False, "children": []},
                {"name": "_data", "type": "区域", "is_workspace": False, "children": []},
            ],
        )

    # 撞名应在 create_nodes 之前抛出 —— 不许留下任何本次要建的节点目录
    assert not (tmp_path / "项目A" / "新区域").exists()
    # Claude Code 修改 2026-07-14 — 补上 codex 复现的漏检: 失败必须整体回滚项目根,
    # 否则 root/_data 残留会被「同名非空不覆盖」守卫永久拦死同名重试(独立验证脚本实测复现)。
    assert not (tmp_path / "项目A").exists(), "失败后必须回滚项目根，否则同名重试会被永久拦住"

    # 回滚后，同名重建（这次给一个不冲突的合法结构）必须能成功，而不是被
    # 「同名项目已存在且非空，不覆盖」卡死。
    retried = create_survey_project(
        str(tmp_path),
        name="项目A",
        structure=[
            {"name": "新区域", "type": "区域", "is_workspace": False, "children": []},
        ],
    )
    assert Path(retried["root"]) == tmp_path / "项目A"


# Claude Code 修改 2026-07-15 — codex 回归实测复现的两个回滚缺陷(都是上一轮那个
# 无条件 rmtree(root) 引入的):
#   (a) root 可能是**用户自己预先建好的空目录**(在资源管理器里先建好再来新建项目),
#       失败时无条件 rmtree 会把用户的目录一起删掉 —— 删用户数据, 最严重。
#       回滚只能删「本次调用真正创建的东西」, 不能删调用前就存在的东西。
#   (b) 一旦有工作区已经 register_workspace 成功, 根库连接被 open_project_db 缓存
#       持有(Windows 上就是文件锁), rmtree(ignore_errors=True) 删不干净, 会静默
#       留下 _data/project.db + -wal + -shm 残骸 -> 同名重试又被"非空"守卫拦死。
#       回滚前必须先释放这些缓存连接。
def test_rollback_never_deletes_a_preexisting_root_dir(tmp_path):
    """用户预先建好的空目录, 建项目失败后必须原样还在 —— 绝不能被回滚删掉。"""
    preexisting = tmp_path / "项目A"
    preexisting.mkdir()

    with pytest.raises(FileExistsError):
        create_survey_project(
            str(tmp_path),
            name="项目A",
            structure=[
                {"name": "新区域", "type": "区域", "is_workspace": False, "children": []},
                {"name": "_data", "type": "区域", "is_workspace": False, "children": []},
            ],
        )

    assert preexisting.exists(), "回滚绝不能删掉调用前就存在的用户目录"
    assert preexisting.is_dir()
    # 但本次调用在里面创建的东西必须清干净(否则同名重试仍被"非空"守卫拦死)。
    leftovers = sorted(p.name for p in preexisting.iterdir())
    assert leftovers == [], f"本次创建的内容必须回滚干净, 残留: {leftovers}"


def test_rollback_releases_db_locks_and_leaves_nothing_behind(tmp_path, monkeypatch):
    """已经建好并登记了工作区之后才失败 -> 根库缓存连接必须先释放, 回滚删干净。

    否则 Windows 上根库文件被锁, rmtree(ignore_errors=True) 静默失败, 留下
    _data/project.db(+wal/shm), 同名重试被「已存在且非空」永久拦死。
    """
    real_ensure = pss_ensure_project_dirs_ref()
    state = {"armed": True, "n": 0}

    def _boom_on_second_workspace(path, *a, **k):
        if state["armed"]:
            state["n"] += 1
            if state["n"] >= 2:
                raise OSError("模拟第二个工作区初始化失败(磁盘满/权限)")
        return real_ensure(path, *a, **k)

    monkeypatch.setattr(
        "app.services.project_service.ensure_project_dirs", _boom_on_second_workspace
    )

    with pytest.raises(OSError):
        create_survey_project(
            str(tmp_path),
            name="项目B",
            structure=[
                {"name": "断面一", "type": "断面", "is_workspace": True, "children": []},
                {"name": "断面二", "type": "断面", "is_workspace": True, "children": []},
            ],
        )
    state["armed"] = False  # 重试不再注入故障 —— 只验证回滚是否真的干净

    root = tmp_path / "项目B"
    assert not root.exists(), (
        "根库连接必须先释放再回滚, 否则会留下锁住的 project.db/wal/shm 残骸: "
        f"{sorted(p.name for p in (root / '_data').iterdir()) if (root / '_data').exists() else '(root gone)'}"
    )

    # 回滚干净 -> 同名重建必须能成功。
    retried = create_survey_project(
        str(tmp_path),
        name="项目B",
        structure=[
            {"name": "断面一", "type": "断面", "is_workspace": True, "children": []},
        ],
    )
    assert Path(retried["root"]) == root


def pss_ensure_project_dirs_ref():
    from app.services.project_service import ensure_project_dirs
    return ensure_project_dirs


# Claude Code 修改 2026-07-15 — codex 回归指出 append_survey_structure 只做了撞名
# 预检, 没有运行时回滚: 权限/磁盘/DB 初始化这类失败发生在第二个工作区时, 第一个
# 已经建好的目录会留在旧项目里, 用户看到"失败"却发现目录多了一半。这跟
# create_survey_project 是同一个坑, 只是那边修了、这边漏了。
def test_append_rolls_back_created_dirs_on_runtime_failure(tmp_path, monkeypatch):
    """第二个工作区初始化失败 -> 第一个已建好的目录也必须回滚, 旧项目回到原样。"""
    created = create_survey_project(str(tmp_path), name="旧项目", structure=[])
    root = Path(created["root"])
    before = sorted(p.name for p in root.iterdir())

    real_ensure = pss_ensure_project_dirs_ref()
    state = {"n": 0}

    def _boom_on_second(path, *a, **k):
        state["n"] += 1
        if state["n"] >= 2:
            raise OSError("模拟第二个工作区初始化失败(磁盘满/权限)")
        return real_ensure(path, *a, **k)

    monkeypatch.setattr(
        "app.services.project_service.ensure_project_dirs", _boom_on_second
    )

    with pytest.raises(OSError):
        append_survey_structure(
            str(root),
            structure=[
                {"name": "one", "type": "断面", "is_workspace": True, "children": []},
                {"name": "two", "type": "断面", "is_workspace": True, "children": []},
            ],
        )

    assert not (root / "one").exists(), "失败必须回滚, 不能把第一个已建好的目录留下"
    assert not (root / "two").exists()
    after = sorted(p.name for p in root.iterdir())
    assert after == before, f"旧项目必须回到原样, 之前={before} 之后={after}"


def test_cannot_append_inside_a_photo_workspace(tmp_path):
    created = create_survey_project(str(tmp_path), name="项目A", sites=["断面A"])
    workspace = created["workspaces"][0]

    with pytest.raises(ValueError, match="不能在其内部追加"):
        append_survey_structure(
            workspace,
            structure=[{
                "name": "错误下级",
                "type": "站点",
                "is_workspace": True,
                "children": [],
            }],
        )
