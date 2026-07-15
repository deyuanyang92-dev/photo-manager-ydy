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
import shutil
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

REGION_MARKER = "region.json"
HIERARCHY_MARKER = "hierarchy.json"
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


def _normalize_structure(nodes: Optional[list[dict]]) -> list[dict]:
    """Validate and copy a user-defined hierarchy without touching the disk."""
    normalized: list[dict] = []
    sibling_names: set[str] = set()
    for raw in nodes or []:
        if not isinstance(raw, dict):
            raise ValueError("项目层级格式不正确")
        name = _validate_name(raw.get("name", ""), "层级名称")
        folded = name.casefold()
        if folded in sibling_names:
            raise ValueError(f"同一层级下名称重复：{name}")
        sibling_names.add(folded)
        node_type = str(raw.get("type") or "自定义层级").strip() or "自定义层级"
        children = _normalize_structure(raw.get("children") or [])
        is_workspace = bool(raw.get("is_workspace"))
        if is_workspace and children:
            raise ValueError(f"工作区必须是末级节点，不能再包含下级目录：{name}")
        normalized.append({
            "name": name,
            "type": node_type,
            "is_workspace": is_workspace,
            "children": children,
        })
    return normalized


def find_project_root(path: str | Path) -> Optional[str]:
    """Return the nearest marked project root containing ``path``."""
    current = Path(path).resolve()
    for candidate in (current, *current.parents):
        if (candidate / "_data" / REGION_MARKER).is_file():
            return str(candidate)
    return None


def append_survey_structure(
    target_dir: str,
    *,
    structure: list[dict],
    project_root: Optional[str] = None,
) -> dict:
    """Append a validated hierarchy below an existing project container.

    The selected target may be a project root or any ordinary container below
    it. A photo workspace remains a leaf and therefore cannot receive children.
    Existing folders are never merged or overwritten.
    """
    from app.db.db_manager import open_project_db_private
    from app.services import project_catalog_service as pcs
    from app.services import project_tree_service as pts
    from app.services.project_paths import normalize_path
    from app.services.project_service import ensure_project_dirs

    target = Path(normalize_path(target_dir))
    if not target.is_dir():
        raise FileNotFoundError(f"追加位置不存在：{target}")
    if pts.is_workspace(str(target)):
        raise ValueError("拍摄工作区必须是末级节点，不能在其内部追加层级")

    hierarchy = _normalize_structure(structure)
    if not hierarchy:
        raise ValueError("请至少添加一个层级")

    explicit_root = Path(normalize_path(project_root)) if project_root else None
    discovered_root = find_project_root(target)
    root = explicit_root or (Path(discovered_root) if discovered_root else target)
    try:
        target.resolve().relative_to(root.resolve())
    except ValueError as exc:
        raise ValueError("追加位置不属于指定项目") from exc

    # Preflight the complete plan before touching disk. A conflict anywhere in
    # the hierarchy aborts the whole append instead of leaving a partial tree.
    def check_conflicts(parent: Path, nodes: list[dict]) -> None:
        for node in nodes:
            node_dir = parent / node["name"]
            if node_dir.exists():
                raise FileExistsError(f"目标位置已存在同名目录：{node_dir}")
            check_conflicts(node_dir, node["children"])

    check_conflicts(target, hierarchy)

    created: list[str] = []
    # Claude Code 修改 2026-07-15 — codex 回归指出这里只做了撞名预检, 没有运行时
    # 回滚: 权限/磁盘满/DB 初始化失败发生在第二个节点时, 前面已经建好的节点会留在
    # 旧项目里 —— 用户看到"失败"却发现项目里凭空多了一半目录, 且再试一次会被撞名
    # 预检拦住。这跟 create_survey_project 是同一个坑(那边已修, 这边漏了)。
    # 记下本次在 target 下真正创建的顶层目录 + 建成的工作区, 失败时按 create 那边
    # 同样的方式回滚(先放 DB 连接再删, 否则 Windows 上删不干净)。
    created_top_dirs: list[Path] = []
    created_workspace_dirs: list[str] = []

    def _rollback_append() -> None:
        from app.db.db_manager import close_project_db

        for ws_dir in created_workspace_dirs:
            try:
                close_project_db(ws_dir)
            except Exception:  # noqa: BLE001
                logger.exception("回滚时释放数据库连接失败: %s", ws_dir)
        try:
            close_project_db(str(root))
        except Exception:  # noqa: BLE001
            logger.exception("回滚时释放根库连接失败: %s", root)
        for node_dir in created_top_dirs:
            shutil.rmtree(node_dir, ignore_errors=True)

    def create_nodes(parent: Path, nodes: list[dict]) -> None:
        for node in nodes:
            node_dir = parent / node["name"]
            node_dir.mkdir(parents=True, exist_ok=False)
            if parent == target:
                created_top_dirs.append(node_dir)
            if node["is_workspace"]:
                ensure_project_dirs(str(node_dir), create_root=True)
                workspace_db = open_project_db_private(str(node_dir), create=True)
                try:
                    workspace_db.commit()
                finally:
                    workspace_db.close()
                created_workspace_dirs.append(str(node_dir))
                try:
                    pcs.register_workspace(
                        str(root),
                        str(node_dir),
                        name=node["name"],
                        project_meta={"name": root.name},
                    )
                except Exception:  # noqa: BLE001
                    logger.exception("工作区登记进项目目录失败: %s", node_dir)
                created.append(str(node_dir))
            else:
                create_nodes(node_dir, node["children"])

    try:
        create_nodes(target, hierarchy)
    except Exception:
        _rollback_append()
        raise

    marker = root / "_data" / HIERARCHY_MARKER
    if marker.is_file():
        try:
            payload = json.loads(marker.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            payload = {"version": 1, "project": root.name, "nodes": []}
        try:
            parent_rel = str(target.resolve().relative_to(root.resolve()))
        except ValueError:
            parent_rel = "."
        payload.setdefault("appends", []).append({
            "parent": "." if parent_rel == "." else parent_rel.replace("\\", "/"),
            "nodes": hierarchy,
        })
        marker.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    logger.info("追加项目层级到 %s, 工作区: %s", target, created)
    return {
        "root": str(root),
        "target": str(target),
        "workspaces": created,
        "structure": hierarchy,
    }


def create_survey_project(
    parent_dir: str,
    *,
    name: str,
    sites: Optional[list[str]] = None,
    structure: Optional[list[dict]] = None,
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
    if structure is not None and site_names:
        raise ValueError("不能同时使用旧采样点列表和新项目层级")
    hierarchy = _normalize_structure(structure)
    if structure is None:
        hierarchy = [
            {"name": site, "type": "断面", "is_workspace": True, "children": []}
            for site in site_names
        ]

    parent = Path(normalize_path(parent_dir))
    if not parent.is_dir():
        raise FileNotFoundError(f"上级目录不存在：{parent}")

    root = parent / proj_name
    if root.exists() and any(root.iterdir()):
        raise FileExistsError(f"同名项目已存在且非空，不覆盖：{root}")

    # ── 项目根: 容器 + 设置锚点(没有 incoming-jpg / results) ──────────────────
    # Claude Code 修改 2026-07-14 — codex 回归复现: 上面那版"整树预检"只挡住了
    # 多层站点半成品, root/_data 本身仍在预检和 create_nodes 之前无条件建好;
    # 一旦后面任何一步失败(撞名/权限/磁盘满/DB异常), root/_data 都会留在盘上,
    # 而"同名项目已存在且非空,不覆盖"这道守卫会把它认成"已有项目",永久拦死同名重试。
    # 修法: 从这里开始的整段建库动作包一层 try/except, 任何失败都回滚。
    #
    # Claude Code 修改 2026-07-15 — codex 回归又实测复现上面那版回滚的两个缺陷:
    #   (a) 无条件 rmtree(root) 会把**用户自己预先建好的空目录**一起删掉(用户在
    #       资源管理器里先建好 "项目A" 再来新建同名项目 —— 上面的守卫允许空目录
    #       通过)。删用户数据, 最严重。回滚只能删「本次真正创建的东西」。
    #       关键: 守卫已保证 root 要么不存在、要么是空的, 所以 root **里面**的
    #       内容必定全是本次创建的 —— 只需区分 root 本身是不是我们建的。
    #   (b) 一旦有工作区 register_workspace 成功, 根库连接被 open_project_db
    #       缓存持有(Windows 上就是文件锁), rmtree(ignore_errors=True) 删不干净,
    #       静默留下 _data/project.db + -wal + -shm 残骸 -> 同名重试又被"非空"
    #       守卫拦死。回滚前必须先释放缓存连接(CLAUDE.md 记过这个历史坑)。
    root_existed_before = root.exists()
    created_workspace_dirs: list[str] = []

    def _rollback() -> None:
        # 先释放本次可能缓存住的 DB 连接(根库 + 已建好的工作区库), 否则 Windows
        # 上文件被锁, 下面的删除会静默失败并留下无法重试的半成品。
        from app.db.db_manager import close_project_db

        for ws_dir in [*created_workspace_dirs, str(root)]:
            try:
                close_project_db(ws_dir)
            except Exception:  # noqa: BLE001
                logger.exception("回滚时释放数据库连接失败: %s", ws_dir)
        if root_existed_before:
            # 用户预先建好的目录 —— 只清空我们放进去的内容, 绝不删目录本身。
            for child in root.iterdir():
                try:
                    if child.is_dir() and not child.is_symlink():
                        shutil.rmtree(child, ignore_errors=True)
                    else:
                        child.unlink(missing_ok=True)
                except OSError:
                    logger.exception("回滚时清理失败: %s", child)
        else:
            shutil.rmtree(root, ignore_errors=True)

    root.mkdir(parents=True, exist_ok=True)
    try:
        (root / "_data").mkdir(parents=True, exist_ok=True)
        region_meta = dict(meta or {})
        region_meta["name"] = proj_name
        write_region_marker(str(root), region_meta)
        (root / "_data" / HIERARCHY_MARKER).write_text(
            json.dumps(
                {"version": 1, "project": proj_name, "nodes": hierarchy},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

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

        # ── 用户定义层级：普通节点只是目录，勾选的末级节点才初始化为工作区 ──────
        created: list[str] = []

        def create_nodes(parent: Path, nodes: list[dict]) -> None:
            for node in nodes:
                node_dir = parent / node["name"]
                node_dir.mkdir(parents=True, exist_ok=False)
                if node["is_workspace"]:
                    ensure_project_dirs(str(node_dir), create_root=True)
                    workspace_db = open_project_db_private(str(node_dir), create=True)
                    try:
                        workspace_db.commit()
                    finally:
                        workspace_db.close()
                    # Claude Code 修改 2026-07-15 — 记下本次真正建成的工作区目录,
                    # 回滚时要先逐个释放它们可能缓存住的 DB 连接(Windows 文件锁)。
                    created_workspace_dirs.append(str(node_dir))
                    try:
                        pcs.register_workspace(
                            str(root), str(node_dir), name=node["name"],
                            project_meta={"name": proj_name, **(meta or {})},
                        )
                    except Exception:  # noqa: BLE001
                        logger.exception("工作区登记进项目目录失败: %s", node_dir)
                    created.append(str(node_dir))
                else:
                    create_nodes(node_dir, node["children"])

        # Claude Code 修改 2026-07-14 — 建目录前整树预检撞名: 避免撞名途中崩溃留下半成品项目
        #   (旧代码直接 create_nodes, 撞名会先建好前面的节点再崩, 且非空守卫挡住重试;
        #    镜像 append_survey_structure 的 check_conflicts 预检)
        def _preflight_conflicts(parent: Path, nodes: list[dict]) -> None:
            for node in nodes:
                node_dir = parent / node["name"]
                if node_dir.exists():
                    raise FileExistsError(f"目标位置已存在同名目录：{node_dir}")
                _preflight_conflicts(node_dir, node["children"])

        _preflight_conflicts(root, hierarchy)

        create_nodes(root, hierarchy)
    except Exception:
        # §7 旧: shutil.rmtree(root, ignore_errors=True)  —— 会误删用户预先建好的
        #        空目录, 且不释放 DB 连接导致 Windows 上删不干净(codex 实测复现)
        _rollback()
        raise
    logger.info("新建项目 %s, 工作区: %s", root, created)
    return {
        "root": str(root),
        "sites": created,
        "workspaces": created,
        "structure": hierarchy,
    }
