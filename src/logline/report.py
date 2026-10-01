"""Markdown reports and JSON export/import for logline.

All helpers here are pure functions of their inputs (plus a DB
connection) so they are unit-testable without the CLI. "Day" always
means UTC calendar date derived from ``created_at``.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import UTC, date, datetime, timedelta
from typing import Any

from . import db

EXPORT_KEYS = ("id", "title", "body", "created_at", "mood", "tags")

WEEK_DAYS = 7
MONTH_DAYS = 30


def period_range(span: str = "week", today: date | None = None) -> tuple[date, date]:
    """Return (start, end) UTC dates for a report span; end is today."""
    ref = today if today is not None else datetime.now(UTC).date()
    days = MONTH_DAYS if span == "month" else WEEK_DAYS
    return ref - timedelta(days=days - 1), ref


def fetch_period_entries(
    conn: sqlite3.Connection, start: date, end: date
) -> dict[str, list[sqlite3.Row]]:
    """Group entries in [start, end] by UTC day, days newest-first.

    Entries within a day are newest-first (id descending).
    """
    rows = db.list_entries(
        conn, since=start.isoformat(), until=end.isoformat()
    )
    by_day: dict[str, list[sqlite3.Row]] = {}
    for row in rows:
        by_day.setdefault(row["created_at"][:10], []).append(row)
    for day_rows in by_day.values():
        day_rows.sort(
            key=lambda r: (r["created_at"], r["id"]), reverse=True
        )
    return dict(sorted(by_day.items(), reverse=True))


def _meta_line(entry: Any) -> str | None:
    parts: list[str] = []
    if entry["mood"]:
        parts.append(f"mood: {entry['mood']}")
    if entry["tags"]:
        tags = [t.strip() for t in str(entry["tags"]).split(",") if t.strip()]
        if tags:
            parts.append(f"tags: {', '.join(tags)}")
    if not parts:
        return None
    return f"*{' · '.join(parts)}*"


def render_report(
    entries_by_day: dict[str, list[Any]], start: date | str, end: date | str
) -> str:
    """Render a Markdown journal report.

    ``entries_by_day`` maps YYYY-MM-DD to entries (newest-first);
    days are emitted newest-first. Entry mood/tags/body lines appear
    only when set.
    """
    start_s = start.isoformat() if isinstance(start, date) else str(start)
    end_s = end.isoformat() if isinstance(end, date) else str(end)
    lines = [f"# Journal report — {start_s} to {end_s}", ""]
    days = sorted(
        [(d, e) for d, e in entries_by_day.items() if e], reverse=True
    )
    if not days:
        lines.append("No entries.")
        return "\n".join(lines) + "\n"
    for day, entries in days:
        count = len(entries)
        noun = "entry" if count == 1 else "entries"
        lines.append(f"## {day} ({count} {noun})")
        lines.append("")
        for entry in entries:
            lines.append(f"### {entry['title']}")
            meta = _meta_line(entry)
            if meta is not None:
                lines.append(meta)
            if entry["body"]:
                lines.append(str(entry["body"]))
            lines.append("")
    return "\n".join(lines) + "\n"


def export_entries(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Return all entries as dicts, oldest first."""
    rows = conn.execute(
        "SELECT id, title, body, created_at, mood, tags"
        " FROM entries ORDER BY id ASC"
    ).fetchall()
    return [{key: row[key] for key in EXPORT_KEYS} for row in rows]


def serialize_export(entries: list[dict[str, Any]]) -> str:
    """Serialize exported entries to JSON (UTF-8, indent 2)."""
    return json.dumps(entries, ensure_ascii=False, indent=2)


def import_entries(conn: sqlite3.Connection, items: Any) -> int:
    """Validate ``items`` then insert every item as a new entry.

    Returns the number of imported entries. ``created_at``/``mood``/
    ``tags`` are preserved (tags normalized); extra keys ignored.
    Raises ValueError on bad shape without changing the DB
    (all-or-nothing: validation runs before any insert, and the
    inserts commit in a single transaction).
    """
    if not isinstance(items, list):
        raise ValueError("import data must be a list of entries")  # noqa: TRY004
    prepared: list[tuple[str, str, str, str, str]] = []
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            raise ValueError(  # noqa: TRY004
                f"entry #{index} must be an object"
            )
        title = item.get("title")
        if not isinstance(title, str) or not title.strip():
            raise ValueError(f"entry #{index} is missing a title")
        values: dict[str, str] = {}
        for name in ("body", "mood", "tags"):
            value = item.get(name, "")
            if not isinstance(value, str):
                raise ValueError(  # noqa: TRY004
                    f"entry #{index} has invalid {name!r}"
                )
            values[name] = value
        created_at = item.get("created_at", "")
        if not created_at:
            created_at = datetime.now(UTC).isoformat(timespec="seconds")
        elif not isinstance(created_at, str):
            raise ValueError(f"entry #{index} has invalid 'created_at'")
        prepared.append(
            (
                title,
                values["body"],
                created_at,
                values["mood"],
                db.normalize_tags(values["tags"]),
            )
        )
    try:
        for title, body, created_at, mood, tags in prepared:
            conn.execute(
                "INSERT INTO entries (title, body, created_at, mood, tags)"
                " VALUES (?, ?, ?, ?, ?)",
                (title, body, created_at, mood, tags),
            )
    except Exception:
        conn.rollback()
        raise
    conn.commit()
    return len(prepared)
