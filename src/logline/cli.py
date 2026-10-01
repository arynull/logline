"""Command-line interface for logline."""
from __future__ import annotations

import argparse
import sys

from . import db


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="logline", description="Minimal CLI journaling tool."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_new = sub.add_parser("new", help="Create a new journal entry.")
    p_new.add_argument("title", help="Entry title (must not be empty).")
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

    sub.add_parser(
        "stats",
        help="Show journal analytics (days are UTC calendar dates).",
        description="Journal analytics. A streak is consecutive UTC "
        "calendar days (from created_at) with at least one entry.",
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "new":
        if not args.title.strip():
            parser.error("title must not be empty")
        with db.connect() as conn:
            entry_id = db.create_entry(
                conn, args.title, args.body, args.mood, args.tags
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
        with db.connect() as conn:
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
        with db.connect() as conn:
            entry = db.get_entry(conn, args.id)
        if entry is None:
            print(f"no entry with id {args.id}", file=sys.stderr)
            return 1
        print(entry["title"])
        print(entry["created_at"])
        if entry["mood"]:
            print(f"mood: {entry['mood']}")
        if entry["tags"]:
            print(f"tags: {entry['tags']}")
        if entry["body"]:
            print()
            print(entry["body"])
        return 0

    if args.command == "search":
        if not args.query.strip():
            parser.error("query must not be empty")
        with db.connect() as conn:
            try:
                entries = db.search_entries(conn, args.query)
            except ValueError as exc:
                parser.error(str(exc))
        for entry in entries:
            print(f"{entry['id']}  {entry['created_at']}  {entry['title']}")
        return 0

    if args.command == "stats":
        with db.connect() as conn:
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

    parser.error(f"unknown command {args.command!r}")
    return 2
