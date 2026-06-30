"""Qt-free media discovery helpers for grouping workflows."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from stat import S_ISREG

from app.services.grouping_service import result_path_key, uid_matches_name

JPG_EXTS = {".jpg", ".jpeg"}
TIFF_EXTS = {".tif", ".tiff"}
RELATED_PICKER_NEAR_SECONDS = 30 * 60


def mtime_text(ts: float) -> str:
    try:
        return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return ""


def split_media_paths(paths: list[str]) -> tuple[list[str], list[str]]:
    jpgs: list[str] = []
    tiffs: list[str] = []
    for path in paths:
        suffix = Path(path).suffix.lower()
        if suffix in JPG_EXTS:
            jpgs.append(path)
        elif suffix in TIFF_EXTS:
            tiffs.append(path)
    return jpgs, tiffs


def name_matches_any_term(name: str, terms: list[str]) -> bool:
    return any(uid_matches_name(term, name) for term in terms)


def media_entries_in_dir(root: str | Path) -> list[dict]:
    """Return JPG/TIF file metadata with one stat call per child."""
    root_path = Path(root)
    entries: list[dict] = []
    try:
        children = list(root_path.iterdir())
    except OSError:
        return []
    for path in children:
        suffix = path.suffix.lower()
        if suffix not in (JPG_EXTS | TIFF_EXTS):
            continue
        try:
            st = path.stat()
        except OSError:
            continue
        if not S_ISREG(st.st_mode):
            continue
        entries.append({
            "path_obj": path,
            "path": str(path),
            "name": path.name,
            "kind": "TIF" if suffix in TIFF_EXTS else "JPG",
            "mtime": st.st_mtime,
            "size": st.st_size,
        })
    return entries


def _anchor_items(media: list[dict], root: Path, uid: str) -> list[tuple[Path, float]]:
    anchors: list[tuple[Path, float]] = []
    for item in media:
        if item["kind"] == "TIF" and uid_matches_name(uid, item["name"]):
            anchors.append((item["path_obj"], float(item["mtime"])))

    if not anchors and uid_matches_name(uid, root.name):
        for item in media:
            if item["kind"] == "TIF":
                anchors.append((item["path_obj"], float(item["mtime"])))
    return anchors


def scan_related_files_in_dir(
    folder: str | Path,
    uid: str,
    *,
    near_seconds: int = RELATED_PICKER_NEAR_SECONDS,
) -> list[dict]:
    """Find matching TIFFs and the JPG time blocks immediately before them."""
    del near_seconds  # The block rule is anchored by neighboring TIFF files.
    root = Path(folder)
    if not root.is_dir() or not uid:
        return []
    media = media_entries_in_dir(root)
    anchors = _anchor_items(media, root, uid)
    folder_matches = uid_matches_name(uid, root.name)

    if not anchors:
        fallback: list[dict] = []
        for item in media:
            name_matches = uid_matches_name(uid, item["name"])
            fallback.append({
                "path": item["path"],
                "name": item["name"],
                "kind": item["kind"],
                "mtime": item["mtime"],
                "size": item["size"],
                "anchor": False,
                "nearest_seconds": 0,
                "weak_match": name_matches or folder_matches,
            })
        return sorted(
            fallback,
            key=lambda item: (
                not bool(item.get("weak_match")),
                float(item["mtime"]),
                item["name"].casefold(),
            ),
        )

    media.sort(key=lambda item: (float(item["mtime"]), str(item["name"]).casefold()))
    anchor_paths = {result_path_key(str(path)) for path, _mtime in anchors}
    tif_indexes = [
        idx for idx, item in enumerate(media)
        if str(item.get("kind")) == "TIF"
    ]
    anchor_indexes = [
        idx for idx, item in enumerate(media)
        if result_path_key(str(item.get("path") or "")) in anchor_paths
    ]

    out: list[dict] = []
    seen: set[str] = set()
    for anchor_idx in anchor_indexes:
        anchor_item = media[anchor_idx]
        anchor_path = str(anchor_item.get("path") or "")
        anchor_name = str(anchor_item.get("name") or "")
        anchor_mtime = float(anchor_item.get("mtime") or 0)
        previous_tif_idx = max(
            (idx for idx in tif_indexes if idx < anchor_idx),
            default=-1,
        )
        for idx in range(previous_tif_idx + 1, anchor_idx + 1):
            item = media[idx]
            key = result_path_key(str(item.get("path") or ""))
            if key in seen:
                continue
            is_anchor = idx == anchor_idx
            if str(item.get("kind")) == "TIF" and not is_anchor:
                continue
            seconds = abs(float(item.get("mtime") or 0) - anchor_mtime)
            seen.add(key)
            out.append({
                "path": item["path"],
                "name": item["name"],
                "kind": item["kind"],
                "mtime": item["mtime"],
                "size": item["size"],
                "anchor": is_anchor,
                "nearest_seconds": seconds,
                "nearest_anchor": anchor_path,
                "nearest_anchor_name": anchor_name,
                "relative_to_tif": "anchor" if is_anchor else "before",
                "source_block": True,
                "default_related": True,
            })
    return sorted(out, key=lambda item: (float(item["mtime"]), item["name"].casefold()))


def scan_all_media_timeline_in_dir(folder: str | Path, uid: str) -> list[dict]:
    """Return every JPG/TIF in one directory, sorted by modification time."""
    root = Path(folder)
    if not root.is_dir():
        return []
    children = media_entries_in_dir(root)
    anchors = _anchor_items(children, root, uid)
    anchor_keys = {result_path_key(str(path)) for path, _mtime in anchors}

    media: list[dict] = []
    for item in children:
        is_anchor = result_path_key(item["path"]) in anchor_keys
        nearest_anchor = None
        if anchors:
            nearest_anchor = min(
                anchors,
                key=lambda anchor: abs(float(item["mtime"]) - anchor[1]),
            )
        nearest_seconds = (
            abs(float(item["mtime"]) - nearest_anchor[1])
            if nearest_anchor is not None else 0
        )
        relative = ""
        if nearest_anchor is not None and not is_anchor:
            relative = "before" if float(item["mtime"]) <= nearest_anchor[1] else "after"
        media.append({
            "path": item["path"],
            "name": item["name"],
            "kind": str(item["kind"]),
            "mtime": item["mtime"],
            "size": item["size"],
            "anchor": is_anchor,
            "nearest_seconds": nearest_seconds,
            "nearest_anchor": str(nearest_anchor[0]) if nearest_anchor else "",
            "nearest_anchor_name": nearest_anchor[0].name if nearest_anchor else "",
            "relative_to_tif": "anchor" if is_anchor else relative,
            "default_related": is_anchor,
            "timeline_only": True,
        })
    return sorted(media, key=lambda item: (float(item["mtime"]), item["name"].casefold()))


def scan_jpgs_near_tiff_in_dir(
    folder: str | Path,
    tiff_path: str,
    *,
    near_seconds: int = RELATED_PICKER_NEAR_SECONDS,
) -> list[dict]:
    """Find JPGs in one directory near an already-linked TIFF timestamp."""
    root = Path(folder)
    tiff = Path(tiff_path)
    if not root.is_dir() or not tiff_path:
        return []
    try:
        anchor_mtime = tiff.stat().st_mtime
    except OSError:
        return []

    out: list[dict] = []
    try:
        children = [
            p for p in root.iterdir()
            if p.is_file() and p.suffix.lower() in JPG_EXTS
        ]
    except OSError:
        return []
    for path in children:
        try:
            st = path.stat()
        except OSError:
            continue
        nearest = abs(st.st_mtime - anchor_mtime)
        if nearest > near_seconds:
            continue
        out.append({
            "path": str(path),
            "name": path.name,
            "kind": "JPG",
            "mtime": st.st_mtime,
            "size": st.st_size,
            "anchor": False,
            "nearest_seconds": nearest,
        })
    return sorted(out, key=lambda item: (-float(item["mtime"]), item["name"].casefold()))


def _normal_dir(path: str | Path | None) -> Path | None:
    if not path:
        return None
    try:
        target = Path(path).expanduser()
        if target.is_file():
            target = target.parent
        if target.is_dir():
            return target
    except Exception:
        return None
    return None


def is_broad_scan_root(path: Path) -> bool:
    text = str(path.resolve())
    return text in {"/", "/mnt", "/mnt/n", "/mnt/c"}


def find_related_media_dirs(
    roots: list[Path],
    uid: str,
    *,
    max_depth: int = 5,
    max_dirs: int = 1200,
    limit: int = 8,
) -> list[Path]:
    """Find source folders that contain files matching the specimen UID."""
    if not uid:
        return []
    found: list[Path] = []
    seen_dirs: set[str] = set()
    visited = 0
    for root in roots:
        root = _normal_dir(root)
        if root is None or is_broad_scan_root(root):
            continue
        stack: list[tuple[Path, int]] = [(root, 0)]
        while stack and visited < max_dirs and len(found) < limit:
            folder, depth = stack.pop()
            visited += 1
            try:
                children = list(folder.iterdir())
            except OSError:
                continue
            folder_key = str(folder.resolve()).casefold()
            if uid_matches_name(uid, folder.name) and folder_key not in seen_dirs:
                seen_dirs.add(folder_key)
                found.append(folder)
                if len(found) >= limit:
                    break
            matched_here = False
            subdirs: list[Path] = []
            for child in children:
                try:
                    if child.is_dir():
                        subdirs.append(child)
                        continue
                    if child.suffix.lower() not in (JPG_EXTS | TIFF_EXTS):
                        continue
                    if uid_matches_name(uid, child.name):
                        matched_here = True
                except OSError:
                    continue
            if matched_here and folder_key not in seen_dirs:
                seen_dirs.add(folder_key)
                found.append(folder)
                if len(found) >= limit:
                    break
            if depth < max_depth:
                for subdir in reversed(sorted(subdirs, key=lambda p: p.name.casefold())):
                    stack.append((subdir, depth + 1))
    return found
