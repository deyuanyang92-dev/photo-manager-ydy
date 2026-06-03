"""db_manager.py — SQLite project database manager for W0.

Each project directory gets its own _data/project.db file.
Connections are cached per resolved project_dir path.
"""

import os
import sqlite3
from pathlib import Path
from typing import Optional

# Cache open connections by resolved project_dir path
_db_cache: dict[str, sqlite3.Connection] = {}

# Load schema SQL once
_SCHEMA_SQL_PATH = Path(__file__).parent / "schema.sql"
_DARWIN_CORE_SQL = """
DROP VIEW IF EXISTS darwin_core;
CREATE VIEW darwin_core AS
SELECT
  uid              AS occurrenceID,
  scientific_name  AS scientificName,
  family           AS family,
  genus            AS genus,
  order_name       AS "order",
  lon              AS decimalLongitude,
  lat              AS decimalLatitude,
  collection_date  AS eventDate,
  collector        AS recordedBy,
  identifier       AS identifiedBy,
  CASE
    WHEN province IS NOT NULL AND province != ''
    THEN province
         || CASE WHEN site IS NOT NULL AND site != '' THEN '·' || site ELSE '' END
         || CASE WHEN station IS NOT NULL AND station != '' THEN '·' || station ELSE '' END
    ELSE ''
  END AS locality,
  storage          AS verbatimPreservation
FROM specimens;
"""


def _project_db_path(resolved_dir: str) -> Path:
    """Return the _data/project.db path for a project directory."""
    return Path(resolved_dir) / "_data" / "project.db"


def open_project_db(project_dir: str) -> sqlite3.Connection:
    """Open (or retrieve cached) the SQLite connection for *project_dir*.

    Creates ``_data/`` subfolder if missing.
    Sets WAL mode, foreign_keys ON, and runs ensure_schema.
    """
    resolved = str(Path(project_dir).resolve())
    if resolved in _db_cache:
        return _db_cache[resolved]

    db_path = _project_db_path(resolved)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.commit()

    ensure_schema(conn)
    _db_cache[resolved] = conn
    return conn


def get_db(project_dir: str) -> sqlite3.Connection:
    """Return cached connection; opens if not yet open."""
    resolved = str(Path(project_dir).resolve())
    if resolved not in _db_cache:
        return open_project_db(project_dir)
    return _db_cache[resolved]


def ensure_schema(conn: sqlite3.Connection) -> None:
    """Idempotently apply schema.sql, then recreate darwin_core view."""
    schema_sql = _SCHEMA_SQL_PATH.read_text(encoding="utf-8")
    conn.executescript(schema_sql)
    conn.executescript(_DARWIN_CORE_SQL)
    conn.commit()


def close_all() -> None:
    """Close and evict all cached connections. Used in tests and on exit."""
    for conn in list(_db_cache.values()):
        try:
            conn.close()
        except Exception:
            pass
    _db_cache.clear()


def close_project_db(project_dir: str) -> None:
    """Close and evict a single project's connection."""
    resolved = str(Path(project_dir).resolve())
    conn = _db_cache.pop(resolved, None)
    if conn:
        try:
            conn.close()
        except Exception:
            pass
