#!/usr/bin/env python3
"""
health-check.py  —  DB integrity + search smoke-test

Usage:
  python3 scripts/health-check.py
  python3 scripts/health-check.py --db corpus/eudi-nexus.db
  python3 scripts/health-check.py --query "trust service provider" --norm "319 401"

Exits 0 if all checks pass, 1 on any failure.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sqlite3
import statistics
import sys
from pathlib import Path

# ── Config ────────────────────────────────────────────────────────────────────

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

_load_dotenv()

DEFAULT_DB = os.environ.get("MCP_DB_PATH", "corpus/eudi-nexus.db")

# Thresholds
MIN_SEGMENTS        = 1000
MIN_EMBED_RATIO     = 0.95   # 95 % of NORM/INFORM must have embeddings
MIN_MEDIAN_CHARS    = 150    # segments should be well-merged
WARN_MEDIAN_CHARS   = 200    # ideal target

# ── Helpers ───────────────────────────────────────────────────────────────────

OK   = "\033[32m✓\033[0m"
WARN = "\033[33m⚠\033[0m"
FAIL = "\033[31m✗\033[0m"

def _load_mcp_server(db_path: str) -> object | None:
    """Load mcp-server.py via importlib (filename has hyphen)."""
    server_path = Path("scripts/mcp-server.py")
    if not server_path.is_file():
        return None
    os.environ["MCP_DB_PATH"]        = db_path
    os.environ["EMBEDDING_BACKEND"]  = "auto"
    sys.argv = ["mcp-server.py"]
    spec = importlib.util.spec_from_file_location("mcp_server", server_path)
    mod  = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    try:
        spec.loader.exec_module(mod)  # type: ignore[union-attr]
        return mod
    except Exception as exc:
        print(f"  {WARN} Could not load mcp-server.py: {exc}")
        return None


# ── Checks ────────────────────────────────────────────────────────────────────

def check_db_exists(db_path: Path) -> bool:
    if db_path.is_file():
        size_mb = db_path.stat().st_size / 1_048_576
        print(f"  {OK}  DB exists  ({size_mb:.1f} MB)  →  {db_path}")
        return True
    print(f"  {FAIL} DB not found: {db_path}")
    print(f"       Run: python3 scripts/build-index.py --rebuild")
    return False


def check_segment_counts(con: sqlite3.Connection) -> bool:
    ok = True

    # Total count
    total = con.execute("SELECT COUNT(*) FROM segments").fetchone()[0]
    flag  = OK if total >= MIN_SEGMENTS else FAIL
    print(f"  {flag}  Total segments: {total:,}  (min {MIN_SEGMENTS:,})")
    if total < MIN_SEGMENTS:
        ok = False

    # Type breakdown
    rows = con.execute(
        "SELECT type, COUNT(*) FROM segments GROUP BY type ORDER BY COUNT(*) DESC"
    ).fetchall()
    print("       Type breakdown:")
    for r in rows:
        print(f"         {r[0]:<10} {r[1]:>6,}")

    return ok


def check_median_length(con: sqlite3.Connection) -> bool:
    lens = [
        r[0] for r in con.execute(
            "SELECT length(text) FROM segments WHERE type IN ('NORM','INFORM')"
        ).fetchall()
    ]
    if not lens:
        print(f"  {FAIL} No NORM/INFORM segments found")
        return False

    med   = statistics.median(lens)
    p10   = sorted(lens)[len(lens) // 10]
    p90   = sorted(lens)[int(len(lens) * 0.9)]
    short = sum(1 for l in lens if l < 100)

    flag = OK if med >= MIN_MEDIAN_CHARS else (WARN if med >= 100 else FAIL)
    ideal = f"  (target ≥ {WARN_MEDIAN_CHARS})"
    print(f"  {flag}  Median length: {med:.0f} chars{ideal}")
    print(f"       p10={p10}  p90={p90}  short(<100)={short} ({short/len(lens)*100:.1f}%)")
    return med >= MIN_MEDIAN_CHARS


def check_embedding_coverage(con: sqlite3.Connection) -> bool:
    total = con.execute(
        "SELECT COUNT(*) FROM segments WHERE type IN ('NORM','INFORM')"
    ).fetchone()[0]
    embedded = con.execute(
        "SELECT COUNT(*) FROM segments WHERE type IN ('NORM','INFORM') AND has_embedding = 1"
    ).fetchone()[0]
    if total == 0:
        print(f"  {FAIL} No NORM/INFORM segments to embed")
        return False

    ratio = embedded / total
    flag  = OK if ratio >= MIN_EMBED_RATIO else WARN
    print(f"  {flag}  Embedding coverage: {embedded:,}/{total:,} = {ratio*100:.1f}%  (min {MIN_EMBED_RATIO*100:.0f}%)")

    # Norms with gaps
    gaps = con.execute("""
        SELECT norm, COUNT(*) as total,
               SUM(CASE WHEN has_embedding=1 THEN 1 ELSE 0 END) as emb
        FROM segments WHERE type IN ('NORM','INFORM')
        GROUP BY norm
        HAVING emb < total
        ORDER BY (total - emb) DESC
        LIMIT 5
    """).fetchall()
    if gaps:
        print(f"       Norms with embedding gaps (top 5):")
        for g in gaps:
            print(f"         {g[0]:<35} {g[2]}/{g[1]} embedded")

    return ratio >= MIN_EMBED_RATIO


def check_norm_list(con: sqlite3.Connection) -> bool:
    rows = con.execute("""
        SELECT norm, version,
               COUNT(*) as segs,
               SUM(has_embedding) as emb
        FROM segments
        GROUP BY norm, version
        ORDER BY norm
    """).fetchall()
    print(f"  {OK}  Norms indexed: {len(rows)}")
    print(f"       {'Norm':<35} {'Ver':<10} {'Segs':>5} {'Emb':>5}")
    print(f"       {'-'*35} {'-'*10} {'-'*5} {'-'*5}")
    for r in rows:
        flag = " " if r[2] == r[3] else f" {WARN}"
        print(f"       {r[0]:<35} {r[1] or '':<10} {r[2]:>5} {r[3]:>5}{flag}")
    return True


def check_fts(con: sqlite3.Connection) -> bool:
    try:
        rows = con.execute(
            "SELECT id FROM segments_fts WHERE segments_fts MATCH 'trust' LIMIT 3"
        ).fetchall()
        flag = OK if rows else WARN
        msg  = f"{len(rows)} result(s)" if rows else "no results (FTS may be empty)"
        print(f"  {flag}  FTS smoke-test (query='trust'): {msg}")
        return bool(rows)
    except Exception as exc:
        print(f"  {FAIL} FTS query failed: {exc}")
        return False


def check_search_norm(
    mod: object,
    query: str,
    norm: str,
) -> bool:
    try:
        r = mod.search_norm(query=query, norm=norm, limit=3)  # type: ignore[attr-defined]
        mode    = r.get("mode", "?")
        count   = r.get("result_count", 0)
        results = r.get("results", [])
        flag    = OK if count > 0 else WARN
        print(f"  {flag}  search_norm(norm='{norm}')  mode={mode}  results={count}")
        for s in results[:3]:
            txt = s.get("text", "")[:100]
            print(f"         [{s.get('hybrid_score', '?')}] {s.get('id','?')}")
            print(f"           {txt!r}")
        return count > 0
    except Exception as exc:
        print(f"  {FAIL} search_norm failed: {exc}")
        return False


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Health-check the eudi-nexus index DB",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python3 scripts/health-check.py
  python3 scripts/health-check.py --db corpus/eudi-nexus.db
  python3 scripts/health-check.py --query \"electronic signature\" --norm \"319 132\"
""",
    )
    parser.add_argument("--db",    default=DEFAULT_DB,                        help="Path to SQLite DB")
    parser.add_argument("--query", default="trust service provider requirements", help="Smoke-test query")
    parser.add_argument("--norm",  default=None,                              help="Norm to scope smoke-test to")
    parser.add_argument("--no-search", action="store_true",                   help="Skip search_norm smoke-test")
    args = parser.parse_args()

    db_path = Path(args.db)
    failures: list[str] = []

    print("\n══════════════════════════════════════════")
    print(" eudi-nexus health check")
    print("══════════════════════════════════════════\n")

    # 1. DB file
    print("[1] DB file")
    if not check_db_exists(db_path):
        sys.exit(1)
    print()

    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row

    # 2. Segment counts
    print("[2] Segment counts")
    if not check_segment_counts(con):
        failures.append("segment_counts")
    print()

    # 3. Median text length (merge quality)
    print("[3] Merge quality (median text length)")
    if not check_median_length(con):
        failures.append("median_length")
    print()

    # 4. Embedding coverage
    print("[4] Embedding coverage")
    if not check_embedding_coverage(con):
        failures.append("embedding_coverage")
    print()

    # 5. Norm list
    print("[5] Norm list")
    check_norm_list(con)
    print()

    # 6. FTS smoke-test
    print("[6] FTS (BM25) smoke-test")
    if not check_fts(con):
        failures.append("fts")
    print()

    # 7. search_norm via mcp-server
    if not args.no_search:
        print("[7] search_norm smoke-test")
        mod = _load_mcp_server(str(db_path))
        if mod is None:
            print(f"  {WARN} mcp-server.py not loadable — skipping search test")
        else:
            # Detect a valid norm from DB if none given
            norm = args.norm
            if norm is None:
                row = con.execute(
                    "SELECT norm FROM segments WHERE type='NORM' LIMIT 1"
                ).fetchone()
                norm = row[0] if row else ""
            check_search_norm(mod, args.query, norm)
        print()

    con.close()

    # ── Summary ──
    print("══════════════════════════════════════════")
    if failures:
        print(f" {FAIL}  {len(failures)} check(s) failed: {', '.join(failures)}")
        print("══════════════════════════════════════════\n")
        sys.exit(1)
    else:
        print(f" {OK}  All checks passed")
        print("══════════════════════════════════════════\n")


if __name__ == "__main__":
    main()
