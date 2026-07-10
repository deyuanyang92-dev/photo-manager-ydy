"""Schema helper for the specimen result-TIFF index."""

from __future__ import annotations

import sqlite3


def ensure_result_tif_index_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS specimen_result_tif_index (
          uid TEXT NOT NULL,
          seq INTEGER,
          absolute_path TEXT NOT NULL,
          file_name TEXT,
          mtime_iso TEXT,
          updated_at TEXT,
          PRIMARY KEY (uid, absolute_path)
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_result_tif_uid_seq "
        "ON specimen_result_tif_index(uid, seq)"
    )
