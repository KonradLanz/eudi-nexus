#!/usr/bin/env python3
"""
test_migrate_norm_aliases.py  —  Unit-Tests für migrate-norm-aliases.py

Testete Eigenschaften:
  1. Neue Spalten werden korrekt angelegt
  2. Migration ist idempotent (zweimaliges Ausführen = kein Fehler)
  3. Vorhandene Zeilen bleiben erhalten (kein Datenverlust)
  4. Neue Spalten sind initial NULL
  5. Alle drei Indizes werden erstellt
  6. index_meta wird mit 'norm-aliases-v2' gesetzt
  7. Dry-run schreibt nichts
  8. Fehlermeldung bei fehlender Tabelle
  9. Fehlermeldung bei fehlender DB-Datei

Usage:
  python3 scripts/test_migrate_norm_aliases.py
  python3 scripts/test_migrate_norm_aliases.py -v
"""
from __future__ import annotations

import importlib.util
import io
import sqlite3
import sys
import tempfile
import pathlib
from contextlib import redirect_stdout
from pathlib import Path

# ---------------------------------------------------------------------------
# Load migrate-norm-aliases as module (handles hyphen in filename)
# ---------------------------------------------------------------------------
_SCRIPTS = pathlib.Path(__file__).parent
_spec = importlib.util.spec_from_file_location(
    "migrate_norm_aliases",
    _SCRIPTS / "migrate-norm-aliases.py",
)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
migrate = _mod.migrate

PASS = "\033[32m\u2713\033[0m"
FAIL = "\033[31m\u2717\033[0m"

_VERBOSE = "-v" in sys.argv


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_db(path: Path, *, with_norm_aliases: bool = True) -> None:
    """Create a minimal DB at *path*, optionally including norm_aliases."""
    con = sqlite3.connect(path)
    con.executescript("""
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS index_meta (
            key   TEXT PRIMARY KEY,
            value TEXT
        );
    """)
    if with_norm_aliases:
        con.executescript("""
            CREATE TABLE norm_aliases (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                norm       TEXT NOT NULL,
                alias      TEXT NOT NULL,
                alias_type TEXT NOT NULL,
                UNIQUE(norm, alias, alias_type)
            );
        """)
        # Insert two rows to verify they survive the migration
        con.execute(
            "INSERT INTO norm_aliases(norm, alias, alias_type) "
            "VALUES ('EN 319 401', 'EN 319 401', 'exact')"
        )
        con.execute(
            "INSERT INTO norm_aliases(norm, alias, alias_type) "
            "VALUES ('EN 319 401', '319401', 'numeric')"
        )
    con.commit()
    con.close()


def _columns(db: Path) -> list[str]:
    con = sqlite3.connect(db)
    cols = [r[1] for r in con.execute("PRAGMA table_info(norm_aliases)")]
    con.close()
    return cols


def _indexes(db: Path) -> set[str]:
    con = sqlite3.connect(db)
    idxs = {
        r[0] for r in con.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='index' AND tbl_name='norm_aliases'"
        )
    }
    con.close()
    return idxs


def _meta(db: Path) -> str | None:
    con = sqlite3.connect(db)
    row = con.execute(
        "SELECT value FROM index_meta WHERE key='schema_migration'"
    ).fetchone()
    con.close()
    return row[0] if row else None


def _rowcount(db: Path) -> int:
    con = sqlite3.connect(db)
    n = con.execute("SELECT COUNT(*) FROM norm_aliases").fetchone()[0]
    con.close()
    return n


def _null_counts(db: Path) -> dict[str, int]:
    """How many rows have NULL for each new column."""
    cols = ["display_name", "shortname", "abbr", "wki_id"]
    con = sqlite3.connect(db)
    result = {}
    for c in cols:
        n = con.execute(
            f"SELECT COUNT(*) FROM norm_aliases WHERE {c} IS NULL"
        ).fetchone()[0]
        result[c] = n
    con.close()
    return result


# ---------------------------------------------------------------------------
# Test runner
# ---------------------------------------------------------------------------

failed = 0
passed = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    global failed, passed
    if condition:
        passed += 1
        if _VERBOSE:
            print(f"  {PASS}  {name}")
    else:
        failed += 1
        label = f"  {FAIL}  {name}"
        print(label + (f"  → {detail}" if detail else ""))


# ---------------------------------------------------------------------------
# T1: Neue Spalten werden angelegt
# ---------------------------------------------------------------------------
print("T1  Neue Spalten nach Migration")
with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
    db1 = Path(f.name)
_make_db(db1)
buf = io.StringIO()
with redirect_stdout(buf):
    migrate(db1)
cols = _columns(db1)
for col in ["display_name", "shortname", "abbr", "wki_id"]:
    check(f"  column '{col}' present", col in cols, f"got {cols}")

# ---------------------------------------------------------------------------
# T2: Migration ist idempotent
# ---------------------------------------------------------------------------
print("T2  Idempotenz (zweimalige Ausführung)")
try:
    buf2 = io.StringIO()
    with redirect_stdout(buf2):
        migrate(db1)  # second run on same DB
    out2 = buf2.getvalue()
    check("  no exception on second run", True)
    check(
        "  'Nothing to do' message",
        "Nothing to do" in out2,
        repr(out2[:120]),
    )
except SystemExit as e:
    check("  no exception on second run", False, str(e))

# ---------------------------------------------------------------------------
# T3: Bestehende Zeilen bleiben erhalten
# ---------------------------------------------------------------------------
print("T3  Datenverlust-Schutz")
check("  row count == 2", _rowcount(db1) == 2, f"got {_rowcount(db1)}")

# ---------------------------------------------------------------------------
# T4: Neue Spalten sind initial NULL
# ---------------------------------------------------------------------------
print("T4  Neue Spalten initial NULL")
nulls = _null_counts(db1)
for col, n in nulls.items():
    check(f"  {col} IS NULL for all rows", n == 2, f"nulls={n}, expected 2")

# ---------------------------------------------------------------------------
# T5: Alle drei Indizes erstellt
# ---------------------------------------------------------------------------
print("T5  Indizes")
idxs = _indexes(db1)
for idx in [
    "norm_aliases_abbr_idx",
    "norm_aliases_wki_idx",
    "norm_aliases_type_norm_idx",
]:
    check(f"  index '{idx}'", idx in idxs, f"got {idxs}")

# ---------------------------------------------------------------------------
# T6: index_meta gesetzt
# ---------------------------------------------------------------------------
print("T6  index_meta = 'norm-aliases-v2'")
check("  meta value", _meta(db1) == "norm-aliases-v2", f"got {_meta(db1)}")

# ---------------------------------------------------------------------------
# T7: Dry-run schreibt nichts
# ---------------------------------------------------------------------------
print("T7  Dry-run — keine Schreiboperationen")
with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
    db_dry = Path(f.name)
_make_db(db_dry)
buf_dry = io.StringIO()
with redirect_stdout(buf_dry):
    migrate(db_dry, dry_run=True)
check("  columns NOT added in dry-run", "display_name" not in _columns(db_dry),
      f"cols={_columns(db_dry)}")
check("  indexes NOT added in dry-run",
      "norm_aliases_abbr_idx" not in _indexes(db_dry),
      f"idxs={_indexes(db_dry)}")
check("  meta NOT set in dry-run", _meta(db_dry) is None, f"meta={_meta(db_dry)}")
out_dry = buf_dry.getvalue()
check("  DRY-RUN in output", "DRY-RUN" in out_dry, repr(out_dry[:80]))

# ---------------------------------------------------------------------------
# T8: Fehlermeldung bei fehlender norm_aliases-Tabelle
# ---------------------------------------------------------------------------
print("T8  Fehler bei fehlender norm_aliases-Tabelle")
with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
    db_no_tbl = Path(f.name)
_make_db(db_no_tbl, with_norm_aliases=False)
try:
    buf_t8 = io.StringIO()
    with redirect_stdout(buf_t8):
        migrate(db_no_tbl)
    check("  SystemExit raised", False, "no exception")
except SystemExit as e:
    check("  SystemExit raised", True)
    check(
        "  error message contains 'norm_aliases'",
        "norm_aliases" in str(e),
        str(e),
    )

# ---------------------------------------------------------------------------
# T9: Fehlermeldung bei nicht-existierender DB-Datei
# ---------------------------------------------------------------------------
print("T9  Fehler bei fehlender DB-Datei")
try:
    migrate(Path("/tmp/does-not-exist-xyz.db"))
    check("  SystemExit raised", False, "no exception")
except SystemExit as e:
    check("  SystemExit raised", True)
    check(
        "  error message mentions path",
        "does-not-exist-xyz" in str(e),
        str(e),
    )

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
print()
total = passed + failed
if failed == 0:
    print(f"\033[32m✔ All {total} tests passed.\033[0m")
else:
    print(f"\033[31m✗ {failed}/{total} tests FAILED.\033[0m")
    sys.exit(1)
