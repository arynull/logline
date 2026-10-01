# logline

A minimal CLI journaling tool. SQLite-backed, stdlib-only, full-text
search, stats, Markdown reports, and JSON export/import.

## Install

```sh
pip install .
```

or with pipx (isolated):

```sh
pipx install .
```

No install needed — run straight from a checkout:

```sh
PYTHONPATH=src python -m logline --version
```

Requires Python 3.12+. No third-party runtime dependencies.

## Quickstart

```sh
logline new "Morning pages" -b "Coffee and code."
# 1
logline list
# 1  2026-10-01T02:00:00+00:00  Morning pages
logline show 1
logline search coffee
logline stats
```

## Command reference

All commands accept `--help`. Days (filters, stats, reports) are UTC
calendar dates derived from `created_at`.

### new

```sh
logline new "Morning pages" -b "Coffee and code." --mood happy --tags work,fun
```

Creates an entry and prints its numeric id. Title must be non-empty
and at most 500 characters. Tags are comma-separated; surrounding
spaces are stripped and empty items dropped (`"a, b ,,c"` → `"a,b,c"`).

### list

```sh
logline list
logline list --mood happy
logline list --tag work
logline list --since 2026-09-01 --until 2026-10-01
```

Newest first, one `<id>  <created_at>  <title>` line per entry.
Filters combine with AND; `--since`/`--until` are inclusive
`YYYY-MM-DD` dates compared to the entry's calendar date (bad date →
exit 2). Empty database prints nothing, exit 0.

### show

```sh
logline show 1
```

Prints title, `created_at`, then `mood:`/`tags:` lines when set and
the body (after a blank line) when non-empty. Unknown or negative id
prints `no entry with id N` on stderr, exit 1.

### search

```sh
logline search harbor
```

FTS5 match over title+body, best match first (bm25), `list`-style
output. No match prints nothing, exit 0. Empty query or invalid FTS5
syntax (e.g. `"unbalanced`, `*`) → stderr, exit 2.

### stats

```sh
logline stats
```

Journal analytics (days are UTC calendar dates):

```text
Total entries: 3
Current streak: 2 day(s)
Longest streak: 2 day(s)
Moods:
  happy: 2
Top tags:
  work: 2
```

Current streak counts consecutive days with ≥1 entry back from today;
a missing today does not break it. Moods skip empty values; tags show
the top 10. Both order most-frequent-first, ties alphabetical.

### report

```sh
logline report
logline report --month
logline report --month -o report.md
```

Markdown report of recent entries. `--week` (default): last 7 UTC
days including today; `--month`: last 30 days including today.
`-o FILE` writes the file (parent dirs created; unwritable path →
stderr, exit 1). Format:

```markdown
# Journal report — 2026-09-24 to 2026-10-01

## 2026-10-01 (2 entries)

### Morning pages
*mood: happy · tags: work, fun*
Coffee and code.
```

Days newest-first, entries within a day newest-first,
`(1 entry)`/`(N entries)` pluralized. The mood/tags line appears only
when mood or tags are set; the body line only when non-empty. Empty
period prints the header plus a `No entries.` line.

### export / import

```sh
logline export
logline export -o backup.json
logline import backup.json
# imported 3 entries
```

`export` prints a JSON array of ALL entries, oldest first. `import`
inserts every item as a NEW entry (new ids), preserving `created_at`,
`mood`, `tags` (tags normalized); extra keys ignored. Import is
all-or-nothing: missing file, invalid JSON, non-list data, or an item
missing its title → stderr, exit 2, database unchanged.

JSON format (UTF-8, indent 2):

```json
[
  {
    "id": 1,
    "title": "Morning pages",
    "body": "Coffee and code.",
    "created_at": "2026-10-01T02:00:00+00:00",
    "mood": "happy",
    "tags": "work,fun"
  }
]
```

## Data directory

Entries live in `~/.logline/logline.db` (SQLite + FTS5 index).
Override with the `LOGLINE_DATA_DIR` environment variable:

```sh
LOGLINE_DATA_DIR=/tmp/demo logline list
```

A corrupt (non-SQLite) database file produces a clean
`database is not a valid logline database` message on stderr, exit 1.
