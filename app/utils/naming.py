"""naming.py — Specimen UID / date-segment derivation (W0 subset).

Faithfully mirrors db-utils.js:158-165 (specimenDateSeg) and
the uid-assembly logic in upsertSpecimen (lines 121-122).

W1 will add the full 7-segment filename parser; that lives separately.
"""

import re
from typing import Optional


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
    return "-".join(str(p) for p in parts if p)
