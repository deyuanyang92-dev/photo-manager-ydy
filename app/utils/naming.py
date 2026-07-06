"""naming.py — Specimen UID / date-segment derivation + 7-segment parser.

Mirrors:
  - db-utils.js:158-165 (specimenDateSeg)
  - upsertSpecimen uid assembly (lines 121-122)
  - naming-validator.js: parseTiffBasename, suggestedTiffName,
    validateTiffBasename, specimenDateSegment

7-segment format:  地区-样地-站位-物种编号-[序号-]保存方式-日期段
  Full result ID:   FJ-XM-B2-DLC001-1-T95E-20260601
  UniqueId:         FJ-XM-B2-DLC001-T95E-20260601        (no seq)
  Legacy v002:      FJ-XM-DLC001-T95E-20260601           (no station)
"""

import re
from dataclasses import dataclass, field
from typing import Optional, Union

from app.services.naming_field_catalog import (
    field_label,
    normalize_naming_components as normalize_catalog_components,
)
from app.utils.path_utils import equivalent_paths


def specimen_date_seg(collection_date: Optional[str], photo_date: Optional[str]) -> str:
    """Derive the date segment of a specimen UID.

    Mirrors db-utils.js::specimenDateSeg exactly:
      c = collection_date stripped of non-digits, first 8 chars
      p = photo_date     stripped of non-digits, first 8 chars
      - if not c -> return p
      - if not p or c == p -> return c
      - if same year (first 4 digits) -> c + "-" + p[4:]
      - else -> c + "-" + p
    """
    c = re.sub(r"\D", "", str(collection_date or ""))[:8]
    p = re.sub(r"\D", "", str(photo_date or ""))[:8]
    if not c:
        return p
    if not p or c == p:
        return c
    if c[:4] == p[:4]:
        return c + "-" + p[4:]
    return c + "-" + p


def coalesce_specimen_dates(
    collection_date: Optional[str],
    photo_date: Optional[str],
) -> tuple[str, str]:
    """When only one date is set, treat collection and photo as the same day."""
    c = str(collection_date or "").strip()
    p = str(photo_date or "").strip()
    if c and not p:
        return c, c
    if p and not c:
        return p, p
    return c, p


def derive_uid(sp: dict) -> str:
    """Derive the canonical UID for a specimen dict.

    Mirrors db-utils.js upsertSpecimen uid assembly (line 121-122):
      [province, site, station, id, storage, date_seg]
      filtered of falsy values, joined with "-".

    Missing station causes automatic degradation (one fewer segment).
    """
    date_seg = specimen_date_seg(sp.get("collectionDate"), sp.get("photoDate"))
    parts = [
        sp.get("province"),
        sp.get("site"),
        sp.get("station"),
        sp.get("id"),
        sp.get("storage"),
        date_seg,
    ]
    return normalize_uid("-".join(str(p) for p in parts if p))


@dataclass(frozen=True)
class SpeciesSequenceSummary:
    """Current numbering state for one species code prefix.

    The prefix is case-insensitive and displayed uppercase.  Gaps are reported
    for human review, but the default next id never reuses a gap.
    """

    prefix: str
    used_numbers: tuple[int, ...] = field(default_factory=tuple)
    max_number: int = 0
    next_number: int = 0
    next_id: str = ""
    width: int = 3
    gaps: list[int] = field(default_factory=list)


_SPECIES_CODE_RE = re.compile(r"^\s*([A-Za-z]+)(\d*)\s*$")


def normalize_uid(value: Optional[str]) -> str:
    """Return the canonical UID spelling used by UI/storage paths.

    UID segments are codes, not prose. Uppercasing the complete UID keeps
    province/site/station/species/storage consistent while leaving digits and
    Chinese text unchanged.
    """
    return str(value or "").strip().upper()


def species_code_parts(value: Optional[str]) -> tuple[str, Optional[int], int]:
    """Return (uppercase prefix, numeric suffix, suffix width) for ``DLC003``.

    ``DLC`` returns ``("DLC", None, 3)`` so suggestions still use three digits.
    Values that do not begin with letters return an empty prefix.
    """
    m = _SPECIES_CODE_RE.match(str(value or ""))
    if not m:
        return "", None, 3
    prefix = m.group(1).upper()
    digits = m.group(2) or ""
    if not digits:
        return prefix, None, 3
    return prefix, int(digits), max(3, len(digits))


def _species_id_from_uid(uid: Optional[str]) -> str:
    parsed = parse_uid(uid)
    if parsed:
        return str(parsed.get("speciesId") or "")
    return ""


def _iter_species_ids(conn, project_dir: Optional[str] = None):
    """Yield species ids from local project tables, tolerating old schemas."""
    try:
        if project_dir:
            project_dirs = equivalent_paths(project_dir)
            owner_filter = ",".join("?" * len(project_dirs)) or "?"
            rows = conn.execute(
                f"""
                SELECT id, uid FROM specimens
                WHERE owner_project_dir IS NULL
                   OR owner_project_dir IN ({owner_filter})
                """,
                project_dirs or [project_dir],
            ).fetchall()
        else:
            rows = conn.execute("SELECT id, uid FROM specimens").fetchall()
        for row in rows:
            sid = row["id"] if hasattr(row, "keys") else row[0]
            uid = row["uid"] if hasattr(row, "keys") else row[1]
            if sid:
                yield str(sid)
            from_uid = _species_id_from_uid(uid)
            if from_uid:
                yield from_uid
    except Exception:
        import logging
        logging.getLogger(__name__).debug("_iter_species_ids: specimens 查询失败", exc_info=True)

    for table in ("tasks", "grouping"):
        try:
            rows = conn.execute(f"SELECT uid FROM {table}").fetchall()
        except Exception:
            continue
        for row in rows:
            uid = row["uid"] if hasattr(row, "keys") else row[0]
            sid = _species_id_from_uid(uid)
            if sid:
                yield sid


def species_sequence_summary(
    conn,
    species_text: Optional[str],
    *,
    project_dir: Optional[str] = None,
) -> SpeciesSequenceSummary:
    """Summarise used numbering for the typed species code prefix.

    Examples:
      existing DLC001, DLC003 + input "dlc" -> next_id "DLC004", gaps [2]
      existing DLC003 + input "DLC003"     -> next_id "DLC004"
    """
    prefix, _, typed_width = species_code_parts(species_text)
    if not prefix:
        return SpeciesSequenceSummary(prefix="")

    used: set[int] = set()
    width = typed_width
    for sid in _iter_species_ids(conn, project_dir=project_dir):
        sid_prefix, number, sid_width = species_code_parts(sid)
        if sid_prefix != prefix or number is None:
            continue
        used.add(number)
        width = max(width, sid_width)

    if not used:
        return SpeciesSequenceSummary(
            prefix=prefix,
            max_number=0,
            next_number=1,
            next_id=f"{prefix}{1:0{width}d}",
            width=width,
        )

    used_sorted = tuple(sorted(used))
    max_number = used_sorted[-1]
    gaps = [n for n in range(1, max_number) if n not in used]
    next_number = max_number + 1
    return SpeciesSequenceSummary(
        prefix=prefix,
        used_numbers=used_sorted,
        max_number=max_number,
        next_number=next_number,
        next_id=f"{prefix}{next_number:0{width}d}",
        width=width,
        gaps=gaps,
    )


# ── Regex patterns for UID parsing ────────────────────────────────────────────
#
# Date segment: 8 digits optionally followed by -4digits (same year) or -8digits (cross year)
# Examples: 20260601  /  20260506-0508  /  20250601-20260601
_DATE_SEG_RE = r"(\d{8}(?:-\d{4}|-\d{8})?)"
_DATE_SEG_FULL_RE = re.compile(r"^" + _DATE_SEG_RE + r"$")
_LEGACY_SHORT_DATE_RE = re.compile(r"^\d{6}$")

# Storage code: alphanumeric (e.g. T95E, RD75E, D70E)
_STORAGE_RE = r"([A-Za-z0-9]+)"

# Species ID: alphanumeric (e.g. DLC001)
_SPECIES_ID_RE = r"([A-Za-z0-9]+)"

# Station: alphanumeric (e.g. B2, S3)
_STATION_RE = r"([A-Za-z0-9]+)"

# Site: one or more non-dash chars (allows Chinese, alphanumeric)
_SITE_RE = r"([^-]+)"

# Province: one or more non-dash chars (allows Chinese)
_PROVINCE_RE = r"([^-]+)"

# Sequence number: one or more digits
_SEQ_RE = r"(\d+)"

# Full result-ID (7 segments, with numeric seq at position 4):
# province-site-station-speciesId-seq-storage-dateSegment
_FULL_RE = re.compile(
    r"^" + _PROVINCE_RE + r"-" + _SITE_RE + r"-" + _STATION_RE + r"-"
    + _SPECIES_ID_RE + r"-" + _SEQ_RE + r"-" + _STORAGE_RE + r"-" + _DATE_SEG_RE + r"$"
)

# UniqueId (6 segments, no seq):
# province-site-station-speciesId-storage-dateSegment
_UNIQUE_RE = re.compile(
    r"^" + _PROVINCE_RE + r"-" + _SITE_RE + r"-" + _STATION_RE + r"-"
    + _SPECIES_ID_RE + r"-" + _STORAGE_RE + r"-" + _DATE_SEG_RE + r"$"
)

# Legacy v002 (5 segments, no station):
# province-site-speciesId-storage-dateSegment
_LEGACY_RE = re.compile(
    r"^" + _PROVINCE_RE + r"-" + _SITE_RE + r"-"
    + _SPECIES_ID_RE + r"-" + _STORAGE_RE + r"-" + _DATE_SEG_RE + r"$"
)

_SPECIES_ID_FULL_RE = re.compile(r"^[A-Za-z]+\d+$")
_STORAGE_CODE_FULL_RE = re.compile(r"^[A-Za-z][A-Za-z0-9]*$", re.IGNORECASE)


def _valid_parsed_species_id(value: str) -> bool:
    """Species codes are voucher prefixes with a numeric suffix, e.g. DLC001."""
    return bool(_SPECIES_ID_FULL_RE.fullmatch(str(value or "")))


def _valid_parsed_storage(value: str) -> bool:
    """Storage codes are preservation codes; pure sequence/date tokens are invalid."""
    return bool(_STORAGE_CODE_FULL_RE.fullmatch(str(value or "")))


def parse_uid(uid: Optional[str]) -> Optional[dict]:
    """Parse a specimen UID into its component fields.

    Supports three formats (tried in order):
      1. Full result-ID with sequence: FJ-XM-B2-DLC001-1-T95E-20260601
      2. UniqueId without sequence:    FJ-XM-B2-DLC001-T95E-20260601
      3. Legacy v002 (no station):     FJ-XM-DLC001-T95E-20260601

    Returns None if none of the patterns match.

    Oracle: naming-validator.js::parseTiffBasename (adapted for UID strings).
    """
    if not uid:
        return None
    s = str(uid).strip()

    # 1. Full result-ID (province-site-station-speciesId-seq-storage-date)
    m = _FULL_RE.match(s)
    if m:
        if not _valid_parsed_species_id(m.group(4)):
            return None
        if not _valid_parsed_storage(m.group(6)):
            return None
        return {
            "province": m.group(1),
            "site": m.group(2),
            "station": m.group(3),
            "speciesId": m.group(4),
            "resultSequence": m.group(5),
            "storage": m.group(6),
            "dateSegment": m.group(7),
            "format": "result_id",
        }

    # 2. UniqueId (province-site-station-speciesId-storage-date)
    m = _UNIQUE_RE.match(s)
    if m:
        if not _valid_parsed_species_id(m.group(4)):
            return None
        if not _valid_parsed_storage(m.group(5)):
            return None
        return {
            "province": m.group(1),
            "site": m.group(2),
            "station": m.group(3),
            "speciesId": m.group(4),
            "resultSequence": None,
            "storage": m.group(5),
            "dateSegment": m.group(6),
            "format": "unique_id",
        }

    # 3. Legacy v002 (province-site-speciesId-storage-date, no station)
    m = _LEGACY_RE.match(s)
    if m:
        if not _valid_parsed_species_id(m.group(3)):
            return None
        if not _valid_parsed_storage(m.group(4)):
            return None
        return {
            "province": m.group(1),
            "site": m.group(2),
            "station": None,
            "speciesId": m.group(3),
            "resultSequence": None,
            "storage": m.group(4),
            "dateSegment": m.group(5),
            "format": "legacy",
        }

    return None


def build_uid(
    *,
    province: Optional[str],
    site: Optional[str],
    station: Optional[str],
    species_id: Optional[str],
    storage: Optional[str],
    date_seg: Optional[str],
) -> str:
    """Assemble a uniqueId (no sequence) from named components.

    Missing / falsy fields are omitted (no double-dash).
    """
    parts = [province, site, station, species_id, storage, date_seg]
    return normalize_uid("-".join(str(p) for p in parts if p))


def build_result_id(
    *,
    province: Optional[str],
    site: Optional[str],
    station: Optional[str],
    species_id: Optional[str],
    storage: Optional[str],
    date_seg: Optional[str],
    seq: Union[int, str],
) -> str:
    """Assemble a full result-ID (with sequence at index 4) from named components.

    Format: province-site-station-speciesId-seq-storage-dateSeg
    """
    parts = [province, site, station, species_id, str(seq), storage, date_seg]
    return normalize_uid("-".join(str(p) for p in parts if p))


def extract_unique_id(result_id: str) -> str:
    """Remove the numeric sequence segment from a result-ID to yield a uniqueId.

    The sequence sits at segment index 4 (0-based) — after speciesId.
    If the input does not have a numeric segment at that position, return as-is.

    Oracle: naming-validator.js — "序号在 index 4=物种编号后"
    Examples:
      FJ-XM-B2-DLC001-1-T95E-20260601  → FJ-XM-B2-DLC001-T95E-20260601
      FJ-XM-B2-DLC001-T95E-20260601    → FJ-XM-B2-DLC001-T95E-20260601  (unchanged)
    """
    if not result_id:
        return result_id
    parsed = parse_uid(result_id)
    if parsed and parsed.get("resultSequence") is not None:
        # Rebuild without sequence
        return build_uid(
            province=parsed["province"],
            site=parsed["site"],
            station=parsed["station"],
            species_id=parsed["speciesId"],
            storage=parsed["storage"],
            date_seg=parsed["dateSegment"],
        )
    return result_id


def validate_uid(uid: Optional[str]) -> bool:
    """Return True if *uid* matches any known valid format.

    Accepts: full result-ID, uniqueId, or legacy v002 (no station).
    Oracle: naming-validator.js::validateTiffBasename (adapted for UID strings).
    """
    return parse_uid(uid) is not None


def suggested_tiff_name(
    sp: Optional[dict],
    result_sequence: Optional[Union[int, str]] = None,
) -> Optional[str]:
    """Return the suggested TIFF filename for a specimen record.

    Format: province-site-station-id-seq-storage-dateSeg.tif
    Default seq = "1".

    Oracle: naming-validator.js::suggestedTiffName (lines 68-75).
    Returns None if sp is None.
    """
    if sp is None:
        return None
    seq = str(result_sequence) if result_sequence is not None else "1"
    date_seg = specimen_date_seg(sp.get("collectionDate"), sp.get("photoDate"))
    parts = [
        sp.get("province"),
        sp.get("site"),
        sp.get("station"),
        sp.get("id"),
        seq,
        sp.get("storage"),
        date_seg,
    ]
    base = "-".join(str(p) for p in parts if p)
    return base + ".tif"


# ── Project-configured naming rules (mirrors naming_panel._build_configured_uid) ─


def safe_uid_segment(value: Optional[str]) -> str:
    text = str(value or "").strip()
    text = re.sub(r"\s+", "_", text)
    return text.replace("-", "_")


_CODE_COMPONENT_KEYS = {"province", "site", "station", "species_id", "storage"}


def _configured_segment_value(key: str, raw_val: Optional[str]) -> str:
    val = str(raw_val or "").strip() if key == "date_seg" else safe_uid_segment(raw_val)
    if key in _CODE_COMPONENT_KEYS:
        return normalize_uid(val)
    return val


def normalize_naming_components(components: Optional[list]) -> list[str]:
    return normalize_catalog_components(components)


def component_values_from_specimen(sp: dict) -> dict[str, str]:
    collection_date, photo_date = coalesce_specimen_dates(
        sp.get("collectionDate") or sp.get("collection_date"),
        sp.get("photoDate") or sp.get("photo_date"),
    )
    date_seg = specimen_date_seg(
        collection_date or None,
        photo_date or None,
    )
    values = {
        "province": str(sp.get("province") or "").strip(),
        "site": str(sp.get("site") or "").strip(),
        "station": str(sp.get("station") or "").strip(),
        "species_id": str(sp.get("id") or sp.get("species_id") or "").strip(),
        "storage": str(sp.get("storage") or "").strip(),
        "collection_date": collection_date,
        "photo_date": photo_date,
        "date_seg": date_seg,
        "habitat": str(sp.get("habitat") or "").strip(),
        "collector": str(sp.get("collector") or "").strip(),
        "photographer": str(sp.get("photographer") or "").strip(),
        "identifier": str(sp.get("identifier") or "").strip(),
        "geo_area": str(sp.get("geo_area") or sp.get("geoArea") or "").strip(),
        "taxon_group": str(sp.get("taxonGroup") or sp.get("taxon_group") or "").strip(),
        "order_name": str(sp.get("order") or sp.get("order_name") or "").strip(),
        "family": str(sp.get("family") or "").strip(),
        "genus": str(sp.get("genus") or "").strip(),
        "scientific_name": str(
            sp.get("scientificName") or sp.get("scientific_name") or ""
        ).strip(),
        "scientific_name_cn": str(
            sp.get("scientificNameCn") or sp.get("scientific_name_cn") or ""
        ).strip(),
        "notes": str(sp.get("notes") or "").strip(),
        "photo_notes": str(
            sp.get("photoNotes") or sp.get("photo_notes") or ""
        ).strip(),
    }
    for key, value in sp.items():
        text_key = str(key or "").strip()
        if text_key and text_key not in values:
            values[text_key] = str(value or "").strip()
    return values


def _values_from_parsed_uid(parsed: dict) -> dict[str, str]:
    return {
        "province": str(parsed.get("province") or ""),
        "site": str(parsed.get("site") or ""),
        "station": str(parsed.get("station") or ""),
        "species_id": str(parsed.get("speciesId") or ""),
        "storage": str(parsed.get("storage") or ""),
        "date_seg": str(parsed.get("dateSegment") or ""),
    }


def build_configured_result_id(
    components: list[str],
    values: dict[str, str],
    *,
    seq: int | None = None,
) -> str:
    """Assemble a result basename using project naming-rule component order."""
    comp_list = normalize_naming_components(components)
    parts: list[str] = []
    for key in comp_list:
        raw_val = values.get(key, "")
        val = _configured_segment_value(key, raw_val)
        if not val:
            continue
        if seq is not None and key == "storage":
            parts.append(str(seq))
        parts.append(val)
    if seq is not None and "storage" not in comp_list:
        insert_at = min(4, len(parts))
        parts.insert(insert_at, str(seq))
    return "-".join(parts)


def build_configured_uid(components: list[str], values: dict[str, str]) -> str:
    return build_configured_result_id(components, values, seq=None)


def _post_date_component_keys(comp_list: list[str]) -> list[str]:
    if "date_seg" not in comp_list:
        return []
    date_idx = comp_list.index("date_seg")
    return comp_list[date_idx + 1:]


def _extract_date_seg_from_parts(parts: list[str]) -> Optional[str]:
    """Remove and return the date segment (1 or 2 dash-parts) from *parts* tail."""
    if not parts:
        return None
    if len(parts) >= 2:
        two_part = f"{parts[-2]}-{parts[-1]}"
        if _DATE_SEG_FULL_RE.fullmatch(two_part):
            parts.pop()
            parts.pop()
            return two_part
    one_part = parts[-1]
    if _DATE_SEG_FULL_RE.fullmatch(one_part):
        return parts.pop()
    if _LEGACY_SHORT_DATE_RE.fullmatch(one_part):
        parts.pop()
        return "20" + one_part
    return None


def naming_rules_summary(components: list[str]) -> str:
    comp_list = normalize_naming_components(components)
    ordered = "-".join(field_label(key, short=True) for key in comp_list)
    if "storage" in comp_list:
        seq_hint = "序号在保存方式前"
    else:
        seq_hint = "序号按项目规则插入"
    return (
        f"项目命名规则：{ordered}（{seq_hint}；"
        "日期段=采集/拍摄日期，只填其一视为同一天）"
    )


def _configured_seq_index(components: list[str]) -> int:
    comp_list = normalize_naming_components(components)
    dummy: dict[str, str] = {}
    for key in comp_list:
        if key == "date_seg":
            dummy[key] = "20260101"
        elif key in _post_date_component_keys(comp_list):
            dummy[key] = "Z"
        else:
            dummy[key] = "A"
    built = build_configured_result_id(comp_list, dummy, seq=0)
    built_parts = built.split("-")
    post_n = len(_post_date_component_keys(comp_list))
    if post_n:
        built_parts = built_parts[:-post_n]
    return built_parts.index("0")


def parse_configured_result_stem(
    stem: str,
    components: list[str],
) -> tuple[str, int] | None:
    """Parse (uid, sequence) from a basename using configured component order."""
    comp_list = normalize_naming_components(components)
    if not comp_list:
        return None

    parts = stem.split("-")
    if not parts:
        return None

    values: dict[str, str] = {}
    post_keys = _post_date_component_keys(comp_list)

    for key in reversed(post_keys):
        if not parts:
            return None
        values[key] = parts.pop()

    if "date_seg" in comp_list:
        if not parts:
            return None
        date_seg = _extract_date_seg_from_parts(parts)
        if not date_seg:
            return None
        values["date_seg"] = date_seg

    if not parts and "date_seg" not in values:
        return None

    try:
        seq_idx = _configured_seq_index(comp_list)
    except ValueError:
        return None

    if seq_idx >= len(parts):
        return None
    seq_part = parts[seq_idx]
    if not seq_part.isdigit():
        return None
    seq = int(seq_part)

    parts_no_seq = parts[:seq_idx] + parts[seq_idx + 1:]
    pre_keys = [
        key for key in comp_list if key != "date_seg" and key not in post_keys
    ]
    pi = 0
    for key in pre_keys:
        if pi >= len(parts_no_seq):
            return None
        values[key] = parts_no_seq[pi]
        pi += 1
    if pi != len(parts_no_seq):
        return None

    rebuilt = build_configured_result_id(comp_list, values, seq=seq)
    if normalize_uid(rebuilt) != normalize_uid(stem):
        return None
    return build_configured_uid(comp_list, values), seq


def _is_inline_descriptor_segment(seg: str) -> bool:
    """Human-readable label embedded before the date (legacy folder names)."""
    if not seg:
        return False
    if any("\u4e00" <= c <= "\u9fff" for c in seg):
        return True
    if len(seg) > 12 and not re.fullmatch(r"[A-Za-z0-9]+", seg):
        return True
    return False


def _peel_inline_descriptors_before_date(
    parts: list[str],
) -> tuple[list[str], Optional[str], list[str]]:
    """Split *parts* into UID core, trailing date, and inline Chinese/descriptive labels."""
    buf = list(parts)
    date_seg = _extract_date_seg_from_parts(buf)
    if not date_seg:
        return parts, None, []
    inline: list[str] = []
    while buf and _is_inline_descriptor_segment(buf[-1]):
        inline.insert(0, buf.pop())
    return buf, date_seg, inline


def _has_storage_before_legacy_short_date(core: list[str]) -> bool:
    """True for legacy cores like ``...-2-R-260618``.

    The six-digit token is a short date/remark, not the storage code.  Keep it
    only as a legacy inline label after parsing the real ``R`` storage segment.
    """
    if len(core) < 2 or not _LEGACY_SHORT_DATE_RE.fullmatch(core[-1]):
        return False
    return bool(re.search(r"[A-Za-z]", core[-2] or ""))


def _append_trailing_date_for_candidate(core: list[str], date_seg: str) -> list[str]:
    out = list(core)
    if not out or not _DATE_SEG_FULL_RE.fullmatch(out[-1]):
        out.append(date_seg)
    return out


def _parseable_stem_candidates(stem: str) -> list[str]:
    """Stems to try when matching project naming rules (handles inline labels)."""
    parts = stem.split("-")
    candidates = [stem]
    if parts and _LEGACY_SHORT_DATE_RE.fullmatch(parts[-1]):
        candidates.insert(0, "-".join(parts[:-1] + ["20" + parts[-1]]))
    buf, date_seg, inline = _peel_inline_descriptors_before_date(list(parts))
    if date_seg and inline:
        # Legacy names often repeat the date after inline Chinese descriptors:
        #   CORE-DATE-地点-物种-DATE
        # If the remaining core already ends with a date, do not append the
        # peeled trailing date again, or the parsed UID becomes DATE-DATE.
        core = list(buf)
        candidates.insert(0, "-".join(_append_trailing_date_for_candidate(core, date_seg)))
        if _has_storage_before_legacy_short_date(core):
            candidates.insert(
                0,
                "-".join(_append_trailing_date_for_candidate(core[:-1], date_seg)),
            )
    return candidates


def _parse_configured_values(
    stem: str,
    components: list[str],
) -> tuple[dict[str, str], int] | None:
    """Return (field_values, sequence) parsed from a configured result basename."""
    comp_list = normalize_naming_components(components)
    parts = stem.split("-")
    if not parts:
        return None

    values: dict[str, str] = {}
    post_keys = _post_date_component_keys(comp_list)

    for key in reversed(post_keys):
        if not parts:
            return None
        values[key] = parts.pop()

    if "date_seg" in comp_list:
        if not parts:
            return None
        date_seg = _extract_date_seg_from_parts(parts)
        if not date_seg:
            return None
        values["date_seg"] = date_seg

    if not parts and "date_seg" not in values:
        return None

    try:
        seq_idx = _configured_seq_index(comp_list)
    except ValueError:
        return None

    if seq_idx >= len(parts):
        return None
    seq_part = parts[seq_idx]
    if not seq_part.isdigit():
        return None
    seq = int(seq_part)

    parts_no_seq = parts[:seq_idx] + parts[seq_idx + 1:]
    pre_keys = [
        key for key in comp_list if key != "date_seg" and key not in post_keys
    ]
    pi = 0
    for key in pre_keys:
        if pi >= len(parts_no_seq):
            return None
        values[key] = parts_no_seq[pi]
        pi += 1
    if pi != len(parts_no_seq):
        return None

    rebuilt = build_configured_result_id(comp_list, values, seq=seq)
    if normalize_uid(rebuilt) != normalize_uid(stem):
        return None
    return values, seq


def _date_seg_to_collection_photo(date_seg: str) -> tuple[str, str]:
    """Best-effort split of a date segment into collection / photo dates."""
    seg = str(date_seg or "").strip()
    if not seg:
        return "", ""
    if re.fullmatch(r"\d{8}-\d{4}", seg):
        col, tail = seg.split("-", 1)
        return col, col[:4] + tail
    if re.fullmatch(r"\d{8}-\d{8}", seg):
        col, photo = seg.split("-", 1)
        return col, photo
    if re.fullmatch(r"\d{8}", seg):
        return seg, seg
    return seg, seg


def _inline_labels_to_metadata(labels: list[str]) -> dict[str, str]:
    """Deprecated heuristic map — prefer ``legacy_filename_photo_notes``."""
    return {}


def legacy_filename_photo_notes(
    inline_labels: list[str] | tuple[str, ...],
    *,
    date_suffix: str = "",
    source_name: str = "",
) -> str:
    """Archive unmapped filename segments into 拍照备注 (legacy migration).

    Format: ``广西防城港-白龙尾-独齿沙蚕-20260618`` — dash-separated, no bracket tags.
    """
    _ = source_name  # kept for call-site compat; no longer written into notes
    labels = [str(x).strip() for x in inline_labels if str(x).strip()]
    if not labels:
        return ""
    date_suffix = re.sub(r"\D", "", str(date_suffix or ""))[:8]
    if date_suffix and (not labels or labels[-1] != date_suffix):
        labels.append(date_suffix)
    return "-".join(labels)


@dataclass(frozen=True)
class ParsedTiffRecognition:
    uid: str
    sequence: int
    core_stem: str
    field_values: dict[str, str]
    inline_labels: tuple[str, ...] = ()
    collection_date: str = ""
    photo_date: str = ""


def recognize_tiff_filename(
    stem: str,
    components: list[str],
) -> Optional[ParsedTiffRecognition]:
    """Parse uid/sequence + naming fields from a legacy or standard TIFF basename."""
    detail, matched = parse_tiff_result_detail_for_components(stem, components)
    if detail is None:
        return None
    parsed_values = _parse_configured_values(detail.core_stem, matched)
    if parsed_values is None:
        values = _values_from_parsed_uid(parse_uid(detail.core_stem) or {})
        seq = detail.sequence
    else:
        values, seq = parsed_values
    buf, _, inline = _peel_inline_descriptors_before_date(stem.split("-"))
    inline = list(inline)
    if buf and _LEGACY_SHORT_DATE_RE.fullmatch(buf[-1]):
        core_parts = detail.core_stem.split("-")
        if buf[-1] not in core_parts:
            inline.insert(0, buf[-1])
    date_seg = values.get("date_seg") or ""
    col, photo = _date_seg_to_collection_photo(date_seg)
    return ParsedTiffRecognition(
        uid=detail.uid,
        sequence=seq,
        core_stem=detail.core_stem,
        field_values=values,
        inline_labels=tuple(inline),
        collection_date=col,
        photo_date=photo,
    )


def tiff_stem_is_recognizable(stem: str, components: list[str]) -> bool:
    """True when the basename matches project naming rules (extra suffixes allowed)."""
    if validate_uid(stem):
        return True
    return parse_tiff_result_detail(stem, components) is not None


def tiff_stem_fully_conforms(stem: str, components: list[str]) -> bool:
    """True when the basename exactly matches naming rules — no legacy extras."""
    if validate_uid(stem):
        return True
    detail, _matched = parse_tiff_result_detail_for_components(stem, components)
    if detail is None:
        return False
    return normalize_uid(detail.core_stem) == normalize_uid(stem)


def tiff_stem_needs_rename_for_organize(
    stem: str,
    components: list[str],
    *,
    panel_uid: str,
    panel_storage: str,
) -> bool:
    """True when organize should prompt for TIFF rename.

    Legacy remark suffixes (Chinese labels, etc.) are allowed when the parsed
    specimen UID matches the panel. Only prompt when the basename is
    unrecognizable, the UID disagrees, or a panel storage code must be patched
    into the filename.
    """
    if validate_uid(stem):
        return False
    rec = recognize_tiff_filename(stem, components)
    if rec is None:
        return True
    comp_list = normalize_naming_components(components)
    if panel_uid:
        rec_base = build_configured_uid(
            comp_list, {**rec.field_values, "storage": ""}
        )
        parsed_panel = parse_uid(panel_uid) or {}
        panel_values = _values_from_parsed_uid(parsed_panel)
        panel_base = build_configured_uid(
            comp_list, {**panel_values, "storage": ""}
        )
        panel_cmp = normalize_uid(panel_base or panel_uid)
        if normalize_uid(rec_base) != panel_cmp:
            return True
    storage = str(panel_storage or "").strip()
    if not storage:
        return False
    file_storage = str(rec.field_values.get("storage") or "").strip()
    return normalize_uid(file_storage) != normalize_uid(storage)


def suggest_tiff_filename_preserve_legacy(
    stem: str,
    components: list[str],
    values: dict[str, str],
    *,
    seq: int | None = None,
) -> str:
    """Build a TIFF basename: configured core + legacy orphan segments + inline labels.

    When a legacy file used a date-like token in the storage slot (``260618``),
    inserting the real storage code (``D79``) keeps that token as a middle remark
    segment instead of dropping descriptive suffixes.
    """
    comp_list = normalize_naming_components(components)
    rec = recognize_tiff_filename(stem, components)
    use_seq = int(seq if seq is not None else (rec.sequence if rec else 1))

    if rec is None:
        merged = dict(values)
        if not merged.get("date_seg"):
            merged["date_seg"] = specimen_date_seg(
                merged.get("collection_date"),
                merged.get("photo_date"),
            )
        return build_configured_result_id(comp_list, merged, seq=use_seq)

    merged = dict(rec.field_values)
    for key in (
        "province",
        "site",
        "station",
        "species_id",
        "storage",
        "date_seg",
        "collection_date",
        "photo_date",
    ):
        val = str(values.get(key) or "").strip()
        if val:
            merged[key] = val
    if not str(merged.get("date_seg") or "").strip():
        merged["date_seg"] = specimen_date_seg(
            merged.get("collection_date"),
            merged.get("photo_date"),
        )

    date_seg = str(merged.get("date_seg") or "").strip()
    if not date_seg:
        _, peeled_date, _ = _peel_inline_descriptors_before_date(stem.split("-"))
        date_seg = str(peeled_date or "").strip()

    prefix_values = dict(merged)
    prefix_values["date_seg"] = ""
    prefix = build_configured_result_id(comp_list, prefix_values, seq=use_seq)

    old_storage = str(rec.field_values.get("storage") or "").strip()
    new_storage = str(merged.get("storage") or "").strip()
    orphan_segments: list[str] = []
    if old_storage and old_storage not in (prefix.split("-") if prefix else []):
        if (
            (not new_storage or normalize_uid(old_storage) != normalize_uid(new_storage))
            and _LEGACY_SHORT_DATE_RE.fullmatch(old_storage)
        ):
            orphan_segments.append(old_storage)

    out_parts: list[str] = []
    if prefix:
        out_parts.extend(prefix.split("-"))
    out_parts.extend(orphan_segments)
    out_parts.extend(rec.inline_labels)
    if date_seg:
        out_parts.append(date_seg)
    return "-".join(out_parts)


@dataclass(frozen=True)
class ParsedTiffStem:
    """Core specimen identity parsed from a TIFF basename.

    Filenames may append arbitrary extra segments after the unique-id core,
    e.g. ``GXHP-SL-DLC001-1-R-20260712-采集人-拍摄人-备注``.
    """

    uid: str
    sequence: int
    core_stem: str
    has_extra_suffix: bool = False


def _naming_component_variants(comp_list: list[str]) -> list[list[str]]:
    """Legacy filenames may omit station and/or storage — try sensible variants."""
    variants: list[list[str]] = []
    seen: set[tuple[str, ...]] = set()

    def add_unique_component_variant(comp: list[str]) -> None:
        key = tuple(comp)
        if comp and key not in seen:
            seen.add(key)
            variants.append(comp)

    no_station = [key for key in comp_list if key != "station"]
    no_storage = [key for key in comp_list if key != "storage"]
    no_both = [key for key in comp_list if key not in ("station", "storage")]

    add_unique_component_variant(no_station)
    add_unique_component_variant(comp_list)
    add_unique_component_variant(no_storage)
    add_unique_component_variant(no_both)
    return variants


def _parse_tiff_result_detail_with_components(
    stem: str,
    comp_list: list[str],
) -> Optional[ParsedTiffStem]:
    for candidate in _parseable_stem_candidates(stem):
        parts = candidate.split("-")
        for end in range(len(parts), 2, -1):
            prefix = "-".join(parts[:end])
            has_extra_suffix = end < len(parts) or candidate != stem

            parsed = parse_uid(prefix)
            if parsed and parsed.get("resultSequence") is not None:
                values = _values_from_parsed_uid(parsed)
                seq = int(parsed["resultSequence"])
                rebuilt = build_configured_result_id(comp_list, values, seq=seq)
                if normalize_uid(rebuilt) == normalize_uid(prefix):
                    return ParsedTiffStem(
                        uid=build_configured_uid(comp_list, values),
                        sequence=seq,
                        core_stem=prefix,
                        has_extra_suffix=has_extra_suffix,
                    )

            core = parse_configured_result_stem(prefix, comp_list)
            if core:
                uid, seq = core
                return ParsedTiffStem(
                    uid=uid,
                    sequence=seq,
                    core_stem=prefix,
                    has_extra_suffix=has_extra_suffix,
                )
    return None


def parse_tiff_result_detail(
    stem: str,
    components: list[str],
) -> Optional[ParsedTiffStem]:
    """Parse specimen uid + sequence; ignore trailing/extra descriptive segments."""
    detail, _matched = parse_tiff_result_detail_for_components(stem, components)
    return detail


def parse_tiff_result_detail_for_components(
    stem: str,
    components: list[str],
) -> tuple[Optional[ParsedTiffStem], list[str]]:
    """Return parsed detail and the component variant that matched."""
    comp_list = normalize_naming_components(components)
    if len(stem.split("-")) < 3:
        return None, comp_list
    for variant in _naming_component_variants(comp_list):
        detail = _parse_tiff_result_detail_with_components(stem, variant)
        if detail is not None:
            return detail, variant
    return None, comp_list


def parse_tiff_result_stem(
    stem: str,
    components: list[str],
) -> tuple[str, int] | None:
    """Return (uid, sequence) when the specimen unique-id core is recognizable."""
    detail = parse_tiff_result_detail(stem, components)
    if detail is None:
        return None
    return detail.uid, detail.sequence
