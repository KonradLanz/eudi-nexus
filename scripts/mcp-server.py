#!/usr/bin/env python3
"""
mcp-server.py  —  EUDI-Nexus MCP Server (stdio transport)

Exposes the eudi-nexus SQLite corpus as MCP tools for Claude / LM Studio / Ollama.
All tools are read-only. No writes to the database.

Tools:
  search_norm     —  Hybrid BM25 + cosine search across all norms
  get_segment     —  Retrieve a single segment by ID
  list_norms      —  List all indexed norms with stats
  get_section     —  All segments of a section in a specific norm

Transport: stdio (compatible with LM Studio, Ollama, Claude Desktop, mcp-cli)

Usage:
  python3 scripts/mcp-server.py
  python3 scripts/mcp-server.py --db corpus/eudi-nexus.db
  python3 scripts/mcp-server.py --no-embed   # BM25-only, no embedding endpoint needed

──────────────────────────────────────────────────────────────────────────────
  Embedding backend auto-detection (priority order):

  1. EMBEDDING_BACKEND env var ("lmstudio" | "ollama") — explicit override
  2. LM Studio  http://localhost:1234/v1/embeddings     — checked first
  3. Ollama     http://localhost:11434/api/embeddings   — fallback
  4. BM25-only  — if neither is reachable

  Environment variables:
    MCP_DB_PATH            corpus/eudi-nexus.db
    EMBEDDING_BACKEND      auto | lmstudio | ollama  (default: auto)
    LMSTUDIO_BASE_URL      http://localhost:1234
    LMSTUDIO_EMBED_MODEL   nomic-embed-text-v1.5
    OLLAMA_BASE_URL        http://localhost:11434
    OLLAMA_EMBED_MODEL     nomic-embed-text   (or mxbai-embed-large)
    EMBEDDING_DIMENSIONS   768
    HYBRID_ALPHA           0.5  (1.0=BM25-only, 0.0=cosine-only)
──────────────────────────────────────────────────────────────────────────────
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

DB_PATH      = Path(os.environ.get("MCP_DB_PATH",             "corpus/eudi-nexus.db"))
HYBRID_ALPHA = float(os.environ.get("HYBRID_ALPHA",           "0.5"))
DEFAULT_DIMS = int(os.environ.get("EMBEDDING_DIMENSIONS",     "768"))

# Embedding backend config
BACKEND      = os.environ.get("EMBEDDING_BACKEND", "auto").lower()  # auto | lmstudio | ollama

# LM Studio
LMSTUDIO_URL        = os.environ.get("LMSTUDIO_BASE_URL",    "http://localhost:1234")
LMSTUDIO_MODEL      = os.environ.get("LMSTUDIO_EMBED_MODEL", "nomic-embed-text-v1.5")

# Ollama
OLLAMA_URL          = os.environ.get("OLLAMA_BASE_URL",      "http://localhost:11434")
OLLAMA_MODEL        = os.environ.get("OLLAMA_EMBED_MODEL",   "nomic-embed-text")

MAX_RESULTS  = 20
DEFAULT_K    = 20


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
    con.execute("PRAGMA query_only=ON")
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
    clauses, params = [], []
    if norm:
        clauses.append("s.norm LIKE ?")
        params.append(f"%{norm}%")
    if version:
        clauses.append("s.version = ?")
        params.append(version)
    return (" AND " + " AND ".join(clauses)) if clauses else "", params


# ──────────────────────────────────────────────────────────────────────────────
# Embedding backends
# ──────────────────────────────────────────────────────────────────────────────

def _embed_lmstudio(text: str) -> list[float] | None:
    """OpenAI-compatible endpoint (LM Studio)."""
    try:
        resp = httpx.post(
            f"{LMSTUDIO_URL}/v1/embeddings",
            json={"model": LMSTUDIO_MODEL, "input": [text]},
            headers={"Content-Type": "application/json"},
            timeout=10.0,
        )
        resp.raise_for_status()
        return resp.json()["data"][0]["embedding"]
    except Exception:
        return None


def _embed_ollama(text: str) -> list[float] | None:
    """Ollama native /api/embeddings endpoint."""
    try:
        resp = httpx.post(
            f"{OLLAMA_URL}/api/embeddings",
            json={"model": OLLAMA_MODEL, "prompt": text},
            headers={"Content-Type": "application/json"},
            timeout=10.0,
        )
        resp.raise_for_status()
        return resp.json()["embedding"]
    except Exception:
        return None


# Resolved at first embed call; cached for the lifetime of the process
_resolved_backend: str | None = None   # "lmstudio" | "ollama" | "none"


def _embed(text: str) -> list[float] | None:
    global _resolved_backend

    if _resolved_backend is None:
        _resolved_backend = _detect_backend()
        _log_backend(_resolved_backend)

    if _resolved_backend == "lmstudio":
        return _embed_lmstudio(text)
    if _resolved_backend == "ollama":
        return _embed_ollama(text)
    return None  # BM25-only mode


def _detect_backend() -> str:
    """Probe available backends. Returns 'lmstudio' | 'ollama' | 'none'."""
    if BACKEND == "lmstudio":
        return "lmstudio"
    if BACKEND == "ollama":
        return "ollama"

    # BACKEND == "auto" — probe in priority order
    try:
        r = httpx.get(f"{LMSTUDIO_URL}/v1/models", timeout=2.0)
        if r.status_code < 500:
            return "lmstudio"
    except Exception:
        pass

    try:
        r = httpx.get(f"{OLLAMA_URL}/api/tags", timeout=2.0)
        if r.status_code < 500:
            return "ollama"
    except Exception:
        pass

    return "none"


def _log_backend(backend: str) -> None:
    label = {
        "lmstudio": f"LM Studio  ({LMSTUDIO_URL})  model={LMSTUDIO_MODEL}",
        "ollama":   f"Ollama     ({OLLAMA_URL})  model={OLLAMA_MODEL}",
        "none":     "No embedding backend found — BM25-only mode",
    }.get(backend, backend)
    print(f"[eudi-nexus] Embedding backend: {label}", file=sys.stderr)


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
    raw = {r["id"]: -r["rank"] for r in rows}
    max_score = max(raw.values()) or 1.0
    return {k: v / max_score for k, v in raw.items()}


def _cosine_search(
    con: sqlite3.Connection,
    query_vec: list[float],
    norm_clause: str,
    norm_params: list,
    k: int,
) -> dict[str, float]:
    blob = _floats_to_blob(query_vec)
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
    norm_clause, norm_params = _norm_filter_clause(norm, version)

    bm25_scores:   dict[str, float] = {}
    cosine_scores: dict[str, float] = {}

    if alpha > 0:
        try:
            bm25_scores = _bm25_search(con, query, norm_clause, norm_params, limit * 2)
        except Exception:
            bm25_scores = {}

    if alpha < 1.0:
        vec = _embed(query)
        if vec:
            try:
                cosine_scores = _cosine_search(
                    con, vec, norm_clause, norm_params, limit * 2
                )
            except Exception:
                cosine_scores = {}

    all_ids = set(bm25_scores) | set(cosine_scores)
    if not all_ids:
        return []

    scored: list[tuple[str, float, float, float]] = []
    for seg_id in all_ids:
        b = bm25_scores.get(seg_id, 0.0)
        c = cosine_scores.get(seg_id, 0.0)
        h = alpha * b + (1.0 - alpha) * c
        scored.append((seg_id, h, b, c))

    scored.sort(key=lambda x: x[1], reverse=True)
    top_ids = [s[0] for s in scored[:limit]]
    score_map = {s[0]: (s[1], s[2], s[3]) for s in scored[:limit]}

    placeholders = ",".join(["?"] * len(top_ids))
    rows = con.execute(
        f"SELECT * FROM segments WHERE id IN ({placeholders})",
        top_ids,
    ).fetchall()

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
        "Use search_norm for semantic + keyword search. "
        "Use get_segment to fetch a specific requirement by its segment_id string. "
        "Use list_norms to see what is indexed (no arguments needed). "
        "Use get_section to retrieve all requirements in a specific section of a norm. "
        "IMPORTANT: Never pass extra keys like 'results', 'data', or 'output' to any tool. "
        "Only pass the parameters listed in each tool's description."
    ),
)

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
    alpha: float = 0.5,
    types: list[str] | None = None,
) -> dict[str, Any]:
    """
    Search EUDI normative segments using hybrid BM25 + semantic search.

    PARAMETERS (pass only these, no others):
      query   (required) Search text. Example: "TSP audit log requirements"
      norm    (optional) Partial norm name filter. Example: "319 401"
      version (optional) Exact version string. Example: "v2.2.1"
      limit   (optional) Integer 1-20. Default: 10.
      alpha   (optional) Float 0.0-1.0. BM25 weight. Default: 0.5.
      types   (optional) List of strings: ["NORM"], ["INFORM"], or ["NORM","INFORM"].

    RETURNS dict with keys: query, result_count, mode, embedding_backend, results.
    results is a list of segment dicts — do NOT pass results as input.
    """
    limit = max(1, min(limit, MAX_RESULTS))
    alpha = max(0.0, min(alpha, 1.0))
    allowed_types = set(types) if types else {"NORM", "INFORM"}

    con = _get_db()
    results = _hybrid_search(con, query, norm, version, limit * 2, alpha)
    results = [r for r in results if r.get("type") in allowed_types]
    results = results[:limit]

    has_cosine = any(r["cosine_score"] > 0 for r in results)
    mode = "hybrid" if (alpha < 1.0 and has_cosine) else "bm25_only"

    return {
        "query":             query,
        "result_count":      len(results),
        "mode":              mode,
        "embedding_backend": _resolved_backend or "none",
        "results":           results,
    }


# ──────────────────────────────────────────────────────────────────────────────
# Tool: get_segment
# ──────────────────────────────────────────────────────────────────────────────

@mcp.tool()
def get_segment(segment_id: str) -> dict[str, Any]:
    """
    Fetch one segment by its exact ID string.

    PARAMETERS (pass only these, no others):
      segment_id (required) Exact ID string. Example: "en319401_p5_b2"

    RETURNS the segment dict, or {"error": "not found"} if the ID does not exist.
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
    List all norms indexed in the database with segment counts.

    PARAMETERS: none — call with no arguments.

    RETURNS dict with keys: total_norms, total_segments, norms (list).
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
    return {
        "total_norms":    len(norms),
        "total_segments": sum(n["total_segments"] for n in norms),
        "norms":          norms,
    }


# ──────────────────────────────────────────────────────────────────────────────
# Tool: get_section
# ──────────────────────────────────────────────────────────────────────────────

@mcp.tool()
def get_section(
    norm: str,
    section: str = "",
    version: str | None = None,
    types: list[str] | None = None,
) -> dict[str, Any]:
    """
    Retrieve all segments in a section of a norm. Section uses prefix match:
    "5" returns section 5, 5.1, 5.1.1, etc. Omit section to get the full norm.

    PARAMETERS (pass only these, no others):
      norm    (required) Partial norm name. Example: "319 401"
      section (optional) Section number string. Example: "5" or "5.1". Default: "" (all).
      version (optional) Exact version string. Example: "v2.2.1"
      types   (optional) List of strings to filter types: ["NORM"], ["INFORM"], ["SECTION"].

    RETURNS dict with keys: norm, version, section, segment_count, segments.
    Do NOT pass keys like "results" or "data" — they are not valid parameters.
    """
    con = _get_db()

    clauses: list[str] = ["s.norm LIKE ?"]
    params: list[Any] = [f"%{norm}%"]

    if section:
        clauses.append("(s.section = ? OR s.section LIKE ?)")
        params.extend([section, f"{section}.%"])

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

    return {
        "norm":          norm,
        "version":       version,
        "section":       section,
        "segment_count": len(rows),
        "segments":      [_row_to_dict(r) for r in rows],
    }


# ──────────────────────────────────────────────────────────────────────────────
# Entrypoint
# ──────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="EUDI-Nexus MCP Server")
    parser.add_argument("--db",       default=None, help="Override DB path")
    parser.add_argument("--no-embed", action="store_true", help="BM25-only mode")
    parser.add_argument("--backend",  default=None, choices=["lmstudio", "ollama", "auto"],
                        help="Embedding backend (overrides EMBEDDING_BACKEND env)")
    args = parser.parse_args()

    if args.db:
        os.environ["MCP_DB_PATH"] = args.db
        DB_PATH = Path(args.db)
    if args.no_embed:
        HYBRID_ALPHA = 1.0
        _resolved_backend = "none"
    if args.backend:
        os.environ["EMBEDDING_BACKEND"] = args.backend
        BACKEND = args.backend

    mcp.run(transport="stdio")
