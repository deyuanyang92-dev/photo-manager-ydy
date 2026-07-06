"""In-memory label print batch for accumulating sheet-paper labels.

The label studio already supports immediate printing from the current specimen
selection.  This small module keeps the separate "print later" state: users can
collect specimen indices under a label bucket, then print that bucket as one A4
/ A5 imposition job.
"""

from __future__ import annotations

from collections.abc import Iterable


_VALID_BUCKETS = ("sample", "tissue")


class LabelPrintBatch:
    """Ordered, de-duplicated specimen index lists per label bucket."""

    def __init__(self) -> None:
        self._indices: dict[str, list[int]] = {bucket: [] for bucket in _VALID_BUCKETS}

    def add(self, bucket: str, indices: Iterable[int]) -> int:
        """Append valid indices to *bucket* and return the number newly added."""
        items = self._bucket(bucket)
        before = len(items)
        seen = set(items)
        for raw in indices:
            try:
                idx = int(raw)
            except (TypeError, ValueError):
                continue
            if idx < 0 or idx in seen:
                continue
            items.append(idx)
            seen.add(idx)
        return len(items) - before

    def clear(self, bucket: str | None = None) -> None:
        """Clear one bucket, or the whole batch when *bucket* is None."""
        if bucket is None:
            for items in self._indices.values():
                items.clear()
            return
        self._bucket(bucket).clear()

    def prune(self, max_count: int) -> None:
        """Drop indices that no longer exist after specimen reload."""
        limit = max(0, int(max_count))
        for bucket in _VALID_BUCKETS:
            self._indices[bucket] = [
                idx for idx in self._indices[bucket] if 0 <= idx < limit
            ]

    def indices(self, bucket: str) -> list[int]:
        """Return a copy of queued indices for *bucket*."""
        return list(self._bucket(bucket))

    def count(self, bucket: str) -> int:
        return len(self._bucket(bucket))

    def _bucket(self, bucket: str) -> list[int]:
        if bucket not in _VALID_BUCKETS:
            raise ValueError(f"unknown label bucket: {bucket!r}")
        return self._indices[bucket]
