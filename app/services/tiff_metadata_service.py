"""Write specimen/project business metadata into TIFF result files.

The writer stores application-owned metadata in a private TIFF tag and uses
ImageDescription only when it is empty or already owned by this application.
Camera/exposure EXIF fields are intentionally left untouched.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from PIL import Image, ImageSequence, TiffImagePlugin

from app.services import project_settings_service as pss

APP_NAME = "SpecimenPhotoWorkbench"
PRIVATE_TAG = 65000
TAG_IMAGE_DESCRIPTION = 270
TAG_SOFTWARE = 305

MODE_FILL_EMPTY = "fill_empty"
MODE_SKIP_WRITTEN = "skip_written"
MODE_FORCE = "force"
VALID_MODES = {MODE_FILL_EMPTY, MODE_SKIP_WRITTEN, MODE_FORCE}


def _clean(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _row_dict(row: Any) -> dict[str, Any]:
    return dict(row) if row is not None else {}


def _tag_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace").strip("\x00").strip()
    if isinstance(value, tuple) and value and all(isinstance(v, int) for v in value):
        return bytes(value).decode("utf-8", errors="replace").strip("\x00").strip()
    if isinstance(value, tuple) and len(value) == 1:
        return _tag_text(value[0])
    return str(value).strip("\x00").strip()


def _loads_app_payload(text: str) -> Optional[dict[str, Any]]:
    if not text:
        return None
    try:
        data = json.loads(text)
    except (TypeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    if data.get("source") != APP_NAME:
        return None
    return data


def _existing_payload(tags: Any) -> tuple[Optional[dict[str, Any]], bool]:
    private = _loads_app_payload(_tag_text(tags.get(PRIVATE_TAG)))
    if private:
        return private, True
    desc = _loads_app_payload(_tag_text(tags.get(TAG_IMAGE_DESCRIPTION)))
    if desc:
        return desc, True
    return None, False


def _selected_specimen_fields(specimen: dict[str, Any], fields: dict[str, bool]) -> dict[str, str]:
    mapping = {
        "uniqueId": ("unique_id", specimen.get("uid")),
        "projectName": ("project_name", None),
        "scientificName": ("scientific_name", specimen.get("scientific_name")),
        "scientificNameCn": (
            "scientific_name_cn",
            specimen.get("scientific_name_cn"),
        ),
        "collectionDate": ("collection_date", specimen.get("collection_date")),
        "photoDate": ("photo_date", specimen.get("photo_date")),
        "collector": ("collector", specimen.get("collector")),
        "photographer": ("photographer", specimen.get("photographer")),
        "identifier": ("identifier", specimen.get("identifier")),
        "lon": ("lon", specimen.get("lon")),
        "lat": ("lat", specimen.get("lat")),
        "geoArea": ("geo_area", specimen.get("geo_area")),
        "taxonGroup": ("taxon_group", specimen.get("taxon_group")),
        "order": ("order", specimen.get("order_name")),
        "family": ("family", specimen.get("family")),
        "notes": ("notes", specimen.get("notes")),
        "photoNotes": ("photo_notes", specimen.get("photo_notes")),
    }
    result: dict[str, str] = {}
    for setting_key, (payload_key, value) in mapping.items():
        if not fields.get(setting_key, False):
            continue
        cleaned = _clean(value)
        if cleaned:
            result[payload_key] = cleaned
    return result


def build_payload(
    db,
    uid: str,
    *,
    project_dir: str = "",
    project_root: Optional[str] = None,
) -> dict[str, Any]:
    """Build the application-owned TIFF metadata payload for *uid*."""
    row = db.execute("SELECT * FROM specimens WHERE uid=?", (uid,)).fetchone()
    specimen = _row_dict(row)
    if not specimen:
        raise ValueError(f"标本不存在，无法写入 TIFF 元数据：{uid}")

    if project_dir:
        project_meta = pss.get_effective(
            project_dir,
            "project_meta",
            pss.DEFAULT_PROJECT_META,
            root=project_root,
        )
        tiff_fields = pss.get_effective(
            project_dir,
            "tiff_fields",
            pss.DEFAULT_TIFF_FIELDS,
            root=project_root,
        )
    else:
        project_meta = pss.load_setting(db, "project_meta", pss.DEFAULT_PROJECT_META)
        tiff_fields = pss.load_setting(db, "tiff_fields", pss.DEFAULT_TIFF_FIELDS)

    selected = _selected_specimen_fields(specimen, tiff_fields)
    project_name = _clean(project_meta.get("name"))
    if tiff_fields.get("projectName", True) and project_name:
        selected["project_name"] = project_name

    project = {
        key: _clean(project_meta.get(key))
        for key in (
            "project_code",
            "name",
            "year",
            "date_range",
            "location",
            "photo_location",
        )
        if _clean(project_meta.get(key))
    }
    return {
        "source": APP_NAME,
        "schema_version": 1,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "specimen_uid": uid,
        "project": project,
        "fields": selected,
    }


def write_tiff_metadata(
    tiff_path: str,
    payload: dict[str, Any],
    *,
    mode: str = MODE_FILL_EMPTY,
) -> dict[str, Any]:
    """Write application metadata to a TIFF file.

    Returns a small status dict: ``written`` is true when the file changed,
    ``skipped`` explains non-writes.
    """
    if mode not in VALID_MODES:
        mode = MODE_FILL_EMPTY
    path = Path(tiff_path)
    if path.suffix.lower() not in {".tif", ".tiff"}:
        return {"written": False, "skipped": "not_tiff"}
    if not path.is_file():
        raise FileNotFoundError(tiff_path)

    with Image.open(path) as img:
        frames = [frame.copy() for frame in ImageSequence.Iterator(img)]
        tags = img.tag_v2
        existing, app_written = _existing_payload(tags)
        if mode == MODE_SKIP_WRITTEN and app_written:
            return {"written": False, "skipped": "already_written"}

        next_payload = json.loads(json.dumps(payload, ensure_ascii=False))
        if mode == MODE_FILL_EMPTY and existing:
            merged = json.loads(json.dumps(existing, ensure_ascii=False))
            merged.setdefault("source", APP_NAME)
            merged.setdefault("schema_version", 1)
            merged.setdefault("specimen_uid", next_payload.get("specimen_uid", ""))
            merged_project = merged.setdefault("project", {})
            for key, value in (next_payload.get("project") or {}).items():
                if value and not merged_project.get(key):
                    merged_project[key] = value
            merged_fields = merged.setdefault("fields", {})
            for key, value in (next_payload.get("fields") or {}).items():
                if value and not merged_fields.get(key):
                    merged_fields[key] = value
            merged["updated_at"] = datetime.now(timezone.utc).isoformat()
            next_payload = merged

        out_json = json.dumps(next_payload, ensure_ascii=False, sort_keys=True)
        if (
            existing
            and json.dumps(existing, ensure_ascii=False, sort_keys=True) == out_json
        ):
            return {"written": False, "skipped": "unchanged"}

        info = TiffImagePlugin.ImageFileDirectory_v2()
        for tag, value in tags.items():
            info[tag] = value
        info[PRIVATE_TAG] = out_json.encode("utf-8")
        info[TAG_SOFTWARE] = APP_NAME

        desc = _tag_text(tags.get(TAG_IMAGE_DESCRIPTION))
        desc_owned = _loads_app_payload(desc) is not None
        if not desc or desc_owned or mode == MODE_FORCE:
            info[TAG_IMAGE_DESCRIPTION] = json.dumps(
                next_payload,
                ensure_ascii=True,
                sort_keys=True,
            )

        first, rest = frames[0], frames[1:]
        save_kwargs: dict[str, Any] = {"tiffinfo": info}
        if rest:
            save_kwargs.update({"save_all": True, "append_images": rest})
        first.save(path, format="TIFF", **save_kwargs)
    return {"written": True, "skipped": ""}


def write_result_tiff_metadata(
    db,
    uid: str,
    tiff_path: str,
    *,
    project_dir: str = "",
    project_root: Optional[str] = None,
    settings: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Apply project-configured metadata write policy to one result TIFF."""
    if settings is None:
        if project_dir:
            settings = pss.get_effective(
                project_dir,
                "tiff_metadata_write",
                pss.DEFAULT_TIFF_METADATA_WRITE,
                root=project_root,
            )
        else:
            settings = pss.load_setting(
                db,
                "tiff_metadata_write",
                pss.DEFAULT_TIFF_METADATA_WRITE,
            )
    if not bool(settings.get("enabled", True)):
        return {"written": False, "skipped": "disabled"}
    payload = build_payload(db, uid, project_dir=project_dir, project_root=project_root)
    return write_tiff_metadata(tiff_path, payload, mode=str(settings.get("mode") or MODE_FILL_EMPTY))


def read_app_metadata(tiff_path: str) -> Optional[dict[str, Any]]:
    """Read this application's TIFF metadata payload, if present."""
    with Image.open(tiff_path) as img:
        payload, _written = _existing_payload(img.tag_v2)
        return payload
