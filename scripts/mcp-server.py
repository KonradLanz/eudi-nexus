#!/usr/bin/env python3
"""
mcp-server.py  —  EUDI-Nexus MCP Server (stdio transport)

Exposes the eudi-nexus SQLite corpus as MCP tools for Claude / LM Studio.
All tools are read-only. No writes to the database.

Tools:
  search_norm     —  Hybrid BM25 + cosine search across all norms
  get_segment     —  Retrieve a single segment by ID
  list_norms      —  List all indexed norms with stats
  get_section     —  All segments of a section in a specific norm

Transport: stdio (compatible with Claude Desktop, LM Studio, mcp-cli)

Usage:
  python3 scripts/mcp-server.py
  python3 scripts/mcp-server.py --db corpus/eudi-nexus.db
  python3 scripts/mcp-server.py --no-embed   # BM25-only, no LM Studio needed

ClaudeDesktop config (~/.config/claude/claude_desktop_config.json):
  {
    "mcpServers": {
      "eudi-nexus": {
        "command": "python3",
        "args": ["/path/to/eudi-nexus/scripts/mcp-server.py"],
        "env": { "MCP_DB_PATH": "/path/to/eudi-nexus/corpus/eudi-nexus.db" }
      }
    }
  }

Environment:
  MCP_DB_PATH           corpus/eudi-nexus.db
  LMSTUDIO_BASE_URL     http://localhost:1234
  EMBEDDING_MODEL       nomic-embed-text-v1.5
  EMBEDDING_DIMENSIONS  768
  HYBRID_ALPHA          0.5   (BM25 weight; 1.0 = BM25-only, 0.0 = cosine-only)
"""
from __future__ import annotations

import json
import os
import sqlite3
import struct
import sys
from pathlib import Path
from typing import Any

try:
    from fastmcp import FastMCP
except ImportError:
    sys.exit("[ERROR] fastmcp not installed — run: pip install fastmcp")

try:
    import sqlite_vec
except ImportError:
    sys.exit("[ERROR] sqlite-vec not installed — run: pip install sqlite-vec")

try:
    import httpx
except ImportError:
    sys.exit("[ERROR] httpx not installed — run: pip install httpx")


# ──────────────────────────────────────────────────────────────────────────────
# Config
# ──────────────────────────────────────────────────────────────────────────────

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

DB_PATH      = Path(os.environ.get("MCP_DB_PATH",          "corpus/eudi-nexus.db"))
BASE_URL     = os.environ.get("LMSTUDIO_BASE_URL",          "http://localhost:1234")
EMBED_MODEL  = os.environ.get("EMBEDDING_MODEL",            "nomic-embed-text-v1.5")
DEFAULT_DIMS = int(os.environ.get("EMBEDDING_DIMENSIONS",   "768"))
HYBRID_ALPHA = float(os.environ.get("HYBRID_ALPHA",         "0.5"))

# Max results returned by search_norm
MAX_RESULTS = 20
# Default top-k for vector search
DEFAULT_K   = 20


# ──────────────────────────────────────────────────────────────────────────────
# DB helpers
# ──────────────────────────────────────────────────────────────────────────────

def _open_db() -> sqlite3.Connection:
    if not DB_PATH.is_file():
        raise RuntimeError(
            f"Database not found: {DB_PATH}\n"
            "Run: python3 scripts/build-index.py"
        )
    con = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.enable_load_extension(True)
    sqlite_vec.load(con)
    con.enable_load_extension(False)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA query_only=ON")  # read-only guard
    return con


def _row_to_dict(row: sqlite3.Row) -> dict:
    d = dict(row)
    if "normative_keywords" in d and d["normative_keywords"]:
        try:
            d["normative_keywords"] = json.loads(d["normative_keywords"])
        except (json.JSONDecodeError, TypeError):
            pass
    return d


def _norm_filter_clause(norm: str | None, version: str | None) -> tuple[str, list]:
    """Build WHERE clause fragment + params for norm/version filtering."""
    clauses, params = [], []
    if norm:
        clauses.append("s.norm LIKE ?")
        params.append(f"%{norm}%")
    if version:
        clauses.append("s.version = ?")
        params.append(version)
    return (" AND " + " AND ".join(clauses)) if clauses else "", params


# ──────────────────────────────────────────────────────────────────────────────
# Embedding client (thin, sync)
# ──────────────────────────────────────────────────────────────────────────────

def _embed(text: str) -> list[float] | None:
    """
    Embed a single text via LM Studio. Returns None on any error
    so the caller can gracefully fall back to BM25-only.
    """
    try:
        resp = httpx.post(
            f"{BASE_URL}/v1/embeddings",
            json={"model": EMBED_MODEL, "input": [text]},
            headers={"Content-Type": "application/json"},
            timeout=10.0,
        )
        resp.raise_for_status()
        return resp.json()["data"][0]["embedding"]
    except Exception:
        return None


def _floats_to_blob(v: list[float]) -> bytes:
    return struct.pack(f"{len(v)}f", *v)


# ──────────────────────────────────────────────────────────────────────────────
# Hybrid search engine
# ──────────────────────────────────────────────────────────────────────────────

def _bm25_search(
    con: sqlite3.Connection,
    query: str,
    norm_clause: str,
    norm_params: list,
    limit: int,
) -> dict[str, float]:
    """
    BM25 via FTS5. Returns {segment_id: normalised_score} (higher = better).
    FTS5 rank is negative (more negative = better match) — we invert + normalise.
    """
    sql = f"""
        SELECT s.id, fts.rank
        FROM segments_fts fts
        JOIN segments s ON s.rowid = fts.rowid
        WHERE segments_fts MATCH ?
        {norm_clause.replace('s.norm', 'fts.norm').replace('s.version', 's.version')}
        ORDER BY fts.rank
        LIMIT ?
    """
    # norm_clause uses s.norm / s.version — for FTS join we keep s.* via JOIN
    # Rebuild simpler: filter on segments table via JOIN
    sql = f"""
        SELECT s.id, fts.rank
        FROM segments_fts fts
        JOIN segments s ON s.rowid = fts.rowid
        WHERE segments_fts MATCH ?
        {norm_clause}
        ORDER BY fts.rank
        LIMIT ?
    """
    rows = con.execute(sql, [query] + norm_params + [limit]).fetchall()
    if not rows:
        return {}
    # ranks are negative floats; most negative = best
    raw = {r["id"]: -r["rank"] for r in rows}   # flip to positive
    max_score = max(raw.values()) or 1.0
    return {k: v / max_score for k, v in raw.items()}


def _cosine_search(
    con: sqlite3.Connection,
    query_vec: list[float],
    norm_clause: str,
    norm_params: list,
    k: int,
) -> dict[str, float]:
    """
    Cosine via sqlite-vec knn. Returns {segment_id: similarity} (higher = better).
    sqlite-vec distance is cosine distance [0,2]; similarity = 1 - distance/2.
    """
    blob = _floats_to_blob(query_vec)
    # sqlite-vec knn: WHERE embedding MATCH ? AND k = ?
    # Then JOIN to apply norm filter
    sql = f"""
        SELECT v.segment_id, v.distance
        FROM segments_vec v
        JOIN segments s ON s.id = v.segment_id
        WHERE v.embedding MATCH ?
          AND k = ?
        {norm_clause}
        ORDER BY v.distance
    """
    rows = con.execute(sql, [blob, k] + norm_params).fetchall()
    return {
        r["segment_id"]: 1.0 - (r["distance"] / 2.0)
        for r in rows
    }


def _hybrid_search(
    con: sqlite3.Connection,
    query: str,
    norm: str | None,
    version: str | None,
    limit: int,
    alpha: float,
) -> list[dict]:
    """
    Hybrid BM25 + cosine search.
    alpha=1.0 → BM25-only, alpha=0.0 → cosine-only, alpha=0.5 → equal weight.
    Returns list of segment dicts with hybrid_score, bm25_score, cosine_score.
    """
    norm_clause, norm_params = _norm_filter_clause(norm, version)

    bm25_scores:   dict[str, float] = {}
    cosine_scores: dict[str, float] = {}

    # BM25
    if alpha > 0:
        try:
            bm25_scores = _bm25_search(con, query, norm_clause, norm_params, limit * 2)
        except Exception:
            bm25_scores = {}

    # Cosine (only if LM Studio available)
    if alpha < 1.0:
        vec = _embed(query)
        if vec:
            try:
                cosine_scores = _cosine_search(
                    con, vec, norm_clause, norm_params, limit * 2
                )
            except Exception:
                cosine_scores = {}

    # Merge candidate IDs
    all_ids = set(bm25_scores) | set(cosine_scores)
    if not all_ids:
        return []

    # Compute hybrid scores
    scored: list[tuple[str, float, float, float]] = []
    for seg_id in all_ids:
        b = bm25_scores.get(seg_id, 0.0)
        c = cosine_scores.get(seg_id, 0.0)
        h = alpha * b + (1.0 - alpha) * c
        scored.append((seg_id, h, b, c))

    scored.sort(key=lambda x: x[1], reverse=True)
    top_ids = [s[0] for s in scored[:limit]]
    score_map = {s[0]: (s[1], s[2], s[3]) for s in scored[:limit]}

    # Fetch full segment rows
    placeholders = ",".join(["?"] * len(top_ids))
    rows = con.execute(
        f"SELECT * FROM segments WHERE id IN ({placeholders})",
        top_ids,
    ).fetchall()

    # Preserve score order
    row_map = {r["id"]: r for r in rows}
    results = []
    for seg_id in top_ids:
        if seg_id not in row_map:
            continue
        d = _row_to_dict(row_map[seg_id])
        h, b, c = score_map[seg_id]
        d["hybrid_score"]  = round(h, 4)
        d["bm25_score"]    = round(b, 4)
        d["cosine_score"]  = round(c, 4)
        results.append(d)

    return results


# ──────────────────────────────────────────────────────────────────────────────
# MCP Server
# ──────────────────────────────────────────────────────────────────────────────

mcp = FastMCP(
    name="eudi-nexus",
    instructions=(
        "Search and retrieve EUDI / eIDAS normative segments from ETSI, CEN, and IETF specs. "
        "Use search_norm for semantic + keyword search. Use get_segment to fetch a specific "
        "requirement by ID. Use list_norms to see what is indexed. Use get_section for all "
        "requirements in a specific section of a norm."
    ),
)

# Lazy DB connection (opened once on first tool call)
_db: sqlite3.Connection | None = None


def _get_db() -> sqlite3.Connection:
    global _db
    if _db is None:
        _db = _open_db()
    return _db


# ──────────────────────────────────────────────────────────────────────────────
# Tool: search_norm
# ──────────────────────────────────────────────────────────────────────────────

@mcp.tool()
def search_norm(
    query: str,
    norm: str | None = None,
    version: str | None = None,
    limit: int = 10,
    alpha: float = HYBRID_ALPHA,
    types: list[str] | None = None,
) -> dict[str, Any]:
    """
    Hybrid BM25 + cosine search over EUDI normative segments.

    Args:
        query:   Natural language or keyword query.
                 Examples: "TSP audit log requirements"
                           "private key activation shall"
                           "QSCD certificate issuance"
        norm:    Optional norm filter (partial match).
                 Examples: "319 401", "319 411", "319 421", "eIDAS"
        version: Optional exact version filter. Example: "v2.2.1"
        limit:   Maximum results to return (1–20, default 10).
        alpha:   BM25 weight (0.0–1.0). Default 0.5 (equal hybrid).
                 Use 1.0 for pure keyword, 0.0 for pure semantic.
        types:   Filter segment types. Options: NORM, INFORM, SECTION.
                 Default: ["NORM", "INFORM"] (excludes section headers).

    Returns:
        dict with:
          query         — echoed query
          result_count  — number of results
          mode          — "hybrid" | "bm25_only" (if LM Studio unavailable)
          results       — list of segments, each with:
            id, norm, version, type, section, section_title,
            text, anchor, page, normative_keywords,
            hybrid_score, bm25_score, cosine_score
    """
    limit = max(1, min(limit, MAX_RESULTS))
    alpha = max(0.0, min(alpha, 1.0))
    allowed_types = set(types) if types else {"NORM", "INFORM"}

    con = _get_db()
    results = _hybrid_search(con, query, norm, version, limit * 2, alpha)

    # Post-filter by type
    results = [r for r in results if r.get("type") in allowed_types]
    results = results[:limit]

    mode = "bm25_only" if alpha == 1.0 or all(
        r["cosine_score"] == 0 for r in results
    ) else "hybrid"

    return {
        "query":        query,
        "result_count": len(results),
        "mode":         mode,
        "results":      results,
    }


# ──────────────────────────────────────────────────────────────────────────────
# Tool: get_segment
# ──────────────────────────────────────────────────────────────────────────────

@mcp.tool()
def get_segment(segment_id: str) -> dict[str, Any]:
    """
    Retrieve a single normative segment by its exact ID.

    Use this to fetch the full text of a segment found via search_norm,
    or to resolve a known requirement ID (e.g. from a cross-reference).

    Args:
        segment_id: Exact segment ID, e.g. "en319401_p5_b2"

    Returns:
        Segment dict with all fields, or {"error": "not found"} if missing.
        Fields: id, norm, version, type, section, section_title, text,
                anchor, page, normative_keywords, profile, has_embedding.
    """
    con = _get_db()
    row = con.execute(
        "SELECT * FROM segments WHERE id = ?", (segment_id,)
    ).fetchone()
    if row is None:
        return {"error": "not found", "segment_id": segment_id}
    return _row_to_dict(row)


# ──────────────────────────────────────────────────────────────────────────────
# Tool: list_norms
# ──────────────────────────────────────────────────────────────────────────────

@mcp.tool()
def list_norms() -> dict[str, Any]:
    """
    List all norms currently indexed in the database.

    Returns a summary per norm/version with segment counts.
    Use this to discover what is available before calling search_norm.

    Returns:
        dict with:
          total_norms   — number of distinct norm/version pairs
          total_segments — total segments across all norms
          norms         — list of norm summaries:
            norm, version, total_segments,
            norm_count (NORM type), inform_count (INFORM type),
            section_count, embedded_count (have vector)
    """
    con = _get_db()
    rows = con.execute(
        """
        SELECT
            norm, version,
            COUNT(*) as total_segments,
            SUM(CASE WHEN type='NORM'    THEN 1 ELSE 0 END) as norm_count,
            SUM(CASE WHEN type='INFORM'  THEN 1 ELSE 0 END) as inform_count,
            SUM(CASE WHEN type='SECTION' THEN 1 ELSE 0 END) as section_count,
            SUM(has_embedding) as embedded_count
        FROM segments
        GROUP BY norm, version
        ORDER BY norm, version
        """
    ).fetchall()

    norms = [dict(r) for r in rows]
    total_segments = sum(n["total_segments"] for n in norms)

    return {
        "total_norms":    len(norms),
        "total_segments": total_segments,
        "norms":          norms,
    }


# ──────────────────────────────────────────────────────────────────────────────
# Tool: get_section
# ──────────────────────────────────────────────────────────────────────────────

@mcp.tool()
def get_section(
    norm: str,
    section: str,
    version: str | None = None,
    types: list[str] | None = None,
) -> dict[str, Any]:
    """
    Retrieve all segments of a specific section in a norm.

    Use this when you need the full context of a section, not just
    the top search hits. Results are ordered by page number.

    Args:
        norm:    Norm name (partial match). Example: "319 401", "319 421"
        section: Section number. Example: "5", "5.1", "6.3.2"
                 Prefix match: "5" returns 5, 5.1, 5.2, 5.1.1, etc.
        version: Optional exact version. Example: "v2.2.1"
        types:   Segment types to include. Default: all types.

    Returns:
        dict with:
          norm, version, section, segment_count, segments
          Each segment: id, type, section, section_title,
                        text, anchor, page, normative_keywords
    """
    con = _get_db()

    clauses = ["s.norm LIKE ?", "(s.section = ? OR s.section LIKE ?)"]
    params: list[Any] = [f"%{norm}%", section, f"{section}.%"]

    if version:
        clauses.append("s.version = ?")
        params.append(version)
    if types:
        placeholders = ",".join(["?"] * len(types))
        clauses.append(f"s.type IN ({placeholders})")
        params.extend(types)

    where = " AND ".join(clauses)
    rows = con.execute(
        f"""
        SELECT id, type, section, section_title, text,
               anchor, page, normative_keywords, norm, version
        FROM segments s
        WHERE {where}
        ORDER BY s.page, s.id
        """,
        params,
    ).fetchall()

    segments = [_row_to_dict(r) for r in rows]

    return {
        "norm":           norm,
        "version":        version,
        "section":        section,
        "segment_count":  len(segments),
        "segments":       segments,
    }


# ──────────────────────────────────────────────────────────────────────────────
# Entrypoint
# ──────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="EUDI-Nexus MCP Server")
    parser.add_argument("--db",       default=None, help="Override DB path")
    parser.add_argument("--no-embed", action="store_true", help="BM25-only mode")
    args = parser.parse_args()

    if args.db:
        import os
        os.environ["MCP_DB_PATH"] = args.db
        DB_PATH = Path(args.db)
    if args.no_embed:
        HYBRID_ALPHA = 1.0

    mcp.run(transport="stdio")
