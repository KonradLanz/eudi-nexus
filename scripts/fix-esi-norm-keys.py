#!/usr/bin/env python3
"""
fix-esi-norm-keys.py  —  Normalise ESI-docbox norm keys to human-readable ETSI names

The ingest pipeline sometimes stores raw ETSI docbox filenames as the `norm` field,
e.g. 'ESI-0019401v331v322' instead of 'EN 319 401', with version 'vX.X.X' instead
of 'v3.3.1'.  This makes the MCP tools fail to match when users supply canonical
norm names.

This script is IDEMPOTENT: rows that already have a correct canonical name are
left untouched.  Run it after any ingest batch that may have introduced ESI keys.

Usage:
    python3 scripts/fix-esi-norm-keys.py             # dry-run (preview only)
    python3 scripts/fix-esi-norm-keys.py --apply     # write changes to DB
    python3 scripts/fix-esi-norm-keys.py --db path/to/other.db --apply

Output:
    Prints a table of every ESI key and its decoded canonical norm + version,
    then (with --apply) commits the UPDATE.
"""
from __future__ import annotations

import argparse
import os
import re
import sqlite3
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

DEFAULT_DB = Path(os.environ.get("MCP_DB_PATH", "corpus/eudi-nexus.db"))

# ESI docbox key pattern: ESI-00NNNNN[-PART]vVVVvVVV
# Examples:
#   ESI-0019401v331v322         -> EN 319 401,  version from v331 -> v3.3.1
#   ESI-0019403-1v241v233       -> EN 319 403-1, version from v241 -> v2.4.1
#   ESI-0019060v002             -> EN 319 060,  version from v002 -> v0.0.2 (unusual)
_ESI_DB_RE = re.compile(
    r"^ESI-(?P<number>\d{7})"
    r"(?:-(?P<part>\d+))?"
    r"v(?P<vmaj>\d+)v(?P<vmin>\d+)"
    r"(?:v(?P<vpatch>\d+))?$"
)
_ESI_DB_RE_SHORT = re.compile(
    r"^ESI-(?P<number>\d{7})"
    r"(?:-(?P<part>\d+))?"
    r"v(?P<ver>\d+)$"
)


def _decode_esi_key(norm_db: str) -> tuple[str, str] | None:
    """
    Decode an ESI docbox key into (canonical_norm, version) tuple.

    Returns None if the input does not match the ESI-00NNNNN pattern.

    Version reconstruction:
      v331v322  -> the first part (v331) is the *latest* version: v3.3.1
      v241v233  -> v2.4.1
      v002      -> v0.0.2 (short form, unusual)
    """
    m = _ESI_DB_RE.match(norm_db)
    short = False
    if not m:
        m = _ESI_DB_RE_SHORT.match(norm_db)
        short = True
    if not m:
        return None

    val = int(m.group("number"))  # e.g. 19401
    full = val + 300_000           # e.g. 319401
    series = full // 1000          # e.g. 319
    seq = full % 1000              # e.g. 401
    doc_type = "EN" if series >= 300 else "TS"
    base = f"{doc_type} {series} {seq:03d}"
    part = m.group("part")
    canonical_norm = f"{base}-{part}" if part else base

    # Version: from 'v331' -> digits are major, minor, patch
    if short:
        ver_str = m.group("ver")  # e.g. '002'
        if len(ver_str) == 3:
            version = f"v{ver_str[0]}.{ver_str[1]}.{ver_str[2]}"
        else:
            version = f"v{ver_str}"
    else:
        vmaj = m.group("vmaj")  # e.g. '331'
        # Split into (major, minor, patch) by taking each digit
        if len(vmaj) == 3:
            version = f"v{vmaj[0]}.{vmaj[1]}.{vmaj[2]}"
        elif len(vmaj) == 2:
            version = f"v{vmaj[0]}.{vmaj[1]}"
        else:
            version = f"v{vmaj}"

    return canonical_norm, version


def main() -> None:
    parser = argparse.ArgumentParser(description="Fix ESI docbox norm keys in DB")
    parser.add_argument("--db", default=str(DEFAULT_DB), help="Path to SQLite DB")
    parser.add_argument("--apply", action="store_true",
                        help="Write changes to DB (default: dry-run preview only)")
    args = parser.parse_args()

    db_path = Path(args.db)
    if not db_path.exists():
        sys.exit(f"[ERROR] DB not found: {db_path}")

    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row

    # Find all distinct ESI-* norm values
    esi_rows = con.execute(
        "SELECT DISTINCT norm, version FROM segments WHERE norm LIKE 'ESI-%' ORDER BY norm"
    ).fetchall()

    if not esi_rows:
        print("[OK] No ESI-* norm keys found in DB. Nothing to do.")
        return

    print(f"Found {len(esi_rows)} ESI-* norm key(s):\n")
    print(f"  {'ESI key':<42} {'version':>10}  ->  {'canonical norm':<22} {'version'}")
    print("  " + "-" * 85)

    updates: list[tuple[str, str, str, str]] = []  # (new_norm, new_ver, old_norm, old_ver)
    skipped: list[str] = []

    for row in esi_rows:
        old_norm: str = row["norm"]
        old_ver: str = row["version"]
        decoded = _decode_esi_key(old_norm)
        if decoded is None:
            skipped.append(old_norm)
            print(f"  {old_norm:<42} {old_ver:>10}  ->  [SKIP — no decode match]")
            continue
        new_norm, new_ver = decoded
        # Use new_ver only when old_ver is 'vX.X.X' (placeholder)
        resolved_ver = new_ver if old_ver == "vX.X.X" else old_ver
        updates.append((new_norm, resolved_ver, old_norm, old_ver))
        marker = "" if args.apply else "[dry-run]"
        print(f"  {old_norm:<42} {old_ver:>10}  ->  {new_norm:<22} {resolved_ver}  {marker}")

    if not updates:
        print("\n[OK] Nothing to update.")
        return

    print(f"\n{len(updates)} row group(s) to update", end="")
    if not args.apply:
        print(" (dry-run — pass --apply to commit)")
        return

    print(" — applying...")
    updated_segments = 0
    for new_norm, new_ver, old_norm, old_ver in updates:
        cur = con.execute(
            "UPDATE segments SET norm = ?, version = ? WHERE norm = ?",
            (new_norm, new_ver, old_norm),
        )
        updated_segments += cur.rowcount
        print(f"  Updated {cur.rowcount:4d} segments: {old_norm!r} -> {new_norm!r} {new_ver}")

    con.commit()
    print(f"\n[OK] Committed. {updated_segments} segment row(s) updated.")
    print("     Re-run list_norms or the MCP server to verify.")


if __name__ == "__main__":
    main()
