"""project_scaffold_service.py — 一步建好整个项目: 调查区域 + 若干采样点工作区。

用户 2026-07-12 报障:
    "我开展一个大项目, 比如江苏盐城2026, 在这个区域设置了 2 个点: 日出海湾、月亮湾。
     这个软件在创建工作区, 就无法自动创建项目目录, 然后内部还有 2 个子目录,
     我可以切换进去。"

旧流程的三个毛病(实测):
  ① 「新建调查区域」-> seed_region_settings -> ensure_project_dirs(create_root=True)
     -> **区域根目录自己被塞进 incoming-jpg/ results/**, 项目根变成拍照工作区,
     照片会堆在项目根上, 概念全乱。
  ② 「新建子文件夹」只 mkdir 一个空壳(is_workspace()=False), 不能直接进去拍,
     要"进入"时才现场初始化。
  ③ 没有"一次建好项目 + N 个点"的入口, 用户要点 4~5 次才凑出想要的结构。

本服务建出来的结构:
    江苏盐城2026/                <- 项目(调查区域) = **容器**, 不放照片
      _data/project.db          <- 设置锚点: 地区/人员等, 采样点自动继承
      _data/region.json         <- 区域标记, 告诉项目树"这不是拍照工作区"
      日出海湾/                  <- 真工作区(建完即可进入拍照)
        incoming-jpg/  results/  _data/project.db
      月亮湾/                    <- 真工作区
        incoming-jpg/  results/  _data/project.db

(Fable 5, 2026-07-12)
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

REGION_MARKER = "region.json"
_BAD_NAME_BITS = ("/", "\\", "..")


def _validate_name(name: str, what: str) -> str:
    text = str(name or "").strip()
    if not text:
        raise ValueError(f"{what}不能为空")
    if any(bit in text for bit in _BAD_NAME_BITS):
        raise ValueError(f"{what}不合法（不能包含 / \\ ..）：{text}")
    return text


def write_region_marker(region_dir: str, meta: Optional[dict] = None) -> str:
    """在 ``_data/region.json`` 打一个「这是项目容器, 不是拍照工作区」的标记。

    用文件标记而不是查数据库: 项目树扫描是**纯路径操作**, 每个节点都开一次 DB
    会把扫描拖垮(而且扫描不该持有子库的文件锁 —— Windows 上会锁住文件夹)。
    """
    data_dir = Path(region_dir) / "_data"
    data_dir.mkdir(parents=True, exist_ok=True)
    marker = data_dir / REGION_MARKER
    payload = {"kind": "region", **(meta or {})}
    marker.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return str(marker)


def create_survey_project(
    parent_dir: str,
    *,
    name: str,
    sites: list[str],
    meta: Optional[dict] = None,
    collector: str = "",
    photographer: str = "",
    identifier: str = "",
    province: str = "",
) -> dict:
    """建项目根 + 每个采样点一个真工作区。返回 {"root": ..., "sites": [...]}。

    · 项目根**不建** incoming-jpg/results —— 它是容器, 不是拍照点。
    · 项目根建 ``_data/project.db`` 只用来存共享设置(地区/人员), 采样点通过
      project_settings_service.get_effective 自动继承 —— 设一次, 每个点不用重填。
    · 每个采样点建成**完整工作区**: incoming-jpg/ results/ _data/project.db,
      并登记进项目目录(project_catalog_service), 项目树里就能来回切。
    · 同名项目已存在 -> 抛 FileExistsError, **绝不覆盖**(里面可能已经有照片)。
    """
    from app.db.db_manager import open_project_db_private
    from app.services import project_catalog_service as pcs
    from app.services import project_settings_service as pss
    from app.services.project_paths import normalize_path
    from app.services.project_service import ensure_project_dirs

    proj_name = _validate_name(name, "项目名称")
    site_names = [_validate_name(s, "采样点名称") for s in (sites or [])]
    if len(set(site_names)) != len(site_names):
        raise ValueError("采样点名称重复")

    parent = Path(normalize_path(parent_dir))
    if not parent.is_dir():
        raise FileNotFoundError(f"上级目录不存在：{parent}")

    root = parent / proj_name
    if root.exists() and any(root.iterdir()):
        raise FileExistsError(f"同名项目已存在且非空，不覆盖：{root}")

    # ── 项目根: 容器 + 设置锚点(没有 incoming-jpg / results) ──────────────────
    root.mkdir(parents=True, exist_ok=True)
    (root / "_data").mkdir(parents=True, exist_ok=True)
    region_meta = dict(meta or {})
    region_meta["name"] = proj_name
    write_region_marker(str(root), region_meta)

    db = open_project_db_private(str(root), create=True)
    try:
        code_labels = pss.load_setting(db, "code_labels", pss.DEFAULT_CODE_LABELS)
        if province:
            code_labels["province"] = province
        pss.save_setting(db, "code_labels", code_labels)

        personnel = pss.load_setting(db, "personnel", {})
        for key, value in (
            ("collector", collector),
            ("photographer", photographer),
            ("identifier", identifier),
        ):
            if value:
                personnel[key] = value
        if personnel:
            pss.save_setting(db, "personnel", personnel)

        if region_meta:
            pss.save_setting(db, "project_meta", region_meta)
    finally:
        db.close()   # 跨工作区写完立刻放锁(Windows: 不放锁 -> 文件夹删不掉/移不动)

    # ── 每个采样点: 完整工作区, 建完就能进去拍 ────────────────────────────────
    created: list[str] = []
    for site in site_names:
        ws = root / site
        ws.mkdir(parents=True, exist_ok=True)
        ensure_project_dirs(str(ws), create_root=True)   # incoming-jpg/ results/ _data/
        site_db = open_project_db_private(str(ws), create=True)
        try:
            site_db.commit()      # ensure_schema 已在 open 时跑过, 这里只是落盘
        finally:
            site_db.close()
        try:
            pcs.register_workspace(
                str(root), str(ws), name=site,
                project_meta={"name": proj_name, **(meta or {})},
            )
        except Exception:  # noqa: BLE001 —— 登记失败不该让整个项目建不出来
            logger.exception("采样点登记进项目目录失败: %s", ws)
        created.append(str(ws))

    logger.info("新建项目 %s, 采样点: %s", root, site_names)
    return {"root": str(root), "sites": created}
