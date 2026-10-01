"""SQLite storage layer for logline entries."""
from __future__ import annotations

import os
import re
import sqlite3
from datetime import UTC, date, datetime
from pathlib import Path

SCHEMA_VERSION = 2

SCHEMA_V2 = """\
CREATE TABLE IF NOT EXISTS entries(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  title TEXT NOT NULL,
  body TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  mood TEXT NOT NULL DEFAULT '',
  tags TEXT NOT NULL DEFAULT ''
);
"""

FTS_SCHEMA = """\
CREATE VIRTUAL TABLE IF NOT EXISTS entries_fts USING fts5(
  title, body, content='entries', content_rowid='id'
);
"""

TRIGGER_AI = """\
CREATE TRIGGER IF NOT EXISTS entries_ai AFTER INSERT ON entries BEGIN
  INSERT INTO entries_fts(rowid, title, body)
  VALUES (new.id, new.title, new.body);
END;
"""

TRIGGER_AD = """\
CREATE TRIGGER IF NOT EXISTS entries_ad AFTER DELETE ON entries BEGIN
  INSERT INTO entries_fts(entries_fts, rowid, title, body)
  VALUES ('delete', old.id, old.title, old.body);
END;
"""

TRIGGER_AU = """\
CREATE TRIGGER IF NOT EXISTS entries_au AFTER UPDATE ON entries BEGIN
  INSERT INTO entries_fts(entries_fts, rowid, title, body)
  VALUES ('delete', old.id, old.title, old.body);
  INSERT INTO entries_fts(rowid, title, body)
  VALUES (new.id, new.title, new.body);
END;
"""

TRIGGERS = (TRIGGER_AI, TRIGGER_AD, TRIGGER_AU)

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


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


def _get_user_version(conn: sqlite3.Connection) -> int:
    row = conn.execute("PRAGMA user_version").fetchone()
    return int(row[0]) if row else 0


def _table_columns(conn: sqlite3.Connection, table: str) -> list[str]:
    return [col[1] for col in conn.execute(f"PRAGMA table_info({table})")]


def _ensure_v2_objects(conn: sqlite3.Connection) -> None:
    """Create the v2 tables/triggers idempotently (no version stamp)."""
    conn.execute(SCHEMA_V2)
    conn.execute(FTS_SCHEMA)
    for trigger in TRIGGERS:
        conn.execute(trigger)


def _migrate_1_to_2(conn: sqlite3.Connection) -> None:
    """Upgrade a v1 (or unversioned legacy) database to v2 in place."""
    cols = _table_columns(conn, "entries")
    if "mood" not in cols:
        conn.execute("ALTER TABLE entries ADD COLUMN mood TEXT NOT NULL DEFAULT ''")
    if "tags" not in cols:
        conn.execute("ALTER TABLE entries ADD COLUMN tags TEXT NOT NULL DEFAULT ''")
    conn.execute(FTS_SCHEMA)
    for trigger in TRIGGERS:
        conn.execute(trigger)
    conn.execute("INSERT INTO entries_fts(entries_fts) VALUES('rebuild')")
    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    conn.commit()


def init_db(conn: sqlite3.Connection) -> None:
    """Create the schema if needed, migrating older databases forward.

    The version is kept in ``PRAGMA user_version`` so future migrations
    can detect the current schema without an extra bookkeeping table.
    """
    version = _get_user_version(conn)
    if version == 0:
        exists = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='entries'"
        ).fetchone()
        if exists is None:
            _ensure_v2_objects(conn)
            conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            conn.commit()
        else:
            _migrate_1_to_2(conn)
    elif version == 1:
        _migrate_1_to_2(conn)
    elif version == SCHEMA_VERSION:
        _ensure_v2_objects(conn)
        conn.commit()
    else:
        raise RuntimeError(f"unsupported logline schema version: {version}")


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


def normalize_tags(raw: str) -> str:
    """Normalize comma-separated tags: strip, drop empties, comma-join."""
    return ",".join(part for part in (t.strip() for t in raw.split(",")) if part)


def validate_date(value: str) -> str:
    """Validate a YYYY-MM-DD calendar date, returning it unchanged.

    Raise ValueError on any format or calendar violation.
    """
    if not _DATE_RE.match(value):
        raise ValueError(f"bad date format: {value!r} (expected YYYY-MM-DD)")
    try:
        date.fromisoformat(value)
    except ValueError:
        raise ValueError(f"bad date format: {value!r} (expected YYYY-MM-DD)") from None
    return value


def create_entry(
    conn: sqlite3.Connection,
    title: str,
    body: str = "",
    mood: str = "",
    tags: str = "",
) -> int:
    """Insert an entry and return its id.

    Raise ValueError when the title is empty or whitespace-only.
    """
    if not title.strip():
        raise ValueError("title must not be empty")
    cursor = conn.execute(
        "INSERT INTO entries (title, body, created_at, mood, tags)"
        " VALUES (?, ?, ?, ?, ?)",
        (title, body, _utcnow_iso8601(), mood, normalize_tags(tags)),
    )
    conn.commit()
    row_id = cursor.lastrowid
    assert row_id is not None
    return row_id


def list_entries(
    conn: sqlite3.Connection,
    mood: str | None = None,
    tag: str | None = None,
    since: str | None = None,
    until: str | None = None,
) -> list[sqlite3.Row]:
    """Return entries newest-first, AND-combining any given filters.

    ``since``/``until`` are inclusive YYYY-MM-DD calendar dates compared
    against the entry's date; invalid values raise ValueError.
    """
    clauses: list[str] = []
    params: list[str] = []
    if mood is not None:
        clauses.append("mood = ?")
        params.append(mood)
    if tag is not None:
        wanted = tag.strip()
        if not wanted:
            clauses.append("1 = 0")
        else:
            escaped = (
                wanted.replace("\\", "\\\\")
                .replace("%", "\\%")
                .replace("_", "\\_")
            )
            clauses.append("(',' || tags || ',') LIKE ? ESCAPE '\\'")
            params.append(f"%,{escaped},%")
    if since is not None:
        validate_date(since)
        clauses.append("substr(created_at, 1, 10) >= ?")
        params.append(since)
    if until is not None:
        validate_date(until)
        clauses.append("substr(created_at, 1, 10) <= ?")
        params.append(until)
    query = "SELECT id, title, body, created_at, mood, tags FROM entries"
    if clauses:
        query += " WHERE " + " AND ".join(clauses)
    query += " ORDER BY id DESC"
    return list(conn.execute(query, params))


def get_entry(conn: sqlite3.Connection, entry_id: int) -> sqlite3.Row | None:
    """Return one entry by id, or None when it does not exist."""
    return conn.execute(
        "SELECT id, title, body, created_at, mood, tags FROM entries WHERE id = ?",
        (entry_id,),
    ).fetchone()


def search_entries(conn: sqlite3.Connection, query: str) -> list[sqlite3.Row]:
    """Full-text search over title+body, best match first (bm25).

    Raise ValueError when the query is empty/whitespace-only or is not
    valid FTS5 query syntax.
    """
    if not query.strip():
        raise ValueError("query must not be empty")
    try:
        return list(
            conn.execute(
                "SELECT e.id, e.title, e.body, e.created_at, e.mood, e.tags"
                " FROM entries_fts JOIN entries e ON e.id = entries_fts.rowid"
                " WHERE entries_fts MATCH ?"
                " ORDER BY bm25(entries_fts), e.id DESC",
                (query.strip(),),
            )
        )
    except sqlite3.OperationalError as exc:
        raise ValueError(f"invalid search query: {exc}") from None
