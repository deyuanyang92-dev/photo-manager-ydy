"""GroupingEntry dataclass — mirrors grouping table (extended columns from W0 spec).

Extended columns beyond the original db-utils.js schema:
  status, source, created_at, updated_at, result_sequence,
  archive_zip, retired_tiff_paths — all from grouping_confirmations.json groups.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class GroupingEntry:
    """One grouping row: one angle-group for one specimen."""
    uid: str
    group_index: int
    angle_label: Optional[str] = None
    jpg_paths: Optional[str] = None       # JSON array stored as TEXT
    composed_tiff_path: Optional[str] = None
    status: Optional[str] = None
    source: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    result_sequence: Optional[int] = None
    archive_zip: Optional[str] = None
    retired_tiff_paths: Optional[str] = None  # JSON array stored as TEXT
    raw_json: Optional[str] = None

    @classmethod
    def from_row(cls, row) -> "GroupingEntry":
        """Construct from a sqlite3.Row (or dict-like)."""
        d = dict(row)
        return cls(**{k: d.get(k) for k in cls.__dataclass_fields__})

    @property
    def jpg_paths_list(self) -> list[str]:
        """Parse jpg_paths JSON array. Returns [] on error or None."""
        if not self.jpg_paths:
            return []
        try:
            return json.loads(self.jpg_paths)
        except json.JSONDecodeError:
            return []

    @property
    def retired_tiff_paths_list(self) -> list[str]:
        """Parse retired_tiff_paths JSON array."""
        if not self.retired_tiff_paths:
            return []
        try:
            return json.loads(self.retired_tiff_paths)
        except json.JSONDecodeError:
            return []

    @property
    def raw(self) -> dict:
        """Parse raw_json back to dict."""
        if not self.raw_json:
            return {}
        try:
            return json.loads(self.raw_json)
        except json.JSONDecodeError:
            return {}
