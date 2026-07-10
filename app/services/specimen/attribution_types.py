"""Shared data types for specimen-photo attribution."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class AttributionCtx:
    """Pre-built context used by the JPG attribution rules."""

    explicit_unassigns: set = field(default_factory=set)
    path_to_uid: dict = field(default_factory=dict)
    assign_to_uid: dict = field(default_factory=dict)
    activations: list = field(default_factory=list)
    organized_group_paths: set = field(default_factory=set)
