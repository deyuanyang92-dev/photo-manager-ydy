"""project_station_import_service.py — 项目级站位总表导入与分发（断面路由）.

A survey has ONE station total-table: a 断面 (transect) column plus
province/site/station/经度/纬度 columns. The user imports it ONCE at the survey
root; each row is routed to the correct 断面 *subfolder*'s ``collection_records``
table by matching the 断面 column value against an EXISTING subfolder.

This is pure routing — NO catalog, NO new tables, NO stable IDs. It NEVER
auto-creates folders the user didn't make: a 断面 value with no matching
existing folder is reported as *unmatched* and skipped (its rows are surfaced,
never silently dropped). When a matched folder has no workspace db yet, the db
is materialised on write (``open_project_db(create=True)``) — the same effect
as the user entering that 断面 and starting to fill its records.

Reuses verbatim:
  - coord_import_service.normalize_rows  — coord parsing + GCJ02/BD09→WGS84.
  - collection_record_service.upsert_record — idempotent record write.
  - project_tree_service.scan_tree — read-only folder enumeration.
  - db_manager.open_project_db(create=True) — materialise the target db.
  - backup_service.snapshot_project — best-effort pre-write safety snapshot.

Qt-free; tested headless.
"""
from __future__ import annotations

import os
from pathlib import Path

from app.db.db_manager import open_project_db
from app.services import backup_service
from app.services import collection_record_service
from app.services import coord_import_service
from app.services import project_tree_service

# Group label for rows whose 断面 routing value is empty — surfaced under
# unmatched so nothing is silently lost.
EMPTY_TRANSECT_LABEL = "(未指定断面)"

# Helper keys normalize_rows attaches that are NOT real record columns.
_HELPER_KEYS = ("ok", "error", "_raw")


# ── folder enumeration / matching ──────────────────────────────────────────────

def _all_dirs(node: dict) -> list[dict]:
    """Flatten a scan_tree node into every descendant dir (root node excluded).

    Returns ``{name, path, has_data}`` for every node BELOW the root — both
    workspaces and plain folders (RESERVED_DIR_NAMES already filtered by
    scan_tree). The root node itself is not a routing target.
    """
    out: list[dict] = []
    for child in node.get("children", []):
        out.append({"name": child["name"], "path": child["path"],
                    "has_data": child.get("has_data", False)})
        out.extend(_all_dirs(child))
    return out


def _match_transect(transect: str, root_dir: str, dirs: list[dict]):
    """Resolve a 断面 value to an existing folder, or None.

    A folder matches when ``os.path.relpath(path, root)`` == value OR
    ``os.path.basename(path)`` == value. Matches against ALL existing dirs
    (workspaces and plain folders alike) — a folder made via mkdir with no
    ``_data`` yet must still match; its db is materialised on write.
    """
    for d in dirs:
        rel = os.path.relpath(d["path"], root_dir)
        if rel == transect or os.path.basename(d["path"]) == transect:
            return d
    return None


# ── preview ────────────────────────────────────────────────────────────────────

def preview_distribution(root_dir: str, rows: list[dict], mapping: dict, *,
                         coord_system: str = "WGS84", default_date: str = "") -> dict:
    """Group total-table *rows* by 断面 and resolve each group to a folder.

    *mapping* is the coord_import mapping (province/site/station/station_label/
    lon/lat or lonlat) PLUS a ``"transect"`` key naming the 断面 routing column.

    Returns::

        {
          "matched":   {transect: {"path", "rel", "rows":[ok recs], "errors":[err recs]}},
          "unmatched": {transect: {"rows":[ok recs], "errors":[err recs]}},
          "totals":    {"rows", "ok", "errors", "matched_transects", "unmatched_transects"},
        }

    Rows with an empty 断面 value are grouped under ``"(未指定断面)"`` and reported
    under *unmatched* — surfaced, never dropped.
    """
    transect_col = mapping["transect"]

    # Group raw rows by their 断面 value (empty → the empty-transect label).
    groups: dict[str, list[dict]] = {}
    for row in rows:
        key = str(row.get(transect_col, "") or "").strip()
        groups.setdefault(key or EMPTY_TRANSECT_LABEL, []).append(row)

    # Enumerate every existing subfolder under root once.
    tree = project_tree_service.scan_tree(root_dir)
    dirs = _all_dirs(tree)

    matched: dict[str, dict] = {}
    unmatched: dict[str, dict] = {}
    total_rows = total_ok = total_err = 0

    for transect, group_rows in groups.items():
        normalized = coord_import_service.normalize_rows(
            group_rows, mapping, coord_system=coord_system, default_date=default_date)
        ok_rows = [r for r in normalized if r.get("ok")]
        err_rows = [r for r in normalized if not r.get("ok")]
        total_rows += len(normalized)
        total_ok += len(ok_rows)
        total_err += len(err_rows)

        # The empty-transect group can never match a real folder.
        match = None
        if transect != EMPTY_TRANSECT_LABEL:
            match = _match_transect(transect, root_dir, dirs)

        if match is not None:
            matched[transect] = {
                "path": match["path"],
                "rel": os.path.relpath(match["path"], root_dir),
                "rows": ok_rows,
                "errors": err_rows,
            }
        else:
            unmatched[transect] = {"rows": ok_rows, "errors": err_rows}

    return {
        "matched": matched,
        "unmatched": unmatched,
        "totals": {
            "rows": total_rows,
            "ok": total_ok,
            "errors": total_err,
            "matched_transects": len(matched),
            "unmatched_transects": len(unmatched),
        },
    }


# ── distribute ─────────────────────────────────────────────────────────────────

def _clean_record(rec: dict) -> dict:
    """Strip helper keys (ok/error/_raw) → a clean dict of real record fields."""
    return {k: v for k, v in rec.items() if k not in _HELPER_KEYS}


def distribute(root_dir: str, plan: dict) -> dict:
    """Write each matched transect's ok rows into its workspace's collection_records.

    *plan* is the dict :func:`preview_distribution` returns (or a filtered
    subset). For each matched transect: snapshot first (best-effort, never
    aborts), materialise the db with ``create=True``, then ``upsert_record``
    each ok row with the helper keys stripped.

    Returns::

        {"written", "skipped_unmatched_rows",
         "targets": {transect: {"path", "written", "db_created"}},
         "unmatched_transects": [values]}
    """
    written = 0
    targets: dict[str, dict] = {}

    for transect, info in plan.get("matched", {}).items():
        path = info["path"]
        ok_rows = info.get("rows", [])

        # db_created = the workspace db did NOT exist before this call.
        db_existed = (Path(path) / "_data" / "project.db").exists()

        # Best-effort snapshot before any write; must never abort distribution.
        try:
            backup_service.snapshot_project(path)
        except Exception:
            pass

        db = open_project_db(path, create=True)
        count = 0
        for rec in ok_rows:
            collection_record_service.upsert_record(db, _clean_record(rec))
            count += 1

        written += count
        targets[transect] = {
            "path": path,
            "written": count,
            "db_created": not db_existed,
        }

    # Every ok row under an unmatched transect is skipped, never written.
    skipped = sum(len(info.get("rows", []))
                  for info in plan.get("unmatched", {}).values())

    return {
        "written": written,
        "skipped_unmatched_rows": skipped,
        "targets": targets,
        "unmatched_transects": list(plan.get("unmatched", {}).keys()),
    }
