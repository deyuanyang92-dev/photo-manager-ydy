"""Read-only audit of TIFF result filenames in legacy photo folders."""
from __future__ import annotations

import csv
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from app.services.organize_service import build_result_basename
from app.services.project_settings_service import DEFAULT_NAMING_RULES
from app.utils.naming import (
    build_configured_result_id,
    naming_rules_summary,
    normalize_naming_components,
    normalize_uid,
    parse_tiff_result_detail,
)


@dataclass(frozen=True)
class TiffNameItem:
    path: str
    name: str
    modified_at: str
    valid: bool
    uid: Optional[str] = None
    sequence: Optional[int] = None
    reason: str = ""
    suggested_name: str = ""


@dataclass(frozen=True)
class TiffNamingAudit:
    folder: str
    items: list[TiffNameItem] = field(default_factory=list)
    rules_summary: str = ""

    @property
    def total(self) -> int:
        return len(self.items)

    @property
    def valid_count(self) -> int:
        return sum(1 for item in self.items if item.valid)

    @property
    def invalid_count(self) -> int:
        return self.total - self.valid_count


def annotate_tiff_entries(
    entries: list,
    *,
    naming_components: Optional[list[str]] = None,
) -> list:
    """Attach filename-recognition state to monitor TIFF entries in place."""
    components = normalize_naming_components(
        naming_components or DEFAULT_NAMING_RULES["components"]
    )
    for entry in entries:
        name = str(getattr(entry, "name", "") or Path(
            str(getattr(entry, "path", "") or "")
        ).name)
        detail = parse_tiff_result_detail(Path(name).stem, components)
        entry.naming_ok = detail is not None
        entry.attributed_specimen_id = detail.uid if detail is not None else None
    return entries


def inspect_tiff_names(
    folder: str,
    *,
    current_uid: Optional[str] = None,
    recursive: bool = False,
    naming_components: Optional[list[str]] = None,
    specimen_values: Optional[dict[str, str]] = None,
) -> TiffNamingAudit:
    """Inspect TIFF names without modifying any files.

    Validation follows the project's naming-rule component order from settings.
    Invalid names receive a suggestion when specimen field values are available.
    """
    root = Path(folder).expanduser().resolve()
    if not root.is_dir():
        raise ValueError(f"扫描目录不存在: {folder}")

    iterator = root.rglob("*") if recursive else root.iterdir()
    paths: list[Path] = []
    for path in iterator:
        if path.is_file() and path.suffix.lower() in {".tif", ".tiff"}:
            paths.append(path)
    return inspect_tiff_paths(
        paths,
        current_uid=current_uid,
        naming_components=naming_components,
        specimen_values=specimen_values,
        folder=str(root),
    )


def inspect_tiff_paths(
    paths: list[str | Path],
    *,
    current_uid: Optional[str] = None,
    naming_components: Optional[list[str]] = None,
    specimen_values: Optional[dict[str, str]] = None,
    folder: Optional[str] = None,
) -> TiffNamingAudit:
    """Inspect explicit TIFF paths without modifying any files."""
    components = normalize_naming_components(
        naming_components or DEFAULT_NAMING_RULES["components"]
    )
    rules_summary = naming_rules_summary(components)
    normalized_current_uid = normalize_uid(current_uid) if current_uid else None

    files: list[tuple[int, Path]] = []
    for raw in paths:
        path = Path(raw).expanduser()
        if not path.is_file() or path.suffix.lower() not in {".tif", ".tiff"}:
            continue
        try:
            resolved = path.resolve()
            files.append((resolved.stat().st_mtime_ns, resolved))
        except OSError:
            continue
    files.sort(key=lambda item: (item[0], item[1].name.casefold(), str(item[1])))

    parsed: list[tuple[int, Path, Optional[str], Optional[int]]] = []
    max_seq = 0
    for mtime_ns, path in files:
        detail = parse_tiff_result_detail(path.stem, components)
        uid = None
        sequence = None
        extra_suffix = False
        if detail:
            uid = detail.uid
            sequence = detail.sequence
            extra_suffix = detail.has_extra_suffix
        if (
            normalized_current_uid
            and uid
            and normalize_uid(uid) == normalized_current_uid
            and sequence is not None
        ):
            max_seq = max(max_seq, sequence)
        parsed.append((mtime_ns, path, uid, sequence, extra_suffix))

    next_suggestion = max_seq + 1
    items: list[TiffNameItem] = []
    for mtime_ns, path, uid, sequence, extra_suffix in parsed:
        valid = uid is not None and sequence is not None
        suggestion = ""
        if valid:
            reason = (
                "符合：标本唯一编号已识别，末尾附加信息已忽略"
                if extra_suffix
                else "符合：标本唯一编号已识别"
            )
        else:
            reason = f"未识别到标本唯一编号（{rules_summary}）"
        if not valid:
            if specimen_values:
                suggestion = (
                    build_configured_result_id(
                        components,
                        specimen_values,
                        seq=next_suggestion,
                    )
                    + ".tif"
                )
                next_suggestion += 1
            elif normalized_current_uid:
                suggestion = (
                    build_result_basename(normalized_current_uid, next_suggestion)
                    + ".tif"
                )
                next_suggestion += 1
        items.append(TiffNameItem(
            path=str(path),
            name=path.name,
            modified_at=datetime.fromtimestamp(
                mtime_ns / 1_000_000_000, tz=timezone.utc
            ).isoformat(),
            valid=valid,
            uid=uid,
            sequence=sequence,
            reason=reason,
            suggested_name=suggestion,
        ))
    return TiffNamingAudit(
        folder=str(folder or _common_parent(files)),
        items=items,
        rules_summary=rules_summary,
    )


def _common_parent(files: list[tuple[int, Path]]) -> str:
    if not files:
        return ""
    parents = [str(path.parent) for _, path in files]
    try:
        return parents[0] if len(parents) == 1 else os.path.commonpath(parents)
    except Exception:
        return parents[0]


def export_tiff_naming_audit_csv(audit: TiffNamingAudit, path: str) -> None:
    """Write audit rows to CSV (UTF-8 BOM) for spreadsheet / data management."""
    with open(path, "w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow([
            "修改时间",
            "TIF文件名",
            "状态",
            "解析标本编号",
            "成果序号",
            "建议名称",
            "文件路径",
        ])
        for item in audit.items:
            writer.writerow([
                item.modified_at,
                item.name,
                "符合" if item.valid else "不符合",
                item.uid or "",
                item.sequence if item.sequence is not None else "",
                item.suggested_name or "",
                item.path,
            ])
