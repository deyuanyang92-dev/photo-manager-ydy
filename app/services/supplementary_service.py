"""supplementary_service.py — 补处理 (supplementary archival) pure logic.

Ports two web-oracle functions so the desktop can archive a selected
JPG + TIFF bundle WITHOUT requiring an active specimen:

  - finalCompositeTarget(name)  (app.js:3808-3824)
        → resolve_specimen_for_tiff(db, tiff_name)
        Resolve the specimen purely from the TIFF filename — match the
        uniqueId (sequence stripped) against the specimens table.
  - validateSmartGroup(files)   (app.js:4097-4123)
        → validate_supp_group(db, paths)
        Split the selection into jpg / tiff / unsupported, enforce
        (≥1 JPG, exactly 1 TIFF, no unsupported), resolve the specimen.

No Qt here — kept importable for unit tests. The actual archival (cjxl /
ZIP / safety gates) stays in app.services.archive_service; this module only
decides *what* to archive and *which specimen* it belongs to.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from app.services.organize_service import _parse_uid_from_tiff_name

# Real-monitor mode floor: a single JPG original is enough (oracle minJpgs=1
# when shouldUseRealMonitor() — desktop always operates on real files).
_MIN_JPGS = 1

_JPG_EXTS = {".jpg", ".jpeg"}
_TIFF_EXTS = {".tif", ".tiff"}

# Exact oracle pause message (app.js:4118) — asserted verbatim in tests.
_MSG_UNNAMED_TIFF = "TIFF 未按完整成果文件名命名，已暂停该组"


class SuppGroupError(Exception):
    """Raised when a 补处理 selection cannot form a valid archival group."""


@dataclass
class SuppGroup:
    """One validated 补处理 unit: JPG originals + one TIFF + its specimen."""

    jpg_paths: list[str]
    tiff_path: str
    uid: str                      # resolved uniqueId (sequence stripped)
    specimen: Optional[dict]      # full specimen row as dict


def _row_to_dict(row) -> dict:
    """Flatten a sqlite3.Row into a plain dict, merging raw_json fallback."""
    d = {k: row[k] for k in row.keys()}
    raw = d.get("raw_json")
    if raw:
        try:
            extra = json.loads(raw)
            if isinstance(extra, dict):
                # DB columns win over raw_json for keys present in both.
                merged = {**extra, **{k: v for k, v in d.items() if v is not None}}
                merged["raw_json"] = raw
                return merged
        except (ValueError, TypeError):
            pass
    return d


def resolve_specimen_for_tiff(
    db: sqlite3.Connection, tiff_name: str
) -> Optional[dict]:
    """Resolve the specimen a TIFF belongs to, from its filename alone.

    Mirrors finalCompositeTarget (app.js:3808-3824): the TIFF basename must
    be a full result-name (PROVINCE-SITE-STATION-ID-SEQ-STORAGE-DATESG); the
    sequence segment is stripped to yield the uniqueId, which is matched
    case-insensitively against the specimens table PRIMARY KEY ``uid``.

    Does NOT consult the active task — independent of activation state.

    Returns the specimen row as a dict, or None if the name is unparseable
    or no matching specimen exists.
    """
    target_uid = _parse_uid_from_tiff_name(tiff_name)
    if not target_uid:
        return None
    row = db.execute(
        "SELECT * FROM specimens WHERE uid = ? COLLATE NOCASE", (target_uid,)
    ).fetchone()
    if row is None:
        return None
    return _row_to_dict(row)


def _kind(path: str) -> str:
    ext = Path(path).suffix.lower()
    if ext in _JPG_EXTS:
        return "jpg"
    if ext in _TIFF_EXTS:
        return "tiff"
    return "other"


def validate_supp_group(
    db: sqlite3.Connection, paths: list[str]
) -> SuppGroup:
    """Validate a 补处理 selection into a SuppGroup.

    Mirrors validateSmartGroup (app.js:4097-4123) with minJpgs=1:
      - split by extension into jpg / tiff / unsupported
      - reject if any unsupported, or <1 JPG, or != 1 TIFF
      - resolve the specimen from the TIFF name; None → pause error

    Raises SuppGroupError (with the oracle's message text) on any failure.
    """
    jpgs: list[str] = []
    tiffs: list[str] = []
    unsupported: list[str] = []
    for p in paths:
        kind = _kind(p)
        if kind == "jpg":
            jpgs.append(p)
        elif kind == "tiff":
            tiffs.append(p)
        else:
            unsupported.append(p)

    if unsupported or len(jpgs) < _MIN_JPGS or len(tiffs) != 1:
        raise SuppGroupError("请选择至少 1 张 JPG 原片和 1 张 TIFF 成片后再压缩")

    tiff_path = tiffs[0]
    specimen = resolve_specimen_for_tiff(db, Path(tiff_path).name)
    if specimen is None:
        raise SuppGroupError(_MSG_UNNAMED_TIFF)

    return SuppGroup(
        jpg_paths=jpgs,
        tiff_path=tiff_path,
        uid=str(specimen["uid"]),
        specimen=specimen,
    )
