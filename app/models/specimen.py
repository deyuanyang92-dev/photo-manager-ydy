"""Specimen dataclass — mirrors db-utils.js schema + DATA-MODEL.md fields.

raw_json carries the complete original specimen object so no field is ever lost.
No species/species_cn columns — Chinese name lives in scientific_name_cn.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Specimen:
    """Core specimen record.

    Field names mirror the SQLite columns (snake_case) as well as the
    camelCase JS source (accessible via raw_json).
    """
    uid: str
    id: Optional[str] = None
    province: Optional[str] = None
    site: Optional[str] = None
    station: Optional[str] = None
    storage: Optional[str] = None
    collection_date: Optional[str] = None
    photo_date: Optional[str] = None
    scientific_name: Optional[str] = None
    scientific_name_cn: Optional[str] = None
    taxon_group: Optional[str] = None
    taxon_group_cn: Optional[str] = None
    order_name: Optional[str] = None
    order_cn: Optional[str] = None
    family: Optional[str] = None
    family_cn: Optional[str] = None
    genus: Optional[str] = None
    genus_cn: Optional[str] = None
    lon: Optional[float] = None
    lat: Optional[float] = None
    geo_area: Optional[str] = None
    collector: Optional[str] = None
    photographer: Optional[str] = None
    identifier: Optional[str] = None
    notes: Optional[str] = None
    photo_notes: Optional[str] = None
    angle: Optional[str] = None
    metadata: int = 0
    pinned: int = 0
    owner_project_dir: Optional[str] = None
    raw_json: Optional[str] = None  # complete original JSON object (zero field loss)

    @classmethod
    def from_row(cls, row) -> "Specimen":
        """Construct from a sqlite3.Row (or dict-like)."""
        d = dict(row)
        return cls(**{k: d.get(k) for k in cls.__dataclass_fields__})

    @property
    def raw(self) -> dict:
        """Parse raw_json back to dict. Returns {} on parse error."""
        if not self.raw_json:
            return {}
        try:
            return json.loads(self.raw_json)
        except json.JSONDecodeError:
            return {}
