#!/usr/bin/env python3
"""
migrate-norm-aliases.py  —  Idempotente Schema-Migration für norm_aliases

Fügt vier neue Spalten zur bestehenden norm_aliases-Tabelle hinzu:
  display_name  — menschenlesbarer Label inkl. Version + Titel-Auszug
  shortname     — KI-generierter Langname (schema.md §2), versionsstabil
  abbr          — kanonische Kurzklammer (z.B. 'TSP-Baseline')
  wki_id        — ETSI Work Item Number für Weblinks + WKI_ID-Upsert.py

Sowie drei neue Indizes:
  norm_aliases_abbr_idx       ON abbr       WHERE abbr IS NOT NULL
  norm_aliases_wki_idx        ON wki_id     WHERE wki_id IS NOT NULL
  norm_aliases_type_norm_idx  ON (alias_type, norm)

Idempotenz:
  Bestehende Spalten/Indizes werden stillschweigend übersprungen.
  Das Script kann beliebig oft ausgeführt werden.

Usage:
  python3 scripts/migrate-norm-aliases.py
  python3 scripts/migrate-norm-aliases.py --db path/to/custom.db
  python3 scripts/migrate-norm-aliases.py --dry-run
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from pathlib import Path


def _load_dotenv(path: Path = Path(".env")) -> None:
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = val


# ---- Migration definition ---------------------------------------------------

# Each entry: (column_name, sql_type, default_expr)
# Order matters: SQLite appends columns in declaration order.
NEW_COLUMNS: list[tuple[str, str, str]] = [
    ("display_name", "TEXT", "NULL"),
    ("shortname",    "TEXT", "NULL"),
    ("abbr",         "TEXT", "NULL"),
    ("wki_id",       "TEXT", "NULL"),
]

# Each entry: (index_name, index_sql)
# Use CREATE INDEX IF NOT EXISTS so these are always safe to re-run.
NEW_INDEXES: list[tuple[str, str]] = [
    (
        "norm_aliases_abbr_idx",
        "CREATE INDEX IF NOT EXISTS norm_aliases_abbr_idx "
        "ON norm_aliases(abbr) WHERE abbr IS NOT NULL",
    ),
    (
        "norm_aliases_wki_idx",
        "CREATE INDEX IF NOT EXISTS norm_aliases_wki_idx "
        "ON norm_aliases(wki_id) WHERE wki_id IS NOT NULL",
    ),
    (
        "norm_aliases_type_norm_idx",
        "CREATE INDEX IF NOT EXISTS norm_aliases_type_norm_idx "
        "ON norm_aliases(alias_type, norm)",
    ),
]


# ---- Helpers ----------------------------------------------------------------

def _existing_columns(con: sqlite3.Connection, table: str) -> set[str]:
    rows = con.execute(f"PRAGMA table_info({table})").fetchall()
    return {r[1] for r in rows}  # r[1] = column name


def _existing_indexes(con: sqlite3.Connection) -> set[str]:
    rows = con.execute(
        "SELECT name FROM sqlite_master WHERE type='index'"
    ).fetchall()
    return {r[0] for r in rows}


# ---- Main -------------------------------------------------------------------

def migrate(db_path: Path, dry_run: bool = False) -> None:
    if not db_path.is_file():
        sys.exit(f"[ERROR] DB not found: {db_path}")

    print(f"[migrate] DB: {db_path}")
    if dry_run:
        print("[migrate] DRY-RUN — no changes written")

    con = sqlite3.connect(db_path)
    con.execute("PRAGMA journal_mode=WAL")

    # Verify norm_aliases exists
    tbl = con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='norm_aliases'"
    ).fetchone()
    if not tbl:
        sys.exit(
            "[ERROR] norm_aliases table not found. "
            "Run build-index.py first to create the schema."
        )

    existing_cols = _existing_columns(con, "norm_aliases")
    existing_idx  = _existing_indexes(con)

    added_cols: list[str]  = []
    skipped_cols: list[str] = []
    added_idx: list[str]   = []
    skipped_idx: list[str] = []

    # --- Columns
    for col, sql_type, default in NEW_COLUMNS:
        if col in existing_cols:
            skipped_cols.append(col)
            continue
        stmt = (
            f"ALTER TABLE norm_aliases "
            f"ADD COLUMN {col} {sql_type} DEFAULT {default}"
        )
        print(f"  ADD COLUMN {col} {sql_type}")
        if not dry_run:
            con.execute(stmt)
        added_cols.append(col)

    # --- Indexes
    for idx_name, idx_sql in NEW_INDEXES:
        if idx_name in existing_idx:
            skipped_idx.append(idx_name)
            continue
        print(f"  CREATE INDEX {idx_name}")
        if not dry_run:
            con.execute(idx_sql)
        added_idx.append(idx_name)

    if not dry_run:
        con.commit()
        # Update index_meta so build-index.py knows migration version
        con.execute(
            "INSERT OR REPLACE INTO index_meta(key, value) "
            "VALUES ('schema_migration', 'norm-aliases-v2')"
        )
        con.commit()

    con.close()

    # ---- Summary
    print()
    if added_cols:
        print(f"  ✔ Columns added   : {', '.join(added_cols)}")
    if skipped_cols:
        print(f"  □ Columns skipped  : {', '.join(skipped_cols)} (already exist)")
    if added_idx:
        print(f"  ✔ Indexes created  : {', '.join(added_idx)}")
    if skipped_idx:
        print(f"  □ Indexes skipped  : {', '.join(skipped_idx)} (already exist)")
    if not added_cols and not added_idx:
        print("  ✔ Nothing to do — schema is already up to date.")
    elif not dry_run:
        print()
        print("  Next steps:")
        print("    python3 scripts/build-index.py          # re-ingest to fill new columns")
        print("    # OR for a quick back-fill without re-ingest: see TODO-MCP.md")
    print()


if __name__ == "__main__":
    _load_dotenv()
    parser = argparse.ArgumentParser(description="Migrate norm_aliases schema")
    parser.add_argument(
        "--db",
        default=os.environ.get("MCP_DB_PATH", "corpus/eudi-nexus.db"),
        help="Path to SQLite DB (default: corpus/eudi-nexus.db or $MCP_DB_PATH)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print planned changes without executing them",
    )
    args = parser.parse_args()
    migrate(Path(args.db), dry_run=args.dry_run)
