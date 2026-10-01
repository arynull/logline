"""SQLite storage layer for logline entries."""
from __future__ import annotations

import os
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

SCHEMA_VERSION = 1

SCHEMA = """\
CREATE TABLE IF NOT EXISTS entries(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  title TEXT NOT NULL,
  body TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL
);
"""


def get_data_dir() -> Path:
    """Return the logline data directory, honoring LOGLINE_DATA_DIR."""
    override = os.environ.get("LOGLINE_DATA_DIR")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".logline"


def get_db_path(data_dir: Path | None = None) -> Path:
    """Return the path of the SQLite database file."""
    base = data_dir if data_dir is not None else get_data_dir()
    return base / "logline.db"


def init_db(conn: sqlite3.Connection) -> None:
    """Create the schema if needed and stamp the schema version.

    The version is kept in ``PRAGMA user_version`` so future migrations
    can detect the current schema without an extra bookkeeping table.
    """
    conn.execute(SCHEMA)
    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    conn.commit()


def connect(db_path: Path | None = None) -> sqlite3.Connection:
    """Open the database, creating the data dir and schema on demand."""
    path = db_path if db_path is not None else get_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    init_db(conn)
    return conn


def _utcnow_iso8601() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def create_entry(conn: sqlite3.Connection, title: str, body: str = "") -> int:
    """Insert an entry and return its id.

    Raise ValueError when the title is empty or whitespace-only.
    """
    if not title.strip():
        raise ValueError("title must not be empty")
    cursor = conn.execute(
        "INSERT INTO entries (title, body, created_at) VALUES (?, ?, ?)",
        (title, body, _utcnow_iso8601()),
    )
    conn.commit()
    row_id = cursor.lastrowid
    assert row_id is not None
    return row_id


def list_entries(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Return all entries, newest first."""
    return list(
        conn.execute(
            "SELECT id, title, body, created_at FROM entries ORDER BY id DESC"
        )
    )


def get_entry(conn: sqlite3.Connection, entry_id: int) -> sqlite3.Row | None:
    """Return one entry by id, or None when it does not exist."""
    return conn.execute(
        "SELECT id, title, body, created_at FROM entries WHERE id = ?",
        (entry_id,),
    ).fetchone()
