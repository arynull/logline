"""Command-line interface for logline."""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path

from . import __version__, db
from . import report as report_mod

EXAMPLES = """\
examples:
  logline new "Morning pages" -b "Coffee and code."
  cat notes.txt | logline new "Day" --stdin
  logline list --since 2026-09-01
  logline search harbor
  logline report --month -o report.md
"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="logline",
        description="Minimal CLI journaling tool.",
        epilog=EXAMPLES,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--version", action="version", version=f"logline {__version__}"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_new = sub.add_parser("new", help="Create a new journal entry.")
    p_new.add_argument(
        "title",
        help=f"Entry title (must not be empty, max {db.MAX_TITLE_LENGTH} chars).",
    )
    p_new.add_argument(
        "-b", "--body", default="", help="Entry body text (default: empty)."
    )
    p_new.add_argument(
        "--mood", default="", help="Entry mood text (default: empty)."
    )
    p_new.add_argument(
        "--tags",
        default="",
        help="Comma-separated tags, e.g. --tags t1,t2 (default: empty).",
    )
    p_new.add_argument(
        "--stdin",
        action="store_true",
        help="Read the entry body from stdin (end input with Ctrl-D when typing).",
    )

    p_list = sub.add_parser("list", help="List entries, newest first.")
    p_list.add_argument("--mood", default=None, help="Filter by mood.")
    p_list.add_argument("--tag", default=None, help="Filter by a single tag.")
    p_list.add_argument(
        "--since", default=None, help="Earliest calendar date (YYYY-MM-DD)."
    )
    p_list.add_argument(
        "--until", default=None, help="Latest calendar date (YYYY-MM-DD)."
    )

    p_show = sub.add_parser("show", help="Show a single entry.")
    p_show.add_argument("id", type=int, help="Numeric entry id.")

    p_search = sub.add_parser(
        "search", help="Full-text search over title and body."
    )
    p_search.add_argument("query", help="FTS5 query string.")

    p_random = sub.add_parser("random", help="Show a random journal entry.")
    p_random.add_argument("--mood", default=None, help="Filter by mood.")
    p_random.add_argument("--tag", default=None, help="Filter by a single tag.")

    p_delete = sub.add_parser("delete", help="Delete a single entry.")
    p_delete.add_argument("id", type=int, help="Numeric entry id.")
    p_delete.add_argument(
        "-y",
        "--yes",
        action="store_true",
        help="Delete without asking for confirmation.",
    )

    p_edit = sub.add_parser("edit", help="Edit an existing entry.")
    p_edit.add_argument("id", type=int, help="Numeric entry id.")
    p_edit.add_argument("--title", default=None, help="New title.")
    p_edit.add_argument("--body", default=None, help="New body text.")
    p_edit.add_argument("--mood", default=None, help="New mood text.")
    p_edit.add_argument(
        "--tags", default=None, help="New comma-separated tags ('' clears)."
    )
    p_edit.add_argument(
        "--clear-mood", action="store_true", help="Set mood to empty."
    )
    p_edit.add_argument(
        "--clear-tags", action="store_true", help="Set tags to empty."
    )

    sub.add_parser(
        "stats",
        help="Show journal analytics (days are UTC calendar dates).",
        description="Journal analytics. A streak is consecutive UTC "
        "calendar days (from created_at) with at least one entry.",
    )

    p_report = sub.add_parser(
        "report",
        help="Render a Markdown report (days are UTC calendar dates).",
        description="Markdown report of recent entries. Days are UTC "
        "calendar dates from created_at. --week (default): last 7 days "
        "including today; --month: last 30 days including today.",
    )
    span = p_report.add_mutually_exclusive_group()
    span.add_argument(
        "--week", action="store_true", help="Last 7 UTC days (default)."
    )
    span.add_argument("--month", action="store_true", help="Last 30 UTC days.")
    p_report.add_argument(
        "-o", "--output", default=None, help="Write the report to FILE."
    )

    p_cal = sub.add_parser(
        "calendar",
        help="Show an ASCII month calendar marking entry days.",
        description="ASCII month calendar where days with at least one "
        "entry are marked with *.",
    )
    p_cal.add_argument(
        "--month",
        default=None,
        metavar="YYYY-MM",
        help="Month to show as YYYY-MM (default: current UTC month).",
    )

    p_export = sub.add_parser(
        "export", help="Export entries as JSON (oldest first)."
    )
    p_export.add_argument(
        "--since", default=None, help="Earliest calendar date (YYYY-MM-DD)."
    )
    p_export.add_argument(
        "--until", default=None, help="Latest calendar date (YYYY-MM-DD)."
    )
    p_export.add_argument(
        "--tag", default=None, help="Filter by a single tag."
    )
    p_export.add_argument(
        "-o", "--output", default=None, help="Write the JSON to FILE."
    )

    p_import = sub.add_parser("import", help="Import entries from JSON.")
    p_import.add_argument("file", help="JSON file produced by logline export.")

    return parser


def _read_stdin_body() -> str:
    try:
        return sys.stdin.read()
    except (OSError, ValueError, EOFError):
        return ""


def _write_output_file(path_str: str, content: str) -> int | None:
    """Write ``content`` to ``path_str``; return 1 on failure, else None."""
    dest = Path(path_str).expanduser()
    try:
        if dest.parent != Path():
            dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(content, encoding="utf-8")
    except OSError as exc:
        print(f"cannot write to {path_str}: {exc}", file=sys.stderr)
        return 1
    return None


def _open_db() -> sqlite3.Connection | None:
    """Open the logline DB, printing a clean error on failure."""
    try:
        return db.connect()
    except db.InvalidDatabaseError as exc:
        print(str(exc), file=sys.stderr)
        return None


def _print_entry(entry: sqlite3.Row) -> None:
    """Print a single entry the same way as ``show``."""
    print(entry["title"])
    print(entry["created_at"])
    if entry["mood"]:
        print(f"mood: {entry['mood']}")
    if entry["tags"]:
        print(f"tags: {entry['tags']}")
    if entry["body"]:
        print()
        print(entry["body"])


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        if args.command == "new":
            if not args.title.strip():
                parser.error("title must not be empty")
            if len(args.title) > db.MAX_TITLE_LENGTH:
                parser.error(
                    f"title must be at most {db.MAX_TITLE_LENGTH} characters"
                )
            if args.stdin and args.body:
                parser.error("--stdin and --body cannot be used together")
            body = _read_stdin_body() if args.stdin else args.body
            conn_ctx = _open_db()
            if conn_ctx is None:
                return 1
            with conn_ctx as conn:
                entry_id = db.create_entry(
                    conn, args.title, body, args.mood, args.tags
                )
            print(entry_id)
            return 0

        if args.command == "list":
            try:
                if args.since is not None:
                    db.validate_date(args.since)
                if args.until is not None:
                    db.validate_date(args.until)
            except ValueError as exc:
                parser.error(str(exc))
            conn_ctx = _open_db()
            if conn_ctx is None:
                return 1
            with conn_ctx as conn:
                entries = db.list_entries(
                    conn,
                    mood=args.mood,
                    tag=args.tag,
                    since=args.since,
                    until=args.until,
                )
            for entry in entries:
                print(f"{entry['id']}  {entry['created_at']}  {entry['title']}")
            return 0

        if args.command == "show":
            conn_ctx = _open_db()
            if conn_ctx is None:
                return 1
            with conn_ctx as conn:
                entry = db.get_entry(conn, args.id)
            if entry is None:
                print(f"no entry with id {args.id}", file=sys.stderr)
                return 1
            _print_entry(entry)
            return 0

        if args.command == "search":
            if not args.query.strip():
                parser.error("query must not be empty")
            conn_ctx = _open_db()
            if conn_ctx is None:
                return 1
            with conn_ctx as conn:
                try:
                    entries = db.search_entries(conn, args.query)
                except ValueError as exc:
                    parser.error(str(exc))
            for entry in entries:
                print(f"{entry['id']}  {entry['created_at']}  {entry['title']}")
            return 0

        if args.command == "random":
            conn_ctx = _open_db()
            if conn_ctx is None:
                return 1
            with conn_ctx as conn:
                entry = db.get_random_entry(
                    conn, mood=args.mood, tag=args.tag
                )
            if entry is None:
                print("no entries found", file=sys.stderr)
                return 1
            _print_entry(entry)
            return 0

        if args.command == "stats":
            conn_ctx = _open_db()
            if conn_ctx is None:
                return 1
            with conn_ctx as conn:
                stats = db.compute_stats(conn)
            print(f"Total entries: {stats.total}")
            print(f"Current streak: {stats.current_streak} day(s)")
            print(f"Longest streak: {stats.longest_streak} day(s)")
            print("Moods:")
            for mood, count in stats.moods:
                print(f"  {mood}: {count}")
            print("Top tags:")
            for tag, count in stats.tags:
                print(f"  {tag}: {count}")
            return 0

        if args.command == "report":
            span = "month" if args.month else "week"
            start, end = report_mod.period_range(span)
            conn_ctx = _open_db()
            if conn_ctx is None:
                return 1
            with conn_ctx as conn:
                by_day = report_mod.fetch_period_entries(conn, start, end)
            text = report_mod.render_report(by_day, start, end)
            if args.output is not None:
                failed = _write_output_file(args.output, text)
                if failed is not None:
                    return failed
                return 0
            print(text, end="")
            return 0

        if args.command == "calendar":
            month_str = (
                args.month
                if args.month is not None
                else datetime.now(UTC).strftime("%Y-%m")
            )
            try:
                year, mon = report_mod.parse_month(month_str)
            except ValueError as exc:
                parser.exit(2, f"{parser.prog}: error: {exc}\n")
            conn_ctx = _open_db()
            if conn_ctx is None:
                return 1
            with conn_ctx as conn:
                entry_days = db.get_entry_days(conn)
            text = report_mod.render_calendar(year, mon, entry_days)
            print(text, end="")
            return 0

        if args.command == "export":
            try:
                if args.since is not None:
                    db.validate_date(args.since)
                if args.until is not None:
                    db.validate_date(args.until)
            except ValueError as exc:
                parser.error(str(exc))
            conn_ctx = _open_db()
            if conn_ctx is None:
                return 1
            with conn_ctx as conn:
                payload = report_mod.serialize_export(
                    report_mod.export_entries(
                        conn,
                        since=args.since,
                        until=args.until,
                        tag=args.tag,
                    )
                )
            if args.output is not None:
                failed = _write_output_file(args.output, payload + "\n")
                if failed is not None:
                    return failed
                return 0
            print(payload)
            return 0

        if args.command == "import":
            src = Path(args.file).expanduser()
            try:
                raw = src.read_text(encoding="utf-8")
            except (OSError, UnicodeError) as exc:
                parser.error(f"cannot read {args.file}: {exc}")
            try:
                items = json.loads(raw)
            except json.JSONDecodeError as exc:
                parser.error(f"invalid JSON in {args.file}: {exc}")
            conn_ctx = _open_db()
            if conn_ctx is None:
                return 1
            with conn_ctx as conn:
                try:
                    count = report_mod.import_entries(conn, items)
                except ValueError as exc:
                    parser.error(str(exc))
            print(f"imported {count} entries")
            return 0

        if args.command == "delete":
            conn_ctx = _open_db()
            if conn_ctx is None:
                return 1
            with conn_ctx as conn:
                entry = db.get_entry(conn, args.id)
                if entry is None:
                    print(f"no entry with id {args.id}", file=sys.stderr)
                    return 1
                if not args.yes:
                    print(
                        f"{entry['id']}  {entry['created_at']}  {entry['title']}"
                    )
                    try:
                        answer = input(
                            f'Delete entry {entry["id"]} '
                            f'"{entry["title"]}"? [y/N]: '
                        )
                    except (EOFError, OSError):
                        print("Aborted.")
                        return 1
                    if answer.strip().lower() not in ("y", "yes"):
                        print("Aborted.")
                        return 1
                db.delete_entry(conn, args.id)
            print(f"deleted {args.id}")
            return 0

        if args.command == "edit":
            changes: dict[str, str] = {}
            if args.title is not None:
                if not args.title.strip():
                    parser.error("title must not be empty")
                if len(args.title) > db.MAX_TITLE_LENGTH:
                    parser.error(
                        f"title must be at most {db.MAX_TITLE_LENGTH} characters"
                    )
                changes["title"] = args.title
            if args.body is not None:
                changes["body"] = args.body
            if args.mood is not None:
                if args.clear_mood:
                    parser.error("--mood and --clear-mood are mutually exclusive")
                changes["mood"] = args.mood
            if args.clear_mood:
                changes["mood"] = ""
            if args.tags is not None:
                if args.clear_tags:
                    parser.error("--tags and --clear-tags are mutually exclusive")
                changes["tags"] = args.tags
            if args.clear_tags:
                changes["tags"] = ""
            if not changes:
                parser.error(
                    "nothing to edit: give at least one of --title, --body,"
                    " --mood, --tags, --clear-mood, --clear-tags"
                )
            conn_ctx = _open_db()
            if conn_ctx is None:
                return 1
            with conn_ctx as conn:
                if not db.update_entry(conn, args.id, **changes):
                    print(f"no entry with id {args.id}", file=sys.stderr)
                    return 1
            print(f"updated {args.id}")
            return 0

        parser.error(f"unknown command {args.command!r}")
        return 2
    except (sqlite3.Error, OSError, UnicodeError) as exc:
        print(f"logline: error: {exc}", file=sys.stderr)
        return 1
