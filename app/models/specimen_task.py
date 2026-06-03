"""SpecimenTask dataclass — mirrors tasks table + raw_json兜底.

Collaboration fields (status, createdBy, assignedTo, role, photoIndexSummary…)
live in raw_json; only the activation fields are first-class columns.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Optional


@dataclass
class SpecimenTask:
    """Task record for a specimen activation / organization state."""
    uid: str
    is_active: int = 0            # 1 = active
    activated_at: Optional[str] = None
    last_organized_at: Optional[str] = None
    next_result_sequence_hint: Optional[int] = None
    raw_json: Optional[str] = None  # collaboration fields etc.

    @classmethod
    def from_row(cls, row) -> "SpecimenTask":
        """Construct from a sqlite3.Row (or dict-like)."""
        d = dict(row)
        return cls(**{k: d.get(k) for k in cls.__dataclass_fields__})

    @property
    def raw(self) -> dict:
        """Parse raw_json back to dict."""
        if not self.raw_json:
            return {}
        try:
            return json.loads(self.raw_json)
        except json.JSONDecodeError:
            return {}

    @property
    def active(self) -> bool:
        return bool(self.is_active)
