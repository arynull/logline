"""SQLite storage layer for logline entries."""
from __future__ import annotations

import contextlib
import os
import re
import sqlite3
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from itertools import pairwise
from pathlib import Path

SCHEMA_VERSION = 2

MAX_TITLE_LENGTH = 500


class InvalidDatabaseError(ValueError):
    """Raised when the db file is not a usable logline database."""

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
        raise InvalidDatabaseError(
            f"unsupported logline schema version: {version}"
        )


def connect(db_path: Path | None = None) -> sqlite3.Connection:
    """Open the database, creating the data dir and schema on demand.

    Raise InvalidDatabaseError when the file exists but is not a
    valid SQLite database (or otherwise unreadable); missing and
    zero-length files are treated as fresh databases.
    """
    path = db_path if db_path is not None else get_db_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise InvalidDatabaseError(
            f"cannot open logline database: {exc}"
        ) from exc
    conn: sqlite3.Connection | None = None
    try:
        conn = sqlite3.connect(str(path))
        conn.row_factory = sqlite3.Row
        init_db(conn)
    except sqlite3.DatabaseError as exc:
        if conn is not None:
            with contextlib.suppress(sqlite3.Error):
                conn.close()
        lowered = str(exc).lower()
        if "unable to open" in lowered or "readonly" in lowered:
            raise InvalidDatabaseError(
                f"cannot open logline database: {exc}"
            ) from exc
        raise InvalidDatabaseError(
            "database is not a valid logline database"
        ) from exc
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
    created_at: str | None = None,
) -> int:
    """Insert an entry and return its id.

    Raise ValueError when the title is empty/whitespace-only or longer
    than MAX_TITLE_LENGTH characters.
    ``created_at`` overrides the timestamp (mainly for tests/tools).
    """
    if not title.strip():
        raise ValueError("title must not be empty")
    if len(title) > MAX_TITLE_LENGTH:
        raise ValueError(
            f"title must be at most {MAX_TITLE_LENGTH} characters"
        )
    cursor = conn.execute(
        "INSERT INTO entries (title, body, created_at, mood, tags)"
        " VALUES (?, ?, ?, ?, ?)",
        (title, body, created_at or _utcnow_iso8601(), mood, normalize_tags(tags)),
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


def delete_entry(conn: sqlite3.Connection, entry_id: int) -> bool:
    """Delete one entry by id; return True when a row was removed.

    The ``entries_ad`` FTS trigger keeps ``entries_fts`` in sync.
    """
    cursor = conn.execute("DELETE FROM entries WHERE id = ?", (entry_id,))
    conn.commit()
    return cursor.rowcount > 0


def update_entry(
    conn: sqlite3.Connection,
    entry_id: int,
    *,
    title: str | None = None,
    body: str | None = None,
    mood: str | None = None,
    tags: str | None = None,
) -> bool:
    """Update only the given fields of one entry; None means unchanged.

    Return False when no entry carries that id. ``created_at`` is never
    touched (an edit is not a new entry) and the ``entries_au`` FTS
    trigger keeps ``entries_fts`` in sync. Tags are normalized like in
    create_entry; an invalid title raises ValueError.
    """
    if get_entry(conn, entry_id) is None:
        return False
    if title is not None:
        if not title.strip():
            raise ValueError("title must not be empty")
        if len(title) > MAX_TITLE_LENGTH:
            raise ValueError(
                f"title must be at most {MAX_TITLE_LENGTH} characters"
            )
    assignments: list[str] = []
    params: list[str] = []
    for column, value in (
        ("title", title),
        ("body", body),
        ("mood", mood),
        ("tags", None if tags is None else normalize_tags(tags)),
    ):
        if value is not None:
            assignments.append(f"{column} = ?")
            params.append(value)
    if not assignments:
        return True
    params.append(str(entry_id))
    conn.execute(
        f"UPDATE entries SET {', '.join(assignments)} WHERE id = ?", params
    )
    conn.commit()
    return True


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


@dataclass
class Stats:
    """Journal analytics; day = UTC calendar date from ``created_at``."""

    total: int = 0
    current_streak: int = 0
    longest_streak: int = 0
    moods: list[tuple[str, int]] = field(default_factory=list)
    tags: list[tuple[str, int]] = field(default_factory=list)


def _entry_days(conn: sqlite3.Connection) -> set[date]:
    days: set[date] = set()
    for (raw,) in conn.execute("SELECT substr(created_at, 1, 10) FROM entries"):
        try:
            days.add(date.fromisoformat(raw))
        except (ValueError, TypeError):
            continue
    return days


def compute_stats(
    conn: sqlite3.Connection, today: date | None = None
) -> Stats:
    """Compute journal stats. Pure function of the DB (+ ``today``).

    Streaks count consecutive UTC calendar days with >=1 entry.
    The current streak counts back from today, or from yesterday when
    today has no entry yet (a missing today never breaks it). Moods
    skip empty values; tags are split on commas, capped at the top 10.
    Both order most-frequent-first with alphabetical tie-breaks.
    ``today`` defaults to the current UTC date; tests may inject it.
    """
    ref = today if today is not None else datetime.now(UTC).date()
    total = conn.execute("SELECT count(*) FROM entries").fetchone()[0]

    days = _entry_days(conn)
    if days:
        start = ref if ref in days else ref - timedelta(days=1)
        current = 0
        cursor = start
        while cursor in days:
            current += 1
            cursor -= timedelta(days=1)
        ordered = sorted(days)
        longest = 1
        run = 1
        for prev, cur in pairwise(ordered):
            if cur - prev == timedelta(days=1):
                run += 1
                longest = max(longest, run)
            else:
                run = 1
    else:
        current = 0
        longest = 0

    mood_counts: Counter[str] = Counter()
    for (mood,) in conn.execute("SELECT mood FROM entries"):
        if mood:
            mood_counts[mood] += 1
    moods = sorted(mood_counts.items(), key=lambda kv: (-kv[1], kv[0]))

    tag_counts: Counter[str] = Counter()
    for (raw,) in conn.execute("SELECT tags FROM entries"):
        if raw:
            for part in raw.split(","):
                tag = part.strip()
                if tag:
                    tag_counts[tag] += 1
    tags = sorted(tag_counts.items(), key=lambda kv: (-kv[1], kv[0]))[:10]

    return Stats(
        total=total,
        current_streak=current,
        longest_streak=longest,
        moods=moods,
        tags=tags,
    )
