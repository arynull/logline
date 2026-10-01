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

    sub.add_parser("list", help="List entries, newest first.")

    p_show = sub.add_parser("show", help="Show a single entry.")
    p_show.add_argument("id", type=int, help="Numeric entry id.")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "new":
        if not args.title.strip():
            parser.error("title must not be empty")
        with db.connect() as conn:
            entry_id = db.create_entry(conn, args.title, args.body)
        print(entry_id)
        return 0

    if args.command == "list":
        with db.connect() as conn:
            entries = db.list_entries(conn)
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
        if entry["body"]:
            print()
            print(entry["body"])
        return 0

    parser.error(f"unknown command {args.command!r}")
    return 2
