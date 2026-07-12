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
from app.services.project_scaffold_service import create_survey_project


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
