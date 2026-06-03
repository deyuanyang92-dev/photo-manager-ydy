"""taxonomy_service.py — 4-level taxonomy data service.

Manages the read-only seed library and the user-writable increment.
Mirrors server.js:353-730 taxonomy CRUD logic and app.js matchTaxon/
taxonomyCandidates behaviour.

Levels / fields
---------------
  taxonGroup  → seed field "class",  CN = "classCn"
  order       → seed field "order",  CN = "orderCn"
  family      → seed field "family", CN = "familyCn"
  scientificName (species + genus) → "species"/"genus", CN = "speciesCn"/"genusCn"

Key invariants (never violated):
  - taxonomy_seed.json is NEVER written.  It is opened once, read, and cached.
  - user_taxonomy.json stores only append / updates to user records.
  - Chinese fields (classCn / orderCn / familyCn / speciesCn) are NEVER
    auto-filled by the service; that policy lives in the widget layer.
  - Selecting a parent level never modifies child-level fields.
"""
from __future__ import annotations

import json
import unicodedata
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional


# ── Level metadata ────────────────────────────────────────────────────────────

# Each level:  (sp_key, seed_key, seed_cn_key, cn_sp_key)
# sp_key      = the field name used on the Specimen object (and in the widget)
# seed_key    = the JSON key in seed/user records for the Latin name
# seed_cn_key = the JSON key for the Chinese name in records
# cn_sp_key   = the Specimen field that stores the Chinese counterpart (may be None)
_LEVELS: list[tuple[str, str, str, Optional[str]]] = [
    ("taxonGroup",     "class",   "classCn",  "taxonGroupCn"),
    ("order",          "order",   "orderCn",  "orderCn"),
    ("family",         "family",  "familyCn", "familyCn"),
    ("scientificName", "species", "speciesCn", "scientificNameCn"),
]

# Quick lookup: sp_key → (seed_key, seed_cn_key, cn_sp_key)
_LEVEL_MAP: dict[str, tuple[str, str, Optional[str]]] = {
    sp: (sk, cn, cns) for sp, sk, cn, cns in _LEVELS
}

VALID_SP_KEYS: frozenset[str] = frozenset(sp for sp, *_ in _LEVELS)

# Source badge labels mirror app.js
SOURCE_LABELS: dict[str, str] = {
    "user":  "用户",
    "seed":  "权威",
    "worms": "WoRMS",
    "cross": "跨",
}


# ── Public data class ─────────────────────────────────────────────────────────

class TaxonCandidate:
    """One autocomplete suggestion returned by :py:meth:`TaxonomyService.search`."""

    __slots__ = ("value", "cn", "source", "full")

    def __init__(
        self,
        value: str,
        cn: str,
        source: str,
        full: dict[str, Any],
    ) -> None:
        self.value = value      # Latin name for this level
        self.cn = cn            # Chinese name for this level (may be "")
        self.source = source    # "user" | "seed" | "worms" | "cross"
        self.full = full        # full record dict (for ancestor path info)

    def __repr__(self) -> str:  # pragma: no cover
        return f"TaxonCandidate({self.value!r}, cn={self.cn!r}, src={self.source!r})"


# ── Helper ────────────────────────────────────────────────────────────────────

def _nfkc(s: str) -> str:
    """NFKC-normalise and lower-case for matching (mirrors app.js matchTaxon)."""
    return unicodedata.normalize("NFKC", s).lower()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Service ───────────────────────────────────────────────────────────────────

class TaxonomyService:
    """Read-only seed + user-writable increment taxonomy service.

    Parameters
    ----------
    seed_path:
        Path to the read-only ``taxonomy_seed.json`` file.
    user_path:
        Path to the user-writable ``user_taxonomy.json`` file.
        Created automatically if it does not exist.
    """

    def __init__(self, seed_path: Path, user_path: Path) -> None:
        self._seed_path = Path(seed_path)
        self._user_path = Path(user_path)
        self._seed: list[dict[str, Any]] = []
        self._user: list[dict[str, Any]] = []
        self._loaded = False

    # ── Lazy load ─────────────────────────────────────────────────────

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        self._seed = self._read_seed()
        self._user = self._read_user()
        self._loaded = True

    def _read_seed(self) -> list[dict[str, Any]]:
        """Load seed JSON.  Returns [] if file is absent (test scenarios)."""
        if not self._seed_path.exists():
            return []
        with open(self._seed_path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []

    def _read_user(self) -> list[dict[str, Any]]:
        """Load user JSON.  Returns [] and creates the file if absent."""
        if not self._user_path.exists():
            self._user_path.parent.mkdir(parents=True, exist_ok=True)
            self._user_path.write_text("[]", encoding="utf-8")
            return []
        with open(self._user_path, encoding="utf-8") as f:
            try:
                data = json.load(f)
            except json.JSONDecodeError:
                return []
        return data if isinstance(data, list) else []

    def _save_user(self) -> None:
        """Persist user records to disk.  Never touches seed file."""
        self._user_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self._user_path, "w", encoding="utf-8") as f:
            json.dump(self._user, f, ensure_ascii=False, indent=2)

    def reload(self) -> None:
        """Force re-load from disk (used after external edits)."""
        self._loaded = False
        self._ensure_loaded()

    # ── Candidate building ─────────────────────────────────────────────

    def _candidates_for(
        self,
        sp_key: str,
        context: dict[str, str],
    ) -> list[TaxonCandidate]:
        """Return de-duplicated candidates for *sp_key* constrained by *context*.

        *context* is a dict of already-known fields on the specimen, e.g.
        ``{"taxonGroup": "Polychaeta", "order": "Phyllodocida"}``.

        Mirrors ``taxonomyCandidates(spKey, sp)`` in app.js.
        """
        self._ensure_loaded()
        sk, cn_key, _ = _LEVEL_MAP[sp_key]

        # Build merged list: user first (higher priority), seed second
        merged = self._user + self._seed

        # Determine which ancestor levels are "known" (exist AND appear in merged).
        # Note: context uses sp_key names (taxonGroup/order/family/scientificName),
        # but seed records use different keys (class/order/family/species).
        # _is_known must map sp_key → seed_record_key for the lookup.
        def _is_known(sp_field: str, seed_field: str, cn_field: str) -> bool:
            """sp_field: context key; seed_field: record key; cn_field: record CN key."""
            v = context.get(sp_field, "")
            if not v:
                return False
            return any(
                e.get(seed_field) == v or e.get(cn_field) == v
                for e in merged
            )

        known_class  = _is_known("taxonGroup", "class", "classCn")
        known_order  = _is_known("order", "order", "orderCn")
        known_family = _is_known("family", "family", "familyCn")

        # Also need genus knowledge for scientificName field
        # (genus lives directly on records)
        ctx_genus = context.get("genus", "")
        known_genus = bool(ctx_genus) and any(
            e.get("genus") == ctx_genus or e.get("genusCn") == ctx_genus
            for e in merged
        )

        def _match_val(e: dict, field: str, cn_field: str, val: str) -> bool:
            return e.get(field) == val or (bool(e.get(cn_field)) and e[cn_field] == val)

        seen: set[str] = set()
        out: list[TaxonCandidate] = []

        def _scan(records: list[dict], source: str) -> None:
            for e in records:
                # Ancestor constraints (mirrors app.js taxonomyCandidates filter logic)
                if sp_key != "taxonGroup" and known_class:
                    if not _match_val(e, "class", "classCn", context.get("taxonGroup", "")):
                        continue
                if sp_key in ("family", "genus", "scientificName") and known_order:
                    if not _match_val(e, "order", "orderCn", context.get("order", "")):
                        continue
                if sp_key in ("genus", "scientificName") and known_family:
                    if not _match_val(e, "family", "familyCn", context.get("family", "")):
                        continue
                if sp_key == "scientificName" and ctx_genus:
                    e_genus = e.get("genus") or (e.get("species", "").split()[0] if e.get("species") else "")
                    if e_genus and e_genus != ctx_genus and e.get("genusCn", "") != ctx_genus:
                        continue

                v = e.get(sk, "")
                if not v or v in seen:
                    continue
                seen.add(v)
                cn = e.get(cn_key, "")
                out.append(TaxonCandidate(value=v, cn=cn, source=source, full=e))

        _scan(self._user, "user")
        _scan(self._seed, "seed")

        return out

    # ── Public API ─────────────────────────────────────────────────────

    def search(
        self,
        sp_key: str,
        query: str,
        context: Optional[dict[str, str]] = None,
        max_results: int = 30,
    ) -> list[TaxonCandidate]:
        """Return autocomplete candidates matching *query* for *sp_key*.

        Parameters
        ----------
        sp_key:
            One of ``"taxonGroup"``, ``"order"``, ``"family"``,
            ``"scientificName"``.
        query:
            User-typed text.  Empty → return all constrained candidates.
        context:
            Dict of already-known specimen fields (used for ancestor
            constraint filtering).  Pass ``{}`` or ``None`` for no
            constraint (cross-level fallback mode).
        max_results:
            Maximum number of results to return (default 30, mirror of
            app.js).

        Returns
        -------
        list[TaxonCandidate]
            Ranked by match offset (earliest hit first).  At most
            *max_results* items.
        """
        if sp_key not in VALID_SP_KEYS:
            raise ValueError(f"Invalid sp_key {sp_key!r}. Must be one of {VALID_SP_KEYS}.")

        ctx = context or {}
        cands = self._candidates_for(sp_key, ctx)
        return self._match(query, cands, max_results=max_results)

    def _match(
        self,
        query: str,
        cands: list[TaxonCandidate],
        max_results: int = 30,
    ) -> list[TaxonCandidate]:
        """Score and rank candidates against *query*.

        Mirrors ``matchTaxon(query, cands)`` in app.js:
          - NFKC normalise + lowercase both sides
          - score = earliest indexOf in value or cn
          - return top *max_results* hits
        """
        q = _nfkc(query).strip()
        if not q:
            return list(cands[:max_results])

        hits: list[tuple[int, TaxonCandidate]] = []
        for c in cands:
            v = _nfkc(c.value)
            cn = _nfkc(c.cn)
            pos_v = v.find(q)
            pos_cn = cn.find(q)
            if pos_v < 0 and pos_cn < 0:
                continue
            score = min(
                pos_v if pos_v >= 0 else 10**9,
                pos_cn if pos_cn >= 0 else 10**9,
            )
            hits.append((score, c))
            if len(hits) >= 200:
                break

        hits.sort(key=lambda x: x[0])
        return [c for _, c in hits[:max_results]]

    def learn(self, record: dict[str, Any]) -> dict[str, Any]:
        """Upsert a 4-tuple into user_taxonomy.json.

        Only persists when all four Latin fields are present:
        ``class``, ``order``, ``family``, ``species``.

        Chinese fields (classCn / orderCn / familyCn / speciesCn / genusCn)
        are stored IF provided in *record*; they are never auto-filled
        by this method.

        Mirrors ``commitTaxonValue`` / ``POST /api/taxonomy/learn`` in app.js
        and server.js.

        Parameters
        ----------
        record:
            Dict with at minimum ``class``, ``order``, ``family``,
            ``species``.  Optional Chinese fields and ``genus``.

        Returns
        -------
        dict
            The upserted record (new or updated).
        """
        self._ensure_loaded()

        cls = (record.get("class") or "").strip()
        order = (record.get("order") or "").strip()
        family = (record.get("family") or "").strip()
        species = (record.get("species") or "").strip()

        if not (cls and order and family and species):
            return {}  # incomplete 4-tuple → do nothing

        key = f"{cls}|{order}|{family}|{species}"
        now = _now_iso()

        # Find existing user record
        entry: Optional[dict[str, Any]] = None
        for e in self._user:
            k = f"{e.get('class','')}|{e.get('order','')}|{e.get('family','')}|{e.get('species','')}"
            if k == key:
                entry = e
                break

        if entry is not None:
            entry["useCount"] = (entry.get("useCount") or 1) + 1
            entry["lastUsedAt"] = now
            entry["lastModifiedAt"] = now
            # Update optional fields only if provided
            for opt_field in ("classCn", "orderCn", "familyCn", "speciesCn", "genus", "genusCn"):
                if record.get(opt_field):
                    entry[opt_field] = record[opt_field]
        else:
            entry = {
                "class": cls,
                "order": order,
                "family": family,
                "species": species,
                "useCount": 1,
                "addedAt": now,
                "lastUsedAt": now,
                "lastModifiedAt": now,
                "recordId": "user:" + uuid.uuid4().hex[:16],
            }
            for opt_field in ("classCn", "orderCn", "familyCn", "speciesCn", "genus", "genusCn"):
                if record.get(opt_field):
                    entry[opt_field] = record[opt_field]
            self._user.append(entry)

        self._save_user()
        return dict(entry)

    def update(self, record_id: str, updates: dict[str, Any]) -> Optional[dict[str, Any]]:
        """Update an existing user record by recordId.

        Saves a history snapshot before writing (mirrors server.js:934-943).
        History is capped at 10 entries (oldest dropped when over limit).

        Parameters
        ----------
        record_id:
            The ``recordId`` of the user record to update.
        updates:
            Fields to update.  Key Latin fields (class/order/family/species)
            can be updated.  Chinese fields are updated only if provided.

        Returns
        -------
        dict or None
            Updated record on success, None if not found.
        """
        self._ensure_loaded()

        entry = next(
            (e for e in self._user if e.get("recordId") == record_id), None
        )
        if entry is None:
            return None  # not found or is a seed record

        # ── Record history (mirrors server.js:934-943) ─────────────────
        before = {
            "class":     entry.get("class", ""),
            "classCn":   entry.get("classCn", ""),
            "order":     entry.get("order", ""),
            "orderCn":   entry.get("orderCn", ""),
            "family":    entry.get("family", ""),
            "familyCn":  entry.get("familyCn", ""),
            "genus":     entry.get("genus", ""),
            "genusCn":   entry.get("genusCn", ""),
            "species":   entry.get("species", ""),
            "speciesCn": entry.get("speciesCn", ""),
        }
        history = entry.setdefault("history", [])
        history.append({"at": _now_iso(), "before": before})
        if len(history) > 10:
            history.pop(0)

        # ── Apply updates ──────────────────────────────────────────────
        allowed = {
            "class", "order", "family", "species",
            "classCn", "orderCn", "familyCn", "speciesCn",
            "genus", "genusCn",
        }
        for k, v in updates.items():
            if k in allowed:
                entry[k] = v

        entry["lastModifiedAt"] = _now_iso()
        self._save_user()
        return dict(entry)

    def delete(self, record_id: str) -> bool:
        """Delete a user record by recordId.

        Seed records cannot be deleted.

        Parameters
        ----------
        record_id:
            The ``recordId`` of the user record to delete.

        Returns
        -------
        bool
            True if the record was found and removed, False otherwise.
        """
        self._ensure_loaded()

        original_len = len(self._user)
        self._user = [
            e for e in self._user if e.get("recordId") != record_id
        ]
        if len(self._user) == original_len:
            return False
        self._save_user()
        return True

    def all_records(
        self,
        source_filter: Optional[str] = None,
        page: int = 0,
        page_size: int = 50,
    ) -> tuple[list[dict[str, Any]], int]:
        """Return paginated merged records (user first, then seed).

        Parameters
        ----------
        source_filter:
            If ``"user"``, return only user records.
            If ``"seed"``, return only seed records.
            Otherwise return all.
        page:
            Zero-based page index.
        page_size:
            Number of records per page.

        Returns
        -------
        tuple[list[dict], int]
            (page_records, total_count)
        """
        self._ensure_loaded()

        if source_filter == "user":
            records = list(self._user)
        elif source_filter == "seed":
            records = list(self._seed)
        else:
            records = list(self._user) + list(self._seed)

        total = len(records)
        start = page * page_size
        return records[start : start + page_size], total

    def seed_count(self) -> int:
        """Number of seed records loaded."""
        self._ensure_loaded()
        return len(self._seed)

    def user_count(self) -> int:
        """Number of user records loaded."""
        self._ensure_loaded()
        return len(self._user)
