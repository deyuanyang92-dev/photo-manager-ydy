"""project_service.py — Project lifecycle management.

Oracle:
  - server.js:2085-2465 (project management endpoints)
  - project-paths.js (dir resolution, legacy fallback)

Constants mirrored from project-paths.js:
  INCOMING_JPG_DIR = "incoming-jpg"
  LEGACY_INCOMING_JPG_DIR = "新拍JPG"
  RESULTS_DIR = "results"
"""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path
from typing import Optional

from app.utils.path_utils import default_registry

# ── Directory name constants ───────────────────────────────────────────────────
# Mirrors project-paths.js constants
INCOMING_JPG_DIR = "incoming-jpg"
LEGACY_INCOMING_JPG_DIR = "新拍JPG"
RESULTS_DIR = "results"
DATA_SUBDIR = "_data"


# ── Low-level helpers ──────────────────────────────────────────────────────────

def ensure_project_dirs(project_dir: str) -> dict:
    """Create the standard subdirectory layout under project_dir.

    Creates:  incoming-jpg/  results/  _data/

    Idempotent — safe to call multiple times.
    Returns a dict with resolved paths.

    Oracle: project-paths.js::ensureProjectDirs
    """
    root = Path(project_dir).resolve()
    root.mkdir(parents=True, exist_ok=True)

    incoming = root / INCOMING_JPG_DIR
    results = root / RESULTS_DIR
    data = root / DATA_SUBDIR

    incoming.mkdir(exist_ok=True)
    results.mkdir(exist_ok=True)
    data.mkdir(exist_ok=True)

    return {
        "projectDir": str(root),
        "incomingJpgDir": str(incoming),
        "resultsDir": str(results),
        "dataDir": str(data),
    }


def get_incoming_jpg_dir(project_dir: str) -> str:
    """Return the path to the incoming-JPG directory.

    Prefers modern 'incoming-jpg' subdir; falls back to legacy '新拍JPG'
    if the modern dir is absent but the legacy dir exists.
    If neither exists, returns the modern path (not yet created).

    Oracle: project-paths.js::resolveIncomingJpgDir
    """
    root = Path(project_dir).resolve()
    modern = root / INCOMING_JPG_DIR
    legacy = root / LEGACY_INCOMING_JPG_DIR

    if modern.exists():
        return str(modern)
    if legacy.exists():
        return str(legacy)
    return str(modern)


def get_results_dir(project_dir: str) -> str:
    """Return the path to the results directory.

    Oracle: project-paths.js::resolveResultsDir
    """
    root = Path(project_dir).resolve()
    return str(root / RESULTS_DIR)


# ── Public service API ─────────────────────────────────────────────────────────

def create_project(name: str, directory: str) -> dict:
    """Create a new project at *directory* and return its descriptor dict.

    - Generates a unique ID.
    - Creates the standard subdirectory layout.
    - Does NOT persist to user_projects.json (caller responsibility).

    Oracle: server.js project creation logic.
    """
    resolved = str(Path(directory).resolve())
    dirs = ensure_project_dirs(resolved)

    project_id = str(uuid.uuid4())
    return {
        "id": project_id,
        "name": name,
        "dir": resolved,
        "directory": resolved,
        "incomingJpgDir": dirs["incomingJpgDir"],
        "resultsDir": dirs["resultsDir"],
    }


def open_project(directory: str) -> dict:
    """Open an existing project directory, registering it in the safe-path registry.

    - Calls ensure_project_dirs to create any missing subdirectories.
    - Registers the directory in default_registry so path-safety checks pass.
    - Returns a descriptor dict.

    Oracle: server.js open-project endpoint + registerAllowedDir pattern.
    """
    resolved = str(Path(directory).resolve())
    dirs = ensure_project_dirs(resolved)

    # Register the project root so assert_safe() passes for its children
    default_registry.register_root(resolved)

    return {
        "dir": resolved,
        "directory": resolved,
        "incomingJpgDir": dirs["incomingJpgDir"],
        "resultsDir": dirs["resultsDir"],
        "dataDir": dirs["dataDir"],
    }


def list_projects(user_projects_json_path: str) -> list:
    """Read the user_projects.json file and return the list of project dicts.

    Returns an empty list if the file is missing or contains no projects.
    Does NOT raise on missing file.

    Oracle: server.js::userProjectsRead / GET /api/user-projects
    """
    path = Path(user_projects_json_path)
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data.get("projects", [])
    except (json.JSONDecodeError, OSError):
        return []
