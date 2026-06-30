"""Global result ledger across known photo workspaces.

Each workspace owns its own ``_data/project.db``.  This service builds a
read-only ledger by combining authoritative DB links from ``grouping`` with a
filesystem scan of ``results/`` so the UI can show both registered results and
orphan files that still need review.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os
import sqlite3
import time
from typing import Iterable, Optional
from urllib.parse import quote

from app.services.project_service import RESULTS_DIR
from app.services.specimen_catalog_service import known_workspace_dirs

_LEDGER_CACHE_TTL_SECONDS = 2.0
_LEDGER_CACHE: dict[tuple, tuple[float, list["ResultLedgerRow"]]] = {}


@dataclass(frozen=True)
class ResultLedgerRow:
    project_dir: str
    project_name: str
    uid: str
    group_index: Optional[int]
    scientific_name: str = ""
    storage: str = ""
    collection_date: str = ""
    photo_date: str = ""
    tiff_path: str = ""
    zip_path: str = ""
    tiff_exists: bool = False
    zip_exists: bool = False
    registered: bool = False
    has_specimen: bool = False
    orphan: bool = False
    inferred_uid: str = ""
    status: str = ""

    @property
    def display_uid(self) -> str:
        return self.uid or self.inferred_uid


def collect_global_result_ledger(
    *,
    current_project_dir: Optional[str] = None,
    current_project_root: Optional[str] = None,
    workspace_dirs: Optional[Iterable[str]] = None,
    include_empty_specimens: bool = True,
    use_cache: bool = True,
) -> list[ResultLedgerRow]:
    """Return registered and orphan TIF/ZIP rows across known workspaces."""
    if workspace_dirs is not None and not current_project_dir and not current_project_root:
        dirs = _dedupe_dirs(workspace_dirs)
    else:
        dirs = known_workspace_dirs(
            current_project_dir=current_project_dir,
            current_project_root=current_project_root,
            extra_dirs=workspace_dirs,
        )
    cache_key = _ledger_cache_key(dirs, include_empty_specimens)
    if use_cache:
        cached = _ledger_cache_get(cache_key)
        if cached is not None:
            return cached
    rows: list[ResultLedgerRow] = []
    for project_dir in dirs:
        rows.extend(_collect_workspace(project_dir, include_empty_specimens))
    sorted_rows = sorted(
        rows,
        key=lambda r: (
            r.project_name.lower(),
            r.display_uid.lower(),
            -1 if r.group_index is None else r.group_index,
            Path(r.tiff_path or r.zip_path).name.lower(),
        ),
    )
    if use_cache:
        _LEDGER_CACHE[cache_key] = (time.monotonic(), list(sorted_rows))
    return sorted_rows


def clear_global_results_cache() -> None:
    _LEDGER_CACHE.clear()


def _ledger_cache_get(key: tuple) -> Optional[list[ResultLedgerRow]]:
    hit = _LEDGER_CACHE.get(key)
    if not hit:
        return None
    ts, rows = hit
    if time.monotonic() - ts > _LEDGER_CACHE_TTL_SECONDS:
        _LEDGER_CACHE.pop(key, None)
        return None
    return list(rows)


def _ledger_cache_key(dirs: Iterable[str], include_empty_specimens: bool) -> tuple:
    parts = []
    for directory in _dedupe_dirs(dirs):
        root = Path(directory)
        db_path = root / "_data" / "project.db"
        wal_path = Path(str(db_path) + "-wal")
        results_dir = root / RESULTS_DIR
        parts.append((
            directory,
            _stat_sig(db_path),
            _stat_sig(wal_path),
            _dir_sig(results_dir),
        ))
    return (bool(include_empty_specimens), tuple(parts))


def _stat_sig(path: Path):
    try:
        st = path.stat()
        return (st.st_mtime_ns, st.st_size)
    except OSError:
        return None


def _dir_sig(path: Path):
    try:
        st = path.stat()
    except OSError:
        return None
    try:
        count = sum(1 for _ in os.scandir(path))
    except OSError:
        count = -1
    return (st.st_mtime_ns, count)


def summarize_ledger(rows: Iterable[ResultLedgerRow]) -> dict[str, int]:
    counts = {
        "rows": 0,
        "specimens": 0,
        "tiffs": 0,
        "zips": 0,
        "complete": 0,
        "orphan": 0,
        "missing": 0,
    }
    seen_uids: set[tuple[str, str]] = set()
    for row in rows:
        counts["rows"] += 1
        if row.display_uid:
            seen_uids.add((row.project_dir, row.display_uid))
        if row.tiff_path:
            counts["tiffs"] += 1
        if row.zip_path:
            counts["zips"] += 1
        if row.status == "完整":
            counts["complete"] += 1
        if row.orphan:
            counts["orphan"] += 1
        if row.status in {"缺 TIF", "缺 ZIP", "缺 TIF文件", "缺 ZIP文件", "无标本"}:
            counts["missing"] += 1
    counts["specimens"] = len(seen_uids)
    return counts


def _collect_workspace(project_dir: str, include_empty_specimens: bool) -> list[ResultLedgerRow]:
    db_path = Path(project_dir) / "_data" / "project.db"
    if not db_path.exists():
        return []
    conn = _connect_readonly(db_path)
    if conn is None:
        return []
    try:
        specimens = _load_specimens(conn, project_dir)
        grouping_rows = _load_grouping(conn)
    finally:
        conn.close()

    project_name = Path(project_dir).name or project_dir
    rows: list[ResultLedgerRow] = []
    registered_paths: set[str] = set()
    grouped_uids: set[str] = set()

    for g in grouping_rows:
        uid = str(g.get("uid") or "")
        grouped_uids.add(uid)
        tiff_path = str(g.get("composed_tiff_path") or "")
        zip_path = str(g.get("archive_zip") or "")
        if tiff_path:
            registered_paths.add(_norm_path(tiff_path))
        if zip_path:
            registered_paths.add(_norm_path(zip_path))
        sp = specimens.get(uid, {})
        rows.append(_make_registered_row(
            project_dir=project_dir,
            project_name=project_name,
            uid=uid,
            group_index=_int_or_none(g.get("group_index")),
            specimen=sp,
            tiff_path=tiff_path,
            zip_path=zip_path,
        ))

    if include_empty_specimens:
        for uid, sp in specimens.items():
            if uid in grouped_uids:
                continue
            rows.append(ResultLedgerRow(
                project_dir=project_dir,
                project_name=project_name,
                uid=uid,
                group_index=None,
                scientific_name=str(sp.get("scientific_name") or ""),
                storage=str(sp.get("storage") or ""),
                collection_date=str(sp.get("collection_date") or ""),
                photo_date=str(sp.get("photo_date") or ""),
                registered=True,
                has_specimen=True,
                status="无成果",
            ))

    rows.extend(_orphan_rows(project_dir, project_name, specimens, registered_paths))
    return rows


def _dedupe_dirs(dirs: Iterable[str]) -> list[str]:
    out: list[str] = []
    for directory in dirs:
        if not directory:
            continue
        try:
            resolved = str(Path(directory).resolve())
        except OSError:
            resolved = str(directory)
        if resolved not in out:
            out.append(resolved)
    return out


def _connect_readonly(db_path: Path) -> Optional[sqlite3.Connection]:
    try:
        resolved = str(db_path.resolve())
        uri_path = quote(resolved, safe="/:\\")
        conn = sqlite3.connect(f"file:{uri_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        return conn
    except (OSError, sqlite3.Error):
        return None


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    try:
        return {str(row["name"]) for row in conn.execute(f"PRAGMA table_info({table})")}
    except sqlite3.Error:
        return set()


def _load_specimens(conn: sqlite3.Connection, project_dir: str) -> dict[str, dict]:
    cols = _table_columns(conn, "specimens")
    if "uid" not in cols:
        return {}
    wanted = [
        "uid",
        "scientific_name",
        "storage",
        "collection_date",
        "photo_date",
        "owner_project_dir",
    ]
    select = ", ".join(
        name if name in cols else f"'' AS {name}"
        for name in wanted
    )
    try:
        rows = conn.execute(f"SELECT {select} FROM specimens").fetchall()
    except sqlite3.Error:
        return {}
    out: dict[str, dict] = {}
    for row in rows:
        uid = str(row["uid"] or "")
        if not uid:
            continue
        item = dict(row)
        item["owner_project_dir"] = item.get("owner_project_dir") or project_dir
        out[uid] = item
    return out


def _load_grouping(conn: sqlite3.Connection) -> list[dict]:
    cols = _table_columns(conn, "grouping")
    if "uid" not in cols:
        return []
    wanted = ["uid", "group_index", "composed_tiff_path", "archive_zip", "status"]
    select = ", ".join(
        name if name in cols else ("NULL AS " + name)
        for name in wanted
    )
    order = " ORDER BY uid, group_index" if "group_index" in cols else " ORDER BY uid"
    try:
        return [dict(row) for row in conn.execute(f"SELECT {select} FROM grouping{order}")]
    except sqlite3.Error:
        return []


def _make_registered_row(
    *,
    project_dir: str,
    project_name: str,
    uid: str,
    group_index: Optional[int],
    specimen: dict,
    tiff_path: str,
    zip_path: str,
) -> ResultLedgerRow:
    tiff_exists = bool(tiff_path and Path(tiff_path).is_file())
    zip_exists = bool(zip_path and Path(zip_path).is_file())
    has_specimen = bool(specimen)
    status = _registered_status(
        has_specimen=has_specimen,
        tiff_path=tiff_path,
        zip_path=zip_path,
        tiff_exists=tiff_exists,
        zip_exists=zip_exists,
    )
    return ResultLedgerRow(
        project_dir=project_dir,
        project_name=project_name,
        uid=uid,
        group_index=group_index,
        scientific_name=str(specimen.get("scientific_name") or ""),
        storage=str(specimen.get("storage") or ""),
        collection_date=str(specimen.get("collection_date") or ""),
        photo_date=str(specimen.get("photo_date") or ""),
        tiff_path=tiff_path,
        zip_path=zip_path,
        tiff_exists=tiff_exists,
        zip_exists=zip_exists,
        registered=True,
        has_specimen=has_specimen,
        status=status,
    )


def _registered_status(
    *,
    has_specimen: bool,
    tiff_path: str,
    zip_path: str,
    tiff_exists: bool,
    zip_exists: bool,
) -> str:
    if not has_specimen:
        return "无标本"
    if tiff_path and not tiff_exists:
        return "缺 TIF文件"
    if zip_path and not zip_exists:
        return "缺 ZIP文件"
    if tiff_path and zip_path:
        return "完整"
    if tiff_path and not zip_path:
        return "缺 ZIP"
    if zip_path and not tiff_path:
        return "缺 TIF"
    return "无成果文件"


def _orphan_rows(
    project_dir: str,
    project_name: str,
    specimens: dict[str, dict],
    registered_paths: set[str],
) -> list[ResultLedgerRow]:
    results_dir = Path(project_dir) / RESULTS_DIR
    if not results_dir.is_dir():
        return []
    pairs: dict[str, dict[str, str]] = {}
    for path in _iter_result_files(results_dir):
        if _norm_path(str(path)) in registered_paths:
            continue
        key = str(path.with_suffix(""))
        ext = path.suffix.lower()
        if ext in {".tif", ".tiff"}:
            pairs.setdefault(key, {})["tiff"] = str(path)
        elif ext == ".zip":
            pairs.setdefault(key, {})["zip"] = str(path)

    rows: list[ResultLedgerRow] = []
    for pair in pairs.values():
        tiff_path = pair.get("tiff", "")
        zip_path = pair.get("zip", "")
        name = Path(tiff_path or zip_path).stem
        inferred_uid = _infer_uid(name, specimens.keys())
        sp = specimens.get(inferred_uid, {})
        rows.append(ResultLedgerRow(
            project_dir=project_dir,
            project_name=project_name,
            uid="",
            group_index=None,
            scientific_name=str(sp.get("scientific_name") or ""),
            storage=str(sp.get("storage") or ""),
            collection_date=str(sp.get("collection_date") or ""),
            photo_date=str(sp.get("photo_date") or ""),
            tiff_path=tiff_path,
            zip_path=zip_path,
            tiff_exists=bool(tiff_path and Path(tiff_path).is_file()),
            zip_exists=bool(zip_path and Path(zip_path).is_file()),
            registered=False,
            has_specimen=bool(sp),
            orphan=True,
            inferred_uid=inferred_uid,
            status="未入库" if inferred_uid else "孤儿文件",
        ))
    return rows


def _iter_result_files(results_dir: Path):
    try:
        for path in results_dir.rglob("*"):
            if path.is_file() and path.suffix.lower() in {".tif", ".tiff", ".zip"}:
                yield path
    except OSError:
        return


def _infer_uid(stem: str, uids: Iterable[str]) -> str:
    stem_upper = stem.upper()
    matches = [uid for uid in uids if uid and str(uid).upper() in stem_upper]
    if not matches:
        return ""
    return sorted(matches, key=len, reverse=True)[0]


def _norm_path(path: str) -> str:
    try:
        return os.path.normcase(str(Path(path).resolve()))
    except OSError:
        return os.path.normcase(os.path.abspath(path))


def _int_or_none(value) -> Optional[int]:
    try:
        if value is None:
            return None
        return int(value)
    except (TypeError, ValueError):
        return None
