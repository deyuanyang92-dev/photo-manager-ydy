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
            rows = conn.execute(
                """
                SELECT id, uid FROM specimens
                WHERE owner_project_dir IS NULL OR owner_project_dir = ?
                """,
                (project_dir,),
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
        pass

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


def normalize_naming_components(components: Optional[list]) -> list[str]:
    defaults = [
        "province",
        "site",
        "station",
        "species_id",
        "storage",
        "date_seg",
    ]
    if not components or not isinstance(components, list):
        return list(defaults)
    out = [str(c) for c in components if c and str(c) != "result_seq"]
    return out or list(defaults)


def component_values_from_specimen(sp: dict) -> dict[str, str]:
    collection_date, photo_date = coalesce_specimen_dates(
        sp.get("collectionDate") or sp.get("collection_date"),
        sp.get("photoDate") or sp.get("photo_date"),
    )
    date_seg = specimen_date_seg(
        collection_date or None,
        photo_date or None,
    )
    return {
        "province": str(sp.get("province") or "").strip(),
        "site": str(sp.get("site") or "").strip(),
        "station": str(sp.get("station") or "").strip(),
        "species_id": str(sp.get("id") or sp.get("species_id") or "").strip(),
        "storage": str(sp.get("storage") or "").strip(),
        "collection_date": collection_date,
        "photo_date": photo_date,
        "date_seg": date_seg,
        "collector": str(sp.get("collector") or "").strip(),
        "photographer": str(sp.get("photographer") or "").strip(),
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
        if key == "date_seg":
            val = str(raw_val or "").strip()
        else:
            val = safe_uid_segment(raw_val)
        if not val:
            continue
        if seq is not None and key == "storage":
            parts.append(str(seq))
        parts.append(val)
    if seq is not None and "storage" not in comp_list:
        insert_at = min(4, len(parts))
        parts.insert(insert_at, str(seq))
    return normalize_uid("-".join(parts))


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
    return None


def naming_rules_summary(components: list[str]) -> str:
    labels = {
        "province": "省",
        "site": "样地",
        "station": "站位",
        "species_id": "编号",
        "storage": "保存",
        "date_seg": "日期",
        "collector": "采集人",
        "photographer": "拍摄人",
        "taxon_group": "类群",
        "order_name": "目",
        "family": "科",
        "genus": "属",
        "scientific_name": "学名",
        "scientific_name_cn": "中文名",
        "notes": "备注",
        "photo_notes": "拍照备注",
    }
    comp_list = normalize_naming_components(components)
    ordered = "-".join(labels.get(key, key) for key in comp_list)
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


def parse_tiff_result_detail(
    stem: str,
    components: list[str],
) -> Optional[ParsedTiffStem]:
    """Parse specimen uid + sequence; ignore any trailing extra segments."""
    comp_list = normalize_naming_components(components)
    parts = stem.split("-")
    if len(parts) < 3:
        return None

    for end in range(len(parts), 2, -1):
        prefix = "-".join(parts[:end])
        has_extra_suffix = end < len(parts)

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


def parse_tiff_result_stem(
    stem: str,
    components: list[str],
) -> tuple[str, int] | None:
    """Return (uid, sequence) when the specimen unique-id core is recognizable."""
    detail = parse_tiff_result_detail(stem, components)
    if detail is None:
        return None
    return detail.uid, detail.sequence
