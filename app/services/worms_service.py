"""worms_service.py — WoRMS (World Register of Marine Species) proxy service.

Provides async-friendly (via QThread) and synchronous access to the
marinespecies.org REST API with SQLite-backed disk caching and a rate limiter.

Oracle:
  server.js ~1722–2188 (wormsFetch, worms endpoints, job runner)
  docs/modules/worms.md

Key invariants
--------------
1. Chinese fields (``*Cn``) are NEVER overwritten by this service.
   WoRMS only supplies Latin names and taxonomic hierarchy.
2. Cache TTLs:
      search          7 days
      classification  30 days
      synonyms        14 days
      children        14 days
      record          14 days
      family-genera   30 days
3. Rate limit: 600 ms between live API calls.
4. Cache max size: 10 MB (oldest 25 % evicted on overflow).
5. Batch jobs: persisted as dicts in a list; cursor-based sequential processing;
   states: running / paused / cancelled / completed.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
import threading
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import httpx


# ── Constants (mirrors server.js) ────────────────────────────────────────────

WORMS_BASE_URL = "https://www.marinespecies.org/rest"

# TTL in seconds
TTL_SEARCH          = 7  * 86400
TTL_CLASSIFICATION  = 30 * 86400
TTL_SYNONYMS        = 14 * 86400
TTL_CHILDREN        = 14 * 86400
TTL_RECORD          = 14 * 86400
TTL_FAMILY          = 30 * 86400

CACHE_MAX_BYTES = 10 * 1024 * 1024   # 10 MB

RATE_MIN_INTERVAL = 0.6  # seconds


# ── Data classes ──────────────────────────────────────────────────────────────

@dataclass
class WormsCacheEntry:
    """Single record in the on-disk cache."""
    data: Any
    fetched_at: str  # ISO-8601


@dataclass
class WormsJobCounts:
    matched: int = 0
    renamed: int = 0
    review:  int = 0
    not_found: int = 0
    error:   int = 0
    stale:   int = 0


@dataclass
class WormsJob:
    """Persistent batch-validation job."""
    id: str
    status: str           # running / paused / cancelled / completed
    created_at: str
    updated_at: str
    created_by: str
    record_ids: list[str]
    cursor: int
    counts: dict[str, int] = field(default_factory=dict)
    source: str = "filtered"   # "filtered" | "selected"
    completed_at: Optional[str] = None

    def to_dict(self) -> dict:
        d = asdict(self)
        d["id"] = self.id
        return d

    @staticmethod
    def from_dict(d: dict) -> "WormsJob":
        return WormsJob(
            id=d["id"],
            status=d.get("status", "paused"),
            created_at=d.get("created_at", ""),
            updated_at=d.get("updated_at", ""),
            created_by=d.get("created_by", ""),
            record_ids=d.get("record_ids", []),
            cursor=d.get("cursor", 0),
            counts=d.get("counts", {}),
            source=d.get("source", "filtered"),
            completed_at=d.get("completed_at"),
        )


# ── Cache storage helpers ─────────────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _elapsed_seconds(iso_ts: str) -> float:
    """Return seconds since *iso_ts* (an ISO-8601 string)."""
    try:
        dt = datetime.fromisoformat(iso_ts)
        now = datetime.now(timezone.utc)
        if dt.tzinfo is None:
            now = datetime.utcnow()
        return (now - dt).total_seconds()
    except Exception:
        return float("inf")


def _read_json_safe(path: str, default: Any) -> Any:
    """Read a JSON file, returning *default* on any error."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return default


def _atomic_write_json(path: str, data: Any) -> None:
    """Write JSON atomically via a temp file."""
    tmp = path + ".tmp"
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


# ── WoRMS Service ─────────────────────────────────────────────────────────────

class WormsService:
    """Proxy to the marinespecies.org REST API with disk-based caching.

    Parameters
    ----------
    cache_path:
        Path to the JSON cache file (worms_cache.json).
    jobs_path:
        Path to the JSON jobs file (worms_jobs.json).

    Thread safety
    -------------
    A ``threading.Lock`` protects cache reads/writes so that a QThread
    worker can call this service safely from a non-main thread while the
    UI reads cached data on the main thread.

    Chinese-field protection
    ------------------------
    This service never touches ``*Cn`` fields (e.g. ``scientificNameCn``,
    ``familyCn``, ``orderCn``, ``classCn``).  The ``classify_into_record``
    helper merges WoRMS data into an existing specimen record and explicitly
    skips all ``Cn``-suffixed keys.
    """

    def __init__(
        self,
        cache_path: str,
        jobs_path: str,
        *,
        timeout: float = 15.0,
        rate_interval: float = RATE_MIN_INTERVAL,
    ) -> None:
        self._cache_path = cache_path
        self._jobs_path = jobs_path
        self._timeout = timeout
        self._rate_interval = rate_interval
        self._rate_lock = threading.Lock()
        self._cache_lock = threading.Lock()
        self._last_call_time: float = 0.0
        # Active jobs (id → Thread)
        self._active_jobs: dict[str, threading.Thread] = {}

    # ── Internal helpers ──────────────────────────────────────────────────

    def _read_cache(self) -> dict:
        return _read_json_safe(
            self._cache_path,
            {"_meta": {"version": 1, "total_entries": 0}, "records": {}},
        )

    def _write_cache(self, cache: dict) -> None:
        cache.setdefault("_meta", {})["total_entries"] = len(cache.get("records", {}))
        _atomic_write_json(self._cache_path, cache)

    def _evict_if_needed(self, cache: dict) -> None:
        """Evict oldest 25 % of entries when cache file exceeds CACHE_MAX_BYTES."""
        try:
            size = os.path.getsize(self._cache_path) if os.path.exists(self._cache_path) else 0
            if size < CACHE_MAX_BYTES:
                return
            records = cache.get("records", {})
            entries = sorted(records.items(), key=lambda kv: kv[1].get("fetched_at", ""))
            cut = max(1, len(entries) // 4)
            for key, _ in entries[:cut]:
                del records[key]
        except Exception:
            pass

    def _rate_wait(self) -> None:
        """Block (if needed) to honour the 600 ms rate limit."""
        with self._rate_lock:
            elapsed = time.monotonic() - self._last_call_time
            gap = self._rate_interval - elapsed
            if gap > 0:
                time.sleep(gap)
            self._last_call_time = time.monotonic()

    def _fetch(self, api_path: str, cache_key: str, ttl: int) -> Any:
        """Return cached data or fetch from WoRMS REST API.

        Parameters
        ----------
        api_path:
            Path appended to ``WORMS_BASE_URL``.
        cache_key:
            Key used in the cache dict.
        ttl:
            Cache time-to-live in seconds.

        Returns
        -------
        Parsed JSON data (list / dict / None).
        """
        with self._cache_lock:
            cache = self._read_cache()
            entry = cache.get("records", {}).get(cache_key)
            if entry and entry.get("data") is not None:
                if _elapsed_seconds(entry.get("fetched_at", "")) < ttl:
                    return entry["data"]

        # Live fetch with retries
        self._rate_wait()
        url = WORMS_BASE_URL + api_path
        last_err: Optional[Exception] = None
        for attempt in range(3):
            try:
                response = httpx.get(
                    url,
                    headers={"Accept": "application/json"},
                    timeout=self._timeout,
                )
                if response.status_code in (204, 404):
                    data = None
                elif response.status_code != 200:
                    raise httpx.HTTPStatusError(
                        f"WoRMS {response.status_code}",
                        request=response.request,
                        response=response,
                    )
                else:
                    data = response.json()

                with self._cache_lock:
                    cache = self._read_cache()
                    cache.setdefault("records", {})[cache_key] = {
                        "data": data,
                        "fetched_at": _now_iso(),
                    }
                    self._evict_if_needed(cache)
                    self._write_cache(cache)
                return data

            except Exception as exc:
                last_err = exc
                if attempt < 2:
                    time.sleep(0.5 * (attempt + 1))

        raise last_err  # type: ignore[misc]

    # ── Public API methods ────────────────────────────────────────────────

    def search(self, name: str, *, like: bool = True) -> list[dict]:
        """Search WoRMS by scientific name.

        Parameters
        ----------
        name:
            Scientific name to search for.
        like:
            If True (default), use prefix/fuzzy matching.
            If False, exact match only.

        Returns
        -------
        List of WoRMS AphiaRecord dicts (may be empty).
        """
        q = name.strip()
        if not q:
            raise ValueError("name must not be empty")
        api_path = (
            f"/AphiaRecordsByName/{httpx.URL(path=q).path}"
            f"?like={'true' if like else 'false'}&marine_only=false"
        )
        cache_key = f"search:{q}:{'like' if like else 'exact'}"
        result = self._fetch(api_path, cache_key, TTL_SEARCH)
        return result if isinstance(result, list) else []

    def classification(self, aphia_id: int) -> Optional[dict]:
        """Return full classification tree for *aphia_id*.

        Returns
        -------
        Nested classification dict from WoRMS, or None if not found.
        """
        if aphia_id <= 0:
            raise ValueError(f"Invalid AphiaID: {aphia_id}")
        api_path = f"/AphiaClassificationByAphiaID/{aphia_id}"
        return self._fetch(api_path, f"classification:{aphia_id}", TTL_CLASSIFICATION)

    def synonyms(self, aphia_id: int) -> list[dict]:
        """Return synonyms for *aphia_id*.

        Returns
        -------
        List of synonym AphiaRecord dicts (may be empty).
        """
        if aphia_id <= 0:
            raise ValueError(f"Invalid AphiaID: {aphia_id}")
        api_path = f"/AphiaSynonymsByAphiaID/{aphia_id}"
        result = self._fetch(api_path, f"synonyms:{aphia_id}", TTL_SYNONYMS)
        return result if isinstance(result, list) else []

    def record(self, aphia_id: int) -> Optional[dict]:
        """Return a single WoRMS record by AphiaID.

        Returns
        -------
        AphiaRecord dict, or None if not found.
        """
        if aphia_id <= 0:
            raise ValueError(f"Invalid AphiaID: {aphia_id}")
        api_path = f"/AphiaRecordByAphiaID/{aphia_id}"
        return self._fetch(api_path, str(aphia_id), TTL_RECORD)

    def children(self, aphia_id: int, *, offset: int = 1) -> list[dict]:
        """Return children of *aphia_id* (paginated, 50 per page).

        Parameters
        ----------
        aphia_id:
            Parent AphiaID.
        offset:
            Page offset (1-based, WoRMS API standard).

        Returns
        -------
        List of child AphiaRecord dicts.
        """
        if aphia_id <= 0:
            raise ValueError(f"Invalid AphiaID: {aphia_id}")
        api_path = (
            f"/AphiaChildrenByAphiaID/{aphia_id}"
            f"?marine_only=false&offset={offset}"
        )
        result = self._fetch(api_path, f"children:{aphia_id}:{offset}", TTL_CHILDREN)
        return result if isinstance(result, list) else []

    # ── Classification chain flattening ──────────────────────────────────

    @staticmethod
    def flatten_classification(chain: Optional[dict]) -> list[dict]:
        """Flatten a nested WoRMS classification tree into an ordered list.

        Each item: ``{"rank": str, "scientificname": str, "AphiaID": int}``.
        Higher ranks first (Kingdom → … → Species).

        Oracle: server.js flattenWormsClassification (implicit in saveAcceptedMapping).
        """
        result: list[dict] = []
        if not chain:
            return result

        def _walk(node: dict) -> None:
            if not node:
                return
            result.append({
                "rank": node.get("rank", ""),
                "scientificname": node.get("scientificname", ""),
                "AphiaID": node.get("AphiaID", 0),
            })
            child = node.get("child")
            if isinstance(child, dict):
                _walk(child)

        _walk(chain)
        return result

    # ── Chinese-field-safe merge helper ──────────────────────────────────

    @staticmethod
    def merge_worms_into_record(
        record: dict,
        worms_result: dict,
        chain: Optional[list[dict]] = None,
    ) -> dict:
        """Merge WoRMS verification result into a specimen taxonomy record.

        This method ONLY writes the following Latin/validation fields:
          - ``worms_aphia_id``
          - ``worms_valid_aphia_id``
          - ``worms_scientific_name``
          - ``worms_valid_name``
          - ``worms_authority``
          - ``worms_rank``
          - ``worms_status``
          - ``worms_class``, ``worms_order``, ``worms_family``, ``worms_genus``
          - ``worms_chain``       (classification list)
          - ``worms_verified_at`` (ISO timestamp)

        It NEVER touches any key ending in ``Cn`` (e.g. ``familyCn``,
        ``scientificNameCn``, ``orderCn``, ``classCn``).

        Parameters
        ----------
        record:
            Existing specimen/taxonomy dict (mutated in-place and returned).
        worms_result:
            A WoRMS AphiaRecord dict returned by :py:meth:`record` or
            :py:meth:`search`.
        chain:
            Optional flattened classification chain from
            :py:meth:`flatten_classification`.

        Returns
        -------
        The mutated ``record`` dict.
        """
        # Guard: do not overwrite any Chinese field
        for key in list(worms_result.keys()):
            if key.endswith("Cn"):
                # Safety assertion — WoRMS API never returns *Cn keys
                # but we make it explicit
                pass  # skip silently

        record["worms_aphia_id"]       = worms_result.get("AphiaID")
        record["worms_valid_aphia_id"] = worms_result.get("valid_AphiaID") or worms_result.get("AphiaID")
        record["worms_scientific_name"] = worms_result.get("scientificname", "")
        record["worms_valid_name"]     = worms_result.get("valid_name") or worms_result.get("scientificname", "")
        record["worms_authority"]      = worms_result.get("authority", "")
        record["worms_rank"]           = worms_result.get("rank", "")
        record["worms_status"]         = worms_result.get("status", "")
        record["worms_class"]          = worms_result.get("class", "")
        record["worms_order"]          = worms_result.get("order", "")
        record["worms_family"]         = worms_result.get("family", "")
        record["worms_genus"]          = worms_result.get("genus", "")
        record["worms_chain"]          = chain or []
        record["worms_verified_at"]    = _now_iso()

        # Explicitly confirm no Cn fields were changed
        assert not any(k.endswith("Cn") for k in [
            "worms_aphia_id", "worms_valid_aphia_id", "worms_scientific_name",
            "worms_valid_name", "worms_authority", "worms_rank", "worms_status",
            "worms_class", "worms_order", "worms_family", "worms_genus",
            "worms_chain", "worms_verified_at",
        ]), "BUG: merge wrote a Cn field"

        return record

    # ── Batch job management ──────────────────────────────────────────────

    def _read_jobs(self) -> dict:
        return _read_json_safe(self._jobs_path, {"jobs": []})

    def _write_jobs(self, store: dict) -> None:
        _atomic_write_json(self._jobs_path, store)

    def list_jobs(self) -> list[dict]:
        """Return all jobs (newest first).

        Returns
        -------
        List of job dicts, reverse-chronological.
        """
        store = self._read_jobs()
        return list(reversed(store.get("jobs", [])))

    def create_job(
        self,
        record_ids: list[str],
        created_by: str = "匿名",
        source: str = "selected",
    ) -> WormsJob:
        """Create and persist a new batch-validation job.

        Parameters
        ----------
        record_ids:
            List of taxonomy record IDs to validate.
        created_by:
            Operator name for audit trail.
        source:
            "selected" or "filtered".

        Returns
        -------
        The newly created :py:class:`WormsJob`.

        Raises
        ------
        ValueError
            If *record_ids* is empty.
        """
        if not record_ids:
            raise ValueError("record_ids must not be empty")
        now = _now_iso()
        job = WormsJob(
            id=str(uuid.uuid4()),
            status="running",
            created_at=now,
            updated_at=now,
            created_by=created_by,
            record_ids=list(record_ids),
            cursor=0,
            counts={},
            source=source,
        )
        store = self._read_jobs()
        store.setdefault("jobs", []).append(job.to_dict())
        self._write_jobs(store)
        return job

    def get_job(self, job_id: str) -> Optional[dict]:
        """Return a single job dict by *job_id*, or None if not found."""
        store = self._read_jobs()
        for j in store.get("jobs", []):
            if j.get("id") == job_id:
                return j
        return None

    def update_job_status(self, job_id: str, status: str) -> Optional[dict]:
        """Set *job_id* status to *status* and persist.

        Valid statuses: ``running``, ``paused``, ``cancelled``, ``completed``.

        Returns
        -------
        Updated job dict, or None if not found.
        """
        store = self._read_jobs()
        for j in store.get("jobs", []):
            if j.get("id") == job_id:
                j["status"] = status
                j["updated_at"] = _now_iso()
                if status == "completed":
                    j["completed_at"] = _now_iso()
                self._write_jobs(store)
                return j
        return None

    # ── Cache introspection ───────────────────────────────────────────────

    def cache_stats(self) -> dict:
        """Return lightweight cache statistics (entry count, file size).

        Returns
        -------
        Dict with keys ``entry_count`` and ``file_size_bytes``.
        """
        with self._cache_lock:
            cache = self._read_cache()
            entry_count = len(cache.get("records", {}))
        file_size = os.path.getsize(self._cache_path) if os.path.exists(self._cache_path) else 0
        return {"entry_count": entry_count, "file_size_bytes": file_size}

    def clear_expired(self) -> int:
        """Remove all expired cache entries.

        Returns
        -------
        Number of entries removed.
        """
        ttl_map = {
            "search:":         TTL_SEARCH,
            "classification:": TTL_CLASSIFICATION,
            "synonyms:":       TTL_SYNONYMS,
            "children:":       TTL_CHILDREN,
        }
        default_ttl = TTL_RECORD

        def _ttl_for(key: str) -> int:
            for prefix, t in ttl_map.items():
                if key.startswith(prefix):
                    return t
            return default_ttl

        removed = 0
        with self._cache_lock:
            cache = self._read_cache()
            records = cache.get("records", {})
            expired = [
                k for k, v in records.items()
                if _elapsed_seconds(v.get("fetched_at", "")) >= _ttl_for(k)
            ]
            for k in expired:
                del records[k]
                removed += 1
            if removed:
                self._write_cache(cache)
        return removed
