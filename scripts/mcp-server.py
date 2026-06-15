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
  get_toc         —  Table of contents (section headings) for a norm

Transport: stdio (compatible with LM Studio, Ollama, Claude Desktop, mcp-cli)

Usage:
  python3 scripts/mcp-server.py
  python3 scripts/mcp-server.py --db corpus/eudi-nexus.db
  python3 scripts/mcp-server.py --no-embed   # BM25-only, no embedding endpoint needed
  python3 scripts/mcp-server.py --log logs/mcp.log

──────────────────────────────────────────────────────────────────────────────
  Embedding backend auto-detection (priority order):

  1. EMBEDDING_BACKEND env var ("lmstudio" | "ollama") — explicit override
  2. LM Studio  http://localhost:1234/v1/embeddings     — checked first
  3. Ollama     http://localhost:11434/api/embeddings   — fallback
  4. BM25-only  — if neither is reachable

  Environment variables:
    MCP_DB_PATH            corpus/eudi-nexus.db
    MCP_LOG_PATH           (empty = stderr only, set to enable file logging)
    MCP_LOG_LEVEL          INFO  (DEBUG | INFO | WARNING)
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
import logging
import os
import sqlite3
import struct
import sys
import time
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
# Logging
# ──────────────────────────────────────────────────────────────────────────────

def _setup_logging(log_path: str = "", level: str = "INFO") -> logging.Logger:
    """
    Configure structured logging to stderr (always) and optionally to a file.

    log_path: file path for persistent log (empty = stderr only)
    level:    DEBUG | INFO | WARNING
    """
    numeric_level = getattr(logging, level.upper(), logging.INFO)
    fmt = "%(asctime)s [%(levelname)-5s] %(message)s"
    datefmt = "%Y-%m-%dT%H:%M:%S"

    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stderr)]

    if log_path:
        log_file = Path(log_path)
        log_file.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setFormatter(logging.Formatter(fmt, datefmt=datefmt))
        handlers.append(file_handler)

    logging.basicConfig(
        level=numeric_level,
        format=fmt,
        datefmt=datefmt,
        handlers=handlers,
        force=True,
    )

    logger = logging.getLogger("eudi-nexus")
    logger.info("eudi-nexus MCP server starting  db=%s  log=%s",
                DB_PATH, log_path or "stderr only")
    return logger


# Initialised later in main() once CLI args are parsed;
# tools use module-level `log` so we provide a safe default.
log: logging.Logger = logging.getLogger("eudi-nexus")


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


# ---------------------------------------------------------------------------
# ESI docbox number → ETSI human-readable norm (mirrors pdf-ingest.py logic)
# ---------------------------------------------------------------------------
import re as _re

_ESI_DB_RE = _re.compile(
    r"^ESI-00(?P<number>\d{5})(?:-(?P<part>\d+))?v",
    _re.IGNORECASE,
)


def _esi_db_key_to_etsi(norm_db: str) -> str | None:
    """
    Convert a raw ESI docbox DB key like 'ESI-0019401v331v322' to the
    human-readable ETSI norm string 'EN 319 401'.

    Returns None if the input does not match the ESI-00NNNNN pattern.
    """
    m = _ESI_DB_RE.match(norm_db)
    if not m:
        return None
    val    = int(m.group("number"))   # e.g. 19401
    full   = val + 300_000             # e.g. 319401
    series = full // 1000              # e.g. 319
    seq    = full % 1000               # e.g. 401
    doc_type = "EN" if series >= 300 else "TS"
    base   = f"{doc_type} {series} {seq:03d}"
    part   = m.group("part")
    return f"{base}-{part}" if part else base


def _resolve_norm(raw: str, con: sqlite3.Connection) -> str:
    """
    Normalise a user-supplied norm identifier to the value stored in segments.norm.

    The DB contains two key formats that must both be handled:
      a) Human-readable ETSI keys:  'EN 319 401', 'TS 119 612', 'ISO/IEC 18013-5'
      b) ESI docbox keys:           'ESI-0019401v331v322', 'ESI-0019102-1v151v142'
         These arise when PDFs were ingested directly from the ETSI docbox by
         their raw filename before the _esi_number_to_etsi_norm fix was applied.
         The fuzzy resolver handles them transparently so tools keep working
         even against a legacy corpus that was not re-ingested.

    Supported input variants (examples for EN 319 401):
      'EN 319 401'         already canonical
      '319 401'            prefix-less
      '319401'             no spaces
      'en319401'           lowercase, no spaces
      'EN319 401'          partial spacing
      '319-401'            dashes instead of spaces
      'ETSI EN 319 401'    with SDO prefix
      'ts 119 612'         wrong series letter case
      'eidas'              alias table shorthand
      'sd-jwt'             alias table shorthand
      'EN 319 4O1'         OCR/typo '4O1' (O vs 0) handled by compact-digit step

    Falls back to the raw value if no match is found (caller uses it in LIKE).
    """
    # 0. Alias table for common shorthand / misspellings
    _ALIASES: dict[str, str] = {
        "eidas":          "eIDAS",
        "eidas2":         "eIDAS",
        "arf":            "ARF",
        "arfreqs":        "ARF",
        "sdjwt":          "SD-JWT",
        "sdjwtvc":        "SD-JWT",
        "sd-jwt":         "SD-JWT",
        "sd_jwt":         "SD-JWT",
        "openid4vp":      "OpenID4VP",
        "openid 4vp":     "OpenID4VP",
        "openid4vc":      "OpenID4VC",
        "iso18013":       "ISO/IEC 18013-5",
        "iso 18013":      "ISO/IEC 18013-5",
        "iso180135":      "ISO/IEC 18013-5",
        "18013":          "ISO/IEC 18013-5",
        "180135":         "ISO/IEC 18013-5",
        "mdl":            "ISO/IEC 18013-5",
        "mdoc":           "ISO/IEC 18013-5",
        # Numeric shorthands (digits only, no spaces/dashes)
        "319401":         "EN 319 401",
        "319403":         "EN 319 403",
        "319411":         "EN 319 411",
        "319412":         "EN 319 412",
        "319421":         "EN 319 421",
        "319431":         "EN 319 431",
        "319476":         "EN 319 476",
        "319479":         "EN 319 479",
        "119612":         "TS 119 612",
        "119495":         "TS 119 495",
        "119182":         "EN 119 182",
        "119102":         "EN 319 102",   # common typo: 119 instead of 319
    }

    def _db_like(pattern: str) -> str | None:
        """Return the first norm value matching LIKE '%pattern%', case-insensitive."""
        row = con.execute(
            "SELECT DISTINCT norm FROM segments WHERE LOWER(norm) LIKE LOWER(?) LIMIT 1",
            (f"%{pattern}%",),
        ).fetchone()
        return row[0] if row else None

    # Compact digit string used across multiple steps below
    compact = "".join(c for c in raw if c.isdigit())

    # --- Step 0: alias lookup ---
    # Works for both human-readable DB keys ('EN 319 401') and ESI-docbox DB keys
    # ('ESI-0019401v331v322'): the alias value is passed to _db_like which does a
    # case-insensitive LIKE on the raw DB column, so it can hit either format.
    normalized_raw = raw.strip().lower().replace(" ", "").replace("-", "").replace("_", "")
    for alias_key, alias_val in _ALIASES.items():
        if normalized_raw == alias_key.replace(" ", "").replace("-", "").replace("_", ""):
            hit = _db_like(alias_val)
            if hit:
                return hit
            # Alias value didn't match — try ESI-reverse on the alias digits too
            alias_compact = "".join(c for c in alias_val if c.isdigit())
            if len(alias_compact) == 6:
                etsi_num = int(alias_compact)
                if etsi_num > 300_000:
                    esi_inner = f"{etsi_num - 300_000:05d}"
                    row = con.execute(
                        "SELECT DISTINCT norm FROM segments WHERE norm LIKE ? LIMIT 1",
                        (f"%00{esi_inner}%",),
                    ).fetchone()
                    if row:
                        return row[0]

    # --- Step 1: direct LIKE (handles 'EN 319 401', '319 401', partial names
    #             AND ESI-docbox keys passed literally) ---
    hit = _db_like(raw)
    if hit:
        return hit

    # --- Step 2: SDO-prefix strip ('ETSI EN 319 401' → 'EN 319 401') ---
    for prefix in ("ETSI ", "CEN ", "ISO/IEC ", "ISO ", "IETF ", "RFC "):
        if raw.upper().startswith(prefix):
            trimmed = raw[len(prefix):]
            hit = _db_like(trimmed)
            if hit:
                return hit
            # Also try ESI-reverse on the trimmed value
            tc = "".join(c for c in trimmed if c.isdigit())
            if len(tc) == 6 and int(tc) > 300_000:
                esi_inner = f"{int(tc) - 300_000:05d}"
                row = con.execute(
                    "SELECT DISTINCT norm FROM segments WHERE norm LIKE ? LIMIT 1",
                    (f"%00{esi_inner}%",),
                ).fetchone()
                if row:
                    return row[0]

    # --- Step 3: numeric core strategies ---
    # The corpus may store norms as ESI-00NNNNN docbox keys instead of 'EN SSS NNN'.
    # For a 6-digit compact like '319401':
    #   3a) Try spaced form '319 401' via LIKE (hits human-readable DB keys)
    #   3b) Try SQL REPLACE strip (hits numeric substrings in any key)
    #   3c) Try ESI reverse-encode: 319401 → subtract 300000 → 19401 →
    #       search for ESI keys containing '0019401' (hits ESI docbox DB keys)
    if len(compact) >= 5:
        # 3a: spaced reconstruction
        if len(compact) == 6:
            spaced = f"{compact[:3]} {compact[3:]}"
            hit = _db_like(spaced)
            if hit:
                return hit
        elif len(compact) == 7:
            # Part-suffixed: '3194031' → '319 403' (part handled by caller if needed)
            spaced = f"{compact[:3]} {compact[3:6]}"
            hit = _db_like(spaced)
            if hit:
                return hit

        # 3b: SQL REPLACE strip – catches substrings in stripped keys
        row = con.execute(
            """
            SELECT DISTINCT norm FROM segments
            WHERE REPLACE(REPLACE(REPLACE(norm,' ',''),'-',''),'/','') LIKE ?
            LIMIT 1
            """,
            (f"%{compact}%",),
        ).fetchone()
        if row:
            return row[0]

        # 3c: ESI reverse-encoding
        #     'EN 319 401' → compact '319401' → 319401−300000=19401 → '0019401'
        #     Finds 'ESI-0019401v331v322' in the DB via LIKE '%0019401%'.
        if len(compact) == 6:
            etsi_num = int(compact)
            if etsi_num > 300_000:
                esi_inner = f"{etsi_num - 300_000:05d}"
                row = con.execute(
                    "SELECT DISTINCT norm FROM segments WHERE norm LIKE ? LIMIT 1",
                    (f"%00{esi_inner}%",),
                ).fetchone()
                if row:
                    return row[0]

    # --- Step 4: no match — return raw for caller's LIKE filter ---
    return raw


def _norm_filter_clause(norm: str | None, version: str | None, con: sqlite3.Connection | None = None) -> tuple[str, list]:
    clauses, params = [], []
    if norm:
        resolved = _resolve_norm(norm, con) if con is not None else norm
        clauses.append("s.norm LIKE ?")
        params.append(f"%{resolved}%")
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
    log.info("Embedding backend: %s", label)


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
    norm_clause, norm_params = _norm_filter_clause(norm, version, con)

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
        "You are a standards research assistant with access to EUDI/eIDAS normative "
        "segments from ETSI, CEN, and IETF specifications.\n\n"

        "BEHAVIOUR RULES — follow these strictly:\n"
        "1. NEVER ask the user which norm to search. Decide autonomously: "
        "   call list_norms first if unsure, pick the best match, then search it.\n"
        "2. NEVER ask 'Would you like me to search X?' — just do it.\n"
        "3. If a result text ends mid-sentence or seems truncated, "
        "   automatically call get_section with the section number to get the full context.\n"
        "4. If search_norm returns 0 results:\n"
        "   a) Call list_norms and find the correct 'norm' field value (e.g. 'EN 319 401').\n"
        "   b) Retry search_norm using that exact norm value.\n"
        "   c) If still 0 results, try a broader query — but NEVER repeat the identical\n"
        "      call with unchanged parameters. Change norm or query every retry.\n"
        "5. Always cite: norm name, version, section number, and segment ID.\n"
        "6. When a requirement contains 'shall', quote it verbatim.\n\n"

        "TOOL USAGE:\n"
        "- search_norm: semantic + keyword search. Use for any content query.\n"
        "- list_norms: lists all indexed norms. Call with NO arguments.\n"
        "- get_toc: table of contents for a norm — section headings with numbers. "
        "  Use BEFORE get_section to find the right section number.\n"
        "- get_section: retrieves all segments of a section. Use after search "
        "  to get full context of a truncated result.\n"
        "- get_segment: fetch one segment by its exact ID string.\n\n"

        "PARAMETER RULES:\n"
        "- Pass ONLY the parameters listed in each tool's description.\n"
        "- NEVER pass keys like 'results', 'data', 'output', or 'response'.\n"
        "- list_norms takes NO arguments at all.\n"
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
      query   (required) Search text. Be specific — use technical terms from the domain.
              Good:  "audit log confidentiality integrity UTC synchronisation"
              Good:  "termination plan private key destruction notification"
              Weak:  "trust service provider requirements"  (too generic, BM25 suffers)
              For broad topics use alpha=0.2 to weight semantic search more heavily.

      norm    (optional) Filter to one norm. Use the EXACT 'norm' field from list_norms.
              Examples: "EN 319 401", "TS 119 612", "ISO/IEC 18013-5", "SD-JWT"
              Fuzzy variants also work: "319401", "eidas", "mdl", "sd-jwt", "openid4vp"
              Omit to search across all norms.

      version (optional) Exact version string. Example: "v3.3.1", "v2.2.1"
              Omit to match all versions.

      limit   (optional) Integer 1-20. Default: 10.
              Use limit=20 for broad/exploratory queries to improve recall.

      alpha   (optional) Float 0.0-1.0. Controls BM25 vs semantic weight.
              1.0 = BM25 only (keyword exact match — best for known section numbers/terms)
              0.5 = balanced hybrid (default — good for most queries)
              0.2 = semantic-heavy (best for conceptual/broad queries like "TSP obligations")
              0.0 = cosine only (requires embedding backend)

      types   (optional) List of strings: ["NORM"], ["INFORM"], or ["NORM","INFORM"].
              Default: both NORM and INFORM. Use ["NORM"] to get only SHALL/MUST requirements.

    RETURNS dict with keys: query, result_count, mode, embedding_backend, results.
    results is a list of segment dicts — do NOT pass results as input.

    WORKFLOW:
      1. If unsure which norm to target, call list_norms first.
      2. Call search_norm with a specific query and the exact norm value.
      3. If result_count=0: broaden query, lower alpha, or omit norm filter entirely.
      4. If a result text is truncated, call get_section with the section number for full context.
    """
    t0 = time.monotonic()
    limit = max(1, min(limit, MAX_RESULTS))
    alpha = max(0.0, min(alpha, 1.0))
    allowed_types = set(types) if types else {"NORM", "INFORM"}

    con = _get_db()
    results = _hybrid_search(con, query, norm, version, limit * 2, alpha)
    results = [r for r in results if r.get("type") in allowed_types]
    results = results[:limit]

    has_cosine = any(r["cosine_score"] > 0 for r in results)
    mode = "hybrid" if (alpha < 1.0 and has_cosine) else "bm25_only"

    elapsed_ms = (time.monotonic() - t0) * 1000
    log.info("search_norm  query=%r  norm=%r  results=%d  mode=%s  %.0fms",
             query, norm, len(results), mode, elapsed_ms)

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
    t0 = time.monotonic()
    con = _get_db()
    row = con.execute(
        "SELECT * FROM segments WHERE id = ?", (segment_id,)
    ).fetchone()
    found = row is not None
    log.info("get_segment  id=%r  found=%s  %.0fms",
             segment_id, found, (time.monotonic() - t0) * 1000)
    if not found:
        return {"error": "not found", "segment_id": segment_id}
    return _row_to_dict(row)


# ──────────────────────────────────────────────────────────────────────────────
# Tool: list_norms
# ──────────────────────────────────────────────────────────────────────────────

@mcp.tool()
def list_norms() -> dict[str, Any]:
    """
    List all norms indexed in the database with segment counts and human-readable metadata.

    PARAMETERS: none — call with no arguments at all.

    RETURNS dict with keys: total_norms, total_segments, norms (list).
    Each norm entry contains:
      norm             Exact value to use as the 'norm' parameter in search_norm / get_section.
                       Examples: "EN 319 401", "TS 119 612", "ISO/IEC 18013-5", "SD-JWT"
      version          Latest version string. Examples: "v3.3.1", "v2.2.1"
      display_name     Human-readable label combining norm + version + title excerpt.
                       Example: "EN 319 401 v3.3.1 — General Policy Requirements for TSP"
      title            Document title from section 0, if available.
      total_segments   Total indexed segments (all types).
      norm_count       Normative (SHALL/MUST) segments.
      inform_count     Informative segments.
      section_count    Section-heading segments.
      embedded_count   Segments with a semantic embedding (cosine search available).

    USAGE NOTE: Always call list_norms before search_norm if you are unsure which
    norm to target. Copy the exact 'norm' field value (e.g. "EN 319 401") into the
    'norm' parameter of search_norm or get_section — do not paraphrase or abbreviate.
    """
    t0 = time.monotonic()
    con = _get_db()
    rows = con.execute(
        """
        SELECT
            s.norm, s.version,
            COUNT(*) as total_segments,
            SUM(CASE WHEN s.type='NORM'    THEN 1 ELSE 0 END) as norm_count,
            SUM(CASE WHEN s.type='INFORM'  THEN 1 ELSE 0 END) as inform_count,
            SUM(CASE WHEN s.type='SECTION' THEN 1 ELSE 0 END) as section_count,
            SUM(s.has_embedding) as embedded_count,
            (
                SELECT n2.text FROM segments n2
                WHERE n2.norm = s.norm AND n2.version = s.version
                  AND n2.type = 'SECTION' AND n2.section = '0'
                LIMIT 1
            ) as title
        FROM segments s
        GROUP BY s.norm, s.version
        ORDER BY s.norm, s.version
        """
    ).fetchall()

    norms = [dict(r) for r in rows]
    for n in norms:
        title_excerpt = (n.get("title") or "").strip()
        if len(title_excerpt) > 80:
            title_excerpt = title_excerpt[:77] + "..."
        version_str = n.get("version") or ""

        # For ESI docbox keys ('ESI-0019401v331v322') add a decoded human name.
        # After a full re-ingest with the fixed pdf-ingest.py these keys will no
        # longer appear, but a legacy corpus should still present readable output.
        etsi_decoded = _esi_db_key_to_etsi(n["norm"])
        label = etsi_decoded if etsi_decoded else n["norm"]
        if etsi_decoded:
            n["etsi_norm"] = etsi_decoded   # e.g. 'EN 319 401'

        n["display_name"] = (
            f"{label} {version_str} — {title_excerpt}"
            if title_excerpt
            else f"{label} {version_str}"
        ).strip()

    log.info("list_norms  total_norms=%d  total_segments=%d  %.0fms",
             len(norms), sum(n["total_segments"] for n in norms),
             (time.monotonic() - t0) * 1000)
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
    Retrieve all segments in a section of a norm. Use this to get full context after
    search_norm returns a truncated or partial result.

    Section matching uses prefix: "5" returns 5, 5.1, 5.1.1, 5.2, etc.
    Omit section entirely to retrieve the full norm.

    PARAMETERS (pass only these, no others):
      norm    (required) Partial norm name — same fuzzy matching as search_norm.
              Use the exact 'norm' field from list_norms for reliable results.
              Examples: "EN 319 401", "319 401", "319401", "TS 119 612"
              Fuzzy shorthand also works: "eidas", "mdl", "sd-jwt", "openid4vp"

      section (optional) Section number string. Examples: "5", "5.1", "7.10", "6".
              Omit or pass "" to retrieve all segments in the norm.

      version (optional) Exact version string. Example: "v3.3.1"
              Omit to match any version (picks most recent if multiple exist).

      types   (optional) Filter by segment type.
              ["NORM"]    — only normative SHALL/MUST requirements
              ["INFORM"]  — only informative guidance
              ["SECTION"] — only section headings (useful for TOC overview)
              Omit for all types.

    RETURNS dict with keys: norm, version, section, segment_count, segments.
    Do NOT pass keys like "results" or "data" — they are not valid parameters.
    """
    t0 = time.monotonic()
    con = _get_db()

    resolved = _resolve_norm(norm, con)

    clauses: list[str] = ["s.norm LIKE ?"]
    params: list[Any] = [f"%{resolved}%"]

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

    log.info("get_section  norm=%r  resolved=%r  section=%r  segments=%d  %.0fms",
             norm, resolved, section, len(rows), (time.monotonic() - t0) * 1000)

    return {
        "norm":          resolved,
        "version":       version,
        "section":       section,
        "segment_count": len(rows),
        "segments":      [_row_to_dict(r) for r in rows],
    }


# ──────────────────────────────────────────────────────────────────────────────
# Tool: get_toc
# ──────────────────────────────────────────────────────────────────────────────

@mcp.tool()
def get_toc(
    norm: str,
    version: str | None = None,
    depth: int = 3,
) -> dict[str, Any]:
    """
    Return the table of contents (section headings) for a norm.

    Use this to orient yourself in an unfamiliar norm BEFORE calling get_section.
    It shows the full numbered structure so you can pick the right section number.

    PARAMETERS (pass only these, no others):
      norm     (required) Norm identifier — same fuzzy matching as search_norm.
               Use the exact 'norm' field from list_norms for reliable results.
               Examples: "EN 319 401", "TS 119 612", "ISO/IEC 18013-5"
               Fuzzy shorthand also works: "319401", "eidas", "mdl", "sd-jwt"

      version  (optional) Exact version string. Example: "v3.3.1"
               Omit to use the most recent indexed version.

      depth    (optional) Integer 1-5. Maximum section-number depth to include.
               1 = top-level only ("1", "2", "A"), useful for a quick overview.
               2 = two levels  ("1", "1.1"),  recommended for navigation.
               3 = three levels (default) — good balance of detail vs. brevity.
               5 = full depth — very long for large norms, use sparingly.

    RETURNS dict with keys: norm, version, depth, section_count, toc.
    toc is a list of {section, title} dicts ordered by document position.

    WORKFLOW:
      1. Call get_toc to see the structure and find which section covers the topic.
      2. Call get_section with the identified section number for full content.
      3. Call search_norm if you want to search across sections without prior knowledge.
    """
    t0 = time.monotonic()
    con = _get_db()

    resolved = _resolve_norm(norm, con)

    clauses: list[str] = ["s.type = 'SECTION'", "s.norm LIKE ?"]
    params: list[Any] = [f"%{resolved}%"]

    if version:
        clauses.append("s.version = ?")
        params.append(version)

    where = " AND ".join(clauses)
    rows = con.execute(
        f"""
        SELECT s.section, s.text, s.version
        FROM segments s
        WHERE {where}
        ORDER BY s.page, s.id
        """,
        params,
    ).fetchall()

    # Filter by depth: count dots in section number.
    # Section "5.1.2" has depth 3 (2 dots + 1).
    # Appendix-style sections like "A.1" are handled the same way.
    def _section_depth(sec: str) -> int:
        return sec.count(".") + 1 if sec.strip() else 1

    toc = [
        {"section": row["section"], "title": (row["text"] or "").strip()}
        for row in rows
        if _section_depth(row["section"] or "") <= depth
    ]

    # Determine which version was actually used (first row, if any).
    actual_version = rows[0]["version"] if rows else version

    log.info("get_toc  norm=%r  version=%r  depth=%d  entries=%d  %.0fms",
             resolved, actual_version, depth, len(toc),
             (time.monotonic() - t0) * 1000)

    return {
        "norm":          resolved,
        "version":       actual_version,
        "depth":         depth,
        "section_count": len(toc),
        "toc":           toc,
    }


# ──────────────────────────────────────────────────────────────────────────────
# Entrypoint
# ──────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="EUDI-Nexus MCP Server")
    parser.add_argument("--db",        default=None, help="Override DB path")
    parser.add_argument("--no-embed",  action="store_true", help="BM25-only mode")
    parser.add_argument("--backend",   default=None, choices=["lmstudio", "ollama", "auto"],
                        help="Embedding backend (overrides EMBEDDING_BACKEND env)")
    parser.add_argument("--log",       default=None,
                        help="Log file path (default: stderr only). Env: MCP_LOG_PATH")
    parser.add_argument("--log-level", default=None,
                        help="Log level DEBUG|INFO|WARNING (default: INFO). Env: MCP_LOG_LEVEL")
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

    # Resolve log config: CLI flag > env var > default
    log_path  = args.log       or os.environ.get("MCP_LOG_PATH",  "")
    log_level = args.log_level or os.environ.get("MCP_LOG_LEVEL", "INFO")
    log = _setup_logging(log_path, log_level)

    mcp.run(transport="stdio")
